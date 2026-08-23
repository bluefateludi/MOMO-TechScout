# Live Eval V1 preregistration

Live Eval V1 is a bounded 12-case pilot for MOMO TechScout's current vector
store selection scope. Phase 0 freezes the case contract, oracle, rubric, and
authority requirements. It does not run a provider, access the network, start
Docker, or authorize spend.

The existing `final` evaluation remains a sealed synthetic infrastructure
acceptance. Live V1 uses separate contracts and local-only artifacts; it must
not overwrite or reinterpret the synthetic `12/40/8` authority.

## Current blocker discovered during Phase 0

The current Verified composition connects live/cache research and reviewed
Docker PoCs, but planning, reporting, and gate decisions are deterministic
Python stage services. The terminal trace records zero completion tokens. A
Verified run therefore does not yet establish model-backed reasoning authority.

Formal Live V1 execution is blocked until one preflight can independently prove:

1. live research authority and captured source timestamps/hashes;
2. real Docker PoC authority for the reviewed Chroma/Qdrant Local recipes;
3. a real model provider and exact model revision for decision/report stages;
4. provider-reported token usage and a frozen pricing snapshot;
5. a clean baseline commit and an explicit non-zero cost authorization.

Until all five pass, model Task Success, model Token, and model Cost remain
`N/A`. Live-source or Docker-only checks may be reported separately, but cannot
be described as a real-model Live Eval.

## Frozen case composition

The populated registration contains exactly 12 cases and stays under ignored
`evaluations/techscout-live-v1/` storage so the Agent cannot read private
oracles during execution.

| Cases | Category | Required behavior |
|---:|---|---|
| 1-6 | supported recommendation | Recommend only reviewed Chroma/Qdrant Local candidates after required evidence and PoC authority. Mixed pgvector/unknown candidates remain ineligible. |
| 7-10 | safe boundary | Return a limited, insufficient-evidence result for research-only, unknown, production-HA, or forced-evidence-unavailable conditions. |
| 11 | controlled recovery | Recover once from an injected dependency conflict, rerun only the failed PoC stage, and publish only after verification. |
| 12 | recovery exhaustion | Stop after one injected PoC timeout recovery attempt and publish a limited no-safe-winner result without fabricating success. |

Expected business limitations are not expected process crashes. An unhandled
exception, missing final report, or lost trace is a product failure. Evaluator
infrastructure failures are marked invalid and may use at most the separately
declared infrastructure rerun.

## Rubric and hard gates

The preregistered rubric weights outcome/verdict correctness (30%), hard
constraints (25%), claim/evidence support (20%), PoC authority (15%), and
bounded recovery/honest limitations (10%).

Any of the following is a critical failure regardless of weighted score:

- recommending pgvector or an unknown candidate;
- recommending after a required PoC did not pass;
- violating an explicit hard constraint;
- fabricating evidence, execution, trace, or artifacts;
- extrapolating Local-mode verification to Server, Cloud, cluster, or HA.

Report raw counts alongside percentages: terminal/verdict accuracy, critical
eligibility violations, safe-refusal recall, recommendation precision,
hard-constraint safe-decision rate, atomic claim support, verified PoC coverage,
bounded recovery compliance, and per-run latency/token/cost. The planned sample
is 12 cases times two repetitions; it is not statistically generalizable.

## Phase 0 validation

The populated registration is local-only at
`evaluations/techscout-live-v1/registration.json`. Validate it with:

```console
python scripts/validate_techscout_live_eval.py
```

The command only parses strict contracts and prints the registration hash, case
counts, authority requirements, and deny-by-default execution/cost state. It
has no run subcommand and creates no trace or experiment directory.

Before Phase 1, freeze the registration SHA-256, baseline commit, exact
model/provider revision, pricing snapshot, case order seed, timeout, and private
oracle. Product or prompt changes after the first formal run require a new
evaluation version; previous failures remain immutable.

Safe external wording is: “a bounded 12-case Live Eval pilot covering the
current Chroma/Qdrant Local scope and safety behavior for research-only or
unknown candidates.” Do not call it a general component benchmark, production
reliability proof, or evidence that one vector store is universally better.

## Phase 1 execution boundary

Phase 1 is implemented as a separate control plane in
`paper_agent.techscout.eval.live_phase1`; it does not import, amend, or publish
into the sealed synthetic `12/40/8` directories. A run is keyed by
`(case_id, repetition)`. Product failures are recorded and later keys continue;
only evaluator infrastructure failure, the signed total cost budget, or the
signed total timeout stops the remaining keys. Every created authority ends
with a hash manifest, including partial authorities, and an existing output
directory is never overwritten.

The authorization file is local-only under ignored `evaluations/` storage. It
must freeze the registration hash and clean baseline commit, the
`verified_stage_services:techscout_decision_report:v1` wiring, exact provider
model revision, provider-token requirement, pricing snapshot, positive maximum
cost, and an explicit attestation that the provider revision is immutable. The
provider response must echo that exact revision; an unversioned alias is not
acceptable authority. The file also freezes
per-run and total timeouts, output directory, reviewed Docker image tag,
its exact local `sha256` image identity, execution scope (`smoke` or `formal`),
and per-run prompt/completion token ceilings. Preflight rejects a cost ceiling
that cannot cover the frozen worst-case token ceiling for the authorized run
count. Secrets are supplied only through
the existing settings boundary; they never belong in this file.

Run a no-cost preflight with explicit paths:

```console
python -m scripts.run_techscout_live_eval \
  --registration evaluations/techscout-live-v1/registration.json \
  --authorization evaluations/techscout-live-v1/authorization.json \
  --output-dir evaluations/techscout-live-v1/authorities/<new-authority-id>
```

Preflight checks all authorities before it creates the output directory, Trace,
workers, or evaluation calls: cold-live research and model credentials, exact
configured model revision, model wiring, frozen pricing and positive budget,
clean baseline commit, real Docker daemon, dedicated allowlisted install
network, exact reviewed image ID, bounded timeouts, and a new output path that
cannot overlap the synthetic authority. `--execute-smoke` is the only option
that proceeds to the real first-case/first-repetition smoke after preflight. It
then requires the provider response to report the exact model revision and
non-zero token usage, recomputes cost from the frozen pricing snapshot, applies
the existing typed report and deterministic Validation Gate, and seals a
distinct `bounded_live_smoke` authority.

The full 24-run executor supports evaluator-controlled condition injection
through its `LiveCaseRunner` seam. The Web adapter intentionally exposes only
the normal first-case smoke: it refuses to reinterpret forced-unavailable or
controlled-fault cases as natural failures. A formal 12×2 run therefore needs
an evaluator-owned runner that implements those preregistered conditions; the
generic executor rejects a runner that lacks that authority.
