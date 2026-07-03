"""Report-assembly LOCATION recovery: a tier writer that overwrites a real
finding's **Location** with a non-existent path is repaired against the
validated report_index Master Finding Index — the finding is RECOVERED, never
dropped. (The StrategyFactory.sol-class corruption.)"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _mech():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_mechanical")


def _proj(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "core").mkdir(parents=True)
    (root / "libraries").mkdir(parents=True)
    (root / "core" / "UmiaMarketManager.sol").write_text("x\n" * 900, encoding="utf-8")
    (root / "libraries" / "GovernanceVM.sol").write_text("y\n" * 200, encoding="utf-8")
    return root


def test_corrupted_location_recovered_from_index(tmp_path: Path):
    m = _mech()
    root = _proj(tmp_path)
    # tier writer corrupted M-25's location with a non-existent file
    section = (
        "## Medium Findings\n\n"
        "### [M-25] CALL action has no freshness validation\n\n"
        "**Severity**: Medium\n"
        "**Location**: `src/factories/StrategyFactory.sol`\n\n"
        "**Description**: TOCTOU over the multi-day window.\n"
    )
    id_to_location = {"M-25": "libraries/GovernanceVM.sol:L130-135; core/UmiaMarketManager.sol:L720-749"}
    patched, n = m._recover_tier_locations(section, id_to_location, str(root))
    assert n == 1
    assert "StrategyFactory.sol" not in patched
    assert "GovernanceVM.sol" in patched
    # the finding itself is preserved (recovered, not dropped)
    assert "### [M-25]" in patched


def test_valid_location_is_untouched(tmp_path: Path):
    m = _mech()
    root = _proj(tmp_path)
    section = (
        "### [M-26] real finding\n\n"
        "**Location**: `core/UmiaMarketManager.sol:L666-674`\n\n"
        "**Description**: x\n"
    )
    id_to_location = {"M-26": "libraries/GovernanceVM.sol:L1"}
    patched, n = m._recover_tier_locations(section, id_to_location, str(root))
    assert n == 0   # body location is real -> not corruption -> untouched
    assert "UmiaMarketManager.sol:L666-674" in patched


def test_no_recover_when_index_also_missing(tmp_path: Path):
    m = _mech()
    root = _proj(tmp_path)
    section = (
        "### [M-27] x\n\n**Location**: `nope/Ghost.sol`\n\n**Description**: x\n"
    )
    id_to_location = {"M-27": "also/Missing.sol:L9"}  # index loc also doesn't resolve
    patched, n = m._recover_tier_locations(section, id_to_location, str(root))
    assert n == 0   # cannot recover to a bad location
    assert "nope/Ghost.sol" in patched


def test_partial_real_location_not_treated_as_corruption(tmp_path: Path):
    m = _mech()
    root = _proj(tmp_path)
    section = (
        "### [M-28] x\n\n"
        "**Location**: `core/UmiaMarketManager.sol:L1; ghost/Fake.sol:L2`\n\n"
    )
    id_to_location = {"M-28": "libraries/GovernanceVM.sol:L1"}
    patched, n = m._recover_tier_locations(section, id_to_location, str(root))
    assert n == 0   # at least one real file cited -> not corruption
    assert "UmiaMarketManager.sol" in patched


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
