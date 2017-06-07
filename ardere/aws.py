"""AWS Helper Classes"""
import logging
import os
import time
import uuid
from collections import defaultdict

import boto3
import botocore
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple  # noqa

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Setup script paths
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_dir_path = os.path.dirname(dir_path)
wait_script_path = os.path.join(parent_dir_path, "src", "shell",
                                "waitforcluster.sh")
telegraf_script_path = os.path.join(parent_dir_path, "src", "shell",
                                    "telegraf.toml")
metric_create_script = os.path.join(parent_dir_path, "ardere", "scripts",
                                    "metric_creator.py")

# EC2 userdata to setup values on load
# Settings for net.ipv4 settings based on:
#    http://stackoverflow.com/questions/410616/increasing-the-maximum-number-of-tcp-ip-connections-in-linux
# Other settings are from operations on kernel tweaks they've done to handle
# large socket conditions.
EC2_USER_DATA = """#!/bin/bash
echo ECS_CLUSTER='{ecs_name}' >> /etc/ecs/ecs.config
sysctl net.core.rmem_default=8388608
sysctl net.core.rmem_max=16777216
sysctl net.core.wmem_max=16777216
sysctl net.core.netdev_max_backlog=2500
sysctl net.core.somaxconn=3240000
sysctl net.netfilter.nf_conntrack_tcp_timeout_established=600
sysctl net.nf_conntrack_max=1000000
sysctl net.ipv4.ip_local_port_range="1024 65535"
sysctl net.ipv4.netfilter.ip_conntrack_max=4999999
sysctl net.ipv4.netfilter.ip_conntrack_tcp_timeout_time_wait=1
sysctl net.ipv4.netfilter.ip_conntrack_tcp_timeout_established=54000
sysctl net.ipv4.tcp_fin_timeout=5
sysctl net.ipv4.tcp_keepalive_time=30
sysctl net.ipv4.tcp_keepalive_intvl=15
sysctl net.ipv4.tcp_keepalive_probes=6
sysctl net.ipv4.tcp_window_scaling=1
sysctl net.ipv4.tcp_rmem="4096 87380 16777216"
sysctl net.ipv4.tcp_wmem="4096 65536 16777216"
sysctl net.ipv4.tcp_mem="786432 1048576 26777216"
sysctl net.ipv4.tcp_max_tw_buckets=360000
sysctl net.ipv4.tcp_max_syn_backlog=3240000
sysctl net.ipv4.tcp_max_tw_buckets=1440000
sysctl net.ipv4.tcp_slow_start_after_idle=0
sysctl net.ipv4.tcp_retries2=5
sysctl net.ipv4.tcp_tw_recycle=1
sysctl net.ipv4.tcp_tw_reuse=1
sysctl vm.min_free_kbytes=65536
sysctl -w fs.file-max=1000000
ulimit -n 1000000
"""

# List tracking vcpu's of all instance types for cpu unit reservations
# We are intentionally leaving out the following instance types as they're
# considered overkill for load-testing purposes or any instance req's we have
# experienced so far:
#     P2, G2, F1, I3, D2
ec2_type_by_vcpu = {
    1: ["t2.nano", "t2.micro", "t2.small", "m3.medium"],
    2: ["t2.medium", "t2.large", "m3.large", "m4.large", "c3.large",
        "c4.large", "r3.large", "r4.large"],
    4: ["t2.xlarge", "m3.xlarge", "m4.xlarge", "c3.xlarge", "c4.xlarge",
        "r3.xlarge", "r4.xlarge"],
    8: ["t2.2xlarge", "m3.2xlarge", "m4.2xlarge", "c3.2xlarge", "c4.2xlarge",
        "r3.2xlarge", "r4.2xlarge"],
    16: ["m4.4xlarge", "c3.4xlarge", "c4.4xlarge", "r3.4xlarge", "r4.4xlarge"],
    32: ["c3.8xlarge", "r3.8xlarge", "r4.8xlarge"],
    36: ["c4.8xlarge"],
    40: ["m4.10xlarge"],
    64: ["m4.16xlarge", "x1.16xlarge", "r4.16xlarge"],
    128: ["x1.32xlarge"]
}

# Build a list of vcpu's by instance type
ec2_vcpu_by_type = {}
for vcpu, instance_types in ec2_type_by_vcpu.items():
    for instance_type in instance_types:
        ec2_vcpu_by_type[instance_type] = vcpu


