"""Informix monitoring queries and how each one turns into metrics.

Informix keeps its monitoring data in the sysmaster database, which describes
the whole server (instance), not one database. So corewatch connects once per
Informix server, to sysmaster, and labels every metric with server="<name>".

The exporter reaches Informix over DRDA with the same ibm_db driver it uses for
Db2, so no Informix client SDK is needed. DRDA must be enabled on the server
(an sqlhosts entry with protocol drsoctcp, usually port 9089).

Every query and column here was run against Informix 15.0.1 Developer Edition.
Column names come back in lower case and character values padded, so the
handlers normalize both. Like the Db2 queries, each one is independent: a
failure is reported as informix_query_success{query}=0 and the rest still run.

The connecting user needs only CONNECT on sysmaster; see docs/privileges-informix.sql.
"""

from __future__ import annotations

from typing import Any, Iterator

from .queries import COUNTER, GAUGE, Query, Sample

PROBE_SQL = "SELECT COUNT(*) AS n FROM systables WHERE tabid = 1"

# name -> (type, help, extra label names)
METRICS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "informix_server_online": (GAUGE, "1 if the server mode is On-Line (sh_mode 5).", ()),
    "informix_server_mode": (GAUGE, "Raw server mode from sysshmvals.sh_mode; 5 is On-Line.", ()),
    "informix_uptime_seconds": (GAUGE, "Seconds since the server was started.", ()),
    "informix_ha_info": (GAUGE, "High-availability role of this server.", ("role",)),
    "informix_sessions_current": (GAUGE, "User sessions connected right now.", ()),
    "informix_sessions_waiting_on_locks": (GAUGE, "Sessions (threads) waiting for a lock right now.", ()),
    "informix_logical_logs": (GAUGE, "Number of logical log files.", ()),
    "informix_logical_logs_free": (
        GAUGE,
        "Logical logs that can be reused: never used, or backed up, not current and older than every open transaction.",
        (),
    ),
    "informix_logical_logs_not_backed_up": (GAUGE, "Full logical logs still waiting to be backed up.", ()),
    "informix_logical_log_utilization_ratio": (GAUGE, "Share of logical logs that cannot be reused yet, 0 to 1.", ()),
    "informix_oldest_transaction_log_span": (
        GAUGE,
        "Logical logs spanned by the oldest open transaction (current log minus the log it began in, plus 1). 0 if none.",
        (),
    ),
    "informix_dbspace_size_bytes": (GAUGE, "Total size of the dbspace's chunks.", ("dbspace",)),
    "informix_dbspace_free_bytes": (GAUGE, "Free space in the dbspace's chunks.", ("dbspace",)),
    "informix_dbspace_utilization_ratio": (
        GAUGE,
        "Used over total pages, 0 to 1. Not reported for blobspaces and sbspaces, which count free space differently.",
        ("dbspace",),
    ),
    "informix_dbspace_info": (GAUGE, "Dbspace type: data, temp, blob or sbspace.", ("dbspace", "type")),
    "informix_chunks_offline": (GAUGE, "Chunks of the dbspace that are offline (down).", ("dbspace",)),
    "informix_backup_found": (GAUGE, "1 if the dbspace has a level-0 archive on record.", ("dbspace",)),
    "informix_backup_level0_age_seconds": (GAUGE, "Seconds since the dbspace's last level-0 archive.", ("dbspace",)),
    "informix_checkpoint_last_duration_seconds": (GAUGE, "Duration of the most recent checkpoint.", ()),
    "informix_checkpoint_last_block_seconds": (GAUGE, "Time transactions were blocked by the most recent checkpoint.", ()),
    "informix_checkpoint_last_timestamp_seconds": (GAUGE, "Unix time of the most recent checkpoint.", ()),
    "informix_database_info": (GAUGE, "One series per database with its logging mode.", ("dbname", "logging")),
    # sysprofile counters (onstat -p), cumulative since start or since onstat -z
    "informix_commits_total": (COUNTER, "Commits (iscommits).", ()),
    "informix_rollbacks_total": (COUNTER, "Rollbacks (isrollbacks).", ()),
    "informix_deadlocks_total": (COUNTER, "Deadlocks detected (deadlks).", ()),
    "informix_lock_timeouts_total": (COUNTER, "Lock wait timeouts (lktouts).", ()),
    "informix_lock_waits_total": (COUNTER, "Lock waits (lockwts).", ()),
    "informix_lock_requests_total": (COUNTER, "Lock requests (lockreqs).", ()),
    "informix_buffer_reads_total": (COUNTER, "Reads satisfied from the buffer pool (bufreads).", ()),
    "informix_disk_reads_total": (COUNTER, "Reads that went to disk (dskreads).", ()),
    "informix_buffer_writes_total": (COUNTER, "Writes to the buffer pool (bufwrites).", ()),
    "informix_disk_writes_total": (COUNTER, "Physical writes to disk (dskwrites).", ()),
    "informix_sequential_scans_total": (COUNTER, "Sequential table scans (seqscans).", ()),
    "informix_checkpoints_total": (COUNTER, "Checkpoints (numckpts).", ()),
    "informix_checkpoint_waits_total": (COUNTER, "Threads that waited for a checkpoint (ckptwts).", ()),
    "informix_buffer_waits_total": (COUNTER, "Waits for a buffer (buffwts).", ()),
    "informix_lock_table_overflows_total": (COUNTER, "Lock table overflows (ovlock).", ()),
    "informix_user_table_overflows_total": (COUNTER, "User table overflows (ovuser).", ()),
    "informix_transaction_table_overflows_total": (COUNTER, "Transaction table overflows (ovtrans).", ()),
}

