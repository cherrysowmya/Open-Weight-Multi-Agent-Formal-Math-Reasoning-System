"""Minimal single-resident-model routing through the provider-neutral backend."""
from __future__ import annotations

from contextlib import contextmanager
import threading
import time

from .backends.base import ModelBackend
from .types import AttemptMetrics


class ModelRouter:
    def __init__(self, backend: ModelBackend, metrics: AttemptMetrics, telemetry,
                 attempt_id: str, sample_memory):
        if not backend.supports_model_switching:
            raise ValueError("V5 requires a backend with synchronous model unloading")
        self.backend = backend
        self.metrics = metrics
        self.telemetry = telemetry
        self.attempt_id = attempt_id
        self.sample_memory = sample_memory
        self.active_model: str | None = None

    def activate(self, model_id: str) -> None:
        if self.active_model == model_id:
            return
        # Strict sequencing: if unload raises, loading the next model never runs.
        self.unload()
        started = time.monotonic()
        self.telemetry.emit("model_loading", self.attempt_id, {"model": model_id})
        # Record ownership before startup so Ctrl-C during a partial load is
        # cleaned up by this router too.
        self.active_model = model_id
        try:
            self.backend.load_model(model_id)
        except BaseException as exc:
            self._transition("load", model_id, started, str(exc))
            # A partially started server still belongs to us and must be reaped.
            self.unload()
            raise
        self._transition("load", model_id, started)
        self.telemetry.emit("model_loaded", self.attempt_id, {"model": model_id})
        self.sample()

    def unload(self) -> None:
        if self.active_model is None:
            return
        model_id = self.active_model
        started = time.monotonic()
        self.sample()
        self.telemetry.emit("model_unloading", self.attempt_id, {"model": model_id})
        try:
            self.backend.unload_model()
        except Exception as exc:
            self._transition("unload", model_id, started, str(exc))
            raise
        self.active_model = None
        self._transition("unload", model_id, started)
        self.telemetry.emit("model_unloaded", self.attempt_id, {"model": model_id})

    def _transition(self, action, model, started, error=None):
        seconds = time.monotonic() - started
        entry = {"action": action, "model": model, "seconds": seconds,
                 "status": "error" if error else "complete", "error": error}
        self.metrics.model_transitions.append(entry)
        if action == "load":
            self.metrics.model_load_seconds += seconds
        else:
            self.metrics.model_unload_seconds += seconds
        self.telemetry.emit("model_transition", self.attempt_id, entry)

    def sample(self):
        try:
            sample = self.sample_memory()
            self.metrics.memory_samples_mb.append(sample)
            rss = sample.get("model_rss_mb")
            if rss is not None and self.active_model:
                peaks = self.metrics.model_peak_sampled_rss_mb
                peaks[self.active_model] = max(peaks.get(self.active_model, 0), rss)
        except Exception:
            # Observability failure must not change proof correctness.
            pass

    def chat(self, *args, **kwargs):
        with self.sampling():
            return self.backend.chat(*args, **kwargs)

    @contextmanager
    def sampling(self):
        """Poll RSS during inference, not just during the lazy server startup.

        Sampled RSS is not exact peak unified-memory/Metal allocation accounting.
        """
        done = threading.Event()
        def poll():
            while not done.wait(1):
                self.sample()
        self.sample()
        worker = threading.Thread(target=poll, daemon=True)
        worker.start()
        try:
            yield
        finally:
            done.set()
            worker.join()
            self.sample()
