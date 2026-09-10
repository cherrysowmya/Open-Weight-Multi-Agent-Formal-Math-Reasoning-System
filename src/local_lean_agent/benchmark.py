"""Sequential, resumable MiniF2F evaluation on the existing local agent stack."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import fcntl
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .backends.mlx import MLXBackend
from .config import AppConfig
from .feedback.lean_lsp_mcp import LeanLSPMCPClient
from .informal.qwen import QwenInformalReasoner
from .minif2f import REVISION, MiniF2FCase, load_dataset, select_cases, sha, statement_probe
from .orchestrator import ProofAgent, validate_task_preserved
from .reproducibility import immutable_artifact_path, run_metadata
from .retrieval.lean_explore import LeanExploreMCPClient
from .telemetry import _serializable
from .verification.kimina import KiminaVerifier


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=path.name + ".",
                                     suffix=".tmp", delete=False) as handle:
        json.dump(_serializable(payload), handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = handle.name
    os.replace(temporary, path)


def environment_metadata(config: AppConfig) -> dict:
    project = config.lean_lsp.project_path.resolve()
    files = {}
    for name in ("lean-toolchain", "lake-manifest.json"):
        path = project / name
        files[name] = sha(path.read_bytes()) if path.exists() else None
    result = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    packages = {}
    for package in ("mlx", "mlx-lm", "transformers", "huggingface-hub"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    return {"project": str(project), "mathlib_commit": result.stdout.strip() or None,
            "inference_packages": packages,
            "files": files, "toolchain": (project / "lean-toolchain").read_text().strip()
            if (project / "lean-toolchain").exists() else None}


def variant_config(config: AppConfig, variant: str, rounds: int) -> AppConfig:
    if variant not in {"lean", "v2", "v3", "v4"} or rounds < 1:
        raise ValueError("Unknown benchmark variant or invalid round budget")
    return replace(config,
        lean_explore=replace(config.lean_explore, enabled=variant != "lean", required=True),
        informal_reasoning=replace(config.informal_reasoning, enabled=variant in {"v3", "v4"},
                                    verifier_enabled=True, rewrite_salvage_enabled=False),
        v4=replace(config.v4, enabled=variant == "v4"),
        agent=replace(config.agent, max_iterations=rounds, fallback_enabled=False,
                      unload_model_after_attempt=False))


def summarize(payload: dict) -> dict:
    result = {}
    for variant in payload["spec"]["variants"]:
        runs = [r for r in payload["attempts"] if r["variant"] == variant]
        selected = payload["spec"]["case_ids"]
        verified_ids = {r["case_id"] for r in runs if r["verified"]}
        statuses = Counter(r["status"] for r in runs)
        metrics = [r["attempt"]["metrics"] for r in runs if r.get("attempt")]
        result[variant] = {
            "selected_problems": len(selected), "completed_attempts": len(runs),
            "scheduled_attempts": len(selected) * payload["spec"]["attempts_per_theorem"],
            "verified_problems": len(verified_ids),
            "success_rate_selected": len(verified_ids) / len(selected),
            "first_attempt_verified": sum(r["verified"] and r["repetition"] == 1 for r in runs),
            "first_candidate_verified": sum(bool(r["verified"] and r.get("attempt")
                and r["attempt"]["iterations"] and r["attempt"]["iterations"][0]["verification"]["valid"])
                for r in runs),
            "failed_generated_candidates": sum(not iteration["verification"]["valid"]
                for r in runs if r.get("attempt") for iteration in r["attempt"]["iterations"]),
            "unknown_identifier_candidates": sum(iteration["verification"]["failure_category"] == "unknown_identifier"
                for r in runs if r.get("attempt") for iteration in r["attempt"]["iterations"]),
            "attempts_per_theorem": payload["spec"]["attempts_per_theorem"],
            "observed_solve_within_k_rate": len(verified_ids) / len(selected),
            "statuses": dict(statuses),
            "wall_clock_seconds": sum(m["wall_clock_seconds"] for m in metrics),
            "all_model_calls": sum(m.get("formal_call_attempts", m["model_calls"])
                + m["informal_generator_calls"] + m["informal_verifier_calls"]
                + m["discussion_partner_calls"] for m in metrics),
            "generated_tokens": sum(m["completion_tokens"] + m["informal_completion_tokens"]
                                     + m.get("discussion_completion_tokens", 0) for m in metrics),
            "prompt_tokens": sum(m["prompt_tokens"] + m["informal_prompt_tokens"]
                                  + m.get("discussion_prompt_tokens", 0) for m in metrics),
            "model_load_seconds": sum(m["model_load_seconds"] for m in metrics),
            "kimina_checks_in_agent": sum(m["kimina_checks"] for m in metrics),
            "independent_rechecks": sum(r.get("audit") is not None for r in runs),
            "informal_generator_calls": sum(m["informal_generator_calls"] for m in metrics),
            "informal_verifier_calls": sum(m["informal_verifier_calls"] for m in metrics),
            "discussion_calls": sum(m["discussion_partner_calls"] for m in metrics),
            "fresh_context_calls": sum(m.get("fresh_subproblem_calls", 0) for m in metrics),
            "retrieval_calls": sum(m["retrieval_calls"] for m in metrics),
            "lean_lsp_calls": sum(m["lean_lsp_calls"] for m in metrics),
            "failure_categories": dict(Counter(r["status"] if r["status"] == "audit_failed"
                else r["attempt"]["failure_category"]
                for r in runs if r.get("attempt") and not r["verified"])),
        }
    return result


def validate_dataset(config: AppConfig, cases: list[MiniF2FCase], output: Path) -> dict:
    verifier = KiminaVerifier(config.kimina)
    if not verifier.health_check():
        raise RuntimeError("Kimina unavailable")
    payload = {"purpose": "statement_elaboration_only_not_proof_verification",
               "dataset_revision": REVISION,
               "environment": environment_metadata(config), "complete": False, "cases": []}
    for case in cases:
        probe = verifier.verify(statement_probe(case), attempt_id="minif2f-statement-" + case.case_id)
        payload["cases"].append({"id": case.case_id, "split": case.split,
                                 "source_sha256": case.sha256, "compatible": probe.valid,
                                 "verification": probe})
        atomic_json(output, payload)
    payload["complete"] = True
    payload["compatible"] = sum(c["compatible"] for c in payload["cases"])
    payload["total"] = len(cases)
    atomic_json(output, payload)
    return payload


def run_benchmark(config: AppConfig, *, data: Path, split: str, output: Path,
                  variants: tuple[str, ...] = ("v4",), limit: int = 3, seed: int = 0,
                  ids: tuple[str, ...] = (), rounds: int = 3, attempts: int = 1,
                  resume: bool = False, max_seconds: float = 1800) -> dict:
    if attempts < 1 or not math.isfinite(max_seconds) or max_seconds <= 0 or not variants or len(set(variants)) != len(variants):
        raise ValueError("Invalid run budgets or duplicate variants")
    if not config.lean_lsp.enabled or not config.lean_lsp.required:
        raise ValueError("MiniF2F evaluation requires the configured Lean-LSP project")
    cases = select_cases(load_dataset(data, split), limit=limit, seed=seed, ids=ids)
    effective = {v: variant_config(config, v, rounds) for v in variants}
    metadata = run_metadata(config, data / "manifest.json")
    spec = {"dataset_manifest_sha256": metadata["manifest_fingerprint"],
        "code_fingerprint": metadata["code_fingerprint"], "config_fingerprint": metadata["config_fingerprint"],
        "environment": environment_metadata(config), "split": split,
        "case_ids": [c.case_id for c in cases], "selection_seed": seed,
        "variants": list(variants), "rounds": rounds, "attempts_per_theorem": attempts,
        "effective_configs": _serializable(effective)}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(output.suffix + ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another process owns this benchmark output") from exc
        if output.exists():
            if not resume:
                raise ValueError("Output exists; use --resume or a new output path")
            payload = json.loads(output.read_text())
            if payload.get("spec") != spec:
                raise ValueError("Resume rejected: dataset, selection, code, config or environment changed")
            keys = [(r["case_id"], r["variant"], r["repetition"]) for r in payload["attempts"]]
            allowed = {(c.case_id, v, k) for c in cases for v in variants for k in range(1, attempts + 1)}
            if len(set(keys)) != len(keys) or not set(keys) <= allowed:
                raise ValueError("Invalid checkpoint attempt keys")
            if payload["experiment_complete"] and set(keys) != allowed:
                raise ValueError("Checkpoint claims completion with missing attempts")
            if payload["experiment_complete"]:
                return payload
        elif resume:
            raise ValueError("Cannot resume a missing checkpoint")
        else:
            payload = {**metadata, "experiment": "minif2f", "spec": spec, "experiment_complete": False,
                       "attempts": [], "preflight": {}, "sessions": [], "summary": {}}
        # Create a checkpoint before acquiring expensive service/model resources.
        atomic_json(output, payload)
        verifier = KiminaVerifier(config.kimina)
        if not verifier.health_check():
            raise RuntimeError("Kimina unavailable; checkpoint can be resumed")
        audit_verifier = KiminaVerifier(replace(config.kimina, reuse_repl=False))
        backend = MLXBackend(config.mlx)
        feedback = LeanLSPMCPClient(config.lean_lsp)
        retrieval = LeanExploreMCPClient(config.lean_explore) if any(v != "lean" for v in variants) else None
        start = time.monotonic()
        done = {(r["case_id"], r["variant"], r["repetition"]) for r in payload["attempts"]}
        stop = "complete"
        try:
            for repetition in range(1, attempts + 1):
                for index, case in enumerate(cases):
                    order = list(variants)
                    if (index + repetition) % 2:
                        order.reverse()
                    for variant in order:
                        if (case.case_id, variant, repetition) in done:
                            continue
                        if time.monotonic() - start >= max_seconds:
                            stop = "session_time_budget"
                            return payload
                        if case.case_id not in payload["preflight"]:
                            check = verifier.verify(statement_probe(case),
                                attempt_id="minif2f-preflight-" + case.case_id)
                            payload["preflight"][case.case_id] = _serializable(check)
                            atomic_json(output, payload)
                        preflight = payload["preflight"][case.case_id]
                        if not preflight["valid"] and preflight["failure_category"] in {
                            "verifier_unavailable", "verifier_timeout"}:
                            payload.setdefault("preflight_errors", []).append({"case_id": case.case_id,
                                                                             "verification": preflight})
                            del payload["preflight"][case.case_id]
                            stop = "preflight_infrastructure_error"
                            return payload
                        record = {"case_id": case.case_id, "split": split, "source_sha256": case.sha256,
                            "variant": variant, "repetition": repetition, "verified": False,
                            "status": "incompatible_statement", "attempt": None, "audit": None}
                        if preflight["valid"]:
                            cfg = replace(effective[variant], agent=replace(effective[variant].agent,
                                log_path=output.with_suffix(".events.jsonl")))
                            reasoner = QwenInformalReasoner(backend, cfg.informal_reasoning) if cfg.informal_reasoning.enabled else None
                            attempt = ProofAgent(backend, verifier, cfg, feedback_provider=feedback,
                                retriever=retrieval if cfg.lean_explore.enabled else None,
                                informal_reasoner=reasoner).solve(case.source)
                            record["attempt"] = attempt.to_dict()
                            record["status"] = "proof_failed"
                            if attempt.stop_reason in {"runtime_error", "verifier_unavailable", "lean_lsp_unavailable", "retrieval_unavailable"}:
                                record["status"] = "infrastructure_error"
                            if attempt.success:
                                integrity = validate_task_preserved(case.source, attempt.final_proof)
                                audit = audit_verifier.verify(attempt.final_proof, attempt_id=attempt.attempt_id + "-audit")
                                record["audit"] = _serializable(audit)
                                record["integrity_error"] = integrity
                                record["verified"] = audit.valid and integrity is None
                                record["status"] = "verified" if record["verified"] else "audit_failed"
                                if record["verified"]:
                                    proof_path = output.parent / (output.stem + ".proofs") / f"{case.case_id}-{variant}-{repetition}.lean"
                                    proof_path.parent.mkdir(parents=True, exist_ok=True)
                                    proof_path.write_text(attempt.final_proof)
                                    record["proof_path"] = str(proof_path)
                        payload["attempts"].append(record)
                        payload["summary"] = summarize(payload)
                        atomic_json(output, payload)
                        if record["status"] == "infrastructure_error":
                            stop = "attempt_infrastructure_error"
                            return payload
            payload["experiment_complete"] = True
        except KeyboardInterrupt:
            stop = "interrupted"
        except Exception as exc:
            stop = "runtime_error"
            payload["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            unloaded = None
            cleanup_errors = []
            for name, close in [("model", backend.unload_model), ("lean_lsp", feedback.close),
                                *(([("retrieval", retrieval.close)]) if retrieval else [])]:
                try:
                    value = close()
                    if name == "model":
                        unloaded = value
                except Exception as exc:
                    cleanup_errors.append(f"{name}: {type(exc).__name__}: {exc}")
            payload["sessions"].append({"stop_reason": stop, "wall_clock_seconds": time.monotonic() - start,
                                        "model_unload_seconds": unloaded, "max_seconds": max_seconds,
                                        "cleanup_errors": cleanup_errors})
            payload["summary"] = summarize(payload)
            if payload["experiment_complete"]:
                artifact = immutable_artifact_path(output, created_at=payload["created_at"],
                    run_id=payload["run_id"], label="minif2f-" + split)
                payload["immutable_artifact"] = str(artifact)
                atomic_json(artifact, payload)
            atomic_json(output, payload)
        return payload
