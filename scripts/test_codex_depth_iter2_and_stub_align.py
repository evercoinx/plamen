"""Codex L1 Thorough depth degraded on the confidence-stub gate: gpt-5.x
stamped 0.74/0.81 for all 22 findings, and the Codex fan-out never ran the
Devil's-Advocate iter2 pass. Two interacting bugs:

PART 1 — stub-detector disagreement. The synthesis check that decides whether
to mechanically RECOMPUTE confidence (_depth_artifact_is_stub) only flagged the
all-identical case (len(set)==1). The depth GATE
(_validate_confidence_scores_quality) degrades on <=2 distinct composites among
>=8 findings. A 2-distinct formulaic stub therefore PASSED the synthesis check
(never recomputed) yet FAILED the gate -> guaranteed degrade. Fix: align the
synthesis check with the gate definition so the stub is recomputed first.

PART 2 — DA iter2 never scheduled on Codex. _depth_da_job_if_required reads
confidence_scores.md, but the Codex fan-out built its job list BEFORE confidence
existed, so the iter2 job was never added. Fix: the fan-out re-evaluates the DA
job AFTER lifecycle synth finalizes confidence (mirrors the Claude PTY pool).
This test pins PART 1 directly (pure function) and asserts the PART 2 wiring
calls _depth_da_job_if_required after synth.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _val():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_validators")


def _conf_table(values):
    rows = "\n".join(
        f"| INV-{i:03d} | 0.90 | 0.80 | 0.70 | 0.30 | {v:.2f} | CONFIDENT |"
        for i, v in enumerate(values, 1)
    )
    return (
        "# Confidence Scores\n\n"
        "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Class |\n"
        "|---|---|---|---|---|---|---|\n" + rows + "\n"
    )


# ── PART 1: stub-detector alignment ──────────────────────────────────────────

def test_two_distinct_composites_among_many_is_stub(tmp_path):
    v = _val()
    # The live Codex signature: 22 findings, only 0.74 and 0.81.
    vals = [0.81] * 17 + [0.74] * 5
    (tmp_path / "confidence_scores.md").write_text(_conf_table(vals),
                                                   encoding="utf-8")
    reason = v._depth_artifact_is_stub(tmp_path / "confidence_scores.md")
    assert reason is not None, (
        "2-distinct-composite-across-22-findings must be detected as a stub so "
        "the lifecycle synth recomputes real scores (it previously passed)"
    )
    assert "distinct" in reason or "formulaic" in reason


def test_all_identical_still_stub(tmp_path):
    v = _val()
    (tmp_path / "confidence_scores.md").write_text(
        _conf_table([0.80] * 10), encoding="utf-8")
    assert v._depth_artifact_is_stub(tmp_path / "confidence_scores.md")


def test_differentiated_table_not_stub(tmp_path):
    v = _val()
    # A genuinely per-finding-scored table (>2 distinct values) must NOT be
    # flagged -- false rejection would create a new halt.
    vals = [0.35, 0.42, 0.55, 0.61, 0.68, 0.72, 0.78, 0.83, 0.90, 0.47]
    (tmp_path / "confidence_scores.md").write_text(_conf_table(vals),
                                                   encoding="utf-8")
    assert v._depth_artifact_is_stub(tmp_path / "confidence_scores.md") is None


def test_gate_and_synth_now_agree_on_the_live_case(tmp_path):
    # The whole point: gate and synth must agree on the 0.74/0.81 table.
    v = _val()
    vals = [0.81] * 17 + [0.74] * 5
    (tmp_path / "confidence_scores.md").write_text(_conf_table(vals),
                                                   encoding="utf-8")
    gate = v._validate_confidence_scores_quality(tmp_path, "thorough")
    synth = v._depth_artifact_is_stub(tmp_path / "confidence_scores.md")
    assert gate and synth, (
        "gate flags it (degrade) AND synth flags it (recompute) -> aligned; "
        "previously only the gate flagged it, causing an unrecoverable degrade"
    )


# ── PART 2: Codex fan-out re-evaluates the DA iter2 job after synth ──────────

def test_fanout_reevaluates_da_job_after_confidence():
    # Source-level wiring assertion: the codex fan-out must call
    # _depth_da_job_if_required AFTER _synthesize_depth_lifecycle_artifacts so
    # iter2 is scheduled against REAL (post-synth) confidence, not the empty
    # pre-run state.
    import re
    src = Path(__file__).with_name("plamen_driver.py").read_text(
        encoding="utf-8")
    m = re.search(
        r"def _run_depth_codex_fanout\(.*?\n(?=\ndef )", src, re.S)
    assert m, "fan-out function not found"
    body = m.group(0)
    synth_idx = body.find("_synthesize_depth_lifecycle_artifacts")
    da_idx = body.find("_depth_da_job_if_required")
    assert synth_idx != -1 and da_idx != -1, (
        "fan-out must both synth confidence and schedule the DA job"
    )
    assert da_idx > synth_idx, (
        "DA iter2 scheduling must come AFTER confidence synth (the ordering "
        "bug that left iter2 unscheduled on Codex)"
    )


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
