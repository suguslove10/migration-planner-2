from flask import Flask, request, jsonify, send_from_directory
import boto3
import json
import os
from datetime import UTC, datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
import logging
from botocore.exceptions import ClientError
from decimal import Decimal

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app)

# Configuration
CONFIG = {
    'UPLOAD_FOLDER': 'uploads',
    'ALLOWED_EXTENSIONS': {'json'},
    'MAX_CONTENT_LENGTH': 16 * 1024 * 1024  # 16MB max file size
}

# Ensure upload directory exists
os.makedirs(CONFIG['UPLOAD_FOLDER'], exist_ok=True)

# Load AWS infrastructure details
try:
    backend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backend')
    infra_file = os.path.join(backend_dir, 'infrastructure_details.json')
    
    with open(infra_file, 'r') as f:
        INFRA_DETAILS = json.load(f)
    API_URL = INFRA_DETAILS['api_url']
    LAMBDA_FUNCTIONS = INFRA_DETAILS['lambda_functions']
    REGION = INFRA_DETAILS.get('region', 'us-east-1')
    logger.info(f"Loaded infrastructure details for region: {REGION}")
except Exception as e:
    logger.warning(f"Failed to load infrastructure details: {str(e)}")
    API_URL = ''
    LAMBDA_FUNCTIONS = {}
    REGION = 'us-east-1'

# Initialize AWS clients
def init_aws_clients():
    try:
        return {
            'lambda': boto3.client('lambda', region_name=REGION),
            's3': boto3.client('s3', region_name=REGION),
            'dynamodb': boto3.client('dynamodb', region_name=REGION),
            'cloudwatch': boto3.client('cloudwatch', region_name=REGION),
            'discovery': boto3.client('discovery', region_name=REGION)
        }
    except Exception as e:
        logger.error(f"Failed to initialize AWS clients: {str(e)}")
        return {}

aws_clients = init_aws_clients()

# Utility Functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in CONFIG['ALLOWED_EXTENSIONS']

def handle_aws_error(e, operation):
    error_msg = f"AWS operation '{operation}' failed: {str(e)}"
    logger.error(error_msg)
    if isinstance(e, ClientError):
        error_code = e.response['Error']['Code']
        if error_code == 'ResourceNotFoundException':
            return jsonify({'error': 'Resource not found'}), 404
        elif error_code == 'ValidationException':
            return jsonify({'error': 'Validation error'}), 400
        elif error_code == 'ThrottlingException':
            return jsonify({'error': 'AWS request throttled'}), 429
    return jsonify({'error': 'Internal server error'}), 500

# Route Handlers
@app.route('/')
def index():
    """Serve the main application page"""
    return send_from_directory('templates', 'index.html')

@app.route('/static/<path:path>')
def serve_static(path):
    """Serve static files"""
    return send_from_directory('static', path)

@app.route('/api/check-config', methods=['GET'])
def check_config():
    """Check AWS configuration status"""
    try:
        # Verify AWS credentials and services
        aws_configured = all([
            bool(aws_clients.get('lambda')),
            bool(aws_clients.get('s3')),
            bool(aws_clients.get('dynamodb'))
        ])
        
        # Check service availability
        service_status = {
            'lambda': check_lambda_availability(),
            's3': check_s3_availability(),
            'dynamodb': check_dynamodb_availability()
        }
        
        return jsonify({
            'configured': aws_configured,
            'mode': 'aws' if aws_configured else 'test',
            'region': REGION,
            'services': service_status
        })
    except Exception as e:
        logger.error(f"Configuration check failed: {str(e)}")
        return jsonify({
            'configured': False,
            'mode': 'test',
            'error': str(e)
        })

def check_lambda_availability():
    """Check Lambda service availability"""
    try:
        aws_clients['lambda'].list_functions(MaxItems=1)
        return True
    except Exception:
        return False

def check_s3_availability():
    """Check S3 service availability"""
    try:
        aws_clients['s3'].list_buckets()
        return True
    except Exception:
        return False

def check_dynamodb_availability():
    """Check DynamoDB service availability"""
    try:
        aws_clients['dynamodb_client'].list_tables(Limit=1)
        return True
    except Exception:
        return False

