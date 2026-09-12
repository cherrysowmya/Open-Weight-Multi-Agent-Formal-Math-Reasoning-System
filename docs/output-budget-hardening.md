# Formal output-budget hardening experiment

This opt-in change addresses output-budget recovery only. It does not add a
specialist, enable tactic fallbacks, change informal/discussion budgets, or
change the theorem-solving round budget. The previous MiniF2F result remains
0/5; implementing a policy is not evidence of improved accuracy.

## Policies

| Policy | Initial / ordinary formal requests | Truncated, non-repetitive candidate | Repetitive candidate | V4 fresh request |
| --- | --- | --- | --- | --- |
| `legacy` (default) | `generation.max_output_tokens` | Reset, capped by `reset_output_tokens` | Reset with the same cap | Also capped by `v4.fresh_max_output_tokens` |
| `fixed` | Configured output allowance | Reset with the same allowance | Reset with the same allowance | Same allowance; no hidden 512-token cap |
| `adaptive` | Configured initial allowance | Double the preceding allowance, up to the recovery ceiling | Reset to the initial allowance | Shares the adaptive allowance; no hidden 512-token cap |

Example adaptive trajectory: **512 → 1,024 → 2,048**, but only after distinct,
non-repetitive truncated responses. A subsequent non-repetitive repair retains
the increased allowance. At the ceiling the next repair keeps that allowance;
the normal formal-round limit still terminates the loop. Changing the theorem
or using forbidden placeholders does not earn more tokens.

“Non-repetitive” is a heuristic, **not** a mathematical quality judgment. We
detect existing degeneration patterns, duplicate rejected proofs, and repeated
lines/assertions even when their local names change (`hp1`, `hp2`, ...), and
eight consecutive identical comma-separated identifiers (runaway rewrite lists).
False positives are possible. Every task-preserving candidate still goes through
Lean verification; a valid proof is accepted even if flagged as repetitive or
if the server reports `finish_reason="length"`.

Adaptive recovery requests a complete replacement file, not an unchecked
continuation appended to a partial proof. The prompt warns that apparent partial
progress is not verified. Ordinary repair includes the candidate; V4 isolated
requests preserve their existing compact-packet boundary and do not inherit a
full candidate conversation.

Both new policies reserve output capacity before sending the request, using
conservative input estimates. Main input + output must fit 12,288 tokens, and
V4 isolated input + output must fit 8,192 (or a smaller configured limit).
Compression may remove advisory material but not the theorem or goal. If the
request cannot fit, it stops with a context-budget failure rather than silently
exceeding the limit. These are conservative estimates, not exact tokenizer counts.

## Run one sample

From the project root, with local Kimina available:

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/my_example.lean --max-rounds 3 \
  --formal-output-policy adaptive \
  --formal-output-tokens 512 --formal-output-ceiling 2048 \
  --output runs/output-budget-sample.lean
```

The model remains local MLX Qwen3. No provider-specific logic was added to the
agent policy; the existing backend receives the selected `max_tokens` argument.

## MiniF2F experiment

The same options work with `minif2f-run`. For the existing five development cases:

```bash
.venv/bin/local-lean-agent --config config/local.toml minif2f-run \
  --split valid --limit 5 --seed 0 --variant v4 --max-rounds 3 \
  --formal-output-policy adaptive \
  --formal-output-tokens 512 --formal-output-ceiling 2048 \
  --output runs/minif2f-output-adaptive.json
