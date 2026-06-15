"""Regression: Windows installs without symlink privilege (Developer Mode off /
non-elevated shell) must fall back to copying FILES instead of leaving the
install half-wired.

Bug (pre-fix): `_safe_link` created directory junctions (no privilege needed)
but linked individual FILES via `os.symlink`, which needs
SeCreateSymbolicLinkPrivilege. On a fresh non-Developer-Mode Windows box every
per-file link (agents/*.md, rules/*.md, commands/*.md, plamen.py, VERSION)
raised a privilege OSError, so the methodology was only partially wired and the
install reported many 'failed to link' lines with no fallback.

Fix: on Windows, when `os.symlink` of a FILE raises a privilege OSError,
`_safe_link` falls back to `shutil.copy2` and returns "copied". Copied
destinations are recorded in the manifest's `copied` list so `run_uninstall`
removes them (they are plain files, not links, and the link-only removal path
would otherwise leak them).
"""
import os
import json
import importlib.util
import tempfile

import pytest

_PLAMEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plamen.py")


def _load():
    import sys
    spec = importlib.util.spec_from_file_location("plamen_mod_win_copy_fallback", _PLAMEN)
    m = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = ["plamen.py"]
    try:
        spec.loader.exec_module(m)
    finally:
        sys.argv = saved
    return m


def _force_no_symlink_privilege(monkeypatch, m):
    """Simulate Windows without SeCreateSymbolicLinkPrivilege: pretend we are on
    win32 and make os.symlink raise the privilege OSError the OS would raise."""
    monkeypatch.setattr(m.sys, "platform", "win32", raising=False)

    def _raise_privilege(src, dst, target_is_directory=False):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(m.os, "symlink", _raise_privilege)


def _force_junction_failure(monkeypatch, m):
    """Simulate Windows where `mklink /J` fails — most commonly because the
    junction would span volumes (src on D:, dst on C:). `subprocess.run(...,
    check=True)` raises CalledProcessError on the non-zero exit."""
    import subprocess as _sp
    monkeypatch.setattr(m.sys, "platform", "win32", raising=False)

    def _fake_run(args, *a, **kw):
        if args[:3] == ["cmd", "/c", "mklink"]:
            raise _sp.CalledProcessError(1, args, stderr=b"cannot span volumes")
        raise AssertionError(f"unexpected subprocess.run({args})")

    monkeypatch.setattr(m.subprocess, "run", _fake_run)


def test_safe_link_copies_dir_when_junction_fails(monkeypatch):
    """Cross-volume junction failure must fall back to copytree and return
    'copied_dir' — not crash on the uncaught CalledProcessError, and not leave
    the methodology tree absent (which hard-fails the Codex backend)."""
    m = _load()
    _force_junction_failure(monkeypatch, m)
    with tempfile.TemporaryDirectory() as root:
        src = os.path.join(root, "plamen_tree")
        os.makedirs(os.path.join(src, "rules"))
        with open(os.path.join(src, "rules", "a.md"), "w") as f:
            f.write("rule a\n")
        with open(os.path.join(src, "CLAUDE.md"), "w") as f:
            f.write("root\n")
        dst = os.path.join(root, ".codex", "plamen")

        status = m._safe_link(src, dst, lambda *_: None)
        assert status == "copied_dir"
        # The destination is a real copied tree, NOT a junction/symlink.
        assert os.path.isdir(dst)
        assert not os.path.islink(dst)
        # Every methodology file is present.
        with open(os.path.join(dst, "CLAUDE.md")) as f:
            assert f.read() == "root\n"
        with open(os.path.join(dst, "rules", "a.md")) as f:
            assert f.read() == "rule a\n"


