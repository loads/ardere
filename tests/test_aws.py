import json
import os
import time
import unittest

import mock
from nose.tools import eq_, ok_

from tests import fixtures


class TestECSManager(unittest.TestCase):
    def _make_FUT(self, plan=None):
        from ardere.aws import ECSManager
        self.boto_mock = mock.Mock()
        ECSManager.boto = self.boto_mock
        if not plan:
            plan = json.loads(fixtures.sample_basic_test_plan)
        return ECSManager(plan)

    def test_init(self):
        ecs = self._make_FUT()
        eq_(ecs._plan["plan_run_uuid"], ecs._plan_uuid)

    def test_ready_file(self):
        ecs = self._make_FUT()
        os.environ["s3_ready_bucket"] = "test_bucket"
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

    def test_request_instances(self):
        os.environ["ecs_profile"] = "arn:something:fantastic:::"
        instances = {
            "t2.medium": 10
        }
        ecs = self._make_FUT()
        ecs._ec2_client.run_instances.return_value = {
            "Instances": [{"InstanceId": 12345}]
        }
        ecs.request_instances(instances)
        ecs._ec2_client.create_tags.assert_called_with(
            Resources=[12345], Tags=[
                {'Value': 'ardere', 'Key': 'Owner'},
                {'Value': u'ardere-test', 'Key': 'ECSCluster'}
            ]
        )

    def test_create_service(self):
        os.environ["s3_ready_bucket"] = "test_bucket"
        ecs = self._make_FUT()

        step = ecs._plan["steps"][0]

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

    def test_create_services(self):
        ecs = self._make_FUT()
        ecs.create_service = mock.Mock()
        ecs.create_services(ecs._plan["steps"])
        ecs.create_service.assert_called()

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
        ecs._ecs_client.get_paginator.return_value = mock_paginator
        ecs._ecs_client.describe_task_definition.return_value = {
            "taskDefinition": {"taskDefinitionArn": "arn:task:::"}
        }
        ecs._ecs_client.deregister_task_definition.side_effect = ClientError(
            {"Error": {}}, "some_op"
        )

        ecs.shutdown_plan(ecs._plan["steps"])
        ecs._ecs_client.delete_service.assert_called()
