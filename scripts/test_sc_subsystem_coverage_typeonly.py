"""FIX #3: SC subsystem-coverage gate must not false-fail type-only buckets.

A bucket whose .sol files ALL lack an executable body (pure struct/enum/
constant/type-alias/abstract type-holder) has no logic for depth to cite —
flagging it as "uncovered" is a false positive that retries twice + runs a
repair + degrades. The gate now skips such buckets, ACCOUNTS for each file
(`[SCOPE-TYPE-ONLY: ...]`, no silent drop), and routes paired cross-chain /
struct<->typehash type files to the consistency lane. A bucket with ANY
executable body, or an unresolvable/unreadable file, stays REQUIRED (recall
preserved; conservative).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import plamen_validators as V


_TYPE_FILE = (
    "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.0;\n\n"
    "struct {name} {{ uint256 a; address b; }}\n"
    "enum Kind {{ A, B, C }}\n"
    "uint256 constant MAX = 1e18;\n"
)
_INTERFACE_FILE = (
    "pragma solidity ^0.8.0;\n"
    "interface I{name} {{ function foo() external returns (uint256); }}\n"
)
_LOGIC_FILE = (
    "pragma solidity ^0.8.0;\n"
    "contract {name} {{\n"
    "  uint256 public x;\n"
    "  function setX(uint256 v) public {{ x = v; }}\n"
    "}}\n"
)


def _build(project_files: dict[str, str], cited: list[str] | None = None):
    """Create (scratchpad, project_root). repo_map.md lists every project file;
    `analysis_breadth.md` cites the given paths.

    A single cited sentinel logic file (`src/core/Sentinel.sol`, its own
    <min_bucket_files bucket so it never affects bucketing) is always added so
    the audit has >=1 citation — otherwise the gate's separate "zero source
    citations across the audit" guard fires, which is unrelated to the
    type-only-bucket behaviour under test.
    """
    proot = Path(tempfile.mkdtemp(prefix="plamen_typeonly_root_"))
    sp = Path(tempfile.mkdtemp(prefix="plamen_typeonly_sp_"))
    (sp / "scip").mkdir()
    files = dict(project_files)
    files["src/core/Sentinel.sol"] = _LOGIC_FILE.format(name="Sentinel")
    repo_map = "".join(f"## {rel}\n" for rel in files)
    (sp / "scip" / "repo_map.md").write_text(repo_map, encoding="utf-8")
    for rel, body in files.items():
        fp = proot / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(body, encoding="utf-8")
    cite_list = list(cited or []) + ["src/core/Sentinel.sol"]
    cite_body = "# Breadth\n" + "\n".join(f"- {c}" for c in cite_list)
    (sp / "analysis_breadth.md").write_text(cite_body, encoding="utf-8")
    return sp, str(proot)


def test_type_only_bucket_not_flagged():
    files = {
        f"src/types/CommonTypes.sol": _TYPE_FILE.format(name="Common"),
        f"src/types/CouponTypes.sol": _TYPE_FILE.format(name="Coupon"),
        f"src/types/MarketTypes.sol": _TYPE_FILE.format(name="Market"),
        f"src/types/AssetTypes.sol": _TYPE_FILE.format(name="Asset"),
    }
    sp, proot = _build(files, cited=[])
    issues = V._validate_sc_subsystem_coverage(
        sp, "thorough", project_root=proot
    )
    assert issues == [], issues
    cov = (sp / "sc_subsystem_coverage.md").read_text(encoding="utf-8")
    # No silent drop: every type-only file is accounted.
    assert "SCOPE-TYPE-ONLY" in cov
    assert "CommonTypes.sol" in cov


def test_bucket_with_logic_file_still_flagged():
    files = {
        "src/logic/A.sol": _TYPE_FILE.format(name="A"),
        "src/logic/B.sol": _TYPE_FILE.format(name="B"),
        "src/logic/C.sol": _TYPE_FILE.format(name="C"),
        "src/logic/Vault.sol": _LOGIC_FILE.format(name="Vault"),  # has a body
    }
    sp, proot = _build(files, cited=[])
    issues = V._validate_sc_subsystem_coverage(
        sp, "thorough", project_root=proot
    )
    assert any("src/logic" in i for i in issues), issues


def test_mixed_bucket_logic_cited_is_covered():
    files = {
        "src/mix/A.sol": _TYPE_FILE.format(name="A"),
        "src/mix/B.sol": _TYPE_FILE.format(name="B"),
        "src/mix/C.sol": _TYPE_FILE.format(name="C"),
        "src/mix/Vault.sol": _LOGIC_FILE.format(name="Vault"),
    }
    sp, proot = _build(files, cited=["src/mix/Vault.sol"])
    issues = V._validate_sc_subsystem_coverage(
        sp, "thorough", project_root=proot
    )
    assert issues == [], issues


def test_unresolvable_files_stay_required():
    # project_root=None → paths cannot resolve → treated as having a body →
    # bucket flagged (conservative, never drops a real logic file on an IO miss).
    files = {
        "src/types/CommonTypes.sol": _TYPE_FILE.format(name="Common"),
        "src/types/CouponTypes.sol": _TYPE_FILE.format(name="Coupon"),
        "src/types/MarketTypes.sol": _TYPE_FILE.format(name="Market"),
        "src/types/AssetTypes.sol": _TYPE_FILE.format(name="Asset"),
    }
    sp, _proot = _build(files, cited=[])
    issues = V._validate_sc_subsystem_coverage(
        sp, "thorough", project_root=None
    )
    assert any("src/types" in i for i in issues), issues


def test_cross_chain_type_twins_emit_consistency_lead():
    files = {
        "src/types/AssetTypesEthereum.sol": _TYPE_FILE.format(name="AssetEth"),
        "src/types/AssetTypesPolygon.sol": _TYPE_FILE.format(name="AssetPoly"),
        "src/types/CommonTypes.sol": _TYPE_FILE.format(name="Common"),
        "src/types/CouponTypes.sol": _TYPE_FILE.format(name="Coupon"),
    }
    sp, proot = _build(files, cited=[])
    issues = V._validate_sc_subsystem_coverage(
        sp, "thorough", project_root=proot
    )
    # Does NOT fail the gate ...
    assert issues == [], issues
    cov = (sp / "sc_subsystem_coverage.md").read_text(encoding="utf-8")
    # ... but routes the twins to the consistency lane.
    assert "CONSISTENCY-CHECK" in cov
    dp = (sp / "detected_patterns.md").read_text(encoding="utf-8")
    assert "CROSS_TYPE_CONSISTENCY" in dp


def test_non_thorough_mode_noop():
    files = {"src/types/A.sol": _TYPE_FILE.format(name="A")}
    sp, proot = _build(files, cited=[])
    assert V._validate_sc_subsystem_coverage(
        sp, "core", project_root=proot
    ) == []
