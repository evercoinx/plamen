"""FIX 2: EVM Foundry build-env bootstrap in the recon pre-pass.

When an EVM scope ships bare `.sol` files with no `foundry.toml`/`hardhat`,
the pre-pass best-effort scaffolds a minimal Foundry env (foundry.toml +
forge-std + import-prefix remappings + well-known libs) and runs `forge build`
so Slither and later PoC verification have a compilable harness. The bootstrap
NEVER raises, is idempotent (no-op when a manifest exists), and degrades to the
existing grep-fallback SKIPPED status on any failure.
"""
from __future__ import annotations

from pathlib import Path

import recon_prepass as RP


def _write_sol(proj: Path, name: str, body: str) -> None:
    (proj / name).write_text(body, encoding="utf-8")


def test_bootstrap_noop_when_foundry_toml_exists(tmp_path):
    """Idempotent: existing foundry.toml is never clobbered."""
    proj = tmp_path
    (proj / "foundry.toml").write_text(
        "[profile.default]\nsrc = 'src'\n", encoding="utf-8"
    )
    _write_sol(proj, "Vault.sol", "pragma solidity ^0.8.0;\ncontract V {}\n")
    sources = list(proj.glob("*.sol"))

    ok, reason = RP._bootstrap_evm_foundry_env(proj, sources)

    assert ok is False
    assert "already exists" in reason
    # foundry.toml content untouched.
    assert "src = 'src'" in (proj / "foundry.toml").read_text(encoding="utf-8")


def test_bootstrap_declines_when_forge_absent(tmp_path, monkeypatch):
    """Degrades cleanly (no raise, no foundry.toml) when forge is not on PATH."""
    proj = tmp_path
    _write_sol(proj, "Vault.sol", "pragma solidity ^0.8.0;\ncontract V {}\n")
    sources = list(proj.glob("*.sol"))

    monkeypatch.setattr(RP.shutil, "which", lambda name: None)

    ok, reason = RP._bootstrap_evm_foundry_env(proj, sources)

    assert ok is False
    assert "forge not on PATH" in reason
    # No foundry.toml was written.
    assert not (proj / "foundry.toml").exists()


def test_bootstrap_declines_when_no_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(RP.shutil, "which", lambda name: "/usr/bin/forge")
    ok, reason = RP._bootstrap_evm_foundry_env(tmp_path, [])
    assert ok is False
    assert "no Solidity source files" in reason
    assert not (tmp_path / "foundry.toml").exists()


def test_write_build_status_skips_cleanly_without_forge(tmp_path, monkeypatch):
    """_write_build_status falls through to the grep-fallback SKIPPED status
    when forge is absent (no bootstrap attempted)."""
    proj = tmp_path / "proj"
    scratch = tmp_path / "scratch"
    proj.mkdir()
    scratch.mkdir()
    _write_sol(proj, "Vault.sol", "pragma solidity ^0.8.0;\ncontract V {}\n")

    monkeypatch.setattr(RP.shutil, "which", lambda name: None)

    status = RP._write_build_status(scratch, proj, "evm")

    assert status == "STUB"
    text = (scratch / "build_status.md").read_text(encoding="utf-8")
    assert "SKIPPED" in text
    # No bootstrap attempted -> no foundry.toml left behind.
    assert not (proj / "foundry.toml").exists()


def test_write_build_status_records_bootstrap_failure(tmp_path, monkeypatch):
    """When forge is present but the build fails (no network / bad version),
    build_status.md records the bootstrap-attempted-but-failed reason and the
    grep fallback is used (STUB)."""
    proj = tmp_path / "proj"
    scratch = tmp_path / "scratch"
    proj.mkdir()
    scratch.mkdir()
    _write_sol(proj, "Vault.sol", "pragma solidity ^0.8.0;\ncontract V {}\n")

    # forge "present" but every forge invocation fails (simulate no network /
    # broken toolchain). _run_forge never raises; bootstrap returns failure.
    monkeypatch.setattr(RP.shutil, "which", lambda name: "/usr/bin/forge")
    monkeypatch.setattr(RP, "_run_forge", lambda args, cwd, timeout: (1, "boom"))

    status = RP._write_build_status(scratch, proj, "evm")

    assert status == "STUB"
    text = (scratch / "build_status.md").read_text(encoding="utf-8")
    assert "bootstrap attempted but failed" in text
    assert "grep fallback used" in text


