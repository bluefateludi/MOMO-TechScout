from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from paper_agent.web.context import execution_context
from paper_agent.web.errors import ConflictError, ErrorKind, WebError, classify_exception
from paper_agent.web.registry import RunRegistry
from paper_agent.web.structured_logging import JsonFormatter, RedactingContextFilter
from paper_agent.web.task_queue import InMemoryRunQueue, QueueFullError, RedisRunQueue
from paper_agent.web.techscout_execution import TechScoutSingleRunExecutor
from paper_agent.web.techscout_service import TechScoutProjectionService
from paper_agent.web.worker import TechScoutWorker, WorkResult
from paper_agent.web.techscout_api_models import TechScoutCreateRunRequest


REQUEST = TechScoutCreateRunRequest.model_validate({
    "question": "Choose a local vector store",
    "project_context": "A Python local RAG service",
    "environment": {
        "python_version": "3.11",
        "operating_system": "Linux",
        "deployment": "single node",
    },
    "hard_constraints": ["local persistence"],
    "candidates": [{"name": "Chroma"}],
    "mode": "fast",
})


def test_idempotent_admission_returns_same_run_and_rejects_key_reuse(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    first, created = registry.admit_techscout_idempotent(
        "00000000-0000-4000-8000-000000000101",
        REQUEST,
        capacity=4,
        idempotency_key="submission-1",
    )
    repeated, repeated_created = registry.admit_techscout_idempotent(
        "00000000-0000-4000-8000-000000000102",
        REQUEST,
        capacity=4,
        idempotency_key="submission-1",
    )
    assert created is True
    assert repeated_created is False
    assert repeated.id == first.id

    changed = REQUEST.model_copy(update={"question": "Choose another store"})
    with pytest.raises(ConflictError) as raised:
        registry.admit_techscout_idempotent(
            "00000000-0000-4000-8000-000000000103",
            changed,
            capacity=4,
            idempotency_key="submission-1",
        )
    assert raised.value.code == "idempotency_conflict"


def test_registry_claim_cancel_and_terminal_transitions_are_atomic(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000101"
    registry.admit_techscout(run_id, REQUEST, 4)
    claimed = registry.claim_techscout(run_id, worker_id="worker-a")
    assert claimed is not None
    assert registry.claim_techscout(run_id, worker_id="worker-b") is None

    running = registry.request_cancel_techscout(run_id)
    assert running.status == "running"
    assert running.cancel_requested is True
    terminal = registry.terminal_techscout(
        run_id,
        "cancelled",
        projection_path=None,
        progress=running.progress.model_copy(update={"stage": "terminal"}),
        worker_id=claimed.worker_id, lease_token=claimed.lease_token,
        fencing_token=claimed.fencing_token,
    )
    assert terminal.status == "cancelled"
    with pytest.raises(ConflictError):
        registry.requeue_techscout(run_id)


def test_queued_run_expiring_before_claim_projects_timed_out(tmp_path, monkeypatch):
    from paper_agent.web import registry as registry_module

    admitted_at = datetime(2026, 8, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(registry_module, "utc_now", lambda: admitted_at)
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000104"
    registry.admit_techscout(run_id, REQUEST, 4)
    monkeypatch.setattr(
        registry_module,
        "utc_now",
        lambda: admitted_at + timedelta(seconds=121),
    )

    assert registry.claim_techscout(run_id, worker_id="worker-a") is None

    expired = registry.get_techscout(run_id)
    assert expired.status == "timed_out"
    assert expired.error_kind == ErrorKind.DEADLINE
    assert expired.error_code == "deadline_exceeded"


def test_in_memory_queue_enforces_capacity_rate_limit_lease_reaping_and_dlq():
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    queue = InMemoryRunQueue(capacity=2, rate_limit=1, rate_window_seconds=60)
    queue.enqueue("run-1", now=now)
    queue.enqueue("run-2", now=now)
    with pytest.raises(QueueFullError):
        queue.enqueue("run-3", now=now)

    first = queue.reserve("worker-a", lease_seconds=10, now=now)
    assert first is not None and first.run_id == "run-1"
    assert queue.allow("client-a", now=now) is True
    assert queue.allow("client-a", now=now) is False
    assert queue.heartbeat(first, lease_seconds=10, now=now + timedelta(seconds=5))
    assert queue.reap_expired(now=now + timedelta(seconds=14)) == []
    expired = queue.reap_expired(now=now + timedelta(seconds=16))
    assert [lease.run_id for lease in expired] == ["run-1"]

    retried = queue.reserve("worker-b", lease_seconds=10, now=now + timedelta(seconds=16))
    assert retried is not None and retried.run_id == "run-2"
    queue.dead_letter(retried, reason="permanent_failure")
    assert queue.dead_letters() == [("run-2", "permanent_failure")]


def test_contextual_json_logging_redacts_secrets():
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1,
        "provider failed token=secret-canary", (), None,
    )
    record.authorization = "Bearer another-secret"
    with execution_context(request_id="req-1", run_id="run-1", worker_id="worker-1"):
        assert RedactingContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
    serialized = json.dumps(payload)
    assert payload["request_id"] == "req-1"
    assert payload["run_id"] == "run-1"
    assert payload["worker_id"] == "worker-1"
    assert "secret-canary" not in serialized
    assert "another-secret" not in serialized


def test_exception_classification_is_bounded_and_safe():
    assert classify_exception(TimeoutError("provider body secret"), attempt=1).kind is ErrorKind.TRANSIENT
    assert classify_exception(ValueError("bad schema"), attempt=1).kind is ErrorKind.PERMANENT
    exhausted = classify_exception(ConnectionError("offline"), attempt=2, max_attempts=2)
    assert exhausted.kind is ErrorKind.TRANSIENT
    assert "offline" not in exhausted.safe_details


def test_registry_deadline_expires_before_claim_with_typed_terminal_error(
    tmp_path, monkeypatch,
):
    import paper_agent.web.registry as registry_module

    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    monkeypatch.setattr(registry_module, "utc_now", lambda: now)
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000203"
    registry.admit_techscout_idempotent(
        run_id, REQUEST, capacity=4, deadline_seconds=1,
    )
    monkeypatch.setattr(
        registry_module, "utc_now", lambda: now + timedelta(seconds=2),
    )

    assert registry.claim_techscout(run_id, worker_id="worker-test") is None
    expired = registry.get_techscout(run_id)
    assert expired.status == "timed_out"
    assert expired.progress.stage == "terminal"
    assert expired.error_kind is ErrorKind.DEADLINE
    assert expired.error_code == "deadline_exceeded"


def test_worker_retries_transient_failure_once_then_completes(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = InMemoryRunQueue(capacity=4)
    run_id = "00000000-0000-4000-8000-000000000201"
    row = registry.admit_techscout(run_id, REQUEST, 4)
    queue.enqueue(run_id)
    calls = 0

    def process(claimed):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("secret provider body")
        return WorkResult(
            status="completed",
            projection_path="techscout/result.json",
            progress=claimed.progress.model_copy(update={"stage": "terminal"}),
        )

    worker = TechScoutWorker(
        registry, queue, process, worker_id="worker-test", lease_seconds=30,
    )
    assert worker.process_once() is True
    retried = registry.get_techscout(run_id)
    assert retried.status == "queued"
    assert retried.attempt_count == 1
    assert worker.process_once() is True
    assert registry.get_techscout(run_id).status == "completed"


def test_worker_dead_letters_permanent_failure_without_leaking_message(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = InMemoryRunQueue(capacity=4)
    run_id = "00000000-0000-4000-8000-000000000202"
    registry.admit_techscout(run_id, REQUEST, 4)
    queue.enqueue(run_id)

    def process(_claimed):
        raise ValueError("token=secret-canary raw provider body")

    worker = TechScoutWorker(registry, queue, process, worker_id="worker-test")
    assert worker.process_once() is True
    failed = registry.get_techscout(run_id)
    assert failed.status == "dead_letter"
    assert failed.error_kind is ErrorKind.PERMANENT
    assert failed.error_code == "execution_failed"
    assert queue.dead_letters() == [(run_id, "execution_failed")]


def test_stale_worker_cannot_terminalize_after_new_fenced_claim(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000301"
    registry.admit_techscout(run_id, REQUEST, 4)
    old = registry.claim_techscout(
        run_id, worker_id="worker-old", lease_token="lease-old",
    )
    assert old is not None
    retried = registry.record_techscout_failure(
        run_id, kind=ErrorKind.TRANSIENT, code="transient_execution_failure",
        retryable=True, worker_id=old.worker_id, lease_token=old.lease_token,
        fencing_token=old.fencing_token,
    )
    assert retried.status == "queued"
    current = registry.claim_techscout(
        run_id, worker_id="worker-new", lease_token="lease-new",
    )
    assert current is not None

    with pytest.raises(ConflictError):
        registry.terminal_techscout(
            run_id, "completed", projection_path="stale.json",
            progress=old.progress.model_copy(update={"stage": "terminal"}),
            worker_id=old.worker_id, lease_token=old.lease_token,
            fencing_token=old.fencing_token,
        )
    with pytest.raises(ConflictError):
        registry.record_techscout_failure(
            run_id, kind=ErrorKind.PERMANENT, code="stale_failure",
            retryable=False, worker_id=old.worker_id,
            lease_token=old.lease_token, fencing_token=old.fencing_token,
        )
    still_owned = registry.get_techscout(run_id)
    assert still_owned.status == "running"
    assert still_owned.worker_id == "worker-new"


def test_stale_worker_heartbeat_cannot_refresh_new_fenced_owner(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000308"
    registry.admit_techscout(run_id, REQUEST, 4)
    old = registry.claim_techscout(
        run_id, worker_id="worker-old", lease_token="lease-old",
    )
    assert old is not None
    registry.record_techscout_failure(
        run_id, kind=ErrorKind.TRANSIENT, code="retry", retryable=True,
        worker_id=old.worker_id, lease_token=old.lease_token,
        fencing_token=old.fencing_token,
    )
    current = registry.claim_techscout(
        run_id, worker_id="worker-new", lease_token="lease-new",
    )
    assert current is not None
    assert registry.heartbeat_techscout(
        run_id, worker_id=old.worker_id, lease_token=old.lease_token,
        fencing_token=old.fencing_token,
    ) is False
    assert registry.heartbeat_techscout(
        run_id, worker_id=current.worker_id, lease_token=current.lease_token,
        fencing_token=current.fencing_token,
    ) is True


def test_terminal_transaction_prioritizes_cancel_over_success(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000302"
    registry.admit_techscout(run_id, REQUEST, 4)
    owner = registry.claim_techscout(
        run_id, worker_id="worker-a", lease_token="lease-a",
    )
    assert owner is not None
    registry.request_cancel_techscout(run_id)
    result = registry.terminal_techscout(
        run_id, "completed", projection_path="success.json",
        progress=owner.progress.model_copy(update={"stage": "research"}),
        worker_id=owner.worker_id, lease_token=owner.lease_token,
        fencing_token=owner.fencing_token,
    )
    assert result.status == "cancelled"
    assert result.stage == "terminal"
    assert result.progress.stage == "terminal"
    assert result.projection_path is None


def test_terminal_transaction_prioritizes_deadline_over_success(tmp_path, monkeypatch):
    import paper_agent.web.registry as registry_module

    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(registry_module, "utc_now", lambda: now)
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000303"
    registry.admit_techscout_idempotent(
        run_id, REQUEST, capacity=4, deadline_seconds=1,
    )
    owner = registry.claim_techscout(
        run_id, worker_id="worker-a", lease_token="lease-a",
    )
    assert owner is not None
    monkeypatch.setattr(
        registry_module, "utc_now", lambda: now + timedelta(seconds=2),
    )
    result = registry.terminal_techscout(
        run_id, "completed", projection_path="late-success.json",
        progress=owner.progress,
        worker_id=owner.worker_id, lease_token=owner.lease_token,
        fencing_token=owner.fencing_token,
    )
    assert result.status == "timed_out"
    assert result.error_kind is ErrorKind.DEADLINE
    assert result.projection_path is None


def test_stale_progress_cannot_reopen_terminal_stage(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000306"
    registry.admit_techscout(run_id, REQUEST, 4)
    owner = registry.claim_techscout(
        run_id, worker_id="worker-a", lease_token="lease-a",
    )
    assert owner is not None
    terminal = registry.terminal_techscout(
        run_id, "completed", projection_path="result.json",
        progress=owner.progress,
        worker_id=owner.worker_id, lease_token=owner.lease_token,
        fencing_token=owner.fencing_token,
    )
    assert terminal.progress.stage == "terminal"
    with pytest.raises(ConflictError):
        registry.update_techscout_progress(
            run_id, owner.progress.model_copy(update={"stage": "research"}),
            worker_id=owner.worker_id, lease_token=owner.lease_token,
            fencing_token=owner.fencing_token,
        )
    unchanged = registry.get_techscout(run_id)
    assert unchanged.stage == "terminal"
    assert unchanged.progress.stage == "terminal"


def test_cancelled_processor_failure_is_acked_not_dead_lettered(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = InMemoryRunQueue(capacity=4)
    run_id = "00000000-0000-4000-8000-000000000304"
    registry.admit_techscout(run_id, REQUEST, 4)
    queue.enqueue(run_id)

    def process(_claimed):
        registry.request_cancel_techscout(run_id)
        raise ValueError("cancelled operation returned an error")

    worker = TechScoutWorker(registry, queue, process, worker_id="worker-a")
    assert worker.process_once() is True
    assert registry.get_techscout(run_id).status == "cancelled"
    assert queue.dead_letters() == []
    assert worker.process_once() is False


def test_expired_processor_failure_is_timed_out_not_dead_lettered(
    tmp_path, monkeypatch,
):
    import paper_agent.web.registry as registry_module

    now = datetime(2026, 8, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(registry_module, "utc_now", lambda: now)
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = InMemoryRunQueue(capacity=4)
    run_id = "00000000-0000-4000-8000-000000000309"
    registry.admit_techscout_idempotent(
        run_id, REQUEST, capacity=4, deadline_seconds=1,
    )
    queue.enqueue(run_id)

    def process(_claimed):
        monkeypatch.setattr(
            registry_module, "utc_now", lambda: now + timedelta(seconds=2),
        )
        raise TimeoutError("operation exceeded its absolute budget")

    worker = TechScoutWorker(registry, queue, process, worker_id="worker-a")
    assert worker.process_once() is True
    assert registry.get_techscout(run_id).status == "timed_out"
    assert queue.dead_letters() == []


def test_duplicate_delivery_for_running_owner_is_acked_without_retry_loop(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = InMemoryRunQueue(capacity=2)
    run_id = "00000000-0000-4000-8000-000000000305"
    registry.admit_techscout(run_id, REQUEST, 4)
    queue.enqueue(run_id)
    original = queue.reserve("worker-a", lease_seconds=30)
    assert original is not None
    assert registry.claim_techscout(
        run_id, worker_id="worker-a", lease_token=original.token,
    ) is not None
    assert queue.retry(original)

    duplicate = TechScoutWorker(
        registry, queue, lambda _row: pytest.fail("duplicate must not execute"),
        worker_id="worker-b",
    )
    assert duplicate.process_once() is True
    assert duplicate.process_once() is False
    assert registry.get_techscout(run_id).worker_id == "worker-a"


class _PingClient:
    def __init__(self) -> None:
        self.healthy = True

    def ping(self):
        if not self.healthy:
            raise ConnectionError("redis unavailable")
        return True


def test_executor_readiness_rechecks_redis_and_fails_closed(tmp_path):
    client = _PingClient()
    queue = RedisRunQueue(client)
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    executor = TechScoutSingleRunExecutor(
        registry, tmp_path / "outputs", queue=queue, embedded_worker=False,
    )
    executor.start()
    assert executor.ready() is True
    client.healthy = False
    assert executor.ready() is False
    executor.close()


def test_redis_factory_sets_explicit_network_timeouts(monkeypatch):
    captured = {}

    def from_url(url, **kwargs):
        captured.update(url=url, **kwargs)
        return _PingClient()

    import redis

    monkeypatch.setattr(redis.Redis, "from_url", from_url)
    RedisRunQueue.from_url("redis://example.test:6379/0")
    assert captured["socket_connect_timeout"] == 2.0
    assert captured["socket_timeout"] == 2.0
    assert captured["retry_on_timeout"] is False


class _FailOnceQueue(InMemoryRunQueue):
    def __init__(self):
        super().__init__(capacity=4)
        self.failed = False

    def enqueue(self, run_id, *, now=None):
        if not self.failed:
            self.failed = True
            raise ConnectionError("redis dropped")
        return super().enqueue(run_id, now=now)


class _FailAllowQueue(InMemoryRunQueue):
    def allow(self, subject, *, now=None):
        raise ConnectionError("redis dropped")


def test_rate_limit_backend_failure_is_fail_closed_before_admission(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    executor = TechScoutSingleRunExecutor(
        registry,
        tmp_path / "outputs",
        queue=_FailAllowQueue(capacity=4),
        embedded_worker=False,
    )
    executor.start()
    service = TechScoutProjectionService(
        registry, executor, tmp_path / "outputs", capacity=4,
    )

    with pytest.raises(WebError) as raised:
        service.create(REQUEST, idempotency_key="rate-limit-failure")

    assert raised.value.code == "execution_unavailable"
    assert registry.list_techscout() == []
    executor.close()


def test_idempotent_retry_redelivers_registry_outbox_after_enqueue_failure(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = _FailOnceQueue()
    executor = TechScoutSingleRunExecutor(
        registry, tmp_path / "outputs", queue=queue, embedded_worker=False,
    )
    executor.start()
    service = TechScoutProjectionService(
        registry, executor, tmp_path / "outputs", capacity=4,
    )
    with pytest.raises(WebError) as raised:
        service.create(REQUEST, idempotency_key="outbox-1")
    assert raised.value.code == "execution_unavailable"
    queued = registry.list_techscout()[0]
    assert queued.status == "queued"
    events, _ = registry.list_events(queued.id, after_sequence=0, limit=20)
    assert any(event.label == "Dispatch pending: queue_unavailable." for event in events)

    repeated = service.create(REQUEST, idempotency_key="outbox-1")
    assert str(repeated.id) == queued.id
    delivered = queue.reserve("worker-a", lease_seconds=30)
    assert delivered is not None and delivered.run_id == queued.id
    executor.close()


class _RejectHeartbeatQueue(InMemoryRunQueue):
    def __init__(self):
        super().__init__(capacity=2)
        self.rejected = threading.Event()

    def heartbeat(self, lease, *, lease_seconds, now=None):
        self.rejected.set()
        return False


def test_lost_queue_lease_fences_processor_before_terminal_commit(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = _RejectHeartbeatQueue()
    run_id = "00000000-0000-4000-8000-000000000310"
    registry.admit_techscout(run_id, REQUEST, 4)
    queue.enqueue(run_id)

    def process(row):
        assert queue.rejected.wait(timeout=2)
        for _ in range(100):
            if registry.get_techscout(run_id).status != "running":
                break
            time.sleep(0.005)
        return WorkResult(
            status="completed", projection_path="stale-success.json",
            progress=row.progress,
        )

    worker = TechScoutWorker(
        registry, queue, process, worker_id="worker-a",
        lease_seconds=1, heartbeat_seconds=0.01,
    )
    assert worker.process_once() is True
    result = registry.get_techscout(run_id)
    assert result.status == "queued"
    assert result.projection_path is None


def test_shutdown_hands_off_active_lease_without_claiming_io_was_terminated(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = InMemoryRunQueue(capacity=4)
    started = threading.Event()
    release = threading.Event()

    def blocking_processor(row):
        started.set()
        release.wait(timeout=5)
        return WorkResult(
            status="completed", projection_path="late.json",
            progress=row.progress,
        )

    executor = TechScoutSingleRunExecutor(
        registry, tmp_path / "outputs", queue=queue,
        shutdown_grace_seconds=0.05,
    )
    executor.worker = TechScoutWorker(
        registry, queue, blocking_processor, worker_id=executor.worker_id,
    )
    executor.start()
    row = registry.admit_techscout(
        "00000000-0000-4000-8000-000000000307", REQUEST, 4,
    )
    executor.submit(row.id)
    assert started.wait(timeout=2)

    executor.close()
    handed_off = registry.get_techscout(row.id)
    assert handed_off.status == "queued"
    assert executor.shutdown_complete is False
    assert executor.shutdown_limitation == "active_external_io_not_terminated"
    release.set()


class _FailingReapQueue(InMemoryRunQueue):
    def __init__(self, *, capacity):
        super().__init__(capacity=capacity)
        self.attempted = threading.Event()

    def reap_expired(self, *, now=None):
        self.attempted.set()
        raise ConnectionError("redis password=do-not-log")


class _FailOnceReserveQueue(InMemoryRunQueue):
    def __init__(self):
        super().__init__(capacity=4)
        self.failed = False
        self.failed_event = threading.Event()

    def reserve(self, worker_id, *, lease_seconds, now=None):
        if not self.failed:
            self.failed = True
            self.failed_event.set()
            raise ConnectionError("redis token=do-not-log")
        return super().reserve(worker_id, lease_seconds=lease_seconds, now=now)


class _FailAfterSettlementQueue(InMemoryRunQueue):
    def __init__(self, operation):
        super().__init__(capacity=4)
        self.operation = operation
        self.tripped = False

    def ack(self, lease):
        if self.operation == "ack":
            self.tripped = True
            raise ConnectionError("redis secret=ack-do-not-log")
        return super().ack(lease)

    def retry(self, lease):
        if self.operation == "retry":
            self.tripped = True
            raise ConnectionError("redis secret=retry-do-not-log")
        return super().retry(lease)

    def reap_expired(self, *, now=None):
        if self.tripped:
            raise ConnectionError("redis secret=settlement-do-not-log")
        return super().reap_expired(now=now)


def test_runner_reports_fatal_queue_failure_instead_of_remaining_false_ready(
    tmp_path, caplog,
):
    executor = TechScoutSingleRunExecutor(
        RunRegistry(tmp_path / "registry.sqlite3"),
        tmp_path / "outputs",
        queue=_FailingReapQueue(capacity=4),
        queue_failure_limit=2,
        queue_backoff_seconds=0.001,
    )

    with caplog.at_level(logging.ERROR):
        executor.start()
        assert executor.wait_failed(timeout=1) is True

    assert executor.ready() is False
    assert executor.failure_code == "queue_unavailable"
    assert "do-not-log" not in caplog.text
    started = time.monotonic()
    executor.close()
    assert time.monotonic() - started < 0.5


def test_external_worker_loop_returns_nonzero_after_fatal_queue_failure(tmp_path):
    from paper_agent.web import techscout_worker

    executor = TechScoutSingleRunExecutor(
        RunRegistry(tmp_path / "registry.sqlite3"),
        tmp_path / "outputs",
        queue=_FailingReapQueue(capacity=4),
        queue_failure_limit=1,
        queue_backoff_seconds=0.001,
    )
    executor.start()

    exit_code = techscout_worker.run_until_stopped(
        executor, threading.Event(), poll_seconds=0.001,
    )

    assert exit_code == 1
    executor.close()


def test_close_interrupts_queue_failure_backoff(tmp_path):
    queue = _FailingReapQueue(capacity=4)
    executor = TechScoutSingleRunExecutor(
        RunRegistry(tmp_path / "registry.sqlite3"),
        tmp_path / "outputs",
        queue=queue,
        queue_failure_limit=100,
        queue_backoff_seconds=10,
        queue_backoff_max_seconds=10,
    )
    executor.start()
    assert queue.attempted.wait(timeout=1)

    started = time.monotonic()
    executor.close()

    assert time.monotonic() - started < 0.5


def test_runner_recovers_after_transient_reserve_failure_and_consumes_work(tmp_path):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = _FailOnceReserveQueue()

    def processor(row):
        return WorkResult(
            status="completed", projection_path="result.json",
            progress=row.progress,
        )

    executor = TechScoutSingleRunExecutor(
        registry,
        tmp_path / "outputs",
        queue=queue,
        processor=processor,
        queue_failure_limit=3,
        queue_backoff_seconds=0.05,
    )
    run = registry.admit_techscout(
        "00000000-0000-4000-8000-000000000320", REQUEST, 4,
    )
    queue.enqueue(run.id)
    executor.start()
    assert queue.failed_event.wait(timeout=1)
    assert executor.ready() is False

    for _ in range(100):
        if (
            registry.get_techscout(run.id).status == "completed"
            and executor.ready()
        ):
            break
        time.sleep(0.01)

    assert registry.get_techscout(run.id).status == "completed"
    assert executor.failed is False
    assert executor.ready() is True
    executor.close()


@pytest.mark.parametrize("operation", ["ack", "retry"])
def test_settlement_queue_failure_is_supervised_and_redacted(
    tmp_path, caplog, operation,
):
    registry = RunRegistry(tmp_path / "registry.sqlite3")
    queue = _FailAfterSettlementQueue(operation)

    def processor(row):
        if operation == "retry":
            raise TimeoutError("provider credential=do-not-log")
        return WorkResult(
            status="completed", projection_path="result.json",
            progress=row.progress,
        )

    executor = TechScoutSingleRunExecutor(
        registry,
        tmp_path / "outputs",
        queue=queue,
        processor=processor,
        queue_failure_limit=2,
        queue_backoff_seconds=0.001,
    )
    run = registry.admit_techscout(
        f"00000000-0000-4000-8000-00000000032{0 if operation == 'ack' else 1}",
        REQUEST,
        4,
    )
    queue.enqueue(run.id)

    with caplog.at_level(logging.ERROR):
        executor.start()
        assert executor.wait_failed(timeout=1) is True

    expected = "completed" if operation == "ack" else "queued"
    assert registry.get_techscout(run.id).status == expected
    assert executor.ready() is False
    assert "do-not-log" not in caplog.text
    executor.close()
