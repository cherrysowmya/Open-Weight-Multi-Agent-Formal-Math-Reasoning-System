"""V3.1 contracts: strategy-grounded search and compiler-specific recovery."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from local_lean_agent.config import AppConfig, AgentConfig, InformalReasoningConfig, LeanExploreConfig, _validate
from local_lean_agent.informal.qwen import QwenInformalReasoner, parse_lemma_queries
from local_lean_agent.orchestrator import (ProofAgent, has_rewrite_failure,
    render_strategy_retrieval, rewrite_prefix_candidate, rewrite_prefix_candidates)
from local_lean_agent.types import AttemptMetrics, FailureCategory, InformalTaskPacket, RetrievalHit, RetrievalResult, VerificationResult
from local_lean_agent.verification.kimina import _classify
from tests.test_informal import RecordingBackend
from tests.test_orchestrator import FakeBackend, FakeRetriever
from tests.test_v3_contracts import PASS, FAIL


TASK = "theorem t : 1 = 1 := by sorry"
BAD = "theorem t : 1 = 1 := by\n  -- DISCARDED_BODY_MARKER\n  rw [bad]"
GOOD = "theorem t : 1 = 1 := by rfl"
DIAGNOSTIC = "3:7: error: Invalid rewrite argument: Expected an equality or iff proof"
OUTLINE = ('<informal_proof>Use reflexivity.</informal_proof>'
           '<lemma_queries>["Reflexivity of equality"]</lemma_queries>')


class RewriteVerifier:
    def __init__(self):
        self.calls = []

    def verify(self, code, *, attempt_id):
        self.calls.append(code)
        valid = code.strip().endswith("by rfl")
        diagnostic = ("3:9: error: Function expected at\n  le_rfl"
                      if "le_rfl 1 2" in code else DIAGNOSTIC)
        return VerificationResult(valid=valid, diagnostics=() if valid else (diagnostic,),
            failure_category=FailureCategory.NONE if valid else FailureCategory.TACTIC_FAILURE)


class SalvageVerifier(RewriteVerifier):
    def verify(self, code, *, attempt_id):
        self.calls.append(code)
        valid = code.strip().endswith("nlinarith")
        return VerificationResult(valid=valid, diagnostics=() if valid else (DIAGNOSTIC,),
            failure_category=FailureCategory.NONE if valid else FailureCategory.TACTIC_FAILURE)


class V3HardeningTests(unittest.TestCase):
    def solve(self, responses, *, retriever=None, required=True, task=TASK, **informal_overrides):
        with tempfile.TemporaryDirectory() as directory:
            informal = replace(InformalReasoningConfig(enabled=True, invocation_policy="always"),
                               **informal_overrides)
            config = AppConfig(informal_reasoning=informal,
                lean_explore=LeanExploreConfig(enabled=retriever is not None, required=required),
                agent=AgentConfig(max_iterations=3, fallback_enabled=False,
                                  log_path=Path(directory) / "trace.jsonl"))
            backend = FakeBackend(responses)
            verifier = RewriteVerifier()
            result = ProofAgent(backend, verifier, config, retriever=retriever,
                informal_reasoner=QwenInformalReasoner(backend, informal)).solve(task)
            return result, backend, verifier

    def test_search_queries_are_bounded_and_deduplicated(self):
        queries, error = parse_lemma_queries(
            '<lemma_queries>[" Reflexivity of equality ", "Reflexivity of equality"]</lemma_queries>',
            limit=2, max_chars=240)
        self.assertEqual(queries, ("Reflexivity of equality",))
        self.assertIsNone(error)

    def test_adjacent_one_item_arrays_are_safely_flattened(self):
        queries, error = parse_lemma_queries(
            '<lemma_queries>["First mathematical fact"]\n["Second mathematical fact"]</lemma_queries>',
            limit=2, max_chars=240)
        self.assertEqual(queries, ("First mathematical fact", "Second mathematical fact"))
        self.assertIsNone(error)

    def test_invalid_query_metadata_is_not_executed(self):
        for raw in ('"not a list"', '["one","two","three"]', '[4]', '[""]',
                    '["by\\n  exact h"]', '["' + 'x'*241 + '"]', '{bad json}'):
            with self.subTest(raw=raw):
                queries, error = parse_lemma_queries(f"<lemma_queries>{raw}</lemma_queries>", limit=2, max_chars=240)
                self.assertEqual(queries, ())
                self.assertIsNotNone(error)

    def test_legacy_outline_without_queries_remains_usable(self):
        self.assertEqual(parse_lemma_queries("<informal_proof>Plan.</informal_proof>",
                         limit=2, max_chars=240), ((), None))

    def test_private_thinking_cannot_supply_queries(self):
        text = '<think><lemma_queries>["PRIVATE"]</lemma_queries></think><informal_proof>Public.</informal_proof>'
        self.assertEqual(parse_lemma_queries(text, limit=2, max_chars=240), ((), None))

    def test_final_revision_queries_replace_rejected_draft_queries(self):
        backend = RecordingBackend([OUTLINE, FAIL,
            OUTLINE.replace("Reflexivity of equality", "Revised mathematical fact"), PASS])
        result = QwenInformalReasoner(backend, InformalReasoningConfig()).reason(InformalTaskPacket(TASK))
        self.assertEqual(result.lemma_queries, ("Revised mathematical fact",))
        self.assertNotIn("lemma_queries", backend.requests[3][-1].content)
        self.assertNotIn("Reflexivity of equality", backend.requests[2][-1].content)

    def test_invalid_search_metadata_does_not_fake_informal_pass(self):
        result = QwenInformalReasoner(RecordingBackend([
            OUTLINE.replace('["Reflexivity of equality"]', '"invalid"'), PASS
        ]), InformalReasoningConfig()).reason(InformalTaskPacket(TASK))
        self.assertTrue(result.accepted)
        self.assertEqual(result.lemma_queries, ())
        self.assertIsNotNone(result.drafts[0].query_error)

    def test_approved_query_is_retrieved_once_and_present_in_first_main_prompt(self):
        retriever = FakeRetriever(RetrievalResult(True, "", hits=(RetrievalHit(1, "Eq.refl", source_text="theorem refl ..."),), tool_calls=2))
        result, backend, _ = self.solve([OUTLINE, PASS, BAD, GOOD], retriever=retriever)
        self.assertTrue(result.success)
        self.assertEqual(retriever.queries.count("Reflexivity of equality"), 1)
        self.assertEqual(result.metrics.strategy_retrieval_queries, 1)
        self.assertEqual(result.metrics.retrieval_queries, 2)
        self.assertEqual(result.metrics.retrieval_calls, 4)
        self.assertEqual(result.metrics.model_calls, 2)
        self.assertIn("Eq.refl", backend.requests[2][-1].content)
        self.assertEqual(result.iterations[0].strategy_retrieval[0].query, "Reflexivity of equality")

    def test_rejected_outline_never_triggers_strategy_search(self):
        reject = PASS.replace('"PASS"', '"REJECT"').replace('"issues": []', '"issues": ["False target"]')
        retriever = FakeRetriever(RetrievalResult(True, ""))
        result, _, _ = self.solve([OUTLINE, reject, GOOD], retriever=retriever)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.strategy_retrieval_queries, 0)

    def test_generator_only_ablation_can_search_without_fabricating_review(self):
        retriever = FakeRetriever(RetrievalResult(True, ""))
        result, _, _ = self.solve([OUTLINE, GOOD], retriever=retriever, verifier_enabled=False)
        self.assertEqual(result.metrics.strategy_retrieval_queries, 1)
        self.assertFalse(result.informal_reasoning.accepted)
        self.assertEqual(result.metrics.informal_verifier_calls, 0)

    def test_required_strategy_outage_stops_before_formal_generation(self):
        class Outage(FakeRetriever):
            def retrieve(self, query):
                return RetrievalResult(query != "Reflexivity of equality", query, error_message="offline")
        result, _, _ = self.solve([OUTLINE, PASS], retriever=Outage(RetrievalResult(True, "")))
        self.assertEqual(result.stop_reason, "retrieval_unavailable")
        self.assertEqual(result.metrics.model_calls, 0)
        self.assertFalse(result.success)

    def test_optional_strategy_outage_retains_formal_path(self):
        class Outage(FakeRetriever):
            def retrieve(self, query):
                return RetrievalResult(query != "Reflexivity of equality", query)
        result, _, _ = self.solve([OUTLINE, PASS, GOOD], retriever=Outage(RetrievalResult(True, "")), required=False)
        self.assertTrue(result.success)

    def test_invalid_rewrite_resets_immediately_without_copying_bad_candidate(self):
        result, backend, _ = self.solve([OUTLINE, PASS, BAD, GOOD])
        self.assertTrue(result.success)
        prompt = backend.requests[3][-1].content
        self.assertIn("`rw` needs a proof of equality", prompt)
        self.assertIn("`le_rfl` proves", prompt)
        self.assertIn("`nlinarith [h]`", prompt)
        self.assertIn("Use reflexivity", prompt)
        self.assertNotIn("DISCARDED_BODY_MARKER", prompt)
        self.assertIn("theorem t : 1 = 1 := by", prompt)
        self.assertNotIn("sorry", prompt)
        self.assertEqual(result.iterations[1].repair_action, "invalid_rewrite_reset")
        self.assertEqual(result.metrics.rewrite_recovery_prompts, 1)

    def test_invalid_input_seed_is_also_removed_from_recovery_prompt(self):
        result, backend, _ = self.solve([OUTLINE, PASS, GOOD], task=BAD)
        self.assertTrue(result.success)
        self.assertEqual([record.iteration for record in result.iterations], [0, 1])
        prompt = backend.requests[2][-1].content
        self.assertNotIn("DISCARDED_BODY_MARKER", prompt)
        self.assertNotIn("rw [bad]", prompt)
        self.assertIn("theorem t : 1 = 1 := by", prompt)
        self.assertEqual(result.iterations[1].repair_action, "invalid_rewrite_reset")

    def test_changed_candidate_with_same_rewrite_failure_resets_again(self):
        result, _, verifier = self.solve([OUTLINE, PASS, BAD, BAD + "\n  trivial", GOOD])
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.rewrite_recovery_prompts, 2)
        self.assertGreaterEqual(len(verifier.calls), 3)
        self.assertGreater(result.metrics.rewrite_salvage_checks, 0)

    def test_hardening_off_preserves_old_search_and_repair_behavior(self):
        retriever = FakeRetriever(RetrievalResult(True, ""))
        result, backend, _ = self.solve([OUTLINE, PASS, BAD, GOOD], retriever=retriever,
            strategy_retrieval_enabled=False, rewrite_recovery_enabled=False)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.strategy_retrieval_queries, 0)
        self.assertEqual(result.metrics.rewrite_recovery_prompts, 0)
        self.assertIn("DISCARDED_BODY_MARKER", backend.requests[3][-1].content)
        self.assertNotIn("lemma_queries", backend.requests[0][0].content)

    def test_valid_seed_still_skips_all_informal_work(self):
        result, backend, _ = self.solve([], task=GOOD)
        self.assertTrue(result.success)
        self.assertEqual(backend.calls, 0)

    def test_v2_mode_does_not_activate_v3_rewrite_recovery(self):
        result, backend, _ = self.solve([BAD, GOOD], enabled=False)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.informal_generator_calls, 0)
        self.assertEqual(result.metrics.rewrite_recovery_prompts, 0)
        self.assertIn("DISCARDED_BODY_MARKER", backend.requests[1][-1].content)

    def test_hardening_manifest_has_generation_repair_and_negative_controls(self):
        from local_lean_agent.v3_suite import load_v3_cases
        cases = load_v3_cases("benchmarks/v3/hardening.toml")
        self.assertEqual([case.mode for case in cases], ["generation", "repair", "negative"])
        self.assertEqual([case.max_rounds for case in cases], [3, 3, 2])

    def test_strategy_evidence_precedes_task_noise_without_duplicates(self):
        fact = RetrievalHit(1, "fact", source_text="lemma fact : True")
        noise = RetrievalHit(2, "irrelevant")
        text = render_strategy_retrieval(RetrievalResult(True, "task", (noise, fact)),
            (RetrievalResult(True, "plan", (fact,)),), 1000)
        self.assertLess(text.index("exact_name=fact"), text.index("exact_name=irrelevant"))
        self.assertEqual(text.count("exact_name=fact"), 1)
        self.assertLessEqual(len(render_strategy_retrieval(None,
            (RetrievalResult(True, "plan", (fact,)),), 50)), 50)

    def test_invalid_rewrite_has_a_specific_failure_category(self):
        self.assertEqual(_classify(DIAGNOSTIC), FailureCategory.TACTIC_FAILURE)
        self.assertTrue(has_rewrite_failure((DIAGNOSTIC,)))
        self.assertTrue(has_rewrite_failure(("Tactic `rewrite` failed: pattern absent",)))
        self.assertFalse(has_rewrite_failure(("linarith failed",)))

    def test_rewrite_prefix_candidate_discards_failing_suffix(self):
        candidate = "theorem t : 1 = 1 := by\n  have h : True := by trivial\n  rw [h]\n  exact le_rfl\n"
        salvaged = rewrite_prefix_candidate(candidate,
            ("3:6: error: Invalid rewrite argument",), "nlinarith")
        self.assertEqual(salvaged,
            "theorem t : 1 = 1 := by\n  have h : True := by trivial\n  nlinarith\n")
        self.assertIsNone(rewrite_prefix_candidate(candidate,
            ("error: Invalid rewrite argument",), "nlinarith"))
        self.assertIsNone(rewrite_prefix_candidate(candidate,
            ("2:2: error: linarith failed",), "nlinarith"))

    def test_prefix_salvage_repairs_an_earlier_bad_inline_have_proof(self):
        candidate = ("theorem t (x : ℝ) : 0 ≤ x^2 := by\n"
                     "  have h : 0 ≤ x^2 := bad_term\n"
                     "  rw [h]\n"
                     "  exact h\n")
        variants = rewrite_prefix_candidates(candidate, (
            "2:22: error: Type mismatch",
            "3:6: error: Invalid rewrite argument",
        ), "nlinarith")
        self.assertEqual(variants[0], ("repair_have_positivity",
            "theorem t (x : ℝ) : 0 ≤ x^2 := by\n"
            "  have h : 0 ≤ x^2 := by positivity\n"
            "  nlinarith\n"))
        self.assertNotIn("bad_term", variants[0][1])
        self.assertNotIn("rw [h]", variants[0][1])

    def test_verified_prefix_salvage_uses_no_extra_model_round(self):
        with tempfile.TemporaryDirectory() as directory:
            informal = InformalReasoningConfig(enabled=True, invocation_policy="always")
            config = AppConfig(informal_reasoning=informal,
                agent=AgentConfig(max_iterations=3, fallback_enabled=False,
                                  log_path=Path(directory) / "trace.jsonl"))
            backend = FakeBackend([OUTLINE, PASS, BAD])
            verifier = SalvageVerifier()
            result = ProofAgent(backend, verifier, config,
                informal_reasoner=QwenInformalReasoner(backend, informal)).solve(TASK)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.model_calls, 1)
        self.assertEqual(result.metrics.rewrite_salvage_checks, 1)
        self.assertEqual(result.metrics.rewrite_salvage_successes, 1)
        self.assertEqual(result.iterations[-1].generation.model, "compiler_prefix_salvage")
        self.assertEqual(result.iterations[-1].repair_action, "rewrite_prefix_salvage")
        self.assertEqual(result.fallback_attempts[-1].tactic,
                         "rewrite_prefix:clean_prefix:nlinarith")
        self.assertNotIn("rw [bad]", result.final_proof)

    def test_unverified_prefix_salvage_cannot_create_success(self):
        result, _, _ = self.solve([OUTLINE, PASS, BAD, GOOD],
            rewrite_salvage_tactics=("simp_all",))
        self.assertTrue(result.success)
        self.assertGreaterEqual(result.metrics.rewrite_salvage_checks, 1)
        self.assertEqual(result.metrics.rewrite_salvage_successes, 0)

    def test_salvage_is_disabled_with_rewrite_hardening(self):
        result, _, _ = self.solve([OUTLINE, PASS, BAD, GOOD],
            rewrite_recovery_enabled=False)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.rewrite_salvage_checks, 0)

    def test_salvage_toggle_preserves_prompt_hardening(self):
        result, _, _ = self.solve([OUTLINE, PASS, BAD, GOOD], rewrite_salvage_enabled=False)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.rewrite_recovery_prompts, 1)
        self.assertEqual(result.metrics.rewrite_salvage_checks, 0)

    def test_salvage_budget_is_per_theorem_not_per_round(self):
        result, _, _ = self.solve([OUTLINE, PASS, BAD, BAD + "\n  trivial", GOOD],
                                  rewrite_salvage_max_checks=2)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.rewrite_salvage_checks, 2)
        self.assertEqual(result.metrics.model_calls, 3)

    def test_invalid_diagnostic_locations_fail_closed(self):
        for location in (0, 999):
            self.assertEqual(rewrite_prefix_candidates(BAD, (
                f"{location}:1: error: Type mismatch", DIAGNOSTIC), "nlinarith"), ())

    def test_salvage_cannot_drop_a_required_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(informal_reasoning=InformalReasoningConfig(enabled=True),
                agent=AgentConfig(log_path=Path(directory) / "trace.jsonl"))
            verifier = SalvageVerifier()
            agent = ProofAgent(FakeBackend([]), verifier, config)
            task = TASK + "\ntheorem still_required : False := by sorry"
            metrics = AttemptMetrics()
            result = agent._try_rewrite_prefix_salvage(task, BAD,
                VerificationResult(False, diagnostics=(DIAGNOSTIC,)),
                attempt_id="integrity", iteration=1, metrics=metrics, attempts=[], attempted=set())
            self.assertIsNone(result)
            self.assertEqual(verifier.calls, [])
            self.assertEqual(metrics.rewrite_salvage_checks, 0)

    def test_generic_repair_warns_against_function_and_reflexivity_misuse(self):
        result, backend, _ = self.solve([OUTLINE, PASS, BAD,
            BAD.replace("rw [bad]", "exact le_rfl 1 2"), GOOD])
        self.assertTrue(result.success)
        repair = backend.requests[4][-1].content
        self.assertIn("Function expected", repair)
        self.assertIn("`le_rfl` only proves", repair)
        self.assertIn("`nlinarith [h]`", repair)

    def test_strategy_query_configuration_is_bounded(self):
        for overrides in ({"strategy_query_limit": 0}, {"strategy_query_limit": 4},
                          {"strategy_query_max_chars": 0}, {"strategy_query_max_chars": 501},
                          {"rewrite_salvage_tactics": ()},
                          {"rewrite_salvage_max_checks": 0},
                          {"rewrite_salvage_max_checks": 17},
                          {"rewrite_salvage_tactics": ("simp",) * 5},
                          {"rewrite_salvage_tactics": ("bad\ntactic",)}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                _validate(AppConfig(informal_reasoning=replace(InformalReasoningConfig(), **overrides)))


if __name__ == "__main__":
    unittest.main()
