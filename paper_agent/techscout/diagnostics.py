"""Safe, read-only startup diagnostics for the bounded Verified demo path."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from typing import Literal

from pydantic import Field, model_validator
from typing_extensions import Self

from paper_agent.config import Settings
from paper_agent.techscout.models import TechScoutModel
from paper_agent.techscout.sandbox import InstallNetworkPolicy


DiagnosticStatus = Literal["ok", "warning", "error"]
DockerProbeCode = Literal[
    "docker_ready",
    "docker_cli_missing",
    "docker_probe_timeout",
    "docker_daemon_unavailable",
]


class DockerProbe(TechScoutModel):
    ready: bool
    code: DockerProbeCode

    @model_validator(mode="after")
    def status_matches_code(self) -> Self:
        if self.ready != (self.code == "docker_ready"):
            raise ValueError("Docker readiness must match its stable code")
        return self


class StartupCheck(TechScoutModel):
    component: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    status: DiagnosticStatus
    code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1)
    action: str = Field(min_length=1)


class VerifiedStartupReport(TechScoutModel):
    status: Literal["ready", "not_ready"]
    checks: tuple[StartupCheck, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def status_matches_checks(self) -> Self:
        expected = (
            "not_ready"
            if any(check.status == "error" for check in self.checks)
            else "ready"
        )
        if self.status != expected:
            raise ValueError("startup status must reflect error checks")
        return self


def probe_docker(
    *,
    executable_locator: Callable[[str], str | None] = shutil.which,
    command_runner: Callable[..., object] = subprocess.run,
) -> DockerProbe:
    """Check Docker CLI and daemon availability without retaining raw output."""
    executable = executable_locator("docker")
    if executable is None:
        return DockerProbe(ready=False, code="docker_cli_missing")
    try:
        completed = command_runner(
            [executable, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return DockerProbe(ready=False, code="docker_probe_timeout")
    except (OSError, subprocess.SubprocessError):
        return DockerProbe(ready=False, code="docker_daemon_unavailable")
    if getattr(completed, "returncode", 1) != 0:
        return DockerProbe(ready=False, code="docker_daemon_unavailable")
    return DockerProbe(ready=True, code="docker_ready")


def diagnose_verified_startup(
    settings: Settings,
    *,
    docker: DockerProbe | None = None,
) -> VerifiedStartupReport:
    """Return stable readiness facts without making provider or research calls."""
    checks = [
        StartupCheck(
            component="configuration",
            status="ok",
            code="configuration_valid",
            message="Verified settings loaded successfully.",
            action="No configuration syntax action is required.",
        ),
        _credential_check(
            configured=bool(settings.tavily_api_key),
            component="live_search",
            missing_status="error",
            configured_code="live_search_configured",
            missing_code="live_search_unconfigured",
            configured_message="The bounded Tavily live-search provider is configured.",
            missing_message="The bounded live-search provider is not configured.",
            action="Set TAVILY_API_KEY in the process environment or local .env.",
        ),
        _credential_check(
            configured=bool(settings.dashscope_api_key),
            component="decision_provider",
            missing_status="error",
            configured_code="decision_provider_configured",
            missing_code="decision_provider_unconfigured",
            configured_message="The bounded decision/report provider credential is configured.",
            missing_message="Verified model-backed decision/report authority is unavailable.",
            action=(
                "Set DASHSCOPE_API_KEY, then use the separately authorized bounded "
                "Hero smoke preflight with an exact model revision."
            ),
        ),
        _credential_check(
            configured=bool(settings.github_token),
            component="github",
            missing_status="warning",
            configured_code="github_authenticated",
            missing_code="github_unauthenticated",
            configured_message="Authenticated read-only GitHub research is configured.",
            missing_message="GitHub research will use unauthenticated public requests.",
            action="Set GITHUB_TOKEN to reduce public API rate-limit risk.",
        ),
        _docker_check(docker or probe_docker()),
        _install_network_check(settings),
    ]
    status = "not_ready" if any(item.status == "error" for item in checks) else "ready"
    return VerifiedStartupReport(status=status, checks=tuple(checks))


def configuration_failure_report() -> VerifiedStartupReport:
    return VerifiedStartupReport(
        status="not_ready",
        checks=(
            StartupCheck(
                component="configuration",
                status="error",
                code="configuration_invalid",
                message="Verified settings could not be loaded safely.",
                action="Review local .env values against .env.example and retry.",
            ),
        ),
    )


def _credential_check(
    *,
    configured: bool,
    component: str,
    missing_status: Literal["warning", "error"],
    configured_code: str,
    missing_code: str,
    configured_message: str,
    missing_message: str,
    action: str,
) -> StartupCheck:
    return StartupCheck(
        component=component,
        status="ok" if configured else missing_status,
        code=configured_code if configured else missing_code,
        message=configured_message if configured else missing_message,
        action="No action is required." if configured else action,
    )


def _docker_check(probe: DockerProbe) -> StartupCheck:
    details = {
        "docker_ready": (
            "ok",
            "Docker CLI can reach the local daemon.",
            "No Docker availability action is required.",
        ),
        "docker_cli_missing": (
            "error",
            "Docker CLI is not available on PATH.",
            "Install Docker CLI and ensure the docker command is on PATH.",
        ),
        "docker_probe_timeout": (
            "error",
            "Docker daemon readiness did not respond within three seconds.",
            "Start or restart Docker Engine/Desktop, then run techscout doctor again.",
        ),
        "docker_daemon_unavailable": (
            "error",
            "Docker CLI could not reach a healthy daemon.",
            "Start Docker Engine/Desktop and confirm docker version succeeds.",
        ),
    }
    status, message, action = details[probe.code]
    return StartupCheck(
        component="docker",
        status=status,
        code=probe.code,
        message=message,
        action=action,
    )


def _install_network_check(settings: Settings) -> StartupCheck:
    network = settings.techscout_docker_install_network
    if network is None:
        return StartupCheck(
            component="docker_install_network",
            status="error",
            code="docker_install_network_unconfigured",
            message="The reviewed install stage has no dedicated Docker network.",
            action=(
                "Create an externally allowlisted install network, then set "
                "TECHSCOUT_DOCKER_INSTALL_NETWORK."
            ),
        )
    if not settings.techscout_docker_egress_allowlist_enforced:
        return StartupCheck(
            component="docker_install_network",
            status="error",
            code="docker_egress_allowlist_unconfirmed",
            message="Destination allowlist enforcement is not confirmed.",
            action=(
                "Enforce the approved PyPI destination allowlist externally, then set "
                "TECHSCOUT_DOCKER_EGRESS_ALLOWLIST_ENFORCED=true."
            ),
        )
    try:
        InstallNetworkPolicy(
            docker_network=network,
            allowed_destinations=("pypi.org", "files.pythonhosted.org"),
            egress_allowlist_enforced=True,
        )
    except ValueError:
        return StartupCheck(
            component="docker_install_network",
            status="error",
            code="docker_install_network_invalid",
            message="The configured Docker install network violates the reviewed policy.",
            action="Use a dedicated network name that is not bridge, host, default, or none.",
        )
    return StartupCheck(
        component="docker_install_network",
        status="ok",
        code="docker_install_network_ready",
        message="The reviewed install network policy is configured.",
        action="No install-network configuration action is required.",
    )
