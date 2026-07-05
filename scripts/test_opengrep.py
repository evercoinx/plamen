"""Tests for P2: OpenGrep cross-ecosystem scanner integration.

Tests skip/fail paths, SARIF parsing, and prepass wiring.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recon_prepass import (
    _run_opengrep_scan,
    _parse_opengrep_sarif,
    _ensure_opengrep_rules,
    _write_build_status,
    run_recon_prepass,
    _write_text,
)

# ── helpers ──────────────────────────────────────────────────────────────

def _mkscratch(tmp_path: Path) -> Path:
    s = tmp_path / ".scratchpad"
    s.mkdir()
    return s


def _mkproj(tmp_path: Path, *, lang: str = "evm") -> Path:
    p = tmp_path / "project"
    p.mkdir()
    ext = {"evm": ".sol", "solana": ".rs", "soroban": ".rs", "aptos": ".move", "sui": ".move"}
    src = p / "src"
    src.mkdir()
    (src / f"Contract{ext.get(lang, '.sol')}").write_text("// source", encoding="utf-8")
    return p


_SAMPLE_SARIF = {
    "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
    "version": "2.1.0",
    "runs": [{
        "tool": {"driver": {"name": "opengrep", "version": "1.16.4"}},
        "results": [
            {
                "ruleId": "solidity.security.reentrancy",
                "level": "error",
                "message": {"text": "Potential reentrancy in external call"},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": "src/Vault.sol"},
                        "region": {"startLine": 42, "startColumn": 5},
                    }
                }],
            },
            {
                "ruleId": "solidity.security.unchecked-return",
                "level": "warning",
                "message": {"text": "Unchecked return value from transfer"},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": "src/Token.sol"},
                        "region": {"startLine": 88, "startColumn": 9},
                    }
                }],
            },
        ],
    }],
}


def _fake_popen_factory(*, sarif=None, returncode=0, timeout=False, seen=None):
    def _factory(cmd, **kwargs):
        if seen is not None:
            seen.append(cmd)
        if sarif is not None:
            for i, arg in enumerate(cmd):
                if arg == "--sarif-output" and i + 1 < len(cmd):
                    Path(cmd[i + 1]).write_text(json.dumps(sarif), encoding="utf-8")
                    break
        proc = mock.Mock()
        proc.pid = 12345
        proc.returncode = returncode
        if timeout:
            proc.communicate.side_effect = [
                subprocess.TimeoutExpired("opengrep", 300),
                ("", ""),
            ]
        else:
            proc.communicate.return_value = ("", "")
        return proc
    return _factory


# ── _run_opengrep_scan: skip/fail paths ─────────────────────────────────

def test_build_status_forge_uses_bounded_production_compile(tmp_path):
    """Recon forge prepass compiles explicit production sources with one worker."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)
    (proj / "foundry.toml").write_text("[profile.default]\n", encoding="utf-8")
    for rel in ("test/Vault.t.sol", "fuzz/VaultFuzz.sol", "src/MockToken.sol"):
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// excluded", encoding="utf-8")

    seen = []

    def fake_hardened(cmd, *args, **kwargs):
        seen.append(cmd)
        return (0, "")

    with mock.patch("shutil.which", side_effect=lambda name: "/usr/bin/forge" if name == "forge" else None), \
         mock.patch("recon_prepass._run_hardened", side_effect=fake_hardened):
        result = _write_build_status(scratch, proj, "evm")

    assert result == "WRITTEN"
    cmd = seen[0]
    assert cmd[:2] == ["forge", "build"]
    assert "src/Contract.sol" in cmd
    assert "--threads" in cmd
    assert "1" in cmd
    assert all("test/" not in arg and "fuzz/" not in arg and "MockToken.sol" not in arg for arg in cmd)


