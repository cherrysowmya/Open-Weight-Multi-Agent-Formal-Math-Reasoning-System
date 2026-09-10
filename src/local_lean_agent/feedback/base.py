from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import LeanFeedback


class LeanFeedbackProvider(ABC):
    @abstractmethod
    def inspect(
        self,
        code: str,
        *,
        compiler_diagnostics: tuple[str, ...],
        attempt_id: str,
    ) -> LeanFeedback:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        return None
