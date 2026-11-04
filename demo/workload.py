#!/usr/bin/env python3
"""Demo traffic and failure scenarios for the corewatch stack.

Runs inside the corewatch image (it already has ibm_db). Two modes:

  workload.py traffic
      Small banking transactions against every engine in COREWATCH_ENGINES,
      every couple of seconds, with an occasional rollback. Keeps the commit,
      lock and buffer panels moving. Waits for each database to come up.

  workload.py scenario <lock-wait|long-txn> --engine <db2|informix> [--seconds N]
      Reproduces a production problem on purpose so the matching alert fires:
        lock-wait  one session holds a row lock while several others queue on it
                   (Db2LockWaits / InformixLockWaits after 5 minutes)
        long-txn   Informix: one transaction stays open while other work fills
                   logical logs (InformixLongTransaction within minutes).
                   Db2: one unit of work stays open (Db2LongTransaction after 35 min)
      Everything is rolled back at the end; no data is left behind.

Demo only. Credentials come from the same environment variables as the stack.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import threading
import time

import ibm_db

TARGETS = {
    "db2": {
        "host": "db2",
        "dsn": "DATABASE=testdb;HOSTNAME={host};PORT=50000;PROTOCOL=TCPIP;UID=db2inst1;PWD={pw};ConnectTimeout=10;",
        "password_env": "DB2_PASSWORD",
        "setup": [
            "CREATE TABLE corewatch_accounts (account_id INTEGER NOT NULL PRIMARY KEY, "
            "holder VARCHAR(60) NOT NULL, balance DECIMAL(14,2) NOT NULL)",
            "CREATE TABLE corewatch_postings (posting_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "account_id INTEGER NOT NULL, amount DECIMAL(14,2) NOT NULL, filler VARCHAR(400))",
            "INSERT INTO corewatch_accounts VALUES (1, 'Operating account', 250000), "
            "(2, 'Member savings', 1200), (3, 'Member checking', 840.50), (4, 'Loan servicing', 98000)",
            "INSERT INTO corewatch_accounts VALUES (8, 'Long transaction scenario', 0)",
            "INSERT INTO corewatch_accounts VALUES (9, 'Lock wait scenario', 0)",
        ],
        "accounts": "corewatch_accounts",
        "postings": "corewatch_postings",
        "lock_wait": "SET CURRENT LOCK TIMEOUT 900",
        "traffic_lock": "SET CURRENT LOCK TIMEOUT 10",
    },
    "informix": {
        "host": "informix",
        "dsn": "DATABASE=corebank;HOSTNAME={host};PORT=9089;PROTOCOL=TCPIP;UID=informix;PWD={pw};ConnectTimeout=10;",
        "password_env": "INFORMIX_PASSWORD",
        # tables come from post_init.sh
        "setup": [
            "ALTER TABLE postings ADD filler VARCHAR(255)",
            "INSERT INTO accounts VALUES (8, 'Long transaction scenario', 0)",
            "INSERT INTO accounts VALUES (9, 'Lock wait scenario', 0)",
        ],
        "accounts": "accounts",
        "postings": "postings",
        "lock_wait": "SET LOCK MODE TO WAIT 900",
        # Informix fails at once on a lock conflict unless told to wait
        "traffic_lock": "SET LOCK MODE TO WAIT 10",
    },
}


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def connect(engine: str, wait: bool = True):
    target = TARGETS[engine]
    host = os.environ.get(f"DEMO_{engine.upper()}_HOST", target["host"])
    dsn = target["dsn"].format(host=host, pw=os.environ.get(target["password_env"], ""))
    while True:
        try:
            return ibm_db.connect(dsn, "", "")
        except Exception as exc:
            if not wait:
                raise
            log(f"{engine}: not ready yet ({str(exc)[:80]}); retrying in 20s")
            time.sleep(20)


def run(conn, sql: str) -> None:
    ibm_db.exec_immediate(conn, sql)


def setup(engine: str, conn) -> None:
    for sql in TARGETS[engine]["setup"]:
        try:
            run(conn, sql)
        except Exception:
            pass  # already there


def manual(conn, engine: str | None = None):
    ibm_db.autocommit(conn, ibm_db.SQL_AUTOCOMMIT_OFF)
    if engine:
        run(conn, TARGETS[engine]["traffic_lock"])
    return conn


# The traffic only uses accounts 1-4, and each scenario has its own row, so the
# scenarios never collide with the traffic or with each other.
LONG_TXN_ACCOUNT = 8
LOCK_WAIT_ACCOUNT = 9


def one_transaction(engine: str, conn) -> None:
    t = TARGETS[engine]
    src, dst = random.sample([1, 2, 3, 4], 2)
    amount = round(random.uniform(5, 500), 2)
    run(conn, f"UPDATE {t['accounts']} SET balance = balance - {amount} WHERE account_id = {src}")
    run(conn, f"UPDATE {t['accounts']} SET balance = balance + {amount} WHERE account_id = {dst}")
    run(conn, f"INSERT INTO {t['postings']} (account_id, amount) VALUES ({src}, {-amount})")
    run(conn, f"INSERT INTO {t['postings']} (account_id, amount) VALUES ({dst}, {amount})")
    if random.random() < 0.08:
        ibm_db.rollback(conn)  # a declined transfer
    else:
        ibm_db.commit(conn)


def traffic_loop(engine: str) -> None:
    while True:
        conn = connect(engine)
        setup(engine, conn)
        manual(conn, engine)
        log(f"{engine}: sending demo traffic")
        n = 0
        try:
            while True:
                one_transaction(engine, conn)
                n += 1
                if n % 500 == 0:
                    # keep the demo tables small
                    run(conn, f"DELETE FROM {TARGETS[engine]['postings']} WHERE filler IS NULL")
                    ibm_db.commit(conn)
                time.sleep(random.uniform(0.5, 2.5))
        except Exception as exc:
            log(f"{engine}: connection lost ({str(exc)[:80]}); reconnecting")
            try:
                ibm_db.close(conn)
            except Exception:
                pass
            time.sleep(10)


def traffic(engines: list[str]) -> None:
    threads = [threading.Thread(target=traffic_loop, args=(e,), daemon=True) for e in engines]
    for t in threads:
        t.start()
    while True:
        time.sleep(3600)


def lock_wait(engine: str, seconds: int, waiters: int) -> None:
    t = TARGETS[engine]
    holder = manual(connect(engine, wait=False), engine)
    setup(engine, holder)
    ibm_db.commit(holder)
    row = f"UPDATE {t['accounts']} SET balance = balance + 1 WHERE account_id = {LOCK_WAIT_ACCOUNT}"
    run(holder, row)
    log(f"{engine}: session A holds a row lock on the lock-wait scenario account; starting {waiters} waiting sessions")
    sessions = []

    def wait_for_lock():
        conn = manual(connect(engine, wait=False))
        sessions.append(conn)
        run(conn, t["lock_wait"])
        try:
            run(conn, row)
        except Exception:
            pass
        ibm_db.rollback(conn)  # let the next waiter through

    threads = [threading.Thread(target=wait_for_lock, daemon=True) for _ in range(waiters)]
    for th in threads:
        th.start()
    log(f"{engine}: holding for {seconds}s. Watch 'waiting on locks' in Grafana; the alert fires after 5 minutes.")
    time.sleep(seconds)
    ibm_db.rollback(holder)
    for th in threads:
        th.join(timeout=30)
    for conn in sessions:
        try:
            ibm_db.rollback(conn)
        except Exception:
            pass
    log(f"{engine}: lock released, everything rolled back")


def long_txn(engine: str, seconds: int) -> None:
    t = TARGETS[engine]
    holder = manual(connect(engine, wait=False), engine)
    setup(engine, holder)
    ibm_db.commit(holder)
    # A real change, so the transaction writes a log record. A no-op update
    # (SET x = x) does not, and Informix would not count it as open.
    run(holder, f"UPDATE {t['accounts']} SET balance = balance + 1 WHERE account_id = {LONG_TXN_ACCOUNT}")
    log(f"{engine}: transaction opened and left uncommitted")
    if engine == "informix":
        fill_informix_logs(t)
    else:
        log("db2: the alert fires once the unit of work is 30 minutes old and stays open for 5 more.")
    log(f"{engine}: holding the transaction open for {seconds}s")
    time.sleep(seconds)
    ibm_db.rollback(holder)
    log(f"{engine}: transaction rolled back")


def fill_informix_logs(t: dict) -> None:
    """Generate log volume until the open transaction spans half of the logical logs.

    Stops well below LTXHWM (70% by default), where the server itself would
    start rolling the transaction back. The work is done server-side: a fixed
    block of rows is updated over and over, each round committed, so the
    filler never becomes a long transaction of its own.
    """
    mon = connect("informix", wait=False)
    filler = manual(connect("informix", wait=False), "informix")
    pad = "x" * 250

    def span():
        st = ibm_db.exec_immediate(
            mon,
            "SELECT (SELECT COUNT(*) FROM sysmaster:syslogs) AS logs, "
            "(SELECT MAX(uniqid) FROM sysmaster:syslogs WHERE is_current = 1) "
            "- (SELECT MIN(logbeg) FROM sysmaster:systxptab WHERE logbeg > 0) + 1 AS span "
            "FROM sysmaster:sysdual",
        )
        row = ibm_db.fetch_assoc(st)
        return int(row["logs"]), int(row["span"] or 0)

    for _ in range(2000):
        run(filler, f"INSERT INTO {t['postings']} (account_id, amount, filler) VALUES (3, 0, '{pad}')")
    ibm_db.commit(filler)
    logs, current = span()
    target = logs // 2
    log(f"informix: generating log activity until the transaction spans {target} of {logs} logs")
    for _ in range(2000):  # safety limit
        if current >= target:
            break
        run(filler, f"UPDATE {t['postings']} SET amount = amount + 0.01 WHERE filler IS NOT NULL")
        ibm_db.commit(filler)
        logs, current = span()
    run(filler, f"DELETE FROM {t['postings']} WHERE filler IS NOT NULL")
    ibm_db.commit(filler)
    log(f"informix: oldest transaction now spans {current} of {logs} logs. The alert fires after 2 minutes.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("traffic")
    sc = sub.add_parser("scenario")
    sc.add_argument("name", choices=["lock-wait", "long-txn"])
    sc.add_argument("--engine", choices=sorted(TARGETS), required=True)
    sc.add_argument("--seconds", type=int, default=0, help="how long to hold (default 420 for lock-wait, "
                    "300 for informix long-txn, 2400 for db2 long-txn)")
    sc.add_argument("--waiters", type=int, default=6)
    args = parser.parse_args(argv)

    if args.mode == "traffic":
        engines = [e for e in os.environ.get("COREWATCH_ENGINES", "db2").split(",") if e in TARGETS]
        if os.environ.get("DEMO_TRAFFIC", "on") == "off" or not engines:
            log("demo traffic is off")
            while True:
                time.sleep(3600)
        traffic(engines)
    elif args.name == "lock-wait":
        lock_wait(args.engine, args.seconds or 420, args.waiters)
    else:
        default = 300 if args.engine == "informix" else 2400
        long_txn(args.engine, args.seconds or default)
    return 0


if __name__ == "__main__":
    sys.exit(main())
