from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import VerificationResult


class LeanVerifier(ABC):
    @abstractmethod
    def health_check(self) -> bool:
        pass

    @abstractmethod
    def verify(self, code: str, *, attempt_id: str) -> VerificationResult:
        pass