def cpu_units_for_instance_type(instance_type):
    # type: (str) -> int
    """Calculate how many CPU units to allocate for an instance_type

    We calculate cpu_units as 1024 * vcpu's for each instance to allocate
    almost the entirety of the instance's cpu units to the load-testing
    container. We take out 512 to ensure some leftover capacity for other
    utility containers we run with the load-testing container.

    """
    return (ec2_vcpu_by_type[instance_type] * 1024) - 512


class ECSManager(object):
    """ECS Manager queries and manages an ECS cluster"""
    # For testing purposes
    boto = boto3

    # ECS optimized AMI id's
    ecs_ami_ids = {
        "us-east-1": "ami-275ffe31",
        "us-east-2": "ami-62745007",
        "us-west-1": "ami-689bc208",
        "us-west-2": "ami-62d35c02"
    }

    influxdb_container = "influxdb:1.2-alpine"
    telegraf_container = "telegraf:1.2-alpine"
    grafana_container = "grafana/grafana:4.1.2"
    python_container = "jfloff/alpine-python:2.7-slim"

    _wait_script = None
    _telegraf_script = None
    _metric_create_script = None

    def __init__(self, plan):
        # type: (Dict[str, Any]) -> None
        """Create and return a ECSManager for a cluster of the given name."""
        self._ecs_client = self.boto.client('ecs')
        self._ec2_client = self.boto.client('ec2')
        self._ecs_name = plan["ecs_name"]
        self._plan = plan

        # Pull out the env vars
        self.s3_ready_bucket = os.environ["s3_ready_bucket"]
        self.container_log_group = os.environ["container_log_group"]
        self.ecs_profile = os.environ["ecs_profile"]

        if "plan_run_uuid" not in plan:
            plan["plan_run_uuid"] = uuid.uuid4().hex

        self._plan_uuid = plan["plan_run_uuid"]

    @property
    def wait_script(self):
        if not self._wait_script:
            with open(wait_script_path, 'r') as f:
                self._wait_script = f.read()
        return self._wait_script

    @property
    def telegraf_script(self):
        if not self._telegraf_script:
            with open(telegraf_script_path, 'r') as f:
                self._telegraf_script = f.read()
        return self._telegraf_script

    @property
    def metric_create_script(self):
        if not self._metric_create_script:
            with open(metric_create_script, 'r') as f:
                self._metric_create_script = f.read()
        return self._metric_create_script

    @property
    def plan_uuid(self):
        return self._plan_uuid

    @property
    def s3_ready_file(self):
        return "https://s3.amazonaws.com/{bucket}/{key}".format(
            bucket=self.s3_ready_bucket,
            key="{}.ready".format(self._plan_uuid)
        )

    @property
    def log_config(self):
        return {
            "logDriver": "awslogs",
            "options": {"awslogs-group": self.container_log_group,
                        "awslogs-region": "us-east-1",
                        "awslogs-stream-prefix":
                            "ardere-{}".format(self.plan_uuid)
                        }
        }

    @property
    def influx_db_name(self):
        return "run-{}".format(self.plan_uuid)

    @property
    def grafana_admin_user(self):
        return self._plan["metrics_options"]["dashboard"]["admin_user"]

    @property
    def grafana_admin_password(self):
        return self._plan["metrics_options"]["dashboard"]["admin_password"]

    def family_name(self, step):
        # type: (Dict[str, Any]) -> str
        """Generate a consistent family name for a given step"""
        return step["name"] + "-" + self._plan_uuid

    def metrics_family_name(self):
        # type: () -> str
        """Generate a consistent metrics family name"""
        return "{}-metrics".format(self._ecs_name)

    def metrics_setup_family_name(self):
        # type: () -> str
        """Generate a consistent metric setup family name"""
        return "{}-metrics-setup".format(self._ecs_name)

    def query_active_instances(self, additional_tags=None):
        # type: (Optional[Dict[str, str]]) -> Dict[str, int]
        """Query EC2 for all the instances owned by ardere for this cluster."""
        instance_dict = defaultdict(int)
        paginator = self._ec2_client.get_paginator('describe_instances')
        filters = {"Owner": "ardere", "ECSCluster": self._ecs_name}
        if additional_tags:
            filters.update(additional_tags)
        response_iterator = paginator.paginate(
            Filters=[
                {
                    "Name": "tag:{}".format(tag_name),
                    "Values": [tag_value]
                } for tag_name, tag_value in filters.items()
            ]
        )
        for page in response_iterator:
            for reservation in page["Reservations"]:
                for instance in reservation["Instances"]:
                    # Determine if the instance is pending/running and count
                    # 0 = Pending, 16 = Running, > is all shutting down, etc.
                    if instance["State"]["Code"] <= 16:
                        instance_dict[instance["InstanceType"]] += 1
        return instance_dict

    def calculate_missing_instances(self, desired, current):
        # type: (Dict[str, int], Dict[str, int]) -> Dict[str, int]
        """Determine how many of what instance types are needed to ensure
        the current instance dict has all the desired instance count/types."""
        needed = {}
        for instance_type, instance_count in desired.items():
            cur = current.get(instance_type, 0)
            if cur < instance_count:
                needed[instance_type] = instance_count - cur
        return needed

    def has_metrics_node(self, instance_type):
        # type: (str) -> bool
        """Return whether a metrics node with this instance type exists"""
        instances = self.query_active_instances(
            additional_tags=dict(Role="metrics")
        )
        return instance_type in instances

    def has_started_metric_creation(self):
        # type: () -> bool
        """Return whether the metric creation container was started"""
        response = self._ecs_client.list_tasks(
            cluster=self._ecs_name,
            startedBy=self.plan_uuid
        )
        return bool(response["taskArns"])

    def has_finished_metric_creation(self):
        # type: () -> bool
        """Return whether the metric creation container has finished"""
        response = self._ecs_client.list_tasks(
            cluster=self._ecs_name,
            startedBy=self.plan_uuid,
            desiredStatus="STOPPED"
        )
        return bool(response["taskArns"])

    def request_instances(self, instances, security_group_ids,
                          additional_tags=None):
        # type: (Dict[str, int], List[str], Optional[Dict[str, str]]) -> None
        """Create requested types/quantities of instances for this cluster"""
        ami_id = self.ecs_ami_ids["us-east-1"]
        tags = dict(Name=self._ecs_name, Owner="ardere",
                    ECSCluster=self._ecs_name)
        if additional_tags:
            tags.update(additional_tags)
        for instance_type, instance_count in instances.items():
            self._ec2_client.run_instances(
                ImageId=ami_id,
                MinCount=instance_count,
                MaxCount=instance_count,
                InstanceType=instance_type,
                UserData=EC2_USER_DATA.format(ecs_name=self._ecs_name),
                IamInstanceProfile={"Arn": self.ecs_profile},
                SecurityGroupIds=security_group_ids,
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [
                            dict(Key=tag_name, Value=tag_value)
                            for tag_name, tag_value in tags.items()
                        ]
                    }
                ]
            )

    def locate_metrics_container_ip(self):
        # type: () -> Tuple[Optional[str], Optional[str]]
        """Locates the metrics container IP and container instance arn

        Returns a tuple of (public_ip, container_arn)

        """
        response = self._ecs_client.list_container_instances(
            cluster=self._ecs_name,
            filter="task:group == service:metrics"
        )
        if not response["containerInstanceArns"]:
            return None, None

        container_arn = response["containerInstanceArns"][0]
        response = self._ecs_client.describe_container_instances(
            cluster=self._ecs_name,
            containerInstances=[container_arn]
        )

        container_instance = response["containerInstances"][0]
        ec2_instance_id = container_instance["ec2InstanceId"]
        instance = self.boto.resource("ec2").Instance(ec2_instance_id)
        return instance.private_ip_address, container_arn

    def locate_metrics_service(self):
        # type: () -> Optional[str]
        """Locate and return the metrics service arn if any"""
        response = self._ecs_client.describe_services(
            cluster=self._ecs_name,
            services=["metrics"]
        )
        if response["services"] and response["services"][0]["status"] == \
                "ACTIVE":
            return response["services"][0]
        else:
            return None

    def create_metrics_service(self, options):
        # type: (Dict[str, Any]) -> Dict[str, Any]
        """Creates an ECS service to run InfluxDB and Grafana for metric
        reporting and returns its info"""
        logger.info("Creating InfluxDB service with options: {}".format(
            options))

        cmd = """\
        export GF_DEFAULT_INSTANCE_NAME=`wget -qO- http://169.254.169.254/latest/meta-data/instance-id` && \
        export GF_SECURITY_ADMIN_USER=%s && \
        export GF_SECURITY_ADMIN_PASSWORD=%s && \
        export GF_USERS_ALLOW_SIGN_UP=false && \
        mkdir "${GF_DASHBOARDS_JSON_PATH}" && \
        ./run.sh
        """ % (self.grafana_admin_user, self.grafana_admin_password)  # noqa
        cmd = ['sh', '-c', '{}'.format(cmd)]

        gf_env = {
            "GF_DASHBOARDS_JSON_ENABLED": "true",
            "GF_DASHBOARDS_JSON_PATH": "/var/lib/grafana/dashboards",
            "__ARDERE_GRAFANA_URL__":
                "http://admin:admin@localhost:3000/api/datasources"
        }

        # Setup the task definition for setting up influxdb/grafana instances
        # per run
        mc_cmd = """\
        pip install influxdb requests boto3 && \
        echo "${__ARDERE_PYTHON_SCRIPT__}" > setup_db.py && \
        python setup_db.py
        """
        mc_cmd = ['sh', '-c', '{}'.format(mc_cmd)]
        self._ecs_client.register_task_definition(
            family=self.metrics_setup_family_name(),
            containerDefinitions=[
                {
                    "name": "metricsetup",
                    "image": self.python_container,
                    "cpu": 128,
                    "entryPoint": mc_cmd,
                    "memoryReservation": 256,
                    "privileged": True,
                    "logConfiguration": self.log_config
                }
            ],
            networkMode="host"
        )

        task_response = self._ecs_client.register_task_definition(
            family=self.metrics_family_name(),
            containerDefinitions=[
                {
                    "name": "influxdb",
                    "image": self.influxdb_container,
                    "cpu": cpu_units_for_instance_type(
                        options["instance_type"]),
                    "memoryReservation": 256,
                    "privileged": True,
                    "portMappings": [
                        {"containerPort": 8086},
                        {"containerPort": 8088}
                    ],
                    "logConfiguration": self.log_config
                },
                {
                    "name": "grafana",
                    "image": self.grafana_container,
                    "cpu": 256,
                    "memoryReservation": 256,
                    "entryPoint": cmd,
                    "portMappings": [
                        {"containerPort": 3000}
                    ],
                    "privileged": True,
                    "environment": [
                        {"name": key, "value": value} for key, value in
                        gf_env.items()
                    ],
                    "logConfiguration": self.log_config
                }
            ],
            # use host network mode for optimal performance
            networkMode="host",

            placementConstraints=[
                # Ensure the service is confined to the right instance type
                {
                    "type": "memberOf",
                    "expression": "attribute:ecs.instance-type == {}".format(
                        options["instance_type"]),
                }
            ],
        )
        task_arn = task_response["taskDefinition"]["taskDefinitionArn"]
        service_result = self._ecs_client.create_service(
            cluster=self._ecs_name,
            serviceName="metrics",
            taskDefinition=task_arn,
            desiredCount=1,
            deploymentConfiguration={
                "minimumHealthyPercent": 0,
                "maximumPercent": 100
            },
            placementConstraints=[
                {
                    "type": "distinctInstance"
                }
            ]
        )
        service_arn = service_result["service"]["serviceArn"]
        return dict(task_arn=task_arn, service_arn=service_arn)

    def run_metric_creation_task(self, container_instance, grafana_auth,
                                 dashboard=None,
                                 dashboard_name=None):
        # type: (str, Tuple[str, str], Optional[str], Optional[str]) -> None
        """Starts the metric creation task"""
        env = {
            "__ARDERE_GRAFANA_USER__": grafana_auth[0],
            "__ARDERE_GRAFANA_PASS__": grafana_auth[1],
            "__ARDERE_PYTHON_SCRIPT__": self.metric_create_script,
            "__ARDERE_INFLUXDB_NAME__": self.influx_db_name
        }

        if dashboard:
            env["__ARDERE_DASHBOARD__"] = dashboard
            env["__ARDERE_DASHBOARD_NAME__"] = dashboard_name

        self._ecs_client.start_task(
            cluster=self._ecs_name,
            taskDefinition=self.metrics_setup_family_name(),
            overrides={
                'containerOverrides': [
                    {
                        "name": "metricsetup",
                        "environment": [
                            {"name": key, "value": value} for key, value in
                            env.items()
                        ]
                    }
                ]
            },
            containerInstances=[container_instance],
            startedBy=self.plan_uuid
        )

    def create_service(self, step):
        # type: (Dict[str, Any]) -> Dict[str, Any]
        """Creates an ECS service for a step and returns its info"""
        logger.info("CreateService called with: {}".format(step))

        # Prep the shell command
        wfc_var = '__ARDERE_WAITFORCLUSTER_SH__'
        wfc_cmd = 'sh -c "${}" waitforcluster.sh {} {}'.format(
            wfc_var,
            self.s3_ready_file,
            step.get("run_delay", 0)
        )
        service_cmd = step["cmd"]
        cmd = ['sh', '-c', '{} && {}'.format(wfc_cmd, service_cmd)]

        # Prep the env vars
        env_vars = [{"name": wfc_var, "value": self.wait_script}]
        for name, value in step.get("env", {}).items():
            env_vars.append({"name": name, "value": value})

        # ECS wants a family name for task definitions, no spaces, 255 chars
        family_name = step["name"] + "-" + self._plan_uuid

        # Use cpu_unit if provided, otherwise monopolize
        cpu_units = step.get(
            "cpu_units",
            cpu_units_for_instance_type(step["instance_type"])
        )

        # Setup the container definition
        container_def = {
            "name": step["name"],
            "image": step["container_name"],
            "cpu": cpu_units,

            # using only memoryReservation sets no hard limit
            "memoryReservation": 256,
            "privileged": True,
            "environment": env_vars,
            "entryPoint": cmd,
            "ulimits": [
                dict(name="nofile", softLimit=1000000, hardLimit=1000000)
            ],
            "logConfiguration": self.log_config
        }
        if "port_mapping" in step:
            ports = [{"containerPort": port} for port in step["port_mapping"]]
            container_def["portMappings"] = ports

        # Setup the telegraf container definition
        cmd = """\
        echo "${__ARDERE_TELEGRAF_CONF__}" > /etc/telegraf/telegraf.conf && \
        export __ARDERE_TELEGRAF_HOST__=`wget -qO- http://169.254.169.254/latest/meta-data/instance-id` && \
        telegraf \
        """  # noqa
        cmd = ['sh', '-c', '{}'.format(cmd)]
        telegraf_def = {
            "name": "telegraf",
            "image": self.telegraf_container,
            "cpu": 512,
            "memoryReservation": 256,
            "entryPoint": cmd,
            "portMappings": [
                {"containerPort": 8125}
            ],
            "privileged": True,
            "environment": [
                {"name": "__ARDERE_TELEGRAF_CONF__",
                 "value": self.telegraf_script},
                {"name": "__ARDERE_TELEGRAF_STEP__",
                 "value": step["name"]},
                {"name": "__ARDERE_INFLUX_ADDR__",
                 "value": "{}:8086".format(self._plan["influxdb_private_ip"])},
                {"name": "__ARDERE_INFLUX_DB__",
                 "value": self.influx_db_name},
                {"name": "__ARDERE_TELEGRAF_TYPE__",
                 "value": step["docker_series"]}
            ],
            "logConfiguration": self.log_config
        }

        task_response = self._ecs_client.register_task_definition(
            family=family_name,
            containerDefinitions=[
                container_def,
                telegraf_def
            ],
            # use host network mode for optimal performance
            networkMode="host",

            placementConstraints=[
                # Ensure the service is confined to the right instance type
                {
                    "type": "memberOf",
                    "expression": "attribute:ecs.instance-type == {}".format(
                        step["instance_type"]),
                }
            ]
        )
        task_arn = task_response["taskDefinition"]["taskDefinitionArn"]
        step["taskArn"] = task_arn
        service_result = self._ecs_client.create_service(
            cluster=self._ecs_name,
            serviceName=step["name"],
            taskDefinition=task_arn,
            desiredCount=step["instance_count"],
            deploymentConfiguration={
                "minimumHealthyPercent": 0,
                "maximumPercent": 100
            },
            placementConstraints=[
                {
                    "type": "distinctInstance"
                }
            ]
        )
        step["serviceArn"] = service_result["service"]["serviceArn"]
        step["service_status"] = "STARTED"
        return step

    def create_services(self, steps):
        # type: (List[Dict[str, Any]]) -> None
        """Create ECS Services given a list of steps"""
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(self.create_service, steps))

    def service_ready(self, step):
        # type: (Dict[str, Any]) -> bool
        """Query a service and return whether all its tasks are running"""
        service_name = step["name"]
        response = self._ecs_client.describe_services(
            cluster=self._ecs_name,
            services=[service_name]
        )

        try:
            deploy = response["services"][0]["deployments"][0]
        except (TypeError, IndexError):
            return False
        return deploy["desiredCount"] == deploy["runningCount"]

    def all_services_ready(self, steps):
        # type: (List[Dict[str, Any]]) -> bool
        """Queries all service ARN's in the plan to see if they're ready"""
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = executor.map(self.service_ready, steps)
        return all(results)

    def service_done(self, step):
        # type: (Dict[str, Any]) -> bool
        """Query a service to return whether its fully drained and back to
        INACTIVE"""
        service_name = step["name"]
        response = self._ecs_client.describe_services(
            cluster=self._ecs_name,
            services=[service_name]
        )

        service = response["services"][0]
        return service["status"] == "INACTIVE"

    def all_services_done(self, steps):
        # type: (List[Dict[str, Any]]) -> bool
        """Queries all service ARN's in the plan to see if they're fully
        DRAINED and now INACTIVE"""
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = executor.map(self.service_done, steps)
        return all(results)

    def stop_finished_service(self, start_time, step):
        # type: (start_time, Dict[str, Any]) -> None
        """Stops a service if it needs to shutdown"""
        if step["service_status"] == "STOPPED":
            return

        # Calculate time
        step_duration = step.get("run_delay", 0) + step["run_max_time"]
        now = time.time()
        if now < (start_time + step_duration):
            return

        # Running long enough to shutdown
        self._ecs_client.update_service(
            cluster=self._ecs_name,
            service=step["name"],
            desiredCount=0
        )
        step["service_status"] = "STOPPED"

    def stop_finished_services(self, start_time, steps):
        # type: (int, List[Dict[str, Any]]) -> None
        """Shuts down any services that have run for their max time"""
        for step in steps:
            self.stop_finished_service(start_time, step)

    def shutdown_plan(self, steps):
        # type: (List[Dict[str, Any]]) -> None
        """Terminate the entire plan, ensure all services and task
        definitions are completely cleaned up and removed"""
        # Locate all the services for the ECS Cluster
        paginator = self._ecs_client.get_paginator('list_services')
        response_iterator = paginator.paginate(
            cluster=self._ecs_name
        )

        # Collect all the service ARN's
        service_arns = []
        for page in response_iterator:
            service_arns.extend(page["serviceArns"])

        # Avoid shutting down metrics if tear down was not requested
        # We have to exclude it from the services discovered above if we
        # should NOT tear it down
        if not self._plan["metrics_options"]["tear_down"]:
            metric_service = self.locate_metrics_service()
            if metric_service and metric_service["serviceArn"] in service_arns:
                service_arns.remove(metric_service["serviceArn"])

        for service_arn in service_arns:
            try:
                self._ecs_client.update_service(
                    cluster=self._ecs_name,
                    service=service_arn,
                    desiredCount=0
                )
            except botocore.exceptions.ClientError:
                continue

            try:
                self._ecs_client.delete_service(
                    cluster=self._ecs_name,
                    service=service_arn
                )
            except botocore.exceptions.ClientError:
                pass

        # Locate all the task definitions for this plan
        step_family_names = [self.family_name(step) for step in steps]

        # Add in the metrics family name if we need to tear_down
        if self._plan["metrics_options"]["tear_down"]:
            step_family_names.append(self.metrics_family_name())
            step_family_names.append(self.metrics_setup_family_name())

        for family_name in step_family_names:
            try:
                response = self._ecs_client.describe_task_definition(
                    taskDefinition=family_name
                )
            except botocore.exceptions.ClientError:
                continue

            task_arn = response["taskDefinition"]["taskDefinitionArn"]

            # Deregister the task
            try:
                self._ecs_client.deregister_task_definition(
                    taskDefinition=task_arn
                )
            except botocore.exceptions.ClientError:
                pass
