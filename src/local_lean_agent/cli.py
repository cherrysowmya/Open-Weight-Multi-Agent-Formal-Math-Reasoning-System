from __future__ import annotations

import argparse
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

from .backends.mlx import MLXBackend
from .config import AppConfig, _validate, load_config
from .feedback.lean_lsp_mcp import LeanLSPMCPClient
from .informal.qwen import QwenInformalReasoner
from .orchestrator import ProofAgent
from .reproducibility import immutable_artifact_path, run_metadata, write_json
from .retrieval.lean_explore import LeanExploreMCPClient
from .telemetry import _serializable
from .types import AttemptResult, FailureCategory
from .v1_suite import assess_v1_result, load_v1_cases, suite_case_payload
from .v2_suite import (
    attempt_measurements,
    comparison_summary,
    load_v2_cases,
    validate_v2_cases,
)
from .v3_suite import (
    attempt_measurements as v3_attempt_measurements,
    comparison_summary as v3_comparison_summary,
    load_v3_cases,
    validate_v3_cases,
)
from .verification.kimina import KiminaVerifier


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local-lean-agent")
    parser.add_argument("--config", type=Path, default=None, help="TOML configuration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve = subparsers.add_parser("solve", help="Generate and verify a Lean proof")
    solve.add_argument("input", type=Path, help="Lean file containing the task")
    solve.add_argument("--output", type=Path, default=None, help="Write the final candidate")
    solve.add_argument(
        "--full-json",
        action="store_true",
        help="Print full candidates and model responses instead of a compact summary",
    )
    solve.add_argument(
        "--max-rounds",
        type=_positive_int,
        default=None,
        help="Override the maximum number of model repair/generation rounds",
    )
    solve.add_argument(
        "--informal-policy", choices=("off", "always", "after_model_failures"),
        default=None, help="Override V3 invocation without editing the shared config",
    )
    solve.add_argument("--v4", choices=("on", "off"), default=None,
                       help="Enable or disable V4 discussion and fresh contexts")
    _formal_output_arguments(solve)

    check = subparsers.add_parser("check", help="Check a Lean file with Kimina")
    check.add_argument("input", type=Path)

    suite = subparsers.add_parser("v1-suite", help="Run the controlled V1 Lean suite")
    suite.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=Path("benchmarks/v1/manifest.toml"),
    )
    suite.add_argument(
        "--output",
        type=Path,
        default=Path("runs/v1-suite-latest.json"),
        help="Write the complete suite trace",
    )
    suite.add_argument(
        "--repetitions",
        type=_positive_int,
        default=1,
        help="Require this many consecutive perfect suite runs",
    )

    v2 = subparsers.add_parser(
        "v2-ablation", help="Run paired LeanExplore OFF/ON retrieval experiments"
    )
    v2.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=Path("benchmarks/v2/manifest.toml"),
    )
    v2.add_argument(
        "--output",
        type=Path,
        default=Path("runs/v2-ablation-latest.json"),
        help="Write complete paired traces and aggregate deltas",
    )
    v2.add_argument(
        "--repetitions",
        type=_positive_int,
        default=1,
        help="Number of paired repetitions (condition order is counterbalanced)",
    )

    validate = subparsers.add_parser("v2-validate", help="Check V2 repair seeds and reference proofs without Qwen")
    validate.add_argument("manifest", type=Path, nargs="?", default=Path("benchmarks/v2/manifest.toml"))
    validate.add_argument("--output", type=Path, default=Path("runs/v2-validation-latest.json"))

    v3 = subparsers.add_parser(
        "v3-ablation", help="Compare full informal reasoning with V2 or generator-only"
    )
    v3.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=Path("benchmarks/v3/manifest.toml"),
    )
    v3.add_argument(
        "--output",
        type=Path,
        default=Path("runs/v3-ablation-latest.json"),
        help="Write complete paired traces and aggregate deltas",
    )
    v3.add_argument(
        "--repetitions",
        type=_positive_int,
        default=1,
        help="Number of paired repetitions (condition order is counterbalanced)",
    )
    v3.add_argument(
        "--informal-policy", choices=("always", "after_model_failures"),
        default=None, help="Invocation policy for enabled informal roles in either condition",
    )
    v3.add_argument(
        "--control", choices=("v2", "generator-only"), default="v2",
        help="Compare full V3 with V2 or with an unreviewed generator-only strategy",
    )

    v3_validate = subparsers.add_parser(
        "v3-validate", help="Check V3 repair seeds and reference proofs without Qwen"
    )
    v3_validate.add_argument(
        "manifest", type=Path, nargs="?", default=Path("benchmarks/v3/manifest.toml")
    )
    v3_validate.add_argument(
        "--output", type=Path, default=Path("runs/v3-validation-latest.json")
    )

    subparsers.add_parser(
        "doctor", help="Check local MLX, Kimina, Lean LSP, and LeanExplore"
    )
    prepare = subparsers.add_parser("minif2f-prepare", help="Download and verify pinned MiniF2F statements")
    prepare.add_argument("--data", type=Path, default=Path("benchmarks/minif2f/data"))
    for command in ("minif2f-validate", "minif2f-run"):
        benchmark = subparsers.add_parser(command, help=(
            "Elaborate MiniF2F statements without proving them" if command.endswith("validate")
            else "Run resumable, independently audited MiniF2F evaluation"))
        benchmark.add_argument("--data", type=Path, default=Path("benchmarks/minif2f/data"))
        benchmark.add_argument("--split", choices=("valid", "test"), default="valid")
        benchmark.add_argument("--limit", type=int, default=3, help="Selected problems; 0 means the whole split")
        benchmark.add_argument("--seed", type=int, default=0, help="Deterministic subset selection, not model sampling")
        benchmark.add_argument("--case", action="append", default=[], help="Explicit problem ID; overrides --limit")
        benchmark.add_argument("--output", type=Path, default=Path("runs/" + command + "-latest.json"))
        if command.endswith("run"):
            _formal_output_arguments(benchmark)
            benchmark.add_argument("--variant", action="append", choices=("lean", "v2", "v3", "v4", "portfolio", "v4_portfolio"),
                                   help="Repeat for paired conditions; default v4")
            benchmark.add_argument("--max-rounds", type=_positive_int, default=3)
            benchmark.add_argument("--attempts", type=_positive_int, default=1)
            benchmark.add_argument("--max-seconds", type=float, default=1800,
                                   help="Soft session limit checked between theorem attempts")
            benchmark.add_argument("--resume", action="store_true")
    return parser


