from pathlib import Path
import json
import tempfile
import unittest

from local_lean_agent.config import AppConfig
from local_lean_agent.reproducibility import (
    immutable_artifact_path,
    run_metadata,
    write_json,
)


class ReproducibilityTests(unittest.TestCase):
    def test_metadata_contains_stable_fingerprints_and_unique_run_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.toml"
            manifest.write_text('suite = "test"\n', encoding="utf-8")
            first = run_metadata(AppConfig(), manifest)
            second = run_metadata(AppConfig(), manifest)
        self.assertEqual(first["config_fingerprint"], second["config_fingerprint"])
        self.assertEqual(first["manifest_fingerprint"], second["manifest_fingerprint"])
        self.assertEqual(first["code_fingerprint"], second["code_fingerprint"])
        self.assertEqual(first["prompt_version"], "v4.0.0")
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_versioned_artifact_does_not_replace_latest_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "v1-suite-latest.json"
            artifact = immutable_artifact_path(
                output,
                created_at="2026-09-02T12:00:00+00:00",
                run_id="abcdef123456",
                label="run-01",
            )
            write_json(artifact, {"passed": 20})
            write_json(output, {"latest": True})
            saved = json.loads(artifact.read_text(encoding="utf-8"))
            latest = json.loads(output.read_text(encoding="utf-8"))
        self.assertIn("history", artifact.parts)
        self.assertNotIn("+", artifact.name)
        self.assertEqual(saved, {"passed": 20})
        self.assertEqual(latest, {"latest": True})


if __name__ == "__main__":
    unittest.main()
