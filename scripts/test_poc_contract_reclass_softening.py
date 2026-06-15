"""Claude DODO verify shards degraded on the PoC-contract gate. Grounded in the
REAL failing ledgers from that run:

  FAIL (gate BUG #1): PoC Class: structural  (queue said `property`; reclassified ...)
                      + STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION   [H-83/H-108/H-46/H-51]
  FAIL (gate BUG #2): PoC Class: integration (queue says `unit`; reclassified ...)
                      + DEPLOYMENT_ONLY_REQUIRES_LIVE_EXTERNAL     [H-55/H-56]
  FAIL (correct):     PoC Class: property (UNCHANGED) + STRUCTURAL skip  [H-106/H-107]
  PASS (correct):     PoC Class: structural (bare) + STRUCTURAL skip     [H-101/H-105]

Bug #1: the softening did an EXACT-match `declared in {"structural","integration"}`
so the trailing justification text the prompt ASKS FOR defeated it. Bug #2: skip
validity was evaluated against the QUEUE class only, so a reclassify-to-
integration + DEPLOYMENT_ONLY (valid for integration) was rejected.

Fix: leading-token match on the declared class + evaluate _valid_poc_skip
against the DECLARED class. Anti-gaming floor preserved: a ledger that leaves
PoC Class as unit/property is still a silent bypass and still fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_validators as V  # noqa: E402


_QUEUE_HDR = (
    "| Finding ID | Severity | Title | Location | PoC Class |\n"
    "|---|---|---|---|---|\n"
)


def _queue(scratchpad: Path, fid: str, sev: str, poc_class: str):
    (scratchpad / "verification_queue.md").write_text(
        _QUEUE_HDR + f"| {fid} | {sev} | t | F.sol:1 | {poc_class} |\n",
        encoding="utf-8",
    )


def _verify(scratchpad: Path, fid: str, ledger_class: str, skip_code: str):
    (scratchpad / f"verify_{fid}.md").write_text(
        f"# Verify {fid}\n\n"
        f"**Verdict**: CONTESTED\n"
        f"**Evidence Tag**: [CODE-TRACE]\n\n"
        f"## PoC Attempt\n"
        f"- PoC Required: NO\n"
        f"- PoC Class: {ledger_class}\n"
        f"- Attempted: NO\n"
        f"- PoC Not Attempted Because: {skip_code}\n"
        f"- Test File: N/A\n"
        f"- Command: N/A\n\n"
        f"### Execution Result\n"
        f"- Compiled: N/A\n"
        f"- Result: NOT_EXECUTED\n"
        f"- Evidence Tag: [CODE-TRACE]\n",
        encoding="utf-8",
    )


def _run_gate(tmp_path: Path, fid: str, queue_class: str,
              ledger_class: str, skip_code: str, sev: str = "Medium"):
    _queue(tmp_path, fid, sev, queue_class)
    _verify(tmp_path, fid, ledger_class, skip_code)
    rows = [{"finding id": fid, "severity": sev, "poc class": queue_class}]
    return V._validate_poc_contract_for_rows(tmp_path, rows, "thorough")


# ── Bug #1: reclassified-to-structural WITH justification now PASSES ──────────

def test_structural_reclass_with_justification_passes(tmp_path):
    issues = _run_gate(
        tmp_path, "H-83", queue_class="property",
        ledger_class="structural  (queue said `property`; reclassified — no "
                     "executable harm assertion exists)",
        skip_code="STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION",
    )
    assert issues == [], (
        "non-silent structural reclassification with the justification text the "
        "prompt asks for must be honored (was BUG #1 false-FAIL)"
    )


# ── Bug #2: reclassified-to-integration + DEPLOYMENT_ONLY now PASSES ──────────

def test_integration_reclass_deployment_skip_passes(tmp_path):
    issues = _run_gate(
        tmp_path, "H-55", queue_class="unit",
        ledger_class="integration (queue says `unit`; reclassified — harm is a "
                     "deployment-process race needing a live external)",
        skip_code="DEPLOYMENT_ONLY_REQUIRES_LIVE_EXTERNAL",
    )
    assert issues == [], (
        "reclassify-to-integration + DEPLOYMENT_ONLY (valid for integration) "
        "must be honored (was BUG #2 false-FAIL on the queue class)"
    )


# ── Anti-gaming floor: bare unit/property + structural skip STILL FAILS ───────

def test_unchanged_property_class_structural_skip_still_fails(tmp_path):
    issues = _run_gate(
        tmp_path, "H-106", queue_class="property",
        ledger_class="property",  # NOT reclassified — silent bypass
        skip_code="STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION",
    )
    assert any("H-106" in i for i in issues), (
        "leaving PoC Class as property while skipping structurally is a SILENT "
        "bypass and must still fail (anti-gaming floor)"
    )


def test_bare_structural_still_passes(tmp_path):
    issues = _run_gate(
        tmp_path, "H-101", queue_class="property",
        ledger_class="structural",  # bare, no justification
        skip_code="STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION",
    )
    assert issues == [], "bare structural reclassification still passes"


def test_unit_deployment_skip_without_reclass_still_fails(tmp_path):
    # unit queue + unit ledger + DEPLOYMENT_ONLY -> invalid for unit, no
    # reclassification -> must fail (no integration declared).
    issues = _run_gate(
        tmp_path, "H-200", queue_class="unit",
        ledger_class="unit",
        skip_code="DEPLOYMENT_ONLY_REQUIRES_LIVE_EXTERNAL",
    )
    assert any("H-200" in i for i in issues), (
        "unit finding skipping via DEPLOYMENT_ONLY without declaring "
        "integration must still fail"
    )


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
