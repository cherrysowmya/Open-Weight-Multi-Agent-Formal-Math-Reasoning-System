# V7 — Local MiniF2F evaluation

> Development-tool commands in this guide require files excluded from the current
> public checkout. See [tool availability and historical recovery](development-tools.md).

V7 evaluates the existing V1–V4 agent on a pinned external benchmark. It does
not add the V5 specialist or claim V6 model-routing hardening is finished.
Inference remains Qwen3-8B through MLX-LM; no paid API or NVIDIA dependency.
See [implementation validation and smoke results](v7-results.md) for the checks
actually run; no full MiniF2F model score is claimed.

## Quick start

From the project root, with Kimina running (`./scripts/start-kimina.sh`):

```bash
# Already downloaded in this workspace; verifies local hashes on subsequent calls.
.venv/bin/local-lean-agent --config config/local.toml minif2f-prepare

# Check statement elaboration, NOT proof correctness; does not load Qwen.
.venv/bin/local-lean-agent --config config/local.toml minif2f-validate \
  --split valid --limit 0 --output runs/minif2f-valid-statements.json

# Deterministic three-problem development subset, current V4 stack.
.venv/bin/local-lean-agent --config config/local.toml minif2f-run \
  --split valid --limit 3 --seed 0 --variant v4 --max-rounds 3 \
  --output runs/minif2f-dev.json
```

`--limit 0` selects all 244 problems in a split. `--case ID` overrides the limit;
repeat it to select several IDs in explicit order. For a small integration smoke:

```bash
.venv/bin/local-lean-agent --config config/local.toml minif2f-run \
  --case mathd_algebra_10 --variant lean --variant v4 --max-rounds 2 \
  --output runs/minif2f-my-smoke.json
```

This explicitly chosen easy problem is a wiring test, not a representative score.
Use `solve your_example.lean` for arbitrary user files; MiniF2F commands only
accept IDs belonging to the pinned split.

## Conditions and budgets

| Variant | Enabled components |
| --- | --- |
| `lean` | Qwen + Kimina + compiler-guided Lean-LSP repair (V1, not V0) |
| `v2` | `lean` + local LeanExplore |
| `v3` | `v2` + isolated informal generator/verifier |
| `v4` | `v3` + discussion, fresh proof contexts, summarization |

Pass multiple `--variant` flags for paired evaluation of the same selected
statements. Execution is sequential, with condition order reversed on alternating
problems/repetitions. There is one shared MLX backend, retained between attempts
and unloaded on session exit; prompts/agent state are fresh for each solve.
This is the existing single-model baseline, not multi-model routing.

`--max-rounds` limits completed formal-generation rounds per solve. Optional
informal and discussion calls have their own limits in `config/local.toml`, so
this is **not** a total-model-call cap. Whole-proof tactic fallback and prefix
salvage are disabled in every benchmark condition to avoid hidden extra searches.
Other feature-specific hardening follows the recorded effective configuration.
Main context is at most 12,288 tokens; isolated roles at most 8,192, per config.

`--attempts K` runs K independent solve episodes per theorem and condition.
`--seed` controls subset selection only, not model sampling. The default main
model temperature is zero; repeated attempts are not guaranteed independent or
different. `observed_solve_within_k_rate` is an empirical union of successes,
**not** an unbiased statistical pass@k estimator.

`--max-seconds` defaults to 1800: a **soft session budget**, checked between
theorem attempts. An in-flight solve can exceed it because each role/service has
its own timeout. Ctrl-C checkpoints completed attempts and closes managed
services; an interrupted in-flight solve is retried on resume.

## Checkpoints and outputs

Add `--resume` to the **same command** to continue. The session-time budget may
change. Dataset, code, configuration, environment, selected IDs, variants,
rounds, and repetition count must otherwise match. An existing output is never
silently overwritten by a new run. Concurrent writers to the same output are
rejected. Completed episodes are skipped, including recorded infrastructure
failures; use a new output for a clean rerun after fixing infrastructure.

Each completed attempt is atomically checkpointed. Files next to the output:

- `NAME.json`: selection, provenance, effective configs, preflight results,
  full attempt traces, independent audits, metrics, and session stop reasons.
- `NAME.events.jsonl`: existing detailed agent events. Interrupted unfinished
  events may remain; episode summaries use checkpointed attempts only.
