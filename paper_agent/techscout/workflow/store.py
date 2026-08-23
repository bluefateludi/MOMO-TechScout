from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol

from paper_agent.techscout.workflow.contracts import DecisionWorkflow, WorkflowEvent


class WorkflowNotFoundError(LookupError):
    code = "workflow_not_found"


class WorkflowCommandConflictError(RuntimeError):
    code = "workflow_command_conflict"


class WorkflowConcurrencyError(RuntimeError):
    code = "workflow_concurrency_conflict"


class DecisionWorkflowStore(Protocol):
    def initialize(
        self,
        workflow: DecisionWorkflow,
        *,
        context_hash: str,
        event: WorkflowEvent,
    ) -> DecisionWorkflow: ...

    def get(self, run_id: str) -> DecisionWorkflow: ...

    def receipt(
        self, run_id: str, *, command_id: str, payload_hash: str,
    ) -> DecisionWorkflow | None: ...

    def transition(
        self,
        previous: DecisionWorkflow,
        updated: DecisionWorkflow,
        *,
        command_id: str,
        payload_hash: str,
        event: WorkflowEvent,
    ) -> DecisionWorkflow: ...

    def events(self, run_id: str) -> tuple[WorkflowEvent, ...]: ...


class SqliteDecisionWorkflowStore:
    """SQLite adapter that atomically stores state, receipts, and audit events."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    def _migrate(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS decision_workflows (
                  run_id TEXT PRIMARY KEY,
                  version INTEGER NOT NULL,
                  state TEXT NOT NULL,
                  context_hash TEXT NOT NULL,
                  snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_workflow_commands (
                  run_id TEXT NOT NULL,
                  command_id TEXT NOT NULL,
                  payload_hash TEXT NOT NULL,
                  response_json TEXT NOT NULL,
                  PRIMARY KEY (run_id, command_id)
                );
                CREATE TABLE IF NOT EXISTS decision_workflow_events (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id TEXT NOT NULL,
                  event_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decision_workflow_events
                  ON decision_workflow_events(run_id, sequence);
            """)

    def initialize(
        self,
        workflow: DecisionWorkflow,
        *,
        context_hash: str,
        event: WorkflowEvent,
    ) -> DecisionWorkflow:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT context_hash,snapshot_json FROM decision_workflows WHERE run_id=?",
                (workflow.run_id,),
            ).fetchone()
            if row is not None:
                db.rollback()
                if row["context_hash"] != context_hash:
                    raise WorkflowCommandConflictError(
                        "workflow run identifier belongs to another Decision Context"
                    )
                return DecisionWorkflow.model_validate_json(row["snapshot_json"])
            db.execute(
                "INSERT INTO decision_workflows(run_id,version,state,context_hash,snapshot_json) VALUES (?,?,?,?,?)",
                (
                    workflow.run_id,
                    workflow.version,
                    workflow.state.value,
                    context_hash,
                    workflow.model_dump_json(),
                ),
            )
            self._insert_event(db, event)
            db.commit()
        return workflow

    def get(self, run_id: str) -> DecisionWorkflow:
        with self._connect() as db:
            row = db.execute(
                "SELECT snapshot_json FROM decision_workflows WHERE run_id=?", (run_id,),
            ).fetchone()
        if row is None:
            raise WorkflowNotFoundError(run_id)
        return DecisionWorkflow.model_validate_json(row["snapshot_json"])

    def receipt(
        self,
        run_id: str,
        *,
        command_id: str,
        payload_hash: str,
    ) -> DecisionWorkflow | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_hash,response_json FROM decision_workflow_commands WHERE run_id=? AND command_id=?",
                (run_id, command_id),
            ).fetchone()
        if row is None:
            return None
        if row["payload_hash"] != payload_hash:
            raise WorkflowCommandConflictError(
                "command identifier was already used with another payload"
            )
        return DecisionWorkflow.model_validate_json(row["response_json"])

    def transition(
        self,
        previous: DecisionWorkflow,
        updated: DecisionWorkflow,
        *,
        command_id: str,
        payload_hash: str,
        event: WorkflowEvent,
    ) -> DecisionWorkflow:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute(
                "SELECT payload_hash,response_json FROM decision_workflow_commands WHERE run_id=? AND command_id=?",
                (previous.run_id, command_id),
            ).fetchone()
            if receipt is not None:
                db.rollback()
                if receipt["payload_hash"] != payload_hash:
                    raise WorkflowCommandConflictError(
                        "command identifier was already used with another payload"
                    )
                return DecisionWorkflow.model_validate_json(receipt["response_json"])
            cursor = db.execute(
                "UPDATE decision_workflows SET version=?,state=?,snapshot_json=? WHERE run_id=? AND version=?",
                (
                    updated.version,
                    updated.state.value,
                    updated.model_dump_json(),
                    previous.run_id,
                    previous.version,
                ),
            )
            if cursor.rowcount != 1:
                db.rollback()
                raise WorkflowConcurrencyError("workflow changed concurrently")
            self._insert_event(db, event)
            db.execute(
                "INSERT INTO decision_workflow_commands(run_id,command_id,payload_hash,response_json) VALUES (?,?,?,?)",
                (previous.run_id, command_id, payload_hash, updated.model_dump_json()),
            )
            db.commit()
        return updated

    def events(self, run_id: str) -> tuple[WorkflowEvent, ...]:
        self.get(run_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT sequence,event_json FROM decision_workflow_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return tuple(
            WorkflowEvent.model_validate_json(row["event_json"]).model_copy(
                update={"sequence": row["sequence"]}
            )
            for row in rows
        )

    @staticmethod
    def _insert_event(db: sqlite3.Connection, event: WorkflowEvent) -> None:
        db.execute(
            "INSERT INTO decision_workflow_events(run_id,event_json) VALUES (?,?)",
            (event.run_id, event.model_dump_json()),
        )
