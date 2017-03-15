import json
import os
import time
import unittest
import uuid

import mock
from botocore.exceptions import ClientError
from nose.tools import eq_, assert_raises

from tests import fixtures

class TestHandler(unittest.TestCase):

    def setUp(self):
        self.plan = json.loads(fixtures.sample_basic_test_plan)
        self.mock_ecs = mock.Mock()
        self._patcher = mock.patch("ardere.handler.ECSManager")
        mock_manager = self._patcher.start()
        mock_manager.return_value = self.mock_ecs

    def tearDown(self):
        self._patcher.stop()

    def test_build_instance_map(self):
        from ardere.handler import _build_instance_map

        result = _build_instance_map(self.plan)
        eq_(len(result), 1)
        eq_(result, {"t2.medium": 1})

    def test_find_test_plan_duration(self):
        from ardere.handler import _find_test_plan_duration

        result = _find_test_plan_duration(self.plan)
        eq_(result, 140)

    def test_populate_missing_instances(self):
        from ardere.handler import populate_missing_instances

        populate_missing_instances(self.plan, {})
        self.mock_ecs.query_active_instances.assert_called()
        self.mock_ecs.request_instances.assert_called()

    def test_create_ecs_services(self):
        from ardere.handler import create_ecs_services

        create_ecs_services(self.plan, {})
        self.mock_ecs.create_services.assert_called_with(self.plan["steps"])

    def test_wait_for_cluster_ready_not_ready(self):
        from ardere.handler import wait_for_cluster_ready
        from ardere.exceptions import ServicesStartingException

        self.mock_ecs.all_services_ready.return_value = False
        assert_raises(ServicesStartingException, wait_for_cluster_ready,
                      self.plan, {})

    def test_wait_for_cluster_ready_all_ready(self):
        from ardere.handler import wait_for_cluster_ready
        from ardere.exceptions import ServicesStartingException

        self.mock_ecs.all_services_ready.return_value = True
        wait_for_cluster_ready(self.plan, {})
        self.mock_ecs.all_services_ready.assert_called()

    @mock.patch("ardere.handler.boto3")
    def test_signal_cluster_start(self, mock_boto):
        from ardere.handler import signal_cluster_start

        self.plan["plan_run_uuid"] = str(uuid.uuid4())

        signal_cluster_start(self.plan, {})
        mock_boto.client.assert_called()

    @mock.patch("ardere.handler.boto3")
    def test_check_for_cluster_done_not_done(self, mock_boto):
        from ardere.handler import check_for_cluster_done
        os.environ["s3_ready_bucket"] = "test_bucket"
        mock_file = mock.Mock()
        mock_file.get.return_value = {"Body": mock_file}
        mock_file.read.return_value = "{}".format(int(time.time())-100).encode(
            'utf-8')
        mock_s3_obj = mock.Mock()
        mock_s3_obj.Object.return_value = mock_file
        mock_boto.resource.return_value = mock_s3_obj

        self.plan["plan_run_uuid"] = str(uuid.uuid4())
        check_for_cluster_done(self.plan, {})

    @mock.patch("ardere.handler.boto3")
    def test_check_for_cluster_done_shutdown(self, mock_boto):
        from ardere.handler import check_for_cluster_done
        from ardere.exceptions import ShutdownPlanException
        os.environ["s3_ready_bucket"] = "test_bucket"
        mock_file = mock.Mock()
        mock_file.get.return_value = {"Body": mock_file}
        mock_file.read.return_value = "{}".format(int(time.time())-400).encode(
            'utf-8')
        mock_s3_obj = mock.Mock()
        mock_s3_obj.Object.return_value = mock_file
        mock_boto.resource.return_value = mock_s3_obj

        self.plan["plan_run_uuid"] = str(uuid.uuid4())
        assert_raises(ShutdownPlanException, check_for_cluster_done,
            self.plan, {})

    @mock.patch("ardere.handler.boto3")
    def test_check_for_cluster_done_object_error(self, mock_boto):
        from ardere.handler import check_for_cluster_done
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
        mock_boto.resource.return_value = mock_s3_obj

        self.plan["plan_run_uuid"] = str(uuid.uuid4())
        assert_raises(ShutdownPlanException, check_for_cluster_done,
            self.plan, {})

    @mock.patch("ardere.handler.boto3")
    def test_cleanup_cluster(self, mock_boto):
        from ardere.handler import cleanup_cluster
        self.plan["plan_run_uuid"] = str(uuid.uuid4())

        cleanup_cluster(self.plan, {})
        mock_boto.resource.assert_called()

    @mock.patch("ardere.handler.boto3")
    def test_cleanup_cluster_error(self, mock_boto):
        from ardere.handler import cleanup_cluster
        self.plan["plan_run_uuid"] = str(uuid.uuid4())

        mock_s3 = mock.Mock()
        mock_boto.resource.return_value = mock_s3
        mock_s3.Object.side_effect = ClientError(
            {"Error": {}}, None
        )
        cleanup_cluster(self.plan, {})
        mock_s3.Object.assert_called()
