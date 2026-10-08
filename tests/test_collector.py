from prometheus_client import generate_latest
from prometheus_client.core import CollectorRegistry

from corewatch.collector import Db2Collector
from corewatch.config import parse_config
from corewatch.queries import QUERIES

ROWS = {
    "log": [{"TOTAL_LOG_USED": 425, "TOTAL_LOG_AVAILABLE": 575}],
    "tablespaces": [
        {"TBSP_NAME": "USERSPACE1", "TBSP_STATE": "NORMAL", "TBSP_PAGE_SIZE": 8192,
         "TBSP_USED_PAGES": 900, "TBSP_USABLE_PAGES": 1000, "TBSP_AUTO_RESIZE_ENABLED": 1,
         "TBSP_TYPE": "DMS", "TBSP_CONTENT_TYPE": "LARGE"},
        {"TBSP_NAME": "OLDDMS", "TBSP_STATE": "BACKUP_PENDING", "TBSP_PAGE_SIZE": 8192,
         "TBSP_USED_PAGES": 0, "TBSP_USABLE_PAGES": 0, "TBSP_AUTO_RESIZE_ENABLED": 0,
         "TBSP_TYPE": "DMS", "TBSP_CONTENT_TYPE": "ANY"},
        {"TBSP_NAME": "TEMPSPACE1", "TBSP_STATE": "NORMAL", "TBSP_PAGE_SIZE": 4096,
         "TBSP_USED_PAGES": 1, "TBSP_USABLE_PAGES": 1, "TBSP_AUTO_RESIZE_ENABLED": 0,
         "TBSP_TYPE": "SMS", "TBSP_CONTENT_TYPE": "SYSTEMP"},
    ],
    "backup": [{"LAST_END": "20261006010000", "NOW": "20261006130000"}],
    "database": [{
        "DEADLOCKS": 3, "LOCK_TIMEOUTS": 4, "LOCK_WAITS": 50, "LOCK_WAIT_TIME": 2500,
        "NUM_LOCKS_WAITING": 2, "APPLS_CUR_CONS": 17, "TOTAL_CONS": 900, "TOTAL_APP_COMMITS": 1000,
        "TOTAL_APP_ROLLBACKS": 10, "ROWS_READ": 5000, "ROWS_RETURNED": 400, "SORT_OVERFLOWS": 1,
        "POOL_DATA_L_READS": 100, "POOL_DATA_P_READS": 5, "POOL_INDEX_L_READS": 200,
        "POOL_INDEX_P_READS": 2, "ACTIVE_SECONDS": 86400,
    }],
    "oldest_uow": [{"AGE_SECONDS": 1900}],
    "hadr": [{"HADR_ROLE": "PRIMARY", "STANDBY_ID": 1, "HADR_STATE": "REMOTE_CATCHUP",
              "HADR_CONNECT_STATUS": "CONNECTED", "HADR_LOG_GAP": 4096}],
    "diag_history": [{"LEVEL": "S", "N": 2}],
}
BY_SQL = {q.sql: q.name for q in QUERIES}


class FakeConn:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.closed = False
        self.calls = 0

    def query(self, sql):
        self.calls += 1
        if sql.startswith("SELECT 1 "):
            return [{"1": 1}]
        name = BY_SQL[sql]
        if name in self.fail:
            raise RuntimeError("SQL0204N name is undefined")
        return ROWS[name]

    def close(self):
        self.closed = True


def make(connector, optional=(), cache=30):
    cfg = parse_config(
        {
            "cache_seconds": cache,
            "optional_queries": list(optional),
            "databases": [{"name": "CORE", "host": "db1", "user": "mon", "password_env": "PW",
                           "labels": {"client": "demo", "environment": "production"}}],
        },
        env={"PW": "x"},
    )
    clock = {"t": 100.0}
    collector = Db2Collector(cfg, connector=connector, clock=lambda: clock["t"])
    return collector, clock


def scrape(collector):
    registry = CollectorRegistry()
    registry.register(collector)
    text = generate_latest(registry).decode()
    return {line.rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
            for line in text.splitlines() if line and not line.startswith("#")}


L = 'client="demo",database="CORE",db_host="db1",environment="production"'


