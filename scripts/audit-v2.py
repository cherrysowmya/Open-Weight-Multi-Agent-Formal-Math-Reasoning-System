#!/usr/bin/env python3
"""Recheck every accepted paired-benchmark proof with Kimina, without an LLM."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def environment_snapshot() -> dict:
    """Record installed versions without network queries or dependency changes."""
    root = Path(__file__).resolve().parents[1]
    snapshots = {}
    for name, executable in (
        ("agent", Path(sys.executable)),
        ("lean_explore", root / "services/lean-explore/.venv/bin/python"),
        ("lean_lsp", root / "services/lean-lsp-mcp/.venv/bin/python"),
    ):
        try:
            output = subprocess.check_output(
                [str(executable), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
                text=True, stderr=subprocess.DEVNULL, timeout=30,
            )
            snapshots[name] = json.loads(output)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            snapshots[name] = {"unavailable": str(exc)}
    mathlib = root / "services/kimina-lean-server/mathlib4"
    snapshots["lean_toolchain"] = (mathlib / "lean-toolchain").read_text().strip()
    try:
        snapshots["mathlib_commit"] = subprocess.check_output(
            ["git", "-C", str(mathlib), "rev-parse", "HEAD"], text=True, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        snapshots["mathlib_commit"] = {"unavailable": str(exc)}
    return snapshots

from local_lean_agent.config import load_config
from local_lean_agent.orchestrator import validate_task_preserved
from local_lean_agent.reproducibility import write_json
from local_lean_agent.types import FailureCategory
from local_lean_agent.verification.kimina import KiminaVerifier


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--output", type=Path, default=Path("runs/v2-audit-latest.json"))
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    if not artifact.get("experiment_complete"):
        parser.error("Only a completed paired experiment can be audited")
    verifier = KiminaVerifier(load_config(args.config).kimina)
    checks = []
    rejected_checks = []
    for condition in ("baseline_cases", "retrieval_cases"):
        for case in artifact[condition]:
            attempt = case["attempt"]
            if not attempt["success"]:
                source = attempt["final_proof"]
                if case["mode"] != "negative" and not validate_task_preserved(attempt["theorem"], source):
                    rejected = verifier.verify(source, attempt_id="v2-audit-rejected-" + attempt["attempt_id"])
                    rejected_checks.append({
                        "condition": case["condition"], "case": case["id"],
                        "verification": rejected,
                        "passed": not rejected.valid and rejected.failure_category not in {
                            FailureCategory.VERIFIER_UNAVAILABLE, FailureCategory.VERIFIER_TIMEOUT,
                        },
                    })
                continue
            source = attempt["final_proof"]
            integrity_error = validate_task_preserved(attempt["theorem"], source)
            checked = verifier.verify(source, attempt_id="v2-audit-" + attempt["attempt_id"])
            checks.append({
                "condition": case["condition"],
                "case": case["id"],
                "attempt_id": attempt["attempt_id"],
                "proof_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "task_preservation_error": integrity_error,
                "verification": checked,
                "passed": not integrity_error and checked.valid,
            })
    negatives = [
        case
        for condition in ("baseline_cases", "retrieval_cases")
        for case in artifact[condition]
        if case["mode"] == "negative"
    ]
    negative_contract = all(
        not case["attempt"]["success"]
        and case["attempt"]["end_reason"] == "MAX_ROUNDS"
        and case["attempt"]["rounds"]
        == artifact["effective_settings"]["round_budgets"][case["id"]]
        for case in negatives
    )
    counterexamples = Path("benchmarks/v2/counterexamples.lean").read_text(encoding="utf-8")
    counterexample_check = verifier.verify(counterexamples, attempt_id="v2-audit-counterexamples")
    passed = bool(checks) and all(c["passed"] for c in checks + rejected_checks) and negative_contract and counterexample_check.valid
    write_json(args.output, {
        "run_id": artifact["run_id"],
        "artifact": str(args.artifact),
        "artifact_sha256": hashlib.sha256(args.artifact.read_bytes()).hexdigest(),
        "environment": environment_snapshot(),
        "passed": passed,
        "accepted_proofs_rechecked": len(checks),
        "rejected_final_candidates_rechecked": len(rejected_checks),
        "negative_attempts_checked": len(negatives),
        "negative_budget_contract": negative_contract,
        "counterexamples": counterexample_check,
        "checks": checks,
        "rejected_checks": rejected_checks,
    })
    print(json.dumps({
        "passed": passed,
        "accepted_proofs_rechecked": len(checks),
        "rejected_final_candidates_rechecked": len(rejected_checks),
        "negative_attempts_checked": len(negatives),
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
