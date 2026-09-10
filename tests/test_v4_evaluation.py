from dataclasses import replace
from pathlib import Path
import unittest

from local_lean_agent.config import AppConfig, V4Config
from local_lean_agent.v3_suite import load_v3_cases
from local_lean_agent.v4_suite import condition_config, load_v4_cases, measurements, summarize
from tests.test_v3_suite import result


class V4EvaluationTests(unittest.TestCase):
    def test_manifest_modes_and_older_runner_boundary(self):
        cases = load_v4_cases("benchmarks/v4/manifest.toml")
        self.assertEqual([c.mode for c in cases], ["generation", "repair", "negative"])
        with self.assertRaises(ValueError):
            load_v3_cases("benchmarks/v4/manifest.toml")

    def test_conditions_share_model_informal_retrieval_and_formal_budgets(self):
        config = AppConfig(v4=V4Config(enabled=True))
        baseline, treatment = [condition_config(config, name) for name in ("v3", "v4")]
        self.assertFalse(baseline.v4.enabled)
        self.assertTrue(treatment.v4.enabled)
        for field in ("generation", "informal_reasoning", "lean_explore", "lean_lsp", "agent", "mlx"):
            self.assertEqual(getattr(baseline, field), getattr(treatment, field))
        self.assertFalse(treatment.agent.fallback_enabled)
        self.assertFalse(treatment.informal_reasoning.rewrite_salvage_enabled)
        self.assertTrue(config.agent.fallback_enabled)

    def test_metrics_include_discussion_cost_without_double_counting_worker(self):
        attempt = result(success=True, informal=True)
        attempt.metrics.model_calls = 3
        attempt.metrics.main_agent_calls = 2
        attempt.metrics.fresh_subproblem_calls = 1
        attempt.metrics.discussion_partner_calls = 1
        attempt.metrics.discussion_completion_tokens = 100
        measured = measurements(load_v4_cases("benchmarks/v4/manifest.toml")[0], attempt)
        self.assertEqual(measured["all_model_calls"], 6)
        self.assertEqual(measured["all_generated_tokens"], 120)
        self.assertTrue(measured["v4_activated"])


if __name__ == "__main__":
    unittest.main()
