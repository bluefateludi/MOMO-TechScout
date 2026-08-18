# TechScout backend reliability

This slice keeps `RunRegistry` (SQLite WAL) as the only task-state authority.
Redis is an optional adapter for dispatch, leases, heartbeats, rate limiting, and
dead-letter routing. A Redis key is never evidence that a run is queued, running,
or terminal; operators and callers must read those facts from the Registry.

## Delivery semantics

Worker delivery is **at least once**. A worker reserves a run with a bounded
lease, then atomically claims the corresponding queued Registry row. Duplicate
delivery is expected after a lease expires or a process is interrupted. The
Registry state transition rejects a second claim and the duplicate delivery is
acknowledged without re-executing the run. This design does not claim
exactly-once execution.

The lifecycle is:

```text
queued -> running -> completed | completed_with_limitations
                  -> cancelled | timed_out | failed | dead_letter
                  -> queued (one bounded transient retry)
```

Submission accepts an optional `Idempotency-Key` header. Reusing a key with the
same normalized request returns the original run. Reusing it for a different
request returns the typed `idempotency_conflict` envelope. Registry admission,
capacity checking, idempotency storage, claims, attempts, cancellation intent,
terminal status, and trace events are transactional.

The queued Registry row is also the durable dispatch outbox. Admission happens
before enqueue, so a Redis failure returns a fail-closed 503 while leaving the
row queued; the dispatcher and an idempotent repeat submission both retry that
delivery. Queue state alone is never used to decide that a run was lost.

Fast runs receive a 120-second Registry deadline and verified runs receive a
300-second deadline. This absolute admission deadline is carried into execution
and rechecked by the terminal Registry transaction; dequeue never grants a new
budget. The existing harness also enforces its stage and whole-run budgets.
Cancellation is immediate for queued work and cooperative for running
work: the current bounded operation is allowed to return, after which the worker
publishes `cancelled` rather than a successful terminal state.

## Queue and worker modes

The default local server preserves the existing single-process experience. It
uses `InMemoryRunQueue`, starts one embedded worker, and still stores durable
state in SQLite.

For process isolation, start Redis, then run the API without an embedded worker:

```console
techscout serve --redis-url redis://127.0.0.1:6379/0 --queue-capacity 100
```

Start one or more worker processes against the same Registry, artifact root, and
Redis namespace:

```console
techscout-worker --redis-url redis://127.0.0.1:6379/0 \
  --state-root outputs/.web --output-root outputs --queue-capacity 100
```

Workers reserve a 30-second lease and heartbeat every 10 seconds. Every claim
stores the worker id, lease token, and a monotonic fencing token in the Registry;
heartbeat, progress, failure, and terminal publication use that full ownership
tuple as a compare-and-swap. The reaper
returns expired deliveries to the queue and records an interrupted, bounded
recovery in the Registry. Transient connection/timeout failures receive at most
one retry by default. Permanent or exhausted failures become `dead_letter`; raw
exception messages are neither persisted nor returned.

Queue commands are supervised by the executor loop. A command failure marks the
executor unavailable immediately, is logged only as the safe
`queue_unavailable` code, and receives bounded exponential backoff. A successful
queue cycle restores readiness. Five consecutive failures stop the consumer;
the standalone worker then exits non-zero so its process supervisor can restart
it, while an embedded API process remains live but reports not-ready. Readiness
also requires the expected runner/dispatcher thread to still be alive.

`SIGINT` and `SIGTERM` stop new reservations and wait for a bounded grace period.
If external I/O is still blocked, shutdown fences and hands off the active lease
before returning and exposes `active_external_io_not_terminated` as a runtime
limitation. Python cannot safely hard-kill that blocked thread; safety comes from
preventing it from publishing progress or a terminal result after handoff. Queue
capacity and a per-subject sliding-window limit provide backpressure before
execution.

## Operations and safety

- `GET /health/live` reports process liveness.
- `GET /health/ready` checks Registry access, executor availability, and live
  queue connectivity on each request. A failed dependency returns HTTP 503.
- `POST /api/v2/runs/{run_id}/cancel` records cancellation intent.
- API responses include a validated or generated `X-Request-ID`.
- worker logs carry request/run/worker context as applicable and redact common
  credential fields and bearer/token values.

The Redis adapter requires `redis>=5,<9`. Redis unavailability makes the
Redis-backed API not ready; it does not silently fall back to an in-process queue.
The client sets explicit two-second connection and command/socket timeouts and
does not retry timed-out commands implicitly.
Do not expose Redis publicly or place credentials in its URL in logs or checked-in
configuration.
