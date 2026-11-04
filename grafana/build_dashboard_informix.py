#!/usr/bin/env python3
"""Add the estate overview and Informix rows to grafana/dashboards/corewatch.json.

Kept as a script so the change is reviewable and repeatable: it removes any
panels it added before, then inserts them again after the Db2 row. Run it from
the repository root after editing the panel list below.
"""

from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent / "dashboards" / "corewatch.json"
DS = {"type": "prometheus", "uid": "prometheus"}
FIRST_ID = 100  # panels added here use ids from 100 up
S = 'server=~"$server"'


def target(expr: str, legend: str = "{{server}}", ref: str = "A") -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref}


def thresholds(*steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def stat(title, exprs, desc, unit="short", steps=(("green", None),), mappings=None, text="value_and_name"):
    return {
        "title": title, "type": "stat", "description": desc,
        "targets": [target(*(e if isinstance(e, tuple) else (e,)), **{"ref": chr(65 + i)}) for i, e in enumerate(exprs)],
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": thresholds(*steps), "color": {"mode": "thresholds"},
                                     "mappings": mappings or []}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background", "graphMode": "none",
                    "textMode": text},
    }


def series(title, exprs, desc, unit="short", **defaults):
    return {
        "title": title, "type": "timeseries", "description": desc,
        "targets": [target(e[0], e[1], chr(65 + i)) for i, e in enumerate(exprs)],
        "fieldConfig": {"defaults": dict({"unit": unit}, **defaults), "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
    }


def bars(title, expr, legend, desc, unit="percentunit", steps=(("green", None), ("orange", 0.9), ("red", 0.97))):
    return {
        "title": title, "type": "bargauge", "description": desc,
        "targets": [target(expr, legend)],
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": thresholds(*steps), "color": {"mode": "thresholds"},
                                     "min": 0, "max": 1}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "orientation": "horizontal", "displayMode": "basic"},
    }


def row(title):
    return {"title": title, "type": "row", "collapsed": False, "panels": []}


ONLINE = [{"type": "value", "options": {"1": {"text": "On-Line", "color": "green"},
                                         "0": {"text": "Not On-Line", "color": "red"}}}]

OVERVIEW = [
    row("Estate overview"),
    (stat("Db2 databases up", [("count(db2_up == 1) or vector(0)", "up"), ("count(db2_up) or vector(0)", "monitored")],
          "Db2 databases the exporter can reach, out of those it is configured to monitor. If the two numbers differ, "
          "look at the Db2 row below or the Db2Down alert.", text="value_and_name"), 4, 4),
    (stat("Informix servers up", [("count(informix_up == 1) or vector(0)", "up"),
                                  ("count(informix_up) or vector(0)", "monitored")],
          "Informix servers the exporter can reach, out of those it is configured to monitor. Monitoring is per "
          "server (instance), through the sysmaster database.", text="value_and_name"), 4, 4),
    (stat("Firing pages", [('count(ALERTS{alertstate="firing", severity="page"}) or vector(0)', "pages")],
          "Alerts with severity page that are firing now: a member-facing outage, or one that is close. "
          "Details in Alertmanager (http://localhost:9093).",
          steps=(("green", None), ("red", 1)), text="value"), 4, 4),
    (stat("Firing tickets", [('count(ALERTS{alertstate="firing", severity="ticket"}) or vector(0)', "tickets")],
          "Alerts with severity ticket that are firing now: fix during business hours.",
          steps=(("green", None), ("orange", 1)), text="value"), 4, 4),
    (stat("Monitoring queries failing", [("count(db2_query_success == 0 or informix_query_success == 0) or vector(0)",
                                          "failing")],
          "Monitoring queries that failed on their last run, across both engines. Usually a missing privilege or a "
          "column that does not exist on an older release. The other queries keep working.",
          steps=(("green", None), ("orange", 1)), text="value"), 4, 4),
    (stat("Exporter", [("up{job=\"corewatch\"}", "exporter")],
          "1 when Prometheus can scrape the corewatch exporter. When it is 0 every database alert is blind, which "
          "is why CorewatchExporterDown pages.",
          steps=(("red", None), ("green", 1)), mappings=[{"type": "value", "options": {
              "1": {"text": "Scraping", "color": "green"}, "0": {"text": "Down", "color": "red"}}}], text="value"), 4, 4),
]

