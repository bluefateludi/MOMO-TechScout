import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.evaluation_demo import build_baseline, render_markdown


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
