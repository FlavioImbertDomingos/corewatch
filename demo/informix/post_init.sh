# shellcheck shell=bash
# corewatch demo: one-time setup for the Informix Developer Edition container.
#
# The IBM image sources (does not execute) this file after its first disk
# initialization, as user informix, when RUN_FILE_POST_INIT=post_init.sh and the
# file is mounted in /opt/ibm/files. (/opt/ibm/config would also work, but the
# image refuses to start unless that directory is mode 777.) It never runs again
# on later starts.
# Because it is sourced into the image's entry script, it must not call exit or
# set -e; everything runs inside a function and returns.
#
# What it sets up:
#   1. the informix password from INFORMIX_PASSWORD (replaces the image default)
#   2. a least-privilege OS user "corewatch" for the exporter, password from
#      COREWATCH_INFORMIX_PASSWORD; it gets CONNECT on sysmaster and nothing else
#   3. a fixed-size data dbspace (datadbs) and a temporary dbspace (tempdbs)
#   4. a small logged demo database, corebank, in datadbs
#   5. a level-0 archive, so the backup panels start with real data
#
# Output goes to /opt/ibm/data/logs/corewatch_post_init.log in the container.

corewatch_post_init() {
    local spaces=/opt/ibm/data/spaces

    echo "corewatch post-init started $(date -u +%FT%TZ)"

    if [ -n "${INFORMIX_PASSWORD:-}" ]; then
        echo "informix:${INFORMIX_PASSWORD}" | sudo chpasswd && echo "informix password set"
    fi

    if [ -n "${COREWATCH_INFORMIX_PASSWORD:-}" ]; then
        id corewatch >/dev/null 2>&1 || sudo useradd -M -s /sbin/nologin corewatch
        echo "corewatch:${COREWATCH_INFORMIX_PASSWORD}" | sudo chpasswd && echo "monitoring user corewatch ready"
    else
        echo "COREWATCH_INFORMIX_PASSWORD is not set; the exporter has no user to log in with"
    fi

    # Raw chunk files must exist, belong to informix and be mode 660.
    for chunk in datadbs.000 tempdbs.000; do
        touch "$spaces/$chunk" && chmod 660 "$spaces/$chunk"
    done
    onspaces -c -d datadbs -p "$spaces/datadbs.000" -o 0 -s 204800 # 200 MB, fixed size
    onspaces -c -t -d tempdbs -p "$spaces/tempdbs.000" -o 0 -s 51200

    dbaccess - - <<'SQL'
CREATE DATABASE corebank IN datadbs WITH LOG;
CREATE TABLE accounts (
    account_id  INTEGER PRIMARY KEY,
    holder      VARCHAR(60) NOT NULL,
    balance     DECIMAL(14,2) NOT NULL
) LOCK MODE ROW;
CREATE TABLE postings (
    posting_id  SERIAL PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts,
    amount      DECIMAL(14,2) NOT NULL,
    posted_at   DATETIME YEAR TO SECOND DEFAULT CURRENT YEAR TO SECOND
) LOCK MODE ROW;
INSERT INTO accounts VALUES (1, 'Operating account', 250000.00);
INSERT INTO accounts VALUES (2, 'Member savings', 1200.00);
INSERT INTO accounts VALUES (3, 'Member checking', 840.50);
INSERT INTO accounts VALUES (4, 'Loan servicing', 98000.00);
SQL

    # Monitoring needs only CONNECT on sysmaster, where its tables are readable
    # by any connected user. No access to corebank or any business database.
    if id corewatch >/dev/null 2>&1; then
        echo "GRANT CONNECT TO corewatch;" | dbaccess sysmaster -
    fi

    # TAPEDEV is /dev/null in this image, so this records a level-0 archive
    # without writing one. Real servers archive to tape, disk or a storage manager.
    ontape -s -L 0 -t /dev/null </dev/null >/dev/null && echo "level-0 archive recorded"

    echo "corewatch post-init finished $(date -u +%FT%TZ)"
    return 0
}

corewatch_post_init >>/opt/ibm/data/logs/corewatch_post_init.log 2>&1
