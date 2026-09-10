"""Live model-quality checks, distinct from deterministic mocked unit tests."""
from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import re
import tomllib

from .informal.qwen import QwenInformalReasoner, contains_lean_code
from .types import InformalTaskPacket, InformalVerdict


def load_quality_cases(path: Path) -> list[dict]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if data.get("suite") != "v3_informal_components":
        raise ValueError("Expected a V3 informal component manifest")
    cases = data.get("case", [])
    seen = set()
    for case in cases:
        if not case.get("id") or case["id"] in seen:
            raise ValueError("Missing or duplicate component case id")
        seen.add(case["id"])
        if case.get("kind") not in {"generator", "verifier", "refinement"}:
            raise ValueError("Unknown V3 component kind")
        if not case.get("theorem"):
            raise ValueError("Missing theorem")
        if case["kind"] != "generator" and not case.get("proof"):
            raise ValueError("Missing supplied proof")
        if case["kind"] == "verifier" and case.get("expected") not in {"accept", "fail"}:
            raise ValueError("Expected verdict must be accept or fail")
        for group in case.get("concepts", []):
            if not group:
                raise ValueError("Empty rubric group")
            for pattern in group:
                re.compile(pattern)
    if not cases:
        raise ValueError("Empty component manifest")
    return cases


def concept_checks(text: str, groups: list[list[str]]) -> list[bool]:
    # Heuristic relevance checks only. They cannot certify valid mathematics.
    return [any(re.search(pattern, text, re.IGNORECASE) for pattern in group)
            for group in groups]


def run_quality_case(case: dict, reasoner: QwenInformalReasoner) -> dict:
    packet = InformalTaskPacket(theorem=case["theorem"])
    result = {"id": case["id"], "kind": case["kind"], "passed": False,
              "rubric_is_heuristic": True, "lean_verified": None}
    requests = []
    try:
        if case["kind"] == "generator":
            generator = QwenInformalReasoner(reasoner.backend, replace(
                reasoner.config, verifier_enabled=False))
            reasoning = generator.reason(packet)
            result["reasoning"] = asdict(reasoning)
            result["checks"] = {
                "parseable_outline": bool(reasoning.final_proof),
                "no_lean_code": bool(reasoning.final_proof) and not contains_lean_code(reasoning.final_proof),
                "within_budget": reasoning.stop_reason == "generator_only",
                "strategy_concepts": all(concept_checks(reasoning.final_proof, case.get("concepts", []))),
            }
            requests = list(reasoning.requests)
        else:
            # Only the theorem and candidate proof enter the request; never the
            # expected verdict, scoring regexes, case id, or other model history.
            review = reasoner.verify(packet, case["proof"], requests=requests)
            result["initial_review"] = asdict(review)
            if case["kind"] == "verifier":
                expected = case["expected"]
                correct = (review.verdict == InformalVerdict.ACCEPT if expected == "accept"
                           else review.verdict in {InformalVerdict.REVISE, InformalVerdict.REJECT})
                result["checks"] = {
                    "expected_verdict": correct,
                    "structured_review": review.verdict != InformalVerdict.MALFORMED,
                    "critique_concepts": all(concept_checks(
                        "\n".join([review.critique, *review.issues, review.suggested_fix or ""]),
                        case.get("concepts", []))),
                }
            else:
                # The true target remains unchanged. This is an injected bad
                # draft, not evidence that the generator made this mistake.
                result["injected_bad_draft"] = case["proof"]
                reasoning = reasoner.reason(replace(packet, rejected_strategies=(
                    "Injected rejected mathematical outline:\n" + case["proof"]
                    + "\nIndependent critique:\n" + review.critique + "\n"
                    + "\n".join(review.issues) + "\n" + (review.suggested_fix or ""))))
                requests.extend(reasoning.requests)
                result["reasoning"] = asdict(reasoning)
                result["checks"] = {
                    "bad_draft_rejected": review.verdict == InformalVerdict.REVISE,
                    "revised_plan_accepted": reasoning.accepted,
                    "revision_concepts": all(concept_checks(reasoning.final_proof, case.get("concepts", []))),
                }
        result["requests"] = [asdict(r) for r in requests]
        result["checks"]["fresh_contexts"] = (
            len({r.conversation_id for r in requests}) == len(requests)
            and all(r.history_messages == 0 and len(r.messages) == 2 for r in requests))
        result["checks"]["context_limits"] = all(
            r.estimated_context_tokens + r.max_output_tokens <= reasoner.config.max_context_tokens
            for r in requests)
        result["passed"] = all(result["checks"].values())
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["requests"] = [asdict(r) for r in requests]
    return result
