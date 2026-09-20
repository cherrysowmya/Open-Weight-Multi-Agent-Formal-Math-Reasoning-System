from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import InformalReasoningResult, InformalTaskPacket


class InformalReasoner(ABC):
    """Provider-neutral boundary for a fresh-context informal proof loop."""

    @abstractmethod
    def reason(self, packet: InformalTaskPacket) -> InformalReasoningResult:
        """Generate and independently critique a mathematical proof outline."""

    def reason_with_events(self, packet: InformalTaskPacket, on_event) -> InformalReasoningResult:
        """Optional progress hook for the sequential reasoning loop."""
        previous = getattr(self, "_on_event", None)
        self._on_event = on_event
        try:
            return self.reason(packet)
        finally:
            self._on_event = previous

    def _emit_event(self, event: str, payload) -> None:
        callback = getattr(self, "_on_event", None)
        if callback is not None:
            callback(event, payload)