def _formal_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--proof-format", choices=("full_file", "proof_body"),
                        help="Return whole files (control) or deterministically assembled proof bodies")
    parser.add_argument("--formal-output-policy", choices=("legacy", "fixed", "adaptive"),
                        help="Override formal output recovery; legacy preserves the old policy")
    parser.add_argument("--formal-output-tokens", type=int, choices=(512, 1024, 2048),
                        help="Fixed or initial adaptive output allowance for formal requests")
    parser.add_argument("--formal-output-ceiling", type=int, choices=(512, 1024, 2048),
                        help="Maximum adaptive formal output allowance")


def _output_budget_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    values = {key: getattr(args, arg, None) for key, arg in (
        ("proof_format", "proof_format"),
        ("output_budget_policy", "formal_output_policy"),
        ("max_output_tokens", "formal_output_tokens"),
        ("max_recovery_output_tokens", "formal_output_ceiling"))}
    values = {key: value for key, value in values.items() if value is not None}
    if values:
        config = replace(config, generation=replace(config.generation, **values))
        _validate(config)
    return config


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        config = _output_budget_overrides(config, args)
        if args.command.startswith("minif2f-"):
            from .minif2f import load_dataset, prepare_dataset, select_cases
            from .benchmark import run_benchmark, validate_dataset
            if args.command == "minif2f-prepare":
                dataset = prepare_dataset(args.data)
                print(json.dumps({"ready": True, "revision": dataset["revision"],
                                  "cases": len(dataset["cases"]), "data": str(args.data)}, indent=2))
                return 0
            if args.command == "minif2f-validate":
                cases = select_cases(load_dataset(args.data, args.split), limit=args.limit,
                                     seed=args.seed, ids=tuple(args.case))
                result = validate_dataset(config, cases, args.output)
                print(json.dumps({key: result[key] for key in ("complete", "compatible", "total")}, indent=2))
                return 0 if result["compatible"] == result["total"] else 1
            result = run_benchmark(config, data=args.data, split=args.split, output=args.output,
                variants=tuple(args.variant or ["v4"]), limit=args.limit, seed=args.seed,
                ids=tuple(args.case), rounds=args.max_rounds, attempts=args.attempts,
                resume=args.resume, max_seconds=args.max_seconds)
            print(json.dumps({"experiment_complete": result["experiment_complete"],
                "summary": result["summary"], "output": str(args.output),
                "stop_reason": result["sessions"][-1]["stop_reason"] if result["sessions"] else None}, indent=2))
            return 0 if result["experiment_complete"] else 1
        if setting := getattr(args, "v4", None):
            config = replace(config, v4=replace(config.v4, enabled=setting == "on"))
        if policy := getattr(args, "informal_policy", None):
            config = replace(config, informal_reasoning=replace(
                config.informal_reasoning, enabled=policy != "off",
                invocation_policy=(config.informal_reasoning.invocation_policy
                                   if policy == "off" else policy),
            ))
        if args.command == "doctor":
            return _doctor(config)
        if args.command == "check":
            code = args.input.read_text(encoding="utf-8")
            result = KiminaVerifier(config.kimina).verify(code, attempt_id="manual-check")
            print(json.dumps(_serializable(result), indent=2, ensure_ascii=False))
            return 0 if result.valid else 1
        if args.command == "v1-suite":
            return _run_v1_suite(
                config, args.manifest, args.output, args.repetitions
            )
        if args.command == "v2-ablation":
            return _run_v2_ablation(
                config, args.manifest, args.output, args.repetitions
            )
        if args.command == "v2-validate":
            validation = validate_v2_cases(load_v2_cases(args.manifest), KiminaVerifier(config.kimina))
            payload = {**run_metadata(config, args.manifest), **validation}
            write_json(args.output, payload)
            print(json.dumps({key: validation[key] for key in ("passed", "repair_seeds_checked", "reference_proofs_checked")}, indent=2))
            return 0 if validation["passed"] else 1
        if args.command == "v3-ablation":
            return _run_v3_ablation(
                config, args.manifest, args.output, args.repetitions, args.control
            )
        if args.command == "v3-validate":
            validation = validate_v3_cases(
                load_v3_cases(args.manifest), KiminaVerifier(config.kimina)
            )
            payload = {**run_metadata(config, args.manifest), **validation}
            write_json(args.output, payload)
            print(
                json.dumps(
                    {
                        key: validation[key]
                        for key in (
                            "passed",
                            "repair_seeds_checked",
                            "reference_proofs_checked",
                        )
                    },
                    indent=2,
                )
            )
            return 0 if validation["passed"] else 1
        if args.command == "solve":
            if args.max_rounds is not None:
                config = replace(
                    config,
                    agent=replace(config.agent, max_iterations=args.max_rounds),
                )
            theorem = args.input.read_text(encoding="utf-8")
            feedback = _feedback_provider(config)
            retriever = _semantic_retriever(config)
            backend = MLXBackend(config.mlx)
            informal_reasoner = (
                QwenInformalReasoner(backend, config.informal_reasoning)
                if config.informal_reasoning.enabled
                else None
            )
            try:
                agent = ProofAgent(
                    backend,
                    KiminaVerifier(config.kimina),
                    config,
                    feedback_provider=feedback,
                    retriever=retriever,
                    informal_reasoner=informal_reasoner,
                )
                result = agent.solve(theorem)
            finally:
                if feedback is not None:
                    feedback.close()
                if retriever is not None:
                    retriever.close()
            _write_attempt_result(result, config.agent.result_dir)
            if args.output is not None and result.success and result.final_proof:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(result.final_proof, encoding="utf-8")
            printable = result if args.full_json else _terminal_summary(result)
            print(json.dumps(_serializable(printable), indent=2, ensure_ascii=False))
            return 0 if result.success else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _doctor(config: AppConfig) -> int:
    backend = MLXBackend(config.mlx)
    kimina = KiminaVerifier(config.kimina)
    mlx_installed = importlib.util.find_spec("mlx_lm") is not None
    kimina_server = kimina.health_check()
    kimina_repl = False
    kimina_repl_error = None
    if kimina_server:
        smoke = kimina.verify("#check Nat", attempt_id="doctor-smoke-check")
        kimina_repl = smoke.valid
        if not smoke.valid:
            kimina_repl_error = (
                smoke.diagnostics[0]
                if smoke.diagnostics
                else smoke.failure_category.value
            )
    feedback = _feedback_provider(config)
    lean_lsp_installed = _command_exists(config.lean_lsp.command)
    lean_lsp_mcp = False
    lean_lsp_goal = False
    lean_lsp_error = None
    if feedback is not None:
        try:
            lean_lsp_mcp = feedback.health_check()
            if lean_lsp_mcp:
                smoke = feedback.inspect(
                    "import Mathlib\n\ntheorem lsp_smoke (n : ℕ) : n + 0 = n := by\n"
                    "  exact Nat.zero_add n\n",
                    compiler_diagnostics=(
                        "4:2: error: type mismatch in seeded LSP smoke test",
                    ),
                    attempt_id="doctor-lsp-smoke",
                )
                lean_lsp_goal = bool(smoke.available and smoke.goal_state)
                lean_lsp_error = smoke.error_message
            else:
                lean_lsp_error = feedback.last_error
        finally:
            feedback.close()
    retriever = _semantic_retriever(config)
    lean_explore_installed = _command_exists(config.lean_explore.command)
    lean_explore_mcp = False
    lean_explore_search = False
    lean_explore_error = None
    lean_explore_version = None
    if retriever is not None:
        try:
            lean_explore_mcp = retriever.health_check()
            lean_explore_version = retriever.data_version
            if lean_explore_mcp:
                retrieval_smoke = retriever.retrieve(
                    "Lean theorem: appending the empty list on the right leaves a list unchanged"
                )
                lean_explore_search = bool(
                    retrieval_smoke.available and retrieval_smoke.hits
                )
                lean_explore_error = retrieval_smoke.error_message
            else:
                lean_explore_error = retriever.last_error
        finally:
            retriever.close()
    statuses = {
        "mlx_lm_installed": mlx_installed,
        "mlx_server": backend.health_check(),
        "kimina_server": kimina_server,
        "kimina_repl": kimina_repl,
        "kimina_repl_error": kimina_repl_error,
        "mlx_managed_mode": config.mlx.managed_server,
        "model": config.mlx.model_id,
        "lean_lsp_enabled": config.lean_lsp.enabled,
        "lean_lsp_mcp_installed": lean_lsp_installed,
        "lean_lsp_mcp": lean_lsp_mcp,
        "lean_lsp_goal": lean_lsp_goal,
        "lean_lsp_error": lean_lsp_error,
        "lean_explore_enabled": config.lean_explore.enabled,
        "lean_explore_installed": lean_explore_installed,
        "lean_explore_mcp": lean_explore_mcp,
        "lean_explore_search": lean_explore_search,
        "lean_explore_data_version": lean_explore_version,
        "lean_explore_error": lean_explore_error,
        "informal_reasoning_enabled": config.informal_reasoning.enabled,
        "informal_invocation_policy": config.informal_reasoning.invocation_policy,
        "informal_max_refinement_rounds": (
            config.informal_reasoning.max_refinement_rounds
        ),
        "informal_max_context_tokens": config.informal_reasoning.max_context_tokens,
    }
    print(json.dumps(statuses, indent=2))
    # In managed mode, MLX is expected to be down until an attempt loads it.
    mlx_ok = statuses["mlx_server"] or (
        config.mlx.managed_server and mlx_installed
    )
    lsp_ok = (
        not config.lean_lsp.enabled
        or not config.lean_lsp.required
        or (lean_lsp_mcp and lean_lsp_goal)
    )
    retrieval_ok = (
        not config.lean_explore.enabled
        or not config.lean_explore.required
        or (lean_explore_mcp and lean_explore_search)
    )
    return 0 if mlx_ok and kimina_repl and lsp_ok and retrieval_ok else 1