def test_copied_dir_reinstall_is_idempotent(monkeypatch):
    """Re-running the copytree fallback over a prior copied tree (with a sibling
    .pre-plamen backup) must refresh the tree, not hit 'backup already exists'."""
    m = _load()
    _force_junction_failure(monkeypatch, m)
    with tempfile.TemporaryDirectory() as root:
        src = os.path.join(root, "plamen_tree")
        os.makedirs(src)
        with open(os.path.join(src, "VERSION"), "w") as f:
            f.write("NEW\n")

        dst = os.path.join(root, ".codex", "plamen")
        # Prior copied tree + a backed-up user original beside it.
        os.makedirs(dst)
        with open(os.path.join(dst, "VERSION"), "w") as f:
            f.write("OLD\n")
        with open(dst + ".pre-plamen", "w") as f:
            f.write("user original\n")

        status = m._safe_link(src, dst, lambda *_: None)
        assert status == "copied_dir"
        with open(os.path.join(dst, "VERSION")) as f:
            assert f.read() == "NEW\n"
        # User's backup untouched (recoverable on uninstall).
        with open(dst + ".pre-plamen") as f:
            assert f.read() == "user original\n"


def test_version_only_manifest_preserves_copied_dirs(monkeypatch):
    """A no-tracking _write_install_manifest() (the unconditional version stamp
    in run_install) must NOT clobber a prior copied_dirs/copied/installed list,
    or uninstall would leak every copied tree."""
    m = _load()
    with tempfile.TemporaryDirectory() as root:
        claude_home = os.path.join(root, ".claude")
        plamen_home = os.path.join(root, ".plamen")
        codex_home = os.path.join(root, ".codex")
        os.makedirs(claude_home)
        os.makedirs(plamen_home)
        os.makedirs(codex_home)
        real_expand = m.os.path.expanduser
        monkeypatch.setattr(m, "CLAUDE_HOME", claude_home, raising=False)
        monkeypatch.setattr(m, "PLAMEN_HOME", plamen_home, raising=False)
        monkeypatch.setattr(
            m.os.path, "expanduser",
            lambda p: codex_home if p == "~/.codex" else real_expand(p),
        )

        # First: a real install recorded a copied dir.
        cdir = os.path.join(codex_home, "plamen")
        m._write_install_manifest(["x"], copied=[], copied_dirs=[cdir])
        # Then: the unconditional version-only stamp runs.
        m._write_install_manifest()

        data = json.load(open(os.path.join(claude_home, m._PLAMEN_MANIFEST)))
        assert data["installed"] == ["x"]
        assert data["copied_dirs"] == [cdir]
        assert data["version"] == m.VERSION


def test_uninstall_removes_copied_dir_tree(monkeypatch):
    """A copied directory tree (cross-volume fallback) must be rmtree'd by
    run_uninstall and any backed-up user original restored — the link-only
    path skips real directories and would otherwise leak the tree."""
    m = _load()
    with tempfile.TemporaryDirectory() as root:
        claude_home = os.path.join(root, ".claude")
        plamen_home = os.path.join(root, ".plamen")
        os.makedirs(claude_home)
        os.makedirs(plamen_home)
        monkeypatch.setattr(m, "CLAUDE_HOME", claude_home, raising=False)
        monkeypatch.setattr(m, "PLAMEN_HOME", plamen_home, raising=False)
        # HOME isolation: keep _manifest_paths()'s ~/.codex from resolving to a
        # REAL ~/.codex on the machine running the suite (uninstall now reads +
        # removes across backends).
        monkeypatch.setenv("HOME", root)
        monkeypatch.setenv("USERPROFILE", root)

        copied_dir = os.path.join(claude_home, "plamen")
        os.makedirs(os.path.join(copied_dir, "rules"))
        with open(os.path.join(copied_dir, "rules", "a.md"), "w") as f:
            f.write("rule\n")

        manifest = {
            "plamen_home": plamen_home,
            "version": m.VERSION,
            "installed": [],
            "copied": [],
            "copied_dirs": [copied_dir],
        }
        with open(os.path.join(claude_home, m._PLAMEN_MANIFEST), "w") as f:
            json.dump(manifest, f)

        monkeypatch.setenv("PLAMEN_UNINSTALL_YES", "1")
        m.run_uninstall()

        # The whole copied tree is gone.
        assert not os.path.exists(copied_dir)
        assert not os.path.isfile(os.path.join(claude_home, m._PLAMEN_MANIFEST))


