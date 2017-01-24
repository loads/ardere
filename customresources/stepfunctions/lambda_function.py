"""Step Function State Machine CloudFormation Custom resoure

Note that some portions of this are copy/paste from:
https://github.com/humilis/humilis-firehose-resource/

As such those portions of code are MIT licensed per:
https://github.com/humilis/humilis-firehose-resource/blob/master/LICENSE

"""
from __future__ import print_function

import json
import sys
import urllib2

import boto3


SUCCESS = "SUCCESS"
FAILED = "FAILED"
FINAL_STATES = ['ACTIVE']

client = boto3.client('stepfunctions')


def send(event, context, response_status, reason=None, response_data=None,
         physical_resource_id=None):
    response_data = response_data or {}
    reason = reason or "See the details in CloudWatch Log Stream: " + \
        context.log_stream_name
    physical_resource_id = physical_resource_id or context.log_stream_name
    response_body = json.dumps(
        {
            'Status': response_status,
            'Reason': reason,
            'PhysicalResourceId': physical_resource_id,
            'StackId': event['StackId'],
            'RequestId': event['RequestId'],
            'LogicalResourceId': event['LogicalResourceId'],
            'Data': response_data
        }
    )

    opener = urllib2.build_opener(urllib2.HTTPHandler)
    request = urllib2.Request(event["ResponseURL"], data=response_body)
    request.add_header("Content-Type", "")
    request.add_header("Content-Length", len(response_body))
    request.get_method = lambda: 'PUT'
    try:
        response = opener.open(request)
        print("Status code: {}".format(response.getcode()))
        print("Status message: {}".format(response.msg))
        return True
    except urllib2.HTTPError as exc:
        print("Failed executing HTTP request: {}".format(exc.code))
        return False


def replace_bools(data):
    """Replace a nesting JSON structure such that the original true becomes
    an actual True value for proper JSON output"""
    new_dict = {}
    for key, value in data.iteritems():
        if value == "false":
            new_dict[key] = False
        elif value == "true":
            new_dict[key] = True
        elif isinstance(value, dict):
            new_dict[key] = replace_true_with_True(value)
        else:
            new_dict[key] = value
    return new_dict


def create_step_function(event, context):
    """Create a Step Function state machine

    The resource properties passed in must include:

        Name - State machine name to use
        Definition - Dict structure of the state machine definition
        Role - The Role Arn to use for the state machine.

    """
    sfn_cfg = event["ResourceProperties"]
    state_machine = sfn_cfg["StateMachine"]
    if not isinstance(state_machine, dict):
        raise Exception("StateMachine must be a dict")

    state_machine_name = state_machine["Name"]
    state_machine_def = state_machine["Definition"]
    state_machine_def = replace_bools(state_machine_def)
    state_machine_role = state_machine["Role"]
    print("Creating state machine with definition: %s" % state_machine_def)
    response = client.create_state_machine(
        name=state_machine_name,
        definition=json.dumps(state_machine_def),
        roleArn=state_machine_role
    )
    if "stateMachineArn" not in response:
        raise Exception("No state machine ARN in response: %s" % response)

    arn = response["stateMachineArn"]

    send(event, context, SUCCESS, physical_resource_id=arn,
         response_data={"Arn": arn})


def delete_step_function(event, context):
    """Delete a Step Function state machine"""
    sfn_arn = event["PhysicalResourceId"]
    try:
        client.delete_state_machine(
            stateMachineArn=sfn_arn
        )
    except Exception as exc:
        if "InvalidArn" not in  str(exc):
            raise
    send(event, context, SUCCESS, response_data={})


HANDLERS = {
    "Delete": delete_step_function,
    "Update": create_step_function,
    "Create": create_step_function
}


def lambda_handler(event, context):
    handler = HANDLERS.get(event["RequestType"])
    try:
        return handler(event, context)
    except:
        msg = ""
        for err in sys.exc_info():
            msg += "\n{}\n".format(err)
        response_data = {
            "Error": "{} resource failed: {}".format(event["RequestType"], msg)
        }
        print(response_data)
        return send(event, context, FAILED, response_data=response_data)
