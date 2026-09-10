"""Offline benchmark contracts; model quality is measured by minif2f-run."""
from contextlib import ExitStack
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_lean_agent.benchmark import run_benchmark, variant_config
from local_lean_agent.cli import build_parser
from local_lean_agent.config import AppConfig, LeanLSPConfig, load_config
from local_lean_agent.minif2f import (
    DEFAULT_DATA, REVISION, MiniF2FCase, load_dataset, normalize_source,
    prepare_dataset, select_cases, sha, statement_probe,
)
from local_lean_agent.types import AttemptMetrics, AttemptResult, FailureCategory, VerificationResult
from local_lean_agent.verification.kimina import KiminaVerifier

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / DEFAULT_DATA


def case(name="fixture"):
    source = f"import Mathlib\n\ntheorem {name} (n : ℕ) : n = n := by\n  sorry\n"
    return MiniF2FCase(name, "valid", source, sha(source), "upstream")


def solved(source):
    return AttemptResult(attempt_id="mock-attempt", theorem=source, success=True,
        final_proof=source.replace("sorry", "rfl"), failure_category=FailureCategory.NONE,
        stop_reason="verified", error_message=None, iterations=[],
        metrics=AttemptMetrics(model_calls=1, formal_call_attempts=1))


class MiniF2FDataTests(unittest.TestCase):
    def test_pinned_splits_are_disjoint_and_complete(self):
        valid, test = load_dataset(DATA, "valid"), load_dataset(DATA, "test")
        self.assertEqual((len(valid), len(test)), (244, 244))
        self.assertFalse({c.case_id for c in valid} & {c.case_id for c in test})
        for entry in valid + test:
            probe = statement_probe(entry)
            self.assertNotIn("sorry", probe)
            self.assertNotIn("theorem ", probe)
            self.assertIn(" : Prop := ", probe)
            self.assertEqual(entry.source.count("sorry"), 1)
            self.assertNotIn("import MiniF2F", entry.source)

    def test_prepare_existing_is_offline_and_idempotent(self):
        with patch("local_lean_agent.minif2f.urlopen", side_effect=AssertionError("network")):
            self.assertEqual(prepare_dataset(DATA)["revision"], REVISION)

    def test_deterministic_selection_and_full_split(self):
        cases = load_dataset(DATA, "valid")
        self.assertEqual(select_cases(cases), select_cases(list(reversed(cases))))
        self.assertNotEqual(select_cases(cases, seed=1), select_cases(cases, seed=2))
        self.assertEqual(len(select_cases(cases, limit=0)), 244)

    def test_explicit_ids_keep_order_and_cannot_cross_splits(self):
        cases = load_dataset(DATA, "valid")
        ids = tuple(c.case_id for c in cases[:2])
        self.assertEqual([c.case_id for c in select_cases(cases, ids=ids, limit=1)], list(ids))
        for kwargs in ({"ids": (ids[0], ids[0])}, {"ids": ("unknown",)}, {"limit": -1}):
            with self.assertRaises(ValueError):
                select_cases(cases, **kwargs)

    def test_rejects_changed_source(self):
        manifest = json.loads((DATA / "manifest.json").read_text())
        entry = next(e for e in manifest["cases"] if e["split"] == "valid")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(manifest))
            (root / "valid").mkdir()
            (root / entry["path"]).write_text("changed")
            with self.assertRaisesRegex(ValueError, "file changed"):
                load_dataset(root, "valid")

    def test_rejects_provenance_change(self):
        manifest = json.loads((DATA / "manifest.json").read_text())
        manifest["revision"] = "another-revision"
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "provenance"):
                load_dataset(Path(directory), "valid")

    def test_narrow_import_format_rejects_solution_material(self):
        raw = ("import Mathlib\nset_option maxHeartbeats 0\n"
               "open BigOperators Real Nat Topology Rat\ntheorem fixture : True := by sorry")
        self.assertIn("maxHeartbeats 200000", normalize_source(raw, "fixture"))
        for altered in (raw.replace("Mathlib", "MiniF2F.Test"), raw + "\naxiom cheat : False",
                        raw.replace("by sorry", "by trivial")):
            with self.assertRaises(ValueError):
                normalize_source(altered, "fixture")

    def test_probe_keeps_hypotheses_and_target(self):
        c = case()
        c = replace(c, source="theorem fixture {α : Type} [Add α] (n : α) (h : n = n) : n = n := by\n  sorry")
        self.assertEqual(statement_probe(c),
            "def fixture_statement {α : Type} [Add α] (n : α) (h : n = n)  : Prop := n = n\n")

    def test_cli_defaults_are_small_and_validation_split_only(self):
        args = build_parser().parse_args(["minif2f-run"])
        self.assertEqual((args.limit, args.split, args.attempts, args.max_rounds), (3, "valid", 1, 3))

    def test_comment_colon_is_not_the_result_type(self):
        entry = next(c for c in load_dataset(DATA, "valid") if c.case_id == "amc12b_2002_p3")
        probe = statement_probe(entry)
        self.assertIn(" : Prop := S.card = 1", probe)
        self.assertIn("-- note: we use", probe)


