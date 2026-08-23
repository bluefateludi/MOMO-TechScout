from __future__ import annotations

import argparse
import json
from pathlib import Path

from paper_agent.config import load_settings
from paper_agent.techscout.eval.live_phase1 import (
    LivePreflightError,
    Phase1LiveExecutor,
    run_live_preflight,
)
from paper_agent.techscout.eval.live_web_runner import VerifiedWebLiveCaseRunner


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deny-by-default TechScout Live Eval Phase 1 preflight."
    )
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--execute-smoke",
        action="store_true",
        help="After successful preflight, spend only within the signed authorization on case 1/repetition 1.",
    )
    args = parser.parse_args()
    try:
        registration, authorization, attestation = run_live_preflight(
            registration_path=args.registration,
            authorization_path=args.authorization,
            output_directory=args.output_dir,
            repository_root=args.repository_root,
        )
    except LivePreflightError as error:
        print(json.dumps({"authorized": False, "blockers": error.blockers}, indent=2))
        return 2
    print(attestation.model_dump_json(indent=2))
    if not args.execute_smoke:
        print("Preflight passed. No paid execution was requested.")
        return 0
    runner = VerifiedWebLiveCaseRunner(
        settings_loader=load_settings,
        exact_model_revision=authorization.exact_model_revision,
        pricing=authorization.pricing,
        maximum_prompt_tokens=authorization.maximum_prompt_tokens_per_run,
        maximum_completion_tokens=authorization.maximum_completion_tokens_per_run,
    )
    observations = Phase1LiveExecutor(runner).execute(
        registration=registration,
        authorization=authorization,
        attestation=attestation,
        output_directory=args.output_dir,
        smoke=True,
    )
    print(json.dumps({"sealed": True, "run_keys": [[item.case_id, item.repetition] for item in observations]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
