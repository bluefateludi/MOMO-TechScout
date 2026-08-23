from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from pydantic import UUID4

from paper_agent.web.api_models import ErrorResponse
from paper_agent.web.techscout_api_models import (
    TechScoutCandidateList,
    TechScoutCandidateProjection,
    TechScoutCreateRunRequest,
    TechScoutEvidenceList,
    TechScoutEvidenceProjection,
    TechScoutReportProjection,
    TechScoutRunDetail,
    TechScoutRunList,
    TechScoutRunSummary,
    TracePage,
)
from paper_agent.web.techscout_service import TechScoutProjectionService


router = APIRouter(prefix="/api/v2/runs", tags=["techscout-runs"])
ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


def _service(request: Request) -> TechScoutProjectionService:
    return request.app.state.techscout_service


@router.post("", response_model=TechScoutRunSummary, status_code=202, responses=ERRORS)
def create_run(
    body: TechScoutCreateRunRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
) -> TechScoutRunSummary:
    subject = request.client.host if request.client else "local"
    return _service(request).create(
        body, idempotency_key=idempotency_key, rate_subject=subject,
    )


@router.post("/{run_id}/cancel", response_model=TechScoutRunDetail, responses=ERRORS)
def cancel_run(run_id: UUID4, request: Request) -> TechScoutRunDetail:
    return _service(request).cancel(str(run_id))


@router.get("", response_model=TechScoutRunList, responses=ERRORS)
def list_runs(request: Request) -> TechScoutRunList:
    return _service(request).list()


@router.get("/{run_id}", response_model=TechScoutRunDetail, responses=ERRORS)
def get_run(run_id: UUID4, request: Request) -> TechScoutRunDetail:
    return _service(request).detail(str(run_id))


@router.get("/{run_id}/report", response_model=TechScoutReportProjection, responses=ERRORS)
def get_report(run_id: UUID4, request: Request) -> TechScoutReportProjection:
    return _service(request).report(str(run_id))


@router.get("/{run_id}/candidates", response_model=TechScoutCandidateList, responses=ERRORS)
def get_candidates(run_id: UUID4, request: Request) -> TechScoutCandidateList:
    return _service(request).candidates(str(run_id))


@router.get(
    "/{run_id}/candidates/{candidate_id}", response_model=TechScoutCandidateProjection,
    responses=ERRORS,
)
def get_candidate(run_id: UUID4, candidate_id: str, request: Request) -> TechScoutCandidateProjection:
    return _service(request).candidate(str(run_id), candidate_id)


@router.get("/{run_id}/evidence", response_model=TechScoutEvidenceList, responses=ERRORS)
def get_evidence(run_id: UUID4, request: Request) -> TechScoutEvidenceList:
    return _service(request).evidence(str(run_id))


@router.get(
    "/{run_id}/evidence/{evidence_id}", response_model=TechScoutEvidenceProjection,
    responses=ERRORS,
)
def get_evidence_item(run_id: UUID4, evidence_id: str, request: Request) -> TechScoutEvidenceProjection:
    return _service(request).evidence_one(str(run_id), evidence_id)


@router.get("/{run_id}/trace", response_model=TracePage, responses=ERRORS)
def get_trace(
    run_id: UUID4,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=128),
) -> TracePage:
    return _service(request).trace(str(run_id), limit, cursor)
