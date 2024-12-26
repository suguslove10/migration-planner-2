import boto3
import json
import time
from botocore.exceptions import ClientError
import os

def check_aws_credentials():
    """Check if AWS credentials are configured"""
    try:
        boto3.client('sts').get_caller_identity()
        return True
    except Exception:
        return False

class InfrastructureCleanup:
    def __init__(self, region='us-east-1'):
        self.region = region
        print(f"Using AWS region: {self.region}")
        
        # Initialize AWS clients
        self.s3 = boto3.client('s3', region_name=self.region)
        self.dynamodb = boto3.client('dynamodb', region_name=self.region)
        self.lambda_client = boto3.client('lambda', region_name=self.region)
        self.iam = boto3.client('iam', region_name=self.region)
        self.apigateway = boto3.client('apigatewayv2', region_name=self.region)
        self.cloudwatch = boto3.client('cloudwatch', region_name=self.region)
        self.logs = boto3.client('logs', region_name=self.region)
        
        # Load infrastructure details
        self.infra_details = self.load_infrastructure_details()

    def load_infrastructure_details(self):
        """Load infrastructure details from file"""
        try:
            with open('infrastructure_details.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print("No infrastructure details file found.")
            return {}

    def delete_cloudwatch_alarms(self):
        """Delete CloudWatch alarms"""
        print("\nDeleting CloudWatch alarms...")
        try:
            # List of all possible alarm names
            alarm_names = [
                'migration-planner-discoveryProcessor-errors',
                'migration-planner-costEstimator-errors',
                'migration-planner-roadmapGenerator-errors',
                'migration-planner-api-latency',
                'migration-planner-dynamodb-throttles'
            ]
            
            # Get existing alarms
            existing_alarms = self.cloudwatch.describe_alarms(AlarmNames=alarm_names)
            existing_alarm_names = [alarm['AlarmName'] for alarm in existing_alarms.get('MetricAlarms', [])]
            
            if existing_alarm_names:
                self.cloudwatch.delete_alarms(AlarmNames=existing_alarm_names)
                print(f"Successfully deleted {len(existing_alarm_names)} CloudWatch alarms")
            else:
                print("No CloudWatch alarms found to delete")
                
        except Exception as e:
            print(f"Error deleting CloudWatch alarms: {str(e)}")

    def delete_api_gateway(self):
        """Delete API Gateway with enhanced error handling"""
        if 'api_url' not in self.infra_details:
            print("\nNo API Gateway found in infrastructure details")
            return

        print("\nDeleting API Gateway...")
        
        try:
            # Extract API ID from the URL correctly
            api_url = self.infra_details['api_url']
            try:
                api_id = api_url.split('/')[2].split('.')[0]
            except Exception:
                print(f"Warning: Could not parse API ID from URL: {api_url}")
                return

            # Verify API exists
            try:
                self.apigateway.get_api(ApiId=api_id)
            except ClientError as e:
                if e.response['Error']['Code'] == 'NotFoundException':
                    print(f"API Gateway {api_id} not found - may have been already deleted")
                    return
                elif e.response['Error']['Code'] == 'AccessDeniedException':
                    print(f"Warning: Insufficient permissions to access API Gateway {api_id}")
                    print("Please ensure your IAM user/role has the following permissions:")
                    print("- apigateway:GET")
                    print("- apigateway:DELETE")
                    print("- apigateway:UpdateStage")
                    return
                else:
                    raise

            # Delete stages first
            try:
                stages = self.apigateway.get_stages(ApiId=api_id)
                for stage in stages.get('Items', []):
                    try:
                        self.apigateway.delete_stage(
                            ApiId=api_id,
                            StageName=stage['StageName']
                        )
                        print(f"Deleted stage: {stage['StageName']}")
                    except Exception as e:
                        print(f"Warning: Error deleting stage {stage['StageName']}: {str(e)}")
            except Exception as e:
                print(f"Warning: Error listing stages: {str(e)}")

            # Delete routes and integrations
            try:
                routes = self.apigateway.get_routes(ApiId=api_id)
                for route in routes.get('Items', []):
                    try:
                        # Delete route
                        self.apigateway.delete_route(
                            ApiId=api_id,
                            RouteId=route['RouteId']
                        )
                        print(f"Deleted route: {route.get('RouteKey', 'unknown')}")

                        # Delete associated integration
                        if 'Target' in route:
                            integration_id = route['Target'].split('/')[-1]
                            try:
                                self.apigateway.delete_integration(
                                    ApiId=api_id,
                                    IntegrationId=integration_id
                                )
                                print(f"Deleted integration for route: {route.get('RouteKey', 'unknown')}")
                            except Exception as e:
                                print(f"Warning: Error deleting integration {integration_id}: {str(e)}")
                    except Exception as e:
                        print(f"Warning: Error deleting route {route.get('RouteId', 'unknown')}: {str(e)}")
            except Exception as e:
                print(f"Warning: Error listing routes: {str(e)}")

            # Final API deletion
            try:
                self.apigateway.delete_api(ApiId=api_id)
                print(f"Successfully deleted API Gateway: {api_id}")
            except ClientError as e:
                if e.response['Error']['Code'] == 'AccessDeniedException':
                    print("Warning: Insufficient permissions to delete API Gateway")
                    print("Please ensure your IAM user/role has apigateway:DELETE permission")
                else:
                    raise
            
        except Exception as e:
            print(f"Error during API Gateway cleanup: {str(e)}")

    def delete_lambda_functions(self):
        """Delete Lambda functions and associated resources"""
        print("\nDeleting Lambda functions...")
        
        function_names = [
            'migration-planner-discovery_processor',
            'migration-planner-cost_estimator',
            'migration-planner-roadmap_generator'
        ]
        
        for function_name in function_names:
            try:
                # Delete function
                try:
                    self.lambda_client.delete_function(FunctionName=function_name)
                    print(f"Successfully deleted Lambda function: {function_name}")
                except ClientError as e:
                    if e.response['Error']['Code'] != 'ResourceNotFoundException':
                        print(f"Error deleting Lambda function {function_name}: {str(e)}")
                
                # Delete associated CloudWatch log group
                log_group_name = f"/aws/lambda/{function_name}"
                try:
                    self.logs.delete_log_group(logGroupName=log_group_name)
                    print(f"Deleted associated log group: {log_group_name}")
                except ClientError as e:
                    if e.response['Error']['Code'] != 'ResourceNotFoundException':
                        print(f"Warning: Error deleting log group {log_group_name}: {str(e)}")
                    
            except Exception as e:
                print(f"Error processing Lambda function {function_name}: {str(e)}")

    def delete_iam_role(self):
        """Delete IAM role and associated policies"""
        role_name = "migration_planner_lambda_role"
        print(f"\nDeleting IAM role: {role_name}")
        
        try:
            # Check if role exists
            try:
                self.iam.get_role(RoleName=role_name)
            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchEntity':
                    print(f"IAM role {role_name} not found - may have been already deleted")
                    return
                raise

            # Detach managed policies
            try:
                attached_policies = self.iam.list_attached_role_policies(RoleName=role_name)
                for policy in attached_policies.get('AttachedPolicies', []):
                    self.iam.detach_role_policy(
                        RoleName=role_name,
                        PolicyArn=policy['PolicyArn']
                    )
                    print(f"Detached policy: {policy['PolicyArn']}")
            except Exception as e:
                print(f"Warning: Error detaching managed policies: {str(e)}")

            # Delete inline policies
            try:
                inline_policies = self.iam.list_role_policies(RoleName=role_name)
                for policy_name in inline_policies.get('PolicyNames', []):
                    self.iam.delete_role_policy(
                        RoleName=role_name,
                        PolicyName=policy_name
                    )
                    print(f"Deleted inline policy: {policy_name}")
            except Exception as e:
                print(f"Warning: Error deleting inline policies: {str(e)}")

            # Delete role
            time.sleep(5)  # Wait for policy detachments to propagate
            self.iam.delete_role(RoleName=role_name)
            print(f"Successfully deleted IAM role: {role_name}")
            
        except Exception as e:
            print(f"Error deleting IAM role: {str(e)}")

    def delete_dynamodb_table(self):
        """Delete DynamoDB table"""
        if 'table_name' not in self.infra_details:
            print("\nNo DynamoDB table found in infrastructure details")
            return

        table_name = self.infra_details['table_name']
        print(f"\nDeleting DynamoDB table: {table_name}")
        
        try:
            # Check if table exists
            try:
                self.dynamodb.describe_table(TableName=table_name)
            except ClientError as e:
                if e.response['Error']['Code'] == 'ResourceNotFoundException':
                    print(f"DynamoDB table {table_name} not found - may have been already deleted")
                    return
                raise

            self.dynamodb.delete_table(TableName=table_name)
            waiter = self.dynamodb.get_waiter('table_not_exists')
            print("Waiting for table deletion to complete...")
            waiter.wait(TableName=table_name)
            print(f"Successfully deleted DynamoDB table: {table_name}")
            
        except Exception as e:
            print(f"Error deleting DynamoDB table: {str(e)}")

    def delete_s3_bucket(self):
        """Delete S3 bucket and its contents"""
        if 'bucket_name' not in self.infra_details:
            print("\nNo S3 bucket found in infrastructure details")
            return

        bucket_name = self.infra_details['bucket_name']
        print(f"\nDeleting S3 bucket: {bucket_name}")
        
        try:
            # Check if bucket exists
            try:
                self.s3.head_bucket(Bucket=bucket_name)
            except ClientError as e:
                error_code = int(e.response['Error']['Code'])
                if error_code == 404:
                    print(f"S3 bucket {bucket_name} not found - may have been already deleted")
                    return
                raise

            # Delete all objects and versions
            try:
                paginator = self.s3.get_paginator('list_object_versions')
                objects_to_delete = []
                
                for page in paginator.paginate(Bucket=bucket_name):
                    # Handle versions
                    if 'Versions' in page:
                        for version in page['Versions']:
                            objects_to_delete.append({
                                'Key': version['Key'],
                                'VersionId': version['VersionId']
                            })
                    
                    # Handle delete markers
                    if 'DeleteMarkers' in page:
                        for marker in page['DeleteMarkers']:
                            objects_to_delete.append({
                                'Key': marker['Key'],
                                'VersionId': marker['VersionId']
                            })
                    
                    if objects_to_delete:
                        self.s3.delete_objects(
                            Bucket=bucket_name,
                            Delete={'Objects': objects_to_delete}
                        )
                        print(f"Deleted {len(objects_to_delete)} objects/versions")
                        objects_to_delete = []
            
            except Exception as e:
                print(f"Warning: Error deleting bucket contents: {str(e)}")

            # Delete the bucket
            self.s3.delete_bucket(Bucket=bucket_name)
            print(f"Successfully deleted S3 bucket: {bucket_name}")
            
        except Exception as e:
            print(f"Error deleting S3 bucket: {str(e)}")

    def cleanup(self):
        """Perform complete cleanup of infrastructure"""
        print("Starting infrastructure cleanup...")
        
        if not self.infra_details:
            print("No infrastructure details found. Nothing to clean up.")
            return
        
        # Delete resources in reverse order of creation
        self.delete_cloudwatch_alarms()
        self.delete_api_gateway()
        self.delete_lambda_functions()
        self.delete_iam_role()
        self.delete_dynamodb_table()
        self.delete_s3_bucket()
        
        # Delete infrastructure details file
        try:
            os.remove('infrastructure_details.json')
            print("\nDeleted infrastructure details file")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"\nWarning: Could not delete infrastructure details file: {str(e)}")
        
        print("\nCleanup completed!")

def confirm_cleanup():
    """Get user confirmation before cleanup"""
    response = input("\nWARNING: This will delete all resources created by the infrastructure script.\nAre you sure you want to proceed? (yes/no): ")
    return response.lower() in ['yes', 'y']

def main():
    if not check_aws_credentials():
        print("Error: AWS credentials not found or not configured.")
        print("Please run 'aws configure' to set up your AWS credentials.")
        return
    
    if not confirm_cleanup():
        print("Cleanup cancelled.")
        return
    
    try:
        cleanup = InfrastructureCleanup()
        cleanup.cleanup()
        print("\nAll resources have been successfully cleaned up!")
        
    except Exception as e:
        print(f"\nError during cleanup: {str(e)}")
        print("Please check the error message and try again.")

if __name__ == "__main__":
    main()