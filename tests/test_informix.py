"""Informix collection, using rows shaped exactly as ibm_db returns them over DRDA
from Informix 15.0.1: lower-case column names and blank-padded strings."""

from prometheus_client import generate_latest
from prometheus_client.core import CollectorRegistry

from corewatch.collector import Collector
from corewatch.config import parse_config
from corewatch.informix_queries import PROBE_SQL, QUERIES

PAD = " " * 20
ROWS = {
    "server": [{"sh_mode": 5, "sh_boottime": 1793830672, "sh_curtime": 1793834272}],
    "ha": [{"ha_type": 0}],
    "profile": [
        {"name": "iscommits" + PAD, "value": 9954},
        {"name": "isrollbacks" + PAD, "value": 12},
        {"name": "deadlks" + PAD, "value": 2},
        {"name": "lktouts" + PAD, "value": 1},
        {"name": "lockwts" + PAD, "value": 40},
        {"name": "bufreads" + PAD, "value": 845780},
        {"name": "dskreads" + PAD, "value": 1702},
        {"name": "numckpts" + PAD, "value": 5},
        {"name": "latchwts" + PAD, "value": 24},  # not exported
    ],
    "sessions": [{"n": 8}],
    "lock_waits": [{"n": 6}],
    # 10 logs: 4 full and backed up, 1 current, 1 full waiting for backup, 4 unused
    "logs": [{"number": i, "uniqid": 100 + i, "is_used": 1, "is_current": 0, "is_backed_up": 1, "oldest_txn": None}
             for i in range(1, 5)]
    + [{"number": 5, "uniqid": 105, "is_used": 1, "is_current": 0, "is_backed_up": 0, "oldest_txn": None}]
    + [{"number": 6, "uniqid": 106, "is_used": 1, "is_current": 1, "is_backed_up": 0, "oldest_txn": None}]
    + [{"number": i, "uniqid": 0, "is_used": 0, "is_current": 0, "is_backed_up": 0, "oldest_txn": None}
       for i in range(7, 11)],
    "long_transaction": [{"cur": 9, "oldest": 6}],
    "dbspaces": [
        {"name": "rootdbs" + PAD, "is_temp": 0, "is_blobspace": 0, "is_sbspace": 0,
         "chksize": 175000, "nfree": 88739, "pagesize": 2048, "is_offline": 0},
        {"name": "datadbs" + PAD, "is_temp": 0, "is_blobspace": 0, "is_sbspace": 0,
         "chksize": 50000, "nfree": 5000, "pagesize": 2048, "is_offline": 0},
        {"name": "datadbs" + PAD, "is_temp": 0, "is_blobspace": 0, "is_sbspace": 0,
         "chksize": 50000, "nfree": 0, "pagesize": 2048, "is_offline": 1},
        {"name": "tempdbs" + PAD, "is_temp": 1, "is_blobspace": 0, "is_sbspace": 0,
         "chksize": 1000, "nfree": 10, "pagesize": 2048, "is_offline": 0},
        {"name": "sbspace1" + PAD, "is_temp": 0, "is_blobspace": 0, "is_sbspace": 1,
         "chksize": 1000, "nfree": 3, "pagesize": 2048, "is_offline": 0},
    ],
    "backup": [
        {"name": "rootdbs" + PAD, "is_temp": 0, "level0": 1793790000, "now": 1793833200},
        {"name": "datadbs" + PAD, "is_temp": 0, "level0": 0, "now": 1793833200},
        {"name": "tempdbs" + PAD, "is_temp": 1, "level0": 0, "now": 1793833200},
    ],
    "checkpoint": [{"cp_time": 0.25, "block_time": 0.0, "clock_time": 1793830693}],
    "databases": [
        {"name": "sysmaster" + PAD, "is_logging": 1, "is_buff_log": 0, "is_ansi": 0},
        {"name": "corebank" + PAD, "is_logging": 1, "is_buff_log": 1, "is_ansi": 0},
        {"name": "scratch" + PAD, "is_logging": 0, "is_buff_log": 0, "is_ansi": 0},
    ],
}
BY_SQL = {q.sql: q.name for q in QUERIES}


class FakeInformix:
    def __init__(self, fail=(), rows=None):
        self.fail = set(fail)
        self.rows = dict(ROWS, **(rows or {}))
        self.dsns = []

    def query(self, sql):
        if sql == PROBE_SQL:
            return [{"n": 1}]
        name = BY_SQL[sql]
        if name in self.fail:
            raise RuntimeError("-206 The specified table is not in the database")
        return self.rows[name]

    def close(self):
        pass


def make(conn, engines=("informix",)):
    targets = []
    if "informix" in engines:
        targets.append({"name": "ifx_prod", "engine": "informix", "host": "ifx1", "user": "corewatch",
                        "password_env": "PW", "labels": {"environment": "production"}})
    if "db2" in engines:
        targets.append({"name": "CORE", "host": "db1", "user": "mon", "password_env": "PW",
                        "labels": {"environment": "production"}})
    cfg = parse_config({"databases": targets}, env={"PW": "x"})
    dsns = []

    def connector(dsn):
        dsns.append(dsn)
        return conn

    return Collector(cfg, connector=connector, clock=lambda: 100.0), dsns


def scrape(collector):
    registry = CollectorRegistry()
    registry.register(collector)
    text = generate_latest(registry).decode()
    return {line.rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
            for line in text.splitlines() if line and not line.startswith("#")}


L = 'db_host="ifx1",environment="production",server="ifx_prod"'


def k(name, extra=""):
    """Series key as Prometheus prints it: labels in alphabetical order."""
    labels = L.split(",") + (extra.split(",") if extra else [])
    return name + "{" + ",".join(sorted(labels)) + "}"


