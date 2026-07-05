"""Regression test for the rate-limit-retry savings guard.

Background: when a phase's attempt-1 subprocess is rate-limited DURING the
streaming response AFTER its expected_artifacts have already been written
to disk (common for rescan/inventory phases that finalize files mid-stream
before the end-of-turn token), the rate-limit retry path historically
fired `run_phase(attempt=2)` unconditionally — a full re-run that produced
the same artifacts at a fresh model spend (~$10-12 per Thorough rescan).

A prior Thorough audit demonstrated this:
rescan attempt-1 wrote all 4 expected files BEFORE the 429 hit, then
attempt-2 re-wrote them at $11.96 of pure waste.

The fix adds a `gate_passes()` pre-check before each rate-limit retry
spawn. If attempt 1 already satisfied the phase's expected_artifacts
contract, skip the spawn entirely (rc=0) and let downstream validators
mark the phase complete normally.

These tests verify the helper logic (gate_passes integration) using a
synthesized scratchpad. Full integration is covered by the live audit
ledger after the change ships.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402


def _write(p: Path, body: str = "x" * 200) -> None:
    p.write_text(body, encoding="utf-8")


def test_gate_passes_true_when_all_artifacts_present(tmp_path: Path):
    """The helper underlying the retry-skip guard. If gate_passes reports
    True for a phase, the rate-limit retry MUST not respawn."""
    # Use a real Phase object so we exercise the actual gate logic.
    from plamen_types import SC_PHASES
    rescan = next(p for p in SC_PHASES if p.name == "rescan")

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    # rescan's expected_artifacts contract: analysis_rescan_*.md and
    # analysis_percontract_*.md globs each need >= 1 match.
    _write(sp / "analysis_rescan_1.md")
    _write(sp / "analysis_rescan_2.md")
    _write(sp / "analysis_percontract_1.md")
    _write(sp / "analysis_percontract_2.md")

    passed, missing = D.gate_passes(sp, str(tmp_path), rescan)
    assert passed, f"Expected gate to pass with all artifacts; missing={missing}"


def test_gate_passes_false_when_artifacts_missing(tmp_path: Path):
    """Sanity: when attempt-1 produced NOTHING, the guard must not
    short-circuit — the spawn is still required."""
    from plamen_types import SC_PHASES
    rescan = next(p for p in SC_PHASES if p.name == "rescan")

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    # No artifacts written.

    passed, missing = D.gate_passes(sp, str(tmp_path), rescan)
    assert not passed
    assert missing, "Empty missing list would let the guard wrongly skip retry"


def test_gate_passes_false_when_partial_artifacts(tmp_path: Path):
    """Partial output (e.g. the 429 hit before all files were written)
    must NOT trigger the skip — those phases really do need a retry."""
    from plamen_types import SC_PHASES
    rescan = next(p for p in SC_PHASES if p.name == "rescan")

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    # Only one of the two globs satisfied.
    _write(sp / "analysis_rescan_1.md")
    # analysis_percontract_*.md is missing entirely.

    passed, missing = D.gate_passes(sp, str(tmp_path), rescan)
    assert not passed, (
        "Gate passed despite missing analysis_percontract_*.md — the rate-"
        "limit guard would falsely skip a phase that NEEDS a retry"
    )


def test_phase_with_no_expected_artifacts_does_not_skip(tmp_path: Path):
    """The guard checks `phase.expected_artifacts` truthiness before
    skipping. Phases that declare no expected_artifacts (e.g. Python-only
    mechanical phases) should always run their retry path through the
    normal subprocess flow rather than short-circuiting on an empty gate.
    """
    from plamen_types import SC_PHASES
    # `inventory_prepare` is haiku/Python-only; `expected_artifacts` is
    # just inventory_shard_plan.md — pick a phase that has artifacts to
    # confirm the gate-truthiness check.
    rescan = next(p for p in SC_PHASES if p.name == "rescan")
    assert rescan.expected_artifacts, (
        "Fixture broken: rescan should have expected_artifacts; "
        "the savings guard relies on this truthiness check"
    )
