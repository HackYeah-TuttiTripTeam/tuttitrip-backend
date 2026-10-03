-- Read-only access for role tuttitrip_readonly (pgAdmin servers, DBOS dashboard)
-- in one environment database. Applied by deploy/admin/setup.sh (idempotent):
--   psql -v dbname=<database> -f deploy/admin/readonly-grants.sql
-- The role itself (LOGIN, password, default_transaction_read_only) is created
-- by setup.sh. Owner of every table is `tuttitrip`; default privileges below
-- cover tables that migrations and `dbos migrate` create later.

SET client_min_messages = warning;

GRANT CONNECT ON DATABASE :"dbname" TO tuttitrip_readonly;

GRANT USAGE ON SCHEMA public TO tuttitrip_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO tuttitrip_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO tuttitrip_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE tuttitrip IN SCHEMA public
    GRANT SELECT ON TABLES TO tuttitrip_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE tuttitrip IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO tuttitrip_readonly;

-- DBOS system tables (workflow_status, operation_outputs, queues, ...).
CREATE SCHEMA IF NOT EXISTS dbos AUTHORIZATION tuttitrip;
GRANT USAGE ON SCHEMA dbos TO tuttitrip_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA dbos TO tuttitrip_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA dbos TO tuttitrip_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE tuttitrip IN SCHEMA dbos
    GRANT SELECT ON TABLES TO tuttitrip_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE tuttitrip IN SCHEMA dbos
    GRANT SELECT ON SEQUENCES TO tuttitrip_readonly;
