"""Tests for v2.6.3: Source ID implicit accounting + severity preservation."""
from __future__ import annotations

import pytest
from pathlib import Path

from plamen_mechanical import (
    _extract_source_ids_from_inventory,
    _collect_raw_candidate_ledger_rows,
)


@pytest.fixture
def scratchpad(tmp_path: Path) -> Path:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    return sp


INVENTORY_TEMPLATE = """\
# Finding Inventory

## Findings

### Finding [INV-001]: Block routes lack concurrency gate
**Severity**: High
**Location**: crates/p2p/src/server.rs
**Source IDs**: CC-15, DST-1
**Verdict**: Confirmed.

### Finding [INV-002]: Headers marked seen before acceptance
**Severity**: High
**Location**: crates/p2p/src/gossip_data_handler.rs
**Source IDs**: CC-04, VS-2
**Verdict**: Confirmed.

### Finding [INV-003]: No source IDs
**Severity**: Medium
**Location**: crates/p2p/src/peer.rs
**Verdict**: Confirmed.
"""


class TestExtractSourceIds:
    def test_basic_extraction(self, scratchpad: Path):
        (scratchpad / "findings_inventory.md").write_text(
            INVENTORY_TEMPLATE, encoding="utf-8"
        )
        ids = _extract_source_ids_from_inventory(scratchpad)
        assert "CC-15" in ids
        assert "DST-1" in ids
        assert "CC-04" in ids
        assert "VS-2" in ids
        assert len(ids) == 4

    def test_no_inventory_file(self, scratchpad: Path):
        ids = _extract_source_ids_from_inventory(scratchpad)
        assert ids == set()

    def test_deduped_inventory_also_scanned(self, scratchpad: Path):
        deduped = """\
# Finding Inventory (Deduped)

### Finding [INV-010]: Something
**Severity**: Medium
**Source IDs**: DST-1, PERT-2
"""
        (scratchpad / "findings_inventory_deduped.md").write_text(
            deduped, encoding="utf-8"
        )
        ids = _extract_source_ids_from_inventory(scratchpad)
        assert "DST-1" in ids
        assert "PERT-2" in ids

    def test_semicolon_separated(self, scratchpad: Path):
        inv = """\
### Finding [INV-001]: Test
**Source IDs**: CC-01; CC-02; SS-5
"""
        (scratchpad / "findings_inventory.md").write_text(inv, encoding="utf-8")
        ids = _extract_source_ids_from_inventory(scratchpad)
        assert ids == {"CC-01", "CC-02", "SS-5"}

    def test_non_matching_tokens_ignored(self, scratchpad: Path):
        """Tokens that don't match _INTERNAL_ID_RE are silently dropped."""
        inv = """\
### Finding [INV-001]: Test
**Source IDs**: CC-01, not-an-id, CC-02
"""
        (scratchpad / "findings_inventory.md").write_text(inv, encoding="utf-8")
        ids = _extract_source_ids_from_inventory(scratchpad)
        assert "CC-01" in ids
        assert "CC-02" in ids
        assert "not-an-id" not in ids

    def test_l1_real_pattern(self, scratchpad: Path):
        """Reproduce an observed L1 pattern: CC-XX, DST-X, DX-X, PERT-X,
        SGI-X, VS-X, SS-X source IDs that caused 196 UNACCOUNTED."""
        inv = """\
### Finding [INV-001]: Test
**Source IDs**: CC-15, DST-1
### Finding [INV-002]: Test
**Source IDs**: CC-04, DX-1, DX-2
### Finding [INV-003]: Test
**Source IDs**: PERT-1, SGI-1
### Finding [INV-004]: Test
**Source IDs**: VS-2, SS-3, CC-20
"""
        (scratchpad / "findings_inventory.md").write_text(inv, encoding="utf-8")
        ids = _extract_source_ids_from_inventory(scratchpad)
        expected = {"CC-15", "DST-1", "CC-04", "DX-1", "DX-2", "PERT-1",
                    "SGI-1", "VS-2", "SS-3", "CC-20"}
        assert ids == expected

    def test_bracketed_source_ids(self, scratchpad: Path):
        """Some inventory entries use [DST-1] with brackets in Source IDs."""
        inv = """\
### Finding [INV-001]: Test
**Source IDs**: [DST-1]
### Finding [INV-002]: Test
**Source IDs**: [PERT-2, DX-1, DX-2, DX-3, DX-4]
"""
        (scratchpad / "findings_inventory.md").write_text(inv, encoding="utf-8")
        ids = _extract_source_ids_from_inventory(scratchpad)
        assert "DST-1" in ids
        assert "PERT-2" in ids
        assert "DX-1" in ids


