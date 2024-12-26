import datetime
import boto3
import json
import time
import zipfile
import os
from botocore.exceptions import ClientError

def check_aws_credentials():
    """Check if AWS credentials are configured"""
    try:
        boto3.client('sts').get_caller_identity()
        return True
    except Exception:
        return False

class InfrastructureManager:
    def __init__(self, region='us-east-1'):
        self.region = region
        print(f"Using AWS region: {self.region}")
        
        # Initialize AWS clients with region
        self.s3 = boto3.client('s3', region_name=self.region)
        self.dynamodb = boto3.client('dynamodb', region_name=self.region)
        self.lambda_client = boto3.client('lambda', region_name=self.region)
        self.iam = boto3.client('iam', region_name=self.region)
        self.apigateway = boto3.client('apigatewayv2', region_name=self.region)
        self.cloudwatch = boto3.client('cloudwatch', region_name=self.region)
        
        # Load existing infrastructure details if available
        self.existing_infrastructure = self.load_existing_infrastructure()

    def load_existing_infrastructure(self):
        """Load existing infrastructure details from file"""
        try:
            with open('infrastructure_details.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def wait_for_lambda_update(self, function_name, max_retries=10, base_delay=10):
        """Wait for Lambda function update to complete with enhanced retry logic"""
        for attempt in range(max_retries):
            try:
                # Get function state
                response = self.lambda_client.get_function(FunctionName=function_name)
                state = response['Configuration'].get('State')
                last_update = response['Configuration'].get('LastUpdateStatus', '')
                
                if state == 'Active' and last_update != 'InProgress':
                    print(f"Lambda function {function_name} is ready")
                    return True
                    
                wait_time = base_delay * (2 ** attempt)  # Exponential backoff
                print(f"Lambda function {function_name} is {state} with update status {last_update}. Waiting {wait_time} seconds...")
                time.sleep(wait_time)
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceNotFoundException':
                    print(f"Lambda function {function_name} not found")
                    return False
                elif e.response['Error']['Code'] == 'ResourceConflictException':
                    wait_time = base_delay * (2 ** attempt)
                    print(f"Lambda function {function_name} has a conflict. Waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    raise
        
        print(f"Timeout waiting for Lambda function {function_name} to be ready")
        return False

    def update_lambda_function(self, function_name, zip_content, role_arn, env_vars):
        """Update Lambda function with retries"""
        max_retries = 5
        base_delay = 10
        
        for attempt in range(max_retries):
            try:
                # Wait for function to be ready
                if not self.wait_for_lambda_update(function_name):
                    raise Exception(f"Timeout waiting for Lambda function {function_name} to be ready")

                # Update code first
                print(f"Updating code for Lambda function: {function_name}")
                self.lambda_client.update_function_code(
                    FunctionName=function_name,
                    ZipFile=zip_content
                )
                
                # Wait for code update to complete
                if not self.wait_for_lambda_update(function_name):
                    raise Exception(f"Timeout waiting for code update on {function_name}")

                # Then update configuration
                print(f"Updating configuration for Lambda function: {function_name}")
                self.lambda_client.update_function_configuration(
                    FunctionName=function_name,
                    Runtime='python3.9',
                    Role=role_arn,
                    Handler='index.lambda_handler',
                    Timeout=29,
                    MemorySize=128,
                    Environment={'Variables': env_vars}
                )
                
                # Wait for configuration update to complete
                if not self.wait_for_lambda_update(function_name):
                    raise Exception(f"Timeout waiting for configuration update on {function_name}")
                
                return True
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceConflictException':
                    if attempt < max_retries - 1:
                        wait_time = base_delay * (2 ** attempt)
                        print(f"Conflict updating {function_name}. Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                raise
        
        return False

    def create_lambda_functions(self, role_arn, table_name):
        """Create Lambda functions with enhanced retry logic"""
        print("\nSetting up Lambda functions...")
        
        functions = {
            'discoveryProcessor': 'discovery_processor',
            'costEstimator': 'cost_estimator',
            'roadmapGenerator': 'roadmap_generator'
        }
        
        lambda_functions = {}
        
        for func_key, func_name in functions.items():
            function_name = f"migration-planner-{func_name}"
            lambda_dir = f"lambda/{func_key}"
            handler_file = f"{lambda_dir}/index.py"
            
            # Skip if handler file doesn't exist
            if not os.path.exists(handler_file):
                print(f"Skipping {function_name} - handler file not found at {handler_file}")
                continue
            
            env_vars = {
                'DISCOVERY_TABLE': table_name,
                'REGION': self.region,
                'FREE_TIER_ENABLED': 'true'
            }
            
            try:
                # Create ZIP file
                zip_path = f"/tmp/{function_name}.zip"
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    zipf.write(handler_file, "index.py")
                
                with open(zip_path, 'rb') as f:
                    zip_content = f.read()
                
                try:
                    # Check if function exists
                    try:
                        self.lambda_client.get_function(FunctionName=function_name)
                        exists = True
                    except ClientError as e:
                        if e.response['Error']['Code'] == 'ResourceNotFoundException':
                            exists = False
                        else:
                            raise

                    if exists:
                        # Update existing function with retries
                        print(f"Updating existing Lambda function: {function_name}")
                        if not self.update_lambda_function(function_name, zip_content, role_arn, env_vars):
                            raise Exception(f"Failed to update Lambda function {function_name}")
                    else:
                        # Create new function
                        print(f"Creating new Lambda function: {function_name}")
                        self.lambda_client.create_function(
                            FunctionName=function_name,
                            Runtime='python3.9',
                            Role=role_arn,
                            Handler='index.lambda_handler',
                            Code={'ZipFile': zip_content},
                            Timeout=29,
                            MemorySize=128,
                            Environment={'Variables': env_vars}
                        )
                    
                finally:
                    # Clean up ZIP file
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                
                # Get function ARN
                response = self.lambda_client.get_function(FunctionName=function_name)
                lambda_functions[func_key] = response['Configuration']['FunctionArn']
                print(f"Successfully configured Lambda function: {function_name}")
                
            except Exception as e:
                print(f"Error with Lambda function {function_name}: {str(e)}")
                raise
        
        return lambda_functions

    def setup_cloudwatch_monitoring(self):
        """Setup CloudWatch monitoring for services"""
        print("\nSetting up CloudWatch monitoring...")
        
        try:
            # Lambda function monitoring
            lambda_functions = ['discoveryProcessor', 'costEstimator', 'roadmapGenerator']
            for function in lambda_functions:
                self.cloudwatch.put_metric_alarm(
                    AlarmName=f'migration-planner-{function}-errors',
                    ComparisonOperator='GreaterThanThreshold',
                    EvaluationPeriods=1,
                    MetricName='Errors',
                    Namespace='AWS/Lambda',
                    Period=300,
                    Statistic='Sum',
                    Threshold=1.0,
                    AlarmDescription=f'Alert when {function} has errors',
                    Dimensions=[
                        {
                            'Name': 'FunctionName',
                            'Value': f'migration-planner-{function}'
                        }
                    ]
                )
            
            # API Gateway monitoring
            self.cloudwatch.put_metric_alarm(
                AlarmName='migration-planner-api-latency',
                ComparisonOperator='GreaterThanThreshold',
                EvaluationPeriods=1,
                MetricName='Latency',
                Namespace='AWS/ApiGateway',
                Period=300,
                Statistic='Average',
                Threshold=1000.0,
                AlarmDescription='Alert when API Gateway latency is high'
            )
            
            # DynamoDB monitoring
            self.cloudwatch.put_metric_alarm(
                AlarmName='migration-planner-dynamodb-throttles',
                ComparisonOperator='GreaterThanThreshold',
                EvaluationPeriods=1,
                MetricName='ThrottledRequests',
                Namespace='AWS/DynamoDB',
                Period=300,
                Statistic='Sum',
                Threshold=1.0,
                AlarmDescription='Alert when DynamoDB requests are throttled'
            )
            
            print("CloudWatch monitoring configured successfully")
        except Exception as e:
            print(f"Error setting up CloudWatch monitoring: {str(e)}")
            raise

    def create_s3_bucket(self):
        """Create S3 bucket with Free Tier optimizations"""
        if 'bucket_name' in self.existing_infrastructure:
            try:
                self.s3.head_bucket(Bucket=self.existing_infrastructure['bucket_name'])
                print(f"\nUsing existing S3 bucket: {self.existing_infrastructure['bucket_name']}")
                return self.existing_infrastructure['bucket_name']
            except ClientError:
                pass

        bucket_name = f"migration-planner-data-{int(time.time())}"
        print(f"\nCreating S3 bucket: {bucket_name}")
        
        try:
            # Create bucket - special handling for us-east-1
            if self.region == 'us-east-1':
                self.s3.create_bucket(Bucket=bucket_name)
            else:
                self.s3.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={
                        'LocationConstraint': self.region
                    }
                )

            # Enable versioning
            self.s3.put_bucket_versioning(
                Bucket=bucket_name,
                VersioningConfiguration={'Status': 'Enabled'}
            )

            # Enable encryption
            self.s3.put_bucket_encryption(
                Bucket=bucket_name,
                ServerSideEncryptionConfiguration={
                    'Rules': [
                        {
                            'ApplyServerSideEncryptionByDefault': {
                                'SSEAlgorithm': 'AES256'
                            }
                        }
                    ]
                }
            )

            # Add lifecycle policy for Free Tier optimization
            lifecycle_policy = {
                'Rules': [
                    {
                        'ID': 'FreeTierOptimization',
                        'Status': 'Enabled',
                        'Prefix': '',
                        'Transitions': [
                            {
                                'Days': 30,
                                'StorageClass': 'STANDARD_IA'
                            }
                        ],
                        'Expiration': {
                            'Days': 90
                        }
                    }
                ]
            }
            
            self.s3.put_bucket_lifecycle_configuration(
                Bucket=bucket_name,
                LifecycleConfiguration=lifecycle_policy
            )

            print(f"Successfully created S3 bucket: {bucket_name}")
            return bucket_name

        except Exception as e:
            print(f"Error creating S3 bucket: {str(e)}")
            raise

    def create_dynamodb_table(self):
        """Create DynamoDB table with Free Tier optimizations"""
        if 'table_name' in self.existing_infrastructure:
            try:
                self.dynamodb.describe_table(TableName=self.existing_infrastructure['table_name'])
                print(f"\nUsing existing DynamoDB table: {self.existing_infrastructure['table_name']}")
                return self.existing_infrastructure['table_name']
            except ClientError:
                pass

        table_name = f"migration-assessments-{int(time.time())}"
        print(f"\nCreating DynamoDB table: {table_name}")
        
        try:
            self.dynamodb.create_table(
                TableName=table_name,
                KeySchema=[
                    {'AttributeName': 'serverId', 'KeyType': 'HASH'},
                    {'AttributeName': 'timestamp', 'KeyType': 'RANGE'}
                ],
                AttributeDefinitions=[
                    {'AttributeName': 'serverId', 'AttributeType': 'S'},
                    {'AttributeName': 'timestamp', 'AttributeType': 'S'}
                ],
                BillingMode='PROVISIONED',
                ProvisionedThroughput={
                    'ReadCapacityUnits': 25,
                    'WriteCapacityUnits': 25
                }
            )
            
            print("Waiting for DynamoDB table to be ready...")
            waiter = self.dynamodb.get_waiter('table_exists')
            waiter.wait(TableName=table_name)
            
            return table_name

        except Exception as e:
            print(f"Error creating DynamoDB table: {str(e)}")
            raise

    def create_lambda_role(self):
        """Create IAM role for Lambda functions"""
        role_name = "migration_planner_lambda_role"
        
        try:
            response = self.iam.get_role(RoleName=role_name)
            print(f"\nUsing existing IAM role: {role_name}")
            return response['Role']['Arn']
        except ClientError:
            print(f"\nCreating IAM role: {role_name}")
            
            try:
                # Create role
                assume_role_policy = {
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole"
                    }]
                }
                
                response = self.iam.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=json.dumps(assume_role_policy)
                )
                
                # Attach AWS managed policies
                managed_policies = [
                    'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole',
                    'arn:aws:iam::aws:policy/AWSApplicationDiscoveryAgentAccess'
                ]
                
                for policy in managed_policies:
                    print(f"Attaching policy: {policy}")
                    self.iam.attach_role_policy(
                        RoleName=role_name,
                        PolicyArn=policy
                    )
                
                # Create custom policy
                policy_document = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                                "s3:PutObject",
                                "s3:ListBucket"
                            ],
                            "Resource": ["arn:aws:s3:::*"]
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "dynamodb:PutItem",
                                "dynamodb:GetItem",
                                "dynamodb:Query",
                                "dynamodb:UpdateItem"
                            ],
                            "Resource": ["arn:aws:dynamodb:*:*:table/*"]
                        },
                        {
                            "Effect": "Allow",
                            "Action": [
                                "cloudwatch:PutMetricData",
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents"
                            ],
                            "Resource": "*"
                        }
                    ]
                }
                
                print("Attaching custom policy")
                self.iam.put_role_policy(
                    RoleName=role_name,
                    PolicyName="migration_planner_policy",
                    PolicyDocument=json.dumps(policy_document)
                )
                
                # Wait for role to be ready
                print("Waiting for IAM role to be ready...")
                time.sleep(10)
                
                return response['Role']['Arn']

            except Exception as e:
                print(f"Error creating IAM role: {str(e)}")
                raise

    def create_api_gateway(self, lambda_functions):
        """Create API Gateway with Free Tier considerations"""
        print("\nSetting up API Gateway...")
        
        api_name = "migration-planner-api"
        
        try:
            # Try to find existing API
            apis = self.apigateway.get_apis()
            existing_api = next(
                (api for api in apis['Items'] if api['Name'] == api_name),
                None
            )
            
            if existing_api:
                print(f"Using existing API Gateway: {api_name}")
                api_id = existing_api['ApiId']
                self.update_api_routes(api_id, lambda_functions)
                return f"https://{api_id}.execute-api.{self.region}.amazonaws.com/prod"
            
            # Create new API
            api_response = self.apigateway.create_api(
                Name=api_name,
                ProtocolType='HTTP',
                CorsConfiguration={
                    'AllowOrigins': ['*'],
                    'AllowMethods': ['POST', 'GET', 'OPTIONS'],
                    'AllowHeaders': ['content-type']
                }
            )
            
            api_id = api_response['ApiId']
            
            # Create stage with throttling for Free Tier
            self.apigateway.create_stage(
                ApiId=api_id,
                StageName='prod',
                AutoDeploy=True,
                DefaultRouteSettings={
                    'ThrottlingBurstLimit': 1000,
                    'ThrottlingRateLimit': 1000
                }
            )
            
            # Create routes
            self.update_api_routes(api_id, lambda_functions)
            
            api_url = f"https://{api_id}.execute-api.{self.region}.amazonaws.com/prod"
            print(f"Successfully created API Gateway: {api_url}")
            return api_url
            
        except Exception as e:
            print(f"Error creating API Gateway: {str(e)}")
            raise

    def update_api_routes(self, api_id, lambda_functions):
        """Update API Gateway routes"""
        routes = {
            'analyze': lambda_functions['discoveryProcessor'],
            'estimate': lambda_functions['costEstimator'],
            'roadmap': lambda_functions['roadmapGenerator']
        }
        
        for route_name, function_arn in routes.items():
            try:
                # Delete existing route if it exists
                try:
                    existing_routes = self.apigateway.get_routes(ApiId=api_id)
                    for route in existing_routes.get('Items', []):
                        if route['RouteKey'] == f"POST /{route_name}":
                            self.apigateway.delete_route(
                                ApiId=api_id,
                                RouteId=route['RouteId']
                            )
                            print(f"Deleted existing route: {route_name}")
                except Exception as e:
                    print(f"Note: No existing route found for {route_name}: {str(e)}")

                # Create integration
                integration = self.apigateway.create_integration(
                    ApiId=api_id,
                    IntegrationType='AWS_PROXY',
                    IntegrationUri=function_arn,
                    PayloadFormatVersion='2.0',
                    IntegrationMethod='POST',
                    TimeoutInMillis=29000
                )
                
                # Create route
                self.apigateway.create_route(
                    ApiId=api_id,
                    RouteKey=f"POST /{route_name}",
                    Target=f"integrations/{integration['IntegrationId']}"
                )
                
                # Add Lambda permission
                function_name = function_arn.split(':')[-1]
                try:
                    self.lambda_client.add_permission(
                        FunctionName=function_name,
                        StatementId=f'ApiGateway-{route_name}',
                        Action='lambda:InvokeFunction',
                        Principal='apigateway.amazonaws.com',
                        SourceArn=f'arn:aws:execute-api:{self.region}:{self.get_account_id()}:{api_id}/*/*'
                    )
                except ClientError as e:
                    if e.response['Error']['Code'] != 'ResourceConflictException':
                        raise
                        
                print(f"Successfully configured route: {route_name}")
                        
            except Exception as e:
                print(f"Error setting up route {route_name}: {str(e)}")
                raise

    def get_account_id(self):
        """Get AWS account ID"""
        return boto3.client('sts').get_caller_identity()['Account']
    
    def create_infrastructure(self):
        """Create or update complete infrastructure"""
        print("Starting infrastructure setup...")
        
        try:
            # Create S3 bucket
            bucket_name = self.create_s3_bucket()
            
            # Create DynamoDB table
            table_name = self.create_dynamodb_table()
            
            # Create IAM role
            role_arn = self.create_lambda_role()
            
            # Create Lambda functions
            lambda_functions = self.create_lambda_functions(role_arn, table_name)
            
            # Create API Gateway
            api_url = self.create_api_gateway(lambda_functions)
            
            # Setup CloudWatch monitoring
            self.setup_cloudwatch_monitoring()
            
            # Save infrastructure details
            infra_details = {
                'api_url': api_url,
                'bucket_name': bucket_name,
                'table_name': table_name,
                'region': self.region,
                'lambda_functions': lambda_functions,
                'created_at': datetime.datetime.now().isoformat()
            }
            
            with open('infrastructure_details.json', 'w') as f:
                json.dump(infra_details, f, indent=2)
            
            print("\nInfrastructure setup completed successfully!")
            print(f"API Gateway URL: {api_url}")
            print(f"S3 Bucket: {bucket_name}")
            print(f"DynamoDB Table: {table_name}")
            
            return infra_details
            
        except Exception as e:
            print(f"Error during infrastructure setup: {str(e)}")
            raise

def main():
    if not check_aws_credentials():
        print("Error: AWS credentials not found or not configured.")
        print("Please run 'aws configure' to set up your AWS credentials.")
        return
    
    try:
        infra_manager = InfrastructureManager()
        infra_details = infra_manager.create_infrastructure()
        
        print("\nSetup completed successfully!")
        print("1. Infrastructure details saved to infrastructure_details.json")
        print("2. CloudWatch monitoring configured")
        print("3. Free Tier optimizations applied")
        print("\nNext steps:")
        print("1. Configure your Lambda functions in the /lambda directory")
        print("2. Update your application configuration with the API Gateway URL")
        print("3. Monitor the CloudWatch dashboard for usage metrics")
        
    except Exception as e:
        print(f"\nError during setup: {str(e)}")
        print("Please check the error message and try again.")

if __name__ == "__main__":
    main()
                