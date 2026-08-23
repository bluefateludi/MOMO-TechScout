# TechScout 运维 Runbook

## 1. 发布状态

默认本地模式属于已合并基线。Redis/独立 Worker 在 Draft PR #102 精确 head `595b506` 已通过离线/确定性独立复审；PR 尚未合并或上线。以下 Redis 命令仍是本地/隔离运行手册，不是生产部署证明。

## 2. 默认本地模式（已实现）

```console
techscout serve
```

- 默认 `127.0.0.1:8000`、Uvicorn 单 worker、无认证。
- PR #102 默认组合使用 InMemory queue + 嵌入式 TechScout Worker，SQLite Registry 保存权威状态。
- `GET /health/live` 检查进程存活；`GET /health/ready` 检查 Registry、executor 和 queue readiness。
- Fast Demo 必须保持 `synthetic` 标识，不能描述为 live 或真实模型效果。

## 3. Redis/API-Worker 拆分（PR #102 已通过离线复审）

候选命令：

```console
techscout serve --redis-url redis://127.0.0.1:6379/0 --queue-capacity 100
```

```console
techscout-worker --redis-url redis://127.0.0.1:6379/0 --state-root outputs/.web --output-root outputs --queue-capacity 100
```

边界：

- API Redis 模式不启动嵌入式 Worker且不静默回退 InMemory；readiness 每次探测 queue，并受 executor/thread supervision 约束。
- API 与 Worker 必须指向同一个 Registry、output root 和 Redis namespace。
- 当前只做过 Worker CLI smoke；在真实 Redis server 上运行前必须完成隔离集成测试。
- Redis URL 不应出现在日志或版本库；Redis 不得暴露公网。

## 4. 常见故障处置

### `queue_full` / `rate_limited`

- Registry capacity 与 queue capacity 都会形成背压；`rate_limited` 来自按 subject 的 sliding window。
- 保存 request ID、run ID、Registry 状态和 queue readiness；不要靠重复 POST 掩盖问题。
- 使用 `Idempotency-Key` 安全重放同一业务提交；不同请求不得复用同一 key。

### 任务长期 `running`

- 查看 worker ID、attempt、deadline、cancel intent、stage workspace、checkpoint、Trace 和结构化日志。
- InMemory 重启路径可按 transient interruption 有界重排。
- Redis lease/reaper 尚未做真实 server/进程崩溃集成；确定性 ambiguous-success 重放已恢复 completed，旧 owner 被 fenced。

### `dead_letter`

- 检查 Registry 的归一化 `error_kind/error_code`、attempt/deadline 与 sealed Trace。
- 不在 Redis 中寻找原始异常；设计上只保存安全 reason。
- PR #102 未提供 DLQ 管理/重放 CLI。禁止手改 Redis/SQLite 后宣称原 run 成功。

### Redis not ready

- `/health/live` 可以仍为 200；`/health/ready` 每次检查 queue 与 worker/dispatcher thread。真实 Redis 网络行为仍需集成验证。
- 停止准入新任务，保存 Redis/Registry/Worker 证据；不要切换到 InMemory 造成两个 dispatch 域。
- 当前没有从 Registry 自动重建 Redis 队列的 reconciliation job。

### artifact/Registry 不一致

- 停止把该 run 当作权威结果，保存 manifest、文件摘要、Registry/WAL 和 Trace。
- 不就地修补产物。新建 run 重试并保留旧失败证据。
- 当前没有 outbox/reconciliation 或 attempt/fencing 产物提升。

## 5. 证据采集清单

- 精确 Git commit、PR 状态、启动参数、Python/OS/Redis 版本。
- request ID、run ID、worker ID、状态/阶段、attempt、deadline、cancel intent。
- Registry DB/WAL/SHM 的一致性备份；run 目录文件名、大小和 SHA-256。
- sealed/interrupted/aborted Trace 与 manifest；脱敏日志窗口。
- Redis namespace 的有界元数据快照，不包含 URL credential 或业务正文。

## 6. 尚未交付的生产运维能力

- 真实 Redis server 集成演练、Cluster/Sentinel、TLS/ACL、备份恢复和容量验证。
- Prometheus 指标、Dashboard、告警、on-call、SLO 和 RPO/RTO。
- Worker drain 管理面、DLQ 查看/重放和跨存储 reconciliation；Registry stale-owner fencing 已有确定性证据。
- 多机共享存储、跨机故障转移和安全认证/租户隔离。
- 阻塞外部 I/O 线程的硬终止；PR #102 只能在 grace 超时后 fencing/handoff 并报告 limitation。