def _terminal_summary(result: AttemptResult) -> dict[str, object]:
    return {
        "success": result.success,
        "end_reason": result.end_reason,
        "rounds": result.rounds,
    }


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _write_attempt_result(result: AttemptResult, result_dir: Path) -> Path:
    result_dir.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(_serializable(result), indent=2, ensure_ascii=False) + "\n"
    attempt_path = result_dir / f"{result.attempt_id}.json"
    attempt_path.write_text(serialized, encoding="utf-8")
    (result_dir / "latest.json").write_text(serialized, encoding="utf-8")
    return attempt_path


def _run_v1_suite(
    config: AppConfig,
    manifest: Path,
    output: Path,
    repetitions: int = 1,
) -> int:
    run_payloads: list[dict[str, object]] = []
    for repetition in range(1, repetitions + 1):
        payload = _execute_v1_suite(config, manifest, repetition)
        metadata = run_metadata(config, manifest)
        artifact = immutable_artifact_path(
            output,
            created_at=str(metadata["created_at"]),
            run_id=str(metadata["run_id"]),
            label=f"run-{repetition:02d}",
        )
        payload = {
            **metadata,
            "repetition": repetition,
            "immutable_artifact": str(artifact),
            **payload,
        }
        write_json(artifact, payload)
        run_payloads.append(payload)

    perfect_runs = sum(bool(run["suite_success"]) for run in run_payloads)
    stable_success = perfect_runs == repetitions
    if repetitions == 1:
        final_payload: dict[str, object] = {
            **run_payloads[0],
            "repetitions": 1,
            "perfect_runs": perfect_runs,
            "stable_success": stable_success,
        }
        terminal = {
            "passed": final_payload["passed"],
            "failed": final_payload["failed"],
            "total": final_payload["total"],
            "suite_success": final_payload["suite_success"],
        }
    else:
        group_metadata = run_metadata(config, manifest)
        final_payload = {
            **group_metadata,
            "repetitions": repetitions,
            "perfect_runs": perfect_runs,
            "stable_success": stable_success,
            "manifest": str(manifest),
            "runs": run_payloads,
        }
        terminal = {
            "repetitions": repetitions,
            "perfect_runs": perfect_runs,
            "stable_success": stable_success,
        }
        group_artifact = immutable_artifact_path(
            output,
            created_at=str(group_metadata["created_at"]),
            run_id=str(group_metadata["run_id"]),
            label="stability",
        )
        final_payload["immutable_artifact"] = str(group_artifact)
        write_json(group_artifact, final_payload)

    write_json(output, final_payload)
    print(json.dumps(terminal, indent=2))
    return 0 if stable_success else 1


