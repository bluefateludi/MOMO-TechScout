from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from paper_agent.techscout.models import SourceType
from paper_agent.techscout.research.normalization import (
    ClaimBoundary,
    ContentOrigin,
    Freshness,
    SourceCandidate,
    SourceNormalizationPolicy,
    VersionFit,
    canonicalize_source_url,
    normalize_and_rank_sources,
)


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


def _candidate(
    *,
    url: str = "https://docs.example.com/guide",
    source_type: SourceType = SourceType.OFFICIAL_DOCUMENTATION,
    origin: ContentOrigin = ContentOrigin.FETCHED_PAGE,
    boundary: ClaimBoundary = ClaimBoundary.FACT,
    version: str | None = None,
    published_at: datetime | None = None,
    accessed_at: datetime = NOW,
    content: str = "The project supports persistent metadata filtering.",
) -> SourceCandidate:
    return SourceCandidate(
        candidate_id="candidate:example",
        url=url,
        title="Example source",
        declared_source_type=source_type,
        origin=origin,
        claim_boundary=boundary,
        version=version,
        published_at=published_at,
        accessed_at=accessed_at,
        media_type="text/plain",
        content=content,
        snapshot_sha256=hashlib.sha256(content.encode()).hexdigest(),
    )


def _policy(**updates) -> SourceNormalizationPolicy:
    values = {
        "candidate_id": "candidate:example",
        "official_domains": ("docs.example.com",),
        "repository_url": "https://github.com/example/project",
        "target_version": "1.2.3",
        "reference_time": NOW,
        "max_age": timedelta(days=30),
        "max_sources": 5,
    }
    values.update(updates)
    return SourceNormalizationPolicy(**values)


def test_canonical_url_removes_tracking_fragment_and_cosmetic_variants() -> None:
    assert (
        canonicalize_source_url(
            "https://DOCS.Example.com:443/guide/?b=2&utm_source=search&a=1#install"
        )
        == "https://docs.example.com/guide?a=1&b=2"
    )
    assert (
        canonicalize_source_url("https://github.com/Example/Project.git/")
        == "https://github.com/example/project"
    )


def test_source_type_is_derived_from_canonical_github_url() -> None:
    release = _candidate(
        url="https://github.com/Example/Project/releases/tag/v1.2.3",
        source_type=SourceType.GITHUB_REPOSITORY,
        origin=ContentOrigin.GITHUB_API,
        version="v1.2.3",
        published_at=NOW - timedelta(days=2),
    )

    result = normalize_and_rank_sources((release,), _policy())

    normalized = result.ranked_sources[0]
    assert normalized.canonical_url.endswith("/releases/tag/v1.2.3")
    assert normalized.source_type is SourceType.GITHUB_RELEASE
    assert normalized.version_fit is VersionFit.MATCH
    assert normalized.published_at == NOW - timedelta(days=2)
    assert normalized.accessed_at == NOW


def test_github_source_outside_candidate_repository_is_not_authoritative() -> None:
    unrelated = _candidate(
        url="https://github.com/other/project/releases/tag/v1.2.3",
        source_type=SourceType.GITHUB_RELEASE,
        origin=ContentOrigin.GITHUB_API,
        version="v1.2.3",
    )

    result = normalize_and_rank_sources((unrelated,), _policy())

    assert result.ranked_sources[0].authoritative is False
    assert result.authoritative_sources == ()


def test_search_summary_is_explicitly_unknown_and_never_authoritative() -> None:
    summary = _candidate(
        origin=ContentOrigin.SEARCH_SUMMARY,
        boundary=ClaimBoundary.UNKNOWN,
        content="A search engine says filtering is supported.",
    )

    result = normalize_and_rank_sources((summary,), _policy())

    normalized = result.ranked_sources[0]
    assert normalized.claim_boundary is ClaimBoundary.UNKNOWN
    assert normalized.authoritative is False
    assert result.authoritative_sources == ()
    with pytest.raises(ValidationError, match="search summary cannot be a fact"):
        _candidate(origin=ContentOrigin.SEARCH_SUMMARY)


def test_inference_remains_distinct_from_retrieved_fact_authority() -> None:
    inference = _candidate(boundary=ClaimBoundary.INFERENCE)

    result = normalize_and_rank_sources((inference,), _policy())

    assert result.ranked_sources[0].claim_boundary is ClaimBoundary.INFERENCE
    assert result.ranked_sources[0].authoritative is False
    assert result.authoritative_sources == ()


def test_deduplication_prefers_fetched_content_over_same_url_search_summary() -> None:
    summary = _candidate(
        url="https://docs.example.com/guide/?utm_campaign=launch",
        origin=ContentOrigin.SEARCH_SUMMARY,
        boundary=ClaimBoundary.UNKNOWN,
        content="Search summary",
    )
    fetched = _candidate(url="https://docs.example.com/guide")

    result = normalize_and_rank_sources((summary, fetched), _policy())

    assert len(result.ranked_sources) == 1
    assert result.ranked_sources[0].origin is ContentOrigin.FETCHED_PAGE
    assert result.authoritative_sources == result.ranked_sources


def test_version_mismatch_and_stale_access_are_not_authoritative() -> None:
    mismatched = _candidate(version="2.0.0")
    stale = _candidate(
        url="https://docs.example.com/old",
        accessed_at=NOW - timedelta(days=31),
    )

    result = normalize_and_rank_sources((mismatched, stale), _policy())

    by_url = {item.canonical_url: item for item in result.ranked_sources}
    assert by_url["https://docs.example.com/guide"].version_fit is VersionFit.MISMATCH
    assert by_url["https://docs.example.com/old"].freshness is Freshness.STALE
    assert result.authoritative_sources == ()


def test_ranking_is_deterministic_and_prefers_matching_release_then_official_docs() -> (
    None
):
    release = _candidate(
        url="https://github.com/example/project/releases/tag/v1.2.3",
        source_type=SourceType.GITHUB_RELEASE,
        origin=ContentOrigin.GITHUB_API,
        version="v1.2.3",
        published_at=NOW - timedelta(days=1),
    )
    docs = _candidate(url="https://docs.example.com/guide", version=None)
    repository = _candidate(
        url="https://github.com/example/project",
        source_type=SourceType.GITHUB_REPOSITORY,
        origin=ContentOrigin.GITHUB_API,
    )

    forward = normalize_and_rank_sources((repository, docs, release), _policy())
    reverse = normalize_and_rank_sources((release, docs, repository), _policy())

    expected = (
        "https://github.com/example/project/releases/tag/v1.2.3",
        "https://docs.example.com/guide",
        "https://github.com/example/project",
    )
    assert (
        tuple(item.canonical_url for item in forward.authoritative_sources) == expected
    )
    assert (
        tuple(item.canonical_url for item in reverse.authoritative_sources) == expected
    )


def test_html_is_normalized_before_fact_materialization() -> None:
    html = "<main><h1>Filtering</h1><p>Supports metadata filters.</p></main>"
    source = _candidate(content=html).model_copy(
        update={
            "media_type": "text/html",
            "snapshot_sha256": hashlib.sha256(html.encode()).hexdigest(),
        }
    )

    result = normalize_and_rank_sources((source,), _policy())

    assert result.authoritative_sources[0].normalized_text == (
        "Filtering\n\nSupports metadata filters."
    )
