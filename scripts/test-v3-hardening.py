#!/usr/bin/env python3
"""Bounded real-Qwen regression runs with hardening-off and model-only controls."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

from local_lean_agent.backends.mlx import MLXBackend
from local_lean_agent.config import load_config
from local_lean_agent.feedback.lean_lsp_mcp import LeanLSPMCPClient
from local_lean_agent.informal.qwen import QwenInformalReasoner
from local_lean_agent.orchestrator import ProofAgent
from local_lean_agent.reproducibility import immutable_artifact_path, run_metadata, write_json
from local_lean_agent.retrieval.lean_explore import LeanExploreMCPClient
from local_lean_agent.v3_suite import load_v3_cases, validate_v3_cases, attempt_measurements, summarize_condition
from local_lean_agent.verification.kimina import KiminaVerifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/v3/hardening.toml"))
    parser.add_argument("--mode", choices=("hardened", "control"), default="hardened")
    parser.add_argument("--no-salvage", action="store_true",
                        help="Disable compiler-prefix probes; test model-only repair")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("runs/v3-hardening-latest.json"))
    args = parser.parse_args()
    config = load_config(args.config)
    if not (config.lean_lsp.enabled and config.lean_lsp.required
            and config.lean_explore.enabled and config.lean_explore.required):
        parser.error("Required local Lean-LSP and LeanExplore must both be enabled")
    cases = load_v3_cases(args.manifest)
    unknown = set(args.case) - {case.case_id for case in cases}
    if unknown:
        parser.error(f"Unknown cases: {sorted(unknown)}")
    if args.case:
        cases = [case for case in cases if case.case_id in args.case]
    config = replace(config, v4=replace(config.v4, enabled=False), informal_reasoning=replace(config.informal_reasoning,
        enabled=True, verifier_enabled=True, invocation_policy="always",
        strategy_retrieval_enabled=args.mode == "hardened",
        rewrite_salvage_enabled=args.mode == "hardened" and not args.no_salvage,
        rewrite_recovery_enabled=args.mode == "hardened"),
        agent=replace(config.agent, fallback_enabled=False, unload_model_after_attempt=False,
                      log_path=args.output.with_suffix(".events.jsonl")))
    metadata = run_metadata(config, args.manifest)
    verifier = KiminaVerifier(config.kimina)
    preflight = validate_v3_cases(cases, verifier)
    if not preflight["passed"]:
        write_json(args.output, {**metadata, "complete": False, "preflight": preflight})
        print(json.dumps({"complete": False, "reason": "fixture_preflight_failed"}))
        return 1
    backend = MLXBackend(config.mlx)
    feedback = LeanLSPMCPClient(config.lean_lsp)
    retrieval = LeanExploreMCPClient(config.lean_explore)
    payload = {**metadata, "complete": False, "mode": args.mode,
               "effective_config": config, "preflight": preflight,
               "round_budgets": {case.case_id: case.max_rounds for case in cases}, "cases": []}
    started = time.monotonic()
    try:
        reasoner = QwenInformalReasoner(backend, config.informal_reasoning)
        for case in cases:
            effective = replace(config, agent=replace(config.agent, max_iterations=case.max_rounds))
            result = ProofAgent(backend, verifier, effective, feedback_provider=feedback,
                retriever=retrieval, informal_reasoner=reasoner).solve(case.path.read_text(encoding="utf-8"))
            recheck = verifier.verify(result.final_proof, attempt_id=result.attempt_id + "-independent") if result.success else None
            measurements = attempt_measurements(case, result)
            proof_origin = (result.iterations[-1].generation.model
                            if result.success and result.iterations else None)
            payload["cases"].append({"id": case.case_id, "mode": case.mode,
                "proof_origin": proof_origin,
                "attempt": result.to_dict(), "measurements": measurements, "independent_recheck": recheck,
                "passed": measurements["outcome_correct"] and (recheck is None or recheck.valid)})
            write_json(args.output, payload)
        payload["complete"] = True
    finally:
        payload["model_unload_seconds"] = backend.unload_model()
        feedback.close()
        retrieval.close()
        payload["wall_clock_seconds"] = time.monotonic() - started
        payload["passed"] = sum(case["passed"] for case in payload["cases"])
        payload["total"] = len(cases)
        payload["summary"] = summarize_condition(payload["cases"])
        artifact = immutable_artifact_path(args.output, created_at=metadata["created_at"],
            run_id=metadata["run_id"], label=args.mode)
        payload["immutable_artifact"] = str(artifact)
        write_json(artifact, payload)
        write_json(args.output, payload)
    print(json.dumps({k: payload[k] for k in ("complete", "mode", "passed", "total")}, indent=2))
    return 0 if payload["complete"] and payload["passed"] == payload["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
