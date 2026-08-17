# seed 幂等：标记放 platform，不一致即失败，单事务加载

数据集标识（dataset + version + seed，取自 manifest）写入 `platform` 库的 `dataset_markers`：`analytics` 内只保留契约五张表，业务库不携带实现元数据，只读身份也无从篡改标记。启动时三分支：表不存在则建 DDL 并单事务加载全部数据；标记匹配且五表行数等于 `expected_counts` 则跳过；其他不一致直接启动失败——truncate + reload 是破坏性动作，显式失败暴露问题（如契约换了版本或数据被改动），处理方式由人决定。

## 后果

- 标记写入前的进程崩溃会留下「数据已载、标记为空」状态，当前按设计 fail-hard（提示人工清卷）；行数全等时的精确自愈是后续改进。
- 数据集新版本目前同样 fail-hard，自动升级路径留给后续阶段（见 issue tracker）。