def _execute_v1_suite(
    config: AppConfig, manifest: Path, repetition: int
) -> dict[str, object]:
    # Freeze the V1 benchmark boundary: it excludes V2 retrieval and V3 reasoning.
    config = replace(
        config,
        v4=replace(config.v4, enabled=False),
        lean_explore=replace(config.lean_explore, enabled=False),
        informal_reasoning=replace(config.informal_reasoning, enabled=False),
    )
    cases = load_v1_cases(manifest)
    backend = MLXBackend(config.mlx)
    verifier = KiminaVerifier(config.kimina)
    feedback = _feedback_provider(config)
    started = time.monotonic()
    case_payloads: list[dict[str, object]] = []
    passed_count = 0
    unload_seconds = 0.0
    try:
        for case in cases:
            case_config = replace(
                config,
                agent=replace(
                    config.agent,
                    max_iterations=case.max_rounds,
                    unload_model_after_attempt=False,
                ),
            )
            result = ProofAgent(
                backend,
                verifier,
                case_config,
                feedback_provider=feedback,
            ).solve(case.path.read_text(encoding="utf-8"))
            _write_attempt_result(result, config.agent.result_dir)
            passed, assessment = assess_v1_result(case, result)
            passed_count += int(passed)
            case_payloads.append(
                suite_case_payload(case, result, passed, assessment)
            )
    finally:
        unload_seconds = backend.unload_model()
        if feedback is not None:
            feedback.close()

    summary: dict[str, object] = {
        "passed": passed_count,
        "failed": len(cases) - passed_count,
        "total": len(cases),
        "suite_success": passed_count == len(cases),
    }
    return {
        **summary,
        "repetition": repetition,
        "wall_clock_seconds": time.monotonic() - started,
        "final_model_unload_seconds": unload_seconds,
        "manifest": str(manifest),
        "cases": case_payloads,
    }


