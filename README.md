# Open-Weight Multi-Agent Formal Math Reasoning System

A reproducible, fully local Lean 4 theorem-proving agent designed first for a
32 GB Apple Silicon Mac. The MVP runs Qwen3-8B through MLX-LM, proposes Lean
files, checks every candidate with a self-hosted Kimina Lean Server, and feeds
compiler diagnostics into a bounded repair loop. Lean is the final authority.

This repository implements V1 compiler guidance, V2 retrieval, V3 informal reasoning,
V4 discussion/context isolation, and V7 MiniF2F evaluation:
**Qwen3 → Kimina/Lean → Lean-LSP-MCP proof state → Qwen repair → Kimina/Lean**.
V1.1 adds a Lean-verified fallback portfolio, strategy-level duplicate rejection,
immutable benchmark artifacts, and a repeated-run stability gate.
V2 adds a fully local LeanExplore MCP client, bounded retrieved declarations in
Qwen prompts, retrieval provenance/metrics, and a paired retrieval OFF/ON suite.
V3 reuses the same loaded Qwen weights for a thinking-enabled informal generator
and fresh-context critical verifier. The specialist prover remains a later milestone.

## Architecture boundary

Agent code depends on `ModelBackend`, not MLX. `MLXBackend` implements
`generate`, `chat`, `load_model`, `unload_model`, and `health_check` using the
OpenAI-compatible localhost API. A future vLLM adapter can implement the same
interface without changing prompts, the repair loop, verification, or metrics.

Managed mode is the default. The backend launches one `mlx_lm.server` process
for the configured model and terminates it after an attempt. This makes model
unloading real and establishes the memory-routing mechanism needed before a
formal specialist is added. Qwen thinking is disabled through
`chat_template_kwargs.enable_thinking=false` for orchestration. V3 informal calls
enable thinking while reusing the same loaded model sequentially.

## Requirements

- Apple Silicon macOS
- Python 3.11+
- MLX-LM 0.31.3 (pinned by the `mlx` install extra)
- A local Kimina Lean Server backed by Lean 4 + Mathlib
- Lean-LSP-MCP 0.14.1 in its dedicated Python 3.13 environment
- LeanExplore 1.3.0 with local dataset `20260714_172516` (dedicated Python 3.13)

Create an environment and install the project:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[mlx]'
```

Kimina is intentionally a separate service. Follow the upstream self-hosted
setup, then expose its current API at `http://127.0.0.1:8000`. This client uses
`POST /api/check`; set `endpoint = "/verify"` for an older server.

On macOS, source Elan in every terminal before starting Kimina; `setup.sh`
cannot persist its `PATH` change into the parent shell:

```bash
./scripts/start-kimina.sh
```

The launcher binds Kimina to `127.0.0.1` by default so proof checking is not
exposed to other machines.

Install the pinned Lean-LSP-MCP service environment once:

```bash
./scripts/setup-lean-lsp-mcp.sh
```

The agent launches it on demand over local MCP stdio. No HTTP port or external
API is used. V1 calls only the local `lean_diagnostic_messages`, `lean_goal`, and
`lean_term_goal` tools; external search and hosted-model tools are not used.

Install the pinned LeanExplore runtime, index, and embedding weights once:

```bash
./scripts/setup-lean-explore.sh
```

Allow several GB of disk space for the index, dependencies, and embedding model.
Setup downloads public artifacts; subsequent retrieval runs offline over MCP
stdio with no search API or API key. See [V2 setup and experiment design](docs/v2.md).

## Run

Check service availability:

```bash
local-lean-agent --config config/local.toml doctor
```

`doctor` performs a real `#check Nat` Kimina REPL smoke test and an MCP/LSP smoke
test that must return an interactive Lean goal. A successful V1 setup reports
`kimina_repl`, `lean_lsp_mcp`, and `lean_lsp_goal` as true. With V2 enabled,
`lean_explore_mcp` and `lean_explore_search` must also be true. A stopped
`mlx_server` is normal in managed mode before solving.

Run the example and save the final candidate:

```bash
local-lean-agent --config config/local.toml solve examples/add_zero.lean \
  --output runs/add_zero.proved.lean
```

`solve` is candidate-first when the input already contains a concrete proof:
Kimina checks that proof before MLX-LM is loaded. If Lean accepts it, the run ends
with zero model calls. If Lean rejects it, the first Qwen request receives the
original candidate and Lean diagnostics and starts the repair loop. Inputs that
contain `sorry` or `admit` remain generation-first tasks.

After each distinct Kimina rejection, the same candidate is inspected through
Lean-LSP-MCP. Its LSP diagnostics and interactive goal state are placed in the
next fresh repair prompt. If LSP is configured as required and unavailable, the
attempt stops with `LEAN_LSP_UNAVAILABLE` instead of silently claiming full V1
behavior. Kimina remains the only component allowed to mark a proof verified.

