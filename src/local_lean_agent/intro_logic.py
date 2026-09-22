"""Pinned, statement-only adaptation of Trequetrum's A Lean Intro to Logic.

This is an unrestricted Lean/Mathlib evaluation, not the game's tactic inventory.
No game solutions, hints, examples or GameLogic proof helpers enter agent inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
from urllib.request import urlopen
import zipfile

from .minif2f import HEARTBEATS, sha, statement_probe as _statement_probe
from .proof_body import masked_source

REPOSITORY = "https://github.com/Trequetrum/lean4game-logic"
REVISION = "40ceec5f3ca5dce6cec2800b8f5e4927631ca2da"
ARCHIVE_SHA256 = "47560902fbef2ccc358608eb3b565c529494434395988f373f3e5a3d5aa15b56"
ARCHIVE = f"https://codeload.github.com/Trequetrum/lean4game-logic/zip/{REVISION}"
DEFAULT_DATA = Path("benchmarks/intro-logic/data")
WORLDS = {f"{topic}{mode}": count for topic, count in
          (("And", 8), ("Imp", 9), ("Not", 12), ("Or", 8), ("Iff", 7))
          for mode in ("Intro", "Tactic")}


@dataclass(frozen=True)
class IntroLogicCase:
    case_id: str
    split: str
    source: str
    sha256: str
    upstream_sha256: str
    world: str
    level: int


def statement_probe(case: IntroLogicCase) -> str:
    probe = _statement_probe(case)
    if case.case_id == "intro_logic_OrIntro_L02":
        # Lean auto-binds K in a theorem TYPE, not in a definition BODY.
        # Only the Prop-valued elaboration probe needs its implicit binder made
        # explicit. The task sent to the agent remains byte-for-byte unchanged.
        name = "def " + case.case_id + "_statement"
        probe = probe.replace(name, name + " {K : Prop}", 1)
    return probe


def normalize_source(raw: str, case_id: str) -> str:
    """Extract only the Statement header outside strings/comments; fail closed."""
    masked = masked_source(raw)
    statements = list(re.finditer(r"(?m)^Statement\b", masked))
    if len(statements) != 1:
        raise ValueError("Expected exactly one game Statement")
    start = statements[0].end()
    # Stop at this Statement's assignment; later examples are reference answers.
    assignment, depth = None, 0
    for index in range(start, len(masked) - 1):
        char = masked[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif masked[index:index + 2] == ":=" and depth == 0:
            assignment = index
            break
    if assignment is None:
        raise ValueError("Game Statement has no unambiguous proof assignment")
    header = ("theorem " + case_id + raw[start:assignment]).rstrip()
    if not re.fullmatch(r"intro_logic_[A-Za-z]+_L\d{2}", case_id):
        raise ValueError("Invalid logic case identifier")
    # The pinned corpus consists only of propositional statements. Reject
    # unknown helpers/commands rather than importing upstream answer material.
    remainder = masked_source(header[len("theorem " + case_id):])
    if re.search(r"\b(?:sorry|admit|axiom|def|theorem|lemma|example|unsafe|GameLogic)\b", remainder):
        raise ValueError("Unexpected proof or helper material in Statement header")
    return (f"import Mathlib\n\nset_option maxHeartbeats {HEARTBEATS}\n\n"
            + header + " := by\n  sorry\n")


def prepare_dataset(root: Path = DEFAULT_DATA, archive_bytes: bytes | None = None) -> dict:
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        load_dataset(root, "all")
        return json.loads(manifest_path.read_text())
    if root.exists() and any(root.iterdir()):
        raise ValueError("Use an empty dataset directory or an existing verified manifest")
    if archive_bytes is None:
        with urlopen(ARCHIVE, timeout=60) as response:
            archive_bytes = response.read(5_000_001)
    if len(archive_bytes) > 5_000_000 or sha(archive_bytes) != ARCHIVE_SHA256:
        raise ValueError("Logic-game archive differs from the pinned SHA256")
    prefix = f"lean4game-logic-{REVISION}/"
    files, records = {}, []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for info in archive.infolist():
            relative = info.filename.removeprefix(prefix)
            match = re.fullmatch(r"Game/Levels/([A-Za-z]+)/L(\d{2})\.lean", relative)
            if not info.filename.startswith(prefix) or not match:
                continue
            world, level = match[1], int(match[2])
            if world not in WORLDS or not 1 <= level <= WORLDS[world] or info.file_size > 200_000:
                raise ValueError("Unexpected upstream level")
            raw = archive.read(info).decode("utf-8")
            case_id = f"intro_logic_{world}_L{level:02}"
            source = normalize_source(raw, case_id)
            path = f"{world}/L{level:02}.lean"
            if path in files:
                raise ValueError("Duplicate upstream level")
            files[path] = source
            records.append({"id": case_id, "world": world, "level": level, "path": path,
                            "sha256": sha(source), "upstream_sha256": sha(raw),
                            "upstream_path": relative})
        for name in ("LICENSE", "lean-toolchain", "lake-manifest.json"):
            files[name] = archive.read(prefix + name).decode("utf-8")
    if len(records) != sum(WORLDS.values()):
        raise ValueError("Expected all 88 game levels")
    manifest = {"dataset": "lean-intro-to-logic", "repository": REPOSITORY,
        "revision": REVISION, "archive_sha256": ARCHIVE_SHA256,
        "upstream_toolchain": files["lean-toolchain"].strip(),
        "adaptation": {"statement_changes": False, "theorem_names_assigned": True,
                       "imports": ["Mathlib"], "maxHeartbeats": HEARTBEATS,
                       "game_tactic_restrictions_enforced": False,
                       "solutions_and_hints_included": False,
                       "autoImplicit": "Lean default; upstream unbound K in OrIntro/L02 preserved"},
        "cases": sorted(records, key=lambda r: (list(WORLDS).index(r["world"]), r["level"]))}
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def load_dataset(root: Path, split: str = "all") -> list[IntroLogicCase]:
    if split not in {"all", "intro", "tactic"} and split not in WORLDS:
        raise ValueError("Unknown logic world or partition")
    manifest = json.loads((root / "manifest.json").read_text())
    if (manifest.get("repository"), manifest.get("revision"), manifest.get("archive_sha256")) != (
            REPOSITORY, REVISION, ARCHIVE_SHA256):
        raise ValueError("Logic dataset provenance mismatch")
    seen, cases = set(), []
    for entry in manifest["cases"]:
        world, level = entry["world"], entry["level"]
        if world not in WORLDS or type(level) is not int or not 1 <= level <= WORLDS[world]:
            raise ValueError("Invalid level metadata")
        expected = f"{world}/L{level:02}.lean"
        if entry["path"] != expected or entry["id"] != f"intro_logic_{world}_L{level:02}":
            raise ValueError("Invalid dataset path or ID")
        if expected in seen:
            raise ValueError("Duplicate logic level")
        seen.add(expected)
        source = (root / expected).read_text(encoding="utf-8")
        if sha(source) != entry["sha256"]:
            raise ValueError(f"Dataset file changed: {expected}")
        if split == "all" or split == world or world.lower().endswith(split):
            cases.append(IntroLogicCase(entry["id"], split, source, entry["sha256"],
                                       entry["upstream_sha256"], world, level))
    if len(seen) != sum(WORLDS.values()):
        raise ValueError("Logic dataset must contain all 88 levels")
    return cases
