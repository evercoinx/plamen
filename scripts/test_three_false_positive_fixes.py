"""Regression suite for THREE confirmed false-positive warnings/retries.

All three were reproduced against a live thorough-mode SC run and are
recall-safe: each fix only suppresses a FALSE warning/retry, never drops or
hides a finding. Every fix gets:

  - a LEGACY-ACCEPT fixture  (the old, correctly-flagged shape still flags),
  - a LIVE-FP-REPRO fixture  (the exact live shape, now CLEAN),
  - a NEGATIVE-CONTROL fixture (genuinely-bad input still fires).

Fixtures use representative/generic shapes only — no protocol, contract, or
specific finding names are baked in. The live runs were READ-ONLY validation.

(A) attention_repair asset-binding closure  — _validate_attention_repair
(B) verify_low PoC-contract / skip-coverage  — _validate_poc_attempt_coverage
                                              + _poc_contract_required
(C) crossbatch id-ledger consumer backstop    — _validate_consumer_ids_in_ledger
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_validators as V  # noqa: E402
from plamen_parsers import id_ledger_register  # noqa: E402


# ===========================================================================
# (A) attention_repair asset-binding closure
# ===========================================================================
#
# Live FP: a CONFIRMED asset-binding-gap row whose escalated finding proves the
# gap with a rich `[TRACE: onCall(zrc20=USDC,...) -> ...(targetZRC20=WZETA)...]`
# closure. The trace names the CONCRETE values/symbols (zrc20, targetZRC20), not
# the abstract queued field labels (`context.asset`), so the literal exact-pair
# matcher missed it and warned "lacks exact field-pair closure". Root fix:
# accept ANY substantive closure (depth-evidence tag OR SAFE_REASON OR explicit
# relation claim). Recall-safe: a row with NO closure at all still warns.

_ATTN_QUEUE_HDR = (
    "# Attention Repair Queue\n"
    "| # | Kind | Target | Reason | Source | Evidence hint |\n"
    "|---|------|--------|--------|--------|---------------|\n"
)


def _attn_setup(
    tmp_path: Path, queue_row: str, summary_row: str, finding_block: str
) -> tuple[list[str], list[str]]:
    (tmp_path / "attention_repair_queue.md").write_text(
        _ATTN_QUEUE_HDR + queue_row + "\n", encoding="utf-8"
    )
    (tmp_path / "attention_repair_summary.md").write_text(
        "# Attention Repair Summary\nverdict present\n"
        "| Queue # | Kind | Target | Verdict | Evidence |\n"
        "|---|---|---|---|---|\n" + summary_row + "\n",
        encoding="utf-8",
    )
    (tmp_path / "attention_repair_findings.md").write_text(
        "# Attention Repair Findings\n" + finding_block + "\n",
        encoding="utf-8",
    )
    return V._validate_attention_repair(tmp_path, "thorough")


def _attn_pair_warnings(soft: list[str]) -> list[str]:
    return [s for s in soft if "asset-binding closure" in s]


def test_A_legacy_accept_exact_field_pair_closure_still_clean(tmp_path):
    """Old correct behavior: a row whose closure literally names both queued
    fields with a relation claim must remain CLEAN (no regression)."""
    queue = "| 1 | asset-binding-gap | `AB-001: fieldOne <-> fieldTwo` | bound? | matrix.md | hint |"
    summary = (
        "| 1 | asset-binding-gap | AB-001: fieldOne <-> fieldTwo | CONFIRMED | "
        "Src.sol:L10: fieldOne does not match fieldTwo -> ATT-1 |"
    )
    finding = (
        "## Finding [ATT-1]: gap\n"
        "**Description**: fieldOne is not validated against fieldTwo before value moves.\n"
    )
    _hard, soft = _attn_setup(tmp_path, queue, summary, finding)
    assert _attn_pair_warnings(soft) == []


def test_A_live_fp_repro_rich_trace_closure_now_clean(tmp_path):
    """Live FP shape: CONFIRMED row, escalated finding closes the gap with a
    rich [TRACE]/[BOUNDARY] tag naming concrete values (not the abstract queued
    field labels). Must be CLEAN now (was a false 'lacks exact closure' warn)."""
    queue = (
        "| 1 | asset-binding-gap | `AB-003: context.asset <-> decoded.targetZRC20` "
        "| bound before value moves? | matrix.md | AB-003 in matrix.md |"
    )
    summary = (
        "| 1 | asset-binding-gap | AB-003: context.asset <-> decoded.targetZRC20 | "
        "CONFIRMED | Src.sol:L443 (zrc20 param), L468 (decoded.targetZRC20); "
        "no-swap path withdraws targetZRC20 without verifying zrc20==targetZRC20 -> ATT-1 |"
    )
    finding = (
        "## Finding [ATT-1]: Gateway Asset Not Verified Against Withdrawal Target\n"
        "**Depth Evidence**: [TRACE: onCall(zrc20=USDC, swapData=empty) -> "
        "_doMixSwap returns amount unchanged -> _handleEvmOrSolanaWithdraw(targetZRC20=WZETA) "
        "-> withdrawAndCall with wrong token], [BOUNDARY: swapData.length=0 -> early return]\n"
        "**Preferred Tag**: CODE-TRACE\n"
        "**Description**: In onCall the gateway receives the authenticated asset and "
        "decodes a target token from the user-supplied message; no check binds them.\n"
    )
    _hard, soft = _attn_setup(tmp_path, queue, summary, finding)
    assert _attn_pair_warnings(soft) == []


def test_A_live_fp_repro_safe_explicit_equality_prose_now_clean(tmp_path):
    """Live FP shape: a SAFE row that proves equality in prose
    ('EXPLICIT_EQUALITY confirmed', 'identical expression') without the rigid
    `SAFE_REASON:` token. Must be CLEAN now (was a false 'marks SAFE without
    SAFE_REASON' warn)."""
    queue = (
        "| 1 | asset-binding-gap | `AB-007: outputAmount <-> targetAmount` "
        "| bound? | matrix.md | hint |"
    )
    summary = (
        "| 1 | asset-binding-gap | AB-007: outputAmount <-> targetAmount | SAFE | "
        "Src.sol:L332-L334: amountsOut = targetAmount - amounts[0]; approve and "
        "withdraw use identical expression; EXPLICIT_EQUALITY confirmed |"
    )
    finding = "## Note\nNo escalation; row is SAFE.\n"
    _hard, soft = _attn_setup(tmp_path, queue, summary, finding)
    assert _attn_pair_warnings(soft) == []


def test_A_negative_control_no_closure_still_warns(tmp_path):
    """Genuinely-bad input: a CONFIRMED row with NO closure of any kind — no
    depth-evidence tag, no SAFE_REASON, no relation claim anywhere in its
    closure context. The check must NOT be vacuous: it STILL warns."""
    queue = "| 1 | asset-binding-gap | `AB-001: fieldOne <-> fieldTwo` | bound? | matrix.md | hint |"
    summary = (
        "| 1 | asset-binding-gap | AB-001: fieldOne <-> fieldTwo | CONFIRMED | "
        "looked at it, seems fine, moving on |"
    )
    finding = "## Finding [ATT-1]: gap\n**Description**: we reviewed the area generally.\n"
    _hard, soft = _attn_setup(tmp_path, queue, summary, finding)
    warns = _attn_pair_warnings(soft)
    assert warns, "negative control: a closure-less row must still warn"
    assert "AB-001" in warns[0]


# ===========================================================================
# (B) verify PoC-contract / skip-coverage honors reclassified PoC Class
# ===========================================================================
#
# Live FP: queue class is property/unit but the verifier non-silently
# RECLASSIFIED to `PoC Class: structural` with a valid structural blocker
# (STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION). Both the hard contract gate and the
# soft attempt-coverage audit used the STALE queue class -> false "mandatory PoC
# not attempted" / "Attempted: NO without structural justification". Root fix:
# both honor the verifier's declared PoC Class via shared _effective_poc_class.
# Recall-safe: a genuine unit/property finding with NO reclassification + invalid
# skip STILL fires.

_VQ_HDR = (
    "| Finding ID | Severity | Title | Location | PoC Class |\n"
    "|---|---|---|---|---|\n"
)


def _verify_setup(
    tmp_path: Path, fid: str, queue_class: str, ledger: str
) -> None:
    (tmp_path / "verification_queue.md").write_text(
        _VQ_HDR + f"| {fid} | Low | t | F.sol:1 | {queue_class} |\n",
        encoding="utf-8",
    )
    (tmp_path / f"verify_{fid}.md").write_text(ledger, encoding="utf-8")


_STRUCTURAL_RECLASS_LEDGER = (
    "**Verdict**: CONFIRMED\n"
    "**Severity:** Low\n"
    "**Preferred Tag**: CODE-TRACE\n"
    "### PoC Attempt\n"
    "- PoC Required: NO\n"
    "- PoC Class: structural\n"
    "- Attempted: NO\n"
    "- PoC Not Attempted Because: STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION\n"
    "- Test File: N/A\n"
    "- Command: N/A\n"
    "- Note: Reclassified from property — harm has no self-contained executable assertion.\n"
    "### Execution Result\n"
    "- Compiled: N/A\n- Result: NOT_EXECUTED\n- Evidence Tag: [CODE-TRACE]\n"
)


def test_B_legacy_accept_bare_structural_still_relaxes(tmp_path):
    """Old correct behavior: a bare-structural queue/ledger never required a
    unit/property PoC. Effective class stays structural -> not required."""
    row = {"poc class": "structural", "severity": "Low", "finding id": "X-1"}
    assert V._poc_contract_required(row, "thorough") is False
    assert V._effective_poc_class("structural") == "structural"


def test_B_live_fp_repro_hard_gate_honors_reclassified_class(tmp_path):
    """Hard contract gate: queue=property, verifier reclassified to structural
    with valid structural blocker -> no longer required (no false retry)."""
    _verify_setup(tmp_path, "HL-05", "property", _STRUCTURAL_RECLASS_LEDGER)
    content = (tmp_path / "verify_HL-05.md").read_text(encoding="utf-8")
    row = {"poc class": "property", "severity": "Low", "finding id": "HL-05"}
    # First-pass (queue) still considers it required...
    assert V._poc_contract_required(row, "thorough") is True
    # ...second-pass (content) relaxes via the declared structural class.
    assert V._poc_contract_required(row, "thorough", content) is False
    assert V._effective_poc_class("property", content) == "structural"


def test_B_live_fp_repro_soft_coverage_no_false_warn(tmp_path):
    """Soft attempt-coverage audit: queue=unit, verifier reclassified to
    structural with valid blocker -> no 'Attempted: NO without structural
    justification' warning (the live FP)."""
    _verify_setup(tmp_path, "HL-15", "unit", _STRUCTURAL_RECLASS_LEDGER)
    warns = V._validate_poc_attempt_coverage(tmp_path, "thorough")
    assert [w for w in warns if "HL-15" in w] == []


def test_B_negative_control_unit_no_reclass_invalid_skip_still_warns(tmp_path):
    """Genuinely-bad input: a unit finding with NO reclassification (ledger left
    at unit) and a skip code that is INVALID for unit
    (STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION). Both gates must STILL fire."""
    bad_ledger = (
        "**Verdict**: CONFIRMED\n"
        "**Severity:** Medium\n"
        "**Preferred Tag**: CODE-TRACE\n"
        "### PoC Attempt\n"
        "- PoC Class: unit\n"          # left testable — silent, not relaxed
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION\n"
        "- Test File: N/A\n- Command: N/A\n"
        "### Execution Result\n- Compiled: N/A\n- Result: NOT_EXECUTED\n"
    )
    _verify_setup(tmp_path, "Z-1", "unit", bad_ledger)
    content = (tmp_path / "verify_Z-1.md").read_text(encoding="utf-8")
    row = {"poc class": "unit", "severity": "Medium", "finding id": "Z-1"}
    # Effective class stays unit (no reclassification away) -> still required.
    assert V._effective_poc_class("unit", content) == "unit"
    assert V._poc_contract_required(row, "thorough", content) is True
    warns = V._validate_poc_attempt_coverage(tmp_path, "thorough")
    assert [w for w in warns if "Z-1" in w], "unit + invalid skip must still warn"

    # And the hard shard contract gate must still flag it as not-attempted.
    issues = V._validate_poc_contract_for_rows(tmp_path, [row], "thorough")
    assert any("Z-1" in i and "not attempted" in i for i in issues), issues


def test_B_negative_control_blank_skip_still_warns(tmp_path):
    """A unit finding with Attempted:NO and a BLANK/missing skip reason must not
    pass — anti-dodge floor."""
    blank_ledger = (
        "**Verdict**: CONFIRMED\n"
        "**Severity:** Medium\n"
        "**Preferred Tag**: CODE-TRACE\n"
        "### PoC Attempt\n"
        "- PoC Class: unit\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: \n"
        "- Test File: N/A\n- Command: N/A\n"
        "### Execution Result\n- Compiled: N/A\n- Result: NOT_EXECUTED\n"
    )
    _verify_setup(tmp_path, "Z-2", "unit", blank_ledger)
    warns = V._validate_poc_attempt_coverage(tmp_path, "thorough")
    assert [w for w in warns if "Z-2" in w], "blank skip reason must still warn"


# ===========================================================================
# (C) crossbatch id-ledger consumer backstop — produced-artifact presence
# ===========================================================================
#
# Live FP: the backstop only treats minted hypothesis IDs (H-/CH-/GRP-/...) in
# _id_ledger.json as 'registered', so legitimate agent-source-namespace IDs
# (CMI-/CC-/CCT-/CR-/DEX-/TF-/...) that the ledger does not track read as
# contamination. Root fix (prefix-AGNOSTIC): an ID-shaped token is registered if
# it is in the ledger OR it actually APPEARS in a produced artifact. Recall-safe:
# an ID present in NO artifact and not in the ledger still flags; ledger-OWNED
# minting namespaces are NOT laundered by stale artifact presence.

def _crossbatch_artifact(tmp_path: Path, referenced_ids: str) -> None:
    (tmp_path / "cross_batch_consistency.md").write_text(
        "# Cross Batch Consistency\n" + referenced_ids + "\n", encoding="utf-8"
    )


def test_C_legacy_accept_ledger_minted_id_clean(tmp_path):
    """Old correct behavior: an ID minted into the ledger is registered."""
    id_ledger_register(
        tmp_path, finding_id="GRP-01", owner_phase="chain", owner_attempt=1,
        owning_artifact="hypotheses.md", title="t",
    )
    _crossbatch_artifact(tmp_path, "Consistency check for GRP-01 looks fine.")
    assert V._validate_consumer_ids_in_ledger(tmp_path, "crossbatch") == []


def test_C_live_fp_repro_agent_source_id_present_in_inventory_clean(tmp_path):
    """Live FP: agent-source/chain IDs (CMI-1, CC-03) NOT in the ledger but
    present in produced artifacts (findings_inventory.md / analysis_*.md). Must
    be CLEAN now (was 'references N unregistered ID(s)')."""
    # Ledger has only the minted hypothesis IDs.
    id_ledger_register(
        tmp_path, finding_id="GRP-01", owner_phase="chain", owner_attempt=1,
        owning_artifact="hypotheses.md", title="t",
    )
    (tmp_path / "findings_inventory.md").write_text(
        "## Inventory\n"
        "- CMI-1: cross-chain message integrity gap (agent source)\n"
        "- CC-03: chain-composition candidate (agent source)\n",
        encoding="utf-8",
    )
    (tmp_path / "analysis_cross_chain.md").write_text(
        "CMI-1 discussed here with file:line evidence.\n", encoding="utf-8",
    )
    _crossbatch_artifact(
        tmp_path, "Batch A and Batch B agree on CMI-1 and CC-03."
    )
    assert V._validate_consumer_ids_in_ledger(tmp_path, "crossbatch") == []


def test_C_negative_control_phantom_id_present_nowhere_still_flags(tmp_path):
    """Genuinely-bad input: an agent-source-shaped ID present in NO produced
    artifact and not in the ledger (hallucination / stale cross-run). STILL
    flags."""
    id_ledger_register(
        tmp_path, finding_id="GRP-01", owner_phase="chain", owner_attempt=1,
        owning_artifact="hypotheses.md", title="t",
    )
    (tmp_path / "findings_inventory.md").write_text(
        "## Inventory\n- CMI-1: real agent finding\n", encoding="utf-8",
    )
    _crossbatch_artifact(tmp_path, "Mentions phantom TF-999 that exists nowhere.")
    issues = V._validate_consumer_ids_in_ledger(tmp_path, "crossbatch")
    assert len(issues) == 1
    assert "TF-999" in issues[0]
    assert "CMI-1" not in issues[0]


def test_C_negative_control_ledger_owned_id_not_laundered_by_artifact(tmp_path):
    """Ledger-OWNED minting namespace (HM-NN) must NOT be laundered by mere
    presence in a (possibly stale) artifact — it must trace to the ledger.
    Mirrors the existing P2.5 guarantee for the hypothesis namespace."""
    id_ledger_register(
        tmp_path, finding_id="GRP-01", owner_phase="chain", owner_attempt=1,
        owning_artifact="hypotheses.md", title="registered",
    )
    # HM-99 sits only in a (stale) artifact, never minted into the ledger.
    (tmp_path / "hypotheses.md").write_text(
        "### HM-99 - stale unregistered hypothesis\n", encoding="utf-8",
    )
    _crossbatch_artifact(tmp_path, "Batches agree on HM-99.")
    issues = V._validate_consumer_ids_in_ledger(tmp_path, "crossbatch")
    assert len(issues) == 1
    assert "HM-99" in issues[0]
