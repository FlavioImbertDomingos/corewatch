# corewatch

Prometheus monitoring for IBM Db2 and IBM Informix running on AIX and Linux. It is aimed at teams that operate a core system on either database and want alerts for the things that stop it: a full transaction log, a long transaction pinning the logical logs, a missed backup, lock pile-ups, a chunk or disk path that went down, a batch job that never started.

It has four parts:

- `corewatch-exporter`, a database exporter written in Python for Db2 and Informix. It runs anywhere that can reach the databases, so nothing has to be installed on the database servers. Both engines go through one driver (ibm_db): Db2 natively, Informix over its DRDA listener, so no Informix client SDK is needed.
- An AIX collector (ksh) for what only AIX has: `errpt`, stale LVM partitions, MPIO paths, paging space, `oslevel`, interim fixes and mksysb age.
- A Linux collector (bash) for what node_exporter does not cover: patch status, pending reboots, multipath path health.
- A job wrapper (POSIX sh) that records the outcome of each batch job on either platform.

Around those: 62 alert rules with tests, an Alertmanager config with two severities, a Grafana dashboard with a description on every panel, a runbook for every alert, and a Docker Compose demo that brings up Db2 Community Edition, Informix Developer Edition or both, with the whole stack, demo traffic and failure scenarios you can trigger on purpose.

An interactive 3D map of the stack is in [docs/index.html](docs/index.html). Open it in a browser.

## What is monitored

| Layer | Signals | Where it comes from |
| --- | --- | --- |
| Db2 | Up, log space, table space use and state, backup age, lock waits, deadlocks, oldest open transaction, connections, throughput, buffer pool reads, HADR state and log gap, severe diagnostic records | `corewatch-exporter` over SQL |
| Informix | Up, server mode, logical logs in use and waiting for backup, the log span of the oldest open transaction, dbspace use, offline chunks, level-0 archive age per dbspace, sessions waiting on locks, deadlocks, lock timeouts, lock, user and transaction table overflows, checkpoint duration and blocking, buffer pool hit ratio, sessions, HA role, databases without logging | `corewatch-exporter` over DRDA, from `sysmaster` |
| AIX | errpt entries by type and class, stale partitions, volume group free space, MPIO path status, paging space, filesystem use, oslevel, interim fixes, mksysb age | `collectors/aix/corewatch_aix.sh` |
| Linux | CPU, memory, disk and filesystem (node_exporter), plus reboot-required, pending security updates, multipath paths | node_exporter and `collectors/linux/corewatch_linux.sh` |
| Batch jobs | Start, end, duration, exit code, last success, allowed gap | `collectors/common/corewatch_job.sh` |
| Outside view | Db2 and Informix listener ports reachable, TLS certificate expiry | blackbox_exporter |

### Db2 and Informix, side by side

The two engines fail in similar ways, but they are monitored from different places, and some of the details matter:

| Concern | Db2 | Informix |
| --- | --- | --- |
| Scope of one connection | One database (`MON_GET_*` table functions) | The whole server, through the `sysmaster` database |
| Label on every metric | `database` | `server` (the `DBSERVERNAME`) |
| Log full | Active log space used (`MON_GET_TRANSACTION_LOG`) | Logical logs that cannot be reused: not backed up, current, or holding part of an open transaction (`syslogs`, `systxptab`) |
| Long transaction | Age of the oldest unit of work | Share of the logical logs the oldest transaction spans, which is what `LTXHWM` acts on |
| Storage | Fixed-size table spaces, table space state | Data dbspaces, offline chunks (`sysdbspaces`, `syschunks`) |
| Backup | Last successful backup in the recovery history | Last level-0 archive per dbspace (`sysdbstab`) |
| Lock waits | `NUM_LOCKS_WAITING` | Sessions with `sysrstcb.lkwait` set |
| Replication | HADR state and log gap | HA role (`sysha_type`); HDR and RSS lag are on the roadmap below |

## Try it

You need Docker with Compose. Db2 needs about 6 GB of free disk, Informix about 2 GB. Both IBM images are x86-64 only, so on an Apple Silicon Mac they run under emulation and are slower.

```
bin/corewatch-demo up
```

The script asks which database to run (Db2, Informix or both), creates `.env` with random passwords if there is none, starts the stack and waits until the exporter sees each database. You can also name the choice: `bin/corewatch-demo up informix`. Other commands:

