#!/usr/bin/ksh
# corewatch AIX collector
#
# Writes host metrics that only AIX has (errpt, LVM stale partitions, MPIO
# paths, paging space, oslevel, interim fixes, mksysb age) to a .prom file.
# Run it from root's crontab every 5 minutes:
#
#   */5 * * * * /opt/corewatch/corewatch_aix.sh >/dev/null 2>&1
#
# Serve the file with node_exporter's textfile collector, or with
# textfile_server.py where node_exporter is not installed.
#
# Settings (environment variables):
#   COREWATCH_DIR       output directory           (default /var/lib/corewatch)
#   ERRPT_WINDOW_MIN    errpt look-back in minutes (default 15)
#   MKSYSB_DIR          directory holding mksysb images (optional)
#   DB2_INSTANCE_USER   instance owner, to count db2diag records (optional)

COREWATCH_DIR=${COREWATCH_DIR:-/var/lib/corewatch}
ERRPT_WINDOW_MIN=${ERRPT_WINDOW_MIN:-15}
OUT="$COREWATCH_DIR/aix.prom"
TMP="$OUT.$$"

mkdir -p "$COREWATCH_DIR" || exit 1
trap 'rm -f "$TMP"' EXIT

metric() { # name help type
    print "# HELP $1 $2"
    print "# TYPE $1 $3"
}

{
    # --- OS level --------------------------------------------------------
    metric aix_oslevel_info "AIX technology level and service pack." gauge
    print "aix_oslevel_info{oslevel=\"$(oslevel -s 2>/dev/null)\"} 1"

    # --- Error log -------------------------------------------------------
    # errpt -s takes mmddhhmmyy. AIX date cannot subtract, so perl does it.
    START=$(perl -e 'use POSIX; print strftime("%m%d%H%M%y", localtime(time - 60 * $ARGV[0]))' "$ERRPT_WINDOW_MIN")
    metric aix_errpt_entries "errpt entries in the look-back window by type and class." gauge
    errpt -s "$START" 2>/dev/null | awk '
        NR > 1 { n[$3 " " $4]++ }
        END {
            split("P T I U", types, " "); split("H S O U", classes, " ")
            for (t = 1; t <= 4; t++) for (c = 1; c <= 4; c++) {
                key = types[t] " " classes[c]
                printf "aix_errpt_entries{type=\"%s\",class=\"%s\"} %d\n", types[t], classes[c], n[key] + 0
            }
        }'

    # --- Volume groups ---------------------------------------------------
    VGS=$(lsvg -o 2>/dev/null)
    metric aix_vg_stale_pps "Stale physical partitions in the volume group." gauge
    for vg in $VGS; do
        lsvg "$vg" 2>/dev/null | awk -v vg="$vg" '
            /STALE PPs:/ { split($0, a, "STALE PPs:"); stale = a[2] + 0 }
            END { printf "aix_vg_stale_pps{vg=\"%s\"} %d\n", vg, stale + 0 }'
    done
    metric aix_vg_free_bytes "Free space in the volume group." gauge
    for vg in $VGS; do
        lsvg "$vg" 2>/dev/null | awk -v vg="$vg" '
            /FREE PPs:/ { split($0, a, "FREE PPs:"); split(a[2], b, "("); free = b[2] + 0 }
            END { printf "aix_vg_free_bytes{vg=\"%s\"} %d\n", vg, free * 1048576 }'
    done

    # --- MPIO paths ------------------------------------------------------
    metric aix_mpio_paths "Disk paths by status. Anything other than Enabled needs a look." gauge
    lspath 2>/dev/null | awk '
        { n[$1]++ }
        END {
            n["Enabled"] += 0; n["Failed"] += 0; n["Missing"] += 0
            for (s in n) printf "aix_mpio_paths{status=\"%s\"} %d\n", s, n[s]
        }'

    # --- Paging space ----------------------------------------------------
    metric aix_paging_space_used_ratio "Paging space in use, 0 to 1." gauge
    lsps -s 2>/dev/null | awk 'NR == 2 { gsub("%", "", $2); printf "aix_paging_space_used_ratio %.2f\n", $2 / 100 }'

    # --- Filesystems (for hosts without node_exporter) --------------------
    metric aix_filesystem_used_ratio "Filesystem space in use, 0 to 1." gauge
    df -k 2>/dev/null | awk '
        NR > 1 && $2 ~ /^[0-9]+$/ && $2 > 0 {
            gsub("%", "", $4)
            printf "aix_filesystem_used_ratio{mountpoint=\"%s\"} %.2f\n", $7, $4 / 100
        }'

    # --- Interim fixes ---------------------------------------------------
    metric aix_interim_fixes "Interim fixes (emgr) installed." gauge
    print "aix_interim_fixes $(emgr -l 2>/dev/null | grep -c '^[0-9]')"

    # --- mksysb age ------------------------------------------------------
    if [[ -n "$MKSYSB_DIR" && -d "$MKSYSB_DIR" ]]; then
        metric aix_mksysb_age_seconds "Age of the newest mksysb image." gauge
        perl -e '
            my $newest = 0;
            for my $f (glob("$ARGV[0]/*")) { my $m = (stat($f))[9]; $newest = $m if -f $f && $m > $newest }
            printf "aix_mksysb_age_seconds %d\n", time - $newest if $newest;' "$MKSYSB_DIR"
    fi

    # --- db2diag (optional) ----------------------------------------------
    if [[ -n "$DB2_INSTANCE_USER" ]]; then
        metric db2_diag_severe_records "Severe and critical db2diag.log records in the last 15 minutes." gauge
        COUNT=$(su - "$DB2_INSTANCE_USER" -c "db2diag -H 15m -level Severe,Critical 2>/dev/null" | grep -c 'LEVEL: ')
        print "db2_diag_severe_records{instance=\"$DB2_INSTANCE_USER\"} ${COUNT:-0}"
    fi

    metric aix_collector_last_run_seconds "Unix time this collector last finished." gauge
    print "aix_collector_last_run_seconds $(perl -e 'print time')"
} > "$TMP"

mv "$TMP" "$OUT"
