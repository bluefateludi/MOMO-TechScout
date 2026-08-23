import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.evaluation_demo import (
    DEFAULT_MANIFEST,
    build_baseline,
    load_manifest,
    render_markdown,
    verify_baseline,
)


EVALUATION_FIXTURE = Path("tests/fixtures/eval_cases.json")
RETRIEVAL_FIXTURE = Path("tests/fixtures/retrieval_eval_cases.json")


def test_build_baseline_combines_real_runner_results() -> None:
    result = build_baseline(
        evaluation_fixture=EVALUATION_FIXTURE,
        retrieval_fixture=RETRIEVAL_FIXTURE,
        k=3,
    )

    assert result["baseline"] == "momo-scholar-offline-demo-v1"
    assert result["claim_and_citation"]["summary"]["case_count"] == 1
    assert result["retrieval"]["k"] == 3
    assert len(result["retrieval"]["cases"]) == 7
    assert set(result["retrieval"]["summary"]) == {"lexical", "vector", "hybrid"}


def test_render_markdown_exposes_metrics_and_limitations() -> None:
    result = build_baseline(
        evaluation_fixture=EVALUATION_FIXTURE,
        retrieval_fixture=RETRIEVAL_FIXTURE,
        k=3,
    )

    output = render_markdown(result)

    assert "Claim and citation contracts" in output
    assert "Retrieval ranking at K=3" in output
    assert "| lexical |" in output
    assert "| vector |" in output
    assert "| hybrid |" in output
    assert "not a public benchmark" in output
    assert "not semantic entailment" in output


def test_json_cli_output_is_machine_readable_and_deterministic() -> None:
    command = [sys.executable, "scripts/evaluation_demo.py", "--format", "json"]

    first = subprocess.run(command, check=True, capture_output=True, text=True)
    second = subprocess.run(command, check=True, capture_output=True, text=True)

    assert first.stdout == second.stdout
    result = json.loads(first.stdout)
    assert result["inputs"]["k"] == 3
    assert result["retrieval"]["summary"]["hybrid"]["recall_at_k"] == pytest.approx(
        6 / 7
    )


def test_cli_rejects_non_positive_k() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/evaluation_demo.py", "--k", "0"],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must be a positive integer" in result.stderr


def test_versioned_manifest_verifies_fixture_hashes_and_metrics() -> None:
    manifest = load_manifest(DEFAULT_MANIFEST)
    result = build_baseline(
        evaluation_fixture=EVALUATION_FIXTURE,
        retrieval_fixture=RETRIEVAL_FIXTURE,
        k=3,
    )

    errors = verify_baseline(
        result,
        manifest=manifest,
        evaluation_fixture=EVALUATION_FIXTURE,
        retrieval_fixture=RETRIEVAL_FIXTURE,
    )

    assert errors == []
    assert manifest["scope"] == "synthetic_regression_contract"


def test_manifest_verification_reports_input_and_metric_regressions() -> None:
    manifest = load_manifest(DEFAULT_MANIFEST)
    result = build_baseline(
        evaluation_fixture=EVALUATION_FIXTURE,
        retrieval_fixture=RETRIEVAL_FIXTURE,
        k=3,
    )
    changed_manifest = json.loads(json.dumps(manifest))
    changed_manifest["inputs"]["evaluation_fixture"]["sha256"] = "0" * 64
    changed_manifest["expected"]["retrieval"]["summary"]["hybrid"][
        "recall_at_k"
    ] = 1.0

    errors = verify_baseline(
        result,
        manifest=changed_manifest,
        evaluation_fixture=EVALUATION_FIXTURE,
        retrieval_fixture=RETRIEVAL_FIXTURE,
    )

    assert any("evaluation fixture sha256" in error for error in errors)
    assert any("retrieval.summary.hybrid.recall_at_k" in error for error in errors)


def test_check_cli_returns_pass_without_corrupting_json_stdout() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/evaluation_demo.py",
            "--format",
            "json",
            "--check",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout)["baseline"] == "momo-scholar-offline-demo-v1"
    assert "Baseline check: PASS" in result.stderr


def test_check_cli_fails_when_k_does_not_match_manifest() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/evaluation_demo.py", "--k", "1", "--check"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "inputs.k" in result.stderr
    assert "Baseline check: FAIL" in result.stderr
