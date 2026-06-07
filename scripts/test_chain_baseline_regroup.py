"""Tests for the H-03 chain scaffold-resumption collision fix.

Covers `_validate_chain_baseline_not_regrouped` and
`_generate_chain_baseline_regroup_retry_hint` (plamen_validators.py).

The driver writes a MECHANICAL_BASELINE scaffold before Chain Agent 1 runs.
If the agent obeys the generic RESUMPTION PROTOCOL it can skip PHASE 1 grouping,
leaving the scaffold in place — post-inventory depth findings (DA-*) then never
get mapped and are dropped from the report. The mechanical gate must FAIL on the
two observable signatures of that no-op and PASS a healthy grouped run.

GENERIC ONLY: neutral IDs (INV-*/DA-*/GRP-* shapes), no protocol/finding names.

Run: pytest scripts/test_chain_baseline_regroup.py -v
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _pv():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    return importlib.import_module("plamen_validators")


# ───────────────────────────── fixtures ─────────────────────────────


def _write_inventory(sp: Path, ids: list[str]) -> None:
    lines = ["# Findings Inventory", ""]
    for fid in ids:
        lines.append(f"### Finding [{fid}]: {fid} title")
        lines.append("**Severity**: Medium")
        lines.append(f"**Location**: src/Foo.sol:L{10 + len(lines)}")
        lines.append("**Root Cause**: example root cause text")
        lines.append("")
    (sp / "findings_inventory.md").write_text("\n".join(lines), encoding="utf-8")


def _write_depth(sp: Path, name: str, ids: list[str]) -> None:
    lines = [f"# Depth Agent {name}", ""]
    for fid in ids:
        lines.append(f"### Finding [{fid}]: {fid} depth title")
        lines.append("**Verdict**: CONFIRMED")
        lines.append("**Severity**: Medium")
        lines.append(f"**Location**: src/Bar.sol:L{20 + len(lines)}")
        lines.append("")
    (sp / f"depth_{name}_findings.md").write_text("\n".join(lines), encoding="utf-8")


def _write_grouped(sp: Path, rows: list[tuple[str, str]], stamped: bool) -> None:
    """rows: list of (constituent_id, hypothesis_id)."""
    head_status = "**Status**: MECHANICAL_BASELINE\n\n" if stamped else ""
    hyp = (
        "# Hypotheses\n\n"
        + head_status
        + "| Hypothesis ID | Severity | Title | Constituent Findings | Location | Notes |\n"
        + "|---------------|----------|-------|----------------------|----------|-------|\n"
    )
    by_hyp: dict[str, list[str]] = {}
    for cid, hid in rows:
        by_hyp.setdefault(hid, []).append(cid)
    for hid, cids in by_hyp.items():
        hyp += f"| {hid} | Medium | grouped | {', '.join(cids)} | UNKNOWN | grouped |\n"
    (sp / "hypotheses.md").write_text(hyp, encoding="utf-8")

    fm = (
        "# Finding Mapping\n\n"
        + head_status
        + "| Finding ID | Hypothesis ID | Mapping Status | Notes |\n"
        + "|------------|---------------|----------------|-------|\n"
    )
    for cid, hid in rows:
        fm += f"| {cid} | {hid} | GROUPED | grouped |\n"
    (sp / "finding_mapping.md").write_text(fm, encoding="utf-8")


# ───────────────────────────── tests ─────────────────────────────


def test_drop_reproduction_stamp_and_unmapped(tmp_path: Path) -> None:
    """The genuine no-op: scaffold un-overwritten + DA-3 unmapped."""
    pv = _pv()
    _write_inventory(tmp_path, ["INV-1", "INV-2", "INV-3"])
    pv._write_chain_passthrough_outputs(tmp_path, "test")  # stamped scaffold
    _write_depth(tmp_path, "x", ["DA-3"])

    issues = pv._validate_chain_baseline_not_regrouped(tmp_path, "thorough")
    assert issues, "expected violations on un-overwritten scaffold"
    joined = " | ".join(issues)
    assert "MECHANICAL_BASELINE" in joined, "stamp issue missing"
    assert "DA-3" in joined, "unmapped depth-ID issue missing"


def test_drop_reproduction_depth_id_only(tmp_path: Path) -> None:
    """Stamp removed (partial overwrite) but DA-3 still unmapped — CHECK 2 alone."""
    pv = _pv()
    _write_inventory(tmp_path, ["INV-1", "INV-2", "INV-3"])
    _write_grouped(
        tmp_path,
        [("INV-1", "GRP-1"), ("INV-2", "GRP-1"), ("INV-3", "GRP-2")],
        stamped=False,
    )
    _write_depth(tmp_path, "x", ["DA-3"])

    issues = pv._validate_chain_baseline_not_regrouped(tmp_path, "thorough")
    assert len(issues) == 1, f"expected exactly one issue, got {issues}"
    assert "DA-3" in issues[0]
    assert "MECHANICAL_BASELINE" not in issues[0]


def test_healthy_grouped_run_no_fire(tmp_path: Path) -> None:
    """Recall-safety anchor: a real grouped run produces zero issues."""
    pv = _pv()
    _write_inventory(tmp_path, ["INV-1", "INV-2", "INV-3"])
    _write_grouped(
        tmp_path,
        [
            ("INV-1", "GRP-1"),
            ("INV-2", "GRP-1"),
            ("INV-3", "GRP-2"),
            ("DA-3", "GRP-2"),
        ],
        stamped=False,
    )
    _write_depth(tmp_path, "x", ["DA-3"])

    issues = pv._validate_chain_baseline_not_regrouped(tmp_path, "thorough")
    assert issues == [], f"healthy grouped run should not fire: {issues}"


def test_light_mode_skip(tmp_path: Path) -> None:
    """Gate only runs in core/thorough; light mode never blocks."""
    pv = _pv()
    _write_inventory(tmp_path, ["INV-1", "INV-2", "INV-3"])
    pv._write_chain_passthrough_outputs(tmp_path, "test")  # stamped scaffold
    _write_depth(tmp_path, "x", ["DA-3"])

    assert pv._validate_chain_baseline_not_regrouped(tmp_path, "light") == []


def test_chain_summary_cross_ref_not_declared(tmp_path: Path) -> None:
    """CHECK 2 sources IDs ONLY from `### Finding [ID]:` depth headings.

    A REF-9 mentioned in chain_summaries_compact.md prose but not declared in
    any depth_*_findings.md must not be treated as a dropped depth finding.
    """
    pv = _pv()
    _write_inventory(tmp_path, ["INV-1", "INV-2"])
    _write_grouped(
        tmp_path,
        [("INV-1", "GRP-1"), ("INV-2", "GRP-1"), ("DA-7", "GRP-2")],
        stamped=False,
    )
    _write_depth(tmp_path, "x", ["DA-7"])  # only genuinely-declared depth ID
    (tmp_path / "chain_summaries_compact.md").write_text(
        "# Chain Summaries\n\nSome prose referencing REF-9 as a refuted item.\n",
        encoding="utf-8",
    )

    issues = pv._validate_chain_baseline_not_regrouped(tmp_path, "thorough")
    assert issues == [], f"cross-ref-only ID must not fire: {issues}"


def test_retry_hint_generation(tmp_path: Path) -> None:
    pv = _pv()
    issues = [
        "hypotheses.md still carries MECHANICAL_BASELINE stamp",
        "depth finding DA-3 declared in depth_x_findings.md is absent",
    ]
    hint = pv._generate_chain_baseline_regroup_retry_hint(issues)
    assert "ATTEMPT 2 RETRY" in hint
    assert "DA-3" in hint
    assert "OVERWRITE" in hint
    assert pv._generate_chain_baseline_regroup_retry_hint([]) == ""
