import os
import logging
import time
from collections import defaultdict

import boto3
import botocore
from typing import Any, Dict, List  # noqa

from ardere.aws import ECSManager
from ardere.exceptions import (
    ServicesStartingException,
    ShutdownPlanException
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class AsynchronousPlanRunner(object):
    """Asynchronous Test Plan Runner

    This step function based runner handles running a test plan in an
    asynchronous manner, where each step will wait for its run_delay if
    present before running.

    """
    # For testing purposes
    boto = boto3

    def __init__(self, event, context):
        logger.info("Called with {}".format(event))
        logger.info("Environ: {}".format(os.environ))
        self.ecs = ECSManager(plan=event)
        self.event = event
        self.context = context

    def _build_instance_map(self):
        """Given a JSON test-plan, build and return a dict of instance types
        and how many should exist for each type."""
        instances = defaultdict(int)
        for step in self.event["steps"]:
            instances[step["instance_type"]] += step["instance_count"]
        return instances

    def _find_test_plan_duration(self):
        # type: (Dict[str, Any]) -> int
        """Locates and calculates the longest test plan duration from its
        delay through its duration of the plan."""
        return max(
            [x.get("run_delay", 0) + x["run_max_time"] for x in
             self.event["steps"]]
        )

    def populate_missing_instances(self):
        """Populate any missing EC2 instances needed for the test plan in the
        cluster

        Step 1

        """
        needed = self._build_instance_map()
        logger.info("Plan instances needed: {}".format(needed))
        current_instances = self.ecs.query_active_instances()
        missing_instances = self.ecs.calculate_missing_instances(
            desired=needed, current=current_instances
        )
        if missing_instances:
            logger.info("Requesting instances: {}".format(missing_instances))
            self.ecs.request_instances(missing_instances)
        return self.event

    def create_ecs_services(self):
        """Create all the ECS services needed

        Step 2

        """
        self.ecs.create_services(self.event["steps"])
        return self.event

    def wait_for_cluster_ready(self):
        """Check all the ECS services to see if they're ready

        Step 3

        """
        if not self.ecs.all_services_ready(self.event["steps"]):
            raise ServicesStartingException()
        return self.event

    def signal_cluster_start(self):
        """Drop a ready file in S3 to trigger the test plan to being

        Step 4

        """
        s3_client = self.boto.client('s3')
        s3_client.put_object(
            ACL="public-read",
            Body=b'{}'.format(int(time.time())),
            Bucket=os.environ["s3_ready_bucket"],
            Key="{}.ready".format(self.ecs.plan_uuid),
            Metadata={
                "ECSCluster": self.event["ecs_name"]
            }
        )
        return self.event

    def check_for_cluster_done(self):
        """Check all the ECS services to see if they've run for their
        specified duration

        Step 5

        """
        # Check to see if the S3 file is still around
        s3 = self.boto.resource('s3')
        try:
            ready_file = s3.Object(
                os.environ["s3_ready_bucket"],
                "{}.ready".format(self.ecs.plan_uuid)
            )
        except botocore.exceptions.ClientError:
            # Error getting to the bucket/key, abort test run
            raise ShutdownPlanException("Error accessing ready file")

        file_contents = ready_file.get()['Body'].read().decode('utf-8')
        start_time = int(file_contents)

        # Update to running count 0 any services that should halt by now
        self.ecs.stop_finished_services(start_time, self.event["steps"])

        # If we're totally done, exit.
        now = time.time()
        plan_duration = self._find_test_plan_duration()
        if now > (start_time + plan_duration):
            raise ShutdownPlanException("Test Plan has completed")
        return self.event

    def cleanup_cluster(self):
        """Shutdown all ECS services and deregister all task definitions

        Step 6

        """
        self.ecs.shutdown_plan(self.event["steps"])

        # Attempt to remove the S3 object
        s3 = self.boto.resource('s3')
        try:
            ready_file = s3.Object(
                os.environ["s3_ready_bucket"],
                "{}.ready".format(self.ecs.plan_uuid)
            )
            ready_file.delete()
        except botocore.exceptions.ClientError:
            pass
        return self.event
