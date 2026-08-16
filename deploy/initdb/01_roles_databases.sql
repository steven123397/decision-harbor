-- DecisionHarbor 数据库初始化：仅在 PGDATA 为空时由 PostgreSQL 镜像执行。
-- 只建数据库与角色；表结构与数据由 API 引导阶段建立（见 docs/design/data-and-seeding.md）。

CREATE ROLE platform_owner LOGIN PASSWORD 'platform_owner';
CREATE ROLE platform_app LOGIN PASSWORD 'platform_app';
CREATE ROLE analytics_owner LOGIN PASSWORD 'analytics_owner';
CREATE ROLE analytics_readonly LOGIN PASSWORD 'analytics_readonly';

CREATE DATABASE platform OWNER platform_owner;
CREATE DATABASE analytics OWNER analytics_owner;

-- 收紧库级 CONNECT：非授权角色连 platform 库都被拒（第二道边界）。
\connect platform
REVOKE CONNECT ON DATABASE platform FROM PUBLIC;
GRANT CONNECT ON DATABASE platform TO platform_owner, platform_app;

\connect platform
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO platform_owner;
GRANT USAGE ON SCHEMA public TO platform_app;

\connect analytics
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO analytics_owner;
-- 库级 CONNECT 同样收紧；只保留两个分析身份。
REVOKE CONNECT ON DATABASE analytics FROM PUBLIC;
GRANT CONNECT ON DATABASE analytics TO analytics_owner, analytics_readonly;
-- 契约表都在 analytics schema；两个身份的 search_path 都指向它。
ALTER ROLE analytics_owner SET search_path = analytics;
ALTER ROLE analytics_readonly SET search_path = analytics;

-- 只读身份的兜底限制（应用层会按连接再设置同值超时）。
ALTER ROLE analytics_readonly SET statement_timeout = '10s';
ALTER ROLE analytics_readonly SET default_transaction_read_only = on;