def _feedback_provider(config: AppConfig) -> LeanLSPMCPClient | None:
    return LeanLSPMCPClient(config.lean_lsp) if config.lean_lsp.enabled else None


def _semantic_retriever(config: AppConfig) -> LeanExploreMCPClient | None:
    return (
        LeanExploreMCPClient(config.lean_explore)
        if config.lean_explore.enabled
        else None
    )


def _run_v2_ablation(
    config: AppConfig,
    manifest: Path,
    output: Path,
    repetitions: int = 1,
) -> int:
    # Freeze the V2 benchmark boundary even when the user's normal config enables V3.
    config = replace(
        config,
        v4=replace(config.v4, enabled=False),
        informal_reasoning=replace(config.informal_reasoning, enabled=False),
    )
    if not config.lean_lsp.enabled or not config.lean_lsp.required:
        raise ValueError("V2 paired benchmark requires Lean LSP enabled and required in both conditions")
    cases = load_v2_cases(manifest)
    metadata = run_metadata(config, manifest)
    validation = validate_v2_cases(cases, KiminaVerifier(config.kimina))
    if not validation["passed"]:
        write_json(output, {**metadata, "experiment_complete": False, "preflight": validation})
        raise ValueError(f"V2 fixture validation failed; see {output}")
    backend = MLXBackend(config.mlx)
    verifier = KiminaVerifier(config.kimina)
    feedback = _feedback_provider(config)
    retrieval_config = replace(config.lean_explore, enabled=True, required=True)
    retriever = LeanExploreMCPClient(retrieval_config)
    baseline_runs: list[dict[str, object]] = []
    retrieval_runs: list[dict[str, object]] = []
    started = time.monotonic()
    unload_seconds = 0.0
    try:
        for repetition in range(1, repetitions + 1):
            for case_index, case in enumerate(cases):
                # Alternate order to reduce warmup/order bias without changing settings.
                conditions = (
                    ("retrieval", True), ("baseline", False)
                ) if (repetition + case_index) % 2 else (
                    ("baseline", False), ("retrieval", True)
                )
                for order_index, (condition, retrieval_enabled) in enumerate(
                    conditions, start=1
                ):
                    case_config = replace(
                        config,
                        lean_explore=replace(
                            retrieval_config, enabled=retrieval_enabled
                        ),
                        agent=replace(
                            config.agent,
                            max_iterations=case.max_rounds,
                            unload_model_after_attempt=False,
                            fallback_enabled=False,
                        ),
                    )
                    result = ProofAgent(
                        backend,
                        verifier,
                        case_config,
                        feedback_provider=feedback,
                        retriever=retriever if retrieval_enabled else None,
                    ).solve(case.path.read_text(encoding="utf-8"))
                    _write_attempt_result(result, config.agent.result_dir)
                    payload: dict[str, object] = {
                        "id": case.case_id,
                        "path": str(case.path),
                        "mode": case.mode,
                        "purpose": case.purpose,
                        "condition": condition,
                        "repetition": repetition,
                        "condition_order": order_index,
                        "measurements": attempt_measurements(case, result),
                        "attempt": result.to_dict(),
                    }
                    (retrieval_runs if retrieval_enabled else baseline_runs).append(
                        payload
                    )
                    write_json(output, {
                        **metadata,
                        "experiment_complete": False,
                        "status": "running",
                        "completed_attempts": len(baseline_runs) + len(retrieval_runs),
                        "preflight": validation,
                        "baseline_cases": baseline_runs,
                        "retrieval_cases": retrieval_runs,
                    })
    finally:
        unload_seconds = backend.unload_model()
        retriever.close()
        if feedback is not None:
            feedback.close()

    comparison = comparison_summary(baseline_runs, retrieval_runs)
    infrastructure_failures = sum(
        item["attempt"]["stop_reason"] not in {"verified", "iteration_budget"}
        for item in [*baseline_runs, *retrieval_runs]
    )
    negative_false_positives = sum(
        item["mode"] == "negative" and item["attempt"]["success"]
        for item in [*baseline_runs, *retrieval_runs]
    )
    retrieval_exercised = all(
        item["measurements"]["retrieval_queries"] > 0 for item in retrieval_runs
    )
    experiment_complete = bool(
        not infrastructure_failures
        and not negative_false_positives
        and retrieval_exercised
        and len(baseline_runs) == len(retrieval_runs) == len(cases) * repetitions
    )
    payload = {
        **metadata,
        "experiment": "v2_semantic_retrieval_ablation",
        "manifest": str(manifest),
        "repetitions": repetitions,
        "cases_per_condition": len(cases) * repetitions,
        "fallback_enabled": False,
        "condition_order": "counterbalanced_by_case_and_repetition",
        "experiment_complete": experiment_complete,
        "preflight": validation,
        "effective_settings": {
            "generation": config.generation,
            "lean_lsp": config.lean_lsp,
            "lean_explore": retrieval_config,
            "fallback_enabled": False,
            "round_budgets": {case.case_id: case.max_rounds for case in cases},
        },
        "infrastructure_failures": infrastructure_failures,
        "negative_false_positives": negative_false_positives,
        "wall_clock_seconds": time.monotonic() - started,
        "final_model_unload_seconds": unload_seconds,
        "comparison": comparison,
        "baseline_cases": baseline_runs,
        "retrieval_cases": retrieval_runs,
    }
    artifact = immutable_artifact_path(
        output,
        created_at=str(metadata["created_at"]),
        run_id=str(metadata["run_id"]),
        label="paired-ablation",
    )
    payload["immutable_artifact"] = str(artifact)
    write_json(artifact, payload)
    write_json(output, payload)
    terminal = {
        "experiment_complete": experiment_complete,
        "cases_per_condition": len(cases) * repetitions,
        "baseline": comparison["baseline"],
        "retrieval": comparison["retrieval"],
        "reductions_baseline_minus_retrieval": comparison[
            "reductions_baseline_minus_retrieval"
        ],
    }
    print(json.dumps(terminal, indent=2))
    return 0 if experiment_complete else 1


