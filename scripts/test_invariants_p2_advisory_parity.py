"""FIX #1: invariants_p2 advisory must use the REAL semantic-gap trigger.

The `_validate_invariants_pass2` advisory previously used a local stricter
parser (`_FLAG_TOKEN`, `flag [:=] -?\\d`, needing >=2) that DIFFERED from the
actual depth niche trigger `_semantic_gap_required` (which also counts gap
TOKENS like SYNC_GAP / CONDITIONAL and table cells). That mismatch produced
false-alarm WARNINGs ("1/4, may not fire") on runs where the niche actually
fired. The advisory now gates on `_semantic_gap_required` directly, so its
warn/no-warn decision can NEVER disagree with whether the niche fires.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import tempfile
from pathlib import Path


def _val():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_validators")


def _mk(body: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="plamen_p2_parity_"))
    (d / "semantic_invariants.md").write_text(body, encoding="utf-8")
    return d


def _warned(v, sp, caplog) -> bool:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="plamen.validators"):
        issues = v._validate_invariants_pass2(sp, "thorough")
    assert issues == []  # advisory is SOFT — never returns hard issues
    return any(
        "invariants_p2" in rec.getMessage() for rec in caplog.records
    )


def test_token_only_flags_no_warning(caplog):
    # Pass 2 header + token-form gap flags (CONDITIONAL / SYNC_GAP), NO `flag: N`
    # summary footer. The real trigger fires via the token fallback → advisory
    # must stay SILENT.
    v = _val()
    sp = _mk(
        "## Pass 2: Recursive Trace Results\n"
        "Found a SYNC_GAP between deposit and withdraw accounting.\n"
        "A CONDITIONAL write path skips the timestamp update.\n"
    )
    assert v._semantic_gap_required(sp) is True
    assert _warned(v, sp, caplog) is False


def test_zero_gap_flags_warns(caplog):
    # Pass 2 header present but ZERO gap flags/tokens → niche will NOT fire →
    # advisory DOES warn.
    v = _val()
    sp = _mk(
        "## Pass 2: Recursive Trace Results\n"
        "No semantic gaps were found. All accounting paths are symmetric.\n"
    )
    assert v._semantic_gap_required(sp) is False
    assert _warned(v, sp, caplog) is True


def test_table_cell_flags_no_warning(caplog):
    # Flags rendered as table cells (`| sync_gaps | 5 |`) — the old local parser
    # was pipe-blind in its first incarnation. `_semantic_gap_required` reads
    # them → no warning.
    v = _val()
    sp = _mk(
        "## Pass 2 Summary Statistics\n"
        "| flag | count |\n|------|-------|\n| sync_gaps | 5 |\n"
        "| accumulation_exposures | 0 |\n"
    )
    assert v._semantic_gap_required(sp) is True
    assert _warned(v, sp, caplog) is False


def test_advisory_parity_holds(caplog):
    # The advisory's warn decision == `not _semantic_gap_required(...)` for every
    # fixture — they can never disagree (single source of truth).
    v = _val()
    fixtures = [
        "## Pass 2\nSYNC_GAP found at L42.\n",
        "## Pass 2\n- conditional_writes: 4\n",
        "## Pass 2\nNothing notable here.\n",
        "## Pass 2\n| cluster_gaps | 2 |\n",
    ]
    for body in fixtures:
        sp = _mk(body)
        warned = _warned(v, sp, caplog)
        assert warned == (not v._semantic_gap_required(sp)), body


def test_no_pass2_header_writes_degraded_sentinel(caplog):
    # No Pass 2 section at all → soft-degrade sentinel, no hard issue.
    v = _val()
    sp = _mk("## Pass 1: Semantic Invariants\nSome content, no pass 2.\n")
    issues = v._validate_invariants_pass2(sp, "thorough")
    assert issues == []
    assert (sp / "invariants_p2.degraded").exists()


def test_non_thorough_mode_noop():
    v = _val()
    sp = _mk("## Pass 2\nNothing.\n")
    assert v._validate_invariants_pass2(sp, "core") == []
