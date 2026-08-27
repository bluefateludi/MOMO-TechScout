# MOMO TechScout M0 Product Gate authority — 2026-08-27

## Authority and decision

- Issue: GitHub #109 under parent Spec #108.
- Inspected baseline: `4eace34b0cab74ef9da41f53cd3e868848708f5f`
  (`fix: make evaluation fixture hashes cross-platform (#107)`). This was the
  freshly fetched `origin/master` when the dedicated branch was created.
- Inspection window: 2026-08-27 19:25–19:34 `+08:00` (China Standard Time).
- M0 Product Gate: **blocked**. The default Web journey does not persist the
  visible Requirements Review and Criteria Confirmation, so a normal homepage
  submission remains `queued`. The existing public Workflow API can complete
  those confirmations and then the same run reaches a terminal state.
- This authority is historical and commit-scoped. During the inspection,
  `origin/master` advanced beyond `4eace34`; later changes are not silently
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
| Browser | Chromium driven through Playwright CLI |
| Server | `python -m paper_agent.web`, loopback port `8765`, isolated ignored state/output roots |

The repository production frontend was built before browser inspection. The
browser used the real FastAPI/React composition, not a TestClient or frontend
mock. API confirmation calls were made over the same loopback server.

An initially invoked machine-level `techscout.exe` served `404` at `/`; its
editable installation was not attributable to this checkout. It was stopped and
excluded. All product observations below used the current checkout's module
entrypoint. Therefore a clean-environment installation of the console script is
still pending rather than inferred from the machine-level command.

## Closed-loop inventory

| Public boundary | Classification | Observable evidence | Honest authority |
|---|---|---|---|
| CLI help and readiness | Completed for inspection; clean install pending | `techscout --help` exposed only `doctor` and `serve`. `techscout doctor --json` returned `not_ready` with stable configuration, provider, GitHub, Docker, and install-network checks. The current-checkout Web module served successfully. | Confirms bounded CLI/readiness contracts, not a reproducible clean install. |
| Web homepage to terminal run | **Blocked** | In Chromium, all five visible review lanes were checked and `Start TechScout task` was pressed. Run `904649bb-5039-416c-a6ff-f26ea6c93388` remained `queued` with `elapsed_seconds=0.0` after 10 seconds. The page itself stated that the preview confirmation was not persisted. | A normal user cannot currently complete the public Decision Workflow from the homepage. Follow-up owner: #110. |
| Workflow and run API | Completed | Public `requirements-review`, `confirm-requirements`, and `confirm-criteria` calls progressed the same run through `requirements_review`, `criteria_confirmation`, and `research_ready`; the Worker then terminalized it. | Confirms the backend gating and execution seam exists. It does not close the Web wiring gap. |
| Fast Demo | Completed, synthetic/offline only | The confirmed run reached `completed` in fixture elapsed `15.672s`; API reported `synthetic=true`, 6 Evidence items, a recommendation for Chroma, and 22 projected Trace events. Refresh showed the terminal state and report link. | Proves the frozen fixture, Harness, MCP, checkpoint, gate, artifact, and Trace engineering path only. No live provider, real source, or Docker authority. |
| Verified | Explicitly limited; real success unverified | Run `d4ad3ee0-d155-4345-bdcb-46a90ff2f18f` reached `completed_with_limitations` in `2.579s`, with `synthetic=false`, `no_safe_winner`, no recommendation, 0 Evidence items, and `live_evidence_unavailable`. All three PoCs were `research_only` and `verified=false`; the issue code was `tool_unavailable`. | Confirms fail-closed/limited semantics in this environment. It does not prove a real model call, real Authoritative Source acquisition, or real Docker PoC. Follow-up owner: #111. |
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
`outputs/m0-product-gate/`; they are not repository fixtures or publishable
metrics. Selected hashes make the exact inspected files identifiable.

| Run | Artifact | SHA-256 |
|---|---|---|
| Fast `904649bb-5039-416c-a6ff-f26ea6c93388` | `decision-report.json` | `2130107ab19a30491849440371fadb19b61e1193d54365cbe1b634ddf0f30f31` |
| Fast | `run_manifest.json` | `284dcab644afb9eb6b6d21b9990af6f11c1dacc20cc40b9a6b970b3f56a11983` |
| Fast | `poc-results.json` | `8719b14c449d5404ec459aea6cd1d899a11676389a7af695b59aec7b1d2e448f` |
| Fast | `traces.jsonl` | `fd98029f91e94e57b0178aa54c04cb39023532a22c00a87f91eed91e940d8221` |
| Verified `d4ad3ee0-d155-4345-bdcb-46a90ff2f18f` | `decision-report.json` | `36fa1f8a246b3b83446ad69e0e918b115b61c5d8cf75d1fb5ac96388076705e8` |
| Verified | `run_manifest.json` | `2fea22d01e374f140e9823e644909d9a445c57c65ea9b6f1f692a9bf3cceee7f` |
| Verified | `poc-results.json` | `ff6dd7e995d1f848e5f52c827613c2a6efe571e276f8ae8ceafc8329a8664a95` |
| Verified | `traces.jsonl` | `755d7f037044a000a07901855e420d9c076a0951d0e941ca029084259494c9da` |

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
4. The inspected page was truthful about the synthetic boundary and explicitly
   disclosed that its review/confirmation was not persisted. The default English
   presentation and later localized UI work belong to #110 and are not changed
   or accepted here.

## Product Gate checklist

- **Completed:** current-checkout server start; production frontend build;
  static Compose contract; public Workflow API; synthetic Fast terminal package;
  honest Verified limitation terminal package; report, PoC, Trace, and manifest
  publication; refresh persistence after API confirmation.
- **Blocked:** homepage-to-`research_ready` wiring; real Compose build/start in
  this environment; real Docker PoC authority.
- **Pending verification:** clean-checkout console-script installation; real
  model revision/token use; cold live Authoritative Sources; real Chroma/Qdrant
  Docker passes; full Verified Hero success.
- **Future capability (not M0):** #110 public Decision Workflow closure and
  localization; #111 real model/source/Docker Verified Hero Slice; later M2–M4
  trustworthiness, live evaluation, release, and Ownership Gates.

M0 may proceed to its follow-up owners with this inventory, but the Product Gate
itself remains blocked until the default public Web journey reaches an honest
terminal result without out-of-band Workflow API calls.
