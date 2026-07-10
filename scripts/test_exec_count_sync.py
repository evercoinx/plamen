"""Executive-Summary finding count must match the delivered `## Summary` table.

Regression for the report-assembly bug where the Executive Summary prose count
(``identified **N findings**: A Critical, ...``) disagreed with the mechanical
``## Summary`` counts table. The prose count is generated once at assembly time
from the pre-floor report_index counts; the material-harm floor then relocates
pure-quality body findings into Appendix C and DECREMENTS the ``## Summary``
table — leaving the prose count stale (e.g. exec says 79, table says 45).

`_reconcile_exec_summary_count` rewrites the prose count to match the (authoritative,
mechanically-correct) ``## Summary`` table without touching the table itself.

Run: `python -m pytest -q test_exec_count_sync.py`
"""
from __future__ import annotations

import importlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

M = importlib.import_module("plamen_mechanical")


def _make_report(exec_total: int, exec_split, table_split) -> str:
    """Assembled report whose exec prose and `## Summary` table can disagree.

    `exec_split` / `table_split` are (C, H, M, L, I) tuples.
    """
    ec, eh, em, el, ei = exec_split
    tc, th, tm, tl, ti = table_split
    ttotal = tc + th + tm + tl + ti
    return (
        "# Security Audit Report — Example\n\n"
        "**Date**: 2026-07-03\n\n"
        "---\n\n"
        "## Executive Summary\n\n"
        f"This security audit examined `Example`. The audit identified "
        f"**{exec_total} findings**: {ec} Critical, {eh} High, {em} Medium, "
        f"{el} Low, {ei} Informational.\n\n"
        "The most severe issues:\n\n_No Critical findings._\n\n"
        "## Summary\n\n"
        "| Severity | Count |\n"
        "|----------|-------|\n"
        f"| Critical | {tc} |\n"
        f"| High | {th} |\n"
        f"| Medium | {tm} |\n"
        f"| Low | {tl} |\n"
        f"| Informational | {ti} |\n"
        f"| **Total** | **{ttotal}** |\n\n"
        "## High Findings\n\n"
        "### [H-01] Something\n\nbody\n"
    )


def _parse_exec(text: str):
    m = re.search(
        r"identified\s+\**(\d+)\s+findings?\**\s*:\s*"
        r"(\d+)\s+Critical\s*,\s*(\d+)\s+High\s*,\s*(\d+)\s+Medium\s*,\s*"
        r"(\d+)\s+Low\s*,\s*(\d+)\s+Informational",
        text, re.IGNORECASE,
    )
    assert m, "exec-summary count line not found"
    total, c, h, md, lo, i = (int(x) for x in m.groups())
    return total, (c, h, md, lo, i)


def _parse_table(text: str):
    region = text.split("## Summary", 1)[1]
    out = {}
    for sev in ("Critical", "High", "Medium", "Low", "Informational"):
        rm = re.search(rf"\|\s*{sev}\s*\|\s*(\d+)\s*\|", region)
        out[sev] = int(rm.group(1))
    return out


def test_exec_count_synced_to_summary_table():
    # Exec says 79 (0/10/26/24/19); table (delivered body) totals 45 (0/10/15/12/8).
    exec_split = (0, 10, 26, 24, 19)
    table_split = (0, 10, 15, 12, 8)
    text = _make_report(79, exec_split, table_split)

    before_total, before_split = _parse_exec(text)
    assert before_total == 79 and before_split == exec_split

    out = M._reconcile_exec_summary_count(text)

    total, split = _parse_exec(out)
    assert total == 45, f"exec total not synced: {total}"
    assert split == table_split, f"exec split not synced: {split}"
    # The mechanically-correct Summary table must be UNCHANGED.
    assert _parse_table(out) == {
        "Critical": 0, "High": 10, "Medium": 15, "Low": 12, "Informational": 8
    }


def test_second_split_case():
    # real-audit-shaped: exec 89 (8/17/27/29/8), delivered table 73 (8/17/20/20/8).
    exec_split = (8, 17, 27, 29, 8)
    table_split = (8, 17, 20, 20, 8)
    text = _make_report(89, exec_split, table_split)
    out = M._reconcile_exec_summary_count(text)
    total, split = _parse_exec(out)
    assert total == 73
    assert split == table_split


def test_idempotent():
    text = _make_report(79, (0, 10, 26, 24, 19), (0, 10, 15, 12, 8))
    once = M._reconcile_exec_summary_count(text)
    twice = M._reconcile_exec_summary_count(once)
    assert once == twice, "reconcile is not idempotent"
    # Already-consistent report is a stable no-op.
    third = M._reconcile_exec_summary_count(twice)
    assert third == twice


def test_already_consistent_noop():
    text = _make_report(45, (0, 10, 15, 12, 8), (0, 10, 15, 12, 8))
    out = M._reconcile_exec_summary_count(text)
    assert out == text


def test_missing_exec_count_line_unchanged():
    text = (
        "# Security Audit Report — Example\n\n"
        "## Executive Summary\n\n"
        "This audit reviewed the protocol and found several issues worth noting.\n\n"
        "## Summary\n\n"
        "| Severity | Count |\n"
        "|----------|-------|\n"
        "| Critical | 0 |\n| High | 2 |\n| Medium | 3 |\n| Low | 1 |\n"
        "| Informational | 0 |\n| **Total** | **6** |\n"
    )
    assert M._reconcile_exec_summary_count(text) == text


def test_missing_summary_table_unchanged():
    text = (
        "## Executive Summary\n\n"
        "The audit identified **5 findings**: 0 Critical, 1 High, 2 Medium, "
        "1 Low, 1 Informational.\n\n"
        "## High Findings\n\n### [H-01] X\n"
    )
    assert M._reconcile_exec_summary_count(text) == text


def test_empty_and_none_safe():
    assert M._reconcile_exec_summary_count("") == ""


def test_floor_integration_syncs_exec():
    """End-to-end via enforce_material_harm_floor: relocating a pure-quality
    finding decrements the Summary table AND the exec prose count together."""
    text = (
        "# Security Audit Report — Example\n\n"
        "## Executive Summary\n\n"
        "The audit identified **2 findings**: 0 Critical, 0 High, 0 Medium, "
        "2 Low, 0 Informational.\n\n"
        "## Summary\n\n"
        "| Severity | Count |\n"
        "|----------|-------|\n"
        "| Critical | 0 |\n| High | 0 |\n| Medium | 0 |\n| Low | 2 |\n"
        "| Informational | 0 |\n| **Total** | **2** |\n\n"
        "## Low Findings\n\n"
        "### [L-01] Real reentrancy loss\n\n"
        "**Severity**: Low\n**Location**: `src/A.sol:L10`\n\nbody\n\n"
        "### [L-02] Missing event on setter\n\n"
        "**Severity**: Low\n**Location**: `src/A.sol:L20`\n\nbody\n"
    )
    disposition = {
        "L-01": ("BODY", ""),
        "L-02": ("APPENDIX", "observability / missing events"),
    }
    out, moved = M.enforce_material_harm_floor(text, disposition)
    assert len(moved) == 1 and moved[0][0] == "L-02"
    total, split = _parse_exec(out)
    # One Low relocated: exec total 2 -> 1, Low 2 -> 1.
    assert total == 1, f"exec total not synced after floor: {total}"
    assert split == (0, 0, 0, 1, 0), f"exec split not synced after floor: {split}"
    # Summary table Low row also decremented to 1.
    assert _parse_table(out)["Low"] == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
