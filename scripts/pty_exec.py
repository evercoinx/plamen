"""PTY-backed Claude Code execution helpers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import select
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


_DONE_RE = re.compile(r"\bDONE\s*:", re.IGNORECASE)
_RATE_LIMIT_STATUSES = {429, 529}

# Ship 8.10: the subprocess-isolation overlay payload. SINGLE source of
# truth shared by plamen_driver (production) and preflight_pty_transports
# (probe). Empty enabledPlugins/hooks/mcpServers disables those subsystems
# (plugin / hook / MCP cold-start hangs) without touching the user's real
# settings.json -- so OAuth keychain auth keeps working.
SUBPROCESS_ISOLATION_PAYLOAD = '{"enabledPlugins":{},"hooks":{},"mcpServers":{}}'


# Ship 8.13: auto-compaction fingerprint. When Claude Code auto-compacts a
# long PTY coordinator turn, the transcript carries one of these phrases.
# Paired (by the caller) with a DONE-then-gate-miss, this is the verified
# DODO premature-DONE signature. Detection is DIAGNOSTIC ONLY -- it never
# gates a phase.
_COMPACTION_MARKERS = (
    "conversation compacted",
    "compacting conversation",
    "until auto-compact",
    "before compaction",
    "continue the conversation from where it left off",
)


def transcript_shows_compaction(
    transcript_path: str | Path | None, extra_text: str = "",
) -> bool:
    """Return True iff the transcript (and optional extra stdio text)
    carries an auto-compaction fingerprint: an explicit compaction phrase,
    or 'compact' appearing repeatedly (>=3x, the summary-block pattern).

    Conservative: any read error returns False. Never raises.
    """
    blob = ""
    try:
        if transcript_path and Path(transcript_path).exists():
            blob = Path(transcript_path).read_text(
                encoding="utf-8", errors="replace"
            )
    except Exception:
        blob = ""
    blob = (blob + "\n" + (extra_text or "")).lower()
    if any(marker in blob for marker in _COMPACTION_MARKERS):
        return True
    return blob.count("compact") >= 3


def isolation_overlay_hash(payload: str | None) -> str:
    """Short stable hash of the isolation overlay payload. Part of the
    preflight cache key so a probe taken under a different overlay is not
    reused."""
    return hashlib.sha256(
        (payload or "").encode("utf-8")
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Ship 8.10: canonical interactive (PTY) claude argv builder.
#
# SINGLE source of truth for the production PTY argv shape. Both
# plamen_driver.run_phase and preflight_pty_transports' probes call this
# so the probe exercises the SAME flag structure + isolation set as
# production. The historical false-negative class -- the probe omitted the
# subprocess-isolation flags and therefore hit the plugin/MCP cold-start
# hang it was meant to avoid, producing "first file never written" -> a
# bogus live_pty_continue=False -- is eliminated when both paths share this
# builder.
#
# The byte sequence reproduces production's prior inline construction
# EXACTLY (model, session-id, dangerously-skip, the add-dirs, then the
# non-MCP isolation set), so wiring production to this builder is a
# refactor with zero argv drift.
# ---------------------------------------------------------------------------

# Volatile argv tokens whose VALUE differs run-to-run (or probe-vs-prod) but
# whose PRESENCE/ORDER is the "shape". Used by claude_pty_argv_shape to
# normalize so probe(haiku, tmp dirs) and production(sonnet, project dirs)
# hash identically when their flag STRUCTURE matches.
_PTY_SHAPE_VALUE_FLAGS = {
    "--model": "<MODEL>",
    "--session-id": "<SID>",
    "--add-dir": "<DIR>",
    "--settings": "<ISO>",
    "--mcp-config": "<ISO>",
}


def build_claude_pty_argv(
    *,
    claude_bin: str,
    model: str,
    session_id: str,
    add_dirs: list[str] | tuple[str, ...],
    disallow_mcp: bool = True,
    isolation_path: str | Path | None = None,
    dangerously_skip_permissions: bool = True,
) -> list[str]:
    """Build the canonical interactive (PTY) claude argv.

    Order mirrors production exactly:
      ``[bin, --model M, --session-id S, --dangerously-skip-permissions,
         (--add-dir D)*, (--disallowedTools mcp__*
         [, --settings ISO, --strict-mcp-config, --mcp-config ISO])?]``

    - ``disallow_mcp`` gates the whole isolation block (production adds it
      only for ``not phase.needs_mcp`` phases).
    - ``isolation_path`` adds the settings/strict-mcp set; pass ``None`` to
      replicate production's "isolation overlay write failed" fallback,
      which keeps ``--disallowedTools mcp__*`` but drops the settings flags.
    """
    argv: list[str] = [
        claude_bin,
        "--model", model,
        "--session-id", session_id,
    ]
    if dangerously_skip_permissions:
        argv.append("--dangerously-skip-permissions")
    for d in add_dirs:
        argv.extend(["--add-dir", str(d)])
    if disallow_mcp:
        argv.extend(["--disallowedTools", "mcp__*"])
        if isolation_path is not None:
            iso = Path(isolation_path).as_posix()
            argv.extend([
                "--settings", iso,
                "--strict-mcp-config", "--mcp-config", iso,
            ])
    return argv


def append_claude_pty_prompt_arg(argv: list[str], prompt: str) -> list[str]:
    """Return ``argv`` with ``prompt`` appended as Claude Code's normal
    positional prompt argument.

    Claude's help documents ``claude [options] [prompt]``. Do not put a bare
    ``--`` before the prompt here: in interactive PTY mode that can leave the
    text sitting in the prompt box instead of executing it. Also avoid placing
    the prompt immediately after variadic options such as ``--mcp-config`` or
    ``--disallowedTools``; append a harmless boolean flag first so option
    parsing is closed before the positional prompt.
    """
    out = list(argv)
    if "--no-chrome" not in out:
        out.append("--no-chrome")
    out.append(prompt)
    return out


def claude_pty_argv_shape(argv: list[str]) -> list[str]:
    """Return the argv with volatile token VALUES normalized to stable
    placeholders, so two argvs with the same flag STRUCTURE compare equal
    regardless of bin path, model, session-id, add-dir, or isolation paths.
    """
    out: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if i == 0:
            out.append("<CLAUDE_BIN>")
            i += 1
            continue
        placeholder = _PTY_SHAPE_VALUE_FLAGS.get(tok)
        if placeholder is not None and i + 1 < n:
            out.append(tok)
            out.append(placeholder)
            i += 2
            continue
        if tok.startswith(
            "Read and fully execute every instruction in "
        ):
            out.append("<PROMPT>")
        else:
            out.append(tok)
        i += 1
    return out


def claude_pty_shape_hash(argv: list[str]) -> str:
    """Stable short hash of the normalized argv shape. Used as part of the
    preflight cache key so a probe taken under a DIFFERENT argv/isolation
    shape (e.g. an old false-negative cache from before Ship 8.10) is
    ignored and re-probed."""
    shape = claude_pty_argv_shape(argv)
    return hashlib.sha256(
        json.dumps(shape, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def encode_claude_project_dir(cwd: str | Path) -> str:
    """Return Claude Code's project-directory encoding for a working dir."""
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(Path(cwd).resolve()))


