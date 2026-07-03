"""Ship 8.2 regression: the EXACT breadth marker variants that halted a
prior audit, replayed end-to-end through
compute_breadth_row_statuses() AND gate_passes().

The original marker contract was validated only against perfect hand-written
fixtures. Real agents produced partial-but-semantically-recoverable marker
sets, and the gate (is_artifact_legacy_unmarked keyed off PLAMEN_ARTIFACT,
plus a literal ## Findings + FINDINGS_COUNT + obligation-receipts hard gate)
halted the phase. These fixtures reproduce the seven real shapes so the
regression can never silently return.

Observed variants (from the failed scratchpad):
  - access_control / token_flow / centralization_risk : STATUS x2 + FINDINGS_COUNT,
        `## Finding [` blocks, NO PLAMEN_ARTIFACT, NO `## Findings`
  - core_state          : PLAMEN_AGENT + PLAMEN_FOCUS + STATUS x2 + FINDINGS_COUNT
  - storage_layout      : PLAMEN_SHARD_OWNER + PLAMEN_SHARD_FOCUS + STATUS x2 + COUNT
  - cross_chain / external_deps : canonical block + `## Findings` + blocks
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
import plamen_validators as V  # noqa: E402

_SENTINEL = "_audit_started_with_markers.json"

# The 7 manifest focus areas from the failed run.
_FOCUS = [
    "core_state",
    "access_control",
    "token_flow",
    "storage_layout",
    "centralization_risk",
    "cross_chain",
    "external_deps",
]


def _body_findings(prefix: str, n: int = 3) -> str:
    blocks = []
    for i in range(n):
        blocks.append(
            f"## Finding [{prefix}-{i+1}]: example finding {i+1}\n"
            "**Verdict**: CONFIRMED\n"
            "**Severity**: Medium\n"
            "Real finding body content with enough detail to be substantive. " * 3
            + "\n"
        )
    return "\n".join(blocks)


def _variant_status_count_only(focus: str) -> str:
    """access_control / token_flow / centralization_risk shape."""
    pfx = focus[:2].upper()
    return (
        f"# Breadth Agent: {focus}\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n\n"
        f"{_body_findings(pfx)}\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 3 -->\n"
    )


def _variant_agent_focus(focus: str) -> str:
    """core_state shape: invented PLAMEN_AGENT / PLAMEN_FOCUS markers."""
    pfx = focus[:2].upper()
    return (
        f"# Breadth Agent B1: {focus}\n"
        "<!-- PLAMEN_AGENT: B1 -->\n"
        f"<!-- PLAMEN_FOCUS: {focus} -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n\n"
        f"{_body_findings(pfx, 7)}\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 7 -->\n"
    )


def _variant_shard_markers(focus: str) -> str:
    """storage_layout shape: copied opengrep-shard PLAMEN_SHARD_* markers."""
    pfx = focus[:2].upper()
    return (
        f"# B6 {focus} Analysis\n"
        "<!-- PLAMEN_SHARD_OWNER: B6 -->\n"
        f"<!-- PLAMEN_SHARD_FOCUS: {focus} -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n\n"
        f"{_body_findings(pfx)}\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 3 -->\n"
    )


def _variant_canonical(focus: str) -> str:
    """cross_chain / external_deps shape: full canonical block + ## Findings."""
    pfx = focus[:2].upper()
    return (
        f"<!-- PLAMEN_ARTIFACT: analysis_{focus}.md -->\n"
        "<!-- PLAMEN_OWNER: B3 -->\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_PHASE: breadth -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n"
        "<!-- AGENT_ROW: B3 -->\n"
        f"<!-- EXPECTED_OUTPUT: analysis_{focus}.md -->\n\n"
        f"# {focus} Analysis\n\n"
        "## Findings\n\n"
        f"{_body_findings(pfx)}\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 3 -->\n"
    )


_VARIANT_FOR = {
    "core_state": _variant_agent_focus,
    "access_control": _variant_status_count_only,
    "token_flow": _variant_status_count_only,
    "storage_layout": _variant_shard_markers,
    "centralization_risk": _variant_status_count_only,
    "cross_chain": _variant_canonical,
    "external_deps": _variant_canonical,
}


