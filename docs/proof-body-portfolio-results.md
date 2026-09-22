# Five-problem proof-body / portfolio comparison

> Historical methodology notice: automatic tactic portfolios and prefix-salvage
> trials have been removed. Related commands, settings and assisted scores below
> describe older versions, not the current solver. See [agent-only repair](agent-only-repair.md).

Run started 2026-09-11; results inspected 2026-09-12.
Run ID: `9f4e1e03baab4ecdaf5111089ea911f6`.
All 15 scheduled attempts completed with no recorded infrastructure or cleanup
errors. The process unloaded the model on completion.

## Results

| Condition | Independently verified | Accuracy on selected five | Attempt time | All model calls | Generated tokens | Portfolio checks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Portfolio only | 2/5 | 40% | 3.27 s | 0 | 0 | 25 |
| V4 agent, proof bodies | 0/5 | 0% | 1,388.46 s | 28 | 17,330 | 0 |
| V4 agent + portfolio, proof bodies | 2/5 | 40% | 996.61 s | 18 | 12,073 | 25 |

Attempt time is the sum of recorded theorem-attempt times; it excludes separate
statement preflight and final audit checks. The entire session took 2,420.23 s
(40.34 minutes), including shared service overhead. Model calls and tokens
include formal, informal, and discussion roles. Portfolio-only time is the cost
observed with the local services available, not cold-start deployment latency.

| MiniF2F validation problem | Portfolio | V4 | V4 + portfolio |
| --- | --- | --- | --- |
| `algebra_sqineq_4bap1lt4bsqpap1sq` | Failed | Failed | Failed |
| `mathd_numbertheory_284` | Verified: `omega` | Failed | Verified: `omega` |
| `numbertheory_aneqprodakp4_anmsqrtanp1eq2` | Failed | Failed | Failed |
| `mathd_algebra_536` | Verified: `norm_num` | Failed | Verified: `norm_num` |
| `mathd_numbertheory_303` | Failed | Failed | Failed |

Both combined-condition successes came from the portfolio, not Qwen's candidate
proofs. All four successful attempt records passed independent Kimina rechecks
and the original-statement integrity check. Portfolio checking consumed 3.26 s
standalone and 3.76 s in the combined condition. These are legitimate automation
successes, explicitly attributed; this sample shows **no additional solves from
the model** beyond the portfolio.

## Fixed setup and interpretation

Selection: MiniF2F `valid`, limit 5, seed 0; one attempt per condition per theorem.
Formal agent budget: three rounds; Qwen3-8B 4-bit via local MLX, main thinking
disabled. Both model conditions used `proof_body` output and the existing
`legacy` 512-token formal output policy. The prior adaptive-output change was
not enabled. V4 retrieval, informal reasoning, discussion and isolated contexts
were active; prefix salvage was disabled in all benchmark conditions.
Portfolio tactics were unchanged: `rfl`, `simp`, `norm_num`, `omega`,
`positivity`, `aesop`. Each was tried at most once per theorem attempt, with
the configured timeout and original MiniF2F heartbeat bound.

The agent-only historical whole-file result was also 0/5, but it was not rerun
as a contemporary whole-file control. This trial therefore does **not** isolate
the causal effect of proof-body generation or demonstrate a token-efficiency
gain from that format. The portfolio conditions also have extra compiler
checks, so this is not an equal-compute comparison. Five development problems
are insufficient for a general MiniF2F performance claim.

## Failure observations

The file assembler preserved the original task, but Qwen did not consistently
follow the new body-only contract. Seven of the 26 formal model responses across
both model conditions returned declaration/file material and were rejected by
the format/integrity guard (`task_mutation` is the current category). Other
responses included the redundant `:=` delimiter, causing a Lean parser error
after assembly. These are interface-following failures, not mathematical
counterexamples. The raw outputs and diagnostics remain in the saved trace.

Further failures included unfinished/repetitive output, tactic syntax in term
positions, unsupported rewrite steps, and missing local hypotheses. Truncation
was reported for 8/15 agent-only responses and 7/11 combined-condition responses.
Body-only output is implemented and software-tested, but this trial does not
show it working reliably enough with Qwen to improve formal success.

Possible next experiments, not changes made to this completed run: improve the
body-only prompt with compact examples; test a narrowly specified delimiter
normalizer while retaining immutable assembly; compare the formats on identical
budgets; and test portfolio-first scheduling to avoid model cost on automation-
solvable tasks. Do not silently reinterpret the existing failed responses as new
successes or replace the saved baseline.

## Reproducibility and validation

Command and feature documentation: [proof-body / portfolio guide](proof-body-portfolio.md).
Local artifacts (ignored by git):

- `runs/minif2f-body-portfolio-five.json`
- `runs/history/minif2f-body-portfolio-five-20260911T184327777922Z-minif2f-valid-9f4e1e03.json`
- `runs/minif2f-body-portfolio-five.events.jsonl`
- `runs/minif2f-body-portfolio-five.proofs/`

The stored package code fingerprint matched the implementation when inspected.
The report was written after completion without changing experiment code or
results. All 297 automated tests were discovered: 282 passed, 15 opt-in tests
skipped. Separately, all 17 proof-body/portfolio tests passed with real Kimina
enabled, including a verified assembled proof and a false-theorem rejection.
