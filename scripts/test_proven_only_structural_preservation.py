"""Tests for the PROVEN_ONLY structural-untestable preservation distinction.

Part (a) of Work Item 2: the proven-only "cap [CODE-TRACE] at Low" rule must
NOT blanket-cap genuinely structurally-untestable CONFIRMED findings whose
verifier declined to downgrade with a VALID PoC-ledger blocker. Weak/lazy
[CODE-TRACE] (a harness exists but no PoC was written; spec/docs-only;
NO_BUILD_ENVIRONMENT contradicted by a SUCCESS build) is STILL capped.

Conservative + additive: relative to the blanket cap this can only RAISE
severity in the verifier-blessed structural case; it never lowers anything and
is a no-op when proven_only is false or the finding carries a proof tag.

All fixtures are synthetic/generic (no protocol/token/contract/function names).

Run: pytest scripts/test_proven_only_structural_preservation.py -v
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def _v():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    if "plamen_validators" in sys.modules:
        del sys.modules["plamen_validators"]
    return importlib.import_module("plamen_validators")


def _scratch(tmp_path: Path, *, proven_only: bool, build_success: bool | None = None) -> Path:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "config.json").write_text(
        json.dumps({"proven_only": bool(proven_only)}), encoding="utf-8"
    )
    if build_success is not None:
        status = "SUCCESS" if build_success else "FAILED"
        (sp / "build_status.md").write_text(
            f"# Build Status\n\n**Status**: {status}\n", encoding="utf-8"
        )
    return sp


def _queue(sp: Path, rows: list[tuple[str, str, str]]) -> None:
    """rows = list of (finding_id, severity, poc_class)."""
    out = [
        "| Queue # | Finding ID | Severity | Title | PoC Class |",
        "|---------|------------|----------|-------|-----------|",
    ]
    for i, (fid, sev, pc) in enumerate(rows, start=1):
        out.append(f"| {i} | {fid} | {sev} | example finding | {pc} |")
    (sp / "verification_queue.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def _verify(sp: Path, fid: str, *, severity: str, verdict: str, tag: str,
            skip_reason: str, attempted: str = "NO") -> None:
    (sp / f"verify_{fid}.md").write_text(
        f"**Severity**: {severity}\n\n"
        f"**Verdict**: {verdict}\n\n"
        f"**Evidence Tag**: {tag}\n\n"
        "### PoC Attempt\n"
        "- PoC Required: YES\n"
        f"- Attempted: {attempted}\n"
        f"- PoC Not Attempted Because: {skip_reason}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# (a) Weak [CODE-TRACE] STILL capped (no regression)
# ---------------------------------------------------------------------------

def test_weak_unit_structural_skip_still_capped(tmp_path: Path):
    """Unit-class finding citing STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION:
    _valid_poc_skip is False for unit → weak → still capped at Low."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-1", "Medium", "unit")])
    _verify(sp, "INV-1", severity="Medium", verdict="CONFIRMED",
            tag="[CODE-TRACE]", skip_reason="STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION")
    assert V._expected_report_index_severities(sp).get("INV-1") == "Low"


def test_na_skip_reason_capped(tmp_path: Path):
    """N/A blocker on a [CODE-TRACE] finding → no valid blocker → capped."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-2", "High", "unit")])
    _verify(sp, "INV-2", severity="High", verdict="CONFIRMED",
            tag="[CODE-TRACE]", skip_reason="N/A")
    assert V._expected_report_index_severities(sp).get("INV-2") == "Low"


def test_no_build_env_contradicted_by_success_build_capped(tmp_path: Path):
    """NO_BUILD_ENVIRONMENT cited while build_status.md = SUCCESS → harness
    exists → not structurally untestable → capped."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True, build_success=True)
    _queue(sp, [("INV-3", "Medium", "structural")])
    _verify(sp, "INV-3", severity="Medium", verdict="CONFIRMED",
            tag="[CODE-TRACE]", skip_reason="NO_BUILD_ENVIRONMENT")
    assert V._expected_report_index_severities(sp).get("INV-3") == "Low"


