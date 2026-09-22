from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import tomllib
from typing import Any

from .orchestrator import has_concrete_proof_candidate, replace_target_proof
from .types import AttemptResult, FailureCategory, InformalVerdict
from .verification.base import LeanVerifier


VALID_MODES = frozenset({"generation", "repair", "negative"})
SUITE_ID = "v3_informal_reasoning_ablation"
INFORMAL_NORMAL_STOPS = frozenset({
    "verifier_accepted", "verifier_rejected", "refinement_budget", "generator_only",
})


def _reports_timeout(message: str | None) -> bool:
    # "tim" also occurs in RuntimeError and estimated; neither means timeout.
    lowered = (message or "").lower()
    return any(marker in lowered for marker in ("timeout", "timed out", "time out"))


@dataclass(frozen=True, slots=True)
class V3Case:
    case_id: str
    path: Path
    mode: str
    max_rounds: int
    purpose: str
    reference_proof: str = ""


def load_v3_cases(manifest_path: str | Path, *, expected_suite: str = SUITE_ID) -> list[V3Case]:
    manifest = Path(manifest_path)
    with manifest.open("rb") as handle:
        data = tomllib.load(handle)
    suite_id = str(data.get("suite", "")).strip()
    if suite_id != expected_suite:
        raise ValueError(
            f"V3 runner requires suite = {expected_suite!r}, got {suite_id!r}."
        )
    raw_cases = data.get("case")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("V3 suite manifest must contain at least one [[case]]")

    cases: list[V3Case] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise ValueError("Each [[case]] must be a TOML table")
        case_id = str(raw.get("id", "")).strip()
        mode = str(raw.get("mode", "")).strip()
        relative_path = Path(str(raw.get("path", "")))
        max_rounds = int(raw.get("max_rounds", 5))
        purpose = str(raw.get("purpose", "")).strip()
        reference = str(raw.get("reference_proof", "")).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"Missing or duplicate V3 case id: {case_id!r}")
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown V3 case mode for {case_id}: {mode!r}")
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"V3 case path must stay under the manifest: {relative_path}")
        if max_rounds <= 0:
            raise ValueError(f"max_rounds must be positive for {case_id}")
        if mode != "negative" and not reference:
            raise ValueError(f"V3 positive case needs reference_proof: {case_id}")
        path = manifest.parent / relative_path
        if not path.is_file():
            raise ValueError(f"V3 case file does not exist: {path}")
        source = path.read_text(encoding="utf-8")
        if mode == "repair" and not has_concrete_proof_candidate(source):
            raise ValueError(f"V3 repair case needs a concrete seed: {case_id}")
        if mode != "repair" and has_concrete_proof_candidate(source):
            raise ValueError(f"V3 {mode} case must start with a proof hole: {case_id}")
        seen.add(case_id)
        cases.append(V3Case(case_id, path, mode, max_rounds, purpose, reference))
    return cases


def validate_v3_cases(cases: list[V3Case], verifier: LeanVerifier) -> dict[str, Any]:
    """Lean-check repair seeds and hidden references before any paired run."""
    checks: list[dict[str, Any]] = []
    infrastructure = {
        FailureCategory.VERIFIER_UNAVAILABLE,
        FailureCategory.VERIFIER_TIMEOUT,
    }
    for case in cases:
        source = case.path.read_text(encoding="utf-8")
        item: dict[str, Any] = {
            "id": case.case_id,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "passed": True,
        }
        if case.mode == "repair":
            seed = verifier.verify(source, attempt_id=f"v3-validate-seed-{case.case_id}")
            item["seed_verification"] = seed
            item["passed"] = not seed.valid and seed.failure_category not in infrastructure
        if case.mode != "negative":
            reference = replace_target_proof(source, case.reference_proof)
            verified = verifier.verify(
                reference, attempt_id=f"v3-validate-reference-{case.case_id}"
            )
            item["reference_source"] = reference
            item["reference_verification"] = verified
            item["passed"] = item["passed"] and verified.valid
        else:
            item["negative_fixture"] = True
        checks.append(item)
    return {
        "passed": all(item["passed"] for item in checks),
        "repair_seeds_checked": sum(case.mode == "repair" for case in cases),
        "reference_proofs_checked": sum(case.mode != "negative" for case in cases),
        "cases": checks,
    }


def outcome_correct(case: V3Case, result: AttemptResult) -> bool:
    if case.mode == "negative":
        return bool(
            not result.success
            and result.end_reason == "MAX_ROUNDS"
            and result.rounds == case.max_rounds
        )
    if case.mode == "repair":
        seed_rejected = bool(
            result.iterations
            and result.iterations[0].iteration == 0
            and not result.iterations[0].verification.valid
        )
        return bool(result.success and seed_rejected and result.rounds >= 1)
    return bool(result.success and result.rounds >= 1)


