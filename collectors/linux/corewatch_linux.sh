#!/usr/bin/env bash
# corewatch Linux (RHEL) collector
#
# node_exporter already covers CPU, memory, disks and filesystems. This script
# adds what it does not: patch status, reboot-required, multipath path health
# and db2diag severity counts. Run it from root's crontab:
#
#   */5 * * * * /opt/corewatch/corewatch_linux.sh >/dev/null 2>&1
#
# Point node_exporter at the output directory:
#   --collector.textfile.directory=/var/lib/corewatch
#
# Settings (environment variables):
#   COREWATCH_DIR       output directory   (default /var/lib/corewatch)
#   CHECK_UPDATES       1 to query dnf for pending security updates (slow; default 0)
#   DB2_INSTANCE_USER   instance owner, to count db2diag records (optional)

set -u
COREWATCH_DIR=${COREWATCH_DIR:-/var/lib/corewatch}
CHECK_UPDATES=${CHECK_UPDATES:-0}
OUT="$COREWATCH_DIR/linux.prom"
TMP=$(mktemp "$OUT.XXXXXX") || exit 1
trap 'rm -f "$TMP"' EXIT
mkdir -p "$COREWATCH_DIR"

metric() { # name help type
    printf '# HELP %s %s\n# TYPE %s %s\n' "$1" "$2" "$1" "$3"
}

{
    # --- OS release ------------------------------------------------------
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        metric linux_os_info "Distribution and version." gauge
        printf 'linux_os_info{id="%s",version="%s",kernel="%s"} 1\n' "${ID:-}" "${VERSION_ID:-}" "$(uname -r)"
    fi

    # --- Reboot required --------------------------------------------------
    if command -v needs-restarting >/dev/null 2>&1; then
        metric linux_reboot_required "1 if updated packages need a reboot to take effect." gauge
        if needs-restarting -r >/dev/null 2>&1; then
            echo "linux_reboot_required 0"
        else
            echo "linux_reboot_required 1"
        fi
    fi

    # --- Pending security updates ----------------------------------------
    if [[ "$CHECK_UPDATES" == "1" ]] && command -v dnf >/dev/null 2>&1; then
        metric linux_security_updates_pending "Security advisories with updates not yet installed." gauge
        count=$(dnf -q updateinfo list --security --updates 2>/dev/null | grep -c . || true)
        echo "linux_security_updates_pending ${count:-0}"
    fi

    # --- Multipath -------------------------------------------------------
    if command -v multipath >/dev/null 2>&1; then
        metric linux_multipath_paths "Multipath paths by state." gauge
        ll=$(multipath -ll 2>/dev/null || true)
        active=$(grep -c ' active ' <<<"$ll" || true)
        failed=$(grep -cE ' (failed|faulty) ' <<<"$ll" || true)
        echo "linux_multipath_paths{state=\"active\"} ${active:-0}"
        echo "linux_multipath_paths{state=\"failed\"} ${failed:-0}"
    fi

    # --- db2diag (optional) ----------------------------------------------
    if [[ -n "${DB2_INSTANCE_USER:-}" ]]; then
        metric db2_diag_severe_records "Severe and critical db2diag.log records in the last 15 minutes." gauge
        count=$(su - "$DB2_INSTANCE_USER" -c "db2diag -H 15m -level Severe,Critical 2>/dev/null" | grep -c 'LEVEL: ' || true)
        echo "db2_diag_severe_records{instance=\"$DB2_INSTANCE_USER\"} ${count:-0}"
    fi

    metric linux_collector_last_run_seconds "Unix time this collector last finished." gauge
    echo "linux_collector_last_run_seconds $(date +%s)"
} > "$TMP"

chmod 644 "$TMP"
mv "$TMP" "$OUT"
trap - EXIT
