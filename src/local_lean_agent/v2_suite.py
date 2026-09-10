from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import tomllib

from .orchestrator import replace_target_proof
from .types import AttemptResult, FailureCategory
from .verification.base import LeanVerifier


VALID_MODES = frozenset({"generation", "repair", "negative"})
SUITE_ID = "v2_semantic_retrieval_ablation"
_UNKNOWN_MARKERS = (
    "unknown identifier",
    "unknown constant",
)


@dataclass(frozen=True, slots=True)
class V2Case:
    case_id: str
    path: Path
    mode: str
    max_rounds: int
    purpose: str
    expected_declarations: tuple[str, ...]
    reference_proof: str = ""


def load_v2_cases(manifest_path: str | Path) -> list[V2Case]:
    manifest = Path(manifest_path)
    with manifest.open("rb") as handle:
        data = tomllib.load(handle)
    suite_id = str(data.get("suite", "")).strip()
    if suite_id != SUITE_ID:
        raise ValueError(
            f"V2 runner requires suite = {SUITE_ID!r}, got {suite_id!r}. "
            "Use `v1-suite` for benchmarks/v1/manifest.toml."
        )
    raw_cases = data.get("case")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("V2 suite manifest must contain at least one [[case]]")

    cases: list[V2Case] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise ValueError("Each [[case]] must be a TOML table")
        case_id = str(raw.get("id", "")).strip()
        mode = str(raw.get("mode", "")).strip()
        relative_path = Path(str(raw.get("path", "")))
        max_rounds = int(raw.get("max_rounds", 5))
        purpose = str(raw.get("purpose", "")).strip()
        expected = tuple(str(item).strip() for item in raw.get("expected_declarations", ()))
        if not case_id or case_id in seen:
            raise ValueError(f"Missing or duplicate V2 case id: {case_id!r}")
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown V2 case mode for {case_id}: {mode!r}")
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"V2 case path must stay under the manifest: {relative_path}")
        if max_rounds <= 0:
            raise ValueError(f"max_rounds must be positive for {case_id}")
        if mode != "negative" and not expected:
            raise ValueError(f"V2 positive case needs expected_declarations: {case_id}")
        path = manifest.parent / relative_path
        if not path.is_file():
            raise ValueError(f"V2 case file does not exist: {path}")
        seen.add(case_id)
        reference = str(raw.get("reference_proof", "")).strip()
        if mode != "negative" and not reference:
            raise ValueError(f"V2 positive case needs reference_proof: {case_id}")
        cases.append(V2Case(case_id, path, mode, max_rounds, purpose, expected, reference))
    return cases


def validate_v2_cases(cases: list[V2Case], verifier: LeanVerifier) -> dict[str, Any]:
    """Check fixtures with Lean; reference proofs never enter the agent packet."""
    checks: list[dict[str, Any]] = []
    infrastructure = {FailureCategory.VERIFIER_UNAVAILABLE, FailureCategory.VERIFIER_TIMEOUT}
    for case in cases:
        source = case.path.read_text(encoding="utf-8")
        item: dict[str, Any] = {
            "id": case.case_id,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "passed": True,
        }
        if case.mode == "repair":
            seed = verifier.verify(source, attempt_id=f"v2-validate-seed-{case.case_id}")
            item["seed_verification"] = seed
            item["passed"] = not seed.valid and seed.failure_category not in infrastructure
        if case.mode != "negative":
            reference = replace_target_proof(source, case.reference_proof)
            verified = verifier.verify(reference, attempt_id=f"v2-validate-reference-{case.case_id}")
            item["reference_source"] = reference
            item["reference_verification"] = verified
            item["passed"] = item["passed"] and verified.valid
        else:
            # These cases' falsity is established by counterexamples in the docs.
            item["negative_fixture"] = True
        checks.append(item)
    return {
        "passed": all(item["passed"] for item in checks),
        "repair_seeds_checked": sum(case.mode == "repair" for case in cases),
        "reference_proofs_checked": sum(case.mode != "negative" for case in cases),
        "cases": checks,
    }


def outcome_correct(case: V2Case, result: AttemptResult) -> bool:
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


