# corewatch

Prometheus monitoring for IBM Db2 running on AIX and Linux. It is aimed at teams that operate a Db2-backed core system and want alerts for the things that stop it: a full transaction log, a missed backup, lock pile-ups, a failed disk path, a batch job that never started.

It has four parts:

- `corewatch-db2`, a Db2 exporter written in Python. It runs anywhere that can reach the database, so nothing has to be installed on the database server.
- An AIX collector (ksh) for what only AIX has: `errpt`, stale LVM partitions, MPIO paths, paging space, `oslevel`, interim fixes and mksysb age.
- A Linux collector (bash) for what node_exporter does not cover: patch status, pending reboots, multipath path health.
- A job wrapper (POSIX sh) that records the outcome of each batch job on either platform.

Around those: 42 alert rules with tests, an Alertmanager config with two severities, a Grafana dashboard, a runbook for every alert, and a Docker Compose demo that brings up Db2 Community Edition with the whole stack.

An interactive 3D map of the stack is in [docs/index.html](docs/index.html). Open it in a browser.

## What is monitored

| Layer | Signals | Where it comes from |
| --- | --- | --- |
| Db2 | Up, log space, table space use and state, backup age, lock waits, deadlocks, oldest open transaction, connections, throughput, buffer pool reads, HADR state and log gap, severe diagnostic records | `corewatch-db2` over SQL |
| AIX | errpt entries by type and class, stale partitions, volume group free space, MPIO path status, paging space, filesystem use, oslevel, interim fixes, mksysb age | `collectors/aix/corewatch_aix.sh` |
| Linux | CPU, memory, disk and filesystem (node_exporter), plus reboot-required, pending security updates, multipath paths | node_exporter and `collectors/linux/corewatch_linux.sh` |
| Batch jobs | Start, end, duration, exit code, last success, allowed gap | `collectors/common/corewatch_job.sh` |
| Outside view | Db2 port reachable, TLS certificate expiry | blackbox_exporter |

## Try it

You need Docker with Compose and about 6 GB of free disk. The Db2 image is x86-64 only, so on an Apple Silicon Mac it runs under emulation and may be slow or fail.

```
cp .env.example .env        # then edit the two passwords
docker compose up -d --build
```

Db2 takes three to five minutes to create its database the first time. Then open:

- Grafana at http://localhost:3000 (user `admin`, the password you set)
- Prometheus alerts at http://localhost:9090/alerts
- Raw metrics at http://localhost:9953/metrics

The demo runs two pretend batch jobs. `posting` fails about one run in five, and `month_end` succeeds once and never runs again, so within a few minutes you can watch `JobFailed` and `JobOverdue` fire.

To see a Db2 alert, hold a lock in one terminal and wait on it in another:

```
docker compose exec db2 su - db2inst1
db2 connect to testdb
db2 "create table t1 (id int not null primary key, v varchar(20))"
db2 "insert into t1 values (1, 'a')"
db2 +c "update t1 set v = 'x' where id = 1"     # +c leaves the transaction open
```

`db2_oldest_uow_age_seconds` starts climbing, and a second session running the same update shows up in `db2_locks_waiting`.

## Install for real

### The Db2 exporter

```
pip install .
export COREWATCH_DB2_PASSWORD=...
corewatch-db2 --config config.yml
```

Copy `config.example.yml` and list your databases. Passwords come from an environment variable or a file. The exporter refuses a password written in the config file.

Create a dedicated database user with the grants in [docs/privileges.sql](docs/privileges.sql). It needs no access to application data.

One exporter can watch many databases. The `labels` you set per database (for example `client` and `environment`) are copied onto every metric, so one dashboard and one rule set cover every hosted customer.

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

Every alert carries a `layer` label (`db2`, `aix`, `linux`, `jobs`, `monitoring`) and a `runbook_url` pointing at [docs/runbooks.md](docs/runbooks.md). When a database is down, Alertmanager suppresses that database's other alerts.

Thresholds are starting points. Table space alerts skip auto-resize table spaces on purpose, because those report as nearly full all the time. The filesystem alerts cover them.

## Tests

```
pip install -e ".[dev]"
pytest
```

- The collector is tested with a fake connection: healthy database, database down, one failing query, broken connection, cache behaviour, no backup, infinite logging.
- The job wrapper is run for real.
- The AIX collector is run under ksh with stub commands in `tests/fake_aix/` that print recorded AIX output, and its parsing is checked.
- Alert rules have `promtool` unit tests in `tests/rules_test.yml`.

## What has and has not been verified

- The exporter's queries and the grants in `docs/privileges.sql` were run against Db2 12.1 Community Edition on Linux, both as the instance owner and as a least-privilege user. They have not been run on Db2 11.5, on AIX, or with HADR configured. A query that fails on your level is reported as `db2_query_success == 0` and the rest keep working.
- The AIX collector has not been run on a real AIX system. Its command output samples were written from the documented formats. Run it once by hand on a test partition and compare the file with what the commands print.
- The Linux collector's `needs-restarting`, `dnf` and `multipath` branches were not exercised, because the build machine had none of them.
- This project knows nothing about any particular core banking application. To watch the application itself, add a synthetic check that logs in and makes one harmless read call through its API.

## License

MIT
