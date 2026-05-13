"""Tests for graceful stop, subprocess termination, and rate-limit detection.

Covers:
  1. GracefulStop: request_halt sets flag, install() is no-op
  2. PauseToggle: Esc triggers halt, Ctrl+P toggles pause, _handle_key dispatch
  3. _wait_with_heartbeat: terminates subprocess on halt (returns -3)
  4. detect_rate_limit: JSON envelope path, text-fallback path, false-positive resistance
  5. rate_limit_wait_interactive: countdown exits on early_resume event
  6. estimate_rate_limit_wait_seconds: parses retry-after / reset-time patterns
  7. wait_halt_choice: Enter → True, Esc → False

Run: python -m pytest test_signal_and_ratelimit.py -v
"""
from __future__ import annotations

import json
import inspect
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D
import plamen_display as display


# ─── GracefulStop ──────────────────────────────────────────────────────


def test_graceful_stop_starts_unrequested():
    """GracefulStop starts with requested=False."""
    gs = display.GracefulStop()
    assert gs.requested is False


def test_graceful_stop_request_halt_sets_flag():
    """request_halt() sets .requested=True."""
    gs = display.GracefulStop()
    with patch.object(display, "print_halt_acknowledged"):
        gs.request_halt()
    assert gs.requested is True


def test_graceful_stop_request_halt_idempotent():
    """Second request_halt() is a no-op (no duplicate message)."""
    gs = display.GracefulStop()
    with patch.object(display, "print_halt_acknowledged") as mock_print:
        gs.request_halt()
        gs.request_halt()
    mock_print.assert_called_once()


def test_graceful_stop_install_is_noop():
    """install() no longer overrides SIGINT."""
    original = signal.getsignal(signal.SIGINT)
    gs = display.GracefulStop()
    gs.install()
    assert signal.getsignal(signal.SIGINT) == original


# ─── PauseToggle / KeyboardController ────────────────────────────────


def test_pause_toggle_starts_unpaused():
    """PauseToggle starts in unpaused state."""
    pt = display.PauseToggle()
    assert pt.paused is False


def test_pause_toggle_halt_requested_reflects_graceful_stop():
    """halt_requested property reflects graceful_stop.requested."""
    old = display.graceful_stop.requested
    try:
        display.graceful_stop.requested = False
        pt = display.PauseToggle()
        assert pt.halt_requested is False
        display.graceful_stop.requested = True
        assert pt.halt_requested is True
    finally:
        display.graceful_stop.requested = old


def test_pause_toggle_wait_if_paused_returns_immediately_when_unpaused():
    """wait_if_paused returns immediately when not paused."""
    pt = display.PauseToggle()
    start = time.time()
    pt.wait_if_paused()
    assert time.time() - start < 0.5


def test_handle_key_esc_triggers_halt():
    """Esc key (0x1b) sets halt via graceful_stop."""
    old = display.graceful_stop.requested
    display.graceful_stop.requested = False
    pt = display.PauseToggle()
    try:
        with patch.object(display, "print_halt_acknowledged"):
            pt._handle_key(b"\x1b")
        assert display.graceful_stop.requested is True
    finally:
        display.graceful_stop.requested = old


def test_handle_key_ctrl_p_toggles_pause():
    """Ctrl+P (0x10) toggles paused state."""
    pt = display.PauseToggle()
    assert pt.paused is False
    with patch.object(display, "print_paused"):
        pt._handle_key(b"\x10")
    assert pt.paused is True
    with patch.object(display, "print_resumed"):
        pt._handle_key(b"\x10")
    assert pt.paused is False


def test_handle_key_other_ignored():
    """Other keys are silently ignored."""
    pt = display.PauseToggle()
    pt._handle_key(b"a")
    assert pt.paused is False


# ─── _wait_with_heartbeat: subprocess termination ──────────────────────


def test_heartbeat_returns_minus3_on_halt():
    """When halt is requested, subprocess gets terminated and -3 is returned."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    sp = Path(tempfile.mkdtemp(prefix="plamen_hb_"))

    old_requested = display.graceful_stop.requested
    def _set_halt():
        time.sleep(0.5)
        display.graceful_stop.requested = True

    t = threading.Thread(target=_set_halt, daemon=True)
    t.start()

    start = time.time()
    try:
        rc = D._wait_with_heartbeat(proc, timeout=30, scratchpad=sp,
                                    phase_name="test", start_time=start)
        assert rc == -3, f"Expected -3, got {rc}"
        elapsed = time.time() - start
        assert elapsed < 12, f"Took {elapsed:.1f}s — too slow"
    finally:
        display.graceful_stop.requested = old_requested
        try:
            proc.kill()
        except Exception:
            pass
        import shutil
        shutil.rmtree(sp, ignore_errors=True)


def test_heartbeat_returns_on_process_exit():
    """Normal subprocess exit returns the exit code."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(42)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    sp = Path(tempfile.mkdtemp(prefix="plamen_hb2_"))
    start = time.time()
    rc = D._wait_with_heartbeat(proc, timeout=30, scratchpad=sp,
                                phase_name="test", start_time=start)
    assert rc == 42
    import shutil
    shutil.rmtree(sp, ignore_errors=True)


