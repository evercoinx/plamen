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
        "| GatewaySend.sol | 301 |\n"
        "| Vault.sol | 200 |\n"
    )
    names = _load_scope_file_paths(str(sf))
    assert "gatewaysend.sol" in names
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

def _build_dhedge_tree(root: Path) -> Path:
    """Synthesize a dHEDGE-shape: 20 in-scope Pools + 12 utils/gmx
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
    sp = _build_dhedge_tree(tmp_path)
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
    sp = _build_dhedge_tree(tmp_path)
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
    sp = _build_dhedge_tree(tmp_path)
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
    sp = _build_dhedge_tree(tmp_path)
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
    sp = _build_dhedge_tree(tmp_path)
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
    orig_copy = _sh.copy2

    def fail_copy(src, dst, **kw):
        raise PermissionError("simulated read-only install dir")

    monkeypatch.setattr(_sh, "copy2", fail_copy)
    captured: list = []
    _import_plamen()._ensure_python3_shim_windows(captured.append)
    # python3.exe was NOT created (copy failed)
    assert not (py_dir / "python3.exe").exists()
    # But python3.bat fallback IS created
    shim = plamen_home / "python3.bat"
    assert shim.is_file(), f"fallback shim missing; output: {captured}"
    content = shim.read_text(encoding="ascii")
    assert "@echo off" in content
    assert str(fake_py) in content
    # restore for other tests
    monkeypatch.setattr(_sh, "copy2", orig_copy)