INFORMIX = [
    row("Informix"),
    (stat("Server up", [f"informix_up{{{S}}}"],
          "1 means the exporter logged in to sysmaster over DRDA and ran its test query. 0 means it could not: the "
          "server is down or not On-Line, the DRDA listener (drsoctcp) is not running, the network path is broken, "
          "or the monitoring user was refused. Check this first when other Informix panels go blank.",
          steps=(("red", None), ("green", 1))), 4, 4),
    (stat("Server mode", [f"informix_server_online{{{S}}}"],
          "From sysshmvals.sh_mode, the same mode onstat - prints. On-Line is the only mode in which applications "
          "work normally. Quiescent, single-user, recovery or shutdown show as Not On-Line and InformixNotOnline pages.",
          steps=(("red", None), ("green", 1)), mappings=ONLINE), 4, 4),
    (stat("Logical logs in use", [f"informix_logical_log_utilization_ratio{{{S}}}"],
          "Share of logical logs that cannot be reused yet. A log is reusable only when it is backed up, is not the "
          "current log and holds no part of an open transaction. At 100% the server blocks every update until a log "
          "is freed. Ticket at 70%, page at 85%.",
          unit="percentunit", steps=(("green", None), ("orange", 0.7), ("red", 0.85))), 4, 4),
    (stat("Oldest transaction spans", [f"informix_oldest_transaction_log_span{{{S}}} / informix_logical_logs{{{S}}}"],
          "How much of the logical log space the oldest open transaction covers. At LTXHWM (70% by default) the server "
          "rolls the transaction back itself, and the rollback needs log space too. corewatch pages at 40% to leave "
          "time to find the session (onstat -x, onstat -u) and end it cleanly.",
          unit="percentunit", steps=(("green", None), ("orange", 0.25), ("red", 0.4))), 4, 4),
    (stat("Since last level-0 archive", [f"max by (server) (informix_backup_level0_age_seconds{{{S}}})"],
          "Age of the oldest level-0 archive across the server's dbspaces (temporary dbspaces are never archived). "
          "A restore can only start from a level-0. Ticket after 26 hours, page after 50. Run bin/corewatch-demo "
          "backup informix to record a new one in the demo.",
          unit="s", steps=(("green", None), ("orange", 93600), ("red", 180000))), 4, 4),
    (stat("Sessions waiting on locks", [f"informix_sessions_waiting_on_locks{{{S}}}"],
          "Sessions blocked right now waiting for a lock another session holds (sysrstcb.lkwait). Short spikes are "
          "normal. A value that stays above 0 means something holds locks too long; onstat -k shows the locks and "
          "onstat -u the waiters. Ticket when more than 5 wait for 5 minutes.",
          steps=(("green", None), ("orange", 1), ("red", 6))), 4, 4),
    (bars("Dbspaces, share used", f"informix_dbspace_utilization_ratio{{{S}}}", "{{server}} {{dbspace}}",
          "Used share of each data and temporary dbspace, summed over its chunks. A full data dbspace makes inserts "
          "into its tables fail. Temporary dbspaces fill and empty with sorts and are not alerted on. Blobspaces and "
          "sbspaces count free space differently and are not shown."), 8, 7),
    (series("Sessions", [(f"informix_sessions_current{{{S}}}", "{{server}}")],
            "User sessions connected to the server (syssessions). The exporter's own session is included. Compare "
            "with the applications' expected pool sizes; a sudden jump often means a connection leak or a retry storm."),
     8, 7),
    (series("Commits and rollbacks per second", [(f"rate(informix_commits_total{{{S}}}[5m])", "{{server}} commits"),
                                                 (f"rate(informix_rollbacks_total{{{S}}}[5m])", "{{server}} rollbacks")],
            "From sysprofile (the counters onstat -p prints). Commits show how much work the server does. Rollbacks "
            "are expected now and then; a rising share means applications are hitting errors, deadlocks or lock "
            "timeouts.", unit="ops"), 8, 7),
    (series("Logical logs", [(f"informix_logical_logs{{{S}}} - informix_logical_logs_free{{{S}}}", "{{server}} in use"),
                             (f"informix_logical_logs_not_backed_up{{{S}}}", "{{server}} waiting for backup")],
            "Logical logs in use and full logs waiting to be backed up. If 'waiting for backup' keeps growing, "
            "continuous log backup (ontape -c, onbar or the ALARMPROGRAM) has stopped, and the server will block "
            "when the last free log fills."), 8, 7),
    (series("Deadlocks and lock timeouts (15 min)",
            [(f"increase(informix_deadlocks_total{{{S}}}[15m])", "{{server}} deadlocks"),
             (f"increase(informix_lock_timeouts_total{{{S}}}[15m])", "{{server}} lock timeouts")],
            "Counts over the last 15 minutes. A deadlock is two sessions each waiting on the other; Informix breaks it "
            "by returning error -143 to one of them. A lock timeout is a session that waited longer than its SET LOCK "
            "MODE TO WAIT limit (error -154). Either one means an application got an error."), 8, 7),
    (series("Buffer pool hit ratio",
            [(f"1 - rate(informix_disk_reads_total{{{S}}}[5m]) / rate(informix_buffer_reads_total{{{S}}}[5m])",
              "{{server}}")],
            "Share of page reads served from the buffer pool instead of disk (1 - dskreads / bufreads, as onstat -p "
            "reports %cached). Busy OLTP servers usually sit above 95%. A lasting drop suggests BUFFERPOOL is too "
            "small for the working set.", unit="percentunit"), 8, 7),
    (series("Checkpoints", [(f"informix_checkpoint_last_duration_seconds{{{S}}}", "{{server}} duration"),
                            (f"informix_checkpoint_last_block_seconds{{{S}}}", "{{server}} transactions blocked")],
            "Duration of the most recent checkpoint and how long it blocked transactions (syscheckpoint). Non-blocking "
            "checkpoints should block for close to zero. Long blocks point at slow disks or too many dirty buffers.",
            unit="s"), 8, 6),
    (stat("Chunks offline", [(f"sum by (server) (informix_chunks_offline{{{S}}})", "{{server}}")],
          "Chunks marked down. The data in an offline chunk cannot be read, and tables in that dbspace return errors. "
          "Usually a disk, path or permission problem; with mirroring the mirror keeps serving. Pages at once.",
          steps=(("green", None), ("red", 1))), 4, 6),
    (stat("Uptime", [f"informix_uptime_seconds{{{S}}}"],
          "Time since the server was started (sysshmvals.sh_boottime). A restart that nobody planned shows up "
          "here and as InformixRestarted.", unit="s"), 4, 6),
    (stat("HA role", [(f"informix_ha_info{{{S}}}", "{{role}}")],
          "Role from sysha_type: standard (no replication), primary, hdr_secondary, sd_secondary or rs_secondary.",
          text="name"), 4, 6),
    (stat("Databases without logging", [(f'count by (server) (informix_database_info{{{S}, logging="none"}}) '
                                         f'or on() vector(0)', "unlogged")],
          "Databases created without transaction logging cannot roll back, cannot be restored to a point in time and "
          "cannot replicate with HDR or RSS. Sometimes deliberate for scratch data; worth knowing about.",
          steps=(("green", None), ("orange", 1))), 4, 6),
]


