#!/bin/bash
set -e

# Create databases (idempotent - psql CREATE DATABASE doesn't support IF NOT EXISTS)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    SELECT 'CREATE DATABASE platform' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'platform')\gexec
    SELECT 'CREATE DATABASE analytics' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'analytics')\gexec
EOSQL

# Create roles (idempotent)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'platform_app') THEN
            CREATE ROLE platform_app LOGIN PASSWORD '${PLATFORM_DB_PASSWORD}';
        END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics_reader') THEN
            CREATE ROLE analytics_reader LOGIN PASSWORD '${ANALYTICS_READER_PASSWORD}';
        END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics_admin') THEN
            CREATE ROLE analytics_admin LOGIN PASSWORD '${ANALYTICS_ADMIN_PASSWORD}';
        END IF;
    END
    \$\$;
EOSQL

# Grant privileges on platform database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d platform <<-EOSQL
    GRANT ALL PRIVILEGES ON DATABASE platform TO platform_app;
    GRANT ALL ON SCHEMA public TO platform_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO platform_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO platform_app;
EOSQL

# Grant privileges on analytics database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d analytics <<-EOSQL
    GRANT ALL PRIVILEGES ON DATABASE analytics TO analytics_admin;
    GRANT ALL ON SCHEMA public TO analytics_admin;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO analytics_admin;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO analytics_admin;

    GRANT CONNECT ON DATABASE analytics TO analytics_reader;
    GRANT USAGE ON SCHEMA public TO analytics_reader;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analytics_reader;
EOSQL
