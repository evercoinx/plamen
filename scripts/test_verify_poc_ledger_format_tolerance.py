"""Verify PoC-ledger format tolerance + write-race resilience.

RCA: verify_INV-XXX.md shards failed the per-shard hard gate
(`_validate_poc_contract_for_rows`) on attempt 1 with
"INV-XXX missing PoC Attempt/Execution Result ledger", then passed on the
retry with the SAME file content. Root cause = write-completion race: the
worker returns "DONE" and the driver runs the gate the instant the file
appears, but the OS write buffer for the ledger sections is not yet flushed,
so the gate reads a partially-written file. The full file (with the canonical
"### PoC Attempt" / "### Execution Result" headings) confirms the write
completed by observation time — only the timing was wrong.

Fix layers:
  1. GATE RESILIENCE: on a section-miss, the gate re-reads the file after a
     short filesystem-sync delay before failing (bounded, mirrors the
     driver-level retry at I/O granularity). Eliminates the attempt-1 churn
     from the partial-write race.
  2. FORMAT TOLERANCE: `_has_poc_ledger_sections` accepts the markdown-heading
     form (canonical), the bold-field form (**PoC Attempt** / **Execution
     Result**), and a single combined heading
     ("### PoC Attempt & Execution Result"). All three are a present ledger.

The contract is preserved: a verify file with genuinely NO PoC accounting in
any form (no Attempt/Exec section, no Attempted/Compiled/Result) still FAILS.
A non-silent structural reclassification is still honored.

Run: pytest scripts/test_verify_poc_ledger_format_tolerance.py -q
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_validators as V  # noqa: E402


def _mkscratch(files: dict[str, str]) -> Path:
    sp = Path(tempfile.mkdtemp(prefix="plamen_pocledger_"))
    for name, body in files.items():
        (sp / name).write_text(body, encoding="utf-8")
    return sp


def _unit_row(fid: str, sev: str = "Medium") -> dict[str, str]:
    return {"finding id": fid, "poc class": "unit", "severity": sev}


# A real passing-PoC ledger in the verifier's CANONICAL markdown-heading form.
def _verify_canonical_headings(fid: str) -> str:
    return (
        f"# Verification: {fid}\n\n"
        "**Preferred Tag**: [POC-PASS]\n"
        "**Evidence Tag**: [POC-PASS]\n"
        "**Verdict**: CONFIRMED\n\n"
        "### PoC Attempt\n"
        "- PoC Required: YES\n"
        "- PoC Class: unit\n"
        "- Attempted: YES\n"
        "- Test File: tests/inv.rs\n"
        "- Command: `cargo test test_inv -- --exact`\n\n"
        "### Execution Result\n"
        "- Compiled: YES\n"
        "- Result: PASS\n"
        "- Output: assertion held; attacker extracts 15% excess\n"
    )


# The verifier's bold-FIELD form (no heading prefix) — observed format drift.
def _verify_bold_fields(fid: str) -> str:
    return (
        f"# Verification: {fid}\n\n"
        "**Preferred Tag**: [POC-PASS]\n"
        "**Evidence Tag**: [POC-PASS]\n"
        "**Verdict**: CONFIRMED\n\n"
        "**PoC Attempt**\n"
        "- PoC Required: YES\n"
        "- PoC Class: unit\n"
        "- Attempted: YES\n"
        "- Test File: tests/inv.rs\n"
        "- Command: `cargo test test_inv -- --exact`\n\n"
        "**Execution Result**\n"
        "- Compiled: YES\n"
        "- Result: PASS\n"
        "- Output: assertion held\n"
    )


# A single COMBINED heading covering both sections at once.
def _verify_combined_heading(fid: str) -> str:
    return (
        f"# Verification: {fid}\n\n"
        "**Preferred Tag**: [POC-PASS]\n"
        "**Evidence Tag**: [POC-PASS]\n"
        "**Verdict**: CONFIRMED\n\n"
        "### PoC Attempt & Execution Result\n"
        "- PoC Required: YES\n"
        "- PoC Class: unit\n"
        "- Attempted: YES\n"
        "- Test File: tests/inv.rs\n"
        "- Command: `cargo test test_inv -- --exact`\n"
        "- Compiled: YES\n"
        "- Result: PASS\n"
        "- Output: assertion held\n"
    )


# Genuinely NO PoC accounting: just a verdict, no Attempt/Exec section in any
# form, no Attempted/Compiled/Result fields. Contract MUST still fail this.
def _verify_no_ledger(fid: str) -> str:
    return (
        f"# Verification: {fid}\n\n"
        "**Preferred Tag**: [POC-PASS]\n"
        "**Evidence Tag**: [POC-PASS]\n"
        "**Verdict**: CONFIRMED\n\n"
        "## Execution Output\n"
        "The function can be called by anyone and the path is reachable.\n\n"
        "## Suggested Fix\n"
        "Add an access-control modifier.\n"
    )


# A non-silent STRUCTURAL reclassification: the verifier declared its own
# PoC Class as structural and skipped via STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION.
def _verify_structural_reclass(fid: str) -> str:
    return (
        f"# Verification: {fid}\n\n"
        "**Preferred Tag**: [CODE-TRACE]\n"
        "**Evidence Tag**: [CODE-TRACE]\n"
        "**Verdict**: CONTESTED\n\n"
        "### PoC Attempt\n"
        "- PoC Required: NO\n"
        "- PoC Class: structural\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION\n"
        "- Test File: N/A\n"
        "- Command: N/A\n\n"
        "### Execution Result\n"
        "- Compiled: N/A\n"
        "- Result: NOT_EXECUTED\n"
        "- Output: no executable harm assertion; structural observation only\n"
    )


# ---------------------------------------------------------------------------
# Format tolerance: real attempt-1 drift forms now PASS for a unit row.
# ---------------------------------------------------------------------------

def test_bold_field_ledger_passes_unit_row():
    sp = _mkscratch({"verify_INV-010.md": _verify_bold_fields("INV-010")})
    issues = V._validate_poc_contract_for_rows(sp, [_unit_row("INV-010")], "thorough")
    assert not any("INV-010" in i and "missing PoC Attempt" in i for i in issues), issues


def test_combined_heading_ledger_passes_unit_row():
    sp = _mkscratch({"verify_INV-011.md": _verify_combined_heading("INV-011")})
    issues = V._validate_poc_contract_for_rows(sp, [_unit_row("INV-011")], "thorough")
    assert not any("INV-011" in i and "missing PoC Attempt" in i for i in issues), issues


def test_has_poc_ledger_sections_helper_accepts_all_forms():
    assert V._has_poc_ledger_sections(_verify_canonical_headings("X")) == (True, True)
    assert V._has_poc_ledger_sections(_verify_bold_fields("X")) == (True, True)
    assert V._has_poc_ledger_sections(_verify_combined_heading("X")) == (True, True)


# ---------------------------------------------------------------------------
# Regression: canonical "### PoC Attempt" / "### Execution Result" still pass.
# ---------------------------------------------------------------------------

def test_canonical_headings_still_pass_unit_row():
    sp = _mkscratch({"verify_INV-001.md": _verify_canonical_headings("INV-001")})
    issues = V._validate_poc_contract_for_rows(sp, [_unit_row("INV-001")], "thorough")
    assert not any("INV-001" in i and "missing PoC Attempt" in i for i in issues), issues


# ---------------------------------------------------------------------------
# Contract preserved: genuinely-absent ledger STILL fails.
# ---------------------------------------------------------------------------

def test_no_ledger_still_fails_unit_row():
    sp = _mkscratch({"verify_INV-099.md": _verify_no_ledger("INV-099")})
    issues = V._validate_poc_contract_for_rows(sp, [_unit_row("INV-099")], "thorough")
    assert any(
        "INV-099" in i and "missing PoC Attempt/Execution Result ledger" in i
        for i in issues
    ), issues


# ---------------------------------------------------------------------------
# Classification honored: non-silent structural reclassification, no demand.
# ---------------------------------------------------------------------------

def test_structural_reclassification_honored():
    sp = _mkscratch({"verify_INV-050.md": _verify_structural_reclass("INV-050")})
    issues = V._validate_poc_contract_for_rows(sp, [_unit_row("INV-050")], "thorough")
    assert not any("INV-050" in i for i in issues), issues


# ---------------------------------------------------------------------------
# Write-completion race resilience: a file whose ledger sections are flushed a
# moment AFTER the gate first reads it must still PASS on attempt 1 (the gate
# re-reads after a sync delay instead of failing). This reproduces the exact
# RCA race: gate runs the instant the file appears with a partial body.
# ---------------------------------------------------------------------------

def test_delayed_ledger_flush_passes_on_attempt_1():
    sp = _mkscratch({})
    fid = "INV-010"
    path = sp / f"verify_{fid}.md"
    # Attempt-1 partial body: header present (passes size), ledger NOT yet flushed.
    partial = (
        f"# Verification: {fid}\n\n"
        "**Preferred Tag**: [POC-PASS]\n"
        "**Evidence Tag**: [POC-PASS]\n"
        "**Verdict**: CONFIRMED\n\n"
        "## Execution Output\n"
        "test output pending flush of the ledger sections...\n"
    )
    full = _verify_canonical_headings(fid)
    path.write_text(partial, encoding="utf-8")

    # Simulate the OS buffer flushing the ledger sections shortly after the
    # gate's first read (well within the gate's bounded re-read window).
    def _flush_later():
        time.sleep(0.15)
        path.write_text(full, encoding="utf-8")

    t = threading.Thread(target=_flush_later)
    t.start()
    try:
        issues = V._validate_poc_contract_for_rows(sp, [_unit_row(fid)], "thorough")
    finally:
        t.join()
    assert not any(fid in i and "missing PoC Attempt" in i for i in issues), issues


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
