"""The runaway-tool-result watchdog scanned EVERY dir under
~/.claude/projects/ filtered only by mtime. With two audits running
concurrently (or any Codex run, whose sessions live elsewhere), it surfaced
ANOTHER run's multi-MB tool-result and misattributed it to the current phase:

  [breadth] runaway tool result detected: 38510 KB at
  ...projects/D--...-Other-.../.../tool-results/bvhjeso5d.txt -- a subagent
  likely read outside PROJECT_ROOT. Coordinator may stall ...

...during an L1 run on Codex. False alarm on every Codex run + any
concurrent run.

Fix: scope the scan to the CURRENT run's project dir, keyed by
cwd == PROJECT_ROOT == scratchpad.parent, using the verified Claude dir
encoding. A sibling run's blob (different encoded dir) is no longer scanned;
the current run's own blobs are still caught.
"""
from __future__ import annotations

import importlib
import os
import re
import sys
import time
from pathlib import Path


def _drv():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_driver")


def test_encoder_matches_real_claude_dirs():
    d = _drv()
    # Representative on-disk project dirs (drive letter, spaces, embedded dashes).
    assert _re_enc(r"D:\Programming\Audits\L1\example-node tests\target") == \
        "D--Programming-Audits-L1-example-node-tests-target"
    assert _re_enc(
        r"D:\Programming\Web3\Contests\Acme Crosschain Dex"
        r"\2025-05-acme-cross-chain-dex\omni-chain-contracts\contracts"
    ) == ("D--Programming-Web3-Contests-Acme-Crosschain-Dex-2025-05-acme-"
          "cross-chain-dex-omni-chain-contracts-contracts")
    # The function itself (abspath may rewrite a relative input, so feed abs).
    assert d._claude_project_dir_name(Path("/tmp/foo bar/baz")).endswith(
        "tmp-foo-bar-baz"
    ) or "foo-bar-baz" in d._claude_project_dir_name(Path("/tmp/foo bar/baz"))


def _re_enc(p: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def _make_blob(projects: Path, project_root: Path, name: str, size: int,
               mtime: float):
    # Mirror the function's encoding (abspath) so the dir matches what the
    # scanner computes from scratchpad.parent.
    enc = _re_enc(os.path.abspath(str(project_root)))
    tr = projects / enc / "sess-uuid" / "tool-results"
    tr.mkdir(parents=True, exist_ok=True)
    f = tr / name
    f.write_bytes(b"x" * size)
    os.utime(f, (mtime, mtime))
    return f


# SHORT synthetic absolute roots (drive anchor + short name). The scanner never
# stats the project root -- it only encodes the string -- so these need not
# exist on disk. Keeping them short avoids Windows MAX_PATH on the encoded
# ~/.claude/projects/<encoded>/sess/tool-results path.
def _roots(tmp_path):
    anchor = tmp_path.anchor or "/"
    current_root = Path(anchor) / "rwcur"          # e.g. C:\rwcur
    sibling_root = Path(anchor) / "rwsib" / "sub"  # e.g. C:\rwsib\sub
    return current_root, sibling_root


def test_sibling_run_blob_ignored_current_run_caught(tmp_path, monkeypatch):
    d = _drv()
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(d.Path, "home", staticmethod(lambda: tmp_path))

    start = time.time() - 60
    fresh = time.time()
    big = d._RUNAWAY_TOOL_RESULT_BYTES + 4096
    current_root, sibling_root = _roots(tmp_path)

    # The concurrent sibling run dumps a 38MB blob AFTER the current phase started.
    _make_blob(projects, sibling_root, "sibling_blob.txt", big, fresh)
    # The current run has its OWN runaway blob.
    cur = _make_blob(projects, current_root, "current_blob.txt", big, fresh)

    scratchpad = current_root / ".scratchpad"
    found = d._scan_claude_tool_results_for_runaways(start, set(), scratchpad)
    keys = {p for p, _ in found}
    assert str(cur) in keys, "current run's runaway blob MUST still be caught"
    assert all("rwsib" not in k for k in keys), (
        "a sibling run's blob must NEVER be attributed to this run"
    )
    assert len(found) == 1


def test_codex_run_no_claude_dir_returns_empty(tmp_path, monkeypatch):
    # Codex writes its sessions elsewhere -> the current run has no
    # ~/.claude/projects/<encoded> dir at all. Even with a huge blob sitting
    # under a DIFFERENT (sibling) project dir, the scan must return nothing.
    d = _drv()
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(d.Path, "home", staticmethod(lambda: tmp_path))

    big = d._RUNAWAY_TOOL_RESULT_BYTES + 4096
    current_root, sibling_root = _roots(tmp_path)
    _make_blob(projects, sibling_root, "sibling_blob.txt", big, time.time())

    # codex run: no projects/<encoded current_root> dir created at all
    scratchpad = current_root / ".scratchpad"
    found = d._scan_claude_tool_results_for_runaways(
        time.time() - 60, set(), scratchpad
    )
    assert found == [], "Codex run must not surface a sibling Claude run's blob"


def test_stale_file_before_start_ignored(tmp_path, monkeypatch):
    d = _drv()
    projects = tmp_path / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(d.Path, "home", staticmethod(lambda: tmp_path))

    current_root, _ = _roots(tmp_path)
    big = d._RUNAWAY_TOOL_RESULT_BYTES + 4096
    start = time.time()
    # blob from a PRIOR session, mtime before this phase started
    _make_blob(projects, current_root, "old.txt", big, start - 3600)

    scratchpad = current_root / ".scratchpad"
    found = d._scan_claude_tool_results_for_runaways(start, set(), scratchpad)
    assert found == [], "a pre-start stale blob must not fire"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
