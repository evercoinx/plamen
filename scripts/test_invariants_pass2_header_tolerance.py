"""invariants_p2 niche-trigger detection regression.

Original bug: _validate_invariants_pass2 checked for a `### Summary Flags`
HEADING via an exact regex, so it warned "missing Summary Flags subblock" on
essentially every Thorough run. Verified against REAL agent output:
  - Run A wrote `### Summary Flags (for Semantic Gap Investigator ...)` (matched)
  - Run B wrote `### Pass 2 Summary Statistics` with the flags INSIDE a ``` fence
    (did NOT match the heading regex, even a tolerant one)
In BOTH the actual flag DATA (sync_gaps / accumulation_exposures /
conditional_writes / cluster_gaps with values) was present — which is what the
SEMANTIC_GAP_INVESTIGATOR trigger actually reads. So the fix detects the flag
DATA, heading- and code-fence-agnostic, and warns ONLY when that data is
genuinely (near-)absent.

FIX #1 (single source of truth): the advisory now gates on the REAL depth
trigger `_semantic_gap_required(scratchpad)` — which counts flag values, table
cells, AND CONFIRMED gap TOKENS — instead of a local ">=2 flags" parser. So it
warns iff the niche genuinely will NOT fire (no POSITIVE trigger). The warning
text changed accordingly ("no semantic-gap flags/tokens ... will not fire");
`_warned_about_niche` matches the new and legacy phrasings.
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


def _run(tmp_path: Path, body: str, caplog):
    (tmp_path / "semantic_invariants.md").write_text(body, encoding="utf-8")
    v = _val()
    with caplog.at_level(logging.WARNING, logger="plamen.validators"):
        issues = v._validate_invariants_pass2(tmp_path, "thorough")
    warned = any(
        # new single-source-of-truth phrasing OR the legacy strings
        ("will not fire" in r.getMessage()
         or "flag data" in r.getMessage()
         or "Summary Flags" in r.getMessage())
        for r in caplog.records
    )
    return issues, warned


_PASS1 = "# Semantic Invariants\n\n## Pass 1\n- inv A\n- inv B\n\n"
_FLAGS = (
    "- sync_gaps: 0\n- accumulation_exposures: 1\n"
    "- conditional_writes: 4\n- cluster_gaps: 2\n"
)

# The flag DATA present under wildly different headings / wrappers -> NO warn.
_FLAGDATA_VARIANTS = [
    # Run B real shape: "Pass 2 Summary Statistics" + flags in a ``` fence
    "### Pass 2 Summary Statistics\n\n```\n" + _FLAGS + "```\n",
    # Run A real shape
    "## Pass 2: trace\n\n### Summary Flags (for niche trigger)\n" + _FLAGS,
    # no flags heading at all, just the data after the Pass 2 header
    "## Pass 2 — Recursive Trace\n\nResults below:\n" + _FLAGS,
    # bold label + '=' form
    "### Pass 2\n\n**Niche flags**\nsync_gaps = 0\naccumulation_exposures = 1\n",
]


def test_flag_data_present_any_shape_does_not_warn(tmp_path, caplog):
    for i, blk in enumerate(_FLAGDATA_VARIANTS):
        caplog.clear()
        issues, warned = _run(tmp_path, _PASS1 + blk, caplog)
        assert issues == [], f"variant {i}: stays soft"
        assert not warned, f"variant {i}: flag DATA present -> must NOT warn: {blk!r}"


def test_genuinely_missing_flag_data_still_warns(tmp_path, caplog):
    body = _PASS1 + "## Pass 2: Recursive Trace\n\n- prose only, no trigger flags\n"
    issues, warned = _run(tmp_path, body, caplog)
    assert issues == []   # still soft
    assert warned         # genuine absence flagged


def test_all_flags_zero_no_positive_trigger_warns(tmp_path, caplog):
    # FIX #1: the ">=2 flags" threshold is gone. A single flag whose VALUE is 0
    # (and no other positive flag/token) means the niche will NOT fire -> warn.
    # NOTE: a single POSITIVE flag (e.g. `sync_gaps: 5`) now correctly does NOT
    # warn (the niche fires) — the old threshold logic warned there wrongly.
    body = _PASS1 + "## Pass 2\n\n- sync_gaps: 0\n"
    issues, warned = _run(tmp_path, body, caplog)
    assert issues == [] and warned


def test_single_positive_flag_does_not_warn(tmp_path, caplog):
    # FIX #1 contract: a single POSITIVE flag means the niche WILL fire, so the
    # advisory must stay silent. The old ">=2 flags" parser warned here wrongly.
    body = _PASS1 + "## Pass 2\n\n- sync_gaps: 5\n"
    v = _val()
    issues, warned = _run(tmp_path, body, caplog)
    assert issues == []
    assert not warned
    assert v._semantic_gap_required(tmp_path) is True


def test_prose_mentioning_flag_words_without_values_not_counted(tmp_path, caplog):
    # The words without "<flag>: <number>" must not be mistaken for the data.
    body = _PASS1 + (
        "## Pass 2\n\nWe considered sync_gaps and conditional_writes but found none.\n"
    )
    issues, warned = _run(tmp_path, body, caplog)
    assert issues == [] and warned, "flag words without values are not the data block"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
