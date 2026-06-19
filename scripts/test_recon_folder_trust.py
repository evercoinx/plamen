"""A PTY worker whose cwd is a never-opened dir hangs on Claude Code's folder
trust dialog. _ensure_claude_folder_trusted pre-accepts trust in ~/.claude.json
so headless workers launch. Must preserve all other config and never clobber."""
import json
import pathlib

import plamen_driver as D


def _home(monkeypatch, tmp_path):
    # Path.home() resolves USERPROFILE on Windows, HOME on POSIX.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_pretrust_sets_flag_and_preserves_existing(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    cj = tmp_path / ".claude.json"
    cj.write_text(json.dumps({
        "projects": {"/already/there": {"foo": 1}},
        "topLevelSetting": "keep-me",
    }), encoding="utf-8")

    proj = str(tmp_path / "fresh_harness")
    newly = D._ensure_claude_folder_trusted(proj)
    assert newly, "expected the fresh dir to be newly trusted"

    data = json.loads(cj.read_text(encoding="utf-8"))
    # untouched config preserved
    assert data["topLevelSetting"] == "keep-me"
    assert data["projects"]["/already/there"] == {"foo": 1}
    # fresh dir now trusted (forward-slash form, matching Claude's convention)
    fkey = str(pathlib.Path(proj).resolve()).replace("\\", "/")
    assert data["projects"][fkey]["hasTrustDialogAccepted"] is True
    # idempotent: re-running trusts nothing new
    assert D._ensure_claude_folder_trusted(proj) == []


def test_pretrust_never_clobbers_unreadable_config(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    cj = tmp_path / ".claude.json"
    cj.write_text("{ this is not valid json", encoding="utf-8")
    # never raises, returns [], and leaves the corrupt file untouched
    assert D._ensure_claude_folder_trusted(str(tmp_path / "x")) == []
    assert cj.read_text(encoding="utf-8") == "{ this is not valid json"


def test_pretrust_creates_config_when_absent(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path)
    proj = str(tmp_path / "harness")
    assert D._ensure_claude_folder_trusted(proj)
    data = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    fkey = str(pathlib.Path(proj).resolve()).replace("\\", "/")
    assert data["projects"][fkey]["hasTrustDialogAccepted"] is True


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
