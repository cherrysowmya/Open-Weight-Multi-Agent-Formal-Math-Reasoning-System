from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import InformalReasoningResult, InformalTaskPacket


class InformalReasoner(ABC):
    """Provider-neutral boundary for a fresh-context informal proof loop."""

    @abstractmethod
    def reason(self, packet: InformalTaskPacket) -> InformalReasoningResult:
        """Generate and independently critique a mathematical proof outline."""