class MiniF2FRunnerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.output = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / "run.json"
        self.config = AppConfig(lean_lsp=LeanLSPConfig(enabled=True))
        self.cases = [case("first"), case("second")]
        self.stack.enter_context(patch("local_lean_agent.benchmark.load_dataset", return_value=self.cases))
        self.stack.enter_context(patch("local_lean_agent.benchmark.environment_metadata", return_value={"toolchain": "test"}))
        self.verifier = Mock(health_check=Mock(return_value=True), verify=Mock(return_value=VerificationResult(True)))
        self.audit = Mock(verify=Mock(return_value=VerificationResult(True)))
        self.verifier_factory = self.stack.enter_context(patch("local_lean_agent.benchmark.KiminaVerifier",
            side_effect=lambda cfg: self.verifier if cfg.reuse_repl else self.audit))
        self.backend = self.stack.enter_context(patch("local_lean_agent.benchmark.MLXBackend")).return_value
        self.backend.unload_model.return_value = 0.1
        self.feedback = self.stack.enter_context(patch("local_lean_agent.benchmark.LeanLSPMCPClient")).return_value
        self.stack.enter_context(patch("local_lean_agent.benchmark.LeanExploreMCPClient"))
        self.agent = self.stack.enter_context(patch("local_lean_agent.benchmark.ProofAgent")).return_value
        self.agent.solve.side_effect = solved

    def run_benchmark(self, **kwargs):
        return run_benchmark(self.config, data=DATA, split="valid", output=self.output,
                             variants=("lean",), ids=tuple(c.case_id for c in self.cases), **kwargs)

    def test_variant_boundaries_and_fallbacks_disabled(self):
        for variant in ("lean", "v2", "v3", "v4"):
            cfg = variant_config(self.config, variant, 3)
            self.assertEqual(cfg.v4.enabled, variant == "v4")
            self.assertEqual(cfg.informal_reasoning.enabled, variant in {"v3", "v4"})
            self.assertEqual(cfg.lean_explore.enabled, variant != "lean")
            self.assertFalse(cfg.agent.fallback_enabled)
            self.assertFalse(cfg.informal_reasoning.rewrite_salvage_enabled)
            self.assertFalse(cfg.agent.unload_model_after_attempt)

    def test_every_success_is_independently_rechecked_and_proof_saved(self):
        result = self.run_benchmark()
        self.assertTrue(result["experiment_complete"])
        self.assertEqual(result["summary"]["lean"]["verified_problems"], 2)
        self.assertEqual(self.audit.verify.call_count, 2)
        self.assertFalse(self.verifier_factory.call_args_list[1].args[0].reuse_repl)
        self.assertTrue(Path(result["immutable_artifact"]).is_file())
        self.assertTrue(all(Path(r["proof_path"]).is_file() for r in result["attempts"]))
        self.backend.unload_model.assert_called_once()
        self.feedback.close.assert_called_once()

    def test_audit_failure_cannot_count_as_success(self):
        self.audit.verify.return_value = VerificationResult(False, failure_category=FailureCategory.UNSAFE_PLACEHOLDER)
        result = self.run_benchmark()
        self.assertTrue(result["experiment_complete"])
        self.assertEqual(result["summary"]["lean"]["verified_problems"], 0)
        self.assertTrue(all(r["status"] == "audit_failed" for r in result["attempts"]))
        self.assertTrue(all("proof_path" not in r for r in result["attempts"]))

    def test_changed_target_cannot_pass_even_with_audit_acceptance(self):
        self.agent.solve.side_effect = lambda source: replace(solved(source), final_proof="theorem other : True := by trivial")
        result = self.run_benchmark()
        self.assertFalse(any(r["verified"] for r in result["attempts"]))
        self.assertTrue(all(r["integrity_error"] for r in result["attempts"]))

    def test_incompatible_statement_counts_in_denominator_without_model(self):
        self.verifier.verify.return_value = VerificationResult(False, failure_category=FailureCategory.UNKNOWN_IDENTIFIER)
        result = self.run_benchmark()
        self.assertEqual(result["summary"]["lean"]["selected_problems"], 2)
        self.assertEqual(result["summary"]["lean"]["statuses"], {"incompatible_statement": 2})
        self.agent.solve.assert_not_called()

    def test_preflight_outage_is_retryable_and_preserves_diagnostics(self):
        self.verifier.verify.return_value = VerificationResult(False, failure_category=FailureCategory.VERIFIER_UNAVAILABLE)
        result = self.run_benchmark()
        self.assertFalse(result["experiment_complete"])
        self.assertEqual(result["attempts"], [])
        self.assertTrue(result["preflight_errors"])
        self.verifier.verify.return_value = VerificationResult(True)
        self.assertTrue(self.run_benchmark(resume=True)["experiment_complete"])

    def test_interrupt_resume_skips_completed_attempts(self):
        self.agent.solve.side_effect = [solved(self.cases[0].source), KeyboardInterrupt()]
        partial = self.run_benchmark()
        self.assertFalse(partial["experiment_complete"])
        self.assertEqual(len(partial["attempts"]), 1)
        self.agent.solve.side_effect = solved
        self.agent.solve.reset_mock()
        final = self.run_benchmark(resume=True)
        self.assertTrue(final["experiment_complete"])
        self.agent.solve.assert_called_once_with(self.cases[1].source)

    def test_complete_resume_needs_no_service_calls(self):
        self.run_benchmark()
        self.verifier.health_check.reset_mock()
        self.run_benchmark(resume=True)
        self.verifier.health_check.assert_not_called()

    def test_no_overwrite_and_no_resume_with_changed_spec(self):
        self.run_benchmark()
        with self.assertRaisesRegex(ValueError, "Output exists"):
            self.run_benchmark()
        with self.assertRaisesRegex(ValueError, "Resume rejected"):
            self.run_benchmark(resume=True, seed=99)

    def test_duplicate_checkpoint_attempts_rejected(self):
        result = self.run_benchmark()
        result["attempts"].append(result["attempts"][0])
        self.output.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError, "checkpoint attempt keys"):
            self.run_benchmark(resume=True)

    def test_infrastructure_failure_stops_without_inflating_success(self):
        self.agent.solve.side_effect = lambda source: replace(solved(source), success=False,
            failure_category=FailureCategory.MODEL_UNAVAILABLE, stop_reason="runtime_error")
        result = self.run_benchmark()
        self.assertFalse(result["experiment_complete"])
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["summary"]["lean"]["statuses"], {"infrastructure_error": 1})

    def test_cleanup_error_still_saves_checkpoint(self):
        self.feedback.close.side_effect = RuntimeError("cleanup failed")
        result = self.run_benchmark()
        self.assertTrue(result["sessions"][-1]["cleanup_errors"])
        self.assertTrue(json.loads(self.output.read_text())["experiment_complete"])

    def test_repetitions_count_unique_problems_not_attempts(self):
        result = self.run_benchmark(attempts=2)
        summary = result["summary"]["lean"]
        self.assertEqual(summary["completed_attempts"], 4)
        self.assertEqual(summary["verified_problems"], 2)
        self.assertEqual(summary["first_attempt_verified"], 2)
        self.assertEqual(summary["observed_solve_within_k_rate"], 1.0)

    def test_output_lock_prevents_concurrent_writers(self):
        with self.output.with_suffix(".json.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "Another process"):
                self.run_benchmark()

    def test_time_budget_stops_before_starting_an_attempt(self):
        with patch("local_lean_agent.benchmark.time.monotonic", side_effect=[0, 2, 3]):
            result = self.run_benchmark(max_seconds=1)
        self.assertFalse(result["experiment_complete"])
        self.agent.solve.assert_not_called()
        self.assertEqual(result["sessions"][-1]["stop_reason"], "session_time_budget")

    def test_nonfinite_budget_rejected(self):
        with self.assertRaises(ValueError):
            self.run_benchmark(max_seconds=float("nan"))


@unittest.skipUnless(os.environ.get("RUN_MINIF2F_LEAN_TESTS") == "1", "requires local Kimina")
class MiniF2FLiveLeanTests(unittest.TestCase):
    def setUp(self):
        config = load_config(ROOT / "config/local.toml")
        self.verifier = KiminaVerifier(replace(config.kimina, reuse_repl=False))
        self.assertTrue(self.verifier.health_check())

    def test_real_audit_accepts_closed_arithmetic_proof(self):
        entry = next(c for c in load_dataset(DATA, "valid") if c.case_id == "mathd_algebra_10")
        # Test-only reference, never sent to the generator or retrieval index.
        result = self.verifier.verify(entry.source.replace("sorry", "norm_num"), attempt_id="minif2f-live-audit")
        self.assertTrue(result.valid, result.diagnostics)

    def test_unproved_benchmark_statement_is_never_success(self):
        entry = load_dataset(DATA, "valid")[0]
        result = self.verifier.verify(entry.source, attempt_id="minif2f-live-placeholder")
        self.assertFalse(result.valid)
        self.assertEqual(result.failure_category, FailureCategory.UNSAFE_PLACEHOLDER)

    def test_comment_bearing_statement_elaborates(self):
        entry = next(c for c in load_dataset(DATA, "valid") if c.case_id == "amc12b_2002_p3")
        result = self.verifier.verify(statement_probe(entry), attempt_id="minif2f-live-comment")
        self.assertTrue(result.valid, result.diagnostics)


if __name__ == "__main__":
    unittest.main()
