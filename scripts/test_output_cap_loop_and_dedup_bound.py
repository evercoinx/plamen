"""Two regressions from a live L1 semantic_dedup hang (2026-06).

1. Dedup overflow: the live cap was raised to 250 (split into 80-pair "rounds"),
   but semantic_dedup runs as ONE subprocess in ONE turn and the multi-round
   prompt still asks it to evaluate every round -> ~240 pairs of focus-inventory
   input + per-pair decision output blew the 32K output-token cap AND saturated
   context (0 decisions, 32-min grind). The live cap MUST stay inside one turn.

2. Cap-LOOP detection: the PTY wait loop already flips ``output_truncated`` when
   the output cap is followed by quiescence. But a turn can AUTO-CONTINUE past
   the cap and hit it again (the live dedup logged the cap error twice in a
   single 32-min turn); events keep flowing so the quiescence gate never trips.
   A sustained cap episode must be treated as terminal so recovery takes over,
   WITHOUT cutting off a turn that scrolls past one cap and recovers.
"""
import re
import time
from pathlib import Path


# ── Fix 1: dedup live cap is turn-safe and forces a single round ──────────────

def test_dedup_live_cap_is_turn_safe():
    import plamen_parsers as p
    # The long-proven-safe single-turn value was 24; the overflow point was ~240
    # (3x80). The default MUST sit comfortably between (turn-safe), not back at
    # the 250 regression.
    assert p._DEDUP_LIVE_PAIR_CAP_DEFAULT <= 60, (
        "dedup live cap must stay inside one turn's input+output budget; "
        f"got {p._DEDUP_LIVE_PAIR_CAP_DEFAULT} (250 hung the live pipeline)"
    )
    # chunk == cap guarantees len(live_pairs) <= _DEDUP_ROUND_CHUNK is always
    # true -> exactly ONE round reaches the single subprocess (no
    # multi-round-in-one-turn overflow).
    assert p._DEDUP_ROUND_CHUNK >= p._DEDUP_LIVE_PAIR_CAP_DEFAULT, (
        "round chunk must be >= cap so the live set is never split into "
        "multiple rounds fed to one turn"
    )


# ── Fix 2: cap-LOOP detection (logic-level reproduction of the wait gate) ──────

# Re-import the real regex + window so the test tracks the source of truth.
def _gate():
    import pty_exec as px
    return px._OUTPUT_CAP_TEXT_RE, px._OUTPUT_CAP_LOOP_S


def _simulate_wait(events, quiescence_s=8.0):
    """Mirror the cap-detection branch of wait_for_turn_complete.

    events: list of (t, recent_output_text, last_event_time) tuples sampled at
    successive polls. Returns the t at which output_truncated would be set, or
    None if it never trips.
    """
    cap_re, loop_s = _gate()
    first_output_cap_seen_at = None
    for (now, recent, last_event_time) in events:
        if cap_re.search(recent):
            if first_output_cap_seen_at is None:
                first_output_cap_seen_at = now
            if (last_event_time is not None
                    and now - last_event_time >= quiescence_s):
                return now  # quiescence path
            if now - first_output_cap_seen_at >= loop_s:
                return now  # cap-loop path
        else:
            first_output_cap_seen_at = None
    return None


_CAP = "API Error: Claude's response exceeded the 32000 output token maximum"
_WORK = "Writing test file RouteProxyMock.sol ... tool_use bash forge build"


def test_cap_loop_trips_when_events_stay_fresh():
    # The live signature: cap text present every poll, last_event_time ALWAYS
    # fresh (turn keeps auto-continuing). Quiescence never trips; the loop gate
    # must terminate it within _OUTPUT_CAP_LOOP_S.
    _, loop_s = _gate()
    events = [(t, _CAP, t - 1.0) for t in range(0, int(loop_s) + 30, 3)]
    tripped = _simulate_wait(events)
    assert tripped is not None and tripped >= loop_s, (
        "a generate-until-cap loop with always-fresh events must trip the "
        "cap-loop gate"
    )


def test_quiescence_path_still_fast():
    # Cap followed by silence (no new events) trips the FAST quiescence path,
    # not the slow loop path.
    events = [(t, _CAP, 0.0) for t in (0.0, 3.0, 9.0, 12.0)]
    tripped = _simulate_wait(events, quiescence_s=8.0)
    assert tripped == 9.0, f"quiescence path should fire at 9.0, got {tripped}"


def test_productive_turn_past_cap_not_cut_off():
    # Turn hits ONE cap, then scrolls past it and keeps working productively
    # (cap text gone, events fresh). Must NOT be terminated even long after the
    # first cap sighting -> the latch reset is what protects recall.
    _, loop_s = _gate()
    events = [(0.0, _CAP, 0.0)]
    events += [(t, _WORK, t - 1.0)
               for t in range(3, int(loop_s) + 200, 3)]
    assert _simulate_wait(events) is None, (
        "a turn that recovers past one cap must never be cut off"
    )


def test_real_dedup_log_string_matches_gate():
    cap_re, _ = _gate()
    assert cap_re.search(_CAP), "gate regex must match the real cap error line"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
