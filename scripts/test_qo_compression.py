"""Fix A — Quality-Observations compression must not be vetoed by the data-loss
gate over NON-impact bullets.

Bug: `_dedup_data_loss_gate` scanned EVERY markdown bullet in the whole report,
but the QO retab (by design) compacts a cosmetic finding's Recommendation/
Evidence/Description bullets into a one-line table row — so those bullets were
counted as "lost" and the entire QO compression was dropped (42 Low/Info shipped
as full sections). Fix: `impact_only=True` scopes the bullet check to
`**Impact**` blocks at the QO pre-gate; the merge path keeps the strict check.
"""
from __future__ import annotations

import importlib
import os
import sys


def _mech():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_mechanical")


# ------------------------------------------------------------- gate scoping ---

def test_recommendation_bullet_not_lost_under_impact_only():
    m = _mech()
    original = (
        "### [L-01] Unused import\n\n**Impact**:\n- no runtime impact\n\n"
        "**Recommendation**:\n- remove the unused SafeMath import\n")
    # deduped keeps the impact bullet but drops the recommendation bullet
    deduped = "| L-01 | Unused import | Low | src/V.sol:L5 | unused_import | no runtime impact |\n"
    # full (strict) gate: the recommendation bullet is reported lost (the bug)
    assert any("remove the unused" in x for x in m._dedup_data_loss_gate(original, deduped))
    # impact_only gate: only the impact bullet matters -> no loss
    assert m._dedup_data_loss_gate(original, deduped, impact_only=True) == []


def test_real_impact_bullet_loss_still_caught_under_impact_only():
    m = _mech()
    original = "### [M-01] Bug\n\n**Impact**:\n- depositors lose 30% of principal\n"
    deduped = "| M-01 | Bug | Medium | x:L1 | - | something else |\n"  # impact gone
    lost = m._dedup_data_loss_gate(original, deduped, impact_only=True)
    assert any("depositors lose 30%" in x for x in lost)


def test_pipe_bullet_is_not_a_phantom_loss():
    m = _mech()
    original = "### [I-01] x\n\n**Impact**:\n- a | b boundary mismatch\n"
    # retab pipe-escapes the cell: `|` -> `/`
    deduped = "| I-01 | x | Info | f:L1 | magic_number | a / b boundary mismatch |\n"
    assert m._dedup_data_loss_gate(original, deduped, impact_only=True) == []


def test_impact_block_text_extracts_only_impact_bullets():
    m = _mech()
    txt = ("### [L-1] t\n\n**Impact**:\n- keep me\n\n**Recommendation**:\n- drop me\n\n"
           "### [L-2] u\n\n**Impact**:\n- keep me too\n\n**Evidence**:\n- drop me too\n")
    block = m._impact_block_text(txt)
    assert "keep me" in block and "keep me too" in block
    assert "drop me" not in block and "drop me too" not in block


# ----------------------------------------------------- end-to-end retab gate ---

def _report(*sections: str) -> str:
    return "# Report\n\n## Low Findings\n\n" + "\n".join(sections) + "\n"


def test_cosmetic_section_retabulates_and_passes_impact_only_gate():
    m = _mech()
    cosmetic = (
        "### [L-01] Unused import `SafeMath` in Vault\n\n"
        "**Severity**: Low\n**Location**: `src/Vault.sol:L5`\n\n"
        "**Impact**:\n- No runtime impact; dead import only grows bytecode.\n\n"
        "**Recommendation**:\n- Remove the unused `SafeMath` import.\n")
    original = _report(cosmetic)
    retab, qo_rows = m._reclassify_cosmetic_low_info_to_qo(original)
    assert qo_rows, "cosmetic unused-import Low should retabulate into a QO row"
    # the OLD (strict) gate would veto on the dropped Recommendation bullet;
    # the impact_only gate (Fix A) must pass so the compression actually lands.
    assert m._dedup_data_loss_gate(original, retab, impact_only=True) == []


def test_security_low_is_not_retabulated():
    m = _mech()
    real = (
        "### [L-02] Missing access control on `setFee`\n\n"
        "**Severity**: Low\n**Location**: `src/Vault.sol:L40`\n\n"
        "**Impact**:\n- Any caller can change the fee (missing access control).\n\n"
        "**Recommendation**:\n- Add onlyOwner.\n")
    original = _report(real)
    retab, qo_rows = m._reclassify_cosmetic_low_info_to_qo(original)
    # security-signal guard keeps it a full section (not a QO row)
    assert not any(r for r in qo_rows if "L-02" in str(r))
    assert "### [L-02]" in retab


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
