"""Tests for Ship 8 of the artifact-complete PTY supervision plan:
generalizing the durable-artifact marker contract to the depth phase.

Covers:
  - depth artifact gate: fresh audit rejects IN_PROGRESS / missing /
    structural-fail; COMPLETE + depth-appropriate structure passes.
  - depth legacy compatibility: resumed scratchpad (no sentinel) keeps
    the legacy glob+quorum behavior; unmarked files tolerated.
  - depth structural check is DEPTH-appropriate: does NOT require
    breadth's `## Findings` heading (depth uses `### Finding [ID]`).
  - compute_phase_row_statuses dispatch + supervision membership.
  - NON-REGRESSION: verify-shard and report-tier phases are NOT
    supervised and their gate behavior is unchanged by Ship 8.

All tests are pure (file state + gate function); no PTY, no subprocess.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402

_SENTINEL = "_audit_started_with_markers.json"
_DEPTH_ROLES = ("token_flow", "state_trace", "edge_case", "external")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _depth_phase(min_bytes: int = 200):
    """A depth-shaped Phase with the canonical 4 roles in example_tokens
    and min_artifacts_count=4 (matching SC_PHASES)."""
    return D.Phase(
        name="depth",
        section_markers=["Phase 4b"],
        expected_artifacts=["depth_*_findings.md"],
        base_timeout_s=60,
        min_artifact_bytes=min_bytes,
        min_artifacts_count=4,
        example_tokens=list(_DEPTH_ROLES),
    )


def _config(project_root: Path) -> dict:
    return {
        "project_root": str(project_root),
        "scratchpad": str(project_root / ".scratchpad"),
    }


def _touch_sentinel(sp: Path) -> None:
    (sp / _SENTINEL).write_text("{}", encoding="utf-8")


def _write_complete_depth(sp: Path, role: str, *, findings: int = 2) -> None:
    """Write a COMPLETE-marked depth file that passes the depth
    structural check (FINDINGS_COUNT + finding blocks; no breadth
    `## Findings` heading)."""
    lines = [
        f"<!-- PLAMEN_ARTIFACT: depth_{role}_findings.md -->",
        f"<!-- PLAMEN_OWNER: depth-{role} -->",
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->",
        "<!-- PLAMEN_PHASE: depth -->",
        "<!-- PLAMEN_VERSION: 1 -->",
        f"<!-- AGENT_ROW: {role} -->",
        f"<!-- EXPECTED_OUTPUT: depth_{role}_findings.md -->",
        "",
        f"# Depth Analysis: {role}",
        "",
    ]
    for i in range(findings):
        lines += [
            f"### Finding [{role.upper()[:2]}-{i+1}]: example",
            "**Severity**: Medium",
            "Real depth finding body content with enough detail to be substantive. " * 4,
            "",
        ]
    lines += [
        "## Semantic Proof Checks",
        "",
        "Challenged invariant X; read-site Y; intent evidence Z.",
        "",
        "<!-- PLAMEN_STATUS: COMPLETE -->",
        f"<!-- PLAMEN_FINDINGS_COUNT: {findings} -->",
    ]
    (sp / f"depth_{role}_findings.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _write_in_progress_depth(sp: Path, role: str) -> None:
    body = (
        f"<!-- PLAMEN_ARTIFACT: depth_{role}_findings.md -->\n"
        f"<!-- PLAMEN_OWNER: depth-{role} -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: depth -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n\n"
        f"# Depth Analysis: {role}\n\n"
        + ("partial work-in-progress body " * 30)
        + "\n"
    )
    (sp / f"depth_{role}_findings.md").write_text(body, encoding="utf-8")


def _write_legacy_depth(sp: Path, role: str) -> None:
    body = (
        f"# Depth Analysis: {role} (legacy, no markers)\n\n"
        f"### Finding [{role.upper()[:2]}-1]: legacy finding\n"
        "**Severity**: High\n"
        + ("legacy depth body content from before the marker contract " * 25)
        + "\n"
    )
    (sp / f"depth_{role}_findings.md").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Dispatch + membership
# ---------------------------------------------------------------------------


def test_depth_is_in_supervised_phases():
    assert "depth" in D.PTY_SUPERVISED_PHASES
    assert "breadth" in D.PTY_SUPERVISED_PHASES
    # Single-subprocess phases are explicitly NOT supervised.
    assert "sc_verify_aggregate" not in D.PTY_SUPERVISED_PHASES
    assert "report_index" not in D.PTY_SUPERVISED_PHASES
    assert "chain" not in D.PTY_SUPERVISED_PHASES


def test_compute_phase_row_statuses_dispatches_depth(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    for role in _DEPTH_ROLES:
        _write_complete_depth(sp, role)
    statuses = D.compute_phase_row_statuses(sp, _depth_phase())
    assert {s["name"] for s in statuses} == {
        f"depth_{r}_findings.md" for r in _DEPTH_ROLES
    }
    assert all(s["status"] == "complete" for s in statuses)


def test_compute_phase_row_statuses_empty_for_unsupervised(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    other = D.Phase("chain", ["x"], ["chain_hypotheses.md"], base_timeout_s=60)
    assert D.compute_phase_row_statuses(sp, other) == []


# ---------------------------------------------------------------------------
# depth artifact: fresh-audit gate
# ---------------------------------------------------------------------------


def test_depth_fresh_rejects_in_progress(tmp_path: Path):
    """Fresh audit: an IN_PROGRESS depth role file blocks the gate."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    # 3 complete, 1 in-progress.
    for role in _DEPTH_ROLES[:3]:
        _write_complete_depth(sp, role)
    _write_in_progress_depth(sp, _DEPTH_ROLES[3])

    passed, missing = D.gate_passes(sp, str(project), _depth_phase())
    assert passed is False
    detail = "; ".join(missing)
    assert "depth_*_findings.md manifest-exact incomplete" in detail
    assert "in_progress: depth_external_findings.md" in detail
    # Completed roles must NOT be listed.
    assert "depth_token_flow_findings.md" not in detail


