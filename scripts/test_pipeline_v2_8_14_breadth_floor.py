"""Pipeline v2.8.14 — Complex-tier breadth floor.

Discrepancy (PulsechainGameWards): the instantiate Merge Hierarchy collapsed
breadth to 5 agents on a Complex codebase (>10 contracts / >5000 lines), below
the documented Complex floor of 7-9 (phase2-instantiate Step 2a) — merging away
whole lenses (NFT/economic/centralization). The 300-line merge cap is an upper
bound on per-agent payload, not a license to drop below the tier floor, and
nothing enforced it. Recall risk on big audits.

Fix: `_codebase_is_complex` (from contract_inventory.md) + a floor check in
`_validate_spawn_manifest_schema` that rejects a <7-agent breadth manifest on a
Complex codebase (retry-driven, not a halt). Conservative on missing data.

Run: pytest scripts/test_pipeline_v2_8_14_breadth_floor.py -q
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_validators as V  # noqa: E402

_HDR = "# Contract Inventory\n\n| File | Path | Lines | Bytes |\n|------|------|-------|-------|\n"


def _inv(rows: str) -> Path:
    sp = Path(tempfile.mkdtemp(prefix="plamen_v2814_"))
    (sp / "contract_inventory.md").write_text(_HDR + rows, encoding="utf-8")
    return sp


def test_complex_by_total_lines():
    # One big contract over the 5000-line threshold.
    assert V._codebase_is_complex(_inv("| Big.sol | core/Big.sol | 6000 | 99 |\n")) is True


def test_complex_by_contract_count():
    rows = "".join(f"| C{i}.sol | core/C{i}.sol | 100 | 99 |\n" for i in range(12))
    assert V._codebase_is_complex(_inv(rows)) is True


def test_simple_not_complex():
    rows = "| A.sol | src/A.sol | 300 | 9 |\n| B.sol | src/B.sol | 200 | 9 |\n"
    assert V._codebase_is_complex(_inv(rows)) is False


def test_interfaces_and_mocks_excluded_from_count():
    # 12 rows but mostly interfaces/mocks → real contract count <= 10, lines low.
    rows = (
        "| Core.sol | core/Core.sol | 400 | 9 |\n"
        + "".join(f"| IFace{i}.sol | interface/IFace{i}.sol | 20 | 9 |\n" for i in range(11))
    )
    # total lines 400+220 = 620 (<5000); real contracts = 1 (interfaces excluded) → not complex
    assert V._codebase_is_complex(_inv(rows)) is False


def test_missing_inventory_not_enforced():
    empty = Path(tempfile.mkdtemp(prefix="plamen_v2814_empty_"))
    assert V._codebase_is_complex(empty) is False  # conservative: no false halt
