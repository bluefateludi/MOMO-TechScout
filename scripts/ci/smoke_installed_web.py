from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def _get_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=1) as response:
        return json.load(response)


def _post_json(
    url: str,
    payload: dict[str, object] | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    headers = {"Content-Type": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else b"",
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.load(response)


def _run_fast_demo(base_url: str) -> None:
    body: dict[str, object] = {
        "question": "Choose a local vector store for a Python 3.11 RAG service.",
        "project_context": (
            "A single-node local service with no separately managed database."
        ),
        "environment": {
            "python_version": "3.11",
            "operating_system": "linux-container",
            "deployment": "single-node-local",
        },
        "hard_constraints": [
            "local persistence",
            "metadata equality filtering",
            "no separately managed database",
        ],
        "candidates": [
            {"name": "Chroma"},
            {"name": "Qdrant Local"},
            {"name": "pgvector"},
        ],
        "mode": "fast",
    }
    created = _post_json(f"{base_url}/api/v2/runs", body)
    run_id = str(created["id"])
    requirements = [
        {
            "requirement_id": f"requirement:must-have-{index}",
            "kind": "hard_constraint",
            "statement": statement,
        }
        for index, statement in enumerate(body["hard_constraints"])
    ]
    _post_json(
        f"{base_url}/api/v2/runs/{run_id}/workflow/requirements-review",
        {"requirements": requirements},
        idempotency_key="wheel-smoke-review",
    )
    criteria = _post_json(
        f"{base_url}/api/v2/runs/{run_id}/workflow/confirm-requirements",
        idempotency_key="wheel-smoke-requirements",
    )
    contract_id = criteria["selection_criteria"]["contract_id"]
    _post_json(
        f"{base_url}/api/v2/runs/{run_id}/workflow/confirm-criteria",
        {"contract_id": contract_id},
        idempotency_key="wheel-smoke-criteria",
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        detail = _get_json(f"{base_url}/api/v2/runs/{run_id}")
        if detail["status"] not in {"queued", "running"}:
            break
        time.sleep(0.1)
    else:
        raise SystemExit("installed Fast Demo did not terminalize within 120 seconds")
    if detail["status"] != "completed" or detail["synthetic"] is not True:
        raise SystemExit(f"installed Fast Demo ended unexpectedly: {detail}")
    report = _get_json(f"{base_url}/api/v2/runs/{run_id}/report")
    if report["verdict"] != "recommended" or report["synthetic"] is not True:
        raise SystemExit(f"installed Fast Demo report was unexpected: {report}")
    print(f"FAST_DEMO_RUN_ID={run_id}")
    print("FAST_DEMO_STATUS=completed")
    print("FAST_DEMO_SYNTHETIC=true")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test an installed TechScout Web entry point outside its checkout."
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--techscout", default="techscout")
    args = parser.parse_args()

    executable = shutil.which(args.techscout)
    if executable is None:
        raise SystemExit("techscout is not installed on PATH")

    root = Path(tempfile.mkdtemp(prefix="momo-techscout-wheel-"))
    log_path = root / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                executable,
                "serve",
                "--port",
                str(args.port),
                "--state-root",
                "state",
                "--output-root",
                "outputs",
            ],
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        base_url = f"http://127.0.0.1:{args.port}"
        try:
            ready_url = f"{base_url}/health/ready"
            for _ in range(50):
                if process.poll() is not None:
                    log.flush()
                    raise SystemExit(
                        f"installed server exited early:\n{log_path.read_text(encoding='utf-8')}"
                    )
                try:
                    if _get_json(ready_url) == {"status": "ready"}:
                        break
                except Exception:
                    time.sleep(0.2)
            else:
                raise SystemExit("installed server did not become ready within 10 seconds")

            with urllib.request.urlopen(f"{base_url}/", timeout=2) as response:
                body = response.read().decode("utf-8")
                if response.status != 200 or '<div id="root"></div>' not in body:
                    raise SystemExit("installed server did not serve the React root")
            _run_fast_demo(base_url)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
