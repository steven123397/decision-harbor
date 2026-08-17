# DecisionHarbor

面向企业内部业务人员的受治理数据分析平台。当前语境围绕首轮显式 SQL 的受控执行：用户在工作台提交 SQL，系统做策略判定、只读执行并保留审计。

## Language

### 查询生命周期

**查询运行（query run）**:
一次查询提交的完整生命周期，从系统收到 SQL 到落入终态。
_Avoid_: 查询任务、执行记录

**终态（succeeded / rejected / failed）**:
succeeded 指策略通过且执行成功；rejected 指策略判定不通过；failed 指策略通过但执行出错。三者互斥且不可再转移。
_Avoid_: 把 rejected 与 failed 混称「失败」

**拒绝码（rejection code）**:
策略判定不通过时给出的稳定标识，回答「为什么不允许这样查」。
_Avoid_: 错误码

**错误码（error code）**:
执行侧失败的稳定标识（超时、容量耗尽、数据库不可达等），回答「为什么没跑成」。
_Avoid_: 拒绝码

**截断（truncated）**:
结果达到行数上限时停止取数并显式标记的部分结果语义；截断结果不是完整结果。
_Avoid_: 限流、部分失败

### 治理与执行

**治理（governance）**:
「策略判定 + 数据库只读身份」两道边界对用户 SQL 的合计约束。
_Avoid_: 权限、RBAC

**策略判定（policy decision）**:
基于 SQL AST 与对象访问范围判定输入是否允许的纯函数过程；不访问数据库、不改写 SQL。
_Avoid_: 校验、过滤

**只读执行器（read-only executor）**:
以只读数据库身份在 analytics 库执行通过策略判定的 SQL 的 API 内部模块。
_Avoid_: 查询引擎

### 数据与边界

**platform 库**:
保存查询审计等产品状态的应用数据库，只允许平台可写身份访问。

**analytics 库**:
承载固定销售分析数据的业务数据库；用户 SQL 只在此库以只读身份执行。

**引导（bootstrap）**:
建库、建角色、迁移与固定数据加载的启动过程，由一次性 init 容器完成。
_Avoid_: 初始化（泛指时）

**数据契约（contract）**:
`datasets/sales-analytics-v1/contract.json`：定义五张分析表、字段与业务口径的机器可读单一事实源；DDL、授权表清单与业务约束都从它派生。
_Avoid_: schema、数据字典
