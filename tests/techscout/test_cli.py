from typer.testing import CliRunner

import paper_agent.techscout.cli as cli_module
from paper_agent.techscout.cli import app


def test_help_states_fast_demo_and_verified_boundaries() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Fast Demo" in result.output
    assert "frozen synthetic evidence" in result.output
    assert "Verified" in result.output
    assert "live execution is not connected" in result.output
    assert "serve" in result.output


def test_serve_uses_loopback_defaults_and_explains_status(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli_module,
        "run_web_server",
        lambda **kwargs: calls.append(kwargs),
    )

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 0
    assert calls == [{
        "host": "127.0.0.1",
        "port": 8000,
        "state_root": cli_module.Path("outputs/.web"),
        "output_root": cli_module.Path("outputs"),
        "dev_origins": (),
        "allow_network": False,
        "redis_url": None,
        "queue_capacity": 100,
    }]
    assert "Fast Demo: frozen synthetic evidence" in result.output
    assert "Verified: completed_with_limitations" in result.output
    assert "live_execution_unavailable" in result.output


def test_serve_rejects_non_loopback_without_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "run_web_server",
        lambda **_: (_ for _ in ()).throw(AssertionError("server must not start")),
    )

    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code != 0
    assert "--allow-network" in result.output
    assert "no authentication" in result.output
