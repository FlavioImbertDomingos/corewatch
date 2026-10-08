# Runbooks

Every alert links to a section here. Each one says what the alert means, what to check, and the usual fix. Commands are given for AIX and Linux where they differ. Run Db2 commands as the instance owner.

Check a command against the man page or `db2 ? <command>` before using it on a production system. Follow your change process for anything that alters state.

## Db2Down

The exporter could not connect to the database or run `SELECT 1`.

1. From the database server: `db2pd -` shows whether the instance is up.
2. If the instance is down: `db2start`, then read `db2diag -H 1h -l Severe,Error` to learn why it stopped.
3. If the instance is up but the database is not: `db2 list active databases`, then `db2 activate db <name>`.
4. If both are up, the problem is between the exporter and Db2. See Db2PortUnreachable.
5. Check the host: `errpt | head` on AIX, `journalctl -p err -S -1h` on Linux, and filesystem space with `df`.

## Db2ExporterDown

Prometheus cannot scrape the exporter, so no Db2 alert can fire.

1. Check the process or container: `systemctl status corewatch-db2` or `docker compose ps corewatch`.
2. Read its log. A config error or a missing password variable stops it at startup.
3. Confirm the port (9953 by default) is reachable from Prometheus.

## Db2PortUnreachable

The TCP probe to the Db2 listener failed.

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
