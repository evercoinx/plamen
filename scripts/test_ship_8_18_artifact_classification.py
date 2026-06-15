"""Ship 8.18 -- harden artifact classification (codex #5).

Three holes closed:
  1. A bare `## Findings` shell (heading, no body) counted as completed work.
  2. An explicit PLAMEN_STATUS: IN_PROGRESS file was tolerated on legacy/resumed
     scratchpads (covered by the inverted Test 17 in test_gate_markers.py).
  3. PLAMEN markers inside fenced code-block EXAMPLES poisoned last-wins status
     parsing and legacy-unmarked detection.

These are tightenings only -- the Ship 8.2/8.7 semantic widenings (block OR
`## Findings` section with body; any-PLAMEN-marker = fresh format) are kept.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_parsers import (  # noqa: E402
    _artifact_has_findings,
    _findings_section_has_body,
    _strip_fenced_code_blocks,
    _extract_artifact_status,
    is_artifact_legacy_unmarked,
)


# --------------------------------------------------------------------------
# Hole 1: bare `## Findings` shell
# --------------------------------------------------------------------------

def test_bare_findings_heading_is_not_findings():
    assert _artifact_has_findings("# Title\n\n## Findings\n") is False


def test_bare_findings_heading_then_eof_is_not_findings():
    assert _artifact_has_findings("## Findings") is False


def test_bare_findings_then_next_heading_is_not_findings():
    text = "## Findings\n\n## Methodology\nlots of words here describing method"
    assert _artifact_has_findings(text) is False


def test_findings_section_with_body_counts():
    text = "## Findings\n\nNo exploitable issues; reviewed all setters and the math.\n"
    assert _findings_section_has_body(text) is True
    assert _artifact_has_findings(text) is True


def test_finding_block_still_counts():
    text = "## Finding [CS-1]: Reentrancy\n\nDetails of the bug here."
    assert _artifact_has_findings(text) is True


def test_8_2_widening_preserved_for_substantive_section():
    """Ship 8.2: a `## Findings` section with prose (no `## Finding [` block)
    still counts -- the tightening only rejects the empty shell."""
    text = "## Findings\n\n- analysed deposit/withdraw symmetry, all consistent\n"
    assert _artifact_has_findings(text) is True


# --------------------------------------------------------------------------
# Hole 3: fenced markers must not poison parsing
# --------------------------------------------------------------------------

def test_strip_fenced_removes_block_content():
    text = "before\n```\n<!-- PLAMEN_STATUS: COMPLETE -->\n```\nafter"
    out = _strip_fenced_code_blocks(text)
    assert "before" in out and "after" in out
    assert "PLAMEN_STATUS" not in out


def test_fenced_complete_marker_does_not_mask_real_in_progress(tmp_path):
    """A real IN_PROGRESS marker outside fences must win over a COMPLETE marker
    that only appears inside a fenced example."""
    p = tmp_path / "a.md"
    p.write_text(
        "# Doc\n\nExample of the contract:\n"
        "```\n<!-- PLAMEN_STATUS: COMPLETE -->\n```\n\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n",
        encoding="utf-8",
    )
    markers = _extract_artifact_status(p)
    assert markers.get("STATUS") == "IN_PROGRESS"


def test_fenced_only_marker_is_legacy_unmarked(tmp_path):
    """A file whose ONLY PLAMEN marker is inside a fenced example is legacy
    (unmarked), not fresh-format."""
    p = tmp_path / "b.md"
    p.write_text(
        "legacy body " * 30 + "\n```\n<!-- PLAMEN_ARTIFACT: x -->\n```\n",
        encoding="utf-8",
    )
    assert is_artifact_legacy_unmarked(p) is True


def test_real_marker_outside_fence_is_not_legacy(tmp_path):
    p = tmp_path / "c.md"
    p.write_text(
        "body content here " * 10 + "\n<!-- PLAMEN_STATUS: COMPLETE -->\n",
        encoding="utf-8",
    )
    assert is_artifact_legacy_unmarked(p) is False