def test_scan_skip_no_opengrep(tmp_path):
    """No opengrep binary -> SKIPPED."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)
    with mock.patch("shutil.which", return_value=None):
        result = _run_opengrep_scan(scratch, proj, "evm")
    assert result.startswith("SKIPPED:")
    assert "opengrep" in result


def test_scan_skip_no_rules_for_lang(tmp_path):
    """Language with no rules -> SKIPPED."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path, lang="sui")
    with mock.patch("shutil.which", return_value="/usr/bin/opengrep"):
        result = _run_opengrep_scan(scratch, proj, "sui")
    assert result.startswith("SKIPPED:")
    assert "no OpenGrep rules" in result


def test_scan_skip_no_source_files(tmp_path):
    """No relevant source files -> SKIPPED."""
    scratch = _mkscratch(tmp_path)
    proj = tmp_path / "empty_proj"
    proj.mkdir()
    (proj / "README.md").write_text("hello", encoding="utf-8")

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "solidity").mkdir()
    (rules_dir / "solidity" / "test.yaml").write_text("rules: []", encoding="utf-8")

    with mock.patch("shutil.which", return_value="/usr/bin/opengrep"), \
         mock.patch("recon_prepass._ensure_opengrep_rules",
                    return_value={"opengrep-rules": rules_dir, "decurity-rules": rules_dir}):
        result = _run_opengrep_scan(scratch, proj, "evm")
    assert result.startswith("SKIPPED:")
    assert ".sol" in result


def test_scan_targets_only_production_source_files(tmp_path):
    """OpenGrep receives explicit production files, not the whole project tree."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)
    for rel in ("test/Vault.t.sol", "fuzz/VaultFuzz.sol", ".medusa-tests/Medusa.sol"):
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// excluded", encoding="utf-8")

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sol_dir = rules_dir / "solidity"
    sol_dir.mkdir()
    (sol_dir / "test.yaml").write_text("rules: []", encoding="utf-8")
    sec_dir = rules_dir / "solidity" / "security"
    sec_dir.mkdir()
    (sec_dir / "test.yaml").write_text("rules: []", encoding="utf-8")

    seen = []
    with mock.patch("shutil.which", return_value="/usr/bin/opengrep"), \
         mock.patch("recon_prepass._ensure_opengrep_rules",
                    return_value={"opengrep-rules": rules_dir, "decurity-rules": rules_dir}), \
         mock.patch("subprocess.Popen",
                    side_effect=_fake_popen_factory(sarif=_SAMPLE_SARIF, seen=seen)):
        result = _run_opengrep_scan(scratch, proj, "evm")

    assert result == "WRITTEN:2 findings"
    cmd = seen[0]
    assert str(proj) not in cmd
    assert "src/Contract.sol" in cmd
    assert all("test/" not in arg and "fuzz/" not in arg and ".medusa-tests" not in arg for arg in cmd)


def test_scan_fail_timeout(tmp_path):
    """Scan times out -> FAILED."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sol_dir = rules_dir / "solidity"
    sol_dir.mkdir()
    (sol_dir / "test.yaml").write_text("rules: []", encoding="utf-8")
    sec_dir = rules_dir / "solidity" / "security"
    sec_dir.mkdir()
    (sec_dir / "test.yaml").write_text("rules: []", encoding="utf-8")

    # _run_hardened returns the 124 sentinel on a tree-killed timeout.
    with mock.patch("shutil.which", return_value="/usr/bin/opengrep"), \
         mock.patch("recon_prepass._ensure_opengrep_rules",
                    return_value={"opengrep-rules": rules_dir, "decurity-rules": rules_dir}), \
         mock.patch("recon_prepass._run_hardened",
                    return_value=(124, "[hardened: timed out after 300s, tree-killed]")):
        result = _run_opengrep_scan(scratch, proj, "evm")
    assert result.startswith("FAILED:")
    assert "timeout" in result