def attempt_measurements(case: V3Case, result: AttemptResult) -> dict[str, Any]:
    generated = [record for record in result.iterations if record.iteration > 0]
    rejected = [record for record in generated if not record.verification.valid]
    reasoning = result.informal_reasoning
    revision_requests = [r for r in (reasoning.reviews if reasoning else ())
                         if r.verdict == InformalVerdict.REVISE]
    revision_passes = sum(
        any(later.refinement_round == review.refinement_round + 1
            and later.verdict == InformalVerdict.ACCEPT for later in reasoning.reviews)
        for review in revision_requests
    )
    approved = bool(reasoning and reasoning.accepted)
    first_pass = bool(generated and generated[0].verification.valid)
    return {
        "outcome_correct": outcome_correct(case, result),
        "proof_failures": len(rejected),
        "unknown_identifier_failures": sum(
            record.verification.failure_category == FailureCategory.UNKNOWN_IDENTIFIER
            or any(marker in diagnostic.lower()
                   for marker in ("unknown identifier", "unknown constant")
                   for diagnostic in record.verification.diagnostics)
            for record in rejected
        ),
        "model_calls": result.metrics.model_calls,
        "kimina_checks": result.metrics.kimina_checks,
        "lean_lsp_calls": result.metrics.lean_lsp_calls,
        "retrieval_queries": result.metrics.retrieval_queries,
        "retrieval_calls": result.metrics.retrieval_calls,
        "strategy_retrieval_queries": result.metrics.strategy_retrieval_queries,
        "rewrite_recovery_prompts": result.metrics.rewrite_recovery_prompts,
        "invalid_rewrite_failures": sum(any("invalid rewrite argument" in d.lower()
                                            for d in record.verification.diagnostics)
                                        for record in rejected),
        "informal_triggered": bool(reasoning and reasoning.triggered),
        "informal_accepted": bool(reasoning and reasoning.accepted),
        "informal_rounds": reasoning.rounds if reasoning else 0,
        "informal_stop_reason": reasoning.stop_reason if reasoning else None,
        "informal_stage_failed": bool(
            reasoning and reasoning.stop_reason not in INFORMAL_NORMAL_STOPS
        ),
        "informal_review_completed": bool(reasoning and reasoning.reviews
            and reasoning.reviews[-1].verdict != InformalVerdict.MALFORMED),
        "first_pass_success": first_pass,
        "successful_formal_repair": bool(result.success and (
            case.mode == "repair" or not first_pass)),
        "informal_pass_downstream_failure": approved and not result.success,
        "informal_revision_requests": len(revision_requests),
        "informal_revisions_next_pass": revision_passes,
        "revised_plan_lean_success": bool(revision_requests and approved and result.success),
        "timeout": bool(result.failure_category == FailureCategory.VERIFIER_TIMEOUT
            or (result.stop_reason == "runtime_error" and _reports_timeout(result.error_message))
            or (reasoning and reasoning.stop_reason == "runtime_error"
                and _reports_timeout(reasoning.error_message))),
        "informal_generator_calls": result.metrics.informal_generator_calls,
        "informal_verifier_calls": result.metrics.informal_verifier_calls,
        "informal_prompt_tokens": result.metrics.informal_prompt_tokens,
        "informal_completion_tokens": result.metrics.informal_completion_tokens,
        "informal_revisions": sum(
            review.verdict == InformalVerdict.REVISE
            for review in (reasoning.reviews if reasoning else ())
        ),
    }