def test_pure_spec_docs_only_capped(tmp_path: Path):
    """PURE_SPEC_OR_DOCS_ONLY is never a structural CONFIRMED harm → capped."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-4", "Medium", "structural")])
    _verify(sp, "INV-4", severity="Medium", verdict="CONFIRMED",
            tag="[CODE-TRACE]", skip_reason="PURE_SPEC_OR_DOCS_ONLY")
    assert V._expected_report_index_severities(sp).get("INV-4") == "Low"


# ---------------------------------------------------------------------------
# (b) Genuinely structural preserved (the fix)
# ---------------------------------------------------------------------------

def test_structural_class_structural_skip_preserved(tmp_path: Path):
    """structural-class CONFIRMED Medium [CODE-TRACE] +
    STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION → preserved at Medium."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-5", "Medium", "structural")])
    _verify(sp, "INV-5", severity="Medium", verdict="CONFIRMED",
            tag="[CODE-TRACE]", skip_reason="STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION")
    assert V._expected_report_index_severities(sp).get("INV-5") == "Medium"


def test_integration_deployment_only_preserved(tmp_path: Path):
    """integration-class + DEPLOYMENT_ONLY_REQUIRES_LIVE_EXTERNAL → preserved."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-6", "High", "integration")])
    _verify(sp, "INV-6", severity="High", verdict="CONFIRMED",
            tag="[CODE-TRACE]", skip_reason="DEPLOYMENT_ONLY_REQUIRES_LIVE_EXTERNAL")
    assert V._expected_report_index_severities(sp).get("INV-6") == "High"


def test_external_unmockable_preserved(tmp_path: Path):
    """EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS with no mock-feasibility prose →
    preserved (valid blocker)."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-7", "Medium", "integration")])
    _verify(sp, "INV-7", severity="Medium", verdict="CONFIRMED",
            tag="[CODE-TRACE]", skip_reason="EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS")
    assert V._expected_report_index_severities(sp).get("INV-7") == "Medium"


def test_external_mockable_capped(tmp_path: Path):
    """EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS but the verifier's own prose says
    the dependency could NOT be mocked in the same clause → invalid skip →
    capped. (reuses _negation_governs_keyword via _valid_poc_skip)."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-8", "Medium", "integration")])
    (sp / "verify_INV-8.md").write_text(
        "**Severity**: Medium\n\n"
        "**Verdict**: CONFIRMED\n\n"
        "**Evidence Tag**: [CODE-TRACE]\n\n"
        "The external dependency could not be mocked in this harness.\n\n"
        "### PoC Attempt\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS\n",
        encoding="utf-8",
    )
    assert V._expected_report_index_severities(sp).get("INV-8") == "Low"


# ---------------------------------------------------------------------------
# (c) Gating no-ops
# ---------------------------------------------------------------------------

def test_proven_only_false_no_cap(tmp_path: Path):
    """Same structural fixture with proven_only:false → unchanged (Medium)."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=False)
    _queue(sp, [("INV-9", "Medium", "unit")])
    _verify(sp, "INV-9", severity="Medium", verdict="CONFIRMED",
            tag="[CODE-TRACE]", skip_reason="STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION")
    # proven_only off → no cap at all even for a weak unit-class CODE-TRACE.
    assert V._expected_report_index_severities(sp).get("INV-9") == "Medium"


