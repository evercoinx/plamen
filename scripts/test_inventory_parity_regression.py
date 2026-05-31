from pathlib import Path

import plamen_driver as D
import plamen_validators as V


def test_inventory_receipt_uses_canonical_blocks_not_source_id_table_rows(tmp_path: Path):
    scratchpad = tmp_path
    (scratchpad / "findings_inventory_chunk_a.md").write_text(
        "\n".join([
            "## Finding [AC-1]: First issue",
            "**Severity**: High",
            "**Location**: src/A.sol:L1",
            "**Source IDs**: AC-1",
            "**Root Cause**: first",
            "",
            "## Finding [AC-2]: Second issue",
            "**Severity**: Low",
            "**Location**: src/B.sol:L2",
            "**Source IDs**: AC-2",
            "**Root Cause**: second",
            "",
        ]),
        encoding="utf-8",
    )
    (scratchpad / "findings_inventory.md").write_text(
        "\n".join([
            "# Finding Inventory",
            "",
            "## Findings",
            "",
            "### Finding [INV-001]: First issue",
            "**Severity**: High",
            "**Location**: src/A.sol:L1",
            "**Source IDs**: AC-1",
            "**Root Cause**: first",
            "",
            "### Finding [INV-002]: Second issue",
            "**Severity**: Low",
            "**Location**: src/B.sol:L2",
            "**Source IDs**: AC-2",
            "**Root Cause**: second",
            "",
            "## Traceability",
            "",
            "| Finding ID | Source IDs |",
            "|------------|------------|",
            "| INV-001 | AC-1 |",
            "| INV-002 | AC-2 |",
            "",
        ]),
        encoding="utf-8",
    )
    (scratchpad / "inventory_merge_receipt.md").write_text(
        "\n".join([
            "# Mechanical Inventory Merge Receipt",
            "",
            "Chunk files: 1",
            "Parsed chunk findings: 2",
            "Merged inventory findings: 2",
            "",
        ]),
        encoding="utf-8",
    )

    issues = D._validate_inventory_parity(scratchpad)

    assert issues == []


def test_inventory_parity_validates_immutable_base_after_working_inventory_changes(tmp_path: Path):
    scratchpad = tmp_path
    (scratchpad / "findings_inventory_chunk_a.md").write_text(
        "\n".join([
            "## Finding [AC-1]: First issue",
            "**Severity**: High",
            "**Location**: src/A.sol:L1",
            "**Source IDs**: AC-1",
            "",
            "## Finding [AC-2]: Second issue",
            "**Severity**: Low",
            "**Location**: src/B.sol:L2",
            "**Source IDs**: AC-2",
            "",
        ]),
        encoding="utf-8",
    )
    base_inventory = "\n".join([
        "# Finding Inventory",
        "",
        "### Finding [INV-001]: First issue",
        "**Severity**: High",
        "**Location**: src/A.sol:L1",
        "**Source IDs**: AC-1",
        "",
        "### Finding [INV-002]: Second issue",
        "**Severity**: Low",
        "**Location**: src/B.sol:L2",
        "**Source IDs**: AC-2",
        "",
    ])
    (scratchpad / "findings_inventory_base.md").write_text(base_inventory, encoding="utf-8")
    # Simulate a later semantic-dedup/current-state projection. Resume parity
    # for the completed inventory phase must not compare this transformed view
    # to the original merge receipt.
    (scratchpad / "findings_inventory.md").write_text(
        "\n".join([
            "# Finding Inventory",
            "",
            "### Finding [INV-001]: First issue",
            "**Severity**: High",
            "**Location**: src/A.sol:L1",
            "**Source IDs**: AC-1",
            "",
        ]),
        encoding="utf-8",
    )
    (scratchpad / "inventory_merge_receipt.md").write_text(
        "\n".join([
            "# Mechanical Inventory Merge Receipt",
            "",
            "Chunk files: 1",
            "Parsed chunk findings: 2",
            "Merged inventory findings: 2",
            "",
        ]),
        encoding="utf-8",
    )

    issues = D._validate_inventory_parity(scratchpad)

    assert issues == []


# --------------------------------------------------------------------------- #
# AP-INVPAR-1 — parity accounts for the disjoint niche-promoted section         #
# --------------------------------------------------------------------------- #

def _finding_block(prefix: str, n: int, fid_kind: str = "INV") -> str:
    return "\n".join([
        f"### Finding [{fid_kind}-{n:03d}]: {prefix} issue {n}",
        "**Severity**: Medium",
        f"**Location**: src/F{n}.sol:L{n}",
        f"**Source IDs**: {prefix}-{n}",
        f"**Root Cause**: cause {n}",
        "",
    ])