def claude_transcript_path(
    session_id: str,
    cwd: str | Path,
    claude_home: str | Path | None = None,
) -> Path:
    home = Path(claude_home) if claude_home else Path.home() / ".claude"
    return home / "projects" / encode_claude_project_dir(cwd) / f"{session_id}.jsonl"


def _event_message(event: dict[str, Any]) -> dict[str, Any]:
    msg = event.get("message")
    return msg if isinstance(msg, dict) else {}


def _event_text(event: dict[str, Any]) -> str:
    msg = _event_message(event)
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def event_is_turn_end(event: dict[str, Any]) -> bool:
    if event.get("type") != "assistant":
        return False
    msg = _event_message(event)
    stop_reason = (msg.get("stop_reason") or "").lower()
    if stop_reason == "end_turn":
        return True
    content = msg.get("content")
    if stop_reason in ("", "stop", "complete", "completed") and isinstance(content, list):
        return any(isinstance(i, dict) and i.get("type") == "text" for i in content)
    return False


def event_has_done(event: dict[str, Any]) -> bool:
    return bool(_DONE_RE.search(_event_text(event)))


def event_is_rate_limited(event: dict[str, Any]) -> bool:
    status = event.get("api_error_status")
    if status in _RATE_LIMIT_STATUSES:
        return True
    for source in (event, _event_message(event), event.get("error")):
        if not isinstance(source, dict):
            continue
        status = source.get("api_error_status") or source.get("status")
        if status in _RATE_LIMIT_STATUSES:
            return True
        err = source.get("error")
        if isinstance(err, dict):
            typ = str(err.get("type") or err.get("code") or "").lower()
            if "rate_limit" in typ or "overloaded" in typ:
                return True
        typ = str(source.get("type") or source.get("code") or "").lower()
        if "rate_limit" in typ or "overloaded" in typ:
            return True
        stop = str(source.get("stop_reason") or "").lower()
        if stop in ("rate_limited", "rate_limit", "overloaded"):
            return True
    text = json.dumps(event, ensure_ascii=True).lower()
    return "rate_limit_error" in text or "overloaded_error" in text


