from dataclasses import replace
from pathlib import Path
import unittest

from local_lean_agent.types import (
    AttemptMetrics,
    AttemptResult,
    FailureCategory,
    GenerationResult,
    IterationRecord,
    LeanFeedback,
    VerificationResult,
)
from local_lean_agent.v1_suite import V1Case, assess_v1_result, load_v1_cases


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "v1" / "manifest.toml"
V2_MANIFEST = ROOT / "benchmarks" / "v2" / "manifest.toml"


def result_with(
    *,
    success: bool,
    model_calls: int,
    stop_reason: str,
    records: list[IterationRecord],
) -> AttemptResult:
    return AttemptResult(
        attempt_id="test",
        theorem="theorem t : True := by sorry",
        success=success,
        final_proof=records[-1].candidate if records else "",
        failure_category=(FailureCategory.NONE if success else FailureCategory.TACTIC_FAILURE),
        stop_reason=stop_reason,
        error_message=None,
        iterations=records,
        metrics=AttemptMetrics(iterations=model_calls, model_calls=model_calls),
    )


def record(
    iteration: int, valid: bool, *, lsp_available: bool = True
) -> IterationRecord:
    return IterationRecord(
        iteration=iteration,
        candidate="theorem t : True := by trivial",
        verification=VerificationResult(
            valid=valid,
            failure_category=(
                FailureCategory.NONE if valid else FailureCategory.TACTIC_FAILURE
            ),
        ),
        generation=GenerationResult(text="", model="test"),
        estimated_context_tokens=0,
        lean_feedback=(
            None
            if valid
            else LeanFeedback(available=lsp_available, tool_calls=2)
        ),
    )


class V1SuiteTests(unittest.TestCase):
    def test_rejects_v2_manifest_in_v1_runner(self) -> None:
        with self.assertRaisesRegex(ValueError, "Use `v2-ablation`"):
            load_v1_cases(V2_MANIFEST)

    def test_manifest_contains_exactly_twenty_unique_cases(self) -> None:
        cases = load_v1_cases(MANIFEST)
        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case.case_id for case in cases}), 20)

    def test_manifest_covers_all_v1_execution_modes(self) -> None:
        cases = load_v1_cases(MANIFEST)
        counts = {
            mode: sum(case.mode == mode for case in cases)
            for mode in {case.mode for case in cases}
        }
        self.assertEqual(
            counts,
            {"generation": 6, "repair": 9, "already_valid": 2, "negative": 3},
        )

    def test_repair_cases_have_concrete_seed_proofs(self) -> None:
        for case in load_v1_cases(MANIFEST):
            if case.mode == "repair":
                with self.subTest(case=case.case_id):
                    self.assertNotIn("sorry", case.path.read_text(encoding="utf-8"))

    def test_generation_and_negative_cases_start_with_holes(self) -> None:
        for case in load_v1_cases(MANIFEST):
            if case.mode in {"generation", "negative"}:
                with self.subTest(case=case.case_id):
                    self.assertIn("sorry", case.path.read_text(encoding="utf-8"))

    def test_generation_requires_a_verified_model_round(self) -> None:
        case = V1Case("g", Path("g.lean"), "generation", 3, "")
        successful = result_with(
            success=True,
            model_calls=1,
            stop_reason="verified",
            records=[record(1, True)],
        )
        passed, _ = assess_v1_result(case, successful)
        self.assertTrue(passed)
        passed, _ = assess_v1_result(
            case, replace(successful, metrics=AttemptMetrics(model_calls=0))
        )
        self.assertFalse(passed)

    def test_repair_requires_rejected_seed_then_verified_model_candidate(self) -> None:
        case = V1Case("r", Path("r.lean"), "repair", 3, "")
        repaired = result_with(
            success=True,
            model_calls=1,
            stop_reason="verified",
            records=[record(0, False), record(1, True)],
        )
        passed, _ = assess_v1_result(case, repaired)
        self.assertTrue(passed)
        no_seed_failure = replace(repaired, iterations=[record(1, True)])
        passed, _ = assess_v1_result(case, no_seed_failure)
        self.assertFalse(passed)

        no_lsp_feedback = replace(
            repaired,
            iterations=[record(0, False, lsp_available=False), record(1, True)],
        )
        passed, _ = assess_v1_result(case, no_lsp_feedback)
        self.assertFalse(passed)

    def test_already_valid_requires_zero_model_calls(self) -> None:
        case = V1Case("v", Path("v.lean"), "already_valid", 3, "")
        valid_seed = result_with(
            success=True,
            model_calls=0,
            stop_reason="verified",
            records=[record(0, True)],
        )
        passed, _ = assess_v1_result(case, valid_seed)
        self.assertTrue(passed)

    def test_negative_requires_exact_budget_and_no_valid_candidate(self) -> None:
        case = V1Case("n", Path("n.lean"), "negative", 3, "")
        exhausted = result_with(
            success=False,
            model_calls=3,
            stop_reason="iteration_budget",
            records=[record(1, False), record(2, False), record(3, False)],
        )
        passed, _ = assess_v1_result(case, exhausted)
        self.assertTrue(passed)
        too_early = replace(exhausted, metrics=AttemptMetrics(model_calls=2))
        passed, _ = assess_v1_result(case, too_early)
        self.assertFalse(passed)

        missing_lsp = replace(
            exhausted,
            iterations=[
                record(1, False),
                record(2, False, lsp_available=False),
                record(3, False),
            ],
        )
        passed, _ = assess_v1_result(case, missing_lsp)
        self.assertFalse(passed)


if __name__ == "__main__":
    unittest.main()