After a rejected Qwen candidate, V1.1 tries a configurable, one-time portfolio
of general tactics (`rfl`, `simp`, `norm_num`, `omega`, `positivity`, and
`aesop`). Each candidate is a complete task-preserving Lean file and is checked
independently by Kimina. The fallback system cannot declare success itself and
does not run before Qwen, so seeded repair cases still exercise the model loop.

Terminal output contains only `success`, canonical `end_reason`, and model `rounds`.
Every complete attempt is written to `runs/results/<attempt-id>.json`, and the
most recent result is also available at `runs/results/latest.json`. Complete
event-level telemetry remains in JSONL. Add `--full-json` only when full attempt
output is explicitly needed in the terminal.

For a bounded negative test, override the model-round budget without editing the
shared configuration:

```bash
local-lean-agent --config config/local.toml solve examples/my_example.lean \
  --max-rounds 3
```

The compact result reports `success`, canonical `end_reason` (for example,
`MAX_ROUNDS`), and the number of model `rounds`. Detailed Lean failure categories
and the legacy internal `stop_reason` remain in the saved full result.

Check an existing file without loading Qwen:

```bash
local-lean-agent --config config/local.toml check runs/add_zero.proved.lean
```

The first managed run downloads the configured 4-bit Qwen model if it is not
already cached. No paid API or API key is used.

## V1 acceptance suite

The controlled manifest at `benchmarks/v1/manifest.toml` contains 20 cases and
now exercises the complete LSP-guided loop:

- 6 incomplete-theorem generation cases;
- 9 deliberately incorrect seeded proofs that require compiler-guided repair;
- 2 already-valid proofs that must use zero model calls;
- 3 false theorems that must reach `MAX_ROUNDS` without a false positive.

Run the complete local suite with one reusable MLX model process:

```bash
local-lean-agent --config config/local.toml v1-suite \
  benchmarks/v1/manifest.toml \
  --output runs/v1-suite-latest.json
```

The terminal prints only aggregate pass/fail counts. The output file stores every
candidate, Lean diagnostic, token count, timing, and case-level contract result.
Passing a repair case requires an observed rejected seed followed by a
Lean-verified model candidate, and every rejected candidate in a required-LSP
case must have available LSP feedback. Merely solving on the first generated
attempt does not satisfy a seeded-repair case. The Qwen benchmark is
probabilistic: infrastructure completion does not imply every model run will
solve all 20 cases within five rounds.

For a V1.1 stability gate, require several consecutive perfect runs:

```bash
local-lean-agent --config config/local.toml v1-suite \
  benchmarks/v1/manifest.toml \
  --repetitions 3 \
  --output runs/v1-stability-latest.json
```

Each repetition is preserved under `runs/history/`; the requested output is only
the latest alias. Artifacts include the run UUID and timestamp, agent and prompt
versions, model ID, platform, and SHA-256 fingerprints for configuration,
manifest, and agent source. A stability run succeeds only when every repetition
scores 20/20.

## Reproducibility and safety

- Main-agent context is capped at 12,288 tokens by validated configuration and
  a conservative preflight estimate.
- Each repair request contains only the task, latest candidate, latest Lean
  diagnostics/proof state, and a bounded four-attempt rejection summary rather
  than an indefinitely growing transcript.
- `sorry`, `admit`, `axiom`, and `constant` declarations are rejected before
  local Kimina verification.
- Every distinct candidate is independently checked by Kimina/Lean; an exact
  duplicate—or the same failed proof under harmless `by exact`/term wrappers—
  inherits the prior rejection and cannot become successful.
- Imports and all source preceding the target proof body are immutable, including
  the theorem declaration; hiding a changed theorem in a comment is rejected.
- JSONL telemetry records attempts, iterations, proofs, failures, model calls,
  token usage, context sizes, Kimina calls, fallback checks, load/unload timing,
  and sampled RSS. Every fallback candidate and verifier result is retained.
- Every LSP inspection stores its diagnostics, goal, source location, latency,
  availability, and exact tool-call count; `lean_lsp_calls` is no longer a
  placeholder counter.
- Retrieval logs queries, ranked declaration IDs/names, descriptions, source,
  dataset version, search latency, tool-call count, and sampled process RSS.
  Informal generator/verifier calls, tokens, contexts, outlines, critiques, and
  verdicts are logged separately. Only the formal-specialist counter remains
  reserved for a later milestone.

## V2 semantic retrieval benchmark

First validate the fixtures without loading Qwen:

```bash
local-lean-agent --config config/local.toml v2-validate
```

