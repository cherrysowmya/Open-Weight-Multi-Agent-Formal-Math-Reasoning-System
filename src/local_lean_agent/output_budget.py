"""Provider-independent formal output policy. Heuristics never certify proofs."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from .config import GenerationConfig
from .types import FailureCategory, IterationRecord


def repetitive_output(candidate: str) -> bool:
    """Catch renamed repeated assertions and runaway comma-separated rewrites.

    This is only a recovery heuristic, not a Lean parser or correctness check.
    The complete candidate is still checked by Lean even when flagged.
    """
    lines = []
    for line in candidate.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        line = re.sub(r"^(have|let)\s+[\w'₀-₉]+(?=\s|:)", r"\1 _", line)
        lines.append(line)
    if any(count >= 5 for count in Counter(lines).values()):
        return True
    # Generation can repeat one lemma hundreds of times on a single line.
    # Require eight consecutive *whole* identifiers, not eight occurrences
    # anywhere in a proof (which would flag ordinary uses of local variables).
    return re.search(
        r"(?<![\w'.])(?P<item>[A-Za-z_][\w'.]*)(?:\s*,\s*(?P=item)(?![\w'.])){7}",
        "\n".join(lines),
    ) is not None


def output_status(finish_reason: str | None, repetitive: bool) -> str:
    if finish_reason == "length":
        return "truncated_repetitive" if repetitive else "truncated_nonrepetitive"
    return "repetitive" if repetitive else "not_truncated"


@dataclass(frozen=True)
class OutputBudgetDecision:
    tokens: int
    action: str
    reset: bool
    previous_status: str


def choose_output_budget(config: GenerationConfig, previous: IterationRecord | None,
                         *, repetitive: bool = False, fresh: bool = False,
                         fresh_limit: int = 512) -> OutputBudgetDecision:
    generated = previous is not None and previous.iteration > 0
    truncated = bool(generated and previous.generation.finish_reason == "length")
    status = output_status(previous.generation.finish_reason, repetitive) if generated else "none"
    invalid_task = bool(previous and previous.verification.failure_category in {
        FailureCategory.TASK_MUTATION, FailureCategory.UNSAFE_PLACEHOLDER})
    base = config.max_output_tokens
    policy = config.output_budget_policy
    if policy == "legacy":
        tokens = min(base, fresh_limit) if fresh else base
        reset = repetitive or truncated
        return OutputBudgetDecision(min(tokens, config.reset_output_tokens) if reset else tokens,
                                    "legacy_reset" if reset else "legacy", reset, status)
    reset = repetitive or (truncated and (policy == "fixed" or invalid_task))
    if policy == "fixed":
        return OutputBudgetDecision(base, "fixed_reset" if reset else "fixed", reset, status)
    if repetitive or invalid_task:
        return OutputBudgetDecision(base, "reset_repetitive" if repetitive else "reset_invalid_task",
                                    True, status)
    previous_limit = (previous.requested_output_tokens or base) if generated else base
    if truncated:
        tokens = min(config.max_recovery_output_tokens, max(base, previous_limit * 2))
        action = "expand_truncated" if tokens > previous_limit else "retry_at_ceiling"
        return OutputBudgetDecision(tokens, action, False, status)
    # Preserve an earned larger budget while repairing a non-repetitive proof.
    tokens = min(config.max_recovery_output_tokens, max(base, previous_limit))
    return OutputBudgetDecision(tokens, "repair" if generated else "initial", False, status)


RECOVERY_INSTRUCTION = """
<output_budget_recovery>
The previous response reached its output-token limit without detected repetition.
This is not evidence that its mathematics or Lean steps are correct. Use the
compiler feedback to repair it. Return one COMPLETE replacement Lean file, not
a suffix to concatenate. Retain useful structure only when justified; do not
repeat assertions or expand the proof unnecessarily. Finish all goals and close
the code fence within the requested output budget.
</output_budget_recovery>
"""
