"""Opt-in real Lean/LSP/retrieval integration, with deterministic model replies.

Run: LOCAL_LEAN_AGENT_LIVE_LEAN=1 .venv/bin/python -m unittest tests.test_v3_lean_integration -v
These tests demonstrate wiring, not model quality or a causal accuracy gain.
"""
import os
from pathlib import Path
import unittest
from dataclasses import replace

from local_lean_agent.config import load_config
from local_lean_agent.feedback.lean_lsp_mcp import LeanLSPMCPClient
from local_lean_agent.informal.qwen import QwenInformalReasoner
from local_lean_agent.orchestrator import ProofAgent, replace_target_proof
from local_lean_agent.reproducibility import write_json
from local_lean_agent.retrieval.lean_explore import LeanExploreMCPClient
from local_lean_agent.verification.kimina import KiminaVerifier
from tests.test_orchestrator import FakeBackend
from tests.test_v3_contracts import PASS


AMGM = "import Mathlib\n\ntheorem square_difference (x y : ℝ) : 2*x*y ≤ x^2+y^2 := by\n  sorry\n"
AMGM_PLAN = "<informal_proof>Since (x-y)^2 is nonnegative, expand it to x^2-2*x*y+y^2. Rearranging yields the desired inequality.</informal_proof>"
AMGM_PLAN_WITH_QUERY = AMGM_PLAN + (
    '<lemma_queries>["Nonnegativity of squares of real numbers"]</lemma_queries>'
)


@unittest.skipUnless(os.environ.get("LOCAL_LEAN_AGENT_LIVE_LEAN") == "1",
                     "Opt-in: requires local Kimina, LSP, and LeanExplore")
