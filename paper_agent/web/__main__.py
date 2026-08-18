from __future__ import annotations

import argparse
from pathlib import Path

from paper_agent.web_server import run_web_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local-only MOMO TechScout Web API/UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--state-root", type=Path, default=Path("outputs/.web"))
    parser.add_argument("--output-root", type=Path, default=Path("outputs"))
    parser.add_argument("--dev-origin", action="append", default=[], help="Exact development browser origin; may be repeated")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--redis-url", help="Redis URL; requires a separate techscout-worker process")
    parser.add_argument("--queue-capacity", type=int, default=100)
    args = parser.parse_args()
    try:
        run_web_server(
            host=args.host,
            port=args.port,
            state_root=args.state_root,
            output_root=args.output_root,
            dev_origins=tuple(args.dev_origin),
            allow_network=args.allow_network,
            redis_url=args.redis_url,
            queue_capacity=args.queue_capacity,
        )
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
