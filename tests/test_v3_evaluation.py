from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_lean_agent.cli import _run_v3_ablation, build_parser
from local_lean_agent.config import AppConfig, LeanExploreConfig, LeanLSPConfig
from local_lean_agent.types import InformalVerdict
from local_lean_agent.v3_suite import (
    V3Case, attempt_measurements, comparison_summary, summarize_condition,
)
from tests.test_v3_suite import result


class V3EvaluationTests(unittest.TestCase):
    def payload(self, attempt, case=None):
        case = case or V3Case("one", Path("unused"), "generation", 3, "")
        return {"id": case.case_id, "repetition": 1, "mode": case.mode,
                "attempt": attempt.to_dict(), "measurements": attempt_measurements(case, attempt)}

    def test_informal_pass_downstream_failure_is_a_separate_proxy(self):
        failed = result(success=False, informal=True)
        succeeded = result(success=True, informal=True)
        summary = summarize_condition([self.payload(failed), self.payload(succeeded)])
        self.assertEqual(summary["informal_accepted_cases"], 2)
        self.assertEqual(summary["positive_successes"], 1)
        self.assertEqual(summary["informal_pass_downstream_failure_rate"], 0.5)
        self.assertEqual(summary["informal_pass_downstream_failures"], 1)

    def test_zero_approved_plans_have_undefined_not_zero_failure_rate(self):
        summary = summarize_condition([self.payload(result(success=False))])
        self.assertIsNone(summary["informal_pass_downstream_failure_rate"])
        self.assertIsNone(summary["revision_next_pass_rate"])

    def test_revision_effectiveness_separates_review_pass_from_lean_success(self):
        attempt = result(success=False, informal=True)
        accepted = replace(attempt.informal_reasoning.reviews[0], refinement_round=2)
        failed = replace(accepted, refinement_round=1, verdict=InformalVerdict.REVISE)
        attempt.informal_reasoning = replace(attempt.informal_reasoning,
                                             rounds=2, reviews=(failed, accepted))
        summary = summarize_condition([self.payload(attempt)])
        self.assertEqual(summary["revision_requests"], 1)
        self.assertEqual(summary["revision_next_pass_rate"], 1.0)
        self.assertEqual(summary["revised_plan_lean_successes"], 0)

    def test_costs_include_all_roles(self):
        attempt = result(success=True, informal=True)
        attempt.metrics.completion_tokens = 90
        attempt.metrics.kimina_checks = 2
        summary = summarize_condition([self.payload(attempt)])
        self.assertEqual(summary["total_model_calls"], 3)
        self.assertEqual(summary["total_generated_tokens"], 110)
        self.assertEqual(summary["average_lean_checks"], 2.0)

    def test_first_pass_and_successful_repair_are_different(self):
        attempt = result(success=True, informal=True)
        rejected = replace(attempt.iterations[0], iteration=1,
            verification=replace(attempt.iterations[0].verification, valid=False))
        attempt.iterations[0] = replace(attempt.iterations[0], iteration=2)
        attempt.iterations.insert(0, rejected)
        summary = summarize_condition([self.payload(attempt)])
        self.assertEqual(summary["first_pass_successes"], 0)
        self.assertEqual(summary["successful_formal_repairs"], 1)

    def test_timeout_metric_does_not_misclassify_generic_runtime_errors(self):
        for error, expected in (
            ("RuntimeError: malformed response", False),
            ("ValueError: estimated context too long", False),
            ("TimeoutError: model did not respond", True),
            ("HTTPClientError: request timed out", True),
        ):
            with self.subTest(error=error):
                attempt = result(success=False)
                attempt.stop_reason = "runtime_error"
                attempt.error_message = error
                self.assertEqual(self.payload(attempt)["measurements"]["timeout"], expected)
                attempt = result(success=False, informal=True)
                attempt.informal_reasoning = replace(attempt.informal_reasoning,
                    stop_reason="runtime_error", error_message=error)
                self.assertEqual(self.payload(attempt)["measurements"]["timeout"], expected)

    def test_mismatched_pairs_are_not_silently_compared(self):
        first = self.payload(result(success=False))
        second = dict(self.payload(result(success=True)), id="different")
        with self.assertRaisesRegex(ValueError, "matching case IDs"):
            comparison_summary([first], [second])

    def test_cli_supports_generator_only_control_without_changing_local_config(self):
        args = build_parser().parse_args(["v3-ablation", "--control", "generator-only",
                                          "--informal-policy", "always"])
        self.assertEqual(args.control, "generator-only")
        self.assertEqual(args.informal_policy, "always")

    def test_paired_runner_changes_only_treatment_components(self):
        config = AppConfig(lean_lsp=LeanLSPConfig(enabled=True, required=True),
                           lean_explore=LeanExploreConfig(enabled=True, required=True))
        for control in ("v2", "generator-only"):
            with self.subTest(control=control), tempfile.TemporaryDirectory() as directory:
                factory = Mock()
                factory.return_value.solve.return_value = result(success=True)
                with patch("local_lean_agent.cli.ProofAgent", factory), \
                     patch("local_lean_agent.cli.MLXBackend") as backend, \
                     patch("local_lean_agent.cli.KiminaVerifier"), \
                     patch("local_lean_agent.cli._feedback_provider"), \
                     patch("local_lean_agent.cli._semantic_retriever"), \
                     patch("local_lean_agent.cli._write_attempt_result"), \
                     patch("local_lean_agent.cli.validate_v3_cases", return_value={"passed": True}), \
                     patch("local_lean_agent.cli.write_json"), patch("builtins.print"):
                    backend.return_value.unload_model.return_value = 0.0
                    _run_v3_ablation(config, Path("benchmarks/v3/smoke.toml"),
                                    Path(directory) / "run.json", control=control)
                calls = factory.call_args_list
                self.assertEqual(len(calls), 4)
                for call in calls:
                    self.assertIs(call.args[0], backend.return_value)
                    effective = call.args[2]
                    self.assertEqual(effective.generation, config.generation)
                    self.assertEqual(effective.lean_explore, config.lean_explore)
                    self.assertEqual(effective.lean_lsp, config.lean_lsp)
                    self.assertFalse(effective.agent.fallback_enabled)
                    self.assertFalse(effective.informal_reasoning.rewrite_salvage_enabled)
                    self.assertFalse(effective.v4.enabled)
                treatment, baseline = calls[0].args[2], calls[1].args[2]
                self.assertTrue(treatment.informal_reasoning.enabled)
                self.assertTrue(treatment.informal_reasoning.verifier_enabled)
                self.assertEqual(baseline.informal_reasoning.enabled, control == "generator-only")
                self.assertFalse(baseline.informal_reasoning.verifier_enabled)


if __name__ == "__main__":
    unittest.main()
