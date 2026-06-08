"""Recall-safe gating for Light mode + NON_EVM_TARGET CROSS_VM recovery.

FIX 1 — Light breadth-floor mode-gating: the Complex-tier breadth floor (>=7)
is a Core/Thorough recall protection. Light deliberately runs 3-4 breadth
agents (AUDIT MODES table), so the floor must NOT false-fail a correctly-sized
Light manifest. Core/Thorough keep the floor unchanged.

FIX 2 — CROSS_VM_SERIALIZATION_CONFORMANCE is an EVM-SIDE skill (it audits
Solidity that serializes OUTBOUND for a non-EVM VM, e.g. an AccountEncoder/Borsh
packer in a bridge — the DODO gap it was built for). It must fire on an EXPLICIT
EVM audit (language='evm') that has non-EVM-target evidence, and must NOT fire on
a NATIVE non-EVM audit (solana/aptos/sui/soroban) — no EVM-side serialization
there — nor on legacy/unknown ('') runs we cannot confirm are EVM. The
detected_patterns.md NON_EVM_TARGET flag is authoritative (the evidence guard,
not a language exclusion, prevents a pure-EVM-no-bridge false-positive);
template_recommendations.md (which documents the
trigger pattern) is excluded from the substring heuristic. Recovery warnings
are deduplicated to at most once per run via a scratchpad marker.
"""
from __future__ import annotations

import logging
from pathlib import Path

import plamen_validators as V
import plamen_driver as D


_HEADER = (
    "| Row Type | Template | Required? | Agent ID | Focus Area | "
    "Expected Output | Status |\n"
    "|----------|----------|-----------|----------|------------|"
    "-----------------|--------|\n"
)


def _row(template: str, agent_id: str, focus: str, out: str) -> str:
    return (f"| AGENT | {template} | YES | {agent_id} | {focus} | "
            f"{out} | QUEUED |\n")


def _write_manifest(sp: Path, rows: str) -> None:
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "spawn_manifest.md").write_text(
        "# Spawn Manifest\n\n" + _HEADER + rows, encoding="utf-8"
    )


def _complex_inventory(sp: Path) -> None:
    # >10 in-scope contracts -> _codebase_is_complex True
    lines = ["# Contract Inventory\n",
             "| Contract | Path | Lines | In Scope |\n",
             "|---|---|---|---|\n"]
    for i in range(12):
        lines.append(f"| C{i} | src/C{i}.sol | 200 | YES |\n")
    (sp / "contract_inventory.md").write_text("".join(lines), encoding="utf-8")


def _four_real_breadth_rows() -> str:
    # 4 real, resolvable skills (no floor-fill fabrication concerns).
    return "".join([
        _row("ORACLE_ANALYSIS", "B1", "oracle", "analysis_oracle.md"),
        _row("ECONOMIC_DESIGN_AUDIT", "B2", "econ", "analysis_econ.md"),
        _row("SEMI_TRUSTED_ROLES", "B3", "roles", "analysis_roles.md"),
        _row("CENTRALIZATION_RISK", "B4", "central", "analysis_central.md"),
    ])


# ---------------- FIX 1: Light breadth-floor mode-gating ----------------

def test_light_complex_4_breadth_passes_no_floor(tmp_path):
    """Light + Complex with 4 breadth agents -> floor NOT enforced."""
    _complex_inventory(tmp_path)
    _write_manifest(tmp_path, _four_real_breadth_rows())
    issues = V._validate_spawn_manifest_schema(tmp_path, mode="light")
    assert not any("breadth tier floor" in i for i in issues), issues


def test_core_complex_4_breadth_still_requires_floor(tmp_path):
    """Core + Complex with 4 breadth agents -> floor STILL enforced (>=7)."""
    _complex_inventory(tmp_path)
    _write_manifest(tmp_path, _four_real_breadth_rows())
    issues = V._validate_spawn_manifest_schema(tmp_path, mode="core")
    assert any("breadth tier floor" in i for i in issues), issues