def test_heartbeat_timeout():
    """Process exceeding timeout raises TimeoutExpired."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    sp = Path(tempfile.mkdtemp(prefix="plamen_hb3_"))
    start = time.time()
    try:
        D._wait_with_heartbeat(proc, timeout=2, scratchpad=sp,
                               phase_name="test", start_time=start)
        assert False, "Should have raised TimeoutExpired"
    except subprocess.TimeoutExpired:
        pass
    finally:
        proc.kill()
        proc.wait()
        import shutil
        shutil.rmtree(sp, ignore_errors=True)


def test_run_phase_launches_isolated_process_group():
    """Phase subprocesses must be killable as a tree on halt/timeout."""
    src = inspect.getsource(D.run_phase)
    wait_src = inspect.getsource(D._wait_with_heartbeat)
    helper_src = inspect.getsource(D._terminate_process_tree)

    assert "CREATE_NEW_PROCESS_GROUP" in src
    assert "start_new_session" in src
    assert "_terminate_process_tree(proc" in src
    assert "_terminate_process_tree(proc" in wait_src
    assert "taskkill" in helper_src
    assert "killpg" in helper_src


# ─── detect_rate_limit ─────────────────────────────────────────────────


def _write_log(content: str) -> Path:
    """Write content to a temp file and return its Path."""
    p = Path(tempfile.mktemp(suffix=".log", prefix="plamen_rl_"))
    p.write_text(content, encoding="utf-8")
    return p


def test_rate_limit_json_429():
    """JSON envelope with api_error_status=429 → detected."""
    envelope = json.dumps({
        "is_error": True,
        "api_error_status": 429,
        "error": {"type": "rate_limit_error", "message": "too many requests"},
    })
    p = _write_log(f"some preamble\n{envelope}\n")
    assert D.detect_rate_limit(p) is True
    p.unlink()


def test_rate_limit_json_529():
    """JSON envelope with api_error_status=529 → detected."""
    envelope = json.dumps({
        "is_error": True,
        "api_error_status": 529,
        "error": {"type": "overloaded_error", "message": "overloaded"},
    })
    p = _write_log(f"{envelope}\n")
    assert D.detect_rate_limit(p) is True
    p.unlink()


def test_rate_limit_json_stop_reason():
    """JSON envelope with stop_reason=rate_limited → detected."""
    envelope = json.dumps({
        "is_error": False,
        "stop_reason": "rate_limited",
    })
    p = _write_log(f"{envelope}\n")
    assert D.detect_rate_limit(p) is True
    p.unlink()


def test_rate_limit_json_clean_exit_not_detected():
    """JSON envelope with is_error=False, stop_reason=end_turn → NOT detected."""
    envelope = json.dumps({
        "is_error": False,
        "stop_reason": "end_turn",
        "total_cost_usd": 0.05,
    })
    p = _write_log(f"{envelope}\n")
    assert D.detect_rate_limit(p) is False
    p.unlink()


def test_rate_limit_text_fallback_429():
    """No JSON envelope, but structured 429 error → detected via text regex."""
    content = "Error: 429 Too Many Requests\nPlease wait and try again."
    p = _write_log(content)
    assert D.detect_rate_limit(p) is True
    p.unlink()


def test_rate_limit_text_fallback_529():
    """No JSON envelope, but structured 529 error → detected via text regex."""
    content = "Error: 529 overloaded\nServer is overloaded."
    p = _write_log(content)
    assert D.detect_rate_limit(p) is True
    p.unlink()


def test_rate_limit_prose_false_positive_resistance():
    """LLM prose mentioning 'quota exhausted' without structured prefix → NOT detected."""
    content = (
        "The analysis showed that the rate limit quota was exhausted until Apr 23.\n"
        "This is because the protocol's internal throttle mechanism prevents abuse.\n"
    )
    p = _write_log(content)
    assert D.detect_rate_limit(p) is False
    p.unlink()


def test_rate_limit_missing_file():
    """Non-existent file → NOT detected."""
    p = Path(tempfile.mktemp(suffix=".log"))
    assert D.detect_rate_limit(p) is False


def test_rate_limit_empty_file():
    """Empty file → NOT detected."""
    p = _write_log("")
    assert D.detect_rate_limit(p) is False
    p.unlink()


# ─── estimate_rate_limit_wait_seconds ──────────────────────────────────


def test_estimate_retry_after_seconds():
    p = _write_log("Error 429. Retry-After: 120 seconds")
    result = D.estimate_rate_limit_wait_seconds(p)
    assert result == 120
    p.unlink()


def test_estimate_retry_after_minutes():
    p = _write_log("Error 429. try again in 5 minutes")
    result = D.estimate_rate_limit_wait_seconds(p)
    assert result == 300
    p.unlink()


def test_estimate_no_hint():
    """No parseable time hint → None (caller defaults to 300)."""
    p = _write_log("Error 429. Please wait.")
    result = D.estimate_rate_limit_wait_seconds(p)
    assert result is None
    p.unlink()


def test_failure_diagnosis_decodes_non_cp1252_stdout(tmp_path):
    """Diagnosis subprocess output is decoded as bytes, not Windows cp1252 text."""
    (tmp_path / "_stdio_breadth.attempt2.log").write_text(
        "timed out after 3600s\n", encoding="utf-8"
    )
    (tmp_path / "_prompt_breadth.attempt2.md").write_text(
        "prompt", encoding="utf-8"
    )

    class FakeResult:
        stdout = b"### What Happened\nbad byte: \x90\n### Root Cause\nok"
        stderr = b""

    with patch.object(display, "_find_claude_bin", return_value="claude"), \
         patch.object(display.subprocess, "run", return_value=FakeResult()):
        display.print_failure_diagnosis(
            "breadth",
            str(tmp_path),
            ["analysis_token_flow.md"],
            {"pipeline": "sc", "mode": "thorough", "language": "evm"},
        )

    out = (tmp_path / "_diagnosis_breadth.md").read_text(encoding="utf-8")
    assert "### What Happened" in out
    assert "bad byte:" in out
    assert "NoneType" not in out


def test_failure_diagnosis_extracts_codex_jsonl_agent_message(tmp_path):
    """Codex --json diagnosis output is rendered as message text, not raw JSONL."""
    (tmp_path / "_stdio_depth.attempt2.log").write_text(
        "missing never_cut_checkpoint.md\n", encoding="utf-8"
    )
    (tmp_path / "_prompt_depth.attempt2.md").write_text(
        "prompt", encoding="utf-8"
    )

    payload = {
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "agent_message",
            "text": "### What Happened\nDepth stopped early.\n\n### Root Cause\nMissing depth_exit.md",
        },
    }

    class FakeResult:
        stdout = (
            b'{"type":"thread.started","thread_id":"t"}\n'
            + json.dumps(payload).encode("utf-8")
            + b"\n"
            b'{"type":"turn.completed","usage":{"input_tokens":1}}\n'
        )
        stderr = b""

    with patch.object(display, "_find_codex_bin", return_value="codex"), \
         patch.object(display.subprocess, "run", return_value=FakeResult()):
        display.print_failure_diagnosis(
            "depth",
            str(tmp_path),
            ["depth_exit.md"],
            {
                "pipeline": "sc",
                "mode": "thorough",
                "language": "evm",
                "cli_backend": "codex",
            },
        )

    out = (tmp_path / "_diagnosis_depth.md").read_text(encoding="utf-8")
    assert "### What Happened" in out
    assert "Depth stopped early" in out
    assert '"thread.started"' not in out
    assert '"turn.completed"' not in out


def test_estimate_missing_file():
    p = Path(tempfile.mktemp(suffix=".log"))
    result = D.estimate_rate_limit_wait_seconds(p)
    assert result is None


# ─── rate_limit_wait_interactive ───────────────────────────────────────


def test_rate_limit_interactive_countdown_expires():
    """Short countdown expires naturally → returns False."""
    old = display.graceful_stop.requested
    display.graceful_stop.requested = False
    try:
        result = display.rate_limit_wait_interactive(2, "test_phase")
        assert result is False
    finally:
        display.graceful_stop.requested = old


def test_rate_limit_interactive_halt_interrupts():
    """Setting halt during countdown → raises KeyboardInterrupt."""
    old = display.graceful_stop.requested
    display.graceful_stop.requested = False

    def _trigger():
        time.sleep(0.5)
        display.graceful_stop.requested = True

    t = threading.Thread(target=_trigger, daemon=True)
    t.start()

    try:
        display.rate_limit_wait_interactive(30, "test_phase")
        assert False, "Should have raised KeyboardInterrupt"
    except KeyboardInterrupt:
        pass
    finally:
        display.graceful_stop.requested = old


# ─── _extract_json_envelope ────────────────────────────────────────────


def test_extract_envelope_valid():
    """Well-formed JSON at end of log → parsed."""
    data = {"is_error": False, "stop_reason": "end_turn", "cost": 0.05}
    tail = f"lots of text\n{json.dumps(data)}\n"
    result = D._extract_json_envelope(tail)
    assert result == data


def test_extract_envelope_missing():
    """No JSON in log → None."""
    result = D._extract_json_envelope("just plain text\nno json here\n")
    assert result is None


def test_extract_envelope_malformed():
    """Truncated JSON → None."""
    result = D._extract_json_envelope('{"is_error": true, "incomplete')
    assert result is None
