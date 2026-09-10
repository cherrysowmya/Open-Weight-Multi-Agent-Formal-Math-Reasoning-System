"""Opt-in: real compiler/LSP/retrieval, scripted model responses."""
import os
import unittest
from dataclasses import replace
from pathlib import Path

from local_lean_agent.config import load_config
from local_lean_agent.feedback.lean_lsp_mcp import LeanLSPMCPClient
from local_lean_agent.retrieval.lean_explore import LeanExploreMCPClient
from local_lean_agent.verification.kimina import KiminaVerifier
from local_lean_agent.orchestrator import ProofAgent, replace_target_proof
from local_lean_agent.reproducibility import write_json
from local_lean_agent.v3_suite import validate_v3_cases
from local_lean_agent.v4_suite import load_v4_cases
from tests.test_orchestrator import FakeBackend
from tests.test_v4 import ADVICE


@unittest.skipUnless(os.environ.get("LOCAL_LEAN_AGENT_LIVE_LEAN") == "1", "Requires local Lean services")
class V4LeanIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config("config/local.toml")
        cls.verifier = KiminaVerifier(cls.config.kimina)
        if not cls.verifier.health_check():
            raise RuntimeError("Kimina must be running for an opted-in live test")
        cls.feedback = LeanLSPMCPClient(cls.config.lean_lsp)
        cls.retriever = LeanExploreMCPClient(cls.config.lean_explore)

    @classmethod
    def tearDownClass(cls):
        cls.feedback.close()
        cls.retriever.close()

    def run_agent(self, name, task, outputs):
        config = replace(self.config,
            informal_reasoning=replace(self.config.informal_reasoning, enabled=False),
            v4=replace(self.config.v4, enabled=True),
            agent=replace(self.config.agent, max_iterations=3, fallback_enabled=False,
                          log_path=Path("runs/v4-integration-events.jsonl")))
        backend = FakeBackend(outputs)
        result = ProofAgent(backend, self.verifier, config, feedback_provider=self.feedback,
                            retriever=self.retriever).solve(task)
        recheck = self.verifier.verify(result.final_proof, attempt_id=name + "-recheck") if result.success else None
        write_json(Path(f"runs/v4-integration-{name}.json"), {
            "test_type": "scripted_model_real_lean_lsp_retrieval", "attempt": result.to_dict(),
            "independent_recheck": recheck})
        return result, recheck

    def test_manifest_preflight(self):
        validation = validate_v3_cases(load_v4_cases("benchmarks/v4/manifest.toml"), self.verifier)
        write_json(Path("runs/v4-validation.json"), validation)
        self.assertTrue(validation["passed"], validation)

    def test_two_errors_then_fresh_context_proves_all_goals(self):
        task = "import Mathlib\n\ntheorem local_goals (P Q : Prop) (hp : P) (hq : Q) : P ∧ Q := by sorry"
        result, recheck = self.run_agent("fresh-goals", task, [
            replace_target_proof(task, "by constructor\n   · exact hp\n   · exact hp"),
            replace_target_proof(task, "by exact hp"), ADVICE,
            replace_target_proof(task, "by exact ⟨hp, hq⟩"),
        ])
        self.assertTrue(result.success, result.error_message)
        self.assertTrue(recheck.valid)
        self.assertEqual(result.metrics.fresh_subproblem_calls, 1)
        self.assertEqual(result.metrics.discussion_partner_calls, 1)
        self.assertEqual(result.metrics.kimina_checks, 3)
        self.assertTrue(result.iterations[1].lean_feedback.goal_state)
        self.assertIn("hp", result.v4_requests[-1].messages[-1].content)

    def test_worker_cannot_prove_only_one_of_two_goals(self):
        task = "import Mathlib\n\ntheorem local_goals (P Q : Prop) (hp : P) (hq : Q) : P ∧ Q := by sorry"
        result, _ = self.run_agent("incomplete-goals", task, [
            replace_target_proof(task, "by exact hp"),
            replace_target_proof(task, "by exact hq"), ADVICE,
            replace_target_proof(task, "by constructor\n   · exact hp\n   · skip"),
        ])
        self.assertFalse(result.success)
        self.assertEqual(result.end_reason, "MAX_ROUNDS")
        self.assertEqual(result.metrics.fresh_subproblem_calls, 1)

    def test_false_theorem_stays_rejected_after_discussion(self):
        task = Path("benchmarks/v4/cases/false_successor.lean").read_text()
        result, _ = self.run_agent("false-theorem", task, [
            replace_target_proof(task, "by rfl"),
            replace_target_proof(task, "by omega"), ADVICE,
            replace_target_proof(task, "by sorry"),
        ])
        self.assertFalse(result.success)
        self.assertEqual(result.rounds, 3)
        self.assertEqual(result.end_reason, "MAX_ROUNDS")
        self.assertEqual(result.metrics.fresh_subproblem_calls, 1)


if __name__ == "__main__":
    unittest.main()
