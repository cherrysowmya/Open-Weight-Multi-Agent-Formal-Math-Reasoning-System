# V5 tokenizer hardening

## Confirmed failure

The original `runs/intro-logic-five-v5.json` completed with 2/5 verified theorems,
both solved by Qwen before specialist invocation. Each of three DeepSeek outputs
failed the exactly-one-complete-Lean-fence contract before Kimina. Responses
included echoed instructions, malformed theorem headers and repeated fences.
One exhausted its 2,048-token output allowance.

Read-only reproduction on the cached DeepSeek snapshot
`851514b69bf4bfbc326c55120d805e1e5146745e` established that its serialized
`tokenizer.json` was lossless with `tokenizers.Tokenizer.from_file`, whereas
Transformers 5.16.1 `AutoTokenizer.from_pretrained` selected a Llama tokenizer
path that dropped spaces, newlines and Unicode logic operators. For example:

```text
Input:   (P Q R : Prop) (h : P → Q → R) : P ∧ Q → R
Decoded: (PQR:Prop)(h:PQR):PQR
```

Direct loading with `PreTrainedTokenizerFast` also preserved the input under
that installation. This isolates a loader/library compatibility problem, not
proof that the cached vocabulary or weights are damaged. It does not establish
that every Transformers 5.x version or every Llama model is affected.

## Implemented fix

The `mlx` dependency extra now pins this tested stack:

| Package | Version |
| --- | --- |
| mlx-lm | 0.31.3 |
| transformers | 5.0.0 |
| tokenizers | 0.22.2 |
| huggingface-hub | 1.32.0 |

Transformers 4.57.6 was tested diagnostically and preserved the input, but was
not retained because it violates MLX-LM 0.31.3's Transformers >=5 dependency.
The selected 5.0.0 stack passes `pip check`. No Hugging Face cache assets, model
weights, installed-library source, or reference benchmark results were edited.

Before a managed MLX model process starts, the backend launches a short-lived,
CPU-only tokenizer preflight using the same AutoTokenizer entry point as MLX-LM.
It checks exact round trips for Lean code, indentation, newlines, Unicode logical
and mathematical symbols, then checks a system/user prompt through the native
chat template. Lossy encoding, a missing template, dropped prompt content or a
failed check prevents model startup. Check failures appear in the attempt's
runtime error; no model output can be treated as a verified proof.

No template replacement, broad output-parser relaxation or specialist prompt
change is included: the native template preserves the test prompt on the fixed
stack. Actual proof response formatting still needs a live smoke test. Managed
startup validation is not a certification of an independently launched external
server or of the model's mathematical ability. Load timing now includes preflight.

Benchmark environment metadata also records `tokenizers` alongside the existing
inference package versions so future comparisons expose this dependency change.

## Offline validation completed

- Cached Qwen3-8B and DeepSeek-Prover-V2-7B: exact Lean and native-chat-template
  round trips passed, with no weights loaded.
- Eight new deterministic tests cover correct text, whitespace/operator loss,
  missing/broken templates, dropped role content, preflight failure/timeout and
  guarded model startup.
- Full suite: **375 discovered, 359 passed, 16 opt-in tests skipped**.
- `pip check` and `git diff --check` passed.

Recheck cached tokenizers without loading either model:

```bash
HF_HUB_OFFLINE=1 .venv/bin/python -m local_lean_agent.backends.tokenizer_check \
  mlx-community/DeepSeek-Prover-V2-7B-4bit
HF_HUB_OFFLINE=1 .venv/bin/python -m local_lean_agent.backends.tokenizer_check \
  mlx-community/Qwen3-8B-4bit
```

## Deferred live validation

The real specialist test refused to start because an existing MLX server owned
port 8080. Process inspection identified the user's all-88 V4 benchmark, not an
orphan server. The user explicitly chose to keep that benchmark running. It was
not interrupted, and no second resident inference model was started.

After V4 exits and releases port 8080, with Kimina still running:

```bash
# Local development checkout: real DeepSeek generation plus authoritative Lean.
HF_HUB_OFFLINE=1 LOCAL_LEAN_AGENT_V5_LIVE_MLX=1 .venv/bin/python -m unittest \
  tests.test_v5.V5ModelSmoke -v

# Run only after the smoke test succeeds; preserve the original failed baseline.
bash scripts/start-logic-benchmark.sh \
  --limit 5 --seed 0 --variant v5 \
  --max-rounds 3 --max-seconds 3600 \
  --proof-format proof_body --live \
  --output runs/intro-logic-five-v5-tokenizer-fixed.json
```

Tests remain local/Git-ignored per the runtime-only repository policy. The
benchmark runner is shipped and can also exercise the live integration. If the
smoke test produces malformed or rejected proof output, inspect and address that
separately before the benchmark; tokenizer success alone is insufficient.

Do not resume the original V5 run under changed dependencies/code. The runner's
fingerprint checks intentionally reject that mixture. Likewise, a future resume
of the already-running V4 checkpoint may reject the modified environment. Keep
its artifacts unchanged and do not bypass those checks. A clean follow-up V4
control under the fixed stack is needed before attributing a V5 score change
solely to specialist capability.

**Status:** tokenizer repair and offline checks complete; real specialist proof
verification, post-fix five-problem accuracy and live memory validation pending.
