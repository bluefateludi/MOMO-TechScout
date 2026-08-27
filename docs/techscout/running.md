# Run modes and operator guide

The words Fast, Live, and Offline describe evidence/execution authority, not just a speed toggle. Do not use them interchangeably in a demo.

> **Current public-journey blocker:** the commit-scoped
> [M0 Product Gate inspection](../acceptance/2026-08-27-m0-product-gate.md)
> found that the homepage's visible review is not persisted into the required
> Workflow transitions. Submitting either mode creates a queued run, but the run
> is not executable until Requirements Review and Criteria Confirmation reach
> `research_ready` through the existing API. The mode table describes execution
> authority after that gate; it does not claim the default Web journey is closed.

## Mode truth table

| User-facing path | Request/API value | Evidence and execution | Current outcome |
|---|---|---|---|
| Fast Demo | `fast` | Frozen synthetic source records and deterministic synthetic PoC responses pass through the real Harness, Skill policy, local stdio MCP transport, checkpoints, gate, artifacts, and Trace. No provider, external research network, or Docker is used. | Implemented. A `completed` result proves the fixture vertical slice passed, not that a real candidate was verified. |
| Verified | `verified` | Bounded Tavily/HTTPS/GitHub research with explicit cache fallback, candidate-scoped context, and reviewed Docker PoCs for Chroma/Qdrant Local. | `completed` only when live authority and required PoCs pass; otherwise bounded `completed_with_limitations`/`no_safe_winner` or safe `failed`. |
| Offline fixture/replay | no new research run | Bundled immutable or frontend mock data; the lifecycle may be simulated. | Implemented for UI review and deterministic acceptance only. |
| Live authority | part of `verified` | Refreshes missing/stale official/GitHub evidence and records `live`, `cache`, or `unavailable` per candidate. | Implemented for the fixed Hero Case; external success is never fabricated when credentials/network are absent. |

## Start with the TechScout CLI

Prerequisites are Python 3.10+, Node.js `^20.19.0` or `>=22.12.0`, npm, and a local checkout. Create a virtual environment, then activate it in the shell that will run both npm and TechScout:

```console
python -m venv .venv
```

Use `source .venv/bin/activate` on macOS/Linux or `.\.venv\Scripts\Activate.ps1` in Windows PowerShell. Then run:

```console
python -m pip install -e .
cd web
npm ci
npm run build
cd ..
techscout doctor
techscout serve
```

`techscout doctor` is a read-only startup check for the complete Verified demo.
It does not call Tavily, DashScope, GitHub, or any other provider, and it does
not start a container. It validates local configuration, checks whether Docker
can reach the daemon within three seconds, and confirms that the reviewed install
stage has a dedicated externally enforced destination-allowlisted network. A
non-ready report exits with status 1 and gives stable codes plus an operator
action. Use `techscout doctor --json` for automation.

The complete Verified demo is ready only when `TAVILY_API_KEY`, Docker, and the
reviewed install network are ready. `DASHSCOPE_API_KEY` enables optional bounded
model-assisted drafting; the deterministic Gate remains authoritative without
it. `GITHUB_TOKEN` is optional for public read-only access but reduces rate-limit
risk. Never set `TECHSCOUT_DOCKER_EGRESS_ALLOWLIST_ENFORCED=true` until the named
network is actually restricted externally to approved package destinations.

Then open `http://127.0.0.1:8000`. The v2 API is under `/api/v2/runs`; the UI
can submit `fast` or `verified`, but on the M0-inspected baseline it does not
persist the Workflow confirmations required to leave `queued`. The default
server is single-process and loopback-only. Binding beyond loopback requires the
explicit CLI flag and is not recommended because authentication is not
implemented.

`techscout --help`, `techscout doctor --help`, and `techscout serve --help` show
the current mode and readiness boundaries. A non-loopback bind is rejected unless
the operator explicitly adds `--allow-network`:

```console
techscout serve --host 0.0.0.0 --allow-network
```

That opt-in exposes an unauthenticated local product and must be protected by the operator's network boundary. The compatibility command `python -m paper_agent.web` remains available. The installed `paper-agent` console script still addresses the historical Scholar workflow.

For an optional API/worker process split backed by Redis, see
[Backend reliability](backend-reliability.md). That mode is explicitly
at-least-once: the SQLite Registry remains the status authority while Redis only
provides dispatch, leases, heartbeats, rate limiting, and dead-letter routing.

## Start with Docker Compose

Prerequisites: Docker Engine with Docker Compose v2 and a local checkout.

```console
docker compose up --build
```

Then open `http://127.0.0.1:8000`. Compose binds the published host port to loopback, runs the application with a read-only root filesystem and dropped capabilities, and stores Web state/artifacts in the `techscout-data` named volume. Stop it with `docker compose down`; add `--volumes` only when you intentionally want to delete that local run data.

The Web container does not receive `/var/run/docker.sock`. Fast Demo remains frozen and synthetic. A Verified request from that container therefore normally reports Docker unavailable unless the operator supplies a separately secured runner boundary; it never borrows Fast fixtures.

## Optional sandbox smoke

The reviewed Docker image and runner are separate from the Fast Demo. The full sandbox image includes the closed Chroma and Qdrant Local recipes. Its installation network must be a dedicated externally enforced egress network restricted to the approved package hosts; it is not created automatically.

For the repository's opt-in Docker smoke:

```powershell
$env:TECHSCOUT_DOCKER_SMOKE = "1"
python -m pytest tests/techscout/test_sandbox_docker_smoke.py
```

This verifies the sandbox path when local Docker prerequisites are deliberately provided. It does not turn the Web Fast Demo into a Docker-backed run.

## Interpreting a run

Check these fields before discussing the result:

- `synthetic` / fixture notice: if true, do not present claims as live research.
- `mode`: `fast` means the frozen harness-backed demo; `verified` means live/cache research plus reviewed Docker intent.
- terminal status: distinguish `completed`, `completed_with_limitations`, and `failed`.
- verdict: `recommended` versus `no_safe_winner`.
- candidate support level: only `v1_supported` candidates may be recommendation-eligible.
- PoC status and recipe ID: `research_only` is not a failed benchmark.
- issues/limitations: cache degradation, unavailable live execution, missing recipe, or exhausted recovery must remain visible.
- Trace checkpoint/recovery links: confirm only the failed stage was repeated.

## Demo wording

Safe wording: “This Fast Demo exercises the actual orchestration, policy, MCP, checkpoint, validation, artifact, and Trace seams with frozen synthetic inputs.”

Unsafe wording: “This is a live comparison,” “Docker verified these candidates,” or “the final benchmark passed” unless the corresponding external authority is later supplied and verified.
