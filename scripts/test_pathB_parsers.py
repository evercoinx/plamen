"""Path-B parser tests: F2 (Chain-Summary table-row fallback) + F5
([EXTERNAL-ASSUMPTION] load-bearing body routing).

These tests exercise STRUCTURE only — synthetic finding IDs and generic harm
phrasing, never a named protocol or a real bug (Plamen Part-0 no-overfit rule).

Run: python -m pytest -q scripts/test_pathB_parsers.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_parsers as P  # noqa: E402


def _write(text: str) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "depth_edge_case_findings.md"
    tmp.write_text(text, encoding="utf-8")
    return tmp


# ---------------------------------------------------------------------------
# F2: Chain-Summary table-row fallback
# ---------------------------------------------------------------------------

def test_f2_row_only_id_is_harvested():
    """An ID that appears ONLY in a Chain-Summary table row is recovered."""
    art = """# Depth Edge-Case Findings

## Chain Summary

| Finding ID | Location | Mechanism | Verdict | Severity |
|------------|----------|-----------|---------|----------|
| DE-9 | src/x.sol:L42 | asymmetric write skips accumulator | PARTIAL | High |
"""
    blocks = P._parse_depth_finding_blocks(_write(art))
    ids = [b["id"] for b in blocks]
    assert "DE-9" in ids, ids
    row = next(b for b in blocks if b["id"] == "DE-9")
    assert row["severity"] == "High", row
    assert "x.sol" in row["location"], row
    assert row["preferred_tag"] == "CODE-TRACE", row  # low-confidence
    assert row.get("_low_confidence_rowonly") == "true", row
    assert "asymmetric" in row["description"].lower(), row


def test_f2_heading_and_row_harvested_exactly_once():
    """An ID present as BOTH a heading and a table row is harvested once."""
    art = """# Depth Findings

## Finding [DE-3]: heading version of the bug

**Severity**: Medium
**Location**: src/y.sol:L10
**Verdict**: CONFIRMED
**Description**: the real analysis block for DE-3.

## Chain Summary

| Finding ID | Location | Verdict | Severity |
|------------|----------|---------|----------|
| DE-3 | src/y.sol:L10 | CONFIRMED | Medium |
| DE-7 | src/z.sol:L88 | PARTIAL | Low |
"""
    blocks = P._parse_depth_finding_blocks(_write(art))
    de3 = [b for b in blocks if b["id"] == "DE-3"]
    assert len(de3) == 1, de3  # no double count from the row
    # The surviving DE-3 is the heading-parsed one (has its real description).
    assert "real analysis" in de3[0]["description"].lower(), de3
    assert de3[0].get("_low_confidence_rowonly") is None, de3
    # DE-7 is row-only → harvested as low-confidence.
    de7 = [b for b in blocks if b["id"] == "DE-7"]
    assert len(de7) == 1, de7
    assert de7[0].get("_low_confidence_rowonly") == "true", de7


def test_f2_non_chain_summary_table_rows_not_harvested():
    """A step-execution table (no location/severity/verdict column) is ignored,
    even if a cell happens to contain a promotable ID."""
    art = """# Depth Findings

## Step Execution Ledger

| Step | Status | Note |
|------|--------|------|
| DE-1 | done | this is a process log, not a finding row |
| DE-2 | skipped | N/A |
"""
    blocks = P._parse_depth_finding_blocks(_write(art))
    ids = [b["id"] for b in blocks]
    assert "DE-1" not in ids, ids
    assert "DE-2" not in ids, ids
    assert blocks == [], blocks


def test_f2_rules_applied_table_not_harvested():
    """A rules-applied table has no loc/sev/verdict column → not a finding
    catalog, so its rows are not harvested even with an ID-shaped first cell."""
    art = """# Depth Findings

| Rule | Applied | Reason |
|------|---------|--------|
| R4 | yes | escalated |
| R10 | no | single fixed state |
"""
    blocks = P._parse_depth_finding_blocks(_write(art))
    assert blocks == [], blocks


# ---------------------------------------------------------------------------
# F5: [EXTERNAL-ASSUMPTION] load-bearing body routing
# ---------------------------------------------------------------------------

def test_f5_external_assumption_with_concrete_harm_forces_body():
    """A quality keyword ('defense-in-depth') would normally route to APPENDIX,
    but an [EXTERNAL-ASSUMPTION] tag + concrete harm must force BODY."""
    # Harm phrasing chosen so it is NOT caught by the primary harm RE
    # (`receive fewer` / `less than` are F5-only), proving the F5 override
    # path — not the generic harm path — is what keeps this in the body.
    title = "Missing nonReentrant guard as defense-in-depth"
    harm = (
        "[EXTERNAL-ASSUMPTION: consumer decodes with Borsh] under the worst "
        "realistic condition claimants receive fewer tokens than they are owed."
    )
    disp, reason = P.classify_body_or_appendix(title, harm_text=harm)
    assert disp == "BODY", (disp, reason)
    assert "external-assumption" in reason.lower(), reason


def test_f5_pure_quality_defense_in_depth_still_appendix():
    """A pure defense-in-depth note with NO concrete harm and NO tag is
    unchanged: it still routes to APPENDIX."""
    title = "Add nonReentrant defense-in-depth to withdraw()"
    harm = "This is a hardening suggestion; no exploit is demonstrated."
    disp, reason = P.classify_body_or_appendix(title, harm_text=harm)
    assert disp == "APPENDIX", (disp, reason)


def test_f5_bare_tag_without_concrete_harm_stays_appendix():
    """The bare [EXTERNAL-ASSUMPTION] tag WITHOUT a concrete-harm phrase must
    NOT force BODY (prevents hardening-note bloat) — quality keyword wins."""
    title = "Add nonReentrant defense-in-depth"
    harm = "[EXTERNAL-ASSUMPTION: gateway config] recommended as a precaution."
    disp, reason = P.classify_body_or_appendix(title, harm_text=harm)
    assert disp == "APPENDIX", (disp, reason)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
