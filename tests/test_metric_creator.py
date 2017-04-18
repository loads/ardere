import os
import unittest

import mock
from nose.tools import assert_raises, eq_


class TestMetricRunner(unittest.TestCase):
    def _make_FUT(self):
        from ardere.scripts.metric_creator import DashboardSetup
        # Setup the env vars we need
        os.environ["__ARDERE_INFLUXDB_NAME__"] = "ardere"
        return DashboardSetup()

    def test_load_dashboard(self):
        ds = self._make_FUT()
        mock_file = mock.Mock()
        mock_file.get.return_value = {"Body": mock_file}
        mock_file.read.return_value = "{}".encode(
            'utf-8')
        mock_s3_obj = mock.Mock()
        mock_s3_obj.Object.return_value = mock_file

        ds.boto = mock.Mock()
        ds.boto.resource.return_value = mock_s3_obj
        ds.dashboard = "asdf:asdf"
        result = ds._load_dashboard()
        eq_(result, dict(id=None, title=None))

    def test_create_dashboard(self):
        ds = self._make_FUT()
        ds._load_dashboard = mock.Mock()
        ds.req = mock.Mock()
        ds.req.post.return_value = mock.Mock(status_code=200)
        ds._create_dashboard("http://localhost")
        ds._load_dashboard.assert_called()

    def test_create_dashboard_exception(self):
        ds = self._make_FUT()
        ds._load_dashboard = mock.Mock()
        ds.req = mock.Mock()
        ds.req.post.return_value = mock.Mock(status_code=500)
        assert_raises(Exception, ds._create_dashboard, "http://localhost")

    def test_ensure_dashboard_create(self):
        ds = self._make_FUT()
        ds.req = mock.Mock()
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        ds._create_dashboard = mock.Mock()
        ds.req.get.return_value = mock_response

        ds._ensure_dashboard("http://localhost")
        ds._create_dashboard.assert_called()

    def test_ensure_dashboard_exception(self):
        ds = self._make_FUT()
        ds.req = mock.Mock()
        mock_response = mock.Mock()
        mock_response.status_code = 500
        ds.req.get.return_value = mock_response
        assert_raises(Exception, ds._ensure_dashboard, "http://localhost")

    def test_create_datasources(self):
        ds = self._make_FUT()
        ds.dashboard = True
        ds.influx = mock.Mock()
        ds.req = mock.Mock()
        mock_client = mock.Mock()
        ds._ensure_dashboard = mock.Mock()
        ds.influx.InfluxDBClient.return_value = mock_client

        ds.create_datasources()
        mock_client.create_database.assert_called()
        ds._ensure_dashboard.assert_called()
