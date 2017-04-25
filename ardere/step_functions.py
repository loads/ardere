import logging
import os
import re
import time
from collections import defaultdict

import boto3
import botocore
import toml
from marshmallow import (
    Schema,
    decorators,
    fields,
    validate,
    ValidationError,
)
from typing import Any, Dict, List  # noqa

from ardere.aws import (
    ECSManager,
    ec2_vcpu_by_type,
)
from ardere.exceptions import (
    CreatingMetricSourceException,
    ServicesStartingException,
    ShutdownPlanException,
    ValidationException,
    UndrainedInstancesException,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Step name is used as the Log stream name.
# Log stream names are limited to 512 characters (no ":" or "*")
# Name format is
# ardere-UUID/STEP_NAME/LUUID
# where UUID is dashed, and LUUID is not
# therefore: 512 - (9 + 36 + 32) = max name len
MAX_NAME_LEN = 435
INVALID_NAME_CHECK = re.compile("([:\*]+)")


class StepValidator(Schema):
    name = fields.String(required=True)
    instance_count = fields.Int(required=True)
    instance_type = fields.String(
        required=True,
        validate=validate.OneOf(ec2_vcpu_by_type.keys())
    )
    run_max_time = fields.Int(required=True)
    run_delay = fields.Int(missing=0)
    container_name = fields.String(required=True)
    cmd = fields.String(required=True)
    port_mapping = fields.List(fields.Int())
    env = fields.Dict()
    docker_series = fields.String(missing="default")

    @decorators.validates("name")
    def validate_name(self, value):
        if len(value) == 0:
            raise ValidationError("Step name missing")
        if len(value) > MAX_NAME_LEN:
            raise ValidationError("Step name too long")
        if INVALID_NAME_CHECK.search(value):
            raise ValidationError("Step name contains invalid characters")


class DashboardOptions(Schema):
    admin_user = fields.String(missing="admin")
    admin_password = fields.String(required=True)
    name = fields.String(required=True)
    filename = fields.String(required=True)


class MetricsOptions(Schema):
    enabled = fields.Bool(missing=True)
    instance_type = fields.String(
        missing="c4.large",
        validate=validate.OneOf(ec2_vcpu_by_type.keys())
    )
    dashboard = fields.Nested(DashboardOptions)
    tear_down = fields.Bool(missing=False)


class PlanValidator(Schema):
    ecs_name = fields.String(required=True)
    name = fields.String(required=True)
    metrics_options = fields.Nested(MetricsOptions, missing={})

    steps = fields.Nested(StepValidator, many=True)

    def _log_validate_name(self, value, name_type):
        if len(value) == 0:
            raise ValidationError("{} missing".format(name_type))
        if len(value) > MAX_NAME_LEN:
            raise ValidationError("{} too long".format(name_type))
        if INVALID_NAME_CHECK.search(value):
            raise ValidationError(
                "{} contained invalid characters".format(name_type))

    @decorators.validates("ecs_name")
    def validate_ecs_name(self, value):
        """Verify a cluster exists for this name"""
        self._log_validate_name(value, "Plan ecs_name")
        client = self.context["boto"].client('ecs')
        response = client.describe_clusters(
            clusters=[value]
        )
        if not response.get("clusters"):
            raise ValidationError("No cluster with the provided name.")

    @decorators.validates("name")
    def validate_name(self, value):
        self._log_validate_name(value, "Step name")


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

        # Load our TOML if needed
        event = self._load_toml(event)

        self.event = event
        self.context = context
        self.ecs = ECSManager(plan=event)

    @property
    def grafana_auth(self):
        if not self.event["metrics_options"].get("dashboard"):
            return "", ""

        dash_opts = self.event["metrics_options"]["dashboard"]
        return dash_opts["admin_user"], dash_opts["admin_password"]

    @property
    def dashboard_options(self):
        return self.event["metrics_options"]["dashboard"]

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

    def _load_toml(self, event):
        """Loads TOML if necessary"""
        return toml.loads(event["toml"]) if "toml" in event else event

    def _validate_plan(self):
        """Validates that the loaded plan is correct"""
        schema = PlanValidator()
        schema.context["boto"] = self.boto
        data, errors = schema.load(self.event)
        if errors:
            raise ValidationException("Failed to validate: {}".format(errors))

        # Replace our event with the validated
        self.event = data

    def populate_missing_instances(self):
        """Populate any missing EC2 instances needed for the test plan in the
        cluster

        """
        # First, validate the test plan, done only as part of step 1
        self._validate_plan()

        needed = self._build_instance_map()

        # Ensure we have the metrics instance
        if self.event["metrics_options"]["enabled"]:
            # Query to see if we need to add a metrics node
            metric_inst_type = self.event["metrics_options"]["instance_type"]

            # We add the instance type to needed to ensure we don't leave out
            # more nodes since this will turn up in the query_active results
            needed[metric_inst_type] += 1

            # We create it here up-front if needed since we have different
            # tags
            if not self.ecs.has_metrics_node(metric_inst_type):
                self.ecs.request_instances(
                    instances={metric_inst_type: 1},
                    security_group_ids=[os.environ["metric_sg"],
                                        os.environ["ec2_sg"]],
                    additional_tags={"Role": "metrics"}
                )

        logger.info("Plan instances needed: {}".format(needed))
        current_instances = self.ecs.query_active_instances()
        missing_instances = self.ecs.calculate_missing_instances(
            desired=needed, current=current_instances
        )
        if missing_instances:
            logger.info("Requesting instances: {}".format(missing_instances))
            self.ecs.request_instances(
                instances=missing_instances,
                security_group_ids=[os.environ["ec2_sg"]]
            )
        return self.event

    def ensure_metrics_available(self):
        """Start the metrics service, ensure its running, and its IP is known

        """
        if not self.event["metrics_options"]["enabled"]:
            return self.event

        # Is the service already running?
        metrics = self.ecs.locate_metrics_service()
        logger.info("Metrics info: %s", metrics)

        if not metrics:
            # Start the metrics service, throw a retry
            self.ecs.create_metrics_service(self.event["metrics_options"])
            raise ServicesStartingException("Triggered metrics start")

        deploy = metrics["deployments"][0]
        ready = deploy["desiredCount"] == deploy["runningCount"]
        logger.info("Deploy info: %s", deploy)
        if not ready:
            raise ServicesStartingException("Waiting for metrics")

        # Populate the IP of the metrics service
        metric_ip, container_arn = self.ecs.locate_metrics_container_ip()

        if not metric_ip:
            raise Exception("Unable to locate metrics IP even though its "
                            "running")

        self.event["influxdb_private_ip"] = metric_ip
        self.event["metric_container_arn"] = container_arn
        return self.event

    def ensure_metric_sources_created(self):
        """Ensure the metrics db and grafana datasource are configured"""
        if not self.event["metrics_options"]["enabled"]:
            return self.event

        if not self.ecs.has_started_metric_creation():
            dashboard = None
            dashboard_name = None
            if self.event["metrics_options"].get("dashboard"):
                dashboard = ":".join([os.environ["metrics_bucket"],
                                      self.dashboard_options["filename"]])
                dashboard_name = self.dashboard_options["name"]
            self.ecs.run_metric_creation_task(
                container_instance=self.event["metric_container_arn"],
                grafana_auth=self.grafana_auth,
                dashboard=dashboard,
                dashboard_name=dashboard_name
            )
            raise CreatingMetricSourceException("Started metric creation")

        if not self.ecs.has_finished_metric_creation():
            raise CreatingMetricSourceException("Metric creation still "
                                                "running")

        metric_ip = self.event["influxdb_private_ip"]
        self.event["grafana_dashboard"] = "http://{}:3000".format(metric_ip)
        return self.event

    def create_ecs_services(self):
        """Create all the ECS services needed

        """
        self.ecs.create_services(self.event["steps"])
        return self.event

    def wait_for_cluster_ready(self):
        """Check all the ECS services to see if they're ready

        """
        if not self.ecs.all_services_ready(self.event["steps"]):
            raise ServicesStartingException()
        return self.event

    def signal_cluster_start(self):
        """Drop a ready file in S3 to trigger the test plan to being

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
        """Shutdown all ECS services and deregister all task definitions"""
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

    def check_drained(self):
        """Ensure that all services are shut down before allowing restart"""
        if self.ecs.all_services_done(self.event["steps"]):
            return self.event
        else:
            raise UndrainedInstancesException("Services still draining")
