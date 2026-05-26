"""Ship 8.16 -- Continuation respawn MUST deliver the compact prompt.

Root cause (codex #1 / T1+T1b): both continuation transports
(`_respawn_missing_only`, `_respawn_via_resume`) preserved the ORIGINAL phase
prompt as the argv positional element while `send_bootstrap` is suppressed
under `PLAMEN_BOOTSTRAP_IN_ARGV=1`. So the model re-ran the full phase prompt
instead of the compact continuation -- and the Ship 8.11/8.12 "synthetic proof"
only asserted the path was INVOKED, never that the spawned argv carried the
continuation text.

These tests assert, for BOTH transports, that the spawned argv's positional
prompt references the continuation/missing-only SNAPSHOT and NOT the original
phase prompt (codex adjustment 2).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402


class _FakeSession:
    """Captures the argv passed to ClaudePtySession without launching a PTY."""

    last = None

    def __init__(self, cmd, *, cwd, env, session_id, prompt_path, log_file):
        self.cmd = list(cmd)
        self.cwd = cwd
        self.env = env
        self.session_id = session_id
        self.prompt_path = prompt_path
        _FakeSession.last = self

    def spawn(self):
        pass

    def send_bootstrap(self):
        pass


def _argv_positional(argv):
    """Return the positional bootstrap prompt (final argv element), or None."""
    if argv and str(argv[-1]).startswith(
        "Read and fully execute every instruction in "
    ):
        return argv[-1]
    return None


def _base_cmd_with_original(tmp_path, original_snap: Path):
    return [
        D.CLAUDE_BIN,
        "--model", "sonnet",
        "--session-id", "OLD-SESSION-ID",
        "--dangerously-skip-permissions",
        "--add-dir", str(tmp_path),
        "--disallowedTools", "mcp__*",
        "--no-chrome", D._argv_bootstrap_instruction(original_snap),
    ]


# --------------------------------------------------------------------------
# unit: _rewrite_argv_positional_prompt
# --------------------------------------------------------------------------

def test_rewrite_replaces_final_positional_prompt(tmp_path):
    snap = tmp_path / "cont.md"
    orig = tmp_path / "orig.md"
    argv = [D.CLAUDE_BIN, "--model", "sonnet", "--no-chrome",
            D._argv_bootstrap_instruction(orig)]
    out = D._rewrite_argv_positional_prompt(argv, snap)
    assert _argv_positional(out) == D._argv_bootstrap_instruction(snap)
    assert orig.as_posix() not in _argv_positional(out)


def test_rewrite_noop_when_no_positional(tmp_path):
    snap = tmp_path / "cont.md"
    argv = [D.CLAUDE_BIN, "--model", "sonnet", "--add-dir", str(tmp_path)]
    out = D._rewrite_argv_positional_prompt(argv, snap)
    assert out == argv  # unchanged; headless send_bootstrap delivers the snapshot


def test_rewrite_targets_last_separator_not_flag_values(tmp_path):
    """Legacy separator argvs still rewrite; flags are not mistaken for prompts."""
    snap = tmp_path / "cont.md"
    orig = tmp_path / "orig.md"
    argv = [D.CLAUDE_BIN, "--strict-mcp-config", "--mcp-config", "iso.json",
            "--", D._argv_bootstrap_instruction(orig)]
    out = D._rewrite_argv_positional_prompt(argv, snap)
    assert "--strict-mcp-config" in out
    assert "--mcp-config" in out
    assert snap.as_posix() in _argv_positional(out)


def test_pty_prompt_arg_uses_documented_positional_not_bare_separator(tmp_path):
    original = tmp_path / "phase.md"
    cmd = D.append_claude_pty_prompt_arg(
        [D.CLAUDE_BIN, "--mcp-config", "iso.json"],
        D._argv_bootstrap_instruction(original),
    )
    assert "--" not in cmd
    assert "--no-chrome" in cmd
    assert cmd[-1] == D._argv_bootstrap_instruction(original)


# --------------------------------------------------------------------------
# integration: missing-only transport
# --------------------------------------------------------------------------

def test_missing_only_argv_references_snapshot_not_original(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "ClaudePtySession", _FakeSession)
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    original = sp / "_prompt_breadth.attempt1.md"
    original.write_text("ORIGINAL FULL BREADTH PROMPT", encoding="utf-8")
    (sp / "spawn_manifest.md").write_text("# manifest", encoding="utf-8")
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")
    base_cmd = _base_cmd_with_original(tmp_path, original)

    sess = D._respawn_missing_only(
        phase=phase,
        scratchpad=sp,
        row_statuses=[{"name": "analysis_core_state.md", "status": "MISSING"}],
        base_cmd=base_cmd,
        cwd=str(tmp_path),
        env={"PLAMEN_BOOTSTRAP_IN_ARGV": "1"},
        log_file=None,
        prompt_path=original,
    )
    pos = _argv_positional(sess.cmd)
    assert pos is not None
    # references the compact missing-only snapshot, not the original prompt
    assert sess.prompt_path != original
    assert sess.prompt_path.as_posix() in pos
    assert original.as_posix() not in pos
    # fresh session uses a NEW session-id (not the old one)
    assert "OLD-SESSION-ID" not in sess.cmd


# --------------------------------------------------------------------------
# integration: resume transport
# --------------------------------------------------------------------------

def test_resume_argv_references_continuation_not_original(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "ClaudePtySession", _FakeSession)
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    original = sp / "_prompt_depth.attempt1.md"
    original.write_text("ORIGINAL FULL DEPTH PROMPT", encoding="utf-8")
    base_cmd = _base_cmd_with_original(tmp_path, original)

    sess = D._respawn_via_resume(
        session_id="RESUME-SESSION-ID",
        continuation="Finish the 3 missing depth rows only.",
        base_cmd=base_cmd,
        cwd=str(tmp_path),
        env={"PLAMEN_BOOTSTRAP_IN_ARGV": "1"},
        log_file=None,
        prompt_path=original,
    )
    pos = _argv_positional(sess.cmd)
    assert pos is not None
    assert sess.prompt_path != original
    assert sess.prompt_path.as_posix() in pos
    assert original.as_posix() not in pos
    # resume uses --resume, not --session-id
    assert "--resume" in sess.cmd
    assert "--session-id" not in sess.cmd


def test_neither_transport_leaves_original_prompt_as_instruction(tmp_path, monkeypatch):
    """Belt-and-suspenders: the original phase-prompt path must never be the
    executable instruction target in either respawn argv."""
    monkeypatch.setattr(D, "ClaudePtySession", _FakeSession)
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    original = sp / "_prompt_breadth.attempt1.md"
    original.write_text("ORIGINAL", encoding="utf-8")
    (sp / "spawn_manifest.md").write_text("# manifest", encoding="utf-8")
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")
    base = _base_cmd_with_original(tmp_path, original)
    orig_instruction = D._argv_bootstrap_instruction(original)

    s1 = D._respawn_missing_only(
        phase=phase, scratchpad=sp,
        row_statuses=[{"name": "analysis_x.md", "status": "MISSING"}],
        base_cmd=base, cwd=str(tmp_path),
        env={"PLAMEN_BOOTSTRAP_IN_ARGV": "1"}, log_file=None, prompt_path=original,
    )
    s2 = D._respawn_via_resume(
        session_id="X", continuation="finish missing",
        base_cmd=base, cwd=str(tmp_path),
        env={"PLAMEN_BOOTSTRAP_IN_ARGV": "1"}, log_file=None, prompt_path=original,
    )
    assert orig_instruction not in s1.cmd
    assert orig_instruction not in s2.cmd
