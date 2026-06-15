"""L1 resume + 529-overload robustness (2026-06-02).

Two compounding bugs on the live L1 Thorough (Irys) run that loop at depth:

BUG 1 — 529/overloaded mis-handled as a 429 usage-cap rate-limit. The depth
phase repeatedly gets Anthropic '529 overloaded_error' ("Overloaded")
responses. These are TRANSIENT PROVIDER overload, NOT the user's rate/usage
cap. Old code treated 529 identically to 429: ~1 auto-wait then surface the
full usage-cap pause panel and EXIT.
  DESIRED: distinguish 529/overloaded from 429/usage-cap. 529 -> short
  exponential backoff (30s/60s/120s/180s cap ~3min) for >=4 retries BEFORE any
  pause, labeled "Anthropic temporarily overloaded". Only if 529 persists past
  the extra retries fall back to the EXISTING pause-for-resume (haltless).
  429/usage-cap behavior UNCHANGED.

BUG 2 — resume reconciliation false-rewind. `location_recovery.md` may be a
valid ~75-byte skip-summary ('SKIPPED: all inventory locations resolve
mechanically') written by `_write_location_recovery_skip` when location
recovery is not needed. That is below min_artifact_bytes (100) so gate_passes
flagged it '(stub only)' -> rewind of location_recovery + downstream
invariants/invariants_p2 on EVERY resume.
  DESIRED: gate_passes treats the valid skip-summary as PASSING; genuinely
  missing/empty/other-broken files still rewind.
"""
from __future__ import annotations

from pathlib import Path

import plamen_driver as D
import plamen_validators as V
import pty_exec as PX
from plamen_mechanical import _write_location_recovery_skip
from plamen_types import Phase


# ── shared fixtures ─────────────────────────────────────────────────────────

def _location_recovery_phase() -> Phase:
    """Mirror the canonical SC/L1 location_recovery phase contract."""
    return Phase(
        "location_recovery",
        ["Step 4a: Finding Inventory"],
        ["location_recovery.md"],
        base_timeout_s=900,
        critical=True,
        model="sonnet",
        modes={"thorough"},
    )


def _overloaded_stdio(scratch: Path, phase_name: str = "depth") -> Path:
    """Write a stdio log carrying a 529 overloaded_error envelope/event."""
    log = scratch / f"_stdio_{phase_name}.log"
    log.write_text(
        '{"type":"assistant","message":{"content":[]}}\n'
        '{"type":"error","api_error_status":529,'
        '"error":{"type":"overloaded_error","message":"Overloaded"}}\n'
        '{"is_error":true,"api_error_status":529,'
        '"error":{"type":"overloaded_error","message":"Overloaded"},'
        '"result":"Overloaded"}\n',
        encoding="utf-8",
    )
    return log


def _usage_cap_stdio(scratch: Path, phase_name: str = "depth") -> Path:
    """Write a stdio log carrying a genuine 429 usage-cap rate-limit."""
    log = scratch / f"_stdio_{phase_name}.log"
    log.write_text(
        '{"type":"assistant","message":{"content":[]}}\n'
        '{"is_error":true,"api_error_status":429,'
        '"error":{"type":"rate_limit_error",'
        '"message":"You have hit your weekly limit. Switch to Team plan."},'
        '"result":"rate_limit_error"}\n',
        encoding="utf-8",
    )
    return log


# ── BUG 1 (a): 529 classifies as overloaded, NOT usage-cap ──────────────────

def test_529_classified_as_overloaded(tmp_path: Path):
    log = _overloaded_stdio(tmp_path)
    assert D.detect_overloaded(log) is True, "529 must be detected as overload"
    # It is still 'rate-limited' in the broad sense (so existing detection
    # paths see it), but specifically overloaded.
    assert D.detect_rate_limit(log) is True


def test_429_not_classified_as_overloaded(tmp_path: Path):
    log = _usage_cap_stdio(tmp_path)
    assert D.detect_overloaded(log) is False, (
        "a genuine 429 usage-cap must NOT be classified as 529 overload"
    )
    assert D.detect_rate_limit(log) is True, "429 is still a rate limit"


def test_event_and_text_overload_helpers():
    assert PX.event_is_overloaded(
        {"api_error_status": 529, "error": {"type": "overloaded_error"}}
    ) is True
    assert PX.event_is_overloaded(
        {"type": "error", "error": {"type": "overloaded_error"}}
    ) is True
    assert PX.event_is_overloaded({"stop_reason": "overloaded"}) is True
    # 429 usage-cap is NOT overloaded
    assert PX.event_is_overloaded(
        {"api_error_status": 429, "error": {"type": "rate_limit_error"}}
    ) is False
    # user/tool-result prose is never trusted
    assert PX.event_is_overloaded(
        {"type": "user", "message": {"content": "529 overloaded"}}
    ) is False
    assert PX.text_shows_overloaded('"type":"overloaded_error"') is True
    assert PX.text_shows_overloaded("api error status 529 overloaded") is True
    assert PX.text_shows_overloaded("you have hit your weekly limit") is False


# ── BUG 1 (b): 529 gets >=4 short-backoff retries before a pause ────────────

def test_overload_backoff_plan_gives_at_least_4_retries():
    plan = D.overload_backoff_plan
    waits = []
    attempts = 0
    while True:
        should, wait = plan(attempts)
        if not should:
            break
        waits.append(wait)
        attempts += 1
    assert len(waits) >= 4, (
        f"529 overload must get >=4 short-backoff retries before pause, "
        f"got {len(waits)}: {waits}"
    )
    # Short exponential backoff, capped ~3 min.
    assert waits[0] <= 60, "first backoff is short"
    assert max(waits) <= 180, f"backoff capped ~3min, got {waits}"
    assert waits == sorted(waits), "backoff is non-decreasing (exponential-ish)"


