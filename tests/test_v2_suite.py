from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock
import unittest

from local_lean_agent.types import (
    AttemptMetrics,
    AttemptResult,
    FailureCategory,
    GenerationResult,
    IterationRecord,
    RetrievalHit,
    RetrievalResult,
    VerificationResult,
)
from local_lean_agent.v2_suite import (
    V2Case,
    attempt_measurements,
    comparison_summary,
    load_v2_cases,
    outcome_correct,
    validate_v2_cases,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "v2" / "manifest.toml"
V1_MANIFEST = ROOT / "benchmarks" / "v1" / "manifest.toml"


def result(*, success: bool, category: FailureCategory, retrieved: bool = False) -> AttemptResult:
    retrieval = (
        RetrievalResult(
            available=True,
            query="append empty",
            hits=(RetrievalHit(1, "List.append_nil"),),
            tool_calls=2,
        )
        if retrieved
        else None
    )
    record = IterationRecord(
        iteration=1,
        candidate="theorem t : True := by trivial",
        verification=VerificationResult(
            valid=success,
            diagnostics=(() if success else ("error: unknown identifier 'Imaginary'",)),
            failure_category=FailureCategory.NONE if success else category,
        ),
        generation=GenerationResult(text="", model="test"),
        estimated_context_tokens=0,
        retrieval=retrieval,
    )
    return AttemptResult(
        attempt_id="test",
        theorem="theorem t : True := by sorry",
        success=success,
        final_proof=record.candidate,
        failure_category=FailureCategory.NONE if success else category,
        stop_reason="verified" if success else "iteration_budget",
        error_message=None,
        iterations=[record],
        metrics=AttemptMetrics(
            iterations=1,
            model_calls=1,
            retrieval_queries=int(retrieved),
            retrieval_calls=2 * int(retrieved),
        ),
    )


class V2SuiteTests(unittest.TestCase):
    def test_rejects_v1_manifest_in_v2_runner(self) -> None:
        with self.assertRaisesRegex(ValueError, "Use `v1-suite`"):
            load_v2_cases(V1_MANIFEST)

    def test_preflight_rejects_an_already_valid_repair_seed(self) -> None:
        case = next(c for c in load_v2_cases(MANIFEST) if c.mode == "repair")
        verifier = Mock()
        verifier.verify.return_value = VerificationResult(valid=True)
        checked = validate_v2_cases([case], verifier)
        self.assertFalse(checked["passed"])
        self.assertEqual(verifier.verify.call_count, 2)

    def test_preflight_accepts_rejected_seed_and_checked_reference(self) -> None:
        case = next(c for c in load_v2_cases(MANIFEST) if c.mode == "repair")
        verifier = Mock()
        verifier.verify.side_effect = [
            VerificationResult(valid=False, failure_category=FailureCategory.TACTIC_FAILURE),
            VerificationResult(valid=True),
        ]
        self.assertTrue(validate_v2_cases([case], verifier)["passed"])

    def test_preflight_does_not_treat_verifier_outage_as_rejected_seed(self) -> None:
        case = next(c for c in load_v2_cases(MANIFEST) if c.mode == "repair")
        verifier = Mock()
        verifier.verify.side_effect = [
            VerificationResult(valid=False, failure_category=FailureCategory.VERIFIER_UNAVAILABLE),
            VerificationResult(valid=True),
        ]
        self.assertFalse(validate_v2_cases([case], verifier)["passed"])

    def test_seed_failures_are_excluded_from_hallucination_counts(self) -> None:
        case = V2Case("x", Path("x.lean"), "repair", 1, "", ("List.append_nil",))
        attempt = result(success=True, category=FailureCategory.NONE)
        seed = replace(
            attempt.iterations[0], iteration=0,
            verification=VerificationResult(valid=False, failure_category=FailureCategory.UNKNOWN_IDENTIFIER),
        )
        attempt.iterations.insert(0, seed)
        measured = attempt_measurements(case, attempt)
        self.assertEqual(measured["proof_failures"], 0)
        self.assertEqual(measured["unknown_identifier_failures"], 0)
        self.assertTrue(measured["outcome_correct"])

    def test_field_type_error_is_not_automatically_counted_as_unknown_name(self) -> None:
        case = V2Case("x", Path("x.lean"), "generation", 1, "", ("Eq.refl",))
        attempt = result(success=False, category=FailureCategory.TACTIC_FAILURE)
        attempt.iterations[0] = replace(attempt.iterations[0], verification=VerificationResult(
            valid=False, failure_category=FailureCategory.TACTIC_FAILURE,
            diagnostics=("invalid field notation, type is not of the form C ...",),
        ))
        self.assertEqual(attempt_measurements(case, attempt)["unknown_identifier_failures"], 0)

    def test_manifest_contains_twenty_balanced_cases(self) -> None:
        cases = load_v2_cases(MANIFEST)
        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case.case_id for case in cases}), 20)
        self.assertEqual(
            {mode: sum(case.mode == mode for case in cases) for mode in {case.mode for case in cases}},
            {"generation": 10, "repair": 8, "negative": 2},
        )

    def test_repair_seeds_are_concrete_and_other_cases_have_holes(self) -> None:
        for case in load_v2_cases(MANIFEST):
            source = case.path.read_text(encoding="utf-8")
            with self.subTest(case=case.case_id):
                self.assertEqual("sorry" not in source, case.mode == "repair")

    def test_measurements_count_unknown_names_and_retrieval_recall(self) -> None:
        case = V2Case(
            "x", Path("x.lean"), "generation", 1, "", ("List.append_nil",)
        )
        measured = attempt_measurements(
            case,
            result(
                success=False,
                category=FailureCategory.UNKNOWN_IDENTIFIER,
                retrieved=True,
            ),
        )
        self.assertEqual(measured["proof_failures"], 1)
        self.assertEqual(measured["unknown_identifier_failures"], 1)
        self.assertTrue(measured["expected_declaration_recall"])

    def test_generation_outcome_requires_a_verified_model_candidate(self) -> None:
        case = V2Case("x", Path("x.lean"), "generation", 1, "", ("Eq.refl",))
        self.assertTrue(outcome_correct(case, result(success=True, category=FailureCategory.NONE)))
        self.assertFalse(
            outcome_correct(
                case,
                result(success=False, category=FailureCategory.TACTIC_FAILURE),
            )
        )

    def test_comparison_reports_failure_reductions_and_paired_win(self) -> None:
        case = V2Case(
            "x", Path("x.lean"), "generation", 1, "", ("List.append_nil",)
        )
        baseline_result = result(
            success=False, category=FailureCategory.UNKNOWN_IDENTIFIER
        )
        retrieval_result = result(
            success=True, category=FailureCategory.NONE, retrieved=True
        )
        baseline = [{
            "mode": case.mode,
            "attempt": baseline_result.to_dict(),
            "measurements": attempt_measurements(case, baseline_result),
        }]
        retrieval = [{
            "mode": case.mode,
            "attempt": retrieval_result.to_dict(),
            "measurements": attempt_measurements(case, retrieval_result),
        }]
        summary = comparison_summary(baseline, retrieval)
        self.assertEqual(summary["paired_retrieval_wins"], 1)
        self.assertEqual(
            summary["reductions_baseline_minus_retrieval"]["proof_failures"], 1
        )
        self.assertEqual(
            summary["reductions_baseline_minus_retrieval"]["unknown_identifier_failures"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
