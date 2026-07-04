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


# ── M1 (recall-build-plan §5.3): run_enumeration_gate folds M1's emitted count ──
# into its aggregate `emitted`, surfaces it as `invariant_emitted`, and keeps the
# INVARIANT pool INDEPENDENT of the co-ref (_MAX_ENUMGAP_PER_RUN) pool.

def _ci_block(n: int, shape: str = "CONSERVATION", fclass: str = "conservation",
              loc: str = "src/Vault.sol:L142") -> str:
    return (
        f"committed-invariant [CI-{n}]\n"
        f"Locus: {loc}  (fn: settle)\n"
        f"Shape: {shape}\n"
        f"Assertion: assert(relation holds at {loc})\n"
        f"Falsify Class: {fclass}\n"
        f"Provenance: skeptic NO-GAP @ instance-{n}\n"
    )


def test_gate_folds_m1_emitted_into_aggregate(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    (sp / "exploration_skeptic_findings.md").write_text(
        "# Exploration Skeptic Findings\n\n"
        + _ci_block(1) + "\n\n" + _ci_block(2) + "\n",
        encoding="utf-8",
    )
    res = eg.run_enumeration_gate(sp)
    # M1's count is surfaced additively and folded into the aggregate emitted.
    assert res.get("invariant_emitted", 0) == 2, res
    assert res["emitted"] >= res["invariant_emitted"], res
    inv = (sp / "findings_inventory.md").read_text(encoding="utf-8")
    assert "INVARIANT" in inv and "CI-1" in inv and "CI-2" in inv


def test_gate_m1_independent_pool_accounting(tmp_path: Path):
    """25 CI blocks -> M1 capped at its OWN _MAX_PER_DERIVER (15) per run,
    INDEPENDENT of the co-ref gate's separate 40-slot pool: the cap is 15, not 40
    and not 0-starved. Independent-pool accounting holds."""
    eg = _eg()
    root, sp = _proj(tmp_path)
    blocks = "\n\n".join(_ci_block(i + 1) for i in range(25))
    (sp / "exploration_skeptic_findings.md").write_text(
        "# Exploration Skeptic Findings\n\n" + blocks + "\n", encoding="utf-8",
    )
    res = eg.run_enumeration_gate(sp)
    # Own pool honored: exactly _MAX_PER_DERIVER emitted (not the co-ref 40 cap,
    # not starved to 0 by the co-ref pool).
    assert res.get("invariant_emitted", 0) == eg._MAX_PER_DERIVER, res


def test_gate_m1_idempotent_no_duplicate(tmp_path: Path):
    """An at-cap CI set (<= _MAX_PER_DERIVER) fully drains in one run; a second
    run emits 0 (receipt honored — no duplicate INVARIANT emission)."""
    eg = _eg()
    root, sp = _proj(tmp_path)
    n = eg._MAX_PER_DERIVER
    blocks = "\n\n".join(_ci_block(i + 1) for i in range(n))
    (sp / "exploration_skeptic_findings.md").write_text(
        "# Exploration Skeptic Findings\n\n" + blocks + "\n", encoding="utf-8",
    )
    res1 = eg.run_enumeration_gate(sp)
    assert res1.get("invariant_emitted", 0) == n, res1
    res2 = eg.run_enumeration_gate(sp)
    assert res2.get("invariant_emitted", 0) == 0, res2


def test_gate_no_ci_returns_backward_compat_dict(tmp_path: Path):
    """Clean no-CI run returns the exact 3-key dict prior callers assert on
    (no `invariant_emitted` key leaks when M1 emits nothing)."""
    eg = _eg()
    root, sp = _proj(tmp_path)
    res = eg.run_enumeration_gate(sp)
    assert "invariant_emitted" not in res, res
    assert set(res.keys()) == {"obligations", "gaps", "emitted"}, res


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
