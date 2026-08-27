# Open-source Reproduction Gate — 2026-08-27

## Authority and scope

- Issue: #117, stacked on #111.
- Stacked baseline: `394269b` (`codex/issue-111-verified-hero-slice`).
- Clean-source implementation commit: `8e2ba62`.
- Installed Fast Demo smoke extension: `cdf1722`; this commit changes only the
  reusable smoke script and was run against the clean-built wheel below.
- Host: Windows PowerShell, Python 3.12.3, Node.js 22.19.0, npm 10.9.3,
  Docker CLI 28.3.2, Docker Compose 2.39.1.
- Clean boundary: `git archive` export with no `.git`, `node_modules`, `outputs`,
  virtual environment, provider credentials, or local `.env`.
- Authority: installation, deterministic Fast Demo, offline fixtures/tests,
  packaged CLI/Web/OpenAPI consistency, and Compose configuration only. This is
  not Verified, Live Eval, model-quality, provider, or Docker PoC authority.

## Reproduction commands

The clean source gate used the documented Python/Node versions and these command
shapes. The virtual environment must be active when running Python-backed npm
contract checks.

```console
git archive --format=tar -o source.tar 8e2ba62
mkdir clean-source
tar -xf source.tar -C clean-source
cd clean-source
npm --prefix web ci --ignore-scripts
python scripts/generate_web_contracts.py --check
npm --prefix web test
npm --prefix web run build
python -m pip wheel . --no-cache-dir --no-deps --no-build-isolation --wheel-dir wheels
python -m pip install --force-reinstall --no-deps wheels/paper_agent-0.1.0-py3-none-any.whl
python scripts/ci/smoke_installed_web.py
```

For `--no-build-isolation`, the build interpreter needs setuptools as declared
by the repository's build system and dev/CI installation. A normal isolated
`pip install .` resolves the build backend automatically. `--no-cache-dir` was
used here so the gate did not depend on a writable user-level pip cache.

## Results

| Gate | Result |
|---|---|
| Clean export | Passed: `.git`, `web/node_modules`, and `outputs` were absent before installation. |
| Python package/wheel | Passed. Wheel SHA-256: `bc525ffce0e6aad0f39ff457d71d2d94e295e66877786c1b91e5d635b500df44`. It includes `paper_agent/web/static/index.html`, the production assets, demo data, both CLI entry points, and no `outputs/`. |
| Installed origin | Passed from outside the checkout: `paper_agent` resolved from the isolated venv's `site-packages`, not the source tree. |
| CLI/Web | Passed: `paper-agent --help`, `techscout --help`, `/health/ready`, and `/` from the wheel-installed loopback server. The root served the packaged React application. |
| Deterministic Fast Demo | Passed from the wheel-installed server: run `1df133ee-1ecb-400e-9729-5d325799829a` terminalized as `completed`, with `synthetic=true` and a synthetic `recommended` report. The run ID is local acceptance evidence, not a product metric. |
| OpenAPI/Web | Passed: generated OpenAPI and TypeScript snapshots matched; 38 Web tests passed; the production build transformed 67 modules. |
| Python offline suite | Passed on the same implementation: `1641 passed, 3 skipped` in 185.22 seconds. The skips were the explicitly opt-in Docker smoke. Ruff passed after standard build outputs were excluded from source scanning. |
| Compose contract | Passed: resolved host binding is `127.0.0.1:8000`, the root filesystem is read-only, all capabilities are dropped, `no-new-privileges` is enabled, and no Docker socket is mounted. |
| Controlled Verified degradation | Passed with provider keys removed: `techscout doctor --json` exited 1 with `not_ready` and stable codes for unconfigured live search/model, unauthenticated GitHub, unavailable Docker daemon, and unconfigured install network. No synthetic Verified success was claimed. |
| Secret canary | Passed for packaged assets, smoke script, and changed public documentation. No common access-key/private-key patterns were found. |

Ordinary test and installed Fast Demo execution were deterministic and used no
research network, model provider, Docker execution, secrets, or provider cost.
Network was used only during the explicit dependency installation step.

## Known limitations

1. The host had a Docker CLI and Compose v2 but no reachable Linux Docker
   daemon. `docker compose config` passed; image build, container start, HTTP
   access through Compose, and named-volume restart were not claimed.
2. The isolated Python dependency install resolved public packages through the
   host-configured Aliyun PyPI mirror. No secret or repository-external source
   file was used, but this run does not independently attest default PyPI
   availability or every supported Python/OS combination.
3. Only Windows, Python 3.12, and Node 22 were exercised locally. CI covers its
   declared Ubuntu/Python 3.12/Node 20 matrix after push.
4. Compose deliberately starts the synthetic Fast Demo Web boundary and does
   not mount the Docker socket. It therefore does not enable or imply the
   reviewed-Docker Verified PoC path.
5. Verified and Live Eval still require their separately authorized exact model
   revision, provider usage/cost budget, authoritative sources, reviewed Docker
   recipes, and externally enforced install-network prerequisites. Those are
   outside #117, as is #120 Release/Ownership work.
