import json
import os
import time
import unittest
import uuid

import mock
from botocore.exceptions import ClientError
from nose.tools import eq_, assert_raises

from tests import fixtures

class TestAsyncPlanRunner(unittest.TestCase):

    def setUp(self):
        self.mock_ecs = mock.Mock()
        self._patcher = mock.patch("ardere.step_functions.ECSManager")
        mock_manager = self._patcher.start()
        mock_manager.return_value = self.mock_ecs

        from ardere.step_functions import AsynchronousPlanRunner

        self.plan = json.loads(fixtures.sample_basic_test_plan)
        self.runner = AsynchronousPlanRunner(self.plan, {})
        self.runner.boto = self.mock_boto = mock.Mock()

    def tearDown(self):
        self._patcher.stop()

    def test_build_instance_map(self):
        result = self.runner._build_instance_map()
        eq_(len(result), 1)
        eq_(result, {"t2.medium": 1})

    def test_find_test_plan_duration(self):
        result = self.runner._find_test_plan_duration()
        eq_(result, 140)

    def test_populate_missing_instances(self):
        self.runner.populate_missing_instances()
        self.mock_ecs.query_active_instances.assert_called()
        self.mock_ecs.request_instances.assert_called()

    def test_create_ecs_services(self):
        self.runner.create_ecs_services()
        self.mock_ecs.create_services.assert_called_with(self.plan["steps"])

    def test_wait_for_cluster_ready_not_ready(self):
        from ardere.exceptions import ServicesStartingException

        self.mock_ecs.all_services_ready.return_value = False
        assert_raises(ServicesStartingException,
                      self.runner.wait_for_cluster_ready)

    def test_wait_for_cluster_ready_all_ready(self):
        self.mock_ecs.all_services_ready.return_value = True
        self.runner.wait_for_cluster_ready()
        self.mock_ecs.all_services_ready.assert_called()

    def test_signal_cluster_start(self):
        self.plan["plan_run_uuid"] = str(uuid.uuid4())

        self.runner.signal_cluster_start()
        self.mock_boto.client.assert_called()

    def test_check_for_cluster_done_not_done(self):
        os.environ["s3_ready_bucket"] = "test_bucket"
        mock_file = mock.Mock()
        mock_file.get.return_value = {"Body": mock_file}
        mock_file.read.return_value = "{}".format(int(time.time())-100).encode(
            'utf-8')
        mock_s3_obj = mock.Mock()
        mock_s3_obj.Object.return_value = mock_file
        self.mock_boto.resource.return_value = mock_s3_obj

        self.plan["plan_run_uuid"] = str(uuid.uuid4())
        self.runner.check_for_cluster_done()

    def test_check_for_cluster_done_shutdown(self):
        from ardere.exceptions import ShutdownPlanException

        os.environ["s3_ready_bucket"] = "test_bucket"
        mock_file = mock.Mock()
        mock_file.get.return_value = {"Body": mock_file}
        mock_file.read.return_value = "{}".format(int(time.time())-400).encode(
            'utf-8')
        mock_s3_obj = mock.Mock()
        mock_s3_obj.Object.return_value = mock_file
        self.mock_boto.resource.return_value = mock_s3_obj

        self.plan["plan_run_uuid"] = str(uuid.uuid4())
        assert_raises(ShutdownPlanException, self.runner.check_for_cluster_done)

    def test_check_for_cluster_done_object_error(self):
        from ardere.exceptions import ShutdownPlanException

        os.environ["s3_ready_bucket"] = "test_bucket"
        mock_file = mock.Mock()
        mock_file.get.return_value = {"Body": mock_file}
        mock_file.read.return_value = "{}".format(int(time.time())-400).encode(
            'utf-8')
        mock_s3_obj = mock.Mock()
        mock_s3_obj.Object.side_effect = ClientError(
            {"Error": {}}, None
        )
        self.mock_boto.resource.return_value = mock_s3_obj

        self.plan["plan_run_uuid"] = str(uuid.uuid4())
        assert_raises(ShutdownPlanException, self.runner.check_for_cluster_done)

    def test_cleanup_cluster(self):
        self.plan["plan_run_uuid"] = str(uuid.uuid4())

        self.runner.cleanup_cluster()
        self.mock_boto.resource.assert_called()

    def test_cleanup_cluster_error(self):
        self.plan["plan_run_uuid"] = str(uuid.uuid4())

        mock_s3 = mock.Mock()
        self.mock_boto.resource.return_value = mock_s3
        mock_s3.Object.side_effect = ClientError(
            {"Error": {}}, None
        )
        self.runner.cleanup_cluster()
        mock_s3.Object.assert_called()
