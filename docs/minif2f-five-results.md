# MiniF2F five-problem evaluation — 2026-09-10

**Verified theorem-solving accuracy: 0/5 (0%).** All five problems completed
their three formal-generation rounds; none produced a Lean-accepted proof.
This is a small validation-set result, not a full MiniF2F test-set score.

## Fixed protocol

- Dataset: pinned MiniF2F Lean 4 port, revision
  `5746b7d6c47855ce1294bed87329618ff7f1bc31`.
- Selection: first five validation cases in deterministic seed-0 ordering;
  no substitutions or cherry-picking based on outcomes.
- Model: `mlx-community/Qwen3-8B-4bit`, local MLX-LM; V4 configuration.
- One solve episode per problem, three formal rounds, 512 output tokens per
  formal request. Informal/discussion roles retained their configured budgets.
- Retrieval, informal generator/verifier, discussion and fresh contexts enabled.
- Whole-proof fallback and prefix salvage disabled, as in the benchmark runner.
- Only unchanged theorems accepted by Kimina and a fresh independent audit
  qualify as successes. No candidate reached the final audit stage in this run.
- Configuration, code and dataset fingerprints are preserved in the JSON trace.

## Results

| Problem | Verified | Formal rounds | Attempt time | Final failure observed |
| --- | --- | ---: | ---: | --- |
| `algebra_sqineq_4bap1lt4bsqpap1sq` | No | 3 | 309.27 s | Nonexistent `pow_two_non_neg`; type mismatch and unsolved goals |
| `mathd_numbertheory_284` | No | 3 | 309.18 s | Invalid tactic syntax and unsolved goals |
| `numbertheory_aneqprodakp4_anmsqrtanp1eq2` | No | 3 | 380.71 s | Invalid induction-case tag/variable handling; categorized `unknown` |
| `mathd_algebra_536` | No | 3 | 217.73 s | Used a proof of an equality where a number was expected |
| `mathd_numbertheory_303` | No | 3 | 369.50 s | Nonexistent `Finset.coe_set_eq` and type mismatches |

Session wall time, including preflight and cleanup: **1,595.73 s (26.6 min)**.
Summed attempt time: 1,586.40 s. The session completed within its 1,800-second
soft budget and unloaded its managed model without cleanup errors.

## Cost and instrumentation

- 15 formal candidates, all rejected; 29 total model calls.
- 18,230 generated tokens; 60,215 prompt tokens reported by the backend.
- 5 informal-generator calls, 4 informal-verifier calls.
- 5 discussion calls and 5 fresh-subproblem calls.
- 126 retrieval tool calls and 40 Lean-LSP calls.
- 15 agent Kimina checks; no final independent rechecks, since there were no
  provisionally successful candidates.
- No infrastructure-failed theorem attempts. Kimina was initially offline;
  it was started and the empty checkpoint resumed before solving began.

## Interpretation

These diagnostics show substantial Lean formalization problems in the generated
candidates. They do not imply the benchmark theorems are false, nor isolate
mathematical reasoning quality from formalization quality. In particular, the
inequality trajectory identified a square-nonnegativity strategy but failed to
express it as valid Lean.

Five problems are too few to estimate broad MiniF2F performance reliably. This
run measures the current, bounded V4 configuration only; there is no paired
baseline here, so it cannot establish whether individual V4 components help.
No code, prompt or budget fixes were applied mid-evaluation to improve the score.

Full machine-readable result:
[minif2f-five-valid-v4.json](../runs/minif2f-five-valid-v4.json).
Immutable snapshot:
[completed run](../runs/history/minif2f-five-valid-v4-20260910T060209660141Z-minif2f-valid-383aea69.json).
The generated run artifacts remain local and are excluded from Git by default.

Reproduction (choose a new output path for a new independent run):

```bash
.venv/bin/local-lean-agent --config config/local.toml minif2f-run \
  --split valid --limit 5 --seed 0 --variant v4 --max-rounds 3 \
  --max-seconds 1800 --output runs/minif2f-five-repeat.json
```
