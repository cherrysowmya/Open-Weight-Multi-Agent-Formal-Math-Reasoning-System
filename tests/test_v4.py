import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_lean_agent.config import AppConfig, AgentConfig, V4Config, _validate
from local_lean_agent.contexts import (TaskPacket, PacketBudgetError, parse_discussion,
    prepare_request, summarize_failures)
from local_lean_agent.orchestrator import ProofAgent
from local_lean_agent.types import GenerationResult, VerificationResult, FailureCategory
from tests.test_orchestrator import FakeBackend


TASK = "theorem target (P : Prop) (h : P) : P := by sorry"
BAD = TASK.replace("sorry", "exact missing")
BAD2 = TASK.replace("sorry", "exact missing_again")
GOOD = TASK.replace("sorry", "exact h")
ADVICE = json.dumps({"diagnosis": "The goal is already a hypothesis.",
    "strategies": ["Use the supplied assumption."], "intermediate_facts": []})


class StrictVerifier:
    def __init__(self):
        self.calls = []

    def verify(self, code, *, attempt_id):
        self.calls.append(code)
        valid = code == GOOD + "\n" or code == GOOD
        return VerificationResult(valid, () if valid else ("Unknown identifier missing",),
            FailureCategory.NONE if valid else FailureCategory.UNKNOWN_IDENTIFIER)


