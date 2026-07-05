"""Recall fix #1: R13 anti-normalization + divergence-promotion must be injected
into the SC breadth worker prompt (and NOT the L1 one, which carries its own).
Regression guard for the gap where a real divergence is observed in a self-check
matrix row and closed with "intended" — because R13 never reached the breadth
worker, that divergence never became a finding.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import plamen_driver as d


def _build(pipeline: str) -> str:
    job = {"output": "analysis_core_state.md", "agent_id": "b1",
           "focus_area": "core_state"}
    return d._build_breadth_worker_prompt(
        job=job, scratchpad=Path(tempfile.mkdtemp()), project_root="/x",
        config={"pipeline": pipeline, "language": "evm", "mode": "thorough"},
        attempt=1,
    )


def test_sc_breadth_prompt_carries_r13_and_promotion_mandate():
    p = _build("sc")
    flat = " ".join(p.split())  # normalize line-wraps before phrase checks
    assert "Anti-Normalization & Divergence Promotion" in flat
    assert "divergence left in a matrix cell is a recall failure" in flat
    assert "5-Question Test" in flat
    # the promotion mandate specifically (the load-bearing line)
    assert "MUST be promoted to a `## Finding [` block" in flat


def test_l1_breadth_prompt_does_not_inject_sc_r13_block():
    p = _build("l1")
    assert "Anti-Normalization & Divergence Promotion" not in p


def test_directive_is_condensed_not_the_full_rule_block():
    # bloat guard: the injected directive stays compact (the 5-Q core + mandate),
    # not the full 38-line rule + passive-attack table.
    block = d._BREADTH_ANTI_NORMALIZATION_DIRECTIVE
    assert block.count("\n") < 30, "directive grew too large — attention bloat risk"
    assert "PASSIVE" in block  # the one passive-attack line is retained
