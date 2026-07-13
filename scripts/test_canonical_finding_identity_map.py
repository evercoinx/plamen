from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_mechanical import (  # noqa: E402
    _allocate_inventory_ledger_id,
    _write_canonical_finding_identity_map,
)
from plamen_parsers import id_ledger_register  # noqa: E402


def _finding(fid: str, title: str, location: str = "src/A.sol:L1") -> str:
    return (
        f"## Finding [{fid}]: {title}\n\n"
        "**Severity**: High\n"
        f"**Location**: {location}\n"
        "**Root Cause**: state update happens after external call\n"
        "**Source IDs**: INV-001\n\n"
        "Substantive exploit narrative.\n"
    )


def test_canonical_identity_map_is_deterministic_and_non_mutating(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    artifact = sp / "analysis_token_flow.md"
    original = "# Token Flow\n\n" + _finding("LOCAL-1", "Withdraw accounting drift")
    artifact.write_text(original, encoding="utf-8")

    count1 = _write_canonical_finding_identity_map(
        sp, phase_name="breadth", pipeline="sc", mode="core"
    )
    first = json.loads((sp / "_canonical_finding_ids.json").read_text(encoding="utf-8"))
    count2 = _write_canonical_finding_identity_map(
        sp, phase_name="breadth", pipeline="sc", mode="core"
    )
    second = json.loads((sp / "_canonical_finding_ids.json").read_text(encoding="utf-8"))

    assert count1 == count2 == 1
    assert artifact.read_text(encoding="utf-8") == original
    assert first["records"][0]["canonical_id"] == second["records"][0]["canonical_id"]
    assert first["records"][0]["fingerprint"] == second["records"][0]["fingerprint"]
    assert first["records"][0]["local_id"] == "LOCAL-1"
    assert first["records"][0]["referenced_ids"] == ["INV-001"]


def test_canonical_identity_map_preserves_distinct_artifact_findings(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    body = _finding("DUP-1", "Same title", "src/A.sol:L7")
    (sp / "analysis_token_flow.md").write_text(body, encoding="utf-8")
    (sp / "analysis_access_control.md").write_text(body, encoding="utf-8")

    count = _write_canonical_finding_identity_map(
        sp, phase_name="breadth", pipeline="sc", mode="core"
    )
    payload = json.loads((sp / "_canonical_finding_ids.json").read_text(encoding="utf-8"))
    ids = {row["canonical_id"] for row in payload["records"]}

    assert count == 2
    assert len(ids) == 2
    assert {row["artifact"] for row in payload["records"]} == {
        "analysis_access_control.md",
        "analysis_token_flow.md",
    }


def test_unmapped_future_id_tokens_are_captured_without_blocking(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "report_index.md").write_text(
        "| H-01 | Future token | High | ZKX-77 |\n"
        "| H-02 | Standard mention | High | ERC-20 |\n",
        encoding="utf-8",
    )

    _write_canonical_finding_identity_map(
        sp, phase_name="report_index", pipeline="sc", mode="core"
    )
    payload = json.loads((sp / "_unmapped_id_tokens.json").read_text(encoding="utf-8"))
    tokens = {row["token"] for row in payload["tokens"]}

    assert "ZKX-77" in tokens
    assert "ERC-20" not in tokens


def test_unmapped_id_tokens_ignore_source_line_ranges(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "analysis_cross_chain.md").write_text(
        "### Finding [CC-1]: issue\n\n"
        "**Severity**: High\n"
        "**Location**: CrossChainRouter.sol:L571-590, "
        "NativeVault.sol:L661-680\n"
        "**Description**: mentions code at L355-365 and real future ZKX-77.\n",
        encoding="utf-8",
    )

    _write_canonical_finding_identity_map(
        sp, phase_name="breadth", pipeline="sc", mode="core"
    )
    payload = json.loads((sp / "_unmapped_id_tokens.json").read_text(encoding="utf-8"))
    tokens = {row["token"] for row in payload["tokens"]}

    assert "ZKX-77" in tokens
    assert "L571-590" not in tokens
    assert "L661-680" not in tokens
    assert "L355-365" not in tokens


def test_phase_validator_refreshes_identity_sidecar_after_breadth(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "_audit_started_with_markers.json").write_text("{}", encoding="utf-8")
    (sp / "spawn_manifest.md").write_text(
        "| Template | Required? | Agent ID | Focus Area | Expected Output | Status | Type |\n"
        "|---|---|---|---|---|---|---|\n"
        "| T | YES | B1 | token | analysis_token.md | PENDING | agent |\n",
        encoding="utf-8",
    )
    (sp / "analysis_token.md").write_text(
        "<!-- PLAMEN_ARTIFACT: analysis_token.md -->\n"
        "<!-- PLAMEN_OWNER: B1 -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: breadth -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n"
        "<!-- AGENT_ROW: B1 -->\n"
        "<!-- EXPECTED_OUTPUT: analysis_token.md -->\n\n"
        + _finding("BREADTH-1", "Token finding")
        + "\n<!-- PLAMEN_STATUS: COMPLETE -->\n",
        encoding="utf-8",
    )
    phase = D.Phase(
        name="breadth",
        section_markers=[],
        expected_artifacts=["analysis_*.md"],
        base_timeout_s=1,
        min_artifact_bytes=50,
        min_artifacts_count=1,
    )

    passed, missing = D._run_phase_validators(
        phase,
        {"mode": "core", "pipeline": "sc", "project_root": str(tmp_path)},
        sp,
        [],
        0,
        {},
    )

    assert passed is True, missing
    payload = json.loads((sp / "_canonical_finding_ids.json").read_text(encoding="utf-8"))
    assert payload["record_count"] == 1
    assert payload["records"][0]["local_id"] == "BREADTH-1"


def test_inventory_ledger_collision_allocates_fresh_inv_id(tmp_path: Path):
    id_ledger_register(
        tmp_path,
        finding_id="INV-001",
        owner_phase="inventory",
        owner_attempt=1,
        owning_artifact="findings_inventory.md",
        title="old root cause",
    )

    inv_id = _allocate_inventory_ledger_id(
        tmp_path,
        preferred_id="INV-001",
        owner_phase="inventory",
        owning_artifact="findings_inventory.md",
        title="new root cause",
    )

    assert inv_id == "INV-002"
