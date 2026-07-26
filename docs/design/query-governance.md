# 查询治理策略与资源限制

## 范围

本文定义 `policy` 模块的判定规则、对象访问范围和 `executor` 的资源限制。治理要求的权威来源是 [产品需求](../background/product-requirements.md) 的"SQL 治理规则"一节；本文将其落实为可实现、可测试的规则。错误码注册表见 [query-runs-api.md](query-runs-api.md)。

## 不变量

- 策略判定只依据 SQL 文本解析出的 AST 与对象访问范围，不使用字符串黑名单作为判定依据。
- 判定是纯函数：相同输入必得相同结论，不访问网络或数据库。
- 任何未被规则显式允许的语句形态一律拒绝（默认拒绝）。
- 每次拒绝携带稳定错误码；错误码一经发布不改语义。

## 判定规则

使用 SQLGlot 以 `postgres` 方言解析。按顺序检查，任一步失败即拒绝：

1. **可解析**：解析失败或产生空语句 → `policy_parse_error`。
2. **单语句**：解析结果必须恰好一条语句 → 否则 `policy_multiple_statements`。
3. **只读表达式**：根节点必须是 `SELECT`、`WITH ... SELECT`，或分支均为只读查询的 `UNION` / `INTERSECT` / `EXCEPT` → 否则 `policy_forbidden_statement`。
4. **全树扫描禁止节点**：遍历整棵 AST，出现以下任意形态即拒绝：
   - 数据与结构修改：`INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`COPY`、`CALL`、`DO`（含出现在 CTE 内的形态）→ `policy_forbidden_statement`；
   - `SELECT INTO`、`FOR UPDATE` / `FOR NO KEY UPDATE` / `FOR SHARE` / `FOR KEY SHARE` 锁定子句 → `policy_forbidden_feature`。锁定子句虽会被只读事务在库层拒绝，但策略层显式拒绝能给出稳定错误码而非透传数据库报错。
5. **函数调用范围**：全树核对函数调用与类型转换目标：
   - 允许：`api/app/sql_functions.py` 收录的安全允许集，覆盖聚合、窗口、数值、字符串、日期时间、条件与类型转换；收录标准与扩展流程见该模块文档字符串；
   - 运算符语法（`AND`、`OR`、`EXISTS`、`ARRAY[…]`、`~`、`COLLATE`）在 AST 上同样落成函数节点，属于语法而非可具名调用的例程，不受允许集约束；其中的子查询与对象引用由规则 6 覆盖；
   - 其余一切调用一律 `policy_forbidden_function`，包括会执行或解释 SQL 文本的（`query_to_xml`、`table_to_xml`、`dblink`）、读系统目录或服务器状态的（`version`、`current_setting`、`to_regclass`、`pg_get_viewdef`、`has_table_privilege`、`current_user`）、有副作用或消耗服务器资源的（`set_config`、`pg_sleep`、`generate_series`）、触及文件系统与网络的（`pg_read_file`、`pg_ls_dir`），以及任何未收录的函数；
   - schema 限定的函数调用（如 `pg_catalog.count(…)`）一律拒绝：限定名可指向别处的同名函数，按名字判定无从覆盖；
   - 不带括号的无参系统信息表达式（`session_user`、`user`、`current_role`、`current_catalog`）语法上是列引用、语义上是函数调用，未加引号且未限定表名时按函数处理；加引号或带表限定的同名列不受影响；
   - 类型转换目标必须是内建数据类型；对象标识与伪类型（`::regclass`、`::oid`、`::regproc`）以及自定义类型 → `policy_forbidden_feature`。`'pg_class'::regclass` 不出现任何表引用就能解析系统对象，必须在此拦住。
6. **对象访问范围**：按真实词法作用域核对每个表引用：
   - 作用域规则同 SQL 标准：非递归 `WITH` 中每个 CTE 体只能看见更早的兄弟（看不见自身），`WITH RECURSIVE` 下全部兄弟互相可见；内层 `WITH` 引入的名字不会外泄到定义它的作用域之外；
   - 引用命中当前作用域可见的 CTE 名 → 通过，不再按物理表核对；派生表别名同理不是物理表；
   - 否则按物理表核对：允许五张契约表的无限定引用，或以契约 schema 限定的引用；
   - 其余一切引用——其他 schema、`pg_catalog`、`information_schema`、系统视图、未知表——→ `policy_forbidden_object`。

允许的语法能力与背景一致：连接、子查询、聚合、窗口函数、集合运算、嵌套与递归 CTE。

判定为允许时，策略输出归一化后的单条语句文本交给执行器；执行器只接受策略输出，不接受原始用户输入。

## 资源限制

执行器对每次执行实施三层限制，参数由环境变量配置：

| 限制 | 默认值 | 实施方式 |
| --- | --- | --- |
| 输入长度 | 100,000 字符 | 路由层在解析前拒绝（422），封顶解析成本与审计存储 |
| 语句超时 | 5,000 ms | 执行器在事务内 `SET LOCAL statement_timeout`，该设置覆盖连接与角色取值；`analytics_reader` 角色级默认值与连接级 `DB_STATEMENT_TIMEOUT_MS`（见 [local-runtime.md](local-runtime.md)）作为兜底 |
| 结果行数上限 | 1,000 行 | 执行器最多取上限 +1 行探测是否超限 |

超时触发 → 记录 `failed`、错误码 `execution_timeout`。

行数超限的处理是**截断而非失败**：返回前 N 行并在响应与审计中显式标记 `truncated: true`。理由：上限的目的是保护 API 内存与响应体大小，而非否定查询本身；截断保留部分结果的分析价值，显式标记防止把截断结果误读为完整结果。

执行使用只读事务（`default_transaction_read_only = on` 配置在 `analytics_reader` 角色上），与策略、身份权限共同构成三层防线。

## 关键决策

- **默认拒绝而非枚举危险**：规则描述"允许什么"，扫描仅用于双保险；SQLGlot 新增或未知的节点类型不会自动获得通行。
- **函数收敛为安全允许集**（取代原"不做函数白名单"决策）：只放行显式收录的分析函数。原决策认为函数滥用可由语句超时与只读授权兜底，实证证伪：`query_to_xml('SELECT relname FROM pg_catalog.pg_class', …)` 把系统 SQL 藏在字符串参数里，由数据库在执行期解释，既不超时也不越权，语句超时与只读身份都拦不住。允许集按能力分组并附收录标准，误伤风险由业务查询语料的单元测试覆盖。
- **CTE 引用按词法作用域判定**：不使用"全树 CTE 名集合"来跳过物理表核对。全局名集合会把词法上不可见的名字当作可见，`WITH shadow AS (WITH pg_class AS (…) SELECT … FROM pg_class) SELECT … FROM pg_class` 的外层 `pg_class` 因此被误判为 CTE 引用而放行，直读系统目录。按作用域判定后，名字不可见的引用一律回落到物理表核对，与物理表同名的 CTE 无法把系统对象带进来。
- **归一化语句交执行器**：执行的是策略解析后重新生成的 SQL 而非原始文本，消除"解析所见"与"执行所见"不一致的走私空间；审计中保存的仍是用户原始 SQL。

## 失败与迁移

- SQLGlot 升级可能改变解析行为：单元测试用例库即回归防线，升级依赖必须全量通过。
- 函数允许集同时收录用户书写形式与 SQLGlot 折叠后的规范名（如 `to_char` 折叠为 `TIME_TO_STR`）。升级后规范名若变化，表现为合法业务查询被拒，由业务查询语料用例暴露；解析器版本随 [local-runtime.md](local-runtime.md) 的依赖锁固定，升级是显式动作。
- 契约表集合变化时，允许对象清单随数据契约版本更新，不在代码中散落硬编码。

## 测试接缝

`policy` 纯函数用 pytest 表驱动单元测试，不需要数据库。用例至少覆盖：

- 允许：单表查询、多表连接、`WITH ... SELECT`、嵌套子查询、聚合与窗口函数、`UNION` / `INTERSECT` / `EXCEPT`、契约 schema 限定引用、CTE 名与物理表同名时的正确解析。
- 允许（词法作用域）：CTE 引用前序兄弟、CTE 体内嵌套 `WITH`、派生表内嵌套 `WITH`、`WITH RECURSIVE` 自引用、同名 CTE 体内引用物理表。
- 允许（函数允许集）：聚合、统计聚合、`FILTER`、`WITHIN GROUP`、窗口函数全集、数值与字符串函数、日期函数、条件与类型转换、运算符与谓词，以及一条覆盖 CTE + 聚合 + 窗口的典型业务查询。这组语料是函数允许集的误伤防线。
- 拒绝：每种被禁语句类型至少一例；多语句（含分号注入形态）；数据修改型 CTE；`SELECT INTO`；锁定子句；系统目录与 `information_schema`；未知表；其他 schema 限定；空输入与不可解析输入。
- 拒绝（函数）：解释 SQL 文本、读目录与服务器状态、有副作用、读文件与跨库、限定名调用、引号包裹的函数名、无参系统信息表达式、未知函数、对象标识与自定义类型转换。
- 拒绝（词法作用域）：内层 CTE 遮蔽物理表、同名 CTE 体内引用系统目录、非递归 CTE 前向引用与自引用、内层 CTE 名外泄。
- 边界：大小写与引号混用的对象名、注释包裹的语句、恰好达到与超过输入长度上限。

两条系统目录读取绕过路径的 SQL 集中在 `api/conftest.py`，同时供策略单元测试与 API 集成测试引用，两侧断言同一份输入。

资源限制（超时、行上限、截断标记）需真实数据库，归入集成测试，见 [query-runs-api.md](query-runs-api.md)。

## 延后决策

- `EXPLAIN` 支持与查询成本预估：出现真实需求时重议。
- 允许集扩容（如 `generate_series`、数组与 JSON 函数）：出现真实业务需求时逐个按收录标准评估。