def _write_manifest(sp: Path) -> None:
    header = (
        "# Spawn Manifest\n\n"
        "| Template | Required? | Agent ID | Focus Area | "
        "Expected Output | Status | Type |\n"
        "|----------|-----------|----------|------------|"
        "-----------------|--------|------|\n"
    )
    rows = [
        f"| TPL | YES | B{i+1} | {f} | analysis_{f}.md | PENDING | agent |"
        for i, f in enumerate(_FOCUS)
    ]
    (sp / "spawn_manifest.md").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def _seed_failed_scratchpad(sp: Path) -> None:
    """Reproduce the failed breadth scratchpad shape from a prior audit."""
    _touch_sentinel(sp)
    _write_manifest(sp)
    for focus in _FOCUS:
        (sp / f"analysis_{focus}.md").write_text(
            _VARIANT_FOR[focus](focus), encoding="utf-8"
        )
    # Foreign per-contract leak (RC6): quarantined, must not be ingested.
    (sp / "analysis_percontract_3.md").write_text(
        "# stray per-contract output\n" + ("body " * 60) + "\n", encoding="utf-8"
    )
    # Opengrep shard present with 190 rows but agents emitted 0 receipts (RC5):
    # must remain a non-blocking warning, not a gate failure.
    rows = "\n".join(
        f"| {i} | rule-{i} | warning | Foo.sol:{i} | msg |" for i in range(1, 191)
    )
    (sp / "opengrep_findings.md").write_text(
        "# OpenGrep Findings\n\n| Row | Rule | Severity | Location | Message |\n"
        "| --- | --- | --- | --- | --- |\n" + rows + "\n",
        encoding="utf-8",
    )


def _touch_sentinel(sp: Path) -> None:
    (sp / _SENTINEL).write_text(
        '{"schema_version": 1, "mode": "thorough", "pipeline": "sc"}',
        encoding="utf-8",
    )


def _breadth_phase():
    return D.Phase(
        name="breadth",
        section_markers=["Phase 3"],
        expected_artifacts=["analysis_*.md"],
        base_timeout_s=60,
        min_artifact_bytes=200,
    )


# ---------------------------------------------------------------------------
# Per-variant classification (compute_breadth_row_statuses)
# ---------------------------------------------------------------------------


def test_all_seven_real_variants_classify_complete(tmp_path: Path):
    """Every one of the 7 real marker variants classifies as COMPLETE via
    compute_breadth_row_statuses on a fresh-audit scratchpad."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _seed_failed_scratchpad(sp)

    statuses = V.compute_breadth_row_statuses(sp, _breadth_phase())
    by_name = {s["name"]: s["status"] for s in statuses}
    assert set(by_name) == {f"analysis_{f}.md" for f in _FOCUS}
    for name, status in by_name.items():
        assert status == "complete", f"{name} -> {status} (expected complete)"


def test_foreign_percontract_not_a_breadth_row(tmp_path: Path):
    """The foreign analysis_percontract_3.md must NOT appear among the
    breadth row statuses (it is not a manifest output) -- so it cannot
    pollute or fail the gate."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _seed_failed_scratchpad(sp)
    statuses = V.compute_breadth_row_statuses(sp, _breadth_phase())
    names = {s["name"] for s in statuses}
    assert "analysis_percontract_3.md" not in names


# ---------------------------------------------------------------------------
# End-to-end gate (gate_passes) -- the 4 acceptance assertions
# ---------------------------------------------------------------------------


def test_replay_gate_passes_with_no_legacy_warning(tmp_path: Path, caplog):
    """Direct replay of the failed scratchpad through gate_passes():
      (1) all 7 outputs classify complete -> gate PASSES
      (2) foreign percontract ignored (not in row statuses, not in missing)
      (3) opengrep receipt absence does NOT affect gate_passes
      (4) NO legacy-unmarked warning for any file carrying a PLAMEN_* marker
    """
    import logging

    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _seed_failed_scratchpad(sp)

    phase = _breadth_phase()
    with caplog.at_level(logging.WARNING):
        passed, missing = D.gate_passes(sp, str(project), phase)

    # (1) gate passes
    assert passed is True, f"gate must pass; missing: {missing!r}"
    assert missing == []

    # (2) foreign percontract not implicated in any missing entry
    assert not any("percontract" in m for m in missing)

    # (4) no legacy-unmarked warning for the marker-bearing files
    legacy_warnings = [
        rec.getMessage()
        for rec in caplog.records
        if "legacy-unmarked-artifact" in rec.getMessage()
    ]
    assert legacy_warnings == [], (
        f"no legacy-unmarked warning expected (all files carry PLAMEN_* "
        f"markers); got: {legacy_warnings!r}"
    )


