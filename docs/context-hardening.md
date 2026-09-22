# Pre-V5 context hardening

Implemented 2026-09-21. This changes prompt evidence, not the inference backend,
model weights, proof authority, or candidate budget. No tactic portfolio is used.
The prompt version is `v4.1.0-context-evidence`; saved historical runs are unchanged.

## Changes

1. **Whole-declaration packing.** Retrieved evidence is a JSON object containing
   a `declarations` array. Each entry retains its exact name, declaration ID,
   available full Lean source, and data version. Optional descriptions are
   omitted first when an entry will not fit. An oversized entry is skipped so
   smaller later entries can still fit. No source or signature is sliced.
   The same packer is used when V4 compresses a packet and when the informal
   generator builds its bounded input. Oversized legacy/unstructured evidence
   is omitted rather than cut mid-declaration. A missing source stays null;
   the system does not fabricate a signature.

2. **Explicit rejection memory.** Main repair, rewrite recovery, informal
   reasoning, discussion, and fresh formal contexts receive recent rejected
   proof bodies paired with their compiler errors. These are labelled negative
   evidence, not mathematical facts. Comments and conversation history are not
   copied into failure memory. Long candidate/error fields may be excerpted,
   with an explicit `excerpted` flag. Recent failures take priority; V4 drops
   older pairs as complete records rather than severing a candidate from its
   error. The immutable task and goal remain unchanged.

3. **Conservative relevance filtering.** A small deterministic topic-overlap
   heuristic compares each hit with its original task or strategy query before
   merging/deduplicating results. It recognizes a few aliases and Lean symbols
   (such as square/sq/`^ 2`, nonnegative/nonneg, append/`++`). Imports, theorem
   names, comments, cursor-line tactics, single-letter variables, and generic
   words such as “real” are not relevance cues. Zero-overlap hits are omitted
   when the query has usable topic cues. Ambiguous/tiny queries retain the
   retriever's results. There is no namespace blacklist or minimum hit quota;
   an empty declarations list is valid.

Compression drops retrieval and other advisory context before rejection memory
and diagnostics. Fully oversized tasks still fail the existing context guard;
this does not enlarge the 12K main / 8K isolated-context limits.

## What this does not establish

This is not a learned semantic reranker, a proof of relevance, or declaration
signature inspection. It can omit useful synonyms and retain mathematically
related but inapplicable lemmas. For example, `Real.sq_le` remains related to a
square query but does not directly prove square nonnegativity. Lean remains the
authority on whether a candidate uses it correctly. Specialist integration and
signature inspection are separate future changes.

Filtering occurs after retrieval: actual LeanExplore calls, latency, and raw
hits remain intact in metrics and saved results. It is not a claimed reduction
in retrieval cost. `retrieval_evidence_filtered` events log kept/omitted names
and the policy version. V4 and informal request records contain the actual
packed context. Benchmark prompt/code fingerprints distinguish this version
from the old baseline; use new output paths for comparisons.

## Validation

The local suite discovered **315 tests: 301 passed, 14 opt-in tests skipped**.
Nineteen new regression tests cover relevance/noise controls, empty selections,
symbol aliases, preservation of raw retrieval, whole-record packing under
small budgets, rejection/error pairing, comment isolation, and both V4 and
informal packet compression. Existing orchestration tests also verify that a
fresh formal request receives the rejected candidate and its error.

An **offline packet replay**, not another model run, used the saved failed
`demo_repair` attempt in `runs/demo/20260921-155602-2c591b17/`:

- 24 raw hits across the task and two strategy searches became 7 unique
  selected declarations. Array, chain-complex, polynomial-variable, and
  number-field noise was omitted.
- Repacking the fresh-context request retained 3 whole declaration records and
  rejected attempts 0, 1, and 2 with their errors.
- Conservative estimated input plus reserved output was 7,755, within 8,192.

This confirms packet behavior, **not improved theorem-solving accuracy**. No
new live Qwen/Kimina success rate is claimed. Local development tests remain
ignored by Git, consistent with the runtime-only public repository policy.

## Running the demo

The command is unchanged; run it from the project root:

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/demo_repair.lean --live --proof-format proof_body \
  --informal-policy always --v4 on --max-rounds 3
```

It creates a new run directory and prints the trace and full-result paths. Keep
both successes and failures when comparing against the saved baseline; do not
replace historical artifacts or treat a single success as an accuracy result.
