#!/usr/bin/env python3
"""Run reproducible local V3/V4 comparisons; each successful proof is recompiled."""
import argparse
from dataclasses import replace
from pathlib import Path
import json

from local_lean_agent.backends.mlx import MLXBackend
from local_lean_agent.config import load_config
from local_lean_agent.feedback.lean_lsp_mcp import LeanLSPMCPClient
from local_lean_agent.informal.qwen import QwenInformalReasoner
from local_lean_agent.orchestrator import ProofAgent
from local_lean_agent.reproducibility import run_metadata, write_json, immutable_artifact_path
from local_lean_agent.retrieval.lean_explore import LeanExploreMCPClient
from local_lean_agent.v3_suite import validate_v3_cases
from local_lean_agent.v4_suite import condition_config, load_v4_cases, measurements, summarize
from local_lean_agent.verification.kimina import KiminaVerifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/v4/manifest.toml"))
    parser.add_argument("--condition", choices=("paired", "v3", "v4", "discussion-only", "fresh-only"), default="paired")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("runs/v4-ablation-latest.json"))
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    config = load_config(args.config)
    if not (config.lean_lsp.enabled and config.lean_lsp.required
            and config.lean_explore.enabled and config.lean_explore.required):
        parser.error("V4 evaluation requires local Lean-LSP and LeanExplore")
    cases = load_v4_cases(args.manifest)
    if set(args.case) - {c.case_id for c in cases}:
        parser.error("Unknown case ID")
    cases = [c for c in cases if not args.case or c.case_id in args.case]
    configs = {name: condition_config(config, name) for name in
               (("v3", "v4") if args.condition == "paired" else (args.condition,))}
    metadata = run_metadata(config, args.manifest)
    payload = {**metadata, "experiment_complete": False, "test_type": "real_qwen",
               "effective_configs": configs, "cases": [], "repetitions": args.repetitions}
    verifier = KiminaVerifier(config.kimina)
    preflight = validate_v3_cases(cases, verifier)
    payload["preflight"] = preflight
    if not preflight["passed"]:
        write_json(args.output, payload)
        print(json.dumps({"experiment_complete": False, "reason": "fixture_preflight_failed"}))
        return 1
    backend = MLXBackend(config.mlx)
    feedback = LeanLSPMCPClient(config.lean_lsp)
    retrieval = LeanExploreMCPClient(config.lean_explore)
    try:
        for repetition in range(1, args.repetitions + 1):
            for index, case in enumerate(cases):
                order = list(configs)
                if (repetition + index) % 2:
                    order.reverse()
                for position, condition in enumerate(order, 1):
                    base = configs[condition]
                    effective = replace(base, agent=replace(base.agent,
                        max_iterations=case.max_rounds,
                        log_path=args.output.with_suffix(".events.jsonl")))
                    reasoner = QwenInformalReasoner(backend, effective.informal_reasoning)
                    result = ProofAgent(backend, verifier, effective,
                        feedback_provider=feedback, retriever=retrieval,
                        informal_reasoner=reasoner).solve(case.path.read_text())
                    recheck = verifier.verify(result.final_proof,
                        attempt_id=result.attempt_id + "-independent") if result.success else None
                    measured = measurements(case, result)
                    payload["cases"].append({"id": case.case_id, "mode": case.mode,
                        "condition": condition, "repetition": repetition, "condition_order": position,
                        "attempt": result.to_dict(), "measurements": measured,
                        "independent_recheck": recheck,
                        "passed": measured["outcome_correct"] and (recheck is None or recheck.valid)})
                    write_json(args.output, payload)
        payload["experiment_complete"] = True
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        payload["model_unload_seconds"] = backend.unload_model()
        feedback.close()
        retrieval.close()
        payload["summary"] = {name: summarize([r for r in payload["cases"]
                                if r["condition"] == name]) for name in configs}
        artifact = immutable_artifact_path(args.output, created_at=metadata["created_at"],
            run_id=metadata["run_id"], label=args.condition)
        payload["immutable_artifact"] = str(artifact)
        write_json(artifact, payload)
        write_json(args.output, payload)
    print(json.dumps({"experiment_complete": payload["experiment_complete"],
                      "summary": payload["summary"]}, indent=2))
    return 0 if payload["experiment_complete"] and all(r["passed"] for r in payload["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
