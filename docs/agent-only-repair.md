# Agent-only proof repair

Automatic tactic portfolios and compiler-prefix salvage trials have been removed.
This is a removal, not a feature flag: there is no solver path that substitutes a
Python-selected list of tactics or appends trial tactics to a failed prefix.

The active loop is:

1. Check an existing concrete input proof, if supplied.
2. Retrieve relevant declarations and inspect Lean feedback as configured.
3. Ask Qwen to generate or revise a candidate, using the theorem, local goals,
   previous diagnostics, retrieved evidence and optional informal guidance.
4. Assemble the immutable task and verify the candidate with Kimina/Lean.
5. On failure, return to agent-guided repair, discussion or fresh context within
   the configured budget. Stop when verified or the budget is exhausted.

Qwen may still choose `simp`, `positivity`, `nlinarith`, or any other appropriate
Lean tactic. Tactics themselves are not banned: the change removes automatic
Python-side enumeration. Prompt examples and compiler-specific repair advice
remain guidance to the model, not executable candidate-generating shortcuts.
This change does not guarantee that the model will choose a correct tactic or
improve accuracy. It makes successful generated proofs attributable to the agent.

## Configuration and commands

The checked-in config is updated. Remove these keys from any custom configs:

- `[agent]`: `fallback_enabled`, `fallback_tactics`.
- `[informal_reasoning]`: `rewrite_salvage_enabled`, `rewrite_salvage_tactics`,
  `rewrite_salvage_max_checks`.

Old keys now cause an explicit migration error, even if set to false. This avoids
silently accepting settings for functionality that no longer exists.
MiniF2F variants are `lean`, `v2`, `v3`, and `v4` only. `portfolio` and
`v4_portfolio` are rejected. The retained local hardening runner no longer has
`--no-salvage`; every condition is model-only for candidate generation.

Normal demo commands are unchanged:

```bash
.venv/bin/local-lean-agent --config config/local.toml solve \
  examples/demo_reasoning.lean --live --proof-format proof_body \
  --informal-policy always --v4 on --max-rounds 3
```

After an unsuccessful model candidate you should see another agent request,
or budget exhaustion, not a tactic-portfolio stage. A failed demo is reported
honestly; no success is fabricated to replace the removed fallback.

## Traces, experiments and provenance

New results omit fallback/portfolio/salvage metrics and synthetic fallback
iteration records. Kimina checks still include concrete input checks and distinct
model candidates; exact repeated failures can reuse the prior rejection. Benchmark
statement preflights and independent final proof audits remain, as do fixture
reference-proof checks. None of those generate a proof by enumerating tactics.

Historical JSON traces and reports are preserved unchanged. Their portfolio-
assisted successes describe an older system and must not be credited to the
current agent-only solver. In particular, the former five-problem comparison's
2/5 assisted score is not a score for this version. Code/config fingerprinting
prevents mixing old checkpoints with the new implementation; use a new output
path for new benchmarks. Old research documents mentioning the retired methods
are marked as historical.

## Validation (2026-09-21)

The local regression suite discovered 296 tests: 282 passed and 14 opt-in tests
were skipped. Seven new removal tests cover the absence of hidden checks,
diagnostic-driven model repair, model-selected tactics remaining legal, input
checks, the new metrics schema, retired config rejection, and removed CLI variants.
Retired feature tests were removed; the smaller count is not a skipped-failure claim.

A real local run of `examples/demo_repair.lean` succeeded in 29.17 seconds:
Lean rejected the supplied `linarith`; Qwen generated `by positivity`; Lean
accepted it. There was one formal model call and exactly two Kimina checks
(the input and the generated candidate). The saved event trace was inspected
and contains no portfolio, fallback, or prefix-salvage events.

Local artifacts: `runs/demo/20260921-154629-dde715ff/events.jsonl`,
`ed80d2cb825342e1a75d923d5873a43e.json` in that directory, and `verified.lean`.
This is a repair smoke test, not a new MiniF2F accuracy measurement. Historical
model-plus-portfolio benchmark scores remain unchanged and do not describe this version.
