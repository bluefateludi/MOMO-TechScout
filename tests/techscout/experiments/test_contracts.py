import pytest
from pydantic import ValidationError

from paper_agent.techscout.errors import FailureCode
from paper_agent.techscout.experiments import (
    ExecutionRequest,
    ExecutionTerminalStatus,
    ExperimentCheck,
    ExperimentRecipe,
    RecipeDisposition,
    ReviewedCommand,
)
from paper_agent.techscout.sandbox.types import NetworkAccess


def _check(check_id: str = "check:offline") -> ExperimentCheck:
    return ExperimentCheck(
        check_id=check_id,
        title="Offline check",
        description="Runs one fixed command without network access.",
        command=ReviewedCommand(
            argv=("python", "--version"),
            image="momo-techscout-sandbox:wave1",
        ),
    )


def test_offline_recipe_requires_unique_checks_and_no_network() -> None:
    with pytest.raises(ValidationError, match="identifiers must be unique"):
        ExperimentRecipe(
            recipe_id="recipe:duplicate@1",
            version="1.0.0",
            title="Duplicate",
            purpose="Reject ambiguous audit records.",
            disposition=RecipeDisposition.OFFLINE_EXECUTABLE,
            checks=(_check(), _check()),
        )

    networked = _check().model_copy(
        update={
            "command": _check().command.model_copy(
                update={"network_access": NetworkAccess.INSTALL_ONLY}
            )
        }
    )
    with pytest.raises(ValidationError, match="cannot request network access"):
        ExperimentRecipe(
            recipe_id="recipe:networked@1",
            version="1.0.0",
            title="Networked",
            purpose="Must fail closed.",
            disposition=RecipeDisposition.OFFLINE_EXECUTABLE,
            checks=(networked,),
        )


def test_research_only_recipe_cannot_claim_checks() -> None:
    with pytest.raises(ValidationError, match="requires only a reason"):
        ExperimentRecipe(
            recipe_id="recipe:research-only@2",
            version="2.0.0",
            title="Research only",
            purpose="Record an unavailable procedure.",
            disposition=RecipeDisposition.RESEARCH_ONLY,
            checks=(_check(),),
            research_only_reason="No reviewed execution procedure exists.",
        )


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReviewedCommand(
            argv=("python", "--version"),
            image="momo-techscout-sandbox:wave1",
            shell=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("recipe", {"recipe_id": "recipe:attacker@1"}),
        ("command", ["sh", "-c", "whoami"]),
        ("shell", True),
        ("network_access", "host"),
        ("filesystem", {"mount": "/"}),
        ("tools", ["shell.exec"]),
    ),
)
def test_execution_request_cannot_smuggle_unreviewed_capabilities(
    field: str,
    value: object,
) -> None:
    payload = {
        "execution_id": "experiment:adversarial",
        "subject_id": "subject:python-runtime",
        "recipe_id": "recipe:python-runtime-offline@1",
        "idempotency_key": "idempotency:adversarial",
        field: value,
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExecutionRequest.model_validate(payload)


def test_terminal_contract_rejects_failure_code_mismatch() -> None:
    from datetime import datetime, timezone

    from paper_agent.techscout.experiments.contracts import (
        ExecutionBudget,
        ExecutionFailure,
        ExperimentResult,
    )

    with pytest.raises(ValidationError, match="failure code must match"):
        ExperimentResult(
            execution_id="experiment:mismatch",
            subject_id="subject:runtime",
            recipe_id="recipe:runtime@1",
            recipe_version="1.0.0",
            recipe_sha256="a" * 64,
            terminal_status=ExecutionTerminalStatus.CANCELLED,
            terminal_reason="Cancelled.",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            budget=ExecutionBudget(),
            check_results=(),
            artifacts=(),
            measurements=(),
            failure=ExecutionFailure(
                code=FailureCode.POC_TIMEOUT,
                message="Wrong code.",
            ),
            cleanup_complete=True,
        )
