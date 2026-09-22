from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ..types import ChatMessage, GenerationResult


class ModelBackend(ABC):
    """Provider-neutral boundary used by all agent logic."""

    @property
    def supports_model_switching(self) -> bool:
        """True only if unload_model synchronously releases this backend's model."""
        return False

    @abstractmethod
    def load_model(self, model_id: str) -> float:
        """Make a model ready and return load time in seconds."""

    @abstractmethod
    def unload_model(self) -> float:
        """Release the loaded model and return unload time in seconds."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return whether the inference service is reachable."""

    @abstractmethod
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        extra: dict[str, Any] | None = None,
    ) -> GenerationResult:
        """Generate the next assistant message."""

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float = 0.0,
        top_p: float = 1.0,
        extra: dict[str, Any] | None = None,
    ) -> GenerationResult:
        return self.chat(
            [ChatMessage(role="user", content=prompt)],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            extra=extra,
        )
