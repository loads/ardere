# First some funky path manipulation so that we can work properly in
# the AWS environment
import sys
import os
dir_path = os.path.dirname(os.path.realpath(__file__))
sys.path.append(dir_path)

from ardere.step_functions import AsynchronousPlanRunner


def populate_missing_instances(event, context):
    runner = AsynchronousPlanRunner(event, context)
    return runner.populate_missing_instances()

def ensure_metrics_available(event, context):
    runner = AsynchronousPlanRunner(event, context)
    return runner.ensure_metrics_available()

def create_ecs_services(event, context):
    runner = AsynchronousPlanRunner(event, context)
    return runner.create_ecs_services()


def wait_for_cluster_ready(event, context):
    runner = AsynchronousPlanRunner(event, context)
    return runner.wait_for_cluster_ready()


def signal_cluster_start(event, context):
    runner = AsynchronousPlanRunner(event, context)
    return runner.signal_cluster_start()


def check_for_cluster_done(event, context):
    runner = AsynchronousPlanRunner(event, context)
    return runner.check_for_cluster_done()


def cleanup_cluster(event, context):
    runner = AsynchronousPlanRunner(event, context)
    return runner.cleanup_cluster()


def check_drain(event, context):
    return AsynchronousPlanRunner(event, context).check_drained()
