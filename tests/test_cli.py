from pathlib import Path
import json
import tempfile
import unittest

from local_lean_agent.cli import build_parser, _terminal_summary, _write_attempt_result
from local_lean_agent.types import (
    AttemptMetrics,
    AttemptResult,
    FailureCategory,
)


class CLITests(unittest.TestCase):
    def _result(self) -> AttemptResult:
        return AttemptResult(
            attempt_id="attempt-123",
            theorem="theorem t : True := by trivial",
            success=True,
            final_proof="theorem t : True := by trivial\n",
            failure_category=FailureCategory.NONE,
            stop_reason="verified",
            error_message=None,
            iterations=[],
            metrics=AttemptMetrics(model_calls=1, kimina_checks=1),
        )

    def test_terminal_summary_has_only_main_fields(self) -> None:
        summary = _terminal_summary(self._result())
        self.assertEqual(set(summary), {"success", "end_reason", "rounds"})
        self.assertEqual(summary["end_reason"], "VERIFIED")
        self.assertEqual(summary["rounds"], 1)

    def test_full_result_is_written_per_attempt_and_as_latest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result_dir = Path(directory)
            path = _write_attempt_result(self._result(), result_dir)
            self.assertEqual(path, result_dir / "attempt-123.json")
            attempt = json.loads(path.read_text(encoding="utf-8"))
            latest = json.loads(
                (result_dir / "latest.json").read_text(encoding="utf-8")
            )
        self.assertEqual(attempt["attempt_id"], "attempt-123")
        self.assertEqual(attempt["end_reason"], "VERIFIED")
        self.assertEqual(attempt["rounds"], 1)
        self.assertEqual(latest, attempt)

    def test_v1_suite_accepts_a_positive_repetition_count(self) -> None:
        args = build_parser().parse_args(
            ["v1-suite", "benchmarks/v1/manifest.toml", "--repetitions", "3"]
        )
        self.assertEqual(args.repetitions, 3)

        with self.assertRaises(SystemExit):
            build_parser().parse_args(["v1-suite", "--repetitions", "0"])

    def test_v2_ablation_parser_uses_paired_manifest(self) -> None:
        args = build_parser().parse_args(
            ["v2-ablation", "benchmarks/v2/manifest.toml", "--repetitions", "2"]
        )
        self.assertEqual(args.command, "v2-ablation")
        self.assertEqual(args.repetitions, 2)

    def test_v3_ablation_parser_uses_paired_manifest(self) -> None:
        args = build_parser().parse_args(
            ["v3-ablation", "benchmarks/v3/manifest.toml", "--repetitions", "2"]
        )
        self.assertEqual(args.command, "v3-ablation")
        self.assertEqual(args.repetitions, 2)


if __name__ == "__main__":
    unittest.main()
