"""Cross-ecosystem port of the 3 obligation-derivers. The bug-class shapes
(critical-asset-mover, array-uniqueness, unbounded-input) exist beyond Solidity,
so the derivers must fire on Rust (Solana/Soroban/L1), Move (Aptos/Sui), and Go
(L1) source — and honestly SKIP where a vector's shape does not exist (Go has no
asset-mover; DAML is unported)."""
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
    root = tmp_path / "proj"
    sp = root / ".scratchpad"
    sp.mkdir(parents=True)
    (sp / "findings_inventory.md").write_text("# Inv\n", encoding="utf-8")
    return root, sp


def _src(root: Path, rel: str, body: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# ── L-10 array-uniqueness across languages ────────────────────────────────────

def test_array_uniqueness_rust(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _src(root, "lib.rs",
         "pub fn start_liquidation(assets: Vec<Address>) {\n"
         "    for a in assets.iter() {\n"
         "        token.transfer(&a, share);\n"
         "    }\n}\n")
    out = eg.compute_array_uniqueness_candidates(sp)
    assert any("start_liquidation" in c["title"] and "assets" in c["title"] for c in out), out


def test_array_uniqueness_move(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _src(root, "m.move",
         "module a::m {\n  public fun payout(recipients: vector<address>) {\n"
         "    let i = 0;\n    while (i < vector::length(&recipients)) {\n"
         "      coin::transfer(*vector::borrow(&recipients, i), amt);\n      i = i + 1;\n"
         "    }\n  }\n}\n")
    out = eg.compute_array_uniqueness_candidates(sp)
    assert any("payout" in c["title"] and "recipients" in c["title"] for c in out), out


def test_array_uniqueness_go(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _src(root, "x.go",
         "package p\nfunc Process(items []Item) {\n"
         "    for _, it := range items {\n        total += it.Amount\n    }\n}\n")
    out = eg.compute_array_uniqueness_candidates(sp)
    assert any("Process" in c["title"] and "items" in c["title"] for c in out), out


def test_array_uniqueness_rust_guard_suppresses(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _src(root, "lib.rs",
         "pub fn batch(assets: Vec<Address>) {\n"
         "    let mut seen = HashSet::new();\n"
         "    for a in assets.iter() {\n        seen.insert(a);\n"
         "        token.transfer(&a, 1);\n    }\n}\n")
    assert eg.compute_array_uniqueness_candidates(sp) == []


# ── L-08 unbounded-input across languages ─────────────────────────────────────

def test_unbounded_rust(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _src(root, "lib.rs",
         "pub fn upload(env: Env, name: String) {\n"
         "    env.storage().instance().set(&KEY, &name);\n}\n")
    out = eg.compute_unbounded_input_candidates(sp)
    assert any("upload" in c["title"] and "name" in c["title"] for c in out), out


def test_unbounded_move(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _src(root, "m.move",
         "module a::m {\n  public fun set_name(s: &signer, name: String) {\n"
         "    move_to(s, Doc { name });\n  }\n}\n")
    out = eg.compute_unbounded_input_candidates(sp)
    assert any("set_name" in c["title"] and "name" in c["title"] for c in out), out


def test_unbounded_rust_lenguard_suppresses(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    _src(root, "lib.rs",
         "pub fn upload(env: Env, name: String) {\n"
         "    if name.len() > 256 { panic!(); }\n"
         "    env.storage().instance().set(&KEY, &name);\n}\n")
    assert eg.compute_unbounded_input_candidates(sp) == []


# ── L-04 asset-mover: fires on Rust/Move, SKIPS Go (honest applicability) ──────

_RUST_GRAPH = {
    "source": "rust-source",
    "var_refs": {"position_id": {"bare": "position_id", "refs": [
        "init (lib.rs:L10)", "read_pos (lib.rs:L20)"]}},
    "functions": {},
}


def test_asset_mover_rust(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    (sp / "_mechanical_graph.json").write_text(json.dumps(_RUST_GRAPH), encoding="utf-8")
    _src(root, "lib.rs",
         "pub fn position_id() -> u64 { 0 }\n"
         "pub fn withdraw_nft(token: TokenClient, to: Address, id: u64) {\n"
         "    token.transfer(&to, &id);\n}\n")
    out = eg.compute_critical_asset_mover_candidates(sp)
    assert any("withdraw_nft" in c["title"] and "position_id" in c["title"] for c in out), out


def test_asset_mover_go_skipped(tmp_path: Path):
    eg = _eg()
    root, sp = _proj(tmp_path)
    # Go has no asset_handle/mover in its lang spec -> L-04 must NOT fire even
    # with an id-shaped state var (node clients have no movable assets).
    (sp / "_mechanical_graph.json").write_text(json.dumps({
        "source": "go-source",
        "var_refs": {"block_id": {"bare": "block_id", "refs": ["a (x.go:L1)", "b (x.go:L2)"]}},
        "functions": {},
    }), encoding="utf-8")
    _src(root, "x.go",
         "package p\nfunc Move(id uint64, to Addr) { send(to, id) }\n")
    assert eg.compute_critical_asset_mover_candidates(sp) == []


# ── language applicability matrix is what we claim ────────────────────────────

def test_lang_applicability_matrix():
    eg = _eg()
    has_mover = {k: ("mover" in v) for k, v in eg._LANG.items()}
    # L-04 applies to sol/rust/move, NOT go
    assert has_mover == {"sol": True, "rust": True, "move": True, "go": False}
    # L-10 + L-08 apply to all four (array_param + str_param present everywhere)
    for k, v in eg._LANG.items():
        assert "array_param" in v and "str_param" in v, k


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
