#!/usr/bin/env python3
"""Run opt-in local Qwen quality tests; no mocks and no paid APIs."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

from local_lean_agent.backends.mlx import MLXBackend
from local_lean_agent.config import load_config
from local_lean_agent.informal.qwen import QwenInformalReasoner
from local_lean_agent.reproducibility import immutable_artifact_path, run_metadata, write_json
from local_lean_agent.v3_quality import load_quality_cases, run_quality_case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--manifest", type=Path, default=Path("benchmarks/v3/components.toml"))
    parser.add_argument("--case", action="append", default=[], help="Case ID; repeat to select several")
    parser.add_argument("--output", type=Path, default=Path("runs/v3-components-latest.json"))
    args = parser.parse_args()
    config = load_config(args.config)
    cases = load_quality_cases(args.manifest)
    unknown = set(args.case) - {c["id"] for c in cases}
    if unknown:
        parser.error(f"Unknown cases: {sorted(unknown)}")
    if args.case:
        cases = [c for c in cases if c["id"] in args.case]
    metadata = run_metadata(config, args.manifest)
    backend = MLXBackend(config.mlx)
    effective = replace(config.informal_reasoning, verifier_enabled=True)
    reasoner = QwenInformalReasoner(backend, effective)
    payload = {**metadata, "test_type": "live_qwen_component_quality", "complete": False,
               "effective_settings": effective, "cases": []}
    started = time.monotonic()
    try:
        payload["model_load_seconds"] = backend.load_model(config.mlx.model_id)
        for case in cases:
            payload["cases"].append(run_quality_case(case, reasoner))
            write_json(args.output, payload)
        payload["complete"] = True
    finally:
        payload["model_unload_seconds"] = backend.unload_model()
        payload["wall_clock_seconds"] = time.monotonic() - started
        payload["passed"] = sum(c["passed"] for c in payload["cases"])
        payload["total"] = len(cases)
        artifact = immutable_artifact_path(args.output, created_at=metadata["created_at"],
                                           run_id=metadata["run_id"], label="components")
        payload["immutable_artifact"] = str(artifact)
        write_json(artifact, payload)
        write_json(args.output, payload)
    print(json.dumps({k: payload[k] for k in ("complete", "passed", "total")}, indent=2))
    return 0 if payload["complete"] and payload["passed"] == payload["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
