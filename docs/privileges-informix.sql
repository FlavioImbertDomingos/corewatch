-- A least-privilege monitoring user for corewatch on Informix.
--
-- Informix authenticates against the operating system (or a mapped user with
-- USERMAPPING), so first create the OS user on the database server:
--   AIX:   mkuser corewatch && passwd corewatch
--   Linux: useradd -M -s /sbin/nologin corewatch && passwd corewatch
-- Then, as user informix:
--   dbaccess sysmaster privileges-informix.sql

DATABASE sysmaster;

-- Every table the exporter reads (sysprofile, syslogs, systxptab, sysrstcb,
-- sysdbspaces, syschunks, sysdbstab, syscheckpoint, sysshmvals, sysha_type,
-- sysdatabases, syssessions) is readable by any user connected to sysmaster.
-- No grant on any business database is needed, and none should be given.
GRANT CONNECT TO corewatch;

-- Verified against Informix 15.0.1 Developer Edition: with only this grant the
-- corewatch user runs every monitoring query, and a connection attempt to an
-- application database is refused with SQL30060N (no CONNECT privilege).
--
-- The exporter connects over DRDA, so the server needs a DRDA listener:
--   sqlhosts:  ifx_core_dr  drsoctcp  ifx-prod-01  9089
--   onconfig:  DBSERVERALIASES ifx_core_dr
-- If a query still fails, the exporter names it: informix_query_success{query="..."} == 0.
