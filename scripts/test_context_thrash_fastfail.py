"""Context-overflow / autocompact-thrash fast-fail (Ship 8.17).

The PTY wait loop already flips ``output_truncated`` for the model OUTPUT cap.
But the INPUT side -- a turn whose context has grown so large that Claude Code
is stuck auto-compacting (or has explicitly given up) -- had no dedicated exit:
the transcript keeps emitting compaction-summary / re-read events so the
quiescence gate never trips, the process stays alive, and it is NOT the output
cap. The ONLY terminator was the full scaled phase deadline (observed: ~3.6h
burn on a 144K-finding report index).

These tests pin:
  1. the overflow/thrash regex matches the real fingerprints (and not benign text);
  2. the dual-signal gate fires only when thrash text/compaction is present AND
     no new PRODUCTIVE event has advanced for >= _CONTEXT_THRASH_LOOP_S;
  3. a compact-THEN-RESUME turn (productive_event_count rises) is NEVER cut off;
  4. a non-thrash session is byte-unaffected;
  5. the real wait_for_turn_complete returns EARLY (well before the deadline)
     on a sustained thrash signature.
"""
import re
import time
from pathlib import Path


def _consts():
    import pty_exec as px
    return px._CONTEXT_OVERFLOW_TEXT_RE, px._CONTEXT_THRASH_LOOP_S


# ── Fix 1: the overflow/thrash regex ─────────────────────────────────────────

_THRASH_LINES = [
    "Autocompact is thrashing",
    "auto-compact is thrashing",
    "Error: a file being read is too large",
    "prompt is too long",
    "input is too long",
    "this request exceeds the maximum context window",
    "context window exceeded",
    "Context low, auto-compact may run",
]

_BENIGN_LINES = [
    "Writing report_index.md ... tool_use Write",
    "forge build succeeded",
    "Analyzed function liquidate() at L120",
    "All findings mapped to report IDs",
]


def test_overflow_regex_matches_real_thrash_fingerprints():
    rx, _ = _consts()
    for line in _THRASH_LINES:
        assert rx.search(line), f"thrash regex must match: {line!r}"


def test_overflow_regex_does_not_match_benign_work():
    rx, _ = _consts()
    for line in _BENIGN_LINES:
        assert not rx.search(line), f"thrash regex must NOT match benign: {line!r}"


# ── Fix 2/3: dual-signal gate logic (mirror of the wait-loop branch) ──────────

def _simulate_thrash(events):
    """Mirror the context-thrash branch of wait_for_turn_complete.

    events: list of (now, recent_output, productive_event_count, thrash_sig)
    sampled at successive polls. Returns the ``now`` at which context_thrash
    would be set, or None if it never trips.
    """
    rx, loop_s = _consts()
    first_thrash_seen_at = None
    last_productive_count = -1
    for (now, recent, productive_count, transcript_compaction) in events:
        overflow_text = bool(rx.search(recent))
        thrash_signature = overflow_text or transcript_compaction
        if productive_count > last_productive_count:
            last_productive_count = productive_count
            first_thrash_seen_at = None
        elif thrash_signature:
            if first_thrash_seen_at is None:
                first_thrash_seen_at = now
            elif now - first_thrash_seen_at >= loop_s:
                return now
        else:
            first_thrash_seen_at = None
    return None


def test_sustained_thrash_with_no_progress_trips():
    # Thrash text present every poll, productive_event_count frozen -> the gate
    # must fire within _CONTEXT_THRASH_LOOP_S.
    _, loop_s = _consts()
    events = [
        (t, "Autocompact is thrashing", 5, True)
        for t in range(0, int(loop_s) + 30, 5)
    ]
    tripped = _simulate_thrash(events)
    assert tripped is not None and tripped >= loop_s, (
        "a stuck-thrashing turn (no new productive output) must trip the gate"
    )


def test_compaction_signature_only_also_trips():
    # No explicit overflow text, but transcript_shows_compaction is True and the
    # productive count is frozen -> sustained churn must still trip.
    _, loop_s = _consts()
    events = [
        (t, "summarizing prior turns", 2, True)
        for t in range(0, int(loop_s) + 30, 5)
    ]
    assert _simulate_thrash(events) is not None


