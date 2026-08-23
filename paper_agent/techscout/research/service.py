from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime, timedelta

from pydantic import ValidationError

from paper_agent.techscout.context import (
    CandidateContextData,
    ContextEngine,
    ContextStage,
)
from paper_agent.techscout.errors import FailureCode
from paper_agent.techscout.models import (
    CacheStatus,
    CandidateEvidence,
    EvidenceKind,
    ResearchRequest,
    SourceChunk,
    SourceDocument,
    SourceType,
)
from paper_agent.techscout.tools.adapters import (
    AdapterError,
    AdapterRateLimited,
    AdapterTimeout,
    FetchAdapter,
    GitHubAdapter,
    ResponseTooLarge,
    SearchAdapter,
    UnsafeUrl,
    UrlPolicy,
)
from paper_agent.techscout.tools.contracts import (
    FetchInput,
    GitHubInspectInput,
    GitHubInspectOutput,
    SearchInput,
    SourceProvenance,
)

from .models import (
    AcquisitionState,
    CandidateResearchResult,
    CandidateSourcePolicy,
    ResearchDelivery,
    SourceAttempt,
)
from .normalization import (
    ClaimBoundary,
    ContentOrigin,
    SourceCandidate,
    SourceNormalizationPolicy,
    canonicalize_source_url,
    normalize_and_rank_sources,
)


class _RejectedProvenance(ValueError):
    pass


class _NormalizationError(ValueError):
    pass


