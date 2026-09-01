# MOMO TechScout M0 Product Gate authority — current through 2026-09-01

## Authority and decision

- Issue: GitHub #109 under parent Spec #108.
- Inspected baseline: `a63703c6e5113ad04952646f5a1f16c90eaac2f3`
  (`Merge pull request #121 from bluefateludi/codex/techscout-chinese-minimal-ui`).
  This was the fetched `origin/master` used in a separate detached worktree for
  the final acceptance rerun; it includes the merged Simplified Chinese UI.
- Final inspection window: 2026-09-01 18:18–18:28 `+08:00` (China Standard
  Time). The earlier 2026-08-27 run against `4eace34` was superseded because it
  did not include the latest merged UI.
- M0 Product Gate: **blocked**. The default Web journey does not persist the
  visible Requirements Review and Criteria Confirmation, so a normal homepage
  submission remains `queued`. The existing public Workflow API can complete
  those confirmations and then the same run reaches a terminal state.
- This authority is commit-scoped. Changes after `a63703c` are not silently
  included in these observations and require their own acceptance.

This record authorizes only the observable statements below. It is not a model
quality, source quality, component performance, production readiness, latency,
cost, or resume-metric authority. Historical MOMO Scholar results are not used.

## Environment and method

| Field | Observed value |
|---|---|
| Host | Windows NT `10.0.26100.0` |
| Shell | PowerShell `7.6.4` |
| Python | `3.12.3` |
| Node / npm | `v22.19.0` / `10.9.3` |
| Current source import | this worktree's `paper_agent/__init__.py` |
| Docker | client `28.3.2`; daemon unavailable |
| Browser | Headed Chromium driven through Playwright CLI |
| Server | `python -m paper_agent.web`, loopback port `8765`, isolated ignored state/output roots |

The repository production frontend was built before browser inspection. The
browser used the real FastAPI/React composition, not a TestClient or frontend
mock. API confirmation calls were made over the same loopback server.

A machine-level `techscout.exe` exists, but its editable installation is not
attributable to this checkout and was not used as product evidence. CLI help and
readiness were invoked directly from this checkout's Typer app; all Web product
observations used this checkout's module entrypoint. Therefore a clean-environment
installation of the console script is still pending rather than inferred from a
machine-level command.

## Closed-loop inventory

| Public boundary | Classification | Observable evidence | Honest authority |
|---|---|---|---|
| CLI help and readiness | Completed for source inspection; clean install pending | The current checkout's Typer app exposed only `doctor` and `serve`. Its `doctor --json` returned `not_ready` with stable configuration, provider, GitHub, Docker, and install-network checks. The current-checkout Web module served successfully. | Confirms bounded CLI/readiness contracts from source, not a reproducible console-script installation. |
| Simplified Chinese UI | Completed as a presentation surface | Headed Chromium rendered Simplified Chinese by default, preserved Must-have, Evidence, PoC, Trace, Fast Demo, Verified, Research-only, and synthetic authority labels, and exposed a language switch. | Confirms the merged PR #121 presentation only; it does not close #110's Workflow acceptance. |
| Web homepage to terminal run | **Blocked** | In Chromium, all five visible review lanes were checked and `启动 TechScout 任务` was pressed. Run `62965cc2-34b6-4976-aa73-e840c1415fe1` remained `queued`, `draft_context`, and `elapsed_seconds=0.0` until out-of-band API confirmation. The page itself stated that the deterministic fixture preview and its confirmations were not persisted. | A normal user cannot currently complete the public Decision Workflow from the homepage. Follow-up owner: #110. |
| Workflow and run API | Completed | Public `requirements-review`, `confirm-requirements`, and `confirm-criteria` calls progressed the same run through `requirements_review`, `criteria_confirmation`, and `research_ready`; the Worker then terminalized it. | Confirms the backend gating and execution seam exists. It does not close the Web wiring gap. |
| Fast Demo | Completed, synthetic/offline only | The confirmed run reached `completed` in fixture elapsed `13.891s`; API reported `synthetic=true`, 6 Evidence items, a recommendation for Chroma, and 22 projected Trace events. Refresh showed the terminal state and report link. | Proves the frozen fixture, Harness, MCP, checkpoint, gate, artifact, and Trace engineering path only. No live provider, real source, or Docker authority. |
| Verified | Explicitly limited; real success unverified | Run `6b8a2c23-2fe2-4dcb-805c-b381698d030c` reached `completed_with_limitations` in `5.969s`, with `synthetic=false`, `no_safe_winner`, no recommendation, 0 Evidence items, and `live_evidence_unavailable`. All three PoCs were `research_only` and `verified=false`; the issue code was `tool_unavailable`. | Confirms fail-closed/limited semantics in this environment. It does not prove a real model call, real Authoritative Source acquisition, or real Docker PoC. Follow-up owner: #111. |
| Compose configuration | Completed | `docker compose config` resolved a loopback-only Web port, read-only root filesystem, dropped capabilities, `no-new-privileges`, tmpfs, and named data volume. | Static Compose contract only. |
| Compose build/start | **Blocked by environment** | `docker compose up --build --no-start` could not inspect/build the image because `//./pipe/docker_engine` did not exist. | No real image build or container runtime authority. Follow-up owner: #111 / the later real Verified gate. |
| Offline fixture/replay | Completed | The homepage exposed the frozen example and the frontend suite covered immutable fixture review. | UI/contract review only; not a new research run or product-effect result. |