```

Suggested conditions (use a different output path for each):

| Condition | CLI options |
| --- | --- |
| Legacy control | `--formal-output-policy legacy --formal-output-tokens 512` |
| Fixed 512 | `--formal-output-policy fixed --formal-output-tokens 512` |
| Fixed 1,024 | `--formal-output-policy fixed --formal-output-tokens 1024` |
| Fixed 2,048 | `--formal-output-policy fixed --formal-output-tokens 2048` |
| Adaptive | `--formal-output-policy adaptive --formal-output-tokens 512 --formal-output-ceiling 2048` |

Hold model, temperature, selected IDs, formal rounds, retrieval, informal and
discussion settings fixed. Larger output ceilings increase available compute:
these conditions are **equal-round**, not equal-token or equal-time comparisons.
Report success together with total tokens, all model calls, latency, truncation,
repetition and compiler failures. Counterbalance condition order when repeating.
The existing 0/5 trace is historical reference; run a contemporary legacy control
for a stronger comparison. The fixed policy also includes the additional
renamed-assertion and rewrite-list repetition heuristics, so comparison with legacy is not a
perfect single-variable intervention.

The old baseline files are not overwritten. Source/config changes deliberately
prevent resuming an old checkpoint with a different policy. Use a new output
path, and `--resume` only with matching flags and unchanged code/config. The
default 1,800-second session budget is soft; additional sessions may be required.
These five cases are development data now; evaluate a chosen policy on untouched
problems before claiming general improvement.

## TOML configuration

Equivalent settings under `[generation]`:

```toml
output_budget_policy = "adaptive"
max_output_tokens = 512
max_recovery_output_tokens = 2048
reset_output_tokens = 512 # Legacy only; adaptive resets to max_output_tokens.
```

The checked-in `config/local.toml` remains `legacy` to avoid silently changing
baseline behavior. For `fixed` and `adaptive`, `v4.fresh_max_output_tokens` is
superseded for **formal** fresh requests only. Discussion and informal output
budgets are unchanged. CLI choices support 512, 1,024 and 2,048; TOML accepts
other positive integer budgets subject to context validation.

## Logging and verification

Each formal request emits `formal_output_budget_selected`, including policy,
previous output status, action, role, requested output tokens, estimated input
tokens and context limit. Iteration records include:

- `requested_output_tokens`
- `output_budget_action`
- `output_status`: `not_truncated`, `repetitive`, `truncated_nonrepetitive`, or
  `truncated_repetitive` (`not_recorded` for non-model/older records).
- The existing, separate `verification.failure_category` and Lean diagnostics.

Missing finish reasons are not guessed from token usage; `not_truncated` means
no reported truncation, not a guarantee of complete output. Repetitiveness is
recorded independently of correctness. Summary counters are
`formal_output_truncations`, `formal_output_repetitions`, and
`formal_output_budget_increases`. The increase counter counts dispatched expanded
requests, not successful repairs. Raw requests are in
`metrics.formal_output_budget_requests`; output classification is recorded after
verification returns, so interrupted in-flight work may have only a request event.

Tests:

```bash
.venv/bin/python -m unittest tests.test_output_budget -v
.venv/bin/python -m unittest discover -s tests -q
```

Opt-in controlled test against real local Kimina (scripted model outputs, not
a model-accuracy experiment):

```bash
RUN_OUTPUT_BUDGET_LEAN_TESTS=1 .venv/bin/python -m unittest tests.test_output_budget -v
```

Validation on 2026-09-11: all 29 output-policy tests passed with the live test
enabled. That includes two real-Lean rejections followed by a valid proof at
requested budgets 512, 1,024, and 2,048, and an independent final recheck.
The full suite discovered 276 tests: 262 passed, 14 opt-in tests skipped.

Deterministic tests use injected model responses and fake compiler outcomes.
They validate routing, safety and accounting—not model quality. The MLX adapter
test additionally checks that all three allowances reach the HTTP request without
clamping. MiniF2F tests check summary counters and rejection of policy changes
on resume. A live five-condition accuracy experiment is not yet completed.

## Real-Qwen smoke result and follow-up fix

The saved `runs/minif2f-output-adaptive-smoke.json` is an actual local-Qwen run
on `algebra_sqineq_4bap1lt4bsqpap1sq` (V4, three rounds, initial 512 tokens,
ceiling 2,048). It finished **0/1**, taking 393.3 seconds for the attempt.
Requested formal budgets were **512, 512, 1,024**. This does not demonstrate
an accuracy gain over the historical baseline.

Inspection exposed a detector gap: rounds 2 and 3 repeated `mul_two` many times
inside one unfinished `rw [...]` list. The original line-based detector labeled
these truncated but non-repetitive, incorrectly granting more tokens for round 3.
The comma-list detector and four regression tests were added after this run.

Reclassifying the saved candidates with the corrected detector gives
`truncated_repetitive` for both rounds 2 and 3, selecting `reset_repetitive`
with 512 tokens for any subsequent request. This is a retrospective routing
check, **not a new generation run or proof success**. The original artifact and
its original classifications remain unchanged. The corrected policy's effect
on live model accuracy still needs the paired experiment above.
