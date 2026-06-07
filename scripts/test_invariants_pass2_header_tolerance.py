"""Header-regex brittleness regression: _validate_invariants_pass2 used exact
`^###\\s+Summary\\s+Flags` / `^##\\s+Pass\\s*2\\s*[:-]` matches, so the LLM
writing the same block in a slightly different shape (## vs ### vs ####, bold,
leading ws, trailing qualifier) produced a recurring FALSE "missing Summary
Flags subblock" warning on essentially every Thorough run.

These fixtures pin the tolerant matching: a Pass 2 section with a Summary Flags
block in ANY common shape must NOT warn; a genuinely-absent block still warns;
prose mentioning the words must not be mistaken for the block.
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path


def _val():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_validators")


_MISS = "missing"  # the warning contains "missing `### Summary Flags` subblock"


def _run(tmp_path: Path, body: str, caplog):
    (tmp_path / "semantic_invariants.md").write_text(body, encoding="utf-8")
    v = _val()
    with caplog.at_level(logging.WARNING, logger="plamen.validators"):
        issues = v._validate_invariants_pass2(tmp_path, "thorough")
    warned = any(_MISS in r.getMessage() and "Summary Flags" in r.getMessage()
                 for r in caplog.records)
    return issues, warned


_PASS1 = "# Semantic Invariants\n\n## Pass 1\n- inv A\n- inv B\n\n"

# Summary Flags block shapes the OLD regex would have rejected -> must NOT warn.
_VARIANTS = [
    "## Pass 2: Recursive Trace Results\n\n### Summary Flags\n- sync_gaps: 0\n",
    "## Pass 2 — Recursive Trace\n\n#### Summary Flags\n- cluster_gaps: 1\n",
    "### Pass 2\n\n## Summary Flags\nsync_gaps=1\n",
    "## Pass 2: results\n\n**Summary Flags**\n- accumulation_exposures: 0\n",
    "## Pass 2\n\n   ### Summary Flags\nflags...\n",
    "## Pass 2: trace\n\n### SUMMARY FLAGS\n- conditional_writes: 2\n",
]


def test_summary_flags_shape_variants_do_not_warn(tmp_path, caplog):
    for i, blk in enumerate(_VARIANTS):
        caplog.clear()
        issues, warned = _run(tmp_path, _PASS1 + blk, caplog)
        assert issues == [], f"variant {i}: must stay soft (return [])"
        assert not warned, f"variant {i} should NOT trigger the missing-flags warning: {blk!r}"


def test_genuinely_missing_summary_flags_still_warns(tmp_path, caplog):
    body = _PASS1 + "## Pass 2: Recursive Trace Results\n\n- some trace, no flags block\n"
    issues, warned = _run(tmp_path, body, caplog)
    assert issues == []          # still soft
    assert warned                # genuine absence still flagged


def test_prose_mentioning_words_is_not_the_block(tmp_path, caplog):
    # "the summary flags are ..." mid-line prose is NOT a Summary Flags heading.
    body = _PASS1 + "## Pass 2: trace\n\nThe summary flags are described in Pass 1 above.\n"
    issues, warned = _run(tmp_path, body, caplog)
    assert issues == []
    assert warned, "mid-line prose must not be mistaken for the Summary Flags block"


def test_pass2_shape_variants_detected(tmp_path, caplog):
    # If Pass 2 itself is written in a variant shape WITH a flags block, no warn.
    for blk in ("### Pass 2\n\n### Summary Flags\n- x\n",
                "**Pass 2**\n\n**Summary Flags**\n- y\n"):
        caplog.clear()
        issues, warned = _run(tmp_path, _PASS1 + blk, caplog)
        assert issues == [] and not warned


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
