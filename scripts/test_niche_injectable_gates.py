"""Fixture tests for PLAN A gates:

- Gate 1: `_validate_niche_manifest_consistency` (niche manifest reconcile +
  recall-safe union repair)
- Gate 2: `_validate_injectable_promotion` (injectable enrichment/promotion)

Per the MEMORY.md ID-regex-catalog rule, these cover the exact source formats
the gates parse before the table/flag regexes ship.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _load(name: str):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    return importlib.import_module(name)


_BINDING_NICHE_TABLE_NONE = (
    "### Niche Agents (Phase 4b - standalone focused agents)\n"
    "\n"
    "| Niche Agent | Trigger | Required? | Reason |\n"
    "|-------------|---------|-----------|--------|\n"
    "(none extracted)\n"
)


def _binding_rules_prose(niches: list[str]) -> str:
    # Recon Addendum niche representation: prose binding rules.
    flag_for = {
        "EVENT_COMPLETENESS": "MISSING_EVENT",
        "SEMANTIC_CONSISTENCY_AUDIT": "HAS_MULTI_CONTRACT",
        "MULTI_STEP_OPERATION_SAFETY": "MULTI_STEP_OPS",
    }
    lines = ["### Niche Agent Binding Rules"]
    for n in niches:
        flag = flag_for.get(n, "SOME_FLAG")
        lines.append(
            f"- {flag} flag detected -> {n} **niche agent** REQUIRED"
        )
    return "\n".join(lines) + "\n"


def _spawn_manifest_with_niches(niches: list[str]) -> str:
    head = (
        "# Spawn Manifest\n"
        "## Breadth Agents\n"
        "| Row Type | Template | Required? | Agent ID | Focus Area | "
        "Expected Output | Status |\n"
        "|----------|----------|-----------|----------|------------|"
        "-----------------|--------|\n"
        "| AGENT | CORE_STATE | YES | B1 | core_state | "
        "analysis_core_state.md | QUEUED |\n"
        "\n"
        "## Niche Agents\n"
        "| Niche Agent | Trigger | Required? | Agent ID | Expected Output |\n"
        "|-------------|---------|-----------|----------|-----------------|\n"
    )
    for n in niches:
        slug = n.lower().replace("_", "-")
        head += (
            f"| {n} | flag | YES | niche-{slug} | "
            f"niche_{n.lower()}_findings.md |\n"
        )
    if not niches:
        head += "(none)\n"
    return head


# ---------------------------------------------------------------------------
# Gate 1
# ---------------------------------------------------------------------------

def test_gate1_binding_none_addendum_and_spawn_list_three_repairs_union(
    tmp_path: Path,
):
    """(i) BINDING niche table = '(none extracted)' while Addendum +
    spawn_manifest list 3 niches -> Gate 1 repairs to the 3-element union and
    does NOT silently pass."""
    v = _load("plamen_validators")
    niches = [
        "EVENT_COMPLETENESS",
        "SEMANTIC_CONSISTENCY_AUDIT",
        "MULTI_STEP_OPERATION_SAFETY",
    ]
    tr = _BINDING_NICHE_TABLE_NONE + "\n" + _binding_rules_prose(niches)
    (tmp_path / "template_recommendations.md").write_text(tr, encoding="utf-8")
    # spawn_manifest C starts EMPTY (the DODO failure: dispatched nothing).
    (tmp_path / "spawn_manifest.md").write_text(
        _spawn_manifest_with_niches([]), encoding="utf-8"
    )

    issues = v._validate_niche_manifest_consistency(tmp_path, "core")

    # Repair succeeds -> returns [] (does not block) but the manifest is
    # rewritten to the union.
    assert issues == []
    sm = (tmp_path / "spawn_manifest.md").read_text(encoding="utf-8")
    repaired = v._niche_tokens_from_required_table(sm)
    assert repaired == set(niches), repaired
    # BINDING MANIFEST table is also repaired to the union.
    tr_after = (tmp_path / "template_recommendations.md").read_text(
        encoding="utf-8"
    )
    assert v._niche_tokens_from_required_table(tr_after) == set(niches)


def test_gate1_all_three_sources_agree_is_noop(tmp_path: Path):
    """(ii) all three sources agree -> no-op pass (manifest unchanged)."""
    v = _load("plamen_validators")
    niches = ["EVENT_COMPLETENESS", "SEMANTIC_CONSISTENCY_AUDIT"]
    table = (
        "### Niche Agents\n"
        "| Niche Agent | Trigger | Required? | Reason |\n"
        "|-------------|---------|-----------|--------|\n"
        "| EVENT_COMPLETENESS | MISSING_EVENT | YES | events |\n"
        "| SEMANTIC_CONSISTENCY_AUDIT | HAS_MULTI_CONTRACT | YES | shared |\n"
    )
    tr = table + "\n" + _binding_rules_prose(niches)
    (tmp_path / "template_recommendations.md").write_text(tr, encoding="utf-8")
    sm_before = _spawn_manifest_with_niches(niches)
    (tmp_path / "spawn_manifest.md").write_text(sm_before, encoding="utf-8")

    issues = v._validate_niche_manifest_consistency(tmp_path, "core")

    assert issues == []
    # No-op: the spawn manifest still resolves to exactly the same set.
    sm_after = (tmp_path / "spawn_manifest.md").read_text(encoding="utf-8")
    assert v._niche_tokens_from_required_table(sm_after) == set(niches)


def test_gate1_flag_derived_only_recovers_niche(tmp_path: Path):
    """(iii) template_recommendations fully degraded but detected_patterns has
    MISSING_EVENT -> niche recovered from the flag-derived fallback."""
    v = _load("plamen_validators")
    # Degraded recommendation file: no niche table, no binding rules.
    (tmp_path / "template_recommendations.md").write_text(
        "# Template Recommendations\n\n(content lost)\n", encoding="utf-8"
    )
    (tmp_path / "detected_patterns.md").write_text(
        "## Detected Patterns\n\nMISSING_EVENT = YES\n", encoding="utf-8"
    )
    (tmp_path / "spawn_manifest.md").write_text(
        _spawn_manifest_with_niches([]), encoding="utf-8"
    )

    issues = v._validate_niche_manifest_consistency(tmp_path, "core")

    assert issues == []
    sm = (tmp_path / "spawn_manifest.md").read_text(encoding="utf-8")
    assert "EVENT_COMPLETENESS" in v._niche_tokens_from_required_table(sm)


def test_gate1_light_mode_skipped(tmp_path: Path):
    """(iv) Light mode -> Gate 1 skipped (no repair, no issues)."""
    v = _load("plamen_validators")
    (tmp_path / "detected_patterns.md").write_text(
        "MISSING_EVENT = YES\n", encoding="utf-8"
    )
    (tmp_path / "spawn_manifest.md").write_text(
        _spawn_manifest_with_niches([]), encoding="utf-8"
    )

    issues = v._validate_niche_manifest_consistency(tmp_path, "light")

    assert issues == []
    # Manifest must be untouched in Light mode.
    sm = (tmp_path / "spawn_manifest.md").read_text(encoding="utf-8")
    assert v._niche_tokens_from_required_table(sm) == set()


# ---------------------------------------------------------------------------
# Gate 2
# ---------------------------------------------------------------------------

def test_gate2_injectable_placeholder_fails(tmp_path: Path):
    """(v) injectable row with '[LLM TO ENRICH]' -> Gate 2 fails."""
    v = _load("plamen_validators")
    tr = (
        "### Injectable Skills\n"
        "\n"
        "| Skill | Required? | Inject Into | Rationale |\n"
        "|-------|-----------|-------------|-----------|\n"
        "| CROSS_VM_SERIALIZATION_CONFORMANCE | NO | depth-external | "
        "[LLM TO ENRICH] |\n"
    )
    (tmp_path / "template_recommendations.md").write_text(tr, encoding="utf-8")
    (tmp_path / "detected_patterns.md").write_text(
        "NON_EVM_TARGET = YES\n", encoding="utf-8"
    )

    issues = v._validate_injectable_promotion(tmp_path, "solana")

    assert issues
    assert any("CROSS_VM_SERIALIZATION_CONFORMANCE" in i for i in issues)
    assert any("placeholder" in i for i in issues)


def test_gate2_required_no_with_trigger_absent_passes_pure_evm(tmp_path: Path):
    """(vi) injectable Required=NO with trigger absent (pure-EVM, no
    NON_EVM_TARGET) -> Gate 2 passes (no false-fire; mirrors the DODO CROSS_VM
    language-gate fix)."""
    v = _load("plamen_validators")
    tr = (
        "### Injectable Skills\n"
        "\n"
        "| Skill | Required? | Inject Into | Rationale |\n"
        "|-------|-----------|-------------|-----------|\n"
        "| CROSS_VM_SERIALIZATION_CONFORMANCE | NO | depth-external | "
        "Not applicable - pure EVM target, no foreign-VM serialization. |\n"
    )
    (tmp_path / "template_recommendations.md").write_text(tr, encoding="utf-8")
    (tmp_path / "detected_patterns.md").write_text(
        "NON_EVM_TARGET = NO\n", encoding="utf-8"
    )

    # Pure-EVM language: even if the flag mistakenly said YES, the language
    # gate suppresses it. Here the flag is NO and language is evm -> no fire.
    issues = v._validate_injectable_promotion(tmp_path, "evm")

    assert issues == [], issues


# Bonus: the promotion repair is recall-safe (only promotes, used on 2nd fail).

def test_gate2_mechanical_promotion_sets_required_yes(tmp_path: Path):
    v = _load("plamen_validators")
    tr = (
        "### Injectable Skills\n"
        "\n"
        "| Skill | Required? | Inject Into | Rationale |\n"
        "|-------|-----------|-------------|-----------|\n"
        "| CROSS_VM_SERIALIZATION_CONFORMANCE | NO | depth-external | "
        "[LLM TO ENRICH] |\n"
    )
    (tmp_path / "template_recommendations.md").write_text(tr, encoding="utf-8")
    (tmp_path / "detected_patterns.md").write_text(
        "NON_EVM_TARGET = YES\n", encoding="utf-8"
    )

    changed = v._promote_injectable_rows(tmp_path, "solana")

    assert changed >= 1
    after = (tmp_path / "template_recommendations.md").read_text(
        encoding="utf-8"
    )
    assert "[LLM TO ENRICH]" not in after
    # No longer flagged after mechanical promotion.
    assert v._validate_injectable_promotion(tmp_path, "solana") == []
