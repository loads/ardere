sample_basic_test_plan = """
{
  "ecs_name": "ardere-test",
  "name": "Loadtest",
  "description": "Run all APLT scenarios",
  "metrics_options": {
    "enabled": true,
    "dashboard": {
        "admin_user": "admin",
        "admin_password": "testing",
        "name": "ap-loadtester",
        "filename": "gf_basic_dashboard.json"
    }
  },
  "steps": [
    {
      "name": "TestCluster",
      "instance_count": 1,
      "instance_type": "t2.medium",
      "run_max_time": 140,
      "env": {
          "SOME_VAR": "great-value"
      },
      "port_mapping": [8000, 4000],
      "container_name": "bbangert/ap-loadtester:latest",
      "cmd": "./apenv/bin/aplt_testplan wss://autopush.stage.mozaws.net 'aplt.scenarios:notification_forever,1000,1,0' --statsd_host=localhost --statsd_port=8125"
    }
  ]
}
"""

sample_toml = """
ecs_name = "ardere-test"
name = "connection loadtest"
description = "autopush: connect and idle forever"


[[steps]]
    name = "***************** RUN #01 ***********************"
    instance_count = 8
    instance_type = "m3.medium"
    container_name = "bbangert/ap-loadtester:latest"
    cmd = "./apenv/bin/aplt_testplan wss://autopush.stage.mozaws.net 'aplt.scenarios:connect_and_idle_forever,10000,5,0'"
    run_max_time = 300
    volume_mapping = "/var/log:/var/log/$RUN_ID:rw"
    docker_series = "push_tests"

[[steps]]
    name = "***************** RUN #02 ***********************"
    instance_count = 8
    run_delay = 330
    instance_type = "m3.medium"
    container_name = "bbangert/ap-loadtester:latest"
    cmd = "./apenv/bin/aplt_testplan wss://autopush.stage.mozaws.net 'aplt.scenarios:connect_and_idle_forever,10000,5,0'"
    run_max_time = 300
    volume_mapping = "/var/log:/var/log/$RUN_ID:rw"
    docker_series = "push_tests"

"""

future_hypothetical_test="""
{
    "name": "TestCluster",
    "instance_count": 1,
    "instance_type": "t2.medium",
    "run_max_time": 140,
    "container_name": "bbangert/pushgo:1.5rc4",
    "port_mapping": "8080,8081,3000,8082",
    "load_balancer": {
        "env_var": "TEST_CLUSTER",
        "ping_path": "/status/health",
        "ping_port": 8080,
        "ping_protocol": "http",
        "listeners": [
            {
                "listen_protocol": "ssl",
                "listen_port": 443,
                "backend_protocol": "tcp",
                "backend_port": 8080
            },
            {
                "listen_protocol": "https",
                "listen_port": 9000,
                "backend_protocol": "http",
                "backend_port": 8090
            }
        ]
    }
}
"""