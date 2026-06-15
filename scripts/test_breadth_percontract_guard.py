"""Ship 8.7-lite: per-contract leak guard for the breadth phase.

Two concerns:
  1. The MODEL-FACING (rendered) breadth prompt must not LEAK downstream
     artifact names at all. Negative examples are still suggestions under
     compaction pressure, so the prompt is allowlist-only.
  2. A per-contract file written during breadth is a blocking containment
     violation and is NOT ingested as a breadth output.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_prompt as p  # noqa: E402
import plamen_validators as V  # noqa: E402
from plamen_types import SC_PHASES  # noqa: E402


def _build_breadth_prompt(tmp_root: Path) -> str:
    scratch = tmp_root / ".scratchpad"
    project = tmp_root / "proj"
    scratch.mkdir(parents=True)
    project.mkdir(parents=True)
    v1 = scratch / "v1.md"
    v1.write_text(
        "## Phase 3: Parallel Breadth Analysis\n\nSpawn agents.\n"
        + ("BODY " * 80),
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
    return p.build_phase_prompt(v1, phase, cfg)


# ---------------------------------------------------------------------------
# Rendered-prompt assertions (the precision note: test the runtime prompt)
# ---------------------------------------------------------------------------


def test_rendered_breadth_prompt_strips_buildstrip_block(tmp_path: Path):
    """The BUILD-STRIP comment (which carries the test-only literal tokens
    `analysis_rescan_*.md analysis_percontract_*.md ...`) MUST be stripped
    from the model-facing prompt. Verified on the rendered prompt, not the
    source file."""
    rendered = _build_breadth_prompt(tmp_path)
    assert "<!-- BUILD-STRIP:" not in rendered, (
        "BUILD-STRIP comment leaked into the model-facing breadth prompt"
    )
    # The unique BUILD-STRIP prose must not survive either.
    assert "raw contract tokens for standalone contract tests only" not in rendered


def test_rendered_breadth_prompt_has_no_downstream_artifact_tokens(
    tmp_path: Path,
):
    rendered = _build_breadth_prompt(tmp_path)
    forbidden = [
        "analysis_percontract",
        "analysis_rescan",
        "per-contract",
        "re-scan",
        "later phase",
        "next phase",
        "downstream",
        "Phase 3b",
        "Phase 3c",
        "Phase 4",
        "Phase 5",
        "Phase 6",
        "findings_inventory.md",
        "semantic_invariants.md",
        "verify_",
        "report_",
        "AUDIT_REPORT.md",
    ]
    hits = [token for token in forbidden if token.lower() in rendered.lower()]
    assert not hits


def test_rendered_breadth_prompt_scopes_to_single_assigned_output(tmp_path: Path):
    """The Output Discipline scoping (write ONLY the single assigned
    analysis_<focus>.md) is present in the rendered prompt -- the positive
    instruction that backs deterministic containment."""
    rendered = _build_breadth_prompt(tmp_path)
    assert "single assigned" in rendered
    assert "OUTPUT ALLOWLIST" in rendered


# ---------------------------------------------------------------------------
# Quarantine / non-ingestion (row-status level)
# ---------------------------------------------------------------------------


def test_percontract_file_is_not_a_breadth_row(tmp_path: Path):
    """A stray analysis_percontract_*.md present during breadth is NOT a
    manifest output and therefore never appears in
    compute_breadth_row_statuses -- it cannot satisfy, pollute, or fail
    the breadth gate."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    (sp / "_audit_started_with_markers.json").write_text("{}", encoding="utf-8")
    # Minimal 2-row manifest.
    (sp / "spawn_manifest.md").write_text(
        "# Spawn Manifest\n\n"
        "| Template | Required? | Agent ID | Focus Area | Expected Output | Status | Type |\n"
        "|----------|-----------|----------|------------|-----------------|--------|------|\n"
        "| TPL | YES | B1 | core_state | analysis_core_state.md | PENDING | agent |\n"
        "| TPL | YES | B2 | access_control | analysis_access_control.md | PENDING | agent |\n",
        encoding="utf-8",
    )

    def _complete(name, owner):
        return (
            f"<!-- PLAMEN_ARTIFACT: {name} -->\n"
            f"<!-- PLAMEN_OWNER: {owner} -->\n"
            "<!-- PLAMEN_PHASE: breadth -->\n"
            f"<!-- AGENT_ROW: {owner} -->\n"
            f"<!-- EXPECTED_OUTPUT: {name} -->\n"
            "<!-- PLAMEN_STATUS: COMPLETE -->\n"
            f"# {name}\n\n## Finding [X-1]: f\n" + ("body " * 40) + "\n"
        )

    (sp / "analysis_core_state.md").write_text(
        _complete("analysis_core_state.md", "B1"),
        encoding="utf-8",
    )
    (sp / "analysis_access_control.md").write_text(
        _complete("analysis_access_control.md", "B2"),
        encoding="utf-8",
    )
    # Stray per-contract leak.
    (sp / "analysis_percontract_3.md").write_text("# stray\n" + ("x " * 100), encoding="utf-8")

    phase = next(ph for ph in SC_PHASES if ph.name == "breadth")
    # Build a fresh Phase with min_artifact_bytes=200 to match production gate.
    from plamen_types import Phase
    bphase = Phase(
        name="breadth",
        section_markers=["Phase 3"],
        expected_artifacts=["analysis_*.md"],
        base_timeout_s=60,
        min_artifact_bytes=200,
    )
    statuses = V.compute_breadth_row_statuses(sp, bphase)
    names = {s["name"] for s in statuses}
    assert names == {"analysis_core_state.md", "analysis_access_control.md"}
    assert "analysis_percontract_3.md" not in names
    # And the gate passes (the stray file does not interfere).
    passed, missing = V.gate_passes(sp, str(tmp_path / "proj"), bphase)
    assert passed is True, f"unexpected missing: {missing!r}"


def test_breadth_protects_percontract_as_blocking_runtime_violation():
    patterns = V._protected_phase_write_patterns("breadth")
    assert "analysis_percontract_*.md" in patterns
    assert "analysis_rescan_*.md" in patterns
