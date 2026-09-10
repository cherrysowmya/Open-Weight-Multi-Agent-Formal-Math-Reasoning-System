from collections.abc import Sequence
from dataclasses import replace
from unittest.mock import patch
import unittest

from local_lean_agent.backends.base import ModelBackend
from local_lean_agent.config import InformalReasoningConfig
from local_lean_agent.informal.qwen import QwenInformalReasoner, _render_packet
from local_lean_agent.types import (
    ChatMessage,
    GenerationResult,
    InformalTaskPacket,
    InformalVerdict,
    TokenUsage,
)


class RecordingBackend(ModelBackend):
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.requests: list[Sequence[ChatMessage]] = []
        self.extras: list[dict | None] = []

    def load_model(self, model_id: str) -> float:
        return 0.0

    def unload_model(self) -> float:
        return 0.0

    def health_check(self) -> bool:
        return True

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        extra=None,
    ) -> GenerationResult:
        index = len(self.requests)
        self.requests.append(messages)
        self.extras.append(extra)
        return GenerationResult(
            text=self.responses[index],
            model="fake-qwen",
            usage=TokenUsage(50 + index, 10, 60 + index),
        )


class InformalReasonerTests(unittest.TestCase):
    def test_runtime_failure_is_counted_and_records_the_request(self):
        backend = RecordingBackend([])
        with patch.object(backend, "chat", side_effect=TimeoutError("local timeout")):
            result = QwenInformalReasoner(backend, InformalReasoningConfig()).reason(
                InformalTaskPacket(theorem="theorem t : True"))
        self.assertEqual(result.stop_reason, "runtime_error")
        self.assertEqual(result.generator_calls, 1)
        self.assertEqual(result.verifier_calls, 0)
        self.assertEqual(len(result.requests), 1)
        self.assertEqual(result.requests[0].role, "generator")
        self.assertEqual(len(result.requests[0].messages), 2)

    def test_length_limited_generator_retains_usage_but_not_a_proof(self):
        backend = RecordingBackend([])
        with patch.object(backend, "chat", return_value=GenerationResult(
            text="<informal_proof>Incomplete.</informal_proof>", model="fake",
            usage=TokenUsage(40, 1536, 1576), finish_reason="length",
        )):
            result = QwenInformalReasoner(backend, InformalReasoningConfig()).reason(
                InformalTaskPacket(theorem="theorem t : True"))
        self.assertEqual(result.stop_reason, "generator_output_exhausted")
        self.assertEqual(result.generator_calls, 1)
        self.assertEqual(result.drafts[0].generation.usage.completion_tokens, 1536)
        self.assertEqual(result.final_proof, "")
        self.assertEqual(result.verifier_calls, 0)

    def test_malformed_or_incomplete_reviews_never_accept(self):
        for review in (
            "<verdict>ACCEPT</verdict>",
            "<verdict>ACCEPT</verdict><critique></critique>",
            "<verdict>ACCEPT</verdict><verdict>REJECT</verdict><critique>Gap.</critique>",
            "<think><verdict>ACCEPT</verdict><critique>Only hidden text.</critique>",
        ):
            with self.subTest(review=review):
                result = QwenInformalReasoner(RecordingBackend([
                    "<informal_proof>Outline.</informal_proof>", review,
                ]), InformalReasoningConfig()).reason(InformalTaskPacket("theorem t : True"))
                self.assertFalse(result.accepted)
                self.assertEqual(result.stop_reason, "verifier_malformed")
                self.assertEqual(result.verifier_calls, 1)

    def test_length_limited_verifier_cannot_accept(self):
        backend = RecordingBackend([])
        with patch.object(backend, "chat", side_effect=[
            GenerationResult("<informal_proof>Outline.</informal_proof>", "fake"),
            GenerationResult("<verdict>ACCEPT</verdict><critique>Fine.</critique>",
                             "fake", finish_reason="length"),
        ]):
            result = QwenInformalReasoner(backend, InformalReasoningConfig()).reason(
                InformalTaskPacket("theorem t : True"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.stop_reason, "verifier_output_exhausted")

    def test_oversized_theorem_stops_before_any_model_call(self):
        backend = RecordingBackend([])
        result = QwenInformalReasoner(backend, InformalReasoningConfig(
            max_packet_chars=100,
        )).reason(InformalTaskPacket("x" * 101))
        self.assertEqual(result.stop_reason, "context_budget")
        self.assertEqual(result.generator_calls, 0)
        self.assertEqual(backend.requests, [])

    def test_complete_theorem_and_balanced_tags_survive_packet_bounding(self):
        theorem = "theorem t (last_assumption : False) : True"
        packet = _render_packet(InformalTaskPacket(
            theorem, current_goal="goal" * 800, retrieved_declarations="lemma" * 4000,
        ), max_chars=3500)
        self.assertIn(theorem, packet)
        self.assertLessEqual(len(packet), 3500)
        self.assertEqual(packet.count("<current_goal>"), packet.count("</current_goal>"))

    def test_total_context_reserves_space_for_output(self):
        backend = RecordingBackend([])
        result = QwenInformalReasoner(backend, InformalReasoningConfig(
            max_context_tokens=1700, max_output_tokens=1536,
        )).reason(InformalTaskPacket("theorem t : True"))
        self.assertEqual(result.stop_reason, "context_budget")
        self.assertEqual(result.generator_calls, 0)

    def test_generator_hidden_thinking_never_crosses_into_verifier(self):
        backend = RecordingBackend([
            "hidden-secret</think><informal_proof>Public outline.</informal_proof>",
            "<verdict>ACCEPT</verdict><critique>Complete.</critique>",
        ])
        result = QwenInformalReasoner(backend, InformalReasoningConfig()).reason(
            InformalTaskPacket("theorem t : True"))
        self.assertTrue(result.accepted)
        self.assertNotIn("hidden-secret", backend.requests[1][-1].content)
        self.assertEqual([r.role for r in result.requests], ["generator", "verifier"])

    def test_separate_theorems_never_share_a_draft(self):
        backend = RecordingBackend([
            "<informal_proof>First secret outline.</informal_proof>",
            "<verdict>ACCEPT</verdict><critique>Complete.</critique>",
            "<informal_proof>Second outline.</informal_proof>",
            "<verdict>ACCEPT</verdict><critique>Complete.</critique>",
        ])
        reasoner = QwenInformalReasoner(backend, InformalReasoningConfig())
        reasoner.reason(InformalTaskPacket("theorem first : True"))
        reasoner.reason(InformalTaskPacket("theorem second : True"))
        self.assertNotIn("First secret", backend.requests[2][-1].content)
        self.assertNotIn("theorem first", backend.requests[3][-1].content)

    def test_overlong_outline_is_not_silently_cut_for_verification(self):
        backend = RecordingBackend(["<informal_proof>" + "x" * 51 + "</informal_proof>"])
        result = QwenInformalReasoner(backend, InformalReasoningConfig(
            max_proof_chars=50,
        )).reason(InformalTaskPacket("theorem t : True"))
        self.assertEqual(result.stop_reason, "generator_output_limit")
        self.assertEqual(result.final_proof, "")
        self.assertEqual(result.verifier_calls, 0)

    def test_generator_and_verifier_use_fresh_thinking_contexts(self) -> None:
        backend = RecordingBackend(
            [
                "<informal_proof>Use symmetry of equality.</informal_proof>",
                "<verdict>ACCEPT</verdict><critique>Complete.</critique>",
            ]
        )
        result = QwenInformalReasoner(
            backend, InformalReasoningConfig(max_refinement_rounds=3)
        ).reason(InformalTaskPacket(theorem="theorem t (a b : Nat) : a + b = b + a"))

        self.assertTrue(result.accepted)
        self.assertEqual(result.rounds, 1)
        self.assertEqual(result.generator_calls, 1)
        self.assertEqual(result.verifier_calls, 1)
        self.assertEqual(len(backend.requests), 2)
        self.assertTrue(all(len(request) == 2 for request in backend.requests))
        self.assertTrue(
            all(extra == {"chat_template_kwargs": {"enable_thinking": True}}
                for extra in backend.extras)
        )
        self.assertNotIn("verifier", backend.requests[0][0].content.lower())
        self.assertIn("independent critical verifier", backend.requests[1][0].content)

    def test_revision_passes_only_explicit_artifacts_to_a_new_request(self) -> None:
        backend = RecordingBackend(
            [
                "<informal_proof>First outline.</informal_proof>",
                "<verdict>REVISE</verdict><critique>Handle the zero case.</critique>",
                "<informal_proof>Revised outline handles zero.</informal_proof>",
                "<verdict>ACCEPT</verdict><critique>No remaining gap.</critique>",
            ]
        )
        result = QwenInformalReasoner(
            backend, InformalReasoningConfig(max_refinement_rounds=3)
        ).reason(InformalTaskPacket(theorem="theorem t : True"))

        self.assertTrue(result.accepted)
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.reviews[0].verdict, InformalVerdict.REVISE)
        second_generator = backend.requests[2][-1].content
        second_verifier = backend.requests[3][-1].content
        self.assertIn("First outline", second_generator)
        self.assertIn("Handle the zero case", second_generator)
        self.assertNotIn("First outline", second_verifier)
        self.assertIn("Revised outline handles zero", second_verifier)
        self.assertTrue(all(len(request) == 2 for request in backend.requests))

    def test_reject_and_malformed_outputs_are_not_accepted(self) -> None:
        rejected = QwenInformalReasoner(
            RecordingBackend(
                [
                    "<informal_proof>Assume the result.</informal_proof>",
                    "<verdict>REJECT</verdict><critique>Circular.</critique>",
                ]
            ),
            InformalReasoningConfig(),
        ).reason(InformalTaskPacket(theorem="theorem false : False"))
        malformed = QwenInformalReasoner(
            RecordingBackend(["unstructured response"]),
            InformalReasoningConfig(),
        ).reason(InformalTaskPacket(theorem="theorem t : True"))

        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.stop_reason, "verifier_rejected")
        self.assertFalse(malformed.accepted)
        self.assertEqual(malformed.stop_reason, "generator_malformed")
        self.assertEqual(malformed.generator_calls, 1)
        self.assertEqual(malformed.verifier_calls, 0)

    def test_refinement_budget_is_enforced(self) -> None:
        backend = RecordingBackend(
            [
                "<informal_proof>Draft one.</informal_proof>",
                "<verdict>REVISE</verdict><critique>Gap one.</critique>",
                "<informal_proof>Draft two.</informal_proof>",
                "<verdict>REVISE</verdict><critique>Gap two.</critique>",
            ]
        )
        result = QwenInformalReasoner(
            backend, InformalReasoningConfig(max_refinement_rounds=2)
        ).reason(InformalTaskPacket(theorem="theorem t : True"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.stop_reason, "refinement_budget")


if __name__ == "__main__":
    unittest.main()
