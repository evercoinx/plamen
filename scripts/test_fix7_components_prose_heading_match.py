"""FIX #7 regression test.

Original failure: `_synthesize_components_audited` third fallback tier
(subsystem_map.md headings) produced an all-zero "Covered (findings)" column
when subsystem headings were prose (e.g. "Rate Limiting (mempool)") because they
never matched path-token finding locations (e.g. "mempool/rate_limiting.rs").

FIX #7 pre-normalizes each heading to a path token (strip parentheticals,
lowercase, spaces/hyphens -> underscores) and matches that token against the
lowercased finding path. Presentation-only: it changes no finding, only counts.

Recall-safety properties asserted here:
  - No finding is dropped, merged, or hidden (function only emits component
    names + counts; findings_inventory.md is read-only input).
  - The new clause is purely additive (counts can only rise, never fall).
"""

import tempfile
from pathlib import Path

import plamen_mechanical as m


def _mktmp() -> Path:
    return Path(tempfile.mkdtemp())


def _parse_covered(table: str) -> dict[str, int]:
    """Parse the Component -> Covered(findings) count column."""
    out: dict[str, int] = {}
    for line in table.splitlines():
        s = line.strip()
        if not s.startswith("|") or s.startswith("| Component") or set(s) <= set("|-: "):
            continue
        cells = [c.strip(" `") for c in s.strip("|").split("|")]
        if len(cells) >= 2:
            try:
                out[cells[0]] = int(cells[1])
            except ValueError:
                continue
    return out


def test_prose_heading_with_parenthetical_attributes_findings():
    """REPRODUCES original all-zero bug; PROVES fix attributes findings."""
    d = _mktmp()
    (d / "subsystem_map.md").write_text(
        "# Subsystem Map\n\n"
        "## Rate Limiting (mempool)\n"
        "Mempool ingress throttling.\n\n"
        "## Gossip Validation\n"
        "Inbound gossip checks.\n",
        encoding="utf-8",
    )
    (d / "findings_inventory.md").write_text(
        "# Findings Inventory\n\n"
        "## Finding [H-1]: Rate limit bypass\n"
        "**Severity**: High\n"
        "**Location**: mempool/rate_limiting.rs:L42\n\n"
        "## Finding [H-2]: Second rate limit issue\n"
        "**Severity**: High\n"
        "**Location**: mempool/rate_limiting.rs:L80\n",
        encoding="utf-8",
    )
    table = m._synthesize_components_audited(d)
    covered = _parse_covered(table)
    # Pre-fix this was 0 (prose heading never matched the path token).
    assert covered.get("Rate Limiting (mempool)") == 2
    # Heading with no matching findings stays at 0 (no spurious inflation).
    assert covered.get("Gossip Validation") == 0


def test_no_finding_text_leaks_into_components_table():
    """The components table must only contain component names + counts, never
    finding titles or descriptions (no finding is surfaced or hidden here)."""
    d = _mktmp()
    (d / "subsystem_map.md").write_text(
        "# Subsystem Map\n\n## Rate Limiting (mempool)\nx\n",
        encoding="utf-8",
    )
    (d / "findings_inventory.md").write_text(
        "# Findings Inventory\n\n"
        "## Finding [H-1]: SECRET_FINDING_TITLE_DO_NOT_LEAK\n"
        "**Severity**: High\n"
        "**Location**: mempool/rate_limiting.rs:L42\n",
        encoding="utf-8",
    )
    table = m._synthesize_components_audited(d)
    assert "SECRET_FINDING_TITLE_DO_NOT_LEAK" not in table


def test_fix_is_additive_only_never_lowers_existing_matches():
    """A heading that already matched via the legacy substring rule
    (`h_norm in rel_norm`) must still match after the fix (count not lowered)."""
    d = _mktmp()
    # Legacy-style heading that IS a path prefix -> matched pre-fix.
    (d / "subsystem_map.md").write_text(
        "# Subsystem Map\n\n## mempool\ny\n",
        encoding="utf-8",
    )
    (d / "findings_inventory.md").write_text(
        "# Findings Inventory\n\n"
        "## Finding [H-1]: x\n"
        "**Severity**: High\n"
        "**Location**: mempool/rate_limiting.rs:L42\n",
        encoding="utf-8",
    )
    table = m._synthesize_components_audited(d)
    covered = _parse_covered(table)
    assert covered.get("mempool") == 1


def test_ledger_tier_unaffected_by_fix():
    """FIX #7 lives only in the subsystem_map fallback. When
    file_coverage_ledger.md exists, that tier is used and the fix is bypassed
    entirely (no behavior change for the primary tier)."""
    d = _mktmp()
    (d / "file_coverage_ledger.md").write_text(
        "| File | Status |\n"
        "|------|--------|\n"
        "| `mempool/rate_limiting.rs` | Covered |\n",
        encoding="utf-8",
    )
    # subsystem_map present but must NOT be consulted because ledger wins.
    (d / "subsystem_map.md").write_text(
        "# Subsystem Map\n\n## Rate Limiting (mempool)\nz\n",
        encoding="utf-8",
    )
    table = m._synthesize_components_audited(d)
    assert "Files Catalogued" in table  # ledger-tier header
    assert "Coverage Source" not in table  # subsystem-tier header absent