def test_overload_backoff_plan_exhaustion_falls_back():
    # past the budget -> (False, 0): caller must fall back to pause-for-resume.
    should, wait = D.overload_backoff_plan(99)
    assert should is False and wait == 0, (
        "after the overload budget is exhausted, the plan must signal "
        "fall-through to the existing pause path (haltless floor)"
    )


def test_overload_sleep_respects_test_override(monkeypatch):
    monkeypatch.setenv("PLAMEN_OVERLOAD_BACKOFF_TEST_S", "0")
    assert D._overload_sleep_seconds(180) == 0, (
        "tests must be able to force a 0-length backoff sleep"
    )
    monkeypatch.delenv("PLAMEN_OVERLOAD_BACKOFF_TEST_S", raising=False)
    assert D._overload_sleep_seconds(120) == 120


# ── BUG 1 (c): a genuine 429 still triggers the EXISTING pause behavior ─────

def test_429_does_not_enter_overload_path(tmp_path: Path):
    """A 429 must NOT be eligible for the 529 short-backoff path.

    The driver guards the overload pre-check with `detect_overloaded(...)`.
    For a genuine 429 that returns False, so the existing usage-cap path
    (long wait + pause panel) runs unchanged.
    """
    log = _usage_cap_stdio(tmp_path)
    # gate: overload path only entered when detect_overloaded is True.
    assert D.detect_overloaded(log) is False
    # And it is still recognized as a rate limit (existing 429 behavior intact).
    assert D.detect_rate_limit(log) is True


# ── BUG 2 (d): valid skip-summary PASSES the reconciliation gate ────────────

def test_location_recovery_skip_summary_passes_gate(tmp_path: Path):
    _write_location_recovery_skip(
        tmp_path, "all inventory locations resolve mechanically"
    )
    f = tmp_path / "location_recovery.md"
    assert f.stat().st_size < 100, (
        "precondition: the skip-summary is below min_artifact_bytes (the bug)"
    )
    passed, missing = V.gate_passes(
        tmp_path, str(tmp_path), _location_recovery_phase()
    )
    assert passed is True, (
        f"valid location_recovery skip-summary must PASS the gate, "
        f"got missing={missing}"
    )
    assert missing == []


def test_location_recovery_skip_summary_passes_via_driver_alias(tmp_path: Path):
    # D.gate_passes is the same function the resume reconciliation uses.
    _write_location_recovery_skip(
        tmp_path, "all inventory locations resolve mechanically"
    )
    passed, missing = D.gate_passes(
        tmp_path, str(tmp_path), _location_recovery_phase()
    )
    assert passed is True, f"driver gate must also pass; missing={missing}"


# ── BUG 2 (e): genuinely missing/empty location_recovery still rewinds ──────

def test_location_recovery_missing_still_fails(tmp_path: Path):
    # no file written at all
    passed, missing = V.gate_passes(
        tmp_path, str(tmp_path), _location_recovery_phase()
    )
    assert passed is False, "a missing location_recovery.md must still fail"
    assert any("location_recovery.md" in m for m in missing)


def test_location_recovery_empty_still_fails(tmp_path: Path):
    # tiny file WITHOUT the skip-summary markers -> a genuine stub.
    (tmp_path / "location_recovery.md").write_text("x", encoding="utf-8")
    passed, missing = V.gate_passes(
        tmp_path, str(tmp_path), _location_recovery_phase()
    )
    assert passed is False, "an empty/garbage stub must still fail (rewind)"
    assert any("stub only" in m for m in missing)


def test_location_recovery_partial_marker_still_fails(tmp_path: Path):
    # has SKIPPED but NOT 'resolve mechanically' and is below min bytes.
    (tmp_path / "location_recovery.md").write_text(
        "# Location Recovery\n\nSKIPPED\n", encoding="utf-8"
    )
    passed, missing = V.gate_passes(
        tmp_path, str(tmp_path), _location_recovery_phase()
    )
    assert passed is False, (
        "a partial/ambiguous skip marker below min bytes must still fail"
    )


def test_location_recovery_substantial_real_content_passes(tmp_path: Path):
    # A real (non-skip) location_recovery file with substantial content still
    # passes on size, unaffected by the skip-summary special case.
    (tmp_path / "location_recovery.md").write_text(
        "# Location Recovery\n\n" + ("| F-1 | src/x.rs:10 | RECOVERED |\n" * 20),
        encoding="utf-8",
    )
    passed, missing = V.gate_passes(
        tmp_path, str(tmp_path), _location_recovery_phase()
    )
    assert passed is True, f"substantial real content must pass; missing={missing}"


# ── BUG 2 (f) regression: other genuinely-broken artifacts still rewind ─────

def test_other_phase_stub_still_fails(tmp_path: Path):
    """The skip-summary exemption is location_recovery-only.

    A different single-file phase with a tiny stub must STILL fail '(stub
    only)' even if its content happens to contain the skip phrases — the
    exemption is keyed on phase.name == 'location_recovery'.
    """
    phase = Phase(
        "invariants",
        ["Step 4a.5"],
        ["semantic_invariants.md"],
        base_timeout_s=900,
        model="sonnet",
        modes={"thorough"},
    )
    # even with the skip phrases, a non-location_recovery phase must fail.
    (tmp_path / "semantic_invariants.md").write_text(
        "SKIPPED resolve mechanically\n", encoding="utf-8"
    )
    passed, missing = V.gate_passes(tmp_path, str(tmp_path), phase)
    assert passed is False, (
        "the skip-summary exemption must NOT leak to other phases"
    )
    assert any("stub only" in m for m in missing)