@dataclass
class TurnCompleteState:
    complete: bool
    done_seen: bool = False
    rate_limited: bool = False
    line_count: int = 0
    last_event_time: float | None = None
    last_assistant: dict[str, Any] | None = None


def inspect_transcript(path: Path) -> TurnCompleteState:
    state = TurnCompleteState(complete=False)
    if not path.exists():
        return state
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                state.line_count += 1
                if event_is_rate_limited(event):
                    state.rate_limited = True
                if event.get("type") == "assistant":
                    state.last_assistant = event
                    if event_has_done(event):
                        state.done_seen = True
                    if event_is_turn_end(event):
                        state.complete = True
                        try:
                            state.last_event_time = path.stat().st_mtime
                        except OSError:
                            state.last_event_time = time.time()
    except OSError:
        return state
    return state


def parse_transcript_usage(path: Path) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "num_turns": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "stop_reason": "?",
        "is_error": False,
    }
    if not path.exists():
        return fields
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event_is_rate_limited(event):
                    fields["is_error"] = True
                    fields["api_error_status"] = 429
                    fields["stop_reason"] = "rate_limited"
                if event.get("type") != "assistant":
                    continue
                msg = _event_message(event)
                stop = msg.get("stop_reason")
                if stop:
                    fields["stop_reason"] = stop
                if event_is_turn_end(event):
                    fields["num_turns"] = int(fields.get("num_turns") or 0) + 1
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                ):
                    try:
                        fields[key] = int(fields.get(key) or 0) + int(usage.get(key) or 0)
                    except Exception:
                        pass
    except OSError:
        pass
    if not fields.get("num_turns"):
        fields["num_turns"] = 1
    return fields


_AGENT_ROW_RE = re.compile(r"AGENT_ROW:\s*([^\s\-<>]+)")
_EXPECTED_OUTPUT_RE = re.compile(r"EXPECTED_OUTPUT:\s*([^\s\-<>]+)")
_AGENTID_HANDLE_RE = re.compile(
    r"agentId:\s*([A-Za-z0-9_-]+)\s*\(use SendMessage",
)