def test_healthy_database():
    collector, _ = make(lambda dsn: FakeConn())
    m = scrape(collector)
    assert m[f"db2_up{{{L}}}"] == 1
    assert m[f"db2_log_utilization_ratio{{{L}}}"] == 0.425
    assert m[f"db2_log_used_bytes{{{L}}}"] == 425
    assert m[f'db2_tablespace_utilization_ratio{{{L},tablespace="USERSPACE1"}}'] == 0.9
    assert m[f'db2_tablespace_used_bytes{{{L},tablespace="USERSPACE1"}}'] == 900 * 8192
    # a table space with no usable pages must not divide by zero
    assert m[f'db2_tablespace_utilization_ratio{{{L},tablespace="OLDDMS"}}'] == 0
    assert m[f'db2_tablespace_state_normal{{{L},state="BACKUP_PENDING",tablespace="OLDDMS"}}'] == 0
    # SMS temp space is always "full"; reporting it would page someone for nothing
    assert f'db2_tablespace_utilization_ratio{{{L},tablespace="TEMPSPACE1"}}' not in m
    info = [k for k in m if k.startswith("db2_tablespace_info") and 'tablespace="TEMPSPACE1"' in k]
    assert len(info) == 1 and 'type="SMS"' in info[0] and 'content="SYSTEMP"' in info[0]
    assert m[f"db2_backup_last_success_age_seconds{{{L}}}"] == 12 * 3600
    assert m[f"db2_deadlocks_total{{{L}}}"] == 3
    assert m[f"db2_lock_wait_seconds_total{{{L}}}"] == 2.5
    assert m[f'db2_bufferpool_physical_reads_total{{{L},type="index"}}'] == 2
    assert m[f"db2_oldest_uow_age_seconds{{{L}}}"] == 1900
    assert m[f'db2_hadr_peer{{{L},role="PRIMARY",standby_id="1"}}'] == 0
    assert m[f'db2_hadr_connected{{{L},role="PRIMARY",standby_id="1"}}'] == 1
    assert m[f'db2_hadr_log_gap_bytes{{{L},role="PRIMARY",standby_id="1"}}'] == 4096
    assert m[f'db2_query_success{{{L},query="hadr"}}'] == 1
    # the optional query is off unless the config asks for it
    assert not any(k.startswith("db2_diag_messages") for k in m)


def test_optional_query_enabled():
    collector, _ = make(lambda dsn: FakeConn(), optional=["diag_history"])
    m = scrape(collector)
    assert m[f'db2_diag_messages{{{L},level="severe"}}'] == 2
    assert m[f'db2_diag_messages{{{L},level="critical"}}'] == 0


def test_one_failing_query_does_not_stop_the_rest():
    collector, _ = make(lambda dsn: FakeConn(fail={"tablespaces"}))
    m = scrape(collector)
    assert m[f'db2_query_success{{{L},query="tablespaces"}}'] == 0
    assert m[f'db2_query_success{{{L},query="log"}}'] == 1
    assert not any(k.startswith("db2_tablespace") for k in m)
    assert m[f"db2_log_utilization_ratio{{{L}}}"] == 0.425


def test_database_down_reports_up_zero_and_reconnects():
    attempts = []

    def connector(dsn):
        attempts.append(dsn)
        if len(attempts) == 1:
            raise RuntimeError("SQL30081N communication error")
        return FakeConn()

    collector, clock = make(connector, cache=0)
    m = scrape(collector)
    assert m[f"db2_up{{{L}}}"] == 0
    assert not any(k.startswith("db2_log") for k in m)
    clock["t"] += 60
    assert scrape(collector)[f"db2_up{{{L}}}"] == 1
    assert len(attempts) == 2


def test_broken_connection_is_closed_and_replaced():
    conns = []

    class Flaky(FakeConn):
        def query(self, sql):
            if self.calls >= 20:
                raise RuntimeError("SQL1224N")
            return super().query(sql)

    def connector(dsn):
        conns.append(Flaky())
        return conns[-1]

    collector, clock = make(connector, cache=0)
    for _ in range(6):
        scrape(collector)
        clock["t"] += 60
    assert len(conns) >= 2 and conns[0].closed


def test_cache_limits_database_load():
    conn = FakeConn()
    collector, clock = make(lambda dsn: conn, cache=30)
    scrape(collector)
    first = conn.calls
    scrape(collector)
    assert conn.calls == first
    clock["t"] += 31
    scrape(collector)
    assert conn.calls == 2 * first


def test_no_backup_in_history():
    ROWS_NO = dict(ROWS, backup=[{"LAST_END": None, "NOW": "20261006130000"}])

    class NoBackup(FakeConn):
        def query(self, sql):
            if BY_SQL.get(sql) == "backup":
                return ROWS_NO["backup"]
            return super().query(sql)

    collector, _ = make(lambda dsn: NoBackup())
    m = scrape(collector)
    assert m[f"db2_backup_found{{{L}}}"] == 0
    assert f"db2_backup_last_success_age_seconds{{{L}}}" not in m


def test_infinite_logging_reports_zero_utilization():
    class Infinite(FakeConn):
        def query(self, sql):
            if BY_SQL.get(sql) == "log":
                return [{"TOTAL_LOG_USED": 5000, "TOTAL_LOG_AVAILABLE": -1}]
            return super().query(sql)

    collector, _ = make(lambda dsn: Infinite())
    m = scrape(collector)
    assert m[f"db2_log_utilization_ratio{{{L}}}"] == 0
    assert f"db2_log_available_bytes{{{L}}}" not in m
