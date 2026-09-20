"""Bounded, model-free tactic portfolio, shared by standalone and agent modes."""
import time
from uuid import uuid4

from .config import AppConfig
from .proof_body import ProofSlot
from .telemetry import JSONLTelemetry
from .types import AttemptMetrics, AttemptResult, FailureCategory, FallbackAttempt
from .verification.base import LeanVerifier


def try_portfolio(theorem: str, *, verifier: LeanVerifier, config: AppConfig,
                  attempt_id: str, metrics: AttemptMetrics, attempts: list[FallbackAttempt],
                  attempted: set[str], telemetry: JSONLTelemetry):
    if not config.agent.fallback_enabled:
        return None
    slot = ProofSlot.from_source(theorem)
    for tactic in config.agent.fallback_tactics:
        key = "portfolio:" + tactic
        if key in attempted:
            continue
        attempted.add(key)
        candidate = slot.assemble("by\n  " + tactic)
        started = time.monotonic()
        telemetry.emit("portfolio_check_started", attempt_id, {"tactic": tactic})
        verification = verifier.verify(candidate, attempt_id=f"{attempt_id}-portfolio-{len(attempts) + 1}")
        elapsed = time.monotonic() - started
        metrics.kimina_checks += 1
        metrics.fallback_checks += 1
        metrics.portfolio_checks += 1
        metrics.portfolio_wall_clock_seconds += elapsed
        metrics.portfolio_successes += int(verification.valid)
        record = FallbackAttempt(tactic, candidate, verification, elapsed)
        attempts.append(record)
        telemetry.emit("fallback_candidate_checked", attempt_id, record)
        if verification.valid or verification.failure_category == FailureCategory.VERIFIER_UNAVAILABLE:
            return candidate, verification
    return None


def solve_portfolio(theorem: str, verifier: LeanVerifier, config: AppConfig) -> AttemptResult:
    """No inference, retrieval, informal reasoning, or LSP; full Lean checks only."""
    attempt_id = uuid4().hex
    started = time.monotonic()
    metrics = AttemptMetrics()
    attempts: list[FallbackAttempt] = []
    telemetry = JSONLTelemetry(config.agent.log_path)
    telemetry.emit("portfolio_started", attempt_id, {"tactics": config.agent.fallback_tactics})
    outcome = try_portfolio(theorem, verifier=verifier, config=config, attempt_id=attempt_id,
                            metrics=metrics, attempts=attempts, attempted=set(), telemetry=telemetry)
    metrics.wall_clock_seconds = time.monotonic() - started
    success = bool(outcome and outcome[1].valid)
    unavailable = bool(outcome and outcome[1].failure_category == FailureCategory.VERIFIER_UNAVAILABLE)
    result = AttemptResult(attempt_id=attempt_id, theorem=theorem, success=success,
        final_proof=outcome[0] if outcome else "",
        failure_category=FailureCategory.NONE if success else (
            outcome[1].failure_category if outcome else
            attempts[-1].verification.failure_category if attempts else FailureCategory.UNKNOWN),
        stop_reason="verified" if success else "verifier_unavailable" if unavailable else "portfolio_exhausted",
        error_message=None, iterations=[], metrics=metrics, fallback_attempts=attempts)
    telemetry.emit("attempt_completed", attempt_id, result)
    return result
