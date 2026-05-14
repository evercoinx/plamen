"""Regression tests for the depth iter2/iter3 filename-drift fix.

Background: the driver's depth manifest instructs the orchestrator to write
`depth_iter2_*_findings.md`, but in practice the LLM frequently drops the
`_findings` segment and writes `depth_iter2_state_trace.md` etc. Multiple
gates and parsers glob for the strict suffix and miss those files.

Live DODO audit (May 2026) burned an entire opus depth retry because of
this exact mismatch — same class as v2.3.4's perturbation_findings fix.

These tests lock in three guarantees:
  1. The tolerant glob in `_validate_confidence_iter2_mandatory` accepts
     both the canonical `depth_iter2_*_findings.md` form AND the de-facto
     `depth_iter2_*.md` form.
  2. The canonicalizer `_canonicalize_depth_iter_filenames` renames the
     non-canonical form to canonical so downstream strict-pattern
     consumers (inventory parsers, never-cut gate, prompt builders)
     also see them.
  3. The canonicalizer is idempotent and does not clobber existing
     canonical files.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
import plamen_validators as V  # noqa: E402


def _write(p: Path, body: str = "x" * 200) -> None:
    p.write_text(body, encoding="utf-8")


def _seed_uncertain_finding_inputs(scratchpad: Path) -> None:
    """Minimal scratchpad state so `_validate_confidence_iter2_mandatory`
    reaches the iter2 artifact-existence check (rather than short-circuiting
    on missing inputs or zero uncertain findings).
    """
    # `_parse_confidence_scores_permissive` normalizes header cells to
    # `findingid` and `composite` (strips non-alphanumerics, lowercases).
    # Anything else fails the column-detection check.
    _write(
        scratchpad / "confidence_scores.md",
        "# Confidence Scores\n\n"
        "| Finding ID | Composite |\n"
        "|------------|-----------|\n"
        "| INV-1      | 0.45      |\n",
    )
    _write(
        scratchpad / "findings_inventory.md",
        "# Findings Inventory\n\n"
        "## Findings\n\n"
        "### Finding [INV-1]: example issue\n"
        "**Severity**: Medium\n"
        "**Location**: A.sol:L1\n"
        "**Description**: example\n",
    )


def test_iter2_gate_tolerates_canonical_filename(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _seed_uncertain_finding_inputs(sp)
    _write(sp / "depth_iter2_state_trace_findings.md")
    # Should accept the canonical name and emit zero issues.
    issues = V._validate_confidence_iter2_mandatory(sp)
    assert issues == [], f"Canonical iter2 name rejected: {issues}"


def test_iter2_gate_tolerates_non_canonical_filename(tmp_path: Path):
    """The actual failure mode from the DODO audit: LLM dropped _findings."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _seed_uncertain_finding_inputs(sp)
    _write(sp / "depth_iter2_state_trace.md")
    # This is the regression — pre-fix the gate would emit
    # "no iter2/DA artifacts exist" and trigger a depth retry.
    issues = V._validate_confidence_iter2_mandatory(sp)
    assert issues == [], (
        "Non-canonical iter2 filename should satisfy the gate; "
        f"instead got: {issues}"
    )


def test_iter2_gate_tolerates_iter3_only(tmp_path: Path):
    """iter3 output implies iter2 ran (driver enforces ordering)."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _seed_uncertain_finding_inputs(sp)
    _write(sp / "depth_iter3_targeted.md")
    issues = V._validate_confidence_iter2_mandatory(sp)
    assert issues == [], f"iter3 should satisfy iter2 gate: {issues}"


def test_iter2_gate_still_fails_on_missing_artifacts(tmp_path: Path):
    """Sanity: the gate MUST still fail when iter2 was actually skipped."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _seed_uncertain_finding_inputs(sp)
    # No depth_iter2_* or depth_iter3_* files written.
    issues = V._validate_confidence_iter2_mandatory(sp)
    assert issues, (
        "Gate must still fail when no iter2 artifacts exist; "
        "otherwise the fix would silently permit skipped iterations"
    )
    assert "iter2" in issues[0].lower()


def test_canonicalizer_renames_non_canonical_iter2(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "depth_iter2_state_trace.md", "content-A")
    _write(sp / "depth_iter2_edge_case.md", "content-B")
    renamed = D._canonicalize_depth_iter_filenames(sp)
    assert len(renamed) == 2, f"Expected 2 renames, got {renamed}"
    assert (sp / "depth_iter2_state_trace_findings.md").exists()
    assert (sp / "depth_iter2_edge_case_findings.md").exists()
    assert not (sp / "depth_iter2_state_trace.md").exists()
    assert not (sp / "depth_iter2_edge_case.md").exists()
    # Content survives the rename
    assert (
        sp / "depth_iter2_state_trace_findings.md"
    ).read_text(encoding="utf-8") == "content-A"


def test_canonicalizer_skips_already_canonical(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "depth_iter2_state_trace_findings.md", "already-canonical")
    renamed = D._canonicalize_depth_iter_filenames(sp)
    assert renamed == [], "Should not rename already-canonical files"
    assert (sp / "depth_iter2_state_trace_findings.md").exists()


def test_canonicalizer_does_not_clobber_existing_canonical(tmp_path: Path):
    """If both forms exist (rare but possible after a partial retry),
    prefer the canonical file and leave the non-canonical untouched."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "depth_iter2_state_trace.md", "non-canonical-content")
    _write(sp / "depth_iter2_state_trace_findings.md", "canonical-content")
    renamed = D._canonicalize_depth_iter_filenames(sp)
    assert renamed == [], f"Must not clobber existing canonical: {renamed}"
    assert (
        sp / "depth_iter2_state_trace_findings.md"
    ).read_text(encoding="utf-8") == "canonical-content"
    assert (sp / "depth_iter2_state_trace.md").exists()  # non-canonical retained


def test_canonicalizer_handles_iter3_too(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "depth_iter3_targeted.md", "iter3-content")
    renamed = D._canonicalize_depth_iter_filenames(sp)
    assert len(renamed) == 1
    assert (sp / "depth_iter3_targeted_findings.md").exists()
