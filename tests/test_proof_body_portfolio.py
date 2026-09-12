"""Proof-slot integrity, prompt contracts, and independently attributed automation."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest

from local_lean_agent.config import AppConfig, AgentConfig, GenerationConfig, V4Config, load_config
from local_lean_agent.orchestrator import ProofAgent
from local_lean_agent.portfolio import solve_portfolio
from local_lean_agent.proof_body import ProofSlot, body_prompt, masked_source
from local_lean_agent.types import FailureCategory, VerificationResult
from tests.test_orchestrator import FakeBackend

TASK = "import Mathlib\n\ntheorem t (n : Nat) : n = n := by\n  sorry\n"


class RecordingVerifier:
    def __init__(self, accept="rfl", unavailable=False):
        self.sources = []
        self.accept = accept
        self.unavailable = unavailable

    def verify(self, code, *, attempt_id):
        self.sources.append(code)
        if self.unavailable:
            return VerificationResult(False, ("offline",), FailureCategory.VERIFIER_UNAVAILABLE)
        valid = self.accept in code and "sorry" not in code
        return VerificationResult(valid, () if valid else ("unsolved goals",),
                                  FailureCategory.NONE if valid else FailureCategory.UNSOLVED_GOALS)


class ProofBodyTests(unittest.TestCase):
    def run_agent(self, responses, *, task=TASK, v4=False, finishes=None):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(responses, finishes)
            verifier = RecordingVerifier()
            cfg = AppConfig(generation=GenerationConfig(proof_format="proof_body", max_output_tokens=512,
                                 output_budget_policy="adaptive"),
                            v4=V4Config(enabled=v4, trigger_after_failures=1, discussion_enabled=False),
                            agent=AgentConfig(fallback_enabled=False, max_iterations=max(1, len(responses)),
                                              log_path=Path(directory) / "events.jsonl"))
            result = ProofAgent(backend, verifier, cfg).solve(task)
        return result, backend, verifier

    def test_imports_statement_comments_and_suffix_are_exactly_preserved(self):
        task = "import Mathlib\nnamespace Demo\n-- original task\ntheorem t : True := by sorry\nend Demo\n"
        slot = ProofSlot.from_source(task)
        result = slot.assemble("by\n  trivial")
        self.assertEqual(slot.prefix, task[:task.index(":=") + 2])
        self.assertTrue(result.startswith(slot.prefix))
        self.assertTrue(result.endswith("end Demo\n"))
        self.assertNotIn("sorry", result)

    def test_default_binder_assignments_are_not_proof_assignments(self):
        source = "theorem t (n : Nat := 1) : n = n := by sorry"
        self.assertEqual(ProofSlot.from_source(source).prefix, source.rsplit(" :=", 1)[0] + " :=")

    def test_comments_strings_and_nested_comments_do_not_create_targets(self):
        source = 'def note := "theorem fake :="\n/- outer /- theorem fake := -/ := -/\n' + TASK
        slot = ProofSlot.from_source(source)
        self.assertTrue(slot.prefix.endswith("n = n :="))
        self.assertEqual(len(masked_source(source)), len(source))
        self.assertEqual(masked_source(source).count("\n"), source.count("\n"))

    def test_prior_helper_proof_is_immutable(self):
        helper = "theorem helper : True := by trivial\n"
        slot = ProofSlot.from_source(helper + TASK)
        self.assertTrue(slot.assemble("by rfl").startswith(helper))

    def test_unsupported_target_and_trailing_commands_fail_closed(self):
        for task in ("def x := 1", TASK + "def changed := 1\n",
                     TASK + "end Demo\naxiom falsehood : False\n", "theorem t : True"):
            with self.subTest(task=task), self.assertRaises(ValueError):
                ProofSlot.from_source(task)

    def test_file_responses_and_command_injections_are_rejected(self):
        slot = ProofSlot.from_source(TASK)
        for body in (TASK, "by rfl\nend Demo", "by rfl\naxiom bad : False", "", "import Mathlib\nby rfl"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                slot.assemble(body)

    def test_term_and_tactic_bodies_reach_compiler_as_complete_tasks(self):
        for body in ("by\n  rfl", "Eq.refl n", "```lean\nby rfl\n```"):
            with self.subTest(body=body):
                result, backend, verifier = self.run_agent([body])
                self.assertTrue(verifier.sources[0].startswith(TASK.split(":=")[0] + ":="))
                self.assertEqual(backend.calls, 1)
                self.assertNotIn("sorry", verifier.sources[0])
                self.assertEqual(result.iterations[0].generation.text, body)

    def test_matching_whole_file_response_is_extracted_without_extra_model_call(self):
        result, backend, verifier = self.run_agent([TASK.replace("sorry", "rfl")])
        self.assertTrue(result.success)
        self.assertEqual(result.iterations[0].proof_body_normalization, "matching_declaration_extracted")
        self.assertEqual(len(verifier.sources), 1)
        self.assertEqual(backend.calls, 1)

    def test_changed_whole_file_response_is_a_repairable_format_failure(self):
        result, backend, verifier = self.run_agent([TASK.replace("n = n", "True").replace("sorry", "trivial"), "by rfl"])
        self.assertTrue(result.success)
        self.assertEqual(result.iterations[0].verification.failure_category, FailureCategory.TASK_MUTATION)
        self.assertEqual(len(verifier.sources), 1)
        self.assertEqual(backend.max_token_requests, [512, 512])

    def test_matching_header_only_and_delimiter_are_normalized(self):
        for body, action in ((TASK.split("\n\n")[1].replace("sorry", "rfl"), "matching_declaration_extracted"),
                             (":= by\n  rfl", "assignment_delimiter_removed")):
            with self.subTest(body=body):
                result, backend, verifier = self.run_agent([body])
                self.assertTrue(result.success)
                self.assertEqual(result.iterations[0].proof_body_normalization, action)
                self.assertTrue(verifier.sources[0].startswith("import Mathlib"))
                self.assertEqual(backend.calls, 1)

    def test_response_recovery_never_adopts_changed_task_material(self):
        slot = ProofSlot.from_source(TASK)
        responses = (
            TASK.replace("n = n", "n = 0"),
            TASK.replace("(n : Nat)", "(n : Int)"),
            TASK.replace("import Mathlib", "import Mathlib\nset_option maxRecDepth 0"),
            TASK.replace("import Mathlib", "import Init"),
            TASK.replace("theorem t", "theorem other"),
            TASK + "theorem extra : True := by trivial",
            TASK + "\nend Unexpected",
            "axiom h : False\n" + TASK,
            ":= by rfl\nend Unexpected",
        )
        for response in responses:
            with self.subTest(response=response), self.assertRaises(ValueError):
                slot.assemble_response(response)

    def test_full_response_recovery_preserves_namespace_suffix_and_layout(self):
        source = "import Mathlib\nnamespace Demo\ntheorem t : True := by sorry\nend Demo\n"
        slot = ProofSlot.from_source(source)
        for response in (source.replace("sorry", "trivial"), "theorem t : True := by trivial"):
            assembled, action = slot.assemble_response(response)
            self.assertTrue(assembled.startswith(slot.prefix))
            self.assertTrue(assembled.endswith(slot.suffix))
            self.assertEqual(action, "matching_declaration_extracted")

    def test_wrapped_invalid_proof_is_still_a_lean_failure(self):
        result, _, verifier = self.run_agent([TASK.replace("sorry", "contradiction")])
        self.assertFalse(result.success)
        self.assertEqual(len(verifier.sources), 1)
        self.assertEqual(result.iterations[0].verification.failure_category, FailureCategory.UNSOLVED_GOALS)

    def test_layout_tolerance_does_not_merge_identifier_tokens(self):
        source = "theorem t (ab : Nat) : ab = ab := by sorry"
        with self.assertRaises(ValueError):
            ProofSlot.from_source(source).assemble_response(source.replace("ab : Nat", "a b : Nat"))

    def test_wrapped_response_recovered_in_fresh_context(self):
        result, backend, _ = self.run_agent(["by contradiction", TASK.replace("sorry", "rfl")], v4=True)
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.fresh_subproblem_calls, 1)
        self.assertIn("Do NOT include the `:=`", backend.requests[1][0].content)
        self.assertEqual(result.iterations[-1].proof_body_normalization, "matching_declaration_extracted")

    def test_repair_and_fresh_prompts_request_bodies_not_files(self):
        for v4 in (False, True):
            result, backend, verifier = self.run_agent(["by\n  have h : True := by", "by rfl"],
                                                       finishes=["length", "stop"], v4=v4)
            self.assertTrue(result.success)
            self.assertEqual(backend.max_token_requests, [512, 1024])
            for request in backend.requests:
                system = request[0].content
                self.assertIn("OUTPUT CONTRACT", system)
                self.assertNotRegex(system, r"complete\s+Lean\s+file")
                self.assertNotIn("COMPLETE replacement Lean file", system)
            self.assertEqual(len(verifier.sources), 2)

    def test_prompt_adaptation_does_not_rewrite_task_or_candidate(self):
        evidence = "<original_task>-- complete Lean file\ntheorem t : True := by sorry</original_task>"
        self.assertIn(evidence, body_prompt("Return a complete Lean file.\n" + evidence))

    def test_correct_seed_bypasses_model(self):
        result, backend, _ = self.run_agent([], task=TASK.replace("sorry", "rfl"))
        self.assertTrue(result.success)
        self.assertFalse(backend.loaded)
        self.assertEqual(backend.calls, 0)

    def test_unsupported_input_fails_before_model(self):
        result, backend, _ = self.run_agent([], task=TASK + "def extra := 1")
        self.assertFalse(result.success)
        self.assertEqual(result.stop_reason, "unsupported_proof_slot")
        self.assertEqual(backend.calls, 0)


class PortfolioTests(unittest.TestCase):
    def config(self, directory):
        return AppConfig(agent=AgentConfig(fallback_tactics=("rfl", "simp", "omega"),
                         log_path=Path(directory) / "events.jsonl"))

    def test_only_portfolio_records_no_model_calls_and_stops_on_success(self):
        with tempfile.TemporaryDirectory() as directory:
            verifier = RecordingVerifier(accept="simp")
            result = solve_portfolio(TASK, verifier, self.config(directory))
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.portfolio_checks, 2)
        self.assertEqual(result.metrics.kimina_checks, 2)
        self.assertEqual(result.metrics.model_calls, 0)
        self.assertEqual(result.metrics.model_load_seconds, 0)
        self.assertEqual(result.metrics.lean_lsp_calls, 0)
        self.assertEqual(result.metrics.retrieval_calls, 0)
        self.assertEqual(result.metrics.portfolio_successes, 1)
        self.assertEqual(result.fallback_attempts[-1].tactic, "simp")
        self.assertGreaterEqual(result.metrics.portfolio_wall_clock_seconds, 0)

    def test_exhaustion_is_bounded_and_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            result = solve_portfolio(TASK, RecordingVerifier("never"), self.config(directory))
        self.assertFalse(result.success)
        self.assertEqual(result.stop_reason, "portfolio_exhausted")
        self.assertEqual(result.metrics.portfolio_checks, 3)
        self.assertEqual(result.metrics.portfolio_successes, 0)

    def test_outage_stops_not_a_mathematical_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            result = solve_portfolio(TASK, RecordingVerifier(unavailable=True), self.config(directory))
        self.assertEqual(result.stop_reason, "verifier_unavailable")
        self.assertEqual(result.metrics.portfolio_checks, 1)

    def test_agent_plus_portfolio_counts_separately_and_never_retries_tactics(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.config(directory)
            cfg = replace(cfg, generation=GenerationConfig(proof_format="proof_body"),
                          agent=replace(cfg.agent, max_iterations=2))
            result = ProofAgent(FakeBackend(["by contradiction", "by decide"]),
                                RecordingVerifier("never"), cfg).solve(TASK)
        self.assertFalse(result.success)
        self.assertEqual(result.metrics.model_calls, 2)
        self.assertEqual(result.metrics.portfolio_checks, 3)
        self.assertEqual(result.metrics.kimina_checks, 5)


@unittest.skipUnless(os.environ.get("RUN_PROOF_BODY_LEAN_TESTS") == "1", "requires local Kimina")
class LiveProofBodyTests(unittest.TestCase):
    def test_assembled_body_and_portfolio_use_real_lean_including_negative_case(self):
        from local_lean_agent.verification.kimina import KiminaVerifier
        cfg = load_config(Path(__file__).resolve().parents[1] / "config/local.toml")
        verifier = KiminaVerifier(replace(cfg.kimina, reuse_repl=False))
        theorem = "import Mathlib\ntheorem square (x y : ℝ) : 2*x*y ≤ x^2+y^2 := by sorry"
        source = ProofSlot.from_source(theorem).assemble("by\n  nlinarith [sq_nonneg (x-y)]")
        self.assertTrue(verifier.verify(source, attempt_id="body-square").valid)
        self.assertFalse(verifier.verify(ProofSlot.from_source(theorem).assemble("by sorry"),
                                         attempt_id="body-sorry").valid)
        for response in (theorem.replace("by sorry", "by nlinarith [sq_nonneg (x-y)]"),
                         ":= by nlinarith [sq_nonneg (x-y)]"):
            assembled, _ = ProofSlot.from_source(theorem).assemble_response(response)
            self.assertTrue(verifier.verify(assembled, attempt_id="normalized-square").valid)
        negative_slot = ProofSlot.from_source("import Mathlib\ntheorem impossible : False := by sorry")
        assembled, _ = negative_slot.assemble_response("theorem impossible : False := by sorry")
        self.assertFalse(verifier.verify(assembled, attempt_id="normalized-negative").valid)
        with tempfile.TemporaryDirectory() as directory:
            cfg = replace(cfg, agent=replace(cfg.agent, log_path=Path(directory)/"events.jsonl"))
            result = solve_portfolio(TASK, verifier, cfg)
            self.assertTrue(result.success)
            self.assertTrue(verifier.verify(result.final_proof, attempt_id="portfolio-audit").valid)
            negative = solve_portfolio("import Mathlib\ntheorem impossible (n : ℕ) : n+1=n := by sorry",
                                       verifier, cfg)
            self.assertFalse(negative.success)
            self.assertEqual(negative.metrics.portfolio_checks, len(cfg.agent.fallback_tactics))
