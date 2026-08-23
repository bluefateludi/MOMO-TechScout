# Redis Worker 与 lease 协议

> 状态：Redis adapter 与 Worker 在 Draft PR #102 精确 head `595b506` 已实现并通过离线/确定性独立复审；PR 尚未合并/上线，且没有运行真实 Redis server 集成。

## 1. 已实现的责任边界

- SQLite WAL `RunRegistry` 是 run 状态、attempt、deadline、取消、错误和事件的唯一权威。
- Redis 只保存 dispatch/processing、lease token/expiry、worker、rate limit 和 dead-letter routing 数据。
- 默认 `techscout serve` 使用 `InMemoryRunQueue` 和嵌入式 Worker。
- `techscout serve --redis-url ...` 使用 Redis dispatch 并关闭嵌入式 Worker；一个或多个 `techscout-worker` 进程消费同一 namespace。
- Worker delivery 是 at least once，不宣称 exactly once。

## 2. PR #102 的 Redis 数据结构

默认 namespace 是 `momo:techscout`：

| 后缀 | 类型 | 用途 |
|---|---|---|
| `pending` | list | 待投递 run ID |
| `processing` | list | 已 reserve 的 run ID |
| `known` | set | queue capacity 与重复 enqueue 去重 |
| `tokens` | hash | run ID -> 随机 lease token |
| `workers` | hash | run ID -> worker ID |
| `leases` | sorted set | run ID -> expiry 毫秒时间戳 |
| `dead` | list | dead-letter run ID |
| `dead_reasons` | hash | run ID -> 归一化 error code |
| `rate:<subject>` | sorted set | sliding-window admission rate limit |

请求正文、原始异常、报告、Trace、credential 和大产物不存入 Redis。

## 3. 初版已落地的原子队列操作

`RedisRunQueue` 通过 Lua `EVAL` 实现：

- `enqueue`：known 去重、capacity 检查并写 pending。
- `reserve`：pending -> processing，写 token/worker/expiry。
- `heartbeat`：仅 token 匹配时延长 expiry。
- `ack` / `retry`：仅 token 匹配时完成或重新入队。
- `dead_letter`：仅 token 匹配时移出活动集合并记录归一化 reason。
- `reap_expired`：仅 expiry 仍过期时移除 owner/token 并返回 pending。
- `allow`：按 subject 做 sliding-window rate limit。
- `ready`：Redis `PING` 失败时返回 not ready；不静默回退 InMemory。

最终独立复审覆盖 capacity、rate limit、heartbeat、reaper、DLQ、Worker retry、live readiness、duplicate delivery、queue failure supervision 和 secret canary。Redis Lua 代码已进入实现，但未针对真实 Redis server 执行集成测试。

## 4. Worker 合同

- 默认 lease 30 秒、heartbeat 10 秒；构造时强制 heartbeat 小于 lease。
- reserve 后先在 Registry 条件 claim；重复投递若 run 已终态则 ack，若仍 running 则 retry。
- transient 错误在 attempt/deadline/cancel 允许时最多重试一次。
- 永久或耗尽错误进入 Registry `dead_letter` 并路由到队列 DLQ。
- running 取消是合作式；终态事务保证取消优先于 success/failure，取消异常不会进入 DLQ。
- SIGINT/SIGTERM 停止入口循环，executor 等待当前有界任务结束。
- 日志带 request/run/worker 上下文，并清理常见 token、authorization 和 credential 值。

## 5. Fencing 与 ambiguous-success 恢复

Registry 保存 worker ID、lease token、absolute lease expiry 与单调 fencing token。heartbeat、progress、failure 和 terminal 都使用完整 owner tuple CAS；旧 owner 的迟到写被拒绝。

若 Redis 已提交 expired lease requeue 但响应丢失，新 delivery 只能在 Registry `lease_expires_at <= now` 时原子 takeover，并递增 fencing。最终验收的 ambiguous-success 原始重放恢复为 `completed`，旧 owner 被 fencing 拒绝。

外部 I/O 线程仍无法被 Python 安全硬终止。shutdown 只在有限 grace 后 handoff lease 并阻止旧线程发布状态，同时暴露 `active_external_io_not_terminated` limitation；OS SIGKILL 也未做真实演练。

## 6. 未实现或未验证

- 真实 Redis server 下的 Lua 原子性、网络响应丢失、连接中断和 Redis 重启验证。
- Redis Cluster/Sentinel、TLS/ACL、持久化、备份与容量测试。
- 真实 API/Worker 多进程竞争和 OS SIGKILL；外部 I/O 线程不能硬终止。
- Redis 调度态从 Registry 重建的 reconciliation job。
- 延迟/full-jitter retry、DLQ 管理/重放 CLI、公平优先级和租户配额。
- 多机共享 SQLite/checkpoint/产物存储；真实模型与 Live Eval。
