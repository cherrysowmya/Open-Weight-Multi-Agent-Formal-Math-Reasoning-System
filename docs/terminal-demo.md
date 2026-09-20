# Terminal demonstration

Use `solve --live` to display actual role/tool transitions as they happen. This
does not simulate agents or expose their private thinking text. Qwen roles reuse
one model sequentially with isolated contexts; they are not several models
running in parallel. The terminal display updates alongside the running solve.

## 1. Prepare before the presentation

Run these commands from the project root using the installed local environment.
Keep the Mac connected to power and close memory-heavy applications. Cold model,
retrieval and Lean startup can take noticeably longer than subsequent operations.
Rehearse before presenting; model answers and latency are not guaranteed.

In terminal A, start Kimina if it is not already running:

```bash
./scripts/start-kimina.sh
```

Keep that terminal running. In terminal B:

```bash
.venv/bin/local-lean-agent --config config/local.toml doctor
```

Kimina and the configured LSP/retrieval checks should be healthy. In managed MLX
mode, `mlx_server: false` before a solve is normal: the solve starts the model
server on demand. Do not launch a second MLX server on the same port.

## 2. Main demonstration: repair an incorrect proof

Open `examples/demo_repair.lean` in your editor (TextEdit: `open -e examples/demo_repair.lean`).
Its contents are editable input, not a hard-coded response:

```lean
import Mathlib

theorem demo_repair (x : ℝ) : 0 ≤ x ^ 2 := by
  linarith
```

Say: “The statement is true, but I have deliberately supplied a bad tactic.
The system must first ask Lean whether that proof works. If rejected, compiler
feedback goes back to the model. Only a compiler-accepted proof is successful.”

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/demo_repair.lean --live --proof-format proof_body \
  --max-rounds 3 --informal-policy off --v4 off
```

This focused demonstration shows the input check, LSP diagnostics, retrieval,
Qwen formal repair, and final Lean verification. A possible repaired body is
`by positivity`, but the model is not forced to output it. If a fallback tactic
solves the theorem, the display labels it as automation, not a Qwen success.
Your input file is unchanged. On success, a separate verified proof is saved.

## 3. Optional multi-role demonstration

Open `examples/demo_reasoning.lean`:

```lean
import Mathlib

theorem demo_reasoning (a b : ℝ) (ha : 0 ≤ a) (hb : 0 ≤ b) :
    0 ≤ a + b := by
  sorry
```

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/demo_reasoning.lean --live --proof-format proof_body \
  --informal-policy always --v4 on --max-rounds 3
```

`always` requests the informal reasoning stage before formal generation for this
unsolved input. Explain the roles as they appear:

- **Informal Generator:** proposes a mathematical argument in a fresh context.
- **Informal Verifier:** critiques that argument in a separate context.
- **Main agent:** converts useful advice into a Lean candidate.
- **Kimina / Lean:** decides whether the complete candidate verifies.

The verifier may be skipped if the generator returns malformed or token-truncated
output. That failure is shown honestly and recorded; no PASS is fabricated.
Discussion/fresh formal contexts appear only when the configured failure trigger
is reached. A simple successful theorem should not invoke every role. Do not
describe unused roles as active or informal acceptance as formal verification.
Use `--informal-policy after_model_failures` for the ordinary adaptive policy.

## 4. Optional negative control

`examples/demo_false.lean` asserts `n + 1 = n` for natural numbers. Run:

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/demo_false.lean --live --proof-format proof_body \
  --max-rounds 1 --informal-policy off --v4 off
