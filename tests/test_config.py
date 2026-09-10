from pathlib import Path
import tempfile
import unittest

from local_lean_agent.config import AppConfig, GenerationConfig, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_obey_context_limit(self) -> None:
        config = AppConfig()
        self.assertEqual(config.generation.max_context_tokens, 12_288)
        self.assertFalse(config.generation.enable_thinking)
        self.assertTrue(config.agent.fallback_enabled)
        self.assertIn("simp", config.agent.fallback_tactics)
        self.assertFalse(config.informal_reasoning.enabled)
        self.assertEqual(config.informal_reasoning.max_context_tokens, 8_192)

    def test_rejects_context_above_main_agent_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.toml"
            path.write_text("[generation]\nmax_context_tokens = 12289\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "12,288"):
                load_config(path)

    def test_generation_config_is_replaceable(self) -> None:
        config = AppConfig(generation=GenerationConfig(max_output_tokens=512))
        self.assertEqual(config.generation.max_output_tokens, 512)

    def test_relative_log_path_is_relative_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings" / "local.toml"
            path.parent.mkdir()
            path.write_text('[agent]\nlog_path = "../runs/a.jsonl"\n', encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.agent.log_path, path.parent / "../runs/a.jsonl")

    def test_relative_result_dir_is_relative_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings" / "local.toml"
            path.parent.mkdir()
            path.write_text('[agent]\nresult_dir = "../results"\n', encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.agent.result_dir, path.parent / "../results")

    def test_accepts_max_rounds_as_public_config_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            path.write_text("[agent]\nmax_rounds = 3\n", encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.agent.max_iterations, 3)

    def test_rejects_both_round_budget_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            path.write_text(
                "[agent]\nmax_rounds = 3\nmax_iterations = 4\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "cannot set both"):
                load_config(path)

    def test_resolves_lean_lsp_paths_from_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings" / "local.toml"
            path.parent.mkdir()
            project = Path(directory) / "lean-project"
            project.mkdir()
            executable = Path(directory) / "bin" / "lean-lsp-mcp"
            executable.parent.mkdir()
            executable.touch()
            path.write_text(
                "[lean_lsp]\n"
                'command = "../bin/lean-lsp-mcp"\n'
                'project_path = "../lean-project"\n',
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertEqual(config.lean_lsp.command, str(executable.resolve()))
        self.assertEqual(config.lean_lsp.project_path, project.resolve())

    def test_loads_configured_fallback_tactics_as_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            path.write_text(
                '[agent]\nfallback_tactics = ["simp", "aesop"]\n',
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertEqual(config.agent.fallback_tactics, ("simp", "aesop"))

    def test_rejects_multiline_fallback_tactic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            path.write_text(
                '[agent]\nfallback_tactics = ["simp\\naesop"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "single-line"):
                load_config(path)

    def test_resolves_lean_explore_paths_and_tuple_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "local.toml"
            path.parent.mkdir()
            path.write_text(
                "[lean_explore]\n"
                "enabled = true\n"
                'command = "../service/bin/lean-explore"\n'
                'cache_dir = "../cache"\n'
                'hf_cache_dir = "../hf"\n'
                'args = ["mcp", "serve"]\n'
                'packages = ["Mathlib"]\n',
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertTrue(config.lean_explore.enabled)
        self.assertTrue(Path(config.lean_explore.command).is_absolute())
        self.assertTrue(config.lean_explore.cache_dir.is_absolute())
        self.assertEqual(config.lean_explore.args, ("mcp", "serve"))
        self.assertEqual(config.lean_explore.packages, ("Mathlib",))

    def test_rejects_lean_explore_source_limit_above_result_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            path.write_text(
                "[lean_explore]\nlimit = 2\nsource_limit = 3\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "source_limit"):
                load_config(path)

    def test_loads_informal_reasoning_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            path.write_text(
                "[informal_reasoning]\n"
                "enabled = true\n"
                'invocation_policy = "always"\n'
                "max_refinement_rounds = 5\n",
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertTrue(config.informal_reasoning.enabled)
        self.assertEqual(config.informal_reasoning.invocation_policy, "always")
        self.assertEqual(config.informal_reasoning.max_refinement_rounds, 5)

    def test_rejects_informal_context_above_isolated_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            path.write_text(
                "[informal_reasoning]\nmax_context_tokens = 8193\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "8,192"):
                load_config(path)

    def test_rejects_more_than_five_informal_refinement_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.toml"
            path.write_text(
                "[informal_reasoning]\nmax_refinement_rounds = 6\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "between 1 and 5"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
