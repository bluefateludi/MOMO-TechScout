# 故障注入计划

> 状态：Draft PR #102 精确 head `595b506` 已通过离线/确定性独立验收，0 阻断；真实 Redis server 和生产演练仍未执行。

## 1. 原则

- 默认在隔离环境、fake adapter 或受控容器内注入，不触碰真实第三方服务。
- 每次实验固定 seed、输入与预期不变量，保留失败前后的 Trace、checkpoint、事件和产物摘要。
- 验证的是可靠性机制，不记录或推断真实模型效果。
- 先验证单故障，再组合故障；设置总时限和停止条件，避免无限重试。
- 注入工具不得包含 secret，不得对生产数据执行破坏性操作。

## 2. 已实现的确定性覆盖

| 场景 | 已有证据 | 当前验证点 |
|---|---|---|
| 并发准入超过容量 | `tests/web/test_registry.py` | 事务化容量检查，只接受有界数量，其余 `queue_full` |
| 终态发布异常后的队列释放 | `tests/web/test_registry.py` | stuck running 可兜底转为 failed，不永久阻塞下一任务 |
| Web 刷新/重启后的 checkpoint 恢复 | `tests/web/test_techscout_wave2_e2e.py` | 活跃 run 重排，Harness 从 checkpoint/workspace 继续 |
| PoC 首次失败后单阶段恢复 | Wave 2 E2E / recovery tests | 有界恢复、失败历史与 recovery Trace 可见 |
| live/cache/Docker 缺失的受限结果 | verified integration tests | 显式 limitation/no-safe-winner，而非伪造成功 |
| 幂等 key 重放/冲突 | PR #102 `test_backend_reliability.py` / API test | 相同请求返回同一 run，不同摘要返回 typed conflict |
| Registry claim/cancel/终态竞争 | PR #102 reliability tests | 条件 claim、cancel intent 和非法终态回退受事务保护 |
| InMemory lease/heartbeat/reaper/DLQ | PR #102 reliability tests | capacity、rate limit、续租、过期重排和 DLQ reason |
| transient 一次重试与 permanent DLQ | PR #102 Worker tests | attempt 有界、归一化错误、不泄露原异常 |
| request/run/worker 日志脱敏 | PR #102 logging test | canary token 和 Authorization 值不可见 |
| deadline 在 claim 前过期 | PR #102 Registry test | 直接进入 typed failed，不执行 processor |

这些是测试或故障注入案例，不是线上事故和生产 SLO 证据。

## 3. 独立审查复现并已关闭的阻断缺陷

| ID | 原缺陷 | 最终结论 |
|---|---|---|
| B1 | lease 过期后旧 Worker 仍可完成 run | owner tuple CAS + lease-expiry takeover；旧 owner fenced，Pass |
| B2 | running 取消可被成功终态覆盖 | terminal transaction 优先取消，Pass |
| B3 | deadline 过期后仍可成功 | terminal transaction 投影 `timed_out`，Pass |
| B4 | 取消相关异常进入 DLQ | cancelled ack、不进 DLQ，Pass |
| B5 | Redis readiness 陈旧 | 每次探测 + queue/thread supervision，Pass |
| B6 | 重复 delivery 活锁 | healthy duplicate ack；ambiguous expiry 可 takeover，Pass |

最终 head `595b506` 的独立验收：聚焦 `19 passed`、Web `82 passed`、TechScout `125 passed / 2 skipped`，secret canary 通过，Standards/Spec 均 Pass、0 阻断。ambiguous-success 原始重放恢复 `completed`，旧 owner 被 fencing。

## 4. 后续单进程矩阵

| ID | 注入点 | 注入方式 | 期望不变量 |
|---|---|---|---|
| L1 | claim 后、首个 checkpoint 前杀进程 | 子进程硬退出 | 重启后 run 可重排；不出现两个本地 running owner |
| L2 | checkpoint 后、产物发布前杀进程 | stage hook | 从最近 checkpoint 恢复；已完成阶段不被无限重复 |
| L3 | 产物已写、Trace seal 前杀进程 | file hook | 旧 Trace 以 interrupted/aborted 形式保留；新 Trace 可验证 |
| L4 | Trace seal 后、registry 终态前杀进程 | commit hook | 重启后不把不一致 run 静默当成功；对账需求被暴露 |
| L5 | SQLite `database is locked` | fake connection/受控锁 | 失败有界、无 busy loop；API 不泄露 SQL/路径 |
| L6 | 输出卷只读/磁盘满 | 隔离临时卷 | 进入安全 failed 或明确不可用；不生成半真半假的成功投影 |
| L7 | stage workspace 主文件损坏 | 改坏临时 fixture | 能读 backup 或明确失败；不忽略校验错误 |
| L8 | 终态发布连续失败 | fake publisher | `fail_stuck_techscout` 尽力释放队列，故障日志可关联 |

## 5. 尚需真实 Redis server 验证的矩阵

以下场景尚未在真实 Redis server 上运行；当前 Pass 只来自确定性 adapter/重放：

| ID | 注入点 | 期望不变量 |
|---|---|---|
| R1 | Worker claim 后崩溃 | lease 到期后仅一个新 token 被 claim；旧 token 无权提交 |
| R2 | Worker 暂停超过 TTL 后恢复 | 在真实进程/网络下验证 Registry fencing 与 takeover |
| R3 | heartbeat 请求超时但 Redis 已成功执行 | Worker 先查 ownership；不盲目扩 lease 或重复副作用 |
| R4 | Redis 在 claim Lua 前后故障 | claim 要么完全发生、要么完全不发生，无半状态 |
| R5 | complete 返回超时 | 使用 run/token 查询幂等判断，不创建重复终态事件 |
| R6 | reaper 与 heartbeat 竞争 | 原子条件保证只有 expiry/owner/token 匹配才回收 |
| R7 | 可重试依赖错误连续发生 | 当前一次有界立即重试后进入 DLQ；延迟退避另行验证 |
| R8 | Redis 数据丢失/重启 | 从持久 run store 重建非终态项；终态产物不由 Redis 丢失 |
| R9 | 两个 API 进程使用同一 idempotency key | SQLite 权威确保相同 digest 同一 run、不同 digest 冲突 |
| R10 | DLQ 重放 | 保留原失败，生成新 attempt/token，可审计且不会覆盖旧证据 |

## 6. 通过标准

每个场景必须自动断言：

- run 状态与事件序列合法，没有从终态回到 running。
- queue mutation 只有当前 lease token 有效，Registry 写入只有当前 owner tuple 有效；文件产物跨资源条件提升仍需单独验证。
- 尝试次数、恢复次数和 lease loss 可从结构化事件还原。
- 终态引用的 manifest/产物摘要可验证；失败时不发布推荐性结果。
- 日志、错误响应、事件和 Trace 不含 canary secret。
- 测试在硬时限内终止，不依赖真实网络或真实模型效果。

## 7. 未实现的交付物

- fault hooks/代理、Redis 测试容器与虚拟时钟。
- chaos 场景自动化、CI 隔离 job 和产物归档格式。
- 对 L4 跨存储窗口的 reconciliation 实现与合同测试。
- 文件产物的 attempt 隔离/条件提升、跨资源 reconciliation，以及 lease 丢失后 processor cancellation。
- 生产演练审批、回滚方案、观察窗口和 SLO 阈值。
- 外部 I/O 线程无法安全硬终止的边界验证；只能断言 fencing 后无法再发布状态。
- 真实 Redis Lua/响应丢失、真实多进程竞争、OS SIGKILL、真实模型与 Live Eval。
