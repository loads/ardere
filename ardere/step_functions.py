import json
import logging
import os
import re
import time
from collections import defaultdict

import boto3
import botocore
import requests
import toml
from influxdb import InfluxDBClient
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

    def _create_dashboard(self, grafana_url):
        # type: (str, Dict[str, any]) -> str
        """Create the dashboard in grafana"""
        s3_filename = self.dashboard_options["filename"]
        s3 = self.boto.resource('s3')
        dash_file = s3.Object(
            os.environ["metrics_bucket"],
            s3_filename
        )
        file_contents = dash_file.get()['Body'].read().decode('utf-8')
        dash_contents = json.loads(file_contents)
        dash_contents["title"] = self.dashboard_options["name"]
        dash_contents["id"] = None

        response = requests.post(grafana_url+"/api/dashboards/db",
                                 auth=self.grafana_auth,
                                 json=dict(
                                     dashboard=dash_contents,
                                     overwrite=True
                                 ))
        if response.status_code != 200:
            raise Exception("Error creating dashboard: {}".format(
                response.status_code))

        return "db/{}".format(response.json()["slug"])

    def _ensure_dashboard(self, grafana_url):
        # type: (str, Dict[str, Any]) -> str
        """Ensure the dashboard is present"""
        dash_name = self.dashboard_options["name"]

        # Verify whether the dashboard exists
        response = requests.get(grafana_url+"/api/search",
                                auth=self.grafana_auth,
                                params=dict(query=dash_name))
        if response.status_code != 200:
            raise Exception("Failure to search dashboards")

        # search results for dashboard
        results = filter(lambda x: x["title"] == dash_name, response.json())
        if not results:
            dash_uri = self._create_dashboard(grafana_url)
        else:
            dash_uri = results[0]["uri"]

        # Return the nice dashboard URL
        return "{grafana}/dashboard/{dash_uri}?var-db={db_name}".format(
            grafana=grafana_url,
            dash_uri=dash_uri,
            db_name=self.ecs.influx_db_name
        )

    def populate_missing_instances(self):
        """Populate any missing EC2 instances needed for the test plan in the
        cluster

        Step 1

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

        Step 2

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
        metric_ip = self.ecs.locate_metrics_container_ip()

        if not metric_ip:
            raise Exception("Unable to locate metrics IP even though its "
                            "running")

        # Create an influxdb for this run
        influx_client = InfluxDBClient(host=metric_ip)
        influx_client.create_database(self.ecs.influx_db_name)

        # Setup the grafana datasource
        grafana_url = "http://{}:3000".format(metric_ip)
        ds_api_url = "{}/api/datasources".format(grafana_url)
        requests.post(ds_api_url, auth=self.grafana_auth, json=dict(
            name=self.ecs.influx_db_name,
            type="influxdb",
            url="http://localhost:8086",
            database=self.ecs.influx_db_name,
            access="proxy",
            basicAuth=False
        ))

        # Setup the grafana dashboard if needed/desired
        if self.event["metrics_options"].get("dashboard"):
            dash_url = self._ensure_dashboard(grafana_url)
            self.event["grafana_dashboard_url"] = dash_url

        self.event["influxdb_public_ip"] = metric_ip
        self.event["grafana_dashboard"] = grafana_url
        return self.event

    def create_ecs_services(self):
        """Create all the ECS services needed

        Step 3

        """
        self.ecs.create_services(self.event["steps"])
        return self.event

    def wait_for_cluster_ready(self):
        """Check all the ECS services to see if they're ready

        Step 4

        """
        if not self.ecs.all_services_ready(self.event["steps"]):
            raise ServicesStartingException()
        return self.event

    def signal_cluster_start(self):
        """Drop a ready file in S3 to trigger the test plan to being

        Step 5

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

        Step 6

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

        Step 7

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

    def check_drained(self):
        """Ensure that all services are shut down before allowing restart

        Step 8

        """
        client = self.boto.client('ecs')
        actives = len(
            client.list_container_instances(
                cluster=self.event["ecs_name"],
                maxResults=1,
                status="ACTIVE",
            ).get('containerInstanceArns', []))
        if actives:
            raise UndrainedInstancesException(
                "Still {} active.".format(actives))
        draining = len(
            client.list_container_instances(
                cluster=self.event["ecs_name"],
                maxResults=1,
                status="DRAINING",
            ).get('containerInstanceArns', []))
        if draining:
            raise UndrainedInstancesException(
                "Still {} draining.".format(draining))
        return self.event