def test_safe_link_copies_file_when_no_privilege(monkeypatch):
    m = _load()
    _force_no_symlink_privilege(monkeypatch, m)
    with tempfile.TemporaryDirectory() as root:
        src = os.path.join(root, "VERSION")
        dst = os.path.join(root, "VERSION.dst")
        with open(src, "w") as f:
            f.write("2.1.0\n")

        status = m._safe_link(src, dst, lambda *_: None)
        assert status == "copied"
        # The destination is a real copy, NOT a symlink.
        assert os.path.isfile(dst)
        assert not os.path.islink(dst)
        with open(dst) as f:
            assert f.read() == "2.1.0\n"


def test_copy_fallback_is_idempotent_on_reinstall(monkeypatch):
    """Re-running the copy fallback must overwrite the prior copy rather than
    hitting the 'backup already exists' skip and returning False."""
    m = _load()
    _force_no_symlink_privilege(monkeypatch, m)
    with tempfile.TemporaryDirectory() as root:
        src = os.path.join(root, "rule.md")
        dst = os.path.join(root, "rule.dst.md")
        # Simulate a pre-existing USER file at dst that the FIRST install backed
        # up, plus our prior copy now sitting at dst.
        with open(dst + ".pre-plamen", "w") as f:
            f.write("user original\n")
        with open(dst, "w") as f:
            f.write("OLD plamen copy\n")
        with open(src, "w") as f:
            f.write("NEW plamen copy\n")

        status = m._safe_link(src, dst, lambda *_: None)
        assert status == "copied"
        with open(dst) as f:
            assert f.read() == "NEW plamen copy\n"
        # The user's backup is untouched (still recoverable on uninstall).
        with open(dst + ".pre-plamen") as f:
            assert f.read() == "user original\n"


def test_manifest_records_copied_subset(monkeypatch):
    m = _load()
    with tempfile.TemporaryDirectory() as root:
        claude_home = os.path.join(root, ".claude")
        plamen_home = os.path.join(root, ".plamen")
        os.makedirs(claude_home)
        os.makedirs(plamen_home)
        monkeypatch.setattr(m, "CLAUDE_HOME", claude_home, raising=False)
        monkeypatch.setattr(m, "PLAMEN_HOME", plamen_home, raising=False)
        real_expand = m.os.path.expanduser
        monkeypatch.setattr(
            m.os.path, "expanduser",
            lambda p: os.path.join(root, ".codex") if p == "~/.codex" else real_expand(p),
        )

        installed = ["a", "b", "c"]
        copied = ["b", "c"]
        m._write_install_manifest(installed, copied=copied)

        data = json.load(open(os.path.join(claude_home, m._PLAMEN_MANIFEST)))
        assert data["installed"] == installed
        assert data["copied"] == copied


def test_uninstall_removes_copied_plain_files(monkeypatch):
    """The copied plain files must be removed by run_uninstall and any
    backed-up user originals restored — mirroring the symlink removal path."""
    m = _load()
    with tempfile.TemporaryDirectory() as root:
        claude_home = os.path.join(root, ".claude")
        plamen_home = os.path.join(root, ".plamen")
        os.makedirs(claude_home)
        os.makedirs(plamen_home)
        monkeypatch.setattr(m, "CLAUDE_HOME", claude_home, raising=False)
        monkeypatch.setattr(m, "PLAMEN_HOME", plamen_home, raising=False)
        monkeypatch.setenv("HOME", root)
        monkeypatch.setenv("USERPROFILE", root)

        # One copied file with NO user backup, one copied file WITH a user
        # backup that must be restored.
        copied_plain = os.path.join(claude_home, "agent.md")
        with open(copied_plain, "w") as f:
            f.write("plamen content\n")

        copied_over_user = os.path.join(claude_home, "rule.md")
        with open(copied_over_user, "w") as f:
            f.write("plamen content\n")
        with open(copied_over_user + ".pre-plamen", "w") as f:
            f.write("user original\n")

        installed = [copied_plain, copied_over_user]
        manifest = {
            "plamen_home": plamen_home,
            "version": m.VERSION,
            "installed": installed,
            "copied": installed,
        }
        with open(os.path.join(claude_home, m._PLAMEN_MANIFEST), "w") as f:
            json.dump(manifest, f)

        monkeypatch.setenv("PLAMEN_UNINSTALL_YES", "1")
        m.run_uninstall()

        # Copied plain file with no backup is gone.
        assert not os.path.exists(copied_plain)
        # Copied-over-user file is restored to the user's original content.
        assert os.path.isfile(copied_over_user)
        with open(copied_over_user) as f:
            assert f.read() == "user original\n"
        assert not os.path.exists(copied_over_user + ".pre-plamen")
        # Manifest removed.
        assert not os.path.isfile(os.path.join(claude_home, m._PLAMEN_MANIFEST))


