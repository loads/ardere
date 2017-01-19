#!/bin/sh
#
# waits for a cluster to be ready + a run_delay
#
# cluster readiness is indicated by existence of ready_url, containing
# a timestamp (seconds since epoch) of when it was made so. timestamp
# is factored into the run_delay.
#

# Polling frequency in seconds
POLL_TIME=4

if [ $# != 2 ]; then
    echo "usage $0: ready_url run_delay"
    exit 1
fi
READY_URL=$1
RUN_DELAY=$2

# XXX: a random jitter, backoff?
JITTER=0

while true; do
    START_TIME=`wget -qO- ${READY_URL}` && break
    sleep $(( ${POLL_TIME} + ${JITTER} ))
done

CURRENT_TIME=`date +%s`
SINCE=$(( ${CURRENT_TIME} - ${START_TIME} ))
if [ ${SINCE} -lt 0 ]; then
    echo "Clock skew: ${SINCE}" >&2
    SINCE=0
fi

RUN_DELAY=$(( ${RUN_DELAY} - ${SINCE} ))
if [ ${RUN_DELAY} -gt 0 ]; then
    FMT_START_TIME=`date '+%FT%T+00:00' -d @${START_TIME}`
    echo "Cluster ready @ ${FMT_START_TIME}" \
         "(sleeping for run_delay=${RUN_DELAY}s)"
    sleep $RUN_DELAY
fi
