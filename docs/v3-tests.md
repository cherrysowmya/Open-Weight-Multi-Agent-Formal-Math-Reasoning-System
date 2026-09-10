# V3 testing guide

V3 is V2 plus informal generation and independent criticism. Tests must separate
software correctness, model mathematical judgment, and formal proof success.
No mocked verifier verdict demonstrates that Qwen can detect a mathematical gap.

## Mandatory twelve checks

| Requirement | Deterministic coverage | Live coverage |
| --- | --- | --- |
| 1. Parseable informal proof | Outline parsing and bounds | Five generator fixtures |
| 2. No Lean code in outline | Code guard | Generator format checks |
| 3. Accept valid reasoning | JSON PASS handling | Two valid-proof fixtures |
| 4. Reject obvious invalid reasoning | JSON FAIL handling | Universal-nonnegativity mistake |
| 5. Catch missing assumption | Critique handoff | Division-by-zero fixture |
| 6. Catch subtle error | Verdict parsing | Square-root sign fixture |
| 7. Generator → verifier happy path | Two fresh requests, same backend | Opt-in full informal solve |
| 8. FAIL → revision → PASS | Injected responses, feedback assertions | Seeded sign-error refinement |
| 9. Fresh verifier context | Payload, UUID, history, secret-token assertions | Request logs in every component run |
| 10. Bounded refinement | Exactly three generator/reviewer pairs | Config and per-request logs |
| 11. Approved plan → Lean | No prose passed directly to compiler | Scripted-model, real-Lean AM–GM repair |
| 12. Trivial task bypass | Zero informal calls | Real-Lean `n + 0 = n` test |

This is a coverage map, not a claim that every live fixture has been run or
passed. See [recorded validation results](v3-test-results.md) for measured outcomes.

## 1. Deterministic software tests (no model or services)

```bash
.venv/bin/python -m unittest discover -s tests -v
```

V3 coverage lives in `test_informal.py`, `test_v3_contracts.py`,
`test_v3_suite.py`, and `test_v3_evaluation.py`. The opt-in Lean tests are skipped
by this command; skipped tests are not reported as live successes.

The assertions cover parseable outlines; obvious Lean-code rejection; complete
theorem preservation in task packets; output and context budgets; strict JSON
review validation; malformed, empty, contradictory, truncated, and duplicate-key
responses; a happy path; FAIL/feedback/revision/PASS; exact three-pair exhaustion;
unique conversation IDs; `history_messages=0`; BANANA-1937 leakage checks on
actual payloads; no cross-theorem history; invocation after exactly two formal
failures; easy-task bypass; seed rejection not counted as a model failure; no
invocation after the formal budget; one informal invocation per attempt; shared
backend reuse; generator-only behavior; and PASS never bypassing Lean or `sorry`
rejection. Informal prose is not submitted directly to Kimina.

Every request records its role, conversation UUID, history size, exact messages,
sampling settings, output budget, and context estimate. A UUID is for auditing;
isolation is enforced by building new messages with no prior transcript. A
token in the *public current outline* is intentionally visible to the reviewer;
the leakage test instead inserts a token into generator-only instructions.

## 2. Live Qwen mathematical-quality tests

The scoring-only manifest `benchmarks/v3/components.toml` has 14 cases:

| Group | Cases |
| --- | --- |
| Generator | Square nonnegativity, square of a difference, sum nonnegativity, AM–GM via square difference, sum of squares |
| Valid reviews | Expanded square-difference proof; equality-hypothesis substitution |
| Invalid reviews | Assumed universal nonnegativity; square-root sign loss; division by zero; circularity; omitted negative case; proving a different conclusion |
| Seeded revision | An invalid square-root argument revised for the true target `a=b ∨ a=-b` |

Run all cases locally (potentially many minutes):

```bash
.venv/bin/python scripts/test-v3-live.py \
  --output runs/v3-components-latest.json
```

Or run selected cases:

```bash
.venv/bin/python scripts/test-v3-live.py \
  --case verifier_obvious_false_assumption \
  --case verifier_missing_nonzero_assumption \
  --output runs/v3-selected-components.json
```

Direct verifier tests call `QwenInformalReasoner.verify` without invoking a
generator. Expected verdicts and rubric regexes never enter a model prompt.
Generator tests check format and loose strategy-concept rubrics, not exact
wording. These are **heuristic relevance checks**, not proof that a generated
argument uses no unsupported assumption or is mathematically sound. Review
the saved outlines and critiques manually for that stronger claim.

The revision case has a deliberately supplied bad draft. It does not claim that
Qwen generated that mistake. The original false target `a²=b² → a=b` cannot be
repaired into a valid proof without changing assumptions or conclusion; that
remains a negative-verifier test. The revision test uses the true disjunction
from the outset, and preserves it through both reviews.

The verifier is prompted for structured JSON:

