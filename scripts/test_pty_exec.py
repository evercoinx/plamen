import io
import inspect
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pty_exec import (  # noqa: E402
    ClaudePtySession,
    encode_claude_project_dir,
    inspect_transcript,
    parse_transcript_usage,
    transcript_shows_compaction,
)
import plamen_driver as D  # noqa: E402


def _write_jsonl(path: Path, *events: dict) -> None:
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in events),
        encoding="utf-8",
    )


def _assistant(stop_reason: str, text: str = "", usage: dict | None = None) -> dict:
    content = [{"type": "text", "text": text}] if text else [
        {"type": "tool_use", "name": "Read", "id": "toolu_1", "input": {}}
    ]
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": content,
            "stop_reason": stop_reason,
            "usage": usage or {},
        },
    }


def test_project_dir_encoding_matches_local_claude_shape():
    encoded = encode_claude_project_dir(r"C:\Users\plmnt\.claude")
    assert encoded.endswith("C--Users-plmnt--claude")


def test_inspect_transcript_does_not_complete_on_mid_tool_loop(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, _assistant("tool_use"))

    state = inspect_transcript(transcript)

    assert state.complete is False
    assert state.line_count == 1


def test_inspect_transcript_completes_on_end_turn_and_records_done(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        _assistant("tool_use"),
        {"type": "user", "message": {"role": "user", "content": "tool result"}},
        _assistant("end_turn", "DONE: recon_summary.md written"),
    )

    state = inspect_transcript(transcript)

    assert state.complete is True
    assert state.done_seen is True


def test_stub_artifact_without_end_turn_is_not_completion_signal(tmp_path):
    (tmp_path / "analysis_1.md").write_text("# reserved\n", encoding="utf-8")
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(transcript, _assistant("tool_use"))

    state = inspect_transcript(transcript)

    assert state.complete is False


def test_wait_for_turn_complete_timeout_without_transcript(tmp_path):
    session = ClaudePtySession(
        ["claude"],
        cwd=tmp_path,
        env={},
        session_id="missing",
        prompt_path=tmp_path / "prompt.md",
        log_file=io.StringIO(),
        claude_home=tmp_path,
    )
    session.is_alive = lambda: True  # type: ignore[method-assign]

    start = time.time()
    state = session.wait_for_turn_complete(timeout_s=0.1, quiescence_s=0.01, poll_s=0.01)

    assert state.complete is False
    assert time.time() - start < 1.0


def test_parse_transcript_usage_accumulates_assistant_usage(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        _assistant(
            "tool_use",
            usage={
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 4,
            },
        ),
        _assistant(
            "end_turn",
            "DONE: complete",
            usage={
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 40,
            },
        ),
    )

    usage = parse_transcript_usage(transcript)

    assert usage["num_turns"] == 1
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 22
    assert usage["cache_read_input_tokens"] == 33
    assert usage["cache_creation_input_tokens"] == 44


def test_transcript_shows_compaction_from_claude_ui_markers(tmp_path):
    log = tmp_path / "stdio.log"
    log.write_text(
        "13% until auto-compact\nConversation compacted (ctrl+o for history)\n",
        encoding="utf-8",
    )

    assert transcript_shows_compaction(log) is True


def test_driver_rate_limit_detection_accepts_claude_session_jsonl(tmp_path):
    log = tmp_path / "stdio.log"
    _write_jsonl(
        log,
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "stop_reason": "rate_limited",
                "content": [],
            },
        },
    )

    assert D.detect_rate_limit(log) is True


def test_posix_pty_spawn_uses_popen_not_raw_fork_waitpid():
    """POSIX PTY launch must not use raw pty.fork()+waitpid.

    Launching from inside Claude Code can inherit parent process signal state;
    Popen ownership plus SIGCHLD reset is the durable path for macOS/Linux.
    """
    spawn_src = inspect.getsource(ClaudePtySession.spawn)
    alive_src = inspect.getsource(ClaudePtySession.is_alive)

    assert "pty.fork()" not in spawn_src
    assert "subprocess.Popen" in spawn_src
    assert "signal.SIGCHLD" in spawn_src
    assert "os.waitpid" not in alive_src
    assert ".poll()" in alive_src