def place(items, y0):
    """Lay panels out left to right in rows of 24 columns."""
    out, x, y, h_row = [], 0, y0, 0
    for item in items:
        if item.get("type") == "row" if isinstance(item, dict) else False:
            if x:
                y += h_row
            item["gridPos"] = {"x": 0, "y": y, "w": 24, "h": 1}
            out.append(item)
            y += 1
            x, h_row = 0, 0
            continue
        panel, w, h = item
        if x + w > 24:
            y += h_row
            x, h_row = 0, 0
        panel["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
        panel["datasource"] = DS
        out.append(panel)
        x += w
        h_row = max(h_row, h)
    return out, y + h_row


HOST_DESCRIPTIONS = {
    "Last exit code": "Exit code of each batch job's last finished run, written by corewatch_job.sh. Anything but 0 "
    "is a failure and JobFailed pages once the job is no longer running.",
    "Since last success": "Time since each job last succeeded. Compared with the job's --max-age, this is what lets "
    "JobOverdue catch a job that never started, which a failure alert alone cannot do.",
    "Run duration": "How long each job's last run took. JobRunningLong opens a ticket when a running job passes "
    "twice its 7-day average.",
    "Permanent errors in errpt": "Permanent (type P) entries in the AIX error report by class: H is hardware, "
    "S software. Hardware errors page: a disk, adapter or memory fault usually shows here first.",
    "Disk paths not Enabled": "MPIO paths (lspath) in any state other than Enabled. One failed path means the disk "
    "has lost its redundancy even though it still works. Pages after 5 minutes.",
    "Stale partitions": "Stale physical partitions per volume group (lsvg): the LVM mirror copies are out of sync, "
    "usually after a disk error. Resynchronize with syncvg once the cause is fixed.",
    "OS level": "AIX technology level and service pack (oslevel -s). Useful when comparing partitions before "
    "patching.",
    "Filesystems, share used": "Used share of each filesystem. Ticket at 90%, page at 97%. On Linux an extra alert "
    "projects the last hour's growth and warns four hours before a filesystem fills.",
    "Paging space used": "AIX paging space in use (lsps). Sustained use above 70% means real memory is short and "
    "performance is already suffering.",
    "CPU busy": "Share of CPU time not idle, per Linux host, from node_exporter.",
    "Memory available": "Memory the kernel can hand to applications without swapping, as a share of the total.",
    "Reboot pending": "1 when the host has patches installed that need a reboot to take effect. Ticket after a week.",
    "Failed multipath paths": "Linux multipath paths in failed state. As on AIX, one failed path means a disk has "
    "lost its redundancy. Pages after 5 minutes.",
    "Firing alerts": "Firing alerts by severity over time, from Prometheus's own ALERTS series.",
}


def reset_layout(panels):
    """Undo an earlier run: put the Db2 row back at the top and close the gap
    where the Informix row was, so running this script twice changes nothing."""
    db2 = next(p for p in panels if p["type"] == "row" and p["title"] == "Db2")
    offset = db2["gridPos"]["y"]
    for p in panels:
        p["gridPos"]["y"] -= offset
    batch = next(p for p in panels if p["type"] == "row" and p["title"] == "Batch jobs")
    db2_bottom = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in panels if p["gridPos"]["y"] < batch["gridPos"]["y"])
    gap = batch["gridPos"]["y"] - db2_bottom
    for p in panels:
        if p["gridPos"]["y"] >= batch["gridPos"]["y"]:
            p["gridPos"]["y"] -= gap


