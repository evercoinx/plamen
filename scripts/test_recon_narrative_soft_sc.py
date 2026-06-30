"""Item 5: soften the recon NARRATIVE gate while keeping the mechanical
inventory gate hard.

The mechanical recon pre-pass writes the authoritative full enumeration
(function_list.md / state_variables.md / contract_inventory.md). Once those
three are complete (marker-free + a substantive enumeration body), the three
NARRATIVE-only checks — design_context Operational Implications, design_context
Key Invariants, and recon_summary "too small" — become non-blocking soft
warnings so a large-codebase recon that completed its authoritative inventory
no longer HALTS on missing narrative prose.

Recall-safety guard: when the mechanical files are still pre-pass-marker-stamped
OR absent OR placeholder-only, the narrative checks stay HARD exactly as before,
so a genuinely-empty recon can never be masked.

All fixtures are synthetic/neutral (ExampleVault / ExampleToken).
"""
from __future__ import annotations

from pathlib import Path

from plamen_mechanical import _PREPASS_MARKER
from plamen_validators import _validate_recon_content_structure


# A substantive, marker-free mechanical enumeration body (a real table with a
# data row) — what the mechanical pre-pass produces and the merge promotes.
_REAL_FUNCS = (
    "# Function List\n\n"
    "Pre-pass: 2 function(s) identified via regex scan.\n\n"
    "| File | Function | Visibility | Line |\n"
    "|------|----------|------------|------|\n"
    "| ExampleVault.sol | deposit | external | 20 |\n"
    "| ExampleVault.sol | withdraw | external | 40 |\n"
)
_REAL_STATEVARS = (
    "# State Variables\n\n"
    "Pre-pass: 2 state(s) identified via regex scan.\n\n"
    "| File | Variable | Type | Line |\n"
    "|------|----------|------|------|\n"
    "| ExampleVault.sol | totalAssets | uint256 | 12 |\n"
    "| ExampleVault.sol | owner | address | 14 |\n"
)
_REAL_CONTRACTS = (
    "# Contract Inventory\n\n"
    "Pre-pass: 1 contract(s) identified via regex scan.\n\n"
    "| Contract | File | Lines |\n"
    "|----------|------|-------|\n"
    "| ExampleVault | ExampleVault.sol | 120 |\n"
)


def _seed_complete_mechanical(scratch: Path) -> None:
    """Write all three canonical inventory files marker-free with real rows."""
    (scratch / "function_list.md").write_text(_REAL_FUNCS, encoding="utf-8")
    (scratch / "state_variables.md").write_text(_REAL_STATEVARS, encoding="utf-8")
    (scratch / "contract_inventory.md").write_text(
        _REAL_CONTRACTS, encoding="utf-8"
    )


def _write_narrative_gaps(scratch: Path) -> None:
    """design_context missing Operational Implications + a too-small
    recon_summary (both narrative gaps)."""
    # Has Key Invariants, NO Operational Implications.
    (scratch / "design_context.md").write_text(
        "# Design Context\n\n## Key Invariants\n"
        "totalAssets == sum(user balances).\n",
        encoding="utf-8",
    )
    # Below the 512-byte (claude) handoff minimum.
    (scratch / "recon_summary.md").write_text(
        "# Recon Summary\n\nbrief.\n", encoding="utf-8"
    )


def test_complete_mechanical_softens_narrative_gaps(tmp_path):
    """Marker-clean complete mechanical files + missing Operational
    Implications + a too-small recon_summary => the narrative checks are SOFT
    (hard == [] so the phase degrades-continues), and they surface as soft
    warnings instead."""
    _seed_complete_mechanical(tmp_path)
    _write_narrative_gaps(tmp_path)

    hard, soft = _validate_recon_content_structure(tmp_path)

    assert hard == [], hard
    # The narrative gaps are still surfaced — just non-blocking.
    assert any("Operational Implications" in s for s in soft), soft
    assert any("too small" in s for s in soft), soft
    # And they carry the soft-routing provenance suffix.
    assert any("mechanical inventory complete" in s for s in soft), soft


def test_marker_stamped_mechanical_keeps_narrative_hard(tmp_path):
    """Mechanical files still pre-pass-marker-stamped (function_list) OR absent
    (state_variables / contract_inventory) + the SAME narrative gaps => the
    narrative checks remain HARD (no softening when the authoritative inventory
    is not yet durable)."""
    # function_list marker-stamped; state_variables / contract_inventory absent.
    (tmp_path / "function_list.md").write_text(
        _PREPASS_MARKER + "\n" + _REAL_FUNCS, encoding="utf-8"
    )
    _write_narrative_gaps(tmp_path)

    hard, _soft = _validate_recon_content_structure(tmp_path)

    # Missing Operational Implications stays HARD.
    assert any("Operational Implications" in h for h in hard), hard
    # recon_summary too-small stays HARD.
    assert any("too small" in h for h in hard), hard
    # The surviving marker is also a hard failure (kept unconditionally).
    assert any("pre-pass overwrite marker" in h for h in hard), hard


def test_empty_placeholder_recon_keeps_narrative_hard(tmp_path):
    """A genuinely-empty recon — all three mechanical files marker-stamped with
    pure [LLM TO ENRICH] placeholder bodies — must NOT be softened. The
    narrative gaps remain HARD so an empty recon can never be masked."""
    placeholder = (
        _PREPASS_MARKER + "\n# Inventory\n\n[LLM TO ENRICH] pre-pass failed\n"
    )
    for name in ("function_list.md", "state_variables.md", "contract_inventory.md"):
        (tmp_path / name).write_text(placeholder, encoding="utf-8")
    _write_narrative_gaps(tmp_path)

    hard, _soft = _validate_recon_content_structure(tmp_path)

    assert any("Operational Implications" in h for h in hard), hard
    assert any("too small" in h for h in hard), hard


def test_missing_key_invariants_softened_when_mechanical_complete(tmp_path):
    """Symmetric to the Operational-Implications case: a missing Key Invariants
    section is ALSO softened when the mechanical inventory is complete."""
    _seed_complete_mechanical(tmp_path)
    # Has Operational Implications, NO Key Invariants. recon_summary clears min.
    (tmp_path / "design_context.md").write_text(
        "# Design Context\n\n## Operational Implications\n"
        "Deposits and withdrawals keep share accounting consistent.\n",
        encoding="utf-8",
    )
    (tmp_path / "recon_summary.md").write_text(
        "# Recon Summary\n\n## Protocol overview\n"
        + ("This ExampleVault holds depositor assets and mints shares; the "
           "attack surface centers on deposit/withdraw accounting. ") * 6
        + "\n",
        encoding="utf-8",
    )

    hard, soft = _validate_recon_content_structure(tmp_path)

    assert hard == [], hard
    assert any("Invariant" in s for s in soft), soft
