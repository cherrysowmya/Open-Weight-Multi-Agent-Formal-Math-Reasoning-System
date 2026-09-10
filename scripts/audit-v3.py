#!/usr/bin/env python3
"""Independently recheck a completed V3 paired experiment with Kimina."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from local_lean_agent.config import load_config
from local_lean_agent.orchestrator import validate_task_preserved
from local_lean_agent.reproducibility import write_json
from local_lean_agent.types import FailureCategory
from local_lean_agent.verification.kimina import KiminaVerifier


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--output", type=Path, default=Path("runs/v3-audit-latest.json"))
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    if artifact.get("experiment") != "v3_informal_reasoning_ablation":
        parser.error("Artifact is not a V3 informal-reasoning experiment")
    if not artifact.get("experiment_complete"):
        parser.error("Only a completed paired experiment can be audited")

    verifier = KiminaVerifier(load_config(args.config).kimina)
    accepted_checks = []
    rejected_checks = []
    conditions = ("baseline_cases", "informal_cases")
    for condition in conditions:
        for case in artifact[condition]:
            attempt = case["attempt"]
            source = attempt["final_proof"]
            integrity_error = validate_task_preserved(attempt["theorem"], source)
            if attempt["success"]:
                checked = verifier.verify(
                    source, attempt_id="v3-audit-" + attempt["attempt_id"]
                )
                accepted_checks.append(
                    {
                        "condition": case["condition"],
                        "case": case["id"],
                        "attempt_id": attempt["attempt_id"],
                        "proof_sha256": hashlib.sha256(source.encode()).hexdigest(),
                        "task_preservation_error": integrity_error,
                        "verification": checked,
                        "passed": not integrity_error and checked.valid,
                    }
                )
            elif case["mode"] != "negative" and not integrity_error:
                checked = verifier.verify(
                    source, attempt_id="v3-audit-rejected-" + attempt["attempt_id"]
                )
                rejected_checks.append(
                    {
                        "condition": case["condition"],
                        "case": case["id"],
                        "verification": checked,
                        "passed": not checked.valid
                        and checked.failure_category
                        not in {
                            FailureCategory.VERIFIER_UNAVAILABLE,
                            FailureCategory.VERIFIER_TIMEOUT,
                        },
                    }
                )

    negatives = [
        case
        for condition in conditions
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
    counterexamples = Path("benchmarks/v3/counterexamples.lean").read_text(
        encoding="utf-8"
    )
    counterexample_check = verifier.verify(
        counterexamples, attempt_id="v3-audit-counterexamples"
    )
    passed = (
        bool(accepted_checks)
        and all(item["passed"] for item in accepted_checks + rejected_checks)
        and negative_contract
        and counterexample_check.valid
    )
    payload = {
        "run_id": artifact["run_id"],
        "artifact": str(args.artifact),
        "artifact_sha256": hashlib.sha256(args.artifact.read_bytes()).hexdigest(),
        "passed": passed,
        "accepted_proofs_rechecked": len(accepted_checks),
        "rejected_final_candidates_rechecked": len(rejected_checks),
        "negative_attempts_checked": len(negatives),
        "negative_budget_contract": negative_contract,
        "counterexamples": counterexample_check,
        "accepted_checks": accepted_checks,
        "rejected_checks": rejected_checks,
    }
    write_json(args.output, payload)
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "passed",
                    "accepted_proofs_rechecked",
                    "rejected_final_candidates_rechecked",
                    "negative_attempts_checked",
                )
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
