from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from paper_agent.web.context import execution_context
from paper_agent.web.errors import ErrorKind, classify_exception
from paper_agent.web.registry import RunRegistry, TechScoutRegistryRun
from paper_agent.web.task_queue import Lease, RunQueue
from paper_agent.web.techscout_api_models import TechScoutProgress, TechScoutStatus


@dataclass(frozen=True, slots=True)
class WorkResult:
    status: TechScoutStatus
    projection_path: str | None
    progress: TechScoutProgress


Processor = Callable[[TechScoutRegistryRun], WorkResult]


class TechScoutWorker:
    """At-least-once worker; Registry remains the task-state authority."""

    def __init__(
        self,
        registry: RunRegistry,
        queue: RunQueue,
        processor: Processor,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        heartbeat_seconds: int = 10,
    ) -> None:
        if heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat must be shorter than the lease")
        self.registry = registry
        self.queue = queue
        self.processor = processor
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.logger = logging.getLogger("paper_agent.web.worker")

    def process_once(self) -> bool:
        lease = self.queue.reserve(
            self.worker_id, lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return False
        row = self.registry.claim_techscout(lease.run_id, worker_id=self.worker_id)
        if row is None:
            current = self.registry.get_techscout(lease.run_id)
            if current.status == "running":
                self.queue.retry(lease)
            else:
                self.queue.ack(lease)
            return True
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(lease, stop_heartbeat),
            name=f"techscout-heartbeat-{self.worker_id}",
            daemon=True,
        )
        heartbeat.start()
        with execution_context(run_id=row.id, worker_id=self.worker_id):
            try:
                if row.cancel_requested:
                    self._terminal(row, "cancelled")
                    self.queue.ack(lease)
                    return True
                result = self.processor(row)
                current = self.registry.get_techscout(row.id)
                if current.cancel_requested:
                    self._terminal(current, "cancelled")
                elif datetime.now(timezone.utc) >= current.deadline_at:
                    self.registry.terminal_techscout(
                        current.id, "failed", projection_path=None,
                        progress=result.progress.model_copy(update={"stage": "terminal"}),
                        error_kind=ErrorKind.DEADLINE,
                        error_code="deadline_exceeded",
                    )
                else:
                    self.registry.terminal_techscout(
                        current.id, result.status,
                        projection_path=result.projection_path,
                        progress=result.progress,
                    )
                self.queue.ack(lease)
            except Exception as error:
                classified = classify_exception(
                    error, attempt=row.attempt_count, max_attempts=row.max_attempts,
                )
                failed = self.registry.record_techscout_failure(
                    row.id, kind=classified.kind, code=classified.code,
                    retryable=classified.retryable,
                )
                self.logger.error(
                    "TechScout execution failed safely",
                    extra={
                        "run_id": row.id, "code": classified.code,
                        "error_kind": classified.kind.value,
                        "attempt": row.attempt_count,
                    },
                )
                if failed.status == "queued":
                    self.queue.retry(lease)
                else:
                    self.queue.dead_letter(lease, reason=classified.code)
            finally:
                stop_heartbeat.set()
                heartbeat.join(timeout=min(1, self.heartbeat_seconds))
        return True

    def _terminal(self, row: TechScoutRegistryRun, status: TechScoutStatus) -> None:
        self.registry.terminal_techscout(
            row.id, status, projection_path=None,
            progress=row.progress.model_copy(update={"stage": "terminal"}),
        )

    def _heartbeat(self, lease: Lease, stopped: threading.Event) -> None:
        while not stopped.wait(self.heartbeat_seconds):
            if not self.queue.heartbeat(lease, lease_seconds=self.lease_seconds):
                self.logger.error(
                    "TechScout worker lease heartbeat was rejected",
                    extra={"run_id": lease.run_id, "code": "lease_lost"},
                )
                return
