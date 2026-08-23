from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

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
    LiveEvidenceResearchService,
    hero_case_policy,
)
from paper_agent.techscout.tools.adapters import AdapterTimeout
from paper_agent.techscout.tools.contracts import (
    FetchOutput,
    GitHubInspectOutput,
    GitHubRelease,
    SearchHit,
    SearchOutput,
    SourceProvenance,
)


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _provenance(
    *,
    cache_status: CacheStatus = CacheStatus.MISS,
    provider: str = "deterministic-test-provider",
) -> SourceProvenance:
    return SourceProvenance(
        provider=provider,
        retrieved_at=NOW,
        snapshot_sha256="a" * 64,
        cache_status=cache_status,
        cache_fallback=cache_status is CacheStatus.STALE,
    )


class _Search:
    def __init__(self, *, cache_status: CacheStatus = CacheStatus.MISS) -> None:
        self.cache_status = cache_status

    def search(self, request):
        return SearchOutput(
            query=request.query,
            candidate_id=request.candidate_id,
            results=(
                SearchHit(
                    title="Filtering",
                    url="https://docs.example.com/filtering",
                    snippet="Filtering docs",
                    score=0.9,
                ),
            ),
            provenance=_provenance(cache_status=self.cache_status),
        )


class _Fetch:
    def __init__(
        self,
        *,
        cache_status: CacheStatus = CacheStatus.MISS,
        error: Exception | None = None,
        content: str = "metadata filtering persistence local Python 3.11",
    ) -> None:
        self.cache_status = cache_status
        self.error = error
        self.content = content

    def fetch(self, request):
        if self.error is not None:
            raise self.error
        return FetchOutput(
            url=request.url,
            candidate_id=request.candidate_id,
            media_type="text/plain",
            content=self.content,
            size_bytes=len(self.content.encode()),
            provenance=_provenance(cache_status=self.cache_status).model_copy(
                update={
                    "snapshot_sha256": hashlib.sha256(self.content.encode()).hexdigest()
                }
            ),
        )


