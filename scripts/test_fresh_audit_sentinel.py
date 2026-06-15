"""Tests for Ship 8.1: the fresh-audit marker sentinel writer.

`scratchpad_is_fresh_audit()` (the reader, plamen_validators) gates the
strict marker contract. Ship 8 added the reader and gates but no WRITER,
so production audits never planted the sentinel and the strict gates
stayed inert. Ship 8.1 adds `_ensure_fresh_audit_sentinel` in
plamen_driver. These tests pin its behavior:

  - brand-new audit (no checkpoint, no sentinel) -> writes sentinel
  - resume of a pre-marker audit (checkpoint exists, no sentinel) ->
    does NOT write (stays legacy)
  - existing sentinel -> idempotent, left untouched
  - a fresh run that wrote the sentinel makes scratchpad_is_fresh_audit
    return True (writer <-> reader round-trip)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_validators import scratchpad_is_fresh_audit  # noqa: E402

_SENTINEL = "_audit_started_with_markers.json"
_CHECKPOINT = "_v2_checkpoint.json"
_CONFIG = {"mode": "thorough", "pipeline": "sc"}


# ---------------------------------------------------------------------------
# brand-new audit -> writes sentinel
# ---------------------------------------------------------------------------


def test_fresh_startup_writes_sentinel(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    # Brand-new: no checkpoint, no sentinel.
    state = D._ensure_fresh_audit_sentinel(sp, _CONFIG)
    assert state == "written"
    sentinel = sp / _SENTINEL
    assert sentinel.exists()
    payload = json.loads(sentinel.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["mode"] == "thorough"
    assert payload["pipeline"] == "sc"
    assert "started_at" in payload and payload["started_at"].endswith("Z")
    assert "driver_version" in payload


# ---------------------------------------------------------------------------
# resume of a pre-marker audit -> does NOT write
# ---------------------------------------------------------------------------


def test_resume_pre_marker_audit_does_not_write_sentinel(tmp_path: Path):
    """A scratchpad with an existing `_v2_checkpoint.json` but NO
    sentinel is a resume of an audit started before the marker contract.
    The writer MUST NOT plant the sentinel -- doing so would retroactively
    flip an in-flight audit into strict mode and start blocking its
    already-written unmarked artifacts."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    # Simulate a prior run's checkpoint.
    (sp / _CHECKPOINT).write_text('{"completed": ["recon"]}', encoding="utf-8")
    state = D._ensure_fresh_audit_sentinel(sp, _CONFIG)
    assert state == "legacy-skip"
    assert not (sp / _SENTINEL).exists()
    # And the reader confirms legacy mode.
    assert scratchpad_is_fresh_audit(sp) is False


# ---------------------------------------------------------------------------
# existing sentinel -> idempotent
# ---------------------------------------------------------------------------


def test_existing_sentinel_is_idempotent(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    original = json.dumps(
        {
            "schema_version": 1,
            "started_at": "2026-05-01T00:00:00Z",
            "driver_version": "2.0.0",
            "mode": "core",
            "pipeline": "sc",
        },
        indent=2,
    )
    (sp / _SENTINEL).write_text(original, encoding="utf-8")
    # Even with a checkpoint also present (post-marker resume), the
    # existing sentinel wins and is left byte-untouched.
    (sp / _CHECKPOINT).write_text('{"completed": []}', encoding="utf-8")

    state = D._ensure_fresh_audit_sentinel(sp, _CONFIG)
    assert state == "exists"
    assert (sp / _SENTINEL).read_text(encoding="utf-8") == original


def test_post_marker_resume_keeps_fresh_mode(tmp_path: Path):
    """A resume where the prior (post-marker) run already planted the
    sentinel: checkpoint AND sentinel both present -> idempotent 'exists'
    and the audit STAYS in fresh/strict mode."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    (sp / _SENTINEL).write_text('{"schema_version": 1}', encoding="utf-8")
    (sp / _CHECKPOINT).write_text('{"completed": ["recon", "breadth"]}', encoding="utf-8")
    state = D._ensure_fresh_audit_sentinel(sp, _CONFIG)
    assert state == "exists"
    assert scratchpad_is_fresh_audit(sp) is True


# ---------------------------------------------------------------------------
# writer <-> reader round-trip
# ---------------------------------------------------------------------------


def test_fresh_run_with_sentinel_makes_reader_true(tmp_path: Path):
    """The whole point: after the writer plants the sentinel on a
    brand-new audit, scratchpad_is_fresh_audit() returns True so the
    strict marker gates activate."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)
    assert scratchpad_is_fresh_audit(sp) is False  # before
    D._ensure_fresh_audit_sentinel(sp, _CONFIG)
    assert scratchpad_is_fresh_audit(sp) is True  # after


# ---------------------------------------------------------------------------
# hard-error contract: brand-new write failure propagates
# ---------------------------------------------------------------------------


def test_brand_new_write_failure_propagates(tmp_path: Path, monkeypatch):
    """A write failure on a brand-new audit MUST raise (so main() can
    hard-exit). The writer deliberately has no try/except around the
    write."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True)

    real_write_text = Path.write_text

    def _boom(self, *args, **kwargs):
        if self.name == _SENTINEL:
            raise OSError("disk full (simulated)")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _boom)
    try:
        raised = False
        try:
            D._ensure_fresh_audit_sentinel(sp, _CONFIG)
        except OSError:
            raised = True
        assert raised, "brand-new sentinel write failure must propagate"
    finally:
        monkeypatch.setattr(Path, "write_text", real_write_text)
    # No partial sentinel left behind.
    assert not (sp / _SENTINEL).exists()