def test_scan_fail_nonzero_no_sarif(tmp_path):
    """Opengrep exits nonzero and no SARIF -> FAILED."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sol_dir = rules_dir / "solidity"
    sol_dir.mkdir()
    (sol_dir / "test.yaml").write_text("rules: []", encoding="utf-8")
    sec_dir = rules_dir / "solidity" / "security"
    sec_dir.mkdir()
    (sec_dir / "test.yaml").write_text("rules: []", encoding="utf-8")

    with mock.patch("shutil.which", return_value="/usr/bin/opengrep"), \
         mock.patch("recon_prepass._ensure_opengrep_rules",
                    return_value={"opengrep-rules": rules_dir, "decurity-rules": rules_dir}), \
         mock.patch("subprocess.Popen",
                    side_effect=_fake_popen_factory(returncode=2)):
        result = _run_opengrep_scan(scratch, proj, "evm")
    assert "FAILED:" in result or "WRITTEN:0" in result


# ── _parse_opengrep_sarif ────────────────────────────────────────────────

def test_parse_sarif_valid(tmp_path):
    """Valid SARIF produces correct finding count and summary."""
    scratch = _mkscratch(tmp_path)
    sarif_path = scratch / "opengrep_results.sarif"
    sarif_path.write_text(json.dumps(_SAMPLE_SARIF), encoding="utf-8")

    count = _parse_opengrep_sarif(scratch, sarif_path)
    assert count == 2

    summary = (scratch / "opengrep_findings.md").read_text(encoding="utf-8")
    assert "reentrancy" in summary
    assert "unchecked-return" in summary
    assert "src/Vault.sol:L42" in summary
    assert "src/Token.sol:L88" in summary
    assert "Total**: 2" in summary


def test_parse_sarif_empty(tmp_path):
    """Empty results list -> 0 findings."""
    scratch = _mkscratch(tmp_path)
    empty_sarif = {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "opengrep"}}, "results": []}],
    }
    sarif_path = scratch / "opengrep_results.sarif"
    sarif_path.write_text(json.dumps(empty_sarif), encoding="utf-8")

    count = _parse_opengrep_sarif(scratch, sarif_path)
    assert count == 0

    summary = (scratch / "opengrep_findings.md").read_text(encoding="utf-8")
    assert "Total**: 0" in summary


def test_parse_sarif_invalid_json(tmp_path):
    """Invalid JSON -> 0 findings + error note."""
    scratch = _mkscratch(tmp_path)
    sarif_path = scratch / "opengrep_results.sarif"
    sarif_path.write_text("not json {{{", encoding="utf-8")

    count = _parse_opengrep_sarif(scratch, sarif_path)
    assert count == 0

    summary = (scratch / "opengrep_findings.md").read_text(encoding="utf-8")
    assert "parse failed" in summary


def test_parse_sarif_pipe_in_message(tmp_path):
    """Pipe characters in messages are escaped for table rendering."""
    scratch = _mkscratch(tmp_path)
    sarif_with_pipe = {
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "opengrep"}},
            "results": [{
                "ruleId": "test.rule",
                "level": "warning",
                "message": {"text": "found A | B in expression"},
                "locations": [],
            }],
        }],
    }
    sarif_path = scratch / "opengrep_results.sarif"
    sarif_path.write_text(json.dumps(sarif_with_pipe), encoding="utf-8")

    count = _parse_opengrep_sarif(scratch, sarif_path)
    assert count == 1
    summary = (scratch / "opengrep_findings.md").read_text(encoding="utf-8")
    assert "\\|" in summary


# ── _ensure_opengrep_rules ──────────────────────────────────────────────

def test_ensure_rules_skip_if_present(tmp_path):
    """Already cloned repos are returned without git clone."""
    with mock.patch("recon_prepass._OPENGREP_RULES_BASE", tmp_path):
        for name in ("opengrep-rules", "decurity-rules", "aptos-move-rules"):
            d = tmp_path / name
            d.mkdir()
            (d / ".git").mkdir()

        with mock.patch("subprocess.run") as mock_run:
            result = _ensure_opengrep_rules()
        mock_run.assert_not_called()
        assert "opengrep-rules" in result
        assert "decurity-rules" in result
        assert "aptos-move-rules" in result


def test_ensure_rules_clones_missing(tmp_path):
    """Missing repos trigger git clone."""
    with mock.patch("recon_prepass._OPENGREP_RULES_BASE", tmp_path):
        def fake_clone(cmd, *args, **kwargs):
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            return (0, "")

        with mock.patch("recon_prepass._run_hardened", side_effect=fake_clone) as mock_run:
            result = _ensure_opengrep_rules()
        assert mock_run.call_count == 3  # opengrep-rules + decurity + aptos-move-rules
        assert "opengrep-rules" in result


# ── run_recon_prepass wiring ─────────────────────────────────────────────

def test_prepass_evm_skips_opengrep_by_default(tmp_path):
    """Startup pre-pass does not block on external OpenGrep scans by default."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)
    config = {
        "scratchpad": str(scratch),
        "project_root": str(proj),
        "language": "evm",
        "pipeline": "sc",
    }
    with mock.patch("recon_prepass._run_opengrep_scan", return_value="SKIPPED:test") as m:
        results = run_recon_prepass(config)
    m.assert_not_called()
    assert "opengrep_scan" not in results


