from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator
from typing_extensions import Self

from paper_agent.techscout.context import ContextPacket
from paper_agent.techscout.errors import FailureCode, StableId
from paper_agent.techscout.models import (
    CandidateEvidence,
    EvidenceKind,
    HttpsUrl,
    NonEmptyStr,
    Sha256,
    SourceChunk,
    SourceDocument,
    SourceType,
    TechScoutModel,
)


class AcquisitionState(str, Enum):
    LIVE = "live"
    CACHE = "cache"
    UNAVAILABLE = "unavailable"


class ResearchProvider(str, Enum):
    TAVILY = "tavily"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    GITHUB = "github"


class PlannedResearchQuery(TechScoutModel):
    query_id: StableId
    question: NonEmptyStr
    providers: tuple[ResearchProvider, ...] = Field(min_length=1, max_length=3)
    official_domains: tuple[NonEmptyStr, ...] = Field(default=(), max_length=5)
    repository_url: HttpsUrl | None = None

    @model_validator(mode="after")
    def provider_routes_are_configured(self) -> Self:
        if len(self.providers) != len(set(self.providers)):
            raise ValueError("research query providers must be unique")
        if ResearchProvider.TAVILY in self.providers and not self.official_domains:
            raise ValueError("Tavily discovery requires official domains")
        if (
            ResearchProvider.OFFICIAL_DOCUMENTATION in self.providers
            and ResearchProvider.TAVILY not in self.providers
        ):
            raise ValueError("official fetch requires a discovery provider")
        if ResearchProvider.GITHUB in self.providers and self.repository_url is None:
            raise ValueError("GitHub research requires a repository URL")
        return self


