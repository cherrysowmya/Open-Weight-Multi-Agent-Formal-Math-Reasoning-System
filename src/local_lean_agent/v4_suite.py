"""V3/V4 matched formal-budget comparisons, with explicit role/cost coverage."""
from dataclasses import replace
from .config import AppConfig
from .types import AttemptResult
from .v3_suite import load_v3_cases, attempt_measurements, V3Case


def load_v4_cases(path):
    return load_v3_cases(path, expected_suite="v4_context_ablation")


def condition_config(config: AppConfig, condition: str) -> AppConfig:
    if condition not in {"v3", "v4", "discussion-only", "fresh-only"}:
        raise ValueError("Unknown V4 condition")
    return replace(config,
        v4=replace(config.v4, enabled=condition != "v3",
            discussion_enabled=condition in {"v4", "discussion-only"},
            fresh_context_enabled=condition in {"v4", "fresh-only"}),
        informal_reasoning=replace(config.informal_reasoning, enabled=True,
            rewrite_salvage_enabled=False),
        agent=replace(config.agent, fallback_enabled=False, unload_model_after_attempt=False))


def measurements(case: V3Case, result: AttemptResult) -> dict:
    measured = attempt_measurements(case, result)
    m = result.metrics
    measured.update({
        "main_agent_calls": m.main_agent_calls,
        "fresh_subproblem_calls": m.fresh_subproblem_calls,
        "discussion_partner_calls": m.discussion_partner_calls,
        "context_compressions": m.context_compressions,
        "discussion_prompt_tokens": m.discussion_prompt_tokens,
        "discussion_completion_tokens": m.discussion_completion_tokens,
        "all_model_calls": max(m.formal_call_attempts, m.model_calls) + m.informal_generator_calls
            + m.informal_verifier_calls + m.discussion_partner_calls,
        "all_generated_tokens": m.completion_tokens + m.informal_completion_tokens
            + m.discussion_completion_tokens,
        "v4_role_errors": sum(r.status == "error" for r in result.v4_requests),
        "v4_activated": bool(m.fresh_subproblem_calls or m.discussion_partner_calls),
    })
    return measured


def summarize(runs: list[dict]) -> dict:
    positives = [r for r in runs if r["mode"] != "negative"]
    return {
        "cases": len(runs),
        "positive_successes": sum(r["passed"] for r in positives),
        "claimed_positive_successes": sum(r["attempt"]["success"] for r in positives),
        "positive_total": len(positives),
        "correct_outcomes": sum(r["passed"] for r in runs),
        "wall_clock_seconds": sum(r["attempt"]["metrics"]["wall_clock_seconds"] for r in runs),
        **{key: sum(r["measurements"][key] for r in runs) for key in (
            "all_model_calls", "all_generated_tokens", "kimina_checks", "proof_failures",
            "discussion_partner_calls", "fresh_subproblem_calls", "v4_activated",
            "v4_role_errors", "context_compressions", "informal_stage_failed")},
    }
