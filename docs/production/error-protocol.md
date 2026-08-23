# 错误协议

## 1. 权威边界

已合并基线 `750b17a` 提供 `error.code/message/details` envelope。Draft PR #102 精确 head `595b506` 已针对取消/deadline/DLQ/readiness 和 queue failure supervision 通过离线/确定性独立复审；PR 尚未合并或上线。

```json
{
  "error": {
    "code": "run_not_found",
    "message": "The requested run was not found.",
    "details": {}
  }
}
```

## 2. PR #102 初版已落地的错误基础设施

- `_MESSAGES` 补齐 `executor_unavailable`，并用 `internal_error` 作为未知 code 的安全 fallback；基线中的消息表不一致已修复。
- 新增 `idempotency_conflict`、`invalid_state_transition`、`rate_limited`、`run_cancelled`、`deadline_exceeded` 等稳定 code。
- `ErrorKind` 区分 transient、permanent、deadline、cancelled、conflict。
- `TimeoutError` / `ConnectionError` 只在 attempt 预算内标为可重试；原始异常消息不进入公开 details。
- 每个响应包含校验后或新生成的 `X-Request-ID`；只接受 `[A-Za-z0-9._-]{1,64}`。
- request/run/worker context 通过 `contextvars` 注入结构化 JSON 日志。
- 日志 filter 清理 credential、authorization、bearer/token 等敏感值。
- 未处理异常仍只返回随机 correlation ID，不回显 stack trace 或第三方正文。
- `429 rate_limited`、`409 idempotency_conflict`、`503 queue_full/executor_unavailable` 已进入 API 合同路径。

最终复审覆盖错误分类、secret canary 脱敏、idempotency conflict、X-Request-ID、取消/deadline/DLQ、live readiness、queue failure supervision 和 OpenAPI/TypeScript 合同，0 阻断。

## 3. HTTP 与异步失败分层

- HTTP 错误表示请求准入/资源/服务边界失败。
- 已接受任务的执行失败写入 Registry 的 `error_kind/error_code` 和终态，不把异步失败伪装成 POST 的同步 500。
- permanent 或超预算 Worker 失败进入 `dead_letter`；队列只保存归一化 reason，不保存原始异常。
- `completed_with_limitations` / `no_safe_winner` 是诚实业务结果，不应归为基础设施错误重试。

## 4. 仍未实现

- error envelope 中显式的 `retryable`、`retry_after_seconds` 字段及 v2 统一 `Retry-After` header。
- 集中 error-code registry、OpenAPI 枚举、弃用/versioning 策略。
- `lease_lost` 与 `stale_fencing_token` 的公开错误合同；当前 owner tuple 拒绝语义主要通过内部状态和确定性测试验证。
- 分布式 trace ID、集中日志平台、错误码 SLO/告警和外部依赖面板。
- 真实 Redis server 下连接错误/超时的端到端归一化验证。