def test_prepass_evm_triggers_opengrep_when_enabled(tmp_path):
    """Explicit startup scanner opt-in still triggers OpenGrep."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)
    config = {
        "scratchpad": str(scratch),
        "project_root": str(proj),
        "language": "evm",
        "pipeline": "sc",
        "prepass_external_scanners": True,
    }
    with mock.patch("recon_prepass._run_opengrep_scan", return_value="SKIPPED:test") as m:
        results = run_recon_prepass(config)
    m.assert_called_once_with(scratch, proj, "evm")
    assert results.get("opengrep_scan") == "SKIPPED:test"


def test_prepass_solana_triggers_opengrep_when_enabled(tmp_path):
    """SC Solana startup OpenGrep runs only with explicit opt-in."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path, lang="solana")
    config = {
        "scratchpad": str(scratch),
        "project_root": str(proj),
        "language": "solana",
        "pipeline": "sc",
        "prepass_external_scanners": True,
    }
    with mock.patch("recon_prepass._run_opengrep_scan", return_value="SKIPPED:test") as m, \
         mock.patch("recon_prepass._bake_rust_scip", return_value="SKIPPED:test"):
        results = run_recon_prepass(config)
    m.assert_called_once_with(scratch, proj, "solana")


def test_prepass_l1_does_not_trigger_opengrep(tmp_path):
    """L1 pipeline does NOT trigger opengrep scan."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)
    config = {
        "scratchpad": str(scratch),
        "project_root": str(proj),
        "language": "solana",
        "pipeline": "l1",
    }
    with mock.patch("recon_prepass._run_opengrep_scan", return_value="SKIPPED:test") as m:
        results = run_recon_prepass(config)
    m.assert_not_called()


def test_prepass_opengrep_failure_does_not_crash(tmp_path):
    """Opt-in OpenGrep exception doesn't crash prepass."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)
    config = {
        "scratchpad": str(scratch),
        "project_root": str(proj),
        "language": "evm",
        "pipeline": "sc",
        "prepass_external_scanners": True,
    }
    with mock.patch("recon_prepass._run_opengrep_scan", side_effect=RuntimeError("boom")):
        results = run_recon_prepass(config)
    assert "FAILED:" in results.get("opengrep_scan", "")
    assert "contract_inventory.md" in results


# ── end-to-end with mock subprocess ──────────────────────────────────────