class V3LeanIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config("config/local.toml")
        cls.verifier = KiminaVerifier(cls.config.kimina)
        if not cls.verifier.health_check():
            raise RuntimeError("Kimina unavailable; an opted-in integration run must not silently skip")
        cls.feedback = LeanLSPMCPClient(cls.config.lean_lsp)
        cls.retriever = LeanExploreMCPClient(cls.config.lean_explore)

    @classmethod
    def tearDownClass(cls):
        cls.feedback.close()
        cls.retriever.close()

    def run_agent(self, name, task, responses, *, rounds=3, policy="after_model_failures", threshold=2):
        config = replace(self.config,
            v4=replace(self.config.v4, enabled=False),
            informal_reasoning=replace(self.config.informal_reasoning, enabled=True,
                verifier_enabled=True, invocation_policy=policy, trigger_after_failures=threshold),
            agent=replace(self.config.agent, max_iterations=rounds, fallback_enabled=False,
                          log_path=Path("runs/v3-integration-events.jsonl")))
        backend = FakeBackend(responses)
        result = ProofAgent(backend, self.verifier, config, feedback_provider=self.feedback,
            retriever=self.retriever, informal_reasoner=QwenInformalReasoner(backend, config.informal_reasoning)).solve(task)
        write_json(Path(f"runs/v3-integration-{name}.json"), {
            "test_type": "scripted_model_real_lean_lsp_retrieval", "attempt": result.to_dict(),
        })
        return result, backend

    def test_two_failures_then_reviewed_square_difference_plan_compiles(self):
        result, backend = self.run_agent("square-difference", AMGM, [
            replace_target_proof(AMGM, "by\n  rfl"),
            replace_target_proof(AMGM, "by\n  exact 0"),
            AMGM_PLAN, PASS,
            replace_target_proof(AMGM, "by\n  nlinarith [sq_nonneg (x-y)]"),
        ])
        self.assertTrue(result.success, result.error_message)
        self.assertEqual([r.verification.valid for r in result.iterations], [False, False, True])
        self.assertEqual(result.metrics.kimina_checks, 3)
        self.assertGreaterEqual(result.metrics.lean_lsp_calls, 2)
        self.assertGreater(result.metrics.retrieval_calls, 0)
        self.assertEqual(result.metrics.informal_generator_calls, 1)
        self.assertEqual(result.metrics.informal_verifier_calls, 1)
        self.assertIn("(x-y)^2", backend.requests[-1][-1].content)
        self.assertTrue(self.verifier.verify(result.final_proof, attempt_id="v3-independent-amgm").valid)

    def test_strategy_search_and_rewrite_recovery_use_real_services(self):
        seeded = AMGM.replace("sorry", (
            "have h : (x-y)^2 ≥ 0 := by positivity\n"
            "  rw [sq_le (le_refl 0)] at h\n"
            "  linarith"
        ))
        result, backend = self.run_agent("strategy-rewrite", seeded, [
            AMGM_PLAN_WITH_QUERY, PASS,
            replace_target_proof(seeded, "by\n  nlinarith [sq_nonneg (x-y)]"),
        ], policy="always")
        self.assertTrue(result.success, result.error_message)
        self.assertEqual([record.iteration for record in result.iterations], [0, 1])
        self.assertFalse(result.iterations[0].verification.valid)
        self.assertEqual(result.iterations[1].repair_action, "invalid_rewrite_reset")
        self.assertEqual(result.metrics.strategy_retrieval_queries, 1)
        self.assertEqual(result.metrics.rewrite_recovery_prompts, 1)
        self.assertEqual(result.iterations[1].strategy_retrieval[0].query,
                         "Nonnegativity of squares of real numbers")
        # Wiring contract, not a semantic-ranking guarantee for a particular query.
        names = {hit.name for hit in result.iterations[1].strategy_retrieval[0].hits}
        self.assertTrue(names)
        self.assertTrue(any(name in backend.requests[-1][-1].content for name in names))
        self.assertNotIn(seeded, backend.requests[-1][-1].content)
        self.assertTrue(self.verifier.verify(result.final_proof,
                                             attempt_id="v3-independent-strategy-rewrite").valid)

    def test_bad_inline_fact_and_rewrite_are_repaired_only_after_lean_accepts(self):
        candidate = replace_target_proof(AMGM, "by\n"
            "  have h : (x-y)^2 ≥ 0 := sq_le (le_refl 0)\n"
            "  rw [h]\n  linarith")
        result, _ = self.run_agent("prefix-salvage", AMGM,
            [AMGM_PLAN, PASS, candidate], rounds=1, policy="always")
        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(result.metrics.model_calls, 1)
        self.assertEqual(result.metrics.rewrite_salvage_successes, 1)
        self.assertEqual(result.iterations[-1].generation.model, "compiler_prefix_salvage")
        self.assertTrue(self.verifier.verify(result.final_proof,
                                             attempt_id="v3-independent-prefix-salvage").valid)

    def test_prefix_salvage_cannot_assume_a_false_local_fact(self):
        task = "import Mathlib\n\ntheorem impossible (n : ℕ) : n + 1 = n := by sorry"
        bad = replace_target_proof(task, "by\n"
            "  have h : n + 1 ≤ n := le_rfl 1 2\n"
            "  rw [h]\n  omega")
        result, _ = self.run_agent("false-prefix-salvage", task, [
            "<informal_proof>An intentionally false argument.</informal_proof>", PASS,
            bad, bad.replace("omega", "linarith"),
        ], rounds=2, policy="always")
        self.assertFalse(result.success, result.to_dict())
        self.assertEqual(result.end_reason, "MAX_ROUNDS")
        self.assertEqual(result.rounds, 2)
        self.assertGreater(result.metrics.rewrite_salvage_checks, 0)
        self.assertLessEqual(result.metrics.rewrite_salvage_checks, 8)
        self.assertEqual(result.metrics.rewrite_salvage_successes, 0)
        self.assertTrue(all(not attempt.verification.valid for attempt in result.fallback_attempts))

    def test_informal_pass_still_cannot_prove_a_false_statement(self):
        task = "import Mathlib\n\ntheorem false_sign (x : ℝ) : x^2 < 0 := by sorry"
        result, _ = self.run_agent("false-pass", task, [
            "<informal_proof>Pretend this false statement follows.</informal_proof>", PASS,
            replace_target_proof(task, "by\n  sorry"),
            replace_target_proof(task, "by\n  positivity"),
        ], rounds=2, policy="always")
        self.assertTrue(result.informal_reasoning.accepted)
        self.assertFalse(result.success)
        self.assertEqual(result.end_reason, "MAX_ROUNDS")
        self.assertTrue(all(not r.verification.valid for r in result.iterations))

    def test_trivial_lean_success_does_not_invoke_informal_roles(self):
        task = "import Mathlib\n\ntheorem easy (n : ℕ) : n + 0 = n := by sorry"
        result, _ = self.run_agent("easy", task, [replace_target_proof(task, "by rfl")])
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.informal_generator_calls, 0)


if __name__ == "__main__":
    unittest.main()
