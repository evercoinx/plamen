"""Regression tests for the depth iter2/iter3 filename-drift fix.

Background: the driver's depth manifest instructs the orchestrator to write
`depth_iter2_*_findings.md`, but in practice the LLM frequently drops the
`_findings` segment and writes `depth_iter2_state_trace.md` etc. Multiple
gates and parsers glob for the strict suffix and miss those files.

A prior live audit burned an entire opus depth retry because of
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
    """The actual failure mode from a prior audit: LLM dropped _findings."""
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


def test_canonicalizer_handles_da_iter_namespace(tmp_path: Path):
    """Actual old-run failure: DA outputs lacked the depth_ prefix, so the
    depth gate missed them and retried the phase."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "da_iter2_edge_case_findings.md",
        "<!-- PLAMEN_ARTIFACT: da_iter2_edge_case_findings.md -->\n"
        "<!-- EXPECTED_OUTPUT: da_iter2_edge_case_findings.md -->\n"
        "complete DA content",
    )
    _write(sp / "da_iter3_findings.md", "iter3-content")

    renamed = D._canonicalize_depth_iter_filenames(sp)

    assert "da_iter2_edge_case_findings.md -> depth_da_iter2_edge_case_findings.md" in renamed
    assert "da_iter3_findings.md -> depth_da_iter3_findings.md" in renamed
    target = sp / "depth_da_iter2_edge_case_findings.md"
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "PLAMEN_ARTIFACT: depth_da_iter2_edge_case_findings.md" in text
    assert "EXPECTED_OUTPUT: depth_da_iter2_edge_case_findings.md" in text
    assert not (sp / "da_iter2_edge_case_findings.md").exists()


def test_iter2_gate_tolerates_da_iter_namespace(tmp_path: Path):
    """Defense in depth: even if canonicalization cannot rename, the iter2
    gate recognizes DA work and does not force a full depth retry."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _seed_uncertain_finding_inputs(sp)
    _write(sp / "da_iter2_state_trace_findings.md", "DA work")

    issues = V._validate_confidence_iter2_mandatory(sp)

    assert issues == []


# --- Widened canonicalizer: token-decomposition variant matrix ------------
# A prior audit burned a $4 opus depth retry because the
# orchestrator wrote `depth_state_trace_iteration2_findings.md` — the
# iteration token mid-string and spelled out, which neither the prefix-only
# canonicalizer nor the iter2 gate glob recognized. The canonicalizer was
# rewritten to DECOMPOSE the filename (find the iteration token, lift N,
# treat the rest as role) rather than enumerate variants. These tests lock
# in every plausible ordering/spelling the orchestrator has produced or
# could produce.


def test_canonicalizer_variant_matrix(tmp_path: Path):
    """Every iteration-token variant rewrites to depth_iter{N}_{role}_findings.md."""
    cases = {
        # role-first, iteration spelled out (the exact prior failure)
        "depth_state_trace_iteration2_findings.md":
            "depth_iter2_state_trace_findings.md",
        # iteration token in prefix position, spelled out
        "depth_iteration2_token_flow_findings.md":
            "depth_iter2_token_flow_findings.md",
        # role-first, abbreviated, no _findings suffix
        "depth_edge_case_iter2.md":
            "depth_iter2_edge_case_findings.md",
        # underscore before the digit
        "depth_external_iter_2_findings.md":
            "depth_iter2_external_findings.md",
        # iter3, role-first, spelled out
        "depth_targeted_iteration3.md":
            "depth_iter3_targeted_findings.md",
        # canonical prefix, missing _findings (legacy variant 1)
        "depth_iter2_state_trace.md":
            "depth_iter2_state_trace_findings.md",
    }
    for src_name, expected in cases.items():
        case_dir = tmp_path / src_name.replace(".md", "")
        case_dir.mkdir()
        _write(case_dir / src_name, f"content-of-{src_name}")
        D._canonicalize_depth_iter_filenames(case_dir)
        produced = sorted(p.name for p in case_dir.glob("*.md"))
        assert produced == [expected], (
            f"{src_name}: expected [{expected}], got {produced}"
        )


def test_canonicalizer_protects_non_iter_depth_files(tmp_path: Path):
    """iter1 base files, DA-form files, and lifecycle files are never renamed."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    protected = [
        # iter1 base findings — no iteration token
        "depth_state_trace_findings.md",
        "depth_token_flow_findings.md",
        "depth_edge_case_findings.md",
        "depth_external_findings.md",
        # Devil's-Advocate iteration form — separate recognized namespace
        "depth_da_state_trace_findings.md",
        "depth_da3_token_flow_findings.md",
        # depth lifecycle / coverage files
        "depth_exit.md",
        "depth_candidates.md",
        "depth_promotion_receipt.md",
        "depth_coverage_state_trace.md",
        # already canonical
        "depth_iter2_state_trace_findings.md",
    ]
    for n in protected:
        _write(sp / n, "protected")
    renamed = D._canonicalize_depth_iter_filenames(sp)
    assert renamed == [], f"protected files were renamed: {renamed}"
    for n in protected:
        assert (sp / n).exists(), f"{n} disappeared"


def test_iter2_gate_tolerates_midstring_iteration_variant(tmp_path: Path):
    """The iter2-mandatory gate's defense-in-depth glob accepts the
    mid-string `_iteration2_` variant even if canonicalization didn't run."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _seed_uncertain_finding_inputs(sp)
    # Pre-canonicalization filename — gate must still recognize it
    _write(sp / "depth_state_trace_iteration2_findings.md", "iter2 work")
    issues = V._validate_confidence_iter2_mandatory(sp)
    assert issues == [], (
        f"gate should accept mid-string iteration2 variant, got: {issues}"
    )


def test_expected_roles_excludes_uncanonicalized_iteration_variant(tmp_path: Path):
    """`_expected_depth_agent_roles` must not mis-parse an un-canonicalized
    `depth_<role>_iteration2_findings.md` as a phantom role.

    Direct regression for a prior failure log's second symptom:
      `[depth] graph consumption: depth_edge_case_iteration2 references 0/4`
    — `iteration2` does not contain the substring `iter2`, so the exclusion
    token list must list both spellings.
    """
    from plamen_parsers import _expected_depth_agent_roles

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    # Canonical iter1 base files — these ARE real roles
    _write(sp / "depth_state_trace_findings.md", "iter1")
    _write(sp / "depth_token_flow_findings.md", "iter1")
    # Un-canonicalized iteration2/3 variants — must NOT become roles
    _write(sp / "depth_edge_case_iteration2_findings.md", "iter2")
    _write(sp / "depth_external_iteration3_findings.md", "iter3")
    # Canonical iter2 form — also excluded
    _write(sp / "depth_iter2_state_trace_findings.md", "iter2-canonical")

    roles = _expected_depth_agent_roles(sp)
    assert "state_trace" in roles
    assert "token_flow" in roles
    assert not any("iteration2" in r for r in roles), (
        f"phantom iteration2 role leaked: {roles}"
    )
    assert not any("iteration3" in r for r in roles), (
        f"phantom iteration3 role leaked: {roles}"
    )
    assert "edge_case_iteration2" not in roles
    assert "external_iteration3" not in roles
