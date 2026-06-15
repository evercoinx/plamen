"""Regression tests for the E2E-hardening bundle (audit findings RPT/GATE/VERIF/
PARSE/LLM/RECON/LIFECYCLE/PARITY + calibration/consolidation).

Every test locks in a behavior change from the post-DODO full-pipeline audit.
HOW-not-WHAT: synthetic IDs/titles stand in for any real finding.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import plamen_parsers as P
import plamen_types as T
import mechanical_verify as MV
from plamen_validators import _validate_report_body, _run_report_quality_gate


def _mkscratch(files: dict[str, str]) -> Path:
    sp = Path(tempfile.mkdtemp(prefix="plamen_e2e_"))
    for name, body in files.items():
        (sp / name).write_text(body, encoding="utf-8")
    return sp


def _manifest(findings):
    return {"findings": findings}


# ---------------------------------------------------------------------------
# GATE-2 / RPT-4: substantive Impact/PoC is telemetry, never flips ok; UNVERIFIED
# is exempt from the PoC-Result requirement.
# ---------------------------------------------------------------------------

def test_gate2_content_does_not_flip_ok():
    body = (
        "## High Findings\n\n### [H-01] Bug [VERIFIED]\n"
        "**Severity**: High\n**Location**: `src/V.sol:L10`\n"
        "**Description**: Something.\n"  # no Impact / PoC
    )
    res = _validate_report_body(body, _manifest([{"report_id": "H-01", "location": "src/V.sol:L10", "severity": "High"}]))
    assert res["ok"] is True
    assert any("Impact" in c or "PoC" in c for c in res["content"])  # surfaced as telemetry


def test_rpt4_unverified_exempt_from_poc_result():
    body = (
        "## Medium Findings\n\n### [M-01] Bug [UNVERIFIED]\n"
        "**Severity**: Medium\n**Location**: `src/V.sol`\n"
        "**Description**: A real description.\n**Impact**: Users can lose funds.\n"
    )
    res = _validate_report_body(body, _manifest([{"report_id": "M-01", "location": "src/V.sol", "severity": "Medium"}]))
    assert res["ok"] is True
    assert not any("PoC Result" in c for c in res["content"])  # UNVERIFIED: no PoC demanded


def test_rpt2_evidence_tag_satisfies_poc():
    body = (
        "## High Findings\n\n### [H-01] Bug [VERIFIED]\n"
        "**Severity**: High\n**Location**: `src/V.sol:L10`\n"
        "**Description**: x.\n**Impact**: theft.\n**Evidence Tag**: [CODE-TRACE]\n"
    )
    res = _validate_report_body(body, _manifest([{"report_id": "H-01", "location": "src/V.sol:L10", "severity": "High"}]))
    assert not any("PoC Result" in c for c in res["content"])


# ---------------------------------------------------------------------------
# GATE-3: marker leak stays HARD; blocked-count is soft.
# ---------------------------------------------------------------------------

def test_gate3_marker_leak_is_hard():
    sp = _mkscratch({})
    proj = sp / "proj"
    proj.mkdir()
    (proj / "AUDIT_REPORT.md").write_text(
        "# Security Audit Report\n\n## High Findings\n\n"
        "### [H-01] [REPORT-BLOCKED: x] Bug\n**Severity**: High\n"
        "**Location**: src/V.sol:L1\n**Impact**: theft\n**PoC Result**: pass\n",
        encoding="utf-8",
    )
    issues = _run_report_quality_gate(sp, str(proj))
    assert any("leaked into client report" in i for i in issues)


# ---------------------------------------------------------------------------
# RPT-3: section extractor does not truncate at an in-finding ## Impact heading.
# ---------------------------------------------------------------------------

def test_rpt3_section_not_truncated_at_impact_heading():
    body = (
        "## High Findings\n\n### [H-01] Bug [VERIFIED]\n"
        "**Severity**: High\n**Location**: `src/V.sol:L10`\n\n"
        "## Impact\n\nAn attacker drains the vault.\n\n"
        "## PoC Result\n\nTest passed.\n\n"
        "### [H-02] Other [VERIFIED]\n**Location**: `src/W.sol:L5`\n"
    )
    sec = P._section_for_report_id(body, "H-01")
    assert "An attacker drains the vault" in sec  # not truncated at ## Impact
    assert "H-02" not in sec  # still isolated from the next finding


# ---------------------------------------------------------------------------
# VERIF-3: property/accounting precedence over broad unit nouns.
# ---------------------------------------------------------------------------

def test_verif3_fee_plus_accounting_is_property():
    assert P.classify_poc_testability("accounting", "", "Fee accounting drift", "High") == "property"


def test_verif3_fee_alone_stays_unit():
    assert P.classify_poc_testability("", "", "Fee calculation off by one wei", "Low") == "unit"


def test_verif3_narrow_unit_wins_over_property():
    # access control (narrow unit) co-occurring with a property word stays unit
    assert P.classify_poc_testability("access control", "", "Reward claim missing onlyOwner", "High") == "unit"


# ---------------------------------------------------------------------------
# VERIF-5: AMBIGUOUS classification + integrity preserves prose (no demotion).
# ---------------------------------------------------------------------------

def test_verif5_ambiguous_on_nonisolated_mixed_result():
    out = "[PASS] test_a()\n[FAIL] test_b()\nSuite result: FAILED"
    assert MV._classify_evm_outcome(1, out, isolated=False) == "AMBIGUOUS"
    # isolated single-test run still classifies normally
    assert MV._classify_evm_outcome(1, "[FAIL] test_x()", isolated=True) == "FAIL"


def test_verif5_integrity_preserves_prose_on_ambiguous():
    state, eff = MV._classify_integrity("[POC-PASS]", "AMBIGUOUS")
    assert state == "MECHANICAL_UNAVAILABLE"
    assert "[POC-PASS]" in eff  # not demoted to CODE-TRACE


def test_verif2_evidence_tag_field_anchored():
    sp = _mkscratch({
        "verify_X.md": (
            "# Verify\n**Evidence Tag**: [CODE-TRACE]\n\n"
            "Reference table example: [POC-PASS] means proof.\n"  # must NOT win
        )
    })
    assert MV._extract_verifier_prose_tag(sp / "verify_X.md") == "[CODE-TRACE]"


# ---------------------------------------------------------------------------
# PARSE-1 / PARSE-2: empty interior cells preserved.
# ---------------------------------------------------------------------------

def test_parse1_skeptic_judge_blank_cell_preserved():
    text = (
        "| Finding ID | Original Severity | Final Severity | Decision | Rationale |\n"
        "|---|---|---|---|---|\n"
        "| H-01 | High |  | DOWNGRADE | reason here |\n"  # blank Final Severity
    )
    rows = P._parse_skeptic_judge_table(text)
    assert len(rows) == 1
    assert rows[0]["decision"] == "DOWNGRADE"  # index alignment held


# ---------------------------------------------------------------------------
# Calibration: High-impact on-chain finding not demoted by on-chain modifier.
# ---------------------------------------------------------------------------

def test_calibration_high_impact_not_onchain_demoted():
    vtxt = (
        "**Impact**: High\n**Likelihood**: High\n"
        "**Severity**: Critical\n"
        "This is an on-chain only exploit with no off-chain path.\n"
    )
    sev = P._enforce_severity_matrix(vtxt, {"severity": "Critical"})
    assert sev == "Critical"  # on-chain -1 must not pull High x High down to High


# ---------------------------------------------------------------------------
# LIFECYCLE-1: skeptic / crossbatch are non-critical (degrade-and-continue).
# ---------------------------------------------------------------------------

def test_lifecycle1_skeptic_crossbatch_noncritical():
    for plist in (T.SC_PHASES, getattr(T, "L1_PHASES", [])):
        for p in plist:
            if p.name in ("skeptic", "crossbatch"):
                assert p.critical is False, f"{p.name} must be non-critical"


def test_lifecycle3_l1_inventory_merge_bounded_below_chunk():
    l1 = {p.name: p for p in getattr(T, "L1_PHASES", [])}
    if "inventory" in l1 and "inventory_chunk_a" in l1:
        assert l1["inventory"].base_timeout_s <= l1["inventory_chunk_a"].base_timeout_s


# ---------------------------------------------------------------------------
# PARITY-7: codex_adapter fallback model map equals canonical.
# ---------------------------------------------------------------------------

def test_parity7_codex_fallback_map_matches_canonical():
    import codex_adapter as CA
    assert CA.CODEX_MODEL_TIERS["sonnet"] == T._CODEX_MODEL_MAP["sonnet"]
    assert CA.CODEX_MODEL_TIERS["opus"] == T._CODEX_MODEL_MAP["opus"]
    assert CA.CODEX_MODEL_TIERS["haiku"] == T._CODEX_MODEL_MAP["haiku"]
