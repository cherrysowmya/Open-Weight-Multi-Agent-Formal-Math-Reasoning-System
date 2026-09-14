# V3.1 — strategy retrieval and invalid-rewrite recovery

> Development-tool commands in this guide require files excluded from the current
> public checkout. See [tool availability and historical recovery](development-tools.md).

This hardening addresses the observed `my_v3` failure without adding V4 roles.
The informal proof was sound, but the main agent repeatedly used an inequality
as a rewrite rule. Symbol-heavy retrieval returned mostly irrelevant declarations.

## Changes

The informal generator can now emit two artifacts:

```text
<informal_proof>A concise mathematical outline.</informal_proof>
<lemma_queries>["One reusable mathematical fact in plain English"]</lemma_queries>
```

There is no additional query-planning model call. The independent reviewer sees
only the task packet and the public outline, not the query metadata, generator
history, or hidden thinking. After acceptance, up to two bounded queries are
sent to local LeanExplore. Results are round-robin merged ahead of the original
task results, deduplicated by declaration name, and constrained by the existing
retrieval context budget. Source names, IDs, text, and individual queries remain
in the trace. The generator-only ablation may also search from its explicitly
unreviewed outline; it never receives a fabricated PASS.

Rejected, exhausted, and malformed outlines do not trigger strategy search.
Malformed optional query metadata is recorded on the draft and does not become
a query. A common local-model variation—two adjacent one-item JSON arrays—is
decoded and flattened within the same two-query cap; scalar values, trailing
prose and over-budget totals still fail closed. Outline-only responses remain
compatible but produce no extra search.
Queries are cached within an attempt; the same plan is not searched again each
formal round. Retrieval outages retain the configured required/optional policy.

On Lean's `Invalid rewrite argument` or `Tactic rewrite failed` diagnostic, V3
now immediately creates a fresh formal repair prompt. It omits the failed candidate body, retains the task,
diagnostics, goals, informal outline and retrieved declarations, and explains
that `rw` requires equality/iff evidence. The prompt asks for explicitly proved
intermediate facts and a suitable solver, including `nlinarith` for polynomial
inequalities. It warns that displayed local facts must be recreated and that
retrieved declarations need their exact qualified names. No theorem-specific
proof or lemma is inserted by the implementation.

Only Lean can accept a candidate. Existing duplicate rejection, statement
preservation, placeholder rejection, formal budgets, and model isolation remain.
No discussion partner, formal specialist, paid API, or second Qwen instance was
introduced. The maximum informal context and output settings are unchanged.

### Compiler-prefix repair (prompt revision v3.1.2)

After a generated candidate fails on an invalid rewrite, the optional prefix
repair keeps the source only up to the earliest compiler error. If an earlier
inline `have` proof is broken, it also tries proving that same local proposition
with `positivity`. It then tries a small generic solver portfolio: `nlinarith`,
`linarith`, `omega`, and `simp_all`. This is tactic search, **not another Qwen
response** and not an informal correctness decision. The original task headers
must remain unchanged and Kimina must accept the complete repaired source.
No benchmark reference proof is used by this path.

There are at most eight probe checks per theorem, shared across all formal
rounds, with duplicate probes skipped. Failed probes cannot produce success;
a verifier outage stops the attempt. Every probe is persisted in
`fallback_attempts` and counted in `kimina_checks`, `fallback_checks`, and
`rewrite_salvage_checks`. Success records use model `compiler_prefix_salvage`,
repair action `rewrite_prefix_salvage`, and `rewrite_salvage_successes`.

## Configuration and instrumentation

```toml
[informal_reasoning]
strategy_retrieval_enabled = true
strategy_query_limit = 2
strategy_query_max_chars = 240
rewrite_recovery_enabled = true
rewrite_salvage_enabled = true
rewrite_salvage_max_checks = 8
rewrite_salvage_tactics = ["nlinarith", "linarith", "omega", "simp_all"]
```

The query count is validated in 1–3 and per-query length in 1–500 characters.
The local defaults are deliberately smaller. Set strategy retrieval and rewrite
recovery to false to disable these hooks. Set just `rewrite_salvage_enabled`
to false to retain prompt/search hardening without tactic probes. Probe limits
are validated in 1–16 checks per theorem, with at most four configured tactics.
V1/V2 commands disable informal reasoning, so these hooks do not run there.
The generic formal repair prompts also changed in v3.1.1; disabling the hooks
does **not** restore byte-identical historical prompts. Compare new paired runs
with the same prompt version, not a new arm against an old saved baseline.

New trace fields include `lemma_queries`, per-draft `query_error`, per-iteration
`strategy_retrieval` and `repair_action`, and metrics
`strategy_retrieval_queries` and `rewrite_recovery_prompts`. All extra search
calls and latency are included in existing retrieval totals. The V3 summary
also counts rejected candidates with invalid-rewrite diagnostics. Kimina now
classifies this diagnostic as `tactic_failure`, rather than `unknown`.
Prompt version is `v3.1.2`; the package version is `0.3.1`.

## Reproduce the bounded regression run

