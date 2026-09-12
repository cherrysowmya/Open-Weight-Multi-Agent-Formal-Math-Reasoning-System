# Proof bodies and bounded tactic-portfolio evaluation

These are separate, opt-in hardening features. The historical five-problem
MiniF2F result and its whole-file configuration are not overwritten.

Completed five-problem comparison: [results and limitations](proof-body-portfolio-results.md).
Portfolio-only and agent-plus-portfolio each verified 2/5; body-only V4 verified
0/5. Both successes were attributable to `omega` / `norm_num`, not model proofs.

## Immutable proof-body generation

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/my_example.lean --proof-format proof_body \
  --output runs/my_example-body.proved.lean
```

The formal model returns only the expression after the final target's `:=`,
including `by` for a tactic script, for example:

```lean
by
  nlinarith [sq_nonneg (x - y)]
```

Python retains imports, preceding declarations, the target statement, and any
trailing namespace/section `end` lines. It inserts and indents the body, then
checks the **complete file** through the existing integrity guard and Kimina.
Raw model output and the assembled candidate are both logged. If the model
echoes a whole file or just its theorem declaration, the adapter extracts its
body **only** when the returned prefix matches the original full prefix or
target declaration (ignoring layout, not identifiers or string contents).
A single leading `:=` is also removed. Changed imports, statements, extra
declarations, or unexpected suffix commands remain format/integrity failures.
The returned prefix is never used to assemble the file: Python's original task
is always retained. `sorry` and `admit` still fail verification. The parser is
deliberately narrow: a final line-start theorem/lemma/example with `:=` is
supported; arbitrary commands after that proof or ambiguous targets fail closed.
For unsupported source layouts, extract a standalone task or use `full_file`.
This is a proof-slot assembler, not a general Lean refactoring parser.

Normal, repair, reset, rewrite-recovery, fresh, and compressed formal requests
all use the selected output contract. Informal and discussion prompts are
unchanged. Truncation recovery replaces the complete **body**, never appends an
unchecked suffix. Existing context and output budgets still apply. Source and
config fingerprints include the implementation and selected format.

Use `--proof-format full_file` for the whole-file control (still the default).
The equivalent TOML option is `generation.proof_format = "proof_body"`.
Compare formats with the same variant, problems, model, sampling and budgets;
body mode's reduced output overhead alone does not establish improved accuracy.

## Three portfolio conditions

| Variant | Behavior |
| --- | --- |
| `portfolio` | Only the configured tactic list; no model, retrieval, or LSP process |
| `v4` | Current V4 agent, no fallback portfolio or prefix salvage |
| `v4_portfolio` | Same V4 agent, with portfolio after its first rejected formal candidate |

The current list, unchanged from the existing implementation, is `rfl`, `simp`,
`norm_num`, `omega`, `positivity`, `aesop`, in that order. Every tactic is a
separate full-file Kimina check, using the original statement, stopping on the
first verified result or verifier outage. Exhausted tactics are not retried on
later model rounds. Each check retains the configured Lean timeout (60 seconds
in local.toml); MiniF2F files also have their existing heartbeat bound. Prefix
salvage is disabled in all these benchmark conditions to avoid mixing methods.
No problem-specific tactics or solution material are added to the portfolio.

Run the same five development cases under all three conditions:

```bash
.venv/bin/local-lean-agent --config config/local.toml minif2f-run \
  --split valid --limit 5 --seed 0 --max-rounds 3 \
  --variant portfolio --variant v4 --variant v4_portfolio \
  --proof-format proof_body --max-seconds 3600 \
  --output runs/minif2f-body-portfolio-five.json
```

Both model-using conditions here use body format, leaving the portfolio
enablement as their difference. The historical whole-file 0/5 run is only a
historical reference, not a matched test of the body-format effect. For a
matched format ablation, rerun `v4` alone with `--proof-format full_file` into
a new output path. The output-budget policy remains `legacy` unless explicitly
overridden, so adaptive budget changes are not silently mixed into this trial.

The runner alternates condition order by problem. Runs are resumable with
`--resume` and identical arguments, code, config and environment. The session
time cap is soft: a current theorem may finish after the cap. Always use a new
output path for changed conditions. This comparison is bounded but **not equal
compute**: `v4_portfolio` gets extra compiler checks; report cost alongside
success rather than attributing its total success rate solely to Qwen.

## Attribution and safety

Summary fields include `proof_format`, `portfolio_checks`,
`portfolio_wall_clock_seconds`, `portfolio_verified_problems`, and
`portfolio_tactic_successes`. `fallback_attempts` records each tactic, complete
candidate, compiler result, and measured wall time. Model-call and generated-token
counts exclude tactic trials; total Kimina checks include them. Final independent
rechecks and statement preflights remain separately recorded. A portfolio success
only counts in benchmark accuracy and per-tactic successes after the independent
audit succeeds. A Lean timeout is not evidence that a theorem is false.

## Tests

```bash
.venv/bin/python -m unittest tests.test_proof_body_portfolio tests.test_minif2f -q
RUN_PROOF_BODY_LEAN_TESTS=1 .venv/bin/python -m unittest tests.test_proof_body_portfolio -q
```

The opt-in test compiles the square-difference body with actual Kimina, rejects
`sorry`, independently rechecks a portfolio proof, and exhausts the portfolio
on a false theorem. Scripted model tests exercise repair and V4 isolated contexts;
they establish software behavior, not model reasoning quality.

### Body-response recovery validation (2026-09-12)

The body-only prompt now includes a short output example and explicitly forbids
echoing `:=`. Guarded normalization is applied in all formal body-format paths,
including fresh contexts. `IterationRecord.proof_body_normalization` and the
`proof_body_response_normalized` event record `body`,
`matching_declaration_extracted`, `assignment_delimiter_removed`, or `rejected`;
raw model output is preserved. Whole-file control mode is unaffected.

An offline replay of the saved comparison's 26 formal responses classified
7 as matching-declaration extraction, 9 as delimiter removal, and 10 as ordinary
bodies. All preserved the original task prefix. This replay tests normalization
only: it neither recompiled those historical candidates nor changed the saved
scores. The fixes do not establish improved mathematical reasoning or benchmark
accuracy. The original run remains an accurate record of the earlier behavior.

Validation: 304 tests discovered, 289 passed and 15 opt-in tests skipped.
Separately, all 24 body/portfolio tests passed with real Kimina enabled,
including compilation of normalized whole-declaration and `:=` responses,
and rejection of an unresolved false-theorem proof. No new Qwen accuracy run
was performed for this normalization fix.
