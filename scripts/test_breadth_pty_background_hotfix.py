"""Breadth PTY execution hotfix — rendered-prompt assertions.

The live DODO breadth attempt serialized workers (foreground Task contract +
"bounded batches of at most 6") and produced only analysis_cross_chain.md
before halting. Under the persistent PTY transport with a disk-derived gate,
breadth MUST spawn every missing row at once as BACKGROUND Task calls.

These tests assert behavior against the FULLY RENDERED `_prompt_breadth` text
(build_phase_prompt output), not just the source templates — and confirm
headless/Codex behavior is NOT globally changed.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_prompt import build_phase_prompt  # noqa: E402
from plamen_types import plamen_home  # noqa: E402

_BREADTH = next(p for p in D.SC_PHASES if p.name == "breadth")
_DEPTH = next(p for p in D.SC_PHASES if p.name == "depth")


def _render(exec_mode: str, backend: str = "claude") -> str:
    sp = Path(tempfile.mkdtemp()) / ".scratchpad"
    sp.mkdir(parents=True)
    cfg = {
        "scratchpad": str(sp), "project_root": str(sp.parent),
        "language": "evm", "mode": "thorough", "pipeline": "sc",
        "proven_only": False, "cli_backend": backend,
        "claude_exec_mode": exec_mode,
    }
    return build_phase_prompt(plamen_home() / "commands" / "plamen.md", _BREADTH, cfg)


# ───────────────────── rendered breadth PTY prompt ─────────────────────

def test_pty_breadth_prompt_mandates_background():
    p = _render("pty")
    assert "run_in_background: true" in p


def test_pty_breadth_prompt_forbids_foreground_contract():
    p = _render("pty")
    assert "All Task calls MUST be foreground" not in p


def test_pty_breadth_prompt_forbids_batches():
    p = _render("pty")
    # the exact serializing directive must be gone
    assert "at most 6 parallel Task calls" not in p
    assert "bounded batches" not in p
    # and the prompt must actively forbid batching/waves (wrap-robust checks)
    assert "split OPEN_OUTPUTS into batches" in p
    assert "phasing" in p  # "Do NOT use Wave 1 / Wave 2 phasing"


def test_pty_breadth_prompt_spawns_all_at_once():
    p = _render("pty")
    assert "Spawn ALL of OPEN_OUTPUTS in ONE assistant message" in p


# ───────────────────── headless/Codex unchanged ─────────────────────

def test_pty_breadth_prompt_requires_complete_disk_artifact():
    p = _render("pty")
    assert "the LAST PLAMEN_STATUS marker is COMPLETE" in p
    assert "missing a final PLAMEN_STATUS: COMPLETE" in p
    assert "## No Findings" in p
    assert "Negative Result" in p


def test_pty_breadth_prompt_does_not_trust_background_completion_text():
    p = _render("pty")
    assert "Task completion text as advisory only" in p
    assert "A background Task that says" in p
    assert "without a COMPLETE artifact on disk is still incomplete" in p
    assert "exists and are >=200 bytes" not in p
    assert "exist and are substantial" not in p


def test_headless_breadth_keeps_foreground_batches():
    p = _render("headless")
    # headless single-turn would orphan background agents -> keep the
    # foreground batched loop unchanged (no global background flip).
    # NOTE: 'run_in_background: true' DOES appear in the headless contract as a
    # PROHIBITION ("Do NOT pass run_in_background: true"), so we assert the
    # distinguishing foreground directives instead.
    assert "bounded batches of at most 6 parallel Task calls" in p
    assert "All Task calls MUST be foreground" in p
    assert "Spawn ALL of OPEN_OUTPUTS in ONE assistant message" not in p


# ───────────────────── missing-only continuation ─────────────────────

def test_missing_only_breadth_uses_background_no_serialize(tmp_path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "spawn_manifest.md").write_text("# manifest", encoding="utf-8")
    prompt_path = sp / "_prompt_breadth.attempt1.md"
    prompt_path.write_text("orig", encoding="utf-8")
    snap = D._build_missing_only_prompt(
        _BREADTH, sp,
        [{"name": "analysis_core_state.md", "status": "MISSING"},
         {"name": "analysis_token_flow.md", "status": "MISSING"}],
        prompt_path,
    )
    txt = snap.read_text(encoding="utf-8")
    assert "run_in_background: true" in txt
    assert "single assistant message" in txt


def test_missing_only_depth_stays_foreground(tmp_path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "spawn_manifest.md").write_text("# manifest", encoding="utf-8")
    prompt_path = sp / "_prompt_depth.attempt1.md"
    prompt_path.write_text("orig", encoding="utf-8")
    snap = D._build_missing_only_prompt(
        _DEPTH, sp,
        [{"name": "depth_token_flow_findings.md", "status": "MISSING"}],
        prompt_path,
    )
    txt = snap.read_text(encoding="utf-8")
    assert "depth rows are defined by this phase's role/output contract" in txt
    assert "spawn_manifest.md" not in txt
    assert "opengrep" not in txt.lower()
    assert "run_in_background: true" not in txt
