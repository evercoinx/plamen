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


def test_pretrust_clears_global_first_run_gates(tmp_path, monkeypatch):
    """Beyond per-folder trust, the global onboarding/theme wizard and the
    one-time --dangerously-skip-permissions acceptance are cleared so a fresh
    `claude` install does not freeze a PTY worker (incidents A and C)."""
    _home(monkeypatch, tmp_path)
    D._ensure_claude_folder_trusted(str(tmp_path / "harness"))
    data = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert data["hasCompletedOnboarding"] is True
    assert data["theme"] == "dark"           # default set when user has none
    assert data["bypassPermissionsModeAccepted"] is True


def test_pretrust_does_not_override_existing_theme(tmp_path, monkeypatch):
    """A user's existing theme is NEVER overridden."""
    _home(monkeypatch, tmp_path)
    cj = tmp_path / ".claude.json"
    cj.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    D._ensure_claude_folder_trusted(str(tmp_path / "harness"))
    data = json.loads(cj.read_text(encoding="utf-8"))
    assert data["theme"] == "light"          # preserved, not clobbered
    assert data["hasCompletedOnboarding"] is True


def test_pretrust_global_gates_idempotent(tmp_path, monkeypatch):
    """Once the global gates are set, a re-run reports nothing new from them."""
    _home(monkeypatch, tmp_path)
    cj = tmp_path / ".claude.json"
    cj.write_text(json.dumps({
        "hasCompletedOnboarding": True,
        "theme": "dark",
        "bypassPermissionsModeAccepted": True,
        "projects": {},
    }), encoding="utf-8")
    proj = str(tmp_path / "harness")
    newly = D._ensure_claude_folder_trusted(proj)
    # only the fresh folder is newly trusted; the global gates were already set
    assert "hasCompletedOnboarding" not in newly
    assert "bypassPermissionsModeAccepted" not in newly
    assert D._ensure_claude_folder_trusted(proj) == []   # fully idempotent


def test_pretrust_writes_skip_dangerous_mode_prompt_to_settings(tmp_path, monkeypatch):
    """AUTHORITATIVE bypass-dialog suppression: `skipDangerousModePermissionPrompt`
    lands in ~/.claude/settings.json (a DIFFERENT file from ~/.claude.json). This
    is the real CLI gate (anthropics/claude-code#25503); without it a PTY worker
    hangs on the dangerous-mode dialog. Pre-existing settings are preserved."""
    _home(monkeypatch, tmp_path)
    sj = tmp_path / ".claude" / "settings.json"
    sj.parent.mkdir(parents=True, exist_ok=True)
    sj.write_text(json.dumps({"env": {"KEEP": "1"}}), encoding="utf-8")

    newly = D._ensure_claude_folder_trusted(str(tmp_path / "harness"))
    assert "skipDangerousModePermissionPrompt" in newly
    sdata = json.loads(sj.read_text(encoding="utf-8"))
    assert sdata["skipDangerousModePermissionPrompt"] is True
    assert sdata["env"] == {"KEEP": "1"}          # existing settings preserved
    # idempotent: re-run reports nothing new
    assert D._ensure_claude_folder_trusted(str(tmp_path / "harness")) == []


def test_pretrust_creates_settings_when_absent(tmp_path, monkeypatch):
    """settings.json is created (with parent dir) when it does not yet exist."""
    _home(monkeypatch, tmp_path)
    D._ensure_claude_folder_trusted(str(tmp_path / "harness"))
    sdata = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert sdata["skipDangerousModePermissionPrompt"] is True


def test_pretrust_never_clobbers_unreadable_settings(tmp_path, monkeypatch):
    """A corrupt settings.json is left untouched (better to risk the prompt than
    to destroy the user's settings) and the call still completes folder trust."""
    _home(monkeypatch, tmp_path)
    sj = tmp_path / ".claude" / "settings.json"
    sj.parent.mkdir(parents=True, exist_ok=True)
    sj.write_text("{ not valid json", encoding="utf-8")
    newly = D._ensure_claude_folder_trusted(str(tmp_path / "harness"))
    assert "skipDangerousModePermissionPrompt" not in newly   # not written
    assert sj.read_text(encoding="utf-8") == "{ not valid json"  # preserved
    # folder trust still recorded despite the unreadable settings file
    data = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    fkey = str(pathlib.Path(str(tmp_path / "harness")).resolve()).replace("\\", "/")
    assert data["projects"][fkey]["hasTrustDialogAccepted"] is True


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
