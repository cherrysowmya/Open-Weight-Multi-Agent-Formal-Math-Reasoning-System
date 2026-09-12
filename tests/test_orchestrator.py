from collections.abc import Sequence
import tempfile
import unittest

from local_lean_agent.backends.base import ModelBackend
from local_lean_agent.config import (
    AgentConfig,
    AppConfig,
    GenerationConfig,
    InformalReasoningConfig,
    LeanExploreConfig,
    LeanLSPConfig,
)
from local_lean_agent.feedback.base import LeanFeedbackProvider
from local_lean_agent.informal.base import InformalReasoner
from local_lean_agent.orchestrator import (
    ProofAgent,
    build_retrieval_query,
    render_retrieval,
    render_rejected_history,
    strategy_fingerprint,
    extract_lean_code,
    has_concrete_proof_candidate,
    is_degenerate_candidate,
    validate_task_preserved,
)
from local_lean_agent.retrieval.base import SemanticRetriever
from local_lean_agent.telemetry import JSONLTelemetry
from local_lean_agent.types import (
    ChatMessage,
    FailureCategory,
    GenerationResult,
    InformalDraft,
    InformalReasoningResult,
    InformalReview,
    InformalTaskPacket,
    InformalVerdict,
    LeanFeedback,
    RetrievalHit,
    RetrievalResult,
    TokenUsage,
    VerificationResult,
)
from local_lean_agent.verification.base import LeanVerifier


class FakeBackend(ModelBackend):
    def __init__(self, responses: list[str], finish_reasons: list[str | None] | None = None):
        self.responses = responses
        self.finish_reasons = finish_reasons or [None] * len(responses)
        self.loaded = False
        self.calls = 0
        self.requests: list[Sequence[ChatMessage]] = []
        self.max_token_requests: list[int] = []

    def load_model(self, model_id: str) -> float:
        self.loaded = True
        return 0.01

    def unload_model(self) -> float:
        self.loaded = False
        return 0.01

    def health_check(self) -> bool:
        return self.loaded

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        extra=None,
    ) -> GenerationResult:
        self.requests.append(messages)
        self.max_token_requests.append(max_tokens)
        text = self.responses[self.calls]
        finish_reason = self.finish_reasons[self.calls]
        self.calls += 1
        return GenerationResult(
            text=text,
            model="fake",
            usage=TokenUsage(100, 20, 120),
            finish_reason=finish_reason,
        )


class FakeVerifier(LeanVerifier):
    def __init__(self):
        self.calls = 0

    def health_check(self) -> bool:
        return True

    def verify(self, code: str, *, attempt_id: str) -> VerificationResult:
        self.calls += 1
        if "rfl" in code:
            return VerificationResult(valid=True)
        return VerificationResult(
            valid=False,
            diagnostics=("1:1: error: unsolved goals",),
            failure_category=FailureCategory.UNSOLVED_GOALS,
        )


class FakeFeedbackProvider(LeanFeedbackProvider):
    def __init__(self, feedback: LeanFeedback):
        self.feedback = feedback
        self.calls = 0
        self.compiler_diagnostics: list[tuple[str, ...]] = []

    def inspect(
        self,
        code: str,
        *,
        compiler_diagnostics: tuple[str, ...],
        attempt_id: str,
    ) -> LeanFeedback:
        self.calls += 1
        self.compiler_diagnostics.append(compiler_diagnostics)
        return self.feedback

    def health_check(self) -> bool:
        return self.feedback.available


class FakeRetriever(SemanticRetriever):
    def __init__(self, result: RetrievalResult):
        self.result = result
        self.queries: list[str] = []

    def retrieve(self, query: str) -> RetrievalResult:
        self.queries.append(query)
        return RetrievalResult(
            available=self.result.available,
            query=query,
            hits=self.result.hits,
            tool_calls=self.result.tool_calls,
            elapsed_seconds=self.result.elapsed_seconds,
            data_version=self.result.data_version,
            error_message=self.result.error_message,
        )

    def health_check(self) -> bool:
        return self.result.available

    def close(self) -> None:
        return None


