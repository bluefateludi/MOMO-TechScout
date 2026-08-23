from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_worker_id: ContextVar[str | None] = ContextVar("worker_id", default=None)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    request_id: str | None = None
    run_id: str | None = None
    worker_id: str | None = None


def current_context() -> ExecutionContext:
    return ExecutionContext(_request_id.get(), _run_id.get(), _worker_id.get())


@contextmanager
def execution_context(
    *,
    request_id: str | None = None,
    run_id: str | None = None,
    worker_id: str | None = None,
) -> Iterator[ExecutionContext]:
    request_token = _request_id.set(request_id if request_id is not None else _request_id.get())
    run_token = _run_id.set(run_id if run_id is not None else _run_id.get())
    worker_token = _worker_id.set(worker_id if worker_id is not None else _worker_id.get())
    try:
        yield current_context()
    finally:
        _worker_id.reset(worker_token)
        _run_id.reset(run_token)
        _request_id.reset(request_token)