def _run_v3_ablation(
    config: AppConfig,
    manifest: Path,
    output: Path,
    repetitions: int = 1,
    control: str = "v2",
) -> int:
    if control not in {"v2", "generator-only"}:
        raise ValueError("Unknown V3 control condition")
    if not config.lean_lsp.enabled or not config.lean_lsp.required:
        raise ValueError("V3 paired benchmark requires Lean LSP enabled and required")
    if not config.lean_explore.enabled or not config.lean_explore.required:
        raise ValueError("V3 paired benchmark requires LeanExplore enabled and required")
    # Keep generic tactic-search assistance out of the informal-reasoning
    # comparison. The separate hardening runner measures this intervention.
    config = replace(config, informal_reasoning=replace(
        config.informal_reasoning, rewrite_salvage_enabled=False),
        v4=replace(config.v4, enabled=False))
    cases = load_v3_cases(manifest)
    metadata = run_metadata(config, manifest)
    verifier = KiminaVerifier(config.kimina)
    validation = validate_v3_cases(cases, verifier)
    if not validation["passed"]:
        write_json(
            output,
            {**metadata, "experiment_complete": False, "preflight": validation},
        )
        raise ValueError(f"V3 fixture validation failed; see {output}")

    backend = MLXBackend(config.mlx)
    feedback = _feedback_provider(config)
    retriever = _semantic_retriever(config)
    assert retriever is not None
    informal_config = replace(config.informal_reasoning, enabled=True, verifier_enabled=True)
    reasoner = QwenInformalReasoner(backend, informal_config)
    generator_config = replace(informal_config, verifier_enabled=False)
    generator_only_reasoner = QwenInformalReasoner(backend, generator_config)
    baseline_runs: list[dict[str, object]] = []
    informal_runs: list[dict[str, object]] = []
    started = time.monotonic()
    unload_seconds = 0.0
    try:
        for repetition in range(1, repetitions + 1):
            for case_index, case in enumerate(cases):
                conditions = (
                    ("informal", True), ("baseline", False)
                ) if (repetition + case_index) % 2 else (
                    ("baseline", False), ("informal", True)
                )
                for order_index, (condition, informal_enabled) in enumerate(
                    conditions, start=1
                ):
                    case_config = replace(
                        config,
                        informal_reasoning=replace(
                            informal_config, enabled=informal_enabled or control == "generator-only",
                            verifier_enabled=informal_enabled,
                        ),
                        agent=replace(
                            config.agent,
                            max_iterations=case.max_rounds,
                            unload_model_after_attempt=False,
                            fallback_enabled=False,
                        ),
                    )
                    result = ProofAgent(
                        backend,
                        verifier,
                        case_config,
                        feedback_provider=feedback,
                        retriever=retriever,
                        informal_reasoner=(reasoner if informal_enabled else
                                           generator_only_reasoner if control == "generator-only" else None),
                    ).solve(case.path.read_text(encoding="utf-8"))
                    _write_attempt_result(result, config.agent.result_dir)
                    payload: dict[str, object] = {
                        "id": case.case_id,
                        "path": str(case.path),
                        "mode": case.mode,
                        "purpose": case.purpose,
                        "condition": condition,
                        "variant": "generator_verifier" if informal_enabled else control,
                        "repetition": repetition,
                        "condition_order": order_index,
                        "measurements": v3_attempt_measurements(case, result),
                        "attempt": result.to_dict(),
                    }
                    (informal_runs if informal_enabled else baseline_runs).append(payload)
                    write_json(
                        output,
                        {
                            **metadata,
                            "experiment_complete": False,
                            "status": "running",
                            "completed_attempts": len(baseline_runs) + len(informal_runs),
                            "preflight": validation,
                            "baseline_cases": baseline_runs,
                            "informal_cases": informal_runs,
                        },
                    )
    finally:
        unload_seconds = backend.unload_model()
        retriever.close()
        if feedback is not None:
            feedback.close()

    comparison = v3_comparison_summary(baseline_runs, informal_runs)
    all_runs = [*baseline_runs, *informal_runs]
    informal_infrastructure_failures = sum(
        item["measurements"]["informal_stop_reason"] in {"runtime_error", "unavailable"}
        for item in all_runs
    )
    infrastructure_failures = sum(
        item["attempt"]["stop_reason"] not in {"verified", "iteration_budget"}
        for item in all_runs
    ) + informal_infrastructure_failures
    negative_false_positives = sum(
        item["mode"] == "negative" and item["attempt"]["success"]
        for item in all_runs
    )
    experiment_complete = bool(
        not infrastructure_failures
        and not negative_false_positives
        and len(baseline_runs) == len(informal_runs) == len(cases) * repetitions
    )
    payload = {
        **metadata,
        "experiment": "v3_informal_reasoning_ablation",
        "control_variant": control,
        "manifest": str(manifest),
        "repetitions": repetitions,
        "cases_per_condition": len(cases) * repetitions,
        "fallback_enabled": False,
        "retrieval_enabled_both_conditions": True,
        "condition_order": "counterbalanced_by_case_and_repetition",
        "experiment_complete": experiment_complete,
        "informal_invoked": comparison["informal"]["informal_triggered_cases"] > 0,
        "informal_exercised": comparison["informal"]["informal_review_completed_cases"] > 0,
        "informal_pipeline_healthy": (
            comparison["informal"]["informal_review_completed_cases"] > 0
            and comparison["informal"]["informal_stage_failures"] == 0
        ),
        "informal_infrastructure_failures": informal_infrastructure_failures,
        "preflight": validation,
        "effective_settings": {
            "generation": config.generation,
            "lean_lsp": config.lean_lsp,
            "lean_explore": config.lean_explore,
            "informal_reasoning": informal_config,
            "baseline_informal_enabled": control == "generator-only",
            "baseline_verifier_enabled": False,
            "fallback_enabled": False,
            "round_budgets": {case.case_id: case.max_rounds for case in cases},
        },
        "infrastructure_failures": infrastructure_failures,
        "negative_false_positives": negative_false_positives,
        "wall_clock_seconds": time.monotonic() - started,
        "final_model_unload_seconds": unload_seconds,
        "comparison": comparison,
        "baseline_cases": baseline_runs,
        "informal_cases": informal_runs,
    }
    artifact = immutable_artifact_path(
        output,
        created_at=str(metadata["created_at"]),
        run_id=str(metadata["run_id"]),
        label="paired-ablation",
    )
    payload["immutable_artifact"] = str(artifact)
    write_json(artifact, payload)
    write_json(output, payload)
    terminal = {
        "experiment_complete": experiment_complete,
        "informal_exercised": payload["informal_exercised"],
        "informal_pipeline_healthy": payload["informal_pipeline_healthy"],
        "cases_per_condition": len(cases) * repetitions,
        "baseline": comparison["baseline"],
        "informal": comparison["informal"],
        "delta_informal_minus_baseline": comparison[
            "delta_informal_minus_baseline"
        ],
        "reductions_baseline_minus_informal": comparison[
            "reductions_baseline_minus_informal"
        ],
    }
    print(json.dumps(terminal, indent=2))
    return 0 if experiment_complete else 1


def _command_exists(command: str) -> bool:
    path = Path(command)
    if path.is_absolute() or len(path.parts) > 1:
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(command) is not None