class ResearchQueryPlan(TechScoutModel):
    plan_id: StableId
    candidate_id: StableId
    target_version: NonEmptyStr | None = None
    queries: tuple[PlannedResearchQuery, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def query_ids_are_unique_and_scoped(self) -> Self:
        identifiers = [item.query_id for item in self.queries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("research query identifiers must be unique")
        return self


class SourceDiscovery(TechScoutModel):
    """Search-index output retained for discovery, never fact authority."""

    discovery_id: StableId
    query_id: StableId
    canonical_url: HttpsUrl
    title: NonEmptyStr
    search_summary: NonEmptyStr
    provider: NonEmptyStr
    state: AcquisitionState
    discovered_at: datetime
    snapshot_sha256: Sha256
    cache_fallback: bool = False

    @field_validator("discovered_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("discovery timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def discovery_must_be_available(self) -> Self:
        if self.state is AcquisitionState.UNAVAILABLE:
            raise ValueError("an unavailable search cannot produce a discovery")
        return self


class FactStance(str, Enum):
    AFFIRMS = "affirms"
    DENIES = "denies"


class FactResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class FactDraft(TechScoutModel):
    """Exact source statement proposed by an injected extraction adapter."""

    statement: NonEmptyStr
    stance: FactStance


class RetrievedFact(TechScoutModel):
    fact_id: StableId
    query_id: StableId
    statement: NonEmptyStr
    stance: FactStance
    evidence_id: StableId
    source_ids: tuple[StableId, ...] = Field(min_length=1)
    chunk_ids: tuple[StableId, ...] = Field(min_length=1)


class FactFinding(TechScoutModel):
    query_id: StableId
    question: NonEmptyStr
    status: FactResolutionStatus
    facts: tuple[RetrievedFact, ...] = Field(max_length=200)
    reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def status_matches_fact_positions(self) -> Self:
        stances = {item.stance for item in self.facts}
        if self.status is FactResolutionStatus.UNKNOWN:
            if self.facts or self.reason is None:
                raise ValueError("unknown finding requires a reason and no facts")
        elif not self.facts or self.reason is not None:
            raise ValueError("resolved or conflicting finding requires facts only")
        elif self.status is FactResolutionStatus.CONFLICT and stances != {
            FactStance.AFFIRMS,
            FactStance.DENIES,
        }:
            raise ValueError("conflict requires both affirming and denying facts")
        elif self.status is FactResolutionStatus.RESOLVED and len(stances) != 1:
            raise ValueError("resolved finding requires one consistent stance")
        return self


class CandidateSourcePolicy(TechScoutModel):
    candidate_id: StableId
    version: NonEmptyStr | None = None
    official_domains: tuple[NonEmptyStr, ...] = Field(max_length=5)
    official_queries: tuple[NonEmptyStr, ...] = Field(max_length=2)
    repository_url: HttpsUrl | None = None
    research_only: bool = False

    @model_validator(mode="after")
    def require_a_source(self) -> Self:
        if not self.official_queries and self.repository_url is None:
            raise ValueError("candidate policy requires an official or GitHub source")
        if self.official_queries and not self.official_domains:
            raise ValueError("official queries require an allowed domain")
        return self


class SourceAttempt(TechScoutModel):
    operation: NonEmptyStr
    reference: NonEmptyStr
    source_type: SourceType | None = None
    state: AcquisitionState
    provider: NonEmptyStr | None = None
    fetched_at: datetime | None = None
    content_sha256: Sha256 | None = None
    cache_fallback: bool = False
    failure_code: FailureCode | None = None

    @property
    def available(self) -> bool:
        return self.state is not AcquisitionState.UNAVAILABLE

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.available:
            if (
                self.source_type is None
                or self.provider is None
                or self.fetched_at is None
                or self.content_sha256 is None
                or self.failure_code is not None
            ):
                raise ValueError("available source requires complete provenance")
        elif self.failure_code is None:
            raise ValueError("unavailable source requires a failure code")
        return self


class CandidateResearchResult(TechScoutModel):
    candidate_id: StableId
    version: NonEmptyStr | None = None
    state: AcquisitionState
    research_only: bool
    query_plan: ResearchQueryPlan
    discoveries: tuple[SourceDiscovery, ...] = Field(max_length=40)
    documents: tuple[SourceDocument, ...] = Field(max_length=5)
    chunks: tuple[SourceChunk, ...] = Field(max_length=200)
    evidence: tuple[CandidateEvidence, ...] = Field(max_length=200)
    fact_findings: tuple[FactFinding, ...] = Field(min_length=1, max_length=8)
    attempts: tuple[SourceAttempt, ...] = Field(max_length=16)

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        if self.query_plan.candidate_id != self.candidate_id:
            raise ValueError("research query plan belongs to another candidate")
        if self.query_plan.target_version != self.version:
            raise ValueError("research query plan version does not match result")
        query_ids = {item.query_id for item in self.query_plan.queries}
        if any(item.query_id not in query_ids for item in self.discoveries):
            raise ValueError("source discovery references an unknown query")
        finding_ids = [item.query_id for item in self.fact_findings]
        if set(finding_ids) != query_ids or len(finding_ids) != len(query_ids):
            raise ValueError("research result requires one finding per query")
        if any(item.candidate_id != self.candidate_id for item in self.documents):
            raise ValueError("research result contains an unrelated candidate source")
        source_ids = {item.source_id for item in self.documents}
        chunk_ids = {item.chunk_id for item in self.chunks}
        if any(item.source_id not in source_ids for item in self.chunks):
            raise ValueError("research chunk references an unknown source")
        if any(item.candidate_id != self.candidate_id for item in self.evidence):
            raise ValueError("research result contains unrelated candidate evidence")
        if any(not set(item.source_ids).issubset(source_ids) for item in self.evidence):
            raise ValueError("research evidence references an unknown source")
        if any(not set(item.chunk_ids).issubset(chunk_ids) for item in self.evidence):
            raise ValueError("research evidence references an unknown chunk")
        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("research evidence identifiers must be unique")
        for finding in self.fact_findings:
            for fact in finding.facts:
                if fact.query_id != finding.query_id:
                    raise ValueError("retrieved fact belongs to another finding")
                evidence_item = evidence_by_id.get(fact.evidence_id)
                if evidence_item is None:
                    raise ValueError("retrieved fact references unknown evidence")
                if (
                    evidence_item.kind is not EvidenceKind.RETRIEVED_FACT
                    or evidence_item.claim != fact.statement
                    or set(evidence_item.source_ids) != set(fact.source_ids)
                    or set(evidence_item.chunk_ids) != set(fact.chunk_ids)
                ):
                    raise ValueError("retrieved fact does not match its evidence")
        if self.state is AcquisitionState.UNAVAILABLE and self.documents:
            raise ValueError("unavailable research cannot contain documents")
        if self.state is not AcquisitionState.UNAVAILABLE and not self.documents:
            raise ValueError("available research requires at least one document")
        return self


class ResearchDelivery(TechScoutModel):
    research: CandidateResearchResult
    context: ContextPacket
