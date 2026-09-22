# V7 implementation validation — 2026-09-10

> Historical methodology notice: automatic tactic portfolios and prefix-salvage
> trials have been removed. Related commands, settings and assisted scores below
> describe older versions, not the current solver. See [agent-only repair](agent-only-repair.md).

V7 MiniF2F ingestion, evaluation, checkpointing and independent proof auditing
are implemented. This is not a completed full-split model evaluation and does
not imply that V5/V6 are implemented.

## Contract and compatibility checks

- Full offline suite: 244 tests discovered, 231 passed, 13 opt-in live tests skipped.
- MiniF2F suite with `RUN_MINIF2F_LEAN_TESTS=1`: all 29 tests passed, including
  three real-Kimina checks (valid proof, placeholder rejection, statement parsing).
- All 244 validation statements elaborated successfully in local Lean/Mathlib.
- All 244 test statements elaborated successfully, without model inference.
- The compatibility pass discovered and fixed a colon-in-comment parser bug;
  its regression is covered by both offline and real-Kimina tests.

Compatibility reports:

- [Validation split](../runs/minif2f-validation-valid-final.json)
- [Test split](../runs/minif2f-validation-test.json)

The earlier `minif2f-validation-valid.json` is retained as diagnostic history;
it predates the parser fix and is superseded by the `-final` report.

## Bounded real-Qwen smoke comparison

One explicitly selected **validation** theorem, `mathd_algebra_10`; one solve
episode per condition, two formal rounds per episode. The configured Qwen3-8B
4-bit MLX model ran sequentially. Whole-proof fallback and prefix salvage were
disabled. This easy, hand-selected smoke case is not a representative sample.

| Measurement | Compiler repair (`lean`) | Full-stack configuration (`v4`) |
| --- | ---: | ---: |
| Independently verified proofs | 0/1 | 0/1 |
| Failed generated candidates | 2 | 2 |
| All model calls | 2 | 6 |
| Generated tokens | 355 | 3,209 |
| Attempt wall time | 51.06 s | 295.59 s |
| Retrieval tool calls | 0 | 24 |
| Lean-LSP calls | 4 | 4 |
| Informal generator / verifier calls | 0 / 0 | 2 / 2 |

The full-stack configuration was enabled, but discussion and fresh-subproblem
calls were **not triggered** within this two-round budget. This does not test
their comparative benefit. V4 ran first and bore the model's cold-load cost;
the baseline reused the same backend. Trace metrics retain these costs.

The full-stack attempt introduced natural-number arithmetic where real-valued
arithmetic was needed and later referenced an undefined `h4`. Its informal
verifier accepted a strategy, but Lean rejected both candidates. The benchmark
correctly kept `verified=false`. The baseline also exhausted its two rounds.

An independent real-Kimina contract test verified the same target with
`by norm_num`. That test-only reference was never passed to Qwen or retrieval;
it is **not** counted as an agent solve. Thus the target is demonstrably
provable even though these bounded agent trajectories failed.

Full trace: [MiniF2F smoke comparison](../runs/minif2f-live-smoke.json).
The completed-run checkpoint also has a timestamped snapshot under `runs/history`.
The run ended cleanly and unloaded its managed model.

## Interpretation and next experiment

The runner works and reports model failures honestly. There is no evidence here
that V4 improves MiniF2F success: one problem, two formal rounds and no activated
discussion/fresh-subproblem role cannot establish that. Do not treat the result
as a published MiniF2F test-set score.

Next, use the deterministic development subset in the [V7 guide](v7-minif2f.md),
freeze budgets/configuration, and evaluate paired conditions. Reserve test-set
proof generation for the frozen evaluation. Improving arithmetic formalization
is separate from completing the evaluation infrastructure.
