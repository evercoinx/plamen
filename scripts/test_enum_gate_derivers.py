"""Mechanical obligation-derivers added to the enumeration gate (L-04/L-08/L-10
classes). Each must fire on its bug-class SHAPE, NOT fire on a near-miss with the
guard present, be idempotent, and respect the shared run budget. Source-parse
based — needs the project tree at scratchpad.parent, no compile/Slither."""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def _eg():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("enumeration_gate")


def _proj(tmp_path: Path):
    """Make <root>/.scratchpad and a sibling src tree the gate can find."""
    root = tmp_path / "proj"
    sp = root / ".scratchpad"
    sp.mkdir(parents=True)
    (sp / "findings_inventory.md").write_text("# Inv\n", encoding="utf-8")
    return root, sp


def _sol(root: Path, rel: str, body: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\n" + body,
                 encoding="utf-8")


# ── L-10 array uniqueness ─────────────────────────────────────────────────────

def test_array_uniqueness_fires(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _sol(root, "Vault.sol",
         "contract Vault {\n"
         "  function startLiquidation(address[] calldata assets) external {\n"
         "    for (uint i; i < assets.length; i++) {\n"
         "      payout += share; IERC20(assets[i]).transfer(msg.sender, share);\n"
         "    }\n  }\n}\n")
    out = eg.compute_array_uniqueness_candidates(sp)
    assert any("startLiquidation" in c["title"] and "assets" in c["title"] for c in out)


def test_array_uniqueness_nearmiss_with_guard(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _sol(root, "Vault.sol",
         "contract Vault {\n"
         "  function batch(address[] calldata assets) external {\n"
         "    // sorted/unique enforced\n"
         "    for (uint i; i < assets.length; i++) {\n"
         "      require(unique[assets[i]] == false);\n"
         "      IERC20(assets[i]).transfer(msg.sender, 1);\n"
         "    }\n  }\n}\n")
    assert eg.compute_array_uniqueness_candidates(sp) == []


# ── L-08 unbounded input ──────────────────────────────────────────────────────

def test_unbounded_input_fires(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _sol(root, "Docs.sol",
         "contract Docs {\n  mapping(uint=>string) docs;\n"
         "  function uploadDocument(uint id, string calldata name) external {\n"
         "    docs[id] = name;\n  }\n}\n")
    out = eg.compute_unbounded_input_candidates(sp)
    assert any("uploadDocument" in c["title"] and "name" in c["title"] for c in out)


def test_unbounded_input_nearmiss_with_length_guard(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _sol(root, "Docs.sol",
         "contract Docs {\n  mapping(uint=>string) docs;\n"
         "  function uploadDocument(uint id, string calldata name) external {\n"
         "    require(bytes(name).length <= 256);\n    docs[id] = name;\n  }\n}\n")
    assert eg.compute_unbounded_input_candidates(sp) == []


# ── L-04 critical asset mover ─────────────────────────────────────────────────

_GRAPH_L04 = {
    "source": "evm-source",
    "var_refs": {
        # critical singleton handle, depended on by >=2 functions
        "QOrg.v4LiquidityTokenId": {"bare": "v4LiquidityTokenId", "refs": [
            "onLBPMigrated (QOrg.sol:L150)", "approvePositionOperator (QOrg.sol:L369)"]},
    },
    "functions": {
        "QOrg.withdrawERC721": {"bare": "withdrawERC721", "loc": "QOrg.sol:L222", "callers": []},
    },
}


def test_critical_asset_mover_fires(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    (sp / "_mechanical_graph.json").write_text(json.dumps(_GRAPH_L04), encoding="utf-8")
    _sol(root, "QOrg.sol",
         "contract QOrg {\n  uint256 public v4LiquidityTokenId;\n"
         "  function withdrawERC721(address t, uint256 tokenId, address to) external {\n"
         "    IERC721(t).safeTransferFrom(address(this), to, tokenId);\n  }\n}\n")
    out = eg.compute_critical_asset_mover_candidates(sp)
    assert any("withdrawERC721" in c["title"] and "v4LiquidityTokenId" in c["title"]
               for c in out), out


def test_critical_asset_mover_nearmiss_excluded(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    (sp / "_mechanical_graph.json").write_text(json.dumps(_GRAPH_L04), encoding="utf-8")
    # mover explicitly excludes the critical asset -> must NOT fire
    _sol(root, "QOrg.sol",
         "contract QOrg {\n  uint256 public v4LiquidityTokenId;\n"
         "  function withdrawERC721(address t, uint256 tokenId, address to) external {\n"
         "    require(tokenId != v4LiquidityTokenId);\n"
         "    IERC721(t).safeTransferFrom(address(this), to, tokenId);\n  }\n}\n")
    assert eg.compute_critical_asset_mover_candidates(sp) == []


def test_critical_asset_mover_no_graph_noop(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)   # no _mechanical_graph.json
    _sol(root, "QOrg.sol", "contract QOrg { uint256 public lpTokenId; }\n")
    assert eg.compute_critical_asset_mover_candidates(sp) == []


# ── shared emitter: end-to-end + idempotent + budget ──────────────────────────

def test_gate_emits_and_is_idempotent(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _sol(root, "Docs.sol",
         "contract Docs {\n  mapping(uint=>string) docs;\n"
         "  function uploadDocument(uint id, string calldata name) external {\n"
         "    docs[id] = name;\n  }\n}\n")
    res1 = eg.run_enumeration_gate(sp)
    inv = (sp / "findings_inventory.md").read_text(encoding="utf-8")
    assert res1["emitted"] >= 1 and "ENUMGAP" in inv and "uploadDocument" in inv
    # re-run: idempotent, no duplicate emission
    res2 = eg.run_enumeration_gate(sp)
    assert res2["emitted"] == 0
    assert (sp / "findings_inventory.md").read_text(encoding="utf-8").count(
        "UNBOUND") <= inv.count("UNBOUND") + 0


def test_emitter_respects_cap(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    cands = [{"key": f"K{i}", "title": f"t{i}", "location": "l", "source_note": "n",
              "root_cause": "rc", "description": "d", "impact": "im"} for i in range(20)]
    assert eg._emit_candidates(sp, cands, cap=5) == 5


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
