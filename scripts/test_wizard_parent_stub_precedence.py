"""Wizard existing-audit detection: a configless stub in a PARENT dir
(cwd/.scratchpad, created when launching from a parent and scoping the audit to a
subdir like /contracts) must NOT mask the real config+checkpoint in a child dir
(cwd/contracts/.scratchpad). Regression for the 'config missing (not recoverable)'
false alarm on resume.
"""
from __future__ import annotations
import importlib, json, os, sys
from pathlib import Path


def _pl():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return importlib.import_module("plamen")


def _real_audit(d: Path):
    sp = d / ".scratchpad"; sp.mkdir(parents=True)
    json.dump({"mode": "thorough", "pipeline": "sc", "language": "evm",
               "project_root": str(d)}, (sp / "config.json").open("w"))
    json.dump({"completed": ["recon", "breadth"], "config": {"mode": "thorough"}},
              (sp / "_v2_checkpoint.json").open("w"))


def _stub(d: Path):
    sp = d / ".scratchpad"; sp.mkdir(parents=True)
    (sp / "analysis_x.md").write_text("# stray\n", encoding="utf-8")


def test_child_config_wins_over_parent_stub(tmp_path):
    PL = _pl()
    _stub(tmp_path)                      # cwd/.scratchpad : configless stub
    _real_audit(tmp_path / "contracts")  # cwd/contracts/.scratchpad : real audit
    info = PL._find_existing_audit(cwd=str(tmp_path))
    assert info is not None
    assert info.get("config_missing", False) is False, info
    assert "contracts" in str(info.get("scratchpad", "")), info


def test_stub_alone_still_reports_missing(tmp_path):
    PL = _pl()
    _stub(tmp_path)  # only a configless stub, no real audit anywhere
    info = PL._find_existing_audit(cwd=str(tmp_path))
    assert info is not None and info.get("config_missing") is True, info


def test_src_config_wins_over_parent_stub(tmp_path):
    PL = _pl()
    _stub(tmp_path)
    _real_audit(tmp_path / "src")
    info = PL._find_existing_audit(cwd=str(tmp_path))
    assert info.get("config_missing", False) is False
    assert "src" in str(info.get("scratchpad", ""))


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
