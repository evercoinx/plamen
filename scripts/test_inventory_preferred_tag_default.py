"""Inventory chunk gate: mechanically default a missing `Preferred Tag` instead
of burning a whole-chunk retry.

A not-yet-verified inventory finding is `[CODE-TRACE]` by definition, so a chunk
that omitted only that field should be auto-filled in place. Content fields
(Source IDs / Severity / Location / Description / Impact) have no safe default
and must still trigger the pervasive-drift retry.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _val():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_validators")


_FIELDS_NO_TAG = (
    "**Source IDs**: B1-{n}\n"
    "**Severity**: Medium\n"
    "**Location**: Contract.sol:L{n}0\n"
    "**Verdict**: CONFIRMED\n"
    "**Root Cause**: root cause text {n}\n"
    "**Description**: description text {n}\n"
    "**Impact**: impact text {n}\n"
)


def _chunk(n_blocks: int, *, with_tag: bool = False, drop_location: bool = False) -> str:
    out = ["# Inventory Chunk\n\n## Per-Finding Detail\n"]
    for i in range(1, n_blocks + 1):
        fields = _FIELDS_NO_TAG.format(n=i)
        if drop_location:
            fields = "\n".join(
                ln for ln in fields.splitlines() if not ln.startswith("**Location**")
            ) + "\n"
        if with_tag:
            fields += "**Preferred Tag**: [POC-PASS]\n"
        out.append(f"\n### [CC-{i}]: Finding {i}\n{fields}")
    return "".join(out)


def _write(tmp_path: Path, text: str, phase: str = "inventory_chunk_b") -> Path:
    p = tmp_path / f"findings_{phase}.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_preferred_tag_is_defaulted_not_retried(tmp_path):
    v = _val()
    f = _write(tmp_path, _chunk(6))  # all 6 missing Preferred Tag
    issues = v._validate_inventory_chunk_structure(tmp_path, "inventory_chunk_b")
    # No pervasive retry for Preferred Tag — it was filled in place.
    assert not any("Preferred Tag (" in i for i in issues), issues
    body = f.read_text(encoding="utf-8")
    assert body.count("**Preferred Tag**: [CODE-TRACE]") == 6, body


def test_non_defaultable_field_still_retries(tmp_path):
    v = _val()
    _write(tmp_path, _chunk(6, drop_location=True))
    issues = v._validate_inventory_chunk_structure(tmp_path, "inventory_chunk_b")
    assert any("Location (" in i for i in issues), issues


def test_idempotent_no_double_insert(tmp_path):
    v = _val()
    f = _write(tmp_path, _chunk(6))
    v._validate_inventory_chunk_structure(tmp_path, "inventory_chunk_b")
    issues2 = v._validate_inventory_chunk_structure(tmp_path, "inventory_chunk_b")
    assert not any("Preferred Tag (" in i for i in issues2), issues2
    assert f.read_text(encoding="utf-8").count("**Preferred Tag**: [CODE-TRACE]") == 6


def test_existing_tag_untouched(tmp_path):
    v = _val()
    f = _write(tmp_path, _chunk(6, with_tag=True))
    issues = v._validate_inventory_chunk_structure(tmp_path, "inventory_chunk_b")
    assert not any("Preferred Tag (" in i for i in issues), issues
    body = f.read_text(encoding="utf-8")
    assert body.count("[CODE-TRACE]") == 0       # nothing defaulted
    assert body.count("**Preferred Tag**: [POC-PASS]") == 6


def test_mixed_tag_defaulted_location_retries(tmp_path):
    v = _val()
    _write(tmp_path, _chunk(6, drop_location=True))  # missing tag AND location
    issues = v._validate_inventory_chunk_structure(tmp_path, "inventory_chunk_b")
    assert not any("Preferred Tag (" in i for i in issues), issues   # tag filled
    assert any("Location (" in i for i in issues), issues  # location retries


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