## External authority separation

The readiness report observed:

- `TAVILY_API_KEY`: not configured, so cold live-search authority was unavailable;
- `DASHSCOPE_API_KEY`: not configured, so no real model-assisted drafting was
  observed (the deterministic gate remains independent);
- `GITHUB_TOKEN`: not configured; no GitHub source success was observed in the
  Verified run;
- Docker daemon: unavailable;
- reviewed install network: not configured and no externally enforced egress
  allowlist was asserted.

Consequently, the Verified terminal package is limitation authority, not success
authority. Frozen Fast Evidence and synthetic PoC results must not fill any of
those missing columns.

## Reproducible artifact evidence

Generated run directories were kept under gitignored
`outputs/m0-current-master/` in the detached acceptance worktree; they are not
repository fixtures or publishable metrics. Selected hashes make the exact
inspected files identifiable.

| Run | Artifact | SHA-256 |
|---|---|---|
| Fast `62965cc2-34b6-4976-aa73-e840c1415fe1` | `decision-report.json` | `d2fafbb452dd400b71b72657b513939e1ed3911393ed0c698a78a63620ad026f` |
| Fast | `run_manifest.json` | `1282bd0adecedc6e3991003dc6d3568fb113c1e47c49ab647c20618a85ec9a8c` |
| Fast | `poc-results.json` | `8719b14c449d5404ec459aea6cd1d899a11676389a7af695b59aec7b1d2e448f` |
| Fast | `traces.jsonl` | `61660fe38d22f28671f42209f1da9467a3b394dd0546fd647f68f813ce299129` |
| Verified `6b8a2c23-2fe2-4dcb-805c-b381698d030c` | `decision-report.json` | `c57be78a424bb9c64e331025a09994380828ebd3e46ed65a27aded60a5c89ae5` |
| Verified | `run_manifest.json` | `780e6716c1c711e3b50ada0bb6eb58b0de1bf594c4116b96fa6e16fa78757eda` |
| Verified | `poc-results.json` | `ff6dd7e995d1f848e5f52c827613c2a6efe571e276f8ae8ceafc8329a8664a95` |
| Verified | `traces.jsonl` | `8fa44cfe7dbf3c7bea20f092c40dcc11f16240bb203c679e1e30bdb95edab76d` |

Both `traces-manifest.json` files declared a sealed
`momo-techscout-trace-v1` artifact and matched the corresponding Trace hash.

## Documentation and UI contradictions

1. The prior README told users to submit a Fast Demo task after starting the
   server, but did not disclose that the visible confirmation screen was only a
   preview and the resulting run would remain queued. README and the run guide
   now point to this blocker.
2. The run guide said the UI submits `fast` or `verified` without explaining
   that it did not complete the required Workflow transitions. It now separates
   submission from executable `research_ready` authority.
3. The delivery-doc index named an older final-delivery commit as if it were the
   current repository authority. It now distinguishes that historical synthetic
   authority from this commit-scoped M0 inspection.
4. The latest page now defaults to Simplified Chinese and is truthful about the
   synthetic boundary. It explicitly discloses that its fixture preview,
   Unknown, Research Questions, PoC Checks, and confirmations are not persisted.
   The localization is present, but #110 remains the owner of the missing public
   Workflow wiring and its complete acceptance.

## Model, Live Eval, and resume authority

- The current Verified composition connects bounded live/cache research and
  reviewed Docker PoCs, but its planning, reporting, and deterministic gate do
  not establish a real model-backed reasoning result. No provider revision or
  provider-reported token usage was observed in this inspection.
- Live Eval V1 has a preregistered 12-case contract and a deny-by-default Phase 1
  control plane. Formal execution remains blocked on live-source authority, real
  reviewed Docker PoCs, an exact model/provider revision, provider token usage,
  a pricing snapshot, a clean baseline, and explicit non-zero cost authority.
- There is no sealed non-synthetic evaluation package. Model/product Task
  Success, Recall, Recovery rate, tokens, latency, cost, and other resume-effect
  metrics remain **N/A**. Historical browser timings and scoped test/CI counts
  retain only their documented engineering authority; MOMO Scholar results are
  not reused.

## Product Gate checklist

- **Completed:** current-checkout server start; Simplified Chinese presentation;
  production frontend build; static Compose contract; public Workflow API;
  synthetic Fast terminal package; honest Verified limitation terminal package;
  report, PoC, Trace, and manifest publication; refresh persistence after API
  confirmation.
- **Blocked:** homepage-to-`research_ready` wiring; real Compose build/start in
  this environment; real Docker PoC authority.
- **Pending verification:** clean-checkout console-script installation; real
  model revision/token use; cold live Authoritative Sources; real Chroma/Qdrant
  Docker passes; full Verified Hero success; sealed non-synthetic Live Eval.
- **Future capability (not M0):** #110 public Decision Workflow closure; #111
  real model/source/Docker Verified Hero Slice; later M2–M4 trustworthiness,
  live evaluation, release, and Ownership Gates.

M0 may proceed to its follow-up owners with this inventory, but the Product Gate
itself remains blocked until the default public Web journey reaches an honest
terminal result without out-of-band Workflow API calls. Issue #109 can close
after this authority is reviewed because its deliverable is the truthful
baseline and blocker ownership, not the implementation of #110 or #111.
