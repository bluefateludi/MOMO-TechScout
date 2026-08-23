# Offline evaluation demo

This is the stable, network-free evaluation path for a MOMO Scholar walkthrough.
It runs the repository's real deterministic evaluators against versioned fixtures
and combines their output into one presentation-ready report.

From the repository root, run:

```powershell
python scripts/evaluation_demo.py
```

The default Markdown report shows:

- claim-level retrieval hit rate, evidence coverage, unsupported claim rate, and
  citation validity;
- Recall@K, Precision@K, MRR@K, and nDCG@K for lexical, fixture-provided vector,
  and production RRF hybrid rankings;
- the interpretation boundary that should be stated during the demo.

For the exact, machine-readable result, run:

```powershell
python scripts/evaluation_demo.py --format json
```

The JSON includes every per-case result, both macro summaries, fixture paths, and
K. It contains no timestamp, random value, network result, or machine-specific
path, so repeated runs from the repository root are directly comparable.

To inspect Top-K sensitivity without changing runtime settings:

```powershell
python scripts/evaluation_demo.py --k 1
python scripts/evaluation_demo.py --k 3
```

Custom fixtures can be supplied explicitly:

```powershell
python scripts/evaluation_demo.py `
  --evaluation-fixture tests/fixtures/eval_cases.json `
  --retrieval-fixture tests/fixtures/retrieval_eval_cases.json `
  --k 3 `
  --format json
```

## Suggested walkthrough

1. Run the Markdown command and explain that the values were computed locally,
   rather than copied into the documentation.
2. Point out that the retrieval table compares three modes on the same seven
   fixed cases.
3. Open the JSON output when an interviewer asks for case-level evidence or exact
   floating-point values.
4. State the limitations before drawing conclusions: these fixtures are contract
   cases, citation validity is an ID-integrity check rather than entailment, and
   vector rankings are fixed inputs rather than live embedding results.

Do not describe these numbers as production quality, model quality, or public
benchmark performance. They are a reproducible engineering baseline for the
current offline retrieval and citation contracts.