def summarize_condition(case_runs: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [item for item in case_runs if item["mode"] != "negative"]
    positive_successes = sum(item["attempt"]["success"] for item in positives)
    approved = sum(item["measurements"]["informal_accepted"] for item in case_runs)
    downstream_failures = sum(item["measurements"]["informal_pass_downstream_failure"] for item in case_runs)
    revision_requests = sum(item["measurements"]["informal_revision_requests"] for item in case_runs)
    revision_passes = sum(item["measurements"]["informal_revisions_next_pass"] for item in case_runs)
    return {
        "strategy_retrieval_queries": sum(item["measurements"].get("strategy_retrieval_queries", 0)
                                          for item in case_runs),
        "rewrite_recovery_prompts": sum(item["measurements"].get("rewrite_recovery_prompts", 0)
                                        for item in case_runs),
        "invalid_rewrite_failures": sum(item["measurements"].get("invalid_rewrite_failures", 0)
                                        for item in case_runs),
        "first_pass_successes": sum(item["measurements"]["first_pass_success"] for item in positives),
        "first_pass_success_rate": (
            sum(item["measurements"]["first_pass_success"] for item in positives) / len(positives)
            if positives else 0.0),
        "average_lean_checks": (sum(item["measurements"]["kimina_checks"] for item in case_runs)
                                / len(case_runs) if case_runs else 0.0),
        "successful_formal_repairs": sum(item["measurements"]["successful_formal_repair"] for item in case_runs),
        "timeout_rate": (sum(item["measurements"]["timeout"] for item in case_runs)
                         / len(case_runs) if case_runs else 0.0),
        "informal_pass_downstream_failures": downstream_failures,
        "informal_pass_downstream_failure_rate": downstream_failures / approved if approved else None,
        "revision_requests": revision_requests,
        "revisions_next_pass": revision_passes,
        "revision_next_pass_rate": revision_passes / revision_requests if revision_requests else None,
        "revised_plan_lean_successes": sum(item["measurements"]["revised_plan_lean_success"] for item in case_runs),
        "total_generated_tokens": sum(item["attempt"]["metrics"]["completion_tokens"]
                                      + item["measurements"]["informal_completion_tokens"] for item in case_runs),
        "positive_successes": positive_successes,
        "positive_total": len(positives),
        "positive_success_rate": positive_successes / len(positives) if positives else 0.0,
        "correct_outcomes": sum(
            item["measurements"]["outcome_correct"] for item in case_runs
        ),
        "proof_failures": sum(
            item["measurements"]["proof_failures"] for item in case_runs
        ),
        "positive_proof_failures": sum(
            item["measurements"]["proof_failures"] for item in positives
        ),
        "unknown_identifier_failures": sum(
            item["measurements"]["unknown_identifier_failures"] for item in case_runs
        ),
        "model_calls": sum(item["measurements"]["model_calls"] for item in case_runs),
        "informal_triggered_cases": sum(
            item["measurements"]["informal_triggered"] for item in case_runs
        ),
        "informal_accepted_cases": sum(
            item["measurements"]["informal_accepted"] for item in case_runs
        ),
        "informal_stage_failures": sum(
            item["measurements"]["informal_stage_failed"] for item in case_runs
        ),
        "informal_review_completed_cases": sum(
            item["measurements"]["informal_review_completed"] for item in case_runs
        ),
        "main_prompt_tokens": sum(
            item["attempt"]["metrics"]["prompt_tokens"] for item in case_runs
        ),
        "main_completion_tokens": sum(
            item["attempt"]["metrics"]["completion_tokens"] for item in case_runs
        ),
        "total_model_calls": sum(
            item["measurements"]["model_calls"]
            + item["measurements"]["informal_generator_calls"]
            + item["measurements"]["informal_verifier_calls"] for item in case_runs
        ),
        "informal_generator_calls": sum(
            item["measurements"]["informal_generator_calls"] for item in case_runs
        ),
        "informal_verifier_calls": sum(
            item["measurements"]["informal_verifier_calls"] for item in case_runs
        ),
        "informal_revisions": sum(
            item["measurements"]["informal_revisions"] for item in case_runs
        ),
        "informal_tokens": sum(
            item["measurements"]["informal_prompt_tokens"]
            + item["measurements"]["informal_completion_tokens"]
            for item in case_runs
        ),
        "wall_clock_seconds": sum(
            item["attempt"]["metrics"]["wall_clock_seconds"] for item in case_runs
        ),
    }


def comparison_summary(
    baseline_runs: list[dict[str, Any]], informal_runs: list[dict[str, Any]]
) -> dict[str, Any]:
    baseline = summarize_condition(baseline_runs)
    informal = summarize_condition(informal_runs)
    paired = list(zip(baseline_runs, informal_runs, strict=True))
    if any((left.get("id"), left.get("repetition")) !=
           (right.get("id"), right.get("repetition")) for left, right in paired):
        raise ValueError("V3 comparison requires matching case IDs and repetitions")
    return {
        "baseline": baseline,
        "informal": informal,
        "delta_informal_minus_baseline": {
            "positive_successes": (
                informal["positive_successes"] - baseline["positive_successes"]
            ),
            "positive_success_rate": (
                informal["positive_success_rate"] - baseline["positive_success_rate"]
            ),
            "correct_outcomes": informal["correct_outcomes"] - baseline["correct_outcomes"],
            "model_calls": informal["model_calls"] - baseline["model_calls"],
            "wall_clock_seconds": (
                informal["wall_clock_seconds"] - baseline["wall_clock_seconds"]
            ),
        },
        "reductions_baseline_minus_informal": {
            "proof_failures": baseline["proof_failures"] - informal["proof_failures"],
            "unknown_identifier_failures": (
                baseline["unknown_identifier_failures"]
                - informal["unknown_identifier_failures"]
            ),
        },
        "paired_informal_wins": sum(
            not left["measurements"]["outcome_correct"]
            and right["measurements"]["outcome_correct"]
            for left, right in paired
        ),
        "paired_informal_losses": sum(
            left["measurements"]["outcome_correct"]
            and not right["measurements"]["outcome_correct"]
            for left, right in paired
        ),
        "paired_ties": sum(
            left["measurements"]["outcome_correct"]
            == right["measurements"]["outcome_correct"]
            for left, right in paired
        ),
    }
