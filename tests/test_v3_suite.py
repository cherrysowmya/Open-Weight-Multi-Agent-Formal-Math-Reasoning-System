from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
import unittest

from local_lean_agent.types import (
    AttemptMetrics,
    AttemptResult,
    FailureCategory,
    GenerationResult,
    InformalDraft,
    InformalReasoningResult,
    InformalReview,
    InformalVerdict,
    IterationRecord,
    TokenUsage,
    VerificationResult,
)
from local_lean_agent.v3_suite import (
    V3Case,
    attempt_measurements,
    comparison_summary,
    load_v3_cases,
    outcome_correct,
    validate_v3_cases,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "v3" / "manifest.toml"
V2_MANIFEST = ROOT / "benchmarks" / "v2" / "manifest.toml"


def result(*, success: bool, informal: bool = False) -> AttemptResult:
    record = IterationRecord(
        iteration=1,
        candidate="theorem t : True := by trivial",
        verification=VerificationResult(
            valid=success,
            diagnostics=(() if success else ("error: unsolved goals",)),
            failure_category=(
                FailureCategory.NONE if success else FailureCategory.UNSOLVED_GOALS
            ),
        ),
        generation=GenerationResult(text="", model="test"),
        estimated_context_tokens=100,
    )
    reasoning = None
    if informal:
        generation = GenerationResult(
            text="informal", model="test", usage=TokenUsage(40, 10, 50)
        )
        reasoning = InformalReasoningResult(
            triggered=True,
            accepted=True,
            rounds=1,
            final_proof="Use the assumption.",
            stop_reason="verifier_accepted",
            generator_calls=1,
            verifier_calls=1,
            drafts=(InformalDraft(1, "Use the assumption.", generation, 50),),
            reviews=(
                InformalReview(
                    1, InformalVerdict.ACCEPT, "Complete.", generation, 50
                ),
            ),
        )
    return AttemptResult(
        attempt_id="test",
        theorem="theorem t : True := by sorry",
        success=success,
        final_proof=record.candidate,
        failure_category=(
            FailureCategory.NONE if success else FailureCategory.UNSOLVED_GOALS
        ),
        stop_reason="verified" if success else "iteration_budget",
        error_message=None,
        iterations=[record],
        metrics=AttemptMetrics(
            iterations=1,
            model_calls=1,
            informal_generator_calls=int(informal),
            informal_verifier_calls=int(informal),
            informal_prompt_tokens=80 * int(informal),
            informal_completion_tokens=20 * int(informal),
        ),
        informal_reasoning=reasoning,
    )


class V3SuiteTests(unittest.TestCase):
    def test_manifest_contains_twenty_balanced_cases(self) -> None:
        cases = load_v3_cases(MANIFEST)
        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case.case_id for case in cases}), 20)
        self.assertEqual(
            {
                mode: sum(case.mode == mode for case in cases)
                for mode in {case.mode for case in cases}
            },
            {"generation": 10, "repair": 8, "negative": 2},
        )

    def test_rejects_a_v2_manifest(self) -> None:
        with self.assertRaisesRegex(ValueError, "V3 runner requires"):
            load_v3_cases(V2_MANIFEST)

    def test_preflight_checks_repair_seed_then_reference(self) -> None:
        case = next(case for case in load_v3_cases(MANIFEST) if case.mode == "repair")
        verifier = Mock()
        verifier.verify.side_effect = [
            VerificationResult(
                valid=False, failure_category=FailureCategory.TACTIC_FAILURE
            ),
            VerificationResult(valid=True),
        ]
        checked = validate_v3_cases([case], verifier)
        self.assertTrue(checked["passed"])
        self.assertEqual(verifier.verify.call_count, 2)

    def test_verifier_outage_does_not_count_as_a_rejected_seed(self) -> None:
        case = next(case for case in load_v3_cases(MANIFEST) if case.mode == "repair")
        verifier = Mock()
        verifier.verify.side_effect = [
            VerificationResult(
                valid=False, failure_category=FailureCategory.VERIFIER_UNAVAILABLE
            ),
            VerificationResult(valid=True),
        ]
        self.assertFalse(validate_v3_cases([case], verifier)["passed"])

    def test_measurements_keep_main_and_informal_calls_separate(self) -> None:
        case = V3Case("x", Path("x.lean"), "generation", 1, "")
        measured = attempt_measurements(case, result(success=True, informal=True))
        self.assertEqual(measured["model_calls"], 1)
        self.assertEqual(measured["informal_generator_calls"], 1)
        self.assertEqual(measured["informal_verifier_calls"], 1)
        self.assertEqual(measured["informal_prompt_tokens"], 80)
        self.assertTrue(measured["informal_accepted"])

    def test_informal_acceptance_does_not_make_outcome_correct(self) -> None:
        case = V3Case("x", Path("x.lean"), "generation", 1, "")
        rejected = result(success=False, informal=True)
        self.assertTrue(rejected.informal_reasoning.accepted)
        self.assertFalse(outcome_correct(case, rejected))

    def test_comparison_reports_paired_gain_and_extra_cost(self) -> None:
        case = V3Case("x", Path("x.lean"), "generation", 1, "")
        baseline_result = result(success=False)
        informal_result = result(success=True, informal=True)
        baseline = [
            {
                "mode": case.mode,
                "attempt": baseline_result.to_dict(),
                "measurements": attempt_measurements(case, baseline_result),
            }
        ]
        informal = [
            {
                "mode": case.mode,
                "attempt": informal_result.to_dict(),
                "measurements": attempt_measurements(case, informal_result),
            }
        ]
        summary = comparison_summary(baseline, informal)
        self.assertEqual(summary["paired_informal_wins"], 1)
        self.assertEqual(summary["informal"]["informal_generator_calls"], 1)
        self.assertEqual(summary["informal"]["informal_tokens"], 100)


if __name__ == "__main__":
    unittest.main()
