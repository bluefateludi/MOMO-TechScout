import math
import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values


RetrievalMode = Literal["auto", "lexical", "hybrid"]


class ObservabilityConfigurationError(ValueError):
    pass

_VALID_RETRIEVAL_MODES: tuple[RetrievalMode, ...] = (
    "auto",
    "lexical",
    "hybrid",
)


@dataclass(frozen=True, slots=True)
class Settings:
    semantic_scholar_api_key: str | None = field(default=None, repr=False)
    openalex_mail_address: str | None = None
    dashscope_api_key: str | None = field(default=None, repr=False)
    tavily_api_key: str | None = field(default=None, repr=False)
    github_token: str | None = field(default=None, repr=False)
    techscout_docker_install_network: str | None = None
    techscout_docker_egress_allowlist_enforced: bool = False
    techscout_docker_storage_quota_supported: bool = True
    techscout_docker_stage_timeout_seconds: float = 240.0
    techscout_live_provider_timeout_seconds: float = 15.0
    bailian_region: str = "beijing"
    bailian_embedding_model: str = "text-embedding-v4"
    dashscope_generation_model: str = "qwen3.7-plus"
    dashscope_generation_base_url: str = (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    dashscope_generation_timeout_seconds: float = 60.0
    pdf_download_timeout_seconds: float = 30.0
    pdf_max_bytes: int = 25_000_000
    pdf_max_pages: int = 200
    analysis_evidence_per_paper: int = 6
    vector_collection: str = "momo_scholar_chunks_v1"
    retrieval_candidate_k: int = 30
    retrieval_mode: RetrievalMode = "auto"
    retrieval_top_k: int = 8
    retrieval_rrf_k: int = 60
    trace_enabled: bool = True
    deployment_environment: str = 'local'
    otlp_enabled: bool = False
    otlp_endpoint: str | None = None
    otlp_timeout_seconds: float = 5.0
    otlp_failure_threshold: int = 3
    otlp_headers: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )


def _optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _string_with_default(value: str | None, default: str) -> str:
    return _optional_string(value) or default


def _positive_int(name: str, value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be at least 1")
    return parsed


def _positive_float(name: str, value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be positive finite") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be positive finite")
    return parsed


def _https_base_url(name: str, value: str | None, default: str) -> str:
    normalized = _string_with_default(value, default)
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{name} must be an HTTPS URL without credentials, query, or fragment"
        )
    return normalized


def _retrieval_mode(value: str | None) -> RetrievalMode:
    normalized = "auto" if value is None else value.strip().lower()
    if normalized not in _VALID_RETRIEVAL_MODES:
        raise ValueError("RETRIEVAL_MODE must be auto, lexical, or hybrid")
    return normalized  # type: ignore[return-value]


def _strict_bool(name: str, value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized == 'true':
        return True
    if normalized == 'false':
        return False
    raise ObservabilityConfigurationError(f'{name} must be true or false')


def _otlp_endpoint(value: str | None, *, enabled: bool) -> str | None:
    normalized = _optional_string(value)
    if normalized is None:
        if enabled:
            raise ObservabilityConfigurationError(
                'OTLP_ENDPOINT is required when OTLP is enabled'
            )
        return None
    try:
        return _https_base_url('OTLP_ENDPOINT', normalized, normalized)
    except ValueError as error:
        raise ObservabilityConfigurationError(str(error)) from error


def _otlp_headers(value: str | None) -> Mapping[str, str]:
    if value is None or not value.strip():
        return MappingProxyType({})
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ObservabilityConfigurationError(
            'OTLP_HEADERS_JSON must be a JSON object'
        ) from error
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in parsed.items()
    ):
        raise ObservabilityConfigurationError(
            'OTLP_HEADERS_JSON must map strings to strings'
        )
    return MappingProxyType(dict(parsed))


def _setting(name: str, dotenv: Mapping[str, str | None]) -> str | None:
    if name in os.environ:
        return os.environ[name]
    return dotenv.get(name)


