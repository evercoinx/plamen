"""Contract tests for the external-dependency mock-tier fuzz directive.

Background: a prior Thorough audit surfaced a fuzz-COVERAGE
gap, not a correctness bug. The invariant-fuzz / Medusa "build a harness
from scratch" fallback only escalated to the self-contained in-scope
surface (pure libraries / math). When the highest-value fuzzable surface
— the protocol's accounting / escrow / settlement state machine — could
not be deployed standalone because its constructor needed a LIVE external
dependency (an AMM pool manager, oracle, router, vault, bridge), the
phase fell straight back to code-trace. CPMM math got fuzzed (>2.7M
calls) while the money-handling escrow/settlement invariants only got
the weaker code-trace guarantee.

The fix adds a generic "External-dependency mock tier" between
"deploy the in-scope state machine standalone" and the code-trace /
COMPILATION_FAILED fallback in BOTH EVM v2 fuzz prompts: build a MINIMAL
faithful mock of only the interface subset the in-scope contract calls,
so the accounting layer becomes fuzzable. RECALL-SAFETY: if a faithful
minimal mock is not achievable in bounded effort, fall back to code-trace
and record an explicit coverage limitation (no silent cap, no guessed
mock). A mock fidelity receipt names the dependency + mocked methods so a
reviewer can judge fidelity.

This is intentionally PROMPT-ONLY — no post-phase validator gates the
output, matching the soft-directive design of the sibling negative-case
reachability tests. These tests lock in the prompt contract so the
directive doesn't get accidentally deleted in a future edit.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_types import plamen_home  # noqa: E402

ROOT = plamen_home()

FUZZ_PROMPTS = [
    ROOT / "prompts" / "evm" / "v2" / "phase4b-invariant-fuzz.md",
    ROOT / "prompts" / "evm" / "v2" / "phase4b-medusa-fuzz.md",
]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def test_fuzz_prompts_exist():
    for p in FUZZ_PROMPTS:
        assert p.exists(), f"Fuzz prompt missing: {p}"


def test_fuzz_prompts_have_mock_tier_heading_or_keyword():
    """Both prompts must name the external-dependency mock tier so the
    agent attempts it before code-trace fallback."""
    required_phrases = [
        # The tier name / forcing keyword
        "mock tier",
        # The motivating dependency classes (live external dependency)
        "external dependency",
    ]
    for p in FUZZ_PROMPTS:
        text = _read(p).lower()
        for phrase in required_phrases:
            assert phrase in text, (
                f"{p.name}: missing required phrase '{phrase}' in the "
                "external-dependency mock-tier directive"
            )


def test_fuzz_prompts_require_interface_subset_only():
    """The mock must cover ONLY the interface subset the in-scope contract
    actually calls — not the full external interface. This bounds effort
    and is what keeps the mock minimal."""
    for p in FUZZ_PROMPTS:
        text = _read(p).lower()
        assert "interface subset" in text, (
            f"{p.name}: must instruct the agent to mock only the "
            "interface subset the in-scope contract actually calls"
        )


def test_fuzz_prompts_have_faithful_or_codetrace_recall_rule():
    """RECALL-SAFETY: a wrong mock yields false PASS/FAIL. The directive
    must require a FAITHFUL mock and, when one is not achievable in
    bounded effort, fall back to code-trace as an explicit coverage
    limitation — never ship a guessed mock, never silently cap."""
    for p in FUZZ_PROMPTS:
        text = _read(p)
        low = text.lower()
        assert "faithful" in low, (
            f"{p.name}: mock-tier directive must require a FAITHFUL mock"
        )
        assert "code-trace" in low or "[CODE-TRACE]" in text, (
            f"{p.name}: must require code-trace fallback when a faithful "
            "minimal mock is not achievable"
        )
        assert "coverage limitation" in low or "no silent cap" in low, (
            f"{p.name}: must record the fallback as an explicit coverage "
            "limitation (no silent cap)"
        )


def test_fuzz_prompts_require_mock_fidelity_receipt():
    """A reviewer must be able to judge mock fidelity — the directive
    requires a receipt line naming which dependency + which methods were
    mocked (or why no mock was shipped)."""
    for p in FUZZ_PROMPTS:
        low = _read(p).lower()
        assert "mock fidelity" in low, (
            f"{p.name}: must require a 'mock fidelity' receipt line "
            "naming the mocked dependency and methods"
        )


def test_fuzz_prompts_dont_introduce_hard_gate():
    """Sanity: the mock-tier directive is prompt-only and must NOT abort
    the phase or emit a driver-pickup halt marker."""
    forbidden_phrases = [
        "REFUSE TO PROCEED",
        "ABORT THE PHASE",
        "EMIT HALT_REQUESTED",
        "[HALT]",
    ]
    for p in FUZZ_PROMPTS:
        text = _read(p)
        for phrase in forbidden_phrases:
            assert phrase not in text, (
                f"{p.name}: contains '{phrase}' which would convert the "
                "soft mock-tier directive into a hard gate"
            )