def test_thorough_complex_4_breadth_still_requires_floor(tmp_path):
    """Thorough + Complex with 4 breadth agents -> floor STILL enforced."""
    _complex_inventory(tmp_path)
    _write_manifest(tmp_path, _four_real_breadth_rows())
    issues = V._validate_spawn_manifest_schema(tmp_path, mode="thorough")
    assert any("breadth tier floor" in i for i in issues), issues


def test_default_mode_core_preserves_floor(tmp_path):
    """Back-compat: default mode is 'core', so legacy callers keep the floor."""
    _complex_inventory(tmp_path)
    _write_manifest(tmp_path, _four_real_breadth_rows())
    issues = V._validate_spawn_manifest_schema(tmp_path)
    assert any("breadth tier floor" in i for i in issues), issues


def test_light_non_complex_4_breadth_no_floor(tmp_path):
    """Light + non-Complex -> no floor regardless (simple codebase)."""
    # No contract_inventory.md -> not complex.
    _write_manifest(tmp_path, _four_real_breadth_rows())
    issues = V._validate_spawn_manifest_schema(tmp_path, mode="light")
    assert not any("breadth tier floor" in i for i in issues), issues


# ---------------- FIX 2: NON_EVM_TARGET false-fire gating ----------------

def _nonevm_manifest(sp: Path) -> None:
    # A breadth focus that _best_non_evm_breadth_focus prefers.
    _write_manifest(sp, _row(
        "CROSS_CHAIN_MESSAGE_INTEGRITY", "B1",
        "cross_chain_message_integrity",
        "analysis_cross_chain_message_integrity.md",
    ))


def _write_heuristic_evidence(sp: Path) -> None:
    (sp / "recon_summary.md").write_text(
        "Gateway withdrawAndCall targets Solana and Bitcoin. "
        "AccountEncoder serializes Solana pubkey/account bytes for a "
        "destination chain.",
        encoding="utf-8",
    )


def _flag_yes(sp: Path) -> None:
    (sp / "detected_patterns.md").write_text(
        "## Detected Patterns\n\nNON_EVM_TARGET = YES\n", encoding="utf-8"
    )


def test_evm_with_nonevm_target_fires_cross_vm(tmp_path):
    """language='evm' + non-EVM-target evidence -> CROSS_VM DOES fire. This is the
    EVM->non-EVM serializer case the skill exists for (the DODO AccountEncoder
    gap it was created to close)."""
    _nonevm_manifest(tmp_path)
    _write_heuristic_evidence(tmp_path)
    _flag_yes(tmp_path)
    breadth, depth = D._parse_sc_skill_bindings(tmp_path, "evm")
    allskills = (
        {s for v in breadth.values() for s in v}
        | {s for v in depth.values() for s in v}
    )
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" in allskills


def test_native_nonevm_language_no_cross_vm(tmp_path):
    """language='solana' + NON_EVM_TARGET=YES -> CROSS_VM does NOT fire. It is an
    EVM-SIDE serialization skill; a native Solana program has no EVM-side
    serialization. This is the exact mis-injection the gating fix closes."""
    _nonevm_manifest(tmp_path)
    _write_heuristic_evidence(tmp_path)
    _flag_yes(tmp_path)
    breadth, depth = D._parse_sc_skill_bindings(tmp_path, "solana")
    allskills = (
        {s for v in breadth.values() for s in v}
        | {s for v in depth.values() for s in v}
    )
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" not in allskills


def test_legacy_unknown_language_no_cross_vm_recovery(tmp_path):
    """language='' (legacy/resume) -> recovery does NOT fire."""
    _nonevm_manifest(tmp_path)
    _write_heuristic_evidence(tmp_path)
    breadth, depth = D._parse_sc_skill_bindings(tmp_path)  # default ''
    allskills = (
        {s for v in breadth.values() for s in v}
        | {s for v in depth.values() for s in v}
    )
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" not in allskills


