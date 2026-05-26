"""Tests for the obligation + attention gates (Steps 5-8 of recall-recovery plan).

Covers:
  - _check_opengrep_obligation_coverage (Step 5)
  - _check_function_summary_obligation (Step 6)
  - _check_pde_section_present (Step 7)
  - _check_perturbation_block_per_finding (Step 8)
  - _OBLIG_RECEIPT_RE parsing

All four gates are WARNING-class for first ship: they emit issues for the
driver to log, but never flip `passed` to False. These tests verify the
mechanical correctness of the emit-vs-no-emit decision.

Run: `pytest scripts/test_obligation_and_attention_gates.py -v`
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _v():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    if "plamen_validators" in sys.modules:
        del sys.modules["plamen_validators"]
    return importlib.import_module("plamen_validators")


# ---------------------------------------------------------------------------
# Receipt regex
# ---------------------------------------------------------------------------


def test_oblig_receipt_re_matches_canonical_form():
    v = _v()
    line = (
        "[OBLIG:opengrep_findings.md:17] STATUS:REPORTED "
        "KEY:basic-arithmetic-underflow@foo.sol:L462 -> H-7"
    )
    m = v._OBLIG_RECEIPT_RE.search(line)
    assert m
    assert m.group("artifact") == "opengrep_findings.md"
    assert m.group("row") == "17"
    assert m.group("status").upper() == "REPORTED"


def test_oblig_receipt_re_short_status_codes():
    v = _v()
    for status_short, status_long in (("R", "REPORTED"), ("D", "DISMISSED"), ("C", "CARRIED")):
        line = f"[OBLIG:function_summary.md:Vault.deposit] STATUS:{status_short} KEY:x -> y"
        m = v._OBLIG_RECEIPT_RE.search(line)
        assert m, f"failed on short code {status_short}"
        assert m.group("status").upper() == status_short


def test_oblig_receipt_re_unicode_arrow():
    v = _v()
    line = "[OBLIG:opengrep_findings.md:5] STATUS:DISMISSED KEY:foo@bar.sol:L10 → out_of_scope"
    m = v._OBLIG_RECEIPT_RE.search(line)
    assert m
    assert m.group("status").upper() == "DISMISSED"


# ---------------------------------------------------------------------------
# Step 5: opengrep obligation gate
# ---------------------------------------------------------------------------


def test_opengrep_gate_vacuous_when_artifact_missing(tmp_path):
    v = _v()
    assert v._check_opengrep_obligation_coverage(tmp_path, "thorough") == []


def test_opengrep_gate_vacuous_when_zero_rows(tmp_path):
    v = _v()
    (tmp_path / "opengrep_findings.md").write_text(
        "# OpenGrep Findings\n\n> **Total**: 0 findings\n\n"
        "| # | Rule | Level | Location | Message |\n"
        "|---|------|-------|----------|----------|\n",
        encoding="utf-8",
    )
    assert v._check_opengrep_obligation_coverage(tmp_path, "thorough") == []


def test_opengrep_gate_fires_when_rows_have_no_receipts(tmp_path):
    v = _v()
    (tmp_path / "opengrep_findings.md").write_text(
        "# OpenGrep Findings\n\n"
        "| # | Rule | Level | Location | Message |\n"
        "|---|------|-------|----------|----------|\n"
        "| 1 | rule-a | warning | foo.sol:L1 | msg a |\n"
        "| 2 | rule-b | warning | foo.sol:L2 | msg b |\n"
        "| 3 | rule-c | warning | foo.sol:L3 | msg c |\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis_core.md").write_text(
        "## Findings\nSome analysis without obligation receipts.\n",
        encoding="utf-8",
    )
    issues = v._check_opengrep_obligation_coverage(tmp_path, "thorough")
    assert issues
    assert "3 row(s)" in issues[0] or "3" in issues[0]
    # Gap file written for post-mortem
    assert (tmp_path / "opengrep_obligation_gap.md").exists()


def test_opengrep_gate_clears_when_all_rows_receipted(tmp_path):
    v = _v()
    (tmp_path / "opengrep_findings.md").write_text(
        "| # | Rule | Level | Location | Message |\n"
        "|---|------|-------|----------|----------|\n"
        "| 1 | rule-a | warning | foo.sol:L1 | a |\n"
        "| 2 | rule-b | warning | foo.sol:L2 | b |\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis_core.md").write_text(
        "## Obligation Receipts — opengrep_findings.md\n\n"
        "[OBLIG:opengrep_findings.md:1] STATUS:REPORTED KEY:rule-a@foo.sol:L1 -> F-1\n"
        "[OBLIG:opengrep_findings.md:2] STATUS:DISMISSED KEY:rule-b@foo.sol:L2 -> informational_style\n",
        encoding="utf-8",
    )
    issues = v._check_opengrep_obligation_coverage(tmp_path, "thorough")
    assert issues == []


def test_opengrep_gate_writes_gap_artifact_on_partial(tmp_path):
    v = _v()
    (tmp_path / "opengrep_findings.md").write_text(
        "| # | Rule | Level | Location | Message |\n"
        "|---|------|-------|----------|----------|\n"
        "| 1 | rule-a | warning | foo.sol:L1 | a |\n"
        "| 2 | rule-b | warning | foo.sol:L2 | b |\n"
        "| 3 | rule-c | warning | foo.sol:L3 | c |\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis_b1.md").write_text(
        "[OBLIG:opengrep_findings.md:1] STATUS:REPORTED KEY:x -> F-1\n",
        encoding="utf-8",
    )
    issues = v._check_opengrep_obligation_coverage(tmp_path, "thorough")
    assert issues
    gap = (tmp_path / "opengrep_obligation_gap.md").read_text(encoding="utf-8")
    assert "row 2" in gap and "row 3" in gap


# ---------------------------------------------------------------------------
# Step 6: function_summary obligation gate
# ---------------------------------------------------------------------------


def _write_function_summary(tmp_path: Path):
    """Write a minimal function_summary.md with two functions per contract."""
    (tmp_path / "function_summary.md").write_text(
        "# Function Summary\n\n"
        "## Vault.sol\n\n"
        "| Function | Visibility | Modifiers | State Reads | State Writes | External Calls | Notes |\n"
        "|----------|-----------|-----------|-------------|--------------|----------------|-------|\n"
        "| `deposit` | external | onlyOwner | balances | balances, totalSupply | IERC20.transferFrom | entry |\n"
        "| `viewFn` | external | view | balances | - | - | view-only |\n"
        "\n"
        "## Router.sol\n\n"
        "| Function | Visibility | Modifiers | State Reads | State Writes | External Calls | Notes |\n"
        "|----------|-----------|-----------|-------------|--------------|----------------|-------|\n"
        "| `swap` | external | - | reserves | reserves | IDODO.mixSwap | external swap |\n",
        encoding="utf-8",
    )


def test_function_summary_parser_extracts_rows(tmp_path):
    v = _v()
    _write_function_summary(tmp_path)
    rows = v._parse_function_summary_rows(tmp_path)
    assert len(rows) == 3
    assert any(r["function"].strip("`") == "deposit" for r in rows)
    deposit = next(r for r in rows if r["function"].strip("`") == "deposit")
    assert "balances" in deposit["state_writes"]
    assert "transferFrom" in deposit["external_calls"]


def test_function_summary_gate_vacuous_when_missing(tmp_path):
    v = _v()
    assert v._check_function_summary_obligation(tmp_path, "thorough") == []


def test_function_summary_gate_fires_when_no_receipts(tmp_path):
    v = _v()
    _write_function_summary(tmp_path)
    issues = v._check_function_summary_obligation(tmp_path, "thorough")
    assert issues
    # Both state-trace and token-flow missing
    assert any("state-trace" in i for i in issues)
    assert any("token-flow" in i for i in issues)
    assert (tmp_path / "function_summary_obligation_gap.md").exists()


def test_function_summary_gate_clears_when_receipts_emitted(tmp_path):
    v = _v()
    _write_function_summary(tmp_path)
    (tmp_path / "depth_state_trace_findings.md").write_text(
        "[OBLIG:function_summary.md:Vault.deposit] STATUS:REPORTED KEY:reentrancy-pre-transfer -> F-1\n",
        encoding="utf-8",
    )
    (tmp_path / "depth_token_flow_findings.md").write_text(
        "[OBLIG:function_summary.md:Vault.deposit] STATUS:CARRIED KEY:approval-flow -> external\n"
        "[OBLIG:function_summary.md:Router.swap] STATUS:DISMISSED KEY:no-issue@Router.sol:L10 -> false_positive\n",
        encoding="utf-8",
    )
    issues = v._check_function_summary_obligation(tmp_path, "thorough")
    assert issues == []


def test_function_summary_gate_ignores_view_only_rows(tmp_path):
    v = _v()
    _write_function_summary(tmp_path)
    # viewFn has no state writes and no external calls — should not require receipt
    (tmp_path / "depth_state_trace_findings.md").write_text(
        "[OBLIG:function_summary.md:Vault.deposit] STATUS:REPORTED KEY:x -> F-1\n",
        encoding="utf-8",
    )
    (tmp_path / "depth_token_flow_findings.md").write_text(
        "[OBLIG:function_summary.md:Vault.deposit] STATUS:CARRIED KEY:x -> external\n"
        "[OBLIG:function_summary.md:Router.swap] STATUS:DISMISSED KEY:x -> false_positive\n",
        encoding="utf-8",
    )
    # viewFn intentionally has no receipt — gate must still pass
    issues = v._check_function_summary_obligation(tmp_path, "thorough")
    assert issues == []


# ---------------------------------------------------------------------------
# Step 7: PDE gate on niche-semantic-consistency
# ---------------------------------------------------------------------------


def test_pde_gate_vacuous_when_niche_output_missing(tmp_path):
    v = _v()
    assert v._check_pde_section_present(tmp_path) == []


def test_pde_gate_fires_when_pde_missing(tmp_path):
    v = _v()
    (tmp_path / "niche_semantic_consistency_findings.md").write_text(
        "# Semantic Consistency Findings\n\n"
        "## Finding [SC-1]: foo\n**Severity**: Medium\n**Verdict**: CONFIRMED\n",
        encoding="utf-8",
    )
    issues = v._check_pde_section_present(tmp_path)
    assert issues
    assert "Pre-Commit Dimension Enumeration" in issues[0]


def test_pde_gate_clears_when_pde_present(tmp_path):
    v = _v()
    (tmp_path / "niche_semantic_consistency_findings.md").write_text(
        "# Semantic Consistency Findings\n\n"
        "## Pre-Commit Dimension Enumeration\n\n"
        "### Sibling Set\n| Member | In Scope? |\n|---|---|\n| Vault | YES |\n\n"
        "## Finding [SC-1]: foo\n",
        encoding="utf-8",
    )
    issues = v._check_pde_section_present(tmp_path)
    assert issues == []


# ---------------------------------------------------------------------------
# Step 8: in-pass perturbation block gate
# ---------------------------------------------------------------------------


def test_perturbation_gate_vacuous_when_depth_outputs_missing(tmp_path):
    v = _v()
    assert v._check_perturbation_block_per_finding(tmp_path) == []


def test_perturbation_gate_fires_on_confirmed_high_without_block(tmp_path):
    v = _v()
    (tmp_path / "depth_state_trace_findings.md").write_text(
        "## Finding [DST-1]: Token drain via withdraw\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: High\n"
        "**Location**: `Vault.sol:L100`\n"
        "Description: bug present.\n\n"
        "## Finding [DST-2]: ...\n"
        "**Verdict**: REFUTED\n"
        "**Severity**: Medium\n"
        "Body.\n",
        encoding="utf-8",
    )
    issues = v._check_perturbation_block_per_finding(tmp_path)
    assert issues
    # DST-1 confirmed-High lacks perturbation block; DST-2 refuted so excluded
    assert "DST-1" in issues[0]
    assert "DST-2" not in issues[0]


def test_perturbation_gate_clears_when_block_present(tmp_path):
    v = _v()
    (tmp_path / "depth_state_trace_findings.md").write_text(
        "## Finding [DST-1]: Token drain\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: High\n"
        "**Location**: `Vault.sol:L100`\n\n"
        "### Perturbation Block — DST-1\n"
        "| Operator | Applied To | Verdict | Evidence |\n"
        "|----------|-----------|---------|----------|\n"
        "| SIBLING | Vault2.sol | D | line 200 |\n",
        encoding="utf-8",
    )
    (tmp_path / "depth_token_flow_findings.md").write_text(
        "## Finding [DT-1]: Approval lingering\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: Critical\n"
        "**Location**: `Router.sol:L50`\n\n"
        "### Perturbation Block — DT-1\n"
        "| Operator | Applied To | Verdict | Evidence |\n"
        "|----------|-----------|---------|----------|\n"
        "| FIELD | decoded.x | D | L60 |\n",
        encoding="utf-8",
    )
    issues = v._check_perturbation_block_per_finding(tmp_path)
    assert issues == []


def test_perturbation_gate_accepts_bold_label_block(tmp_path):
    v = _v()
    (tmp_path / "depth_state_trace_findings.md").write_text(
        "## Finding [DST-1]: Token drain\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: High\n"
        "**Location**: `Vault.sol:L100`\n\n"
        "**Perturbation Block**:\n"
        "- SIBLING: checked `Vault.sol:L120`; same invariant fails.\n",
        encoding="utf-8",
    )
    assert v._check_perturbation_block_per_finding(tmp_path) == []


def test_perturbation_gate_ignores_low_and_refuted(tmp_path):
    v = _v()
    (tmp_path / "depth_state_trace_findings.md").write_text(
        "## Finding [DST-1]: minor cosmetic\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: Low\n"
        "Body.\n\n"
        "## Finding [DST-2]: dismissed by depth\n"
        "**Verdict**: REFUTED\n"
        "**Severity**: High\n"
        "Body.\n",
        encoding="utf-8",
    )
    issues = v._check_perturbation_block_per_finding(tmp_path)
    assert issues == []


# ---------------------------------------------------------------------------
# Integration: gates are exported through plamen_validators
# ---------------------------------------------------------------------------


def test_gates_exported_via_all():
    v = _v()
    for name in (
        "_check_opengrep_obligation_coverage",
        "_check_function_summary_obligation",
        "_check_pde_section_present",
        "_check_perturbation_block_per_finding",
    ):
        assert hasattr(v, name)
        assert name in v.__all__
