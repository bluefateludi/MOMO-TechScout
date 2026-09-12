import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from paper_agent.config import Settings, load_settings


SUPPORTED_SETTING_NAMES = (
    "SEMANTIC_SCHOLAR_API_KEY",
    "OPENALEX_MAIL_ADDRESS",
    "DASHSCOPE_API_KEY",
    "TAVILY_API_KEY",
    "GITHUB_TOKEN",
    "TECHSCOUT_DOCKER_INSTALL_NETWORK",
    "TECHSCOUT_DOCKER_EGRESS_ALLOWLIST_ENFORCED",
    "TECHSCOUT_DOCKER_STORAGE_QUOTA_SUPPORTED",
    "TECHSCOUT_DOCKER_STAGE_TIMEOUT_SECONDS",
    "TECHSCOUT_LIVE_PROVIDER_TIMEOUT_SECONDS",
    "BAILIAN_REGION",
    "BAILIAN_EMBEDDING_MODEL",
    "DASHSCOPE_GENERATION_MODEL",
    "DASHSCOPE_GENERATION_BASE_URL",
    "DASHSCOPE_GENERATION_TIMEOUT_SECONDS",
    "PDF_DOWNLOAD_TIMEOUT_SECONDS",
    "PDF_MAX_BYTES",
    "PDF_MAX_PAGES",
    "ANALYSIS_EVIDENCE_PER_PAPER",
    "VECTOR_COLLECTION",
    "RETRIEVAL_CANDIDATE_K",
    "RETRIEVAL_MODE",
    "RETRIEVAL_TOP_K",
    "RETRIEVAL_RRF_K",
    'TRACE_ENABLED',
    'DEPLOYMENT_ENVIRONMENT',
    'OTLP_ENABLED',
    'OTLP_ENDPOINT',
    'OTLP_TIMEOUT_SECONDS',
    'OTLP_FAILURE_THRESHOLD',
    'OTLP_HEADERS_JSON',
)


@pytest.fixture(autouse=True)
def isolated_settings_environment(tmp_path, monkeypatch) -> None:
    for variable in SUPPORTED_SETTING_NAMES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)


def test_load_settings_uses_safe_defaults(monkeypatch):
    assert load_settings() == Settings()


def test_load_settings_uses_hybrid_retrieval_defaults(monkeypatch) -> None:
    settings = load_settings()
    assert settings.retrieval_mode == "auto"
    assert settings.retrieval_candidate_k == 30
    assert settings.retrieval_top_k == 8
    assert settings.retrieval_rrf_k == 60


def test_fulltext_generation_defaults() -> None:
    settings = Settings()
    assert settings.bailian_embedding_model == "text-embedding-v4"
    assert settings.dashscope_generation_model == "qwen3.7-plus"
    assert settings.dashscope_generation_base_url == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert settings.dashscope_generation_timeout_seconds == 60.0
    assert settings.pdf_download_timeout_seconds == 30.0
    assert settings.pdf_max_bytes == 25_000_000
    assert settings.pdf_max_pages == 200
    assert settings.analysis_evidence_per_paper == 6


