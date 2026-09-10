from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import RetrievalResult


class SemanticRetriever(ABC):
    @abstractmethod
    def retrieve(self, query: str) -> RetrievalResult:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass

    def close(self) -> None:
        pass
