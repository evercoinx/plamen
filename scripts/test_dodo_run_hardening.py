"""Regression tests for the DODO Thorough-run hardening bundle (FC1-FC6).

Each failure class came from the 2026-05-29 DODO cross-chain-dex Thorough run
(_plamen.log) where prose/LLM-quality gates hard-degraded content-valid phases,
forcing retries, CRITICAL halts, and a 10-phase resume rewind. These tests lock
in the root-cause fixes. They are HOW-not-WHAT: no DODO finding ID is hardcoded;
synthetic IDs/titles stand in for the real ones.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from plamen_parsers import classify_poc_testability
from plamen_driver import (  # noqa: E402  (re-exports plamen_validators via *)
    _validate_verify_completion,
    _is_spec_support_path,
    _phase_content_gate_issues,
    _backfill_legacy_unmarked_completed_phase,
)
from plamen_types import SC_PHASES, Phase
import pty_exec


def _mkscratch(files: dict[str, str]) -> Path:
    sp = Path(tempfile.mkdtemp(prefix="plamen_dodo_"))
    for name, body in files.items():
        (sp / name).write_text(body, encoding="utf-8")
    return sp


# ---------------------------------------------------------------------------
# FC1: classifier synonyms + verifier-challenge softening
# ---------------------------------------------------------------------------

def test_fc1_classifier_event_setter_synonyms_are_structural():
    # Synonyms added in the FC1 classifier fix must route to structural, so the
    # mechanical fallback queue builder never demands an impossible unit PoC.
    assert classify_poc_testability("", "CODE-TRACE", "setBot and superWithdraw Emit No Events", "Medium") == "structural"
    assert classify_poc_testability("", "CODE-TRACE", "Router emits no event on config change", "Low") == "structural"
    assert classify_poc_testability("", "CODE-TRACE", "DODOApprove Has No Admin Setter", "Low") == "structural"
    assert classify_poc_testability("", "CODE-TRACE", "Pool deployed without a setter for slippage", "Low") == "structural"


def _verify_files(declared_class: str) -> dict[str, str]:
    # Mirror the canonical verification_queue.md + verify_*.md shape used by
    # test_poc_classification.py so the shard/row parsing matches production.
    # Queue PoC Class is always 'unit' (the LLM-queue's wrong estimate); the
    # verifier's OWN ledger PoC Class is the variable under test.
    queue = (
        "# Verification Queue Manifest\n"
        "| Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |\n"
        "|---------|-----------|----------|-------|-----------|--------------|----------|------------------|-----------|\n"
        "| 1 | H-01 | Medium | Missing event on admin setter | missing event | [CODE-TRACE] | Gateway.sol:10 | hypotheses.md | unit |\n"
    )
    verify = (
        "# Verify H-01\n"
        "**Severity**: Medium\n"
        "**Evidence Tag**: [CODE-TRACE]\n"
        "**Verdict**: CONTESTED\n\n"
        "### PoC Attempt\n"
        "- **PoC Required**: YES\n"
        f"- **PoC Class**: {declared_class}\n"
        "- **Attempted**: NO\n"
        "- **PoC Not Attempted Because**: STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION - design risk only\n"
        "- **Test File**: N/A\n"
        "- **Command**: N/A\n\n"
        "### Execution Result\n"
        "- **Compiled**: N/A\n"
        "- **Result**: NOT_EXECUTED\n"
        "- **Output**: not executed\n"
    )
    return {"verification_queue.md": queue, "verify_H-01.md": verify}


def test_fc1_honors_verifier_structural_reclassification():
    # Queue says unit, but the verifier (who read the code) declares structural
    # in its OWN ledger and skips structurally -> gate must NOT hard-fail.
    sp = _mkscratch(_verify_files(declared_class="structural"))
    issues = _validate_verify_completion(sp, "sc_verify_medium_a", mode="core")
    assert not any("H-01 mandatory" in i for i in issues), issues


def test_fc1_still_fails_silent_structural_bypass():
    # Anti-gaming: queue=unit AND verifier ledger still declares unit while
    # skipping structurally -> silent bypass, must still hard-fail.
    sp = _mkscratch(_verify_files(declared_class="unit"))
    issues = _validate_verify_completion(sp, "sc_verify_medium_a", mode="core")
    assert any("H-01 mandatory" in i for i in issues), issues


# ---------------------------------------------------------------------------
# FC4: degrade safety net re-validates content before halting
# ---------------------------------------------------------------------------

def test_fc4_content_gate_clean_for_valid_attention_repair():
    sp = _mkscratch({
        "attention_repair_queue.md": (
            "| # | Kind | Target | Reason | Source | Evidence hint |\n"
            "|---|------|--------|--------|--------|---------------|\n"
            "| 1 | asset-binding-gap | `AB-001: a <-> b` | exact pair unresolved | `m.md` | `AB-001` |\n"
        ),
        "attention_repair_summary.md": (
            "# Attention Repair\n\n"
            "| Queue # | Kind | Target | Verdict | Evidence | Notes |\n"
            "|---------|------|--------|---------|----------|-------|\n"
            "| 1 | asset-binding-gap | `AB-001: a <-> b` | SAFE | Router.sol:L10 proves a is validated against b before transfer | SAFE_REASON:EXPLICIT_BINDING_CHECK; exact pair closed |\n"
        ),
    })
    phase = Phase("attention_repair", ["Attention Repair"], ["attention_repair_summary.md"], 3000)
    issues = _phase_content_gate_issues(phase, {"mode": "thorough"}, sp, str(sp))
    assert issues == [], issues


def test_fc4_content_gate_flags_missing_attention_repair_summary():
    sp = _mkscratch({
        "attention_repair_queue.md": (
            "| # | Kind | Target | Reason | Source | Evidence hint |\n"
            "|---|------|--------|--------|--------|---------------|\n"
            "| 1 | asset-binding-gap | `AB-001: a <-> b` | exact pair unresolved | `m.md` | `AB-001` |\n"
        ),
    })
    phase = Phase("attention_repair", ["Attention Repair"], ["attention_repair_summary.md"], 3000)
    issues = _phase_content_gate_issues(phase, {"mode": "thorough"}, sp, str(sp))
    assert issues, "missing summary must produce a content-gate issue"


# ---------------------------------------------------------------------------
# FC3: legacy-unmarked backfill helper is a strict no-op off breadth/depth
# ---------------------------------------------------------------------------

def test_fc3_backfill_noop_for_non_supervised_phase():
    sp = _mkscratch({})
    phase = Phase("chain", ["Chain"], ["hypotheses.md"], 3000)
    assert _backfill_legacy_unmarked_completed_phase(sp, phase) == []


# ---------------------------------------------------------------------------
# FC5: output-token-cap detection + bounded dedup timeout
# ---------------------------------------------------------------------------

def test_fc5_output_cap_regex_distinct_from_usage_cap():
    out_err = "API Error: Claude's response exceeded the 32000 output token maximum"
    usage_err = "You've hit your weekly limit"
    assert pty_exec._OUTPUT_CAP_TEXT_RE.search(out_err)
    assert not pty_exec._USAGE_CAP_TEXT_RE.search(out_err)
    assert pty_exec._USAGE_CAP_TEXT_RE.search(usage_err)
    assert not pty_exec._OUTPUT_CAP_TEXT_RE.search(usage_err)


def test_fc5_output_cap_regex_is_number_agnostic():
    assert pty_exec._OUTPUT_CAP_TEXT_RE.search(
        "response exceeded the 64000 output token maximum"
    )


def test_fc5_turn_state_has_output_truncated_default_false():
    st = pty_exec.TurnCompleteState(complete=False)
    assert st.output_truncated is False


def test_fc5_semantic_dedup_timeout_is_bounded():
    phase = next(p for p in SC_PHASES if p.name == "sc_semantic_dedup")
    assert phase.base_timeout_s == 1200


# ---------------------------------------------------------------------------
# FC6: interface/mock recognition + report_index CONTESTED-vs-UNRESOLVED text
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "interfaces/IDODORouteProxy.sol",
    "src/interfaces/IUniswapV2Router01.sol",
    "IUniswapV2Router01.sol",
    "contracts/mocks/ERC20Mock.sol",
    "GatewayEVMMock.sol",
])
def test_fc6_interface_and_mock_paths_are_support(path):
    assert _is_spec_support_path(path) is True


@pytest.mark.parametrize("path", [
    "src/GatewayCrossChain.sol",
    "contracts/Vault.sol",
])
def test_fc6_production_paths_are_not_support(path):
    assert _is_spec_support_path(path) is False


def test_fc6_report_index_prompt_defines_contested_vs_unresolved():
    src = (Path(__file__).resolve().parent / "plamen_prompt.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "CONTESTED vs UNRESOLVED" in src
    assert "verifier CONTESTED" in src
    assert "valid ONLY when a Skeptic-Judge" in src
