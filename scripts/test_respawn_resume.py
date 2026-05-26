"""Tests for Ship 6 of the artifact-complete PTY supervision plan.

Validates ``plamen_driver._build_resume_cmd_and_snapshot`` -- the pure
helper underneath ``_respawn_via_resume``. Numbered tests 35-37 match
the plan's ``test_respawn_resume.py`` section.

We test the helper (not ``_respawn_via_resume`` itself) because the
full respawn invokes ``ClaudePtySession.spawn`` which forks a real
``claude`` PTY child; the unit-test boundary is the pure command +
snapshot construction. Production-level coverage of the spawn path
lives in Ship 4's empirical preflight (``_test_agentid_resume``) and
the live DODO/etc. audits that exercise it end-to-end.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402


_BASE_CMD = [
    "claude",
    "--model", "haiku",
    "--session-id", "1c0b1deb-aaaa-bbbb-cccc-deadbeef0001",
    "--dangerously-skip-permissions",
    "--add-dir", "/some/project/root",
    "--add-dir", "/home/user/.claude",
]


# ---------------------------------------------------------------------------
# Test 35 -- strips --session-id pair
# ---------------------------------------------------------------------------


def test_respawn_via_resume_strips_session_id_flag(tmp_path: Path):
    """The resume command MUST NOT contain the original
    ``--session-id <uuid>`` pair. Claude Code's CLI treats
    ``--session-id`` and ``--resume`` as mutually exclusive; if both
    are present the subprocess errors out before the supervision loop
    can issue the continuation prompt.

    Verifies BOTH the flag itself and the UUID that followed it are
    stripped, and that no other argv tokens are mutated."""
    prompt_path = tmp_path / "phase3-breadth.prompt.md"
    prompt_path.write_text("bootstrap text", encoding="utf-8")
    resume_cmd, _ = D._build_resume_cmd_and_snapshot(
        session_id="new-resumed-id-9999",
        continuation="continuation text body",
        base_cmd=_BASE_CMD,
        prompt_path=prompt_path,
        now_ts=1_700_000_000,
    )
    assert "--session-id" not in resume_cmd
    # The original UUID must NOT survive in the resume cmd.
    assert "1c0b1deb-aaaa-bbbb-cccc-deadbeef0001" not in resume_cmd
    # All other tokens (and ordering between them) MUST be preserved.
    assert "--model" in resume_cmd
    assert "haiku" in resume_cmd
    assert "--dangerously-skip-permissions" in resume_cmd
    assert "/some/project/root" in resume_cmd
    assert "/home/user/.claude" in resume_cmd
    # The two original --add-dir entries must remain in order.
    add_dir_indexes = [
        i for i, t in enumerate(resume_cmd) if t == "--add-dir"
    ]
    assert len(add_dir_indexes) == 2
    assert resume_cmd[add_dir_indexes[0] + 1] == "/some/project/root"
    assert resume_cmd[add_dir_indexes[1] + 1] == "/home/user/.claude"


# ---------------------------------------------------------------------------
# Test 36 -- adds --resume <session_id>
# ---------------------------------------------------------------------------


def test_respawn_via_resume_adds_resume_flag_with_correct_id(tmp_path: Path):
    """The resume command MUST insert ``--resume <session_id>``
    immediately after the program name (consistent CLI ordering).
    The session_id MUST be exactly the one passed to the helper, not
    a fresh UUID and not the original one that was stripped."""
    prompt_path = tmp_path / "phase3-breadth.prompt.md"
    prompt_path.write_text("bootstrap text", encoding="utf-8")
    new_id = "FRESH-RESUME-TARGET-7777"
    resume_cmd, _ = D._build_resume_cmd_and_snapshot(
        session_id=new_id,
        continuation="continuation text body",
        base_cmd=_BASE_CMD,
        prompt_path=prompt_path,
        now_ts=1_700_000_000,
    )
    # Program name is unchanged at index 0.
    assert resume_cmd[0] == "claude"
    # --resume <new_id> is inserted as tokens 1 and 2.
    assert resume_cmd[1] == "--resume"
    assert resume_cmd[2] == new_id
    # There must be EXACTLY ONE --resume in the command.
    assert resume_cmd.count("--resume") == 1


# ---------------------------------------------------------------------------
# Test 37 -- continuation snapshot is written to disk
# ---------------------------------------------------------------------------


def test_respawn_via_resume_writes_continuation_snapshot(tmp_path: Path):
    """The continuation message MUST be persisted to a sibling of
    the original prompt_path, named so multiple respawns within the
    same audit do not collide. The bootstrap prompt for the resumed
    session reads from THIS file, not the original prompt -- so
    correctness depends on the content matching the continuation
    verbatim."""
    prompt_path = tmp_path / "phase3-breadth.prompt.md"
    prompt_path.write_text("bootstrap text", encoding="utf-8")
    continuation_text = (
        "The driver's artifact-complete gate did not pass.\n"
        "Incomplete rows:\n"
        "- B3 / analysis_access_control.md / IN_PROGRESS\n"
        "Respawn ONLY these rows."
    )
    resume_cmd, snapshot = D._build_resume_cmd_and_snapshot(
        session_id="resume-target",
        continuation=continuation_text,
        base_cmd=_BASE_CMD,
        prompt_path=prompt_path,
        now_ts=1_700_000_000,
    )
    # Snapshot lives next to the original prompt with a deterministic
    # name (timestamp + .continuation. infix). Different respawns of
    # the same audit can coexist on disk.
    assert snapshot.exists()
    assert snapshot.parent == prompt_path.parent
    assert "continuation" in snapshot.name
    # Snapshot content is EXACTLY the continuation string.
    assert snapshot.read_text(encoding="utf-8") == continuation_text
    # The original bootstrap prompt is left untouched -- the resumed
    # session bootstraps from the snapshot, not the original.
    assert prompt_path.read_text(encoding="utf-8") == "bootstrap text"


# ---------------------------------------------------------------------------
# Defensive counterparts
# ---------------------------------------------------------------------------


def test_respawn_via_resume_empty_base_cmd_raises(tmp_path: Path):
    """An empty base_cmd (which would produce a bogus argv) must
    raise ValueError -- the supervised loop's except-handler converts
    that to rc=-2 (whole-phase retry) so the system degrades safely."""
    prompt_path = tmp_path / "p.md"
    prompt_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        D._build_resume_cmd_and_snapshot(
            session_id="x",
            continuation="y",
            base_cmd=[],
            prompt_path=prompt_path,
        )


def test_respawn_via_resume_base_cmd_without_session_id_still_works(tmp_path: Path):
    """If the base_cmd doesn't have a --session-id pair at all
    (caller error or a non-supervised entry path), the helper still
    constructs a valid resume command -- it just doesn't have to
    strip anything."""
    prompt_path = tmp_path / "p.md"
    prompt_path.write_text("x", encoding="utf-8")
    resume_cmd, _ = D._build_resume_cmd_and_snapshot(
        session_id="resume-id",
        continuation="continuation",
        base_cmd=["claude", "--model", "haiku"],
        prompt_path=prompt_path,
    )
    assert resume_cmd[0] == "claude"
    assert resume_cmd[1] == "--resume"
    assert resume_cmd[2] == "resume-id"
    assert "--model" in resume_cmd
    assert "haiku" in resume_cmd