def parse_transcript_agentids(transcript_path: Path) -> dict[str, dict[str, str]]:
    """Parse a Claude Code session transcript and correlate each
    Agent/Task subagent dispatch with the ``agentId`` handle that came
    back in its tool_result.

    Ship 5 of the artifact-complete PTY supervision plan. Consumed by
    Ship 6 to build a continuation message that addresses paused
    subagents by their manifest row (e.g. "B3 / analysis_access_control.md"),
    not by the opaque handle string.

    Args:
        transcript_path: ``.jsonl`` transcript file written by Claude
            Code under ``~/.claude/projects/<encoded_cwd>/<session_id>.jsonl``.

    Returns:
        ``{agent_row: {"agent_id": agent_row, "expected_output": str,
        "handle": str, "description": str}}`` mapping ONE row per
        subagent dispatch that carried an ``AGENT_ROW:`` marker in its
        dispatch prompt. Dispatches without an AGENT_ROW marker are
        skipped (we do not fabricate a row name from the handle alone).
        Dispatches that have not yet returned a handle are still
        included with ``handle == ""`` so the continuation message can
        still name the row -- the caller decides whether to attempt
        SendMessage or fall back to respawn based on handle emptiness.

        Returns ``{}`` on every failure mode: missing file, unreadable
        file, JSON parse errors line-by-line (the parser continues
        past corrupt lines), empty transcript, transcript with no
        Agent/Task dispatches, or transcript whose dispatches all
        lacked the AGENT_ROW marker.

    Marker format. The orchestrator's dispatch prompt MUST contain
    a line of the form::

        AGENT_ROW: B3
        EXPECTED_OUTPUT: analysis_access_control.md

    The Subagent Prompt Template in
    ``~/.plamen/prompts/shared/v2/phase3-breadth.md`` injects these
    lines verbatim in every Task/Agent dispatch prompt the breadth
    orchestrator builds, exactly so this parser can correlate.
    """
    try:
        if not transcript_path.exists():
            return {}
    except Exception:
        return {}

    # Per-tool_use_id state. We scan the transcript once, in order,
    # because tool_use and its matching tool_result are typically on
    # different lines. Correlate by ``id`` <-> ``tool_use_id``.
    dispatches: dict[str, dict[str, str]] = {}
    results: dict[str, str] = {}

    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as fp:
            for raw_line in fp:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    # Skip corrupt lines; do not fail the whole parse.
                    continue
                msg = obj.get("message") if isinstance(obj, dict) else None
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type")
                    if itype == "tool_use" and item.get("name") in ("Agent", "Task"):
                        tu_id = item.get("id")
                        if not isinstance(tu_id, str) or not tu_id:
                            continue
                        inp = item.get("input")
                        if not isinstance(inp, dict):
                            continue
                        prompt_text = inp.get("prompt") or ""
                        description = inp.get("description") or ""
                        if not isinstance(prompt_text, str):
                            prompt_text = ""
                        if not isinstance(description, str):
                            description = ""
                        row_m = _AGENT_ROW_RE.search(prompt_text)
                        exp_m = _EXPECTED_OUTPUT_RE.search(prompt_text)
                        dispatches[tu_id] = {
                            "agent_row": (row_m.group(1).strip() if row_m else ""),
                            "expected_output": (
                                exp_m.group(1).strip() if exp_m else ""
                            ),
                            "description": description.strip(),
                        }
                    elif itype == "tool_result":
                        tu_id = item.get("tool_use_id")
                        if not isinstance(tu_id, str) or not tu_id:
                            continue
                        result_content = item.get("content")
                        text = ""
                        if isinstance(result_content, list):
                            for rc in result_content:
                                if (
                                    isinstance(rc, dict)
                                    and rc.get("type") == "text"
                                ):
                                    t = rc.get("text")
                                    if isinstance(t, str):
                                        text += t
                        elif isinstance(result_content, str):
                            text = result_content
                        handle_m = _AGENTID_HANDLE_RE.search(text)
                        if handle_m:
                            results[tu_id] = handle_m.group(1).strip()
    except OSError:
        return {}
    except Exception:
        # Any unforeseen parse failure -> empty mapping (conservative).
        return {}

    out: dict[str, dict[str, str]] = {}
    for tu_id, d in dispatches.items():
        agent_row = d.get("agent_row") or ""
        if not agent_row:
            # The plan's contract: do NOT fabricate a row mapping for
            # dispatches that lacked the AGENT_ROW marker. Such dispatches
            # cannot be correlated to a manifest row and so cannot be
            # named precisely in a continuation message; the caller must
            # respawn that row from scratch.
            continue
        out[agent_row] = {
            "agent_id": agent_row,
            "expected_output": d.get("expected_output") or "",
            "handle": results.get(tu_id, ""),
            "description": d.get("description") or "",
        }
    return out


