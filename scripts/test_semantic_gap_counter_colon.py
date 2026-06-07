"""_SEMANTIC_GAP_COUNTER_RE accuracy: read the flag form agents ACTUALLY write.

Verified against real DODO + AwesomeX semantic_invariants.md: agents write
`- sync_gaps: 0` (COLON), often inside a ``` fence. The prior `=`-only regex
matched NONE of it, so the explicit per-flag counts were always 0 and the
niche-trigger decision fell back to the fuzzy word-presence heuristic (lines
3481-3489), which only sets a binary 1 and fires on prose mentions.

NOTE: this is an ACCURACY/robustness fix, not a recall fix — the fallback
already made `_semantic_gap_required` True on those runs (the niche fired). This
makes the explicit counts correct (conditional_writes=4, not 1) and removes the
dependency on the loose fallback.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path


def _val():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_validators")


def _mk(body: str) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "semantic_invariants.md").write_text(body, encoding="utf-8")
    return d


def test_colon_format_read_exact_values():
    v = _val()
    # colon form inside a code fence, no SYNC_GAP/CONDITIONAL prose words ->
    # counts come purely from the explicit regex, not the fallback.
    sp = _mk("## Pass 2\n```\n- sync_gaps: 0\n- conditional_writes: 4\n"
             "- cluster_gaps: 2\n```\n")
    c = v._semantic_gap_trigger_counts(sp)
    assert c["conditional_writes"] == 4, c   # exact value, not fallback's 1
    assert c["cluster_gaps"] == 2, c
    assert c["sync_gaps"] == 0, c
    assert v._semantic_gap_required(sp) is True


def test_equals_format_still_supported():
    v = _val()
    sp = _mk("## Pass 2\n- sync_gaps = 0\n- conditional_writes = 4\n")
    assert v._semantic_gap_trigger_counts(sp)["conditional_writes"] == 4
    assert v._semantic_gap_required(sp) is True


def test_bold_and_backtick_wrappers_tolerated():
    v = _val()
    sp = _mk("## Pass 2\n- **sync_gaps**: 1\n- `cluster_gaps`: 0\n")
    c = v._semantic_gap_trigger_counts(sp)
    assert c["sync_gaps"] == 1
    assert v._semantic_gap_required(sp) is True


def test_no_flag_data_required_false():
    v = _val()
    sp = _mk("## Pass 2\n- prose only, no trigger flags here\n")
    assert v._semantic_gap_required(sp) is False


def test_zero_flags_not_required():
    v = _val()
    sp = _mk("## Pass 2\n- sync_gaps: 0\n- accumulation_exposures: 0\n"
             "- conditional_writes: 0\n- cluster_gaps: 0\n")
    # all explicit zeros, no fallback words -> not required.
    assert v._semantic_gap_required(sp) is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
