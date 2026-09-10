"""Exercise paired scheduling, persistence and independent audit without services."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from local_lean_agent.config import AppConfig, LeanExploreConfig, LeanLSPConfig
from local_lean_agent.types import VerificationResult
from tests.test_v3_suite import result


spec = importlib.util.spec_from_file_location("v4_live_runner", "scripts/test-v4-live.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class V4RunnerTests(unittest.TestCase):
    def run_script(self, *, preflight=True, recheck=True):
        config = AppConfig(lean_lsp=LeanLSPConfig(enabled=True, required=True),
                           lean_explore=LeanExploreConfig(enabled=True, required=True))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "paired.json"
            args = ["test-v4-live.py", "--case", "hypothesis_chain",
                    "--repetitions", "2", "--output", str(output)]
            with patch.object(runner, "load_config", return_value=config), \
                 patch.object(runner, "MLXBackend") as backend, \
                 patch.object(runner, "KiminaVerifier") as verifier, \
                 patch.object(runner, "LeanLSPMCPClient") as feedback, \
                 patch.object(runner, "LeanExploreMCPClient") as retrieval, \
                 patch.object(runner, "ProofAgent") as agent, \
                 patch.object(runner, "validate_v3_cases", return_value={"passed": preflight}), \
                 patch("sys.argv", args), patch("builtins.print"):
                backend.return_value.unload_model.return_value = 0.0
                verifier.return_value.verify.return_value = VerificationResult(recheck)
                agent.return_value.solve.side_effect = [result(success=True) for _ in range(4)]
                code = runner.main()
                payload = json.loads(output.read_text())
                if preflight:
                    self.assertTrue(Path(payload["immutable_artifact"]).is_file())
                    backend.return_value.unload_model.assert_called_once()
                    feedback.return_value.close.assert_called_once()
                    retrieval.return_value.close.assert_called_once()
                return code, payload, backend, verifier, agent

    def test_pairs_counterbalance_and_share_single_backend(self):
        code, payload, backend, verifier, agent = self.run_script()
        self.assertEqual(code, 0)
        self.assertTrue(payload["experiment_complete"])
        self.assertEqual([c["condition"] for c in payload["cases"]], ["v4", "v3", "v3", "v4"])
        self.assertEqual(verifier.return_value.verify.call_count, 4)
        self.assertEqual(backend.call_count, 1)
        for call in agent.call_args_list:
            self.assertIs(call.args[0], backend.return_value)
            self.assertFalse(call.args[2].agent.fallback_enabled)
            self.assertFalse(call.args[2].informal_reasoning.rewrite_salvage_enabled)

    def test_failed_independent_recheck_is_not_a_pass(self):
        code, payload, _, _, _ = self.run_script(recheck=False)
        self.assertEqual(code, 1)
        self.assertTrue(payload["experiment_complete"])
        self.assertFalse(any(c["passed"] for c in payload["cases"]))
        self.assertEqual(payload["summary"]["v4"]["positive_successes"], 0)
        self.assertEqual(payload["summary"]["v4"]["claimed_positive_successes"], 2)

    def test_preflight_failure_prevents_loading_model(self):
        code, payload, backend, _, _ = self.run_script(preflight=False)
        self.assertEqual(code, 1)
        self.assertFalse(payload["experiment_complete"])
        backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
