"""Regression: `_check_claude_md_version()` must actually fire on a stale install.

The documented "version mismatch" warning (README.md, docs/getting-started.md,
docs/updating.md) silently no-op'd: the old regex
`Security Auditor \\(v([0-9.]+)\\)` anchored to the version sitting on the
"Security Auditor" title line, but the injected CLAUDE.md header is
`# Plamen — Security Auditor` (no version) with the version on line 4 as
`...autonomous Web3 security auditing agent (v2.1.0).`. Against the real
injected layout the old regex returned None, so the check was a silent pass and
a user who pulled a new version but skipped `plamen install` got stale
orchestrator rules with zero warning.

These tests feed the REAL injected CLAUDE.md layout and assert the version is
extracted and the mismatch warning fires.
"""
import io
import os
import importlib.util
import sys
import tempfile

import pytest

_PLAMEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plamen.py")

# Long-form start marker that `plamen install` actually writes (see
# _CLAUDE_MD_START in plamen.py). The bare `<!-- PLAMEN:START -->` is never
# written by install.
_START = "<!-- PLAMEN:START — managed by plamen install, do not edit -->"
_END = "<!-- PLAMEN:END -->"


def _load():
    spec = importlib.util.spec_from_file_location("plamen_mod_vcheck", _PLAMEN)
    m = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = ["plamen.py"]
    try:
        spec.loader.exec_module(m)
    finally:
        sys.argv = saved
    return m


def _injected_claude_md(version: str) -> str:
    """Reproduce the real injected layout: long-form marker, the repo header
    whose version sits on line 4 (NOT on the Security Auditor title line),
    end marker."""
    return (
        "# Some user's personal global instructions\n\n"
        f"{_START}\n"
        "# Plamen — Security Auditor\n\n"
        "You are **Plamen**, an autonomous Web3 security auditing agent "
        f"(v{version}).\n"
        "Methodology files live under `~/.claude/rules/`.\n"
        f"{_END}\n"
    )


def _run_check(m, monkeypatch, claude_home_dir):
    """Point the module's CLAUDE_HOME at our temp dir and capture stdout."""
    monkeypatch.setattr(m, "CLAUDE_HOME", claude_home_dir, raising=False)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    try:
        m._check_claude_md_version()
    finally:
        monkeypatch.setattr(sys, "stdout", sys.__stdout__)
    return buf.getvalue()


def test_version_extracted_from_real_injected_layout(monkeypatch):
    """The version on line 4 (not the title line) must be extracted, so a
    mismatch with the repo VERSION produces the documented warning."""
    m = _load()
    # Force a deterministic repo version that differs from the injected one.
    monkeypatch.setattr(m, "VERSION", "9.9.9", raising=False)
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "CLAUDE.md"), "w", encoding="utf-8") as f:
            f.write(_injected_claude_md("2.1.0"))
        out = _run_check(m, monkeypatch, d)
    assert "Version mismatch" in out, (
        "warning must fire when injected version != repo VERSION; "
        f"got stdout={out!r}"
    )
    assert "v2.1.0" in out
    assert "v9.9.9" in out


def test_no_warning_when_versions_match(monkeypatch):
    """No false-positive warning when the injected version matches the repo."""
    m = _load()
    monkeypatch.setattr(m, "VERSION", "2.1.0", raising=False)
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "CLAUDE.md"), "w", encoding="utf-8") as f:
            f.write(_injected_claude_md("2.1.0"))
        out = _run_check(m, monkeypatch, d)
    assert "Version mismatch" not in out, f"unexpected warning: {out!r}"


def test_silent_when_not_installed(monkeypatch):
    """No CLAUDE.md on disk -> silent (not installed yet)."""
    m = _load()
    monkeypatch.setattr(m, "VERSION", "2.1.0", raising=False)
    with tempfile.TemporaryDirectory() as d:
        out = _run_check(m, monkeypatch, d)  # no CLAUDE.md written
    assert out == ""


def test_silent_when_no_injection(monkeypatch):
    """CLAUDE.md exists but has no Plamen marker block -> silent."""
    m = _load()
    monkeypatch.setattr(m, "VERSION", "2.1.0", raising=False)
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "CLAUDE.md"), "w", encoding="utf-8") as f:
            f.write("# Just the user's own notes, no plamen block\n")
        out = _run_check(m, monkeypatch, d)
    assert out == ""


def test_regex_matches_committed_repo_claude_md():
    """The committed ~/.plamen/CLAUDE.md (the file that gets injected verbatim)
    must contain an extractable (vX.Y.Z) tag, guarding against future header
    reformatting that would silently re-break the check."""
    import re
    repo_md = os.path.join(os.path.dirname(_PLAMEN), "CLAUDE.md")
    if not os.path.isfile(repo_md):
        pytest.skip("repo CLAUDE.md not present")
    content = open(repo_md, encoding="utf-8").read()
    assert re.search(r"\(v(\d+\.\d+\.\d+)\)", content), (
        "repo CLAUDE.md no longer carries a (vX.Y.Z) tag — the version check "
        "will silently no-op again"
    )
