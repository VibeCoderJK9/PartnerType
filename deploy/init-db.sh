#!/bin/sh
set -eu
# Only generated hexadecimal secrets; never interpolate operator input as SQL.
APP_PASSWORD=$(cat /run/secrets/db_password)
case "$APP_PASSWORD" in *[!a-f0-9]*|'') exit 1;; esac
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 --set=db_name="$POSTGRES_DB" <<SQL
CREATE ROLE partnerhub_app LOGIN PASSWORD '$APP_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO partnerhub_app;
SQL
