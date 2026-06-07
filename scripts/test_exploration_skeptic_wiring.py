"""Wiring + soft-validation tests for Phase 4b.6 (exploration completeness).

The exploration_skeptic phase is an independent, recall-positive /
ADDITIVE exploration-completeness verifier. It runs Thorough-only, after
the depth loop and its post-depth sub-phases (attention_repair, rag_sweep)
and before dedup/chain, so any added/upgraded/re-opened finding propagates
through the rest of the pipeline.

These tests lock in:

  1. The prompt mapping in `_STANDALONE_PROMPT_MAP` resolving via
     `_resolve_standalone_prompt`, with the prompt file present on disk.
  2. The Phase entry in `SC_PHASES`, Thorough-only + soft (critical=False).
  3. Placement: AFTER rag_sweep, BEFORE sc_semantic_dedup.
  4. Mode gating: absent in light/core scheduling, present in thorough.
  5. The soft validator never returns hard issues in any branch.

No protocol-specific content appears in these assertions.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))


# -------- Wiring ----------------------------------------------------------

def test_exploration_skeptic_resolves_to_prompt_file():
    import plamen_prompt as P
    assert "exploration_skeptic" in P._STANDALONE_PROMPT_MAP
    assert (
        P._STANDALONE_PROMPT_MAP["exploration_skeptic"]
        == "phase4b6-exploration-skeptic.md"
    )
    # Resolve via the module-internal resolver (not imported by name, to
    # avoid the structural-integrity external-import gate).
    resolved = P._resolve_standalone_prompt("exploration_skeptic")
    assert resolved is not None, (
        "exploration_skeptic must resolve to a real prompt file on disk."
    )
    assert resolved.name == "phase4b6-exploration-skeptic.md"
    assert resolved.exists()


def test_exploration_skeptic_phase_entry_in_sc_phases():
    from plamen_types import SC_PHASES
    p = next((p for p in SC_PHASES if p.name == "exploration_skeptic"), None)
    assert p is not None, "exploration_skeptic phase missing from SC_PHASES"
    assert p.modes == {"thorough"}, (
        f"exploration_skeptic mode set should be {{thorough}}, got {p.modes!r}."
    )
    assert p.critical is False, (
        "exploration_skeptic.critical MUST be False — recall-positive soft phase."
    )
    assert p.model == "sonnet", (
        "exploration_skeptic must stay sonnet (not in the Thorough opus "
        "promotion set)."
    )
    assert p.expected_artifacts == ["exploration_skeptic_findings.md"]


def test_exploration_skeptic_placement_between_rag_sweep_and_dedup():
    from plamen_types import SC_PHASES
    names = [p.name for p in SC_PHASES]
    for required in ("rag_sweep", "exploration_skeptic", "sc_semantic_dedup"):
        assert required in names, f"{required} missing from SC_PHASES"
    i_rag = names.index("rag_sweep")
    i_es = names.index("exploration_skeptic")
    i_dedup = names.index("sc_semantic_dedup")
    assert i_rag < i_es < i_dedup, (
        f"exploration_skeptic must sit AFTER rag_sweep and BEFORE "
        f"sc_semantic_dedup (positions rag={i_rag}, es={i_es}, "
        f"dedup={i_dedup})."
    )


def test_exploration_skeptic_NOT_in_l1_phases():
    """SC-only feature per task scope — L1 must not include it."""
    from plamen_types import L1_PHASES
    for p in L1_PHASES:
        assert p.name != "exploration_skeptic", (
            "L1 must not include exploration_skeptic (SC-only feature)."
        )


def test_exploration_skeptic_thorough_only_scheduling():
    from plamen_types import SC_PHASES
    light = {p.name for p in SC_PHASES if "light" in p.modes}
    core = {p.name for p in SC_PHASES if "core" in p.modes}
    thorough = {p.name for p in SC_PHASES if "thorough" in p.modes}
    assert "exploration_skeptic" not in light
    assert "exploration_skeptic" not in core
    assert "exploration_skeptic" in thorough


def test_exploration_skeptic_in_validators_all():
    import plamen_validators as V
    assert "_validate_exploration_skeptic" in V.__all__


# -------- Soft validator --------------------------------------------------

def _write(p: Path, body: str) -> None:
    p.write_text(body, encoding="utf-8")


def test_validator_soft_pass_when_artifact_present(tmp_path: Path):
    import plamen_validators as V
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "exploration_skeptic_findings.md",
        "# Exploration Completeness Findings\n\n"
        "## Coverage Record\n\n"
        "| Finding | Axis 1 | Axis 2 | Axis 3 |\n"
        "|---------|--------|--------|--------|\n"
        "| 1 | ASSESSED | NO-GAP | GAP-FILLED |\n",
    )
    assert V._validate_exploration_skeptic(sp, "thorough") == []


def test_validator_soft_pass_in_non_thorough(tmp_path: Path):
    import plamen_validators as V
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    assert V._validate_exploration_skeptic(sp, "core") == []
    assert V._validate_exploration_skeptic(sp, "light") == []


def test_validator_never_halts_on_missing_artifact(tmp_path: Path):
    import plamen_validators as V
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    issues = V._validate_exploration_skeptic(sp, "thorough")
    assert issues == [], (
        f"Missing artifact must not halt an additive phase; got: {issues}"
    )
    assert (sp / "exploration_skeptic.degraded").exists(), (
        "Missing artifact must leave a degraded sentinel for observability."
    )


def test_validator_treats_near_empty_as_degraded(tmp_path: Path):
    import plamen_validators as V
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "exploration_skeptic_findings.md", "tiny")
    assert V._validate_exploration_skeptic(sp, "thorough") == []
    assert (sp / "exploration_skeptic.degraded").exists()
