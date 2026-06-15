"""Haltless fix for the report_index obligation-retention halt.

Reproduces the live L1 report_index hard-halt where an active Medium+
`exact_value_binding` obligation whose only coverage disposition was
"DEFERRED — <reason>" caused `_validate_obligation_ledger_retention` to emit an
issue that propagated through `_validate_report_coverage_semantic_contract` →
`_validate_report_coverage_accounting` → driver `sys.exit(EXIT_DEGRADED)`.

Two recall-safe contract guarantees are tested:

1. ACCEPT DEFERRED / APPENDIX_ONLY: an obligation the report_coverage ledger
   explicitly carries as DEFERRED/APPENDIX_ONLY (tracked + reasoned, not
   silently dropped) is NOT flagged by the obligation gate.
2. DEGRADE-NOT-HALT: a genuinely unmentioned obligation is STILL flagged
   (silent-drop protection preserved), but the report_index aggregation layer
   routes it to report_semantic_retention_risks.md instead of contributing to
   the hard-fail set that halts the pipeline.

Run: `pytest scripts/test_report_index_obligation_haltless.py -v`
"""
from __future__ import annotations

import importlib
import os
import sys


def _v():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    if "plamen_validators" in sys.modules:
        del sys.modules["plamen_validators"]
    return importlib.import_module("plamen_validators")


_OBLIGATION_LEDGER = (
    '{\n'
    '  "schema_version": "plamen.obligation_ledger.v1",\n'
    '  "obligations": [\n'
    '    {"id":"OBL-AB-001","class":"exact_value_binding","status":"active",'
    '"severity_signal":"Medium","field_a":"msg.value","field_b":"amount"}\n'
    '  ]\n'
    '}\n'
)


# ---------------------------------------------------------------------------
# 1. DEFERRED disposition is accepted (reproduces & fixes the live halt)
# ---------------------------------------------------------------------------


def test_deferred_obligation_passes(tmp_path):
    v = _v()
    (tmp_path / "obligation_ledger.json").write_text(
        _OBLIGATION_LEDGER, encoding="utf-8"
    )
    # DEFERRED token in the SAME claim unit as the obligation ID, and phrased
    # so the exact-pair relation does NOT prematurely close it — this exercises
    # the DEFERRED-acceptance path specifically, not pair closure.
    coverage = (
        "OBL-AB-001 binding DEFERRED for follow-up review with documented "
        "reason; lane did not run in this mode.\n"
    )
    issues = v._validate_obligation_ledger_retention(tmp_path, coverage)
    assert issues == [], (
        "DEFERRED is a tracked, reasoned disposition — must not be flagged. "
        f"got: {issues}"
    )


# ---------------------------------------------------------------------------
# 2. APPENDIX_ONLY disposition is accepted
# ---------------------------------------------------------------------------


def test_appendix_only_obligation_passes(tmp_path):
    v = _v()
    (tmp_path / "obligation_ledger.json").write_text(
        _OBLIGATION_LEDGER, encoding="utf-8"
    )
    coverage = (
        "OBL-AB-001 binding APPENDIX_ONLY minor disposition moved to appendix "
        "with documented reason.\n"
    )
    issues = v._validate_obligation_ledger_retention(tmp_path, coverage)
    assert issues == [], f"APPENDIX_ONLY must not be flagged. got: {issues}"


# ---------------------------------------------------------------------------
# 3. Unmentioned obligation is still flagged (contract preserved) AND routed
#    to report_semantic_retention_risks.md by the aggregation layer rather
#    than the hard-fail set.
# ---------------------------------------------------------------------------


def test_unmentioned_obligation_still_flagged(tmp_path):
    v = _v()
    (tmp_path / "obligation_ledger.json").write_text(
        _OBLIGATION_LEDGER, encoding="utf-8"
    )

    # 3a. Validator-level: a genuinely unmentioned obligation (no disposition,
    # no report ID, no token) is STILL flagged. Silent-drop protection intact.
    coverage_text = (
        "A nearby token-flow issue is covered, but the exact msg.value/amount "
        "pair is never named and has no disposition."
    )
    direct = v._validate_obligation_ledger_retention(tmp_path, coverage_text)
    assert direct, "Unmentioned obligation MUST still be flagged by the gate."

    # 3b. Aggregation-level (degrade-not-halt): the report_index coverage
    # contract must route the unaccounted obligation to the human-review risks
    # artifact and NOT contribute it to the returned hard-fail set.
    (tmp_path / "report_coverage.md").write_text(
        "# Report Coverage Audit\n\n"
        "## Raw Candidate Ledger\n"
        "| Source File | Candidate ID / Label | Severity Signal | Status | "
        "Report ID / Refutation / Reason |\n"
        "|---|---|---|---|---|\n"
        "| depth_token_flow_findings.md | H-01 | High | PROMOTED | H-01 |\n",
        encoding="utf-8",
    )

    contract_issues = v._validate_report_coverage_semantic_contract(tmp_path)
    # The obligation issue must NOT appear in the hard-fail set returned to the
    # driver report_index gate.
    assert not any(
        "obligation retention" in i for i in contract_issues
    ), (
        "Obligation retention issue must degrade (risks file), not hard-halt. "
        f"got: {contract_issues}"
    )

    # It must instead be flagged in the human-review artifact (never dropped).
    risks = tmp_path / "report_semantic_retention_risks.md"
    assert risks.exists(), (
        "Unaccounted obligation must be flagged in "
        "report_semantic_retention_risks.md (silent-drop protection)."
    )
    body = risks.read_text(encoding="utf-8")
    assert "UNACCOUNTED-OBLIGATION" in body or "obligation retention" in body, (
        "Risks file must record the unaccounted obligation."
    )

    # And the full accounting wrapper must not surface the obligation issue as
    # a hard-fail either (this is the exact list the driver halts on).
    acct_issues = v._validate_report_coverage_accounting(tmp_path)
    assert not any(
        "obligation retention" in i for i in acct_issues
    ), (
        "report_coverage accounting must not carry the obligation issue into "
        f"the driver hard-fail set. got: {acct_issues}"
    )


# ---------------------------------------------------------------------------
# 4. Regression: report ID and refutation tokens still satisfy the gate.
# ---------------------------------------------------------------------------


def test_report_id_or_refutation_still_passes(tmp_path):
    v = _v()
    (tmp_path / "obligation_ledger.json").write_text(
        _OBLIGATION_LEDGER, encoding="utf-8"
    )

    # Report ID present in the relevant unit.
    by_report_id = v._validate_obligation_ledger_retention(
        tmp_path,
        "H-01 preserves OBL-AB-001 msg.value not validated against amount.",
    )
    assert by_report_id == [], f"report ID must satisfy gate. got: {by_report_id}"

    # Refutation token present (same claim unit as the obligation ID).
    by_refutation = v._validate_obligation_ledger_retention(
        tmp_path,
        "OBL-AB-001 msg.value/amount binding REFUTED with no reachable path.",
    )
    assert by_refutation == [], (
        f"refutation token must satisfy gate. got: {by_refutation}"
    )

    # Exact pair closure (existing path) still works.
    by_pair = v._validate_obligation_ledger_retention(
        tmp_path,
        "OBL-AB-001 msg.value is validated against amount before transfer.",
    )
    assert by_pair == [], f"exact pair closure must satisfy gate. got: {by_pair}"
