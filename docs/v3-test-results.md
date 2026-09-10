# V3 validation results — September 2, 2026 (America/New_York)

These results separate software contracts, real compiler behavior, and model
quality. Artifact timestamps after midnight use September 3 UTC.

## Tests actually run

| Layer | Observed result | Evidence |
| --- | --- | --- |
| Offline regression suite | 145 passed; 3 opt-in integration tests skipped | `python -m unittest discover -s tests -q` collects 148 tests |
| Real Lean/LSP/LeanExplore integration | 3/3 passed, with scripted model replies | `runs/v3-integration-{square-difference,false-pass,easy}.json` |
| Direct real-Qwen verifier checks | 5/5 selected cases passed | `runs/v3-verifier-quality-smoke.json` |
| Real-Qwen generator and seeded refinement | 2/2 selected cases passed | `runs/v3-generator-refinement-smoke.json` |
| Original 20-case fixture preflight | 18 reference proofs compiled; 8 bad seeds rejected | `runs/v3-validation-latest.json` |
| Additional 8-case reasoning preflight | 6 reference proofs compiled; 1 bad seed rejected | `runs/v3-reasoning-validation.json` |
| Independent saved-smoke audit | Accepted proof recompiled, rejected final candidate still rejected, 2 negative budgets checked, all four counterexamples compiled | `runs/v3-smoke-audit.json` |

The 148 collected tests include the project's earlier regression coverage;
they are not 148 newly added V3 tests. Full Python byte-compilation also passed.
The live component scripts preserve immutable copies in `runs/history/`.

## What the live checks demonstrated

The five direct Qwen reviews accepted the valid expanded-square argument and
rejected the universal-nonnegativity assumption, square-root sign error,
division-by-zero cancellation, and circular irrationality argument. The sign
error was labeled `REVISE`, not `REJECT`; the test requires detection of the
invalid argument rather than exact error-category wording.

The generator-only AM–GM case produced the transformation to `(x-y)² ≥ 0`.
In the seeded refinement case, Qwen criticized the supplied square-root mistake
and generated a factorization argument using `(a-b)(a+b)=0`. A fresh review
accepted it. The target was `a=b ∨ a=-b` from the outset, not the false `a=b`
target. These two checks took approximately 103 seconds; the five reviews took
approximately 160 seconds. They reused one local Qwen model per run.

The real compiler integration forced two rejected candidates, passed the
informal outline to the main prompt, and checked
`nlinarith [sq_nonneg (x-y)]` successfully. There were three Kimina checks and
one generator/verifier pair. The other integration cases kept a false theorem
rejected despite an injected PASS, and proved `n+0=n` with no informal calls.
The model replies here were scripted: this proves wiring and the verification
boundary, not that Qwen independently repaired that Lean proof.

Regression tests also exposed and fixed overly broad timeout classification
(`RuntimeError` was accidentally matched as a timeout) and unclosed MCP output
pipes during integration-test teardown.

## What is not established

Only seven of the fourteen live component fixtures were run in this validation
batch. Passing heuristic outline rubrics does not certify mathematical soundness.
No full 20-case or focused 8-case paired model experiment, and no live
generator-only versus generator+verifier ablation, has been completed for this
revision.

An **earlier**, small paired smoke run is preserved in
`runs/v3-smoke-latest.json`: V2 solved its one positive theorem, V3 did not;
both correctly failed the negative theorem. Attempt wall time was about
32.9 seconds for V2 versus 262.4 seconds for V3. The saved V3 final candidate
was independently rechecked and remained invalid. That run predates the new
JSON-review prompt and has its own code fingerprint, so it must not be presented
as a benchmark of the final revision. It is nevertheless evidence against
assuming that adding informal roles automatically improves formal success.

The next experiment is the paired focused reasoning set, followed by the
generator-only control. See [the testing guide](v3-tests.md) for exact commands.
Report Lean success, latency, tokens, refinement outcomes, and PASS/downstream
failure separately. A downstream formal failure is not by itself proof that
the informal mathematics was false.

## V3.1 hardening follow-up

The later strategy-retrieval and rewrite-recovery work is documented separately
in [V3.1 hardening](v3-hardening.md), including the retained 2/3 development runs.
The user's post-parser run solved the generation case but failed the seeded
repair. Additional compiler-prefix repair now targets the observed invalid
local-fact/rewrite pattern; these probes are logged separately from Qwen output.

Validation on September 3, 2026 for prompt revision `v3.1.2`:

- Offline collection: **183 tests**, **177 passed**, six opt-in tests skipped.
- All **six real Kimina/LSP/LeanExplore integration tests passed** (86.1 seconds),
  with scripted model replies. This now includes strategy-retrieval wiring,
  positive prefix repair, and a false theorem with a false local fact.
- Positive prefix repair used one main-model response and one compiler probe,
  and the resulting proof independently recompiled. See
  `runs/v3-integration-prefix-salvage.json`.
- The negative prefix case stopped after two formal rounds. All eight bounded
  probes failed; the injected informal PASS did not create a Lean success. See
  `runs/v3-integration-false-prefix-salvage.json`.
- Python byte-compilation passed.

These compiler tests establish the safety/wiring contract, not real-Qwen quality.
The final real-Qwen run completed **3/3** in 386.5 seconds: both positive proofs
passed independent compilation after one compiler-prefix probe each, and the
false division theorem stopped at two rounds. All informal stages completed
without exhaustion. Evidence: `runs/v3-hardening-final-validation.json` and its
immutable history copy, detailed in the hardening guide. This small development
regression is not a held-out accuracy estimate or a reasoning-only ablation.