class LiveEvidenceResearchService:
    """Acquire bounded official evidence and feed candidate-scoped context packets."""

    def __init__(
        self,
        *,
        search: SearchAdapter,
        fetch: FetchAdapter,
        github: GitHubAdapter,
        context_engine: ContextEngine,
        max_sources: int = 5,
        chunk_size_chars: int = 1_200,
        max_source_age: timedelta = timedelta(days=30),
        stage_top_k: Mapping[ContextStage, int] | None = None,
    ) -> None:
        if not 1 <= max_sources <= 5:
            raise ValueError("research max_sources must be between 1 and 5")
        if chunk_size_chars < 32:
            raise ValueError("research chunk_size_chars must be at least 32")
        if max_source_age.total_seconds() <= 0:
            raise ValueError("research max_source_age must be positive")
        configured_top_k = dict(
            stage_top_k
            or {
                ContextStage.RESEARCH: 5,
                ContextStage.POC_PLANNING: 3,
                ContextStage.VALIDATION: 5,
                ContextStage.REPORTING: 5,
            }
        )
        if any(value < 1 or value > 8 for value in configured_top_k.values()):
            raise ValueError("stage top_k must be between 1 and 8")
        self._search = search
        self._fetch = fetch
        self._github = github
        self._context_engine = context_engine
        self._max_sources = max_sources
        self._chunk_size = chunk_size_chars
        self._max_source_age = max_source_age
        self._stage_top_k = configured_top_k

    def research(
        self,
        *,
        request: ResearchRequest,
        policy: CandidateSourcePolicy,
        stage: ContextStage,
        as_of: datetime,
    ) -> ResearchDelivery:
        if stage is ContextStage.INTAKE_PLANNING:
            raise ValueError("live evidence is not loaded for intake planning")
        candidate = self._candidate(request, policy.candidate_id)
        expected_version = candidate.resolved_version or candidate.requested_version
        if expected_version is not None and policy.version != expected_version:
            raise ValueError("candidate source policy version does not match request")
        result = self._acquire(policy, as_of=as_of)
        context = CandidateContextData(
            candidate_id=result.candidate_id,
            documents=result.documents,
            chunks=result.chunks,
            evidence=result.evidence[:50],
        )
        packet = self._context_engine.build(
            packet_id=f"context:{request.run_id}:{stage.value}:{candidate.candidate_id}",
            stage=stage,
            request=request,
            candidate_context=context,
            as_of=as_of,
            candidate_version=policy.version,
            top_k=self._stage_top_k.get(stage, 5),
        )
        return ResearchDelivery(research=result, context=packet)

    def _acquire(
        self,
        policy: CandidateSourcePolicy,
        *,
        as_of: datetime,
    ) -> CandidateResearchResult:
        documents: list[SourceDocument] = []
        chunks: list[SourceChunk] = []
        evidence: list[CandidateEvidence] = []
        attempts: list[SourceAttempt] = []
        candidates: list[SourceCandidate] = []
        seen_urls: set[str] = set()
        normalization_policy = SourceNormalizationPolicy(
            candidate_id=policy.candidate_id,
            official_domains=policy.official_domains,
            repository_url=policy.repository_url,
            target_version=policy.version,
            reference_time=as_of,
            max_age=self._max_source_age,
            max_sources=self._max_sources,
        )

        for query in policy.official_queries:
            try:
                search = self._search.search(
                    SearchInput(
                        query=query,
                        candidate_id=policy.candidate_id,
                        domains=policy.official_domains,
                        max_results=self._max_sources,
                    )
                )
            except AdapterError as error:
                attempts.append(self._failed_attempt("search", query, error))
                continue
            if search.candidate_id != policy.candidate_id:
                raise ValueError("search result belongs to another candidate")
            try:
                self._provenance_state(search.provenance)
            except _RejectedProvenance as error:
                attempts.append(self._failed_attempt("search", query, error))
                continue
            for hit in search.results:
                if len(candidates) >= self._max_sources:
                    break
                try:
                    normalized_url = canonicalize_source_url(hit.url)
                except ValueError as error:
                    attempts.append(
                        self._failed_attempt("fetch", hit.url, UnsafeUrl(str(error)))
                    )
                    continue
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)
                try:
                    UrlPolicy(allowed_domains=policy.official_domains).validate(
                        hit.url
                    )
                    fetched = self._fetch.fetch(
                        FetchInput(url=hit.url, candidate_id=policy.candidate_id)
                    )
                    if fetched.candidate_id != policy.candidate_id:
                        raise ValueError("fetched source belongs to another candidate")
                    candidate = SourceCandidate(
                        candidate_id=policy.candidate_id,
                        url=fetched.url,
                        title=hit.title,
                        declared_source_type=SourceType.OFFICIAL_DOCUMENTATION,
                        origin=ContentOrigin.FETCHED_PAGE,
                        claim_boundary=ClaimBoundary.FACT,
                        version=None,
                        published_at=None,
                        accessed_at=fetched.provenance.retrieved_at,
                        content=fetched.content,
                        media_type=fetched.media_type,
                        snapshot_sha256=fetched.provenance.snapshot_sha256,
                    )
                    self._validate_candidate(candidate, normalization_policy)
                    available_attempt = self._available_attempt(
                        operation="fetch",
                        reference=normalized_url,
                        source_type=SourceType.OFFICIAL_DOCUMENTATION,
                        provenance=fetched.provenance,
                    )
                    candidates.append(candidate)
                    attempts.append(available_attempt)
                except (
                    AdapterError,
                    ValidationError,
                    _RejectedProvenance,
                    _NormalizationError,
                ) as error:
                    attempts.append(self._failed_attempt("fetch", hit.url, error))

        if policy.repository_url is not None:
            try:
                github = self._github.inspect_repository(
                    GitHubInspectInput(
                        repository_url=policy.repository_url,
                        candidate_id=policy.candidate_id,
                    )
                )
                if github.candidate_id != policy.candidate_id:
                    raise ValueError("GitHub source belongs to another candidate")
                state = self._provenance_state(github.provenance)
                repository = SourceCandidate(
                    candidate_id=policy.candidate_id,
                    url=github.repository_url,
                    title=f"{policy.candidate_id} GitHub repository",
                    declared_source_type=SourceType.GITHUB_REPOSITORY,
                    origin=ContentOrigin.GITHUB_API,
                    claim_boundary=ClaimBoundary.FACT,
                    version=None,
                    published_at=None,
                    accessed_at=github.provenance.retrieved_at,
                    content=self._github_text(github),
                    media_type="text/plain",
                    snapshot_sha256=github.provenance.snapshot_sha256,
                )
                self._validate_candidate(repository, normalization_policy)
                github_candidates = [repository]
                for release in github.releases:
                    release_candidate = SourceCandidate(
                        candidate_id=policy.candidate_id,
                        url=release.url,
                        title=f"{policy.candidate_id} release {release.tag}",
                        declared_source_type=SourceType.GITHUB_RELEASE,
                        origin=ContentOrigin.GITHUB_API,
                        claim_boundary=ClaimBoundary.FACT,
                        version=release.tag,
                        published_at=release.published_at,
                        accessed_at=github.provenance.retrieved_at,
                        content=self._release_text(release.tag, release.published_at),
                        media_type="text/plain",
                        snapshot_sha256=github.provenance.snapshot_sha256,
                    )
                    self._validate_candidate(release_candidate, normalization_policy)
                    github_candidates.append(release_candidate)
                attempts.append(
                    SourceAttempt(
                        operation="github",
                        reference=canonicalize_source_url(github.repository_url),
                        source_type=SourceType.GITHUB_REPOSITORY,
                        state=state,
                        provider=github.provenance.provider,
                        fetched_at=github.provenance.retrieved_at,
                        content_sha256=github.provenance.snapshot_sha256,
                        cache_fallback=github.provenance.cache_fallback,
                    )
                )
                candidates.extend(github_candidates)
            except (
                AdapterError,
                ValidationError,
                _RejectedProvenance,
                _NormalizationError,
            ) as error:
                attempts.append(
                    self._failed_attempt("github", policy.repository_url, error)
                )

        normalized = normalize_and_rank_sources(candidates, normalization_policy)
        self._materialize_sources(
            policy=policy,
            sources=normalized.authoritative_sources,
            documents=documents,
            chunks=chunks,
            evidence=evidence,
        )
        available_states = [item.state for item in attempts if item.available]
        state = (
            AcquisitionState.LIVE
            if documents and AcquisitionState.LIVE in available_states
            else AcquisitionState.CACHE
            if documents and AcquisitionState.CACHE in available_states
            else AcquisitionState.UNAVAILABLE
        )
        return CandidateResearchResult(
            candidate_id=policy.candidate_id,
            version=policy.version,
            state=state,
            research_only=policy.research_only,
            documents=tuple(documents),
            chunks=tuple(chunks),
            evidence=tuple(evidence),
            attempts=tuple(attempts),
        )

    def _materialize_sources(
        self,
        *,
        policy,
        sources,
        documents,
        chunks,
        evidence,
    ) -> None:
        for source in sources:
            source_id = self._stable_id(
                "source", policy.candidate_id, source.canonical_url
            )
            document = SourceDocument(
                source_id=source_id,
                candidate_id=policy.candidate_id,
                source_type=source.source_type,
                url=source.canonical_url,
                title=source.title,
                version=source.version,
                as_of=source.accessed_at,
                content_sha256=source.snapshot_sha256,
            )
            source_chunks = self._chunks(source_id, source.normalized_text)
            documents.append(document)
            chunks.extend(source_chunks)
            evidence.extend(
                CandidateEvidence(
                    evidence_id=self._stable_id("evidence", chunk.chunk_id),
                    candidate_id=policy.candidate_id,
                    constraint="source relevance",
                    claim=chunk.text,
                    source_ids=(source_id,),
                    chunk_ids=(chunk.chunk_id,),
                    kind=EvidenceKind.RETRIEVED_FACT,
                )
                for chunk in source_chunks
            )

    @staticmethod
    def _validate_candidate(candidate, policy) -> None:
        try:
            normalize_and_rank_sources((candidate,), policy)
        except ValueError as error:
            raise _NormalizationError(str(error)) from error

    def _available_attempt(
        self,
        *,
        operation,
        reference,
        source_type,
        provenance,
    ) -> SourceAttempt:
        return SourceAttempt(
            operation=operation,
            reference=reference,
            source_type=source_type,
            state=self._provenance_state(provenance),
            provider=provenance.provider,
            fetched_at=provenance.retrieved_at,
            content_sha256=provenance.snapshot_sha256,
            cache_fallback=provenance.cache_fallback,
        )

    def _chunks(self, source_id: str, text: str) -> tuple[SourceChunk, ...]:
        paragraphs = [
            part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()
        ]
        pieces: list[str] = []
        for paragraph in paragraphs:
            words = paragraph.split()
            current: list[str] = []
            for word in words:
                if current and len(" ".join((*current, word))) > self._chunk_size:
                    pieces.append(" ".join(current))
                    current = []
                current.append(word)
            if current:
                pieces.append(" ".join(current))
        return tuple(
            SourceChunk(
                chunk_id=f"chunk:{source_id.split(':', 1)[1]}:{ordinal:03d}",
                source_id=source_id,
                text=piece,
                ordinal=ordinal,
                content_sha256=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
            )
            for ordinal, piece in enumerate(pieces)
        )

    @staticmethod
    def _github_text(output: GitHubInspectOutput) -> str:
        issues = "\n".join(
            f"issue {item.number} {item.state} {item.title} {item.url}"
            for item in output.issues
        )
        return "\n\n".join(
            part
            for part in (
                output.description,
                f"default branch {output.default_branch}; archived {output.archived}",
                output.readme_excerpt,
                issues,
            )
            if part.strip()
        )

    @staticmethod
    def _release_text(tag: str, published_at: datetime | None) -> str:
        published = published_at.isoformat() if published_at is not None else "unknown"
        return f"release {tag}; published_at {published}"

    @staticmethod
    def _provenance_state(provenance: SourceProvenance) -> AcquisitionState:
        provider = provenance.provider.lower()
        if "synthetic" in provider or "frozen" in provider:
            raise _RejectedProvenance(
                "synthetic or frozen provenance is not live evidence"
            )
        if provenance.cache_status in {CacheStatus.HIT, CacheStatus.STALE}:
            return AcquisitionState.CACHE
        if provenance.cache_status is CacheStatus.MISS:
            return AcquisitionState.LIVE
        raise ValueError("live evidence requires explicit live or cache provenance")

    @staticmethod
    def _failed_attempt(
        operation: str, reference: str, error: Exception
    ) -> SourceAttempt:
        if isinstance(error, AdapterTimeout):
            code = (
                FailureCode.SEARCH_TIMEOUT
                if operation == "search"
                else FailureCode.TOOL_TIMEOUT
            )
        elif isinstance(error, AdapterRateLimited):
            code = FailureCode.SEARCH_RATE_LIMITED
        elif isinstance(error, UnsafeUrl):
            code = FailureCode.UNSAFE_REQUEST
        elif isinstance(error, ResponseTooLarge):
            code = FailureCode.PAGE_PARSING_FAILED
        elif isinstance(error, _RejectedProvenance):
            code = FailureCode.MALFORMED_MCP_RESPONSE
        elif isinstance(error, _NormalizationError):
            code = FailureCode.PAGE_PARSING_FAILED
        elif isinstance(error, ValidationError):
            code = FailureCode.PAGE_PARSING_FAILED
        else:
            code = (
                FailureCode.SEARCH_UNAVAILABLE
                if operation == "search"
                else FailureCode.TOOL_UNAVAILABLE
            )
        return SourceAttempt(
            operation=operation,
            reference=reference,
            state=AcquisitionState.UNAVAILABLE,
            failure_code=code,
        )

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
        return f"{prefix}:{digest}"

    @staticmethod
    def _candidate(request: ResearchRequest, candidate_id: str):
        for candidate in request.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise ValueError(f"candidate is not part of request: {candidate_id}")
