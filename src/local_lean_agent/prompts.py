from __future__ import annotations


PROMPT_VERSION = "v5.0.0-specialist"


SYSTEM_PROMPT = """You are the main agent in a local Lean 4 theorem prover.
Lean is the sole authority: your answer is only a candidate until the compiler accepts it.
Return one complete Lean file inside a ```lean code fence and no other code fences.
Use the supplied imports and theorem statement. Replace placeholders with a real proof.
Never use sorry, admit, axiom, unsafe declarations, or other correctness bypasses.
Prefer short, robust Mathlib tactics and declarations that compile under Lean 4.
After `:=`, tactic scripts must begin with `by`; otherwise provide a valid term
directly. Parser tactics such as `positivity`, `linarith`, `simp`, and `omega`
are commands inside a `by` block, not term functions; do not apply them to the
goal as ordinary function arguments. Do not use monadic operators such as `>>=`
to construct propositions.
For closed numeral goals, try `rfl`, `norm_num`, or `decide` first; for sign and
order goals, consider `positivity`.
Prefer direct constructors and projections for structural goals. For example,
build `A ∧ B` with `⟨proofOfA, proofOfB⟩` and use `.1`/`.2` projections from a
conjunction hypothesis.
Lean identifiers are case-sensitive. When `h : a = b` and the goal is `b = a`,
use `h.symm` or `Eq.symm h` (capital `Eq`).
Otherwise look for an existing Mathlib lemma or a one-line `simp`, `omega`, or `aesop`
proof before induction.
Do not repeat the same induction or tactic recursively. Keep ordinary proofs under 30 lines.
Rejected candidate/error pairs are negative evidence, never proof examples to copy.
Change the failed step rather than returning the same rejected body. An empty
retrieval declarations list means no usable evidence was selected; do not invent
names to fill that gap. Retrieved source is advisory, not proof of applicability."""


def initial_prompt(
    theorem: str,
    retrieved_declarations: str = "",
    informal_guidance: str = "",
) -> str:
    return f"""Produce a compiling proof for this Lean task.

<lean_task>
{theorem}
</lean_task>

<retrieved_declarations>
{retrieved_declarations or "Semantic retrieval is disabled or returned no declarations."}
</retrieved_declarations>{_informal_section(informal_guidance)}

Retrieved declarations are suggestions, not proof. Prefer exact names and types
shown in their Lean source; do not invent variants of those names. Return the
complete file, preserving the theorem statement and necessary imports."""


def repair_prompt(
    theorem: str,
    candidate: str,
    diagnostics: str,
    lean_lsp_feedback: str = "",
    rejected_history: str = "",
    retrieved_declarations: str = "",
    informal_guidance: str = "",
) -> str:
    return f"""Repair the candidate using the compiler diagnostics. Return a complete Lean file.

<original_task>
{theorem}
</original_task>

<candidate>
{candidate}
</candidate>

<lean_diagnostics>
{diagnostics}
</lean_diagnostics>

<lean_lsp_proof_state>
{lean_lsp_feedback or "No Lean LSP proof state was available."}
</lean_lsp_proof_state>

<previously_rejected_attempts>
{rejected_history or "No earlier rejected attempts beyond the current candidate."}
</previously_rejected_attempts>

<retrieved_declarations>
{retrieved_declarations or "Semantic retrieval is disabled or returned no declarations."}
</retrieved_declarations>{_informal_section(informal_guidance)}

Use the proof state to distinguish local hypotheses from the target goal.
If multiple goals are shown, close every goal explicitly with bullets or a tactic
combinator such as `<;>`; a following tactic may affect only the first goal.
Never reference a hypothesis that is absent from the current LSP context.
If `linarith` failed on a nonlinear sign goal involving a square or product,
try the zero-argument tactic `positivity` inside a `by` block before inventing a
theorem name. An unknown identifier is not evidence that a similarly named
declaration exists.
If Lean reports `No goals to be solved`, the preceding tactic already closed the
goal: remove the redundant tactic at the reported position instead of changing
the successful preceding step.
If Lean says `Function expected`, the named term is not callable with the supplied
arguments. Use its printed type instead of guessing an application. In particular,
`le_rfl` only proves an expression is at most itself; it cannot prove an inequality
between different expressions and should not be applied as a multi-argument function.
If a polynomial goal follows from a proved equality or inequality `h`, try
`nlinarith [h]` directly instead of manually asserting the desired rearrangement.
Do not return an approach listed in the rejected history. Change only what is
needed. Treat retrieved declarations as suggestions until Lean verifies them,
and use only the exact declaration names shown. Do not use proof placeholders."""


