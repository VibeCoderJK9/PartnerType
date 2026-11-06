#!/bin/sh
set -eu
psql -U ph_test_owner -d ph_test_control -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE ph_test_app LOGIN PASSWORD 'isolated-test-app-only' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
