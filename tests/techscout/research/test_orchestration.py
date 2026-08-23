from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from paper_agent.evidence.hybrid import HybridEvidenceRetriever
from paper_agent.evidence.models import RetrievalCandidate
from paper_agent.evidence.retriever import LexicalCandidateSource
from paper_agent.techscout.context import ContextEngine, ContextStage, HybridContextRetriever
from paper_agent.techscout.models import (
    CacheStatus,
    Candidate,
    EnvironmentSpec,
    ResearchRequest,
)
from paper_agent.techscout.research import (
    AcquisitionState,
    CandidateSourcePolicy,
    FactDraft,
    FactResolutionStatus,
    FactStance,
    LiveEvidenceResearchService,
    ResearchProvider,
)
from paper_agent.techscout.tools.adapters import AdapterTimeout
from paper_agent.techscout.tools.contracts import (
    FetchOutput,
    GitHubInspectOutput,
    GitHubIssue,
    GitHubRelease,
    SearchHit,
    SearchOutput,
    SourceProvenance,
)


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)
FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "research-intelligence-multisource.json"
)


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _provenance(
    content: str,
    *,
    provider: str,
    cache_status: CacheStatus = CacheStatus.MISS,
    retrieved_at: datetime = NOW,
) -> SourceProvenance:
    return SourceProvenance(
        provider=provider,
        retrieved_at=retrieved_at,
        snapshot_sha256=hashlib.sha256(content.encode()).hexdigest(),
        cache_status=cache_status,
        cache_fallback=cache_status is CacheStatus.STALE,
    )


class _VectorSource:
    def retrieve(self, question, chunks, limit):
        return [
            RetrievalCandidate(
                chunk_id=chunk.chunk_id,
                paper_id=chunk.paper_id,
                text=chunk.text,
                retrieval_sources=("vector",),
                vector_score=1.0 - (rank / 100),
                vector_rank=rank,
            )
            for rank, chunk in enumerate(chunks[:limit], start=1)
        ]


def _context_engine() -> ContextEngine:
    hybrid = HybridEvidenceRetriever(
        lexical_source=LexicalCandidateSource(),
        vector_source=_VectorSource(),
        requested_mode="hybrid",
        candidate_k=8,
        top_k=8,
        rrf_k=60,
    )
    return ContextEngine(HybridContextRetriever(hybrid))


def _request() -> ResearchRequest:
    return ResearchRequest(
        run_id="run:research-intelligence",
        question="Can Qdrant satisfy the required local RAG capabilities?",
        project_context="Python 3.11 local RAG",
        environment=EnvironmentSpec(
            python_version="3.11",
            operating_system="linux",
            deployment="local",
        ),
        hard_constraints=("metadata filtering", "backup API"),
        candidates=(
            Candidate(
                candidate_id="candidate:qdrant",
                name="Qdrant Local",
                requested_version="1.0",
            ),
        ),
    )


def _policy(data: dict[str, object]) -> CandidateSourcePolicy:
    return CandidateSourcePolicy(
        candidate_id=str(data["candidate_id"]),
        version=str(data["version"]),
        official_domains=("docs.example.com",),
        official_queries=tuple(data["queries"]),
        repository_url="https://github.com/qdrant/qdrant-client",
    )


class _TavilyFake:
    def __init__(
        self,
        data: dict[str, object],
        events: list[str],
        *,
        cache_status: CacheStatus = CacheStatus.MISS,
    ) -> None:
        self._data = data
        self._events = events
        self._cache_status = cache_status

    def search(self, request):
        self._events.append(f"tavily:{request.query}")
        raw_results = self._data["search_results"][request.query]
        results = tuple(SearchHit(**item) for item in raw_results)
        serialized = json.dumps(raw_results, sort_keys=True)
        return SearchOutput(
            query=request.query,
            candidate_id=request.candidate_id,
            results=results,
            provenance=_provenance(
                serialized,
                provider="tavily-fake",
                cache_status=self._cache_status,
            ),
        )


class _OfficialDocsFake:
    def __init__(
        self,
        data: dict[str, object],
        events: list[str],
        *,
        cache_status: CacheStatus = CacheStatus.MISS,
        retrieved_at: datetime = NOW,
    ) -> None:
        self._data = data
        self._events = events
        self._cache_status = cache_status
        self._retrieved_at = retrieved_at

    def fetch(self, request):
        self._events.append(f"official:{request.url}")
        content = self._data["official_pages"][request.url]
        return FetchOutput(
            url=request.url,
            candidate_id=request.candidate_id,
            media_type="text/plain",
            content=content,
            size_bytes=len(content.encode()),
            provenance=_provenance(
                content,
                provider="official-docs-fake",
                cache_status=self._cache_status,
                retrieved_at=self._retrieved_at,
            ),
        )


class _GitHubFake:
    def __init__(
        self,
        data: dict[str, object],
        events: list[str],
        *,
        error: Exception | None = None,
        cache_status: CacheStatus = CacheStatus.MISS,
    ) -> None:
        self._data = data
        self._events = events
        self._error = error
        self._cache_status = cache_status

    def inspect_repository(self, request):
        self._events.append("github")
        if self._error is not None:
            raise self._error
        github = self._data["github"]
        releases = tuple(
            GitHubRelease(
                tag=item["tag"],
                url=item["url"],
                published_at=datetime.fromisoformat(item["published_at"]),
            )
            for item in github["releases"]
        )
        payload = json.dumps(github, sort_keys=True)
        return GitHubInspectOutput(
            candidate_id=request.candidate_id,
            repository_url=github["repository_url"],
            default_branch="main",
            description=github["description"],
            stars=100,
            archived=False,
            readme_excerpt=github["readme_excerpt"],
            releases=releases,
            issues=(
                GitHubIssue(
                    number=9,
                    title="Backup API is supported",
                    state="open",
                    url="https://github.com/qdrant/qdrant-client/issues/9",
                ),
            ),
            provenance=_provenance(
                payload,
                provider="github-fake",
                cache_status=self._cache_status,
            ),
        )