- `NAME.proofs/`: only independently verified, statement-preserving Lean files.
- `history/`: timestamped completed-run snapshot.
- `NAME.json.lock`: advisory file lock; the file can remain after exit.

Terminal output is a summary; prompts, diagnostics, candidates, and role traces
remain in files. `experiment_complete` means every scheduled slot was evaluated,
not that every theorem was proved. Exit 0 means experiment completion, even if
proofs failed. Partial runs return 1; invalid arguments/unavailable startup
services return 2.

Only successful independent Kimina rechecks of an unchanged theorem count as
`verified`. The audit uses `reuse_repl=false`; `sorry`, `admit`, axioms and task
mutation cannot create a benchmark success. Informal acceptance is never proof.

The denominator of `success_rate_selected` includes every selected problem,
including incompatible statements and infrastructure failures. On a partial run
it is a conservative progress figure, not a completed benchmark score. The
summary separately counts incompatible statements, failed proofs, infrastructure
errors and failed audits. Preflight outages stop the session and are retryable;
they do not become mathematical failures. Never silently filter out hard cases.

`first_attempt_verified` refers to the first full solve episode;
`first_candidate_verified` refers to the first generated Lean candidate within
an audited successful episode. Summaries also include failed/unknown-name
candidates, tokens, model calls, retrieval/LSP/verification calls and load time.
Per-attempt context sizes and memory samples remain in the full trace. Memory
samples are not a continuous measurement of peak unified/GPU memory.
Aggregate attempt wall time excludes dataset preflight, final audit and cleanup;
`sessions[].wall_clock_seconds` includes those session costs.

## Dataset provenance and compatibility

Source: [yangky11/miniF2F-lean4](https://github.com/yangky11/miniF2F-lean4),
revision `5746b7d6c47855ce1294bed87329618ff7f1bc31`, with 244 `valid` and 244
`test` statements. This is a Lean 4 community port of
[MiniF2F](https://github.com/openai/miniF2F), not a claim to reproduce the
original paper's toolchain or a different corrected benchmark variant.

The local manifest records archive, original-source and normalized-source SHA256
hashes. Upstream MIT license and toolchain/Mathlib metadata are preserved. Only
standalone unproved statements are loaded; no upstream solution modules are
imported. Normalization changes `maxHeartbeats 0` to `200000` and whitespace,
not mathematical statements. Each run verifies source hashes.

Upstream uses Lean 4.24.0; this workspace uses Lean 4.26.0. Preflight transforms
each theorem into a Prop-valued definition to check elaboration without assuming
or proving it. Preserve this adapted-environment label when reporting results.
The run captures the configured local Mathlib checkout/toolchain, package
versions, source/config fingerprints and retrieval data version. If you point
Kimina at another installation, ensure its Mathlib/toolchain matches Lean-LSP;
the local filesystem metadata cannot attest a different remote server.
Model ID is recorded, but upstream weight changes are not prevented by that ID;
freeze/cache a model revision for publication-quality replication. Open-weight
training contamination of MiniF2F cannot be ruled out.

## Evaluation protocol

1. Develop and tune on `valid`; keep `test` explicitly opt-in and held out.
2. Freeze model weights, toolchain, retrieval index, prompts, budgets and config.
3. Run paired conditions with identical selected IDs and report success plus cost.
4. Then run `--split test --limit 0` with the frozen settings. A full split on this
   Mac can take many sessions; repeat the command with `--resume` as needed.
5. Report denominator, failures, variant, split, dataset revision, environment
   adaptation, attempts and all budgets. Do not compare smoke accuracy with
   published full-test scores.

The minimum implementation is the reproducible local runner and audit pipeline.
Good next engineering improvements are stronger weight/toolchain locking,
hard per-theorem deadlines, and continuous memory measurement. Potential research
work is a frozen, held-out cost/accuracy ablation of retrieval, informal critique
and context isolation; merely adding the benchmark is not a research result.

Offline regression tests:

```bash
.venv/bin/python -m unittest tests.test_minif2f -v
.venv/bin/python -m unittest discover -s tests -q
```

Opt-in real-Kimina contract tests (no model inference):

```bash
RUN_MINIF2F_LEAN_TESTS=1 .venv/bin/python -m unittest tests.test_minif2f -v
```
