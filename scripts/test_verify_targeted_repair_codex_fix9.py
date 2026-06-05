"""FIX #9: extend the targeted PoC-contract one-shot repair to also match the
Codex-direction failure shape, backend-gated to codex.

Before FIX #9, `verify_poc_contract_only_failed_ids` only matched the
Claude-direction "says Attempted:YES but lacks concrete Test File/Command"
sub-class. A Codex verify shard that failed the gate with the
"{fid} mandatory {class} PoC not attempted with valid blocker" sub-class was
NOT eligible for the cheap one-shot targeted repair, so it fell straight to the
expensive full verify-shard degrade-and-continue.

FIX #9: when `scratchpad` is supplied AND `cli_backend == "codex"`, the
mandatory-not-attempted sub-class is ALSO selected for the targeted repair.
This ONLY changes WHICH ids are eligible for the cheap repair path; the
underlying `_validate_poc_contract_for_rows` gate is UNTOUCHED (anti-fabrication
preserved). For claude (or absent config), behavior is unchanged.

Run: pytest scripts/test_verify_targeted_repair_codex_fix9.py -q
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_validators as V  # noqa: E402


_POC_PREFIX = "verify PoC contract: "
_ATTEMPTED_YES_SUB = "says Attempted:YES but lacks concrete Test File/Command"
_MANDATORY_SUB = "mandatory unit PoC not attempted with valid blocker"


def _mkscratch(backend: str | None) -> Path:
    sp = Path(tempfile.mkdtemp(prefix="plamen_vtr_fix9_"))
    if backend is not None:
        (sp / "config.json").write_text(
            json.dumps({"cli_backend": backend}), encoding="utf-8"
        )
    return sp


def _mandatory_missing(*fids: str) -> list[str]:
    subs = "; ".join(f"{fid} {_MANDATORY_SUB}" for fid in fids)
    return [_POC_PREFIX + subs]


# ---------------------------------------------------------------------------
# Core FIX #9 behavior: Codex-direction shape is selected ONLY for codex backend
# ---------------------------------------------------------------------------

def test_codex_mandatory_shape_is_selected_for_targeted_repair():
    """REPRODUCES THE ORIGINAL FAILURE: a Codex verify output failing with
    "mandatory unit PoC not attempted with valid blocker" must now be SELECTED
    for the cheap targeted repair (not the full re-run)."""
    sp = _mkscratch("codex")
    fids = V.verify_poc_contract_only_failed_ids(
        _mandatory_missing("F-1", "F-3"), sp
    )
    assert fids == ["F-1", "F-3"]


def test_codex_mandatory_shape_bracketed_id():
    sp = _mkscratch("codex")
    fids = V.verify_poc_contract_only_failed_ids(
        [_POC_PREFIX + f"[H-5] {_MANDATORY_SUB}"], sp
    )
    assert fids == ["H-5"]


def test_codex_mandatory_shape_dedup_order_preserved():
    sp = _mkscratch("codex")
    fids = V.verify_poc_contract_only_failed_ids(
        _mandatory_missing("F-2", "F-1", "F-2"), sp
    )
    assert fids == ["F-2", "F-1"]


def test_codex_mixed_attempted_yes_and_mandatory_both_selected():
    """Under codex, a combined entry mixing the Claude-direction and
    Codex-direction sub-classes is fully repairable by the same scoped hint."""
    sp = _mkscratch("codex")
    combined = _POC_PREFIX + (
        f"F-1 {_ATTEMPTED_YES_SUB}; F-2 {_MANDATORY_SUB}"
    )
    fids = V.verify_poc_contract_only_failed_ids([combined], sp)
    assert fids == ["F-1", "F-2"]


# ---------------------------------------------------------------------------
# Backend-AGNOSTIC (post-Claude-DODO fix): the mandatory-not-attempted class is
# now eligible on ALL backends. A Claude DODO run degraded shards because the
# verifier non-silently reclassified findings (exactly as the prompt asks) and
# got only generic whole-shard retries with no precise per-finding hint -> could
# never converge. The repair EXECUTION path is backend-agnostic, so the hint is
# now offered on Claude too. Gate stays exactly as hard (re-validated by the
# same _validate_poc_contract_for_rows on the repaired file).
# ---------------------------------------------------------------------------

def test_claude_backend_now_selects_mandatory_shape():
    """Under claude the mandatory-not-attempted class is now SELECTED for the
    cheap targeted repair (previously bailed -> guaranteed shard degrade)."""
    sp = _mkscratch("claude")
    assert V.verify_poc_contract_only_failed_ids(
        _mandatory_missing("F-1"), sp
    ) == ["F-1"]


def test_absent_config_selects_mandatory_shape():
    sp = _mkscratch(None)  # no config.json
    assert V.verify_poc_contract_only_failed_ids(
        _mandatory_missing("F-1"), sp
    ) == ["F-1"]


def test_no_scratchpad_arg_selects_mandatory_shape():
    """No scratchpad passed (original call shape) -> mandatory class now
    selected (backend-agnostic)."""
    assert V.verify_poc_contract_only_failed_ids(
        _mandatory_missing("F-1")
    ) == ["F-1"]


def test_codex_still_bails_on_non_poc_or_other_subclass():
    """Even under codex, a genuinely non-repairable sub-class still bails."""
    sp = _mkscratch("codex")
    # missing-ledger sub-class is not repairable by the scoped hint
    assert V.verify_poc_contract_only_failed_ids(
        [_POC_PREFIX + "F-2 missing PoC Attempt/Execution Result ledger"], sp
    ) == []
    # a schema issue mixed in is still not PoC-contract-only
    assert V.verify_poc_contract_only_failed_ids(
        ["verify schema: missing required verifier fields in verify_F-1.md"]
        + _mandatory_missing("F-2"),
        sp,
    ) == []


def test_codex_attempted_yes_class_still_works():
    """The always-on Claude-direction class continues to work under codex."""
    sp = _mkscratch("codex")
    fids = V.verify_poc_contract_only_failed_ids(
        [_POC_PREFIX + f"F-1 {_ATTEMPTED_YES_SUB}"], sp
    )
    assert fids == ["F-1"]


# ---------------------------------------------------------------------------
# Gate-strictness invariant: FIX #9 must NOT relax _validate_poc_contract_for_rows
# ---------------------------------------------------------------------------

def test_underlying_poc_contract_gate_strictness_unchanged():
    """FIX #9 only changes targeted-repair ELIGIBILITY (the selector). It must
    NOT touch the hard PoC-contract gate. Assert the gate still FAILS a codex
    verify file that declares `Attempted: NO` without a valid blocker for a
    locally-PoC-required (unit) row — i.e. it still emits the mandatory issue."""
    sp = _mkscratch("codex")
    # A unit-class, High-severity finding whose verify file skips the PoC with
    # NO valid blocker -> the gate must still flag it (anti-fabrication intact).
    verify_md = (
        "# verify F-9\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: High\n"
        "**Evidence Tag**: [CODE-TRACE]\n"
        "\n"
        "### PoC Attempt\n"
        "- PoC Required: YES\n"
        "- PoC Class: unit\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: N/A\n"
        "- Test File: N/A\n"
        "- Command: N/A\n"
        "\n"
        "### Execution Result\n"
        "- Compiled: N/A\n"
        "- Result: NOT_EXECUTED\n"
    )
    (sp / "verify_F-9.md").write_text(verify_md, encoding="utf-8")
    rows = [{
        "finding id": "F-9",
        "severity": "High",
        "poc class": "unit",
    }]
    issues = V._validate_poc_contract_for_rows(sp, rows, "thorough")
    # The hard gate still produces the mandatory-not-attempted issue: strictness
    # is unchanged by FIX #9 (which only affects the targeted-repair selector).
    assert any("mandatory" in i and "F-9" in i for i in issues), issues


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
