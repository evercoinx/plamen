"""Regression: the markdown block-separator glue bug that silently dropped
ENUMGAP candidates (INV-191/INV-195 class).

When a sibling deriver had already created the shared
'## Enumeration-Coverage Candidates (ENUMGAP)' section, the later append site
computed hdr=="" and did `inv_text.rstrip() + hdr + "\\n".join(appended)`. The
rstrip() removed the trailing newline of the prior block, so the first appended
'### Finding [..]' header concatenated directly onto the previous block's
'**Impact**:' line and became invisible to every `^### Finding` parser.

These tests pin the fix: `_append_inventory_blocks` inserts a blank-line
separator whenever hdr is empty, so every appended '### Finding' header is
line-anchored (matches `^### Finding`)."""
from __future__ import annotations

import importlib
import os
import re
import sys


def _eg():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("enumeration_gate")


def test_append_helper_inserts_separator_when_hdr_empty():
    eg = _eg()
    # Prior inventory ends with a finding block whose last line is **Impact**:.
    prior = (
        "# Finding Inventory\n\n"
        "## Enumeration-Coverage Candidates (ENUMGAP)\n\n"
        "### Finding [INV-190]: a prior gap\n"
        "**Severity**: Low\n"
        "**Impact**: some prior harm\n"
    )
    appended = [
        "### Finding [INV-191]: a glued-on gap",
        "**Severity**: Low",
        "**Impact**: the harm that must not vanish",
        "",
    ]
    out = eg._append_inventory_blocks(prior, "", appended)
    # The new header must be line-anchored, not glued to '**Impact**: some prior harm'.
    assert re.search(r"^### Finding \[INV-191\]", out, re.M), out
    assert "harm### Finding" not in out
    # Every '### Finding' header in the result is line-anchored.
    inline = [m.start() for m in re.finditer(r"(?<!\n)### Finding ", out)]
    assert inline == [], f"glued (non-line-anchored) headers at {inline}\n{out}"


def test_two_derivers_same_section_no_glue(tmp_path):
    """Two derivers appending to the SAME shared section in sequence — the
    second batch (hdr=="" because the section already exists) must not glue."""
    eg = _eg()
    inv = tmp_path / "findings_inventory.md"
    inv.write_text("# Finding Inventory\n\n### Finding [INV-001]: base\n"
                   "**Severity**: Low\n**Impact**: base harm\n", encoding="utf-8")

    def _cand(key, title):
        return {
            "key": key, "title": title, "location": "Vault.sol:L10",
            "source_note": "test", "root_cause": "rc",
            "description": "desc", "impact": "concrete harm",
        }

    # First deriver creates the section.
    assert eg._emit_candidates(tmp_path, [_cand("k1", "first")], cap=10) == 1
    # Second deriver appends to the now-existing section (hdr=="" path).
    assert eg._emit_candidates(tmp_path, [_cand("k2", "second")], cap=10) == 1

    text = inv.read_text(encoding="utf-8")
    # Both emitted findings plus the base finding must be line-anchored headers.
    anchored = re.findall(r"^### Finding \[(INV-\d+)\]", text, re.M)
    assert len(anchored) >= 3, f"expected >=3 anchored headers, got {anchored}\n{text}"
    # No header glued onto a previous line.
    glued = [m.start() for m in re.finditer(r"(?<!\n)### Finding ", text)]
    assert glued == [], f"glued headers at {glued}\n{text}"
