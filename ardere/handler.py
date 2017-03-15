import logging
import os
import time
from collections import defaultdict

import boto3
import botocore
from typing import Any, Dict, List  # noqa

from aws import ECSManager
from exceptions import (
    ServicesStartingException,
    ShutdownPlanException
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _build_instance_map(test_plan):
    """Given a JSON test-plan, build and return a dict of instance types
    and how many should exist for each type."""
    instances = defaultdict(int)
    for step in test_plan["steps"]:
        instances[step["instance_type"]] += step["instance_count"]
    return instances


def _find_test_plan_duration(plan):
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
    needed = _build_instance_map(event)
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
    plan_duration = _find_test_plan_duration(event)
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