class _GitHub:
    def __init__(
        self,
        *,
        cache_status: CacheStatus = CacheStatus.MISS,
        error: Exception | None = None,
    ) -> None:
        self.cache_status = cache_status
        self.error = error

    def inspect_repository(self, request):
        if self.error is not None:
            raise self.error
        return GitHubInspectOutput(
            candidate_id=request.candidate_id,
            repository_url=request.repository_url,
            default_branch="main",
            description="Local vector store",
            stars=10,
            archived=False,
            readme_excerpt="metadata filtering and persistence",
            releases=(),
            issues=(),
            provenance=_provenance(cache_status=self.cache_status),
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


def _request(*, version: str = "1.0") -> ResearchRequest:
    return ResearchRequest(
        run_id="run:live-evidence",
        question="Which candidate supports metadata filtering and persistence?",
        project_context="Python 3.11 local RAG",
        environment=EnvironmentSpec(
            python_version="3.11",
            operating_system="linux",
            deployment="local",
        ),
        hard_constraints=("metadata filtering", "persistence"),
        candidates=(
            Candidate(
                candidate_id="candidate:qdrant",
                name="Qdrant Local",
                requested_version=version,
            ),
            Candidate(candidate_id="candidate:chroma", name="Chroma"),
        ),
    )


def _policy(*, version: str = "1.0") -> CandidateSourcePolicy:
    return CandidateSourcePolicy(
        candidate_id="candidate:qdrant",
        version=version,
        official_domains=("docs.example.com",),
        official_queries=("Qdrant metadata filtering",),
        repository_url="https://github.com/qdrant/qdrant-client",
    )


def _service(*, search=None, fetch=None, github=None, stage_top_k=None):
    hybrid = HybridEvidenceRetriever(
        lexical_source=LexicalCandidateSource(),
        vector_source=_VectorSource(),
        requested_mode="hybrid",
        candidate_k=8,
        top_k=8,
        rrf_k=60,
    )
    return LiveEvidenceResearchService(
        search=search or _Search(),
        fetch=fetch or _Fetch(),
        github=github or _GitHub(),
        context_engine=ContextEngine(HybridContextRetriever(hybrid)),
        chunk_size_chars=60,
        stage_top_k=stage_top_k,
    )


def test_live_success_normalizes_candidate_evidence_with_hash_provenance() -> None:
    delivery = _service().research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    result = delivery.research
    assert result.state is AcquisitionState.LIVE
    assert result.candidate_id == "candidate:qdrant"
    assert result.version == "1.0"
    assert result.documents
    assert result.chunks
    assert result.evidence
    assert all(item.candidate_id == "candidate:qdrant" for item in result.documents)
    assert all(len(item.content_sha256) == 64 for item in result.documents)
    assert all(
        item.content_sha256 == hashlib.sha256(item.text.encode()).hexdigest()
        for item in result.chunks
    )
    assert all(attempt.fetched_at == NOW for attempt in result.attempts if attempt.available)
    assert delivery.context.candidate_id == "candidate:qdrant"


def test_search_summaries_are_discovery_only_and_urls_are_deduplicated() -> None:
    class DuplicateSearch(_Search):
        def search(self, request):
            return super().search(request).model_copy(
                update={
                    "results": (
                        SearchHit(
                            title="Search-only summary",
                            url="https://docs.example.com/filtering/?utm_source=search",
                            snippet="UNFETCHED_SEARCH_SUMMARY_CLAIM",
                        ),
                        SearchHit(
                            title="Canonical page",
                            url="https://DOCS.example.com:443/filtering",
                            snippet="duplicate discovery record",
                        ),
                    )
                }
            )

    class CountingFetch(_Fetch):
        calls = 0

        def fetch(self, request):
            self.calls += 1
            return super().fetch(request)

    fetch = CountingFetch()
    delivery = _service(search=DuplicateSearch(), fetch=fetch).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert fetch.calls == 1
    assert delivery.research.documents[0].url == "https://docs.example.com/filtering"
    assert all(
        "UNFETCHED_SEARCH_SUMMARY_CLAIM" not in item.claim
        for item in delivery.research.evidence
    )


def test_matching_github_release_is_ranked_with_version_and_source_type() -> None:
    class ReleasedGitHub(_GitHub):
        def inspect_repository(self, request):
            return super().inspect_repository(request).model_copy(
                update={
                    "releases": (
                        GitHubRelease(
                            tag="v1.0",
                            url=(
                                "https://github.com/qdrant/qdrant-client/"
                                "releases/tag/v1.0"
                            ),
                            published_at=NOW,
                        ),
                    )
                }
            )

    delivery = _service(github=ReleasedGitHub()).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    release = delivery.research.documents[0]
    assert release.source_type.value == "github_release"
    assert release.version == "v1.0"
    assert release.url.endswith("/releases/tag/v1.0")


def test_cache_only_sources_are_never_reported_as_live() -> None:
    delivery = _service(
        fetch=_Fetch(cache_status=CacheStatus.STALE),
        github=_GitHub(cache_status=CacheStatus.HIT),
    ).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert delivery.research.state is AcquisitionState.CACHE
    assert {attempt.state for attempt in delivery.research.attempts if attempt.available} == {
        AcquisitionState.CACHE
    }
    assert any(attempt.cache_fallback for attempt in delivery.research.attempts)


def test_sources_outside_freshness_window_are_not_materialized_as_facts() -> None:
    class OldFetch(_Fetch):
        def fetch(self, request):
            return super().fetch(request).model_copy(
                update={
                    "provenance": _provenance().model_copy(
                        update={"retrieved_at": NOW - timedelta(days=31)}
                    )
                }
            )

    delivery = _service(
        fetch=OldFetch(), github=_GitHub(error=AdapterTimeout("offline"))
    ).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert delivery.research.state is AcquisitionState.UNAVAILABLE
    assert delivery.research.documents == ()
    assert delivery.research.evidence == ()
    assert any(attempt.available for attempt in delivery.research.attempts)


def test_timeout_without_cache_is_explicitly_unavailable() -> None:
    delivery = _service(
        fetch=_Fetch(error=AdapterTimeout("offline")),
        github=_GitHub(error=AdapterTimeout("offline")),
    ).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert delivery.research.state is AcquisitionState.UNAVAILABLE
    assert delivery.research.documents == ()
    assert delivery.research.chunks == ()
    assert delivery.context.sources == ()
    assert all(not attempt.available for attempt in delivery.research.attempts)
    assert {attempt.failure_code.value for attempt in delivery.research.attempts} >= {
        "tool_timeout"
    }


def test_candidate_and_version_mismatch_fail_before_provider_access() -> None:
    service = _service()

    try:
        service.research(
            request=_request(version="2.0"),
            policy=_policy(version="1.0"),
            stage=ContextStage.RESEARCH,
            as_of=NOW,
        )
    except ValueError as exc:
        assert "version" in str(exc)
    else:  # pragma: no cover - proves the fail-closed boundary
        raise AssertionError("version mismatch was accepted")

    wrong_candidate = _policy().model_copy(update={"candidate_id": "candidate:other"})
    try:
        service.research(
            request=_request(),
            policy=wrong_candidate,
            stage=ContextStage.RESEARCH,
            as_of=NOW,
        )
    except ValueError as exc:
        assert "candidate" in str(exc)
    else:  # pragma: no cover - proves the fail-closed boundary
        raise AssertionError("unrelated candidate was accepted")


def test_cross_candidate_provider_output_is_rejected() -> None:
    class CrossCandidateSearch(_Search):
        def search(self, request):
            return super().search(request).model_copy(
                update={"candidate_id": "candidate:chroma"}
            )

    service = _service(search=CrossCandidateSearch())

    try:
        service.research(
            request=_request(),
            policy=_policy(),
            stage=ContextStage.RESEARCH,
            as_of=NOW,
        )
    except ValueError as exc:
        assert "candidate" in str(exc)
    else:  # pragma: no cover - proves the fail-closed boundary
        raise AssertionError("cross-candidate provider output was accepted")


def test_synthetic_provenance_is_unavailable_not_live() -> None:
    class SyntheticFetch(_Fetch):
        def fetch(self, request):
            return super().fetch(request).model_copy(
                update={
                    "provenance": _provenance(provider="frozen-synthetic-fixture")
                }
            )

    class SyntheticGitHub(_GitHub):
        def inspect_repository(self, request):
            return super().inspect_repository(request).model_copy(
                update={
                    "provenance": _provenance(provider="synthetic-demo")
                }
            )

    delivery = _service(
        fetch=SyntheticFetch(), github=SyntheticGitHub()
    ).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert delivery.research.state is AcquisitionState.UNAVAILABLE
    assert delivery.research.documents == ()
    assert all(not attempt.available for attempt in delivery.research.attempts)


def test_malformed_page_is_reported_unavailable_without_leaking_parser_error() -> None:
    class MalformedJsonFetch(_Fetch):
        def fetch(self, request):
            return FetchOutput(
                url=request.url,
                candidate_id=request.candidate_id,
                media_type="application/json",
                content="{malformed",
                size_bytes=10,
                provenance=_provenance(),
            )

    delivery = _service(
        fetch=MalformedJsonFetch(),
        github=_GitHub(error=AdapterTimeout("offline")),
    ).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert delivery.research.state is AcquisitionState.UNAVAILABLE
    assert {attempt.failure_code.value for attempt in delivery.research.attempts} == {
        "page_parsing_failed",
        "tool_timeout",
    }


def test_source_policy_blocks_search_hits_outside_candidate_domains() -> None:
    class OffDomainSearch(_Search):
        def search(self, request):
            return super().search(request).model_copy(
                update={
                    "results": (
                        SearchHit(
                            title="Untrusted mirror",
                            url="https://evil.example.net/qdrant",
                            snippet="not official",
                        ),
                    )
                }
            )

    class MustNotFetch(_Fetch):
        def fetch(self, request):  # pragma: no cover - must remain unreachable
            raise AssertionError("off-domain URL reached the fetch boundary")

    delivery = _service(
        search=OffDomainSearch(),
        fetch=MustNotFetch(),
        github=_GitHub(error=AdapterTimeout("offline")),
    ).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert delivery.research.state is AcquisitionState.UNAVAILABLE
    assert {attempt.failure_code.value for attempt in delivery.research.attempts} == {
        "unsafe_request",
        "tool_timeout",
    }


def test_stage_specific_top_k_is_applied_before_context_delivery() -> None:
    content = "\n\n".join(
        f"metadata filtering persistence section {index}" for index in range(6)
    )
    service = _service(
        fetch=_Fetch(content=content),
        stage_top_k={ContextStage.RESEARCH: 4, ContextStage.POC_PLANNING: 2},
    )

    research = service.research(
        request=_request(), policy=_policy(), stage=ContextStage.RESEARCH, as_of=NOW
    )
    poc = service.research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.POC_PLANNING,
        as_of=NOW,
    )

    assert len(research.context.chunks) == 4
    assert len(poc.context.chunks) == 2
    assert all(source.version is None for source in poc.context.sources)


def test_large_live_source_is_bounded_before_context_delivery() -> None:
    content = "\n\n".join(
        f"metadata filtering persistence section {index}" for index in range(60)
    )

    delivery = _service(fetch=_Fetch(content=content)).research(
        request=_request(),
        policy=_policy(),
        stage=ContextStage.RESEARCH,
        as_of=NOW,
    )

    assert len(delivery.research.evidence) > 50
    assert 1 <= len(delivery.context.chunks) <= 8


def test_hero_case_keeps_pgvector_research_only() -> None:
    assert hero_case_policy(
        Candidate(candidate_id="candidate:chroma", name="Chroma")
    ).research_only is False
    assert hero_case_policy(
        Candidate(candidate_id="candidate:qdrant", name="Qdrant Local")
    ).research_only is False
    assert hero_case_policy(
        Candidate(candidate_id="candidate:pgvector", name="pgvector")
    ).research_only is True