def test_load_settings_reads_environment(monkeypatch):
    values = {
        "SEMANTIC_SCHOLAR_API_KEY": "semantic-scholar-key",
        "OPENALEX_MAIL_ADDRESS": "researcher@example.test",
        "DASHSCOPE_API_KEY": "  dashscope-key  ",
        "BAILIAN_REGION": "  shanghai  ",
        "BAILIAN_EMBEDDING_MODEL": "  custom-embedding  ",
        "DASHSCOPE_GENERATION_MODEL": "  custom-generation  ",
        "DASHSCOPE_GENERATION_BASE_URL": "  https://example.test/v2  ",
        "DASHSCOPE_GENERATION_TIMEOUT_SECONDS": " 45.5 ",
        "PDF_DOWNLOAD_TIMEOUT_SECONDS": " 12.25 ",
        "PDF_MAX_BYTES": " 123456 ",
        "PDF_MAX_PAGES": " 99 ",
        "ANALYSIS_EVIDENCE_PER_PAPER": " 4 ",
        "VECTOR_COLLECTION": "  custom-chunks  ",
        "RETRIEVAL_CANDIDATE_K": "  42  ",
        "RETRIEVAL_MODE": " hybrid ",
        "RETRIEVAL_TOP_K": " 9 ",
        "RETRIEVAL_RRF_K": " 61 ",
    }
    for variable, value in values.items():
        monkeypatch.setenv(variable, value)

    assert load_settings() == Settings(
        semantic_scholar_api_key="semantic-scholar-key",
        openalex_mail_address="researcher@example.test",
        dashscope_api_key="dashscope-key",
        bailian_region="shanghai",
        bailian_embedding_model="custom-embedding",
        dashscope_generation_model="custom-generation",
        dashscope_generation_base_url="https://example.test/v2",
        dashscope_generation_timeout_seconds=45.5,
        pdf_download_timeout_seconds=12.25,
        pdf_max_bytes=123456,
        pdf_max_pages=99,
        analysis_evidence_per_paper=4,
        vector_collection="custom-chunks",
        retrieval_candidate_k=42,
        retrieval_mode="hybrid",
        retrieval_top_k=9,
        retrieval_rrf_k=61,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("AUTO", "auto"), (" lexical ", "lexical"), ("Hybrid", "hybrid")],
)
def test_load_settings_normalizes_retrieval_mode(
    monkeypatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("RETRIEVAL_MODE", raw)
    assert load_settings().retrieval_mode == expected


@pytest.mark.parametrize("raw", ["unknown", "", "vector"])
def test_load_settings_rejects_unknown_retrieval_mode(
    monkeypatch, raw: str
) -> None:
    monkeypatch.setenv("RETRIEVAL_MODE", raw)
    with pytest.raises(ValueError, match="RETRIEVAL_MODE"):
        load_settings()


@pytest.mark.parametrize(
    ("name", "raw", "message"),
    [
        ("RETRIEVAL_TOP_K", "0", "must be at least 1"),
        ("RETRIEVAL_RRF_K", "-1", "must be at least 1"),
        ("RETRIEVAL_TOP_K", "1.5", "must be an integer"),
        ("RETRIEVAL_RRF_K", "not-an-int", "must be an integer"),
    ],
)
def test_load_settings_rejects_invalid_retrieval_k(
    monkeypatch, name: str, raw: str, message: str
) -> None:
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError, match=rf"{name} {message}"):
        load_settings()


@pytest.mark.parametrize("name", [
    "DASHSCOPE_GENERATION_TIMEOUT_SECONDS",
    "PDF_DOWNLOAD_TIMEOUT_SECONDS",
])
@pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "word"])
def test_timeouts_reject_invalid_values(name, raw, monkeypatch) -> None:
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError, match=rf"{name} must be positive finite"):
        load_settings()


@pytest.mark.parametrize("name", [
    "PDF_MAX_BYTES",
    "PDF_MAX_PAGES",
    "ANALYSIS_EVIDENCE_PER_PAPER",
])
@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "word"])
def test_fulltext_limits_reject_invalid_values(name, raw, monkeypatch) -> None:
    monkeypatch.setenv(name, raw)
    with pytest.raises(ValueError, match=rf"{name} must"):
        load_settings()


