#!/bin/bash
# 建库与角色。仅在数据卷首次初始化时执行（docker-entrypoint-initdb.d 语义），
# 因此角色/库创建无需幂等守卫；数据卷复用时本脚本不会再次运行。
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE ROLE platform_app LOGIN PASSWORD '${PLATFORM_APP_PASSWORD}';
    CREATE ROLE analytics_owner LOGIN PASSWORD '${ANALYTICS_OWNER_PASSWORD}';
    CREATE ROLE analytics_readonly LOGIN PASSWORD '${ANALYTICS_READONLY_PASSWORD}';
    CREATE DATABASE platform OWNER platform_app;
    CREATE DATABASE analytics OWNER analytics_owner;
SQL

# 平台库：仅平台身份可连接（owner 自带连接权）。
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname platform <<-SQL
    REVOKE CONNECT ON DATABASE platform FROM PUBLIC;
SQL

# 分析库：撤销公共连接，只读身份仅获连接权；对象权限由迁移后的授权步骤授予。
# 角色默认只读事务是第一道数据库防线（执行器另设事务级只读）。
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname analytics <<-SQL
    REVOKE CONNECT ON DATABASE analytics FROM PUBLIC;
    GRANT CONNECT ON DATABASE analytics TO analytics_readonly;
    ALTER ROLE analytics_readonly SET search_path = analytics;
    ALTER ROLE analytics_readonly SET default_transaction_read_only = on;
SQL