def test_replay_opengrep_receipts_warning_only_not_gating(tmp_path: Path):
    """(3) explicit: opengrep receipt coverage with 0/190 receipts returns
    issues but those issues NEVER contribute to gate_passes' missing list
    (warning-only)."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _seed_failed_scratchpad(sp)

    # The dedicated coverage check reports a gap...
    issues = V._check_opengrep_obligation_coverage(sp, "thorough")
    assert issues, "expected an opengrep coverage gap (0/190 receipts)"
    # ...and the gap is framed as telemetry, not a completeness requirement
    # (Ship 8.6-lite wording).
    assert any("telemetry" in i.lower() for i in issues), (
        f"opengrep gap should be telemetry-framed, got: {issues!r}"
    )
    assert not any(
        "incomplete" in i.lower() or "INCOMPLETE" in i for i in issues
    ), f"opengrep gap must not imply incompleteness, got: {issues!r}"

    # ...but the gate still passes (coverage is non-blocking).
    passed, missing = D.gate_passes(sp, str(project), _breadth_phase())
    assert passed is True
    assert not any("opengrep" in m.lower() for m in missing)


# ---------------------------------------------------------------------------
# Negative controls: genuine non-work still fails (no quality loss)
# ---------------------------------------------------------------------------


def test_no_marker_file_on_fresh_is_in_progress(tmp_path: Path):
    """A file with NO PLAMEN_* marker at all on a fresh audit is still
    caught (legacy-on-fresh -> in_progress) -- quality preserved."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    _write_manifest(sp)
    # Write 6 good, 1 marker-less.
    for focus in _FOCUS[:6]:
        (sp / f"analysis_{focus}.md").write_text(
            _VARIANT_FOR[focus](focus), encoding="utf-8"
        )
    (sp / f"analysis_{_FOCUS[6]}.md").write_text(
        "# no markers here\n" + ("body " * 60) + "\n", encoding="utf-8"
    )
    passed, missing = D.gate_passes(sp, str(project), _breadth_phase())
    assert passed is False
    assert any(f"analysis_{_FOCUS[6]}.md" in m for m in missing)


def test_complete_marker_but_no_work_is_structural_fail(tmp_path: Path):
    """A COMPLETE-marked file with no finding blocks and no rationale still
    fails (structural_fail) -- garbage is still rejected."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    _write_manifest(sp)
    for focus in _FOCUS[:6]:
        (sp / f"analysis_{focus}.md").write_text(
            _VARIANT_FOR[focus](focus), encoding="utf-8"
        )
    (sp / f"analysis_{_FOCUS[6]}.md").write_text(
        "<!-- PLAMEN_STATUS: COMPLETE -->\n# empty\n" + ("padding " * 50) + "\n",
        encoding="utf-8",
    )
    passed, missing = D.gate_passes(sp, str(project), _breadth_phase())
    assert passed is False
    detail = "; ".join(missing)
    assert "structural_fail" in detail and f"analysis_{_FOCUS[6]}.md" in detail


def test_incomplete_status_still_in_progress(tmp_path: Path):
    """A fresh file with PLAMEN_* markers but last STATUS == IN_PROGRESS is
    still incomplete."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    _touch_sentinel(sp)
    _write_manifest(sp)
    for focus in _FOCUS[:6]:
        (sp / f"analysis_{focus}.md").write_text(
            _VARIANT_FOR[focus](focus), encoding="utf-8"
        )
    (sp / f"analysis_{_FOCUS[6]}.md").write_text(
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n# wip\n## Finding [X-1]: x\n"
        + ("body " * 50) + "\n",
        encoding="utf-8",
    )
    passed, missing = D.gate_passes(sp, str(project), _breadth_phase())
    assert passed is False
    assert any(f"in_progress: " in m and f"analysis_{_FOCUS[6]}.md" in m for m in missing)


# ---------------------------------------------------------------------------
# Legacy/resumed compatibility
# ---------------------------------------------------------------------------


def test_no_marker_file_on_legacy_is_tolerated(tmp_path: Path):
    """On a resumed/legacy scratchpad (no sentinel), a genuinely
    marker-less file is tolerated (legacy_unmarked) and the gate passes
    if all manifest outputs exist substantially."""
    project = tmp_path
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    # NO sentinel -> legacy mode.
    _write_manifest(sp)
    for focus in _FOCUS[:6]:
        (sp / f"analysis_{focus}.md").write_text(
            _VARIANT_FOR[focus](focus), encoding="utf-8"
        )
    (sp / f"analysis_{_FOCUS[6]}.md").write_text(
        "# legacy no markers\n" + ("body " * 60) + "\n", encoding="utf-8"
    )
    passed, missing = D.gate_passes(sp, str(project), _breadth_phase())
    assert passed is True, f"legacy unmarked should be tolerated; missing: {missing!r}"