def test_depth_fresh_rejects_missing(tmp_path: Path):
    """Fresh audit: a missing canonical depth role file blocks the
    gate even though the other 3 exist (canonical-role enforcement)."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    for role in _DEPTH_ROLES[:3]:
        _write_complete_depth(sp, role)
    # external missing entirely
    passed, missing = D.gate_passes(sp, str(project), _depth_phase())
    assert passed is False
    assert "missing: depth_external_findings.md" in "; ".join(missing)


def test_depth_fresh_rejects_structural_zero_findings_no_rationale(tmp_path: Path):
    """Fresh audit: a COMPLETE depth file with FINDINGS_COUNT 0 and no
    `## No Findings` rationale fails the structural check."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    for role in _DEPTH_ROLES[:3]:
        _write_complete_depth(sp, role)
    # 4th: COMPLETE but zero findings, no rationale.
    bad = (
        "<!-- PLAMEN_ARTIFACT: depth_external_findings.md -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: depth -->\n"
        f"# Depth Analysis: external\n\n"
        + ("body padding " * 40)
        + "\n## Semantic Proof Checks\n\nstuff\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 0 -->\n"
    )
    (sp / "depth_external_findings.md").write_text(bad, encoding="utf-8")

    passed, missing = D.gate_passes(sp, str(project), _depth_phase())
    assert passed is False
    detail = "; ".join(missing)
    assert "structural_fail:" in detail
    assert "depth_external_findings.md" in detail


