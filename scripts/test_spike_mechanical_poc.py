"""Tests for the Pattern 3 mechanical-PoC spike.

The spike is read-only and bounded. Tests cover:
- verify_*.md parsing (Test File field variations, Command-based extraction,
  bullet vs bold layouts, missing fields, malformed inputs)
- classification logic (LLM tag vs forge status)
- absence of forge does not crash the script
- bounded timeout / error classification
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

import spike_mechanical_poc as sp


@pytest.fixture
def scratch():
    p = Path(tempfile.mkdtemp(prefix="spike_test_"))
    yield p
    shutil.rmtree(p, ignore_errors=True)


def _write_verify(scratch: Path, fid: str, body: str) -> Path:
    p = scratch / f"verify_{fid}.md"
    p.write_text(body, encoding="utf-8")
    return p


# --- Parsing tests ----------------------------------------------------------


class TestParser:
    def test_full_ledger_extraction(self, scratch: Path):
        body = """# Verification: INV-002 — Sample finding

**Finding ID**: INV-002
**Severity**: High
**Verdict**: CONFIRMED
**Evidence Tag**: [POC-PASS]

## PoC Attempt

- **PoC Required**: YES
- **PoC Class**: unit
- **Attempted**: YES
- **PoC Not Attempted Because**: N/A
- **Test File**: `test/VerifyINV002.t.sol` — contract `VerifyINV002`, function `test_INV002_minReturnAmount_zero_bypass()`
- **Command**: `forge test --match-test "test_INV002_minReturnAmount_zero_bypass" --match-path "test/VerifyINV002.t.sol" -vvv`

## Execution Result

- **Result**: PASS
"""
        path = _write_verify(scratch, "INV-002", body)
        probe = sp.parse_verify_file(path)
        assert probe.finding_id == "INV-002"
        assert probe.llm_tag == "[POC-PASS]"
        assert probe.llm_verdict == "CONFIRMED"
        assert probe.poc_class == "unit"
        assert probe.test_file_resolved == "test/VerifyINV002.t.sol"
        assert probe.test_function == "test_INV002_minReturnAmount_zero_bypass"

    def test_test_file_field_path_only(self, scratch: Path):
        body = """# Verification: H-3

**Finding ID**: H-3
**Verdict**: CONFIRMED
**Evidence Tag**: [CODE-TRACE]

- **Test File**: `test/VerifyH3Poc.t.sol`
- **Command**: `forge test --match-test "test_H3_drain"`
"""
        path = _write_verify(scratch, "H-3", body)
        probe = sp.parse_verify_file(path)
        assert probe.test_file_resolved == "test/VerifyH3Poc.t.sol"
        assert probe.test_function == "test_H3_drain"

    def test_missing_test_file(self, scratch: Path):
        body = """# Verification: H-9

**Evidence Tag**: [CODE-TRACE]

- **Test File**: N/A
- **Command**: N/A
"""
        path = _write_verify(scratch, "H-9", body)
        probe = sp.parse_verify_file(path)
        assert probe.test_file_resolved is None
        assert probe.test_function is None

    def test_evidence_tag_with_downgrade_note(self, scratch: Path):
        body = """# Verification: H-1

**Evidence Tag**: [CODE-TRACE] (was [POC-PASS], integrity downgrade: No assertion found)

- **Test File**: N/A
"""
        path = _write_verify(scratch, "H-1", body)
        probe = sp.parse_verify_file(path)
        assert probe.llm_tag == "[CODE-TRACE]"

    def test_bold_layout_no_bullet_prefix(self, scratch: Path):
        body = """# Verification: M-05

**Finding ID**: M-05
**Verdict**: CONFIRMED
**Evidence Tag**: [POC-PASS]

**Test File**: `test/VerifyM5.t.sol`
**Command**: `forge test --match-test "test_M5"`
"""
        path = _write_verify(scratch, "M-05", body)
        probe = sp.parse_verify_file(path)
        assert probe.test_file_resolved == "test/VerifyM5.t.sol"
        assert probe.test_function == "test_M5"

    def test_plain_bullet_ledger_extraction(self, scratch: Path):
        body = """# Verification: H-87

Finding ID: H-87
Verdict: CONFIRMED
Evidence Tag: [POC-PASS]

## PoC Attempt

- PoC Class: unit
- Attempted: YES
- Test File: test/VerifyH87_MessageRouterAsymmetry.t.sol
- Command: forge test --match-test "test_H87" --match-path test/VerifyH87_MessageRouterAsymmetry.t.sol -vvv

## Execution Result

- Result: PASS
"""
        path = _write_verify(scratch, "H-87", body)
        probe = sp.parse_verify_file(path)
        assert probe.finding_id == "H-87"
        assert probe.llm_tag == "[POC-PASS]"
        assert probe.llm_verdict == "CONFIRMED"
        assert probe.poc_class == "unit"
        assert probe.test_file_resolved == "test/VerifyH87_MessageRouterAsymmetry.t.sol"
        assert probe.test_function == "test_H87"

    def test_read_error_does_not_raise(self, scratch: Path):
        # Verify that a missing file produces an EXEC_ERROR probe, not exception
        nonexistent = scratch / "verify_NONEXISTENT.md"
        probe = sp.parse_verify_file(nonexistent)
        assert probe.forge_status == "EXEC_ERROR"

    def test_no_finding_id_field_falls_back_to_filename(self, scratch: Path):
        body = """# Some header

