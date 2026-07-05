"""M2 (recall): interface-vs-implementation parity. A contract that `is IFoo`
but whose external/public function is missing from `IFoo` is an interface-
completeness gap (the Umia I-01 miss). Inheritance-gated, standard-fn-denylisted,
mechanical Solidity parse. Pins low false positives."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _rp():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("recon_prepass")


def test_flags_custom_omission_only(tmp_path: Path):
    rp = _rp()
    (tmp_path / "IFoo.sol").write_text(
        "interface IFoo {\n function a() external;\n}\n", encoding="utf-8")
    (tmp_path / "Foo.sol").write_text(
        "contract Foo is IFoo {\n"
        " function a() external {}\n"            # declared -> ok
        " function createThing() external {}\n"  # custom, missing -> FLAG
        " function _helper() internal {}\n"      # internal -> skip
        " function supportsInterface(bytes4) external returns(bool){}\n"  # std -> skip
        " function onERC721Received() external returns(bytes4){}\n"       # std -> skip
        "}\n", encoding="utf-8")
    fs = rp.compute_interface_parity_findings(str(tmp_path))
    assert len(fs) == 1
    assert "createThing" in fs[0]["title"]
    assert fs[0]["severity"] == "Informational"


def test_not_inheriting_interface_no_flag(tmp_path: Path):
    rp = _rp()
    # contract does NOT inherit any interface -> inheritance-gated -> no flags
    (tmp_path / "Bar.sol").write_text(
        "contract Bar {\n function x() external {}\n}\n", encoding="utf-8")
    assert rp.compute_interface_parity_findings(str(tmp_path)) == []


def test_denylist_excludes_standard_fns(tmp_path: Path):
    rp = _rp()
    (tmp_path / "IX.sol").write_text("interface IX {\n}\n", encoding="utf-8")
    (tmp_path / "X.sol").write_text(
        "contract X is IX {\n"
        " function initialize() external {}\n"
        " function transferOwnership(address) external {}\n"
        " function unlockCallback(bytes calldata) external {}\n"
        "}\n", encoding="utf-8")
    assert rp.compute_interface_parity_findings(str(tmp_path)) == []


def test_writer_emits_promotable_niche_file(tmp_path: Path):
    rp = _rp()
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / "IFoo.sol").write_text("interface IFoo {\n}\n", encoding="utf-8")
    (proj / "Foo.sol").write_text(
        "contract Foo is IFoo {\n function createThing() external {}\n}\n", encoding="utf-8")
    rp._write_interface_parity_findings(sp, proj)
    out = (sp / "niche_interface_parity_findings.md").read_text(encoding="utf-8")
    # must be parseable by the niche promoter (IFACE-N + required fields)
    assert "### Finding [IFACE-1]:" in out
    assert "**Severity**:" in out and "**Location**:" in out and "**Description**:" in out
    # confirm the niche promoter recognizes the id form
    m = importlib.import_module("plamen_mechanical")
    assert m._NICHE_FINDING_HEADING_RE.search("### Finding [IFACE-1]: x")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