class FakeInformalReasoner(InformalReasoner):
    def __init__(self, accepted: bool = True):
        self.accepted = accepted
        self.packets: list[InformalTaskPacket] = []

    def reason(self, packet: InformalTaskPacket) -> InformalReasoningResult:
        self.packets.append(packet)
        generation = GenerationResult(
            text="informal",
            model="fake-informal",
            usage=TokenUsage(70, 15, 85),
        )
        draft = InformalDraft(1, "Use reflexivity.", generation, 73)
        review = InformalReview(
            1,
            InformalVerdict.ACCEPT if self.accepted else InformalVerdict.REVISE,
            "The outline is mathematically complete." if self.accepted else "Gap.",
            generation,
            81,
        )
        return InformalReasoningResult(
            triggered=True,
            accepted=self.accepted,
            rounds=1,
            final_proof=draft.proof,
            stop_reason=("verifier_accepted" if self.accepted else "refinement_budget"),
            generator_calls=1,
            verifier_calls=1,
            drafts=(draft,),
            reviews=(review,),
        )


class OrchestratorTests(unittest.TestCase):
    def test_informal_reasoning_triggers_after_a_rejected_model_candidate(self) -> None:
        backend = FakeBackend(
            [
                "```lean\ntheorem t : 1 = 1 := by omega\n```",
                "```lean\ntheorem t : 1 = 1 := by rfl\n```",
            ]
        )
        reasoner = FakeInformalReasoner()
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                informal_reasoning=InformalReasoningConfig(
                    enabled=True,
                    invocation_policy="after_model_failures",
                    trigger_after_failures=1,
                ),
                agent=AgentConfig(
                    fallback_enabled=False,
                    log_path=f"{directory}/attempts.jsonl",
                ),
            )
            result = ProofAgent(
                backend,
                FakeVerifier(),
                config,
                informal_reasoner=reasoner,
            ).solve("theorem t : 1 = 1 := by sorry")

        self.assertTrue(result.success)
        self.assertEqual(len(reasoner.packets), 1)
        self.assertNotIn("omega", reasoner.packets[0].theorem)
        self.assertIn("omega", reasoner.packets[0].rejected_strategies)
        self.assertNotIn("informal_mathematical_guidance", backend.requests[0][-1].content)
        self.assertIn("informal_mathematical_guidance", backend.requests[1][-1].content)
        self.assertIn("Use reflexivity", backend.requests[1][-1].content)
        self.assertEqual(result.metrics.informal_generator_calls, 1)
        self.assertEqual(result.metrics.informal_verifier_calls, 1)
        self.assertEqual(result.metrics.informal_prompt_tokens, 140)
        self.assertEqual(result.metrics.informal_completion_tokens, 30)
        self.assertEqual(result.metrics.informal_context_sizes, [73, 81])
        self.assertIsNotNone(result.informal_reasoning)

    def test_informal_acceptance_never_overrides_lean_rejection(self) -> None:
        backend = FakeBackend(
            ["```lean\ntheorem t : False := by contradiction\n```"]
        )
        reasoner = FakeInformalReasoner(accepted=True)
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                informal_reasoning=InformalReasoningConfig(
                    enabled=True, invocation_policy="always"
                ),
                agent=AgentConfig(
                    max_iterations=1,
                    fallback_enabled=False,
                    log_path=f"{directory}/attempts.jsonl",
                ),
            )
            result = ProofAgent(
                backend,
                FakeVerifier(),
                config,
                informal_reasoner=reasoner,
            ).solve("theorem t : False := by sorry")

        self.assertTrue(result.informal_reasoning.accepted)
        self.assertFalse(result.success)
        self.assertEqual(result.end_reason, "MAX_ROUNDS")
        self.assertEqual(result.metrics.kimina_checks, 1)

    def test_valid_input_skips_even_always_on_informal_reasoning(self) -> None:
        backend = FakeBackend([])
        reasoner = FakeInformalReasoner()
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                informal_reasoning=InformalReasoningConfig(
                    enabled=True, invocation_policy="always"
                ),
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(
                backend,
                FakeVerifier(),
                config,
                informal_reasoner=reasoner,
            ).solve("theorem t : 1 = 1 := by rfl")
        self.assertTrue(result.success)
        self.assertEqual(reasoner.packets, [])
        self.assertIsNone(result.informal_reasoning)

    def test_retrieval_query_excludes_the_failed_proof_body(self) -> None:
        theorem = "theorem t (xs : List Nat) : xs ++ [] = xs := by\n  exact Imaginary.nil xs"
        query = build_retrieval_query(theorem)
        self.assertIn("xs ++ [] = xs", query)
        self.assertNotIn("Imaginary.nil", query)

    def test_retrieval_render_includes_provenance_name_and_source(self) -> None:
        rendered = render_retrieval(
            RetrievalResult(
                available=True,
                query="append empty",
                data_version="v-test",
                hits=(
                    RetrievalHit(
                        declaration_id=42,
                        name="List.append_nil",
                        description="Append nil on the right.",
                        source_text="theorem append_nil (as : List α) : as ++ [] = as",
                    ),
                ),
            )
        )
        self.assertIn("declaration_id=42", rendered)
        self.assertIn("exact_name=List.append_nil", rendered)
        self.assertIn("theorem append_nil", rendered)
        self.assertIn("v-test", rendered)

    def test_retrieved_lean_source_is_injected_and_counted(self) -> None:
        backend = FakeBackend(["```lean\ntheorem t : 1 = 1 := by rfl\n```"])
        retriever = FakeRetriever(
            RetrievalResult(
                available=True,
                query="ignored",
                tool_calls=3,
                elapsed_seconds=0.25,
                hits=(
                    RetrievalHit(
                        declaration_id=7,
                        name="Eq.refl",
                        source_text="protected theorem Eq.refl (a : α) : a = a",
                    ),
                ),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                lean_explore=LeanExploreConfig(enabled=True, required=True),
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(
                backend, FakeVerifier(), config, retriever=retriever
            ).solve("theorem t : 1 = 1 := by sorry")
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.retrieval_queries, 1)
        self.assertEqual(result.metrics.retrieval_calls, 3)
        self.assertEqual(result.metrics.retrieval_latency_seconds, 0.25)
        self.assertIn("exact_name=Eq.refl", backend.requests[0][-1].content)
        self.assertEqual(result.iterations[0].retrieval.hits[0].declaration_id, 7)

    def test_required_retrieval_failure_stops_before_model_loading(self) -> None:
        backend = FakeBackend([])
        retriever = FakeRetriever(
            RetrievalResult(
                available=False,
                query="ignored",
                error_message="local index unavailable",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                lean_explore=LeanExploreConfig(enabled=True, required=True),
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(
                backend, FakeVerifier(), config, retriever=retriever
            ).solve("theorem t : 1 = 1 := by sorry")
        self.assertFalse(result.success)
        self.assertEqual(result.failure_category, FailureCategory.RETRIEVAL_UNAVAILABLE)
        self.assertEqual(result.end_reason, "RETRIEVAL_UNAVAILABLE")
        self.assertEqual(result.rounds, 0)
        self.assertFalse(backend.loaded)

    def test_extracts_lean_fence(self) -> None:
        self.assertEqual(extract_lean_code("text\n```lean\n#check Nat\n```"), "#check Nat\n")

    def test_extracts_unclosed_lean_fence(self) -> None:
        self.assertEqual(extract_lean_code("text\n```lean\n#check Nat"), "#check Nat\n")

    def test_rejects_changed_theorem_statement(self) -> None:
        task = "theorem t (n : Nat) : n + 0 = n := by sorry"
        candidate = "theorem t (n : Nat) : True := by trivial"
        self.assertIsNotNone(validate_task_preserved(task, candidate))

    def test_rejects_theorem_statement_spoofed_inside_comment(self) -> None:
        task = "theorem original (n : Nat) : n + 0 = n := by sorry"
        candidate = (
            "/- theorem original (n : Nat) : n + 0 = n := by fake -/\n"
            "theorem changed (n : Nat) : True := by trivial"
        )
        self.assertIsNotNone(validate_task_preserved(task, candidate))

    def test_rejects_added_global_hypothesis_before_theorem(self) -> None:
        task = "theorem original : True := by sorry"
        candidate = (
            "variable (hidden : False)\n"
            "theorem original : True := by trivial"
        )
        self.assertIsNotNone(validate_task_preserved(task, candidate))

    def test_rejects_changed_import_prefix(self) -> None:
        task = "import Mathlib\n\ntheorem original : True := by sorry"
        candidate = "theorem original : True := by trivial"
        self.assertIsNotNone(validate_task_preserved(task, candidate))

    def test_accepts_preserved_theorem_statement(self) -> None:
        task = "theorem t (n : Nat) : n + 0 = n := by sorry"
        candidate = "theorem t (n : Nat) : n + 0 = n := by simp"
        self.assertIsNone(validate_task_preserved(task, candidate))

    def test_detects_repetitive_candidate(self) -> None:
        candidate = "theorem t : True := by\n" + "  induction n with\n" * 5
        self.assertTrue(is_degenerate_candidate(candidate))

    def test_normal_short_candidate_is_not_degenerate(self) -> None:
        self.assertFalse(is_degenerate_candidate("theorem t : True := by trivial"))

    def test_strategy_fingerprint_ignores_term_vs_exact_wrapper(self) -> None:
        direct = "theorem t : True := trivial"
        tactic = "theorem t : True := by\n  exact trivial"
        self.assertEqual(strategy_fingerprint(direct), strategy_fingerprint(tactic))
        self.assertNotEqual(
            strategy_fingerprint(direct),
            strategy_fingerprint("theorem t : True := by simp"),
        )

    def test_detects_concrete_input_candidate_but_not_sorry_task(self) -> None:
        self.assertTrue(
            has_concrete_proof_candidate("theorem t : True := by trivial")
        )
        self.assertFalse(
            has_concrete_proof_candidate("theorem t : True := by sorry")
        )
        self.assertTrue(
            has_concrete_proof_candidate(
                "-- mentioning sorry in a comment is fine\ntheorem t : True := by trivial"
            )
        )

    def test_repairs_until_lean_accepts(self) -> None:
        backend = FakeBackend(
            [
                "```lean\ntheorem t : 1 = 1 := by omega\n```",
                "```lean\ntheorem t : 1 = 1 := by rfl\n```",
            ]
        )
        verifier = FakeVerifier()
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                agent=AgentConfig(
                    fallback_enabled=False,
                    log_path=f"{directory}/attempts.jsonl",
                ),
            )
            agent = ProofAgent(
                backend,
                verifier,
                config,
                JSONLTelemetry(config.agent.log_path),
            )
            result = agent.solve("theorem t : 1 = 1 := by sorry")
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.iterations, 2)
        self.assertEqual(result.metrics.model_calls, 2)
        self.assertEqual(result.metrics.kimina_checks, 2)
        self.assertEqual(result.stop_reason, "verified")
        self.assertIsNone(result.error_message)
        self.assertFalse(backend.loaded)

    def test_verified_fallback_closes_a_rejected_model_candidate(self) -> None:
        backend = FakeBackend(
            ["```lean\ntheorem t : True := by contradiction\n```"]
        )

        class SimpOnlyVerifier(LeanVerifier):
            def health_check(self) -> bool:
                return True

            def verify(self, code: str, *, attempt_id: str) -> VerificationResult:
                if code.rstrip().endswith("by\n    simp"):
                    return VerificationResult(valid=True)
                return VerificationResult(
                    valid=False,
                    diagnostics=("error: unsolved goals",),
                    failure_category=FailureCategory.UNSOLVED_GOALS,
                )

        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                agent=AgentConfig(
                    fallback_enabled=True,
                    fallback_tactics=("rfl", "simp"),
                    log_path=f"{directory}/attempts.jsonl",
                ),
            )
            result = ProofAgent(backend, SimpOnlyVerifier(), config).solve(
                "theorem t : True := by sorry"
            )
        self.assertTrue(result.success)
        self.assertEqual(result.rounds, 1)
        self.assertEqual(result.metrics.fallback_checks, 2)
        self.assertEqual(result.metrics.kimina_checks, 3)
        self.assertEqual(len(result.fallback_attempts), 2)
        self.assertEqual(result.iterations[-1].generation.model, "v1_1_fallback")
        self.assertTrue(result.final_proof.rstrip().endswith("by\n    simp"))

    def test_fallback_portfolio_is_attempted_only_once_for_false_theorem(self) -> None:
        backend = FakeBackend(
            [
                "```lean\ntheorem t : False := by contradiction\n```",
                "```lean\ntheorem t : False := by decide\n```",
                "```lean\ntheorem t : False := by aesop\n```",
            ]
        )

        class RejectAllVerifier(LeanVerifier):
            def health_check(self) -> bool:
                return True

            def verify(self, code: str, *, attempt_id: str) -> VerificationResult:
                return VerificationResult(
                    valid=False,
                    diagnostics=("error: unsolved goals",),
                    failure_category=FailureCategory.UNSOLVED_GOALS,
                )

        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                agent=AgentConfig(
                    max_iterations=3,
                    fallback_enabled=True,
                    fallback_tactics=("rfl", "simp"),
                    log_path=f"{directory}/attempts.jsonl",
                ),
            )
            result = ProofAgent(backend, RejectAllVerifier(), config).solve(
                "theorem t : False := by sorry"
            )
        self.assertFalse(result.success)
        self.assertEqual(result.end_reason, "MAX_ROUNDS")
        self.assertEqual(result.metrics.model_calls, 3)
        self.assertEqual(result.metrics.fallback_checks, 2)
        self.assertEqual(result.metrics.kimina_checks, 5)

    def test_valid_input_candidate_skips_model_loading(self) -> None:
        backend = FakeBackend([])
        verifier = FakeVerifier()
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(backend, verifier, config).solve(
                "theorem t : 1 = 1 := by rfl"
            )
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.model_calls, 0)
        self.assertEqual(result.metrics.kimina_checks, 1)
        self.assertEqual(result.metrics.iterations, 0)
        self.assertEqual(result.iterations[0].iteration, 0)
        self.assertEqual(result.iterations[0].generation.model, "input_candidate")
        self.assertFalse(backend.loaded)

    def test_rejects_seed_then_asks_model_to_repair_it(self) -> None:
        backend = FakeBackend(
            [
                "```lean\nimport Mathlib\n\ntheorem repair_test_3 (x : ℝ) : "
                "0 ≤ x^2 := by\n  positivity\n```"
            ]
        )

        class RepairVerifier(LeanVerifier):
            def health_check(self) -> bool:
                return True

            def verify(self, code: str, *, attempt_id: str) -> VerificationResult:
                if "positivity" in code:
                    return VerificationResult(valid=True)
                return VerificationResult(
                    valid=False,
                    diagnostics=("4:2: error: linarith failed to find a contradiction",),
                    failure_category=FailureCategory.TACTIC_FAILURE,
                )

        source = (
            "import Mathlib\n\n"
            "theorem repair_test_3 (x : ℝ) : 0 ≤ x^2 := by\n  linarith\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(backend, RepairVerifier(), config).solve(source)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.model_calls, 1)
        self.assertEqual(result.metrics.kimina_checks, 2)
        self.assertEqual([record.iteration for record in result.iterations], [0, 1])
        repair_request = backend.requests[0][-1].content
        self.assertIn("linarith", repair_request)
        self.assertIn("failed to find a contradiction", repair_request)
        self.assertIn("positivity", result.final_proof)

    def test_lean_lsp_goal_is_added_to_repair_packet_and_metrics(self) -> None:
        backend = FakeBackend(
            ["```lean\ntheorem t : 1 = 1 := by rfl\n```"]
        )
        feedback = FakeFeedbackProvider(
            LeanFeedback(
                available=True,
                diagnostics=("l1c1-l1c5, severity: 1\nunsolved goals",),
                goal_state="n : ℕ\n⊢ n + 0 = n",
                line=1,
                column=1,
                tool_calls=2,
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                lean_lsp=LeanLSPConfig(enabled=True, required=True),
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(
                backend,
                FakeVerifier(),
                config,
                feedback_provider=feedback,
            ).solve("theorem t : 1 = 1 := by omega")
        self.assertTrue(result.success)
        self.assertEqual(feedback.calls, 1)
        self.assertEqual(result.metrics.lean_lsp_calls, 2)
        self.assertIsNotNone(result.iterations[0].lean_feedback)
        repair_packet = backend.requests[0][-1].content
        self.assertIn("<lean_lsp_proof_state>", repair_packet)
        self.assertIn("n : ℕ\n⊢ n + 0 = n", repair_packet)
        self.assertIn("unsolved goals", repair_packet)

    def test_required_lean_lsp_failure_stops_before_loading_model(self) -> None:
        backend = FakeBackend([])
        feedback = FakeFeedbackProvider(
            LeanFeedback(
                available=False,
                error_message="MCP server unavailable",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                lean_lsp=LeanLSPConfig(enabled=True, required=True),
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(
                backend,
                FakeVerifier(),
                config,
                feedback_provider=feedback,
            ).solve("theorem t : 1 = 1 := by omega")
        self.assertFalse(result.success)
        self.assertEqual(result.failure_category, FailureCategory.LEAN_LSP_UNAVAILABLE)
        self.assertEqual(result.end_reason, "LEAN_LSP_UNAVAILABLE")
        self.assertEqual(result.rounds, 0)
        self.assertFalse(backend.loaded)

    def test_optional_lean_lsp_failure_falls_back_to_compiler_diagnostics(self) -> None:
        backend = FakeBackend(
            ["```lean\ntheorem t : 1 = 1 := by rfl\n```"]
        )
        feedback = FakeFeedbackProvider(
            LeanFeedback(available=False, error_message="MCP server unavailable")
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                lean_lsp=LeanLSPConfig(enabled=True, required=False),
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(
                backend,
                FakeVerifier(),
                config,
                feedback_provider=feedback,
            ).solve("theorem t : 1 = 1 := by omega")
        self.assertTrue(result.success)
        self.assertIn("Lean-LSP-MCP unavailable", backend.requests[0][-1].content)

    def test_truncated_candidate_uses_compact_fresh_repair(self) -> None:
        repeated = "theorem t : 1 = 1 := by\n" + "  induction n with\n" * 5
        backend = FakeBackend(
            [repeated, "```lean\ntheorem t : 1 = 1 := by rfl\n```"],
            finish_reasons=["length", "stop"],
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=1024),
                agent=AgentConfig(
                    fallback_enabled=False,
                    log_path=f"{directory}/attempts.jsonl",
                ),
            )
            result = ProofAgent(backend, FakeVerifier(), config).solve(
                "theorem t : 1 = 1 := by sorry"
            )
        self.assertTrue(result.success)
        second_prompt = backend.requests[1][-1].content
        self.assertIn("Discard it completely", second_prompt)
        self.assertNotIn(repeated, second_prompt)
        self.assertEqual(backend.max_token_requests, [1024, 512])

    def test_repeated_rejected_candidate_forces_fresh_strategy(self) -> None:
        rejected = "```lean\ntheorem t : 1 = 1 := by omega\n```"
        backend = FakeBackend(
            [
                rejected,
                rejected,
                "```lean\ntheorem t : 1 = 1 := by rfl\n```",
            ],
            finish_reasons=["stop", "stop", "stop"],
        )
        verifier = FakeVerifier()
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                agent=AgentConfig(
                    fallback_enabled=False,
                    log_path=f"{directory}/attempts.jsonl",
                ),
            )
            result = ProofAgent(backend, verifier, config).solve(
                "theorem t : 1 = 1 := by sorry"
            )
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.model_calls, 3)
        self.assertEqual(result.metrics.kimina_checks, 2)
        self.assertIn("repeated a rejected proof", backend.requests[2][-1].content)
        self.assertIn("<previously_rejected_attempts>", backend.requests[2][-1].content)
        self.assertIn("Attempt 1 rejected approach:", backend.requests[2][-1].content)
        self.assertIn("single short tactic", backend.requests[2][-1].content)

    def test_stops_immediately_when_verifier_is_unavailable(self) -> None:
        backend = FakeBackend(["```lean\ntheorem t : 1 = 1 := by omega\n```"])

        class UnavailableVerifier(LeanVerifier):
            def health_check(self) -> bool:
                return False

            def verify(self, code: str, *, attempt_id: str) -> VerificationResult:
                return VerificationResult(
                    valid=False,
                    diagnostics=("Failed to start REPL",),
                    failure_category=FailureCategory.VERIFIER_UNAVAILABLE,
                )

        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                agent=AgentConfig(log_path=f"{directory}/attempts.jsonl"),
            )
            result = ProofAgent(backend, UnavailableVerifier(), config).solve(
                "theorem t : 1 = 1 := by sorry"
            )
        self.assertFalse(result.success)
        self.assertEqual(result.metrics.iterations, 1)
        self.assertEqual(result.stop_reason, "verifier_unavailable")
        self.assertEqual(result.error_message, "Failed to start REPL")

    def test_false_theorem_stops_after_three_rounds_without_accepting_sorry(self) -> None:
        backend = FakeBackend(
            [
                "```lean\nimport Mathlib\n\ntheorem impossible_test (n : ℕ) : "
                "n + 1 = n := by\n  sorry\n```",
                "```lean\nimport Mathlib\n\ntheorem impossible_test (n : ℕ) : "
                "n + 1 = n := by\n  contradiction\n```",
                "```lean\nimport Mathlib\n\ntheorem impossible_test (n : ℕ) : "
                "n + 1 = n := by\n  omega\n```",
            ]
        )

        class RejectAllVerifier(LeanVerifier):
            def health_check(self) -> bool:
                return True

            def verify(self, code: str, *, attempt_id: str) -> VerificationResult:
                category = (
                    FailureCategory.UNSAFE_PLACEHOLDER
                    if "sorry" in code
                    else FailureCategory.TACTIC_FAILURE
                )
                return VerificationResult(
                    valid=False,
                    diagnostics=("Lean rejected the candidate",),
                    failure_category=category,
                )

        theorem = (
            "import Mathlib\n\n"
            "theorem impossible_test (n : ℕ) : n + 1 = n := by\n  sorry\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=512),
                agent=AgentConfig(
                    max_iterations=3,
                    fallback_enabled=False,
                    log_path=f"{directory}/attempts.jsonl",
                ),
            )
            result = ProofAgent(backend, RejectAllVerifier(), config).solve(theorem)
        self.assertFalse(result.success)
        self.assertEqual(result.end_reason, "MAX_ROUNDS")
        self.assertEqual(result.rounds, 3)
        self.assertEqual(result.stop_reason, "iteration_budget")
        self.assertEqual(result.metrics.kimina_checks, 3)
        self.assertTrue(all(not item.verification.valid for item in result.iterations))


if __name__ == "__main__":
    unittest.main()
