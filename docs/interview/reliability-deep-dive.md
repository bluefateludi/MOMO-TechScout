# 可靠性 Deep Dive

## 一句话主线

我把可靠性拆成四个可验证的边界：**准入不超卖、执行可恢复、发布不撒谎、失败可追踪**；PR #102 又增加了 at-least-once Worker/lease 层，但我会区分确定性 owner fencing 与真实 Redis/多进程集成。

## 1. 准入不超卖（已实现）

`RunRegistry` 在 `BEGIN IMMEDIATE` 事务里统计 `queued/running` 数量、插入 run、追加 accepted 事件。并发测试证明容量检查发生在同一写锁边界内。claim 同样使用事务和 `WHERE status='queued'` 条件更新；而且只要库里有一个 TechScout `running`，就不会 claim 第二个。

PR #102 让 idempotency、deadline、attempt、cancel intent 与完整 owner tuple 进入 Registry。最终 head `595b506` 已用确定性复审关闭 deadline、取消与旧 Worker 终态的原阻断反例；该结论不外推到真实 Redis 或多进程环境。

## 2. 执行可恢复（已实现但有限）

Web 启动时把活跃 TechScout run 重新置为 queued。引擎把 Harness checkpoint 放在独立 SQLite 文件，把阶段副产物放在 workspace；workspace 采用 tmp + fsync + replace，并保留 backup。再次执行时，如果 workspace 存在，就让 Harness 按 run ID 从 checkpoint 恢复。

面试边界：这是 checkpoint-based resume，不是 exactly-once。进程可能在“产物已写、registry 尚未终态”的窗口退出，当前靠重排和保留 interrupted Trace 暴露问题，还没有跨存储事务或自动对账器。

## 3. 发布不撒谎（已实现）

Harness 有确定性 gate 和有界恢复。外部搜索、缓存、Docker 或 recipe 不可用时，系统产生 limitation、`no_safe_winner` 或 failed，而不是把基础设施缺失推断为组件不兼容。Fast Demo 在 API 中标记 `synthetic=true`。

成功路径先生成投影/manifest 等产物，再记录 terminal Trace 并 seal，最后在 SQLite 同一事务写终态状态、projection path 和终态事件。异常路径尽量发布失败投影；终态发布本身失败时，还有最后的 queue-release 兜底。

面试边界：文件系统与 SQLite 不是同一事务，因此只能说“有意安排提交顺序并提供恢复证据”，不能说“原子 exactly-once 发布”。

## 4. 失败可追踪（已实现）

系统有两层可观测性：registry 中的游标分页事件用于 Web 进度，run 目录中的 sealed JSONL Trace 用于执行/provenance。事件文本写入前会做脱敏、控制字符清理和长度限制；未知 API 异常只返回 correlation ID。

PR #102 增加了校验/生成的 `X-Request-ID`、request/run/worker context 和结构化脱敏日志。面试边界：仍没有 Prometheus SLO、集中日志平台或告警路由。

## 5. Worker、lease 与 fencing 的真实状态

单进程重启时，可以直接假设旧线程消失；多 Worker 下这个假设不成立。Worker 可能只是网络分区或长暂停，lease 到期后新 Worker 会接管，而旧 Worker 随后恢复。如果只有分布式锁/TTL，旧 Worker 仍可能迟到写终态。

PR #102 初版已落地：

- 默认 InMemory queue + embedded Worker，以及可选 Redis adapter + 独立 Worker CLI；
- reserve/lease/heartbeat/ack/retry/reaper/DLQ 的 queue-level token 校验；
- SQLite 条件 claim、一次有界 transient retry、deadline 与合作式取消；
- readiness、request ID 和结构化脱敏日志。

最终验收结论：`595b506` 上六个原阻断均关闭，Standards/Spec Pass、0 阻断；ambiguous-success 原始重放 completed，旧 owner fenced。准确边界仍是离线/确定性复审：真实 Redis Lua/响应丢失、真实多进程竞争、OS SIGKILL 未验证，阻塞外部 I/O 线程只能 fencing/handoff、不能硬终止。

## 6. 可能被追问的取舍

### 为什么当前用 SQLite？

目标是本地单用户垂直切片。SQLite WAL 提供了足够清晰的事务、低运维成本和确定性测试；先验证任务合同和恢复边界，比过早引入分布式组件更合适。

### 为什么 Redis 不能成为唯一事实源？

队列和 lease 是短期协调状态；报告、manifest、Trace 和最终 run 投影需要更稳定的持久化、审计与对账。Redis 丢失后应该能从持久事实源重建调度态。

### 最大的当前技术债是什么？

跨文件系统与 Registry 的发布窗口、真实 Redis/多进程集成缺失，以及阻塞外部 I/O 线程无法硬终止。owner tuple fencing 已通过确定性复审；`executor_unavailable` 消息映射由安全 fallback 兜底。
