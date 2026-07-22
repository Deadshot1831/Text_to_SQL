-- PostgreSQL only. Runs after schema+seed in docker-entrypoint-initdb.d.
-- Creates a SELECT-only role: the second line of defense behind the app-level
-- guardrails. Even a query the guardrails miss cannot write as this user.
-- (DuckDB local dev skips this file; it relies on guardrails + read-only txn.)

CREATE ROLE app_readonly WITH LOGIN PASSWORD 'readonly_pw';

GRANT CONNECT ON DATABASE appdb TO app_readonly;
GRANT USAGE ON SCHEMA public TO app_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_readonly;

-- Cover any tables created later, and explicitly withhold write privileges.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO app_readonly;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM app_readonly;
