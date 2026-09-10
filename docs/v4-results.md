# V4 validation status — September 9, 2026

The V4 implementation is available as package `0.4.0`, prompt version `v4.0.0`.
The real-Qwen V3/V4 comparison completed: both conditions solved **0/2 positive
theorems** and correctly left the false theorem unproved. V4 activated its new
roles on all three cases, but increased latency and tokens without improving
proof success in this small run.

## Completed checks

- Offline regression suite: **215 collected, 205 passed, 10 opt-in tests skipped**.
  This includes 28 new V4 component/evaluation/runner tests, alongside prior tests.
- Real Kimina/Lean-LSP/LeanExplore integration: **4/4 passed**, using scripted
  model responses; the run took 118.3 seconds.
- Fixture preflight: both positive reference proofs compiled, and the concrete
  invalid proof seed was rejected.
- Python byte-compilation and the CLI/script help checks passed.

| Live integration case | Result | Evidence |
| --- | --- | --- |
| Two rejected proofs → discussion → fresh proof of both goals | Verified; independent recheck passed | `runs/v4-integration-fresh-goals.json` |
| Worker proves only one of two goals | Rejected; three-round budget exhausted | `runs/v4-integration-incomplete-goals.json` |
| False successor theorem, including worker output containing `sorry` | Rejected; three-round budget exhausted | `runs/v4-integration-false-theorem.json` |
| Benchmark reference/seed preflight | Passed | `runs/v4-validation.json` |

Each of the three proof-loop integration cases invoked one discussion request
and one fresh formal request after two failed formal rounds. These artifacts
demonstrate routing and verification boundaries with real Lean services. They
do not test Qwen's ability to generate the scripted advice or proof.

Offline tests additionally check context IDs and absence of leaked candidate
history, exact preservation of hypotheses during compression, failures when
critical input cannot fit, malformed/truncated/failed discussion responses,
per-role call limits, counted failed formal calls, V3 ablation boundaries,
paired condition ordering, checkpoint/history persistence, shared backend reuse,
and failure of the independent recheck. Final telemetry/summary refinements were
checked offline after the live integration run.

## Completed real-Qwen comparison

An earlier launch was blocked before execution by account usage limits. The
authorized retry ran to completion on September 9, 2026, approximately
20:07–20:31 UTC. No agent code or prompts were changed during the run.

- Run ID: `470b670f46c64b5f9530c00ef455e8a4`.
- Full results: `runs/v4-paired-validation.json`.
- Immutable copy: `runs/history/v4-paired-validation-20260909T200723302684Z-paired-470b670f.json`.
- Event trace: `runs/v4-paired-validation.events.jsonl`.
- Independent audit: `runs/v4-paired-audit.json`.
- Code fingerprint: `140cde188a998776c6f9a3b2efa5ddace38a8d3cde4f729e5dfdfeb3e6d918d8`.

| Metric | V3 | V4 |
| --- | ---: | ---: |
| Positive theorems verified | 0/2 | 0/2 |
| Correct outcomes, including negative control | 1/3 | 1/3 |
| Sum of theorem-attempt wall time | 578.6 s | 877.9 s |
| All model calls | 15 | 19 |
| All generated tokens | 5,759 | 9,967 |
| Kimina checks inside attempts | 10 | 9 |
| Rejected formal rounds | 10 | 10 |
| Discussion calls | 0 | 3 |
| Fresh formal calls | 0 | 3 |
| V4 request errors | 0 | 0 |
| Informal-stage failures | 1 | 2 |

V4 added 299.2 seconds (**51.7%**) and 4,208 generated tokens (**73.1%**).
Wall time is the sum of per-attempt metrics; it excludes preflight, later audits
and final cleanup. Kimina counts also exclude preflight/audit checks. Duplicate
proof rejection can consume a formal round without another compiler call.
The run contains one pair per case; no statistical accuracy claim is warranted.

| Case | V3 | V4 |
| --- | --- | --- |
| `hypothesis_chain` | Failed, 3 rounds | Failed, 3 rounds |
| `square_bound` | Failed, 4 rounds | Failed, 4 rounds |
| `false_successor` | Correctly unproved, 3 rounds | Correctly unproved, 3 rounds |

The comparison completed even though positive cases failed. A runner exit code
of 1 for unmet expected outcomes does not mean the experiment was interrupted;
the artifact has `experiment_complete: true`, six saved attempts, no run-level
error, and the final summary/history copy.

## Failure analysis

On `hypothesis_chain`, both conditions repeatedly used inappropriate `f ≫ g`
notation in an ordinary implication proof. V4's discussion response included
that notation, and the fresh worker copied it. The worker changed the overall
proof structure but still failed Lean's syntax/type checks.

On `square_bound`, V3 repeatedly tried to rewrite with inequality evidence and
supplied incorrect assumptions to multiplication lemmas. V4 described useful
mathematics involving multiplication on the interval `[0,1]`, but its generated
Lean still misapplied multiplication lemmas. The final candidate also used
`sq_nonneg hx` with a proof where a value was expected and referenced the unknown
identifier `mul_right_one`. Useful discussion did not yield a compiling proof.

On the false theorem, discussion correctly noted that the successor equality
was false, but the worker still tried to prove it. Lean rejected the result.
Both conditions stopped at their configured three-round budget. Identifying
falsity in discussion is advisory and does not grant proof success.

The informal generator exhausted its output budget on the V3 implication case.
The informal verifier exhausted its output budget on V4's implication and
negative cases. These are V3-role failures, separate from V4's zero request
errors; zero V4 errors means the new role requests completed and parsed, not
that their advice was mathematically or formally correct.

All three V4 discussion requests returned structured advice and all three fresh
requests returned candidates. Their records have distinct conversation IDs,
zero history messages, and estimated input-plus-output sizes no greater than
7,522 tokens, below the 8,192 limit. There were six context compressions in total.
One local MLX model process was observed during execution, and the runner
recorded unloading it on completion.

## Independent audit and next experiment

The independent audit recompiled **all six final candidates** and confirmed
that each remained rejected with the original task preserved. It also checked
both negative round budgets against the fingerprint-matched manifest and
compiled `∀ n : ℕ, n + 1 ≠ n` via an equivalent Lean example. Thus the negative
fixture is false; its lack of a proof is the expected outcome. There were no
successful generated proofs to audit. The two reference proofs had already
passed fixture preflight.

The measured bottlenecks are Lean formalization and output-budget exhaustion.
The next useful engineering experiment is to improve use of retrieved lemma
types and prevent unsupported Lean notation in discussion advice, then rerun
the same paired set and a separate held-out set. Fixes have not been mixed into
this run or retrospectively credited as successes.

To reproduce the comparison and audit:

```bash
.venv/bin/python scripts/test-v4-live.py \
  --output runs/v4-paired-validation.json

.venv/bin/python scripts/audit-v4.py runs/v4-paired-validation.json \
  --output runs/v4-paired-audit.json
```

The default runs three fixtures under both V3 and V4, with matched formal-round
budgets, independent recompilation of successes, role activation/error metrics,
token/call costs, checkpoints and immutable final artifacts. The runner can
also isolate discussion or fresh contexts; see [the usage guide](v4.md).

Inspect `v4_activated`, `v4_role_errors`, and informal-stage failures alongside
success counts. The small initial set is a development smoke test; larger
repeated held-out and equal-compute comparisons remain research work.
