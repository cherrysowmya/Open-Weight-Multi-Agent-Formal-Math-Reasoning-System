# V2 paired benchmark — 2026-09-02

> Historical methodology notice: automatic tactic portfolios and prefix-salvage
> trials have been removed. Related commands, settings and assisted scores below
> describe older versions, not the current solver. See [agent-only repair](agent-only-repair.md).

## Result

In this one 20-case paired run, local LeanExplore improved positive-task success
from **6/18 to 18/18**, reduced unknown-name failure rounds from **31 to 5**, and
reduced rejected model-candidate rounds from **58 to 17**. Both negative cases
were rejected through exactly three rounds in both conditions.

This is a small integration/diagnostic result, not a claim of general theorem-proving
performance or a statistically established improvement. Both arms disabled the
V1.1 fallback portfolio and used the V2 prompt template. The OFF score must not be
compared directly with the historical V1.1 20/20 acceptance result.

| Metric | Retrieval OFF | Retrieval ON |
| --- | ---: | ---: |
| Verified positive tasks | 6/18 | 18/18 |
| Generation tasks verified | 1/10 | 10/10 |
| Seeded repairs verified | 5/8 | 8/8 |
| Negative safety cases passed | 2/2 | 2/2 |
| Correct outcomes, including negatives | 8/20 | 20/20 |
| Rejected generated candidate rounds, all cases | 58 | 17 |
| Rejected generated candidate rounds, positive cases only | 52 | 11 |
| Unknown-name failure rounds, all cases | 31 | 5 |
| Unknown-name failure rounds, positive cases only | 28 | 3 |
| Unknown-name failure rounds / model calls | 48.4% | 14.3% |
| Model calls | 64 | 35 |
| Reported input tokens, total | 57,669 | 48,686 |
| Reported output tokens, total | 4,549 | 1,760 |
| Maximum reported input tokens in a call | 1,581 | 2,422 |
| Cumulative attempt wall time, seconds | 830.2 | 500.2 |
| Retrieval queries / MCP tool calls | 0 / 0 | 25 / 150 |
| Retrieval time, seconds (included above) | 0 | 13.8 |
| Any-round annotated-name hit rate | N/A | 12/18 (66.7%) |

There were **12 paired retrieval wins, zero losses, and eight ties**. The complete
run took 1,332.2 seconds (22.2 minutes), excluding the earlier diagnostic run and
the post-run proof audit. Unknown-name failure rounds fell by 83.9%; rejected
candidate rounds fell by 70.7%. Counts are failed model rounds, not counts of
distinct fabricated declarations; repeated rejected proofs can contribute multiple
rounds. Deliberately broken input seeds are excluded.

## Case-by-case outcomes

Numbers in parentheses are model rounds. `MAX` means the positive task exhausted
four rounds without a verified proof. For negatives, `safe MAX` means the expected
three-round exhaustion with no accepted candidate.

| Case | OFF | ON |
| --- | --- | --- |
| `gen_list_append_nil` | MAX (4) | Verified (3) |
| `gen_list_nil_append` | MAX (4) | Verified (1) |
| `gen_list_reverse_reverse` | MAX (4) | Verified (1) |
| `gen_list_length_reverse` | MAX (4) | Verified (3) |
| `gen_list_length_append` | MAX (4) | Verified (1) |
| `gen_set_union_comm` | MAX (4) | Verified (3) |
| `gen_set_inter_comm` | MAX (4) | Verified (2) |
| `gen_nat_gcd_comm` | MAX (4) | Verified (1) |
| `gen_nat_lcm_comm` | MAX (4) | Verified (1) |
| `gen_finset_union_comm` | Verified (1) | Verified (1) |
| `repair_list_append_nil` | MAX (4) | Verified (1) |
| `repair_reverse_unknown` | Verified (1) | Verified (1) |
| `repair_length_append_unknown` | Verified (4) | Verified (3) |
| `repair_set_union_unknown` | Verified (1) | Verified (1) |
| `repair_nat_gcd_unknown` | Verified (2) | Verified (1) |
| `repair_abs_nonneg_unknown` | Verified (1) | Verified (3) |
| `repair_set_union_empty` | MAX (4) | Verified (1) |
| `repair_function_comp_id` | MAX (4) | Verified (1) |
| `negative_nat_successor` | safe MAX (3) | safe MAX (3) |
| `negative_union_inter` | safe MAX (3) | safe MAX (3) |

The outcome ties are not necessarily cost ties: retrieval made the absolute-value
repair slower (three rounds versus one), while shortening two other successful
repairs. Most of the success-rate gain came from generation: nine extra generation
successes versus three extra seeded-repair successes.

## Failure categories