def test_bootstrap_success_path_sets_build_env(tmp_path, monkeypatch):
    """When forge is present and build succeeds, _write_build_status promotes
    to a real evm_forge build (SUCCESS), recording the bootstrap note."""
    proj = tmp_path / "proj"
    scratch = tmp_path / "scratch"
    proj.mkdir()
    scratch.mkdir()
    _write_sol(
        proj, "Vault.sol",
        "// SPDX-License-Identifier: MIT\npragma solidity 0.8.20;\n"
        "import '@openzeppelin/contracts/token/ERC20/ERC20.sol';\n"
        "contract V {}\n",
    )

    monkeypatch.setattr(RP.shutil, "which", lambda name: "/usr/bin/forge")

    # Simulate every forge call succeeding; the final `forge build` (run again
    # in _write_build_status's normal path) also succeeds.
    monkeypatch.setattr(RP, "_run_forge", lambda args, cwd, timeout: (0, "ok"))

    def _fake_run(cmd, **kwargs):
        class _P:
            returncode = 0
            stdout = "Compiling ...\nCompiler run successful"
            stderr = ""
        return _P()

    monkeypatch.setattr(RP.subprocess, "run", _fake_run)

    status = RP._write_build_status(scratch, proj, "evm")

    assert status == "WRITTEN"
    text = (scratch / "build_status.md").read_text(encoding="utf-8")
    assert "Build Env Bootstrap" in text and "SUCCESS" in text
    assert "**Tool**: evm_forge" in text
    # A foundry.toml was scaffolded with the detected solc version.
    ft = (proj / "foundry.toml").read_text(encoding="utf-8")
    assert 'solc = "0.8.20"' in ft
    # OpenZeppelin remapping detected and written.
    remap = (proj / "remappings.txt").read_text(encoding="utf-8")
    assert "@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/" in remap


def test_detect_solc_version_picks_most_common(tmp_path):
    f1 = tmp_path / "A.sol"
    f2 = tmp_path / "B.sol"
    f3 = tmp_path / "C.sol"
    f1.write_text("pragma solidity 0.8.19;\n", encoding="utf-8")
    f2.write_text("pragma solidity 0.8.19;\n", encoding="utf-8")
    f3.write_text("pragma solidity 0.8.13;\n", encoding="utf-8")
    assert RP._detect_solc_version([f1, f2, f3]) == "0.8.19"


def test_detect_solc_version_caret(tmp_path):
    f = tmp_path / "A.sol"
    f.write_text("pragma solidity ^0.8.20;\n", encoding="utf-8")
    assert RP._detect_solc_version([f]) == "0.8.20"


def test_detect_import_libs_dedup_and_order(tmp_path):
    f = tmp_path / "A.sol"
    f.write_text(
        "import '@openzeppelin/contracts/token/ERC20/ERC20.sol';\n"
        "import 'solmate/tokens/ERC20.sol';\n"
        "import 'solady/utils/SafeTransferLib.sol';\n",
        encoding="utf-8",
    )
    libs = RP._detect_import_libs([f])
    dirs = [d for (_p, d, _s, _r) in libs]
    assert "openzeppelin-contracts" in dirs
    assert "solmate" in dirs
    assert "solady" in dirs
    # de-dup: openzeppelin only appears once even though multiple prefixes match
    assert dirs.count("openzeppelin-contracts") == 1