def test_compact_then_resume_is_never_cut_off():
    # The recall-safe case: turn compacts (thrash signature present) but KEEPS
    # producing new productive events -> productive_event_count rises each poll
    # -> latch resets -> never cut off, even long past _CONTEXT_THRASH_LOOP_S.
    _, loop_s = _consts()
    events = []
    count = 0
    for i, t in enumerate(range(0, int(loop_s) + 400, 5)):
        count += 1  # a new productive (tool_use/text) event every poll
        events.append((float(t), "compacting conversation", count, True))
    assert _simulate_thrash(events) is None, (
        "a compact-then-resume turn making forward progress must never be cut off"
    )


def test_no_thrash_signature_never_trips():
    # Benign working session: no overflow text, no compaction -> gate inert.
    _, loop_s = _consts()
    events = [
        (t, "forge build succeeded", i, False)
        for i, t in enumerate(range(0, int(loop_s) + 400, 5))
    ]
    assert _simulate_thrash(events) is None


def test_thrash_clears_then_resumes_resets_latch():
    # Thrash for a while (but under the window), then the signature clears
    # entirely -> latch resets -> never trips.
    _, loop_s = _consts()
    half = int(loop_s) // 2
    events = [(float(t), "prompt is too long", 1, True)
              for t in range(0, half, 5)]
    events += [(float(t), "forge build", 1, False)
               for t in range(half, half + int(loop_s) + 50, 5)]
    assert _simulate_thrash(events) is None


# ── Fix 5: the real wait loop returns EARLY on sustained thrash ───────────────

class _FakeSession:
    """Minimal stand-in driving the real wait_for_turn_complete.

    Always-alive process, a transcript that never reaches end_turn, and a
    recent-output buffer carrying a thrash fingerprint -> without the new gate
    the loop would block to the full deadline; with it, it returns early.
    """

    def __init__(self, tmp_path, recent_text, transcript_text=None):
        import threading
        self.transcript_path = Path(tmp_path) / "transcript.jsonl"
        # A transcript with one assistant event but no end_turn. Because the
        # file is STATIC, inspect_transcript recomputes the same
        # productive_event_count every poll -> it does NOT advance after the
        # first read, which is exactly the "no forward progress" signature the
        # gate keys on (paired with a thrash signature).
        if transcript_text is None:
            transcript_text = (
                '{"type":"assistant","message":{"content":'
                '[{"type":"text","text":"compacting conversation"}]}}\n'
            )
        self.transcript_path.write_text(transcript_text, encoding="utf-8")
        self._recent_output = recent_text
        self._recent_output_lock = threading.Lock()

    def is_alive(self):
        return True


def test_real_wait_loop_fast_fails_on_thrash(tmp_path, monkeypatch):
    import pty_exec as px
    # Shrink the thrash window so the test is fast, via the module constant the
    # wait loop reads.
    monkeypatch.setattr(px, "_CONTEXT_THRASH_LOOP_S", 0.4, raising=False)
    sess = _FakeSession(tmp_path, "Autocompact is thrashing")
    bound = px.ClaudePtySession.wait_for_turn_complete.__get__(sess, _FakeSession)
    t0 = time.time()
    # Generous deadline: if the gate is broken, this would block the full 30s.
    state = bound(timeout_s=30.0, quiescence_s=8.0, poll_s=0.05,
                  transcript_poll_s=0.05)
    elapsed = time.time() - t0
    assert getattr(state, "context_thrash", False), (
        "sustained thrash signature must set state.context_thrash"
    )
    assert elapsed < 10.0, (
        f"thrash fast-fail must return well before the 30s deadline; "
        f"took {elapsed:.1f}s"
    )


def test_real_wait_loop_no_early_exit_without_thrash(tmp_path, monkeypatch):
    import pty_exec as px
    monkeypatch.setattr(px, "_CONTEXT_THRASH_LOOP_S", 0.4, raising=False)
    # Benign recent output AND a transcript with NO compaction fingerprint ->
    # neither thrash signal is present -> the gate stays inert and the loop runs
    # to its (short) deadline without flagging context_thrash.
    benign_transcript = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"analyzed liquidate() at L120"}]}}\n'
    )
    sess = _FakeSession(tmp_path, "forge build succeeded",
                        transcript_text=benign_transcript)
    bound = px.ClaudePtySession.wait_for_turn_complete.__get__(sess, _FakeSession)
    state = bound(timeout_s=0.6, quiescence_s=8.0, poll_s=0.05,
                  transcript_poll_s=0.05)
    assert not getattr(state, "context_thrash", False)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