```

The expected result is failure, never a successful `sorry`. Exhausting the budget
does not prove falsity; this particular statement is known to be false in advance.
Tactic-portfolio checks have separate costs and can add time beyond the model-round
budget. Do not use a hard negative as the opening time-critical demonstration.

## 5. What appears in the terminal

The following is an illustrative sequence, not a promised model result:

```text
[    0.0s] Orchestrator | Starting theorem attempt; Lean is the final authority.
[    0.0s] Kimina / Lean | Checking input candidate.
... REJECTED ...
... Lean-LSP-MCP | Inspecting compiler diagnostics and current goals.
... LeanExplore | Searching local declarations.
... MLX | Loading ...
... Main agent (Qwen) | Round 1: generating/repairing a Lean proof ...
... Kimina / Lean | Checking model candidate.
... Kimina / Lean | ACCEPTED.
... Orchestrator | Finished: SUCCESS (verified).
```

The final JSON includes success/failure, stop reason, formal rounds, wall time,
role call counts, total input/output tokens, retrieval/LSP/Kimina counts,
portfolio attribution, load/unload time, and absolute artifact paths.
Fresh formal contexts are a subset of formal calls, not extra model calls to add
again. Token counts use the usage reported by the backend. Detailed memory samples,
candidate code, diagnoses, model output and per-round metrics remain in the full
result and event trace. The displayed elapsed time is not a progress estimate.

## 6. Locate the full trace and proof

Every `--live` run creates a fresh directory under `runs/demo/`:

```text
runs/demo/<timestamp>-<unique-id>/
├── events.jsonl          Timestamped stages and detailed payloads
├── <attempt-id>.json     Full final result and metrics
├── latest.json          Same final result for convenience
└── verified.lean        Created only if this run succeeds
```

The final `trace_file`, `full_result_file`, and `verified_proof_file` fields give
absolute paths you can open directly in your editor. `verified_proof_file` is
null on failure. Memory and timings refer to the recorded attempt, not an external
profiler. Traces can contain the theorem and model responses; review before sharing.

Optional overrides:

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/demo_repair.lean --live --proof-format proof_body \
  --trace runs/my-presentation.events.jsonl \
  --output runs/my-presentation.proved.lean
```

`--trace` appends, so repeated uses of a custom path can contain multiple attempt
IDs. Default demo paths avoid that mixture. A failed run does not overwrite an
older explicit `--output` file; trust the current result and its proof path.
Live stage messages go to stderr, while final JSON goes to stdout, so you may
redirect stdout to a JSON file without losing the live terminal display.
Without `--live`, the existing compact terminal output stays unchanged.

## Rehearsal record (2026-09-20)

The repair command above was exercised with the real local stack. Lean rejected
the seeded `linarith`, Qwen returned `by positivity`, and Kimina accepted the
assembled proof on the first formal model call. No portfolio tactics were used.
The attempt took 64.03 seconds including cold startup, used 1,628 reported prompt
tokens and 10 generated tokens, and made two Kimina checks. This is a rehearsal
observation, not a guaranteed presentation runtime or success rate.

The local artifacts are in `runs/demo/20260920-173847-dd68b879/`:
`events.jsonl`, `fbe01d162047428fbcc2ec47d8700700.json`, and `verified.lean`.
These are generated local files, not part of the public repository.

The optional multi-role command also completed successfully in 138.82 seconds.
It made one informal generator call, one fresh-context informal verifier call,
and one formal model call. The informal verifier accepted the outline, but Lean
rejected the model's formal candidate. The bounded portfolio then tried five
tactics, with `positivity` producing the verified proof. Present this as a
**system success via Lean automation**, not a successful Qwen formal proof or
evidence that informal acceptance guarantees correctness. There were six Kimina
checks, 3,906 reported prompt tokens, and 1,445 generated tokens across roles.

Its artifacts are in `runs/demo/20260920-174009-488a7d21/`:
`events.jsonl`, `af15ae82ed8c4b4e92c2559820e9efe9.json`, and `verified.lean`.
Neither rehearsal needed the discussion partner or fresh formal subproblem role;
their zero call counts are intentional. The negative-control command is supplied
for presentation use but was not part of these two live rehearsals.

Software validation: 310 tests discovered, 295 passed and 15 opt-in tests skipped.
The six new local progress tests cover event timing, artifact paths, role labels,
success/failure summaries, preservation of input, and observer error isolation.
