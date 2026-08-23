# 任务生命周期

## 1. 权威层次

`750b17a` 是已合并基线。Draft PR #102 精确 head `595b506` 已实现扩展生命周期，并通过离线/确定性独立复审；PR 尚未合并或上线。

## 2. PR #102 已验收的公开状态

```text
queued -> running -> completed
                  -> completed_with_limitations
                  -> cancelled
                  -> failed
                  -> dead_letter
running -> queued       (一次有界 transient retry)
running -> interrupted  (Worker 中断投影)
```

阶段投影仍为 `plan -> research -> verify -> decide -> terminal`。Redis/InMemory 队列只负责 dispatch；SQLite Registry 保存权威状态。

## 3. 转移合同与验收结果

| 转移 | 初版意图 | 当前验收状态 |
|---|---|---|
| 请求 -> `queued` | API / Registry | capacity、idempotency、request hash、deadline、run 与 accepted event 同一 SQLite 事务 |
| `queued` -> `running` | 条件 claim、worker/lease/fence、attempt +1 | duplicate ack 与 expired lease takeover 已通过确定性复审 |
| queued/running 取消 | 立即/合作式取消 | cancel 优先于 success/failure 已通过回归 |
| deadline | claim/完成前拒绝过期任务 | `timed_out` 优先级已通过回归 |
| retry/DLQ | transient 有界重排，永久/耗尽进 DLQ | 取消/超时不误入 DLQ已通过回归 |
| Worker 终态 | 当前 owner 提交成功/受限/失败 | owner tuple CAS 与旧 owner fencing 已通过复审 |

成功发布仍是：Harness 完成 → 写投影/产物 → seal Trace → Registry 终态。文件系统与 SQLite 没有跨资源事务，因此不是 exactly-once 提交。

## 4. 幂等、deadline、取消与重试的当前状态

- `Idempotency-Key` 可选；相同 key + 相同规范化请求返回原 run，不重复入队。
- 同一 key 对应不同 request hash 返回 `409 idempotency_conflict`。
- Fast/Verified deadline、queued/running 取消、`max_attempts=2` 和错误分类代码均已落地。
- deadline、取消与 DLQ 的原阻断反例已在 `595b506` 关闭并通过独立复审。
- 原始异常消息不持久化到公开错误；保存归一化 `error_kind` / `error_code`。

## 5. 队列 delivery 与 lease

- PR #102 明确使用 at-least-once delivery。
- InMemory/Redis adapter 都有 reserve、lease、heartbeat、ack、retry、reaper 和 dead-letter 合同。
- Redis Lua 对队列操作校验 lease token；Registry 条件 claim 拒绝对非 queued run 的重复 claim。
- Registry 额外持久化 lease expiry；ambiguous reaper success 可在 expiry 后原子 takeover，旧 owner 无法提交。
- 未运行真实 Redis server，因此 Lua、API/Worker 多进程和网络故障行为尚无集成权威。
- Registry progress/terminal 已受 owner tuple CAS 保护；文件产物与 SQLite 终态仍没有跨资源事务、attempt 隔离或 reconciliation。

## 6. 未实现或待验证

- 真实 Redis server Lua/网络响应丢失、真实多进程 lease/heartbeat 竞争。
- OS SIGKILL 和阻塞外部 I/O 线程硬终止；只能 fencing/handoff。
- 延迟指数退避、DLQ 查看/重放、任务优先级与公平调度。
- attempt 隔离产物、outbox/reconciliation 和跨存储对账。
- 多机共享 checkpoint/产物存储、任务保留/删除与合规协议。
- 真实模型与 Live Eval 未验证。
