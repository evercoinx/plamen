from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_parsers as P  # noqa: E402
import plamen_mechanical as M  # noqa: E402
import plamen_validators as V  # noqa: E402


def test_inventory_parser_captures_optional_discovery_metadata(tmp_path):
    inv = tmp_path / "findings_inventory.md"
    inv.write_text(
        "\n".join([
            "# Findings Inventory",
            "",
            "### Finding [INV-001]: Optional metadata is preserved",
            "**Severity**: High",
            "**Location**: Vault.sol:L42",
            "**Verdict**: PARTIAL",
            "**Root Cause**: branch omits accounting update",
            "**Description**: mismatch in settlement path",
            "**Impact**: loss under reachable state",
            "**Discovery Steer** (OPTIONAL): branch creates `pendingShares` mismatch",
            "**Missing Precondition** (if PARTIAL): `pendingShares` already nonzero",
            "**Precondition Type**: STATE",
            "**Postconditions Created**: pending claim remains open",
            "**Postcondition Types**: STATE, BALANCE",
            "**Semantic Invariant**: claims settle exactly once",
            "**Branch Preconditions**: delayed settlement branch reachable",
            "**Terminal Mechanism**: stale claim consumption",
            "**Composition Candidates**: INV-002",
            "",
        ]),
        encoding="utf-8",
    )

    parsed = P._parse_inventory_chunk(inv)

    assert len(parsed) == 1
    entry = parsed[0]
    assert entry["discovery_steer"] == "branch creates pendingShares mismatch"
    assert entry["missing_precondition"] == "pendingShares already nonzero"
    assert entry["precondition_type"] == "STATE"
    assert entry["postconditions_created"] == "pending claim remains open"
    assert entry["postcondition_types"] == "STATE, BALANCE"
    assert entry["semantic_invariant"] == "claims settle exactly once"
    assert entry["branch_preconditions"] == "delayed settlement branch reachable"
    assert entry["terminal_mechanism"] == "stale claim consumption"
    assert entry["composition_candidates"] == "INV-002"


def test_depth_parser_captures_optional_discovery_metadata(tmp_path):
    depth = tmp_path / "depth_state_findings.md"
    depth.write_text(
        "\n".join([
            "### Finding [ST-1]: Optional metadata survives promotion",
            "**Severity**: Medium",
            "**Location**: Vault.sol:L77",
            "**Verdict**: CONFIRMED",
            "**Description**: state trace confirmed mismatch",
            "**Discovery Steer**: candidate ID INV-003 via `claimCursor`",
            "**Terminal Mechanism**: terminal stale cursor read",
            "",
        ]),
        encoding="utf-8",
    )

    parsed = P._parse_depth_finding_blocks(depth)

    assert len(parsed) == 1
    assert parsed[0]["discovery_steer"] == "candidate ID INV-003 via claimCursor"
    assert parsed[0]["terminal_mechanism"] == "terminal stale cursor read"


def test_mechanical_inventory_preserves_optional_discovery_metadata(tmp_path):
    chunk = tmp_path / "findings_inventory_chunk_a.md"
    chunk.write_text(
        "\n".join([
            "# Inventory Chunk",
            "",
            "### Finding [CC-1]: Metadata survives mechanical merge",
            "**Severity**: High",
            "**Location**: Vault.sol:L42",
            "**Verdict**: PARTIAL",
            "**Root Cause**: branch omits accounting update",
            "**Description**: mismatch in settlement path",
            "**Impact**: loss under reachable state",
            "**Discovery Steer**: branch creates `pendingShares` mismatch",
            "**Missing Precondition**: `pendingShares` already nonzero",
            "**Terminal Mechanism**: stale claim consumption",
            "",
        ]),
        encoding="utf-8",
    )

    parsed, merged = M._write_mechanical_inventory_from_chunks(tmp_path)

    assert parsed == 1
    assert merged == 1
    text = (tmp_path / "findings_inventory.md").read_text(encoding="utf-8")
    assert "**Discovery Steer**: branch creates pendingShares mismatch" in text
    assert "**Missing Precondition**: pendingShares already nonzero" in text
    assert "**Terminal Mechanism**: stale claim consumption" in text


def test_depth_promotion_preserves_optional_discovery_metadata(tmp_path):
    (tmp_path / "findings_inventory.md").write_text(
        "# Finding Inventory\n\n## Findings\n\n",
        encoding="utf-8",
    )
    (tmp_path / "depth_state_trace_findings.md").write_text(
        "\n".join([
            "### Finding [DST-1]: Depth metadata survives promotion",
            "**Severity**: High",
            "**Location**: Vault.sol:L77",
            "**Verdict**: CONFIRMED",
            "**Preferred Tag**: CODE-TRACE",
            "**Description**: state trace confirmed mismatch",
            "**Discovery Steer**: branch creates `claimCursor` mismatch",
            "**Terminal Mechanism**: stale claim consumption",
            "",
        ]),
        encoding="utf-8",
    )

    promoted = V._promote_depth_findings_to_inventory(tmp_path)

    assert promoted == ["DST-1"]
    text = (tmp_path / "findings_inventory.md").read_text(encoding="utf-8")
    assert "**Source IDs**: [DST-1]" in text
    assert "**Discovery Steer**: branch creates claimCursor mismatch" in text
    assert "**Terminal Mechanism**: stale claim consumption" in text
