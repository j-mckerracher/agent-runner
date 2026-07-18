"""Headless RPC-mode client for the omp (oh-my-pi) CLI.

The built-in ``openai-compat`` runner drives omp through its ``--mode rpc``
protocol: a long-running subprocess that exchanges newline-delimited JSON over
stdio. This module owns that lifecycle — spawning omp, holding stdin open until
a turn finishes, parsing the event stream, and translating omp frames into a
single final answer plus optional live-event callbacks.

The frame contract below was verified against a live omp install (see the
session spikes). Notable points that differ from the published docs:

* ``agent_end`` is the only reliable completion marker. A single prompt may span
  multiple ``turn_start``/``turn_end`` cycles (e.g. a tool turn followed by a
  text turn), and no ``stopReason`` field is emitted.
* Assistant message sub-events are spelled ``toolcall_start`` / ``toolcall_delta``
  / ``toolcall_end`` (one word), and text as ``text_start`` / ``text_delta`` /
  ``text_end``. Tool execution also surfaces as top-level ``tool_execution_start``
  / ``_update`` / ``_end`` frames which carry the clean ``toolName`` / ``args`` /
  ``result`` fields.
* ``extension_ui_request`` is polymorphic. ``setStatus`` / ``setWidget`` are
  fire-and-forget UI pushes that need no response; only genuinely interactive
  methods (``open_url`` and selector/confirm/input prompts) must be answered.
* ``response`` frames acknowledge a submitted command. ``success: true`` is a
  bare accept-ack and is ignored, but ``success: false`` means omp rejected the
  prompt (unknown model, missing provider API key, etc.) and no ``agent_end``
  will follow — the client must fail fast on it rather than wait for the turn
  timeout.
* stdin must stay open until ``agent_end`` — closing it early makes omp ack the
  prompt and exit before running the turn.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# extension_ui_request methods that require an ``extension_ui_response``. Anything
# else (setStatus, setWidget, and any other status/widget push) is fire-and-forget.
INTERACTIVE_UI_METHODS = frozenset(
    {"open_url", "select", "confirm", "input", "prompt", "password"}
)

# Sentinel enqueued by the reader thread when omp's stdout reaches EOF.
_STDOUT_EOF = object()


class OmpRpcError(RuntimeError):
    """omp RPC session failed. Carries the exit code and captured stderr tail."""

    def __init__(self, message: str, *, returncode: Optional[int] = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class OmpToolCall:
    """A single tool invocation observed during a turn."""

    tool_call_id: Optional[str]
    tool_name: Optional[str]
    args: Optional[dict] = None
    intent: Optional[str] = None
    result_text: Optional[str] = None
    is_error: bool = False


@dataclass
class OmpRpcResult:
    """Outcome of a single ``prompt`` turn."""

    text: str
    tool_calls: list[OmpToolCall] = field(default_factory=list)
    turns: int = 0
    messages: Optional[list] = None
    error: Optional[str] = None


# on_event(event_type, payload) — live event callback (e.g. mapped to telemetry).
EventCallback = Callable[[str, dict], None]
# ui_request_handler(frame) -> response value or None. None => don't answer.
UiRequestHandler = Callable[[dict], Optional[object]]


def _extract_text_from_messages(messages: Optional[list]) -> str:
    """Return the last assistant text from an ``agent_end.messages`` array.

    Message content may be a plain string or a list of ``{"type": "text",
    "text": ...}`` parts. Returns an empty string when nothing usable is found.
    """
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "".join(parts)
    return ""


class OmpRpcSession:
    """Manage one omp ``--mode rpc`` subprocess and drive prompts through it."""

    def __init__(
        self,
        *,
        repo: Optional[str] = None,
        model: Optional[str] = None,
        extra_flags: Optional[list[str]] = None,
        no_extensions: bool = True,
        on_event: Optional[EventCallback] = None,
        ui_request_handler: Optional[UiRequestHandler] = None,
        ready_timeout: float = 60.0,
        turn_timeout: float = 1800.0,
        omp_cmd: str = "omp",
    ) -> None:
        self.repo = repo
        self.model = model
        self.extra_flags = list(extra_flags or [])
        self.no_extensions = no_extensions
        self.on_event = on_event
        self.ui_request_handler = ui_request_handler
        self.ready_timeout = ready_timeout
        self.turn_timeout = turn_timeout
        self.omp_cmd = omp_cmd

        self._proc: Optional[subprocess.Popen] = None
        self._stdout_q: "queue.Queue[object]" = queue.Queue()
        self._stderr_buf: list[str] = []
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None
        self._stdin_lock = threading.Lock()
        self._closed = False

    # -- lifecycle -----------------------------------------------------------

    def build_cmd(self) -> list[str]:
        workdir = str(Path(self.repo).expanduser().resolve()) if self.repo else os.getcwd()
        cmd = [
            self.omp_cmd,
            "--mode",
            "rpc",
            "--no-session",
            "--approval-mode",
            "yolo",
            "--cwd",
            workdir,
        ]
        if self.no_extensions:
            cmd.append("--no-extensions")
        if self.model:
            cmd.extend(["--model", self.model])
        if self.extra_flags:
            cmd.extend(self.extra_flags)
        return cmd

    def start(self) -> None:
        cmd = self.build_cmd()
        logger.info("OmpRpcSession.start: cmd=%s", cmd)
        popen_kwargs: dict = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True  # own process group for killpg
        self._proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603

        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()

        self._await_ready()

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except (ValueError, TypeError):
                    logger.debug("OmpRpcSession: non-JSON stdout line: %s", line[:200])
                    continue
                self._stdout_q.put(frame)
        finally:
            self._stdout_q.put(_STDOUT_EOF)

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for line in self._proc.stderr:
                self._stderr_buf.append(line)
                if len(self._stderr_buf) > 500:
                    del self._stderr_buf[:250]
        except Exception:  # noqa: BLE001
            pass

    def _stderr_tail(self, limit: int = 2000) -> str:
        text = "".join(self._stderr_buf)
        return text[-limit:] if len(text) > limit else text

    def _await_ready(self) -> None:
        deadline = time.monotonic() + self.ready_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._fail("omp did not emit a ready frame in time")
            try:
                item = self._stdout_q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    self._fail("omp exited before ready")
                continue
            if item is _STDOUT_EOF:
                self._fail("omp stdout closed before ready")
            frame = item  # type: ignore[assignment]
            if isinstance(frame, dict):
                if frame.get("type") == "ready":
                    logger.info("OmpRpcSession: ready")
                    return
                if frame.get("type") == "extension_ui_request":
                    self._handle_ui_request(frame)

    def _fail(self, message: str) -> None:
        returncode = self._proc.poll() if self._proc is not None else None
        raise OmpRpcError(message, returncode=returncode, stderr=self._stderr_tail())

    # -- messaging -----------------------------------------------------------

    def _send(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise OmpRpcError("omp process is not running")
        payload = json.dumps(obj) + "\n"
        with self._stdin_lock:
            try:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                raise OmpRpcError(
                    f"failed to write to omp stdin: {exc}",
                    returncode=self._proc.poll(),
                    stderr=self._stderr_tail(),
                ) from exc

    def _emit(self, event_type: str, payload: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.debug("OmpRpcSession: on_event callback raised", exc_info=True)

    def _handle_ui_request(self, frame: dict) -> None:
        method = frame.get("method")
        request_id = frame.get("id")
        if method not in INTERACTIVE_UI_METHODS:
            # Fire-and-forget UI push (setStatus/setWidget/etc.) — no response.
            return
        logger.info("OmpRpcSession: interactive extension_ui_request method=%s", method)
        value: Optional[object] = None
        if self.ui_request_handler is not None:
            try:
                value = self.ui_request_handler(frame)
            except Exception:  # noqa: BLE001
                logger.warning("OmpRpcSession: ui_request_handler raised", exc_info=True)
                value = None
        if value is None:
            value = "cancelled"
        if request_id is not None:
            self._send({"type": "extension_ui_response", "id": request_id, "value": value})

    # -- turns ---------------------------------------------------------------

    def run_prompt(self, message: str) -> OmpRpcResult:
        """Send a prompt and consume frames until ``agent_end``."""
        if not message:
            raise ValueError("message must not be empty")
        command_id = uuid.uuid4().hex[:16]
        self._send({"id": command_id, "type": "prompt", "message": message})
        return self._consume_turn(command_id=command_id)

    def steer(self, message: str) -> None:
        """Inject a message into the running turn (no completion wait)."""
        self._send({"id": uuid.uuid4().hex[:16], "type": "steer", "message": message})

    def follow_up(self, message: str) -> None:
        """Queue a message after the current turn (no completion wait)."""
        self._send({"id": uuid.uuid4().hex[:16], "type": "follow_up", "message": message})

    def abort(self) -> None:
        """Abort the active turn."""
        self._send({"id": uuid.uuid4().hex[:16], "type": "abort"})

    def _consume_turn(self, command_id: Optional[str] = None) -> OmpRpcResult:
        text_all: list[str] = []
        last_turn_text: list[str] = []
        tool_calls: list[OmpToolCall] = []
        tool_calls_by_id: dict[str, OmpToolCall] = {}
        turns = 0
        error: Optional[str] = None
        messages: Optional[list] = None

        deadline = time.monotonic() + self.turn_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OmpRpcError(
                    "omp turn timed out before agent_end",
                    returncode=self._proc.poll() if self._proc else None,
                    stderr=self._stderr_tail(),
                )
            try:
                item = self._stdout_q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    raise OmpRpcError(
                        f"omp exited (code={self._proc.returncode}) before agent_end",
                        returncode=self._proc.returncode,
                        stderr=self._stderr_tail(),
                    )
                continue
            if item is _STDOUT_EOF:
                raise OmpRpcError(
                    "omp stdout closed before agent_end",
                    returncode=self._proc.poll() if self._proc else None,
                    stderr=self._stderr_tail(),
                )
            frame = item  # type: ignore[assignment]
            if not isinstance(frame, dict):
                continue
            ftype = frame.get("type")

            if ftype == "turn_start":
                turns += 1
                last_turn_text = []
            elif ftype == "message_update":
                ev = frame.get("assistantMessageEvent") or {}
                sub = ev.get("type")
                if sub == "text_delta":
                    delta = ev.get("delta") or ""
                    if delta:
                        last_turn_text.append(delta)
                        text_all.append(delta)
                        self._emit("omp.text_delta", {"delta": delta})
                elif sub in ("toolcall_start", "toolcall_delta", "toolcall_end"):
                    self._emit("omp.toolcall", {"subtype": sub})
            elif ftype == "tool_execution_start":
                tc = OmpToolCall(
                    tool_call_id=frame.get("toolCallId"),
                    tool_name=frame.get("toolName"),
                    args=frame.get("args") if isinstance(frame.get("args"), dict) else None,
                    intent=frame.get("intent"),
                )
                tool_calls.append(tc)
                if tc.tool_call_id:
                    tool_calls_by_id[tc.tool_call_id] = tc
                self._emit(
                    "omp.tool_start",
                    {"tool_name": tc.tool_name, "tool_call_id": tc.tool_call_id, "intent": tc.intent},
                )
            elif ftype == "tool_execution_end":
                call_id = frame.get("toolCallId")
                tc = tool_calls_by_id.get(call_id) if call_id else None
                if tc is None:
                    tc = OmpToolCall(tool_call_id=call_id, tool_name=frame.get("toolName"))
                    tool_calls.append(tc)
                tc.is_error = bool(frame.get("isError"))
                tc.result_text = _tool_result_text(frame.get("result"))
                self._emit(
                    "omp.tool_end",
                    {"tool_name": tc.tool_name, "tool_call_id": tc.tool_call_id, "is_error": tc.is_error},
                )
            elif ftype == "extension_ui_request":
                self._handle_ui_request(frame)
            elif ftype == "response":
                # Ack/failure for a submitted command. ``success: true`` is just
                # the command-accepted ack and is ignored; a ``success: false``
                # response means omp rejected the prompt outright (e.g. unknown
                # model, missing provider API key) and NO ``agent_end`` will
                # follow — fail fast instead of blocking until ``turn_timeout``.
                if (
                    frame.get("command") == "prompt"
                    and frame.get("success") is False
                    and frame.get("id") in (None, command_id)
                ):
                    reason = frame.get("error") or "omp rejected the prompt command"
                    self._emit("omp.error", {"message": reason})
                    raise OmpRpcError(
                        f"omp prompt command failed: {reason}",
                        returncode=self._proc.poll() if self._proc else None,
                        stderr=self._stderr_tail(),
                    )
            elif ftype == "error":
                error = frame.get("message") or json.dumps(frame)[:500]
                self._emit("omp.error", {"message": error})
            elif ftype == "agent_end":
                messages = frame.get("messages") if isinstance(frame.get("messages"), list) else None
                break
            # All other frames (agent_start, message_start/end, turn_end,
            # available_commands_update, and ``response`` success acks) are
            # intentionally ignored.

        text = _extract_text_from_messages(messages)
        if not text:
            text = "".join(last_turn_text) or "".join(text_all)
        return OmpRpcResult(
            text=text,
            tool_calls=tool_calls,
            turns=turns,
            messages=messages,
            error=error,
        )

    # -- teardown ------------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("OmpRpcSession: omp did not exit after kill")
        finally:
            if self._reader is not None:
                self._reader.join(timeout=2)
            if self._stderr_reader is not None:
                self._stderr_reader.join(timeout=2)

    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "OmpRpcSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _tool_result_text(result: object) -> Optional[str]:
    """Flatten an omp tool ``result`` object into plain text."""
    if result is None:
        return None
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = [
                part.get("text")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            ]
            if parts:
                return "".join(parts)
        text = result.get("text")
        if isinstance(text, str):
            return text
    return None
