from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from pydantic import TypeAdapter

from paper_agent.modeling import StrictModel
from paper_agent.observability.sanitize import sanitize_event_data
from paper_agent.web.api_models import ApiStatus, CreateRunRequest, Phase, RunProgress
from paper_agent.web.errors import ConflictError, ErrorKind, WebError
from paper_agent.web.techscout_api_models import (
    TechScoutCreateRunRequest,
    TechScoutProgress,
    TechScoutStage,
    TechScoutStatus,
)


_CREDENTIAL_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|passwd|secret|token)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:api[_-]?key|authorization|password|secret|token)=)[^&#\s]+"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RegistryRun(StrictModel):
    id: str
    artifact_run_id: str | None
    origin: str
    status: ApiStatus
    phase: Phase
    request: CreateRunRequest
    progress: RunProgress
    error: "RegistryError | None"
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class RegistryError(StrictModel):
    stage: str
    code: str
    paper_id: str | None = None


class RegistryEvent(StrictModel):
    sequence: int
    event_type: str
    stage: str | None
    status: str
    label: str
    skill: str | None
    tool: str | None
    duration_ms: int | None
    created_at: datetime


class TechScoutRegistryRun(StrictModel):
    id: str
    status: TechScoutStatus
    stage: TechScoutStage
    request: TechScoutCreateRunRequest
    progress: TechScoutProgress
    projection_path: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    idempotency_key: str | None = None
    request_hash: str
    deadline_at: datetime
    attempt_count: int
    max_attempts: int
    cancel_requested: bool
    worker_id: str | None = None
    lease_token: str | None = None
    fencing_token: int = 0
    error_kind: ErrorKind | None = None
    error_code: str | None = None


class RunRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 5:
                raise RuntimeError("run registry schema is newer than this server")
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY,
                  artifact_run_id TEXT UNIQUE,
                  origin TEXT NOT NULL CHECK (origin IN ('live','bundled_demo')),
                  status TEXT NOT NULL CHECK (status IN ('queued','running','completed','completed_with_degradation','failed','interrupted')),
                  phase TEXT NOT NULL CHECK (phase IN ('queued','initializing','search','acquisition','chunking','retrieval','analysis','synthesis','citation_check','publishing','terminal')),
                  request_json TEXT NOT NULL, progress_json TEXT NOT NULL,
                  error_json TEXT, created_at TEXT NOT NULL, started_at TEXT,
                  finished_at TEXT, updated_at TEXT NOT NULL
                )
            """)
            if version < 2:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS run_events (
                      sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                      run_id TEXT NOT NULL,
                      event_type TEXT NOT NULL CHECK (event_type IN ('run','stage','skill','tool','recovery','approval')),
                      stage TEXT, status TEXT NOT NULL, label TEXT NOT NULL,
                      skill TEXT, tool TEXT, duration_ms INTEGER,
                      created_at TEXT NOT NULL,
                      FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                    )
                """)
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events(run_id,sequence)"
                )
            if version < 3:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS techscout_runs (
                      id TEXT PRIMARY KEY,
                      status TEXT NOT NULL CHECK (status IN ('queued','running','completed','completed_with_limitations','failed')),
                      stage TEXT NOT NULL CHECK (stage IN ('plan','research','verify','decide','terminal')),
                      request_json TEXT NOT NULL, progress_json TEXT NOT NULL,
                      projection_path TEXT, created_at TEXT NOT NULL, started_at TEXT,
                      finished_at TEXT, updated_at TEXT NOT NULL
                    )
                """)
                db.execute("ALTER TABLE run_events RENAME TO run_events_v2")
                db.execute("""
                    CREATE TABLE run_events (
                      sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                      run_id TEXT NOT NULL,
                      event_type TEXT NOT NULL CHECK (event_type IN ('run','stage','skill','tool','recovery','approval')),
                      stage TEXT, status TEXT NOT NULL, label TEXT NOT NULL,
                      skill TEXT, tool TEXT, duration_ms INTEGER,
                      created_at TEXT NOT NULL
                    )
                """)
                db.execute("""
                    INSERT INTO run_events
                    SELECT sequence,run_id,event_type,stage,status,label,skill,tool,duration_ms,created_at
                    FROM run_events_v2
                """)
                db.execute("DROP TABLE run_events_v2")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events(run_id,sequence)"
                )
            if version < 4:
                db.execute("ALTER TABLE techscout_runs RENAME TO techscout_runs_v3")
                db.execute("""
                    CREATE TABLE techscout_runs (
                      id TEXT PRIMARY KEY,
                      status TEXT NOT NULL CHECK (status IN (
                        'queued','running','completed','completed_with_limitations','failed',
                        'cancelled','interrupted','dead_letter'
                      )),
                      stage TEXT NOT NULL CHECK (stage IN ('plan','research','verify','decide','terminal')),
                      request_json TEXT NOT NULL, progress_json TEXT NOT NULL,
                      projection_path TEXT, created_at TEXT NOT NULL, started_at TEXT,
                      finished_at TEXT, updated_at TEXT NOT NULL,
                      idempotency_key TEXT UNIQUE, request_hash TEXT NOT NULL,
                      deadline_at TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
                      max_attempts INTEGER NOT NULL DEFAULT 2,
                      cancel_requested INTEGER NOT NULL DEFAULT 0,
                      worker_id TEXT, error_kind TEXT, error_code TEXT
                    )
                """)
                fallback_deadline = (utc_now() + timedelta(minutes=5)).isoformat()
                db.execute("""
                    INSERT INTO techscout_runs (
                      id,status,stage,request_json,progress_json,projection_path,
                      created_at,started_at,finished_at,updated_at,request_hash,deadline_at
                    )
                    SELECT id,status,stage,request_json,progress_json,projection_path,
                           created_at,started_at,finished_at,updated_at,'legacy',?
                    FROM techscout_runs_v3
                """, (fallback_deadline,))
                db.execute("DROP TABLE techscout_runs_v3")
                db.execute(
                    "CREATE INDEX idx_techscout_status_created ON techscout_runs(status,created_at,id)"
                )
            if version < 5:
                db.execute("ALTER TABLE techscout_runs RENAME TO techscout_runs_v4")
                db.execute("""
                    CREATE TABLE techscout_runs (
                      id TEXT PRIMARY KEY,
                      status TEXT NOT NULL CHECK (status IN (
                        'queued','running','completed','completed_with_limitations','failed',
                        'cancelled','timed_out','interrupted','dead_letter'
                      )),
                      stage TEXT NOT NULL CHECK (stage IN ('plan','research','verify','decide','terminal')),
                      request_json TEXT NOT NULL, progress_json TEXT NOT NULL,
                      projection_path TEXT, created_at TEXT NOT NULL, started_at TEXT,
                      finished_at TEXT, updated_at TEXT NOT NULL,
                      idempotency_key TEXT UNIQUE, request_hash TEXT NOT NULL,
                      deadline_at TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
                      max_attempts INTEGER NOT NULL DEFAULT 2,
                      cancel_requested INTEGER NOT NULL DEFAULT 0,
                      worker_id TEXT, lease_token TEXT,
                      fencing_token INTEGER NOT NULL DEFAULT 0,
                      error_kind TEXT, error_code TEXT
                    )
                """)
                db.execute("""
                    INSERT INTO techscout_runs (
                      id,status,stage,request_json,progress_json,projection_path,
                      created_at,started_at,finished_at,updated_at,idempotency_key,
                      request_hash,deadline_at,attempt_count,max_attempts,cancel_requested,
                      worker_id,error_kind,error_code
                    )
                    SELECT id,status,stage,request_json,progress_json,projection_path,
                           created_at,started_at,finished_at,updated_at,idempotency_key,
                           request_hash,deadline_at,attempt_count,max_attempts,cancel_requested,
                           worker_id,error_kind,error_code
                    FROM techscout_runs_v4
                """)
                db.execute("DROP TABLE techscout_runs_v4")
                db.execute(
                    "CREATE INDEX idx_techscout_status_created ON techscout_runs(status,created_at,id)"
                )
            db.execute("PRAGMA user_version=5")
            db.commit()

    def ready(self) -> bool:
        try:
            with self._connect() as db:
                return db.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def admit_techscout(
        self, run_id: str, request: TechScoutCreateRunRequest, capacity: int,
    ) -> TechScoutRegistryRun:
        row, _ = self.admit_techscout_idempotent(run_id, request, capacity=capacity)
        return row

    def admit_techscout_idempotent(
        self,
        run_id: str,
        request: TechScoutCreateRunRequest,
        *,
        capacity: int,
        idempotency_key: str | None = None,
        max_attempts: int = 2,
        deadline_seconds: int | None = None,
    ) -> tuple[TechScoutRegistryRun, bool]:
        now = utc_now().isoformat()
        request_json = request.model_dump_json()
        request_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        deadline = (
            utc_now() + timedelta(
                seconds=deadline_seconds or (300 if request.mode == "verified" else 120)
            )
        ).isoformat()
        progress = TechScoutProgress(
            stage="plan", completed_stages=[], elapsed_seconds=0,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = db.execute(
                    "SELECT id,request_hash FROM techscout_runs WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    db.rollback()
                    if existing["request_hash"] != request_hash:
                        raise ConflictError("idempotency_conflict")
                    return self.get_techscout(existing["id"]), False
            active = db.execute(
                "SELECT COUNT(*) FROM techscout_runs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if active >= capacity:
                db.rollback()
                raise WebError(503, "queue_full")
            db.execute(
                """INSERT INTO techscout_runs (
                     id,status,stage,request_json,progress_json,projection_path,
                     created_at,started_at,finished_at,updated_at,idempotency_key,
                     request_hash,deadline_at,attempt_count,max_attempts,cancel_requested,
                     worker_id,error_kind,error_code
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, "queued", "plan", request_json,
                    progress.model_dump_json(), None, now, None, None, now,
                    idempotency_key, request_hash, deadline, 0, max_attempts, 0,
                    None, None, None,
                ),
            )
            self._append_event_in_transaction(
                db, run_id, event_type="run", stage="plan", status="queued",
                label="TechScout run accepted by the local queue.",
            )
            db.commit()
        return self.get_techscout(run_id), True

    def get_techscout(self, run_id: str) -> TechScoutRegistryRun:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM techscout_runs WHERE id=?", (run_id,),
            ).fetchone()
        if row is None:
            raise WebError(404, "run_not_found")
        return self._parse_techscout(row)

    def list_techscout(self) -> list[TechScoutRegistryRun]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM techscout_runs ORDER BY created_at DESC,id DESC"
            ).fetchall()
        return [self._parse_techscout(row) for row in rows]

    def active_techscout(self) -> list[TechScoutRegistryRun]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM techscout_runs WHERE status IN ('queued','running') ORDER BY created_at,id"
            ).fetchall()
        return [self._parse_techscout(row) for row in rows]

    def claim_oldest_techscout(self) -> TechScoutRegistryRun | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM techscout_runs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if row is None:
                db.rollback()
                return None
            db.rollback()
        return self.claim_techscout(
            row["id"], worker_id="local-worker",
            lease_token=f"local:{uuid.uuid4().hex}",
        )

    def claim_techscout(
        self, run_id: str, *, worker_id: str, lease_token: str | None = None,
    ) -> TechScoutRegistryRun | None:
        now = utc_now()
        lease_token = lease_token or f"compat:{uuid.uuid4().hex}"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT deadline_at FROM techscout_runs WHERE id=? AND status='queued'",
                (run_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                return None
            if datetime.fromisoformat(row["deadline_at"]) <= now:
                progress = TechScoutProgress(
                    stage="terminal", completed_stages=[], elapsed_seconds=0,
                ).model_dump_json()
                db.execute(
                    """UPDATE techscout_runs SET status='timed_out',stage='terminal',
                       error_kind='deadline',error_code='deadline_exceeded',
                       progress_json=?,finished_at=?,updated_at=?
                       WHERE id=? AND status='queued'""",
                    (progress, now.isoformat(), now.isoformat(), run_id),
                )
                self._append_event_in_transaction(
                    db, run_id, event_type="run", stage="terminal", status="timed_out",
                    label="TechScout run deadline expired before execution.",
                )
                db.commit()
                return None
            changed = db.execute(
                """UPDATE techscout_runs SET status='running',
                   started_at=COALESCE(started_at,?),updated_at=?,worker_id=?,
                   lease_token=?,fencing_token=fencing_token+1,
                   attempt_count=attempt_count+1 WHERE id=? AND status='queued'""",
                (
                    now.isoformat(), now.isoformat(), worker_id, lease_token, run_id,
                ),
            ).rowcount
            if changed:
                self._append_event_in_transaction(
                    db, run_id, event_type="stage", stage="plan",
                    status="running", label="TechScout execution started.",
                )
            db.commit()
        return self.get_techscout(run_id) if changed else None

    def update_techscout_progress(
        self,
        run_id: str,
        progress: TechScoutProgress,
        *,
        event_type: str = "stage",
        label: str | None = None,
        skill: str | None = None,
        tool: str | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
        fencing_token: int | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT stage,status,worker_id,lease_token,fencing_token
                   FROM techscout_runs WHERE id=?""", (run_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise WebError(404, "run_not_found")
            if row["status"] != "running" or not self._techscout_owner_matches(
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
            ):
                db.rollback()
                raise ConflictError()
            changed = db.execute(
                """UPDATE techscout_runs SET stage=?,progress_json=?,updated_at=?
                   WHERE id=? AND status='running' AND worker_id=?
                   AND lease_token=? AND fencing_token=?""",
                (
                    progress.stage, progress.model_dump_json(), utc_now().isoformat(),
                    run_id, worker_id, lease_token, fencing_token,
                ),
            ).rowcount
            if not changed:
                db.rollback()
                raise ConflictError()
            if label is not None:
                self._append_event_in_transaction(
                    db, run_id, event_type=event_type, stage=progress.stage,
                    status="running", label=label, skill=skill, tool=tool,
                )
            db.commit()

    def terminal_techscout(
        self,
        run_id: str,
        status: TechScoutStatus,
        *,
        projection_path: str | None,
        progress: TechScoutProgress,
        error_kind: ErrorKind | None = None,
        error_code: str | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
        fencing_token: int | None = None,
    ) -> TechScoutRegistryRun:
        if status not in {
            "completed", "completed_with_limitations", "failed", "cancelled",
            "timed_out", "interrupted", "dead_letter",
        }:
            raise ValueError("terminal TechScout status required")
        now_value = utc_now()
        now = now_value.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT status,cancel_requested,deadline_at,worker_id,
                          lease_token,fencing_token
                   FROM techscout_runs WHERE id=?""", (run_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise WebError(404, "run_not_found")
            if row["status"] not in {"queued", "running", "interrupted"}:
                db.rollback()
                raise ConflictError()
            if row["status"] == "running" and not self._techscout_owner_matches(
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
            ):
                db.rollback()
                raise ConflictError()
            if row["cancel_requested"]:
                status = "cancelled"
                projection_path = None
                error_kind = ErrorKind.CANCELLED
                error_code = "run_cancelled"
            elif datetime.fromisoformat(row["deadline_at"]) <= now_value:
                status = "timed_out"
                projection_path = None
                error_kind = ErrorKind.DEADLINE
                error_code = "deadline_exceeded"
            terminal_progress = progress.model_copy(update={"stage": "terminal"})
            owner_guard = row["status"] == "running"
            changed = db.execute(
                """UPDATE techscout_runs SET status=?,stage='terminal',progress_json=?,
                   projection_path=?,finished_at=?,updated_at=?,worker_id=NULL,
                   lease_token=NULL,error_kind=?,error_code=? WHERE id=?
                   AND status IN ('queued','running','interrupted')
                   AND (?=0 OR (worker_id=? AND lease_token=? AND fencing_token=?))""",
                (
                    status, terminal_progress.model_dump_json(), projection_path, now, now,
                    error_kind.value if error_kind else None, error_code, run_id,
                    int(owner_guard), worker_id, lease_token, fencing_token,
                ),
            )
            if not changed.rowcount:
                db.rollback()
                raise ConflictError()
            self._append_event_in_transaction(
                db, run_id, event_type="run", stage="terminal", status=status,
                label="TechScout run reached a terminal state.",
            )
            db.commit()
        return self.get_techscout(run_id)

    def request_cancel_techscout(self, run_id: str) -> TechScoutRegistryRun:
        now = utc_now().isoformat()
        progress = TechScoutProgress(
            stage="terminal", completed_stages=[], elapsed_seconds=0,
        ).model_dump_json()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM techscout_runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise WebError(404, "run_not_found")
            if row["status"] == "queued":
                db.execute(
                    """UPDATE techscout_runs SET status='cancelled',stage='terminal',
                       progress_json=?,cancel_requested=1,finished_at=?,updated_at=?
                       WHERE id=? AND status='queued'""",
                    (progress, now, now, run_id),
                )
                self._append_event_in_transaction(
                    db, run_id, event_type="run", stage="terminal",
                    status="cancelled", label="TechScout run was cancelled before execution.",
                )
            elif row["status"] == "running":
                db.execute(
                    "UPDATE techscout_runs SET cancel_requested=1,updated_at=? WHERE id=?",
                    (now, run_id),
                )
            db.commit()
        return self.get_techscout(run_id)

    def record_techscout_failure(
        self,
        run_id: str,
        *,
        kind: ErrorKind,
        code: str,
        retryable: bool,
        worker_id: str | None = None,
        lease_token: str | None = None,
        fencing_token: int | None = None,
    ) -> TechScoutRegistryRun:
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT status,attempt_count,max_attempts,cancel_requested,deadline_at,
                          worker_id,lease_token,fencing_token,progress_json
                   FROM techscout_runs WHERE id=?""",
                (run_id,),
            ).fetchone()
            if row is None:
                db.rollback()
                raise WebError(404, "run_not_found")
            if row["status"] != "running":
                db.rollback()
                raise ConflictError()
            if not self._techscout_owner_matches(
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
            ):
                db.rollback()
                raise ConflictError()
            cancelled = bool(row["cancel_requested"])
            timed_out = datetime.fromisoformat(row["deadline_at"]) <= now
            can_retry = (
                retryable and row["attempt_count"] < row["max_attempts"]
                and not cancelled and not timed_out
            )
            status = (
                "cancelled" if cancelled else "timed_out" if timed_out
                else "queued" if can_retry else "dead_letter"
            )
            stage = "plan" if can_retry else "terminal"
            finished_at = None if can_retry else now.isoformat()
            if cancelled:
                kind, code = ErrorKind.CANCELLED, "run_cancelled"
            elif timed_out:
                kind, code = ErrorKind.DEADLINE, "deadline_exceeded"
            progress = TechScoutProgress.model_validate_json(row["progress_json"])
            progress = progress.model_copy(update={"stage": stage})
            db.execute(
                """UPDATE techscout_runs SET status=?,stage=?,progress_json=?,
                   worker_id=NULL,lease_token=NULL,error_kind=?,error_code=?,
                   finished_at=?,updated_at=? WHERE id=? AND status='running'
                   AND worker_id=? AND lease_token=? AND fencing_token=?""",
                (
                    status, stage, progress.model_dump_json(), kind.value, code,
                    finished_at, now.isoformat(), run_id,
                    worker_id, lease_token, fencing_token,
                ),
            )
            self._append_event_in_transaction(
                db, run_id,
                event_type="recovery" if can_retry else "run",
                stage=stage, status=status,
                label=(
                    "TechScout transient failure scheduled for a bounded retry."
                    if can_retry
                    else "TechScout execution reached a safe terminal state."
                ),
            )
            db.commit()
        return self.get_techscout(run_id)

    def heartbeat_techscout(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
    ) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """UPDATE techscout_runs SET updated_at=? WHERE id=? AND status='running'
                   AND worker_id=? AND lease_token=? AND fencing_token=?""",
                (
                    utc_now().isoformat(), run_id, worker_id, lease_token,
                    fencing_token,
                ),
            ).rowcount
            db.commit()
        return bool(changed)

    def fail_stuck_techscout(self, run_id: str) -> None:
        """Last-resort queue release when normal failed publication also fails."""
        now = utc_now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """UPDATE techscout_runs SET status='failed',stage='terminal',
                   finished_at=?,updated_at=? WHERE id=? AND status='running'""",
                (now, now, run_id),
            ).rowcount
            if changed:
                self._append_event_in_transaction(
                    db, run_id, event_type="run", stage="terminal", status="failed",
                    label="TechScout terminalization failed safely and released the queue.",
                )
            db.commit()

    def requeue_techscout(self, run_id: str) -> TechScoutRegistryRun:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """UPDATE techscout_runs SET status='queued',worker_id=NULL,updated_at=?
                   WHERE id=? AND status IN ('running','interrupted')
                   AND cancel_requested=0 AND attempt_count < max_attempts""",
                (utc_now().isoformat(), run_id),
            ).rowcount
            if changed:
                self._append_event_in_transaction(
                    db, run_id, event_type="recovery", stage="plan", status="queued",
                    label="Web process refresh requeued the checkpointed run.",
                )
            if not changed:
                status = db.execute(
                    "SELECT status FROM techscout_runs WHERE id=?", (run_id,),
                ).fetchone()
                db.rollback()
                if status is None:
                    raise WebError(404, "run_not_found")
                if status["status"] == "queued":
                    return self.get_techscout(run_id)
                raise ConflictError()
            db.commit()
        return self.get_techscout(run_id)

    def admit(self, run_id: str, request: CreateRunRequest, capacity: int) -> RegistryRun:
        now = utc_now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
            if active >= capacity:
                db.rollback()
                raise WebError(503, "queue_full")
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, None, "live", "queued", "queued", request.model_dump_json(),
                 RunProgress().model_dump_json(), None, now, None, None, now),
            )
            self._append_event_in_transaction(
                db, run_id, event_type="run", stage="queued", status="queued",
                label="Run accepted by the local queue.",
            )
            db.commit()
        return self.get(run_id)

    def get(self, run_id: str) -> RegistryRun:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise WebError(404, "run_not_found")
        return self._parse(row)

    def claim_oldest(self) -> RegistryRun | None:
        now = utc_now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM runs WHERE status='running' LIMIT 1").fetchone():
                db.rollback()
                return None
            row = db.execute("SELECT id FROM runs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                db.rollback()
                return None
            changed = db.execute(
                "UPDATE runs SET status='running',phase='initializing',started_at=?,updated_at=? WHERE id=? AND status='queued'",
                (now, now, row["id"]),
            ).rowcount
            if changed:
                self._append_event_in_transaction(
                    db, row["id"], event_type="stage", stage="initializing",
                    status="running", label="Execution started.",
                )
            db.commit()
        return self.get(row["id"]) if changed else None

    def set_artifact_id(self, run_id: str, artifact_run_id: str) -> None:
        if not artifact_run_id or Path(artifact_run_id).name != artifact_run_id or any(c in artifact_run_id for c in ("/", "\\")):
            raise ValueError("artifact_run_id must be a basename")
        self._update(run_id, artifact_run_id=artifact_run_id)

    def update_progress(self, run_id: str, phase: Phase, progress: RunProgress) -> None:
        phases = ["queued","initializing","search","acquisition","chunking","retrieval","analysis","synthesis","citation_check","publishing","terminal"]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT phase FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                db.rollback()
                raise WebError(404, "run_not_found")
            current_phase = row["phase"]
            if phases.index(phase) < phases.index(current_phase):
                db.rollback()
                return
            now = utc_now().isoformat()
            db.execute(
                "UPDATE runs SET phase=?,progress_json=?,updated_at=? WHERE id=?",
                (phase, progress.model_dump_json(), now, run_id),
            )
            if phase != current_phase:
                self._append_event_in_transaction(
                    db, run_id, event_type="stage", stage=phase, status="running",
                    label=f"Entered {phase.replace('_', ' ')} stage.",
                )
            db.commit()

    def terminal(self, run_id: str, status: ApiStatus, *, finished_at: datetime | None = None, error: RegistryError | dict[str, object] | None = None) -> None:
        if status not in ("completed", "completed_with_degradation", "failed", "interrupted"):
            raise ValueError("terminal status required")
        now = utc_now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is None:
                db.rollback()
                raise WebError(404, "run_not_found")
            db.execute(
                """UPDATE runs SET status=?,phase='terminal',finished_at=?,error_json=?,updated_at=?
                   WHERE id=?""",
                (
                    status, (finished_at or utc_now()).isoformat(),
                    RegistryError.model_validate(error).model_dump_json() if error else None,
                    now, run_id,
                ),
            )
            self._append_event_in_transaction(
                db, run_id, event_type="run", stage="terminal", status=status,
                label="Run reached a terminal state.",
            )
            db.commit()

    def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        stage: str | None,
        status: str,
        label: str,
        skill: str | None = None,
        tool: str | None = None,
        duration_ms: int | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._run_exists(db, run_id):
                db.rollback()
                raise WebError(404, "run_not_found")
            self._append_event_in_transaction(
                db, run_id, event_type=event_type, stage=stage, status=status,
                label=label, skill=skill, tool=tool, duration_ms=duration_ms,
                secrets=secrets,
            )
            db.commit()

    def list_events(
        self, run_id: str, *, after_sequence: int, limit: int,
    ) -> tuple[list[RegistryEvent], bool]:
        with self._connect() as db:
            if not self._run_exists(db, run_id):
                raise WebError(404, "run_not_found")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM run_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (run_id, after_sequence, limit + 1),
            ).fetchall()
        page = rows[:limit]
        return [self._parse_event(row) for row in page], len(rows) > limit

    @staticmethod
    def _append_event_in_transaction(
        db: sqlite3.Connection,
        run_id: str,
        *,
        event_type: str,
        stage: str | None,
        status: str,
        label: str,
        skill: str | None = None,
        tool: str | None = None,
        duration_ms: int | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        if event_type not in {"run", "stage", "skill", "tool", "recovery", "approval"}:
            raise ValueError("unsupported event type")
        if duration_ms is not None and duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")

        def clean(value: str | None, maximum: int) -> str | None:
            if value is None:
                return None
            redacted = sanitize_event_data(value, secrets=secrets)
            if not isinstance(redacted, str):
                raise ValueError("trace text must be a string")
            normalized = re.sub(r"[\x00-\x1f\x7f]", " ", redacted).strip()
            normalized = _BEARER_VALUE.sub("[REDACTED]", normalized)
            normalized = _CREDENTIAL_VALUE.sub(
                lambda match: f"{match.group(1)}=[REDACTED]", normalized,
            )
            normalized = _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", normalized)
            if not normalized:
                raise ValueError("trace text must not be empty")
            return normalized[:maximum]

        db.execute(
            """INSERT INTO run_events
               (run_id,event_type,stage,status,label,skill,tool,duration_ms,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                run_id, event_type, clean(stage, 80), clean(status, 80),
                clean(label, 240), clean(skill, 120), clean(tool, 120),
                duration_ms, utc_now().isoformat(),
            ),
        )

    @staticmethod
    def _parse_event(row: sqlite3.Row) -> RegistryEvent:
        return RegistryEvent(
            sequence=row["sequence"],
            event_type=row["event_type"], stage=row["stage"], status=row["status"],
            label=row["label"], skill=row["skill"], tool=row["tool"],
            duration_ms=row["duration_ms"],
            created_at=TypeAdapter(datetime).validate_python(row["created_at"]),
        )

    def active(self) -> list[RegistryRun]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY created_at").fetchall()
        return [self._parse(row) for row in rows]

    def list(self, limit: int, cursor: str | None = None) -> tuple[list[RegistryRun], str | None]:
        parameters: list[object] = []
        where = ""
        if cursor:
            try:
                decoded = urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
                created_at, run_id = decoded.split("\0", 1)
            except (ValueError, UnicodeError) as exc:
                raise WebError(422, "validation_error") from exc
            where = "WHERE (created_at < ? OR (created_at = ? AND id < ?))"
            parameters.extend((created_at, created_at, run_id))
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM runs {where} ORDER BY created_at DESC,id DESC LIMIT ?",
                (*parameters, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            token = f"{page[-1]['created_at']}\0{page[-1]['id']}".encode("utf-8")
            next_cursor = urlsafe_b64encode(token).decode("ascii")
        return [self._parse(row) for row in page], next_cursor

    def seed_demo(
        self,
        *,
        run_id: str,
        artifact_run_id: str,
        request: CreateRunRequest,
        started_at: datetime,
        finished_at: datetime,
        status: ApiStatus,
    ) -> None:
        if status not in ("completed", "completed_with_degradation"):
            raise ValueError("bundled demo must be successful and terminal")
        now = finished_at.isoformat()
        with self._connect() as db:
            existing = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if existing is not None:
                row = self._parse(existing)
                if row.origin != "bundled_demo" or row.artifact_run_id != artifact_run_id:
                    raise RuntimeError("bundled demo registry row does not match packaged artifacts")
                db.execute(
                    """UPDATE runs SET status=?,phase='terminal',request_json=?,progress_json=?,
                       error_json=NULL,started_at=?,finished_at=?,updated_at=? WHERE id=?""",
                    (
                        status, request.model_dump_json(),
                        RunProgress(completed_units=request.paper_limit, total_units=request.paper_limit).model_dump_json(),
                        started_at.isoformat(), now, now, run_id,
                    ),
                )
                return
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, artifact_run_id, "bundled_demo", status, "terminal",
                    request.model_dump_json(),
                    RunProgress(completed_units=request.paper_limit, total_units=request.paper_limit).model_dump_json(),
                    None, started_at.isoformat(), started_at.isoformat(), now, now,
                ),
            )

    def _update(self, run_id: str, **values: object) -> None:
        values["updated_at"] = utc_now().isoformat()
        assignments = ",".join(f"{name}=?" for name in values)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"UPDATE runs SET {assignments} WHERE id=?", (*values.values(), run_id))
            db.commit()

    @staticmethod
    def _parse(row: sqlite3.Row) -> RegistryRun:
        return RegistryRun(
            id=row["id"], artifact_run_id=row["artifact_run_id"], origin=row["origin"],
            status=row["status"], phase=row["phase"],
            request=CreateRunRequest.model_validate_json(row["request_json"]),
            progress=RunProgress.model_validate_json(row["progress_json"]),
            error=RegistryError.model_validate_json(row["error_json"]) if row["error_json"] else None,
            created_at=TypeAdapter(datetime).validate_python(row["created_at"]),
            started_at=TypeAdapter(datetime).validate_python(row["started_at"]) if row["started_at"] else None,
            finished_at=TypeAdapter(datetime).validate_python(row["finished_at"]) if row["finished_at"] else None,
            updated_at=TypeAdapter(datetime).validate_python(row["updated_at"]),
        )

    @staticmethod
    def _run_exists(db: sqlite3.Connection, run_id: str) -> bool:
        return (
            db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is not None
            or db.execute(
                "SELECT 1 FROM techscout_runs WHERE id=?", (run_id,)
            ).fetchone() is not None
        )

    @staticmethod
    def _parse_techscout(row: sqlite3.Row) -> TechScoutRegistryRun:
        return TechScoutRegistryRun(
            id=row["id"], status=row["status"], stage=row["stage"],
            request=TechScoutCreateRunRequest.model_validate_json(row["request_json"]),
            progress=TechScoutProgress.model_validate_json(row["progress_json"]),
            projection_path=row["projection_path"],
            created_at=TypeAdapter(datetime).validate_python(row["created_at"]),
            started_at=TypeAdapter(datetime).validate_python(row["started_at"]) if row["started_at"] else None,
            finished_at=TypeAdapter(datetime).validate_python(row["finished_at"]) if row["finished_at"] else None,
            updated_at=TypeAdapter(datetime).validate_python(row["updated_at"]),
            idempotency_key=row["idempotency_key"], request_hash=row["request_hash"],
            deadline_at=TypeAdapter(datetime).validate_python(row["deadline_at"]),
            attempt_count=row["attempt_count"], max_attempts=row["max_attempts"],
            cancel_requested=bool(row["cancel_requested"]), worker_id=row["worker_id"],
            lease_token=row["lease_token"], fencing_token=row["fencing_token"],
            error_kind=row["error_kind"], error_code=row["error_code"],
        )

    @staticmethod
    def _techscout_owner_matches(
        row: sqlite3.Row,
        *,
        worker_id: str | None,
        lease_token: str | None,
        fencing_token: int | None,
    ) -> bool:
        return (
            worker_id is not None
            and lease_token is not None
            and fencing_token is not None
            and row["worker_id"] == worker_id
            and row["lease_token"] == lease_token
            and row["fencing_token"] == fencing_token
        )