def attempt_measurements(case: V2Case, result: AttemptResult) -> dict[str, Any]:
    generated = [record for record in result.iterations if record.iteration > 0]
    rejected = [record for record in generated if not record.verification.valid]
    unknown_identifier_failures = sum(
        record.verification.failure_category == FailureCategory.UNKNOWN_IDENTIFIER
        or any(
            marker in diagnostic.lower()
            for marker in _UNKNOWN_MARKERS
            for diagnostic in record.verification.diagnostics
        )
        for record in rejected
    )
    retrieved_names = sorted(
        {
            hit.name
            for record in generated
            if record.retrieval is not None and record.retrieval.available
            for hit in record.retrieval.hits
        }
    )
    expected_found = sorted(set(case.expected_declarations) & set(retrieved_names))
    return {
        "outcome_correct": outcome_correct(case, result),
        "proof_failures": len(rejected),
        "unknown_identifier_failures": unknown_identifier_failures,
        "model_calls": result.metrics.model_calls,
        "kimina_checks": result.metrics.kimina_checks,
        "lean_lsp_calls": result.metrics.lean_lsp_calls,
        "retrieval_queries": result.metrics.retrieval_queries,
        "retrieval_calls": result.metrics.retrieval_calls,
        "retrieval_latency_seconds": result.metrics.retrieval_latency_seconds,
        "retrieved_names": retrieved_names,
        "expected_declarations": list(case.expected_declarations),
        "expected_declaration_recall": bool(expected_found),
        "expected_declarations_found": expected_found,
    }


def summarize_condition(case_runs: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [item for item in case_runs if item["mode"] != "negative"]
    return {
        "positive_successes": sum(item["attempt"]["success"] for item in positives),
        "positive_total": len(positives),
        "positive_success_rate": (
            sum(item["attempt"]["success"] for item in positives) / len(positives)
            if positives
            else 0.0
        ),
        "correct_outcomes": sum(item["measurements"]["outcome_correct"] for item in case_runs),
        "proof_failures": sum(item["measurements"]["proof_failures"] for item in case_runs),
        "positive_proof_failures": sum(item["measurements"]["proof_failures"] for item in positives),
        "wall_clock_seconds": sum(item["attempt"]["metrics"]["wall_clock_seconds"] for item in case_runs),
        "unknown_identifier_failures": sum(
            item["measurements"]["unknown_identifier_failures"] for item in case_runs
        ),
        "model_calls": sum(item["measurements"]["model_calls"] for item in case_runs),
        "retrieval_queries": sum(item["measurements"]["retrieval_queries"] for item in case_runs),
        "retrieval_calls": sum(item["measurements"]["retrieval_calls"] for item in case_runs),
        "retrieval_latency_seconds": sum(
            item["measurements"]["retrieval_latency_seconds"] for item in case_runs
        ),
        "expected_declaration_recall": (
            sum(item["measurements"]["expected_declaration_recall"] for item in positives)
            / len(positives)
            if positives
            else 0.0
        ),
    }


def comparison_summary(
    baseline_runs: list[dict[str, Any]], retrieval_runs: list[dict[str, Any]]
) -> dict[str, Any]:
    baseline = summarize_condition(baseline_runs)
    retrieval = summarize_condition(retrieval_runs)
    paired = list(zip(baseline_runs, retrieval_runs, strict=True))
    return {
        "baseline": baseline,
        "retrieval": retrieval,
        "delta_retrieval_minus_baseline": {
            "positive_successes": retrieval["positive_successes"] - baseline["positive_successes"],
            "positive_success_rate": retrieval["positive_success_rate"] - baseline["positive_success_rate"],
            "correct_outcomes": retrieval["correct_outcomes"] - baseline["correct_outcomes"],
        },
        "reductions_baseline_minus_retrieval": {
            "proof_failures": baseline["proof_failures"] - retrieval["proof_failures"],
            "unknown_identifier_failures": (
                baseline["unknown_identifier_failures"]
                - retrieval["unknown_identifier_failures"]
            ),
        },
        "paired_retrieval_wins": sum(
            not left["measurements"]["outcome_correct"]
            and right["measurements"]["outcome_correct"]
            for left, right in paired
        ),
        "paired_retrieval_losses": sum(
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
