"""v2.8.17 — instantiate skill floor-fill: reject fabricated skill templates.

Root cause (PulsechainGameWards 2026-05-31): the Complex breadth floor (>=7)
forced instantiate to add breadth agents beyond the real recon-recommended
skills (5), and it FABRICATED skill-template names after the protocol's domains
(REWARD_ACCOUNTING / GAME_LOGIC_CORRECTNESS / MINIGAME_AUXILIARY). Those resolve
to no SKILL.md -> not injected -> the agents run skill-less (one stubbed).

Fix: (1) instantiate prompt Step 2a.3 (bind real skills or the GENERAL
sentinel, never invent); (2) driver recognizes GENERAL as a no-skill sentinel
(no false warning); (3) the manifest schema gate rejects fabricated templates
(soft/retry, GENERAL is the escape so no halt loop).
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


# ---------------- helper-level ----------------

def test_skill_template_resolves_real_vs_fake():
    for real in ("ORACLE_ANALYSIS", "ECONOMIC_DESIGN_AUDIT",
                 "OUTCOME_DETERMINISM", "SHARE_ALLOCATION_FAIRNESS"):
        assert V._skill_template_resolves(real) is True, real
    for fake in ("REWARD_ACCOUNTING", "GAME_LOGIC_CORRECTNESS",
                 "MINIGAME_AUXILIARY", "GENERAL"):
        assert V._skill_template_resolves(fake) is False, fake


def test_fabricated_templates_detected(tmp_path):
    rows = (
        _row("ORACLE_ANALYSIS", "B1", "oracle", "analysis_oracle.md")
        + _row("REWARD_ACCOUNTING", "B2", "reward", "analysis_reward.md")
        + _row("MINIGAME_AUXILIARY", "B3", "minigame", "analysis_minigame.md")
    )
    _write_manifest(tmp_path, rows)
    fab = V._manifest_fabricated_templates(tmp_path)
    assert "B2:REWARD_ACCOUNTING" in fab
    assert "B3:MINIGAME_AUXILIARY" in fab
    assert not any(f.startswith("B1:") for f in fab)


def test_general_sentinel_and_baseline_not_flagged(tmp_path):
    rows = (
        _row("GENERAL", "B1", "misc", "analysis_misc.md")
        + _row("CORE_STATE", "B2", "core", "analysis_core.md")
        + _row("ACCESS_CONTROL", "B3", "access", "analysis_access.md")
        + _row("ECONOMIC_DESIGN_AUDIT", "B4", "econ", "analysis_econ.md")
    )
    _write_manifest(tmp_path, rows)
    assert V._manifest_fabricated_templates(tmp_path) == []


def test_combined_cell_split(tmp_path):
    rows = _row("SEMI_TRUSTED_ROLES + REWARD_ACCOUNTING", "B1", "x",
                "analysis_x.md")
    _write_manifest(tmp_path, rows)
    fab = V._manifest_fabricated_templates(tmp_path)
    assert fab == ["B1:REWARD_ACCOUNTING"]  # real half passes, fake half flagged


# ---------------- gate-level ----------------

def test_schema_gate_flags_fabricated_on_complex(tmp_path):
    _complex_inventory(tmp_path)
    rows = "".join(
        _row(t, f"B{i+1}", f"f{i}", f"analysis_f{i}.md") for i, t in enumerate([
            "SEMI_TRUSTED_ROLES", "ORACLE_ANALYSIS", "ECONOMIC_DESIGN_AUDIT",
            "CENTRALIZATION_RISK", "OUTCOME_DETERMINISM",
            "REWARD_ACCOUNTING", "GAME_LOGIC_CORRECTNESS",
        ])
    )
    _write_manifest(tmp_path, rows)
    issues = V._validate_spawn_manifest_schema(tmp_path)
    assert any("fabricated skill template" in i for i in issues), issues


def test_schema_gate_general_floorfill_passes(tmp_path):
    _complex_inventory(tmp_path)
    rows = "".join(
        _row(t, f"B{i+1}", f"f{i}", f"analysis_f{i}.md") for i, t in enumerate([
            "SEMI_TRUSTED_ROLES", "ORACLE_ANALYSIS", "ECONOMIC_DESIGN_AUDIT",
            "CENTRALIZATION_RISK", "OUTCOME_DETERMINISM",
            "SHARE_ALLOCATION_FAIRNESS", "GENERAL",
        ])
    )
    _write_manifest(tmp_path, rows)
    issues = V._validate_spawn_manifest_schema(tmp_path)
    assert not any("fabricated skill template" in i for i in issues), issues
    assert not any("breadth tier floor" in i for i in issues), issues


# ---------------- driver injection ----------------

def test_general_sentinel_no_warning(caplog):
    with caplog.at_level(logging.WARNING):
        block = D._sc_skill_injection_block(["GENERAL"], agent_kind="breadth",
                                            language="evm")
    assert block == ""
    assert not any("did not resolve" in r.message for r in caplog.records)


def test_real_skill_injects(caplog):
    with caplog.at_level(logging.WARNING):
        block = D._sc_skill_injection_block(["ORACLE_ANALYSIS"],
                                            agent_kind="breadth", language="evm")
    assert "ASSIGNED SKILL METHODOLOGY" in block
    assert "oracle-analysis" in block.lower()
    assert not any("did not resolve" in r.message for r in caplog.records)
