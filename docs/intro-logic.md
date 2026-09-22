# A Lean Intro to Logic benchmark

The system now includes all **88 levels** of
[Trequetrum's A Lean Intro to Logic](https://github.com/Trequetrum/lean4game-logic),
pinned to commit `40ceec5f3ca5dce6cec2800b8f5e4927631ca2da`.
This is a statement-only benchmark adaptation of an educational game, not a
claim that upstream publishes a standardized LLM benchmark or train/test split.

## Scope and attribution

| Topic | Intro levels | Tactic levels |
| --- | ---: | ---: |
| Conjunction | 8 | 8 |
| Implication | 9 | 9 |
| Negation | 12 | 12 |
| Disjunction | 8 | 8 |
| Equivalence | 7 | 7 |
| Total | 44 | 44 |

`benchmarks/intro-logic/data/` contains an input `.lean` file for each level,
the manifest, and upstream license/toolchain metadata. The MIT license is retained
in `data/LICENSE`. The manifest records source paths, the pinned upstream commit,
archive hash and per-file hashes. Preparing an existing dataset verifies it
offline; preparing an empty directory downloads the pinned archive once.

Only each level's `Statement` header is extracted. Python assigns a unique
theorem name, uses `import Mathlib`, sets a bounded heartbeat limit, and replaces
the supplied solution with `by sorry`. Upstream solutions, examples, story text,
hints, and `GameLogic` helper proofs are not included in model inputs.

**Evaluation mode: unrestricted Mathlib theorem solving.** Game-specific tactic
inventories are not enforced. Model-selected `simp`, `aesop`, or library lemmas
are allowed; the Python tactic portfolio remains absent. Classical reasoning is
also not prohibited, unlike the game's constructive teaching emphasis. Scores
therefore must not be presented as restricted-inventory game-playing scores.

Intro and Tactic levels include duplicate or closely related propositions;
88 levels are not necessarily 88 mathematically independent problems. Report
world/partition results and do not use these partitions as independent training
and test sets. Stripping reference proofs prevents direct prompt leakage but
does not establish absence of these public problems from model training data.

The game targets Lean 4.7; our adaptation uses the installed Mathlib toolchain
(currently Lean 4.26). One upstream level, `OrIntro/L02`, uses an implicit `K`
without listing it among its explicit binders. The input theorem preserves this.
Only the proof-independent elaboration probe makes `{K : Prop}` explicit because
Lean auto-implicit inference differs for a definition body versus theorem type.

## Run a small evaluation

Start Kimina with the existing startup workflow. From the project root:

```bash
# Already prepared in this checkout; checks its hashes without downloading.
.venv/bin/local-lean-agent intro-logic-prepare

# Check all 88 statement types without generating or assuming their proofs.
.venv/bin/local-lean-agent --config config/local.toml intro-logic-validate \
  --limit 0 --output runs/intro-logic-validation-new.json

# Five deterministic problems; up to three formal candidates per theorem.
bash scripts/start-logic-benchmark.sh \
  --limit 5 --seed 0 --variant v4 --max-rounds 3 \
  --proof-format proof_body --live \
  --output runs/intro-logic-my-five.json
```

The wrapper calls `intro-logic-prepare` and then `intro-logic-run` with your
arguments. No arguments selects five problems, one V4 attempt per theorem,
three formal rounds, and a 1,800-second soft session budget. Optional arguments
are safe on macOS Bash 3.2. You can invoke the CLI directly instead:

```bash
.venv/bin/local-lean-agent --config config/local.toml intro-logic-run \
  --limit 5 --variant v4 --proof-format proof_body --live \
  --output runs/intro-logic-my-five.json
```

An existing output is never silently overwritten. To continue a partial run,
repeat the **same arguments and output** with `--resume`. Resume verifies code,
configuration, dataset, selected cases and environment fingerprints. Changing
one of those requires a new output path. `--max-seconds` is a soft session limit
checked between theorem attempts; an in-flight attempt may run beyond it.
Infrastructure-error records remain in the trace and denominator, rather than
silently becoming proof successes or disappearing.

## Select worlds, all levels, or explicit cases

```bash
# A single world (0 means all cases in the selected partition).
bash scripts/start-logic-benchmark.sh --split AndIntro --limit 0 \
  --variant v4 --output runs/logic-andintro.json

# All 88 levels; increase the session budget deliberately.
bash scripts/start-logic-benchmark.sh --limit 0 --variant v4 \
  --max-seconds 14400 --output runs/logic-all-v4.json

# Explicit IDs override --limit and preserve the given order.
bash scripts/start-logic-benchmark.sh \
  --case intro_logic_AndIntro_L01 --case intro_logic_NotIntro_L01 \
  --output runs/logic-selected.json
```

`--split` accepts `all`, `intro`, `tactic`, or any of the ten world names such as
`AndIntro`, `ImpTactic`, and `IffIntro`. `--seed` controls deterministic subset
selection, not model sampling. `--attempts N` performs N independently logged
solver trajectories per theorem; it is not the number of candidates in one
trajectory (`--max-rounds`).

## Compare configurations, including V5

Available labels are `lean`, `v2`, `v3`, `v4`, and `v5`. All use Lean/compiler
feedback; the successive labels add retrieval, informal reasoning, V4 discussion
and fresh contexts, then the conditional formal specialist. Informal invocation
policy remains the configured policy (default after formal failures), not always-on.

```bash
# Requires cached DeepSeek weights; see docs/v5.md for setup.
bash scripts/start-logic-benchmark.sh --limit 5 --seed 0 \
  --variant v4 --variant v5 --max-rounds 3 --max-seconds 3600 \
  --proof-format proof_body --live --output runs/logic-v4-v5.json
```

The runner counterbalances condition order. The `v4` condition disables the
specialist even if shared configuration enables it; only `v5` enables it.
Specialist attempts consume the same formal round budget. Success on the first
Qwen attempt legitimately results in zero specialist calls. This extension does
not add V5 to the existing MiniF2F labels or silently change their conditions.

## Results and traces

For `--output runs/logic-v4-v5.json`, artifacts are:

- `runs/logic-v4-v5.json`: checkpoint, per-theorem full attempts, selected IDs,
  effective configs, failures, audit results and aggregate/per-world metrics.
- `runs/logic-v4-v5.events.jsonl`: detailed agent events and benchmark case labels.
- `runs/logic-v4-v5.proofs/`: only independently rechecked successful proofs.
- `runs/history/`: run-ID-labelled completed report snapshots.

Terminal output ends with a JSON summary. `--live` also prints the case ID,
condition, agent stages and verifier outcomes to stderr. Each success must pass
task-integrity checks and a second Kimina check with REPL reuse disabled.
`sorry`, an informal PASS, and a model's success claim never count as a proof.

`success_rate_selected` is verified unique theorems divided by **all selected
theorems**, including unattempted cases during a partial run. Read it alongside
`experiment_complete`, `attempted_problems`, `unattempted_problems`,
`not_verified_attempted_problems`, and `success_rate_attempted`. Per-world results
use the same selected denominator. Multiple trajectories are reported as an
observed solve-within-k rate, not an unbiased statistical pass@k estimator.
Tokens, calls, compiler failures, unknown identifiers, retrieval/LSP checks,
specialist usage and timing are recorded separately.

## Validation

All **88/88 statement probes** compiled with local Lean 4.26 and passed Kimina
statement validation. This verifies environment compatibility, not solving
accuracy. The offline suite passes (351 passed, 16 skipped), including
20 logic-benchmark tests covering extraction/no-solution-leakage,
hash/path checks, world counts, deterministic selection, labelled V5 conditions,
independent audits and resume protection. See `intro-logic-results.md` for the
separately labelled local model smoke-test results.
