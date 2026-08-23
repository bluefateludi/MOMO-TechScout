# TechScout 后端可靠性文档

> 已合并事实基线：`750b17a7a2bf3217793c70e4fc065f1728288743`
>
> 未合并实现权威：Draft PR #102，分支 `codex/techscout-backend-reliability-v1`，精确 head `595b506501b1b893b33845e50b17fc06ae267e75`
>
> 本目录只描述工程实现、演进协议与运维验证。面试表达位于 [`../interview/`](../interview/README.md)。

## 状态词汇

- **已实现（基线）**：`750b17a` 代码路径中存在，并有代码或测试可核对。
- **已实现并通过独立复审（PR #102，未合并）**：代码和离线/确定性规格均已验收；尚未进入 `master`、未上线，也不代表真实 Redis 或生产环境已验证。
- **本轮计划**：仍停留在协议或后续验证阶段，PR #102 尚未完整实现。
- **未实现**：既不在基线代码中，也不应被表述为已上线能力。

本文中的“生产”表示面向生产化的设计与运维要求，不表示系统已经部署到生产环境。

## 阅读顺序

1. [后端可靠性架构](architecture.md)
2. [错误协议](error-protocol.md)
3. [任务生命周期](task-lifecycle.md)
4. [Redis Worker 与 lease 协议](redis-worker-lease.md)
5. [运维 Runbook](runbook.md)
6. [故障注入计划](fault-injection-plan.md)

## 当前结论

| 能力 | 状态 | 可核验事实 |
|---|---|---|
| API、队列投影与事件 | 已实现 | FastAPI + SQLite WAL；run 状态与 append-only 事件持久化 |
| TechScout 执行 | 已实现 | 单进程内一个 daemon thread；一次最多执行一个 TechScout run |
| 重启恢复 | 已实现但有限 | 启动时重排活跃任务；存在 stage workspace 时从 SQLite checkpoint 恢复 |
| 终态发布 | 已实现但有限 | 先生成投影/产物并封存 Trace，再在 SQLite 事务写终态状态与事件 |
| 默认本地队列 | PR #102 已通过独立复审 | InMemory dispatch + 嵌入式 Worker；重复 delivery 回归已关闭 |
| Redis 队列适配器 | PR #102 已通过离线复审 | dispatch/lease/heartbeat/rate limit/reaper/DLQ 与 Worker CLI；未运行真实 Redis server |
| 幂等准入/日志 | PR #102 已通过独立复审 | 幂等、request context 与 secret canary 均有确定性证据 |
| fencing/取消/deadline/错误终态 | PR #102 已通过独立复审 | 六个原阻断反例均关闭，0 阻断 |
| 健康检查 | PR #102 已通过独立复审 | live queue readiness 每次探测并受 queue failure supervision 约束 |
| Registry fencing | PR #102 已通过独立复审 | owner/lease/fencing tuple CAS；ambiguous-success 重放完成且旧 owner 被拒绝 |
| 多机部署 | 未实现 | 无真实 Redis、多机或共享存储验证 |
| 多租户 | 未实现 | Web 仍默认单 Uvicorn worker、loopback、无认证 |
| 真实模型效果 | 不在范围 | 本目录不记录或推断模型质量、成功率、成本改善等结论 |

最终独立验收基于精确 head `595b506`：Standards 与 Spec 均 Pass、0 阻断；聚焦可靠性 `19 passed`、Web `82 passed`、TechScout `125 passed / 2 skipped`，secret canary 通过。ambiguous-success 原始重放恢复为 `completed`，旧 owner 被 fencing 拒绝。Ruff、OpenAPI/TypeScript 合同和 Web build 通过。