def load_settings() -> Settings:
    dotenv = dotenv_values(Path.cwd() / ".env")
    otlp_enabled = _strict_bool(
        'OTLP_ENABLED',
        _setting('OTLP_ENABLED', dotenv),
        False,
    )
    trace_enabled = _strict_bool(
        'TRACE_ENABLED',
        _setting('TRACE_ENABLED', dotenv),
        True,
    )
    if otlp_enabled and not trace_enabled:
        raise ObservabilityConfigurationError(
            'OTLP export requires TRACE_ENABLED=true'
        )
    return Settings(
        semantic_scholar_api_key=_optional_string(
            _setting("SEMANTIC_SCHOLAR_API_KEY", dotenv)
        ),
        openalex_mail_address=_optional_string(
            _setting("OPENALEX_MAIL_ADDRESS", dotenv)
        ),
        dashscope_api_key=_optional_string(_setting("DASHSCOPE_API_KEY", dotenv)),
        tavily_api_key=_optional_string(_setting("TAVILY_API_KEY", dotenv)),
        github_token=_optional_string(_setting("GITHUB_TOKEN", dotenv)),
        techscout_docker_install_network=_optional_string(
            _setting("TECHSCOUT_DOCKER_INSTALL_NETWORK", dotenv)
        ),
        techscout_docker_egress_allowlist_enforced=_strict_bool(
            "TECHSCOUT_DOCKER_EGRESS_ALLOWLIST_ENFORCED",
            _setting("TECHSCOUT_DOCKER_EGRESS_ALLOWLIST_ENFORCED", dotenv),
            False,
        ),
        techscout_docker_storage_quota_supported=_strict_bool(
            "TECHSCOUT_DOCKER_STORAGE_QUOTA_SUPPORTED",
            _setting("TECHSCOUT_DOCKER_STORAGE_QUOTA_SUPPORTED", dotenv),
            True,
        ),
        techscout_docker_stage_timeout_seconds=_positive_float(
            "TECHSCOUT_DOCKER_STAGE_TIMEOUT_SECONDS",
            _setting("TECHSCOUT_DOCKER_STAGE_TIMEOUT_SECONDS", dotenv),
            240.0,
        ),
        techscout_live_provider_timeout_seconds=_positive_float(
            "TECHSCOUT_LIVE_PROVIDER_TIMEOUT_SECONDS",
            _setting("TECHSCOUT_LIVE_PROVIDER_TIMEOUT_SECONDS", dotenv),
            15.0,
        ),
        bailian_region=_string_with_default(
            _setting("BAILIAN_REGION", dotenv), "beijing"
        ),
        bailian_embedding_model=_string_with_default(
            _setting("BAILIAN_EMBEDDING_MODEL", dotenv), "text-embedding-v4"
        ),
        dashscope_generation_model=_string_with_default(
            _setting("DASHSCOPE_GENERATION_MODEL", dotenv), "qwen3.7-plus"
        ),
        dashscope_generation_base_url=_https_base_url(
            "DASHSCOPE_GENERATION_BASE_URL",
            _setting("DASHSCOPE_GENERATION_BASE_URL", dotenv),
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        dashscope_generation_timeout_seconds=_positive_float(
            "DASHSCOPE_GENERATION_TIMEOUT_SECONDS",
            _setting("DASHSCOPE_GENERATION_TIMEOUT_SECONDS", dotenv),
            60.0,
        ),
        pdf_download_timeout_seconds=_positive_float(
            "PDF_DOWNLOAD_TIMEOUT_SECONDS",
            _setting("PDF_DOWNLOAD_TIMEOUT_SECONDS", dotenv),
            30.0,
        ),
        pdf_max_bytes=_positive_int(
            "PDF_MAX_BYTES", _setting("PDF_MAX_BYTES", dotenv), 25_000_000
        ),
        pdf_max_pages=_positive_int(
            "PDF_MAX_PAGES", _setting("PDF_MAX_PAGES", dotenv), 200
        ),
        analysis_evidence_per_paper=_positive_int(
            "ANALYSIS_EVIDENCE_PER_PAPER",
            _setting("ANALYSIS_EVIDENCE_PER_PAPER", dotenv),
            6,
        ),
        vector_collection=_string_with_default(
            _setting("VECTOR_COLLECTION", dotenv), "momo_scholar_chunks_v1"
        ),
        retrieval_candidate_k=_positive_int(
            "RETRIEVAL_CANDIDATE_K",
            _setting("RETRIEVAL_CANDIDATE_K", dotenv),
            30,
        ),
        retrieval_mode=_retrieval_mode(_setting("RETRIEVAL_MODE", dotenv)),
        retrieval_top_k=_positive_int(
            "RETRIEVAL_TOP_K", _setting("RETRIEVAL_TOP_K", dotenv), 8
        ),
        retrieval_rrf_k=_positive_int(
            "RETRIEVAL_RRF_K", _setting("RETRIEVAL_RRF_K", dotenv), 60
        ),
        trace_enabled=trace_enabled,
        deployment_environment=_string_with_default(
            _setting('DEPLOYMENT_ENVIRONMENT', dotenv),
            'local',
        ),
        otlp_enabled=otlp_enabled,
        otlp_endpoint=_otlp_endpoint(
            _setting('OTLP_ENDPOINT', dotenv),
            enabled=otlp_enabled,
        ),
        otlp_timeout_seconds=_positive_float(
            'OTLP_TIMEOUT_SECONDS',
            _setting('OTLP_TIMEOUT_SECONDS', dotenv),
            5.0,
        ),
        otlp_failure_threshold=_positive_int(
            'OTLP_FAILURE_THRESHOLD',
            _setting('OTLP_FAILURE_THRESHOLD', dotenv),
            3,
        ),
        otlp_headers=_otlp_headers(_setting('OTLP_HEADERS_JSON', dotenv)),
    )
