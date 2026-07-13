"""Tests for v2.0.0 post-release fixes (May 14, 2026).

Covers fixes that landed AFTER the v2.0.0 tag (2c76b3e):

  - `_validate_recon_coverage`: skip_tokens auto-exempts audit-convention
    out-of-scope dirs (interfaces, mock, test, script, fixture); now
    case-insensitive
  - `_validate_recon_coverage`: scope_file kwarg narrows the universe
    when the user provides an explicit audit-scope file
  - `_load_scope_file_paths`: parses bare paths, markdown tables,
    bullet lists from the wizard's scope file
  - `_path_in_scope_file`: basename + full-path + suffix match
  - `_path_in_subsystem_scope`: case-insensitive prefix match
  - `_ensure_python3_shim_windows`: creates python3.exe next to
    python.exe on Windows (avoids Microsoft Store stub popups);
    idempotent; no-op on non-Windows
  - Validator phase-graph ceiling (14400s) accommodates breadth (10800s)
    in all 6 mode/pipeline combos

Run: `pytest scripts/test_v2_post_release_fixes.py -v`
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
# `plamen` (the installer / wrapper module) lives at repo root, not in
# `scripts/`. The validators / parsers / types live in `scripts/`. Add
# both to sys.path so the imports below resolve regardless of how
# pytest invokes us (from repo root, scripts/, or via -k filter).
for p in (_HERE, _REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from plamen_validators import _validate_recon_coverage  # noqa: E402
from plamen_parsers import (  # noqa: E402
    _load_scope_file_paths,
    _path_in_scope_file,
    _path_in_subsystem_scope,
)
from plamen_types import (  # noqa: E402
    validate_phase_graph,
    SC_PHASES,
    L1_PHASES,
)


def _import_plamen():
    """Lazy import of the plamen wrapper module.

    Cannot be imported at module level because plamen.py replaces
    sys.stdout / sys.stderr on Windows at import time (UTF-8
    bootstrap), which breaks pytest's stdout capture mechanism
    with `ValueError: I/O operation on closed file`.
    """
    import plamen  # noqa: PLC0415
    return plamen


# ---------------------------------------------------------------------------
# 1. Phase-graph validator accepts all current phase timeouts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phases,pipeline,label",
    [
        (SC_PHASES, "sc", "SC"),
        (L1_PHASES, "l1", "L1"),
    ],
)
@pytest.mark.parametrize("mode", ["light", "core", "thorough"])
def test_phase_graph_validates_clean(phases, pipeline, label, mode):
    """No phase declares base_timeout_s above the validator ceiling.

    Regression guard: breadth was bumped to 10800s; validator ceiling
    was 7200s, so startup crashed for every Thorough run with:
        phase 'breadth' timeout 10800s exceeds 2-hour ceiling
    Fixed by raising the cap to 14400s in plamen_types.py.
    """
    issues = validate_phase_graph(phases, mode=mode, pipeline=pipeline)
    assert issues == [], f"{label}/{mode}: {issues}"


def test_validator_ceiling_is_14400():
    """The hard cap must accommodate the 2x breadth bump (10800s).

    Pinning the exact value catches accidental reverts to the
    pre-fix 7200s ceiling.
    """
    import inspect
    src = inspect.getsource(validate_phase_graph)
    assert "14400" in src, "validator ceiling lost"
    assert "7200" not in src or "7200s exceeds" not in src, \
        "stale 7200 reference still in validator"


# ---------------------------------------------------------------------------
# 2. _load_scope_file_paths parses every documented format
# ---------------------------------------------------------------------------

def test_scope_file_parses_bare_paths(tmp_path):
    sf = tmp_path / "scope.txt"
    sf.write_text("contracts/Vault.sol\nsrc/Pool.sol\n")
    names = _load_scope_file_paths(str(sf))
    assert "contracts/vault.sol" in names
    assert "vault.sol" in names  # basename also registered
    assert "src/pool.sol" in names
    assert "pool.sol" in names


def test_scope_file_parses_markdown_table(tmp_path):
    sf = tmp_path / "scope.txt"
    sf.write_text(
        "| File | Lines |\n"
        "|------|-------|\n"
        "| MessageRouter.sol | 301 |\n"
        "| Vault.sol | 200 |\n"
    )
    names = _load_scope_file_paths(str(sf))
    assert "messagerouter.sol" in names
    assert "vault.sol" in names


def test_scope_file_parses_bullet_list(tmp_path):
    sf = tmp_path / "scope.txt"
    sf.write_text("- contracts/A.sol\n- src/B.sol\n")
    names = _load_scope_file_paths(str(sf))
    assert "contracts/a.sol" in names
    assert "a.sol" in names


def test_scope_file_ignores_comments(tmp_path):
    sf = tmp_path / "scope.txt"
    sf.write_text("# header comment\nVault.sol\n// another comment\n")
    names = _load_scope_file_paths(str(sf))
    assert "vault.sol" in names
    # Comments must not produce spurious entries
    assert not any("comment" in n for n in names)


def test_scope_file_empty_or_missing_returns_empty_set():
    assert _load_scope_file_paths("") == set()
    assert _load_scope_file_paths(None) == set()
    assert _load_scope_file_paths("/definitely/nonexistent/path.txt") == set()


def test_scope_file_supports_multi_language(tmp_path):
    """sol / rs / move / go / vy should all be picked up."""
    sf = tmp_path / "scope.txt"
    sf.write_text(
        "contracts/Vault.sol\n"
        "programs/lib.rs\n"
        "sources/Module.move\n"
        "consensus/engine.go\n"
        "vault.vy\n"
    )
    names = _load_scope_file_paths(str(sf))
    for ext in ("sol", "rs", "move", "go", "vy"):
        assert any(n.endswith("." + ext) for n in names), \
            f"missing {ext} entry"


# ---------------------------------------------------------------------------
# 3. _path_in_scope_file matching semantics
# ---------------------------------------------------------------------------

def test_path_in_scope_file_direct_match_case_insensitive():
    scope = {"contracts/vault.sol", "vault.sol"}
    assert _path_in_scope_file("contracts/Vault.sol", scope)
    assert _path_in_scope_file("CONTRACTS/VAULT.SOL", scope)


def test_path_in_scope_file_basename_match():
    scope = {"vault.sol"}
    assert _path_in_scope_file("src/deep/path/Vault.sol", scope)


def test_path_in_scope_file_empty_means_everything():
    """Empty scope_names means no scope file means walk everything."""
    assert _path_in_scope_file("anything.sol", set())


def test_path_in_scope_file_no_match():
    scope = {"vault.sol", "pool.sol"}
    assert not _path_in_scope_file("interfaces/IAave.sol", scope)


# ---------------------------------------------------------------------------
# 4. _path_in_subsystem_scope is case-insensitive
# ---------------------------------------------------------------------------

def test_subsystem_scope_case_insensitive():
    assert _path_in_subsystem_scope("Src/Core/Vault.sol", "src/core")
    assert _path_in_subsystem_scope("src/core/Vault.sol", "Src/Core")
    assert _path_in_subsystem_scope("src/core/Vault.sol", "src/core")


def test_subsystem_scope_empty_means_no_prefix():
    """Empty prefix means everything is in scope."""
    assert _path_in_subsystem_scope("anything", "")


def test_subsystem_scope_outside_prefix_rejected():
    assert not _path_in_subsystem_scope("test/foo.sol", "src/core")


# ---------------------------------------------------------------------------
# 5. _validate_recon_coverage skip_tokens & scope_file
# ---------------------------------------------------------------------------

def _build_scope_tree(root: Path) -> Path:
    """Synthesize a fund-manager-shape: 20 in-scope Pools + 12 utils/gmx
    + 15 interfaces/aave stubs + 15 tests. Returns the scratchpad path."""
    (root / "contracts").mkdir(parents=True)
    for i in range(20):
        (root / "contracts" / f"Pool{i}.sol").write_text("contract X {}")
    (root / "utils" / "gmx").mkdir(parents=True)
    for i in range(12):
        (root / "utils" / "gmx" / f"G{i}.sol").write_text("contract X {}")
    (root / "interfaces" / "aave").mkdir(parents=True)
    for i in range(15):
        (root / "interfaces" / "aave" / f"I{i}.sol").write_text("interface X {}")
    (root / "test").mkdir()
    for i in range(15):
        (root / "test" / f"t{i}.sol").write_text("contract T {}")
    sp = root / ".scratchpad"
    sp.mkdir()
    return sp


def test_recon_coverage_auto_skips_interfaces_and_tests(tmp_path):
    """interfaces/* and test/* must auto-exempt regardless of citation."""
    sp = _build_scope_tree(tmp_path)
    (sp / "recon_summary.md").write_text("- contracts/Pool0.sol")
    issues = _validate_recon_coverage(sp, str(tmp_path), "evm")
    flat = " ".join(issues).lower()
    assert "interfaces" not in flat, \
        f"interfaces should auto-exempt: {issues}"
    assert "test" not in flat or "test" in "utils/gmx", \
        f"test should auto-exempt: {issues}"


def test_recon_coverage_still_flags_uncited_utils(tmp_path):
    """utils/* is NOT auto-skipped — it can be in-scope (protocol wrappers).

    Recon must either cite OR ACKNOWLEDGE utils files. Without a scope
    file and without recon citation, the bucket trips the gate.
    """
    sp = _build_scope_tree(tmp_path)
    (sp / "recon_summary.md").write_text("- contracts/Pool0.sol")
    issues = _validate_recon_coverage(sp, str(tmp_path), "evm")
    assert any("utils/gmx" in i for i in issues), \
        f"utils/gmx must still be flagged when uncited: {issues}"


def test_recon_coverage_case_insensitive_skip_tokens(tmp_path):
    """Test/, Interfaces/, Mocks/ (capitalized) auto-exempt too."""
    (tmp_path / "Test").mkdir()
    for i in range(15):
        (tmp_path / "Test" / f"t{i}.sol").write_text("x")
    (tmp_path / "Interfaces").mkdir()
    for i in range(15):
        (tmp_path / "Interfaces" / f"i{i}.sol").write_text("x")
    (tmp_path / "src").mkdir()
    for i in range(15):
        (tmp_path / "src" / f"S{i}.sol").write_text("contract X {}")
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "recon_summary.md").write_text("- src/S0.sol")
    issues = _validate_recon_coverage(sp, str(tmp_path), "evm")
    flat = " ".join(issues).lower()
    assert "test" not in flat
    assert "interfaces" not in flat


def test_recon_coverage_scope_file_narrows_universe(tmp_path):
    """When scope file lists only contracts/Pool*, utils/gmx is exempt."""
    sp = _build_scope_tree(tmp_path)
    (sp / "recon_summary.md").write_text("- contracts/Pool0.sol")
    sf = tmp_path / "scope.txt"
    sf.write_text("\n".join(f"contracts/Pool{i}.sol" for i in range(20)))
    issues = _validate_recon_coverage(
        sp, str(tmp_path), "evm", scope_file=str(sf)
    )
    assert issues == [], \
        f"scope file should exempt utils/gmx: {issues}"


def test_recon_coverage_scope_file_includes_utils_still_flags(tmp_path):
    """If scope file LISTS utils, recon must still cite them."""
    sp = _build_scope_tree(tmp_path)
    (sp / "recon_summary.md").write_text("- contracts/Pool0.sol")
    sf = tmp_path / "scope.txt"
    sf.write_text(
        "\n".join(f"contracts/Pool{i}.sol" for i in range(20))
        + "\n"
        + "\n".join(f"utils/gmx/G{i}.sol" for i in range(12))
    )
    issues = _validate_recon_coverage(
        sp, str(tmp_path), "evm", scope_file=str(sf)
    )
    assert any("utils/gmx" in i for i in issues), \
        f"utils/gmx in-scope-but-uncited must flag: {issues}"


def test_recon_coverage_acknowledged_exempts_bucket(tmp_path):
    """ACKNOWLEDGED row in scope_leftover.md exempts the bucket."""
    sp = _build_scope_tree(tmp_path)
    (sp / "recon_summary.md").write_text("- contracts/Pool0.sol")
    (sp / "scope_leftover.md").write_text(
        "| File | Status |\n"
        "|------|--------|\n"
        + "\n".join(
            f"| utils/gmx/G{i}.sol | ACKNOWLEDGED |" for i in range(12)
        )
    )
    issues = _validate_recon_coverage(sp, str(tmp_path), "evm")
    assert issues == [], \
        f"ACKNOWLEDGED rows should exempt the bucket: {issues}"


# ---------------------------------------------------------------------------
# 6. _ensure_python3_shim_windows
# ---------------------------------------------------------------------------

def test_python3_shim_noop_on_non_windows(monkeypatch):
    """No-op on macOS / Linux — python3 is a real binary there."""
    monkeypatch.setattr(sys, "platform", "linux")
    captured: list = []
    _import_plamen()._ensure_python3_shim_windows(captured.append)
    assert captured == [], \
        f"non-Windows must produce no output: {captured}"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows shim copy logic only meaningful on Windows",
)
def test_python3_shim_creates_copy(tmp_path, monkeypatch):
    """Tier 1: copy python.exe -> python3.exe in same dir."""
    py_dir = tmp_path / "Python312"
    py_dir.mkdir()
    fake_py = py_dir / "python.exe"
    fake_py.write_bytes(b"fake python contents")
    monkeypatch.setattr(sys, "executable", str(fake_py))
    captured: list = []
    _import_plamen()._ensure_python3_shim_windows(captured.append)
    py3 = py_dir / "python3.exe"
    assert py3.is_file(), "python3.exe should be created"
    assert py3.read_bytes() == b"fake python contents"
    # Should announce success
    assert any("python3.exe" in line for line in captured), \
        f"expected success log: {captured}"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows shim copy logic only meaningful on Windows",
)
def test_python3_shim_idempotent(tmp_path, monkeypatch):
    """No-op if python3.exe already exists next to python.exe."""
    py_dir = tmp_path / "Python312"
    py_dir.mkdir()
    (py_dir / "python.exe").write_bytes(b"new contents")
    (py_dir / "python3.exe").write_bytes(b"existing contents")
    monkeypatch.setattr(sys, "executable", str(py_dir / "python.exe"))
    captured: list = []
    _import_plamen()._ensure_python3_shim_windows(captured.append)
    # python3.exe must NOT be overwritten
    assert (py_dir / "python3.exe").read_bytes() == b"existing contents"
    # No log lines either
    assert captured == [], \
        f"idempotent path must produce no output: {captured}"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows shim fallback only meaningful on Windows",
)
def test_python3_shim_fallback_when_copy_fails(tmp_path, monkeypatch):
    """Tier 2: when copy raises, write python3.bat in PLAMEN_HOME."""
    py_dir = tmp_path / "Python312"
    py_dir.mkdir()
    fake_py = py_dir / "python.exe"
    fake_py.write_bytes(b"fake python")
    monkeypatch.setattr(sys, "executable", str(fake_py))
    plamen_home = tmp_path / "plamen_home"
    plamen_home.mkdir()
    plamen = _import_plamen()
    monkeypatch.setattr(plamen, "PLAMEN_HOME", str(plamen_home))

    import shutil as _sh

    def fail_copy(src, dst, **kw):
        raise PermissionError("simulated read-only install dir")

    monkeypatch.setattr(_sh, "copy2", fail_copy)
    captured: list = []
    plamen._ensure_python3_shim_windows(captured.append)
    # python3.exe was NOT created (copy failed)
    assert not (py_dir / "python3.exe").exists()
    # But python3.bat fallback IS created
    shim = plamen_home / "python3.bat"
    assert shim.is_file(), f"fallback shim missing; output: {captured}"
    content = shim.read_text(encoding="ascii")
    assert "@echo off" in content
    assert str(fake_py) in content


# ---------------------------------------------------------------------------
# 7. _update_path_env: persists to registry even when shell already has path
# ---------------------------------------------------------------------------

def test_update_path_env_persists_when_already_in_current_path(monkeypatch):
    """Regression guard for the Foundry-on-Windows bug.

    User had `~/.foundry/bin` in their Git Bash session (sourced from
    .bashrc) but NOT in the Windows User PATH (registry). Codex
    subprocesses inherited from User PATH and didn't see forge, so the
    fuzz phase reported COMPILATION_FAILED.

    _update_path_env had an early-out: if the dir was already in the
    current process PATH, it skipped _persist_path_windows. That's
    exactly the case that needed persisting.

    Fix: persist runs unconditionally, decoupled from current-PATH
    check.
    """
    plamen = _import_plamen()
    monkeypatch.setattr(sys, "platform", "win32")
    # Simulate: directory is on disk AND already in current PATH
    fake_dir = "/c/Users/test/.foundry/bin"
    monkeypatch.setattr(os.path, "isdir", lambda p: True)
    monkeypatch.setenv("PATH", fake_dir + os.pathsep + os.environ.get("PATH", ""))

    persist_called: list = []
    monkeypatch.setattr(plamen, "_persist_path_windows", persist_called.append)

    plamen._update_path_env([fake_dir], persist=True)

    assert persist_called, (
        "regression: _persist_path_windows was skipped because the dir "
        "was already in current PATH — exactly the bug we just fixed"
    )


def test_update_path_env_no_persist_on_non_windows(monkeypatch):
    """Persist is a no-op on macOS / Linux even with persist=True."""
    plamen = _import_plamen()
    monkeypatch.setattr(sys, "platform", "linux")
    persist_called: list = []
    monkeypatch.setattr(plamen, "_persist_path_windows", persist_called.append)
    plamen._update_path_env(["/tmp"], persist=True)
    assert persist_called == [], \
        "persist must not run on non-Windows"


# ---------------------------------------------------------------------------
# 8. _report_toolchain_visibility: cross-OS report runs on every install
# ---------------------------------------------------------------------------

def test_report_toolchain_visibility_runs_on_all_platforms(monkeypatch):
    """The report must be callable on macOS / Linux / Windows alike.

    It uses _find_bin which uses shutil.which under the hood — that's
    OS-portable.
    """
    plamen = _import_plamen()
    captured: list = []
    plamen._report_toolchain_visibility(captured.append)
    # Either "All chain toolchains visible" or "Not detected (N)"
    output = "".join(captured)
    assert "Toolchain Visibility" in output or "All chain" in output or "Not detected" in output
    # Posix-only PATH-source hint must NOT appear on Windows
    if sys.platform == "win32":
        assert ".bashrc" not in output and ".zshrc" not in output, \
            "POSIX-only PATH hint leaked on Windows"


def test_report_toolchain_visibility_lists_all_chains(monkeypatch):
    """The report must enumerate Foundry / Solana / Aptos / Sui /
    Soroban / Go / Rust so a user sees what each missing tool unlocks."""
    plamen = _import_plamen()
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda *a, **k: None)  # simulate empty PATH

    captured: list = []
    plamen._report_toolchain_visibility(captured.append)
    output = "".join(captured)
    # Every chain we support must appear when nothing is installed.
    for chain in ("Foundry", "Solana", "Aptos", "Sui", "Stellar", "Go", "Rust"):
        assert chain in output, f"missing {chain} in report: {output[:500]}"
    # Posix-only PATH-source hint MUST appear on POSIX
    if sys.platform != "win32":
        assert ".bashrc" in output or ".zshrc" in output


def test_run_install_calls_toolchain_report():
    """run_install must call _report_toolchain_visibility on every OS."""
    plamen = _import_plamen()
    import inspect
    src = inspect.getsource(plamen.run_install)
    assert "_report_toolchain_visibility" in src, \
        "run_install must call the toolchain report"
    # Must NOT be wrapped in a Windows-only `if sys.platform == 'win32':`
    # Check that the report-visibility line is at outer-indent (4 spaces)
    # rather than inside a platform conditional (8+ spaces).
    for line in src.splitlines():
        if "_report_toolchain_visibility" in line:
            leading = len(line) - len(line.lstrip())
            assert leading == 4, (
                f"toolchain report indented to {leading} spaces — "
                f"likely wrapped in a platform-specific conditional; "
                f"must be unconditional so macOS / Linux see it too"
            )


# ---------------------------------------------------------------------------
# Per-ecosystem assertion dispatch (Steps 1 & 2)
#
# Validates plamen_validators._resolve_assert_dispatch() routes correctly per
# detected language, and that each ecosystem's nontrivial regex catches real
# assertions while letting trivial forms fall through to the trivial-check.
# This is the change that converts every Solidity Foundry PoC from
# [POC-PASS] → [CODE-TRACE] back to [POC-PASS] in the final report.
# ---------------------------------------------------------------------------

import importlib


def _validators():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    if "plamen_validators" in sys.modules:
        del sys.modules["plamen_validators"]
    return importlib.import_module("plamen_validators")


def test_assert_dispatch_evm_foundry_nontrivial_matches():
    v = _validators()
    d = v._resolve_assert_dispatch("evm")
    nt = d["nontrivial"]
    # Real Foundry forms a verifier writes
    assert nt.search("assertEq(attackerBalance, 100 ether)")
    assert nt.search("assertEq(token.balanceOf(user), expected)")
    assert nt.search("assertTrue(bug_present)")
    assert nt.search("assertGt(received, sent)")
    assert nt.search("vm.expectRevert(stdError.arithmeticError)")
    assert nt.search("vm.expectEmit(true, true, false, true)")


def test_assert_dispatch_evm_hardhat_chai_matches():
    v = _validators()
    d = v._resolve_assert_dispatch("evm")
    nt = d["nontrivial"]
    assert nt.search('expect(tx).to.emit(contract, "Transfer")')
    assert nt.search('expect(result).to.equal(100)')
    assert nt.search('expect(call).to.revert')


def test_assert_dispatch_evm_trivial_falls_through():
    """Trivial literal-only assertions must NOT match nontrivial regex.

    This is the cascade that lets _validate_poc_pass_integrity catch trivial
    PoCs via the trivial_strs string check rather than treating them as real.
    """
    v = _validators()
    d = v._resolve_assert_dispatch("evm")
    nt = d["nontrivial"]
    for c in [
        "assertEq(1, 1)",
        "assertEq(0, 0)",
        "assertEq(true, true)",
        "assertEq(false, false)",
        "assertTrue(true)",
        "assertFalse(false)",
    ]:
        assert not nt.search(c), f"trivial form {c!r} should not match nontrivial"


def test_assert_dispatch_evm_any_catches_all():
    v = _validators()
    d = v._resolve_assert_dispatch("evm")
    a = d["any"]
    for c in [
        "assertEq(1, 1)", "assertEq(x, y)", "assertTrue(z)",
        "vm.expectRevert()", "vm.expectEmit(true, true, true, true)",
        'expect(tx).to.emit(c, "E")',
    ]:
        assert a.search(c), f"any_re should match {c!r}"


def test_assert_dispatch_solana_rust_matches():
    v = _validators()
    d = v._resolve_assert_dispatch("solana")
    nt = d["nontrivial"]
    assert nt.search("assert_eq!(actual_balance, expected_balance)")
    assert nt.search("assert!(result.is_err())")
    assert nt.search("#[should_panic]")
    assert nt.search("token_account.amount.unwrap_err()")
    # Trivial cascade
    assert not nt.search("assert_eq!(1, 1)")


def test_assert_dispatch_soroban_uses_rust_family():
    """Soroban shares Rust assertion vocabulary with Solana."""
    v = _validators()
    sol = v._resolve_assert_dispatch("solana")
    sor = v._resolve_assert_dispatch("soroban")
    assert sol["any"].pattern == sor["any"].pattern
    assert sol["nontrivial"].pattern == sor["nontrivial"].pattern


def test_assert_dispatch_aptos_move_two_arg_form():
    v = _validators()
    d = v._resolve_assert_dispatch("aptos")
    nt = d["nontrivial"]
    # Aptos assert!(cond, abort_code)
    assert nt.search("assert!(balance == 100, EINVALID_BALANCE)")
    assert nt.search("assert!(result == expected, 1)")
    # Any-form catches even trivial assert!
    assert d["any"].search("assert!(true)")


def test_assert_dispatch_sui_move_single_arg_form():
    v = _validators()
    d = v._resolve_assert_dispatch("sui")
    nt = d["nontrivial"]
    # Sui assert!(cond)
    assert nt.search("assert!(balance == 100)")
    assert nt.search("assert!(result == expected)")
    assert d["any"].search("assert!(true)")


def test_assert_dispatch_l1_go_testing_idioms():
    v = _validators()
    d = v._resolve_assert_dispatch("l1_go")
    nt = d["nontrivial"]
    assert nt.search('t.Fatalf("got %v want %v", got, want)')
    assert nt.search('t.Errorf("mismatch: %v", err)')
    assert nt.search("require.Equal(t, expected, actual)")
    assert nt.search("assert.True(t, cond)")
    assert nt.search("require.NoError(t, err)")
    # Any-form catches bare t.Fatal / t.Error
    assert d["any"].search('t.Fatal("x")')


def test_assert_dispatch_l1_rust_matches_rust_family():
    v = _validators()
    rust = v._resolve_assert_dispatch("solana")
    l1r = v._resolve_assert_dispatch("l1_rust")
    assert rust["any"].pattern == l1r["any"].pattern


def test_assert_dispatch_unknown_language_falls_back_to_union():
    """Unknown / missing language → union regex (broadest detection).

    Back-compat: any caller without language info still benefits from
    Foundry/Move/Go vocabulary additions.
    """
    v = _validators()
    d = v._resolve_assert_dispatch("")
    a = d["any"]
    # The union regex must match each ecosystem's canonical form
    assert a.search("assertEq(x, y)")            # EVM
    assert a.search("assert_eq!(x, y)")          # Rust
    assert a.search("assert!(cond, code)")       # Move
    assert a.search('t.Fatal("x")')              # Go
    assert a.search('expect(x).to.equal(y)')     # Hardhat


def test_read_language_from_config(tmp_path):
    v = _validators()
    # Empty scratchpad → empty string
    assert v._read_language_from_config(tmp_path) == ""
    # Write config.json with language
    import json
    (tmp_path / "config.json").write_text(json.dumps({"language": "solana"}))
    assert v._read_language_from_config(tmp_path) == "solana"
    # Malformed JSON → empty (graceful)
    (tmp_path / "config.json").write_text("{not json")
    assert v._read_language_from_config(tmp_path) == ""


def test_legacy_globals_still_exported_and_broader_than_v1():
    """Direct importers of _ANY_ASSERT_RE / _NONTRIVIAL_ASSERT_RE keep working.

    The legacy globals are now the union across all ecosystems — broader than
    the v1 Rust-only regex, but still semantically a "match any assertion"
    fallback when no language is known.
    """
    v = _validators()
    assert hasattr(v, "_ANY_ASSERT_RE")
    assert hasattr(v, "_NONTRIVIAL_ASSERT_RE")
    assert hasattr(v, "_TRIVIAL_ASSERT_STRS")
    # Confirm the v1-missing Solidity vocabulary is now in the union
    assert v._ANY_ASSERT_RE.search("assertEq(a, b)")
    assert v._NONTRIVIAL_ASSERT_RE.search("assertEq(actualVal, 100)")
