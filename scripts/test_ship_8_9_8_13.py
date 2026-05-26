"""Ship 8.9 (coordinator context diet) + Ship 8.13 (auto-compaction telemetry).

8.9  -- breadth subagents return a TERSE status line only; no pasted analysis
        bodies / no <ANALYSIS_TEXT> echo. Analysis stays on disk. Lowers the
        coordinator context that triggers auto-compaction.
8.13 -- diagnostic-only: when the coordinator emits DONE after an
        auto-compaction yet the disk gate rejects it, the driver logs ONE
        warning. It NEVER gates, NEVER fails the phase, emitted at most once
        per phase run.
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
import plamen_prompt as p  # noqa: E402
import pty_exec as ptx  # noqa: E402
from plamen_types import SC_PHASES  # noqa: E402


# ===========================================================================
# Ship 8.9 -- rendered breadth prompt is status-only
# ===========================================================================


def _render_breadth(tmp_root: Path) -> str:
    scratch = tmp_root / ".scratchpad"
    project = tmp_root / "proj"
    scratch.mkdir(parents=True, exist_ok=True)
    project.mkdir(parents=True, exist_ok=True)
    (scratch / "v1.md").write_text(
        "## Phase 3: Parallel Breadth Analysis\n\nSpawn agents.\n" + ("BODY " * 80),
        encoding="utf-8",
    )
    cfg = {
        "project_root": str(project),
        "scratchpad": str(scratch),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
        "cli_backend": "claude",
    }
    phase = next(ph for ph in SC_PHASES if ph.name == "breadth")
    return p.build_phase_prompt(scratch / "v1.md", phase, cfg)


def test_89_breadth_return_is_status_line_only(tmp_path):
    rendered = _render_breadth(tmp_path)
    assert "STATUS LINE ONLY" in rendered
    assert "ALL analysis stays" in rendered
    # The terse return line is still specified.
    assert "DONE: {N} findings written to {expected_output}" in rendered


def test_89_no_analysis_text_echo_target(tmp_path):
    """The only ANALYSIS_TEXT mention in the rendered prompt must be a
    FORBIDDING directive ('Do NOT include ... <ANALYSIS_TEXT>'), never a
    salvage/echo target. Mirrors the per-contract leak-guard pattern."""
    import re

    rendered = _render_breadth(tmp_path)
    for m in re.finditer(r"ANALYSIS_TEXT", rendered):
        window = rendered[max(0, m.start() - 80): m.start() + 20].lower()
        assert any(
            neg in window for neg in ("do not", "must not", "never", "no ")
        ), (
            f"ANALYSIS_TEXT appears outside a forbidding context: "
            f"...{rendered[max(0, m.start()-80):m.start()+20]!r}..."
        )


def test_89_no_required_body_paste_directive(tmp_path):
    """No surviving instruction to paste finding bodies / code / evidence
    into the return message."""
    rendered = _render_breadth(tmp_path)
    low = rendered.lower()
    assert "do not paste finding bodies" in low


# ===========================================================================
# Ship 8.13 -- compaction detector (unit)
# ===========================================================================


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def test_813_detects_before_compaction(tmp_path):
    t = _write(tmp_path / "t.jsonl", "...summary below ... before compaction ...")
    assert ptx.transcript_shows_compaction(t) is True


def test_813_detects_continue_from_left_off(tmp_path):
    t = _write(
        tmp_path / "t.jsonl",
        "Continue the conversation from where it left off without asking.",
    )
    assert ptx.transcript_shows_compaction(t) is True


def test_813_detects_repeated_compact(tmp_path):
    t = _write(tmp_path / "t.jsonl", "compact compact compact happened")
    assert ptx.transcript_shows_compaction(t) is True


def test_813_clean_transcript_no_detection(tmp_path):
    t = _write(tmp_path / "t.jsonl", "normal turn, one DONE summary, no markers")
    assert ptx.transcript_shows_compaction(t) is False


def test_813_missing_file_is_false(tmp_path):
    assert ptx.transcript_shows_compaction(tmp_path / "nope.jsonl") is False
    assert ptx.transcript_shows_compaction(None) is False


def test_813_extra_text_channel(tmp_path):
    t = _write(tmp_path / "t.jsonl", "clean")
    # stdio-side marker still trips detection.
    assert ptx.transcript_shows_compaction(t, extra_text="before compaction") is True


# ===========================================================================
# Ship 8.13 -- telemetry integration: emitted once, never gates
# ===========================================================================


class _TurnState:
    def __init__(self, complete=True, rate_limited=False):
        self.complete = complete
        self.rate_limited = rate_limited
        self.line_count = 0
        self.last_event_time = None


class _MockSession:
    def __init__(self, transcript_path: Path, session_id="s"):
        self.transcript_path = transcript_path
        self.session_id = session_id
        self._alive = True
        self.wait_calls = 0
        self.terminate_calls = 0
        self.send_continuation_calls: list[str] = []

    def spawn(self):
        pass

    def send_bootstrap(self):
        pass

    def is_alive(self):
        return self._alive

    def write(self, text):
        pass

    def send_continuation(self, msg):
        self.send_continuation_calls.append(msg)

    def terminate(self, grace_s=5.0):
        self.terminate_calls += 1
        self._alive = False

    def wait_for_turn_complete(self, *a, **k):
        self.wait_calls += 1
        return _TurnState(complete=True, rate_limited=False)


def _breadth_phase():
    return D.Phase(
        name="breadth",
        section_markers=["Phase 3"],
        expected_artifacts=["analysis_*.md"],
        base_timeout_s=60,
        min_artifact_bytes=200,
    )


def _seed_manifest_and_incomplete(sp: Path):
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "_audit_started_with_markers.json").write_text("{}", encoding="utf-8")
    (sp / "spawn_manifest.md").write_text(
        "# Spawn Manifest\n\n"
        "| Template | Required? | Agent ID | Focus Area | Expected Output | Status | Type |\n"
        "|----------|-----------|----------|------------|-----------------|--------|------|\n"
        "| TPL | YES | B1 | core_state | analysis_core_state.md | PENDING | agent |\n",
        encoding="utf-8",
    )
    (sp / "analysis_core_state.md").write_text(
        "<!-- PLAMEN_ARTIFACT: analysis_core_state.md -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n# x\n\n## Findings\n\n"
        + ("partial " * 40),
        encoding="utf-8",
    )


def _complete(sp: Path):
    (sp / "analysis_core_state.md").write_text(
        "<!-- PLAMEN_ARTIFACT: analysis_core_state.md -->\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n# x\n\n## Finding [CS-1]: f\n"
        + ("body " * 60) + "\n<!-- PLAMEN_FINDINGS_COUNT: 1 -->\n",
        encoding="utf-8",
    )


def test_813_info_emitted_once_and_never_gates(tmp_path, monkeypatch, caplog):
    """DONE-after-compaction + gate-miss -> exactly ONE telemetry info,
    across multiple continuation attempts, and it NEVER changes the rc /
    gates the phase (recovery proceeds normally to rc=0)."""
    project = tmp_path
    sp = project / ".scratchpad"
    _seed_manifest_and_incomplete(sp)
    # Transcript carries the auto-compaction fingerprint.
    transcript = sp / "session.jsonl"
    transcript.write_text(
        "...lots of turns...\nbefore compaction\nContinue the conversation "
        "from where it left off...\n",
        encoding="utf-8",
    )

    respawns = {"n": 0}

    def _fake_respawn(**kwargs):
        respawns["n"] += 1
        if respawns["n"] >= 2:
            _complete(sp)  # second respawn fills the row -> next gate passes
        return _MockSession(transcript)

    monkeypatch.setattr(D, "_respawn_missing_only", _fake_respawn)

    session = _MockSession(transcript)
    config = {
        "project_root": str(project),
        "scratchpad": str(sp),
        "claude_pty_quiescence_s": 0.0,
        "pty_continuation_budget": 5,
    }
    with caplog.at_level(logging.INFO):
        rc, final = D._run_supervised_pty_loop(
            session=session,
            scratchpad=sp,
            project_root=str(project),
            phase=_breadth_phase(),
            config=config,
            preflight={
                "live_pty_continue_supported": False,
                "agentid_resume_supported": False,
            },
            timeout=5.0,
            quiescence_s=0.0,
            on_poll=None,
            base_cmd=["claude", "--session-id", "s"],
            cwd=str(project),
            env={},
            log_file=None,
            prompt_path=sp / "prompt.md",
        )

    # Recovery succeeded -> telemetry did NOT gate the phase.
    assert rc == 0
    # Info emitted EXACTLY once despite >=2 gate-fail iterations.
    msg = "Claude context compaction"
    occurrences = caplog.text.count(msg)
    assert occurrences == 1, f"expected 1 telemetry info, got {occurrences}"
    assert "WARNING" not in caplog.text


def test_813_no_info_without_compaction_fingerprint(tmp_path, monkeypatch, caplog):
    """A plain gate-miss with NO compaction fingerprint -> NO telemetry
    info (no false alarms / no log spam)."""
    project = tmp_path
    sp = project / ".scratchpad"
    _seed_manifest_and_incomplete(sp)
    transcript = sp / "session.jsonl"
    transcript.write_text("clean turn, single DONE, no markers", encoding="utf-8")

    def _fake_respawn(**kwargs):
        _complete(sp)
        return _MockSession(transcript)

    monkeypatch.setattr(D, "_respawn_missing_only", _fake_respawn)

    session = _MockSession(transcript)
    config = {
        "project_root": str(project),
        "scratchpad": str(sp),
        "claude_pty_quiescence_s": 0.0,
        "pty_continuation_budget": 5,
    }
    with caplog.at_level(logging.INFO):
        rc, final = D._run_supervised_pty_loop(
            session=session,
            scratchpad=sp,
            project_root=str(project),
            phase=_breadth_phase(),
            config=config,
            preflight={
                "live_pty_continue_supported": False,
                "agentid_resume_supported": False,
            },
            timeout=5.0,
            quiescence_s=0.0,
            on_poll=None,
            base_cmd=["claude", "--session-id", "s"],
            cwd=str(project),
            env={},
            log_file=None,
            prompt_path=sp / "prompt.md",
        )
    assert rc == 0
    assert "Claude context compaction" not in caplog.text
