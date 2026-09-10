import subprocess
from types import SimpleNamespace

from typer.testing import CliRunner

import paper_agent.techscout.cli as cli_module
from paper_agent.config import Settings
from paper_agent.techscout.cli import app
from paper_agent.techscout.diagnostics import (
    DockerProbe,
    VerifiedStartupReport,
    diagnose_verified_startup,
    probe_docker,
)


def _settings(**overrides: object) -> Settings:
    values = {
        "tavily_api_key": "tavily-private",
        "dashscope_api_key": "dashscope-private",
        "github_token": "github-private",
        "techscout_docker_install_network": "techscout-egress",
        "techscout_docker_egress_allowlist_enforced": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_ready_report_checks_full_verified_demo_without_exposing_credentials() -> None:
    report = diagnose_verified_startup(
        _settings(), docker=DockerProbe(ready=True, code="docker_ready")
    )

    assert report.status == "ready"
    assert [check.code for check in report.checks] == [
        "configuration_valid",
        "live_search_configured",
        "decision_provider_configured",
        "github_authenticated",
        "docker_ready",
        "docker_install_network_ready",
    ]
    serialized = report.model_dump_json()
    assert "private" not in serialized


def test_missing_live_dependencies_are_stable_actionable_diagnostics() -> None:
    report = diagnose_verified_startup(
        Settings(), docker=DockerProbe(ready=False, code="docker_cli_missing")
    )

    assert report.status == "not_ready"
    by_code = {check.code: check for check in report.checks}
    assert by_code["live_search_unconfigured"].status == "error"
    assert "TAVILY_API_KEY" in by_code["live_search_unconfigured"].action
    assert by_code["decision_provider_unconfigured"].status == "error"
    assert by_code["github_unauthenticated"].status == "warning"
    assert by_code["docker_cli_missing"].status == "error"
    assert by_code["docker_install_network_unconfigured"].status == "error"


def test_invalid_install_network_is_reported_without_echoing_its_value() -> None:
    report = diagnose_verified_startup(
        _settings(techscout_docker_install_network="private network"),
        docker=DockerProbe(ready=True, code="docker_ready"),
    )

    check = report.checks[-1]
    assert report.status == "not_ready"
    assert check.code == "docker_install_network_invalid"
    assert "private network" not in check.model_dump_json()


def test_docker_probe_discards_raw_command_failures() -> None:
    missing = probe_docker(executable_locator=lambda _: None)
    assert missing == DockerProbe(ready=False, code="docker_cli_missing")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 3, output="token=private")

    timed_out = probe_docker(
        executable_locator=lambda _: "docker", command_runner=timeout
    )
    assert timed_out == DockerProbe(ready=False, code="docker_probe_timeout")
    assert "private" not in timed_out.model_dump_json()

    unavailable = probe_docker(
        executable_locator=lambda _: "docker",
        command_runner=lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="credential=private"
        ),
    )
    assert unavailable == DockerProbe(ready=False, code="docker_daemon_unavailable")
    assert "private" not in unavailable.model_dump_json()


def test_doctor_json_is_machine_readable_and_uses_exit_status(monkeypatch) -> None:
    expected = diagnose_verified_startup(
        _settings(), docker=DockerProbe(ready=True, code="docker_ready")
    )
    monkeypatch.setattr(cli_module, "diagnose_verified_startup", lambda settings: expected)
    monkeypatch.setattr(cli_module, "load_settings", _settings)

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    assert VerifiedStartupReport.model_validate_json(result.output) == expected


def test_doctor_hides_configuration_exception_details(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda: (_ for _ in ()).throw(ValueError("api_key=private raw config")),
    )

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert "private" not in result.output
    report = VerifiedStartupReport.model_validate_json(result.output)
    assert report.checks[0].code == "configuration_invalid"
