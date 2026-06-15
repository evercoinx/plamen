"""Regression: `_merge_settings_json()` must respect deliberate user removals
of Plamen-shipped allow/deny/env entries across a re-install/upgrade.

Before v2.1.0 the merge did an unconditional union:
    merged = list(dict.fromkeys(existing_list + plamen_list))
so if a user deliberately removed a Plamen-shipped permission (e.g. dropped
`Bash(*)` from allow, or a too-aggressive deny entry), the next
`plamen install`/upgrade silently re-added it. There was no marker
distinguishing Plamen-managed entries from user entries, so removals could not
be respected.

The fix records the Plamen-injected baseline under a `_plamenManaged` key in
settings.json (mirroring the managed-block marker approach used for CLAUDE.md).
On re-merge, only Plamen entries the user has NOT explicitly removed since the
last install are re-added; user-added entries are always preserved.
"""
import importlib.util
import json
import os
import sys
import tempfile

import pytest

_PLAMEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plamen.py")


def _load():
    spec = importlib.util.spec_from_file_location("plamen_mod_smerge", _PLAMEN)
    m = importlib.util.module_from_spec(spec)
    saved = sys.argv
    sys.argv = ["plamen.py"]
    try:
        spec.loader.exec_module(m)
    finally:
        sys.argv = saved
    return m


def _noop(_s):  # the `w` writer sink
    pass


def _run_merge(m, monkeypatch, home_dir, plamen_settings):
    """Point PLAMEN_HOME (example source) and CLAUDE_HOME (target) at temp dirs.

    Returns the parsed settings.json after the merge.
    """
    example_dir = os.path.join(home_dir, "plamen_home")
    target_dir = os.path.join(home_dir, "claude_home")
    os.makedirs(example_dir, exist_ok=True)
    os.makedirs(target_dir, exist_ok=True)
    with open(os.path.join(example_dir, "settings.json.example"), "w") as f:
        json.dump(plamen_settings, f)
    monkeypatch.setattr(m, "PLAMEN_HOME", example_dir, raising=False)
    monkeypatch.setattr(m, "CLAUDE_HOME", target_dir, raising=False)
    m._merge_settings_json(_noop)
    with open(os.path.join(target_dir, "settings.json")) as f:
        return json.load(f)


_PLAMEN_SETTINGS = {
    "env": {"PLAMEN_X": "1", "PLAMEN_Y": "2"},
    "permissions": {
        "allow": ["Bash(*)", "Read(*)"],
        "deny": ["Bash(rm -rf:*)"],
        "defaultMode": "acceptEdits",
    },
}


def _write_target(home_dir, settings):
    target_dir = os.path.join(home_dir, "claude_home")
    os.makedirs(target_dir, exist_ok=True)
    with open(os.path.join(target_dir, "settings.json"), "w") as f:
        json.dump(settings, f)


def test_fresh_install_adds_all_entries_and_records_baseline(monkeypatch):
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        out = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
    assert set(out["permissions"]["allow"]) == {"Bash(*)", "Read(*)"}
    assert out["permissions"]["deny"] == ["Bash(rm -rf:*)"]
    assert out["env"] == {"PLAMEN_X": "1", "PLAMEN_Y": "2"}
    # Baseline marker recorded for the next upgrade.
    assert out["_plamenManaged"]["permissions"]["allow"] == ["Bash(*)", "Read(*)"]
    assert out["_plamenManaged"]["permissions"]["deny"] == ["Bash(rm -rf:*)"]
    assert sorted(out["_plamenManaged"]["env"]) == ["PLAMEN_X", "PLAMEN_Y"]


def test_idempotent_reinstall_no_change(monkeypatch):
    """Re-running merge twice with the same shipment is a no-op (apart from
    deterministic baseline)."""
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        first = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
        # Re-run against the file the first merge produced.
        _write_target(d, first)
        second = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
    assert second["permissions"] == first["permissions"]
    assert second["env"] == first["env"]
    assert second["_plamenManaged"] == first["_plamenManaged"]


def test_user_removed_allow_entry_not_readded(monkeypatch):
    """User drops a Plamen-shipped allow entry; upgrade must NOT re-add it."""
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        first = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
        # User edits: removes Bash(*) from allow.
        first["permissions"]["allow"] = [
            e for e in first["permissions"]["allow"] if e != "Bash(*)"
        ]
        _write_target(d, first)
        # Upgrade re-runs the same shipment.
        out = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
    assert "Bash(*)" not in out["permissions"]["allow"], (
        "user-removed Plamen allow entry was silently re-added"
    )
    assert "Read(*)" in out["permissions"]["allow"]


def test_user_removed_deny_entry_not_readded(monkeypatch):
    """User finds a Plamen deny entry too aggressive and removes it; upgrade
    must NOT re-add it."""
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        first = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
        first["permissions"]["deny"] = []
        _write_target(d, first)
        out = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
    assert out["permissions"]["deny"] == [], (
        "user-removed Plamen deny entry was silently re-added"
    )


def test_user_removed_env_key_not_readded(monkeypatch):
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        first = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
        del first["env"]["PLAMEN_X"]
        _write_target(d, first)
        out = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
    assert "PLAMEN_X" not in out["env"], (
        "user-removed Plamen env key was silently re-added"
    )
    assert out["env"].get("PLAMEN_Y") == "2"


def test_user_added_entries_preserved(monkeypatch):
    """A permission the user added themselves is never touched."""
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        first = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
        first["permissions"]["allow"].append("WebFetch(*)")
        first["env"]["USER_OWN"] = "x"
        _write_target(d, first)
        out = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
    assert "WebFetch(*)" in out["permissions"]["allow"]
    assert out["env"].get("USER_OWN") == "x"


def test_new_shipment_entry_added_after_removal_of_other(monkeypatch):
    """An entry NEW in the shipment is added even if the user removed a
    DIFFERENT Plamen entry (removal tracking is per-entry, not global)."""
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        first = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
        # User removes Bash(*).
        first["permissions"]["allow"] = [
            e for e in first["permissions"]["allow"] if e != "Bash(*)"
        ]
        _write_target(d, first)
        # New shipment adds Edit(*) and keeps the rest.
        new_ship = json.loads(json.dumps(_PLAMEN_SETTINGS))
        new_ship["permissions"]["allow"].append("Edit(*)")
        out = _run_merge(m, monkeypatch, d, new_ship)
    assert "Bash(*)" not in out["permissions"]["allow"], "removal not respected"
    assert "Edit(*)" in out["permissions"]["allow"], "new shipment entry missing"
    assert "Read(*)" in out["permissions"]["allow"]


def test_legacy_settings_without_marker_treated_as_first_install(monkeypatch):
    """A pre-v2.1.0 settings.json (no _plamenManaged marker, entries already
    present from the old union code) must not lose its existing Plamen entries:
    with no baseline, nothing is treated as 'removed'."""
    m = _load()
    with tempfile.TemporaryDirectory() as d:
        legacy = {
            "env": {"PLAMEN_X": "1", "PLAMEN_Y": "2"},
            "permissions": {
                "allow": ["Bash(*)", "Read(*)"],
                "deny": ["Bash(rm -rf:*)"],
                "defaultMode": "acceptEdits",
            },
        }
        _write_target(d, legacy)
        out = _run_merge(m, monkeypatch, d, _PLAMEN_SETTINGS)
    assert set(out["permissions"]["allow"]) == {"Bash(*)", "Read(*)"}
    assert out["permissions"]["deny"] == ["Bash(rm -rf:*)"]
    assert "_plamenManaged" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