These are the saved categories of rejected generated rounds, including the
negative cases. They are diagnostics, not a manually adjudicated mathematical
failure taxonomy.

| Category | OFF | ON |
| --- | ---: | ---: |
| Unknown identifier / constant | 31 | 5 |
| Tactic/type failure | 15 | 4 |
| Unsolved goals | 6 | 4 |
| Other/unclassified | 6 | 3 |
| Unsafe placeholder | 0 | 1 |

The unsafe-placeholder event was a generated `by sorry` for the false union/intersection
task. It was rejected, and the attempt ultimately ended with `MAX_ROUNDS`, not success.
Retrieval did not eliminate name errors or Lean application mistakes. The exact
annotated reference name appeared in only 12 positive cases, despite 18 successes;
an outcome gain alone does not prove the intended lemma was retrieved or used.

## Validation and independent audit

- **87 deterministic unit tests passed.**
- All **eight repair seeds** were rejected by the configured Lean environment.
- All **18 positive reference proofs** compiled. References were used only for
  preflight/scoring, never supplied as model answers.
- All **24 accepted final proofs** across the two conditions independently
  recompiled after the experiment and preserved the original tasks.
- All **12 rejected positive-task final candidates** preserved their tasks but
  still failed independent compilation; no false negative was found among them.
- All **four negative attempts** ended after exactly three model calls without
  success. Concrete counterexamples for both statements compiled separately.
- The experiment recorded zero infrastructure failures.

Preflight corrected two initially invalid fixture assumptions: the proposed
`List.reverse_involutive xs` and `Function.id_comp f` seeds actually compiled.
The final fixtures use verified non-existent names instead. Reference arguments
for `List.length_reverse` and `List.length_append` were also corrected to match
the installed Lean library's implicit arguments before benchmarking.

## Configuration and provenance

Complete-run UUID: `9e0a4a03a61a4f0a9e7a142be70530f3`.
Metadata timestamp: `2026-09-02T19:22:41.859607+00:00`.

- Agent `0.2.0`, prompt `v2.0.0`.
- `mlx-community/Qwen3-8B-4bit` through MLX-LM `0.31.3`; thinking OFF,
  temperature 0, 512 output-token cap, 12,288-token context guard.
- Required Lean-LSP-MCP in both arms; self-hosted Kimina is proof authority.
- Lean `4.26.0`; Mathlib commit `2df2f0150c275ad53cb3c90f7c98ec15a56a1a67`.
- LeanExplore `1.3.0`, dataset `20260714_172516`, offline local backend.
- Packages `Mathlib` and `Init`, top eight results, source for top five,
  reranker OFF. The 0.6B embedding model is local, not a second generation agent.
- Positive budgets: four model rounds; negative budgets: three.
- Counterbalanced condition order; one reusable MLX process; no fallback portfolio.

The first Mathlib-only run was stopped after an index coverage audit showed that
12 positive fixtures' reference declarations were excluded by the package filter.
Ten completed attempts from that run were preserved as an explicitly aborted
diagnostic artifact. They are not pooled into the reported results. All 40 attempts
in the complete run used the corrected scope; no settings were changed midway.

Artifacts:

- [Immutable complete paired trace](../runs/history/v2-ablation-latest-20260902T192241859607Z-paired-ablation-9e0a4a03.json)
- [Independent audit and installed dependency versions](../runs/v2-audit-latest.json)
- [Fixture validation](../runs/v2-validation-latest.json)
- [Excluded Mathlib-only partial run](../runs/history/v2-mathlib-only-partial-130cf2cc533f43b2965db1f23f97e2e9.json)
- [Case manifest and reference proofs](../benchmarks/v2/manifest.toml)

## Limits and next steps

This is one run of easy, related tasks with descriptive names, not held-out MiniF2F
or a publishable retrieval study. The control is the empty-retrieval V2 template,
not the historical V1 prompt; fallback tactics were disabled in both arms. A larger
neutral-named, deduplicated benchmark and repeated paired runs are needed before
generalizing. A BM25-only control would also be needed to separate the contribution
of neural semantic search from the full hybrid retrieval system.

The saved process RSS samples do not measure peak total unified memory. In
particular, the retrieval PID field samples the CLI launcher, not its child search
worker. No claim of complete 32 GB peak-memory certification follows from this run.
Shared persistent processes and warm caches also limit interpretation of timing.

The minimum viable V2 baseline and its empirical test are complete. Good engineering
follow-ups are package-coverage preflight checks, better query/rank analysis,
process-tree memory accounting, and a locked dependency/model-revision setup.
Potential research extensions should follow a larger repeated evaluation rather
than treating this 20-case result as evidence about difficult mathematics.

See [V2 methodology and commands](v2.md) to reproduce the run and audit.
