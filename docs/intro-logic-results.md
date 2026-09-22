# A Lean Intro to Logic: initial validation

## Dataset and software checks

- Dataset: 88 statement-only levels from `Trequetrum/lean4game-logic`, revision
  `40ceec5f3ca5dce6cec2800b8f5e4927631ca2da`.
- All 88 statement probes compiled with Lean 4.26 and passed Kimina validation.
  This tests statement elaboration, **not theorem-solving accuracy**.
- Offline regression suite: 351 passed, 16 skipped, including 20 tests for the
  logic dataset, CLI, script, independent audits and checkpoint behavior.
- Startup wrapper passes macOS Bash syntax and argument-boundary checks.

The original game uses Lean 4.7 and pedagogical tactic inventories. This
adaptation permits unrestricted Mathlib proofs and excludes supplied solutions,
hints and game helper proofs. Intro/Tactic pairs are often duplicate or related
statements; they are not independent train/test splits.

## Local model smoke test

Configuration: Qwen3-8B 4-bit through MLX-LM, `v4`, proof-body output, one
trajectory per theorem, three formal rounds, 512-token formal output allowance.
Informal reasoning triggers after a formal failure; discussion/fresh contexts
trigger after two. The specialist and automatic tactic portfolio are disabled.

Reproduction command:

```bash
.venv/bin/local-lean-agent --config config/local.toml intro-logic-run \
  --limit 5 --seed 0 --variant v4 --max-rounds 3 --max-seconds 600 \
  --proof-format proof_body --live --output runs/intro-logic-five-v4.json
```

Use a new output filename to repeat the experiment. Use `--resume` with the
same filename and settings to continue a partial run; it must pass fingerprint
checks. The 600-second limit is checked between theorem attempts, so a session
can overrun it while completing the current theorem.

Run ID: `c9cf5408e43745229de2c0d3ac0570a7`.

Code fingerprint:
`3d16d25cb37cb234cf6b23d7b8a037dad26d8d6efc98d81273aa2e914fb0b989`.

Mathlib revision: `2df2f0150c275ad53cb3c90f7c98ec15a56a1a67`;
Lean `4.26.0`, MLX `0.32.2`, MLX-LM `0.31.3`.

Artifacts (local, intentionally excluded from Git):

- `runs/intro-logic-validation.json`: all statement validation results.
- `runs/intro-logic-five-v4.json`: checkpoint, full attempts and aggregate metrics.
- `runs/intro-logic-five-v4.events.jsonl`: agent/tool events.
- `runs/intro-logic-five-v4.proofs/`: independently audited successful proofs,
  created only when there is a verified success.

## Completed result: 2/5 verified (40%)

| Selected case | Result | Formal rounds | Agent seconds |
| --- | --- | ---: | ---: |
| `intro_logic_AndTactic_L08` | Failed: conjunction projections/type mismatches | 3 | 287.08 |
| `intro_logic_ImpTactic_L07` | Failed: invalid tactics/application, repeated candidate | 3 | 163.31 |
| `intro_logic_ImpTactic_L06` | Verified, independent audit passed | 1 | 3.23 |
| `intro_logic_IffTactic_L07` | Failed: invalid introduction/split, repeated candidate | 3 | 265.33 |
| `intro_logic_NotTactic_L03` | Verified, independent audit passed | 1 | 14.70 |

All five selected cases were attempted. Both successes were first-candidate
successes. No specialist or automatic tactic portfolio was used. The three
failures exhausted the three-round formal budget; they are not claims that the
statements are false. Informal acceptance never overrode Lean rejection.

Aggregate metrics:

- Agent time: **733.65 seconds**; summed execution sessions: **793.31 seconds**
  including preflight, independent audits and cleanup, excluding the gap between
  sessions. Warm/cold service state differed across the resume boundary.
- 19 model calls: 11 formal, 3 informal generator, 2 informal verifier,
  3 discussion. Three formal calls used fresh contexts.
- 24,670 prompt tokens and 8,513 generated tokens.
- 9 agent Kimina checks, 2 independent proof rechecks, 16 Lean-LSP calls,
  72 retrieval tool calls.
- 9 rejected generated candidates, including locally rejected repetitions;
  these are not all separate compiler executions.
- Zero formal-output truncations. One informal generator response exhausted
  its output allowance; this is a separate failure from formal truncation.

The first 600-second soft session stopped after four problems (741.27 seconds
elapsed). The local verifier was restarted and the exact checkpoint resumed
with `--resume`; the fifth problem completed in the second session. No earlier
attempt was rerun or replaced. An unavailable-verifier launch made no new
attempt. The completed report has `experiment_complete: true`, five completed
attempts, and zero unattempted problems.

Completed snapshot:
`runs/history/intro-logic-five-v4-20260922T172224994063Z-intro-logic-all-c9cf5408.json`.

This small smoke test validates the execution/reporting pipeline and exposes
Lean formalization weaknesses. It does **not** establish accuracy on all 88
levels, a V4-versus-V5 advantage, or statistical reliability. Keep these inputs
and budgets fixed for a later labelled comparison; do not tune on them and
describe the resulting score as held-out performance.