@app.route('/api/servers', methods=['GET'])
def get_servers():
    """Get server list from test data or AWS Discovery Service"""
    try:
        servers = []
        # First try to load from test-server.json
        if os.path.exists('test-server.json'):
            with open('test-server.json', 'r') as f:
                data = json.load(f)
                servers = data.get('servers', [])
        # If no test data and AWS configured, try Discovery Service
        elif aws_clients.get('discovery'):
            try:
                response = aws_clients['discovery'].describe_agents()
                for agent in response.get('agentInfos', []):
                    server_details = get_server_details(agent['agentId'])
                    if server_details:
                        servers.append(server_details)
            except Exception as e:
                logger.error(f"Error getting servers from Discovery Service: {str(e)}")
        
        return jsonify({'servers': servers})
    except Exception as e:
        logger.error(f"Error getting servers: {str(e)}")
        return jsonify({'error': str(e)}), 500

def get_server_details(server_id):
    """Get detailed server information"""
    try:
        response = aws_clients['discovery'].describe_server_information(
            serverIds=[server_id]
        )['serverInfo'][0]
        
        return {
            'serverId': server_id,
            'serverName': response.get('serverName', ''),
            'serverType': response.get('serverType', ''),
            'metrics': {
                'cpu': {
                    'cores': response.get('numCores', 0),
                    'utilization': 0
                },
                'memory': {
                    'total': response.get('ramBytes', 0),
                    'used': 0
                },
                'storage': {
                    'total': response.get('diskBytes', 0),
                    'used': 0
                }
            }
        }
    except Exception as e:
        logger.error(f"Error getting server details: {str(e)}")
        return None

