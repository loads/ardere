import json
import os
import time
import unittest

import mock
from nose.tools import assert_raises, eq_, ok_

from tests import fixtures


class TestECSManager(unittest.TestCase):
    def _make_FUT(self, plan=None):
        from ardere.aws import ECSManager
        os.environ["s3_ready_bucket"] = "test_bucket"
        os.environ["ecs_profile"] = "arn:something:fantastic:::"
        os.environ["container_log_group"] = "ardere"
        self.boto_mock = mock.Mock()
        ECSManager.boto = self.boto_mock
        if not plan:
            plan = json.loads(fixtures.sample_basic_test_plan)
            plan["metrics_options"] = dict(
                dashboard=dict(
                    admin_user="admin",
                    admin_password="admin"
                ),
                tear_down=False
            )
        return ECSManager(plan)

    def test_init(self):
        ecs = self._make_FUT()
        eq_(ecs._plan["plan_run_uuid"], ecs._plan_uuid)
        eq_(ecs.plan_uuid, ecs._plan_uuid)

    def test_ready_file(self):
        ecs = self._make_FUT()
        ready_filename = ecs.s3_ready_file
        ok_("test_bucket" in ready_filename)
        ok_(ecs._plan_uuid in ready_filename)

    def test_query_active(self):
        mock_paginator = mock.Mock()
        mock_paginator.paginate.return_value = [
            {"Reservations": [
                {
                    "Instances": [
                        {
                            "State": {
                                "Code": 16
                            },
                            "InstanceType": "t2.medium"
                        }
                    ]
                }
            ]}
        ]

        ecs = self._make_FUT()
        ecs._ec2_client.get_paginator.return_value = mock_paginator
        instance_dct = ecs.query_active_instances()
        eq_(len(instance_dct.values()), 1)

    def test_calculate_missing_instances(self):
        ecs = self._make_FUT()
        result = ecs.calculate_missing_instances(
            desired={"t2.medium": 2}, current={"t2.medium": 1}
        )
        eq_(result, {"t2.medium": 1})

    def test_has_metrics_node(self):
        mock_paginator = mock.Mock()
        mock_paginator.paginate.return_value = [
            {"Reservations": [
                {
                    "Instances": [
                        {
                            "State": {
                                "Code": 16
                            },
                            "InstanceType": "t2.medium"
                        }
                    ]
                }
            ]}
        ]

        ecs = self._make_FUT()
        ecs._ec2_client.get_paginator.return_value = mock_paginator
        resp = ecs.has_metrics_node("t2.medium")
        eq_(resp, True)

    def test_has_started_metric_creation(self):
        ecs = self._make_FUT()
        ecs._ecs_client.list_tasks.return_value = {"taskArns": [123]}
        eq_(ecs.has_started_metric_creation(), True)

    def test_has_finished_metric_creation(self):
        ecs = self._make_FUT()
        ecs._ecs_client.list_tasks.return_value = {"taskArns": [123]}
        eq_(ecs.has_finished_metric_creation(), True)

    def test_request_instances(self):
        instances = {
            "t2.medium": 10
        }
        ecs = self._make_FUT()
        ecs._ec2_client.run_instances.return_value = {
            "Instances": [{"InstanceId": 12345}]
        }
        ecs.request_instances(instances, ["i-382842"], {"Role": "metrics"})
        ecs._ec2_client.run_instances.assert_called()

    def test_locate_metrics_container_ip(self):
        ecs = self._make_FUT()
        ecs._ecs_client.list_container_instances.return_value = {
            "containerInstanceArns": ["arn:of:some:container::"]
        }
        ecs._ecs_client.describe_container_instances.return_value = {
            "containerInstances": [
                {"ec2InstanceId": "e-28193823"}
            ]
        }
        mock_resource = mock.Mock()
        ecs.boto.resource.return_value = mock_resource
        ecs.locate_metrics_container_ip()
        ecs.boto.resource.assert_called()

    def test_locate_metrics_container_ip_not_found(self):
        ecs = self._make_FUT()
        ecs._ecs_client.list_container_instances.return_value = {
            "containerInstanceArns": []
        }
        result = ecs.locate_metrics_container_ip()
        eq_(result, (None, None))

    def test_locate_metrics_service(self):
        ecs = self._make_FUT()
        ecs._ecs_client.describe_services.return_value = {
            "services": [
                {"stuff": 1, "status": "ACTIVE"}
            ]
        }
        result = ecs.locate_metrics_service()
        eq_(result, {"stuff": 1, "status": "ACTIVE"})

    def test_locate_metrics_service_not_found(self):
        ecs = self._make_FUT()
        ecs._ecs_client.describe_services.return_value = {
            "services": []
        }
        result = ecs.locate_metrics_service()
        eq_(result, None)

    def test_create_metrics_service(self):
        ecs = self._make_FUT()

        # Setup mocks
        ecs._ecs_client.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": "arn:of:some:task::"
            }
        }
        ecs._ecs_client.create_service.return_value = {
            "service": {"serviceArn": "arn:of:some:service::"}
        }

        result = ecs.create_metrics_service(dict(instance_type="c4.large"))
        eq_(result["service_arn"], "arn:of:some:service::")

    def test_run_metric_creation_task(self):
        ecs = self._make_FUT()
        ecs.run_metric_creation_task("arn:::", ("admin", "admin"),
                                     "asdf", "atitle")
        ecs._ecs_client.start_task.assert_called()

    def test_create_service(self):
        ecs = self._make_FUT()

        step = ecs._plan["steps"][0]
        ecs._plan["influxdb_private_ip"] = "1.1.1.1"
        step["docker_series"] = "default"

        # Setup mocks
        ecs._ecs_client.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": "arn:of:some:task::"
            }
        }
        ecs._ecs_client.create_service.return_value = {
            "service": {"serviceArn": "arn:of:some:service::"}
        }

        ecs.create_service(step)

        eq_(step["serviceArn"], "arn:of:some:service::")
        ecs._ecs_client.register_task_definition.assert_called()
        _, kwargs = ecs._ecs_client.register_task_definition.call_args
        container_def = kwargs["containerDefinitions"][0]

        eq_(container_def["cpu"], 1536)

        _, kwargs = ecs._ecs_client.register_task_definition.call_args
        container_def = kwargs["containerDefinitions"][0]
        ok_("portMappings" in container_def)

    def test_create_services(self):
        ecs = self._make_FUT()
        ecs.create_service = mock.Mock()
        ecs.create_services(ecs._plan["steps"])
        ecs.create_service.assert_called()

    def test_create_services_ecs_error(self):
        from botocore.exceptions import ClientError
        ecs = self._make_FUT()

        step = ecs._plan["steps"][0]
        ecs._plan["influxdb_private_ip"] = "1.1.1.1"
        step["docker_series"] = "default"
        ecs._ecs_client.register_task_definition.side_effect = ClientError(
            {"Error": {}}, "some_op"
        )

        with assert_raises(ClientError):
            ecs.create_services(ecs._plan["steps"])

    def test_service_ready_true(self):
        ecs = self._make_FUT()
        step = ecs._plan["steps"][0]

        ecs._ecs_client.describe_services.return_value = {
            "services": [{
                "deployments": [{
                    "desiredCount": 2,
                    "runningCount": 2
                }]
            }]
        }

        result = ecs.service_ready(step)
        eq_(result, True)

    def test_service_not_known_yet(self):
        ecs = self._make_FUT()
        step = ecs._plan["steps"][0]

        ecs._ecs_client.describe_services.return_value = {
            "services": []
        }

        result = ecs.service_ready(step)
        eq_(result, False)

    def test_all_services_ready(self):
        ecs = self._make_FUT()
        ecs.service_ready = mock.Mock()

        ecs.all_services_ready(ecs._plan["steps"])
        ecs.service_ready.assert_called()

    def test_service_done_true(self):
        ecs = self._make_FUT()
        step = ecs._plan["steps"][0]

        ecs._ecs_client.describe_services.return_value = {
            "services": [{
                "status": "INACTIVE"
            }]
        }

        result = ecs.service_done(step)
        eq_(result, True)

    def test_service_not_known(self):
        ecs = self._make_FUT()
        step = ecs._plan["steps"][0]

        ecs._ecs_client.describe_services.return_value = {
            "services": [{
                "status": "DRAINING"
            }]
        }

        result = ecs.service_done(step)
        eq_(result, False)

    def test_all_services_done(self):
        ecs = self._make_FUT()
        ecs.service_done = mock.Mock()
        ecs.all_services_done(ecs._plan["steps"])
        ecs.service_done.assert_called()

    def test_stop_finished_service_stopped(self):
        ecs = self._make_FUT()
        ecs._ecs_client.update_service = mock.Mock()
        step = ecs._plan["steps"][0]
        step["service_status"] = "STARTED"
        past = time.time() - 400
        ecs.stop_finished_service(past, step)
        ecs._ecs_client.update_service.assert_called()
        eq_(step["service_status"], "STOPPED")

    def test_stop_finished_service_stop_already_stopped(self):
        ecs = self._make_FUT()
        ecs._ecs_client.update_service = mock.Mock()
        step = ecs._plan["steps"][0]
        step["service_status"] = "STOPPED"
        past = time.time() - 400
        ecs.stop_finished_service(past, step)
        ecs._ecs_client.update_service.assert_not_called()
        eq_(step["service_status"], "STOPPED")

    def test_stop_finished_service_still_running(self):
        ecs = self._make_FUT()
        ecs._ecs_client.update_service = mock.Mock()
        step = ecs._plan["steps"][0]
        step["service_status"] = "STARTED"
        past = time.time() - 100
        ecs.stop_finished_service(past, step)
        ecs._ecs_client.update_service.assert_not_called()
        eq_(step["service_status"], "STARTED")

    def test_stop_finished_services(self):
        ecs = self._make_FUT()
        ecs.stop_finished_service = mock.Mock()

        past = time.time() - 100
        ecs.stop_finished_services(past, ecs._plan["steps"])
        ecs.stop_finished_service.assert_called()

    def test_shutdown_plan(self):
        mock_paginator = mock.Mock()
        mock_paginator.paginate.return_value = [
            {"serviceArns": ["arn:123:::", "arn:456:::"]}
        ]

        ecs = self._make_FUT()
        ecs.locate_metrics_service = mock.Mock()
        ecs.locate_metrics_service.return_value = dict(
            serviceArn="arn:456:::"
        )
        ecs._ecs_client.get_paginator.return_value = mock_paginator
        ecs._ecs_client.describe_task_definition.return_value = {
            "taskDefinition": {"taskDefinitionArn": "arn:task:::"}
        }

        ecs.shutdown_plan(ecs._plan["steps"])
        ecs._ecs_client.deregister_task_definition.assert_called()
        ecs._ecs_client.delete_service.assert_called()

    def test_shutdown_plan_update_error(self):
        from botocore.exceptions import ClientError

        mock_paginator = mock.Mock()
        mock_paginator.paginate.return_value = [
            {"serviceArns": ["arn:123:::", "arn:456:::"]}
        ]

        ecs = self._make_FUT()
        ecs.locate_metrics_service = mock.Mock()
        ecs.locate_metrics_service.return_value = dict(
            serviceArn="arn:456:::"
        )
        ecs._ecs_client.get_paginator.return_value = mock_paginator
        ecs._ecs_client.describe_task_definition.return_value = {
            "taskDefinition": {"taskDefinitionArn": "arn:task:::"}
        }
        ecs._ecs_client.update_service.side_effect = ClientError(
            {"Error": {}}, "some_op"
        )

        ecs.shutdown_plan(ecs._plan["steps"])
        ecs._ecs_client.delete_service.assert_not_called()

    def test_shutdown_plan_describe_error(self):
        from botocore.exceptions import ClientError

        mock_paginator = mock.Mock()
        mock_paginator.paginate.return_value = [
            {"serviceArns": ["arn:123:::", "arn:456:::"]}
        ]

        ecs = self._make_FUT()
        ecs.locate_metrics_service = mock.Mock()
        ecs.locate_metrics_service.return_value = dict(
            serviceArn="arn:456:::"
        )
        ecs._plan["steps"] = ecs._plan["steps"][:1]
        ecs._ecs_client.get_paginator.return_value = mock_paginator
        ecs._ecs_client.describe_task_definition.side_effect = ClientError(
            {"Error": {}}, "some_op"
        )

        ecs.shutdown_plan(ecs._plan["steps"])
        ecs._ecs_client.deregister_task_definition.assert_not_called()

    def test_shutdown_plan_delete_error(self):
        from botocore.exceptions import ClientError

        mock_paginator = mock.Mock()
        mock_paginator.paginate.return_value = [
            {"serviceArns": ["arn:123:::", "arn:456:::"]}
        ]

        ecs = self._make_FUT()
        ecs.locate_metrics_service = mock.Mock()
        ecs.locate_metrics_service.return_value = dict(
            serviceArn="arn:456:::"
        )
        ecs._ecs_client.get_paginator.return_value = mock_paginator
        ecs._ecs_client.describe_task_definition.return_value = {
            "taskDefinition": {"taskDefinitionArn": "arn:task:::"}
        }
        ecs._ecs_client.delete_service.side_effect = ClientError(
            {"Error": {}}, "some_op"
        )

        ecs.shutdown_plan(ecs._plan["steps"])
        ecs._ecs_client.delete_service.assert_called()

    def test_shutdown_plan_deregister_error(self):
        from botocore.exceptions import ClientError

        mock_paginator = mock.Mock()
        mock_paginator.paginate.return_value = [
            {"serviceArns": ["arn:123:::", "arn:456:::"]}
        ]

        ecs = self._make_FUT()
        ecs.locate_metrics_service = mock.Mock()
        ecs.locate_metrics_service.return_value = dict(
            serviceArn="arn:456:::"
        )
        ecs._plan["metrics_options"]["tear_down"] = True
        ecs._ecs_client.get_paginator.return_value = mock_paginator
        ecs._ecs_client.describe_task_definition.return_value = {
            "taskDefinition": {"taskDefinitionArn": "arn:task:::"}
        }
        ecs._ecs_client.deregister_task_definition.side_effect = ClientError(
            {"Error": {}}, "some_op"
        )

        ecs.shutdown_plan(ecs._plan["steps"])
        ecs._ecs_client.delete_service.assert_called()
