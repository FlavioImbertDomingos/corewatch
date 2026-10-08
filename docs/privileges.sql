-- A least-privilege monitoring user for corewatch.
--
-- Db2 authenticates against the operating system, so first create the OS user
-- (mkuser corewatch on AIX, useradd corewatch on Linux) and set its password.
-- Then, connected to each database as a DBADM or SECADM user, run:

GRANT CONNECT ON DATABASE TO USER corewatch;

-- Monitor table functions and the backup history view. DATAACCESS is not needed.
-- These grants were run and verified against Db2 12.1 Community Edition on Linux.
GRANT EXECUTE ON FUNCTION SYSPROC.MON_GET_DATABASE      TO USER corewatch;
GRANT EXECUTE ON FUNCTION SYSPROC.MON_GET_TABLESPACE    TO USER corewatch;
GRANT EXECUTE ON FUNCTION SYSPROC.MON_GET_UNIT_OF_WORK  TO USER corewatch;
GRANT EXECUTE ON FUNCTION SYSPROC.MON_GET_HADR          TO USER corewatch;
GRANT EXECUTE ON FUNCTION SYSPROC.MON_GET_TRANSACTION_LOG TO USER corewatch;
GRANT SELECT  ON SYSIBMADM.DB_HISTORY      TO USER corewatch;

-- Only if you enable the optional diag_history query:
-- GRANT EXECUTE ON FUNCTION SYSPROC.PD_GET_DIAG_HIST TO USER corewatch;

-- If a grant is refused or a query still fails, the exporter says which one:
-- look for db2_query_success{query="..."} == 0. On some Db2 levels the
-- simplest working alternative is the SQLADM authority or membership in the
-- group named by SYSMON_GROUP. Check with your DBA before granting either.
