"""Deterministic output-policy contracts; no claim about model proof quality."""
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest

from local_lean_agent.cli import build_parser, _output_budget_overrides
from local_lean_agent.config import AppConfig, AgentConfig, GenerationConfig, V4Config, _validate, load_config
from local_lean_agent.orchestrator import ProofAgent
from local_lean_agent.output_budget import choose_output_budget, output_status, repetitive_output
from local_lean_agent.types import FailureCategory, GenerationResult, IterationRecord, VerificationResult
from tests.test_orchestrator import FakeBackend

TASK = "theorem t : 1 = 1 := by sorry"
GOOD = TASK.replace("sorry", "rfl")
SHORT = TASK.replace("sorry", "have h1 : 1 = 1 := by")
LONGER = TASK.replace("sorry", "have h2 : 2 = 2 := by")
OTHER = TASK.replace("sorry", "have h3 : 3 = 3 := by")


class ExactVerifier:
    def __init__(self):
        self.calls = []

    def verify(self, code, *, attempt_id):
        self.calls.append(code)
        valid = code.strip() == GOOD
        return VerificationResult(valid, () if valid else ("unexpected end of input",),
                                  FailureCategory.NONE if valid else FailureCategory.LEAN_SYNTAX)


class OutputBudgetTests(unittest.TestCase):
    def run_agent(self, responses, finishes, *, policy="adaptive", initial=512, ceiling=2048,
                  v4=None, context=12288, task=TASK):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(responses, finishes)
            verifier = ExactVerifier()
            config = AppConfig(
                generation=GenerationConfig(max_output_tokens=initial, max_context_tokens=context,
                    output_budget_policy=policy, max_recovery_output_tokens=ceiling),
                v4=v4 or V4Config(),
                agent=AgentConfig(max_iterations=len(responses), fallback_enabled=False,
                                  log_path=Path(directory) / "events.jsonl"))
            result = ProofAgent(backend, verifier, config).solve(task)
            events = [json.loads(line) for line in Path(config.agent.log_path).read_text().splitlines()]
        return result, backend, verifier, events

    def test_adaptive_unfinished_proofs_grow_512_1024_2048(self):
        result, backend, verifier, _ = self.run_agent([SHORT, LONGER, GOOD], ["length", "length", "stop"])
        self.assertTrue(result.success)
        self.assertEqual(backend.max_token_requests, [512, 1024, 2048])
        self.assertEqual(len(verifier.calls), 3)
        self.assertIn(SHORT, backend.requests[1][-1].content)
        self.assertIn("COMPLETE replacement", backend.requests[1][-1].content)
        self.assertEqual(result.metrics.formal_output_budget_increases, 2)

    def test_all_fixed_budgets_apply_to_resets_and_fresh_contexts(self):
        for tokens in (512, 1024, 2048):
            with self.subTest(tokens=tokens):
                result, backend, _, _ = self.run_agent([SHORT, GOOD], ["length", "stop"],
                    policy="fixed", initial=tokens,
                    v4=V4Config(enabled=True, discussion_enabled=False, trigger_after_failures=1))
                self.assertTrue(result.success)
                self.assertEqual(backend.max_token_requests, [tokens, tokens])
                self.assertEqual(result.metrics.fresh_subproblem_calls, 1)
                self.assertEqual(result.iterations[1].requested_output_tokens, tokens)

    def test_adaptive_growth_is_not_capped_by_v4_fresh_setting(self):
        result, backend, _, _ = self.run_agent([SHORT, LONGER, GOOD], ["length", "length", "stop"],
            v4=V4Config(enabled=True, discussion_enabled=False, fresh_max_output_tokens=512))
        self.assertEqual(backend.max_token_requests, [512, 1024, 2048])
        self.assertEqual(result.v4_requests[-1].role, "fresh_subproblem")
        self.assertIn("output_budget_recovery", result.v4_requests[-1].messages[0].content)
        self.assertTrue(result.success)

    def test_repeated_renamed_assertions_reset_without_more_tokens(self):
        repeated = TASK.replace("sorry", "\n" + "\n".join(
            f"  have hp{i} : 1 = 1 := by omega" for i in range(7)))
        self.assertTrue(repetitive_output(repeated))
        result, backend, _, _ = self.run_agent([repeated, GOOD], ["length", "stop"])
        self.assertEqual(backend.max_token_requests, [512, 512])
        self.assertEqual(result.iterations[0].output_status, "truncated_repetitive")
        self.assertIn("Discard it completely", backend.requests[1][-1].content)
        self.assertNotIn(repeated, backend.requests[1][-1].content)

    def test_duplicate_failed_candidates_do_not_earn_growth(self):
        result, backend, _, _ = self.run_agent([SHORT, SHORT, GOOD], ["length", "length", "stop"])
        self.assertEqual(backend.max_token_requests, [512, 1024, 512])
        self.assertEqual(result.iterations[1].output_status, "truncated_repetitive")

    def test_single_line_rewrite_loop_resets_including_fresh_context(self):
        repeated = TASK.replace("sorry", "rw [sub_sq, " + ", ".join(["mul_two"] * 30))
        for v4 in (V4Config(), V4Config(enabled=True, discussion_enabled=False,
                                      trigger_after_failures=1)):
            with self.subTest(v4=v4.enabled):
                result, backend, verifier, _ = self.run_agent(
                    [repeated, GOOD], ["length", "stop"], v4=v4)
                self.assertTrue(result.success)
                self.assertEqual(backend.max_token_requests, [512, 512])
                self.assertEqual(result.iterations[0].output_status, "truncated_repetitive")
                self.assertEqual(result.iterations[1].output_budget_action, "reset_repetitive")
                self.assertEqual(result.metrics.formal_output_repetitions, 1)
                self.assertEqual(result.metrics.formal_output_budget_increases, 0)
                self.assertIn(repeated, [code.strip() for code in verifier.calls])

    def test_rewrite_repetition_threshold_and_identifier_boundaries(self):
        for name in ("mul_two", "Nat.add_assoc", "h'"):
            # Wrap between groups, avoiding the separate five-identical-lines
            # heuristic so these checks isolate comma-list token boundaries.
            with self.subTest(name=name):
                self.assertFalse(repetitive_output("rw [" + ", ".join([name] * 7) + "]"))
                self.assertTrue(repetitive_output("rw [" + ", ".join([name] * 8) + "]"))
                self.assertTrue(repetitive_output("rw [" + ", ".join([name] * 8)))
                self.assertFalse(repetitive_output("rw [" + ", ".join(
                    [name] * 7 + [name + "_other"]) + "]"))
                self.assertTrue(repetitive_output("rw [" + ", ".join([name] * 4)
                                                 + ",\n " + ", ".join([name] * 4)))

    def test_nonconsecutive_lemmas_and_comments_are_not_rewrite_loops(self):
        self.assertFalse(repetitive_output("rw [" + ", ".join(["mul_two", "add_assoc"] * 8) + "]"))
        self.assertFalse(repetitive_output("-- rw [" + ", ".join(["mul_two"] * 8) + "]"))

    def test_flagged_rewrite_list_does_not_override_compiler_acceptance(self):
        from unittest.mock import patch
        repeated = TASK.replace("sorry", "rw [" + ", ".join(["mul_two"] * 8) + "]")
        with patch.object(ExactVerifier, "verify", return_value=VerificationResult(True)):
            result, backend, _, _ = self.run_agent([repeated], ["length"])
        self.assertTrue(result.success)
        self.assertEqual(result.iterations[0].output_status, "truncated_repetitive")
        self.assertEqual(backend.calls, 1)

    def test_repetitive_nontruncated_output_is_distinguished(self):
        repeated = TASK.replace("sorry", "\n" + "  simp\n" * 6)
        result, backend, _, _ = self.run_agent([repeated, GOOD], ["stop", "stop"])
        self.assertEqual(result.iterations[0].output_status, "repetitive")
        self.assertEqual(result.metrics.formal_output_truncations, 0)
        self.assertEqual(backend.max_token_requests, [512, 512])

    def test_ceiling_stops_growth_but_not_lean_round_accounting(self):
        result, backend, _, _ = self.run_agent([SHORT, LONGER, OTHER], ["length"] * 3, ceiling=1024)
        self.assertEqual(backend.max_token_requests, [512, 1024, 1024])
        self.assertEqual(result.iterations[-1].output_budget_action, "retry_at_ceiling")
        self.assertEqual(result.rounds, 3)
        self.assertFalse(result.success)
        self.assertEqual(result.stop_reason, "iteration_budget")

    def test_length_finish_is_not_itself_a_proof_rejection(self):
        result, backend, verifier, _ = self.run_agent([GOOD], ["length"])
        self.assertTrue(result.success)
        self.assertEqual(result.metrics.formal_output_truncations, 1)
        self.assertEqual(result.iterations[0].verification.failure_category, FailureCategory.NONE)
        self.assertEqual(len(verifier.calls), 1)

    def test_truncation_and_compiler_error_are_independent_fields(self):
        result, _, _, events = self.run_agent([SHORT], ["length"])
        record = result.iterations[0]
        self.assertEqual(record.output_status, "truncated_nonrepetitive")
        self.assertEqual(record.verification.failure_category, FailureCategory.LEAN_SYNTAX)
        request = next(e["payload"] for e in events if e["event"] == "formal_output_budget_selected")
        self.assertEqual(request["requested_output_tokens"], 512)
        self.assertEqual(request["policy"], "adaptive")

    def test_normal_compiler_failure_does_not_expand_budget(self):
        result, backend, _, _ = self.run_agent([SHORT, GOOD], ["stop", "stop"])
        self.assertEqual(backend.max_token_requests, [512, 512])
        self.assertEqual(result.metrics.formal_output_budget_increases, 0)

    def test_larger_budget_is_preserved_for_followup_repairs(self):
        _, backend, _, _ = self.run_agent([SHORT, LONGER, GOOD], ["length", "stop", "stop"])
        self.assertEqual(backend.max_token_requests, [512, 1024, 1024])

    def test_mutated_task_does_not_earn_growth(self):
        bad = "theorem other : True := by trivial"
        result, backend, verifier, _ = self.run_agent([bad, GOOD], ["length", "stop"])
        self.assertEqual(backend.max_token_requests, [512, 512])
        self.assertEqual(result.iterations[0].verification.failure_category, FailureCategory.TASK_MUTATION)
        self.assertEqual(len(verifier.calls), 1)

    def test_placeholder_failure_does_not_earn_growth(self):
        previous = IterationRecord(1, TASK,
            VerificationResult(False, failure_category=FailureCategory.UNSAFE_PLACEHOLDER),
            GenerationResult(TASK, "mock", finish_reason="length"), 50, requested_output_tokens=512)
        decision = choose_output_budget(GenerationConfig(output_budget_policy="adaptive"), previous)
        self.assertEqual(decision.action, "reset_invalid_task")

    def test_legacy_preserves_historical_reset_limit(self):
        _, backend, _, _ = self.run_agent([SHORT, GOOD], ["length", "stop"], policy="legacy", initial=1024)
        self.assertEqual(backend.max_token_requests, [1024, 512])

    def test_legacy_reset_limit_is_configurable(self):
        previous = IterationRecord(1, SHORT, VerificationResult(False),
            GenerationResult(SHORT, "mock", finish_reason="length"), 50)
        config = GenerationConfig(max_output_tokens=2048, reset_output_tokens=1024)
        self.assertEqual(choose_output_budget(config, previous).tokens, 1024)

    def test_verified_input_does_not_load_or_call_model(self):
        result, backend, _, _ = self.run_agent([], [], task=GOOD)
        self.assertTrue(result.success)
        self.assertEqual(backend.calls, 0)
        self.assertEqual(result.metrics.formal_output_budget_requests, [])

    def test_oversized_main_prompt_never_reaches_backend(self):
        result, backend, _, _ = self.run_agent([GOOD], ["stop"], initial=2048, context=2100)
        self.assertEqual(result.failure_category, FailureCategory.CONTEXT_BUDGET)
        self.assertEqual(backend.calls, 0)

    def test_oversized_isolated_prompt_never_reaches_backend(self):
        result, backend, _, _ = self.run_agent([SHORT, GOOD], ["length", "stop"],
            v4=V4Config(enabled=True, discussion_enabled=False, trigger_after_failures=1,
                        max_context_tokens=2048))
        self.assertEqual(result.failure_category, FailureCategory.CONTEXT_BUDGET)
        self.assertEqual(backend.calls, 1)

    def test_every_sent_request_reserves_input_plus_output_space(self):
        result, _, _, _ = self.run_agent([SHORT, LONGER, GOOD], ["length", "length", "stop"],
                                       v4=V4Config(enabled=True, discussion_enabled=False))
        for request in result.metrics.formal_output_budget_requests:
            self.assertLessEqual(request["estimated_input_tokens"] + request["requested_output_tokens"],
                                 request["context_limit"])

    def test_missing_finish_reason_does_not_infer_truncation(self):
        self.assertEqual(output_status(None, False), "not_truncated")

    def test_nonrepetitive_lines_are_not_flagged(self):
        self.assertFalse(repetitive_output(SHORT))

    def test_cli_fixed_policy_overrides_all_formal_paths(self):
        for command in (["solve", "example.lean"], ["minif2f-run"]):
            args = build_parser().parse_args(command + ["--formal-output-policy", "fixed",
                                                        "--formal-output-tokens", "2048"])
            config = _output_budget_overrides(AppConfig(), args)
            self.assertEqual(config.generation.output_budget_policy, "fixed")
            self.assertEqual(config.generation.max_output_tokens, 2048)

    def test_invalid_adaptive_ceiling_fails_before_inference(self):
        args = build_parser().parse_args(["minif2f-run", "--formal-output-policy", "adaptive",
            "--formal-output-tokens", "2048", "--formal-output-ceiling", "512"])
        with self.assertRaisesRegex(ValueError, "at least"):
            _output_budget_overrides(AppConfig(), args)

    def test_config_loads_policy_and_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[generation]\noutput_budget_policy = "adaptive"\nmax_output_tokens = 512\n'
                            'max_recovery_output_tokens = 2048\nreset_output_tokens = 1024\n')
            config = load_config(path)
            self.assertEqual(config.generation.reset_output_tokens, 1024)
        for values in ({"output_budget_policy": "mystery"}, {"reset_output_tokens": 0},
                       {"max_recovery_output_tokens": True}, {"max_output_tokens": 512.5}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                _validate(replace(AppConfig(), generation=replace(GenerationConfig(), **values)))


@unittest.skipUnless(os.environ.get("RUN_OUTPUT_BUDGET_LEAN_TESTS") == "1", "requires local Kimina")
class OutputBudgetLiveLeanTests(unittest.TestCase):
    def test_injected_truncations_expand_then_real_lean_verifies(self):
        """Scripted generation isolates routing; correctness uses the real compiler."""
        from local_lean_agent.verification.kimina import KiminaVerifier
        root = Path(__file__).resolve().parents[1]
        local = load_config(root / "config/local.toml")
        verifier = KiminaVerifier(replace(local.kimina, reuse_repl=False))
        self.assertTrue(verifier.health_check(), "Local Kimina must be running")
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend([SHORT, LONGER, GOOD], ["length", "length", "stop"])
            config = AppConfig(generation=GenerationConfig(max_output_tokens=512,
                output_budget_policy="adaptive", max_recovery_output_tokens=2048),
                agent=AgentConfig(max_iterations=3, fallback_enabled=False,
                                  log_path=Path(directory) / "events.jsonl"))
            result = ProofAgent(backend, verifier, config).solve(TASK)
        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(backend.max_token_requests, [512, 1024, 2048])
        self.assertEqual(result.metrics.kimina_checks, 3)
        self.assertFalse(result.iterations[0].verification.valid)
        self.assertFalse(result.iterations[1].verification.valid)
        self.assertTrue(result.iterations[2].verification.valid)
        self.assertEqual(result.metrics.formal_output_truncations, 2)
        self.assertTrue(verifier.verify(result.final_proof, attempt_id="output-budget-independent-audit").valid)


if __name__ == "__main__":
    unittest.main()
