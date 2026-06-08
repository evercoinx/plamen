"""Ecosystem-path fix regression tests (non-EVM language misconfiguration +
recon-prepass build parity).

Covers:
- STEP 2A: language<->source-extension consistency startup gate
  (_validate_language_source_consistency / _dominant_source_suffix).
- STEP 2C: recon-prepass non-EVM build-root resolution (_resolve_build_root).

Run: python -m pytest test_ecosystem_path_fixes.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
import recon_prepass as RP  # noqa: E402


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("// src\n", encoding="utf-8")


# ---- STEP 2A: language consistency gate ----------------------------------


def test_dominant_suffix_picks_rust_over_none(tmp_path: Path):
    proj = tmp_path / "crate" / "src"
    _touch(proj / "lib.rs")
    _touch(proj / "state.rs")
    dominant, counts = D._dominant_source_suffix(proj)
    assert dominant == ".rs"
    assert counts[".rs"] >= 2


def test_language_gate_halts_on_definite_contradiction(tmp_path: Path):
    """language=evm but only .rs files present => fail-fast halt with an
    actionable message naming the candidate language(s)."""
    proj = tmp_path / "crate" / "src"
    _touch(proj / "lib.rs")
    ok, msg = D._validate_language_source_consistency(proj, "evm")
    assert ok is False
    assert "language=evm" in msg
    assert ".rs" in msg
    # Suggests a recognized Rust language.
    assert ("solana" in msg) or ("soroban" in msg)


def test_language_gate_passes_on_match(tmp_path: Path):
    proj = tmp_path / "crate" / "src"
    _touch(proj / "lib.rs")
    ok, msg = D._validate_language_source_consistency(proj, "solana")
    assert ok is True
    assert "OK" in msg


def test_language_gate_continues_when_indeterminate(tmp_path: Path):
    """No recognized source files in PROJECT_PATH or its consulted ancestors
    => WARN + continue (never block on an indeterminate signal)."""
    # Nest deeply so the 2-ancestor fallback also sees only empty dirs (the
    # pytest tmp root may contain sibling-test source files above this).
    proj = tmp_path / "iso_a" / "iso_b" / "iso_c" / "empty"
    proj.mkdir(parents=True)
    (proj / "README.md").write_text("docs\n", encoding="utf-8")
    ok, msg = D._validate_language_source_consistency(proj, "evm")
    assert ok is True
    assert "indeterminate" in msg


def test_language_gate_finds_solidity_via_ancestor_walk(tmp_path: Path):
    """A scope-dir PROJECT_PATH still sees source files via the ancestor walk
    is not needed here, but the dominant-extension scan must see in-tree .sol."""
    proj = tmp_path / "contracts"
    _touch(proj / "Vault.sol")
    ok, _msg = D._validate_language_source_consistency(proj, "evm")
    assert ok is True


# ---- STEP 2C: recon-prepass build-root resolution ------------------------


def test_resolve_build_root_walks_up_to_cargo_manifest(tmp_path: Path):
    root = tmp_path / "crate"
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    scope = root / "src"
    resolved = RP._resolve_build_root(scope, "solana")
    assert resolved == root.resolve()


def test_resolve_build_root_none_when_no_manifest(tmp_path: Path):
    scope = tmp_path / "crate" / "src"
    scope.mkdir(parents=True)
    assert RP._resolve_build_root(scope, "solana") is None


def test_resolve_build_root_move_manifest(tmp_path: Path):
    root = tmp_path / "pkg"
    (root / "sources").mkdir(parents=True)
    (root / "Move.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    resolved = RP._resolve_build_root(root / "sources", "aptos")
    assert resolved == root.resolve()
