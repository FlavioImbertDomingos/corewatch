#!/bin/sh
# corewatch job wrapper: record the outcome of a batch job.
#
#   corewatch_job.sh <job-name> [--max-age SECONDS] -- command [args...]
#
# Example crontab line for a nightly job that must succeed at least every 26 hours:
#   0 1 * * * /opt/corewatch/corewatch_job.sh nightly_close --max-age 93600 -- /opt/app/close.sh
#
# Plain POSIX sh, so the same file runs on AIX and Linux. It writes
# $COREWATCH_DIR/job_<name>.prom and exits with the job's own exit code.
#
# Three alerts read these metrics: the job failed, the job ran far longer than
# usual, and the job has not succeeded within --max-age.

COREWATCH_DIR=${COREWATCH_DIR:-/var/lib/corewatch}

usage() { echo "usage: $0 <job-name> [--max-age SECONDS] -- command [args...]" >&2; exit 64; }

[ $# -ge 3 ] || usage
JOB=$1; shift
MAX_AGE=0
if [ "$1" = "--max-age" ]; then MAX_AGE=$2; shift 2; fi
[ "$1" = "--" ] || usage
shift
[ $# -ge 1 ] || usage

case $JOB in *[!A-Za-z0-9_]*) echo "job name may hold only letters, digits and _" >&2; exit 64;; esac

now() { perl -e 'print time' 2>/dev/null || date +%s; }

mkdir -p "$COREWATCH_DIR" || exit 1
OUT="$COREWATCH_DIR/job_$JOB.prom"
TMP="$OUT.$$"

# Keep the previous success time, so a failed run does not erase it.
LAST_SUCCESS=0
if [ -f "$OUT" ]; then
    LAST_SUCCESS=$(awk '$1 ~ /^corewatch_job_last_success_seconds/ { print $2 }' "$OUT")
    LAST_SUCCESS=${LAST_SUCCESS:-0}
fi

write() { # start end duration exit running
    {
        echo "# HELP corewatch_job_last_start_seconds Unix time the job last started."
        echo "# TYPE corewatch_job_last_start_seconds gauge"
        echo "corewatch_job_last_start_seconds{job_name=\"$JOB\"} $1"
        echo "# HELP corewatch_job_last_end_seconds Unix time the job last ended."
        echo "# TYPE corewatch_job_last_end_seconds gauge"
        echo "corewatch_job_last_end_seconds{job_name=\"$JOB\"} $2"
        echo "# HELP corewatch_job_last_duration_seconds How long the last finished run took."
        echo "# TYPE corewatch_job_last_duration_seconds gauge"
        echo "corewatch_job_last_duration_seconds{job_name=\"$JOB\"} $3"
        echo "# HELP corewatch_job_last_exit_code Exit code of the last finished run."
        echo "# TYPE corewatch_job_last_exit_code gauge"
        echo "corewatch_job_last_exit_code{job_name=\"$JOB\"} $4"
        echo "# HELP corewatch_job_running 1 while the job is running."
        echo "# TYPE corewatch_job_running gauge"
        echo "corewatch_job_running{job_name=\"$JOB\"} $5"
        echo "# HELP corewatch_job_last_success_seconds Unix time the job last succeeded."
        echo "# TYPE corewatch_job_last_success_seconds gauge"
        echo "corewatch_job_last_success_seconds{job_name=\"$JOB\"} $LAST_SUCCESS"
        echo "# HELP corewatch_job_max_age_seconds Longest allowed gap between successes, 0 for no limit."
        echo "# TYPE corewatch_job_max_age_seconds gauge"
        echo "corewatch_job_max_age_seconds{job_name=\"$JOB\"} $MAX_AGE"
    } > "$TMP" && mv "$TMP" "$OUT"
}

PREV_END=0; PREV_EXIT=0; PREV_DUR=0
if [ -f "$OUT" ]; then
    PREV_END=$(awk '$1 ~ /^corewatch_job_last_end_seconds/ { print $2 }' "$OUT"); PREV_END=${PREV_END:-0}
    PREV_EXIT=$(awk '$1 ~ /^corewatch_job_last_exit_code/ { print $2 }' "$OUT"); PREV_EXIT=${PREV_EXIT:-0}
    PREV_DUR=$(awk '$1 ~ /^corewatch_job_last_duration_seconds/ { print $2 }' "$OUT"); PREV_DUR=${PREV_DUR:-0}
fi

START=$(now)
write "$START" "$PREV_END" "$PREV_DUR" "$PREV_EXIT" 1

"$@"
CODE=$?

END=$(now)
[ "$CODE" -eq 0 ] && LAST_SUCCESS=$END
write "$START" "$END" "$((END - START))" "$CODE" 0
exit "$CODE"
