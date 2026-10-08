#!/bin/sh
# Demo only: pretends to be a batch scheduler so the job metrics have data.
# "posting" runs every minute and fails about one run in five.
# "month_end" never runs after its first success, so its max-age alert fires.
export COREWATCH_DIR=/textfiles
/corewatch_job.sh month_end --max-age 300 -- true
n=0
while true; do
    n=$((n + 1))
    if [ $((n % 5)) -eq 0 ]; then
        /corewatch_job.sh posting --max-age 600 -- sh -c 'sleep 3; exit 1'
    else
        /corewatch_job.sh posting --max-age 600 -- sleep 5
    fi
    sleep 55
done
