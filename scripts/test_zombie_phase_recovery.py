"""FIX #5: zombie-phase early-complete must rescue idle workers whose gate
already passes, WITHOUT ever early-completing a scaffold/placeholder phase.

The motivating bug: `chain_agent2` wrote all three of its expected artifacts
(gate passed) then idled emitting spinner churn — the supervised wait loop
blocked the full ~90-min LOC-scaled timeout. The recovery (in `_pty_poll`)
completes such a phase early. The load-bearing correctness is the
scaffold-exclusion decision: it MUST treat chain_agent2 / chain_iter2 as
recoverable while EXCLUDING the phases whose gate passes on a driver-written
scaffold (sc_semantic_dedup / semantic_dedup PASSTHROUGH, chain Agent 1
MECHANICAL_BASELINE, report-tier placeholder pre-writes, append-only phases).
"""
from __future__ import annotations

import importlib
import os
import sys


def _drv():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_driver")


class _P:
    def __init__(self, name: str, appends: bool = False):
        self.name = name
        self.appends_existing_artifact = appends


def test_chain_agent2_is_recoverable():
    d = _drv()
    # The exact phase that hung — NOT a scaffold, so it MUST be eligible.
    assert d._zombie_phase_is_scaffold_excluded(_P("chain_agent2")) is False
    assert d._zombie_phase_is_scaffold_excluded(_P("chain_iter2")) is False


def test_chain_agent1_is_excluded():
    d = _drv()
    # chain Agent 1 passes its gate on the MECHANICAL_BASELINE scaffold.
    assert d._zombie_phase_is_scaffold_excluded(_P("chain")) is True


def test_semantic_dedup_excluded_both_pipelines():
    d = _drv()
    assert d._zombie_phase_is_scaffold_excluded(_P("sc_semantic_dedup")) is True
    assert d._zombie_phase_is_scaffold_excluded(_P("semantic_dedup")) is True


def test_report_tier_placeholders_excluded():
    d = _drv()
    for name in (
        "report_critical_high",
        "report_critical_high_merge",
        "report_medium",
        "report_medium_merge",
        "report_low_info",
        "report_low_info_merge",
    ):
        assert d._zombie_phase_is_scaffold_excluded(_P(name)) is True, name


def test_append_only_phase_excluded():
    d = _drv()
    # appends_existing_artifact=True (e.g. invariants_p2) → gate passes on the
    # prior artifact → must not early-complete.
    assert d._zombie_phase_is_scaffold_excluded(
        _P("invariants_p2", appends=True)
    ) is True


def test_normal_phases_recoverable():
    d = _drv()
    for name in ("inventory", "verify_core", "skeptic", "crossbatch",
                 "report_index"):
        assert d._zombie_phase_is_scaffold_excluded(_P(name)) is False, name
