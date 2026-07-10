"""FIX 1 (attribution) — provenance-preserving dedup merge + attribution ledger.

Root cause 1 (attribution unmeasurable):
because (a) the multi-axis promoter stamped `AXIS-101` (not a clean greppable
class token) and (b) when semantic dedup merged an AXIS/CI finding INTO a normal
twin, the survivor kept only the NORMAL source and the "also independently found
by M2/M1" provenance was DROPPED.

These tests prove:
  - the M2 axis promoter stamps a clean `AXISGAP:` class token;
  - the M1 committed-invariant emitter stamps a clean `INVARIANT:CI-n` token;
  - the dedup MERGE path APPENDS an absorbed finding's generator provenance to
    the survivor's Source IDs (never drops it);
  - `mechanism_attribution.md` lists both the normal and the generator token for
    the merged survivor.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _mod(name: str):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module(name)


def _write_inventory_with_twin(scratchpad: Path) -> Path:
    """A normal (TF-4) finding and an AXIS finding on the SAME location."""
    inv = scratchpad / "findings_inventory.md"
    inv.write_text(
        "# Findings Inventory\n\n"
        "### Finding [INV-001]: Public withdraw drains the vault\n"
        "**Severity**: Medium\n"
        "**Location**: src/Vault.sol:L100-L120\n"
        "**Preferred Tag**: [CODE-TRACE]\n"
        "**Source IDs**: TF-4\n"
        "**Verdict**: CONFIRMED\n"
        "**Description**: withdraw() is externally callable with no owner gate.\n"
        "**Impact**: Anyone drains the vault.\n\n"
        "### Finding [INV-002]: Public withdraw drains the vault (axis)\n"
        "**Severity**: Medium\n"
        "**Location**: src/Vault.sol:L100-L120\n"
        "**Preferred Tag**: [CODE-TRACE]\n"
        "**Source IDs**: AXISGAP:AXIS-101 (multi-axis coverage meta-pass; a "
        "mechanically-hot function was interrogated on a previously-unexamined "
        "risk axis — verifier to confirm or refute)\n"
        "**Verdict**: NEEDS_VERIFICATION\n"
        "**Description**: withdraw() lacks an access-control axis check.\n"
        "**Impact**: Verifier to confirm the concrete harm.\n\n",
        encoding="utf-8",
    )
    return inv


def test_axis_absorbed_into_normal_survivor_preserves_axisgap(tmp_path: Path):
    pm = _mod("plamen_mechanical")
    scratchpad = tmp_path
    inv = _write_inventory_with_twin(scratchpad)

    finfo = pm._dedup_parse_finding_info(inv.read_text(encoding="utf-8"))
    # Absorb the AXIS finding (INV-002) INTO the normal twin (INV-001).
    merges = [("INV-002", "INV-001", "same-site-test")]
    pm._apply_merges_to_inventory(inv, inv, merges, finfo)

    text = inv.read_text(encoding="utf-8")

    # Survivor block only.
    assert "[INV-001]" in text
    assert "[INV-002]:" not in text  # absorbed block removed
    # The survivor's Source IDs must carry BOTH the normal token AND a clean
    # AXISGAP: class token (provenance-preserving merge).
    src_lines = [
        ln for ln in text.splitlines()
        if ln.lstrip().lower().startswith("**source ids**")
    ]
    assert src_lines, "survivor has no Source IDs line"
    joined = " ".join(src_lines)
    assert "TF-4" in joined, f"normal token lost: {joined!r}"
    assert "AXISGAP:" in joined, f"generator provenance dropped on merge: {joined!r}"


def test_attribution_ledger_lists_both_tokens(tmp_path: Path):
    pm = _mod("plamen_mechanical")
    scratchpad = tmp_path
    inv = _write_inventory_with_twin(scratchpad)

    finfo = pm._dedup_parse_finding_info(inv.read_text(encoding="utf-8"))
    pm._apply_merges_to_inventory(
        inv, inv, [("INV-002", "INV-001", "same-site-test")], finfo
    )

    res = pm.write_mechanism_attribution_ledger(scratchpad)
    assert res["rows"] == 1, res
    assert res["axisgap"] == 1, res

    ledger = (scratchpad / "mechanism_attribution.md").read_text(encoding="utf-8")
    # The merged survivor row lists both provenance tokens.
    row = [ln for ln in ledger.splitlines() if "INV-001" in ln]
    assert row, "INV-001 missing from ledger"
    assert "TF-4" in row[0], row[0]
    assert "AXISGAP:" in row[0], row[0]
    assert "AXISGAP" in row[0].split("|")[-2], "Generator Provenance column missing AXISGAP"


def test_reverse_direction_also_preserves_both(tmp_path: Path):
    """If the AXIS finding is the SURVIVOR and the normal twin is absorbed, the
    survivor must still end up with BOTH the AXISGAP token and the normal one."""
    pm = _mod("plamen_mechanical")
    scratchpad = tmp_path
    inv = _write_inventory_with_twin(scratchpad)

    finfo = pm._dedup_parse_finding_info(inv.read_text(encoding="utf-8"))
    pm._apply_merges_to_inventory(
        inv, inv, [("INV-001", "INV-002", "same-site-test")], finfo
    )
    text = inv.read_text(encoding="utf-8")
    assert "[INV-002]" in text
    src = " ".join(
        ln for ln in text.splitlines()
        if ln.lstrip().lower().startswith("**source ids**")
    )
    assert "AXISGAP:" in src, src
    assert "TF-4" in src, src


def test_provenance_class_tokens_helper():
    pm = _mod("plamen_mechanical")
    toks = pm._provenance_class_tokens(
        "TF-4, AXISGAP:AXIS-102 (co-found), INVARIANT:CI-3 (falsify)"
    )
    assert toks == ["AXISGAP:AXIS-102", "INVARIANT:CI-3"], toks
    # No class tokens -> empty.
    assert pm._provenance_class_tokens("TF-4, CS-1") == []


def test_axis_promoter_stamps_clean_axisgap_token(tmp_path: Path):
    """M2: promote_axis_findings_to_inventory stamps `AXISGAP:` not bare AXIS-."""
    eg = _mod("enumeration_gate")
    scratchpad = tmp_path
    (scratchpad / "findings_inventory.md").write_text(
        "# Findings Inventory\n\n### Finding [INV-005]: seed\n"
        "**Severity**: Low\n**Location**: a.sol:L1\n"
        "**Source IDs**: CS-1\n**Verdict**: CONFIRMED\n"
        "**Description**: seed\n**Impact**: seed\n\n",
        encoding="utf-8",
    )
    (scratchpad / "axis_coverage_findings.md").write_text(
        "### Finding [AXIS-101]: withdraw() unexamined on access axis\n"
        "**Severity**: Medium\n"
        "**Location**: src/Vault.sol:L100\n"
        "**Preferred Tag**: [CODE-TRACE]\n"
        "**Verdict**: NEEDS_VERIFICATION\n"
        "**Description**: the access-control axis was never examined here.\n"
        "**Impact**: potential unauthorized withdraw.\n\n",
        encoding="utf-8",
    )
    res = eg.promote_axis_findings_to_inventory(scratchpad)
    assert res["emitted"] == 1, res
    inv_text = (scratchpad / "findings_inventory.md").read_text(encoding="utf-8")
    assert "AXISGAP:AXIS-101" in inv_text, inv_text


def test_invariant_emitter_stamps_clean_invariant_token(tmp_path: Path):
    """M1: committed-invariant candidates carry a clean `INVARIANT:CI-n` token."""
    eg = _mod("enumeration_gate")
    scratchpad = tmp_path
    cand = {
        "key": "INVARIANT:skeptic.md:CI-3",
        "source_tag": "INVARIANT:CI-3",
        "title": "Committed invariant CI-3 — falsify",
        "location": "src/Vault.sol:L50",
        "source_note": "CI-3; committed-invariant assertion; Falsify Class: property",
        "root_cause": "guard asserted, never falsified",
        "description": "falsify the committed invariant CI-3",
        "impact": "if the guard fails, the safe verdict is wrong",
    }
    (scratchpad / "findings_inventory.md").write_text(
        "# Findings Inventory\n\n", encoding="utf-8"
    )
    n = eg._emit_candidates(scratchpad, [cand], 15, source_id="INVARIANT")
    assert n == 1, n
    inv_text = (scratchpad / "findings_inventory.md").read_text(encoding="utf-8")
    assert "INVARIANT:CI-3" in inv_text, inv_text


if __name__ == "__main__":
    import tempfile

    fails = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            if fn.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if fails else 0)
