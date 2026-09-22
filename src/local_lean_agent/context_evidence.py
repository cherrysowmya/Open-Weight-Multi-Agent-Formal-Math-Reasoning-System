"""Bounded prompt evidence, independent of inference and retrieval backends.

Filtering is a conservative lexical heuristic, not a semantic relevance oracle.
Raw retrieval and verification results remain unchanged in attempt logs.
"""
from __future__ import annotations

import json
import re

from .proof_body import masked_source, target_assignment
from .types import IterationRecord, RetrievalHit


EVIDENCE_VERSION = "context-evidence-v1"
_STOP = set("""the a an and or of for to in on by is are be all any every with
    from this that following prove proof theorem lemma declaration lean current
    goal import mathlib exact have let where if then else implies using given
    number numbers real nat int rat complex prop type true false def protected
    variable variables example non negative positive zero one two
    goals cursor its counterpart""".split())
_ALIASES = {
    "sq": "square", "squared": "square", "squares": "square",
    "nonneg": "nonnegative", "nonpositivity": "nonpositive",
    "nonpos": "nonpositive", "positivity": "nonnegative",
    "abs": "absolute", "add": "sum", "addition": "sum",
    "mul": "product", "multiplication": "product",
    "sub": "difference", "subtract": "difference",
    "eq": "equality", "equal": "equality", "equals": "equality",
    "refl": "equality", "reflexivity": "equality",
    "nil": "empty", "append": "append",
}
_TOPICS = set(_ALIASES.values()) | {"sqrt", "inequality", "divisibility",
    "divisible", "prime", "finite", "continuous", "injective", "surjective"}


def _terms(text: str, *, query: bool = False) -> set[str]:
    if query:
        # Ignore imports, comments, and user-chosen theorem names as relevance cues.
        text = masked_source(text)
        text = re.sub(r"(?m)^\s*(?:import|open)\b[^\n]*", "", text)
        text = re.sub(r"<cursor>[^\n]*", "", text)
        text = re.sub(r"\b(?:theorem|lemma|def)\s+[\w.']+", "", text)
        text = re.sub(r"(?m)^\s*Lean declaration to prove:|Current Lean goal:", "", text)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()
    text = re.sub(r"\bnon[- ]negative\b", "nonnegative", text)
    terms = {_ALIASES.get(t, t) for t in re.findall(r"[a-z]+", text)
             if (len(t) > 2 or t in _ALIASES) and t not in _STOP}
    if re.search(r"\^\s*2\b|²|\bpow_two\b", text):
        terms.add("square")
    if re.search(r"\b0\s*≤", text):
        terms.add("nonnegative")
    if "++" in text:
        terms.add("append")
    return terms


def relevant_hit(hit: RetrievalHit, query: str) -> bool:
    """Drop zero-overlap hits only when the query supplies usable topic cues.

    No domain blacklist, score threshold, quota, extra model, or tool call.
    Ambiguous/tiny queries retain the retriever's results. Type words alone
    (e.g. Real) and single-letter variables are not evidence of relevance.
    """
    focus = _terms(query, query=True)
    if not focus or (len(focus) < 2 and not focus & _TOPICS):
        return True
    evidence = _terms(hit.name + " " + (hit.description or "") + " "
                      + masked_source(hit.source_text or ""))
    return bool(focus & evidence)


def pack_declarations(entries: list[dict], max_chars: int) -> str:
    """Pack whole records, skipping oversized ones so later short hits can fit."""
    if max_chars <= 0:
        return ""
    packed: list[dict] = []

    def render(items: list[dict]) -> str:
        return json.dumps({"format": EVIDENCE_VERSION, "declarations": items},
                          ensure_ascii=False)

    for entry in entries:
        # Descriptions are optional; never slice Lean source or a name/signature.
        compact = {k: v for k, v in entry.items() if k != "description"}
        for variant in (entry, compact):
            if len(render([*packed, variant])) <= max_chars:
                packed.append(variant)
                break
    result = render(packed)
    return result if len(result) <= max_chars else ""


def compact_declarations(text: str, max_chars: int) -> str:
    """Repack structured evidence during V4 compression, never slice it.

    Unrecognized legacy text is omitted when over budget, not partially copied.
    """
    if len(text) <= max_chars:
        return text
    try:
        obj = json.loads(text)
        if obj.get("format") == EVIDENCE_VERSION and isinstance(obj.get("declarations"), list):
            return pack_declarations(obj["declarations"], max_chars)
    except (ValueError, AttributeError, TypeError):
        pass
    return ""


def _without_comments(text: str) -> str:
    """Remove Lean comments, preserving strings and newlines in rejected code."""
    pattern = re.compile(r'"(?:\\.|[^"\\])*"|--[^\n]*|/-', re.S)
    out: list[str] = []
    pos = 0
    while match := pattern.search(text, pos):
        out.append(text[pos:match.start()])
        token = match.group()
        pos = match.end()
        if token.startswith('"'):
            out.append(token)
        elif token == "/-":
            depth = 1
            start = pos
            while depth and pos < len(text):
                if text[pos:pos + 2] == "/-":
                    depth += 1
                    pos += 2
                elif text[pos:pos + 2] == "-/":
                    depth -= 1
                    pos += 2
                else:
                    pos += 1
            out.append(" " + "\n" * text[start:pos].count("\n"))
        else:
            out.append(" ")
    out.append(text[pos:])
    return "".join(out).strip()


def rejected_body(candidate: str) -> str:
    start = target_assignment(candidate)
    body = candidate[start + 2:] if start is not None else candidate
    return _without_comments(body)


def failure_memory(records: list[IterationRecord], max_chars: int,
                   max_attempts: int = 4) -> tuple[str, tuple[int, ...]]:
    """Recent candidate/error pairs; excerpt oversized fields explicitly."""
    selected: list[dict] = []
    ids: list[int] = []
    for record in reversed(records):
        if record.verification.valid:
            continue
        body = rejected_body(record.candidate)
        diagnostic = "\n".join(record.verification.diagnostics) or "Lean rejected this candidate"
        entry = {"attempt": record.iteration, "status": "rejected",
                 "category": record.verification.failure_category.value,
                 "candidate": body[:1200], "diagnostics": diagnostic[:800],
                 "excerpted": len(body) > 1200 or len(diagnostic) > 800}
        render = lambda items: json.dumps(items, ensure_ascii=False)
        if not selected:
            while len(render([entry])) > max_chars and (entry["candidate"] or entry["diagnostics"]):
                key = max(("candidate", "diagnostics"), key=lambda k: len(entry[k]))
                entry[key] = entry[key][:len(entry[key]) // 2]
                entry["excerpted"] = True
        if len(render([entry, *selected])) > max_chars:
            break
        selected.insert(0, entry)
        ids.insert(0, record.iteration)
        if len(selected) == max_attempts:
            break
    return (json.dumps(selected, ensure_ascii=False) if selected else "", tuple(ids))


def compact_failures(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    try:
        entries = json.loads(text)
        if isinstance(entries, list):
            while entries and len(json.dumps(entries, ensure_ascii=False)) > max_chars:
                entries.pop(0)
            return json.dumps(entries, ensure_ascii=False) if entries else ""
    except ValueError:
        pass
    return ""
