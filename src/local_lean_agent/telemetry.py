from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import threading
from typing import Any


class JSONLTelemetry:
    def __init__(self, path: str | Path, observer=None):
        self.path = Path(path)
        self.observer = observer
        self._lock = threading.Lock()

    def emit(self, event: str, attempt_id: str, payload: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "attempt_id": attempt_id,
            "payload": _serializable(payload),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if self.observer is not None:
            try:
                self.observer(record)
            except Exception:
                # Presentation failures must not change proof verification.
                pass


def _serializable(value: Any) -> Any:
    if is_dataclass(value):
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return _serializable(to_dict())
        return _serializable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    return value
