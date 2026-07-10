"""Pipeline v2.8.8 — Cross-chain DEX Thorough-run retry-hardening fixes.

Three avoidable-retry / false-warning classes from the 2026-05-29 Claude-PTY
Thorough run, each fixed mechanically:

  T2a — _composition_obligation_rows: a composition row where the chain agent
        EXPLICITLY declined to promote a formal CH ID ("noting but not
        assigning formal CH ID") must NOT mint an *active* retention
        obligation (it forced the report_index OBL-CHAIN-CH-13 retry).

  T1b — _validate_poc_contract_for_rows: the per-shard hard gate now uses the
        same project-wide mock-feasibility signal as the aggregate skip audit,
        scoped to Critical/High/Medium, so a locally-mockable EXTERNAL_DEP skip
        FAILS at shard time (where a retry fixes it) instead of slipping
        through as a WARN-only audit row. Low/Info stay WARN by design.

  T2b — _check_speculative_critical_chains: a Critical chain whose constituents
        are EACH independently verified ([POC-PASS]/[MEDUSA-PASS]) is NOT
        speculative — its Critical is inherited from a verified constituent —
        so the speculative-Critical warning is suppressed even when the Index
        Agent left the row's own Verification column as UNVERIFIED (the C-02
        false warning).

Run: pytest scripts/test_pipeline_v2_8_8_retry_hardening.py -q
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_mechanical as M  # noqa: E402
import plamen_validators as V  # noqa: E402
import plamen_driver as D  # noqa: E402


# ---------------------------------------------------------------------------
# Fix #3 — perturbation worker gets extra timeout ceiling (no silent coverage loss)
# ---------------------------------------------------------------------------

def test_perturbation_gets_extra_timeout_headroom():
    base = 7260.0
    assert D._depth_job_timeout({"output": "perturbation_findings.md"}, base) > base
    assert D._depth_job_timeout({"output": "perturbation_findings.md"}, base) == base * D._PERTURBATION_TIMEOUT_MULT
    # Every other depth worker keeps the uniform base timeout.
    assert D._depth_job_timeout({"output": "depth_token_flow_findings.md"}, base) == base
    assert D._depth_job_timeout({"output": "blind_spot_a_findings.md"}, base) == base


def _mkscratch(files: dict[str, str]) -> Path:
    sp = Path(tempfile.mkdtemp(prefix="plamen_v288_"))
    for name, body in files.items():
        (sp / name).write_text(body, encoding="utf-8")
    return sp


# ---------------------------------------------------------------------------
# T2a — declined composition rows do not mint active obligations
# ---------------------------------------------------------------------------

def test_t2a_declined_chain_minted_as_covered_not_active():
    sp = _mkscratch({
        "composition_coverage.md": (
            "# Composition Coverage\n\n"
            "CH-07: attacker chains COMPOSED theft to drain the vault across users.\n"
            "CH-13 would be: sandwich inflates amountInMax to drain value, "
            "noting but not assigning formal CH ID as both are Medium and the "
            "compound is incremental.\n"
        ),
    })
    rows = M._composition_obligation_rows(sp)
    by_src = {str(r["source_id"]): r for r in rows}
    assert "CH-07" in by_src and "CH-13" in by_src, by_src.keys()
    # Real chain stays active and gate-relevant.
    assert by_src["CH-07"]["status"] == "active"
    # Declined / hypothetical chain is pre-closed — no retry-forcing obligation.
    assert by_src["CH-13"]["status"] == "covered"
    assert by_src["CH-13"]["closure_reason"], "declined row must carry a closure reason"


def test_t2a_declined_row_not_flagged_by_retention_gate():
    """End-to-end: a declined chain obligation must not fail the retention gate
    even when report coverage never mentions it (the exact OBL-CHAIN-CH-13
    failure)."""
    sp = _mkscratch({
        "composition_coverage.md": (
            "CH-13 would be: drain value via sandwich, noting but not assigning "
            "formal CH ID; compound is incremental.\n"
        ),
    })
    # Materialize the obligation ledger the gate reads.
    M._write_obligation_ledger(sp, "thorough")
    # Coverage text deliberately silent about CH-13.
    issues = V._validate_obligation_ledger_retention(sp, "no mention of the chain here")
    assert not any("CH-13" in i for i in issues), issues


def test_t2a_overmatch_guard_real_chain_with_decline_words_stays_active():
    """v2.8.8 hardening: a REAL fund-loss chain line that merely contains
    'declining' or 'not assigning blame' (decline-adjacent words NOT tied to a
    chain/CH referent) must NOT be silently marked covered."""
    sp = _mkscratch({
        "composition_coverage.md": (
            "CH-30: declining pool liquidity lets the attacker drain user funds.\n"
            "CH-31: COMPOSED theft drains the vault; we are not assigning blame here.\n"
        ),
    })
    rows = M._composition_obligation_rows(sp)
    by_src = {str(r["source_id"]): r for r in rows}
    assert by_src["CH-30"]["status"] == "active", by_src["CH-30"]
    assert by_src["CH-31"]["status"] == "active", by_src["CH-31"]


def test_t2a_genuine_active_chain_still_enforced():
    """Guard against over-suppression: a real composed chain with no decline
    language stays active and IS demanded by the retention gate."""
    sp = _mkscratch({
        "composition_coverage.md": (
            "CH-21: COMPOSED theft path drains the vault.\n"
        ),
    })
    M._write_obligation_ledger(sp, "thorough")
    issues = V._validate_obligation_ledger_retention(sp, "coverage with no CH-21 or report id")
    assert any("CH-21" in i for i in issues), issues


# ---------------------------------------------------------------------------
# T1b — per-shard mock-feasibility override (Medium+ scope)
# ---------------------------------------------------------------------------

_QUEUE = (
    "# Verification Queue Manifest\n"
    "| Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |\n"
    "|---|---|---|---|---|---|---|---|---|\n"
    "| 1 | H-90 | High | Passing mock proof | x | [POC-PASS] | A.sol:1 | hypotheses.md | unit |\n"
    "| 2 | H-30 | Medium | Mockable external dep | x | [POC-PASS] | A.sol:2 | hypotheses.md | unit |\n"
    "| 3 | H-31 | Low | Mockable external dep | x | [POC-PASS] | A.sol:3 | hypotheses.md | unit |\n"
)

# A PASSING verify file whose code block names a *Mock identifier → makes
# project-wide mocking demonstrably feasible.
_VERIFY_MOCK_PROOF = (
    "# Verify H-90\nSeverity: High\nEvidence Tag: [POC-PASS]\nVerdict: CONFIRMED\n\n"
    "```solidity\ncontract SwapRouterMock { }\n```\n"
)


def _ext_dep_skip(fid: str, sev: str) -> str:
    # Affirmative mock phrasing (no negation adjacent to 'mock') so the narrow
    # legacy _valid_poc_skip accepts it — the override is what must reject it.
    return (
        f"# Verify {fid}\nSeverity: {sev}\nEvidence Tag: [CODE-TRACE]\nVerdict: CONTESTED\n\n"
        "### PoC Attempt\n"
        "- PoC Required: YES\n"
        "- PoC Class: unit\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS\n"
        "- Test File: N/A\n"
        "- Command: N/A\n\n"
        "### Execution Result\n"
        "- Compiled: N/A\n"
        "- Result: NOT_EXECUTED\n"
        "- Output: requires deploying with a mock BridgeRouter; full mock setup "
        "is disproportionate for this finding\n"
    )


def _t1b_scratch() -> Path:
    return _mkscratch({
        "verification_queue.md": _QUEUE,
        "verify_H-90.md": _VERIFY_MOCK_PROOF,
        "verify_H-30.md": _ext_dep_skip("H-30", "Medium"),
        "verify_H-31.md": _ext_dep_skip("H-31", "Low"),
    })


def test_t1b_project_mock_feasible_detected():
    sp = _t1b_scratch()
    feasible, example = V._project_mock_feasible(sp)
    assert feasible is True
    assert "Mock" in example, example


def test_t1b_medium_mockable_external_dep_skip_hard_fails():
    sp = _t1b_scratch()
    rows = [{"finding id": "H-30", "poc class": "unit", "severity": "Medium"}]
    issues = V._validate_poc_contract_for_rows(sp, rows, "thorough")
    assert any("H-30" in i and "skip invalid" in i for i in issues), issues


def test_t1b_low_mockable_external_dep_skip_stays_warn_only():
    """Medium+ scope: a Low finding's mockable EXTERNAL_DEP skip is NOT hard-
    failed (it remains a WARN-only aggregate-audit row by design)."""
    sp = _t1b_scratch()
    rows = [{"finding id": "H-31", "poc class": "unit", "severity": "Low"}]
    issues = V._validate_poc_contract_for_rows(sp, rows, "thorough")
    assert not any("H-31" in i for i in issues), issues


def test_t1b_no_override_when_mocking_not_feasible():
    """Guard against false positives: with no passing mock anywhere, a Medium
    EXTERNAL_DEP skip is honored (legacy behavior, no override)."""
    sp = _mkscratch({
        "verification_queue.md": (
            "# Verification Queue Manifest\n"
            "| Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 1 | H-30 | Medium | Mockable external dep | x | [POC-PASS] | A.sol:2 | hypotheses.md | unit |\n"
        ),
        "verify_H-30.md": _ext_dep_skip("H-30", "Medium"),
    })
    feasible, _ = V._project_mock_feasible(sp)
    assert feasible is False
    rows = [{"finding id": "H-30", "poc class": "unit", "severity": "Medium"}]
    issues = V._validate_poc_contract_for_rows(sp, rows, "thorough")
    assert not any("skip invalid" in i for i in issues), issues


# ---------------------------------------------------------------------------
# T1b degrade-net — the Medium+ override is bounded (cannot hard-halt forever)
# ---------------------------------------------------------------------------

def test_t1b_override_message_carries_split_marker():
    """The override issue emitted by the contract gate must contain the marker
    that _validate_verify_completion uses to split bounded vs hard issues —
    otherwise the degrade-net would never recognize it."""
    sp = _t1b_scratch()
    rows = [{"finding id": "H-30", "poc class": "unit", "severity": "Medium"}]
    issues = V._validate_poc_contract_for_rows(sp, rows, "thorough")
    assert any(V._OVERRIDE_ISSUE_MARK in i for i in issues), (V._OVERRIDE_ISSUE_MARK, issues)


def test_t1b_override_retry_counter_bounds():
    sp = _mkscratch({})
    ph = "sc_verify_medium_a"
    assert V._verify_override_exhausted(sp, ph) is False
    V._bump_verify_override(sp, ph)
    assert V._verify_override_exhausted(sp, ph) is False  # 1 < cap(2)
    V._bump_verify_override(sp, ph)
    assert V._verify_override_exhausted(sp, ph) is True   # 2 >= cap(2)


def test_t1b_override_degraded_record_written():
    sp = _mkscratch({})
    V._record_verify_override_degraded(sp, "sc_verify_low_a", ["H-99 EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS skip invalid for Medium unit finding"])
    out = (sp / "verify_override_degraded.md").read_text(encoding="utf-8")
    assert "H-99" in out and "sc_verify_low_a" in out


# ---------------------------------------------------------------------------
# T2b — constituent-verified chains are not flagged speculative
# ---------------------------------------------------------------------------

def _t2b_scratch() -> Path:
    return _mkscratch({
        "report_index.md": (
            "## Master Finding Index\n\n"
            "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |\n"
            "|---|---|---|---|---|---|---|\n"
            "| C-01 | Public withdraw | Critical | G.sol:L286 | VERIFIED | - | H-01 |\n"
            "| C-02 | Chain drain | Critical | G.sol:L286 | UNVERIFIED | - | CH-01 |\n"
            "| C-09 | Speculative chain | Critical | X.sol:L10 | UNVERIFIED | - | CH-09 |\n\n"
            "## Verification Files\n\n"
            "| Report ID | Internal Hypothesis | Verification Files |\n"
            "|---|---|---|\n"
            "| C-02 | CH-01 | verify_H-01.md + verify_H-20.md (constituents) |\n"
            "| C-09 | CH-09 | verify_H-50.md (constituents) |\n"
        ),
        "verify_H-01.md": "# H-01\nSeverity: Critical\nEvidence Tag: [POC-PASS]\nVerdict: CONFIRMED\n",
        "verify_H-20.md": "# H-20\nSeverity: Medium\nEvidence Tag: [POC-PASS]\nVerdict: CONFIRMED\n",
        "verify_H-50.md": "# H-50\nSeverity: Medium\nEvidence Tag: [CODE-TRACE]\nVerdict: CONTESTED\n",
    })


def test_t2b_constituents_verified_true_when_all_poc_pass():
    sp = _t2b_scratch()
    assert V._chain_constituents_verified(sp, "C-02") is True
    # C-09's only constituent is [CODE-TRACE] → not constituent-verified.
    assert V._chain_constituents_verified(sp, "C-09") is False


def test_t2b_speculative_gate_suppresses_constituent_backed_chain():
    sp = _t2b_scratch()
    flagged = V._check_speculative_critical_chains(sp)
    # The genuinely-speculative chain is still flagged...
    assert any("C-09" in f for f in flagged), flagged
    # ...but the constituent-verified chain is NOT.
    assert not any("C-02" in f for f in flagged), flagged
