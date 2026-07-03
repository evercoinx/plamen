"""Tests for v2.0.10 P4 + P5 — prompt-source fixes + receipt coverage gates.

Scope of this ship:
  P4.2 — Breadth + rescan forbidden-output block (not just depth).
  P4.4 — sc_semantic_dedup prompt removes "PASSTHROUGH is OK" softening,
         requires 100% candidate-pair disposition.
  P5   — Dedup decision coverage gate (WARNING-class first ship; promotes
         per the plan's 3-stage path).

Other P4 items (P4.1 skeptic schema already aligned in P3.2; P4.3
perturbation obligations / P4.5 opengrep sharding / P4.6 step-trace promote /
P4.7 verify skeleton pre-write) are deferred to a v2.0.11 follow-up; each
needs its own targeted prompt-template work that doesn't share machinery
with this ship.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_prompt import _render_forbidden_output_block  # noqa: E402
from plamen_validators import _check_dedup_decision_coverage  # noqa: E402


# ---------------------------------------------------------------------------
# P4.2 — Breadth + rescan forbidden-output block
# ---------------------------------------------------------------------------


def test_p42_breadth_has_output_allowlist_block():
    """An observed percontract leak class is the canonical target. Breadth's
    forbidden-output block now explicitly names the rescan/percontract
    files (so the LLM can't 'helpfully' write them during breadth)."""
    block = _render_forbidden_output_block("breadth")
    assert "OUTPUT ALLOWLIST (HARD)" in block
    assert "spawn_manifest.md" in block
    assert "manifest rows" in block
    assert "analysis_rescan" not in block
    assert "analysis_percontract" not in block


def test_p42_breadth_block_does_not_list_later_phase_artifacts():
    """Breadth's block also names later-phase artifacts (depth, chain,
    verify, report) for completeness — the LLM can drift into any of them."""
    block = _render_forbidden_output_block("breadth")
    for token in (
        "depth_*_findings.md", "hypotheses.md", "verify_*.md",
        "findings_inventory", "semantic_invariants.md",
        "report_index.md", "AUDIT_REPORT.md",
    ):
        assert token not in block, f"breadth block leaked token: {token}"


def test_p42_rescan_block_has_output_allowlist():
    """Rescan also gets a forbidden-output block — different namespace
    from breadth (owns rescan_*.md + percontract_*.md, must not touch
    breadth's analysis_<focus_area>.md)."""
    block = _render_forbidden_output_block("rescan")
    assert "OUTPUT ALLOWLIST (HARD)" in block
    assert "current prompt and manifest" in block
    assert "depth_*_findings.md" not in block
    assert "hypotheses.md" not in block


def test_p42_depth_block_preserved_for_non_regression():
    """Non-regression: depth's existing forbidden-output block is unchanged."""
    block = _render_forbidden_output_block("depth")
    assert "OUTPUT ALLOWLIST (HARD)" in block
    assert "Expected Output Contract" in block
    assert "hypotheses.md" not in block
    assert "verify_*.md" not in block


def test_p42_non_task_phases_get_no_block():
    """Phases without a defined forbidden-set return ""."""
    assert _render_forbidden_output_block("inventory_chunk_a") == ""
    assert _render_forbidden_output_block("invariants") == ""
    assert _render_forbidden_output_block("instantiate") == ""


# ---------------------------------------------------------------------------
# P4.4 — sc_semantic_dedup prompt softening removed
# ---------------------------------------------------------------------------


def test_p44_dedup_prompt_no_longer_says_passthrough_is_crash_safety_only():
    """The original phrasing 'A PASSTHROUGH ... status means "crash-safety
    net only"' gave the LLM permission to skip semantic review. v2.0.10
    removes that softening and replaces with explicit '100% coverage required'.
    """
    text = (
        SCRIPTS_DIR.parent / "prompts" / "shared" / "v2" / "phase4e-semantic-dedup.md"
    ).read_text(encoding="utf-8")
    # The old "crash-safety net only" phrasing as a permission to skip
    # must be gone.
    assert '"crash-safety net only."' not in text, (
        "Old softening language still present"
    )
    # The new contract MUST be present. Match the actual file format
    # (PASSTHROUGH is wrapped in backticks in the prompt header).
    assert "`PASSTHROUGH` IS NOT A COMPLETION STATE" in text
    assert "100% coverage" in text


# ---------------------------------------------------------------------------
# P5 — Dedup decision coverage gate
# ---------------------------------------------------------------------------


def test_p5_dedup_coverage_full_coverage_no_issues(tmp_path):
    """Every candidate pair has a disposition row → no issues."""
    (tmp_path / "dedup_candidate_pairs.md").write_text(
        "| Pair | A | B | Similarity |\n"
        "|------|---|---|------------|\n"
        "| 1 | INV-001 | INV-002 | 0.85 |\n"
        "| 2 | INV-003 | INV-004 | 0.72 |\n",
        encoding="utf-8",
    )
    (tmp_path / "dedup_decisions.md").write_text(
        "| Pair | Disposition | Reason |\n"
        "|------|-------------|--------|\n"
        "| 1 | MERGE | same root cause |\n"
        "| 2 | KEEP SEPARATE | different functions |\n",
        encoding="utf-8",
    )
    assert _check_dedup_decision_coverage(tmp_path) == []


def test_p5_dedup_coverage_partial_flagged(tmp_path):
    """Some pairs lack a disposition → issue (WARNING-class)."""
    (tmp_path / "dedup_candidate_pairs.md").write_text(
        "| Pair | A | B |\n"
        "|------|---|---|\n"
        "| 1 | INV-001 | INV-002 |\n"
        "| 2 | INV-003 | INV-004 |\n"
        "| 3 | INV-005 | INV-006 |\n",
        encoding="utf-8",
    )
    (tmp_path / "dedup_decisions.md").write_text(
        "| Pair | Disposition |\n"
        "|------|-------------|\n"
        "| 1 | MERGE |\n",
        encoding="utf-8",
    )
    issues = _check_dedup_decision_coverage(tmp_path)
    assert len(issues) == 1
    assert "1/3" in issues[0]
    assert "unaccounted" in issues[0]


def test_p5_dedup_coverage_passthrough_unchanged_flagged(tmp_path):
    """PASSTHROUGH stub left unchanged → only counts as accounted if it
    actually contains the PASSTHROUGH-keyword disposition. Coverage
    detects the gap between unaccounted pairs and the stub.
    """
    (tmp_path / "dedup_candidate_pairs.md").write_text(
        "| Pair | A | B |\n"
        "|------|---|---|\n"
        "| 1 | INV-001 | INV-002 |\n"
        "| 2 | INV-003 | INV-004 |\n",
        encoding="utf-8",
    )
    # PASSTHROUGH stub: single status line, NO per-pair rows.
    (tmp_path / "dedup_decisions.md").write_text(
        "# Dedup Decisions\n\nStatus: PASSTHROUGH\n",
        encoding="utf-8",
    )
    issues = _check_dedup_decision_coverage(tmp_path)
    # No table rows in decisions → 0 accounted → all unaccounted.
    assert len(issues) == 1
    assert "0/2" in issues[0]


def test_p5_dedup_coverage_no_pairs_no_issues(tmp_path):
    """No candidate pairs (empty file) → no coverage check needed."""
    (tmp_path / "dedup_candidate_pairs.md").write_text(
        "# No pairs\n\n", encoding="utf-8"
    )
    (tmp_path / "dedup_decisions.md").write_text(
        "Status: PASSTHROUGH\n", encoding="utf-8"
    )
    assert _check_dedup_decision_coverage(tmp_path) == []


def test_p5_dedup_coverage_missing_files_no_issues(tmp_path):
    """Either file missing → silent (no false halts on legacy runs)."""
    assert _check_dedup_decision_coverage(tmp_path) == []