PROFILE_COUNTERS = {
    "iscommits": "informix_commits_total",
    "isrollbacks": "informix_rollbacks_total",
    "deadlks": "informix_deadlocks_total",
    "lktouts": "informix_lock_timeouts_total",
    "lockwts": "informix_lock_waits_total",
    "lockreqs": "informix_lock_requests_total",
    "bufreads": "informix_buffer_reads_total",
    "dskreads": "informix_disk_reads_total",
    "bufwrites": "informix_buffer_writes_total",
    "dskwrites": "informix_disk_writes_total",
    "seqscans": "informix_sequential_scans_total",
    "numckpts": "informix_checkpoints_total",
    "ckptwts": "informix_checkpoint_waits_total",
    "buffwts": "informix_buffer_waits_total",
    "ovlock": "informix_lock_table_overflows_total",
    "ovuser": "informix_user_table_overflows_total",
    "ovtrans": "informix_transaction_table_overflows_total",
}

# sysha_type.ha_type
HA_ROLES = {0: "standard", 1: "primary", 2: "hdr_secondary", 3: "sd_secondary", 4: "rs_secondary"}


def _rows(rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Lower-case keys and strip padded strings, whatever the driver returns."""
    for row in rows:
        yield {
            str(k).lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
        }


def _num(value: Any) -> float:
    return 0.0 if value is None else float(value)


def _server(rows):
    for row in _rows(rows):
        mode = _num(row["sh_mode"])
        yield Sample("informix_server_mode", mode)
        yield Sample("informix_server_online", 1.0 if mode == 5 else 0.0)
        yield Sample("informix_uptime_seconds", max(_num(row["sh_curtime"]) - _num(row["sh_boottime"]), 0.0))


def _ha(rows):
    for row in _rows(rows):
        role = HA_ROLES.get(int(_num(row["ha_type"])), f"type_{int(_num(row['ha_type']))}")
        yield Sample("informix_ha_info", 1.0, (("role", role),))


def _profile(rows):
    for row in _rows(rows):
        metric = PROFILE_COUNTERS.get(str(row["name"]))
        if metric:
            yield Sample(metric, _num(row["value"]))


def _count(metric: str):
    def handler(rows):
        row = next(_rows(rows), {"n": 0})
        yield Sample(metric, _num(row["n"]))

    return handler


def _logs(rows):
    """A log is reusable only if it is backed up, is not the current log and holds
    no part of an open transaction. Counting backed-up logs alone would show a
    server with one long transaction as nearly empty, right up to the moment it
    blocks."""
    total = free = waiting = 0
    for row in _rows(rows):
        total += 1
        used, current, backed = (int(_num(row[k])) for k in ("is_used", "is_current", "is_backed_up"))
        oldest_open = _num(row.get("oldest_txn"))
        pinned = oldest_open > 0 and _num(row["uniqid"]) >= oldest_open
        if not used or (backed and not current and not pinned):
            free += 1
        elif not backed and not current:
            waiting += 1
    yield Sample("informix_logical_logs", float(total))
    yield Sample("informix_logical_logs_free", float(free))
    yield Sample("informix_logical_logs_not_backed_up", float(waiting))
    yield Sample("informix_logical_log_utilization_ratio", (total - free) / total if total else 0.0)


def _long_transaction(rows):
    row = next(_rows(rows), {})
    current, oldest = row.get("cur"), row.get("oldest")
    span = _num(current) - _num(oldest) + 1 if current and oldest else 0.0
    yield Sample("informix_oldest_transaction_log_span", max(span, 0.0))


def _dbspace_type(row: dict[str, Any]) -> str:
    if _num(row["is_sbspace"]):
        return "sbspace"
    if _num(row["is_blobspace"]):
        return "blob"
    if _num(row["is_temp"]):
        return "temp"
    return "data"


def _dbspaces(rows):
    spaces: dict[str, dict[str, Any]] = {}
    for row in _rows(rows):
        space = spaces.setdefault(
            str(row["name"]), {"type": _dbspace_type(row), "pages": 0.0, "free": 0.0, "bytes": 0.0, "free_bytes": 0.0, "offline": 0.0}
        )
        page = _num(row["pagesize"])
        space["pages"] += _num(row["chksize"])
        space["free"] += _num(row["nfree"])
        space["bytes"] += _num(row["chksize"]) * page
        space["free_bytes"] += _num(row["nfree"]) * page
        space["offline"] += 1.0 if _num(row["is_offline"]) else 0.0
    for name, space in spaces.items():
        label = (("dbspace", name),)
        yield Sample("informix_dbspace_info", 1.0, (("dbspace", name), ("type", space["type"])))
        yield Sample("informix_chunks_offline", space["offline"], label)
        if space["type"] in ("data", "temp"):
            yield Sample("informix_dbspace_size_bytes", space["bytes"], label)
            yield Sample("informix_dbspace_free_bytes", space["free_bytes"], label)
            ratio = 1 - space["free"] / space["pages"] if space["pages"] else 0.0
            yield Sample("informix_dbspace_utilization_ratio", ratio, label)


def _backup(rows):
    for row in _rows(rows):
        if _num(row["is_temp"]):
            continue  # temporary dbspaces are never archived
        label = (("dbspace", str(row["name"])),)
        level0 = _num(row["level0"])
        if level0 <= 0:
            yield Sample("informix_backup_found", 0.0, label)
            continue
        yield Sample("informix_backup_found", 1.0, label)
        yield Sample("informix_backup_level0_age_seconds", max(_num(row["now"]) - level0, 0.0), label)


def _checkpoint(rows):
    for row in _rows(rows):
        yield Sample("informix_checkpoint_last_duration_seconds", _num(row["cp_time"]))
        yield Sample("informix_checkpoint_last_block_seconds", _num(row["block_time"]))
        yield Sample("informix_checkpoint_last_timestamp_seconds", _num(row["clock_time"]))


def _databases(rows):
    for row in _rows(rows):
        if _num(row["is_ansi"]):
            mode = "ansi"
        elif not _num(row["is_logging"]):
            mode = "none"
        elif _num(row["is_buff_log"]):
            mode = "buffered"
        else:
            mode = "unbuffered"
        yield Sample("informix_database_info", 1.0, (("dbname", str(row["name"])), ("logging", mode)))


QUERIES: list[Query] = [
    Query("server", "SELECT sh_mode, sh_boottime, sh_curtime FROM sysshmvals", _server),
    Query("ha", "SELECT ha_type FROM sysha_type", _ha),
    Query("profile", "SELECT name, value FROM sysprofile", _profile),
    Query("sessions", "SELECT COUNT(*) AS n FROM syssessions", _count("informix_sessions_current")),
    Query(
        "lock_waits",
        # one row per thread; cheap, unlike scanning syslocks on a busy server
        "SELECT COUNT(*) AS n FROM sysrstcb WHERE lkwait <> 0",
        _count("informix_sessions_waiting_on_locks"),
    ),
    Query(
        "logs",
        "SELECT number, uniqid, is_used, is_current, is_backed_up, "
        "(SELECT MIN(logbeg) FROM systxptab WHERE logbeg > 0) AS oldest_txn FROM syslogs",
        _logs,
    ),
    Query(
        "long_transaction",
        "SELECT (SELECT MAX(uniqid) FROM syslogs WHERE is_current = 1) AS cur, "
        "MIN(logbeg) AS oldest FROM systxptab WHERE logbeg > 0",
        _long_transaction,
    ),
    Query(
        "dbspaces",
        "SELECT d.name, d.is_temp, d.is_blobspace, d.is_sbspace, c.chksize, c.nfree, c.pagesize, c.is_offline "
        "FROM sysdbspaces d, syschunks c WHERE d.dbsnum = c.dbsnum",
        _dbspaces,
    ),
    Query(
        "backup",
        "SELECT d.name, d.is_temp, s.level0, DBINFO('utc_current') AS now "
        "FROM sysdbspaces d, sysdbstab s WHERE d.dbsnum = s.dbsnum",
        _backup,
    ),
    Query(
        "checkpoint",
        "SELECT FIRST 1 cp_time, block_time, clock_time FROM syscheckpoint ORDER BY clock_time DESC",
        _checkpoint,
    ),
    Query("databases", "SELECT name, is_logging, is_buff_log, is_ansi FROM sysdatabases", _databases),
]
