"""Ship 8.24 -- deterministic P0 replay suite (the hard acceptance gate).

Synthetic scratchpads, NO LLM calls. Replays each implemented P0 fix
(8.14-8.19) as an integrated scenario so a regression in any of them fails
here, and adds phase-matrix coverage (missing artifacts -> critical phase
gate fails).

Scope: ONLY the implemented P0 fixes. The P1 replay cases from the plan
(stale inventory chunk, missing verifier output, stale PoC evidence tag) are
intentionally NOT included -- 8.20-8.23 are not implemented.

Rescan note (codex 8.24 adjustment): rescan partial completion proves the
EXACT GATE FAILS and recovery is a WHOLE-PHASE RETRY -- NOT missing-only
continuation (that becomes Option B once rescan marker semantics are designed).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_prompt import _resolve_recon_prompt, build_phase_prompt  # noqa: E402
from plamen_types import plamen_home  # noqa: E402
from plamen_validators import (  # noqa: E402
    gate_passes,
    _quarantine_stale_on_retry,
    _snapshot_file_state,
    _detect_foreign_phase_writes,
)

RECON = next(p for p in D.SC_PHASES if p.name == "recon")
BREADTH = next(p for p in D.SC_PHASES if p.name == "breadth")
RESCAN = next(p for p in D.SC_PHASES if p.name == "rescan")
SUB = "z" * 300


class _FakeSession:
    last = None

    def __init__(self, cmd, *, cwd, env, session_id, prompt_path, log_file):
        self.cmd = list(cmd)
        self.prompt_path = prompt_path
        _FakeSession.last = self

    def spawn(self):
        pass

    def send_bootstrap(self):
        pass


def _argv_positional(argv):
    if argv and str(argv[-1]).startswith(
        "Read and fully execute every instruction in "
    ):
        return argv[-1]
    return None


# ==========================================================================
# 8.14 -- prompt source: recon resolves to the V2 single-agent body
# ==========================================================================

def test_replay_8_14_recon_uses_v2_prompt(tmp_path):
    p = _resolve_recon_prompt({"pipeline": "sc", "language": "evm"})
    assert p.parent.name == "v2"
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    prompt = build_phase_prompt(
        plamen_home() / "commands" / "plamen.md", RECON,
        {"scratchpad": str(sp), "project_root": str(tmp_path), "language": "evm",
         "mode": "thorough", "pipeline": "sc", "proven_only": False,
         "cli_backend": "claude", "claude_exec_mode": "pty"},
    )
    assert "Spawn Agent 1A" not in prompt
    assert "`claude -p`" not in prompt
    assert "{network_if_provided}" not in prompt


# ==========================================================================
# 8.15 -- recon targeted repair: replay (443-byte summary)
# ==========================================================================

def test_replay_8_15_recon_failure_preserves_valid_drafts(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    for name in RECON.expected_artifacts:
        sp.joinpath(name).write_text(
            "y" * 443 if name == "recon_summary.md" else SUB, encoding="utf-8")
    missing = [
        "rc=-2 parity: recon_summary.md is too small after nonzero rc (443 bytes, min=512)",
        "recon content: recon_summary.md is too small to be a clean handoff",
    ]
    renamed = _quarantine_stale_on_retry(sp, RECON, missing)
    assert renamed == []  # nothing valid destroyed
    for keep in RECON.expected_artifacts:
        if keep != "recon_summary.md":
            assert sp.joinpath(keep).exists()


# ==========================================================================
# 8.16 -- breadth false-DONE + missing rows: respawn delivers compact prompt
# ==========================================================================

def test_replay_8_16_missing_only_respawn_delivers_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "ClaudePtySession", _FakeSession)
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    original = sp / "_prompt_breadth.attempt1.md"
    original.write_text("ORIGINAL FULL PROMPT", encoding="utf-8")
    (sp / "spawn_manifest.md").write_text("# manifest", encoding="utf-8")
    base_cmd = [D.CLAUDE_BIN, "--model", "sonnet", "--session-id", "OLD",
                "--add-dir", str(tmp_path), "--no-chrome",
                D._argv_bootstrap_instruction(original)]
    sess = D._respawn_missing_only(
        phase=BREADTH, scratchpad=sp,
        row_statuses=[{"name": "analysis_core_state.md", "status": "MISSING"}],
        base_cmd=base_cmd, cwd=str(tmp_path),
        env={"PLAMEN_BOOTSTRAP_IN_ARGV": "1"}, log_file=None, prompt_path=original,
    )
    pos = _argv_positional(sess.cmd)
    assert sess.prompt_path != original
    assert sess.prompt_path.as_posix() in pos
    assert original.as_posix() not in pos


# ==========================================================================
# 8.17 -- rescan partial completion: EXACT gate fails (whole-phase retry)
# ==========================================================================

def test_replay_8_17_rescan_partial_exact_gate_fails(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    (sp / "rescan_manifest.md").write_text(
        "# Rescan Manifest\n- analysis_rescan_1.md\n- analysis_rescan_2.md\n"
        "- analysis_percontract_core.md\n", encoding="utf-8")
    # Only 1 of 3 declared files landed (partial completion / false-DONE).
    (sp / "analysis_rescan_1.md").write_text(SUB, encoding="utf-8")
    passed, missing = gate_passes(sp, str(tmp_path), RESCAN)
    assert passed is False, "exact gate must fail on partial rescan"
    assert "analysis_rescan_2.md" in missing
    # Recovery for rescan is the whole-phase retry path (NOT missing-only
    # continuation): rescan is NOT in PTY_SUPERVISED_PHASES in this ship.
    assert "rescan" not in D.PTY_SUPERVISED_PHASES


# ==========================================================================
# 8.19 -- rate-limit contamination: clean baseline detects foreign write
# ==========================================================================

def test_replay_8_19_clean_baseline_detects_foreign_write(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    # findings_inventory.md is owned by a later phase than recon.
    clean = _snapshot_file_state(sp, str(tmp_path))
    (sp / "findings_inventory.md").write_text(SUB, encoding="utf-8")
    detected = _detect_foreign_phase_writes(
        sp, str(tmp_path), D.SC_PHASES, "recon", "sc", clean)
    assert "findings_inventory.md" in detected


# ==========================================================================
# Phase-matrix coverage: missing artifacts -> critical phase gate fails
# ==========================================================================

def test_phase_matrix_missing_artifacts_fail_critical_phases(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    for phase in (RECON, BREADTH, RESCAN):
        passed, missing = gate_passes(sp, str(tmp_path), phase)
        assert passed is False, f"{phase.name}: empty scratchpad must fail gate"
        assert missing, f"{phase.name}: expected missing detail"
