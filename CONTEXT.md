# DecisionHarbor

面向企业内部业务人员的受治理数据分析平台。当前语境围绕首轮显式 SQL 的受控执行：用户在工作台提交 SQL，系统做策略判定、只读执行并保留审计。

## Language

### 查询生命周期

**查询运行（query run）**:
一次查询提交的完整生命周期，从系统收到 SQL 到落入终态。
_Avoid_: 查询任务、执行记录

**中间态（received / queued / running / cancelling）**:
received 指已受理未入队；queued 指已进入后台执行队列；running 指执行者已认领并执行；cancelling 指取消请求已受理、正在与执行结果竞争终态。中间态可被接管与取消，不是可观察的稳定状态。
_Avoid_: 把 received 与 queued 混称「排队」

**终态（succeeded / rejected / failed / cancelled）**:
succeeded 指策略通过且执行成功；rejected 指策略判定不通过；failed 指策略通过但执行出错；cancelled 指取消事实在终态发布前获胜。四者互斥且不可再转移。
_Avoid_: 把 rejected 与 failed 混称「失败」、把 cancelled 混入执行失败

**尝试（attempt）**:
单次运行内第几次实际执行，从 1 计数；执行者崩溃被接管后的重新执行与基础设施类失败（连接丢失、语句超时）触发的自动重跑同样递增，硬上界 3；耗尽后不再有任何执行路径。
_Avoid_: 重试次数（retry 是用户动作）

**自动重跑（automatic re-enqueue）**:
基础设施类失败（连接丢失、语句超时、执行者崩溃接管）在 attempt 未耗尽时由系统自动回队重跑的处置；数据库确定性错误不重跑、直接 failed 终态。
_Avoid_: 与重试关系（retry_of）混称——后者是用户动作

**重试关系（retry_of）**:
用户对 failed 或 cancelled 运行发起重试时，新运行指向原运行的关系；重试创建新运行（独立 attempt 预算），不复活原运行。
_Avoid_: 与自动重跑混称——后者是系统动作

**幂等键（idempotency key）**:
提交查询时可选携带的提交标识；数据库唯一索引保证一个键终身只绑定一条查询运行记录——同键同 SQL 重放返回原运行，同键异 SQL 构成冲突。
_Avoid_: 与运行 id 混称

**执行租约（lease）**:
执行者认领运行时写入的 ownership 凭据（worker_id + lease_expires_at），到期未续期即视为失去所有权，其他执行者可接管。
_Avoid_: 锁

**代（generation）**:
随认领/接管递增的运行版本号；终态发布与取消生效都携带它做 fencing，失去所有权的旧执行者因代过期无法发布结果。
_Avoid_: 版本号（泛指时）

**结果快照（result snapshot）**:
成功终态发布时与终态同事务写入的有限结果（列、行、行数、截断标记）；读取快照不重新执行 SQL。
_Avoid_: 结果缓存

**保留期（retention window）**:
结果快照自终态发布时间起保存 24 小时；过期后快照不可读，运行审计继续保留。
_Avoid_: TTL（泛指时）

**拒绝码（rejection code）**:
策略判定不通过时给出的稳定标识，回答「为什么不允许这样查」。
_Avoid_: 错误码

**错误码（error code）**:
执行侧失败的稳定标识（超时、容量耗尽、数据库不可达等），回答「为什么没跑成」。
_Avoid_: 拒绝码

**截断（truncated）**:
结果达到行数上限或字节上限（取数与快照统一约束，具体数值见 ADR-0011）时停止取数并显式标记的部分结果语义；截断结果不是完整结果。单行自身超过字节上限不属于截断，以稳定错误进入 failed 终态。
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
