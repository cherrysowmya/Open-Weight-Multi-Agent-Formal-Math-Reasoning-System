from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
import threading
import time
from typing import Any

from ..config import LeanLSPConfig
from ..types import LeanFeedback
from .base import LeanFeedbackProvider


_REQUIRED_TOOLS = frozenset(
    {"lean_diagnostic_messages", "lean_goal", "lean_term_goal"}
)
_LOCATION = re.compile(r"(?m)(\d+):(\d+):")
_LSP_RANGE = re.compile(r"l(\d+)c(\d+)-l(\d+)c(\d+)")


class MCPProtocolError(RuntimeError):
    pass


class LeanLSPMCPClient(LeanFeedbackProvider):
    """Persistent stdio MCP client for the local ``lean-lsp-mcp`` server."""

    def __init__(self, config: LeanLSPConfig):
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

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        if not self.config.project_path.is_dir():
            raise RuntimeError(
                f"Lean project path does not exist: {self.config.project_path}"
            )
        environment = os.environ.copy()
        elan_bin = str(Path.home() / ".elan" / "bin")
        current_path = environment.get("PATH", "")
        if elan_bin not in current_path.split(os.pathsep):
            environment["PATH"] = elan_bin + os.pathsep + current_path
        environment["LEAN_PROJECT_PATH"] = str(self.config.project_path.resolve())
        environment["LEAN_LOG_LEVEL"] = "NONE"
        self._process = subprocess.Popen(
            [self.config.command, *self.config.args],
            cwd=self.config.project_path,
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
                        "version": "0.1.0",
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
                raise MCPProtocolError(
                    "Lean-LSP-MCP is missing required tools: " + ", ".join(sorted(missing))
                )
            self.last_error = None
        except Exception:
            self.close()
            raise

    def health_check(self) -> bool:
        try:
            self.start()
            return _REQUIRED_TOOLS <= self._tools
        except (OSError, RuntimeError, MCPProtocolError, TimeoutError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def inspect(
        self,
        code: str,
        *,
        compiler_diagnostics: tuple[str, ...],
        attempt_id: str,
    ) -> LeanFeedback:
        started = time.monotonic()
        calls = 0
        scratch_dir = self.config.project_path / ".local_lean_agent"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", attempt_id)
        path: Path | None = None
        try:
            self.start()
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f"{safe_id}-",
                suffix=".lean",
                dir=scratch_dir,
                delete=False,
            ) as handle:
                handle.write(code)
                path = Path(handle.name).resolve()

            diagnostic_output = self.call_tool(
                "lean_diagnostic_messages", {"file_path": str(path)}
            )
            calls += 1
            lsp_diagnostics = _as_diagnostics(diagnostic_output)
            line, column = _diagnostic_location(compiler_diagnostics)
            lsp_range = _lsp_diagnostic_range(lsp_diagnostics)
            if lsp_range is not None:
                start_line, start_column, end_line, _ = lsp_range
                line, column = start_line, start_column
                if end_line > start_line:
                    line, column = end_line, None
            term_line, term_column = line, column
            goal_state: str | None = None
            prefer_term_goal = _diagnostics_prefer_term_goal(lsp_diagnostics)
            if prefer_term_goal and term_line is not None:
                term_arguments: dict[str, Any] = {
                    "file_path": str(path),
                    "line": term_line,
                }
                if term_column is not None and term_column > 0:
                    term_arguments["column"] = term_column
                term_goal = self.call_tool("lean_term_goal", term_arguments)
                calls += 1
                if not _unhelpful_term_goal(term_goal):
                    goal_state = term_goal
                    line, column = term_line, term_column
            if goal_state is None and line is not None:
                arguments: dict[str, Any] = {"file_path": str(path), "line": line}
                if column is not None and column > 0:
                    arguments["column"] = column
                goal_state = self.call_tool("lean_goal", arguments)
                calls += 1
                if _unhelpful_goal(goal_state):
                    fallback_lines = [
                        candidate_line
                        for candidate_line in (
                            lsp_range[2] if lsp_range is not None else None,
                            _last_nonempty_line(code),
                        )
                        if candidate_line is not None and candidate_line != line
                    ]
                    for fallback_line in fallback_lines:
                        goal_state = self.call_tool(
                            "lean_goal",
                            {"file_path": str(path), "line": fallback_line},
                        )
                        calls += 1
                        line, column = fallback_line, None
                        if not _unhelpful_goal(goal_state):
                            break
                if _unhelpful_goal(goal_state):
                    goal_state = None
            if (
                goal_state is None
                and term_line is not None
                and not prefer_term_goal
            ):
                term_arguments: dict[str, Any] = {
                    "file_path": str(path),
                    "line": term_line,
                }
                if term_column is not None and term_column > 0:
                    term_arguments["column"] = term_column
                term_goal = self.call_tool("lean_term_goal", term_arguments)
                calls += 1
                if not _unhelpful_term_goal(term_goal):
                    goal_state = term_goal
                    line, column = term_line, term_column
            return LeanFeedback(
                available=True,
                diagnostics=tuple(
                    item[: self.config.max_feedback_chars] for item in lsp_diagnostics
                ),
                goal_state=(
                    None
                    if goal_state is None
                    else goal_state[: self.config.max_feedback_chars]
                ),
                line=line,
                column=column,
                tool_calls=calls,
                elapsed_seconds=time.monotonic() - started,
            )
        except (OSError, RuntimeError, MCPProtocolError, TimeoutError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return LeanFeedback(
                available=False,
                tool_calls=calls,
                elapsed_seconds=time.monotonic() - started,
                error_message=self.last_error,
            )
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.start()
        if name not in self._tools:
            raise MCPProtocolError(f"Lean-LSP-MCP tool is unavailable: {name}")
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments}
        )
        if not isinstance(result, dict):
            raise MCPProtocolError(f"Invalid MCP result for {name}")
        content = result.get("content", [])
        texts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        rendered = "\n".join(text for text in texts if text).strip()
        if result.get("isError"):
            raise MCPProtocolError(rendered or f"Lean-LSP-MCP tool failed: {name}")
        return rendered

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
                    raise TimeoutError(f"MCP request timed out: {method}")
                selector = self._selector
                if selector is None or not selector.select(remaining):
                    raise TimeoutError(f"MCP request timed out: {method}")
                assert process.stdout is not None
                line = process.stdout.readline()
                if not line:
                    raise MCPProtocolError(
                        f"Lean-LSP-MCP exited during {method} with code {process.poll()}"
                    )
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise MCPProtocolError(
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
            raise MCPProtocolError("Lean-LSP-MCP process is not running")
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
                    process.wait(timeout=5)
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


def _diagnostic_location(diagnostics: tuple[str, ...]) -> tuple[int | None, int | None]:
    for diagnostic in diagnostics:
        match = _LOCATION.search(diagnostic)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None, None


def _as_diagnostics(output: str) -> tuple[str, ...]:
    if not output:
        return ()
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return (output,)
    if isinstance(parsed, list):
        return tuple(str(item) for item in parsed)
    return (str(parsed),)


def _lsp_diagnostic_range(
    diagnostics: tuple[str, ...],
) -> tuple[int, int, int, int] | None:
    for diagnostic in diagnostics:
        match = _LSP_RANGE.search(diagnostic)
        if match:
            return tuple(int(group) for group in match.groups())  # type: ignore[return-value]
    return None


def _last_nonempty_line(code: str) -> int | None:
    lines = code.splitlines()
    for index in range(len(lines), 0, -1):
        if lines[index - 1].strip():
            return index
    return None


def _unhelpful_goal(goal: str | None) -> bool:
    if not goal:
        return True
    lowered = goal.lower().strip()
    return (
        "not a valid goal position" in lowered
        or "no goals on line" in lowered
        or lowered.endswith("no goals")
    )


def _unhelpful_term_goal(goal: str | None) -> bool:
    if not goal:
        return True
    lowered = goal.lower()
    return "not a valid term goal position" in lowered or "no term goal found" in lowered


def _diagnostics_prefer_term_goal(diagnostics: tuple[str, ...]) -> bool:
    text = "\n".join(diagnostics).lower()
    return any(
        marker in text
        for marker in (
            "unknown identifier",
            "unknown constant",
            "function expected",
            "application type mismatch",
            "type mismatch",
            "invalid field",
            "failed to synthesize",
        )
    )
