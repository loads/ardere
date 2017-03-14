import logging
import os
import time
import uuid
from collections import defaultdict

import boto3
import botocore
from concurrent.futures import ThreadPoolExecutor

from typing import Any, Dict, List  # noqa


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Shell script to load
dir_path = os.path.dirname(os.path.realpath(__file__))
parent_dir_path = os.path.dirname(dir_path)
shell_path = os.path.join(parent_dir_path, "src", "shell",
                          "waitforcluster.sh")


class ServicesStartingException(Exception):
    """Exception to indicate Services are still Starting"""


class ShutdownPlanException(Exception):
    """Exception to indicate the Plan should be Shutdown"""


class ECSManager(object):
    """ECS Manager queries and manages an ECS cluster"""

    # ECS optimized AMI id's
    ecs_ami_ids = {
        "us-east-1": "ami-b2df2ca4",
        "us-east-2": "ami-832b0ee6",
        "us-west-1": "ami-dd104dbd",
        "us-west-2": "ami-022b9262"
    }

    def __init__(self, plan):
        # type: (str) -> None
        """Create and return a ECSManager for a cluster of the given name."""
        self._ecs_client = boto3.client('ecs')
        self._ec2_client = boto3.client('ec2')
        self._ecs_name = plan["ecs_name"]

        if "plan_run_uuid" not in plan:
            plan["plan_run_uuid"] = str(uuid.uuid4())

        self._plan_uuid = plan["plan_run_uuid"]

    @property
    def s3_ready_file(self):
        return "https://s3.amazonaws.com/{bucket}/{key}".format(
            bucket=os.environ["s3_ready_bucket"],
            key="{}.ready".format(self._plan_uuid)
        )

    def family_name(self, step):
        """Generate a consistent family name for a given step"""
        return step["name"] + "-" + self._plan_uuid

    def query_active_instances(self):
        # type: () -> Dict[str, int]
        """Query EC2 for all the instances owned by ardere for this cluster."""
        instance_dict = defaultdict(int)
        paginator = self._ec2_client.get_paginator('describe_instances')
        response_iterator = paginator.paginate(
            Filters=[
                {
                    "Name": "tag:Owner",
                    "Values": ["ardere"]
                },
                {
                    "Name": "tag:ECSCluster",
                    "Values": [self._ecs_name]
                }
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

    def request_instances(self, instances):
        # type: (Dict[str, int]) -> None
        """Create requested types/quantities of instances for this cluster"""
        ami_id = self.ecs_ami_ids["us-east-1"]
        request_instances = []
        for instance_type, instance_count in instances.items():
            result = self._ec2_client.run_instances(
                ImageId=ami_id,
                KeyName="loads",
                MinCount=instance_count,
                MaxCount=instance_count,
                InstanceType=instance_type,
                UserData="#!/bin/bash \necho ECS_CLUSTER='" + self._ecs_name +
                         "' >> /etc/ecs/ecs.config",
                IamInstanceProfile={"Arn": os.environ["ecs_profile"]}
            )

            # Track returned instances for tagging step
            request_instances.extend([x["InstanceId"] for x in
                                      result["Instances"]])

        self._ec2_client.create_tags(
            Resources=request_instances,
            Tags=[
                dict(Key="Owner", Value="ardere"),
                dict(Key="ECSCluster", Value=self._ecs_name)
            ]
        )

    def create_service(self, step):
        # type: (Dict[str, Any]) -> Dict[str, Any]
        """Creates an ECS service for a step and returns its info"""
        logger.info("CreateService called with: {}".format(step))

        # Load the shell script
        with open(shell_path, 'r') as f:
            shell_script = f.read()

        # Prep the shell command
        shell_command = [
            'sh', '-c', '"$WAITFORCLUSTER"',
            'waitforcluster.sh', self.s3_ready_file,
            str(step.get("run_delay", 0))
        ]
        shell_command2 = ' '.join(shell_command) + ' && ' + step[
            "additional_command_args"]
        shell_command3 = ['sh', '-c', '{}'.format(shell_command2)]

        # ECS wants a family name for task definitions, no spaces, 255 chars
        family_name = step["name"] + "-" + self._plan_uuid
        task_response = self._ecs_client.register_task_definition(
            family=family_name,
            containerDefinitions=[
                {
                    "name": step["name"],
                    "image": step["container_name"],
                    "cpu": step["cpu_units"],
                    # using only memoryReservation sets no hard limit
                    "memoryReservation": 256,
                    "environment": [
                        {
                            "name": "WAITFORCLUSTER",
                            "value": shell_script
                        }
                    ],
                    "entryPoint": shell_command3
                }
            ],
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
                },
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
        with ThreadPoolExecutor(max_workers=8) as executer:
            results = executer.map(self.create_service, steps)
        return list(results)

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
        except TypeError:
            return False
        return deploy["desiredCount"] == deploy["runningCount"]

    def all_services_ready(self, steps):
        # type: (List[Dict[str, Any]]) -> bool
        """Queries all service ARN's in the plan to see if they're ready"""
        with ThreadPoolExecutor(max_workers=8) as executer:
            results = executer.map(self.service_ready, steps)
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
        for step in steps:
            try:
                response = self._ecs_client.describe_task_definition(
                    taskDefinition=self.family_name(step)
                )
            except botocore.exceptions.ClientError:
                continue

            task_arn = response["taskDefinition"]["taskDefinitionArn"]

            # Deregister the task
            try:
                response = self._ecs_client.deregister_task_definition(
                    taskDefinition=task_arn
                )
            except botocore.exceptions.ClientError:
                continue


def build_instance_map(test_plan):
    """Given a JSON test-plan, build and return a dict of instance types
    and how many should exist for each type."""
    instances = defaultdict(int)
    for step in test_plan["steps"]:
        instances[step["instance_type"]] += step["instance_count"]
    return instances


def find_test_plan_duration(plan):
    # type: (Dict[str, Any]) -> int
    """Locates and calculates the longest test plan duration from its
    delay through its duration of the plan."""
    return max(
        [x.get("run_delay", 0) + x["run_max_time"] for x in plan["steps"]]
    )


def populate_missing_instances(event, context):
    logger.info("Called with {}".format(event))
    logger.info("Environ: {}".format(os.environ))
    ecs_manager = ECSManager(plan=event)
    needed = build_instance_map(event)
    logger.info("Plan instances needed: {}".format(needed))
    current_instances = ecs_manager.query_active_instances()
    missing_instances = ecs_manager.calculate_missing_instances(
        desired=needed, current=current_instances
    )
    if missing_instances:
        logger.info("Requesting instances: {}".format(missing_instances))
        ecs_manager.request_instances(missing_instances)
    return event


def create_ecs_services(event, context):
    logger.info("Called with {}".format(event))
    ecs_manager = ECSManager(plan=event)
    ecs_manager.create_services(event["steps"])
    return event


def wait_for_cluster_ready(event, context):
    logger.info("Called with {}".format(event))
    ecs_manager = ECSManager(plan=event)
    if not ecs_manager.all_services_ready(event["steps"]):
        raise ServicesStartingException()
    return event


def signal_cluster_start(event, context):
    logger.info("Called with {}".format(event))
    logger.info("Bucket: {}".format(os.environ["s3_ready_bucket"]))
    logger.info("Key: {}.ready".format(event["plan_run_uuid"]))
    s3_client = boto3.client('s3')
    s3_client.put_object(
        ACL="public-read",
        Body=b'{}'.format(int(time.time())),
        Bucket=os.environ["s3_ready_bucket"],
        Key="{}.ready".format(event["plan_run_uuid"]),
        Metadata={
            "ECSCluster": event["ecs_name"]
        }
    )
    return event


def check_for_cluster_done(event, context):
    logger.info("Called with {}".format(event))
    ecs_manager = ECSManager(plan=event)

    # Check to see if the S3 file is still around
    s3 = boto3.resource('s3')
    try:
        ready_file = s3.Object(
            os.environ["s3_ready_bucket"],
            "{}.ready".format(event["plan_run_uuid"])
        )
    except botocore.exceptions.ClientError:
        # Error getting to the bucket/key, abort test run
        raise ShutdownPlanException("Error accessing ready file")

    file_contents = ready_file.get()['Body'].read().decode('utf-8')
    start_time = int(file_contents)

    # Update to running count 0 any services that should halt by now
    ecs_manager.stop_finished_services(start_time, event["steps"])

    # If we're totally done, exit.
    now = time.time()
    plan_duration = find_test_plan_duration(event)
    if now > (start_time + plan_duration):
        raise ShutdownPlanException("Test Plan has completed")
    return event

def cleanup_cluster(event, context):
    logger.info("Called with {}".format(event))
    ecs_manager = ECSManager(plan=event)
    ecs_manager.shutdown_plan(event["steps"])

    # Attempt to remove the S3 object
    s3 = boto3.resource('s3')
    try:
        ready_file = s3.Object(
            os.environ["s3_ready_bucket"],
            "{}.ready".format(event["plan_run_uuid"])
        )
        ready_file.delete()
    except botocore.exceptions.ClientError:
        pass
    return event