def test_connects_to_sysmaster_over_drda_port():
    collector, dsns = make(FakeInformix())
    scrape(collector)
    assert dsns == ["DATABASE=sysmaster;HOSTNAME=ifx1;PORT=9089;PROTOCOL=TCPIP;UID=corewatch;PWD=x;ConnectTimeout=10;"]


def test_healthy_server():
    collector, _ = make(FakeInformix())
    m = scrape(collector)
    assert m[k("informix_up")] == 1
    assert m[k("informix_server_online")] == 1
    assert m[k("informix_uptime_seconds")] == 3600
    assert m[k("informix_ha_info", 'role="standard"')] == 1
    assert m[k("informix_commits_total")] == 9954
    assert m[k("informix_lock_timeouts_total")] == 1
    assert m[k("informix_disk_reads_total")] == 1702
    assert not any("latch" in k for k in m)
    assert m[k("informix_sessions_current")] == 8
    assert m[k("informix_sessions_waiting_on_locks")] == 6
    assert m[k("informix_query_success", 'query="profile"')] == 1
    assert m['corewatch_engine_enabled{engine="informix"}'] == 1


def test_logical_logs():
    m = scrape(make(FakeInformix())[0])
    assert m[k("informix_logical_logs")] == 10
    assert m[k("informix_logical_logs_free")] == 8
    assert m[k("informix_logical_logs_not_backed_up")] == 1
    assert m[k("informix_logical_log_utilization_ratio")] == 0.2
    # the oldest open transaction began in log 6 and log 9 is current
    assert m[k("informix_oldest_transaction_log_span")] == 4


def test_open_transaction_pins_backed_up_logs():
    # the same logs, but a transaction that began in log 102 is still open:
    # logs 102-104 are backed up yet cannot be reused
    logs = [dict(r, oldest_txn=102) for r in ROWS["logs"]]
    m = scrape(make(FakeInformix(rows={"logs": logs}))[0])
    assert m[k("informix_logical_logs_free")] == 5
    assert m[k("informix_logical_log_utilization_ratio")] == 0.5


def test_no_open_transaction_reports_zero_span():
    conn = FakeInformix(rows={"long_transaction": [{"cur": 9, "oldest": None}]})
    assert scrape(make(conn)[0])[k("informix_oldest_transaction_log_span")] == 0


def test_dbspaces_and_chunks():
    m = scrape(make(FakeInformix())[0])
    assert round(m[k("informix_dbspace_utilization_ratio", 'dbspace="rootdbs"')], 4) == round(1 - 88739 / 175000, 4)
    # two chunks summed, one of them offline
    assert m[k("informix_dbspace_utilization_ratio", 'dbspace="datadbs"')] == 0.95
    assert m[k("informix_dbspace_size_bytes", 'dbspace="datadbs"')] == 100000 * 2048
    assert m[k("informix_chunks_offline", 'dbspace="datadbs"')] == 1
    assert m[k("informix_dbspace_info", 'dbspace="tempdbs",type="temp"')] == 1
    # sbspaces count free space in their own units, so no ratio for them
    assert m[k("informix_dbspace_info", 'dbspace="sbspace1",type="sbspace"')] == 1
    assert k("informix_dbspace_utilization_ratio", 'dbspace="sbspace1"') not in m


def test_backups_per_dbspace():
    m = scrape(make(FakeInformix())[0])
    assert m[k("informix_backup_found", 'dbspace="rootdbs"')] == 1
    assert m[k("informix_backup_level0_age_seconds", 'dbspace="rootdbs"')] == 43200
    assert m[k("informix_backup_found", 'dbspace="datadbs"')] == 0
    assert k("informix_backup_level0_age_seconds", 'dbspace="datadbs"') not in m
    # temporary dbspaces are never archived, so they are not reported at all
    assert not any('dbspace="tempdbs"' in k and k.startswith("informix_backup") for k in m)


def test_database_logging_modes():
    m = scrape(make(FakeInformix())[0])
    assert m[k("informix_database_info", 'dbname="corebank",logging="buffered"')] == 1
    assert m[k("informix_database_info", 'dbname="scratch",logging="none"')] == 1
    assert m[k("informix_database_info", 'dbname="sysmaster",logging="unbuffered"')] == 1


def test_server_not_online():
    conn = FakeInformix(rows={"server": [{"sh_mode": 1, "sh_boottime": 0, "sh_curtime": 10}]})
    m = scrape(make(conn)[0])
    assert m[k("informix_server_online")] == 0
    assert m[k("informix_server_mode")] == 1


def test_failing_query_is_isolated():
    m = scrape(make(FakeInformix(fail={"long_transaction"}))[0])
    assert m[k("informix_query_success", 'query="long_transaction"')] == 0
    assert m[k("informix_query_success", 'query="logs"')] == 1
    assert k("informix_oldest_transaction_log_span") not in m


def test_server_down():
    collector, _ = make(None)
    collector._connector = lambda dsn: (_ for _ in ()).throw(RuntimeError("SQL30081N"))
    m = scrape(collector)
    assert m[k("informix_up")] == 0
    assert not any(k.startswith("informix_logical") for k in m)


def test_db2_and_informix_side_by_side():
    class Both(FakeInformix):
        def query(self, sql):
            if sql.startswith("SELECT 1 "):
                return [{"1": 1}]
            if sql in BY_SQL or sql == PROBE_SQL:
                return super().query(sql)
            raise RuntimeError("Db2 query not faked here")

    m = scrape(make(Both(), engines=("informix", "db2"))[0])
    assert m['db2_up{database="CORE",db_host="db1",environment="production"}'] == 1
    assert m[k("informix_up")] == 1
    assert m['corewatch_engine_enabled{engine="db2"}'] == 1
    assert m['corewatch_engine_enabled{engine="informix"}'] == 1
