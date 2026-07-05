"""Pipeline v2.8.10 — report_index duplicate-binding false-positive on chains.

Root cause (an observed halt): a CHAIN row in the Master Finding Index
(Trust Adj. CHAIN-UPGRADE/CHAIN-DOWNGRADE) lists its `+`-joined CONSTITUENTS,
which legitimately ALSO have their own standalone rows (a chain references
standalone findings — the prompt explicitly allows the `H-2+H-13` form). The
duplicate-binding gate counted those constituents toward the uniqueness
substrate and flagged every chain constituent as a duplicate binding → 31 false
dups → report_index HALT. (a prior run didn't hit it because its chains used `CH-` ids,
so constituents weren't in the chain's internal cell.)

Fix: chain rows index their constituents for completeness (master set) but are
excluded from `master_list` (the duplicate-count substrate). Real double-binds
(same id sole-bound in two non-chain rows) still fire.

Run: pytest scripts/test_pipeline_v2_8_10_index_chain_binding.py -q
"""
from __future__ import annotations

import sys
import tempfile
from collections import Counter
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_validators as V  # noqa: E402


def _mkscratch(report_index_body: str) -> Path:
    sp = Path(tempfile.mkdtemp(prefix="plamen_v2810_"))
    (sp / "report_index.md").write_text(report_index_body, encoding="utf-8")
    return sp


_HEADER = (
    "## Master Finding Index\n\n"
    "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |\n"
    "|---|---|---|---|---|---|---|\n"
)


def test_chain_constituents_not_counted_as_duplicate_binding():
    """The exact observed shape: a CHAIN row whose constituents also
    have standalone rows must NOT be flagged as duplicate-bound."""
    body = _HEADER + (
        "| C-01 | editWinner + withdrawAmt single-block drain | Critical | a.sol:1 | VERIFIED (constituents) | CHAIN-UPGRADE(High) | HH-07+H-18 |\n"
        "| H-07 | withdrawAmt arbitrary destination | High | b.sol:2 | VERIFIED | - | H-18 |\n"
        "| H-12 | editWinner re-target | High | c.sol:3 | VERIFIED | - | HH-07 |\n"
    )
    sp = _mkscratch(body)
    master, _excluded, master_list = V._collect_index_acknowledged_ids(sp)
    counts = Counter(master_list)
    # No internal id appears more than once in the uniqueness substrate.
    dups = [i for i, c in counts.items() if c > 1]
    assert dups == [], f"chain constituents falsely double-counted: {dups} ({counts})"
    # But the constituents ARE still indexed (completeness preserved).
    assert "H-18" in master and "HH-07" in master, master


def test_real_double_binding_still_detected():
    """Guard against over-fix: the SAME internal id sole-bound in two non-chain
    standalone rows is a genuine duplicate and must still be counted."""
    body = _HEADER + (
        "| M-01 | foo | Medium | x:1 | VERIFIED | - | H-99 |\n"
        "| M-02 | bar | Medium | y:2 | VERIFIED | - | H-99 |\n"
    )
    sp = _mkscratch(body)
    _master, _excluded, master_list = V._collect_index_acknowledged_ids(sp)
    assert master_list.count("H-99") == 2, master_list


def test_v2_8_13_compound_constituent_exempt_from_dup_count():
    """v2.8.13 (halt-safe): a `+`-joined compound/consolidation row's constituent
    that ALSO has a single-ID standalone row is NOT a duplicate binding — compound
    rows are references for uniqueness, only single-ID rows count. (The dominant
    report_index HALT class was non-chain compound dups; over-consolidation
    *quality* is a separate, non-halt concern.)"""
    body = _HEADER + (
        "| M-05 | consolidated finding | Medium | z:1 | VERIFIED | - | HH-02+HH-03 |\n"
        "| M-06 | standalone | Medium | z:2 | VERIFIED | - | HH-03 |\n"
    )
    sp = _mkscratch(body)
    m, _e, master_list = V._collect_index_acknowledged_ids(sp)
    # Compound row M-05 contributes nothing to the uniqueness substrate; only the
    # single-ID standalone M-06 counts HH-03 once -> no false duplicate -> no halt.
    assert master_list.count("HH-03") == 1, master_list
    # Both constituents still indexed for completeness.
    assert "HH-02" in m and "HH-03" in m, m
    # And a genuine double-bind (same id sole-bound in TWO single-ID rows) is
    # still caught — exemption applies only to multi-constituent rows.
    body2 = _HEADER + (
        "| M-07 | standalone a | Medium | z:3 | VERIFIED | - | HH-09 |\n"
        "| M-08 | standalone b | Medium | z:4 | VERIFIED | - | HH-09 |\n"
    )
    _m2, _e2, ml2 = V._collect_index_acknowledged_ids(_mkscratch(body2))
    assert ml2.count("HH-09") == 2, ml2
