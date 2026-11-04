# Runbooks

Every alert links to a section here. Each one says what the alert means, what to check, and the usual fix. Commands are given for AIX and Linux where they differ. Run Db2 commands as the instance owner and Informix commands as user `informix`, with `INFORMIXDIR`, `INFORMIXSERVER` and `ONCONFIG` set.

Check a command against the man page, `db2 ? <command>` or the Informix documentation before using it on a production system. Follow your change process for anything that alters state.

Sections: [exporter](#corewatchexporterdown), [Db2](#db2down), [Informix](#informixdown), [AIX](#hostdown), [Linux](#linuxmultipath), [batch jobs](#jobfailed).

## Db2Down

The exporter could not connect to the database or run `SELECT 1`.

1. From the database server: `db2pd -` shows whether the instance is up.
2. If the instance is down: `db2start`, then read `db2diag -H 1h -l Severe,Error` to learn why it stopped.
3. If the instance is up but the database is not: `db2 list active databases`, then `db2 activate db <name>`.
4. If both are up, the problem is between the exporter and Db2. See Db2PortUnreachable.
5. Check the host: `errpt | head` on AIX, `journalctl -p err -S -1h` on Linux, and filesystem space with `df`.

## CorewatchExporterDown

Prometheus cannot scrape the exporter, so no Db2 or Informix alert can fire.

1. Check the process or container: `systemctl status corewatch-exporter` or `docker compose ps corewatch`.
2. Read its log. A config error, an unknown engine or a missing password variable stops it at startup with `configuration error: ...`.
3. Confirm the port (9953 by default) is reachable from Prometheus.

## Db2PortUnreachable

The TCP probe to the Db2 listener failed. It only fires while the exporter is configured to monitor Db2 (`corewatch_engine_enabled{engine="db2"}`), so a Db2 that is deliberately not running does not page.

1. On the server: `db2 get dbm cfg | grep -i SVCENAME` and `db2set DB2COMM` (should show `TCPIP`).
2. Confirm the listener: `netstat -Aan | grep <port>` on AIX, `ss -tlnp | grep <port>` on Linux.
3. If Db2 is listening locally, check firewalls and routing between the prober and the server.

## Db2QueryFailing

One monitoring query fails while the database is up. The `query` label names it.

1. Read the exporter log for the SQL code.
2. `SQL0551N` or `SQL1092N` means a missing privilege. Compare the user's grants with `docs/privileges.sql`.
3. `SQL0206N` or `SQL0440N` means the column or routine does not exist on this Db2 level. Open an issue with the output of `db2level`.

## Db2LogSpace

The transaction log is filling. At 100% Db2 returns `SQL0964C` and transactions fail.

1. Find what holds the log: `db2pd -db <name> -transactions` and look for the oldest transaction, or `db2 "select application_handle, uow_start_time from table(mon_get_unit_of_work(null,-2)) order by uow_start_time fetch first 5 rows only"`.
2. Map the handle to a user and host: `db2pd -db <name> -applications`.
3. With the application owner's agreement, have it commit, or run `db2 "force application (<handle>)"`. A forced rollback of a large transaction takes time and log space of its own.
4. For room right now: `db2 update db cfg for <name> using LOGSECOND <higher>` takes effect online.
5. Check that archiving works. A failing archive target also fills the active log path: `db2pd -db <name> -logs`, and `df` on the log and archive filesystems.

## Db2Tablespace

A fixed-size table space is nearly full. Auto-resize table spaces are covered by the filesystem alerts.

1. `db2pd -db <name> -tablespaces` for size, free pages and containers.
2. If it uses automatic storage, add a path: `db2 "alter stogroup IBMSTOGROUP add '/newpath'"`.
3. Otherwise extend a container: `db2 "alter tablespace <ts> extend (all <pages>)"`, or turn on `AUTORESIZE YES`.
4. Check the filesystem underneath has room: `df -g` on AIX, `df -h` on Linux. Grow it with `chfs -a size=+NG /fs` on AIX or `lvextend -r` on Linux.

## Db2TablespaceState

A table space is not in NORMAL state. The `state` label says which.

1. `BACKUP_PENDING`: take a backup of the database or table space.
2. `ROLLFORWARD_PENDING` or `RESTORE_PENDING`: a restore was started and not finished. Complete the `rollforward ... and stop`.
3. `QUIESCED`: a utility holds it. `db2 list utilities show detail`.
4. `OFFLINE`: a container is missing. Check the filesystem and disks, then `db2 "alter tablespace <ts> switch online"`.

## Db2Backup

No successful backup within the expected window, or none on record.

1. `db2 list history backup all for <name>` shows recent attempts and their SQL codes.
2. Check the backup target has space and is mounted.
3. Check the scheduler ran the job (see JobFailed and JobOverdue).
4. Run one by hand if policy allows: `db2 backup db <name> online to <path> compress include logs`.
5. Verify the image: `db2ckbkp <image file>`.

## Db2Locks

Agents are waiting on locks, or deadlocks are being detected.

1. `db2pd -db <name> -wlocks` lists each waiter and the holder blocking it.
2. `db2pd -db <name> -applications` maps handles to users and hosts.
3. `db2pd -db <name> -transactions` shows how long the holder has been open.
4. Decide with the application owner whether to wait or to force the holder.
5. Repeated deadlocks between the same statements are an application or indexing problem. Capture them with a locking event monitor and hand the output to the vendor.

## Db2LongTransaction

A unit of work has been open for more than 30 minutes. Long transactions pin log space and hold locks.

1. Identify it as in Db2LogSpace step 1.
2. A batch job may legitimately run this long. If so, raise the threshold for that database or silence the alert for the batch window.
3. An idle session holding an open transaction is usually a client that forgot to commit.

## Db2Hadr

The HADR pair is not in PEER state, is disconnected, or the standby is falling behind.

1. On both servers: `db2pd -db <name> -hadr`. Read `HADR_STATE`, `HADR_CONNECT_STATUS` and `HADR_LOG_GAP`.
2. `DISCONNECTED`: check the network between the sites and that the standby instance is up. Start it with `db2 start hadr on db <name> as standby`.
3. `REMOTE_CATCHUP` that never reaches `PEER`: the standby cannot keep up. Check its disk and CPU, and the link bandwidth.
4. Do not run `takeover hadr ... by force` without following the DR runbook. A forced takeover while the primary is still alive causes a split brain.

## Db2Diag

New severe or critical records in `db2diag.log`.

1. `db2diag -H 30m -l Severe,Critical` to read them.
2. `db2 ? <SQL code>` explains any code in the record.
3. If Db2 wrote a FODC directory (first occurrence data capture), keep it and open a case with IBM: `db2support . -d <name> -c`.

## Db2Restarted

The database was activated in the last 10 minutes after having been up. It was either restarted on purpose or it crashed and recovered.

1. Check for a change ticket.
2. If there is none, read `db2diag -H 1h` for the reason, and `errpt` or `journalctl` for a host event at the same time.

## InformixDown

The exporter could not log in to the `sysmaster` database over DRDA or run its test query.

1. On the server: `onstat -`. "Shared memory not initialized" means the server is off-line; any other line shows the mode and uptime.
2. If it is off-line: read the end of the message log with `tail -100 $(onstat -c | awk '$1 == "MSGPATH" {print $2}')`, fix the cause, then start it with `oninit -vy`.
3. If it is On-Line, check the DRDA listener the exporter uses. `grep drsoctcp $INFORMIXSQLHOSTS` (default `$INFORMIXDIR/etc/sqlhosts`) shows its name and port, and that name must be in `DBSERVERNAME` or `DBSERVERALIASES` in the onconfig file. `onstat -g ntt` lists the listener threads.
4. If the listener is up, test the login from the exporter host. A refused password or a missing `CONNECT` on sysmaster shows as `SQL30060N` or `SQL30082N` in the exporter log. See [docs/privileges-informix.sql](privileges-informix.sql).
5. Check the host: `errpt | head` on AIX, `journalctl -p err -S -1h` on Linux, and filesystem space with `df`.

## InformixNotOnline

The server answers, but is not On-Line (`sh_mode` is not 5). The value in the alert is the raw mode.

1. `onstat -` names the mode: Quiescent, Single-User, Fast Recovery, Archive Backup, Shutdown.
2. Quiescent or Single-User usually means someone ran `onmode -s` or `onmode -j` for maintenance. Confirm with the change calendar, then return to On-Line with `onmode -m`.
3. Fast Recovery after a crash finishes on its own; watch `onstat -m`. If it does not progress, read the message log for the reason.
4. A secondary server (HDR, RSS, SDS) runs read-only by design. If this alert fires there, add a `role` exception to the rule.

## InformixPortUnreachable

The TCP probe to the Informix SQLI listener (9088 in the demo) failed. Applications use this port. It only fires while the exporter is configured to monitor Informix.

1. `onstat -g ntt` and `ss -tlnp | grep <port>` on Linux, `netstat -Aan | grep <port>` on AIX, show whether the listener is up.
2. `grep onsoctcp $INFORMIXSQLHOSTS` shows the port. The service name or port must match what clients use.
3. Restart one listener without restarting the server: `onmode -P restart <servername>` (12.10 and later).
4. If the server listens locally, check firewalls and routing between the prober and the server.

## InformixQueryFailing

One monitoring query fails while the server is up. The `query` label names it.

1. Read the exporter log for the error number.
2. `-387` (no connect permission) or `-272` (no select permission) means a missing grant. Compare with [docs/privileges-informix.sql](privileges-informix.sql).
3. `-206` (table not in the database) or `-217` (column not found) means the sysmaster table or column does not exist on this release. The queries were run against Informix 15.0.1; open an issue with the output of `onstat -`.

## InformixLogs

Logical logs are running out. A log is reusable only when it is backed up, is not the current log and holds no part of an open transaction. When none is free the server blocks every update.

1. `onstat -l` lists the logs with their flags: `U` used, `B` backed up, `C` current, `L` holds the last checkpoint. Logs flagged `U` without `B` wait for backup; see InformixLogBackup.
2. If the logs are backed up but still not free, an open transaction pins them. See InformixLongTransaction: `onstat -x` shows where each transaction began (`begin_logpos`).
3. Back up full logs now: `ontape -a`, or `onbar -b -l` with a storage manager.
4. For room right now, add a log after the current one: `onparams -a -d <dbspace> -s <size KB> -i`. With `DYNAMIC_LOGS 2` the server adds logs itself during a long-transaction rollback.

## InformixLogBackup

Full logical logs are waiting to be backed up. Continuous log backup has stopped or cannot keep up, and when the last free log fills the server blocks.

1. `onstat -l`: logs flagged `U` but not `B` are the backlog.
2. Find out how logs are normally backed up: an `ontape -c` session, `onbar -b -l -C`, or the `ALARMPROGRAM` script with `BACKUPLOGS=Y`. Check that process is running and read its log (`BAR_ACT_LOG` for onbar).
3. Check the backup target (tape, directory or storage manager) has space and is reachable.
4. Back up the waiting logs by hand: `ontape -a` or `onbar -b -l`.

## InformixLongTransaction

The oldest open transaction covers a large share of the logical logs. At `LTXHWM` (70% by default) the server starts rolling it back on its own, and the rollback needs log space too; at `LTXEHWM` it gives the rollback exclusive use of the logs, which stops other work.

1. `onstat -x` lists open transactions. `begin_logpos` is `<log unique ID>:<position>`; the lowest unique ID is the oldest transaction. Compare it with the current log (flag `C`) in `onstat -l`.
2. The transaction's `userthread` value appears in the `address` column of `onstat -u`, which gives its session ID (`sessid`).
3. See what the session is doing: `onstat -g ses <sid>` and `onstat -g sql <sid>`.
4. With the application owner's agreement, have it commit, or end the session with `onmode -z <sid>`. Ending it rolls the transaction back, which takes time and log space.
5. Afterwards, ask whether the batch job should commit in smaller units.

## InformixDbspace

A data dbspace is nearly full. Inserts into tables in it fail when it fills.

1. `onstat -d` shows every chunk with its size and free pages.
2. Add a chunk: `onspaces -a <dbspace> -p <path> -o <offset KB> -s <size KB>`. The path must exist, belong to informix:informix and be mode 660.
3. Better long-term: let the space grow by itself. Add a storage pool entry (`EXECUTE FUNCTION task("storagepool add", ...)`) and mark chunks extendable (`EXECUTE FUNCTION task("modify chunk extendable", ...)`) in the `sysadmin` database.
4. Check the filesystem or volume underneath has room: `df -g` on AIX, `df -h` on Linux.

## InformixChunkOffline

A chunk is marked down. Data in it cannot be read and tables in that dbspace return errors.

1. `onstat -d` shows the chunk's flags; `D` means down. The message log says when and why it went down.
2. Check the device: does the path exist, is it owned by informix:informix with mode 660, is the disk healthy? On AIX: `errpt -a | more` and `lspath -l <hdisk>`. On Linux: `dmesg | tail` and `multipath -ll`.
3. If the dbspace is mirrored, the mirror keeps serving. After fixing the device, recover the chunk: `onspaces -s <dbspace> -p <path> -o <offset> -O`.
4. If it is not mirrored, the data needs a warm restore of that dbspace: `onbar -r <dbspace>` or `ontape -r -D <dbspace>`, followed by log restore.

## InformixBackup

A dbspace has no level-0 archive within the expected window, or none at all. A restore always starts from a level-0.

1. See what the scheduler did: `onstat -g arc` (last archive per dbspace), the onbar activity log (`BAR_ACT_LOG`), and JobFailed or JobOverdue if the archive job runs under `corewatch_job.sh`.
2. Check the backup target has space and is reachable.
3. Run one by hand if policy allows: `ontape -s -L 0` or `onbar -b -L 0`.
4. A newly added dbspace has no archive until the next level-0 runs. That is expected after `onspaces -c`.

## InformixLocks

Sessions are queuing on locks, or deadlocks are occurring.

1. `onstat -u` shows sessions; a waiting session has a lock address in the `wait` column. `onstat -k` lists the locks with their `owner` and `waiter`.
2. Find the holder's session and what it runs: `onstat -g ses <sid>`, `onstat -g sql <sid>`.
3. With the owner's agreement, have it commit, or end it with `onmode -z <sid>`.
4. For recurring contention: tables created with page-level locking lock many rows at once (`ALTER TABLE <t> LOCK MODE (ROW)`), and applications should use `SET LOCK MODE TO WAIT <seconds>` instead of failing at once or waiting forever.
5. Deadlocks return error `-143` to one of the sessions. Repeated deadlocks between the same programs mean they update rows in a different order.

## InformixOverflow

The lock, user or transaction table hit its limit (`onstat -p`: `ovlock`, `ovuser`, `ovtrans`). The session that hit it got an error.

1. `onstat -p` shows the overflow counters; `onstat -z` resets them after the fix.
2. Lock table: find sessions holding many locks with `onstat -u` (`locks` column). A program that locks a whole table row by row needs `LOCK TABLE` or smaller transactions. Raise `LOCKS` in the onconfig file if the load is legitimate.
3. User or transaction table: check `onstat -g ses` for a connection leak before raising limits.

## InformixCheckpoint

The last checkpoint blocked transactions for more than two seconds. Non-blocking checkpoints should block for close to zero.

1. `onstat -g ckp` shows recent checkpoints with duration, block time, dirty buffers and the trigger.
2. Long flushes point at slow disks (`iostat` on the chunk devices) or too many dirty buffers.
3. Review `AUTO_CKPTS`, `RTO_SERVER_RESTART`, the physical log size and the LRU settings. Blocking checkpoints triggered by a full physical log mean it is too small.

## InformixRestarted

The server's uptime reset in the last 10 minutes.

1. Was it planned? Check the change calendar.
2. If not: `onstat -m` and the message log around the restart. An assertion failure leaves `af.*` files in `DUMPDIR`.
3. Check the host: `errpt` on AIX, `journalctl -b -1` on Linux.

## InformixNotLogged

A database has no transaction logging. It cannot roll back, cannot be restored to a point in time, and cannot be replicated with HDR or RSS.

1. Confirm whether that is deliberate (scratch or staging data sometimes is).
2. To turn logging on: `ondblog unbuf <db>` (or `buf`), then take a level-0 archive (`ontape -s -L 0`), which completes the change. With ontape alone: `ontape -s -L 0 -U <db>`.

## HostDown

Prometheus cannot scrape the host's collector.

1. Ping and log in. If the host is unreachable, check the HMC (AIX partitions) or the hypervisor console.
2. If the host is up, the collector is down. AIX: is `textfile_server.py` or node_exporter running? Linux: `systemctl status node_exporter`.

## AixErrpt

The AIX error log has new permanent errors. Class H is hardware, class S is software.

1. `errpt | head -20` for the summary.
2. `errpt -a -j <IDENTIFIER>` for the detail of one entry, including the resource name and probable causes.
3. For hardware: note the resource (`hdisk3`, `fcs0`, `ent1`), check redundancy is intact with `lspath` and `lsvg -l <vg>`, and open a hardware case.
4. After the cause is fixed, entries age out of the alert's look-back window by themselves. Do not run `errclear` to silence an alert.

## AixStale

A mirrored volume group has stale partitions. One copy of the data is out of date.

1. `lsvg <vg>` shows the stale PV and PP counts. `lsvg -l <vg>` shows which logical volumes are stale.
2. `lspv` and `errpt` to find whether a disk failed or went missing.
3. When the disk is back: `varyonvg <vg>` (it is safe on an active group and revives the disk), then `syncvg -v <vg>`.

## AixPaths

A disk path is Failed or Missing. The disk still works on its other paths, but redundancy is lost.

1. `lspath` shows which disk and which parent adapter.
2. `lsmpio -l <hdisk>` for per-path state, and `errpt` for the event.
3. Many failed paths on one adapter point at that adapter, its cable or a SAN switch. Involve the storage team.
4. Once fixed: `chpath -l <hdisk> -p <parent> -s enable`.

## AixPaging

Paging space use is high. The system is short of memory.

1. `svmon -G` for overall memory, `svmon -Pt 10` for the top processes.
2. `lsps -a` for each paging space.
3. If Db2 grew: check `INSTANCE_MEMORY` and `DATABASE_MEMORY`, and whether a workload changed.
4. Adding paging space (`chps -s <LPs> hd6`) buys time. It does not fix a memory shortage.

## Filesystem

A filesystem is nearly full. For Db2 the dangerous ones hold the active logs, the archive logs, the diagnostic path and the backups.

1. Find what grew. AIX: `du -sk /fs/* | sort -n | tail`. Linux: `du -xsh /fs/* | sort -h | tail`.
2. Usual causes: archive logs not being pruned, old backup images, a large `db2diag.log` (rotate it with `db2diag -A`), core or FODC dumps.
3. Grow the filesystem online if the volume group has room. AIX: `chfs -a size=+5G /fs`. Linux: `lvextend -r -L +5G /dev/vg/lv`.
4. Never delete active Db2 log files by hand. Remove archived logs only through `db2 prune history ... and delete` or your backup tool.

## AixMksysb

The newest mksysb image is more than 8 days old. Without one, rebuilding the operating system after a failure is slow.

1. Check the job that creates it, and space in the target directory.
2. Run one: `mksysb -i /backup/$(hostname).mksysb`.

## CollectorStale

A host collector script has not rewritten its file for 15 minutes.

1. Check the cron entry: `crontab -l`.
2. Run the script by hand and read the error.
3. Check the output directory exists and has space.

## LinuxMultipath

A multipath path has failed.

1. `multipath -ll` shows the device and the failed path.
2. `journalctl -k -S -1h | grep -i -E "multipath|scsi"` for the event.
3. Involve the storage team if several devices lost the same path.

## LinuxSwap

Swap use is high.

1. `free -h`, then `ps aux --sort=-rss | head` for the largest processes.
2. For Db2 hosts, check `INSTANCE_MEMORY` against physical memory.

## LinuxPatching

Updates are installed but need a reboot, or security updates have been waiting too long.

1. `needs-restarting -r` and `dnf updateinfo list --security --updates`.
2. Schedule the patch window and reboot through your change process.

## JobFailed

A batch job ended with a non-zero exit code.

1. Read the job's own log.
2. Decide whether it is safe to rerun. Some posting jobs must not run twice.
3. Rerun through the scheduler, wrapped in `corewatch_job.sh`, so the alert clears when it succeeds.

## JobOverdue

A job has not succeeded within the window given by `--max-age`. Either it keeps failing or it never started.

1. Check the scheduler: was the job held, disabled or blocked by a predecessor?
2. Check the host was up at the scheduled time.
3. Run it, then find out why it did not start.

## JobRunningLong

A job has run for more than twice its 7-day average.

1. Is it making progress? Check its log and, for Db2 work, `db2pd -db <name> -applications` and `-wlocks`.
2. A job waiting on a lock looks exactly like a slow job. See Db2Locks.
3. Month-end runs take longer. Expect this alert then, or silence it for that window.
