from __future__ import annotations

import uuid
from pathlib import Path

from paper_agent.web.errors import WebError
from paper_agent.web.task_queue import QueueFullError
from paper_agent.web.event_cursor import decode_event_cursor, encode_event_cursor
from paper_agent.web.registry import RunRegistry, TechScoutRegistryRun
from paper_agent.web.techscout_api_models import (
    TechScoutApprovalProjection,
    TechScoutCandidateList,
    TechScoutCandidateProjection,
    TechScoutCreateRunRequest,
    TechScoutEvidenceList,
    TechScoutEvidenceProjection,
    TechScoutRecoveryProjection,
    TechScoutReportProjection,
    TechScoutRunDetail,
    TechScoutRunList,
    TechScoutRunSummary,
    TraceEvent,
    TracePage,
)
from paper_agent.web.techscout_execution import (
    TechScoutProjectionBundle,
    TechScoutSingleRunExecutor,
)
from paper_agent.web.techscout_fixtures import DETAIL, EVIDENCE, REPORT, SYNTHETIC_RUN_ID, TRACE


class TechScoutProjectionService:
    """API projections over durable queued runs plus the explicit Wave 1 fixture."""

    def __init__(
        self,
        registry: RunRegistry,
        executor: TechScoutSingleRunExecutor,
        output_root: Path,
        capacity: int,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.output_root = output_root.resolve()
        self.capacity = capacity

    def create(
        self,
        request: TechScoutCreateRunRequest,
        *,
        idempotency_key: str | None = None,
        rate_subject: str = "local",
    ) -> TechScoutRunSummary:
        if not self.executor.available:
            raise WebError(503, "executor_unavailable")
        if not self.executor.queue.allow(rate_subject):
            raise WebError(429, "rate_limited")
        row, created = self.registry.admit_techscout_idempotent(
            str(uuid.uuid4()), request, capacity=self.capacity,
            idempotency_key=idempotency_key,
        )
        if created:
            try:
                self.executor.submit(row.id)
            except QueueFullError as error:
                self.registry.terminal_techscout(
                    row.id, "failed", projection_path=None,
                    progress=row.progress.model_copy(update={"stage": "terminal"}),
                )
                raise WebError(503, "queue_full") from error
        return self._summary(row)

    def cancel(self, run_id: str) -> TechScoutRunDetail:
        self.registry.request_cancel_techscout(run_id)
        return self.detail(run_id)

    def ready(self) -> bool:
        return self.executor.available and self.registry.ready()

    def list(self) -> TechScoutRunList:
        live = [self._summary(row) for row in self.registry.list_techscout()]
        fixture = TechScoutRunSummary(**DETAIL.model_dump(exclude={
            "project_context", "environment", "hard_constraints", "candidates",
            "recovery", "approval", "issues",
        }))
        return TechScoutRunList(items=[*live, fixture])

    def detail(self, run_id: str) -> TechScoutRunDetail:
        if run_id == SYNTHETIC_RUN_ID:
            return DETAIL
        row = self.registry.get_techscout(run_id)
        bundle = self._bundle(row)
        if bundle is not None:
            return bundle.detail
        candidates = [
            TechScoutCandidateProjection(
                candidate_id=_candidate_id(item.name),
                name=item.name,
                support_level="v1_supported" if _candidate_supported(item.name) else "research_only",
                requested_version=item.requested_version,
                compatibility="unknown",
                verdict="insufficient_evidence",
                evidence_ids=[],
            )
            for item in row.request.candidates
        ]
        return TechScoutRunDetail(
            **self._summary(row).model_dump(),
            project_context=row.request.project_context,
            environment=row.request.environment,
            hard_constraints=row.request.hard_constraints,
            candidates=candidates,
            recovery=TechScoutRecoveryProjection(
                attempted=False, outcome="not_needed", attempts_used=0,
            ),
            approval=TechScoutApprovalProjection(required=False, status="not_required"),
            issues=[],
        )

    def report(self, run_id: str) -> TechScoutReportProjection:
        if run_id == SYNTHETIC_RUN_ID:
            return REPORT
        row = self.registry.get_techscout(run_id)
        bundle = self._bundle(row)
        if bundle is None or bundle.report is None:
            raise WebError(404, "report_unavailable")
        return bundle.report

    def candidates(self, run_id: str) -> TechScoutCandidateList:
        return TechScoutCandidateList(items=self.detail(run_id).candidates)

    def candidate(self, run_id: str, candidate_id: str) -> TechScoutCandidateProjection:
        item = next(
            (candidate for candidate in self.detail(run_id).candidates if candidate.candidate_id == candidate_id),
            None,
        )
        if item is None:
            raise WebError(404, "candidate_not_found")
        return item

    def evidence(self, run_id: str) -> TechScoutEvidenceList:
        if run_id == SYNTHETIC_RUN_ID:
            return TechScoutEvidenceList(items=EVIDENCE)
        row = self.registry.get_techscout(run_id)
        bundle = self._bundle(row)
        return TechScoutEvidenceList(items=bundle.evidence if bundle else [])

    def evidence_one(self, run_id: str, evidence_id: str) -> TechScoutEvidenceProjection:
        item = next(
            (evidence for evidence in self.evidence(run_id).items if evidence.evidence_id == evidence_id),
            None,
        )
        if item is None:
            raise WebError(404, "evidence_not_found")
        return item

    def trace(self, run_id: str, limit: int, cursor: str | None) -> TracePage:
        if run_id == SYNTHETIC_RUN_ID:
            events = TRACE.items
            after = decode_event_cursor(cursor)
            remaining = events[after:]
            page = remaining[:limit]
            next_cursor = page[-1].cursor if len(remaining) > limit and page else None
            return TracePage(items=page, next_cursor=next_cursor)
        self.registry.get_techscout(run_id)
        events, has_more = self.registry.list_events(
            run_id, after_sequence=decode_event_cursor(cursor), limit=limit,
        )
        items = [
            TraceEvent(
                cursor=encode_event_cursor(event.sequence),
                event_type=event.event_type,
                stage=event.stage,
                status=event.status,
                label=event.label,
                skill=event.skill,
                tool=event.tool,
                duration_ms=event.duration_ms,
                created_at=event.created_at,
            )
            for event in events
        ]
        return TracePage(
            items=items,
            next_cursor=items[-1].cursor if has_more and items else None,
        )

    @staticmethod
    def _summary(row: TechScoutRegistryRun) -> TechScoutRunSummary:
        return TechScoutRunSummary(
            id=row.id,
            status=row.status,
            synthetic=True,
            fixture_name="wave2_fast_demo",
            question=row.request.question,
            mode=row.request.mode,
            progress=row.progress,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )

    def _bundle(self, row: TechScoutRegistryRun) -> TechScoutProjectionBundle | None:
        if row.projection_path is None:
            return None
        path = (self.output_root / row.projection_path).resolve()
        try:
            path.relative_to(self.output_root)
        except ValueError as exc:
            raise WebError(404, "run_not_found") from exc
        if not path.is_file():
            raise WebError(404, "run_not_found")
        return TechScoutProjectionBundle.model_validate_json(path.read_text(encoding="utf-8"))


def _candidate_id(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "candidate"


def _candidate_supported(name: str) -> bool:
    import re
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return normalized in {"chroma", "chromadb", "qdrant", "qdrant local", "qdrant client"}
