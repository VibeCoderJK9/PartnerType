#!/bin/sh
# Creates the restricted application role for the local demo. The demo uses
# fixed, public passwords and must never be exposed beyond this computer.
set -eu
psql -U partnertype_demo_owner -d partnertype_demo -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE partnertype_demo_app LOGIN PASSWORD 'local-demo-app-only' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE partnertype_demo TO partnertype_demo_app;
SQL
