#!/usr/bin/env bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v db_migrator_password="$DB_MIGRATOR_PASSWORD" \
  -v platform_writer_password="$PLATFORM_WRITER_PASSWORD" \
  -v analytics_reader_password="$ANALYTICS_READER_PASSWORD" \
  -v platform_readiness_password="$PLATFORM_READINESS_PASSWORD" \
  -v analytics_readiness_password="$ANALYTICS_READINESS_PASSWORD" <<'EOSQL'
CREATE ROLE db_migrator LOGIN PASSWORD :'db_migrator_password' NOSUPERUSER CREATEDB;
CREATE ROLE platform_writer LOGIN PASSWORD :'platform_writer_password' NOSUPERUSER NOCREATEDB;
CREATE ROLE analytics_reader LOGIN PASSWORD :'analytics_reader_password' NOSUPERUSER NOCREATEDB;
CREATE ROLE platform_readiness LOGIN PASSWORD :'platform_readiness_password' NOSUPERUSER NOCREATEDB;
CREATE ROLE analytics_readiness LOGIN PASSWORD :'analytics_readiness_password' NOSUPERUSER NOCREATEDB;
CREATE DATABASE platform OWNER db_migrator;
CREATE DATABASE analytics OWNER db_migrator;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname platform <<'EOSQL'
REVOKE CONNECT ON DATABASE platform FROM PUBLIC;
GRANT CONNECT ON DATABASE platform TO db_migrator, platform_writer, platform_readiness;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname analytics <<'EOSQL'
REVOKE CONNECT ON DATABASE analytics FROM PUBLIC;
GRANT CONNECT ON DATABASE analytics TO db_migrator, analytics_reader, analytics_readiness;
EOSQL
