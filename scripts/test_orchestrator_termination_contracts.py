"""Lock-in tests for orchestrator HARD STOP termination contracts.

Background: an observed audit's breadth phase took 1h06m wall-time
even though the last legitimate output was written at 30:04. The LLM
orchestrator inside the breadth subprocess produced its 6 required
analysis files in the first 30 minutes, then kept running for another
36 minutes producing future-phase artifacts (e.g. `analysis_rescan_2.md`)
that the driver's phase-isolation layer quarantined and discarded.

Root cause: the breadth + rescan prompts had no explicit termination
contract for the ORCHESTRATOR agent (only for sub-agents). With no
"return immediately when done" signal, the LLM stayed helpful and
started downstream-phase work. The driver waited for natural subprocess
exit — token waste, wall-time waste, session-quota waste.

The fix adds an explicit HARD STOP "Orchestrator Termination Contract"
section to both prompts: enumerates the prohibitions (do NOT write
later-phase artifacts) and gives a literal return signal format.

These tests guarantee both contracts stay in place — if a future edit
removes them, the audit will regress to the same wall-time bloat.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_prompt import _STANDALONE_PROMPT_MAP, _STANDALONE_V2_DIR  # noqa: E402


def _read(phase: str) -> str:
    name = _STANDALONE_PROMPT_MAP.get(phase)
    assert name, f"phase {phase!r} not mapped in _STANDALONE_PROMPT_MAP"
    return (_STANDALONE_V2_DIR / name).read_text(encoding="utf-8")


def _assert_termination_contract(phase: str, must_contain: tuple[str, ...]):
    text = _read(phase)
    assert "Orchestrator Termination Contract" in text, (
        f"{phase}: 'Orchestrator Termination Contract' section heading "
        "missing. This is the load-bearing directive that prevents the "
        "observed-class wall-time bloat (LLM keeps running after its work is done)."
    )
    assert "HARD STOP" in text, (
        f"{phase}: 'HARD STOP' emphasis missing — without it the LLM "
        "treats the contract as advisory."
    )
    assert "return immediately" in text.lower(), (
        f"{phase}: 'return immediately' phrasing missing from termination contract."
    )
    for tok in must_contain:
        assert tok in text, (
            f"{phase}: required prohibition/return-signal token {tok!r} missing."
        )


def test_breadth_orchestrator_termination_contract():
    _assert_termination_contract(
        "breadth",
        must_contain=(
            "manifest-derived breadth set",
            "Quarantined overflow",
            "DONE:",
        ),
    )


def test_rescan_orchestrator_termination_contract():
    _assert_termination_contract(
        "rescan",
        must_contain=(
            "files outside the two artifact families",
            "DONE:",
        ),
    )


def test_breadth_prompt_says_extra_work_is_wasted():
    """The user-facing rationale (cost discipline) needs to be visible
    so an LLM reading the prompt understands WHY it should stop. Vague
    'return when done' historically didn't trigger termination."""
    text = _read("breadth")
    # Either "wasted" or "discarded" or "thrown away" — any clear waste signal
    waste_signal = any(
        kw in text.lower()
        for kw in ("wasted", "discarded", "thrown away", "quarantined")
    )
    assert waste_signal, "Breadth prompt must explain that post-completion work is wasted/discarded"


def test_rescan_prompt_says_extra_work_is_wasted():
    text = _read("rescan")
    waste_signal = any(
        kw in text.lower()
        for kw in ("wasted", "discarded", "thrown away", "quarantined")
    )
    assert waste_signal, "Rescan prompt must explain that post-completion work is wasted/discarded"


def test_breadth_subagent_scope_directive_preserved():
    """The pre-existing sub-agent scope-containment directive (SCOPE:
    Write ONLY to your assigned output file) must remain. The
    termination contract added is for the ORCHESTRATOR; this is for the
    spawned sub-agents."""
    text = _read("breadth")
    assert "SCOPE:" in text and "Return your findings and stop" in text, (
        "Pre-existing sub-agent scope directive was accidentally removed "
        "when adding the orchestrator termination contract."
    )
