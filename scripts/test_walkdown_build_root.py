"""Walk-DOWN build-root discovery (monorepo support). When the audit scope
points at an umbrella/repo root with no manifest of its own, the resolver must
find the real build project in a subdir — for every ecosystem — and must NOT
descend into vendored deps (lib/, node_modules/, target/). Mirror of walk-up."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _rp():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("recon_prepass")


def _mk(p: Path, body: str = "x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# ── EVM ──────────────────────────────────────────────────────────────────────

def test_evm_walkdown_finds_subproject(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "monorepo"
    sub = root / "packages" / "contracts"
    _mk(sub / "foundry.toml", "[profile.default]\nsrc='src'\n")
    _mk(sub / "src" / "Vault.sol", "contract V {}\n")
    # umbrella root has NO foundry.toml
    got = rp._resolve_evm_build_root(root)
    assert got == sub.resolve()


def test_evm_walkdown_ignores_vendored_dep_manifest(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "repo"
    sub = root / "contracts"
    _mk(sub / "foundry.toml", "x")
    _mk(sub / "src" / "A.sol", "contract A {}\n")
    # a dependency under lib/ ALSO has a foundry.toml — must be pruned
    _mk(sub / "lib" / "dep" / "foundry.toml", "x")
    _mk(sub / "lib" / "dep" / "src" / "Dep.sol", "contract Dep {}\n")
    got = rp._resolve_evm_build_root(root)
    assert got == sub.resolve()  # the project, never lib/dep


def test_evm_walkdown_picks_subproject_with_most_sources(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "mono"
    big = root / "packages" / "core"
    small = root / "packages" / "tooling"
    _mk(big / "foundry.toml", "x")
    for i in range(4):
        _mk(big / "src" / f"C{i}.sol", "contract C {}\n")
    _mk(small / "foundry.toml", "x")
    _mk(small / "src" / "T.sol", "contract T {}\n")
    got = rp._resolve_evm_build_root(root)
    assert got == big.resolve()


def test_evm_walkup_still_preferred(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "sc"
    _mk(root / "foundry.toml", "x")
    scope = root / "src" / "core"
    _mk(scope / "Q.sol", "contract Q {}\n")
    # scope points BELOW the root -> walk-up wins, walk-down never needed
    assert rp._resolve_evm_build_root(scope) == root.resolve()


# ── Move (aptos/sui) ─────────────────────────────────────────────────────────

def test_move_walkdown_finds_subproject(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "mono"
    sub = root / "move" / "pkg"
    _mk(sub / "Move.toml", "[package]\nname='p'\n")
    _mk(sub / "sources" / "m.move", "module a::m {}\n")
    assert rp._resolve_build_root(root, "aptos") == sub.resolve()
    assert rp._resolve_build_root(root, "sui") == sub.resolve()


# ── Rust (solana/soroban) ────────────────────────────────────────────────────

def test_rust_walkdown_finds_subproject(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "mono"
    sub = root / "programs" / "vault"
    _mk(sub / "Cargo.toml", "[package]\nname='v'\n")
    _mk(sub / "src" / "lib.rs", "pub fn f() {}\n")
    assert rp._resolve_build_root(root, "solana") == sub.resolve()
    assert rp._resolve_build_root(root, "soroban") == sub.resolve()


def test_rust_walkdown_prunes_target_dir(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "repo"
    sub = root / "program"
    _mk(sub / "Cargo.toml", "x")
    _mk(sub / "src" / "lib.rs", "pub fn f() {}\n")
    # a build-artifact Cargo.toml under target/ must be pruned
    _mk(sub / "target" / "dep" / "Cargo.toml", "x")
    assert rp._resolve_build_root(root, "solana") == sub.resolve()


# ── negative ─────────────────────────────────────────────────────────────────

def test_no_manifest_anywhere_returns_none(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "bare"
    _mk(root / "src" / "Loose.sol", "contract L {}\n")  # no foundry.toml at all
    assert rp._resolve_evm_build_root(root) is None


def test_manifest_without_sources_not_selected(tmp_path: Path):
    rp = _rp()
    root = tmp_path / "mono"
    # a sub-package with a manifest but ONLY test sources -> not the audit target
    tooling = root / "tooling"
    _mk(tooling / "foundry.toml", "x")
    _mk(tooling / "test" / "Helper.t.sol", "contract H {}\n")
    assert rp._resolve_evm_build_root(root) is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
