#!/usr/bin/env python3
"""Recheck completed V4 comparison candidates with local Lean, without inference."""
import argparse
import hashlib
import json
from pathlib import Path

from local_lean_agent.config import load_config
from local_lean_agent.orchestrator import validate_task_preserved
from local_lean_agent.reproducibility import write_json
from local_lean_agent.types import FailureCategory
from local_lean_agent.v4_suite import load_v4_cases
from local_lean_agent.verification.kimina import KiminaVerifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/v4/manifest.toml"))
    parser.add_argument("--output", type=Path, default=Path("runs/v4-audit-latest.json"))
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text())
    if not artifact.get("experiment_complete") or artifact.get("test_type") != "real_qwen":
        parser.error("Requires a completed real-Qwen V4 comparison artifact")
    if hashlib.sha256(args.manifest.read_bytes()).hexdigest() != artifact["manifest_fingerprint"]:
        parser.error("Manifest does not match the experiment fingerprint")
    cases_by_id = {case.case_id: case for case in load_v4_cases(args.manifest)}
    verifier = KiminaVerifier(load_config(args.config).kimina)
    if not verifier.health_check():
        parser.error("Local Kimina is unavailable")
    checks = []
    for case in artifact["cases"]:
        attempt = case["attempt"]
        source = attempt["final_proof"]
        integrity_error = validate_task_preserved(attempt["theorem"], source)
        checked = verifier.verify(source, attempt_id="v4-audit-" + attempt["attempt_id"])
        available = checked.failure_category not in {
            FailureCategory.VERIFIER_UNAVAILABLE, FailureCategory.VERIFIER_TIMEOUT}
        agreement = ((checked.valid and not integrity_error) if attempt["success"]
                     else (not checked.valid or bool(integrity_error)))
        negative_budget = True
        if case["mode"] == "negative":
            negative_budget = (not attempt["success"] and attempt["end_reason"] == "MAX_ROUNDS"
                               and attempt["rounds"] == cases_by_id[case["id"]].max_rounds)
        checks.append({"id": case["id"], "condition": case["condition"],
            "attempt_id": attempt["attempt_id"], "claimed_success": attempt["success"],
            "proof_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "task_preservation_error": integrity_error, "verification": checked,
            "negative_budget_contract": negative_budget,
            "passed": available and agreement and negative_budget})
    counterexample = verifier.verify(
        "import Mathlib\nexample (n : ℕ) : n + 1 ≠ n := by omega\n",
        attempt_id="v4-audit-false-successor-refutation")
    payload = {"run_id": artifact["run_id"], "artifact": str(args.artifact),
        "artifact_sha256": hashlib.sha256(args.artifact.read_bytes()).hexdigest(),
        "passed": bool(checks) and all(c["passed"] for c in checks) and counterexample.valid,
        "checks": checks, "false_successor_refutation": counterexample}
    write_json(args.output, payload)
    print(json.dumps({"passed": payload["passed"], "candidates_rechecked": len(checks),
                      "false_successor_refuted": counterexample.valid}, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
