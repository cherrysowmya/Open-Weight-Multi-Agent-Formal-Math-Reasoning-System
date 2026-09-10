from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time
from typing import Any

from ..config import LeanExploreConfig
from ..types import RetrievalHit, RetrievalResult
from .base import SemanticRetriever


_REQUIRED_TOOLS = frozenset({"search_summary", "get_source_code"})


class LeanExploreProtocolError(RuntimeError):
    pass


class LeanExploreMCPClient(SemanticRetriever):
    """Persistent stdio client for LeanExplore's fully local MCP backend."""

    def __init__(self, config: LeanExploreConfig):
        self.config = config
        self._process: subprocess.Popen[str] | None = None
        self._selector: selectors.BaseSelector | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._tools: set[str] = set()
        self.last_error: str | None = None

    @property
    def process_id(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def data_version(self) -> str | None:
        if self.config.data_version:
            return self.config.data_version
        for active in (
            self.config.cache_dir / "active_version",
            self.config.cache_dir.parent / "active_version",
        ):
            try:
                value = active.read_text(encoding="utf-8").strip()
                if value:
                    return value
            except OSError:
                continue
        return None

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        environment = os.environ.copy()
        environment["LEAN_EXPLORE_CACHE_DIR"] = str(self.config.cache_dir)
        environment["HF_HOME"] = str(self.config.hf_cache_dir)
        # Runtime is offline after setup/warmup; no Hugging Face HEAD requests.
        environment["HF_HUB_OFFLINE"] = "1"
        environment["TRANSFORMERS_OFFLINE"] = "1"
        environment["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
        environment["LEAN_EXPLORE_EMBEDDING_BATCH_SIZE"] = str(
            self.config.embedding_batch_size
        )
        environment["LEAN_EXPLORE_RERANKER_BATCH_SIZE"] = str(
            self.config.reranker_batch_size
        )
        if self.config.data_version:
            environment["LEAN_EXPLORE_VERSION"] = self.config.data_version
        self._process = subprocess.Popen(
            [self.config.command, *self.config.args],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            start_new_session=True,
        )
        assert self._process.stdout is not None
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._process.stdout, selectors.EVENT_READ)
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "local-lean-agent",
                        "version": "0.2.0",
                    },
                },
            )
            self._notify("notifications/initialized", {})
            listed = self._request("tools/list", {})
            tools = listed.get("tools", []) if isinstance(listed, dict) else []
            self._tools = {
                str(tool.get("name")) for tool in tools if isinstance(tool, dict)
            }
            missing = _REQUIRED_TOOLS - self._tools
            if missing:
                raise LeanExploreProtocolError(
                    "LeanExplore is missing required tools: "
                    + ", ".join(sorted(missing))
                )
            self.last_error = None
        except Exception:
            self.close()
            raise

    def health_check(self) -> bool:
        try:
            self.start()
            return _REQUIRED_TOOLS <= self._tools
        except (
            OSError,
            RuntimeError,
            LeanExploreProtocolError,
            TimeoutError,
        ) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def retrieve(self, query: str) -> RetrievalResult:
        started = time.monotonic()
        calls = 0
        query = query[: self.config.max_query_chars]
        try:
            self.start()
            summary_text = self.call_tool(
                "search_summary",
                {
                    "query": query,
                    "limit": self.config.limit,
                    "rerank_top": self.config.rerank_top,
                    "packages": list(self.config.packages),
                },
            )
            calls += 1
            summary = _json_object(summary_text, "search_summary")
            raw_hits = summary.get("results", [])
            if not isinstance(raw_hits, list):
                raise LeanExploreProtocolError(
                    "LeanExplore search_summary returned invalid results"
                )
            hits: list[RetrievalHit] = []
            for index, raw in enumerate(raw_hits):
                if not isinstance(raw, dict):
                    continue
                declaration_id = int(raw["id"])
                source_text: str | None = None
                if index < self.config.source_limit:
                    source_response = self.call_tool(
                        "get_source_code", {"declaration_id": declaration_id}
                    )
                    calls += 1
                    source = _json_object(source_response, "get_source_code")
                    value = source.get("source_text")
                    source_text = None if value is None else str(value)
                hits.append(
                    RetrievalHit(
                        declaration_id=declaration_id,
                        name=str(raw.get("name", "")),
                        description=(
                            None
                            if raw.get("description") is None
                            else str(raw["description"])
                        ),
                        source_text=source_text,
                    )
                )
            self.last_error = None
            processing = summary.get("processing_time_ms")
            return RetrievalResult(
                available=True,
                query=str(summary.get("query", query)),
                hits=tuple(hits),
                tool_calls=calls,
                elapsed_seconds=time.monotonic() - started,
                backend_processing_ms=(
                    int(processing) if processing is not None else None
                ),
                data_version=self.data_version,
            )
        except (
            OSError,
            RuntimeError,
            KeyError,
            TypeError,
            ValueError,
            LeanExploreProtocolError,
            TimeoutError,
        ) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return RetrievalResult(
                available=False,
                query=query,
                tool_calls=calls,
                elapsed_seconds=time.monotonic() - started,
                data_version=self.data_version,
                error_message=self.last_error,
            )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.start()
        if name not in self._tools:
            raise LeanExploreProtocolError(f"LeanExplore tool is unavailable: {name}")
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        if not isinstance(result, dict):
            raise LeanExploreProtocolError(f"Invalid MCP result for {name}")
        if result.get("isError"):
            raise LeanExploreProtocolError(
                _mcp_text(result) or f"LeanExplore tool failed: {name}"
            )
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            if set(structured) == {"result"}:
                wrapped = structured["result"]
                if isinstance(wrapped, str):
                    return wrapped
                return json.dumps(wrapped, ensure_ascii=False)
            return json.dumps(structured, ensure_ascii=False)
        return _mcp_text(result)

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        with self._lock:
            process = self._require_process()
            request_id = self._next_id
            self._next_id += 1
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            deadline = time.monotonic() + self.config.request_timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"LeanExplore MCP request timed out: {method}")
                selector = self._selector
                if selector is None or not selector.select(remaining):
                    raise TimeoutError(f"LeanExplore MCP request timed out: {method}")
                assert process.stdout is not None
                line = process.stdout.readline()
                if not line:
                    raise LeanExploreProtocolError(
                        f"LeanExplore exited during {method} with code {process.poll()}"
                    )
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise LeanExploreProtocolError(
                        f"MCP {method} failed: {message['error']}"
                    )
                return message.get("result")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._require_process()
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, message: dict[str, Any]) -> None:
        process = self._require_process()
        assert process.stdin is not None
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _require_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None or process.poll() is not None:
            raise LeanExploreProtocolError("LeanExplore MCP process is not running")
        return process

    def close(self) -> None:
        process = self._process
        selector = self._selector
        self._process = None
        self._selector = None
        self._tools.clear()
        if selector is not None:
            selector.close()
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            if process.poll() is None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


def _mcp_text(result: dict[str, Any]) -> str:
    content = result.get("content", [])
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def _json_object(text: str, tool: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LeanExploreProtocolError(
            f"LeanExplore {tool} returned non-JSON output"
        ) from exc
    if not isinstance(parsed, dict):
        raise LeanExploreProtocolError(
            f"LeanExplore {tool} returned a non-object payload"
        )
    return parsed
