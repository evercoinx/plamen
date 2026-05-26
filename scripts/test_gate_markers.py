"""Tests for Ship 3 of the artifact-complete PTY supervision plan.

Validates the marker-aware breadth gate in `plamen_validators.gate_passes`:

  - fresh-audit scratchpads (sentinel present) BLOCK missing, stub, unmarked,
    in_progress, and structurally-incomplete artifacts.
  - legacy/resumed scratchpads (sentinel absent) keep size-based semantics
    and emit warnings for unmarked / in-progress artifacts instead of
    failing the gate.

Test numbers 12-18 match the plan's `Tests to add` section for
`test_gate_markers.py`. The gate is exercised through the public
`plamen_driver.gate_passes` re-export pattern used by sibling tests
(test_breadth_manifest_regression.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402


_SENTINEL = "_audit_started_with_markers.json"


def _write_manifest(sp: Path, rows: list[tuple[str, str]]) -> None:
    """Write a minimal spawn_manifest.md that parse_breadth_manifest_outputs
    accepts. `rows` is a list of (agent_id, focus_area) pairs; the parser
    derives `analysis_<focus_area>.md` filenames from these.
    """
    header = (
        "# Spawn Manifest\n\n"
        "| Template | Required? | Agent ID | Focus Area | Expected Output | Status | Type |\n"
        "|----------|-----------|----------|------------|-----------------|--------|------|\n"
    )
    body_lines = []
    for agent_id, focus in rows:
        body_lines.append(
            f"| TPL | YES | {agent_id} | {focus} | analysis_{focus}.md | PENDING | agent |"
        )
    (sp / "spawn_manifest.md").write_text(header + "\n".join(body_lines) + "\n", encoding="utf-8")


def _write_complete_artifact(
    sp: Path,
    filename: str,
    agent_id: str = "B1",
    *,
    body_padding_bytes: int = 800,
    findings_count: int = 2,
    include_findings_heading: bool = True,
    include_no_findings_rationale: bool = False,
    include_obligation_receipts: bool = True,
    include_placeholders: bool = False,
) -> Path:
    """Write a COMPLETE-marked artifact that passes structural checks by
    default. Optional kwargs flip specific structural properties for tests
    that exercise the structural_fail bucket."""
    lines = [
        f"<!-- PLAMEN_ARTIFACT: {filename} -->",
        f"<!-- PLAMEN_OWNER: {agent_id} -->",
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->",
        "<!-- PLAMEN_PHASE: breadth -->",
        "<!-- PLAMEN_VERSION: 1 -->",
        f"<!-- AGENT_ROW: {agent_id} -->",
        f"<!-- EXPECTED_OUTPUT: {filename} -->",
        "",
        f"# {filename}",
        "",
    ]
    if include_findings_heading:
        lines += ["## Findings", ""]
        for i in range(max(findings_count, 0)):
            lines.append(f"[{agent_id}-{i+1}] Real finding {i+1} with body content.")
        lines.append("")
    if include_no_findings_rationale:
        lines += ["## No Findings", "", "Reasoned-through rationale here.", ""]
    if include_placeholders:
        lines += ["TODO: write this section later", ""]
    if include_obligation_receipts:
        lines += [
            "## Obligation Receipts -- opengrep_findings.md",
            "",
            "| Row | Rule | Location | Addressed By | Notes |",
            "|-----|------|----------|--------------|-------|",
            "| 1 | sample-rule | Foo.sol:42 | (none) | by design |",
            "",
        ]
    # Pad to ensure size > min_artifact_bytes
    padding = "x" * max(body_padding_bytes, 0)
    lines.append(padding)
    lines.append("<!-- PLAMEN_STATUS: COMPLETE -->")
    lines.append(f"<!-- PLAMEN_FINDINGS_COUNT: {findings_count} -->")
    p = sp / filename
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _write_in_progress_artifact(sp: Path, filename: str, agent_id: str = "B1") -> Path:
    """Write a marker-bearing artifact that stopped at IN_PROGRESS."""
    body = (
        f"<!-- PLAMEN_ARTIFACT: {filename} -->\n"
        f"<!-- PLAMEN_OWNER: {agent_id} -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: breadth -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n"
        f"# {filename}\n\n"
        "## Findings\n\n"
        + ("partial work-in-progress body content " * 30)
        + "\n"
    )
    p = sp / filename
    p.write_text(body, encoding="utf-8")
    return p


def _write_legacy_unmarked_artifact(sp: Path, filename: str) -> Path:
    """Write a substantive artifact with no PLAMEN markers (legacy shape)."""
    body = (
        f"# Legacy Analysis: {filename}\n\n"
        "## Findings\n\n"
        + ("legacy artifact body content from before marker contract " * 30)
        + "\n"
    )
    p = sp / filename
    p.write_text(body, encoding="utf-8")
    return p


def _breadth_phase() -> object:
    """Build a breadth-shaped Phase with min_artifact_bytes=200, matching
    the plan's anti-skeleton size floor. Returns a Phase-compatible object;
    SC_PHASES already names breadth but uses default min_bytes, so we
    construct one explicitly for tightness."""
    return D.Phase(
        name="breadth",
        section_markers=["Phase 3"],
        expected_artifacts=["analysis_*.md"],
        base_timeout_s=60,
        min_artifact_bytes=200,
    )


def _touch_sentinel(sp: Path) -> None:
    (sp / _SENTINEL).write_text(
        '{"schema_version": 1, "started_at": "test"}', encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Test 12 -- fresh-audit blocking on unmarked artifact
# ---------------------------------------------------------------------------


def test_gate_fresh_rejects_unmarked(tmp_path: Path, caplog):
    """A fresh-audit scratchpad (sentinel present) MUST NOT pass an
    artifact that lacks any PLAMEN markers, even if it is substantive
    and well-sized. The marker requirement is blocking from day one."""
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    _write_manifest(sp, [("B1", "core_state")])
    _write_legacy_unmarked_artifact(sp, "analysis_core_state.md")

    phase = _breadth_phase()
    passed, missing = D.gate_passes(sp, str(project), phase)

    assert passed is False
    detail = "; ".join(missing)
    assert "in_progress: analysis_core_state.md" in detail
    # Counterpart: must not be reported as missing or stub (size and existence are OK)
    assert "missing: " not in detail
    assert "stub: " not in detail


# ---------------------------------------------------------------------------
# Test 13 -- fresh-audit blocking on IN_PROGRESS-marked artifact
# ---------------------------------------------------------------------------


def test_gate_fresh_rejects_in_progress(tmp_path: Path):
    """A fresh-audit scratchpad MUST block an artifact whose PLAMEN_STATUS
    is IN_PROGRESS even though it has markers and substantive size. This
    is the central correctness property closing the DODO failure class."""
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    _write_manifest(sp, [("B1", "core_state")])
    _write_in_progress_artifact(sp, "analysis_core_state.md")

    phase = _breadth_phase()
    passed, missing = D.gate_passes(sp, str(project), phase)

    assert passed is False
    detail = "; ".join(missing)
    assert "in_progress: analysis_core_state.md" in detail


# ---------------------------------------------------------------------------
# Test 14 -- fresh-audit blocking on COMPLETE-but-structurally-broken artifact
# ---------------------------------------------------------------------------


def test_gate_fresh_rejects_structural_incomplete(tmp_path: Path):
    """A COMPLETE-marked artifact that fails the structural completeness
    check (here: missing the required `## Findings` heading) MUST NOT
    pass. The point is to prevent a polished-but-empty shell from being
    waved through merely because it carries a COMPLETE marker."""
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    _write_manifest(sp, [("B1", "core_state")])
    _write_complete_artifact(
        sp,
        "analysis_core_state.md",
        agent_id="B1",
        include_findings_heading=False,  # missing required heading
    )

    phase = _breadth_phase()
    passed, missing = D.gate_passes(sp, str(project), phase)

    assert passed is False
    detail = "; ".join(missing)
    assert "structural_fail:" in detail
    assert "analysis_core_state.md" in detail


# ---------------------------------------------------------------------------
# Test 15 -- fresh-audit accepts COMPLETE + structural OK
# ---------------------------------------------------------------------------


def test_gate_fresh_accepts_complete_plus_structural(tmp_path: Path):
    """A fresh-audit scratchpad MUST pass an artifact that has both a
    COMPLETE marker and satisfies every structural check. This is the
    happy-path coverage and the sanity counterpart to tests 12-14."""
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    _write_manifest(sp, [("B1", "core_state")])
    _write_complete_artifact(sp, "analysis_core_state.md", agent_id="B1")

    phase = _breadth_phase()
    passed, missing = D.gate_passes(sp, str(project), phase)

    assert passed is True, f"unexpected missing: {missing!r}"
    assert missing == []


# ---------------------------------------------------------------------------
# Test 16 -- legacy scratchpad accepts unmarked artifact with warning
# ---------------------------------------------------------------------------


def test_gate_legacy_accepts_unmarked_with_log(tmp_path: Path, caplog):
    """Legacy / resumed scratchpads (no `_audit_started_with_markers.json`
    sentinel) MUST still pass unmarked artifacts so resumed audits
    started before this version keep working. A warning is logged for
    forensic traceability."""
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    # NO sentinel -> legacy mode
    _write_manifest(sp, [("B1", "core_state")])
    _write_legacy_unmarked_artifact(sp, "analysis_core_state.md")

    phase = _breadth_phase()
    import logging

    with caplog.at_level(logging.WARNING):
        passed, missing = D.gate_passes(sp, str(project), phase)

    assert passed is True, f"unexpected missing: {missing!r}"
    assert missing == []
    # Warning should mention legacy-unmarked-artifact for the file
    assert any(
        "legacy-unmarked-artifact" in rec.getMessage()
        and "analysis_core_state.md" in rec.getMessage()
        for rec in caplog.records
    ), f"missing expected warning; got: {[r.getMessage() for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Test 17 -- legacy scratchpad tolerates IN_PROGRESS with warning
# ---------------------------------------------------------------------------


def test_gate_legacy_blocks_explicit_in_progress(tmp_path: Path, caplog):
    """Ship 8.18 (codex #5, inverts old Test 17): an EXPLICIT marker-bearing
    IN_PROGRESS file must NEVER pass -- not even on a legacy/resumed scratchpad.
    An actively-incomplete file means work was started and not finished; resume
    must finish it, not skip it. (Legacy *unmarked* files -- no markers at all
    -- remain tolerated; see test_gate_legacy_accepts_unmarked_with_log.)"""
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    # NO sentinel -> legacy mode
    _write_manifest(sp, [("B1", "core_state")])
    _write_in_progress_artifact(sp, "analysis_core_state.md")

    phase = _breadth_phase()
    import logging

    with caplog.at_level(logging.WARNING):
        passed, missing = D.gate_passes(sp, str(project), phase)

    assert passed is False, "explicit IN_PROGRESS must block even on legacy"
    detail = " ".join(missing)
    assert "in_progress: analysis_core_state.md" in detail
    assert any(
        "now BLOCK (Ship 8.18)" in rec.getMessage()
        and "analysis_core_state.md" in rec.getMessage()
        for rec in caplog.records
    ), f"missing expected block warning; got: {[r.getMessage() for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Test 18 -- partial-completion mix routes only in_progress rows to bucket
# ---------------------------------------------------------------------------


def test_gate_breadth_partial_returns_inprogress_bucket_only(tmp_path: Path):
    """Mixed-completion fresh-audit run: 5 of 8 manifest rows are
    COMPLETE+structurally OK, 3 are IN_PROGRESS. The gate MUST fail with
    ONLY the 3 in_progress rows in the in_progress bucket and the 5
    complete rows absent from every failure bucket. This is the exact
    DODO 2026-05-21 attempt-1 shape (6 of 8 missing); here we use 3 of 8
    IN_PROGRESS to exercise the bucket plumbing and confirm the
    completed rows are NOT re-listed for retry."""
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)

    focus_areas = [
        "core_state",
        "cross_chain_timing",
        "access_control",
        "centralization",
        "cross_chain_msg",
        "storage_layout",
        "migration",
        "token_flow",
    ]
    agent_ids = [f"B{i+1}" for i in range(len(focus_areas))]
    _write_manifest(sp, list(zip(agent_ids, focus_areas)))

    # 5 COMPLETE rows
    complete_rows = list(zip(agent_ids[:5], focus_areas[:5]))
    # 3 IN_PROGRESS rows
    in_progress_rows = list(zip(agent_ids[5:], focus_areas[5:]))

    for agent_id, focus in complete_rows:
        _write_complete_artifact(sp, f"analysis_{focus}.md", agent_id=agent_id)
    for agent_id, focus in in_progress_rows:
        _write_in_progress_artifact(sp, f"analysis_{focus}.md", agent_id=agent_id)

    phase = _breadth_phase()
    passed, missing = D.gate_passes(sp, str(project), phase)

    assert passed is False
    detail = "; ".join(missing)

    # Every in_progress row MUST be listed in the in_progress bucket
    for _, focus in in_progress_rows:
        assert f"analysis_{focus}.md" in detail
    assert "in_progress:" in detail
    # The completed rows MUST NOT appear in any failure bucket
    for _, focus in complete_rows:
        assert f"analysis_{focus}.md" not in detail
    # The missing / stub / structural_fail buckets MUST be empty for this
    # scenario (all files exist, are sized, and the 5 COMPLETE rows pass
    # structural checks).
    assert "missing: " not in detail
    assert "stub: " not in detail
    assert "structural_fail:" not in detail