def reset_repair_prompt(
    theorem: str,
    diagnostics: str,
    repair_number: int,
    lean_lsp_feedback: str = "",
    rejected_history: str = "",
    retrieved_declarations: str = "",
    informal_guidance: str = "",
) -> str:
    strategies = (
        "Use an existing named Mathlib theorem with `exact` or `simpa using`.",
        "Use a single short tactic such as `rfl`, `norm_num`, `positivity`, `decide`, `simp`, `omega`, or `aesop`.",
        "Decompose the goal directly, but do not use induction or `congr`.",
    )
    strategy = strategies[(repair_number - 1) % len(strategies)]
    return f"""The previous candidate was truncated, degenerated, or repeated a rejected proof.
Discard it completely and solve the original task from a fresh context.

<original_task>
{theorem}
</original_task>

<lean_diagnostics_from_discarded_candidate>
{diagnostics}
</lean_diagnostics_from_discarded_candidate>

<lean_lsp_proof_state>
{lean_lsp_feedback or "No Lean LSP proof state was available."}
</lean_lsp_proof_state>

<previously_rejected_attempts>
{rejected_history or "No earlier rejected attempts were recorded."}
</previously_rejected_attempts>

<retrieved_declarations>
{retrieved_declarations or "Semantic retrieval is disabled or returned no declarations."}
</retrieved_declarations>{_informal_section(informal_guidance)}

Required new approach: {strategy}
Return a complete Lean file in at most 20 lines. Do not repeat the previous proof,
do not retry any approach in the rejected history, close every displayed goal,
and do not reference hypotheses absent from the LSP context. If Lean reported
`Function expected`, do not call that term with the same explicit arguments.
`le_rfl` proves only `t ≤ t`; it cannot establish a rearranged inequality between
different expressions. Pass proved polynomial facts directly to `nlinarith [h]`
instead of manufacturing a target-shaped fact. Do not use proof
placeholders. Use exact names from retrieved Lean source; never guess a similar
declaration name."""


def _informal_section(informal_guidance: str) -> str:
    if not informal_guidance:
        return ""
    return f"""

<informal_mathematical_guidance>
{informal_guidance}
</informal_mathematical_guidance>

This is advisory mathematical reasoning from isolated model calls, not a Lean
proof and not evidence of correctness. Translate useful steps into Lean and
accept them only if Lean verifies the resulting candidate. Never copy a Lean-like
identifier from this informal text unless retrieved Lean source shows its exact
name and type; the informal roles are not authorities on declarations."""


def rewrite_repair_prompt(
    theorem: str, diagnostics: str, lean_lsp_feedback: str,
    retrieved_declarations: str, informal_guidance: str, rejected_history: str = "",
) -> str:
    return f"""Lean rejected a rewrite step. Rebuild the formal proof
from the original task; do not copy the discarded candidate or its rewrite.
Keep any useful mathematical strategy, but change its Lean implementation.

<original_task>
{theorem}
</original_task>

<lean_diagnostics>
{diagnostics}
</lean_diagnostics>

<lean_lsp_proof_state>
{lean_lsp_feedback or "No proof state available."}
</lean_lsp_proof_state>

<retrieved_declarations>
{retrieved_declarations or "No retrieved declarations available."}
</retrieved_declarations>{_informal_section(informal_guidance)}

`rw` needs a proof of equality or logical equivalence and an occurrence of its
pattern in the target. It cannot use an inequality as a rewrite rule. Do not reuse
the rejected rewrite expression. Establish needed intermediate facts
with `have` and a valid proof; use inequality facts as hypotheses, not rewrite rules.
For polynomial inequalities, supply proved sign facts to `nlinarith`; `positivity`
can prove sign subgoals. Once a sign fact `h` is established, try `nlinarith [h]`
directly before manually constructing a rearranged inequality. `le_rfl` proves
only `t ≤ t`, takes its target implicitly, and cannot relate two different
expressions. `ring` can normalize polynomial identities. For other
goals, use a declaration whose type actually matches the needed proposition.
Retrieved source may omit its surrounding namespace: use the exact fully qualified
declaration name printed beside it, not an unqualified name copied from its body.
The displayed local context belongs to the discarded candidate. Recreate any
needed local facts in the new proof; they do not exist automatically.
<previously_rejected_attempts>
{rejected_history or "No earlier rejected attempts were recorded."}
</previously_rejected_attempts>
Return the complete unchanged theorem with a short proof in a single Lean fence.
No placeholders. Only Lean can establish success."""