```json
{
  "verdict": "FAIL",
  "issues": ["The proof omits negative values of x."],
  "feedback": "The theorem is true but the argument is incomplete.",
  "suggested_fix": "Cover both signs or use square nonnegativity.",
  "confidence": 0.8
}
```

`PASS` maps to internal `accept`; `FAIL`/`REVISE` requests revision; `REJECT`
stops the informal loop for a false statement or fundamental obstruction.
Confidence is optional, uncalibrated, and never controls correctness. Historical
tagged reviews remain parseable for compatibility; new prompts request JSON.
Unparseable or truncated reviews are errors, never PASS. No hidden native
thinking is promoted to an outline when MLX returns only a reasoning field.

## 3. Real Lean/LSP/retrieval integration

```bash
LOCAL_LEAN_AGENT_LIVE_LEAN=1 .venv/bin/python -m unittest \
  tests.test_v3_lean_integration -v
```

These use deterministic model replies and **real** Kimina, Lean-LSP-MCP, and
LeanExplore. They require your services to be available. The primary test forces
two distinct failed Lean candidates for `2*x*y ≤ x²+y²`, then supplies the
informal square-difference plan, an independent review, and the formal candidate:

```lean
by
  nlinarith [sq_nonneg (x-y)]
```

It checks two real rejections, retrieval/LSP activity, exactly one generator and
one verifier call, an approved plan passed to the main prompt, and independently
recompiles the final proof. Other cases prove that an injected informal PASS
does not make a false theorem succeed and that a trivial theorem bypasses V3.
Artifacts are saved as `runs/v3-integration-*.json` and a separate event JSONL.
This demonstrates the end-to-end software contract, **not a measured improvement
in Qwen accuracy**.

For a real-Qwen demonstration, with V3 deliberately enabled (ordinary solves
may also use Lean-checked fallback/prefix probes; inspect the final record's
`generation.model` for provenance):

```bash
local-lean-agent --config config/local.toml solve \
  benchmarks/v3/cases/21_square_difference.lean \
  --informal-policy always --output runs/square_difference.proved.lean
```

## 4. Paired evaluation and verifier ablation

The original 20-case integration set remains `benchmarks/v3/manifest.toml`.
The additional eight-case `reasoning.toml` emphasizes the requested transformations:
six positive generation/repair cases and two false statements about square roots
and division by zero. Counterexamples are in `benchmarks/v3/counterexamples.lean`.

```bash
local-lean-agent --config config/local.toml v3-validate \
  benchmarks/v3/reasoning.toml --output runs/v3-reasoning-validation.json

# V2 versus generator + verifier
local-lean-agent --config config/local.toml v3-ablation \
  benchmarks/v3/reasoning.toml --output runs/v3-reasoning-ablation.json

# Generator only versus generator + verifier
local-lean-agent --config config/local.toml v3-ablation \
  benchmarks/v3/reasoning.toml --control generator-only \
  --informal-policy always --output runs/v3-verifier-ablation.json
```

The control is labeled in `control_variant` and every attempt's `variant`.
Both conditions share main-model settings, retrieval/LSP, formal round budgets,
and disabled whole-proof fallbacks **and compiler-prefix probes**. They differ
in the selected informal components, including configured strategy search and
rewrite prompts. This evaluates the V3 package, not reasoning text alone; use
separate feature ablations to attribute effects to those hardening components.
The generator-only arm uses an **unreviewed** outline; it never gets a fabricated
PASS. Use `--repetitions 3` for repeated pairs. Model processes are reused
sequentially, not loaded once per role. Preserve each immutable run artifact.

Reported metrics include positive success, first-generated-candidate success,
successful formal repairs, average Kimina checks (including seeds), rejected
candidate rounds, wall time, all-role call counts, generated tokens, and timeouts.
The first-pass metric excludes input seeds and is reported over positive cases.
Formal budgets are matched; **total token/time budgets are not matched** because
V3 spends extra calls. Do not infer compute-efficiency superiority from accuracy
alone.

`informal_pass_downstream_failure_rate` is PASS followed by formal failure divided
by all PASS cases. It is an **operational disagreement proxy, not a mathematical
false-positive rate**: a sound argument may still fail during formalization.
`revision_next_pass_rate` counts explicit revision requests whose next review
passes; `revised_plan_lean_successes` additionally requires eventual Lean success.
Undefined rates have JSON `null`, not a misleading zero.

For a completed paired run, recheck final proofs and negative budgets:

```bash
.venv/bin/python scripts/audit-v3.py runs/v3-reasoning-ablation.json
```

Minimum viable evidence is passing contracts, valid fixtures, real Lean
integration, and an honestly reported live quality run. Good engineering work
includes stronger token/memory accounting and robust output-budget handling.
Research claims need larger held-out problems, manually checked argument
labels, repeated paired runs, and matched-compute controls. An easy suite can
show a null result or regression even when all components function correctly.
