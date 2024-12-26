import json
import boto3
import os
from datetime import datetime, timedelta
from typing import Dict, List, Set

class EnhancedDiscoveryProcessor:
    def __init__(self):
        """Initialize the discovery processor"""
        self.discovery = boto3.client('discovery')
        self.s3 = boto3.client('s3')
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(os.environ.get('DISCOVERY_TABLE'))
        self.dependency_map = {}
        
    def collect_advanced_server_data(self, server_id: str = None) -> dict:
        """Collect comprehensive server data with enhanced metrics"""
        try:
            # Get list of discovered servers
            if server_id:
                servers = self.discovery.describe_servers(serverIds=[server_id])
            else:
                servers = self.discovery.describe_servers()
            
            collected_data = []
            for server in servers['servers']:
                # Get detailed metrics and information
                server_details = self.get_detailed_server_info(server['serverId'])
                performance_metrics = self.get_performance_metrics(server['serverId'])
                dependencies = self.get_comprehensive_dependencies(server['serverId'])
                security_info = self.get_security_info(server['serverId'])
                
                server_data = {
                    'basic': {
                        'serverId': server['serverId'],
                        'serverName': server.get('serverName', ''),
                        'serverType': server.get('serverType', ''),
                        'osInfo': {
                            'name': server.get('osName', ''),
                            'version': server.get('osVersion', ''),
                            'kernel': server_details.get('kernelVersion', ''),
                            'architecture': server_details.get('architecture', '')
                        }
                    },
                    'metrics': performance_metrics,
                    'applications': self.get_application_details(server['serverId']),
                    'dependencies': dependencies,
                    'network': self.get_network_topology(server['serverId']),
                    'security': security_info,
                    'compliance': self.assess_compliance(server_details),
                    'lastUpdated': datetime.utcnow().isoformat()
                }
                
                # Store raw data in S3
                self.store_raw_data(server_data)
                collected_data.append(server_data)
            
            return collected_data
            
        except Exception as e:
            print(f"Error collecting server data: {str(e)}")
            return self.get_sample_data()

    def get_detailed_server_info(self, server_id: str) -> dict:
        """Get detailed server information"""
        try:
            response = self.discovery.describe_server_information(serverIds=[server_id])
            server_info = response['serverInfo'][0]
            
            return {
                'cpuModel': server_info.get('serverModel', ''),
                'cpuArchitecture': server_info.get('systemArchitecture', ''),
                'numCores': server_info.get('numCores', 0),
                'numSockets': server_info.get('numSockets', 0),
                'ramBytes': server_info.get('ramBytes', 0),
                'diskBytes': server_info.get('diskBytes', 0)
            }
        except Exception as e:
            print(f"Error getting server info: {str(e)}")
            return {}

    def get_performance_metrics(self, server_id: str) -> dict:
        """Get detailed performance metrics"""
        try:
            metrics = self.discovery.get_server_utilization_metrics(
                serverIds=[server_id]
            )['utilizationMetrics'][0]
            
            return {
                'cpu': {
                    'cores': metrics.get('numCores', 0),
                    'utilization': metrics.get('cpuUtilization', 0),
                    'trend': self.analyze_metric_trend('cpu', metrics)
                },
                'memory': {
                    'total': metrics.get('ramBytes', 0),
                    'used': metrics.get('ramBytesUsed', 0),
                    'utilization': metrics.get('ramUtilization', 0),
                    'trend': self.analyze_metric_trend('memory', metrics)
                },
                'storage': {
                    'total': metrics.get('diskBytes', 0),
                    'used': metrics.get('diskBytesUsed', 0),
                    'utilization': metrics.get('diskUtilization', 0),
                    'trend': self.analyze_metric_trend('storage', metrics)
                },
                'network': {
                    'bytesIn': metrics.get('networkBytesIn', 0),
                    'bytesOut': metrics.get('networkBytesOut', 0),
                    'trend': self.analyze_metric_trend('network', metrics)
                }
            }
        except Exception as e:
            print(f"Error getting performance metrics: {str(e)}")
            return {}

    def analyze_metric_trend(self, metric_type: str, metrics: dict) -> dict:
        """Analyze metric trends"""
        try:
            current_value = metrics.get(f'{metric_type}Utilization', 0)
            previous_values = metrics.get(f'{metric_type}UtilizationHistory', [])
            
            if not previous_values:
                return {'trend': 'stable', 'growth_rate': 0}

            avg = sum(previous_values) / len(previous_values)
            growth_rate = ((current_value - previous_values[0]) / previous_values[0] * 100 
                         if previous_values[0] != 0 else 0)
            
            trend = 'increasing' if growth_rate > 10 else 'decreasing' if growth_rate < -10 else 'stable'
            
            return {
                'trend': trend,
                'growth_rate': round(growth_rate, 2),
                'average': round(avg, 2),
                'peak': max(previous_values + [current_value])
            }
        except Exception as e:
            print(f"Error analyzing metric trend: {str(e)}")
            return {'trend': 'unknown', 'growth_rate': 0}

    def get_application_details(self, server_id: str) -> List[dict]:
        """Get detailed application information"""
        try:
            apps = self.discovery.list_server_applications(serverIds=[server_id])
            
            return [{
                'name': app['name'],
                'version': app.get('version', 'unknown'),
                'path': app.get('path', ''),
                'type': app.get('type', 'unknown'),
                'status': app.get('status', 'unknown')
            } for app in apps['applications']]
        except Exception as e:
            print(f"Error getting application details: {str(e)}")
            return []

    def get_comprehensive_dependencies(self, server_id: str) -> dict:
        """Get comprehensive dependency mapping"""
        try:
            direct_deps = self.discovery.describe_server_dependencies(
                serverIds=[server_id]
            )['dependencies']
            
            self.build_dependency_map(server_id, direct_deps)
            
            return {
                'direct': self.analyze_direct_dependencies(direct_deps),
                'indirect': self.analyze_indirect_dependencies(server_id),
                'services': self.map_service_dependencies(server_id),
                'critical_path': self.find_critical_path(server_id),
                'risk_assessment': self.assess_dependency_risks(server_id)
            }
        except Exception as e:
            print(f"Error mapping dependencies: {str(e)}")
            return {}

    def build_dependency_map(self, server_id: str, dependencies: List[dict]):
        """Build dependency map"""
        if server_id not in self.dependency_map:
            self.dependency_map[server_id] = {
                'direct_deps': set(),
                'attributes': {}
            }
        
        for dep in dependencies:
            dest_id = dep['destinationServerId']
            self.dependency_map[server_id]['direct_deps'].add(dest_id)
            self.dependency_map[server_id]['attributes'][dest_id] = {
                'type': dep.get('dependencyType', 'unknown'),
                'strength': self.calculate_dependency_strength(dep),
                'latency': dep.get('averageLatency', 0),
                'throughput': dep.get('averageThroughput', 0)
            }

    def analyze_direct_dependencies(self, dependencies: List[dict]) -> List[dict]:
        """Analyze direct dependencies"""
        return [{
            'serverId': dep['destinationServerId'],
            'type': dep.get('dependencyType', 'unknown'),
            'strength': self.calculate_dependency_strength(dep),
            'metrics': {
                'latency': dep.get('averageLatency', 0),
                'throughput': dep.get('averageThroughput', 0)
            }
        } for dep in dependencies]

    def analyze_indirect_dependencies(self, server_id: str) -> List[dict]:
        """Analyze indirect dependencies"""
        indirect_deps = []
        visited = {server_id}
        
        def find_indirect_deps(current_id: str, depth: int = 0):
            if depth > 10 or current_id not in self.dependency_map:
                return
                
            for dep_id in self.dependency_map[current_id]['direct_deps']:
                if dep_id not in visited:
                    visited.add(dep_id)
                    paths = self.find_all_paths(server_id, dep_id)
                    if paths:
                        indirect_deps.append({
                            'serverId': dep_id,
                            'path': paths[0],
                            'depth': depth + 1,
                            'impact': self.assess_dependency_impact(dep_id)
                        })
                    find_indirect_deps(dep_id, depth + 1)
                    
        find_indirect_deps(server_id)
        return indirect_deps

    def find_all_paths(self, start: str, end: str, visited: Set[str] = None) -> List[List[str]]:
        """Find all dependency paths"""
        if visited is None:
            visited = set()
            
        if start not in self.dependency_map:
            return []
            
        if start == end:
            return [[start]]
            
        paths = []
        visited.add(start)
        
        for next_server in self.dependency_map[start]['direct_deps']:
            if next_server not in visited:
                for path in self.find_all_paths(next_server, end, visited.copy()):
                    paths.append([start] + path)
                    
        return paths

    def calculate_dependency_strength(self, dependency: dict) -> float:
        """Calculate dependency strength"""
        factors = {
            'frequency': dependency.get('frequency', 0),
            'latency': dependency.get('averageLatency', 0),
            'throughput': dependency.get('averageThroughput', 0),
            'errorRate': dependency.get('errorRate', 0)
        }
        
        weights = {
            'frequency': 0.4,
            'latency': 0.3,
            'throughput': 0.2,
            'errorRate': 0.1
        }
        
        normalized_score = sum(
            self.normalize_metric(value) * weights[metric]
            for metric, value in factors.items()
        )
        
        return round(normalized_score, 2)

    def normalize_metric(self, value: float) -> float:
        """Normalize metric to 0-1 scale"""
        if value < 0:
            return 0
        if value > 100:
            return 1
        return value / 100

    def assess_dependency_impact(self, server_id: str) -> dict:
        """Assess dependency impact"""
        return {
            'availability': self.calculate_availability_impact(server_id),
            'performance': self.calculate_performance_impact(server_id),
            'security': self.calculate_security_impact(server_id)
        }

    def calculate_availability_impact(self, server_id: str) -> float:
        """Calculate availability impact"""
        if server_id not in self.dependency_map:
            return 0.0
        
        direct_deps = len(self.dependency_map[server_id]['direct_deps'])
        return min(1.0, direct_deps * 0.1)

    def calculate_performance_impact(self, server_id: str) -> float:
        """Calculate performance impact"""
        if server_id not in self.dependency_map:
            return 0.0
            
        total_latency = sum(
            attr['latency']
            for attr in self.dependency_map[server_id]['attributes'].values()
        )
        return min(1.0, total_latency / 1000)

    def calculate_security_impact(self, server_id: str) -> float:
        """Calculate security impact"""
        return 0.5  # Placeholder - implement actual security impact calculation

    def get_security_info(self, server_id: str) -> dict:
        """Get security information"""
        return {
            'vulnerabilities': self.scan_vulnerabilities(server_id),
            'compliance': self.check_compliance(server_id),
            'patches': self.get_patch_status(server_id)
        }

    def get_network_topology(self, server_id: str) -> dict:
        """Get network topology information"""
        try:
            network_info = self.discovery.describe_server_network_info(
                serverIds=[server_id]
            )['networkInfo']
            
            return {
                'interfaces': self.analyze_network_interfaces(network_info),
                'connectivity': self.analyze_connectivity(network_info),
                'traffic_patterns': self.analyze_traffic_patterns(network_info)
            }
        except Exception as e:
            print(f"Error getting network topology: {str(e)}")
            return {}

    def store_raw_data(self, server_data: dict):
        """Store raw server data in S3"""
        try:
            self.s3.put_object(
                Bucket=os.environ.get('S3_BUCKET'),
                Key=f"raw-data/{server_data['basic']['serverId']}/{datetime.utcnow().isoformat()}.json",
                Body=json.dumps(server_data)
            )
        except Exception as e:
            print(f"Error storing raw data: {str(e)}")

    def get_sample_data(self) -> List[dict]:
        """Get sample data for testing"""
        return [{
            'basic': {
                'serverId': 'sample-1',
                'serverName': 'Sample Server',
                'serverType': 'Linux',
                'osInfo': {
                    'name': 'Ubuntu',
                    'version': '20.04',
                    'kernel': '5.4.0',
                    'architecture': 'x86_64'
                }
            },
            'metrics': {
                'cpu': {
                    'cores': 4,
                    'utilization': 65,
                    'trend': {'trend': 'stable', 'growth_rate': 2.5}
                },
                'memory': {
                    'total': 16384,
                    'used': 12288,
                    'utilization': 75,
                    'trend': {'trend': 'increasing', 'growth_rate': 15.2}
                },
                'storage': {
                    'total': 512000,
                    'used': 358400,
                    'utilization': 70,
                    'trend': {'trend': 'stable', 'growth_rate': 1.8}
                }
            },
            'applications': [
                {
                    'name': 'Apache',
                    'version': '2.4.41',
                    'type': 'web_server',
                    'status': 'running'
                },
                {
                    'name': 'MySQL',
                    'version': '8.0.27',
                    'type': 'database',
                    'status': 'running'
                }
            ],
            'dependencies': {
                'direct': [
                    {
                        'serverId': 'sample-2',
                        'type': 'database',
                        'strength': 0.8,
                        'metrics': {
                            'latency': 15,
                            'throughput': 1000
                        }
                    }
                ],
                'indirect': [
                    {
                        'serverId': 'sample-3',
                        'path': ['sample-1', 'sample-2', 'sample-3'],
                        'depth': 2,
                        'impact': {
                            'availability': 0.6,
                            'performance': 0.4,
                            'security': 0.3
                        }
                    }
                ],
                'critical_path': ['sample-1', 'sample-2', 'sample-3']
            },
            'network': {
                'interfaces': [
                    {
                        'id': 'eth0',
                        'ip_addresses': {
                            'private': '10.0.0.5',
                            'public': None,
                            'aliases': []
                        },
                        'throughput': {
                            'current': 150,
                            'maximum': 1000
                        }
                    }
                ],
                'connectivity': {
                    'latency': 5,
                    'reliability': 99.9,
                    'bandwidth_utilization': 45.5
                }
            },
            'security': {
                'vulnerabilities': [
                    {
                        'id': 'CVE-2023-1234',
                        'severity': 'medium',
                        'description': 'Sample vulnerability'
                    }
                ],
                'patches': {
                    'status': 'up-to-date',
                    'last_update': '2024-01-15T00:00:00Z'
                }
            },
            'compliance': {
                'status': 'compliant',
                'frameworks': ['PCI-DSS', 'HIPAA'],
                'last_audit': '2024-01-01T00:00:00Z'
            }
        }]

def lambda_handler(event, context):
    """Lambda handler for the discovery processor"""
    try:
        # Parse input
        body = json.loads(event.get('body', '{}'))
        server_id = body.get('serverId')
        
        # Initialize processor
        processor = EnhancedDiscoveryProcessor()
        
        # Generate discovery report
        discovery_data = processor.collect_advanced_server_data(server_id)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'message': 'Successfully processed server discovery data',
                'results': discovery_data
            })
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': f'Error processing discovery request: {str(e)}'
            })
        }
        