class ClaudePtySession:
    def __init__(
        self,
        argv: list[str],
        cwd: str | Path,
        env: dict[str, str],
        session_id: str,
        prompt_path: str | Path,
        log_file: TextIO,
        claude_home: str | Path | None = None,
    ) -> None:
        self.argv = argv
        self.cwd = str(cwd)
        self.env = env
        self.session_id = session_id
        self.prompt_path = Path(prompt_path)
        self.transcript_path = claude_transcript_path(session_id, cwd, claude_home)
        self.log_file = log_file
        self.proc: Any = None
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None

    def spawn(self) -> None:
        if sys.platform == "win32":
            import winpty  # type: ignore

            self.proc = winpty.PtyProcess.spawn(
                self.argv,
                cwd=self.cwd,
                env=self.env,
                dimensions=(40, 120),
            )
        else:
            import pty

            child_pid, master_fd = pty.fork()
            if child_pid == 0:
                os.chdir(self.cwd)
                os.execvpe(self.argv[0], self.argv, self.env)
            self._child_pid = child_pid
            self._master_fd = master_fd
        self._start_reader()

    def send_bootstrap(self) -> None:
        if self.env.get("PLAMEN_BOOTSTRAP_IN_ARGV") == "1":
            return
        prompt = (
            "Read and fully execute every instruction in "
            f"{self.prompt_path.as_posix()}. When done, output your one-line "
            "DONE summary."
        )
        if sys.platform == "win32":
            self.write(prompt)
            time.sleep(0.75)
            try:
                self.proc.sendcontrol("m")
            except Exception:
                self.write("\r\n")
        else:
            self.write(prompt + "\n")

    def send_continuation(self, message: str) -> None:
        """Send a continuation user-message into the live PTY.

        Ship 5 of the artifact-complete PTY supervision plan. Mirrors
        ``send_bootstrap``'s platform-conditional CR/CRLF handling
        exactly: on Windows we write the prompt body, idle 0.75s for
        the prompt box to settle, then send a Carriage Return via
        ``sendcontrol("m")`` (with a ``\\r\\n`` fallback if the winpty
        control method is unavailable); on POSIX we append ``\\n`` to
        the body and write it in one shot.

        Caller MUST verify ``self.is_alive()`` before invoking. Ship 6
        wraps this call in the supervised PTY loop so a dead PTY
        triggers the ``--resume`` fallback instead.

        No new lifecycle, no new threads, no reader-stop handling --
        the existing reader thread captures Claude's response on the
        same channel it captured the bootstrap turn.
        """
        if sys.platform == "win32":
            self.write(message)
            time.sleep(0.75)
            try:
                self.proc.sendcontrol("m")
            except Exception:
                self.write("\r\n")
        else:
            self.write(message + "\n")

    def write(self, text: str) -> None:
        if sys.platform == "win32":
            self.proc.write(text)
        else:
            os.write(self._master_fd, text.encode("utf-8", errors="replace"))

    def is_alive(self) -> bool:
        if self.proc is None:
            return False
        if sys.platform == "win32":
            return bool(self.proc.isalive())
        try:
            pid, _status = os.waitpid(self._child_pid, os.WNOHANG)
            return pid == 0
        except ChildProcessError:
            return False

    def terminate(self, grace_s: float = 5.0) -> None:
        self._reader_stop.set()
        try:
            if self.proc is None:
                return
            if sys.platform == "win32":
                try:
                    self.proc.terminate(force=False)
                    deadline = time.time() + grace_s
                    while time.time() < deadline and self.proc.isalive():
                        time.sleep(0.1)
                    if self.proc.isalive():
                        self.proc.kill()
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
            else:
                try:
                    os.killpg(os.getpgid(self._child_pid), signal.SIGTERM)
                except Exception:
                    try:
                        os.kill(self._child_pid, signal.SIGTERM)
                    except Exception:
                        pass
                deadline = time.time() + grace_s
                while time.time() < deadline and self.is_alive():
                    time.sleep(0.1)
                if self.is_alive():
                    try:
                        os.killpg(os.getpgid(self._child_pid), signal.SIGKILL)
                    except Exception:
                        pass
        finally:
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=1.0)

    def wait_for_turn_complete(
        self,
        timeout_s: float,
        quiescence_s: float = 8.0,
        poll_s: float = 0.1,
        transcript_poll_s: float = 0.5,
        on_poll: Any = None,
    ) -> TurnCompleteState:
        deadline = time.time() + timeout_s
        state = TurnCompleteState(complete=False)
        last_transcript_poll = 0.0
        while True:
            now = time.time()
            if now - last_transcript_poll >= transcript_poll_s:
                state = inspect_transcript(self.transcript_path)
                last_transcript_poll = now
            if on_poll:
                on_poll(now, state)
            if state.rate_limited:
                return state
            if state.complete and state.last_event_time is not None:
                if now - state.last_event_time >= quiescence_s:
                    return state
            if not self.is_alive():
                return state
            if now >= deadline:
                return state
            time.sleep(poll_s)

    def _start_reader(self) -> None:
        def _reader() -> None:
            while not self._reader_stop.is_set():
                try:
                    if sys.platform == "win32":
                        if not self.proc or not self.proc.isalive():
                            break
                        chunk = self.proc.read(4096)
                    else:
                        readable, _, _ = select.select([self._master_fd], [], [], 0.25)
                        if not readable:
                            if not self.is_alive():
                                break
                            continue
                        data = os.read(self._master_fd, 4096)
                        if not data:
                            break
                        chunk = data.decode("utf-8", errors="replace")
                    if chunk:
                        self.log_file.write(chunk)
                        self.log_file.flush()
                except Exception:
                    time.sleep(0.1)

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()
