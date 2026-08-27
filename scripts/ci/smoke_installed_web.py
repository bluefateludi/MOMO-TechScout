from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def _get_json(url: str) -> dict[str, str]:
    with urllib.request.urlopen(url, timeout=1) as response:
        return json.load(response)


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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        ready_url = f"http://127.0.0.1:{args.port}/health/ready"
        for _ in range(50):
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise SystemExit(f"installed server exited early:\n{output}")
            try:
                if _get_json(ready_url) == {"status": "ready"}:
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise SystemExit("installed server did not become ready within 10 seconds")

        with urllib.request.urlopen(
            f"http://127.0.0.1:{args.port}/", timeout=2
        ) as response:
            body = response.read().decode("utf-8")
            if response.status != 200 or '<div id="root"></div>' not in body:
                raise SystemExit("installed server did not serve the React root")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
