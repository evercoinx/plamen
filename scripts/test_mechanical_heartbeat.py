"""Tests for the mechanical-step heartbeat (`_PhaseHeartbeatThread`).

The driver wraps long *synchronous in-process* mechanical steps (verify
recovery shard, mechanical PoC verify, cross-tier report_dedup) in
`_phase_heartbeat_thread(...)` so a multi-minute silent window can't be
mistaken for a hang. These tests pin the behavioral contract the wrap-sites
rely on:

  1. FIRES  — a step running LONGER than the interval emits >= 1 pulse.
  2. NO FALSE FIRE — a step SHORTER than the interval emits ZERO pulses
     (the first pulse only after `interval` seconds; no t=0 pulse).
  3. NO LEAK — after the context exits the daemon thread is stopped/joined,
     and an exception inside the block still stops the thread + propagates.
  4. NO-OP — display=None (or lacking print_phase_heartbeat) never raises,
     never pulses.

Run: pytest test_mechanical_heartbeat.py -v
Standalone: python test_mechanical_heartbeat.py
"""
from __future__ import annotations

import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(__file__))

from plamen_driver import _phase_heartbeat_thread, _PhaseHeartbeatThread


class _CapturingDisplay:
    """Minimal fake `display` sink: records every heartbeat pulse."""

    def __init__(self):
        self.pulses = []
        self._lock = threading.Lock()

    def print_phase_heartbeat(self, phase_name, elapsed_s, **kwargs):
        with self._lock:
            self.pulses.append((phase_name, elapsed_s, kwargs))

    @property
    def count(self):
        with self._lock:
            return len(self.pulses)


class _DisplayWithoutMethod:
    """A display object missing print_phase_heartbeat -> must no-op."""


def _fmt(name, ok):
    return f"[{'PASS' if ok else 'FAIL'}] {name}"


def test_fires_on_long_step(tmp_path=None):
    """A step longer than the interval emits >= 1 pulse.

    NOTE: the class floors the interval to a 1s minimum (anti-spam), so the
    smallest meaningful "long" step is > 1s. We sleep ~1.4s and expect >= 1
    pulse (fires ~once per second).
    """
    disp = _CapturingDisplay()
    interval = 1  # the class's minimum; casting/flooring keeps this at 1s
    with _phase_heartbeat_thread(disp, "verify-recovery", None, interval=interval):
        time.sleep(1.4)  # > interval -> expect ~1 pulse
    assert disp.count >= 1, f"expected >=1 pulse, got {disp.count}"
    # Every pulse must carry the phase name (truthful, not asserting progress).
    for name, elapsed, _ in disp.pulses:
        assert name == "verify-recovery"
        assert isinstance(elapsed, int) and elapsed >= 0


def test_no_false_fire_on_fast_step():
    """A step SHORTER than the interval emits ZERO pulses (no t=0 pulse)."""
    disp = _CapturingDisplay()
    interval = 0.5  # 500ms interval
    t0 = time.monotonic()
    with _phase_heartbeat_thread(disp, "report_dedup", None, interval=interval):
        time.sleep(0.02)  # 20ms << interval -> must emit nothing
    # Ensure the block really was fast (guards against a slow CI skewing intent).
    assert time.monotonic() - t0 < interval, "test block itself exceeded interval"
    assert disp.count == 0, f"fast step should emit 0 pulses, got {disp.count}"


def test_thread_stopped_and_joined_on_exit():
    """After context exit, no heartbeat thread is left alive (no leak)."""
    disp = _CapturingDisplay()
    hb = _PhaseHeartbeatThread(disp, "mechanical_verify", None, interval=0.02)
    with hb:
        time.sleep(0.05)
        t = hb._thread
        assert t is not None and t.is_alive(), "thread should run inside the block"
    # Give join() its window; __exit__ joins with a 2s timeout.
    assert hb._thread is None or not hb._thread.is_alive(), "thread leaked after exit"
    # No lingering heartbeat-* daemon thread.
    leaked = [
        th for th in threading.enumerate()
        if th.name.startswith("heartbeat-") and th.is_alive()
    ]
    assert not leaked, f"leaked heartbeat threads: {[t.name for t in leaked]}"


def test_exception_inside_block_still_stops_thread_and_propagates():
    """An exception raised in the wrapped step propagates AND stops the thread."""
    disp = _CapturingDisplay()
    hb = _PhaseHeartbeatThread(disp, "verify-recovery", None, interval=0.02)
    raised = False
    try:
        with hb:
            time.sleep(0.03)
            raise ValueError("boom from mechanical step")
    except ValueError as e:
        raised = True
        assert "boom" in str(e)
    assert raised, "__exit__ must NOT suppress the exception"
    assert hb._thread is None or not hb._thread.is_alive(), "thread leaked after exception"


def test_noop_when_display_is_none():
    """display=None: never raises, never pulses, no thread started."""
    hb = _PhaseHeartbeatThread(None, "report_dedup", None, interval=0.02)
    with hb:
        time.sleep(0.05)
    assert hb._thread is None, "no thread should start with display=None"


def test_noop_when_display_lacks_method():
    """A display object without print_phase_heartbeat: no-op, no raise."""
    disp = _DisplayWithoutMethod()
    hb = _PhaseHeartbeatThread(disp, "mechanical_verify", None, interval=0.02)
    with hb:
        time.sleep(0.05)
    assert hb._thread is None, "no thread should start when method is absent"


def _all():
    return [
        ("fires_on_long_step", test_fires_on_long_step),
        ("no_false_fire_on_fast_step", test_no_false_fire_on_fast_step),
        ("thread_stopped_and_joined_on_exit", test_thread_stopped_and_joined_on_exit),
        ("exception_inside_block_still_stops_thread_and_propagates",
         test_exception_inside_block_still_stops_thread_and_propagates),
        ("noop_when_display_is_none", test_noop_when_display_is_none),
        ("noop_when_display_lacks_method", test_noop_when_display_lacks_method),
    ]


if __name__ == "__main__":
    failures = 0
    for name, fn in _all():
        try:
            fn()
            print(_fmt(name, True))
        except Exception as exc:
            failures += 1
            print(_fmt(name, False) + f"  -> {exc!r}")
    print(f"\n{len(_all()) - failures}/{len(_all())} passed")
    sys.exit(1 if failures else 0)