```bash
.venv/bin/python -m unittest tests.test_v3_hardening -v

# Opt-in: real Kimina, LSP and LeanExplore with deterministic model replies
LOCAL_LEAN_AGENT_LIVE_LEAN=1 .venv/bin/python -m unittest \
  tests.test_v3_lean_integration.V3LeanIntegration.test_strategy_search_and_rewrite_recovery_use_real_services -v

.venv/bin/python scripts/test-v3-hardening.py \
  --output runs/v3-hardening-smoke.json

# Same current prompts/budgets/roles, with hardening hooks disabled:
.venv/bin/python scripts/test-v3-hardening.py --mode control \
  --output runs/v3-hardening-control.json

# Prompt/search hardening alone, without compiler-prefix tactic probes:
.venv/bin/python scripts/test-v3-hardening.py --no-salvage \
  --output runs/v3-hardening-model-only.json
```

The manifest contains the user's exact theorem, a concrete invalid-rewrite seed,
and a false division-cancellation theorem. Budgets are 3, 3, and 2 formal rounds.
This runner disables the older whole-proof fallback portfolio. In hardened mode
it now enables the bounded compiler-prefix probes unless `--no-salvage` is set;
do not describe all successful proofs as model-generated. Each case records
`proof_origin`, and summary metrics count probe checks and successes separately.
The regular `v3-ablation` command disables prefix probes in **both** conditions,
so the informal reasoning comparison does not silently add tactic search.
Positive references and the bad seed are checked
before inference; references are never passed to Qwen. Successful output is
independently rechecked by Kimina. Requests run sequentially on one MLX model.
The terminal shows a compact summary; separate JSON/JSONL files and immutable
history artifacts contain details. `--case user_my_v3` selects one case.

To test a personal Lean file with ordinary fallback settings:

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/my_v3.lean --informal-policy always --max-rounds 3 \
  --output runs/my_v3.proved.lean
```

## Interpretation

Live diagnostic searches showed that whole-outline queries can still rank poorly.
The local index contains the needed square-nonnegativity fact, but small wording
changes alter its rank substantially. This implementation exposes and uses
atomic queries; it does not guarantee relevant retrieval or prove that queries
are mathematically sufficient. Correctness still comes only from Lean.

The minimum engineering gate is deterministic contracts plus real compiler and
model regression checks. A broader claim needs repeated, held-out paired runs
and separate ablations of query planning and rewrite recovery. A success after
changing both features does not identify which caused the improvement. V4
should not be credited with resolving a V3 formalization bottleneck.

## Current measured status

The first real-Qwen hardening run is saved in `runs/v3-hardening-smoke.json`.
It completed all three cases with two correct outcomes. The concrete invalid
rewrite was repaired in one model round and independently recompiled; the false
division theorem stopped at its exact two-round budget. The user's generation
case still failed after three rounds. In that run the model emitted two adjacent
query arrays, so the then-strict parser recorded `query_error` and ran zero
strategy queries. This led directly to the bounded parser change above.

The user's subsequent run (`d657cdb96f2b440fab5ddf40b7378996`, preserved in
`runs/history/v3-hardening-latest-20260903T040103435877Z-hardened-d657cdb9.json`)
also scored 2/3, but
the failing case changed: `user_my_v3` succeeded in three Qwen rounds, while
the seeded invalid-rewrite case failed. The false division theorem stayed
unproved, but its informal generator exhausted its output budget; a correct
negative outcome did not mean that all informal roles completed successfully.

Further one-case runs are preserved as `runs/v3-hardening-invalid-rewrite.json`
and `runs/v3-hardening-invalid-rewrite-salvage.json`. Both failed. They exposed
an earlier malformed local `have` proof, which made keeping that prefix useless.
The new earliest-error cut and independently checked local-fact repair address
that defect. These are development cases used to tune the system, not held-out
evidence of generalization.

### Final validation: September 3, 2026

The frozen `v3.1.2` revision completed the full real-Qwen run **3/3** in
386.5 seconds. Full results are in `runs/v3-hardening-final-validation.json`,
with immutable evidence at
`runs/history/v3-hardening-final-validation-20260903T053725511773Z-hardened-beeb6889.json`.

| Case | Outcome | Main-model rounds | Compiler-prefix probes | Independent recheck |
| --- | --- | ---: | ---: | --- |
| `user_my_v3` | Verified | 3 | 1, successful | Passed |
| `invalid_rewrite` | Verified | 1 | 1, successful | Passed |
| `false_division` | Not proved; `MAX_ROUNDS` | 2 | 0 | Not applicable |

All three informal generator/verifier stages completed in one round each:
the two positive outlines were accepted, while the negative outline was rejected.
There were no informal-stage failures or timeouts. Total model calls were 12
(six main, three generator, three verifier), with four strategy queries. Both
positive proofs were **probe-assisted**, not unaided model outputs; neither is
a first-pass model success. One repeated candidate reused its known rejection,
so model rounds and actual Kimina checks intentionally differ.

The offline suite collected 183 tests: 177 passed, six live tests skipped.
All six opt-in real-service tests then passed in 86.1 seconds, including an
injected informal PASS plus an invalid local fact on a false theorem. Those
tests use scripted model responses and are distinct from the actual-Qwen run.

This completes the bounded engineering regression gate. It does not establish
general accuracy or a causal benefit from informal reasoning: two fixtures are
variants of the same AM–GM inequality and were used during development. Retain
the earlier failed runs, and use repeated held-out paired experiments plus
`--no-salvage` before making research claims about Qwen or V3 accuracy.
