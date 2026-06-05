"""Manual-halt-during-verify left a run unresumable: resume reported
"Config was lost and could not be reconstructed from checkpoint" even though
_v2_checkpoint.json held the full embedded config.

Root cause: _find_existing_audit's primary branch did `except Exception:
continue` on a config.json parse failure -> a TRUNCATED/corrupt config.json
(what a halt mid-write leaves) skipped the ENTIRE scratchpad candidate instead
of falling through to the checkpoint-reconstruction fallback right below it.
So the embedded config was never consulted.

Fix A: a corrupt config.json is treated like a missing one -> fall through to
       checkpoint recovery (which self-heals config.json).
Fix B/C: every config.json write (setup + reconstruction) is atomic temp+
       replace, so an interrupted write can never CREATE the corruption.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def _plamen():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return importlib.import_module("plamen")


_FULL_CONFIG = {
    "project_root": "X",
    "scratchpad": "X/.scratchpad",
    "mode": "thorough",
    "pipeline": "sc",
    "language": "evm",
    "cli_backend": "claude",
    "proven_only": False,
}


def _checkpoint_with_config(sp: Path, completed):
    (sp / "_v2_checkpoint.json").write_text(
        json.dumps({"completed": completed, "degraded": [],
                    "rate_limited_at": None, "config": _FULL_CONFIG}, indent=2),
        encoding="utf-8",
    )


def test_corrupt_config_recovers_from_checkpoint(tmp_path: Path):
    p = _plamen()
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    # Truncated config.json — exactly what a halt mid-write leaves.
    (sp / "config.json").write_text('{"project_root": "X", "mod', encoding="utf-8")
    _checkpoint_with_config(sp, ["recon", "instantiate", "breadth"])
    # a real artifact so the fallback's "has_checkpoint or artifacts" gate passes
    (sp / "findings_inventory.md").write_text("# inv\n", encoding="utf-8")

    info = p._find_existing_audit(str(tmp_path))
    assert info is not None, "corrupt config.json must NOT make the run invisible"
    assert not info.get("config_missing"), (
        "corrupt config.json with a config-bearing checkpoint must RECOVER, "
        "not report 'config lost'"
    )
    assert info["mode"] == "thorough" and info["pipeline"] == "sc"
    # config.json must be self-healed back to valid JSON...
    healed = json.loads((sp / "config.json").read_text(encoding="utf-8"))
    assert healed["mode"] == "thorough"
    # ...and the atomic reconstruct must leave no temp turd behind.
    assert not (sp / "config.json.tmp").exists()


def test_valid_config_still_used_directly(tmp_path: Path):
    p = _plamen()
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "config.json").write_text(json.dumps(_FULL_CONFIG), encoding="utf-8")
    _checkpoint_with_config(sp, ["recon"])
    info = p._find_existing_audit(str(tmp_path))
    assert info is not None and not info.get("config_missing")
    assert info["mode"] == "thorough"


def test_corrupt_config_no_checkpoint_is_honestly_missing(tmp_path: Path):
    # No checkpoint to recover from + corrupt config + an artifact -> the
    # honest config_missing path (NOT a silent skip, NOT a false recovery).
    p = _plamen()
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "config.json").write_text("{ truncated", encoding="utf-8")
    (sp / "findings_inventory.md").write_text("# inv\n", encoding="utf-8")
    info = p._find_existing_audit(str(tmp_path))
    assert info is not None and info.get("config_missing") is True


def test_setup_config_write_is_atomic_no_tmp_left(tmp_path: Path):
    # The setup writer must os.replace a temp file, leaving no .tmp residue.
    p = _plamen()
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    cfg_path = os.path.join(str(sp), "config.json")
    _tmp = cfg_path + ".tmp"
    with open(_tmp, "w", encoding="utf-8") as f:
        json.dump(_FULL_CONFIG, f, indent=2)
    os.replace(_tmp, cfg_path)
    assert os.path.isfile(cfg_path) and not os.path.isfile(_tmp)
    assert json.loads(Path(cfg_path).read_text(encoding="utf-8"))["mode"] == "thorough"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