def test_depth_fresh_accepts_complete_without_breadth_findings_heading(tmp_path: Path):
    """Fresh audit happy path: all 4 canonical depth files COMPLETE +
    depth-appropriate structure pass. Crucially, the depth files use
    `### Finding [ID]` blocks and do NOT contain a breadth-style
    `## Findings` heading -- proving the depth structural check does
    not blindly apply breadth's required heading."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    for role in _DEPTH_ROLES:
        _write_complete_depth(sp, role)
    # Sanity: the depth files genuinely lack a "## Findings" heading.
    sample = (sp / "depth_token_flow_findings.md").read_text(encoding="utf-8")
    assert "## Findings" not in sample
    assert "### Finding [" in sample

    passed, missing = D.gate_passes(sp, str(project), _depth_phase())
    assert passed is True, f"unexpected missing: {missing!r}"
    assert missing == []


def test_depth_fresh_accepts_zero_findings_with_rationale(tmp_path: Path):
    """A depth role legitimately finding nothing passes when it carries
    a `## No Findings` rationale + COMPLETE marker."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    for role in _DEPTH_ROLES[:3]:
        _write_complete_depth(sp, role)
    nf = (
        "<!-- PLAMEN_ARTIFACT: depth_external_findings.md -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: depth -->\n"
        "# Depth Analysis: external\n\n"
        + ("analysis body padding " * 30)
        + "\n## No Findings\n\nNo external-interaction vulns triggered in scope.\n"
        "## Semantic Proof Checks\n\nchecked oracle freshness, no gap.\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 0 -->\n"
    )
    (sp / "depth_external_findings.md").write_text(nf, encoding="utf-8")

    passed, missing = D.gate_passes(sp, str(project), _depth_phase())
    assert passed is True, f"unexpected missing: {missing!r}"


# ---------------------------------------------------------------------------
# depth legacy compatibility
# ---------------------------------------------------------------------------


def test_depth_legacy_uses_quorum_not_canonical(tmp_path: Path):
    """Legacy/resumed scratchpad (no sentinel): the depth gate falls
    through to the existing glob+quorum (min_artifacts_count=4). Four
    substantial unmarked depth files PASS -- the canonical-role marker
    enforcement does NOT kick in, so in-flight audits are not broken."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    # NO sentinel -> legacy mode.
    for role in _DEPTH_ROLES:
        _write_legacy_depth(sp, role)
    passed, missing = D.gate_passes(sp, str(project), _depth_phase())
    assert passed is True, f"legacy depth should pass via quorum: {missing!r}"


def test_depth_legacy_quorum_still_enforced(tmp_path: Path):
    """Legacy mode: fewer than min_artifacts_count substantial files
    still fails via the existing quorum (no regression of the old
    behavior)."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    # Only 2 of 4 -> below quorum=4.
    for role in _DEPTH_ROLES[:2]:
        _write_legacy_depth(sp, role)
    passed, missing = D.gate_passes(sp, str(project), _depth_phase())
    assert passed is False
    assert any("depth_*_findings.md" in m for m in missing)


# ---------------------------------------------------------------------------
# NON-REGRESSION: verify + report tiers are single-subprocess, unsupervised
# ---------------------------------------------------------------------------


def test_verify_phase_not_supervised_and_gate_unchanged(tmp_path: Path):
    """A verify shard phase is NOT in PTY_SUPERVISED_PHASES (single
    subprocess; 'Do not spawn subagents'). compute_phase_row_statuses
    returns [] for it, so no marker enforcement is layered on top of
    the existing verify-shard gate / N/A semantics."""
    # Representative verify shard phase name.
    assert not any(
        p in D.PTY_SUPERVISED_PHASES
        for p in ("sc_verify_crithigh", "sc_verify_aggregate", "sc_verify_low_a")
    )
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    vphase = D.Phase(
        "sc_verify_crithigh", ["x"], [], base_timeout_s=60
    )
    assert D.compute_phase_row_statuses(sp, vphase) == []


def test_report_tier_phase_not_supervised(tmp_path: Path):
    """Report tier / index phases are single-subprocess and NOT
    supervised; their empty-tier / N/A semantics are preserved by
    Ship 8 leaving them entirely alone."""
    for name in (
        "report_index",
        "report_critical_high",
        "report_medium",
        "report_low_info",
        "report_assemble",
    ):
        assert name not in D.PTY_SUPERVISED_PHASES
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    rphase = D.Phase(
        "report_critical_high", ["x"], ["report_critical_high.md"],
        base_timeout_s=60,
    )
    assert D.compute_phase_row_statuses(sp, rphase) == []
