"""Tests for Ship 1 of the artifact-complete PTY supervision plan.

Validates the marker parser and structural completeness helpers added to
`plamen_parsers.py`. These are pure unit tests over file content -- no
driver state, no PTY, no subprocess. Wiring into the breadth gate happens
in Ship 3 (with its own test file).

Test numbering matches `~/.claude/plans/artifact-complete-pty-supervision.md`
section "Tests to add (40 new tests across 7 files)" -> `test_artifact_markers.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_parsers import (  # noqa: E402
    _extract_artifact_status,
    _structural_completeness_ok,
    is_artifact_complete,
    is_artifact_legacy_unmarked,
)


# ---------------------------------------------------------------------------
# Marker extraction (tests 1-4)
# ---------------------------------------------------------------------------


def test_extract_artifact_status_complete(tmp_path):
    """Test 1: a file with the full marker set including a final
    COMPLETE line returns every marker, with STATUS resolved to COMPLETE
    (last-line-wins semantics)."""
    p = tmp_path / "analysis_access_control.md"
    p.write_text(
        "<!-- PLAMEN_ARTIFACT: analysis_access_control.md -->\n"
        "<!-- PLAMEN_OWNER: B3 -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: breadth -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n"
        "\n"
        "# Access Control Analysis\n"
        "\n"
        "## Findings\n"
        "\n"
        "Finding 1...\n"
        "\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 7 -->\n",
        encoding="utf-8",
    )
    markers = _extract_artifact_status(p)
    assert markers["ARTIFACT"] == "analysis_access_control.md"
    assert markers["OWNER"] == "B3"
    assert markers["STATUS"] == "COMPLETE"  # last-write wins
    assert markers["PHASE"] == "breadth"
    assert markers["VERSION"] == "1"
    assert markers["FINDINGS_COUNT"] == "7"


def test_extract_artifact_status_in_progress_only(tmp_path):
    """Test 2: a partially written file with IN_PROGRESS but no COMPLETE
    line resolves STATUS to IN_PROGRESS and has no FINDINGS_COUNT."""
    p = tmp_path / "a.md"
    p.write_text(
        "<!-- PLAMEN_ARTIFACT: a.md -->\n"
        "<!-- PLAMEN_OWNER: B1 -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "\n"
        "# Partially written\n"
        "...interrupted before COMPLETE\n",
        encoding="utf-8",
    )
    markers = _extract_artifact_status(p)
    assert markers["STATUS"] == "IN_PROGRESS"
    assert markers.get("FINDINGS_COUNT") is None
    assert "FINDINGS_COUNT" not in markers


def test_extract_artifact_status_legacy_unmarked(tmp_path):
    """Test 3: a legacy artifact written before the marker contract was
    introduced returns an empty marker dict."""
    p = tmp_path / "legacy_analysis.md"
    p.write_text(
        "# Legacy Analysis (no markers)\n"
        "\n"
        "## Findings\n"
        "\n"
        "Real legacy body content that was written without any "
        "PLAMEN_ARTIFACT marker because the file predates Ship 1.\n",
        encoding="utf-8",
    )
    markers = _extract_artifact_status(p)
    assert markers == {}


def test_extract_artifact_status_multiple_status_last_wins(tmp_path):
    """Test 4: when multiple PLAMEN_STATUS lines exist (e.g. the
    subagent's initial IN_PROGRESS write followed by its final COMPLETE
    edit), the LAST value wins -- the final-write-wins rule from the
    plan."""
    p = tmp_path / "a.md"
    p.write_text(
        "<!-- PLAMEN_ARTIFACT: a.md -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "\n"
        "body lines from the initial Write call\n"
        "\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 3 -->\n",
        encoding="utf-8",
    )
    markers = _extract_artifact_status(p)
    assert markers["STATUS"] == "COMPLETE"


# ---------------------------------------------------------------------------
# is_artifact_complete (tests 5-7)
# ---------------------------------------------------------------------------


def test_is_artifact_complete_true(tmp_path):
    """Test 5: a COMPLETE-marked artifact of sufficient size returns
    True."""
    p = tmp_path / "a.md"
    body = "x" * 600  # well above min_bytes=200
    p.write_text(
        "<!-- PLAMEN_ARTIFACT: a.md -->\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 2 -->\n"
        + body
        + "\n",
        encoding="utf-8",
    )
    assert is_artifact_complete(p, min_bytes=200) is True


def test_is_artifact_complete_false_in_progress_marker(tmp_path):
    """Test 6: an IN_PROGRESS artifact (even if substantially sized)
    must NOT register as complete. This is the central correctness
    property that closes an observed failure class."""
    p = tmp_path / "a.md"
    body = "x" * 600
    p.write_text(
        "<!-- PLAMEN_ARTIFACT: a.md -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        + body
        + "\n",
        encoding="utf-8",
    )
    assert is_artifact_complete(p, min_bytes=200) is False


def test_is_artifact_complete_false_too_small(tmp_path):
    """Test 7: a COMPLETE-marked file whose body falls below
    min_bytes still fails the gate. Marker is necessary but not
    sufficient -- this prevents a polished-looking but empty
    skeleton from passing."""
    p = tmp_path / "a.md"
    # File is well under min_bytes=200 even after the markers.
    p.write_text(
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 0 -->\n",
        encoding="utf-8",
    )
    assert is_artifact_complete(p, min_bytes=200) is False


# ---------------------------------------------------------------------------
# Structural completeness (tests 8-11)
# ---------------------------------------------------------------------------


def test_structural_check_findings_count_optional_when_blocks_present(tmp_path):
    """Test 8 (Ship 8.2, was '...missing_findings_count_fails', INVERTED):
    PLAMEN_FINDINGS_COUNT is now informational. A COMPLETE artifact with
    `## Finding [` blocks but NO FINDINGS_COUNT marker PASSES -- the
    findings-present decision uses block detection, not the count. This
    removes the attempt-1 wasted retry the canonical files hit in a past audit."""
    p = tmp_path / "a.md"
    p.write_text(
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "\n"
        "# Title\n"
        "\n"
        "## Finding [CS-1]: a real finding\n"
        "body with detail.\n",
        encoding="utf-8",
    )
    # require_findings_count_marker=True must be a no-op now.
    ok, reasons = _structural_completeness_ok(
        p,
        required_headings=(),
        require_findings_count_marker=True,
    )
    assert ok is True, f"expected pass (count optional); reasons: {reasons!r}"


def test_structural_check_findings_count_marker_is_noop(tmp_path):
    """Ship 8.2 compatibility no-op: passing require_findings_count_marker
    True vs False yields the SAME verdict (the marker never affects the
    gate)."""
    p = tmp_path / "a.md"
    p.write_text(
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "# T\n## Finding [X-1]: f\nbody\n",
        encoding="utf-8",
    )
    ok_true, _ = _structural_completeness_ok(p, require_findings_count_marker=True)
    ok_false, _ = _structural_completeness_ok(p, require_findings_count_marker=False)
    assert ok_true == ok_false is True


def test_structural_check_no_findings_no_rationale_fails(tmp_path):
    """Test 9 (Ship 8.2, rebased on block-absence): a COMPLETE artifact
    with NO finding blocks and NO '## No Findings' / '## Negative Result'
    rationale is empty/incomplete and fails. (FINDINGS_COUNT value is
    irrelevant -- the gate keys off blocks + rationale.)"""
    p = tmp_path / "a.md"
    p.write_text(
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 0 -->\n"
        "\n"
        "# Title\n"
        "\n"
        "(no finding blocks, no rationale section)\n"
        + ("padding " * 30) + "\n",
        encoding="utf-8",
    )
    ok, reasons = _structural_completeness_ok(p, required_headings=())
    assert ok is False
    assert any(
        "empty/incomplete" in r or "No Findings" in r for r in reasons
    ), f"expected empty/incomplete failure, got: {reasons!r}"


def test_structural_check_no_findings_with_rationale_passes(tmp_path):
    """Ship 8.2: a genuine zero-findings artifact PASSES when it carries
    a '## No Findings' rationale (no finding blocks needed)."""
    p = tmp_path / "a.md"
    p.write_text(
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "# Title\n\n## No Findings\n\nAnalyzed scope; nothing reportable.\n"
        + ("padding " * 30) + "\n",
        encoding="utf-8",
    )
    ok, reasons = _structural_completeness_ok(p, required_headings=())
    assert ok is True, f"expected pass with rationale; reasons: {reasons!r}"


def test_structural_check_depth_triple_hash_finding_block_passes(tmp_path):
    """Ship 8.2 (depth shape): `### Finding [DT-1]` (3 hashes) counts as a
    finding block -- the detector matches 2 OR 3 hashes so depth artifacts
    are not silently regressed."""
    p = tmp_path / "depth_token_flow_findings.md"
    p.write_text(
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "# Depth\n\n### Finding [DT-1]: value flow gap\nbody detail.\n",
        encoding="utf-8",
    )
    ok, reasons = _structural_completeness_ok(p, required_headings=())
    assert ok is True, f"depth triple-hash block should pass; reasons: {reasons!r}"


def test_structural_check_placeholder_strings_fail(tmp_path):
    """Test 10: an artifact that still contains skeleton-placeholder
    substrings at COMPLETE time fails structurally."""
    p = tmp_path / "a.md"
    p.write_text(
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "\n"
        "# Title\n"
        "\n"
        "## Finding [CS-1]: x\n"
        "TODO: write the finding here\n",
        encoding="utf-8",
    )
    ok, reasons = _structural_completeness_ok(
        p,
        required_headings=(),
        placeholder_strings=("TODO:", "FILL_ME", "<placeholder>"),
    )
    assert ok is False
    assert any(
        "placeholder" in r.lower() and "TODO" in r for r in reasons
    ), f"expected placeholder failure naming TODO:, got: {reasons!r}"


def test_structural_check_obligation_receipts_not_required(tmp_path):
    """Test 11 (Ship 8.2, INVERTED): a COMPLETE artifact with findings
    PASSES even when an opengrep shard exists and no '## Obligation
    Receipts' section is present. Receipt coverage is warning-only
    (owned by _check_opengrep_obligation_coverage) and MUST NOT hard-fail
    the artifact gate -- this resolves the warning-only-vs-hard-fail
    inconsistency (RC4b)."""
    artifact = tmp_path / "analysis_access_control.md"
    artifact.write_text(
        "<!-- PLAMEN_ARTIFACT: analysis_access_control.md -->\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
        "\n"
        "# Access Control Analysis\n"
        "\n"
        "## Finding [AC-1]: a real finding with sufficient detail.\n",
        encoding="utf-8",
    )
    shard = tmp_path / "opengrep_obligations_B3_access_control.md"
    shard.write_text(
        "| Row | Rule | Location |\n|-----|------|----------|\n"
        "| 1 | sample-rule | Foo.sol:42 |\n",
        encoding="utf-8",
    )
    # Passing the (now no-op) shard arg must NOT fail the artifact.
    ok, reasons = _structural_completeness_ok(
        artifact,
        required_headings=(),
        require_obligation_receipts_if_shard_exists=shard,
    )
    assert ok is True, (
        f"obligation receipts must not be a hard gate; reasons: {reasons!r}"
    )