def test_poc_pass_never_capped(tmp_path: Path):
    """[POC-PASS] under proven_only → proof tag → never touched by the cap."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-10", "High", "unit")])
    _verify(sp, "INV-10", severity="High", verdict="CONFIRMED",
            tag="[POC-PASS]", skip_reason="N/A", attempted="YES")
    assert V._expected_report_index_severities(sp).get("INV-10") == "High"


# ---------------------------------------------------------------------------
# (d) Provenance acceptance
# ---------------------------------------------------------------------------

def test_provenance_accepts_structural_untestable_token(tmp_path: Path):
    V = _v()
    assert V._report_index_adjustment_reason_present("STRUCTURAL-UNTESTABLE(Medium)") is True
    # And it does NOT regress the empty / none cases.
    assert V._report_index_adjustment_reason_present("") is False
    assert V._report_index_adjustment_reason_present("-") is False


# ---------------------------------------------------------------------------
# (e) E2E genericized: 5 structural keep Medium, 2 POC-PASS keep Medium,
#     1 trusted-actor row untouched by THIS layer.
# ---------------------------------------------------------------------------

def test_e2e_five_structural_kept_medium(tmp_path: Path):
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    rows = []
    for i in range(1, 6):
        rows.append((f"INV-S{i}", "Medium", "structural"))
    for i in range(1, 3):
        rows.append((f"INV-P{i}", "Medium", "unit"))
    rows.append(("INV-T1", "Medium", "unit"))
    _queue(sp, rows)
    for i in range(1, 6):
        _verify(sp, f"INV-S{i}", severity="Medium", verdict="CONFIRMED",
                tag="[CODE-TRACE]",
                skip_reason="STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION")
    for i in range(1, 3):
        _verify(sp, f"INV-P{i}", severity="Medium", verdict="CONFIRMED",
                tag="[POC-PASS]", skip_reason="N/A", attempted="YES")
    _verify(sp, "INV-T1", severity="Medium", verdict="CONFIRMED",
            tag="[POC-PASS]", skip_reason="N/A", attempted="YES")
    out = V._expected_report_index_severities(sp)
    kept_medium = sum(1 for i in range(1, 6) if out.get(f"INV-S{i}") == "Medium")
    assert kept_medium == 5, f"expected 5 structural kept Medium, got {kept_medium}: {out}"
    assert out.get("INV-P1") == "Medium"
    assert out.get("INV-P2") == "Medium"


# ---------------------------------------------------------------------------
# (f) Production / on-chain proof-GRADE tags are never capped (PROD-* fix).
#     Regression for the workflow's self-caught bug: the proven-only cap gated
#     on has_mechanical_proof(), which excludes [PROD-ONCHAIN/SOURCE/FORK].
#     A finding confirmed against forked/live state (0.9-1.0 confidence) is
#     proof-grade and MUST be preserved, even with a unit poc_class + an
#     otherwise-weak skip reason that would cap a [CODE-TRACE] finding.
# ---------------------------------------------------------------------------

def test_prod_source_best_tag_preserved(tmp_path: Path):
    """[PROD-SOURCE] unit-class finding with an N/A skip (which WOULD cap a
    [CODE-TRACE]) → proof-grade short-circuits the cap → preserved at High."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-PS1", "High", "unit")])
    _verify(sp, "INV-PS1", severity="High", verdict="CONFIRMED",
            tag="[PROD-SOURCE]", skip_reason="N/A")
    assert V._expected_report_index_severities(sp).get("INV-PS1") == "High"


def test_prod_fork_best_tag_preserved(tmp_path: Path):
    """[PROD-FORK] verified against forked state → preserved at Medium."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-PF1", "Medium", "unit")])
    _verify(sp, "INV-PF1", severity="Medium", verdict="CONFIRMED",
            tag="[PROD-FORK]", skip_reason="N/A")
    assert V._expected_report_index_severities(sp).get("INV-PF1") == "Medium"


def test_prod_onchain_best_tag_preserved(tmp_path: Path):
    """[PROD-ONCHAIN] (highest confidence) → preserved at Critical."""
    V = _v()
    sp = _scratch(tmp_path, proven_only=True)
    _queue(sp, [("INV-PO1", "Critical", "unit")])
    _verify(sp, "INV-PO1", severity="Critical", verdict="CONFIRMED",
            tag="[PROD-ONCHAIN]", skip_reason="N/A")
    assert V._expected_report_index_severities(sp).get("INV-PO1") == "Critical"


def test_has_proof_grade_helper_recognizes_prod_but_mechanical_does_not(tmp_path: Path):
    """Unit guard on the two helpers: has_mechanical_proof stays narrow
    (test-pass only); has_proof_grade_evidence ORs in the PROD-* tags."""
    import plamen_types as T
    for tag in ("[PROD-ONCHAIN]", "[PROD-SOURCE]", "[PROD-FORK]"):
        assert T.has_proof_grade_evidence(f"x {tag} y") is True
        assert T.has_mechanical_proof(f"x {tag} y") is False
    # mechanical-pass tags satisfy both
    assert T.has_mechanical_proof("[POC-PASS]") is True
    assert T.has_proof_grade_evidence("[POC-PASS]") is True
    # plain code-trace satisfies neither
    assert T.has_mechanical_proof("[CODE-TRACE]") is False
    assert T.has_proof_grade_evidence("[CODE-TRACE]") is False
