import logging
import json
import os

import boto3
import influxdb
import requests

try:
    from typing import Any, Dict  # noqa
except ImportError:  # pragma: nocover
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()


class DashboardSetup(object):
    # For testing purposes
    boto = boto3
    req = requests
    influx = influxdb

    def __init__(self):
        self.influx_db_name = os.environ["__ARDERE_INFLUXDB_NAME__"]
        self.dashboard = os.environ.get("__ARDERE_DASHBOARD__")
        self.dashboard_name = os.environ.get("__ARDERE_DASHBOARD_NAME__")
        self.grafana_auth = (
            os.environ.get("__ARDERE_GRAFANA_USER__"),
            os.environ.get("__ARDERE_GRAFANA_PASS__")
        )

    def _load_dashboard(self):
        # type: () -> Dict[str, Any]
        """Load dashboard from S3 and update JSON contents"""
        logger.info("Fetching dashboard from S3")
        bucket, filename = self.dashboard.split(":")
        s3 = self.boto.resource('s3')
        dash_file = s3.Object(bucket, filename)
        file_contents = dash_file.get()['Body'].read().decode('utf-8')
        dash_contents = json.loads(file_contents)
        dash_contents["title"] = self.dashboard_name
        dash_contents["id"] = None
        logger.info("Fetched dashboard file")
        return dash_contents

    def _create_dashboard(self, grafana_url):
        # type: (str) -> None
        """Create the dashboard in grafana"""
        dash_contents = self._load_dashboard()
        logger.info("Creating dashboard in grafana")
        response = self.req.post(grafana_url + "/api/dashboards/db",
                                 auth=self.grafana_auth,
                                 json=dict(
                                     dashboard=dash_contents,
                                     overwrite=True
                                 ))
        if response.status_code != 200:
            raise Exception("Error creating dashboard: {}".format(
                response.status_code))

    def _ensure_dashboard(self, grafana_url):
        # type: (str) -> None
        """Ensure the dashboard is present"""
        # Verify whether the dashboard exists
        response = self.req.get(grafana_url + "/api/search",
                                auth=self.grafana_auth,
                                params=dict(query=self.dashboard_name))
        if response.status_code != 200:
            raise Exception("Failure to search dashboards")

        # search results for dashboard
        results = filter(lambda x: x["title"] == self.dashboard_name,
                         response.json())
        if not results:
            self._create_dashboard(grafana_url)

    def create_datasources(self):
        # type: () -> None
        # Create an influxdb for this run
        logger.info("Create influx database")
        influx_client = self.influx.InfluxDBClient()
        influx_client.create_database(self.influx_db_name)

        # Setup the grafana datasource
        grafana_url = "http://127.0.0.1:3000"
        ds_api_url = "http://127.0.0.1:3000/api/datasources"
        logger.info("Create datasource in grafana")
        self.req.post(ds_api_url, auth=self.grafana_auth, json=dict(
            name=self.influx_db_name,
            type="influxdb",
            url="http://localhost:8086",
            database=self.influx_db_name,
            access="proxy",
            basicAuth=False
        ))

        # Setup the grafana dashboard if needed/desired
        if self.dashboard:
            self._ensure_dashboard(grafana_url)


if __name__ == "__main__":  # pragma: no cover
    logger.info("Creating datasources")
    DashboardSetup().create_datasources()
    logger.info("Finished.")