def main():
    d = json.loads(PATH.read_text())
    panels = [p for p in d["panels"] if p.get("id", 0) < FIRST_ID]
    reset_layout(panels)
    for p in panels:
        if p["type"] != "row" and not p.get("description") and p["title"] in HOST_DESCRIPTIONS:
            p["description"] = HOST_DESCRIPTIONS[p["title"]]
    overview, y = place(OVERVIEW, 0)
    shift = y
    db2_end = next(p["gridPos"]["y"] for p in panels if p["type"] == "row" and p["title"] == "Batch jobs")
    informix, y_after = place(INFORMIX, db2_end + shift)
    shift_after = y_after - (db2_end + shift)
    for p in panels:
        p["gridPos"]["y"] += shift + (shift_after if p["gridPos"]["y"] >= db2_end else 0)
    new = overview + informix
    for i, p in enumerate(new):
        p["id"] = FIRST_ID + i
    d["panels"] = sorted(panels + new, key=lambda p: (p["gridPos"]["y"], p["gridPos"]["x"]))
    d["title"] = "corewatch: Db2, Informix, AIX and Linux"
    d["tags"] = ["corewatch", "db2", "informix", "aix", "linux"]
    d["description"] = ("Db2 and Informix database health, batch jobs, and the AIX and Linux hosts underneath. "
                        "Hover the (i) next to a panel title for what it shows and when to worry.")
    variables = [v for v in d["templating"]["list"] if v["name"] != "server"]
    variables.append({
        "name": "server", "label": "Informix server", "type": "query", "datasource": DS,
        "query": {"query": "label_values(informix_up, server)", "refId": "B"},
        "refresh": 2, "includeAll": True, "multi": True, "current": {"text": "All", "value": "$__all"},
        "allValue": ".*",
    })
    for v in variables:
        if v["name"] == "database":
            v["label"] = "Db2 database"
    d["templating"]["list"] = variables
    d["version"] = d.get("version", 1) + 1
    PATH.write_text(json.dumps(d, indent=2) + "\n")
    print(f"{PATH}: {len(d['panels'])} panels")


if __name__ == "__main__":
    main()