def test_uninstall_codex_only_removes_owned_trees_keeps_shared_config(monkeypatch):
    """Codex-only install (manifest under ~/.codex, none under ~/.claude) must
    NOT be a no-op: adapter-owned trees (agents/skills/commands) are removed,
    while shared config.toml / AGENTS.md (may hold user API keys/edits) are KEPT."""
    m = _load()
    with tempfile.TemporaryDirectory() as root:
        claude_home = os.path.join(root, ".claude")
        codex_home = os.path.join(root, ".codex")
        plamen_home = os.path.join(root, ".plamen")
        os.makedirs(claude_home)
        os.makedirs(codex_home)
        os.makedirs(plamen_home)
        monkeypatch.setattr(m, "CLAUDE_HOME", claude_home, raising=False)
        monkeypatch.setattr(m, "PLAMEN_HOME", plamen_home, raising=False)
        monkeypatch.setenv("HOME", root)
        monkeypatch.setenv("USERPROFILE", root)

        for tree in ("agents", "skills", "commands"):
            d = os.path.join(codex_home, tree)
            os.makedirs(d)
            with open(os.path.join(d, "x.toml"), "w") as f:
                f.write("x\n")
        config_toml = os.path.join(codex_home, "config.toml")
        agents_md = os.path.join(codex_home, "AGENTS.md")
        with open(config_toml, "w") as f:
            f.write('api_key = "USER-SECRET"\n')
        with open(agents_md, "w") as f:
            f.write("user agents\n")

        # codex manifest, NO claude manifest -> a codex-only install
        manifest = {"plamen_home": plamen_home, "version": m.VERSION,
                    "installed": [], "copied": [], "copied_dirs": [], "shims": []}
        with open(os.path.join(codex_home, m._PLAMEN_MANIFEST), "w") as f:
            json.dump(manifest, f)

        monkeypatch.setenv("PLAMEN_UNINSTALL_YES", "1")
        m.run_uninstall()

        for tree in ("agents", "skills", "commands"):
            assert not os.path.exists(os.path.join(codex_home, tree)), tree
        # Shared config PRESERVED — deleting it would be user-data loss.
        assert os.path.isfile(config_toml)
        with open(config_toml) as f:
            assert f.read() == 'api_key = "USER-SECRET"\n'
        assert os.path.isfile(agents_md)
        assert not os.path.isfile(os.path.join(codex_home, m._PLAMEN_MANIFEST))


def test_uninstall_removes_recorded_shims(monkeypatch):
    """python3 shims recorded in the manifest must be removed by uninstall."""
    m = _load()
    with tempfile.TemporaryDirectory() as root:
        claude_home = os.path.join(root, ".claude")
        plamen_home = os.path.join(root, ".plamen")
        os.makedirs(claude_home)
        os.makedirs(plamen_home)
        monkeypatch.setattr(m, "CLAUDE_HOME", claude_home, raising=False)
        monkeypatch.setattr(m, "PLAMEN_HOME", plamen_home, raising=False)
        monkeypatch.setenv("HOME", root)
        monkeypatch.setenv("USERPROFILE", root)

        shim = os.path.join(plamen_home, "python3.bat")
        with open(shim, "w") as f:
            f.write("@echo off\n")
        manifest = {"plamen_home": plamen_home, "version": m.VERSION,
                    "installed": [], "copied": [], "copied_dirs": [], "shims": [shim]}
        with open(os.path.join(claude_home, m._PLAMEN_MANIFEST), "w") as f:
            json.dump(manifest, f)

        monkeypatch.setenv("PLAMEN_UNINSTALL_YES", "1")
        m.run_uninstall()
        assert not os.path.exists(shim)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