class V4Tests(unittest.TestCase):
    def solve(self, outputs, *, task=TASK, rounds=4, **options):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(v4=V4Config(enabled=True, **options),
                agent=AgentConfig(max_iterations=rounds, fallback_enabled=False,
                    log_path=Path(directory) / "events.jsonl"))
            backend = FakeBackend(outputs)
            verifier = StrictVerifier()
            result = ProofAgent(backend, verifier, config).solve(task)
        return result, backend, verifier

    def test_easy_proof_bypasses_discussion_and_worker(self):
        result, _, _ = self.solve([GOOD])
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.discussion_partner_calls, 0)
        self.assertEqual(result.metrics.fresh_subproblem_calls, 0)

    def test_valid_seed_bypasses_all_model_work(self):
        result, backend, _ = self.solve([], task=GOOD)
        self.assertTrue(result.success)
        self.assertEqual(backend.calls, 0)

    def test_two_failures_trigger_discussion_then_fresh_formal_round(self):
        result, _, verifier = self.solve([BAD, BAD2, ADVICE, GOOD], rounds=3)
        self.assertTrue(result.success)
        self.assertEqual(result.rounds, 3)
        self.assertEqual(result.metrics.main_agent_calls, 2)
        self.assertEqual(result.metrics.discussion_partner_calls, 1)
        self.assertEqual(result.metrics.fresh_subproblem_calls, 1)
        self.assertEqual([r.role for r in result.v4_requests],
                         ["main", "main", "discussion_partner", "fresh_subproblem"])
        self.assertEqual(len(verifier.calls), 3)
        self.assertEqual(result.iterations[-1].repair_action, "fresh_subproblem")
        fresh = json.loads(result.v4_requests[-1].messages[-1].content)
        self.assertEqual(json.loads(fresh["discussion"])["strategies"], ["Use the supplied assumption."])

    def test_contexts_are_independent_and_candidate_history_is_excluded(self):
        result, _, _ = self.solve([BAD + "\n-- BANANA-1937", BAD2, ADVICE, GOOD])
        records = result.v4_requests
        self.assertEqual(len({r.conversation_id for r in records}), len(records))
        for request in records[-2:]:
            self.assertEqual(request.history_messages, 0)
            self.assertEqual(len(request.messages), 2)
            self.assertNotIn("BANANA-1937", str(request.messages))
            self.assertNotIn(BAD2, str(request.messages))
            self.assertLessEqual(request.estimated_context_tokens + request.max_output_tokens, 8192)
        self.assertTrue(records[-2].enable_thinking)
        self.assertFalse(records[-1].enable_thinking)

    def test_malformed_discussion_is_recorded_and_formal_repair_continues(self):
        result, _, _ = self.solve([BAD, BAD2, "Looks good!", GOOD])
        self.assertTrue(result.success)
        discussion = result.v4_requests[-2]
        self.assertEqual(discussion.status, "error")
        self.assertTrue(discussion.error_message)
        self.assertEqual(json.loads(result.v4_requests[-1].messages[-1].content)["discussion"], "")

    def test_discussion_transport_error_does_not_abandon_formal_budget(self):
        original = FakeBackend.chat
        def chat(backend, messages, **kwargs):
            if "discussion partner" in messages[0].content:
                raise TimeoutError("local discussion timed out")
            return original(backend, messages, **kwargs)
        with patch.object(FakeBackend, "chat", chat):
            result, _, _ = self.solve([BAD, BAD2, GOOD])
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.discussion_partner_calls, 1)
        self.assertIn("TimeoutError", result.v4_requests[-2].error_message)

    def test_exhausted_discussion_output_is_not_used(self):
        original = FakeBackend.chat
        def chat(backend, messages, **kwargs):
            output = original(backend, messages, **kwargs)
            return replace(output, finish_reason="length") if "discussion partner" in messages[0].content else output
        with patch.object(FakeBackend, "chat", chat):
            result, _, _ = self.solve([BAD, BAD2, ADVICE, GOOD])
        self.assertTrue(result.success)
        self.assertEqual(result.v4_requests[-2].status, "error")
        self.assertEqual(json.loads(result.v4_requests[-1].messages[-1].content)["discussion"], "")

    def test_long_candidate_is_compressed_before_main_repair(self):
        # Only a failed proof-body comment is large; the theorem is still small.
        long_bad = BAD + "\n-- " + "noise " * 2000
        result, _, _ = self.solve([long_bad, GOOD], discussion_enabled=False,
                                  fresh_context_enabled=False)
        self.assertTrue(result.success)
        self.assertEqual(result.v4_requests[-1].role, "main_compressed")
        self.assertLessEqual(result.v4_requests[-1].estimated_context_tokens
                             + result.v4_requests[-1].max_output_tokens, 12288)

    def test_disabled_v4_preserves_formal_requests_and_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(agent=AgentConfig(fallback_enabled=False,
                log_path=Path(directory) / "events.jsonl"))
            backend = FakeBackend([BAD, BAD2, GOOD])
            result = ProofAgent(backend, StrictVerifier(), config).solve(TASK)
        self.assertTrue(result.success)
        self.assertEqual(result.v4_requests, [])
        self.assertEqual(result.metrics.fresh_subproblem_calls, 0)
        self.assertEqual(result.metrics.discussion_partner_calls, 0)

    def test_discussion_cannot_certify_a_false_proof(self):
        result, _, _ = self.solve([BAD, BAD2, ADVICE, TASK], rounds=3)
        self.assertFalse(result.success)
        self.assertEqual(result.end_reason, "MAX_ROUNDS")

    def test_worker_cannot_change_statement(self):
        result, _, verifier = self.solve([BAD, BAD2, ADVICE, "theorem other : True := by trivial"], rounds=3)
        self.assertFalse(result.success)
        self.assertEqual(result.failure_category, FailureCategory.TASK_MUTATION)
        self.assertEqual(len(verifier.calls), 2)

    def test_roles_are_bounded_and_return_to_main(self):
        result, _, _ = self.solve([BAD, BAD2, ADVICE, BAD, GOOD], rounds=4)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.discussion_partner_calls, 1)
        self.assertEqual(result.metrics.fresh_subproblem_calls, 1)
        self.assertEqual(result.v4_requests[-1].role, "main")

    def test_no_remaining_formal_round_means_no_extra_roles(self):
        result, _, _ = self.solve([BAD, BAD2], rounds=2)
        self.assertFalse(result.success)
        self.assertEqual(result.metrics.discussion_partner_calls, 0)

    def test_discussion_only_and_worker_only_ablations(self):
        for outputs, options, expected in (
            ([BAD, BAD2, ADVICE, GOOD], {"fresh_context_enabled": False}, (1, 0)),
            ([BAD, BAD2, GOOD], {"discussion_enabled": False}, (0, 1)),
        ):
            with self.subTest(options=options):
                result, _, _ = self.solve(outputs, **options)
                self.assertTrue(result.success)
                self.assertEqual((result.metrics.discussion_partner_calls,
                                  result.metrics.fresh_subproblem_calls), expected)

    def test_compression_preserves_exact_task_and_local_hypotheses(self):
        packet = TaskPacket(TASK, goal_and_hypotheses="P : Prop\nh : P\n⊢ P",
                            retrieved_declarations="irrelevant " * 10000)
        request = prepare_request(packet, system="Prove the task", role="fresh_subproblem",
            iteration=3, context_limit=2048, output_limit=256, thinking=False, compress=True)
        data = json.loads(request.messages[-1].content)
        self.assertEqual(data["task"], TASK)
        self.assertEqual(data["goal_and_hypotheses"], packet.goal_and_hypotheses)
        self.assertIn("retrieved_declarations", request.compressed_fields)
        self.assertLessEqual(request.estimated_context_tokens + request.max_output_tokens, 2048)

    def test_oversized_critical_task_fails_before_model_call(self):
        packet = TaskPacket("theorem " + "a" * 10000)
        with self.assertRaises(PacketBudgetError):
            prepare_request(packet, system="Prove", role="fresh_subproblem", iteration=3,
                context_limit=2048, output_limit=256, thinking=False, compress=True)

    def test_summary_retains_only_compiler_evidence_with_source_ids(self):
        result, _, _ = self.solve([BAD, BAD2], rounds=2)
        summary, ids = summarize_failures(result.iterations, 2400)
        self.assertEqual(ids, (1, 2))
        self.assertIn("Unknown identifier", summary)
        self.assertNotIn("exact missing", summary)

    def test_small_summary_keeps_recent_failure(self):
        result, _, _ = self.solve([BAD, BAD2], rounds=2)
        record = replace(result.iterations[-1], verification=VerificationResult(
            False, ("A long compiler diagnostic " * 100,), FailureCategory.TACTIC_FAILURE))
        summary, ids = summarize_failures([record], 128)
        self.assertEqual(ids, (2,))
        self.assertLessEqual(len(summary), 128)
        self.assertIn("Attempt 2", summary)

    def test_failed_formal_request_is_counted_and_logged(self):
        with patch.object(FakeBackend, "chat", side_effect=TimeoutError("local timeout")):
            result, _, _ = self.solve([])
        self.assertFalse(result.success)
        self.assertEqual(result.metrics.formal_call_attempts, 1)
        self.assertEqual(result.metrics.main_agent_calls, 1)
        self.assertEqual(result.metrics.model_calls, 0)
        self.assertEqual(result.v4_requests[0].status, "error")
        self.assertIn("local timeout", result.v4_requests[0].error_message)

    def test_invalid_discussion_structures_fail_closed(self):
        for text in ("PASS", "", "{}", "[]", '<think>private',
                     ADVICE.replace('["Use the supplied assumption."]', "[]")):
            with self.subTest(text=text), self.assertRaises((ValueError, TypeError)):
                parse_discussion(text)

    def test_private_thinking_not_forwarded(self):
        self.assertNotIn("SECRET", parse_discussion("<think>SECRET</think>" + ADVICE))

    def test_config_limits(self):
        for overrides in ({"max_context_tokens": 8193}, {"max_discussion_calls": 0},
                          {"max_fresh_calls": 4}, {"trigger_after_failures": 0},
                          {"discussion_max_output_tokens": 8192}, {"summary_max_chars": 0}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                _validate(AppConfig(v4=replace(V4Config(), **overrides)))


if __name__ == "__main__":
    unittest.main()
