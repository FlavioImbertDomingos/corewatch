"""The monitoring queries and how each one turns into metrics.

Every query is independent. If one fails (a column missing on an older Db2
level, a missing privilege), the exporter reports db2_query_success{query}=0
for that query and keeps going with the rest.

The connecting user needs DBADM, SQLADM or the EXECUTE privilege on the
monitor routines, plus SELECT on SYSIBMADM.DB_HISTORY. The grants in
docs/privileges.sql were run against Db2 12.1 Community Edition. See docs/privileges.sql.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator

GAUGE = "gauge"
COUNTER = "counter"

# name -> (type, help, extra label names beyond database and the config labels)
METRICS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "db2_log_utilization_ratio": (GAUGE, "Share of transaction log space in use, 0 to 1.", ()),
    "db2_log_used_bytes": (GAUGE, "Transaction log space in use.", ()),
    "db2_log_available_bytes": (GAUGE, "Transaction log space still free.", ()),
    "db2_tablespace_used_bytes": (GAUGE, "Bytes used in the table space.", ("tablespace",)),
    "db2_tablespace_usable_bytes": (GAUGE, "Usable bytes in the table space at its current size.", ("tablespace",)),
    "db2_tablespace_utilization_ratio": (
        GAUGE,
        "Used over usable pages, 0 to 1. Not reported for SMS table spaces, which grow with the filesystem.",
        ("tablespace",),
    ),
    "db2_tablespace_info": (GAUGE, "Table space type (DMS or SMS) and content type.", ("tablespace", "type", "content")),
    "db2_tablespace_auto_resize": (GAUGE, "1 if the table space grows automatically.", ("tablespace",)),
    "db2_tablespace_state_normal": (GAUGE, "1 if the table space state is NORMAL.", ("tablespace", "state")),
    "db2_backup_found": (GAUGE, "1 if the history file holds at least one successful backup.", ()),
    "db2_backup_last_success_age_seconds": (GAUGE, "Seconds since the last successful backup ended.", ()),
    "db2_deadlocks_total": (COUNTER, "Deadlocks since activation.", ()),
    "db2_lock_timeouts_total": (COUNTER, "Lock timeouts since activation.", ()),
    "db2_lock_waits_total": (COUNTER, "Lock waits since activation.", ()),
    "db2_lock_wait_seconds_total": (COUNTER, "Time spent waiting on locks since activation.", ()),
    "db2_locks_waiting": (GAUGE, "Agents waiting on a lock right now.", ()),
    "db2_connections_current": (GAUGE, "Applications connected right now.", ()),
    "db2_connections_total": (COUNTER, "Connections since activation.", ()),
    "db2_commits_total": (COUNTER, "Application commits since activation.", ()),
    "db2_rollbacks_total": (COUNTER, "Application rollbacks since activation.", ()),
    "db2_rows_read_total": (COUNTER, "Rows read since activation.", ()),
    "db2_rows_returned_total": (COUNTER, "Rows returned to applications since activation.", ()),
    "db2_sort_overflows_total": (COUNTER, "Sorts that spilled to disk since activation.", ()),
    "db2_bufferpool_logical_reads_total": (COUNTER, "Buffer pool logical reads.", ("type",)),
    "db2_bufferpool_physical_reads_total": (COUNTER, "Buffer pool physical reads.", ("type",)),
    "db2_active_seconds": (GAUGE, "Seconds since the database was activated.", ()),
    "db2_oldest_uow_age_seconds": (GAUGE, "Age of the oldest open unit of work.", ()),
    "db2_hadr_configured": (GAUGE, "1 if HADR is running for this database.", ()),
    "db2_hadr_peer": (GAUGE, "1 if the HADR pair is in PEER state.", ("role", "standby_id")),
    "db2_hadr_connected": (GAUGE, "1 if the HADR pair is connected.", ("role", "standby_id")),
    "db2_hadr_log_gap_bytes": (GAUGE, "Log the standby has not yet received.", ("role", "standby_id")),
    "db2_diag_messages": (GAUGE, "db2diag.log records in the last 5 minutes by level.", ("level",)),
}


@dataclass(frozen=True)
class Sample:
    name: str
    value: float
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Query:
    name: str
    sql: str
    handler: Callable[[list[dict[str, Any]]], Iterator[Sample]]
    optional: bool = False


def _num(value: Any) -> float:
    return 0.0 if value is None else float(value)


def _log(rows):
    for row in rows:
        used = _num(row["TOTAL_LOG_USED"])
        available = _num(row["TOTAL_LOG_AVAILABLE"])
        yield Sample("db2_log_used_bytes", used)
        if available < 0:
            # -1 means infinite logging (LOGSECOND = -1): the log cannot fill
            yield Sample("db2_log_utilization_ratio", 0.0)
            continue
        yield Sample("db2_log_available_bytes", available)
        total = used + available
        yield Sample("db2_log_utilization_ratio", used / total if total else 0.0)


def _tablespaces(rows):
    for row in rows:
        name = str(row["TBSP_NAME"]).strip()
        page = _num(row["TBSP_PAGE_SIZE"])
        used = _num(row["TBSP_USED_PAGES"])
        usable = _num(row["TBSP_USABLE_PAGES"])
        state = str(row["TBSP_STATE"]).strip()
        label = (("tablespace", name),)
        yield Sample("db2_tablespace_used_bytes", used * page, label)
        yield Sample("db2_tablespace_usable_bytes", usable * page, label)
        kind = str(row["TBSP_TYPE"]).strip()
        if kind != "SMS":
            yield Sample("db2_tablespace_utilization_ratio", used / usable if usable else 0.0, label)
        yield Sample(
            "db2_tablespace_info",
            1.0,
            (("tablespace", name), ("type", kind), ("content", str(row["TBSP_CONTENT_TYPE"]).strip())),
        )
        yield Sample("db2_tablespace_auto_resize", 1.0 if _num(row["TBSP_AUTO_RESIZE_ENABLED"]) else 0.0, label)
        yield Sample(
            "db2_tablespace_state_normal",
            1.0 if state == "NORMAL" else 0.0,
            (("tablespace", name), ("state", state)),
        )


def _backup(rows):
    last = rows[0]["LAST_END"] if rows else None
    now = rows[0]["NOW"] if rows else None
    if not last or not now:
        yield Sample("db2_backup_found", 0.0)
        return
    fmt = "%Y%m%d%H%M%S"
    age = (datetime.strptime(str(now).strip(), fmt) - datetime.strptime(str(last).strip(), fmt)).total_seconds()
    yield Sample("db2_backup_found", 1.0)
    yield Sample("db2_backup_last_success_age_seconds", max(age, 0.0))


def _database(rows):
    for row in rows:
        yield Sample("db2_deadlocks_total", _num(row["DEADLOCKS"]))
        yield Sample("db2_lock_timeouts_total", _num(row["LOCK_TIMEOUTS"]))
        yield Sample("db2_lock_waits_total", _num(row["LOCK_WAITS"]))
        yield Sample("db2_lock_wait_seconds_total", _num(row["LOCK_WAIT_TIME"]) / 1000.0)
        yield Sample("db2_locks_waiting", _num(row["NUM_LOCKS_WAITING"]))
        yield Sample("db2_connections_current", _num(row["APPLS_CUR_CONS"]))
        yield Sample("db2_connections_total", _num(row["TOTAL_CONS"]))
        yield Sample("db2_commits_total", _num(row["TOTAL_APP_COMMITS"]))
        yield Sample("db2_rollbacks_total", _num(row["TOTAL_APP_ROLLBACKS"]))
        yield Sample("db2_rows_read_total", _num(row["ROWS_READ"]))
        yield Sample("db2_rows_returned_total", _num(row["ROWS_RETURNED"]))
        yield Sample("db2_sort_overflows_total", _num(row["SORT_OVERFLOWS"]))
        for kind in ("DATA", "INDEX"):
            label = (("type", kind.lower()),)
            yield Sample("db2_bufferpool_logical_reads_total", _num(row[f"POOL_{kind}_L_READS"]), label)
            yield Sample("db2_bufferpool_physical_reads_total", _num(row[f"POOL_{kind}_P_READS"]), label)
        yield Sample("db2_active_seconds", _num(row["ACTIVE_SECONDS"]))


def _oldest_uow(rows):
    yield Sample("db2_oldest_uow_age_seconds", _num(rows[0]["AGE_SECONDS"]) if rows else 0.0)


def _hadr(rows):
    yield Sample("db2_hadr_configured", 1.0 if rows else 0.0)
    for row in rows:
        label = (
            ("role", str(row["HADR_ROLE"]).strip()),
            ("standby_id", str(row["STANDBY_ID"]).strip()),
        )
        yield Sample("db2_hadr_peer", 1.0 if str(row["HADR_STATE"]).strip() == "PEER" else 0.0, label)
        yield Sample(
            "db2_hadr_connected",
            1.0 if str(row["HADR_CONNECT_STATUS"]).strip() == "CONNECTED" else 0.0,
            label,
        )
        yield Sample("db2_hadr_log_gap_bytes", _num(row["HADR_LOG_GAP"]), label)


_DIAG_LEVELS = {"C": "critical", "S": "severe", "E": "error"}


def _diag(rows):
    counts = {name: 0.0 for name in _DIAG_LEVELS.values()}
    for row in rows:
        level = _DIAG_LEVELS.get(str(row["LEVEL"]).strip())
        if level:
            counts[level] = _num(row["N"])
    for level, count in counts.items():
        yield Sample("db2_diag_messages", count, (("level", level),))


QUERIES: list[Query] = [
    Query(
        "log",
        "SELECT TOTAL_LOG_USED, TOTAL_LOG_AVAILABLE FROM TABLE(MON_GET_TRANSACTION_LOG(-2))",
        _log,
    ),
    Query(
        "tablespaces",
        "SELECT TBSP_NAME, TBSP_STATE, TBSP_PAGE_SIZE, TBSP_USED_PAGES, TBSP_USABLE_PAGES, "
        "TBSP_AUTO_RESIZE_ENABLED, TBSP_TYPE, TBSP_CONTENT_TYPE FROM TABLE(MON_GET_TABLESPACE(NULL, -2))",
        _tablespaces,
    ),
    Query(
        "backup",
        "SELECT MAX(END_TIME) AS LAST_END, "
        "VARCHAR_FORMAT(CURRENT TIMESTAMP, 'YYYYMMDDHH24MISS') AS NOW "
        "FROM SYSIBMADM.DB_HISTORY WHERE OPERATION = 'B' AND (SQLCODE IS NULL OR SQLCODE >= 0)",
        _backup,
    ),
    Query(
        "database",
        "SELECT DEADLOCKS, LOCK_TIMEOUTS, LOCK_WAITS, LOCK_WAIT_TIME, NUM_LOCKS_WAITING, "
        "APPLS_CUR_CONS, TOTAL_CONS, TOTAL_APP_COMMITS, TOTAL_APP_ROLLBACKS, ROWS_READ, "
        "ROWS_RETURNED, SORT_OVERFLOWS, POOL_DATA_L_READS, POOL_DATA_P_READS, "
        "POOL_INDEX_L_READS, POOL_INDEX_P_READS, "
        "TIMESTAMPDIFF(2, CHAR(CURRENT TIMESTAMP - DB_CONN_TIME)) AS ACTIVE_SECONDS "
        "FROM TABLE(MON_GET_DATABASE(-2))",
        _database,
    ),
    Query(
        "oldest_uow",
        "SELECT COALESCE(MAX(TIMESTAMPDIFF(2, CHAR(CURRENT TIMESTAMP - UOW_START_TIME))), 0) "
        "AS AGE_SECONDS FROM TABLE(MON_GET_UNIT_OF_WORK(NULL, -2)) "
        "WHERE UOW_START_TIME IS NOT NULL AND UOW_STOP_TIME IS NULL "
        "AND APPLICATION_HANDLE <> MON_GET_APPLICATION_HANDLE()",
        _oldest_uow,
    ),
    Query(
        "hadr",
        "SELECT HADR_ROLE, STANDBY_ID, HADR_STATE, HADR_CONNECT_STATUS, HADR_LOG_GAP "
        "FROM TABLE(MON_GET_HADR(NULL))",
        _hadr,
    ),
    Query(
        "diag_history",
        "SELECT LEVEL, COUNT(*) AS N FROM TABLE(PD_GET_DIAG_HIST('MAIN', 'ALL', '', "
        "CURRENT TIMESTAMP - 5 MINUTES, CAST(NULL AS TIMESTAMP))) "
        "WHERE LEVEL IN ('C', 'S', 'E') GROUP BY LEVEL",
        _diag,
        optional=True,
    ),
]
