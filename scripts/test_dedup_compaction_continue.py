"""Regression: the semantic-dedup phase must spend its in-session continuation
budget (the compaction-continue path) when the LLM subprocess compacted
mid-write and left the driver's pre-written PASSTHROUGH scaffold unchanged
while live candidate pairs remain — exactly the behavior breadth/depth get.

Before the fix, the generic artifact gate passed on the crash-safety scaffold
from turn 1, so `run_phase` returned 0 immediately and the continuation budget
was never used → the phase "passed" without ever deduping.

The guard is `_dedup_passthrough_should_continue`. It must:
  - fire ONLY for the semantic-dedup phase names,
  - fire ONLY while budget remains (attempt <= budget),
  - fire ONLY when dedup_decisions.md is PASSTHROUGH AND candidate pairs exist,
  - and (critical=True safety) STOP firing once the budget is spent, so the
    caller accepts the passthrough floor (return 0) instead of escalating to a
    whole-phase-retry sentinel that would cascade to a critical halt.
"""
from pathlib import Path

import plamen_driver as D


def _write_passthrough(scratchpad: Path):
    (scratchpad / "dedup_decisions.md").write_text(
        "# Dedup Decisions\n\n**Status**: PASSTHROUGH\n\n"
        "No decisions evaluated yet.\n",
        encoding="utf-8",
    )


def _write_live_pairs(scratchpad: Path):
    (scratchpad / "dedup_candidate_pairs.md").write_text(
        "# Candidate Pairs\n\n"
        "| Finding A | Finding B | Signal |\n"
        "| --- | --- | --- |\n"
        "| INV-1 | INV-2 | location overlap |\n"
        "| INV-3 | INV-4 | shared function |\n",
        encoding="utf-8",
    )


def test_fires_for_dedup_passthrough_with_live_pairs(tmp_path: Path):
    _write_passthrough(tmp_path)
    _write_live_pairs(tmp_path)
    # Budget remains -> should continue (drive a fresh missing-only respawn).
    assert D._dedup_passthrough_should_continue(
        "sc_semantic_dedup", attempt=1, budget=3, scratchpad=tmp_path
    )
    assert D._dedup_passthrough_should_continue(
        "semantic_dedup", attempt=3, budget=3, scratchpad=tmp_path
    )


def test_does_not_fire_once_budget_spent(tmp_path: Path):
    # critical=True safety: once budget is exhausted the guard must STOP so the
    # caller accepts the passthrough floor (return 0), never -2 -> no halt.
    _write_passthrough(tmp_path)
    _write_live_pairs(tmp_path)
    assert not D._dedup_passthrough_should_continue(
        "sc_semantic_dedup", attempt=4, budget=3, scratchpad=tmp_path
    )


def test_does_not_fire_when_decisions_are_real(tmp_path: Path):
    # LLM actually overwrote the scaffold -> no PASSTHROUGH -> accept the gate.
    (tmp_path / "dedup_decisions.md").write_text(
        "# Dedup Decisions\n\n**Status**: COMPLETE\n\n"
        "| Pair | Decision |\n| --- | --- |\n| INV-1+INV-2 | MERGE |\n",
        encoding="utf-8",
    )
    _write_live_pairs(tmp_path)
    assert not D._dedup_passthrough_should_continue(
        "sc_semantic_dedup", attempt=1, budget=3, scratchpad=tmp_path
    )


def test_does_not_fire_when_no_live_pairs(tmp_path: Path):
    # Passthrough is legitimate when there are no candidate pairs to evaluate.
    _write_passthrough(tmp_path)
    (tmp_path / "dedup_candidate_pairs.md").write_text(
        "# Candidate Pairs\n\n| Finding A | Finding B | Signal |\n"
        "| --- | --- | --- |\n",
        encoding="utf-8",
    )
    assert not D._dedup_passthrough_should_continue(
        "sc_semantic_dedup", attempt=1, budget=3, scratchpad=tmp_path
    )


def test_never_fires_for_non_dedup_phases(tmp_path: Path):
    _write_passthrough(tmp_path)
    _write_live_pairs(tmp_path)
    for phase_name in ("breadth", "depth", "chain", "report_index", "verify"):
        assert not D._dedup_passthrough_should_continue(
            phase_name, attempt=1, budget=3, scratchpad=tmp_path
        )


if __name__ == "__main__":
    import pytest
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
