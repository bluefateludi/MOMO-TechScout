# Architecture and artifact authority

## Implemented vertical slice

```mermaid
flowchart TB
    subgraph Product["Local product surface"]
        UI["React/Vite UI"]
        API["FastAPI v2 routes"]
        REG["SQLite WAL run registry and append-only events"]
    end

    subgraph Agent["Bounded decision system"]
        HAR["LangGraph Harness"]
        STATE["Strict ResearchState"]
        SKILL["Fixed Skill registry and policy router"]
        CHECK["Separate SQLite checkpoints"]
        GATE["Deterministic Validation Gate"]
        REC["Typed failed-stage recovery"]
    end

    subgraph Tools["Tool boundary"]
        MCP["Local stdio MCP client/server"]
        FAST["Frozen search and synthetic PoC\nFast Demo implementation"]
        LIVE["Tavily, HTTPS fetch, GitHub read-only, cache\nimplemented adapters; not Web-wired"]
        DOCKER["Reviewed Docker recipe compiler/runner\nimplemented module; not Fast-Demo-wired"]
    end

    subgraph Authority["Durable outputs"]
        FILES["Report, evidence, PoC records, manifest"]
        TRACE["Sanitized sealed JSONL Trace"]
    end

    UI --> API
    API --> REG
    API --> HAR
    HAR <--> STATE
    HAR --> SKILL --> MCP --> FAST
    MCP -. future Web integration .-> LIVE
    MCP -. future Web integration .-> DOCKER
    HAR <--> CHECK
    HAR --> GATE
    GATE -->|"recoverable within bound"| REC --> HAR
    GATE -->|"publish or limit/fail"| FILES
    HAR --> TRACE
```

The graph is intentionally bounded rather than an open-ended ReAct loop. Code owns state transitions, tool permissions, budgets, recipe compilation, gate decisions, and terminal status. Stage services own research/planning behavior. Model output, when connected, cannot override these controls.

## Stage and context boundaries

| Stage | Context admitted | Output contract |
|---|---|---|
| Intake/planning | Normalized request, environment, hard constraints, candidate identities, Skill summaries | Typed research plan |
| Research | One candidate, relevant constraints, bounded source metadata and search history | Normalized source/chunk/evidence identifiers |
| PoC planning | Candidate/version evidence and trusted recipe schema only | Structured `PocPlan`; no raw shell text |
| Verification | Structured plan compiled through the closed recipe registry | Typed PoC result or research-only disposition |
| Validation/reporting | Evidence index, PoC results, limitations, failures, manifest requirements | `recommended` or `no_safe_winner`; terminal manifest |

Raw pages, unrelated candidates, secrets, and arbitrary repository content are not meant to be copied into every prompt. Sources carry stable identifiers, timestamps, and content hashes.

## Authority hierarchy

1. A terminal manifest and its immutable artifact hashes are run authority.
2. The sealed local Trace is execution/provenance authority, subject to its allowlisted schema and sanitizer.
3. SQLite stores authoritative run status, discovery, progress, idempotency, attempts, deadlines, cancellation, and event projections; it does not rewrite the immutable artifacts. The default local composition uses an in-memory dispatch queue. Optional Redis-backed deployment uses Redis only for dispatch, leases, heartbeats, rate limiting, and dead-letter routing, with at-least-once delivery.
4. Synthetic fixtures establish deterministic contract behavior only.
5. A sealed non-synthetic evaluation package, after offline verification, is the required authority for model/product-effect metrics such as Task Success, Recall, Token, and Cost. Browser acceptance and scoped test/CI records remain separate authorities for engineering-delivery claims.

Optional OTLP export is a secondary observability sink. Export failure must not change local terminal artifacts. Evaluation outputs include resolved configuration, environment, case-level records, summary, resume projection, and a sealed manifest. The available runner diagnostics use synthetic fixtures, so their model/product-effect and resume authority is **N/A**.

## Failure semantics

Search/cache, tool-schema, dependency, PoC, report, deadline, and unsafe-operation failures are typed. Recovery is local to the failed stage and linked to a checkpoint; completed work is reused where valid. If the policy bound is exhausted, the product publishes an honest limited artifact or fails safely. It does not restart indefinitely or infer incompatibility from missing infrastructure.
