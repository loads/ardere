# Requirements

A Cloud Formation template will construct the required elements including:

* S3 Bucket - for state coordination
* Lambda Step Framework - state machine for test execution management
* Lambda functions
  * 1/m cloudwatch scheduled event -> Lambda to watch for S3 Service Running file
  * 1/m cloudwatch scheduled event -> Lambda to see Test Plan is done running
  * Lambda function to load test plan and start a run
  * Lambda function to shut down ECS services for a run
  * Lambda function/app providing API for the service via HTTP API Gateway
* ECS template construction + Auto-Scaling Group - for test bed creation

All containers used must include a Waiter program. The Waiter program:
* Takes 3 args, S3 file to watch for, service UUID to trigger on, time to wait after trigger
* Polls S3 file to watch for, if it exists, loads its content to determine appropriate remaining time to wait if needed before exiting
* When time has elapsed,Waiter terminates

# Lambda State machine description for Test Run Execution:



# Lambda Usage

Due to the heavy concurrency nature of making AWS calls, Node is used to make as many calls at once as possible so the Lambda may run as briefly as possible.

## SetupTestPlan Lambda, runs as first step of test run state machine
1. Load given S3/PlanUUID JSON/YAML file describing test plan
2. Parse test plan steps for steps that result in DNS names.
3. Create CloudFormation template based on test plan, with ELB's for DNS names.
4. Start CloudFormation stack, get stack name/id.
5. Create S3/PlanUUID/RunUUID/ dir
6. Create necessary ECS Service JSON files, send off ecs:create_service calls
  * Wrap container commands with Waiter and appropriate wait time
7. Save S3/PlanUUID/RunUUID/services.json file with hash of:
  * CloudFormation stack name.
  * Container count needed for each service name.
8. Done

## AllRunning Lambda, running once per min via CloudWatch Scheduled Event
1. Load S3/AllRunningTaskList, list of current Task ID’s being monitored
2. Call GetTask with short time-out until timeout occurs to get any new tasks to watch
3. Iterate through task list…
  * Load S3/PlanUUID/RunUUID/services.json to find CF instance name.
  * Call CF DescribeStacks to pull stack information and steps that have completed.
  * If stack creation has failed, call Fail and stop.
  * If stack is complete, save out ECS service identifiers, and proceed to ECS Service verification.
  * Call ECS DescribeServices with service lists, determine if all deployments for all services have PendingCount 0 or not
  * Signal with Success/Fail if all files are there, or HeartBeat if not.
4. Save new TaskList back out, subtracting tasks that had Success/Fail called
5. Done

## StartTestPlan Lambda, runs in state machine after AllRunning has checked in with Success
1. Create S3/PlanUUID/RunUUID/RUN.txt with contents as the UTC seconds since epoch
2. Done

## TestPlanDone Lambda, running once per min via CloudWatch Scheduled Event
1. Load S3/DoneTaskList, list of current Task ID’s being monitored that are running
2. Call GetTask with short time-out until timeout occurs to get all new tasks
3. Iterate through task list…
  * Load S3/PlanUUID/RunUUID/services.json to get services running and time they should be stopped
  * Load S3/PlanUUID/RunUUID/RUN.txt to see time the service started for the maths
  * Shutdown any services that have run for as long as they’re supposed to with ecs_update_service
  * If the entire test is done, signal Success/Fail, HeartBeat if not
4. Save new TaskList back out, subtracting tasks that had Success/Fail called
5. Done

## CleanUp Lambda, runs last in state machine, or to cleanup if fails occur
1. Load S3/PlanUUID/RunUUID/services.json to load all involved services
2. Iterate through service ARN’s
  * Ecs_update_service to set desired tasks to 0
  * Ecs_delete_service
3. Write S3/PlanUUID/RunUUID/FINISHED.txt with current time
4. Done