@app.route('/api/analyze', methods=['POST'])
def analyze_server():
    """Analyze server for migration using AWS Lambda"""
    try:
        data = request.json
        server_id = data.get('serverId')
        
        if not server_id:
            return jsonify({'error': 'Server ID is required'}), 400

        try:
            # Get server data from test-server.json
            with open('test-server.json', 'r') as f:
                server_data = json.load(f)
                server = next((s for s in server_data.get('servers', []) 
                             if s.get('serverId') == server_id), None)
                
                if not server:
                    return jsonify({'error': 'Server not found'}), 404

            # Analyze server metrics
            cpu_util = server['metrics']['cpu']['utilization']
            memory_util = (server['metrics']['memory']['used'] / server['metrics']['memory']['total']) * 100
            storage_util = (server['metrics']['storage']['used'] / server['metrics']['storage']['total']) * 100

            # Determine complexity based on utilization
            avg_util = (cpu_util + memory_util + storage_util) / 3
            if avg_util > 75:
                complexity_level = 'High'
                complexity_score = 8.5
            elif avg_util > 50:
                complexity_level = 'Medium'
                complexity_score = 6.5
            else:
                complexity_level = 'Low'
                complexity_score = 4.5

            # Create analysis result
            analysis_result = {
                'complexity': {
                    'level': complexity_level,
                    'score': complexity_score,
                    'description': f'{complexity_level} complexity based on resource utilization patterns'
                },
                'migrationStrategy': {
                    'strategy': 'Rehost',
                    'risk_level': complexity_level,
                    'description': 'Standard lift-and-shift migration recommended based on server profile'
                },
                'dependencies': [
                    {
                        'name': 'Application Dependencies',
                        'type': 'Service',
                        'description': f'Server using {cpu_util}% CPU, {memory_util:.1f}% Memory, {storage_util:.1f}% Storage'
                    }
                ]
            }

            # Log analysis details
            logger.info(f"Analyzed server {server_id}: Complexity {complexity_level}")
            
            # Store results in DynamoDB if configured
            store_analysis_results(server_id, analysis_result)
            
            return jsonify({
                'message': 'Analysis completed successfully',
                'results': [analysis_result]
            })

        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            raise

    except Exception as e:
        logger.error(f"Error analyzing server: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/estimate', methods=['POST'])
def estimate_costs():
    """Enhanced cost estimation with detailed breakdown"""
    try:
        data = request.json
        server_id = data.get('serverId')
        
        if not server_id:
            return jsonify({'error': 'Server ID is required'}), 400

        # Get server data
        with open('test-server.json', 'r') as f:
            server_data = json.load(f)
            server = next((s for s in server_data.get('servers', []) 
                         if s.get('serverId') == server_id), None)

        if not server:
            return jsonify({'error': 'Server not found'}), 404

        # Calculate detailed costs
        metrics = server['metrics']
        cpu_util = metrics['cpu']['utilization']
        memory_used = metrics['memory']['used']
        storage_used = metrics['storage']['used']

        # Compute costs
        compute_costs = {
            'instance': {
                'type': 't3.medium',
                'monthlyCost': 500.00 * (cpu_util / 100.0),
                'specs': {
                    'cpu': 2,
                    'memory': 4
                }
            },
            'optimization': {
                'recommendations': [
                    'Consider Reserved Instance for 40% additional savings',
                    'Implement auto-scaling for optimal resource usage'
                ]
            }
        }

        # Storage costs
        storage_costs = {
            'ebs': {
                'type': 'gp3',
                'sizeGB': round(storage_used / 1024),
                'monthlyCost': 300.00 * (storage_used / metrics['storage']['total'])
            },
            'backup': {
                'sizeGB': round(storage_used / 1024 / 2),
                'monthlyCost': 100.00 * (storage_used / metrics['storage']['total'])
            },
            'optimization': {
                'recommendations': [
                    'Use lifecycle policies for older data',
                    'Implement data tiering strategy'
                ]
            }
        }

        # Calculate totals
        current_monthly = float(compute_costs['instance']['monthlyCost'] + 
                              storage_costs['ebs']['monthlyCost'] +
                              storage_costs['backup']['monthlyCost'])
        
        projected_monthly = current_monthly * 0.7  # 30% savings
        monthly_savings = current_monthly - projected_monthly
        migration_cost = 5000.00

        # Enhanced cost estimate
        cost_estimate = {
            'summary': {
                'currentMonthlyCost': current_monthly,
                'projectedMonthlyCost': projected_monthly,
                'monthlySavings': monthly_savings,
                'migrationCost': migration_cost,
                'roiMonths': round(migration_cost / monthly_savings, 1),
                'threeYearSavings': (monthly_savings * 36) - migration_cost
            },
            'breakdown': {
                'compute': compute_costs,
                'storage': storage_costs
            },
            'optimization': {
                'potentialSavings': {
                    'reservedInstances': '40%',
                    'storageOptimization': '20%',
                    'rightSizing': '15%'
                },
                'recommendations': [
                    'Use Reserved Instances for predictable workloads',
                    'Implement auto-scaling for variable loads',
                    'Optimize storage with lifecycle policies',
                    'Consider spot instances for non-critical workloads'
                ]
            }
        }

        # Store the cost estimate
        store_cost_estimate(server_id, cost_estimate)
        return jsonify(cost_estimate)

    except Exception as e:
        logger.error(f"Error estimating costs: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/roadmap', methods=['POST'])
def generate_roadmap():
    """Generate comprehensive migration roadmap"""
    try:
        # Load servers data
        with open('test-server.json', 'r') as f:
            data = json.load(f)
            servers = data.get('servers', [])

        if not servers:
            return jsonify({'error': 'No servers available for roadmap'}), 400

        # Calculate total effort based on server complexity
        total_effort = sum(480 for server in servers)  # Base 480 hours per server
        current_date = datetime.now(UTC)

        # Generate comprehensive roadmap with all phases
        roadmap = {
            'timeline': [
                {
                    'name': 'Assessment Phase',
                    'duration': '2 weeks',
                    'startDate': current_date.strftime('%Y-%m-%d'),
                    'endDate': (current_date + timedelta(days=14)).strftime('%Y-%m-%d'),
                    'tasks': [
                        'Infrastructure assessment',
                        'Dependency mapping',
                        'Risk assessment',
                        'Cost analysis'
                    ],
                    'risks': [
                        {
                            'severity': 'Medium',
                            'description': 'Dependency mapping complexity'
                        }
                    ],
                    'milestones': [
                        'Assessment documentation complete',
                        'Dependencies identified'
                    ]
                },
                {
                    'name': 'Planning Phase',
                    'duration': '3 weeks',
                    'startDate': (current_date + timedelta(days=15)).strftime('%Y-%m-%d'),
                    'endDate': (current_date + timedelta(days=35)).strftime('%Y-%m-%d'),
                    'tasks': [
                        'Architecture design',
                        'Migration strategy',
                        'Resource allocation',
                        'Timeline planning'
                    ],
                    'risks': [
                        {
                            'severity': 'Low',
                            'description': 'Resource availability'
                        }
                    ],
                    'milestones': [
                        'Migration strategy approved',
                        'Resource plan finalized'
                    ]
                },
                {
                    'name': 'Migration Phase',
                    'duration': '4 weeks',
                    'startDate': (current_date + timedelta(days=36)).strftime('%Y-%m-%d'),
                    'endDate': (current_date + timedelta(days=63)).strftime('%Y-%m-%d'),
                    'tasks': [
                        'Environment setup',
                        'Data migration',
                        'Application migration',
                        'Configuration transfer'
                    ],
                    'risks': [
                        {
                            'severity': 'High',
                            'description': 'Data transfer complexity'
                        },
                        {
                            'severity': 'Medium',
                            'description': 'Application compatibility'
                        }
                    ],
                    'milestones': [
                        'Environment ready',
                        'Data migration complete',
                        'Applications migrated'
                    ]
                },
                {
                    'name': 'Testing Phase',
                    'duration': '2 weeks',
                    'startDate': (current_date + timedelta(days=64)).strftime('%Y-%m-%d'),
                    'endDate': (current_date + timedelta(days=77)).strftime('%Y-%m-%d'),
                    'tasks': [
                        'Functionality testing',
                        'Performance testing',
                        'Security testing',
                        'User acceptance testing'
                    ],
                    'risks': [
                        {
                            'severity': 'Medium',
                            'description': 'Performance issues'
                        }
                    ],
                    'milestones': [
                        'All tests passed',
                        'UAT sign-off received'
                    ]
                },
                {
                    'name': 'Cutover Phase',
                    'duration': '1 week',
                    'startDate': (current_date + timedelta(days=78)).strftime('%Y-%m-%d'),
                    'endDate': (current_date + timedelta(days=84)).strftime('%Y-%m-%d'),
                    'tasks': [
                        'Final data sync',
                        'DNS cutover',
                        'Go-live verification',
                        'Post-migration monitoring'
                    ],
                    'risks': [
                        {
                            'severity': 'High',
                            'description': 'Service disruption during cutover'
                        }
                    ],
                    'milestones': [
                        'Production cutover complete',
                        'System operational'
                    ]
                }
            ],
            'projectSummary': {
                'duration': '84 days',
                'totalServers': len(servers),
                'totalEffort': total_effort,
                'criticalPath': [server['serverName'] for server in servers],
                'progress': {
                    'assessment': 0,
                    'planning': 0,
                    'migration': 0,
                    'testing': 0,
                    'cutover': 0
                }
            }
        }

        return jsonify(roadmap)

    except Exception as e:
        logger.error(f"Error generating roadmap: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload-test-data', methods=['POST'])
def upload_test_data():
    """Handle test data file upload"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
            
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(CONFIG['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            try:
                # Read and validate the uploaded file
                with open(filepath, 'r') as f:
                    test_data = json.load(f)
                
                # Validate server data structure
                if 'servers' not in test_data:
                    raise ValueError("Invalid file format: missing 'servers' key")

                for server in test_data['servers']:
                    required_fields = ['serverId', 'serverName', 'metrics']
                    for field in required_fields:
                        if field not in server:
                            raise ValueError(f"Invalid server data: missing '{field}' field")
                    
                    # Validate metrics structure
                    metrics = server['metrics']
                    required_metrics = ['cpu', 'memory', 'storage']
                    for metric in required_metrics:
                        if metric not in metrics:
                            raise ValueError(f"Invalid metrics data: missing '{metric}' metrics")
                
                # Save validated data to test-server.json
                with open('test-server.json', 'w') as f:
                    json.dump(test_data, f, indent=2)
                
                # Clean up uploaded file
                os.remove(filepath)
                
                return jsonify({
                    'message': 'Test data uploaded successfully',
                    'servers': test_data['servers']
                })
                
            except json.JSONDecodeError:
                return jsonify({'error': 'Invalid JSON file'}), 400
            except ValueError as ve:
                return jsonify({'error': str(ve)}), 400
        
        return jsonify({'error': 'Invalid file type'}), 400

    except Exception as e:
        logger.error(f"Error uploading test data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/free-tier-usage', methods=['GET'])
def get_free_tier_usage():
   """Get AWS Free Tier usage metrics with real-time tracking"""
   try:
       cloudwatch = aws_clients['cloudwatch']
       end_time = datetime.now(UTC)
       start_time = end_time - timedelta(days=30)
       
       # Get Lambda invocations
       lambda_metrics = cloudwatch.get_metric_statistics(
           Namespace='AWS/Lambda',
           MetricName='Invocations',
           StartTime=start_time,
           EndTime=end_time,
           Period=2592000,  # 30 days in seconds
           Statistics=['Sum']
       )
       lambda_usage = sum(point['Sum'] for point in lambda_metrics.get('Datapoints', []))

       # Get S3 storage
       s3_metrics = cloudwatch.get_metric_statistics(
           Namespace='AWS/S3',
           MetricName='BucketSizeBytes',
           StartTime=start_time,
           EndTime=end_time,
           Period=86400,  # Daily
           Statistics=['Average']
       )
       s3_usage = sum(point['Average'] for point in s3_metrics.get('Datapoints', [])) / (1024**3)  # Convert to GB

       # Get DynamoDB storage
       dynamodb_metrics = cloudwatch.get_metric_statistics(
           Namespace='AWS/DynamoDB',
           MetricName='TableSizeBytes',
           StartTime=start_time,
           EndTime=end_time,
           Period=86400,
           Statistics=['Average']
       )
       dynamodb_usage = sum(point['Average'] for point in dynamodb_metrics.get('Datapoints', [])) / (1024**3)

       # Get API Gateway calls
       api_metrics = cloudwatch.get_metric_statistics(
           Namespace='AWS/ApiGateway',
           MetricName='Count',
           StartTime=start_time,
           EndTime=end_time,
           Period=2592000,
           Statistics=['Sum']
       )
       api_usage = sum(point['Sum'] for point in api_metrics.get('Datapoints', []))

       return jsonify({
           'lambda': {
               'used': int(lambda_usage),
               'limit': 1000000  # 1M requests/month
           },
           's3': {
               'used': round(s3_usage, 2),
               'limit': 5  # 5GB
           },
           'dynamodb': {
               'used': round(dynamodb_usage, 2),
               'limit': 25  # 25GB
           },
           'apiGateway': {
               'used': int(api_usage),
               'limit': 1000000  # 1M requests/month
           }
       })

   except Exception as e:
       logger.error(f"Error getting Free Tier usage: {str(e)}")
       return jsonify({
           'lambda': {'used': 0, 'limit': 1000000},
           's3': {'used': 0, 'limit': 5},
           'dynamodb': {'used': 0, 'limit': 25},
           'apiGateway': {'used': 0, 'limit': 1000000}
       })
def get_lambda_usage(start_time, end_time):
    """Get Lambda invocations usage"""
    try:
        response = aws_clients['cloudwatch'].get_metric_statistics(
            Namespace='AWS/Lambda',
            MetricName='Invocations',
            Dimensions=[],
            StartTime=start_time,
            EndTime=end_time,
            Period=2592000,  # 30 days
            Statistics=['Sum']
        )
        
        used = sum(point['Sum'] for point in response['Datapoints'])
        return {
            'used': int(used),
            'limit': 1000000  # Free Tier limit
        }
    except Exception as e:
        logger.error(f"Error getting Lambda usage: {str(e)}")
        return {'used': 0, 'limit': 1000000}

def get_s3_usage():
    """Get S3 storage usage"""
    try:
        response = aws_clients['cloudwatch'].get_metric_statistics(
            Namespace='AWS/S3',
            MetricName='BucketSizeBytes',
            Dimensions=[],
            StartTime=datetime.now(UTC) - timedelta(days=1),
            EndTime=datetime.now(UTC),
            Period=86400,
            Statistics=['Average']
        )
        
        used = sum(point['Average'] for point in response['Datapoints']) / (1024 ** 3)  # Convert to GB
        return {
            'used': round(used, 2),
            'limit': 5  # Free Tier limit in GB
        }
    except Exception as e:
        logger.error(f"Error getting S3 usage: {str(e)}")
        return {'used': 0, 'limit': 5}

def get_dynamodb_usage():
    """Get DynamoDB storage usage"""
    try:
        response = aws_clients['cloudwatch'].get_metric_statistics(
            Namespace='AWS/DynamoDB',
            MetricName='TableSizeBytes',
            Dimensions=[],
            StartTime=datetime.now(UTC) - timedelta(days=1),
            EndTime=datetime.now(UTC),
            Period=86400,
            Statistics=['Average']
        )
        
        used = sum(point['Average'] for point in response['Datapoints']) / (1024 ** 3)  # Convert to GB
        return {
            'used': round(used, 2),
            'limit': 25  # Free Tier limit in GB
        }
    except Exception as e:
        logger.error(f"Error getting DynamoDB usage: {str(e)}")
        return {'used': 0, 'limit': 25}

def get_api_gateway_usage(start_time, end_time):
    """Get API Gateway requests usage"""
    try:
        response = aws_clients['cloudwatch'].get_metric_statistics(
            Namespace='AWS/ApiGateway',
            MetricName='Count',
            Dimensions=[],
            StartTime=start_time,
            EndTime=end_time,
            Period=2592000,  # 30 days
            Statistics=['Sum']
        )
        
        used = sum(point['Sum'] for point in response['Datapoints'])
        return {
            'used': int(used),
            'limit': 1000000  # Free Tier limit
        }
    except Exception as e:
        logger.error(f"Error getting API Gateway usage: {str(e)}")
        return {'used': 0, 'limit': 1000000}

def store_analysis_results(server_id, results):
    """Store server analysis results in DynamoDB"""
    try:
        table = aws_clients['dynamodb'].Table(INFRA_DETAILS.get('table_name', 'migration-assessments'))
        
        item = {
            'serverId': server_id,
            'timestamp': datetime.now(UTC).isoformat(),
            'type': 'analysis',
            'results': json.loads(json.dumps(results), parse_float=Decimal),
            'ttl': int((datetime.now(UTC) + timedelta(days=90)).timestamp())
        }
        
        table.put_item(Item=item)
        logger.info(f"Stored analysis results for server {server_id}")
    except Exception as e:
        logger.error(f"Failed to store analysis results: {str(e)}")

def store_cost_estimate(server_id, estimate):
    """Store cost estimation results in DynamoDB"""
    try:
        table = aws_clients['dynamodb'].Table(INFRA_DETAILS.get('table_name', 'migration-assessments'))
        
        # Convert all Decimal values to strings for storage
        estimate_copy = json.loads(
            json.dumps(estimate),
            parse_float=str
        )
        
        item = {
            'serverId': server_id,
            'timestamp': datetime.now(UTC).isoformat(),
            'type': 'cost_estimate',
            'estimate': estimate_copy,
            'ttl': int((datetime.now(UTC) + timedelta(days=90)).timestamp())
        }
        
        table.put_item(Item=item)
        logger.info(f"Stored cost estimate for server {server_id}")
    except Exception as e:
        logger.error(f"Failed to store cost estimate: {str(e)}")

# Error Handlers
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Resource not found'}), 404

@app.errorhandler(400)
def bad_request_error(error):
    return jsonify({'error': 'Bad request'}), 400

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Enable CORS for development
    from flask_cors import CORS
    CORS(app)
    
    # Start the application
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True)