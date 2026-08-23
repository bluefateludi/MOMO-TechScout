"""Run MOMO Scholar's deterministic offline evaluation demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Sequence

from paper_agent.eval.retrieval_runner import evaluate_retrieval_fixture
from paper_agent.eval.runner import evaluate_fixture


DEFAULT_EVALUATION_FIXTURE = Path("tests/fixtures/eval_cases.json")
DEFAULT_RETRIEVAL_FIXTURE = Path("tests/fixtures/retrieval_eval_cases.json")
DEFAULT_MANIFEST = Path("tests/fixtures/evaluation_demo_manifest.json")
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
    retrieval_result["case_count"] = len(retrieval_result["cases"])
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


def load_manifest(path: Path) -> dict[str, object]:
    """Load the versioned synthetic regression contract."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("demo manifest must be a JSON object")
    if manifest.get("schema_version") != 1:
        raise ValueError("demo manifest schema_version must be 1")
    for field in ("baseline", "scope", "inputs", "parameters", "expected"):
        if field not in manifest:
            raise ValueError(f"demo manifest missing required field: {field}")
    return manifest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compare_expected(
    actual: object,
    expected: object,
    *,
    path: str,
    tolerance: float,
) -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        errors: list[str] = []
        for key, expected_value in expected.items():
            if key not in actual:
                errors.append(f"{path}.{key}: missing from result")
                continue
            errors.extend(
                _compare_expected(
                    actual[key],
                    expected_value,
                    path=f"{path}.{key}",
                    tolerance=tolerance,
                )
            )
        return errors
    if (
        isinstance(expected, (int, float))
        and not isinstance(expected, bool)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
    ):
        if abs(float(actual) - float(expected)) <= tolerance:
            return []
    elif actual == expected:
        return []
    return [f"{path}: expected {expected!r}, got {actual!r}"]


def verify_baseline(
    result: dict[str, object],
    *,
    manifest: dict[str, object],
    evaluation_fixture: Path,
    retrieval_fixture: Path,
) -> list[str]:
    """Return every manifest mismatch without hiding later regressions."""
    errors: list[str] = []
    if result.get("baseline") != manifest.get("baseline"):
        errors.append(
            f"baseline: expected {manifest.get('baseline')!r}, "
            f"got {result.get('baseline')!r}"
        )

    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        return errors + ["manifest.inputs: expected object"]
    for label, path in (
        ("evaluation_fixture", evaluation_fixture),
        ("retrieval_fixture", retrieval_fixture),
    ):
        entry = inputs.get(label)
        if not isinstance(entry, dict):
            errors.append(f"manifest.inputs.{label}: expected object")
            continue
        expected_hash = entry.get("sha256")
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            readable_label = label.replace("_", " ")
            errors.append(
                f"{readable_label} sha256: expected {expected_hash!r}, "
                f"got {actual_hash!r}"
            )

    parameters = manifest.get("parameters")
    expected = manifest.get("expected")
    if not isinstance(parameters, dict):
        errors.append("manifest.parameters: expected object")
    else:
        errors.extend(
            _compare_expected(
                result.get("inputs", {}),
                {"k": parameters.get("k")},
                path="inputs",
                tolerance=0.0,
            )
        )
    if not isinstance(expected, dict):
        errors.append("manifest.expected: expected object")
        return errors

    tolerance = manifest.get("numeric_tolerance", 0.0)
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool):
        errors.append("manifest.numeric_tolerance: expected number")
        return errors
    errors.extend(
        _compare_expected(
            result,
            expected,
            path="result",
            tolerance=float(tolerance),
        )
    )
    return errors


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
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Versioned regression manifest used by --check.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when fixture hashes, K, case counts, or summary metrics drift.",
    )
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
    if args.check:
        errors = verify_baseline(
            result,
            manifest=load_manifest(args.manifest),
            evaluation_fixture=args.evaluation_fixture,
            retrieval_fixture=args.retrieval_fixture,
        )
        if errors:
            print("Baseline check: FAIL", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
            return 1
        print(f"Baseline check: PASS ({args.manifest.as_posix()})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
