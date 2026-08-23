from __future__ import annotations

import ipaddress
import logging
from pathlib import Path


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def validate_server_binding(host: str, *, allow_network: bool) -> None:
    if not _is_loopback_host(host) and not allow_network:
        raise ValueError(
            "non-loopback binding requires --allow-network; "
            "the local Web product has no authentication"
        )


def run_web_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    state_root: Path = Path("outputs/.web"),
    output_root: Path = Path("outputs"),
    dev_origins: tuple[str, ...] = (),
    allow_network: bool = False,
    redis_url: str | None = None,
    queue_capacity: int = 100,
) -> None:
    validate_server_binding(host, allow_network=allow_network)
    if not _is_loopback_host(host):
        logging.warning("MOMO TechScout Web is binding beyond loopback without authentication")

    import uvicorn

    from paper_agent.web.app import create_app
    from paper_agent.web.task_queue import RedisRunQueue

    queue = (
        RedisRunQueue.from_url(redis_url, capacity=queue_capacity)
        if redis_url is not None
        else None
    )

    app = create_app(
        state_root=state_root,
        output_root=output_root,
        allowed_origins=dev_origins,
        queue_capacity=queue_capacity if queue is not None else 4,
        techscout_queue=queue,
        embedded_techscout_worker=queue is None,
    )
    uvicorn.run(app, host=host, port=port, workers=1)
