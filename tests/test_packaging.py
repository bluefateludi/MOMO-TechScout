import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def _load_pyproject() -> dict:
    repository_root = Path(__file__).resolve().parents[1]
    return tomllib.loads(
        (repository_root / "pyproject.toml").read_text(encoding="utf-8")
    )


def test_pyproject_uses_explicit_setuptools_package_discovery() -> None:
    pyproject = _load_pyproject()

    assert pyproject["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "paper_agent",
        "paper_agent.*",
    ]
    assert pyproject["build-system"] == {
        "requires": ["setuptools>=61"],
        "build-backend": "setuptools.build_meta",
    }
    assert (
        "tomli>=2; python_version < '3.11'"
        in pyproject["project"]["optional-dependencies"]["dev"]
    )


def test_pyproject_exposes_techscout_and_preserves_paper_agent_commands() -> None:
    scripts = _load_pyproject()["project"]["scripts"]

    assert scripts["techscout"] == "paper_agent.techscout.cli:app"
    assert scripts["paper-agent"] == "paper_agent.cli:app"


def test_web_package_declares_the_supported_node_runtime() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    package = json.loads(
        (repository_root / "web" / "package.json").read_text(encoding="utf-8")
    )

    assert package["engines"]["node"] == "^20.19.0 || >=22.12.0"


def test_pdf_runtime_and_agpl_metadata_are_declared() -> None:
    pyproject = _load_pyproject()
    assert "pymupdf>=1.24,<2" in pyproject["project"]["dependencies"]
    assert "pdf" not in pyproject["project"].get("optional-dependencies", {})
    assert pyproject["project"]["license"] == {"file": "LICENSE"}
    assert "llm" not in pyproject["project"].get("optional-dependencies", {})
    assert not any("openai" in dependency.lower() for dependency in pyproject["project"]["dependencies"])
    assert pyproject["tool"]["setuptools"]["license-files"] == [
        "LICENSE", "THIRD_PARTY_NOTICES.md"
    ]


def test_observability_extra_is_optional() -> None:
    pyproject = _load_pyproject()
    extra = pyproject['project']['optional-dependencies']['observability']
    assert 'opentelemetry-api>=1.38,<2' in extra
    assert 'opentelemetry-sdk>=1.38,<2' in extra
    assert 'opentelemetry-exporter-otlp-proto-http>=1.38,<2' in extra
    assert not any(
        dependency.startswith('opentelemetry')
        for dependency in pyproject['project']['dependencies']
    )


def test_agpl_and_pymupdf_notices_are_present() -> None:
    root = Path(__file__).resolve().parents[1]
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (
        root / "LICENSE"
    ).read_text(encoding="utf-8")
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "PyMuPDF" in notices
    assert "MuPDF" in notices
    assert "AGPL" in notices


def test_wheel_excludes_runtime_outputs_directory(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    wheel_dir = tmp_path / "wheels"
    project.mkdir()
    wheel_dir.mkdir()

    shutil.copy2(repository_root / "pyproject.toml", project / "pyproject.toml")
    shutil.copy2(repository_root / "LICENSE", project / "LICENSE")
    shutil.copy2(
        repository_root / "THIRD_PARTY_NOTICES.md",
        project / "THIRD_PARTY_NOTICES.md",
    )
    shutil.copytree(repository_root / "paper_agent", project / "paper_agent")
    outputs = project / "outputs"
    outputs.mkdir()
    (outputs / "example.txt").write_text("runtime output", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(project),
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--disable-pip-version-check",
            "--wheel-dir",
            str(wheel_dir),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    diagnostics = (
        f"command: {' '.join(result.args)}\n"
        f"exit code: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert result.returncode == 0, diagnostics
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, (
        f"expected exactly one wheel, found {[wheel.name for wheel in wheels]}\n"
        f"{diagnostics}"
    )
    with zipfile.ZipFile(wheels[0]) as wheel:
        members = wheel.namelist()
        entry_points = wheel.read(next(
            member for member in members if member.endswith("entry_points.txt")
        )).decode("utf-8")
    assert any(member.startswith("paper_agent/") for member in members)
    assert "paper-agent = paper_agent.cli:app" in entry_points
    assert "techscout = paper_agent.techscout.cli:app" in entry_points
    assert "paper_agent/web/static/index.html" in members
    assert any(
        member.startswith("paper_agent/web/static/assets/")
        for member in members
    )
    assert any(member.endswith("licenses/LICENSE") for member in members)
    assert any(member.endswith("licenses/THIRD_PARTY_NOTICES.md") for member in members)
    assert not any(member.startswith("outputs/") for member in members)