| Command | What it does |
| --- | --- |
| `bin/corewatch-demo status` | Containers, whether each database answers, failing monitoring queries, firing alerts |
| `bin/corewatch-demo scenario long-txn informix` | Opens a transaction and fills logical logs until it spans half of them. `InformixLongTransaction` pages two minutes later |
| `bin/corewatch-demo scenario lock-wait db2` | One session holds a row lock while six wait behind it. The lock-wait alert fires after five minutes. Works with `informix` too |
| `bin/corewatch-demo backup informix` | Records a level-0 archive, which resets the backup age |
| `bin/corewatch-demo down` | Stops everything; `down --volumes` also deletes the database data |

The selection is saved in `.env` as `COMPOSE_PROFILES` (which containers run) and `COREWATCH_ENGINES` (which targets the exporter monitors), so a database you chose not to run is never reported as down, and plain `docker compose up -d` repeats the same choice. Scenarios roll everything back when they end.

Db2 takes three to five minutes to create its database the first time, Informix about one minute. Then open:

- Grafana at http://localhost:3000 (user `admin`, the password you set)
- Prometheus alerts at http://localhost:9090/alerts
- Raw metrics at http://localhost:9953/metrics

A small transaction generator (`demo/workload.py`) sends banking-style transfers to each running database, with an occasional rollback, so the throughput and lock panels move. Set `DEMO_TRAFFIC=off` in `.env` to stop it.

The demo also runs two pretend batch jobs. `posting` fails about one run in five, and `month_end` succeeds once and never runs again, so within a few minutes you can watch `JobFailed` and `JobOverdue` fire.

The Informix container is set up on first start by `demo/informix/post_init.sh`: it replaces the image's default password, creates a least-privilege `corewatch` OS user for the exporter, adds a fixed-size data dbspace and a temporary dbspace, creates a logged `corebank` database, and records a level-0 archive.

To make a Db2 alert by hand instead of with a scenario, hold a lock in one terminal and wait on it in another:

```
docker compose exec db2 su - db2inst1
db2 connect to testdb
db2 "create table t1 (id int not null primary key, v varchar(20))"
db2 "insert into t1 values (1, 'a')"
db2 +c "update t1 set v = 'x' where id = 1"     # +c leaves the transaction open
```

`db2_oldest_uow_age_seconds` starts climbing, and a second session running the same update shows up in `db2_locks_waiting`.

## Install for real

### The exporter

```
pip install .
export COREWATCH_DB2_PASSWORD=... COREWATCH_INFORMIX_PASSWORD=...
corewatch-exporter --config config.yml
```

Copy `config.example.yml` and list your targets. A target without `engine` is a Db2 database; `engine: informix` makes it an Informix server, monitored through its DRDA port (9089 by default). Passwords come from an environment variable or a file. The exporter refuses a password written in the config file. `--engines db2,informix` (or `COREWATCH_ENGINES`) monitors only some of the targets. The old command name, `corewatch-db2`, still works.

Create dedicated monitoring users. Neither needs access to application data:

- Db2: the grants in [docs/privileges.sql](docs/privileges.sql), run in each database.
- Informix: `CONNECT` on `sysmaster` only, as in [docs/privileges-informix.sql](docs/privileges-informix.sql). The server needs a DRDA listener: an `sqlhosts` entry with protocol `drsoctcp`, named in `DBSERVERALIASES`.

One exporter can watch many targets of both engines. The `labels` you set per target (for example `client` and `environment`) are copied onto every metric, so one dashboard and one rule set cover every hosted customer.

### AIX hosts

```
mkdir -p /opt/corewatch /var/lib/corewatch
cp collectors/aix/corewatch_aix.sh collectors/common/corewatch_job.sh /opt/corewatch/
crontab -e    # add:  */5 * * * * /opt/corewatch/corewatch_aix.sh >/dev/null 2>&1
```

The script writes `/var/lib/corewatch/aix.prom`. Serve that directory in one of two ways:

- node_exporter's textfile collector, if you run node_exporter on AIX.
- `python3 src/corewatch/textfile_server.py --dir /var/lib/corewatch --port 9954`. It uses only the standard library, so the python3 from the AIX Toolbox is enough.