def test_detected_patterns_non_evm_no_overrides_heuristic_false_positive(tmp_path):
    """detected_patterns NON_EVM_TARGET=NO is authoritative -> no recovery
    even for a non-EVM language with positive heuristic substrings."""
    _nonevm_manifest(tmp_path)
    _write_heuristic_evidence(tmp_path)
    (tmp_path / "detected_patterns.md").write_text(
        "## Detected Patterns\n\nNON_EVM_TARGET = NO\n", encoding="utf-8"
    )
    breadth, depth = D._parse_sc_skill_bindings(tmp_path, "solana")
    allskills = (
        {s for v in breadth.values() for s in v}
        | {s for v in depth.values() for s in v}
    )
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" not in allskills


def test_template_recommendations_not_a_heuristic_source(tmp_path):
    """The CROSS_VM trigger documented in template_recommendations.md must NOT
    by itself trigger recovery (the DODO false-positive source)."""
    _nonevm_manifest(tmp_path)
    # Only template_recommendations.md carries the trigger words; no real
    # recon evidence anywhere else.
    (tmp_path / "template_recommendations.md").write_text(
        "## Injectable Skills\n\n"
        "| Skill | Trigger | Inject Into |\n"
        "|---|---|---|\n"
        "| CROSS_VM_SERIALIZATION_CONFORMANCE | NON_EVM_TARGET (EVM "
        "serializes for Solana/Bitcoin: Pubkey/Borsh/base58) | breadth |\n",
        encoding="utf-8",
    )
    breadth, depth = D._parse_sc_skill_bindings(tmp_path, "solana")
    allskills = (
        {s for v in breadth.values() for s in v}
        | {s for v in depth.values() for s in v}
    )
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" not in allskills


def test_genuine_evm_recovery_still_happens(tmp_path):
    """detected_patterns NON_EVM_TARGET=YES + EVM audit -> recovery fires (the
    genuine EVM->non-EVM serializer case, e.g. the DODO AccountEncoder)."""
    _nonevm_manifest(tmp_path)
    (tmp_path / "detected_patterns.md").write_text(
        "## Detected Patterns\n\nNON_EVM_TARGET: YES\n", encoding="utf-8"
    )
    breadth, depth = D._parse_sc_skill_bindings(tmp_path, "evm")
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" in breadth.get(
        "cross_chain_message_integrity", []
    )
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" in depth.get("external", [])


def test_genuine_evm_recovery_via_heuristic_fallback(tmp_path):
    """No NON_EVM_TARGET flag but positive heuristic + EVM audit ->
    recovery fires via the substring fallback."""
    _nonevm_manifest(tmp_path)
    _write_heuristic_evidence(tmp_path)  # no detected_patterns.md flag
    breadth, depth = D._parse_sc_skill_bindings(tmp_path, "evm")
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" in breadth.get(
        "cross_chain_message_integrity", []
    )
    assert "CROSS_VM_SERIALIZATION_CONFORMANCE" in depth.get("external", [])


def test_recovery_warning_deduplicated_across_many_calls(tmp_path, caplog):
    """Recovery warnings emit at most once per distinct message per run, even
    across dozens of worker prompt builds (no log spam)."""
    _nonevm_manifest(tmp_path)
    (tmp_path / "detected_patterns.md").write_text(
        "NON_EVM_TARGET = YES\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        for _ in range(20):
            D._parse_sc_skill_bindings(tmp_path, "evm")
    recovered = [
        r for r in caplog.records
        if "recovered CROSS_VM_SERIALIZATION_CONFORMANCE" in r.getMessage()
    ]
    # Two distinct messages (breadth + depth), each emitted once.
    assert len(recovered) <= 2, [r.getMessage() for r in recovered]
    assert (tmp_path / "_skill_recovery_logged.flag").exists()
