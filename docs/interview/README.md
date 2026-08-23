# TechScout 面试材料

> 已合并事实基线：`750b17a7a2bf3217793c70e4fc065f1728288743`
>
> 未合并实现权威：Draft PR #102，精确 head `595b506501b1b893b33845e50b17fc06ae267e75`
>
> 本目录是面试表达，不是工程规范。工程事实与演进协议以 [`../production/`](../production/README.md) 为准。

## 诚实表达规则

- **已实现（基线）**：可以用“我实现了”，但要说清本地单进程、synthetic Fast Demo 等边界。
- **PR #102 已实现并通过独立复审**：可以描述离线/确定性验证事实；必须同时说明尚未合并、未上线，且真实 Redis/多进程/OS hard-kill/真实模型与 Live Eval 未验证。
- **未实现**：主动说明，随后解释如果生产化会如何验证。
- 三个 STAR 是确定性故障注入/恢复验证，不是线上事故复盘。
- 不讨论真实模型效果，不引用 synthetic 指标作为产品质量或简历成果。

最终独立验收基于 `595b506`：Standards/Spec 均 Pass、0 阻断；聚焦 `19 passed`、Web `82 passed`、TechScout `125 passed / 2 skipped`，secret canary 通过；ambiguous-success 原始重放恢复 `completed`，旧 owner 被 fencing。它是离线/确定性工程权威，不是线上事故、生产 SLO 或真实模型效果。

## 内容

1. [90 秒项目介绍](90-second-introduction.md)
2. [可靠性 deep dive](reliability-deep-dive.md)
3. [三个 STAR 故障案例](star-failure-cases.md)
4. [常见后端追问与诚实边界](backend-faq-and-honest-boundaries.md)