class TestCollectRawCandidateLedgerRows:
    def test_source_ids_not_unaccounted(self, scratchpad: Path):
        """Source IDs referenced by promoted INV entries should be PROMOTED,
        not UNACCOUNTED. This is the root cause of the 196 UNACCOUNTED bug."""
        (scratchpad / "findings_inventory.md").write_text(
            INVENTORY_TEMPLATE, encoding="utf-8"
        )
        promoted = {"INV-001", "INV-002", "INV-003"}
        excluded: set[str] = set()
        rows = _collect_raw_candidate_ledger_rows(scratchpad, promoted, excluded)
        unaccounted = [r for r in rows if "UNACCOUNTED" in r]
        assert len(unaccounted) == 0, (
            f"Expected 0 UNACCOUNTED, got {len(unaccounted)}: {unaccounted}"
        )

    def test_source_ids_marked_promoted(self, scratchpad: Path):
        (scratchpad / "findings_inventory.md").write_text(
            INVENTORY_TEMPLATE, encoding="utf-8"
        )
        promoted = {"INV-001", "INV-002", "INV-003"}
        rows = _collect_raw_candidate_ledger_rows(scratchpad, promoted, set())
        promoted_rows = [r for r in rows if "PROMOTED" in r]
        promoted_ids_in_rows = set()
        for r in promoted_rows:
            parts = [p.strip() for p in r.split("|") if p.strip()]
            if len(parts) >= 2:
                promoted_ids_in_rows.add(parts[1])
        for sid in ("CC-15", "DST-1", "CC-04", "VS-2"):
            assert sid in promoted_ids_in_rows, f"Source ID {sid} not PROMOTED"

    def test_genuinely_unknown_id_auto_excluded(self, scratchpad: Path):
        """An ID that appears in a scanned artifact but is NOT a Source ID
        of any promoted finding should be AUTO_EXCLUDED (v2.7.8)."""
        inv = """\
### Finding [INV-001]: Test
**Source IDs**: CC-01

Some other reference to BLIND-99 in the text.
"""
        (scratchpad / "findings_inventory.md").write_text(inv, encoding="utf-8")
        promoted = {"INV-001"}
        rows = _collect_raw_candidate_ledger_rows(scratchpad, promoted, set())
        auto_excluded = [r for r in rows if "AUTO_EXCLUDED" in r]
        auto_excluded_ids = set()
        for r in auto_excluded:
            parts = [p.strip() for p in r.split("|") if p.strip()]
            if len(parts) >= 2:
                auto_excluded_ids.add(parts[1])
        assert "BLIND-99" in auto_excluded_ids

    def test_verification_queue_source_ids_also_accounted(self, scratchpad: Path):
        """Source IDs appearing in verification_queue.md columns should also
        be accounted via the inventory Source IDs extraction."""
        (scratchpad / "findings_inventory.md").write_text(
            INVENTORY_TEMPLATE, encoding="utf-8"
        )
        vq = """\
| Finding ID | Severity | Primary Artifact | Source IDs |
|------------|----------|------------------|-----------|
| INV-001 | High | verify_INV-001.md | CC-15, DST-1 |
| INV-002 | High | verify_INV-002.md | CC-04, VS-2 |
"""
        (scratchpad / "verification_queue.md").write_text(vq, encoding="utf-8")
        promoted = {"INV-001", "INV-002", "INV-003"}
        rows = _collect_raw_candidate_ledger_rows(scratchpad, promoted, set())
        unaccounted = [r for r in rows if "UNACCOUNTED" in r]
        assert len(unaccounted) == 0, f"UNACCOUNTED: {unaccounted}"


class TestSeverityPreservation:
    """Test that _enforce_severity_matrix preserves verifier-stated severity."""

    def test_high_severity_preserved(self):
        from plamen_parsers import _enforce_severity_matrix
        verify_text = (
            "Severity: High\n"
            "Preferred Tag: [FUZZ-PASS]\n"
            "Evidence Tag: [CODE-TRACE]\n"
            "Verdict: CONFIRMED\n"
        )
        queue_row = {"severity": "High"}
        result = _enforce_severity_matrix(verify_text, queue_row)
        assert result == "High", f"Expected High, got {result}"

    def test_queue_only_high_demoted(self):
        from plamen_parsers import _enforce_severity_matrix
        verify_text = "Verdict: CONFIRMED\nLocation: foo.rs:L10\n"
        queue_row = {"severity": "High"}
        result = _enforce_severity_matrix(verify_text, queue_row)
        assert result == "Medium", f"Expected Medium (E7), got {result}"
