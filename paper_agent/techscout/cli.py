from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from paper_agent.web_server import run_web_server, validate_server_binding


app = typer.Typer(
    help=(
        "MOMO TechScout local product. Fast Demo uses frozen synthetic evidence; "
        "Verified accepts live intent but live execution is not connected."
    ),
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Run MOMO TechScout product commands."""


@app.command()
def serve(
    host: Annotated[
        str,
        typer.Option(help="Bind host. Non-loopback requires --allow-network."),
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Bind port.")] = 8000,
    state_root: Annotated[
        Path,
        typer.Option(help="Directory for the local Web run registry."),
    ] = Path("outputs/.web"),
    output_root: Annotated[
        Path,
        typer.Option(help="Directory for generated run artifacts."),
    ] = Path("outputs"),
    dev_origin: Annotated[
        list[str] | None,
        typer.Option("--dev-origin", help="Exact development browser origin; repeatable."),
    ] = None,
    allow_network: Annotated[
        bool,
        typer.Option(help="Allow an unauthenticated non-loopback bind."),
    ] = False,
    redis_url: Annotated[
        str | None,
        typer.Option(help="Redis URL; dispatch runs to a separate techscout-worker."),
    ] = None,
    queue_capacity: Annotated[
        int,
        typer.Option(min=1, help="Maximum queued plus leased TechScout runs."),
    ] = 100,
) -> None:
    """Serve the local Web UI and API with a loopback-only default."""
    try:
        validate_server_binding(host, allow_network=allow_network)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--host") from None

    typer.echo("Fast Demo: frozen synthetic evidence; no live provider or Docker execution.")
    typer.echo(
        "Verified: completed_with_limitations (live_execution_unavailable); "
        "live verification is not connected."
    )
    run_web_server(
        host=host,
        port=port,
        state_root=state_root,
        output_root=output_root,
        dev_origins=tuple(dev_origin or ()),
        allow_network=allow_network,
        redis_url=redis_url,
        queue_capacity=queue_capacity,
    )


if __name__ == "__main__":
    app()