**Evidence Tag**: [CODE-TRACE]
"""
        path = _write_verify(scratch, "CUSTOM-42", body)
        probe = sp.parse_verify_file(path)
        # Should fall back to deriving from filename or header
        assert "CUSTOM-42" in probe.finding_id or probe.finding_id == "CUSTOM-42"


# --- Classification tests ---------------------------------------------------


class TestClassification:
    def _probe(self, **kw) -> sp.FindingProbe:
        defaults = dict(
            verify_file="x.md", finding_id="X-1", llm_tag="",
            llm_verdict="", poc_class="", test_file_field="",
        )
        defaults.update(kw)
        return sp.FindingProbe(**defaults)

    def test_pass_pass_is_match(self):
        p = self._probe(llm_tag="[POC-PASS]", forge_status="PASS")
        match, rec = sp.classify_match(p)
        assert match == "MATCH"
        assert rec == "[POC-PASS]"

    def test_fail_fail_is_match(self):
        p = self._probe(llm_tag="[POC-FAIL]", forge_status="FAIL")
        match, rec = sp.classify_match(p)
        assert match == "MATCH"
        assert rec == "[POC-FAIL]"

    def test_code_trace_no_test_is_match(self):
        p = self._probe(llm_tag="[CODE-TRACE]", forge_status="NO_TEST_FILE")
        match, rec = sp.classify_match(p)
        assert match == "MATCH"
        assert rec == "[CODE-TRACE]"

    def test_poc_pass_but_compile_fail_is_mismatch(self):
        p = self._probe(llm_tag="[POC-PASS]", forge_status="COMPILE_FAIL")
        match, rec = sp.classify_match(p)
        assert match == "MISMATCH"
        assert rec == "[POC-FAIL]"

    def test_poc_pass_but_actually_fails_is_mismatch(self):
        p = self._probe(llm_tag="[POC-PASS]", forge_status="FAIL")
        match, rec = sp.classify_match(p)
        assert match == "MISMATCH"

    def test_code_trace_but_test_passes_is_mismatch(self):
        # Test was downgraded by integrity check, but actually passes
        # → mechanical recommends POC-PASS
        p = self._probe(llm_tag="[CODE-TRACE]", forge_status="PASS")
        match, rec = sp.classify_match(p)
        assert match == "MISMATCH"
        assert rec == "[POC-PASS]"

    def test_empty_llm_tag_undetermined(self):
        p = self._probe(llm_tag="", forge_status="PASS")
        match, rec = sp.classify_match(p)
        assert match == "UNDETERMINED"

    def test_not_run_status_undetermined(self):
        p = self._probe(llm_tag="[POC-PASS]", forge_status="NOT_RUN")
        match, rec = sp.classify_match(p)
        assert match == "UNDETERMINED"

    def test_timeout_recommends_code_trace(self):
        p = self._probe(llm_tag="[POC-PASS]", forge_status="TIMEOUT")
        match, rec = sp.classify_match(p)
        assert rec == "[CODE-TRACE]"


# --- Report writer tests ----------------------------------------------------


class TestReportWriter:
    def test_empty_probes_writes_zero_summary(self, scratch: Path):
        out = scratch / "report.md"
        sp.write_report([], out, scratch, scratch, 60)
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert "Total verify_*.md files: 0" in text
        assert "Recommendation" in text
        # JSON sidecar also written
        json_out = scratch / "report.json"
        assert json_out.exists()

    def test_match_pct_computation_in_report(self, scratch: Path):
        out = scratch / "report.md"
        probes = [
            sp.FindingProbe(
                verify_file="a.md", finding_id="A-1", llm_tag="[POC-PASS]",
                llm_verdict="CONFIRMED", poc_class="unit", test_file_field="",
                forge_status="PASS", match="MATCH", recommended_tag="[POC-PASS]",
            ),
            sp.FindingProbe(
                verify_file="b.md", finding_id="B-2", llm_tag="[POC-PASS]",
                llm_verdict="CONFIRMED", poc_class="unit", test_file_field="",
                forge_status="FAIL", match="MISMATCH", recommended_tag="[POC-FAIL]",
            ),
        ]
        sp.write_report(probes, out, scratch, scratch, 60)
        text = out.read_text(encoding="utf-8")
        # 1 match out of 2 determined → 50.0%
        assert "50.0%" in text
        # Decision path B (30-70%)
        assert "B:" in text


# --- Smoke tests for main() entry point -------------------------------------


class TestMainEntryPoint:
    def test_missing_scratchpad_returns_1(self, scratch: Path):
        rc = sp.main(["--scratchpad", str(scratch / "nope"),
                      "--project", str(scratch)])
        assert rc == 1

    def test_missing_project_returns_1(self, scratch: Path):
        rc = sp.main(["--scratchpad", str(scratch),
                      "--project", str(scratch / "nope")])
        assert rc == 1

    def test_no_verify_files_completes_cleanly(self, scratch: Path):
        # Setup minimal project with foundry.toml so the warning is suppressed
        proj = scratch / "proj"
        proj.mkdir()
        (proj / "foundry.toml").write_text("[profile.default]\n", encoding="utf-8")
        # Empty scratchpad: 0 verify files
        if not shutil.which("forge"):
            pytest.skip("forge not on PATH")
        rc = sp.main(["--scratchpad", str(scratch),
                      "--project", str(proj),
                      "--output", str(scratch / "out.md")])
        assert rc == 0
        assert (scratch / "out.md").exists()
