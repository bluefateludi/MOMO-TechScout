from __future__ import annotations

import argparse
import json
from pathlib import Path

from paper_agent.techscout.eval.live_contracts import (
    LiveCaseCategory,
    load_live_evaluation_registration,
)


DEFAULT_REGISTRATION = Path("evaluations/techscout-live-v1/registration.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the TechScout Live Eval V1 draft without running it."
    )
    parser.add_argument(
        "registration",
        nargs="?",
        type=Path,
        default=DEFAULT_REGISTRATION,
    )
    args = parser.parse_args()

    registration, sha256 = load_live_evaluation_registration(args.registration)
    category_counts = {
        category.value: sum(case.category is category for case in registration.cases)
        for category in LiveCaseCategory
    }
    print(
        json.dumps(
            {
                "suite_id": registration.suite_id,
                "status": registration.status,
                "case_count": len(registration.cases),
                "category_counts": category_counts,
                "baseline_git_commit": registration.baseline_git_commit,
                "execution_authorized": registration.policy.execution_authorized,
                "maximum_approved_cost_usd": registration.policy.maximum_approved_cost_usd,
                "required_authorities": registration.required_authorities.model_dump(
                    mode="json"
                ),
                "registration_sha256": sha256,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
