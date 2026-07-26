#!/bin/bash
# 首次在空数据卷上启动时执行：创建双库三身份与权限边界。
# 身份职责见 docs/design/architecture.md。
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE ROLE platform_app LOGIN PASSWORD '${PLATFORM_APP_PASSWORD}';
    CREATE ROLE analytics_owner LOGIN PASSWORD '${ANALYTICS_OWNER_PASSWORD}';
    CREATE ROLE analytics_reader LOGIN PASSWORD '${ANALYTICS_READER_PASSWORD}';

    CREATE DATABASE platform OWNER platform_app;
    CREATE DATABASE analytics OWNER analytics_owner;

    -- 两库互不可达：收回 PUBLIC 连接权，逐库精确授权
    REVOKE CONNECT ON DATABASE platform FROM PUBLIC;
    REVOKE CONNECT ON DATABASE analytics FROM PUBLIC;
    GRANT CONNECT ON DATABASE platform TO platform_app;
    GRANT CONNECT ON DATABASE analytics TO analytics_owner;
    GRANT CONNECT ON DATABASE analytics TO analytics_reader;

    -- 只读身份的角色级兜底：只读事务 + 语句超时（执行器另行 SET LOCAL）
    ALTER ROLE analytics_reader SET default_transaction_read_only = on;
    ALTER ROLE analytics_reader SET statement_timeout = '${QUERY_TIMEOUT_MS:-5000}ms';
    ALTER ROLE analytics_reader IN DATABASE analytics SET search_path = analytics;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname analytics <<-EOSQL
    -- 收紧默认 public schema，业务对象统一位于契约 schema（由迁移创建）
    REVOKE ALL ON SCHEMA public FROM PUBLIC;
EOSQL
