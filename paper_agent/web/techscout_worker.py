from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path

from paper_agent.config import load_settings
from paper_agent.web.registry import RunRegistry
from paper_agent.web.task_queue import RedisRunQueue
from paper_agent.web.techscout_execution import TechScoutSingleRunExecutor
from paper_agent.web.verified_composition import make_verified_services_factory


def run_until_stopped(
    executor: TechScoutSingleRunExecutor,
    stopped: threading.Event,
    *,
    poll_seconds: float = 0.5,
) -> int:
    """Return non-zero when the supervised queue runner becomes fatal."""
    while not stopped.wait(poll_seconds):
        if executor.failed:
            return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an at-least-once MOMO TechScout Redis worker.",
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("TECHSCOUT_REDIS_URL", "redis://localhost:6379/0"),
    )
    parser.add_argument(
        "--state-root", type=Path,
        default=Path(os.environ.get("TECHSCOUT_STATE_ROOT", "outputs/.web")),
    )
    parser.add_argument(
        "--output-root", type=Path,
        default=Path(os.environ.get("TECHSCOUT_OUTPUT_ROOT", "outputs")),
    )
    parser.add_argument("--queue-capacity", type=int, default=100)
    args = parser.parse_args()

    state_root = args.state_root.resolve()
    output_root = args.output_root.resolve()
    registry = RunRegistry(state_root / "run-registry.sqlite3")
    queue = RedisRunQueue.from_url(args.redis_url, capacity=args.queue_capacity)
    verified_factory = make_verified_services_factory(
        output_root=output_root, state_root=state_root, settings_loader=load_settings,
    )
    executor = TechScoutSingleRunExecutor(
        registry, output_root, verified_services_factory=verified_factory,
        queue=queue, queue_capacity=args.queue_capacity,
    )
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    executor.start()
    exit_code = 0
    try:
        exit_code = run_until_stopped(executor, stopped)
    finally:
        executor.close()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
