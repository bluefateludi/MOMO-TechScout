from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from paper_agent.web.context import execution_context
from paper_agent.web.errors import ConflictError, ErrorKind, classify_exception
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
        self._active_lock = threading.Lock()
        self._active: tuple[Lease, TechScoutRegistryRun] | None = None

    def process_once(self) -> bool:
        lease = self.queue.reserve(
            self.worker_id, lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return False
        row = self.registry.claim_techscout(
            lease.run_id, worker_id=self.worker_id, lease_token=lease.token,
        )
        if row is None:
            self.queue.ack(lease)
            return True
        with self._active_lock:
            self._active = (lease, row)
        stop_heartbeat = threading.Event()
        ownership_lost = threading.Event()
        handoff_complete = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(
                lease, row.fencing_token, stop_heartbeat, ownership_lost,
                handoff_complete,
            ),
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
                if ownership_lost.is_set():
                    raise ConflictError()
                self.registry.terminal_techscout(
                    row.id, result.status,
                    projection_path=result.projection_path,
                    progress=result.progress,
                    worker_id=row.worker_id, lease_token=row.lease_token,
                    fencing_token=row.fencing_token,
                )
                self.queue.ack(lease)
            except ConflictError:
                self.logger.warning(
                    "TechScout stale worker result was fenced",
                    extra={"run_id": row.id, "code": "ownership_lost"},
                )
                if not ownership_lost.is_set() or handoff_complete.is_set():
                    self.queue.ack(lease)
            except Exception as error:
                classified = classify_exception(
                    error, attempt=row.attempt_count, max_attempts=row.max_attempts,
                )
                failed = self.registry.record_techscout_failure(
                    row.id, kind=classified.kind, code=classified.code,
                    retryable=classified.retryable,
                    worker_id=row.worker_id, lease_token=row.lease_token,
                    fencing_token=row.fencing_token,
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
                elif failed.status == "dead_letter":
                    self.queue.dead_letter(lease, reason=classified.code)
                else:
                    self.queue.ack(lease)
            finally:
                stop_heartbeat.set()
                heartbeat.join(timeout=min(1, self.heartbeat_seconds))
                with self._active_lock:
                    if self._active == (lease, row):
                        self._active = None
        return True

    def handoff_active(self) -> bool:
        """Fence and requeue active work; cannot terminate blocking external I/O."""
        with self._active_lock:
            active = self._active
        if active is None:
            return True
        lease, row = active
        try:
            recovered = self.registry.record_techscout_failure(
                row.id, kind=ErrorKind.TRANSIENT, code="shutdown_interrupted",
                retryable=True, worker_id=row.worker_id,
                lease_token=row.lease_token, fencing_token=row.fencing_token,
            )
        except ConflictError:
            self.queue.ack(lease)
            return False
        if recovered.status == "queued":
            self.queue.retry(lease)
        elif recovered.status == "dead_letter":
            self.queue.dead_letter(lease, reason="shutdown_interrupted")
        else:
            self.queue.ack(lease)
        return False

    def _terminal(self, row: TechScoutRegistryRun, status: TechScoutStatus) -> None:
        self.registry.terminal_techscout(
            row.id, status, projection_path=None,
            progress=row.progress.model_copy(update={"stage": "terminal"}),
            worker_id=row.worker_id, lease_token=row.lease_token,
            fencing_token=row.fencing_token,
        )

    def _heartbeat(
        self,
        lease: Lease,
        fencing_token: int,
        stopped: threading.Event,
        ownership_lost: threading.Event,
        handoff_complete: threading.Event,
    ) -> None:
        while not stopped.wait(self.heartbeat_seconds):
            try:
                queue_owned = self.queue.heartbeat(
                    lease, lease_seconds=self.lease_seconds,
                )
            except Exception:
                queue_owned = False
            registry_probe_failed = False
            try:
                registry_owned = self.registry.heartbeat_techscout(
                    lease.run_id, worker_id=lease.worker_id,
                    lease_token=lease.token,
                    fencing_token=fencing_token,
                )
            except Exception:
                registry_owned = False
                registry_probe_failed = True
            if not queue_owned or not registry_owned:
                ownership_lost.set()
                if registry_owned:
                    try:
                        recovered = self.registry.record_techscout_failure(
                            lease.run_id, kind=ErrorKind.TRANSIENT,
                            code="lease_lost", retryable=True,
                            worker_id=lease.worker_id, lease_token=lease.token,
                            fencing_token=fencing_token,
                        )
                        if recovered.status == "queued":
                            try:
                                self.queue.retry(lease)
                            except Exception:
                                pass
                        elif recovered.status == "dead_letter":
                            try:
                                self.queue.dead_letter(
                                    lease, reason="lease_lost",
                                )
                            except Exception:
                                pass
                        else:
                            try:
                                self.queue.ack(lease)
                            except Exception:
                                pass
                        handoff_complete.set()
                    except ConflictError:
                        handoff_complete.set()
                elif not registry_probe_failed:
                    handoff_complete.set()
                self.logger.error(
                    "TechScout worker lease heartbeat was rejected",
                    extra={"run_id": lease.run_id, "code": "lease_lost"},
                )
                return
