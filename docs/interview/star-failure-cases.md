# 三个 STAR 故障案例

> 以下都是仓库中的确定性故障注入或恢复验证，不是生产事故。回答时应明确这一点。

## STAR 1：进程中断后从 checkpoint 恢复

### S — Situation

TechScout 是异步长任务。即使是本地单进程，Web 刷新或进程退出也可能让 run 停在 `running`，如果只能整单重跑，会浪费已完成阶段并让 Trace 难以解释。

### T — Task

我要让重启后的任务能够重新入队，并在已有 checkpoint 的情况下继续执行，同时保留恢复事件，而不是把中断伪装成正常连续执行。

### A — Action

- 将 Web registry 与 Harness checkpoint 分开持久化。
- stage workspace 用临时文件、`fsync`、原子替换和 backup 保存阶段副产物。
- executor 启动时扫描 `queued/running` TechScout run，重新置为 `queued` 并追加 recovery 事件。
- RunEngine 检测到 workspace 后，按 run ID 调用 Harness 恢复。
- 用测试先执行到 planning checkpoint 后模拟中断，再启动 executor，断言任务最终进入合法终态且产物存在。

### R — Result

基线确定性测试证明本地 checkpoint 恢复路径可工作。PR #102 最终 head `595b506` 又通过独立复审验证 owner tuple fencing、duplicate ack、expired takeover 和 ambiguous-success 恢复；原始重放最终 `completed`，旧 owner 被拒绝。

### 追问诚实边界

如果进程死在 Trace seal 与 Registry 终态之间，仍有跨存储不一致窗口。PR #102 已用 owner tuple CAS 拒绝旧 owner 的 Registry 写入，但文件产物与 SQLite 仍没有跨资源事务；仍需 attempt 隔离、条件提升和 reconciliation。

## STAR 2：PoC 首次失败，只恢复失败阶段

### S — Situation

候选组件验证涉及检索、计划和 Docker PoC。PoC 的依赖冲突或临时执行失败不应该让整个研究从头开始，也不能无限重试到“看起来成功”。

### T — Task

实现类型化、可审计、最多一次的局部恢复，并保留第一次失败证据。

### A — Action

- Harness 将失败归一化为 stage、code、recoverable 与 recovery action。
- recovery policy 只允许登记的失败类型，并要求存在 checkpoint。
- 恢复时回到失败 stage，而不是重新执行所有已完成阶段。
- `poc-results.json` 保留失败与后续 attempt 历史；Trace 记录 error classified、recovery started/finished。
- 用确定性 fixture 注入首次 PoC 失败，断言恢复次数有界、历史未被覆盖、最终 gate 仍按事实决定。

### R — Result

测试证明单阶段恢复和证据保留合同成立。这里的“恢复成功”是 fixture 下的工程验证，不代表真实 Docker 环境或任意组件都能恢复，更不是模型效果指标。

### 追问诚实边界

已合并基线的恢复预算主要在 Harness 内。PR #102 的 Worker retry/DLQ、取消/超时优先和 duplicate delivery 已通过离线/确定性独立复审；真实 Redis 与多进程仍未验证。

## STAR 3：外部验证能力缺失时 fail closed

### S — Situation

Verified 路径依赖 live search、cache、GitHub 和受限 Docker。常见风险是把 provider key 缺失、网络不可达或 Docker 不可用，错误解释成“候选组件不兼容”，从而产出误导性推荐。

### T — Task

让基础设施不可用显式进入 provenance、issue 和 limitation，并保证没有足够权威时不发布虚假的兼容性结论。

### A — Action

- 将 research acquisition state 标准化为 live/cache/unavailable，并保留 source provenance。
- Docker PoC 只接受 reviewed recipe；缺 Docker、安装网络或 recipe 时返回 typed unavailable/research-only。
- deterministic gate 根据证据与 PoC 合同选择 completed with limitations、no safe winner 或 failed。
- Fast Demo 强制标记 synthetic，并与 verified service factory 分离。
- integration tests 注入 provider/cache/Docker 缺失，检查 API、报告和 Trace 中 limitation 保持可见。

### R — Result

系统在受控测试中不会用 synthetic 或缺失基础设施伪造 verified 成功。这个结果说明失败语义设计有效，不说明任何真实候选组件优劣，也不提供真实模型成功率。

### 追问诚实边界

当前仍是本地单用户产品，未实现外部依赖 SLO、熔断器面板或生产告警；这些属于生产化后续工作。
