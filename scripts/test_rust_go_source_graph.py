"""Tier-2 compilation-free source-parse graph for Rust (Solana/Soroban/L1) and
Go (L1) — the fallback beneath the SCIP bake. Closes the only ecosystems that
previously dropped straight to advisory when rust-analyzer/scip-go was absent.
Proves the enumeration gate gets a real graph with zero toolchain dependency."""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def _rp():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("recon_prepass")


def _mk(p: Path, body: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


_RUST = """\
pub struct Vault { balance: u64 }
impl Vault {
    pub fn withdraw(&mut self) { self.balance = 0; }
    pub fn read_balance(&self) -> u64 { self.balance }
}
"""

_GO = """\
package vault
type Vault struct { Balance uint64 }
func (v *Vault) Withdraw() { v.Balance = 0 }
func (v *Vault) ReadBalance() uint64 { return v.Balance }
"""


def test_rust_source_graph_built_without_toolchain(tmp_path: Path):
    rp = _rp()
    sp = tmp_path / "scratch"; sp.mkdir()
    proj = tmp_path / "proj"; _mk(proj / "lib.rs", _RUST)
    assert rp._bake_rust_source_graph(sp, proj) == "WRITTEN"
    g = json.loads((sp / "_mechanical_graph.json").read_text(encoding="utf-8"))
    assert g["source"] == "rust-source"
    refs = {d.split("(", 1)[0].strip() for d in g["var_refs"]["balance"]["refs"]}
    assert refs == {"withdraw", "read_balance"}


def test_go_source_graph_built_without_toolchain(tmp_path: Path):
    rp = _rp()
    sp = tmp_path / "scratch"; sp.mkdir()
    proj = tmp_path / "proj"; _mk(proj / "vault.go", _GO)
    assert rp._bake_go_source_graph(sp, proj) == "WRITTEN"
    g = json.loads((sp / "_mechanical_graph.json").read_text(encoding="utf-8"))
    assert g["source"] == "go-source"
    refs = {d.split("(", 1)[0].strip() for d in g["var_refs"]["Balance"]["refs"]}
    assert refs == {"Withdraw", "ReadBalance"}


def test_rust_tiered_falls_back_not_mocks(tmp_path: Path, monkeypatch):
    rp = _rp()
    sp = tmp_path / "scratch"; sp.mkdir()
    proj = tmp_path / "proj"; _mk(proj / "lib.rs", _RUST)
    # force the SCIP tier to "fail" (no toolchain) -> must fall back to source
    monkeypatch.setattr(rp, "_bake_rust_scip", lambda s, p: "SKIPPED:rust-analyzer not found")
    r = rp._bake_rust_graph(sp, proj)
    assert r.startswith("WRITTEN:rust-source")
    assert (sp / "_mechanical_graph.json").exists()


def test_go_tiered_prefers_scip_when_available(tmp_path: Path, monkeypatch):
    rp = _rp()
    sp = tmp_path / "scratch"; sp.mkdir()
    proj = tmp_path / "proj"; _mk(proj / "v.go", _GO)
    monkeypatch.setattr(rp, "_bake_go_scip", lambda s, p: "WRITTEN")
    assert rp._bake_go_graph(sp, proj) == "WRITTEN:scip"


def test_rust_gate_runs_on_source_graph(tmp_path: Path, monkeypatch):
    """End-to-end: SCIP unavailable -> source graph -> enumeration gate emits an
    ENUMGAP for an unaddressed co-referencer of a shared field."""
    rp = _rp()
    eg = importlib.import_module("enumeration_gate")
    sp = tmp_path / "scratch"; sp.mkdir()
    proj = tmp_path / "proj"; _mk(proj / "lib.rs", _RUST)
    monkeypatch.setattr(rp, "_bake_rust_scip", lambda s, p: "FAILED:no toolchain")
    rp._bake_rust_graph(sp, proj)
    (sp / "findings_inventory.md").write_text(
        "# Inv\n\n### Finding [INV-001]: withdraw zeroes balance\n"
        "**Severity**: Medium\n**Location**: `lib.rs:L3`\n**Source IDs**: B1\n"
        "withdraw resets balance, gated, safe.\n", encoding="utf-8")
    res = eg.run_enumeration_gate(sp)
    assert res["emitted"] >= 1
    assert "read_balance" in (sp / "findings_inventory.md").read_text(encoding="utf-8")


def test_no_sources_skips(tmp_path: Path):
    rp = _rp()
    sp = tmp_path / "scratch"; sp.mkdir()
    empty = tmp_path / "empty"; empty.mkdir()
    assert rp._bake_rust_source_graph(sp, empty) == "SKIPPED:no .rs sources"
    assert rp._bake_go_source_graph(sp, empty) == "SKIPPED:no .go sources"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
