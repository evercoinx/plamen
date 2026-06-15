"""Regression: `plamen setup` must persist toolchain PATH on macOS/Linux.

Before this fix, _update_path_env only persisted PATH on Windows (registry).
On POSIX, toolchains installed by setup (foundry -> ~/.foundry/bin, cargo,
solana) updated only the in-process PATH and vanished -> audit subprocesses
spawned from a future shell hit `COMPILATION_FAILED` (tool not found). The
fresh-POSIX-user open-source blocker. `_persist_path_posix` writes an
idempotent marker block to the user's shell rc file(s).
"""
import os
import importlib.util
import tempfile

import pytest

_PLAMEN = os.path.join(os.path.expanduser("~/.plamen"), "plamen.py")


def _load():
    import sys
    spec = importlib.util.spec_from_file_location("plamen_mod_test", _PLAMEN)
    m = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = ["plamen.py"]
    try:
        spec.loader.exec_module(m)
    finally:
        sys.argv = saved
    return m


def _home(monkeypatch, d, shell="/bin/zsh"):
    # ntpath.expanduser uses USERPROFILE first; posixpath uses HOME.
    monkeypatch.setenv("USERPROFILE", d)
    monkeypatch.setenv("HOME", d)
    monkeypatch.setenv("SHELL", shell)


def test_creates_marker_block_in_profile(monkeypatch):
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        _home(monkeypatch, d)
        m._persist_path_posix("/opt/.foundry/bin")
        prof = open(os.path.join(d, ".profile"), encoding="utf-8").read()
        assert prof.count("# >>> plamen toolchain PATH >>>") == 1
        assert 'export PATH="/opt/.foundry/bin:$PATH"' in prof


def test_dirs_accumulate_and_idempotent(monkeypatch):
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        _home(monkeypatch, d)
        m._persist_path_posix("/opt/.foundry/bin")
        m._persist_path_posix("/opt/.cargo/bin")
        m._persist_path_posix("/opt/.foundry/bin")  # re-add -> no dup
        prof = open(os.path.join(d, ".profile"), encoding="utf-8").read()
        assert prof.count("# >>> plamen toolchain PATH >>>") == 1
        assert prof.count("/opt/.foundry/bin") == 1
        assert 'export PATH="/opt/.foundry/bin:/opt/.cargo/bin:$PATH"' in prof


def test_preserves_existing_rc_content_and_writes_shell_rc(monkeypatch):
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        _home(monkeypatch, d, shell="/usr/bin/zsh")
        open(os.path.join(d, ".zshrc"), "w", encoding="utf-8").write("alias ll='ls -la'\n")
        m._persist_path_posix("/opt/.foundry/bin")
        zrc = open(os.path.join(d, ".zshrc"), encoding="utf-8").read()
        assert "alias ll='ls -la'" in zrc          # existing content preserved
        assert zrc.count("# >>> plamen toolchain PATH >>>") == 1
        assert 'export PATH="/opt/.foundry/bin:$PATH"' in zrc


def test_bash_targets_bashrc(monkeypatch):
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        _home(monkeypatch, d, shell="/bin/bash")
        open(os.path.join(d, ".bashrc"), "w", encoding="utf-8").write("# bash\n")
        m._persist_path_posix("/opt/.cargo/bin")
        assert "plamen toolchain PATH" in open(os.path.join(d, ".bashrc"), encoding="utf-8").read()
        # ~/.profile baseline always written too
        assert "plamen toolchain PATH" in open(os.path.join(d, ".profile"), encoding="utf-8").read()


def test_update_path_env_routes_posix_not_windows(monkeypatch):
    """persist=True on non-win32 must call _persist_path_posix, never registry."""
    m = _load()
    if m.sys.platform == "win32":
        pytest.skip("routing test is for the POSIX branch")
    with tempfile.TemporaryDirectory() as d:
        _home(monkeypatch, d)
        toolbin = os.path.join(d, "toolbin")
        os.makedirs(toolbin)
        m._update_path_env([toolbin], persist=True)
        assert "plamen toolchain PATH" in open(os.path.join(d, ".profile"), encoding="utf-8").read()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
