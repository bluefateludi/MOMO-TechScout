from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from paper_agent.web.context import execution_context
from paper_agent.web.errors import ConflictError, ErrorKind, classify_exception
from paper_agent.web.registry import RunRegistry
from paper_agent.web.structured_logging import JsonFormatter, RedactingContextFilter
from paper_agent.web.task_queue import InMemoryRunQueue, QueueFullError
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
    assert registry.claim_techscout(run_id, worker_id="worker-a") is not None
    assert registry.claim_techscout(run_id, worker_id="worker-b") is None

    running = registry.request_cancel_techscout(run_id)
    assert running.status == "running"
    assert running.cancel_requested is True
    terminal = registry.terminal_techscout(
        run_id,
        "cancelled",
        projection_path=None,
        progress=running.progress.model_copy(update={"stage": "terminal"}),
    )
    assert terminal.status == "cancelled"
    with pytest.raises(ConflictError):
        registry.requeue_techscout(run_id)


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
    assert queue.reap_expired(now=now + timedelta(seconds=16)) == ["run-1"]

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
    assert expired.status == "failed"
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
