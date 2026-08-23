# 90 秒项目介绍

## 可直接使用的版本

“MOMO TechScout 是一个帮助 Python AI 应用开发者选择开源组件的证据驱动研究与验证 Agent。用户给出环境、硬约束和候选组件后，系统会经过有界的规划、检索、PoC 验证和确定性 gate，输出可追溯的对比报告，而不是让模型在开放循环里自由决定是否发布结论。

我这部分重点解决的是本地 Web 后端的可靠执行。**已经实现的部分**是 FastAPI API、SQLite WAL 运行注册表、append-only 事件、单 TechScout 后台线程、Harness checkpoint、阶段 workspace、失败投影和封存 Trace。任务从 queued 到 running 的 claim 使用事务和条件更新；进程重启后活跃任务会重新入队，如果已有 checkpoint，就从最近阶段恢复。终态前先持久化产物并 seal Trace，再把 projection path、终态和终态事件放在同一个 SQLite 事务里。系统还会把缺少 live provider 或 Docker 等情况显式降级，不把缺失基础设施解释成候选组件不兼容。

在 Draft PR #102 初版中，我进一步落地了统一 RunQueue、默认 InMemory 嵌入式 Worker、可选 Redis dispatch/lease/heartbeat/reaper/DLQ、独立 Worker CLI、幂等提交、取消、deadline、一次有界 transient retry、健康检查和结构化脱敏日志。原门禁是 Python `189 passed / 2 skipped`，Ruff、OpenAPI/TypeScript 合同、Web build 和 Worker CLI smoke 通过。

最终在精确 head `595b506` 上，独立验收 Standards/Spec 都 Pass、0 阻断；聚焦 19、Web 82、TechScout 125/2 skipped 和 secret canary 均通过。ambiguous-success 原始重放最终 completed，旧 owner 被 fencing。边界是 PR 尚未合并或上线，未运行真实 Redis Lua/网络响应丢失、多进程竞争、OS SIGKILL，也未验证真实模型或 Live Eval；阻塞外部 I/O 线程只能 fencing/handoff，不能安全硬终止。”

## 30 秒压缩版

“TechScout 是一个有界的研究与验证 Agent。我实现了 SQLite 权威状态、checkpoint、sealed Trace 和 at-least-once Worker 可靠性；PR #102 在 `595b506` 通过独立确定性复审、0 阻断。它尚未合并/上线，未跑真实 Redis/多进程/SIGKILL，阻塞 I/O 线程也不能硬终止。”

## 状态提示

| 表达 | 状态 |
|---|---|
| SQLite WAL、事务 claim、单线程 executor、checkpoint/Trace | 已实现 |
| Redis adapter、独立 Worker、lease/heartbeat/reaper/DLQ | PR #102 已实现并通过离线/确定性独立复审；未合并/上线 |
| Registry owner tuple fencing | PR #102 已通过确定性复审；真实 Redis/多进程未验证 |
| 多机共享存储、OS hard-kill | 未实现/未验证 |
| 多实例生产部署、SLO、真实流量事故经验 | 未实现 |
| 真实模型成功率/成本改善 | 不在本材料范围，不能推断 |