def test_scan_success_writes_sarif_and_summary(tmp_path):
    """Full success path: subprocess writes SARIF, parser writes summary."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path)

    # Pre-write build_status.md
    (scratch / "build_status.md").write_text("# Build Status\n\n**Status**: SUCCESS\n",
                                              encoding="utf-8")

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    sol_dir = rules_dir / "solidity"
    sol_dir.mkdir()
    (sol_dir / "test.yaml").write_text("rules: []", encoding="utf-8")
    sec_dir = rules_dir / "solidity" / "security"
    sec_dir.mkdir()
    (sec_dir / "test.yaml").write_text("rules: []", encoding="utf-8")

    with mock.patch("shutil.which", return_value="/usr/bin/opengrep"), \
         mock.patch("recon_prepass._ensure_opengrep_rules",
                    return_value={"opengrep-rules": rules_dir, "decurity-rules": rules_dir}), \
         mock.patch("subprocess.Popen",
                    side_effect=_fake_popen_factory(sarif=_SAMPLE_SARIF)):
        result = _run_opengrep_scan(scratch, proj, "evm")

    assert result == "WRITTEN:2 findings"
    assert (scratch / "opengrep_results.sarif").exists()
    assert (scratch / "opengrep_findings.md").exists()

    summary = (scratch / "opengrep_findings.md").read_text(encoding="utf-8")
    assert "reentrancy" in summary

    # build_status should be updated
    bs = (scratch / "build_status.md").read_text(encoding="utf-8")
    assert "OPENGREP_AVAILABLE: true" in bs
    assert "OPENGREP_FINDINGS: 2" in bs


# ── P5: Aptos Move rules via OpenGrep ───────────────────────────────────

def test_aptos_resolves_move_rules(tmp_path):
    """Aptos lang uses aptos-move-rules repo for OpenGrep scan."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path, lang="aptos")

    rules_base = tmp_path / "aptos_rules"
    rules_base.mkdir()
    move_rules = rules_base / "rules"
    move_rules.mkdir()
    (move_rules / "signer-leak.yaml").write_text("rules: []", encoding="utf-8")

    _MOVE_SARIF = {
        "version": "2.1.0",
        "runs": [{"tool": {"driver": {"name": "opengrep"}}, "results": [{
            "ruleId": "signer-leak",
            "level": "error",
            "message": {"text": "Public function returning signer"},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": "src/module.move"},
                "region": {"startLine": 10},
            }}],
        }]}],
    }

    with mock.patch("shutil.which", return_value="/usr/bin/opengrep"), \
         mock.patch("recon_prepass._ensure_opengrep_rules",
                    return_value={"aptos-move-rules": rules_base}), \
         mock.patch("subprocess.Popen",
                    side_effect=_fake_popen_factory(sarif=_MOVE_SARIF)):
        result = _run_opengrep_scan(scratch, proj, "aptos")

    assert result == "WRITTEN:1 findings"
    summary = (scratch / "opengrep_findings.md").read_text(encoding="utf-8")
    assert "signer-leak" in summary
    assert "src/module.move:L10" in summary


def test_sui_still_skipped_no_rules(tmp_path):
    """Sui lang has no rules -> SKIPPED even with opengrep available."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path, lang="sui")
    with mock.patch("shutil.which", return_value="/usr/bin/opengrep"):
        result = _run_opengrep_scan(scratch, proj, "sui")
    assert result.startswith("SKIPPED:")


def test_prepass_aptos_triggers_opengrep_when_enabled(tmp_path):
    """SC Aptos startup OpenGrep runs only with explicit opt-in."""
    scratch = _mkscratch(tmp_path)
    proj = _mkproj(tmp_path, lang="aptos")
    config = {
        "scratchpad": str(scratch),
        "project_root": str(proj),
        "language": "aptos",
        "pipeline": "sc",
        "prepass_external_scanners": True,
    }
    with mock.patch("recon_prepass._run_opengrep_scan", return_value="SKIPPED:test") as m:
        results = run_recon_prepass(config)
    m.assert_called_once_with(scratch, proj, "aptos")
    assert results.get("opengrep_scan") == "SKIPPED:test"