def _service(
    search,
    fetch,
    github,
    *,
    fact_extractor=None,
) -> LiveEvidenceResearchService:
    return LiveEvidenceResearchService(
        search=search,
        fetch=fetch,
        github=github,
        context_engine=_context_engine(),
        chunk_size_chars=240,
        fact_extractor=fact_extractor,
    )


def test_multisource_tracer_bullet_plans_discovers_and_resolves_truthfully() -> None:
    data = _fixture()
    events: list[str] = []
    delivery = _service(
        _TavilyFake(data, events),
        _OfficialDocsFake(data, events),
        _GitHubFake(data, events),
    ).research(
        request=_request(),
        policy=_policy(data),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    result = delivery.research
    assert result.query_plan.candidate_id == "candidate:qdrant"
    assert result.query_plan.target_version == "1.0"
    assert tuple(query.question for query in result.query_plan.queries) == tuple(
        data["queries"]
    )
    assert all(
        query.providers
        == (
            ResearchProvider.TAVILY,
            ResearchProvider.OFFICIAL_DOCUMENTATION,
            ResearchProvider.GITHUB,
        )
        for query in result.query_plan.queries
    )

    assert events[-1] == "github"
    assert all(not event.startswith("github") for event in events[:-1])
    assert len(result.discoveries) == 1
    assert result.discoveries[0].canonical_url == "https://docs.example.com/filtering"
    assert result.discoveries[0].search_summary.startswith("SEARCH_ONLY:")
    assert all(
        "SEARCH_ONLY:" not in evidence.claim for evidence in result.evidence
    )

    findings = {item.question: item for item in result.fact_findings}
    filtering = findings["Qdrant metadata filtering in version 1.0"]
    assert filtering.status is FactResolutionStatus.CONFLICT
    assert {fact.stance for fact in filtering.facts} == {
        FactStance.AFFIRMS,
        FactStance.DENIES,
    }
    assert all(fact.source_ids for fact in filtering.facts)
    assert all(fact.evidence_id for fact in filtering.facts)
    assert findings["Qdrant backup API in version 1.0"].status is (
        FactResolutionStatus.UNKNOWN
    )
    assert findings["Qdrant backup API in version 1.0"].facts == ()

    assert result.documents[0].source_type.value == "official_documentation"
    assert all(document.version != "v2.0" for document in result.documents)


def test_cached_official_fact_survives_github_degradation_without_becoming_live() -> None:
    data = _fixture()
    events: list[str] = []
    delivery = _service(
        _TavilyFake(data, events, cache_status=CacheStatus.STALE),
        _OfficialDocsFake(data, events, cache_status=CacheStatus.STALE),
        _GitHubFake(data, events, error=AdapterTimeout("offline")),
    ).research(
        request=_request(),
        policy=_policy(data),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    result = delivery.research
    assert result.state is AcquisitionState.CACHE
    filtering = next(
        item
        for item in result.fact_findings
        if item.question == "Qdrant metadata filtering in version 1.0"
    )
    assert filtering.status is FactResolutionStatus.RESOLVED
    assert {fact.stance for fact in filtering.facts} == {FactStance.AFFIRMS}
    assert result.discoveries[0].state is AcquisitionState.CACHE
    assert result.discoveries[0].cache_fallback is True
    assert any(
        attempt.operation == "github"
        and attempt.state is AcquisitionState.UNAVAILABLE
        for attempt in result.attempts
    )


def test_stale_official_page_and_unavailable_github_produce_unknown() -> None:
    data = _fixture()
    events: list[str] = []
    delivery = _service(
        _TavilyFake(data, events),
        _OfficialDocsFake(
            data,
            events,
            retrieved_at=NOW - timedelta(days=31),
        ),
        _GitHubFake(data, events, error=AdapterTimeout("offline")),
    ).research(
        request=_request(),
        policy=_policy(data),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert delivery.research.state is AcquisitionState.UNAVAILABLE
    assert delivery.research.documents == ()
    assert delivery.research.evidence == ()
    assert all(
        finding.status is FactResolutionStatus.UNKNOWN
        for finding in delivery.research.fact_findings
    )


def test_unselected_live_stale_source_cannot_promote_cached_authority_to_live() -> None:
    data = _fixture()
    events: list[str] = []
    delivery = _service(
        _TavilyFake(data, events),
        _OfficialDocsFake(
            data,
            events,
            retrieved_at=NOW - timedelta(days=31),
        ),
        _GitHubFake(data, events, cache_status=CacheStatus.HIT),
    ).research(
        request=_request(),
        policy=_policy(data),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert delivery.research.state is AcquisitionState.CACHE
    assert {
        document.source_type.value for document in delivery.research.documents
    } == {"github_repository"}


def test_fact_adapter_cannot_promote_search_summary_or_fabricated_text() -> None:
    class FabricatingExtractor:
        def extract(self, *, query, excerpt):
            return (
                FactDraft(
                    statement="SEARCH_ONLY: filtering is always available",
                    stance=FactStance.AFFIRMS,
                ),
            )

    data = _fixture()
    events: list[str] = []
    service = _service(
        _TavilyFake(data, events),
        _OfficialDocsFake(data, events),
        _GitHubFake(data, events),
        fact_extractor=FabricatingExtractor(),
    )

    with pytest.raises(ValueError, match="exact source statement"):
        service.research(
            request=_request(),
            policy=_policy(data),
            stage=ContextStage.RESEARCH,
            as_of=NOW,
        )
