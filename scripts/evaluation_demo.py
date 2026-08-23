"""Run MOMO Scholar's deterministic offline evaluation demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from paper_agent.eval.retrieval_runner import evaluate_retrieval_fixture
from paper_agent.eval.runner import evaluate_fixture


DEFAULT_EVALUATION_FIXTURE = Path("tests/fixtures/eval_cases.json")
DEFAULT_RETRIEVAL_FIXTURE = Path("tests/fixtures/retrieval_eval_cases.json")
BASELINE_NAME = "momo-scholar-offline-demo-v1"


def build_baseline(
    *,
    evaluation_fixture: Path,
    retrieval_fixture: Path,
    k: int,
) -> dict[str, object]:
    """Evaluate both versioned fixtures and return one auditable result."""
    claim_result = evaluate_fixture(evaluation_fixture)
    retrieval_result = evaluate_retrieval_fixture(retrieval_fixture, k=k)
    return {
        "baseline": BASELINE_NAME,
        "inputs": {
            "evaluation_fixture": evaluation_fixture.as_posix(),
            "retrieval_fixture": retrieval_fixture.as_posix(),
            "k": k,
        },
        "claim_and_citation": claim_result,
        "retrieval": retrieval_result,
        "limitations": [
            "Fixtures are deterministic contract cases, not a public benchmark.",
            "Citation validity checks referenced IDs, not semantic entailment.",
            "Vector rankings are fixture-provided; no embedding service is called.",
        ],
    }


def render_markdown(result: dict[str, object]) -> str:
    """Render a compact presentation view while preserving exact JSON output."""
    claim_result = result["claim_and_citation"]
    retrieval_result = result["retrieval"]
    assert isinstance(claim_result, dict)
    assert isinstance(retrieval_result, dict)
    claim_summary = claim_result["summary"]
    retrieval_summary = retrieval_result["summary"]
    assert isinstance(claim_summary, dict)
    assert isinstance(retrieval_summary, dict)

    lines = [
        f"# MOMO Scholar offline evaluation — {result['baseline']}",
        "",
        "## Claim and citation contracts",
        "",
        "| Cases | Retrieval hit rate | Evidence coverage | Unsupported claim rate ↓ | Citation validity |",
        "|---:|---:|---:|---:|---:|",
        (
            f"| {claim_summary['case_count']} "
            f"| {claim_summary['retrieval_hit_rate']:.4f} "
            f"| {claim_summary['evidence_coverage']:.4f} "
            f"| {claim_summary['unsupported_claim_rate']:.4f} "
            f"| {claim_summary['citation_validity']:.4f} |"
        ),
        "",
        f"## Retrieval ranking at K={retrieval_result['k']}",
        "",
        "| Mode | Recall@K | Precision@K | MRR@K | nDCG@K |",
        "|:---|---:|---:|---:|---:|",
    ]
    for mode in ("lexical", "vector", "hybrid"):
        metrics = retrieval_summary[mode]
        lines.append(
            f"| {mode} | {metrics['recall_at_k']:.4f} "
            f"| {metrics['precision_at_k']:.4f} "
            f"| {metrics['mrr_at_k']:.4f} "
            f"| {metrics['ndcg_at_k']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            *[f"- {item}" for item in result["limitations"]],
        ]
    )
    return "\n".join(lines) + "\n"


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic MOMO Scholar evaluation demo."
    )
    parser.add_argument(
        "--evaluation-fixture",
        type=Path,
        default=DEFAULT_EVALUATION_FIXTURE,
    )
    parser.add_argument(
        "--retrieval-fixture",
        type=Path,
        default=DEFAULT_RETRIEVAL_FIXTURE,
    )
    parser.add_argument("--k", type=_positive_integer, default=3)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)

    result = build_baseline(
        evaluation_fixture=args.evaluation_fixture,
        retrieval_fixture=args.retrieval_fixture,
        k=args.k,
    )
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_markdown(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