All eight repair seeds must be rejected by Lean and all 18 positive reference
proofs must compile. References and expected declaration labels are used only
for validation/scoring, never passed to Qwen or used as retrieval queries.

Run the 20-case paired experiment (40 theorem attempts):

```bash
local-lean-agent --config config/local.toml v2-ablation \
  --output runs/v2-ablation-latest.json
```

Both conditions use identical Qwen settings, LSP feedback, and case-specific
budgets. The V1.1 fallback portfolio is disabled in both to isolate retrieval.
The runner checkpoints the output file after each attempt, prints the summary
at completion, and preserves a final immutable artifact under `runs/history/`.
Use `--repetitions 3` for repeated paired runs. `experiment_complete` means the
experiment ran without infrastructure errors, not that retrieval improved scores.

The original `v1-suite` explicitly disables retrieval even when V2 is enabled
in the config; its existing V1.1 fallback setting is preserved. It is a separate
acceptance suite, not the control condition of this V2 experiment.
The runners validate the manifest's suite identifier and reject a V1/V2 mismatch
before loading Qwen.

Read [V2 methodology](docs/v2.md) and [measured V2 results](docs/v2-results.md)
for metric definitions, fixture validation, and limitations.

## V3 informal reasoning benchmark

Ordinary solves now use an adaptive V3 policy: after one rejected Qwen-generated
formal candidate, Qwen produces a mathematical outline in a fresh thinking-enabled
context and a second fresh request critically reviews it. Up to three refinement
rounds are allowed. The outline is advisory; only Lean/Kimina can mark a formal
proof successful.

Validate and run the paired V2-versus-V3 experiment with:

```bash
local-lean-agent --config config/local.toml v3-validate
local-lean-agent --config config/local.toml v3-ablation \
  benchmarks/v3/manifest.toml \
  --output runs/v3-ablation-latest.json
```

Both conditions keep V2 retrieval enabled and disable whole-proof fallback and
compiler-prefix tactic probes. The V3 condition adds informal reasoning and its
configured strategy-search/rewrite-prompt hooks; improvements cannot be attributed
to the informal text alone. See [V3 design and experiment methodology](docs/v3.md)
for isolation guarantees, metrics, interpretation, and the independent audit command.
The [V3 testing guide](docs/v3-tests.md) covers deterministic contracts, live
mathematical critique tests, real Lean integration, and generator-only ablation.
The [V3.1 hardening guide](docs/v3-hardening.md) documents strategy-derived
LeanExplore queries, rewrite-failure recovery, and the focused regression runner.

## V4 discussion and fresh proof contexts

V4 is enabled in `config/local.toml`. After two failed model candidates, it can
ask Qwen for a fresh mathematical discussion and use an isolated 8K formal proof
context within the existing round budget. Long repair prompts are compressed
without truncating the theorem or selected Lean goals/hypotheses.

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/my_v4.lean --v4 on --max-rounds 4 \
  --output runs/my_v4.proved.lean

.venv/bin/python scripts/test-v4-live.py \
  --output runs/v4-ablation-latest.json
```

Use `--v4 off` for a V3 solve. The V1/V2/V3 benchmark commands automatically
disable V4. See [V4 usage and architecture](docs/v4.md) and
[V4 comparison results](docs/v4-results.md). The first real-Qwen comparison
completed: both conditions solved 0/2 positive cases and correctly rejected the
negative case. V4 used more time and tokens, with all new roles activated.

Run the dependency-free unit tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## V7 MiniF2F evaluation

V7 can now evaluate the existing V1–V4 stack on a pinned MiniF2F Lean 4 dataset,
without waiting for the V5 specialist or V6 multi-model routing. It supports
deterministic subsets, paired conditions, checkpoints/resume, statement
compatibility preflight and independent Kimina proof audits.

```bash
.venv/bin/local-lean-agent --config config/local.toml minif2f-run \
  --split valid --limit 3 --variant v4 --max-rounds 3 \
  --output runs/minif2f-dev.json
```

See [V7 setup, evaluation protocol and commands](docs/v7-minif2f.md). Start with
the validation split; full benchmark scores and smoke tests are different claims.

## Next milestones

1. Address the measured V4 Lean-formalization and informal-output-budget failures,
   then repeat paired evaluation on development and held-out cases.
2. Add the 4-bit DeepSeek-Prover specialist (V5).
3. Harden exclusive model routing and resource accounting (V6).
4. Run larger, frozen MiniF2F ablations using the V7 evaluation runner.

Upstream interfaces used by this MVP:

- [MLX-LM HTTP server](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md)
- [Kimina Lean Server](https://github.com/project-numina/kimina-lean-server)
- [Lean-LSP-MCP](https://github.com/project-numina/lean-lsp-mcp)
- [LeanExplore](https://github.com/justincasher/lean-explore)
