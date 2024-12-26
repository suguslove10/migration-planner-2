import json
import boto3
import os
from datetime import datetime
from decimal import Decimal

class CostEstimator:
    def __init__(self, region='ap-south-1'):
        self.region = region
        # Updated pricing for Mumbai region (ap-south-1)
        self.pricing = {
            'compute': {
                'ec2': {
                    't3.micro': {'cpu': 2, 'memory': 1, 'hourly': 0.0113, 'freeTierHours': 750},
                    't3.small': {'cpu': 2, 'memory': 2, 'hourly': 0.0226},
                    't3.medium': {'cpu': 2, 'memory': 4, 'hourly': 0.0452},
                    't3.large': {'cpu': 2, 'memory': 8, 'hourly': 0.0904},
                    'm5.large': {'cpu': 2, 'memory': 8, 'hourly': 0.1060},
                    'c5.large': {'cpu': 2, 'memory': 4, 'hourly': 0.0890},
                    'r5.large': {'cpu': 2, 'memory': 16, 'hourly': 0.1330}
                },
                'lambda': {
                    'price_per_request': 0.0000002,
                    'price_per_gb_second': 0.0000166667,
                    'free_tier_requests': 1000000,
                    'free_tier_compute': 400000  # GB-seconds
                }
            },
            'storage': {
                's3': {
                    'standard': {
                        'storage_per_gb': 0.0230,
                        'put_request': 0.0055,  # per 1000 requests
                        'get_request': 0.00043,  # per 1000 requests
                        'free_tier_storage': 5,  # GB
                        'free_tier_requests': 2000  # PUT/COPY/POST/LIST requests
                    },
                    'ia': {
                        'storage_per_gb': 0.0125,
                        'retrieval_per_gb': 0.01
                    }
                },
                'ebs': {
                    'gp3': {
                        'storage_per_gb': 0.0924,
                        'iops_base': 3000,  # Included IOPS
                        'throughput_base': 125,  # Included MB/s
                        'additional_iops': 0.0058,  # per IOPS
                        'additional_throughput': 0.0462  # per MB/s
                    },
                    'io1': {
                        'storage_per_gb': 0.1425,
                        'iops_per_gb': 0.0657
                    }
                }
            },
            'database': {
                'rds': {
                    'mysql': {
                        'db.t3.micro': {'hourly': 0.0170, 'freeTierHours': 750},
                        'db.t3.small': {'hourly': 0.0340},
                        'db.t3.medium': {'hourly': 0.0680}
                    }
                },
                'dynamodb': {
                    'write_unit': 0.00148,  # per WCU per hour
                    'read_unit': 0.00029,   # per RCU per hour
                    'storage_per_gb': 0.285,
                    'free_tier_storage': 25,  # GB
                    'free_tier_wcu': 25,
                    'free_tier_rcu': 25
                }
            },
            'network': {
                'data_transfer_out': {
                    'first_1gb': 0.00,
                    'up_to_10tb': 0.126,
                    'next_40tb': 0.122,
                    'next_100tb': 0.119
                },
                'data_transfer_in': 0.00
            }
        }

    def estimate_compute_costs(self, server_specs, use_free_tier=True):
        """Estimate EC2 and Lambda costs based on server specifications"""
        cpu_cores = server_specs['metrics']['cpu']['cores']
        memory_gb = server_specs['metrics']['memory']['total'] / 1024  # Convert MB to GB
        utilization = server_specs['metrics']['cpu']['utilization'] / 100

        # Find suitable instance type
        suitable_instances = []
        for instance_type, specs in self.pricing['compute']['ec2'].items():
            if specs['cpu'] >= cpu_cores and specs['memory'] >= memory_gb:
                suitable_instances.append((instance_type, specs))

        if not suitable_instances:
            # Default to largest available if no suitable instance found
            instance_type = 'r5.large'
            specs = self.pricing['compute']['ec2']['r5.large']
        else:
            # Choose most cost-effective instance
            instance_type, specs = min(suitable_instances, key=lambda x: x[1]['hourly'])

        # Calculate monthly cost
        monthly_hours = 730  # Average hours per month
        base_monthly_cost = specs['hourly'] * monthly_hours

        # Apply Free Tier if applicable
        if use_free_tier and 'freeTierHours' in specs:
            free_hours = min(specs['freeTierHours'], monthly_hours)
            base_monthly_cost = max(0, specs['hourly'] * (monthly_hours - free_hours))

        # Add costs for Lambda functions (if needed for application components)
        estimated_lambda_requests = 50000  # Estimated monthly requests
        estimated_lambda_duration = 500  # Estimated average duration in ms
        estimated_lambda_memory = 128  # Estimated memory in MB

        lambda_compute_gb_seconds = (
            estimated_lambda_requests * 
            estimated_lambda_duration / 1000 * 
            estimated_lambda_memory / 1024
        )

        lambda_costs = 0
        if use_free_tier:
            # Apply Free Tier for Lambda
            lambda_requests = max(0, estimated_lambda_requests - 
                                self.pricing['compute']['lambda']['free_tier_requests'])
            lambda_compute = max(0, lambda_compute_gb_seconds - 
                               self.pricing['compute']['lambda']['free_tier_compute'])
        else:
            lambda_requests = estimated_lambda_requests
            lambda_compute = lambda_compute_gb_seconds

        lambda_costs = (
            lambda_requests * self.pricing['compute']['lambda']['price_per_request'] +
            lambda_compute * self.pricing['compute']['lambda']['price_per_gb_second']
        )

        return {
            'instanceType': instance_type,
            'monthlyComputeCost': round(base_monthly_cost + lambda_costs, 2),
            'details': {
                'ec2': {
                    'instanceType': instance_type,
                    'monthlyCost': round(base_monthly_cost, 2),
                    'specs': {
                        'cpu': specs['cpu'],
                        'memory': specs['memory']
                    }
                },
                'lambda': {
                    'estimatedRequests': estimated_lambda_requests,
                    'estimatedCompute': round(lambda_compute_gb_seconds, 2),
                    'monthlyCost': round(lambda_costs, 2)
                }
            }
        }

    def estimate_storage_costs(self, storage_specs, use_free_tier=True):
        """Estimate storage costs across different storage types"""
        storage_gb = storage_specs['metrics']['storage']['total'] / 1024  # Convert MB to GB
        used_storage_gb = storage_specs['metrics']['storage']['used'] / 1024
        
        # Calculate EBS costs
        ebs_costs = self._calculate_ebs_costs(storage_gb)
        
        # Calculate S3 costs
        estimated_s3_storage = storage_gb * 0.3  # Estimate 30% of data going to S3
        s3_costs = self._calculate_s3_costs(estimated_s3_storage, use_free_tier)
        
        # Calculate backup storage costs
        backup_storage = storage_gb * 0.5  # Estimate 50% of data needs backup
        backup_costs = self._calculate_backup_costs(backup_storage)

        return {
            'monthly': {
                'ebs': ebs_costs,
                's3': s3_costs,
                'backup': backup_costs,
                'total': round(
                    ebs_costs['monthlyCost'] + 
                    s3_costs['monthlyCost'] + 
                    backup_costs['monthlyCost'], 
                    2
                )
            },
            'details': {
                'totalStorageGB': storage_gb,
                'usedStorageGB': used_storage_gb,
                'distribution': {
                    'ebs': storage_gb,
                    's3': estimated_s3_storage,
                    'backup': backup_storage
                }
            }
        }

    def _calculate_ebs_costs(self, storage_gb):
        """Calculate EBS storage costs"""
        if storage_gb <= 150:
            # Use gp3 for smaller volumes
            storage_type = 'gp3'
            base_cost = storage_gb * self.pricing['storage']['ebs']['gp3']['storage_per_gb']
            iops_cost = 0  # First 3000 IOPS included
            throughput_cost = 0  # First 125 MB/s included
        else:
            # Use io1 for larger volumes
            storage_type = 'io1'
            base_cost = storage_gb * self.pricing['storage']['ebs']['io1']['storage_per_gb']
            # Estimate IOPS needed (1 IOPS per GB up to 50 IOPS/GB)
            iops = min(storage_gb * 30, storage_gb * 50)
            iops_cost = iops * self.pricing['storage']['ebs']['io1']['iops_per_gb']

        return {
            'type': storage_type,
            'sizeGB': storage_gb,
            'monthlyCost': round(base_cost + iops_cost, 2),
            'details': {
                'storageCost': round(base_cost, 2),
                'iopsCost': round(iops_cost, 2) if 'iops_cost' in locals() else 0,
                'throughputCost': round(throughput_cost, 2) if 'throughput_cost' in locals() else 0
            }
        }

    def _calculate_s3_costs(self, storage_gb, use_free_tier=True):
        """Calculate S3 storage and request costs"""
        # Estimate request patterns
        estimated_put_requests = 10000  # Monthly PUT/COPY/POST/LIST requests
        estimated_get_requests = 50000  # Monthly GET requests

        # Apply Free Tier if applicable
        if use_free_tier:
            storage_gb = max(0, storage_gb - self.pricing['storage']['s3']['standard']['free_tier_storage'])
            put_requests = max(0, estimated_put_requests - 
                             self.pricing['storage']['s3']['standard']['free_tier_requests'])
        else:
            put_requests = estimated_put_requests

        # Calculate costs
        storage_cost = storage_gb * self.pricing['storage']['s3']['standard']['storage_per_gb']
        put_cost = (put_requests / 1000) * self.pricing['storage']['s3']['standard']['put_request']
        get_cost = (estimated_get_requests / 1000) * self.pricing['storage']['s3']['standard']['get_request']

        return {
            'monthlyCost': round(storage_cost + put_cost + get_cost, 2),
            'details': {
                'storage': {
                    'sizeGB': storage_gb,
                    'cost': round(storage_cost, 2)
                },
                'requests': {
                    'put': estimated_put_requests,
                    'get': estimated_get_requests,
                    'cost': round(put_cost + get_cost, 2)
                }
            }
        }

    def _calculate_backup_costs(self, storage_gb):
        """Calculate backup storage costs using S3 IA"""
        # Use S3 IA pricing for backups
        storage_cost = storage_gb * self.pricing['storage']['s3']['ia']['storage_per_gb']
        # Estimate one full retrieval per month
        retrieval_cost = storage_gb * self.pricing['storage']['s3']['ia']['retrieval_per_gb']

        return {
            'monthlyCost': round(storage_cost + retrieval_cost, 2),
            'details': {
                'storage': {
                    'sizeGB': storage_gb,
                    'cost': round(storage_cost, 2)
                },
                'retrieval': {
                    'cost': round(retrieval_cost, 2)
                }
            }
        }

    def estimate_database_costs(self, server_specs, use_free_tier=True):
        """Estimate database costs if applicable"""
        # Check if server runs database workloads
        has_database = any(app.lower().find('sql') != -1 
                         for app in server_specs.get('applications', []))

        if not has_database:
            return {
                'monthly': 0,
                'details': {'required': False}
            }

        # Estimate RDS costs
        memory_gb = server_specs['metrics']['memory']['total'] / 1024
        storage_gb = server_specs['metrics']['storage']['total'] / 1024

        # Choose appropriate instance type
        if memory_gb <= 1:
            instance_type = 'db.t3.micro'
        elif memory_gb <= 2:
            instance_type = 'db.t3.small'
        else:
            instance_type = 'db.t3.medium'

        # Calculate monthly cost
        monthly_hours = 730
        instance_cost = (
            self.pricing['database']['rds']['mysql'][instance_type]['hourly'] * 
            monthly_hours
        )

        # Apply Free Tier if applicable
        if (use_free_tier and 
            'freeTierHours' in self.pricing['database']['rds']['mysql'][instance_type]):
            free_hours = self.pricing['database']['rds']['mysql'][instance_type]['freeTierHours']
            instance_cost = max(0, 
                self.pricing['database']['rds']['mysql'][instance_type]['hourly'] * 
                (monthly_hours - free_hours)
            )

        # Calculate storage cost
        storage_cost = storage_gb * self.pricing['storage']['ebs']['gp3']['storage_per_gb']

        return {
            'monthly': round(instance_cost + storage_cost, 2),
            'details': {
                'required': True,
                'instance': {
                    'type': instance_type,
                    'monthlyCost': round(instance_cost, 2)
                },
                'storage': {
                    'sizeGB': storage_gb,
                    'monthlyCost': round(storage_cost, 2)
                }
            }
        }

    def estimate_network_costs(self, server_specs):
        """Estimate network transfer costs"""
        # Estimate monthly data transfer based on server metrics
        # Assume 20% of storage is transferred out monthly
        storage_gb = server_specs['metrics']['storage']['total'] / 1024
        estimated_transfer_gb = storage_gb * 0.20

        # Calculate tiered costs
        remaining_transfer = estimated_transfer_gb
        total_cost = 0
        cost_breakdown = {}

        # Free first 1 GB
        if remaining_transfer > 1:
            cost_breakdown['first_1gb'] = {
                'gb': 1,
                'cost': 0
            }
            remaining_transfer -= 1
        else:
            cost_breakdown['first_1gb'] = {
                'gb': remaining_transfer,
                'cost': 0
            }
            remaining_transfer = 0

        # Up to 10 TB
        if remaining_transfer > 0:
            transfer_in_tier = min(remaining_transfer, 10240)  # 10 TB in GB
            cost = transfer_in_tier * self.pricing['network']['data_transfer_out']['up_to_10tb']
            cost_breakdown['up_to_10tb'] = {
                'gb': transfer_in_tier,
                'cost': round(cost, 2)
            }
            total_cost += cost
            remaining_transfer -= transfer_in_tier

        # Next 40 TB
        if remaining_transfer > 0:
            transfer_in_tier = min(remaining_transfer, 40960)  # 40 TB in GB
            cost = transfer_in_tier * self.pricing['network']['data_transfer_out']['next_40tb']
            cost_breakdown['next_40tb'] = {
                'gb': transfer_in_tier,
                'cost': round(cost, 2)
            }
            total_cost += cost
            remaining_transfer -= transfer_in_tier

        # Next 100 TB
        if remaining_transfer > 0:
            transfer_in_tier = min(remaining_transfer, 102400)  # 100 TB in GB
            cost = transfer_in_tier * self.pricing['network']['data_transfer_out']['next_100tb']
            cost_breakdown['next_100tb'] = {
                'gb': transfer_in_tier,
                'cost': round(cost, 2)
            }
            total_cost += cost

        # Estimate inter-AZ transfer costs (if applicable)
        has_dependencies = len(server_specs.get('dependencies', [])) > 0
        inter_az_transfer = storage_gb * 0.05 if has_dependencies else 0  # Estimate 5% inter-AZ transfer
        inter_az_cost = inter_az_transfer * 0.01  # $0.01 per GB for inter-AZ transfer

        return {
            'monthly': round(total_cost + inter_az_cost, 2),
            'details': {
                'dataTransferOut': {
                    'estimatedGB': estimated_transfer_gb,
                    'breakdown': cost_breakdown,
                    'totalCost': round(total_cost, 2)
                },
                'interAZ': {
                    'estimatedGB': inter_az_transfer,
                    'cost': round(inter_az_cost, 2)
                }
            }
        }

    def calculate_total_cost(self, server_specs, use_free_tier=True):
        """Calculate total monthly costs across all service categories"""
        compute_costs = self.estimate_compute_costs(server_specs, use_free_tier)
        storage_costs = self.estimate_storage_costs(server_specs, use_free_tier)
        database_costs = self.estimate_database_costs(server_specs, use_free_tier)
        network_costs = self.estimate_network_costs(server_specs)

        # Calculate one-time migration costs
        migration_costs = self._estimate_migration_costs(server_specs)

        # Calculate total monthly cost
        monthly_total = (
            compute_costs['monthlyComputeCost'] +
            storage_costs['monthly']['total'] +
            database_costs['monthly'] +
            network_costs['monthly']
        )

        # Calculate three-year TCO
        three_year_tco = (monthly_total * 36) + migration_costs['total']

        # Generate savings analysis
        on_prem_costs = self._estimate_on_prem_costs(server_specs)
        monthly_savings = on_prem_costs['monthly'] - monthly_total
        three_year_savings = (monthly_savings * 36) - migration_costs['total']

        return {
            'currency': 'USD',
            'monthly': {
                'compute': compute_costs['monthlyComputeCost'],
                'storage': storage_costs['monthly']['total'],
                'database': database_costs['monthly'],
                'network': network_costs['monthly'],
                'total': round(monthly_total, 2)
            },
            'oneTime': migration_costs,
            'projected': {
                'threeYearTCO': round(three_year_tco, 2),
                'monthlySavings': round(monthly_savings, 2),
                'threeYearSavings': round(three_year_savings, 2),
                'paybackPeriodMonths': round(
                    migration_costs['total'] / monthly_savings if monthly_savings > 0 else float('inf'),
                    1
                )
            },
            'details': {
                'compute': compute_costs['details'],
                'storage': storage_costs['details'],
                'database': database_costs['details'],
                'network': network_costs['details']
            },
            'assumptions': {
                'freeTierEligible': use_free_tier,
                'onPremCosts': on_prem_costs,
                'exchangeRates': {
                    'USD_TO_INR': 83.0
                }
            }
        }

    def _estimate_migration_costs(self, server_specs):
        """Estimate one-time migration costs"""
        # Base migration cost per server
        base_cost = 5000  # Base cost in USD

        # Complexity multipliers
        complexity_multipliers = {
            'Low': 1.0,
            'Medium': 1.5,
            'High': 2.0
        }

        # Calculate complexity score
        complexity_score = 0
        
        # CPU utilization factor
        cpu_util = server_specs['metrics']['cpu']['utilization']
        if cpu_util > 80:
            complexity_score += 3
        elif cpu_util > 60:
            complexity_score += 2
        else:
            complexity_score += 1

        # Memory utilization factor
        memory_total = server_specs['metrics']['memory']['total']
        memory_used = server_specs['metrics']['memory']['used']
        memory_util = (memory_used / memory_total) * 100
        if memory_util > 80:
            complexity_score += 3
        elif memory_util > 60:
            complexity_score += 2
        else:
            complexity_score += 1

        # Storage factor
        storage_gb = server_specs['metrics']['storage']['total'] / 1024
        if storage_gb > 1000:
            complexity_score += 3
        elif storage_gb > 500:
            complexity_score += 2
        else:
            complexity_score += 1

        # Dependencies factor
        dependency_count = len(server_specs.get('dependencies', []))
        complexity_score += min(dependency_count, 3)

        # Determine complexity level
        if complexity_score > 8:
            complexity_level = 'High'
        elif complexity_score > 5:
            complexity_level = 'Medium'
        else:
            complexity_level = 'Low'

        # Calculate total migration cost
        migration_cost = base_cost * complexity_multipliers[complexity_level]

        # Add additional costs
        data_transfer_cost = (storage_gb * 0.1)  # Estimate data transfer cost
        testing_cost = migration_cost * 0.2  # Estimate testing cost
        training_cost = 1000  # Base training cost

        total_cost = migration_cost + data_transfer_cost + testing_cost + training_cost

        return {
            'total': round(total_cost, 2),
            'breakdown': {
                'baseMigration': round(migration_cost, 2),
                'dataTransfer': round(data_transfer_cost, 2),
                'testing': round(testing_cost, 2),
                'training': round(training_cost, 2)
            },
            'complexity': {
                'level': complexity_level,
                'score': complexity_score
            }
        }

    def _estimate_on_prem_costs(self, server_specs):
        """Estimate current on-premises costs"""
        # Hardware costs (amortized monthly)
        server_cost = 15000  # Average server cost
        server_lifetime_months = 36  # 3-year lifecycle
        monthly_hardware = server_cost / server_lifetime_months

        # Power and cooling
        power_cost_per_kwh = 0.15
        power_usage_kw = server_specs['metrics']['cpu']['cores'] * 0.1  # Estimate 100W per core
        monthly_power = power_usage_kw * 24 * 30 * power_cost_per_kwh

        # Maintenance and support (typically 20% of hardware cost annually)
        monthly_maintenance = (server_cost * 0.20) / 12

        # Data center costs (space, overhead)
        monthly_datacenter = 200  # Average monthly cost for rack space and overhead

        # Storage costs
        storage_gb = server_specs['metrics']['storage']['total'] / 1024
        monthly_storage = storage_gb * 0.10  # Estimate $0.10 per GB for on-prem storage

        # Labor costs
        monthly_labor = 500  # Estimated monthly labor cost per server

        total_monthly = (
            monthly_hardware +
            monthly_power +
            monthly_maintenance +
            monthly_datacenter +
            monthly_storage +
            monthly_labor
        )

        return {
            'monthly': round(total_monthly, 2),
            'breakdown': {
                'hardware': round(monthly_hardware, 2),
                'power': round(monthly_power, 2),
                'maintenance': round(monthly_maintenance, 2),
                'datacenter': round(monthly_datacenter, 2),
                'storage': round(monthly_storage, 2),
                'labor': round(monthly_labor, 2)
            }
        }

def lambda_handler(event, context):
    try:
        # Parse input
        body = json.loads(event.get('body', '{}'))
        server_data = body.get('serverData')
        
        if not server_data:
            raise ValueError("Server data is required")
            
        # Initialize cost estimator
        estimator = CostEstimator()
        
        # Get cost estimates
        use_free_tier = body.get('useFreeTier', True)
        cost_estimate = estimator.calculate_total_cost(server_data, use_free_tier)
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(cost_estimate)
        }
        
    except ValueError as e:
        return {
            'statusCode': 400,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': str(e)})
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({'error': f'Internal server error: {str(e)}'})
        }