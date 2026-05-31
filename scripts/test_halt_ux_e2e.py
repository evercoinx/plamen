from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def test_user_stop_hard_exit_releases_lock_and_terminates_active_worker_child():
    """E2E stop-path guard: printing "Stopped" must mean the process exits.

    The regression this covers was visible in terminals as:
      - Esc prompt accepted
      - purge/keep prompt accepted
      - "Stopped. Re-run..." printed
      - driver process and Claude child still alive

    This test runs the hard-exit path in a real subprocess so os._exit(),
    atexit bypass, run-lock release, and active-worker cleanup are exercised
    together.
    """
    with tempfile.TemporaryDirectory(prefix="plamen_halt_e2e_") as td:
        root = Path(td)
        child_script = root / "child_stop_path.py"
        scratch = root / ".scratchpad"
        scratch.mkdir()
        config_path = scratch / "config.json"
        worker_pid_path = root / "worker.pid"
        terminated_marker = root / "worker_terminated.txt"

        child_script.write_text(
            f"""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, {str(SCRIPTS_DIR)!r})
import plamen_driver as D

scratch = Path({str(scratch)!r})
config_path = Path({str(config_path)!r})
worker_pid_path = Path({str(worker_pid_path)!r})
terminated_marker = Path({str(terminated_marker)!r})
config_path.write_text('{{"project_root": ".", "scratchpad": "."}}', encoding='utf-8')

ok, msg = D._acquire_run_lock(scratch, config_path)
if not ok:
    raise SystemExit(msg)

worker = subprocess.Popen([
    sys.executable,
    "-c",
    "import time; time.sleep(60)",
], cwd=str(Path.home()))
worker_pid_path.write_text(str(worker.pid), encoding='utf-8')

class FakeSession:
    def terminate(self, grace_s=0.1):
        terminated_marker.write_text("called", encoding='utf-8')
        try:
            worker.terminate()
            worker.wait(timeout=5)
        except Exception:
            try:
                worker.kill()
            except Exception:
                pass

D._register_active_worker_session(FakeSession())
D._hard_exit_after_user_stop(0)
raise SystemExit("hard exit returned")
""",
            encoding="utf-8",
        )

        proc = subprocess.Popen(
            [sys.executable, str(child_script)],
            cwd=str(Path.home()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(timeout=15)

        assert proc.returncode == 0, (stdout, stderr)
        assert not (scratch / ".plamen_run.lock").exists()
        assert terminated_marker.exists()

        worker_pid = int(worker_pid_path.read_text(encoding="utf-8"))
        deadline = time.time() + 5
        while time.time() < deadline and _pid_running(worker_pid):
            time.sleep(0.05)
        assert not _pid_running(worker_pid)


def test_force_run_lock_refuses_existing_live_lock(tmp_path):
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()
    config_path = scratch / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    lock = scratch / ".plamen_run.lock"
    lock.write_text(
        f'{{"pid": {os.getpid()}, "started_at": "2026-01-01T00:00:00Z", "config_path": "old"}}',
        encoding="utf-8",
    )

    ok, msg = D._acquire_run_lock(scratch, config_path, force=True)

    assert not ok
    assert "--force refused" in msg
    assert '"config_path": "old"' in lock.read_text(encoding="utf-8")


def test_force_run_lock_removes_stale_lock(tmp_path):
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()
    config_path = scratch / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    lock = scratch / ".plamen_run.lock"
    lock.write_text(
        '{"pid": 99999999, "started_at": "2026-01-01T00:00:00Z", "config_path": "old"}',
        encoding="utf-8",
    )

    ok, msg = D._acquire_run_lock(scratch, config_path, force=True)

    try:
        assert ok, msg
        import json

        assert json.loads(lock.read_text(encoding="utf-8"))["config_path"] == str(config_path)
    finally:
        D._release_run_lock()