Optional settings are environment variables documented at the top of the script: `MKSYSB_DIR`, `DB2_INSTANCE_USER`, `ERRPT_WINDOW_MIN`.

### Linux hosts

Install node_exporter with `--collector.textfile.directory=/var/lib/corewatch`, then:

```
install -m 755 collectors/linux/corewatch_linux.sh collectors/common/corewatch_job.sh /opt/corewatch/
crontab -e    # add:  */5 * * * * /opt/corewatch/corewatch_linux.sh >/dev/null 2>&1
```

### Batch jobs

Put the wrapper in front of each scheduled command:

```
/opt/corewatch/corewatch_job.sh nightly_close --max-age 93600 -- /opt/app/close.sh
```

It passes the job's exit code through unchanged. `--max-age` is the longest allowed gap between successes, in seconds. That is what lets `JobOverdue` catch a job that never started, which a failure alert alone cannot do.

## Alerts

Rules are in `prometheus/rules/`. There are two severities:

- `page` means a member-facing outage or one that is close.
- `ticket` means fix it in business hours.

Every alert carries a `layer` label (`db2`, `informix`, `aix`, `linux`, `jobs`, `monitoring`) and a `runbook_url` pointing at [docs/runbooks.md](docs/runbooks.md). When a Db2 database or an Informix server is down, Alertmanager suppresses its other alerts.

Thresholds are starting points. Db2 table space alerts skip auto-resize table spaces on purpose, because those report as nearly full all the time; the filesystem alerts cover them. Informix dbspace alerts skip temporary dbspaces, which fill and empty with sorts. `InformixLongTransaction` pages when the oldest transaction spans 40% of the logical logs, well before the default `LTXHWM` of 70%, where the server starts rolling it back itself.

The dashboard (`grafana/dashboards/corewatch.json`) opens with an estate overview, then one row each for Db2, Informix, batch jobs, AIX and Linux. Hover the (i) next to a panel title for what it shows and when to worry. The overview and Informix rows are generated by `grafana/build_dashboard_informix.py`; edit the script and run it again rather than editing the JSON by hand.

## Tests

```
pip install -e ".[dev]"
pytest
```

- The collector is tested with a fake connection: healthy database, database down, one failing query, broken connection, cache behaviour, no backup, infinite logging.
- The Informix collection is tested with rows shaped exactly as the driver returns them from Informix 15 (lower-case column names, padded strings): logical logs pinned by an open transaction, long-transaction span, dbspaces with an offline chunk, sbspaces, per-dbspace archives, logging modes, a server that is not On-Line, and Db2 and Informix side by side.
- The job wrapper is run for real.
- The AIX collector is run under ksh with stub commands in `tests/fake_aix/` that print recorded AIX output, and its parsing is checked.
- Alert rules have `promtool` unit tests in `tests/rules_test.yml`.

## What has and has not been verified

- The exporter's queries and the grants in `docs/privileges.sql` were run against Db2 12.1 Community Edition on Linux, both as the instance owner and as a least-privilege user. They have not been run on Db2 11.5, on AIX, or with HADR configured. A query that fails on your level is reported as `db2_query_success == 0` and the rest keep working.
- The Informix queries were run against Informix 15.0.1 Developer Edition on Linux, over DRDA, as the least-privilege `corewatch` user from `docs/privileges-informix.sql`, which was also confirmed unable to connect to an application database. The long-transaction, lock-wait and archive metrics were checked by causing each condition and watching the metric and the alert. They have not been run on Informix 12.10 or 14.10, on AIX, or with HDR, RSS or SDS secondaries; `sysha_type` role mapping and the `sh_mode` value 5 for On-Line are the only HA and mode values observed. A query that fails on your release is reported as `informix_query_success == 0`.
- The AIX collector has not been run on a real AIX system. Its command output samples were written from the documented formats. Run it once by hand on a test partition and compare the file with what the commands print.
- The Linux collector's `needs-restarting`, `dnf` and `multipath` branches were not exercised, because the build machine had none of them.
- Not yet covered for Informix: HDR and RSS replication lag (`onstat -g cluster`), Enterprise Replication queues, and severe entries in the online log. These are the next additions.
- This project knows nothing about any particular core banking application. To watch the application itself, add a synthetic check that logs in and makes one harmless read call through its API.

## License

MIT
