from __future__ import annotations

import re
import time
from typing import Any

from ..config import KiminaConfig
from ..http import HTTPClientError, request_json
from ..types import FailureCategory, VerificationResult
from .base import LeanVerifier


_FORBIDDEN = re.compile(r"\b(?:sorry|admit|axiom|constant)\b")


class KiminaVerifier(LeanVerifier):
    def __init__(self, config: KiminaConfig):
        self.config = config

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key:
            return {}
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def health_check(self) -> bool:
        try:
            request_json(
                "GET",
                f"{self.config.base_url.rstrip('/')}{self.config.health_endpoint}",
                headers=self._headers(),
                timeout=min(5.0, self.config.request_timeout_seconds),
            )
            return True
        except HTTPClientError:
            return False

    def verify(self, code: str, *, attempt_id: str) -> VerificationResult:
        forbidden = _FORBIDDEN.search(_strip_comments(code))
        if forbidden:
            return VerificationResult(
                valid=False,
                diagnostics=(f"Forbidden proof placeholder: {forbidden.group(0).strip()}",),
                failure_category=FailureCategory.UNSAFE_PLACEHOLDER,
            )

        endpoint = self.config.endpoint
        if endpoint == "/verify":
            payload = {
                "codes": [{"custom_id": attempt_id, "proof": code}],
                "timeout": self.config.lean_timeout_seconds,
                "infotree_type": "original",
            }
        else:
            payload = {
                "snippets": [{"id": attempt_id, "code": code}],
                "timeout": self.config.lean_timeout_seconds,
                "debug": True,
                "reuse": self.config.reuse_repl,
                "infotree": "original",
            }
        started = time.monotonic()
        try:
            data = request_json(
                "POST",
                f"{self.config.base_url.rstrip('/')}{endpoint}",
                payload=payload,
                headers=self._headers(),
                timeout=self.config.request_timeout_seconds,
            )
        except HTTPClientError as exc:
            return VerificationResult(
                valid=False,
                diagnostics=(str(exc),),
                failure_category=FailureCategory.VERIFIER_UNAVAILABLE,
                elapsed_seconds=time.monotonic() - started,
            )
        return _parse_response(data, time.monotonic() - started)


def _parse_response(data: dict[str, Any], elapsed: float) -> VerificationResult:
    results = data.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return VerificationResult(
            valid=False,
            diagnostics=("Kimina returned no verification result",),
            failure_category=FailureCategory.UNKNOWN,
            elapsed_seconds=elapsed,
            raw=data,
        )
    item = results[0]
    server_error = item.get("error")
    if server_error:
        message = str(server_error)
        category = (
            FailureCategory.VERIFIER_TIMEOUT
            if "timed out" in message.lower() or "timeout" in message.lower()
            else FailureCategory.VERIFIER_UNAVAILABLE
        )
        return VerificationResult(False, (message,), category, elapsed, data)
    response = item.get("response")
    if not isinstance(response, dict):
        return VerificationResult(
            False,
            ("Kimina returned neither a response nor an error",),
            FailureCategory.UNKNOWN,
            elapsed,
            data,
        )
    if response.get("message"):
        message = str(response["message"])
        return VerificationResult(False, (message,), _classify(message), elapsed, data)

    messages = response.get("messages") or []
    diagnostics = tuple(_format_message(message) for message in messages if isinstance(message, dict))
    errors = [message for message in messages if isinstance(message, dict) and message.get("severity") == "error"]
    sorries = response.get("sorries") or []
    if errors:
        combined = "\n".join(diagnostics)
        return VerificationResult(False, diagnostics, _classify(combined), elapsed, data)
    if sorries:
        return VerificationResult(
            False,
            diagnostics + ("Lean reported an unresolved sorry",),
            FailureCategory.UNSAFE_PLACEHOLDER,
            elapsed,
            data,
        )
    return VerificationResult(True, diagnostics, FailureCategory.NONE, elapsed, data)


def _format_message(message: dict[str, Any]) -> str:
    position = message.get("pos") or {}
    line = position.get("line")
    column = position.get("column")
    location = f"{line}:{column}: " if line is not None and column is not None else ""
    severity = message.get("severity", "message")
    return f"{location}{severity}: {message.get('data', '')}".strip()


def _classify(message: str) -> FailureCategory:
    lowered = message.lower()
    if (
        "unexpected token" in lowered
        or "unexpected identifier" in lowered
        or "expected command" in lowered
        or "parser" in lowered
        or "invalid syntax" in lowered
    ):
        return FailureCategory.LEAN_SYNTAX
    if "unknown identifier" in lowered or "unknown constant" in lowered:
        return FailureCategory.UNKNOWN_IDENTIFIER
    if "unsolved goals" in lowered or "no goals to be solved" in lowered:
        return FailureCategory.UNSOLVED_GOALS
    if "timed out" in lowered or "timeout" in lowered:
        return FailureCategory.VERIFIER_TIMEOUT
    if (
        "tactic" in lowered
        or "failed" in lowered
        or "could not prove" in lowered
        or "type mismatch" in lowered
        or "typeclass instance problem" in lowered
        or "invalid rewrite argument" in lowered
    ):
        return FailureCategory.TACTIC_FAILURE
    return FailureCategory.UNKNOWN


def _strip_comments(code: str) -> str:
    without_blocks = re.sub(r"/-.*?-/", "", code, flags=re.DOTALL)
    return re.sub(r"--.*$", "", without_blocks, flags=re.MULTILINE)
