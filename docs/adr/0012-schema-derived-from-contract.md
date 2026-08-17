# analytics 的 DDL 从契约派生，业务口径落为 CHECK 约束

`CREATE TABLE` 由 `contract.json` 生成（字段类型、`NOT NULL`、主键、唯一、外键），不手写第二份 schema，契约保持单一事实源。契约中的业务口径同时落为数据库 CHECK 约束：`allowed_values` 生成 `IN` 清单、折扣区间、成本不高于标价——违反契约口径的数据在数据库层即被拒绝。表级 `GRANT SELECT` 也按契约表清单逐一派生，不在 initdb 脚本中硬编码。

数据加载读取已提交的权威 CSV，经 psycopg `COPY ... FROM STDIN` 流式完成；seed 不调用生成器、不重新生成数据。
