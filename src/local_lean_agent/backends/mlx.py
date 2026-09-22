from __future__ import annotations

from collections.abc import Sequence
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlparse

from ..config import MLXConfig
from ..http import HTTPClientError, request_json
from ..types import ChatMessage, GenerationResult, TokenUsage
from .base import ModelBackend


class MLXBackend(ModelBackend):
    """OpenAI-compatible adapter for ``mlx_lm.server``.

    In managed mode this class owns the server process. Consequently,
    ``unload_model`` terminates the process and actually releases unified memory.
    """

    def __init__(self, config: MLXConfig):
        self.config = config
        self._model_id: str | None = None
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def supports_model_switching(self) -> bool:
        return self.config.managed_server

    @property
    def model_id(self) -> str | None:
        return self._model_id

    @property
    def process_id(self) -> int | None:
        return None if self._process is None else self._process.pid

    def load_model(self, model_id: str) -> float:
        if self._model_id == model_id and self.health_check():
            return 0.0
        started = time.monotonic()
        if self.config.managed_server:
            self.unload_model()
            if self.health_check():
                raise RuntimeError(
                    f"An MLX server is already using {self.config.base_url}. "
                    "Set managed_server=false to use that external process."
                )
            parsed = urlparse(self.config.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8080
            command = [
                sys.executable,
                "-m",
                self.config.server_module,
                "--model",
                model_id,
                "--host",
                host,
                "--port",
                str(port),
                *self.config.extra_server_args,
            ]
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = started + self.config.startup_timeout_seconds
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise RuntimeError(
                        "mlx_lm.server exited during startup; run the server command "
                        "manually to inspect its error output"
                    )
                if self.health_check():
                    break
                time.sleep(self.config.poll_interval_seconds)
            else:
                self.unload_model()
                raise TimeoutError("Timed out waiting for mlx_lm.server to become healthy")
        elif not self.health_check():
            raise RuntimeError(
                f"No MLX-LM server is reachable at {self.config.base_url}; "
                "start it or enable managed_server"
            )
        self._model_id = model_id
        return time.monotonic() - started

    def unload_model(self) -> float:
        started = time.monotonic()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        # Retain ownership if termination fails: never permit another model to
        # load while the previous process might still own unified memory.
        self._process = None
        self._model_id = None
        return time.monotonic() - started

    def health_check(self) -> bool:
        try:
            request_json(
                "GET",
                f"{self.config.base_url.rstrip('/')}/models",
                timeout=min(5.0, self.config.request_timeout_seconds),
            )
            return True
        except HTTPClientError:
            return False

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        extra: dict[str, Any] | None = None,
    ) -> GenerationResult:
        if self._model_id is None:
            raise RuntimeError("load_model must be called before chat")
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [message.as_api_dict() for message in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": False,
        }
        if extra:
            payload.update(extra)
        started = time.monotonic()
        data = request_json(
            "POST",
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            payload=payload,
            timeout=self.config.request_timeout_seconds,
        )
        latency = time.monotonic() - started
        try:
            choice = data["choices"][0]
            message = choice["message"]
            # mlx-lm may return only a `reasoning` field when a thinking-enabled
            # request exhausts its token budget before reaching final content.
            # That is a valid, empty completion for application purposes, not a
            # malformed HTTP response. Hidden reasoning is deliberately not
            # promoted to answer text or passed between isolated roles.
            text = message.get("content", "") if isinstance(message, dict) else message
            if text is None:
                text = ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("MLX-LM returned an unexpected chat response") from exc
        usage_data = data.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
            completion_tokens=int(usage_data.get("completion_tokens", 0)),
            total_tokens=int(usage_data.get("total_tokens", 0)),
        )
        return GenerationResult(
            text=str(text),
            model=str(data.get("model", self._model_id)),
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            latency_seconds=latency,
        )

    def __enter__(self) -> "MLXBackend":
        return self

    def __exit__(self, *_: object) -> None:
        self.unload_model()
