from pathlib import Path

import plamen_driver as D


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
