# 常见后端追问与诚实边界

## 架构与队列

### Q：现在到底有没有 Worker？

已合并基线有进程内 daemon worker thread。Draft PR #102 精确 head `595b506` 已实现 `TechScoutWorker`/`RunQueue` 并通过离线/确定性独立复审，0 阻断；PR 未合并，真实 Redis server 未集成运行。

### Q：为什么不用 Celery/Redis？

当前仍不用 Celery。PR #102 实现了项目内 RunQueue/Worker 和可选 Redis adapter，让 SQLite Registry 继续拥有权威状态，Redis 只做 dispatch/lease。这样可以把任务语义留在本项目中；代价是真实 Redis、多进程竞争和运维生态还需继续验证。

### Q：SQLite claim 会不会并发重复？

Registry 使用 worker/lease/fencing tuple CAS 和 absolute lease expiry。确定性复审覆盖 healthy duplicate ack、expired takeover、旧 owner 拒绝；真实 Redis/多进程竞争仍未验证。

### Q：是否 exactly once？

不是。PR #102 明确是 at-least-once delivery。Redis lease token 保护队列 mutation，Registry owner tuple CAS 抑制重复投递和旧 owner 写入；但文件产物和 SQLite 终态没有跨资源事务，仍不能宣称 exactly-once execution。

## 恢复与一致性

### Q：重启怎么恢复？

executor 启动时把活跃 TechScout run 重新入队。RunEngine 发现 stage workspace 后按 run ID 从 Harness SQLite checkpoint 恢复。测试覆盖了 planning checkpoint 后中断再恢复。

### Q：如果在写完产物、更新数据库前崩溃？

这是明确的当前窗口。现有顺序会保留产物/中断 Trace并重新入队，但没有自动 reconciliation。生产化计划是 attempt 隔离、manifest digest、fencing 条件提交和对账器。

### Q：checkpoint 会不会损坏？

stage workspace 有 tmp + fsync + replace 和一个 backup；Harness checkpoint 是 SQLite。仍不能保证所有磁盘故障都可恢复，故障注入计划包含主 workspace 损坏、磁盘满和 SQLite 锁场景。

### Q：如何避免无限重试？

Harness 的业务阶段恢复类型化且有界。PR #102 又实现 Worker 层最多一次 transient retry，永久/超预算错误进入 `dead_letter`。延迟指数退避/full jitter 和真实 Redis retry 集成尚未实现。

## API 与错误

### Q：错误协议是什么？

当前 envelope 是 `error.code/message/details`。Pydantic 校验映射为 422；未知异常只返回 correlation ID。事件文本做脱敏和长度限制。

### Q：协议有什么已知问题？

基线的 `executor_unavailable` 消息表缺口已在 PR #102 修复；未知 code 也安全 fallback 到 `internal_error`。PR 尚未合并，所以谈当前 `master` 时仍要区分这个差异。

### Q：客户端什么时候可以重试？

Worker 按错误类型和 attempt/deadline 有界 retry；取消/超时优先、DLQ 和 duplicate delivery 已通过确定性复审。真实网络不确定性仍需集成验证。

## Redis 与 lease（PR #102 已通过离线/确定性复审）

### Q：为什么 lease 还要 fencing token？

lease 只能说明 ownership 可能过期，不能让旧 Worker 忘记自己。PR #102 同时用 Redis lease token 约束队列 mutation，并用 Registry worker/lease/fencing tuple CAS 拒绝旧 owner 的 heartbeat、progress、failure 与 terminal；真实 Redis 和多进程竞态仍未集成验证。

### Q：heartbeat 超时怎么办？

lease lost 后 Registry owner tuple CAS 阻止旧线程发布；该语义已通过确定性复审。但 Python 仍不能安全硬杀阻塞的外部 I/O 线程，shutdown 只能在 grace 超时后 handoff 并暴露 limitation。

### Q：Redis 挂了，任务会不会丢？

SQLite Registry、manifest、Trace 和产物仍是事实源，Redis 只存调度态。但 PR #102 没有实现从 Registry 自动重建 Redis 队列的 reconciliation，也未做 Redis 重启演练，因此不能承诺 Redis 故障后自动无损恢复。

### Q：为什么不把报告也放 Redis？

报告和 Trace 体积大、需要长期审计与摘要校验；Redis 更适合短期协调。把它作为唯一事实源会把队列故障扩大成业务数据丢失。

## 安全、运维与规模

### Q：现在能公网部署吗？

不应直接公网部署。当前默认 loopback、单用户、无认证；虽然有同源校验、CSP、请求大小限制和输出脱敏，但没有多租户授权。

### Q：有生产 SLO 吗？

没有。仓库有确定性工程测试和规划目标，但没有生产流量、on-call 或 SLO 证据。我会先实现指标和故障注入，再用部署数据设阈值。

### Q：扩容瓶颈在哪里？

PR #102 支持 API/Worker 进程拆分，但 SQLite 单写、本地 checkpoint/产物、缺少跨资源事务和真实 Redis 验证仍是瓶颈。多机扩容前需要共享持久存储和对账，而不只是增加 Worker 数。

### Q：三个 STAR 是真实事故吗？

不是。它们是确定性故障注入/恢复测试：进程中断、PoC 首次失败、外部能力缺失。它们证明工程合同在受控条件下成立，不提供生产事故影响、MTTR 或真实模型效果。

## 一张诚实边界表

| 可以说 | 不能说 |
|---|---|
| “SQLite 事务化准入和 claim 已实现” | “已经是分布式队列” |
| “checkpoint 中断恢复有确定性测试” | “任务 exactly once” |
| “sealed Trace 与 limitation 可见” | “有完整生产 observability/SLO” |
| “PR #102 已通过离线/确定性独立复审，0 阻断” | “Redis Worker 已上线或完成真实 Redis 集成” |
| “Registry owner tuple fencing 已通过确定性复审” | “真实 Redis/多进程/文件产物跨资源一致性已验证” |
| “Fast Demo 走真实 orchestration seam” | “Fast Demo 是 live 或证明模型效果” |
| “故障案例来自注入测试” | “这是线上事故复盘” |
