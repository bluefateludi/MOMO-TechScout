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
                  -> cancelled | failed | dead_letter
                  -> queued (one bounded transient retry)
```

Submission accepts an optional `Idempotency-Key` header. Reusing a key with the
same normalized request returns the original run. Reusing it for a different
request returns the typed `idempotency_conflict` envelope. Registry admission,
capacity checking, idempotency storage, claims, attempts, cancellation intent,
terminal status, and trace events are transactional.

Fast runs receive a 120-second Registry deadline and verified runs receive a
300-second deadline. The existing harness also enforces its stage and whole-run
budgets. Cancellation is immediate for queued work and cooperative for running
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

Workers reserve a 30-second lease and heartbeat every 10 seconds. The reaper
returns expired deliveries to the queue and records an interrupted, bounded
recovery in the Registry. Transient connection/timeout failures receive at most
one retry by default. Permanent or exhausted failures become `dead_letter`; raw
exception messages are neither persisted nor returned.

`SIGINT` and `SIGTERM` stop new reservations and wait for the active bounded work
before the worker closes. Queue capacity and a per-subject sliding-window limit
provide backpressure before execution.

## Operations and safety

- `GET /health/live` reports process liveness.
- `GET /health/ready` checks Registry access, executor availability, and queue
  connectivity. A failed dependency returns HTTP 503.
- `POST /api/v2/runs/{run_id}/cancel` records cancellation intent.
- API responses include a validated or generated `X-Request-ID`.
- worker logs carry request/run/worker context as applicable and redact common
  credential fields and bearer/token values.

The Redis adapter requires `redis>=5,<9`. Redis unavailability makes the
Redis-backed API not ready; it does not silently fall back to an in-process queue.
Do not expose Redis publicly or place credentials in its URL in logs or checked-in
configuration.
