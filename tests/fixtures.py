sample_basic_test_plan = """
{
  "ecs_name": "ardere-test",
  "name": "Loadtest",
  "description": "Run all APLT scenarios",
  "steps": [
    {
      "name": "TestCluster",
      "instance_count": 1,
      "instance_type": "t2.medium",
      "run_max_time": 40,
      "cpu_units": 2030,
      "container_name": "bbangert/ap-loadtester:latest",
      "additional_command_args": "./apenv/bin/aplt_testplan wss://autopush.stage.mozaws.net 'aplt.scenarios:notification_forever,1000,1,0'",
      "docker_series": "push_tester"
    }
  ]
}
"""