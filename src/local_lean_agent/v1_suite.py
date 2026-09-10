from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from .types import AttemptResult


VALID_MODES = frozenset({"generation", "repair", "already_valid", "negative"})
SUITE_ID = "v1_compiler_guided_loop"


@dataclass(frozen=True, slots=True)
class V1Case:
    case_id: str
    path: Path
    mode: str
    max_rounds: int
    purpose: str


def load_v1_cases(manifest_path: str | Path) -> list[V1Case]:
    manifest = Path(manifest_path)
    with manifest.open("rb") as handle:
        data = tomllib.load(handle)
    suite_id = str(data.get("suite", "")).strip()
    if suite_id != SUITE_ID:
        raise ValueError(
            f"V1 runner requires suite = {SUITE_ID!r}, got {suite_id!r}. "
            "Use `v2-ablation` for benchmarks/v2/manifest.toml."
        )
    raw_cases = data.get("case")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("V1 suite manifest must contain at least one [[case]]")

    cases: list[V1Case] = []
    seen: set[str] = set()
    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise ValueError("Each [[case]] must be a TOML table")
        case_id = str(raw.get("id", "")).strip()
        mode = str(raw.get("mode", "")).strip()
        relative_path = Path(str(raw.get("path", "")))
        max_rounds = int(raw.get("max_rounds", 5))
        purpose = str(raw.get("purpose", "")).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"Missing or duplicate V1 case id: {case_id!r}")
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown V1 case mode for {case_id}: {mode!r}")
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"V1 case path must stay under the manifest: {relative_path}")
        if max_rounds <= 0:
            raise ValueError(f"max_rounds must be positive for {case_id}")
        path = manifest.parent / relative_path
        if not path.is_file():
            raise ValueError(f"V1 case file does not exist: {path}")
        seen.add(case_id)
        cases.append(V1Case(case_id, path, mode, max_rounds, purpose))
    return cases


def assess_v1_result(case: V1Case, result: AttemptResult) -> tuple[bool, str]:
    rejected = [
        item for item in result.iterations if not item.verification.valid
    ]
    compiler_guidance_observed = all(
        item.lean_feedback is not None and item.lean_feedback.available
        for item in rejected
    )
    if case.mode == "generation":
        passed = (
            result.success
            and result.rounds >= 1
            and compiler_guidance_observed
        )
        reason = (
            "generated proof verified with LSP-guided repair when needed"
            if passed
            else "generation was not verified or a rejected candidate lacked LSP feedback"
        )
    elif case.mode == "repair":
        seeded_failure = bool(
            result.iterations
            and result.iterations[0].iteration == 0
            and not result.iterations[0].verification.valid
        )
        passed = (
            result.success
            and seeded_failure
            and result.rounds >= 1
            and compiler_guidance_observed
        )
        reason = (
            "seed rejected with LSP feedback, repaired proof verified"
            if passed
            else "required LSP-guided seed-failure-to-verified-repair trace was not observed"
        )
    elif case.mode == "already_valid":
        passed = bool(
            result.success
            and result.rounds == 0
            and result.iterations
            and result.iterations[0].iteration == 0
            and result.iterations[0].verification.valid
        )
        reason = "valid seed accepted without model call" if passed else "valid seed path failed"
    else:
        passed = bool(
            not result.success
            and result.end_reason == "MAX_ROUNDS"
            and result.rounds == case.max_rounds
            and all(not item.verification.valid for item in result.iterations)
            and compiler_guidance_observed
        )
        reason = (
            "false theorem rejected through budget with LSP feedback"
            if passed
            else "negative safety or LSP-feedback contract failed"
        )
    return passed, reason


def suite_case_payload(
    case: V1Case, result: AttemptResult, passed: bool, assessment: str
) -> dict[str, Any]:
    return {
        "id": case.case_id,
        "path": str(case.path),
        "mode": case.mode,
        "purpose": case.purpose,
        "expected_max_rounds": case.max_rounds,
        "passed": passed,
        "assessment": assessment,
        "attempt": result.to_dict(),
    }
