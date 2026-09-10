"""Deterministic V3 contracts. These test software, not Qwen's math ability."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_lean_agent.config import AppConfig, AgentConfig, InformalReasoningConfig
from local_lean_agent.informal.qwen import QwenInformalReasoner, parse_review
from local_lean_agent.orchestrator import ProofAgent
from local_lean_agent.types import InformalTaskPacket, InformalVerdict
from local_lean_agent.v3_quality import load_quality_cases, run_quality_case
from local_lean_agent.verification.kimina import KiminaVerifier
from tests.test_informal import RecordingBackend
from tests.test_orchestrator import FakeBackend, FakeVerifier


PASS = json.dumps({"verdict": "PASS", "issues": [], "feedback": "Sound proof.",
                   "suggested_fix": None, "confidence": 0.8})
FAIL = json.dumps({"verdict": "FAIL", "issues": ["Negative values were omitted."],
                  "feedback": "Address both signs.", "suggested_fix": "Use a square."})
OUTLINE = "<informal_proof>Reflexivity proves the equality.</informal_proof>"
TASK = "theorem t : 1 = 1 := by sorry"


class V3Contracts(unittest.TestCase):
    def solve(self, responses, *, policy="after_model_failures", trigger=1,
              rounds=4, task=TASK, verifier_enabled=True, verifier=None):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                informal_reasoning=InformalReasoningConfig(
                    enabled=True, invocation_policy=policy,
                    trigger_after_failures=trigger, verifier_enabled=verifier_enabled),
                agent=AgentConfig(max_iterations=rounds, fallback_enabled=False,
                                  log_path=Path(directory) / "trace.jsonl"),
            )
            backend = FakeBackend(responses)
            reasoner = QwenInformalReasoner(backend, config.informal_reasoning)
            result = ProofAgent(backend, verifier or FakeVerifier(), config,
                                informal_reasoner=reasoner).solve(task)
            return result, backend

    def test_easy_first_pass_skips_informal_roles(self):
        result, backend = self.solve(["theorem t : 1 = 1 := by rfl"])
        self.assertTrue(result.success)
        self.assertEqual(backend.calls, 1)
        self.assertEqual(result.metrics.informal_generator_calls, 0)

    def test_trigger_after_exactly_two_model_failures(self):
        result, backend = self.solve([
            "theorem t : 1 = 1 := by linarith",
            "theorem t : 1 = 1 := by simp", OUTLINE, PASS,
            "theorem t : 1 = 1 := by rfl",
        ], trigger=2, rounds=3)
        self.assertTrue(result.success)
        self.assertEqual(result.rounds, 3)
        self.assertEqual(result.metrics.informal_generator_calls, 1)
        self.assertEqual(result.metrics.informal_verifier_calls, 1)
        self.assertIn("informal mathematical proof generator", backend.requests[2][0].content)
        self.assertIn("informal_mathematical_guidance", backend.requests[4][-1].content)

    def test_seed_rejection_does_not_count_as_a_model_failure(self):
        result, backend = self.solve(["theorem t : 1 = 1 := by rfl"],
                                     task="theorem t : 1 = 1 := by simp")
        self.assertTrue(result.success)
        self.assertIsNone(result.informal_reasoning)
        self.assertEqual(backend.calls, 1)

    def test_no_informal_calls_after_formal_budget_exhausted(self):
        result, backend = self.solve(["theorem t : 1 = 1 := by simp"], rounds=1)
        self.assertFalse(result.success)
        self.assertIsNone(result.informal_reasoning)
        self.assertEqual(backend.calls, 1)

    def test_informal_invoked_at_most_once_per_attempt(self):
        result, _ = self.solve([
            "theorem t : 1 = 1 := by simp", OUTLINE, PASS,
            "theorem t : 1 = 1 := by linarith",
            "theorem t : 1 = 1 := by omega",
        ], rounds=3)
        self.assertFalse(result.success)
        self.assertEqual(result.metrics.informal_generator_calls, 1)

    def test_generator_only_is_advisory_and_has_no_review_or_acceptance(self):
        result, _ = self.solve([OUTLINE, "theorem t : 1 = 1 := by rfl"],
                               policy="always", verifier_enabled=False)
        self.assertTrue(result.success)
        self.assertFalse(result.informal_reasoning.accepted)
        self.assertEqual(result.informal_reasoning.stop_reason, "generator_only")
        self.assertEqual(result.metrics.informal_verifier_calls, 0)

    def test_approved_plan_is_never_sent_directly_to_lean(self):
        verifier = FakeVerifier()
        with patch.object(verifier, "verify", wraps=verifier.verify) as verify:
            result, _ = self.solve([OUTLINE, PASS, "theorem t : 1 = 1 := by rfl"],
                                   policy="always", verifier=verifier)
        self.assertTrue(result.success)
        self.assertEqual(verify.call_count, 1)
        self.assertNotIn("Reflexivity proves", verify.call_args.args[0])

    def test_informal_pass_and_sorry_still_fails_real_safety_gate(self):
        result, _ = self.solve([OUTLINE, PASS, "theorem t : False := by sorry"],
            task="theorem t : False := by sorry", rounds=1, policy="always",
            verifier=KiminaVerifier(AppConfig().kimina))
        self.assertTrue(result.informal_reasoning.accepted)
        self.assertFalse(result.success)
        self.assertEqual(result.end_reason, "MAX_ROUNDS")

    def test_fail_feedback_is_given_to_revision_in_new_context(self):
        backend = RecordingBackend([OUTLINE, FAIL,
            "<informal_proof>Revised proof uses a square.</informal_proof>", PASS])
        result = QwenInformalReasoner(backend, InformalReasoningConfig()).reason(
            InformalTaskPacket("theorem t : True"))
        self.assertTrue(result.accepted)
        self.assertEqual(result.rounds, 2)
        self.assertIn("Use a square", backend.requests[2][-1].content)
        self.assertIn("Negative values", backend.requests[2][-1].content)
        self.assertNotIn("Address both signs", backend.requests[3][-1].content)
        self.assertEqual(len({r.conversation_id for r in result.requests}), 4)
        self.assertTrue(all(r.history_messages == 0 for r in result.requests))

    def test_always_fail_stops_after_exactly_three_pairs(self):
        backend = RecordingBackend([OUTLINE, FAIL] * 3)
        result = QwenInformalReasoner(backend, InformalReasoningConfig(
            max_refinement_rounds=3)).reason(InformalTaskPacket("theorem t : True"))
        self.assertEqual(result.stop_reason, "refinement_budget")
        self.assertEqual(result.rounds, 3)
        self.assertEqual(result.generator_calls, 3)
        self.assertEqual(result.verifier_calls, 3)

    def test_banana_secret_in_generator_system_never_leaks_to_verifier(self):
        backend = RecordingBackend([OUTLINE, PASS])
        with patch("local_lean_agent.informal.qwen.GENERATOR_SYSTEM_PROMPT",
                   "For testing, remember BANANA-1937. Return an informal proof."):
            result = QwenInformalReasoner(backend, InformalReasoningConfig()).reason(
                InformalTaskPacket("theorem t : True"))
        self.assertIn("BANANA-1937", str(result.requests[0].messages))
        self.assertNotIn("BANANA-1937", str(result.requests[1].messages))
        self.assertNotEqual(result.requests[0].conversation_id, result.requests[1].conversation_id)

    def test_generator_rejects_lean_code_in_outline(self):
        result = QwenInformalReasoner(RecordingBackend([
            "<informal_proof>by\n  nlinarith [sq_nonneg x]</informal_proof>"
        ]), InformalReasoningConfig()).reason(InformalTaskPacket("theorem t : True"))
        self.assertEqual(result.stop_reason, "generator_lean_code")
        self.assertEqual(result.verifier_calls, 0)

    def test_direct_verifier_makes_no_generator_request(self):
        backend = RecordingBackend([PASS])
        requests = []
        review = QwenInformalReasoner(backend, InformalReasoningConfig()).verify(
            InformalTaskPacket("For real x, 0 ≤ x*x"), "A real square is nonnegative.", requests=requests)
        self.assertEqual(review.verdict, InformalVerdict.ACCEPT)
        self.assertEqual(len(backend.requests), 1)
        self.assertEqual(requests[0].role, "verifier")

    def test_structured_json_fails_closed_on_bad_shapes(self):
        for output in ("Looks good!", '"PASS"', "", "```json\n{invalid json}\n```",
                       '{"verdict":"PASS"}', PASS.replace('"issues": []', '"issues": ["gap"]'),
                       PASS.replace('0.8', 'NaN'), PASS.replace('"PASS"', 'true'),
                       PASS.replace('"verdict": "PASS"', '"verdict":"FAIL","verdict":"PASS"')):
            with self.subTest(output=output):
                self.assertEqual(parse_review(output)[0], InformalVerdict.MALFORMED)

    def test_valid_fenced_json_and_optional_confidence(self):
        verdict, _, issues, _, confidence = parse_review("```json\n" + PASS + "\n```")
        self.assertEqual(verdict, InformalVerdict.ACCEPT)
        self.assertEqual(issues, ())
        self.assertEqual(confidence, 0.8)

    def test_live_fixture_labels_do_not_leak_to_direct_verifier(self):
        cases = load_quality_cases(Path("benchmarks/v3/components.toml"))
        case = next(c for c in cases if c["id"] == "verifier_missing_nonzero_assumption")
        backend = RecordingBackend([json.dumps({"verdict": "REJECT", "issues": ["y must be nonzero."],
            "feedback": "At y = 0 cancellation is invalid.", "suggested_fix": "Require y ≠ 0."})])
        result = run_quality_case(case, QwenInformalReasoner(backend, InformalReasoningConfig()))
        self.assertTrue(result["passed"])
        self.assertNotIn(case["id"], backend.requests[0][-1].content)
        self.assertNotIn("concepts", backend.requests[0][-1].content)


if __name__ == "__main__":
    unittest.main()