def _build_inventory_with_promotions(scratchpad: Path, *,
                                     receipt_promoted: int,
                                     niche_blocks: int) -> None:
    """7 base INV blocks (chunk-sourced) + 1 depth-promotion-supplement block +
    `niche_blocks` niche-promoted blocks. Receipt records merged=parsed=7 and a
    depth-promotion receipt of `receipt_promoted` depth finding(s)."""
    # Chunk source: 7 findings AC-1..AC-7.
    chunk = ["# Chunk A"]
    for i in range(1, 8):
        chunk.append("\n".join([
            f"## Finding [AC-{i}]: base issue {i}",
            "**Severity**: Medium",
            f"**Location**: src/F{i}.sol:L{i}",
            f"**Source IDs**: AC-{i}",
            f"**Root Cause**: cause {i}",
            "",
        ]))
    (scratchpad / "findings_inventory_chunk_a.md").write_text(
        "\n".join(chunk), encoding="utf-8")

    body = ["# Finding Inventory", "", "## Findings", ""]
    for i in range(1, 8):
        body.append("\n".join([
            f"### Finding [INV-{i:03d}]: base issue {i}",
            "**Severity**: Medium",
            f"**Location**: src/F{i}.sol:L{i}",
            "**Source IDs**: AC-" + str(i),
            f"**Root Cause**: cause {i}",
            "",
        ]))
    body += ["## Depth Promotion Supplement", "", _finding_block("DEPTH", 101)]
    if niche_blocks:
        body += ["## Niche-Promoted Findings", ""]
        for j in range(niche_blocks):
            body.append(_finding_block("NICHE", 201 + j))
    (scratchpad / "findings_inventory.md").write_text(
        "\n".join(body), encoding="utf-8")

    (scratchpad / "inventory_merge_receipt.md").write_text(
        "\n".join([
            "# Mechanical Inventory Merge Receipt",
            "",
            "Chunk files: 1",
            "Parsed chunk findings: 7",
            "Merged inventory findings: 7",
            "",
        ]),
        encoding="utf-8",
    )
    (scratchpad / "depth_promotion_receipt.md").write_text(
        f"# Depth Promotion Receipt\n\nPromoted {receipt_promoted} depth finding(s).\n",
        encoding="utf-8",
    )


def test_inventory_parity_accounts_for_niche_promoted_section(tmp_path: Path):
    _build_inventory_with_promotions(tmp_path, receipt_promoted=1, niche_blocks=2)
    issues = D._validate_inventory_parity(tmp_path)
    assert issues == [], issues


def test_count_niche_promotion_inventory_blocks():
    text = "\n".join([
        "## Niche-Promoted Findings",
        "",
        "### Finding [INV-301]: a\n**Location**: x:L1\n",
        "### Finding [INV-302]: b\n**Location**: y:L2\n",
    ])
    assert V._count_niche_promotion_inventory_blocks(text) == 2
    assert V._count_niche_promotion_inventory_blocks("# no section") == 0


def test_inventory_parity_still_catches_truncation(tmp_path: Path):
    # 7 chunk source IDs but only 2 inventory blocks → Path C / coverage fires.
    chunk = ["# Chunk A"]
    for i in range(1, 8):
        chunk.append("\n".join([
            f"## Finding [AC-{i}]: base issue {i}",
            "**Severity**: Medium",
            f"**Location**: src/F{i}.sol:L{i}",
            f"**Source IDs**: AC-{i}",
            "",
        ]))
    (tmp_path / "findings_inventory_chunk_a.md").write_text(
        "\n".join(chunk), encoding="utf-8")
    body = ["# Finding Inventory", "", "## Findings", ""]
    for i in range(1, 3):
        body.append("\n".join([
            f"### Finding [INV-{i:03d}]: base issue {i}",
            "**Severity**: Medium",
            f"**Location**: src/F{i}.sol:L{i}",
            f"**Source IDs**: AC-{i}",
            "",
        ]))
    (tmp_path / "findings_inventory.md").write_text("\n".join(body), encoding="utf-8")
    (tmp_path / "inventory_merge_receipt.md").write_text(
        "# Mechanical Inventory Merge Receipt\n\nChunk files: 1\n"
        "Parsed chunk findings: 7\nMerged inventory findings: 2\n",
        encoding="utf-8",
    )
    issues = D._validate_inventory_parity(tmp_path)
    assert issues, "truncation must still be flagged"
