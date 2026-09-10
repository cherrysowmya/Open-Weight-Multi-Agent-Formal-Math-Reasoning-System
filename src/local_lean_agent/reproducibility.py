from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
from typing import Any
from uuid import uuid4

from . import __version__
from .config import AppConfig
from .prompts import PROMPT_VERSION
from .telemetry import _serializable


def run_metadata(config: AppConfig, manifest: Path) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": uuid4().hex,
        "created_at": created_at,
        "agent_version": __version__,
        "prompt_version": PROMPT_VERSION,
        "model_id": config.mlx.model_id,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "config_fingerprint": _hash_json(asdict(config)),
        "manifest_fingerprint": _hash_bytes(manifest.read_bytes()),
        "code_fingerprint": package_code_fingerprint(),
    }


def package_code_fingerprint() -> str:
    package_root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(str(path.relative_to(package_root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def immutable_artifact_path(
    output: Path, *, created_at: str, run_id: str, label: str
) -> Path:
    parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    timestamp = parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    history = output.parent / "history"
    return history / f"{output.stem}-{timestamp}-{label}-{run_id[:8]}.json"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_serializable(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        _serializable(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _hash_bytes(encoded)


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