@pytest.mark.parametrize(
    "raw",
    [
        "http://example.test/v1",
        "https://user:password@example.test/v1",
        "https://example.test/v1?mode=test",
        "https://example.test/v1#fragment",
        "https:///v1",
    ],
)
def test_generation_base_url_rejects_invalid_values(raw, monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_GENERATION_BASE_URL", raw)
    with pytest.raises(ValueError, match="DASHSCOPE_GENERATION_BASE_URL"):
        load_settings()


def test_generation_base_url_allows_configured_https_host(monkeypatch) -> None:
    monkeypatch.setenv(
        "DASHSCOPE_GENERATION_BASE_URL", "https://gateway.example.test/v1"
    )
    assert load_settings().dashscope_generation_base_url == (
        "https://gateway.example.test/v1"
    )


def test_settings_repr_hides_api_keys():
    settings = Settings(
        semantic_scholar_api_key="sentinel-semantic-scholar-secret",
        dashscope_api_key="sentinel-dashscope-secret",
    )

    representation = repr(settings)

    assert "sentinel-semantic-scholar-secret" not in representation
    assert "sentinel-dashscope-secret" not in representation


def test_optional_observability_settings_are_safe(monkeypatch) -> None:
    monkeypatch.setenv('TRACE_ENABLED', 'true')
    monkeypatch.setenv('OTLP_ENABLED', 'true')
    monkeypatch.setenv('OTLP_ENDPOINT', 'https://collector.example.test/v1/traces')
    monkeypatch.setenv('OTLP_TIMEOUT_SECONDS', '7.5')
    monkeypatch.setenv('OTLP_FAILURE_THRESHOLD', '2')
    monkeypatch.setenv(
        'OTLP_HEADERS_JSON',
        '{"Authorization": "secret-header"}',
    )

    settings = load_settings()
    assert settings.trace_enabled
    assert settings.otlp_enabled
    assert settings.otlp_timeout_seconds == 7.5
    assert settings.otlp_failure_threshold == 2
    assert settings.otlp_headers == {'Authorization': 'secret-header'}
    assert 'secret-header' not in repr(settings)


@pytest.mark.parametrize('raw', ['yes', '1', ''])
def test_observability_booleans_are_strict(raw, monkeypatch) -> None:
    monkeypatch.setenv('OTLP_ENABLED', raw)
    with pytest.raises(ValueError, match='OTLP_ENABLED'):
        load_settings()


def test_otlp_requires_local_trace(monkeypatch) -> None:
    monkeypatch.setenv('TRACE_ENABLED', 'false')
    monkeypatch.setenv('OTLP_ENABLED', 'true')
    monkeypatch.setenv(
        'OTLP_ENDPOINT',
        'https://collector.example.test/v1/traces',
    )
    with pytest.raises(ValueError, match='TRACE_ENABLED'):
        load_settings()


@pytest.mark.parametrize(
    'endpoint',
    [
        'http://collector.example.test/v1/traces',
        'https://user:password@collector.example.test/v1/traces',
        'https://collector.example.test/v1/traces?token=secret',
    ],
)
def test_otlp_endpoint_rejects_unsafe_urls(endpoint, monkeypatch) -> None:
    monkeypatch.setenv('OTLP_ENABLED', 'true')
    monkeypatch.setenv('OTLP_ENDPOINT', endpoint)
    with pytest.raises(ValueError, match='OTLP_ENDPOINT'):
        load_settings()


@pytest.mark.parametrize("value", ["not-an-integer", "1.5", ""])
def test_load_settings_rejects_non_integer_candidate_k(monkeypatch, value):
    monkeypatch.setenv("RETRIEVAL_CANDIDATE_K", value)

    with pytest.raises(ValueError, match="RETRIEVAL_CANDIDATE_K must be an integer"):
        load_settings()


@pytest.mark.parametrize("value", ["0", "-1"])
def test_load_settings_rejects_candidate_k_less_than_one(monkeypatch, value):
    monkeypatch.setenv("RETRIEVAL_CANDIDATE_K", value)

    with pytest.raises(ValueError, match="RETRIEVAL_CANDIDATE_K must be at least 1"):
        load_settings()


def test_load_settings_reads_dotenv_from_current_directory(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=file-key\nRETRIEVAL_MODE=hybrid\n",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.dashscope_api_key == "file-key"
    assert settings.retrieval_mode == "hybrid"


def test_process_environment_takes_precedence_over_dotenv(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=file-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "process-key")

    assert load_settings().dashscope_api_key == "process-key"


def test_fulltext_settings_load_from_dotenv_with_environment_precedence(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / ".env").write_text(
        "DASHSCOPE_GENERATION_MODEL=file-model\n"
        "PDF_MAX_PAGES=150\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PDF_MAX_PAGES", "175")

    settings = load_settings()

    assert settings.dashscope_generation_model == "file-model"
    assert settings.pdf_max_pages == 175


def test_load_settings_ignores_parent_dotenv(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=parent-key\n",
        encoding="utf-8",
    )
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)

    assert load_settings().dashscope_api_key is None


def test_consecutive_loads_from_different_directories_do_not_leak(
    tmp_path, monkeypatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / ".env").write_text(
        "DASHSCOPE_API_KEY=first-key\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(first)
    first_settings = load_settings()
    monkeypatch.chdir(second)
    second_settings = load_settings()

    assert first_settings.dashscope_api_key == "first-key"
    assert second_settings.dashscope_api_key is None


def test_load_settings_does_not_mutate_process_environment(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=file-key\n",
        encoding="utf-8",
    )
    before = os.environ.copy()

    load_settings()

    assert os.environ == before


def test_blank_dotenv_strings_preserve_optional_and_default_behavior(
    tmp_path,
) -> None:
    (tmp_path / ".env").write_text(
        "DASHSCOPE_API_KEY=\nBAILIAN_REGION=\nVECTOR_COLLECTION=\n",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.bailian_region == "beijing"
    assert settings.vector_collection == "momo_scholar_chunks_v1"


def test_blank_dotenv_retrieval_mode_preserves_validation(tmp_path) -> None:
    (tmp_path / ".env").write_text("RETRIEVAL_MODE=\n", encoding="utf-8")

    with pytest.raises(ValueError, match="RETRIEVAL_MODE"):
        load_settings()


@pytest.mark.parametrize(
    "name",
    [
        "RETRIEVAL_CANDIDATE_K",
        "RETRIEVAL_TOP_K",
        "RETRIEVAL_RRF_K",
    ],
)
def test_blank_dotenv_retrieval_k_preserves_validation(
    tmp_path, name: str
) -> None:
    (tmp_path / ".env").write_text(f"{name}=\n", encoding="utf-8")

    with pytest.raises(ValueError, match=rf"{name} must be an integer"):
        load_settings()


def test_dotenv_example_is_safe_and_documents_retrieval_defaults() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    example_path = repository_root / ".env.example"

    assert example_path.exists()

    values = dotenv_values(example_path)
    assert values["DASHSCOPE_API_KEY"] == ""
    assert values["BAILIAN_EMBEDDING_MODEL"] == "text-embedding-v4"
    assert values["DASHSCOPE_GENERATION_MODEL"] == "qwen3.7-plus"
    assert values["DASHSCOPE_GENERATION_BASE_URL"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert values["DASHSCOPE_GENERATION_TIMEOUT_SECONDS"] == "60"
    assert values["PDF_DOWNLOAD_TIMEOUT_SECONDS"] == "30"
    assert values["PDF_MAX_BYTES"] == "25000000"
    assert values["PDF_MAX_PAGES"] == "200"
    assert values["ANALYSIS_EVIDENCE_PER_PAPER"] == "6"
    assert values["RETRIEVAL_MODE"] == "auto"
    assert values["RETRIEVAL_CANDIDATE_K"] == "30"
    assert values["RETRIEVAL_TOP_K"] == "8"
    assert values["RETRIEVAL_RRF_K"] == "60"
    assert not any(
        value
        for name, value in values.items()
        if name.endswith("API_KEY")
    )
    assert not {"OPENAI_API_KEY", "OPENAI_BASE_URL", "PAPER_AGENT_MODEL"} & set(values)


def test_obsolete_openai_settings_are_not_part_of_settings() -> None:
    fields = Settings.__dataclass_fields__
    assert not {"openai_api_key", "openai_base_url", "paper_agent_model"} & set(fields)


def test_gitignore_protects_dotenv_without_ignoring_example() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    gitignore_path = repository_root / ".gitignore"
    rules = {
        line.strip()
        for line in gitignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".env" in rules
    assert ".env.example" not in rules
