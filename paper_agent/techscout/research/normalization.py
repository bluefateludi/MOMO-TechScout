from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from enum import Enum
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator
from typing_extensions import Self

from paper_agent.techscout.errors import StableId
from paper_agent.techscout.models import (
    HttpsUrl,
    NonEmptyStr,
    Sha256,
    SourceType,
    TechScoutModel,
)


_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


class ContentOrigin(str, Enum):
    FETCHED_PAGE = "fetched_page"
    GITHUB_API = "github_api"
    SEARCH_SUMMARY = "search_summary"


class ClaimBoundary(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class Freshness(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class VersionFit(str, Enum):
    MATCH = "match"
    UNVERSIONED = "unversioned"
    MISMATCH = "mismatch"


class SourceAuthority(str, Enum):
    OFFICIAL = "official"
    PROJECT_MAINTAINER = "project_maintainer"
    SEARCH_INDEX = "search_index"
    UNTRUSTED = "untrusted"


class SourceCandidate(TechScoutModel):
    """Bounded source material before trust, freshness, and version checks."""

    candidate_id: StableId
    url: HttpsUrl
    title: NonEmptyStr
    declared_source_type: SourceType
    origin: ContentOrigin
    claim_boundary: ClaimBoundary
    version: NonEmptyStr | None = None
    published_at: datetime | None = None
    accessed_at: datetime
    media_type: NonEmptyStr
    content: NonEmptyStr
    snapshot_sha256: Sha256

    @field_validator("published_at", "accessed_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def keep_search_summaries_outside_fact_authority(self) -> Self:
        if (
            self.origin is ContentOrigin.SEARCH_SUMMARY
            and self.claim_boundary is ClaimBoundary.FACT
        ):
            raise ValueError("search summary cannot be a fact")
        return self


class SourceNormalizationPolicy(TechScoutModel):
    candidate_id: StableId
    official_domains: tuple[NonEmptyStr, ...] = Field(max_length=10)
    repository_url: HttpsUrl | None = None
    target_version: NonEmptyStr | None = None
    reference_time: datetime
    max_age: timedelta = timedelta(days=30)
    max_sources: int = Field(default=5, ge=1, le=5)

    @field_validator("reference_time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference_time must include a timezone")
        return value

    @model_validator(mode="after")
    def require_positive_age(self) -> Self:
        if self.max_age.total_seconds() <= 0:
            raise ValueError("max_age must be positive")
        return self


class NormalizedSource(TechScoutModel):
    candidate_id: StableId
    canonical_url: HttpsUrl
    title: NonEmptyStr
    source_type: SourceType
    authority: SourceAuthority
    origin: ContentOrigin
    claim_boundary: ClaimBoundary
    freshness: Freshness
    version_fit: VersionFit
    authoritative: bool
    version: NonEmptyStr | None = None
    published_at: datetime | None = None
    accessed_at: datetime
    normalized_text: NonEmptyStr
    snapshot_sha256: Sha256

    @field_validator("published_at", "accessed_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("normalized source timestamps must include a timezone")
        return value


class SourceNormalizationResult(TechScoutModel):
    ranked_sources: tuple[NormalizedSource, ...]
    authoritative_sources: tuple[NormalizedSource, ...] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_authoritative_projection(self) -> Self:
        ranked = {item.canonical_url: item for item in self.ranked_sources}
        if any(not item.authoritative for item in self.authoritative_sources):
            raise ValueError("authoritative projection contains an ineligible source")
        if any(
            ranked.get(item.canonical_url) != item
            for item in self.authoritative_sources
        ):
            raise ValueError("authoritative projection must come from ranked sources")
        return self


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def canonicalize_source_url(value: str) -> str:
    """Return one stable identity for safe HTTPS documentation/GitHub URLs."""

    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("source URL must be absolute HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("source URL cannot contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source URL has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("source URL must use the standard HTTPS port")

    host = parsed.hostname.lower().rstrip(".")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    parts = [part for part in path.split("/") if part]
    if host == "github.com" and len(parts) >= 2:
        parts[0] = parts[0].lower()
        parts[1] = parts[1].lower()
        if len(parts) == 2 and parts[1].endswith(".git"):
            parts[1] = parts[1][:-4]
        path = "/" + "/".join(parts)
    else:
        path = "/" + "/".join(parts)
    if path != "/":
        path = path.rstrip("/")
    else:
        path = ""

    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in _TRACKING_PARAMETERS
        ),
        doseq=True,
    )
    return urlunsplit(("https", host, path, query, ""))


def normalize_and_rank_sources(
    candidates: Sequence[SourceCandidate],
    policy: SourceNormalizationPolicy,
) -> SourceNormalizationResult:
    """Normalize, deduplicate, classify, and rank candidate evidence deterministically."""

    normalized: list[NormalizedSource] = []
    for candidate in candidates:
        if candidate.candidate_id != policy.candidate_id:
            raise ValueError("source candidate belongs to another candidate")
        canonical_url = canonicalize_source_url(candidate.url)
        source_type = _source_type(canonical_url, candidate.declared_source_type)
        authority = _authority(
            canonical_url,
            source_type,
            candidate.origin,
            policy.official_domains,
            policy.repository_url,
        )
        freshness = (
            Freshness.CURRENT
            if policy.reference_time - candidate.accessed_at <= policy.max_age
            else Freshness.STALE
        )
        version_fit = _version_fit(candidate.version, policy.target_version)
        authoritative = (
            candidate.claim_boundary is ClaimBoundary.FACT
            and candidate.origin
            in {ContentOrigin.FETCHED_PAGE, ContentOrigin.GITHUB_API}
            and authority
            in {SourceAuthority.OFFICIAL, SourceAuthority.PROJECT_MAINTAINER}
            and freshness is Freshness.CURRENT
            and version_fit is not VersionFit.MISMATCH
        )
        normalized.append(
            NormalizedSource(
                candidate_id=candidate.candidate_id,
                canonical_url=canonical_url,
                title=candidate.title,
                source_type=source_type,
                authority=authority,
                origin=candidate.origin,
                claim_boundary=candidate.claim_boundary,
                freshness=freshness,
                version_fit=version_fit,
                authoritative=authoritative,
                version=candidate.version,
                published_at=candidate.published_at,
                accessed_at=candidate.accessed_at,
                normalized_text=_normalize_content(
                    candidate.content, candidate.media_type
                ),
                snapshot_sha256=candidate.snapshot_sha256,
            )
        )

    by_url: dict[str, NormalizedSource] = {}
    for source in normalized:
        existing = by_url.get(source.canonical_url)
        if existing is None or _rank_key(source) < _rank_key(existing):
            by_url[source.canonical_url] = source
    ranked = tuple(sorted(by_url.values(), key=_rank_key))
    authoritative = tuple(source for source in ranked if source.authoritative)[
        : policy.max_sources
    ]
    return SourceNormalizationResult(
        ranked_sources=ranked,
        authoritative_sources=authoritative,
    )


def _source_type(url: str, declared: SourceType) -> SourceType:
    parsed = urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname == "github.com":
        if len(parts) >= 4 and parts[2:4] == ["releases", "tag"]:
            return SourceType.GITHUB_RELEASE
        if len(parts) >= 3 and parts[2] == "issues":
            return SourceType.GITHUB_ISSUE
        return SourceType.GITHUB_REPOSITORY
    return declared


def _authority(
    url: str,
    source_type: SourceType,
    origin: ContentOrigin,
    official_domains: Sequence[str],
    repository_url: str | None,
) -> SourceAuthority:
    if origin is ContentOrigin.SEARCH_SUMMARY:
        return SourceAuthority.SEARCH_INDEX
    host = urlsplit(url).hostname or ""
    if host == "github.com" and source_type in {
        SourceType.GITHUB_REPOSITORY,
        SourceType.GITHUB_RELEASE,
        SourceType.GITHUB_ISSUE,
    }:
        if repository_url is None:
            return SourceAuthority.UNTRUSTED
        repository = canonicalize_source_url(repository_url)
        if url == repository or url.startswith(f"{repository}/"):
            return SourceAuthority.PROJECT_MAINTAINER
        return SourceAuthority.UNTRUSTED
    allowed = tuple(domain.casefold().strip(".") for domain in official_domains)
    if source_type is SourceType.OFFICIAL_DOCUMENTATION and any(
        host == domain or host.endswith(f".{domain}") for domain in allowed
    ):
        return SourceAuthority.OFFICIAL
    return SourceAuthority.UNTRUSTED


def _version_fit(source_version: str | None, target_version: str | None) -> VersionFit:
    if source_version is None or target_version is None:
        return VersionFit.UNVERSIONED
    source = source_version.casefold().removeprefix("v")
    target = target_version.casefold().removeprefix("v")
    if target.endswith(".*"):
        return (
            VersionFit.MATCH if source.startswith(target[:-1]) else VersionFit.MISMATCH
        )
    return VersionFit.MATCH if source == target else VersionFit.MISMATCH


def _normalize_content(content: str, media_type: str) -> str:
    if media_type == "text/html":
        parser = _TextExtractor()
        parser.feed(content)
        content = "\n\n".join(parser.parts)
    elif media_type == "application/json":
        try:
            content = json.dumps(
                json.loads(content), sort_keys=True, ensure_ascii=False
            )
        except json.JSONDecodeError as exc:
            raise ValueError("source JSON could not be normalized") from exc
    normalized = content.replace("\x00", "").strip()
    if not normalized:
        raise ValueError("source has no usable text")
    return normalized


def _rank_key(source: NormalizedSource) -> tuple[object, ...]:
    boundary_rank = {
        ClaimBoundary.FACT: 0,
        ClaimBoundary.INFERENCE: 1,
        ClaimBoundary.UNKNOWN: 2,
    }
    version_rank = {
        VersionFit.MATCH: 0,
        VersionFit.UNVERSIONED: 1,
        VersionFit.MISMATCH: 2,
    }
    type_rank = {
        SourceType.OFFICIAL_DOCUMENTATION: 0,
        SourceType.GITHUB_RELEASE: 1,
        SourceType.GITHUB_REPOSITORY: 2,
        SourceType.PACKAGE_METADATA: 3,
        SourceType.GITHUB_ISSUE: 4,
        SourceType.PAPER: 5,
    }
    freshness_rank = {
        Freshness.CURRENT: 0,
        Freshness.UNKNOWN: 1,
        Freshness.STALE: 2,
    }
    published = source.published_at.timestamp() if source.published_at else 0.0
    return (
        0 if source.authoritative else 1,
        boundary_rank[source.claim_boundary],
        version_rank[source.version_fit],
        freshness_rank[source.freshness],
        type_rank[source.source_type],
        -source.accessed_at.timestamp(),
        -published,
        source.canonical_url,
    )
