"""Wiring + soft-validation tests for Phase 4a.5 Pass 2.

V1 ran a two-pass Semantic Invariant phase in Thorough mode. The V1→V2
deterministic-driver refactor dropped the Pass 2 phase definition while
leaving five separate documentation references and the prompt file
intact (in V1 path). An observed audit confirmed Pass 2 never ran
in our V2 Thorough audit — `semantic_invariants.md` had only `Phase
4a.5 Pass 1` content. A live-audit process log describes Pass 2
specifically catching a missing-approve-on-sentinel-address regression
— a finding an earlier v2 audit MISSED.

These tests lock in the Pass 2 wiring:

  1. The standalone prompt for Pass 2 is mapped in
     `_STANDALONE_PROMPT_MAP`.
  2. The Phase entry exists in BOTH SC_PHASES and L1_PHASES.
  3. Pass 2 is mode-gated to Thorough only (Core/Light skip).
  4. The validator is SOFT — never returns hard issues / never halts.
  5. Missing Pass 2 output writes a degraded sentinel; pipeline proceeds.
  6. Present Pass 2 output passes the validator cleanly.

These guards prevent the same V1→V2 wiring regression from happening
again on this phase.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))


# -------- Wiring: standalone prompt + phase entry -------------------------

def test_invariants_p2_standalone_prompt_mapped():
    from plamen_prompt import _STANDALONE_PROMPT_MAP, _STANDALONE_V2_DIR
    assert "invariants_p2" in _STANDALONE_PROMPT_MAP, (
        "Phase invariants_p2 must be in _STANDALONE_PROMPT_MAP — "
        "otherwise V2 driver falls back to V1 section extraction and "
        "the Phase 4a.5 Pass 2 prompt is lost."
    )
    prompt_path = _STANDALONE_V2_DIR / _STANDALONE_PROMPT_MAP["invariants_p2"]
    assert prompt_path.exists(), (
        f"Mapped prompt file missing on disk: {prompt_path}. The mapping "
        "points at a file the driver will try to load — if missing, the "
        "phase fails on its first attempt."
    )


def test_invariants_p2_prompt_has_required_sections():
    """The Pass 2 prompt must mention the BRANCH_ASYMMETRY /
    DIRECTIONAL_PAIRING_GAP / CROSS_FIELD_DECODE_GAP classifications —
    these are the bug classes the v1.0.5 benchmark caught and the v2
    audit missed. If a refactor drops them, recall regresses again."""
    from plamen_prompt import _STANDALONE_PROMPT_MAP, _STANDALONE_V2_DIR
    text = (_STANDALONE_V2_DIR / _STANDALONE_PROMPT_MAP["invariants_p2"]).read_text(
        encoding="utf-8",
    )
    required = (
        "BRANCH_ASYMMETRY",
        "DIRECTIONAL_PAIRING_GAP",
        "CROSS_FIELD_DECODE_GAP",
        "CONFIRMED_GAP",
        "Summary Flags",  # triggers SEMANTIC_GAP_INVESTIGATOR niche agent
    )
    for tok in required:
        assert tok in text, (
            f"Pass 2 prompt missing required directive {tok!r}. This is a "
            "load-bearing classification — its omission was the regression "
            "that hid a missing-approve-on-sentinel-address regression and a "
            "native-token-wrap decode gap."
        )


def test_invariants_p2_phase_entry_in_sc_phases():
    from plamen_types import SC_PHASES
    p = next((p for p in SC_PHASES if p.name == "invariants_p2"), None)
    assert p is not None, "invariants_p2 phase missing from SC_PHASES"
    # Mode-gated to Thorough only — Core skips per user direction (cost).
    assert p.modes == {"thorough"}, (
        f"invariants_p2 mode set should be {{thorough}}, got {p.modes!r}. "
        "Core/Light skip Pass 2 to keep audit cost predictable."
    )
    # Soft phase — failure must not halt pipeline.
    assert p.critical is False, (
        "invariants_p2.critical MUST be False. Pass 2 is an enrichment "
        "phase; failure should NOT halt the audit. Pass 1 data alone is "
        "sufficient for depth agents."
    )
    # Model should be sonnet per implementation plan (per user approval).
    assert p.model == "sonnet"


def test_invariants_p2_phase_entry_in_l1_phases():
    from plamen_types import L1_PHASES
    p = next((p for p in L1_PHASES if p.name == "invariants_p2"), None)
    assert p is not None, "invariants_p2 phase missing from L1_PHASES"
    assert p.modes == {"thorough"}
    assert p.critical is False
    assert p.model == "sonnet"


def test_invariants_p2_runs_after_pass1():
    """Phase order: invariants_p2 must appear immediately AFTER invariants
    in both phase lists. If the order is reversed, Pass 2 would read an
    empty file or stale data."""
    from plamen_types import SC_PHASES, L1_PHASES
    for label, lst in (("SC", SC_PHASES), ("L1", L1_PHASES)):
        names = [p.name for p in lst]
        assert "invariants" in names and "invariants_p2" in names, label
        i1, i2 = names.index("invariants"), names.index("invariants_p2")
        assert i2 == i1 + 1, (
            f"{label}: expected invariants_p2 immediately after invariants; "
            f"got positions {i1} → {i2}"
        )


# -------- Soft validator behavior ----------------------------------------

def _write(p: Path, body: str) -> None:
    p.write_text(body, encoding="utf-8")


def test_validator_soft_pass_when_pass2_section_present(tmp_path: Path):
    import plamen_validators as V
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "semantic_invariants.md",
        "# Semantic Invariants — Phase 4a.5 Pass 1\n\n"
        "(pass 1 content here)\n\n"
        "## Pass 2: Recursive Trace Results\n\n"
        "| Variable | Flag | Classification |\n"
        "|----------|------|----------------|\n"
        "| feePercent | CONDITIONAL | CONFIRMED_GAP |\n\n"
        "### Summary Flags\n"
        "- sync_gaps: 1\n",
    )
    issues = V._validate_invariants_pass2(sp, "thorough")
    assert issues == [], (
        f"Pass 2 section present + summary flags — should pass cleanly, "
        f"got: {issues}"
    )


def test_validator_soft_pass_in_non_thorough_modes(tmp_path: Path):
    """Light/Core skip Pass 2 entirely. Validator must be a no-op in
    those modes — even with weirdly-shaped semantic_invariants.md."""
    import plamen_validators as V
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "semantic_invariants.md", "garbage content")
    assert V._validate_invariants_pass2(sp, "core") == []
    assert V._validate_invariants_pass2(sp, "light") == []


def test_validator_never_returns_hard_issue(tmp_path: Path):
    """Critical: the validator must NEVER halt the audit. All failure
    modes return empty list. The audit user explicitly required no
    new halt paths from this fix."""
    import plamen_validators as V
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    # Case 1: file missing entirely
    assert V._validate_invariants_pass2(sp, "thorough") == []
    # Case 2: file exists but no Pass 2 section
    _write(sp / "semantic_invariants.md", "# Pass 1 only\n")
    assert V._validate_invariants_pass2(sp, "thorough") == []
    # Case 3: file unreadable (best-effort write a directory in place)
    # We can't easily make it unreadable on Windows without permission
    # changes; the function's try/except handles Exception either way.
    # The relevant check: existing tests above cover the common cases.


def test_validator_writes_degraded_sentinel_on_missing_pass2(tmp_path: Path):
    """When Pass 2 didn't produce output, the validator should leave a
    sentinel file so downstream phases / debugging can SEE that Pass 2
    was supposed to run but didn't. This is observability, NOT a halt."""
    import plamen_validators as V
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "semantic_invariants.md", "# Pass 1 only — no Pass 2\n")
    V._validate_invariants_pass2(sp, "thorough")
    sentinel = sp / "invariants_p2.degraded"
    assert sentinel.exists(), (
        "Missing Pass 2 output should write `invariants_p2.degraded` "
        "sentinel for observability. Pipeline continues, but the gap "
        "is visible in scratchpad."
    )
    text = sentinel.read_text(encoding="utf-8")
    assert "INVARIANTS_P2_DEGRADED" in text
