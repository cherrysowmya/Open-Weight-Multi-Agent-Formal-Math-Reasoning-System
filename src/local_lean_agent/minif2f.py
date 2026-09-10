"""Pinned MiniF2F data and proof-independent statement elaboration."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.request import urlopen
import zipfile

REPOSITORY = "https://github.com/yangky11/miniF2F-lean4"
REVISION = "5746b7d6c47855ce1294bed87329618ff7f1bc31"
ARCHIVE = f"https://codeload.github.com/yangky11/miniF2F-lean4/zip/{REVISION}"
DEFAULT_DATA = Path("benchmarks/minif2f/data")
HEARTBEATS = 200000


def sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


@dataclass(frozen=True)
class MiniF2FCase:
    case_id: str
    split: str
    source: str
    sha256: str
    upstream_sha256: str


def normalize_source(raw: str, expected_name: str) -> str:
    # The pinned source format is intentionally narrow. Never copy unknown
    # helper proofs, attributes, solutions or benchmark imports into prompts.
    pattern = (r"\Aimport Mathlib\s+set_option maxHeartbeats 0\s+"
               r"open BigOperators Real Nat Topology Rat\s+"
               r"(theorem " + re.escape(expected_name) + r"\b[\s\S]*?)"
               r"\s*:=\s*by\s+sorry\s*\Z")
    match = re.fullmatch(pattern, raw)
    if not match:
        raise ValueError(f"Unsupported upstream statement format: {expected_name}")
    declaration = match.group(1)
    if re.search(r"\b(sorry|admit|axiom|theorem|lemma|def|unsafe)\b", declaration[len("theorem "):]):
        raise ValueError(f"Unexpected declaration/proof material: {expected_name}")
    return (f"import Mathlib\n\nset_option maxHeartbeats {HEARTBEATS}\n\n"
            "open BigOperators Real Nat Topology Rat\n\n" + declaration + " := by\n  sorry\n")


def prepare_dataset(root: Path = DEFAULT_DATA, archive_bytes: bytes | None = None) -> dict:
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        load_dataset(root, "valid")
        load_dataset(root, "test")
        return json.loads(manifest_path.read_text())
    if root.exists() and any(root.iterdir()):
        raise ValueError("Dataset directory is nonempty without a manifest; use a fresh directory")
    if archive_bytes is None:
        with urlopen(ARCHIVE, timeout=60) as response:
            archive_bytes = response.read(20_000_001)
    if len(archive_bytes) > 20_000_000:
        raise ValueError("Unexpectedly large MiniF2F archive")
    records, files = [], {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        prefix = f"miniF2F-lean4-{REVISION}/"
        for info in archive.infolist():
            if not info.filename.startswith(prefix):
                continue
            relative = info.filename.removeprefix(prefix)
            match = re.fullmatch(r"MiniF2F/(Valid|Test)/([A-Za-z0-9_]+)\.lean", relative)
            if match:
                if info.file_size > 200_000:
                    raise ValueError("Oversized source file")
                split, name = match.group(1).lower(), match.group(2)
                raw = archive.read(info).decode("utf-8")
                source = normalize_source(raw, name)
                path = f"{split}/{name}.lean"
                if path in files:
                    raise ValueError("Duplicate source path in archive")
                files[path] = source
                records.append({"id": name, "split": split, "path": path,
                                "sha256": sha(source), "upstream_sha256": sha(raw)})
        for name in ("LICENSE", "lean-toolchain", "lake-manifest.json"):
            files[name] = archive.read(prefix + name).decode("utf-8")
    for split in ("valid", "test"):
        if sum(r["split"] == split for r in records) != 244:
            raise ValueError(f"Expected 244 {split} problems")
    if len({r["id"] for r in records}) != 488:
        raise ValueError("Duplicate IDs or split overlap")
    manifest = {"dataset": "miniF2F-lean4", "repository": REPOSITORY, "revision": REVISION,
        "archive_sha256": sha(archive_bytes), "upstream_toolchain": files["lean-toolchain"].strip(),
        "adaptation": {"maxHeartbeats": HEARTBEATS, "statement_changes": False},
        "cases": sorted(records, key=lambda r: (r["split"], r["id"]))}
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_dataset(root: Path, split: str) -> list[MiniF2FCase]:
    if split not in {"valid", "test"}:
        raise ValueError("MiniF2F split must be valid or test")
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("revision") != REVISION or manifest.get("repository") != REPOSITORY:
        raise ValueError("Dataset provenance differs from the pinned MiniF2F source")
    cases, seen = [], set()
    for entry in manifest["cases"]:
        if entry["id"] in seen:
            raise ValueError("Duplicate problem ID in manifest")
        seen.add(entry["id"])
        if entry["split"] != split:
            continue
        expected = f"{split}/{entry['id']}.lean"
        if not re.fullmatch(r"[A-Za-z0-9_]+", entry["id"]) or entry["path"] != expected:
            raise ValueError("Invalid dataset path")
        source = (root / expected).read_text()
        if sha(source) != entry["sha256"]:
            raise ValueError(f"Dataset file changed: {expected}")
        cases.append(MiniF2FCase(entry["id"], split, source, entry["sha256"], entry["upstream_sha256"]))
    if len(cases) != 244:
        raise ValueError("MiniF2F split must contain exactly 244 cases")
    return cases


def select_cases(cases: list[MiniF2FCase], *, limit: int = 3, seed: int = 0,
                 ids: tuple[str, ...] = ()) -> list[MiniF2FCase]:
    if limit < 0 or len(ids) != len(set(ids)):
        raise ValueError("Invalid selection limit or duplicate IDs")
    if ids:
        lookup = {c.case_id: c for c in cases}
        if set(ids) - lookup.keys():
            raise ValueError("Requested case is absent from the selected split")
        return [lookup[name] for name in ids]
    ordered = sorted(cases, key=lambda c: sha(f"{seed}:{c.case_id}"))
    return ordered[:limit] if limit else ordered


def statement_probe(case: MiniF2FCase) -> str:
    """Elaborate the target as a Prop-valued definition, without assuming/proving it."""
    prefix, rest = case.source.split("theorem " + case.case_id, 1)
    header, suffix = rest.rsplit(":=", 1)
    if suffix.strip() != "by\n  sorry":
        raise ValueError("Expected a single unproved dataset statement")
    # Preserve offsets while ignoring comments (one upstream declaration has a
    # colon and parentheses inside a line comment between its hypotheses).
    masked = re.sub(r"--[^\n]*", lambda m: " " * len(m.group()), header)
    depth = 0
    for index, char in enumerate(masked):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        elif char == ":" and depth == 0:
            return (prefix + "def " + case.case_id + "_statement" + header[:index]
                    + " : Prop := " + header[index + 1:].strip() + "\n")
    raise ValueError("Cannot locate the theorem result type")
