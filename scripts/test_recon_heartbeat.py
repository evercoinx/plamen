"""Targeted test for the recon-phase heartbeat helper (_phase_heartbeat_thread).

Verifies the daemon-thread heartbeat that wraps the long synchronous in-process
recon prepass (Slither/graph bake) so the terminal isn't silent:
  (a) at least one heartbeat fires during a slow step,
  (b) the heartbeat reports newly-written artifacts,
  (c) the thread is stopped (not alive) after the context exits,
  (d) it degrades to a no-op (no crash) when display is None.
"""
import threading
import time
from pathlib import Path

import plamen_driver


class _FakeDisplay:
    """Captures print_phase_heartbeat calls, mirroring the real signature."""

    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def print_phase_heartbeat(self, phase_name, elapsed_s, new_artifacts=None,
                              updated_artifacts=None, status=None,
                              status_style="warning", tool_calls_delta_kb=None):
        with self._lock:
            self.calls.append({
                "phase_name": phase_name,
                "elapsed_s": elapsed_s,
                "new_artifacts": list(new_artifacts) if new_artifacts else None,
            })


def test_heartbeat_fires_and_reports_artifacts(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    disp = _FakeDisplay()

    def _slow_step():
        # Sleep well past the 1s interval; write two artifacts mid-flight.
        time.sleep(1.4)
        (scratch / "caller_map.md").write_text("x")
        time.sleep(1.4)
        (scratch / "_mechanical_graph.json").write_text("{}")
        time.sleep(0.6)

    ctx = plamen_driver._phase_heartbeat_thread(disp, "recon", scratch, interval=1)
    with ctx:
        _slow_step()

    # (a) at least one heartbeat fired during the slow step
    assert len(disp.calls) >= 1, f"expected >=1 heartbeat, got {len(disp.calls)}"
    assert all(c["phase_name"] == "recon" for c in disp.calls)

    # (b) the heartbeat reported the newly-written artifacts
    reported = set()
    for c in disp.calls:
        for a in (c["new_artifacts"] or []):
            reported.add(a)
    assert "caller_map.md" in reported, f"new artifacts not reported: {reported}"
    assert "_mechanical_graph.json" in reported, f"new artifacts not reported: {reported}"

    # (c) the thread is stopped (not alive) after the context exits
    assert ctx._thread is not None
    assert not ctx._thread.is_alive(), "heartbeat thread leaked (still alive)"


def test_heartbeat_no_display_is_noop(tmp_path):
    # Degrades to a no-op without a display; must not raise or leak a thread.
    with plamen_driver._phase_heartbeat_thread(None, "recon", tmp_path, interval=1) as ctx:
        time.sleep(0.2)
    assert ctx._thread is None


def test_heartbeat_stops_even_on_exception(tmp_path):
    disp = _FakeDisplay()
    ctx = plamen_driver._phase_heartbeat_thread(disp, "recon", tmp_path, interval=1)
    try:
        with ctx:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    # Exception inside the wrapped step must not suppress, and thread must stop.
    assert ctx._thread is None or not ctx._thread.is_alive()


if __name__ == "__main__":
    import tempfile
    for fn in (test_heartbeat_fires_and_reports_artifacts,
               test_heartbeat_no_display_is_noop,
               test_heartbeat_stops_even_on_exception):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
        print(f"PASS: {fn.__name__}")
    print("ALL PASS")
