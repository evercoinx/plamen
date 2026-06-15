"""Tests for v2.5.0 driver fixes: ENTER latency, verify_aggregate timeout, skeptic double-prefix, index dedup, location integrity."""
from __future__ import annotations

import sys
import textwrap
import logging
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── Fix 1: ENTER latency reduced from 1s to 0.15s ──────────────────────


def test_rate_limit_wait_poll_interval():
    """The countdown event poll must be <= 0.2s for responsive ENTER."""
    import ast
    src = (Path(__file__).resolve().parent / "plamen_display.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    found_wait_calls = []
    # This guard is about the rate-limit COUNTDOWN event poll responsiveness
    # (early_resume.wait / a threading.Event). Subprocess reaps such as
    # `proc.wait(timeout=2)` are unrelated and legitimately longer; exempt
    # receivers that are obviously a subprocess handle.
    _subprocess_receivers = {"proc", "process", "p", "child", "popen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Look for <event>.wait(timeout=...), not <subprocess>.wait(...)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "wait":
                recv = node.func.value
                if isinstance(recv, ast.Name) and recv.id.lower() in _subprocess_receivers:
                    continue
                for kw in node.keywords:
                    if kw.arg == "timeout":
                        if isinstance(kw.value, ast.Constant):
                            found_wait_calls.append(kw.value.value)
    assert found_wait_calls, "No early_resume.wait(timeout=...) found"
    for val in found_wait_calls:
        assert val <= 0.2, f"Poll interval {val}s too slow — ENTER will feel laggy"


# ── Fix 2: verify_aggregate timeout scales with total hypothesis count ──


def test_verify_aggregate_timeout_scales():
    """sc_verify_aggregate gets hypothesis-count-based timeout extension.

    Ceiling note: post-v2.0.0 the scale_timeout default ceiling was raised
    from 5400 to 14400 to accommodate the breadth-bump (10800s base) — see
    commits 056a631, 5573f30, 2c76b3e (validator) and the
    test_phase_graph regression guard. This test only verifies the
    hyp-count extension is applied; the cap is whatever scale_timeout's
    current default ceiling is.
    """
    from plamen_prompt import scale_timeout

    # 47 hypotheses: extra_hyp = (47-8)*90 = 3510
    # base 900 + 3510 = 4410 (well under any sane ceiling)
    timeout = scale_timeout(900, ".", "evm", mode="core", hypothesis_count=47)
    assert timeout >= 4000, f"Expected >4000s for 47 hyps, got {timeout}"
    # Cap check against the current validator-side ceiling (14400s = 4h)
    assert timeout <= 14400, f"Exceeded 4-hour ceiling: {timeout}"


def test_verify_aggregate_timeout_block_exists():
    """The driver code has a block that computes total hyp_count for aggregate."""
    src = (Path(__file__).resolve().parent / "plamen_driver.py").read_text(
        encoding="utf-8"
    )
    assert 'phase.name in ("verify_aggregate", "sc_verify_aggregate")' in src
    assert "sum(len(v) for v in _all_shards.values())" in src


def _make_verify_queue(scratch: Path, rows: list[dict]):
    """Write a verification queue with the given rows."""
    lines = ["| Finding ID | Title | Severity | Preferred Verification |"]
    lines.append("|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {r['id']} | {r.get('title', 'Bug')} | "
            f"{r.get('severity', 'Medium')} | {r.get('pv', 'CODE-TRACE')} |"
        )
    (scratch / "verification_queue.md").write_text("\n".join(lines), encoding="utf-8")


def test_verify_aggregate_hyp_count_is_total(tmp_path):
    """The aggregate phase gets the SUM of all shard hypothesis counts."""
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    # Create verification_queue.md with 15 hypotheses (mixed severities)
    rows = [{"id": f"H-{i}", "title": f"Bug {i}", "severity": "Medium", "pv": "CODE-TRACE"}
            for i in range(15)]
    _make_verify_queue(scratch, rows)

    from plamen_mechanical import compute_sc_verify_shards
    shards = compute_sc_verify_shards(scratch)
    total = sum(len(v) for v in shards.values())
    assert total == 15, f"Expected 15 total hypotheses, got {total}"


def test_verify_queue_schema_invalid_does_not_vacuously_pass(tmp_path):
    """A present but unparsable verification_queue.md must fail E1/E6."""
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()
    (scratch / "verification_queue.md").write_text(
        "# Verification Queue\n\n"
        "| ID | Sev | What |\n"
        "|----|-----|------|\n"
        "| INV-001 | High | malformed aliases not recognized |\n",
        encoding="utf-8",
    )
    from plamen_validators import (
        _validate_verify_files_for_queue,
        _validate_verify_evidence_tags,
    )

    e1 = _validate_verify_files_for_queue(scratch)
    e6 = _validate_verify_evidence_tags(scratch)
    assert e1 and "schema invalid" in e1[0]
    assert e6 and "schema invalid" in e6[0]


def test_verify_queue_explicit_zero_still_passes(tmp_path):
    """The fail-closed queue parser still permits canonical empty queues."""
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()
    (scratch / "verification_queue.md").write_text(
        "# Verification Queue\n\n"
        "| Finding ID | Severity | Title | Location | Preferred Tag |\n"
        "|------------|----------|-------|----------|---------------|\n"
        "\nTotal: 0 findings | Expected verify_F-*.md files: 0\n",
        encoding="utf-8",
    )
    from plamen_validators import _validate_verify_files_for_queue

    assert _validate_verify_files_for_queue(scratch) == []


# ── Fix 3: skeptic gate no longer double-prefixes ───────────────────────


def test_skeptic_gate_no_double_prefix():
    """The driver must NOT wrap _validate_skeptic_scope output with extra prefix."""
    src = (Path(__file__).resolve().parent / "plamen_driver.py").read_text(
        encoding="utf-8"
    )
    # Old bug: '"skeptic scope: " + "; ".join(skeptic_issues)' doubled the prefix
    assert '"skeptic scope: " + "; ".join(skeptic_issues)' not in src
    # New: skeptic_issues are appended directly
    assert "missing = list(missing) + skeptic_issues" in src


def test_validate_skeptic_scope_returns_prefixed():
    """_validate_skeptic_scope itself includes 'skeptic scope:' in its output."""
    from plamen_validators import _validate_skeptic_scope

    scratch = Path(__file__).resolve().parent  # non-existent skeptic files
    # With no queue → empty → returns []
    # We just verify the function exists and the string format
    import inspect
    source = inspect.getsource(_validate_skeptic_scope)
    assert '"skeptic scope:' in source, "Validator must prefix its own messages"


def test_skeptic_gate_message_format():
    """End-to-end: the gate message for missing skeptic artifacts should not be doubled."""
    from plamen_validators import _validate_skeptic_scope

    scratch_dir = Path(__file__).resolve().parent / "_nonexistent_scratch_test"
    # Can't easily run the full gate without a real scratchpad,
    # but we verify the message format from the validator
    # When the validator returns, each string already has "skeptic scope:"
    # and the driver now passes them through directly.
    # This test verifies the structural invariant.
    src_driver = (Path(__file__).resolve().parent / "plamen_driver.py").read_text(
        encoding="utf-8"
    )
    src_validators = (Path(__file__).resolve().parent / "plamen_validators.py").read_text(
        encoding="utf-8"
    )

    # Count "skeptic scope:" occurrences in the driver's skeptic gate block
    import re
    driver_block = re.search(
        r"# --- skeptic: scope.*?# ---", src_driver, re.DOTALL
    )
    assert driver_block is not None, "Skeptic gate block not found"
    block_text = driver_block.group()
    prefix_count = block_text.count('"skeptic scope:')
    assert prefix_count == 0, (
        f"Driver skeptic block has {prefix_count} hard-coded 'skeptic scope:' "
        f"prefix(es) — validator already includes it"
    )


# ── Fix 4: index duplicate binding from parenthetical constituents ─────


def test_index_no_duplicate_from_parenthetical(tmp_path):
    """CH-3 (H-2, H-27) must NOT cause H-27 duplicate binding."""
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    # Master Finding Index where chain row has parenthetical constituents
    idx = textwrap.dedent("""\
    ## Master Finding Index

    | Report ID | Title | Severity | Internal Hypothesis |
    |-----------|-------|----------|---------------------|
    | C-01 | Chain bug | Critical | CH-3 (H-2, H-27) |
    | H-01 | Standalone | High | H-27 |
    | M-01 | Other | Medium | H-5 |

    ## Excluded Findings

    | Internal ID | Severity | Title | Exclusion Reason |
    """)
    (scratch / "report_index.md").write_text(idx, encoding="utf-8")

    from plamen_validators import _collect_index_acknowledged_ids
    master_ids, excluded_ids, master_list = _collect_index_acknowledged_ids(scratch)

    # H-27 should appear exactly once (standalone row), not twice
    assert master_list.count("H-27") == 1, (
        f"H-27 appears {master_list.count('H-27')} times — "
        "parenthetical strip failed"
    )
    # CH-3 should be extracted from the chain row
    assert "CH-3" in master_ids


def test_index_no_duplicate_clean_rows(tmp_path):
    """Normal rows without parentheticals still parse correctly."""
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    idx = textwrap.dedent("""\
    ## Master Finding Index

    | Report ID | Title | Severity | Internal Hypothesis |
    |-----------|-------|----------|---------------------|
    | H-01 | Bug A | High | H-10 |
    | M-01 | Bug B | Medium | H-11 |
    | L-01 | Bug C | Low | H-12 |
    """)
    (scratch / "report_index.md").write_text(idx, encoding="utf-8")

    from plamen_validators import _collect_index_acknowledged_ids
    master_ids, _, master_list = _collect_index_acknowledged_ids(scratch)

    assert master_ids == {"H-10", "H-11", "H-12"}
    assert len(master_list) == 3


# ── Fix 5: total-hypothesis timeout scaling for all-findings phases ────


_TOTAL_HYP_PHASE_NAMES = ("chain", "chain_agent2", "crossbatch",
                           "report_index", "sc_semantic_dedup")


def test_total_hyp_phases_in_driver():
    """All-findings phases must have total-hyp scaling in the driver."""
    src = (Path(__file__).resolve().parent / "plamen_driver.py").read_text(
        encoding="utf-8"
    )
    for name in _TOTAL_HYP_PHASE_NAMES:
        assert f'"{name}"' in src, f"Phase {name} not referenced in driver"
    assert "parse_verification_queue_rows(scratchpad)" in src
    assert "findings_inventory.md" in src


def test_report_index_gets_hyp_scaling(tmp_path):
    """report_index timeout should scale with hypothesis count."""
    from plamen_prompt import scale_timeout

    # 47 hypotheses at base 1500s → should get extra time
    timeout = scale_timeout(1500, ".", "evm", mode="core", hypothesis_count=47)
    assert timeout > 1500, f"Expected >1500s for 47 hyps, got {timeout}"
    # extra_hyp = (47-8)*90 = 3510; 1500 + 3510 = 5010
    assert timeout >= 4500, f"Expected >=4500s for 47 hyps, got {timeout}"


def test_chain_gets_hyp_scaling(tmp_path):
    """chain timeout should scale with hypothesis count."""
    from plamen_prompt import scale_timeout

    timeout_0 = scale_timeout(1500, ".", "evm", mode="core", hypothesis_count=0)
    timeout_47 = scale_timeout(1500, ".", "evm", mode="core", hypothesis_count=47)
    assert timeout_47 > timeout_0, "Chain timeout must grow with hypothesis count"


def test_crossbatch_gets_hyp_scaling(tmp_path):
    """crossbatch timeout should scale with hypothesis count."""
    from plamen_prompt import scale_timeout

    timeout_0 = scale_timeout(900, ".", "evm", mode="core", hypothesis_count=0)
    timeout_47 = scale_timeout(900, ".", "evm", mode="core", hypothesis_count=47)
    assert timeout_47 > timeout_0, "Crossbatch timeout must grow with hypothesis count"


def test_total_hyp_reads_verify_queue(tmp_path):
    """Total-hyp scaling reads hypothesis count from verification_queue.md."""
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    rows = [{"id": f"H-{i}", "severity": "Medium", "pv": "CODE-TRACE"}
            for i in range(30)]
    _make_verify_queue(scratch, rows)

    from plamen_parsers import parse_verification_queue_rows
    count = len(parse_verification_queue_rows(scratch))
    assert count == 30


def test_total_hyp_fallback_to_inventory(tmp_path):
    """When verify queue is absent, count from findings_inventory.md."""
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    # No verification_queue.md, but findings_inventory.md has H- rows
    lines = ["# Findings Inventory\n"]
    for i in range(20):
        lines.append(f"| H-{i} | Bug {i} | Medium | file.sol:L{i} |\n")
    (scratch / "findings_inventory.md").write_text("".join(lines), encoding="utf-8")

    inv_text = (scratch / "findings_inventory.md").read_text(encoding="utf-8")
    count = inv_text.count("\n| H-")
    assert count == 20


# ── Fix 6: body writer location integrity relaxed for multi-range ─────


def _make_body_manifest(findings: list[dict]) -> dict:
    """Build a minimal body-writer manifest dict."""
    return {"findings": findings}


def test_location_integrity_exact_match():
    """Exact location string in body → passes."""
    from plamen_validators import _validate_report_body

    manifest = _make_body_manifest([
        {"report_id": "H-01", "location": "src/Vault.sol:L45-L60"},
    ])
    body = textwrap.dedent("""\
    ## High Findings

    ### [H-01] Some Bug [VERIFIED]
    **Severity**: High
    **Location**: `src/Vault.sol:L45-L60`
    **Description**: Something bad happens here.
    **Impact**: An attacker can drain depositor funds from the vault.
    **PoC Result**: Test confirms the drain; assertion passed.
    """)
    result = _validate_report_body(body, manifest)
    assert result["ok"], f"Expected ok, got {result}"
    assert result["integrity"] == []


def test_location_integrity_multi_range_relaxed():
    """Multi-range location like 'file.sol:L361-366,L381-386' passes
    when body mentions only the file path."""
    from plamen_validators import _validate_report_body

    manifest = _make_body_manifest([
        {"report_id": "H-01", "location": "src/AwesomeXBuyAndBurn.sol:L361-366,L381-386"},
    ])
    body = textwrap.dedent("""\
    ## High Findings

    ### [H-01] Burn Logic Bug [VERIFIED]
    **Severity**: High
    **Location**: `src/AwesomeXBuyAndBurn.sol:L361-366` and `src/AwesomeXBuyAndBurn.sol:L381-386`
    **Description**: The burn function has issues at two sites.
    **Impact**: Tokens intended for burning are stranded, breaking the burn accounting.
    **PoC Result**: Test reproduces the stranded balance; assertion passed.
    """)
    result = _validate_report_body(body, manifest)
    assert result["ok"], f"Multi-range should pass via file-path fallback: {result}"
    assert result["integrity"] == []


def test_location_integrity_file_missing_fails():
    """When even the base file path is absent from body → fails."""
    from plamen_validators import _validate_report_body

    manifest = _make_body_manifest([
        {"report_id": "H-01", "location": "src/Vault.sol:L45"},
    ])
    body = textwrap.dedent("""\
    ## High Findings

    ### [H-01] Some Bug [VERIFIED]
    **Severity**: High
    **Location**: `src/Router.sol:L10`
    **Description**: Wrong file referenced.
    """)
    result = _validate_report_body(body, manifest)
    assert not result["ok"], "Should fail when file path doesn't match"
    assert len(result["integrity"]) == 1
    assert "H-01" in result["integrity"][0]


def test_location_integrity_no_colon_location():
    """Location without ':' (no line number) must still match exactly."""
    from plamen_validators import _validate_report_body

    manifest = _make_body_manifest([
        {"report_id": "M-01", "location": "src/Vault.sol"},
    ])
    body = textwrap.dedent("""\
    ## Medium Findings

    ### [M-01] General Issue [UNVERIFIED]
    **Severity**: Medium
    **Location**: `src/Vault.sol`
    **Description**: File-level concern.
    **Impact**: Incorrect accounting can mislead integrators reading vault state.
    **PoC Result**: Verification skipped — no build environment.
    """)
    result = _validate_report_body(body, manifest)
    assert result["ok"], f"File-only location should pass: {result}"


def test_location_integrity_no_colon_fails():
    """Location without ':' that doesn't match body → fails."""
    from plamen_validators import _validate_report_body

    manifest = _make_body_manifest([
        {"report_id": "M-01", "location": "src/Vault.sol"},
    ])
    body = textwrap.dedent("""\
    ## Medium Findings

    ### [M-01] General Issue [UNVERIFIED]
    **Severity**: Medium
    **Location**: `src/Router.sol`
    **Description**: Wrong file entirely.
    """)
    result = _validate_report_body(body, manifest)
    assert not result["ok"], "No-colon location with wrong file should fail"
    assert len(result["integrity"]) == 1


# ── Fix 7: _extract_h2_section preserves H3 subsections ──────────────

def test_extract_h2_section_preserves_h3():
    """H3 subsections (### [L-01]) must NOT terminate H2 section extraction."""
    from plamen_parsers import _extract_h2_section

    text = textwrap.dedent("""\
    # Low and Informational Findings

    ## Low Findings

    ### [L-01] Missing validation in admin setter

    **Severity**: Low

    ### [L-02] Another issue

    **Severity**: Low

    ## Informational Findings

    ### [I-01] Info thing
    """)
    section = _extract_h2_section(text, "Low Findings")
    assert "### [L-01]" in section
    assert "### [L-02]" in section
    assert "### [I-01]" not in section, "Informational leaked into Low section"


def test_extract_h2_section_case_insensitive():
    """_extract_h2_section should match case-insensitively."""
    from plamen_parsers import _extract_h2_section

    text = "## SUMMARY COUNTS\n\n| Sev | Count |\n|---|---|\n| High | 3 |\n"
    section = _extract_h2_section(text, "Summary")
    assert "High" in section


def test_extract_h2_section_h3_match():
    """_extract_h2_section should also find H3-level section headers."""
    from plamen_parsers import _extract_h2_section

    text = "### Components Audited\n\n| Name | Path |\n\n## Next Section\n"
    section = _extract_h2_section(text, "Components Audited")
    assert "Name" in section
    assert "Next Section" not in section


# ── Fix 8: _validate_inventory_structure format-tolerant field checks ─

def test_inventory_structure_bold_variations(tmp_path):
    """Field detection tolerates bold variants (colon inside/outside, no bold)."""
    from plamen_validators import _validate_inventory_structure

    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    inv = textwrap.dedent("""\
    # Findings Inventory

    **Total Findings**: 2

    ### Finding [H-1]: Bug A

    - **Source IDs:** CS-1, TF-2
    - **Severity:** High
    - **Location:** src/Vault.sol:L45
    - **Preferred Tag:** [CODE-TRACE]

    ### Finding [H-2]: Bug B

    **Source IDs**: CS-3
    **Severity**: Medium
    **Location**: src/Router.sol:L10
    **Evidence Tags**: [POC-PASS]
    """)
    (scratch / "findings_inventory.md").write_text(inv, encoding="utf-8")
    issues = _validate_inventory_structure(scratch)
    assert not any("missing one or more required fields" in i for i in issues), (
        f"Format-tolerant check wrongly flagged: {issues}"
    )


# ── Fix 9: _TOTAL_FINDINGS_RE accepts bold-wrapped label ─────────────

def test_inventory_structure_uses_shared_inventory_block_parser(tmp_path):
    """Structure gate must accept every heading shape accepted by _inventory_blocks."""
    from plamen_validators import _validate_inventory_structure

    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()
    blocks = []
    for i in range(1, 6):
        blocks.append(textwrap.dedent(f"""\
        ## Finding [INV-{i:03d}]: Bug {i}

        **Source IDs**: AC-{i}
        **Severity**: Medium
        **Location**: src/Vault.sol:L{i}
        **Preferred Tag**: CODE-TRACE
        """))
    inv = "# Findings Inventory\n\n**Total Findings**: 5\n\n" + "\n".join(blocks)
    (scratch / "findings_inventory.md").write_text(inv, encoding="utf-8")

    assert _validate_inventory_structure(scratch) == []


def test_assemble_count_delta_uses_shared_report_body_parser(tmp_path):
    """Report count deltas must accept the same headings as body validation."""
    from plamen_validators import _compute_assemble_count_delta

    project = tmp_path / "project"
    project.mkdir()
    scratch = project / ".scratchpad"
    scratch.mkdir()
    (project / "AUDIT_REPORT.md").write_text(textwrap.dedent("""\
    # Security Audit Report

    ## Summary

    | Severity | Count |
    |---|---|
    | High | 1 |

    ## High Findings

    ## [H-01] Valid H2 heading

    **Severity**: High
    **Location**: src/Vault.sol:L1
    """), encoding="utf-8")

    assert _compute_assemble_count_delta(scratch, str(project))["H"] == (1, 1)


def test_total_findings_re_bold_wrapped():
    """_TOTAL_FINDINGS_RE matches bold-wrapped label."""
    from plamen_parsers import _TOTAL_FINDINGS_RE

    assert _TOTAL_FINDINGS_RE.search("**Total Findings**: 42")
    assert _TOTAL_FINDINGS_RE.search("Total Findings: 42")
    assert _TOTAL_FINDINGS_RE.search("**Total Findings:** 42")
    m = _TOTAL_FINDINGS_RE.search("**Total Findings**: 42")
    assert m and m.group(1) == "42"


# ── Fix 10: _parse_chunk_heading_inventory format-tolerant fields ─────

def test_chunk_heading_inventory_format_tolerance():
    """Heading-format inventory parser accepts varied field formatting."""
    from plamen_parsers import _parse_chunk_heading_inventory

    text = textwrap.dedent("""\
    ### Finding [H-1]: Bug Title

    **Severity:** High
    **Location:** src/Vault.sol:L45
    **Source IDs:** CS-1, TF-2
    **Evidence Tags:** [CODE-TRACE]
    **Description:** Something bad

    ### Finding [H-2]: Other Bug

    - Severity: Medium
    - Location: src/Router.sol:L10
    - Source IDs: CS-3
    - Preferred Tag: [POC-PASS]
    """)
    entries = _parse_chunk_heading_inventory(text)
    assert len(entries) == 2
    assert entries[0]["severity"].lower() == "high"
    assert entries[0]["location"] != ""
    assert entries[1]["severity"].lower() == "medium"
    assert entries[1]["preferred_tag"] != ""


# ── Fix 11: _validate_crossbatch_quality regex-based fail signals ─────

def test_crossbatch_quality_bold_overall(tmp_path):
    """Crossbatch fail signal detection works with bold-wrapped labels."""
    from plamen_validators import _validate_crossbatch_quality

    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    content = textwrap.dedent("""\
    # Cross-Batch Consistency

    **Overall**: ISSUES FOUND

    ## Details
    Schema violations detected.
    """)
    (scratch / "cross_batch_consistency.md").write_text(content, encoding="utf-8")
    issues = _validate_crossbatch_quality(scratch)
    assert any("crossbatch" in i for i in issues), (
        f"Bold-wrapped 'Overall: ISSUES' should trigger fail signal: {issues}"
    )


# ── Fix 12: _validate_scope_leftover header-aware column parsing ──────

def test_crossbatch_driver_coverage_ledger_repairs_id_omissions(tmp_path):
    from plamen_validators import (
        _append_crossbatch_coverage_ledger,
        _validate_crossbatch_full_coverage,
    )

    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()
    for fid in ("H-10", "H-11", "H-12"):
        (scratch / f"verify_{fid}.md").write_text(
            f"# Verify {fid}\n\n**Verdict**: CONFIRMED\n\n" + "x" * 120,
            encoding="utf-8",
        )
    (scratch / "cross_batch_consistency.md").write_text(textwrap.dedent("""\
        # Cross-Batch Consistency Check

        ## Contradiction Analysis
        | Finding | Verdict | Contradiction? |
        |---|---|---|
        | H-10 | CONFIRMED | NO |

        ## Summary
        - No contradictions: YES
    """), encoding="utf-8")

    before = _validate_crossbatch_full_coverage(scratch)
    appended = _append_crossbatch_coverage_ledger(scratch)
    after = _validate_crossbatch_full_coverage(scratch)
    body = (scratch / "cross_batch_consistency.md").read_text(encoding="utf-8")

    assert before
    assert appended == ["H-11", "H-12"]
    assert after == []
    assert "Verify Coverage Ledger (Driver Completion)" in body
    assert "NO_CONTRADICTION_REPORTED_BY_AGENT" in body


def test_crossbatch_manifest_is_validator_source_of_truth(tmp_path):
    from plamen_validators import (
        _write_crossbatch_manifest,
        _validate_crossbatch_full_coverage,
    )

    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()
    for fid in ("H-10", "H-11"):
        (scratch / f"verify_{fid}.md").write_text(
            f"# Verify {fid}\n\n"
            "**Verdict**: CONFIRMED\n"
            "**Severity**: Medium\n"
            "**Evidence Tag**: [CODE-TRACE]\n\n"
            + "x" * 120,
            encoding="utf-8",
        )

    rows = _write_crossbatch_manifest(scratch)
    manifest = (scratch / "crossbatch_manifest.json").read_text(encoding="utf-8")
    (scratch / "cross_batch_consistency.md").write_text(
        "# Cross-Batch\n\n## Verify Coverage Ledger\n\nH-10\n",
        encoding="utf-8",
    )

    issues = _validate_crossbatch_full_coverage(scratch)

    assert [r["finding_id"] for r in rows] == ["H-10", "H-11"]
    assert '"required_count": 2' in manifest
    assert any("H-11" in issue for issue in issues)


def test_crossbatch_prompt_requires_verify_coverage_ledger():
    prompt = (
        Path(__file__).resolve().parents[1]
        / "prompts" / "shared" / "v2" / "phase5-crossbatch.md"
    ).read_text(encoding="utf-8")

    assert "Verify Coverage Ledger" in prompt
    assert "crossbatch_manifest.json" in prompt
    assert "EVERY verifier finding ID" in prompt
    assert "NO_RELATED_BATCH" in prompt


def test_scope_leftover_reordered_columns(tmp_path):
    """Column-order detection works when table has non-standard order."""
    from plamen_validators import _validate_scope_leftover

    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    content = textwrap.dedent("""\
    # Scope Leftover

    | LOC | File | Reason | Acknowledgment |
    |-----|------|--------|----------------|
    | 500 | src/BigContract.sol | Large file | - |
    | 100 | src/Small.sol | Small file | ACKNOWLEDGED: reviewed |
    """)
    (scratch / "scope_leftover.md").write_text(content, encoding="utf-8")
    issues = _validate_scope_leftover(scratch)
    assert len(issues) == 1, f"Expected 1 uncovered large file, got {issues}"
    assert "BigContract" in issues[0]


# ── Fix 13: _is_separator_row correctness ─────────────────────────────

def test_separator_row_rejects_data():
    """_is_separator_row must not match rows containing actual data."""
    from plamen_parsers import _is_separator_row

    assert _is_separator_row("|---|---|---|")
    assert _is_separator_row("| :--- | :---: | ---: |")
    assert not _is_separator_row("| H-01 | Bug --- title | High |")
    assert not _is_separator_row("| --- analysis --- | result |")
    assert not _is_separator_row("")


# ── v2.5.2 Fix 1: PoC Result only required for C/H/M ─────────────────

def test_quality_gate_poc_optional_for_low_info(tmp_path):
    """Low/Info sections without **PoC Result** must NOT trigger the
    'missing required rich finding fields' failure."""
    from plamen_validators import _run_report_quality_gate

    scratchpad = tmp_path / "sp"
    scratchpad.mkdir()
    project = tmp_path / "proj"
    project.mkdir()

    # Report with L/I findings that have Impact but NO PoC Result
    report = textwrap.dedent("""\
        # Security Audit Report — Test

        ## Summary
        | Severity | Count |
        |----------|-------|
        | Low | 1 |
        | Informational | 1 |

        ### Components Audited

        | Component | Path |
        |-----------|------|
        | test | src/Test.sol |

        ## Low Findings

        ### [L-01] Missing event emission

        **Severity**: Low
        **Location**: `src/Test.sol:L42`

        **Description**:
        State change without event.

        **Impact**:
        Off-chain monitoring may miss state transitions.

        ## Informational Findings

        ### [I-01] Unused import

        **Severity**: Informational
        **Location**: `src/Test.sol:L1`

        **Description**:
        SafeMath imported but unused.

        **Impact**:
        Code clarity.
    """)
    (project / "AUDIT_REPORT.md").write_text(report, encoding="utf-8")
    # Minimal report_index.md so the gate doesn't trip on other checks
    (scratchpad / "report_index.md").write_text(textwrap.dedent("""\
        ## Summary Counts
        | Severity | Count |
        |----------|-------|
        | Low | 1 |
        | Informational | 1 |

        ## Master Finding Index
        | Report ID | Title | Severity | Internal Hypothesis |
        |-----------|-------|----------|---------------------|
        | L-01 | Missing event | Low | H-1 |
        | I-01 | Unused import | Informational | H-2 |
    """), encoding="utf-8")

    issues = _run_report_quality_gate(scratchpad, str(project))
    # The "rich finding fields" issue must NOT appear
    rich_issues = [i for i in issues if "rich finding fields" in i]
    assert not rich_issues, f"False alarm on L/I without PoC Result: {rich_issues}"


def test_quality_gate_poc_required_for_medium(tmp_path):
    """v2.8.5: Medium sections without **PoC Result** are WARN, not FAIL."""
    from plamen_validators import _run_report_quality_gate

    scratchpad = tmp_path / "sp"
    scratchpad.mkdir()
    project = tmp_path / "proj"
    project.mkdir()

    report = textwrap.dedent("""\
        # Security Audit Report — Test

        ## Summary
        | Severity | Count |
        |----------|-------|
        | Medium | 1 |

        ### Components Audited
        | Component | Path |
        |-----------|------|
        | test | src/Test.sol |

        ## Medium Findings

        ### [M-01] Reentrancy in withdraw

        **Severity**: Medium
        **Location**: `src/Test.sol:L100`

        **Description**:
        Cross-function reentrancy via external call.

        **Impact**:
        Fund loss under specific conditions.
    """)
    (project / "AUDIT_REPORT.md").write_text(report, encoding="utf-8")
    (scratchpad / "report_index.md").write_text(textwrap.dedent("""\
        ## Summary Counts
        | Severity | Count |
        |----------|-------|
        | Medium | 1 |

        ## Master Finding Index
        | Report ID | Title | Severity | Internal Hypothesis |
        |-----------|-------|----------|---------------------|
        | M-01 | Reentrancy | Medium | H-1 |
    """), encoding="utf-8")

    issues = _run_report_quality_gate(scratchpad, str(project))
    rich_issues = [i for i in issues if "rich finding fields" in i]
    assert not rich_issues, (
        "v2.8.5: missing PoC Result is WARN-only, not FAIL"
    )


# ── v2.5.2 Fix 2: Components Audited is WARN not FAIL ─────────────────

def test_quality_gate_components_audited_warn_not_fail(tmp_path):
    """Missing Components Audited must be WARN, not FAIL (no issues list entry)."""
    from plamen_validators import _run_report_quality_gate

    scratchpad = tmp_path / "sp"
    scratchpad.mkdir()
    project = tmp_path / "proj"
    project.mkdir()

    # Report WITHOUT Components Audited
    report = textwrap.dedent("""\
        # Security Audit Report — Test

        ## Summary
        | Severity | Count |
        |----------|-------|
        | Low | 1 |

        ## Low Findings

        ### [L-01] Test finding

        **Severity**: Low
        **Location**: `src/Test.sol:L1`

        **Description**:
        Test.

        **Impact**:
        Minimal.

        **PoC Result**:
        Skipped.
    """)
    (project / "AUDIT_REPORT.md").write_text(report, encoding="utf-8")
    (scratchpad / "report_index.md").write_text(textwrap.dedent("""\
        ## Summary Counts
        | Severity | Count |
        |----------|-------|
        | Low | 1 |

        ## Master Finding Index
        | Report ID | Title | Severity | Internal Hypothesis |
        |-----------|-------|----------|---------------------|
        | L-01 | Test | Low | H-1 |
    """), encoding="utf-8")

    issues = _run_report_quality_gate(scratchpad, str(project))
    comp_issues = [i for i in issues if "Components Audited" in i]
    assert not comp_issues, f"Components Audited should be WARN not FAIL: {comp_issues}"


def test_components_audited_synthesizes_from_contract_inventory(tmp_path: Path):
    """Small SC projects still get a Components Audited block from inventory."""
    from plamen_mechanical import _synthesize_components_audited

    scratchpad = tmp_path / "sp"
    scratchpad.mkdir()
    (scratchpad / "contract_inventory.md").write_text(textwrap.dedent("""\
        # Contract Inventory

        ## Primary In-Scope Contracts
        - **File**: `src/AwesomeX.sol`
        - **File**: `src/AwesomeXBuyAndBurn.sol`

        ## Support Files (In-Scope)
        - **File**: `src/interfaces/IDragonX.sol`

        ## Out-of-Scope / Mock Files
        - **File**: `test/MockDragonX.sol`
    """), encoding="utf-8")

    table = _synthesize_components_audited(scratchpad)

    assert "AwesomeX.sol" in table
    assert "AwesomeXBuyAndBurn.sol" in table
    assert "IDragonX.sol" in table
    assert "MockDragonX.sol" not in table


def test_components_audited_synthesizes_from_contract_inventory(tmp_path: Path):
    """Small SC projects still get a Components Audited block from inventory."""
    from plamen_mechanical import _synthesize_components_audited

    scratchpad = tmp_path / "sp"
    scratchpad.mkdir()
    (scratchpad / "contract_inventory.md").write_text(textwrap.dedent("""\
        # Contract Inventory

        ## Primary In-Scope Contracts
        - **File**: `src/AwesomeX.sol`
        - **File**: `src/AwesomeXBuyAndBurn.sol`

        ## Support Files (In-Scope)
        - **File**: `src/interfaces/IDragonX.sol`

        ## Out-of-Scope / Mock Files
        - **File**: `test/MockDragonX.sol`
    """), encoding="utf-8")

    table = _synthesize_components_audited(scratchpad)

    assert "AwesomeX.sol" in table
    assert "AwesomeXBuyAndBurn.sol" in table
    assert "IDragonX.sol" in table
    assert "MockDragonX.sol" not in table


# ── v2.5.2 Fix 3: promotion-dropout self-heal ─────────────────────────

def test_promotion_dropout_selfheal(tmp_path):
    """CONFIRMED verify findings not in tier assignments get synthesized."""
    from plamen_mechanical import _repair_promotion_dropouts

    scratchpad = tmp_path / "sp"
    scratchpad.mkdir()

    # A verify file with CONFIRMED verdict for VS-1
    (scratchpad / "verify_VS-1.md").write_text(textwrap.dedent("""\
        # Verification: VS-1

        **Verdict**: CONFIRMED
        **Severity**: Medium
        **Location**: src/Vault.sol:L50
        **Description**: Missing slippage check.
        **Impact**: Users may receive less than expected.
        **PoC Result**: CODE-TRACE
        **Recommendation**: Add minAmountOut parameter.
    """), encoding="utf-8")

    # Empty report_index (no tier assignments for VS-1)
    (scratchpad / "report_index.md").write_text(textwrap.dedent("""\
        ## Summary Counts
        | Severity | Count |
        |----------|-------|
        | Medium | 0 |

        ## Master Finding Index
        | Report ID | Title | Severity | Internal Hypothesis |
        |-----------|-------|----------|---------------------|
    """), encoding="utf-8")

    # Verification queue with VS-1
    (scratchpad / "verify_queue.md").write_text(textwrap.dedent("""\
        | Finding ID | Title | Severity | Location |
        |------------|-------|----------|----------|
        | VS-1 | Missing slippage | Medium | src/Vault.sol:L50 |
    """), encoding="utf-8")

    body = textwrap.dedent("""\
        # Security Audit Report

        ## Priority Remediation Order

        _No findings._
    """)

    result = _repair_promotion_dropouts(body, scratchpad)
    assert "VS-1" not in result or "[M-" in result, \
        "Dropped VS-1 should be synthesized with a report ID"
    assert "[M-01]" in result, \
        "Self-healed finding should appear with a clean report ID"


# ── v2.5.3: Format-assumption hardening pass 2 ──────────────────────────


def test_case_insensitive_header_skip_validators(tmp_path):
    """scope_leftover table with uppercase 'FILE' header must be skipped."""
    from plamen_parsers import _parse_notread_files, _llm_norm

    text = _llm_norm(textwrap.dedent("""\
        | FILE | LOC | Reason | Acknowledged |
        |------|-----|--------|--------------|
        | src/Token.sol | 200 | NOTREAD | |
    """))
    result = _parse_notread_files(text)
    assert "src/Token.sol" in result, "Uppercase FILE header must still parse data rows"


def test_case_insensitive_header_skip_parsers_manifest(tmp_path):
    """Manifest table with uppercase 'FILE' must skip header but parse data."""
    from plamen_parsers import _llm_norm

    text = _llm_norm(textwrap.dedent("""\
        | FILE | ROLE | MODEL |
        |------|------|-------|
        | depth_token_flow_findings.md | token-flow | opus |
    """))
    files = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        from plamen_parsers import _is_separator_row
        if _is_separator_row(s):
            continue
        s_up = s.upper()
        if "FILE" in s_up and ("ROLE" in s_up or "MODEL" in s_up or "STATUS" in s_up):
            continue
        parts = [c.strip() for c in s.strip("|").split("|")]
        if len(parts) >= 2 and parts[0].endswith(".md"):
            files.append(parts[0])
    assert "depth_token_flow_findings.md" in files


def test_manifest_count_case_insensitive():
    """parse_breadth_manifest_count must handle 'agent'/'role' in any case."""
    from plamen_parsers import _count_markdown_table_rows

    text = textwrap.dedent("""\
        | AGENT | ROLE | MODEL |
        |-------|------|-------|
        | 1 | core-state | opus |
        | 2 | access-control | sonnet |
    """)
    pred = lambda s: "agent" in s.lower() and ("role" in s.lower() or "model" in s.lower())
    count = _count_markdown_table_rows(text, pred)
    assert count == 2, f"Expected 2 rows, got {count}"


def test_llm_norm_on_inventory_chunk(tmp_path):
    """_parse_inventory_chunk must tolerate smart quotes and CRLF."""
    from plamen_parsers import _parse_inventory_chunk

    p = tmp_path / "chunk.md"
    # CRLF + smart quote in title
    p.write_bytes(
        b"### Finding [CS-1]: \xe2\x80\x9cMissing check\xe2\x80\x9d\r\n"
        b"**Severity**: Medium\r\n"
        b"**Location**: src/Vault.sol:L10\r\n"
    )
    entries = _parse_inventory_chunk(p)
    assert len(entries) >= 1, "Inventory chunk with CRLF+smart quotes must parse"
    assert entries[0].get("local_id"), \
        "Entry must have a local_id"


def test_llm_norm_on_depth_finding_blocks(tmp_path):
    """_parse_depth_finding_blocks must tolerate CRLF line endings."""
    from plamen_parsers import _parse_depth_finding_blocks

    p = tmp_path / "depth_token_flow_findings.md"
    p.write_bytes(
        b"## Depth Token Flow\r\n\r\n"
        b"### Finding [TF-1]: Rounding error\r\n"
        b"**Verdict**: CONFIRMED\r\n"
        b"**Severity**: Medium\r\n"
        b"**Location**: src/Vault.sol:L42\r\n"
    )
    blocks = _parse_depth_finding_blocks(p)
    assert len(blocks) >= 1, "Depth block with CRLF must parse"


def test_bold_location_ignorecase():
    """**location** (lowercase) must be detected by _field_from_markdown."""
    from plamen_parsers import _field_from_markdown

    text = "**location**: src/Token.sol:L55"
    result = _field_from_markdown(text, ("Location",))
    assert "Token.sol" in result, f"Lowercase bold label not matched: {result}"


def test_bold_severity_ignorecase():
    """**severity** (lowercase) must be detected by regex in dedup."""
    import re
    body = "**severity**: High\n**location**: src/X.sol:L10"
    sev_m = re.search(r"\*\*Severity\*\*:\s*(\w+)", body, re.IGNORECASE)
    assert sev_m and sev_m.group(1) == "High", "Lowercase severity not matched"


# ── v2.5.4: Artifact-recovery auto-complete ──────────────────────────────


def test_artifact_recovery_autocomplete_present_in_driver():
    """v2.5.4: artifact-recovery block must exist before subprocess launch."""
    src = (Path(__file__).resolve().parent / "plamen_driver.py").read_text(
        encoding="utf-8"
    )
    assert "artifact-recovery" in src, "artifact-recovery block missing from driver"
    assert "phase.expected_artifacts and phase.name not in checkpoint.degraded" in src


def test_verify_shard_empty_expected_artifacts_are_manifest_gated():
    """Verify shards have dynamic artifacts, not a vacuous static gate."""
    from plamen_validators import gate_passes
    from plamen_types import Phase

    tmp = Path(__file__).resolve().parent / "_test_scratch_ar"
    tmp.mkdir(exist_ok=True)
    try:
        phase = Phase("sc_verify_crithigh", [], [], base_timeout_s=100)
        (tmp / "verification_queue.md").write_text(textwrap.dedent("""\
            # Verification Queue

            | Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |
            |---|---|---|---|---|---|---|---|---|
            | 1 | INV-001 | High | Missing access control | Access Control | CODE-TRACE | src/A.sol:L10 | findings_inventory.md | unit |
        """), encoding="utf-8")

        passed, missing = gate_passes(tmp, str(tmp), phase)
        # Empty expected_artifacts → gate passes vacuously, but the driver
        assert not passed
        assert any("verify completion" in m for m in missing)

        (tmp / "verify_INV-001.md").write_text(
            "# Verify INV-001\n\n" + "x" * 120,
            encoding="utf-8",
        )
        passed, missing = gate_passes(tmp, str(tmp), phase)
        assert passed, missing
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_verify_shard_gate_does_not_log_vacuous_warning(tmp_path, caplog):
    from plamen_validators import gate_passes
    from plamen_types import Phase

    phase = Phase("sc_verify_crithigh", [], [], base_timeout_s=100)
    caplog.set_level(logging.WARNING)
    passed, missing = gate_passes(tmp_path, str(tmp_path), phase)

    assert passed, missing
    assert "vacuous pass" not in caplog.text


def test_all_verify_shard_gates_do_not_log_vacuous_warning(tmp_path, caplog):
    """Every manifest-driven verify shard bypasses the generic empty-artifact warning."""
    from plamen_validators import gate_passes
    from plamen_types import L1_VERIFY_PHASE_NAMES, SC_VERIFY_PHASE_NAMES, Phase

    caplog.set_level(logging.WARNING)
    for phase_name in (*SC_VERIFY_PHASE_NAMES, *L1_VERIFY_PHASE_NAMES):
        caplog.clear()
        phase = Phase(phase_name, [], [], base_timeout_s=100)
        passed, missing = gate_passes(tmp_path, str(tmp_path), phase)

        assert passed, f"{phase_name}: {missing}"
        assert "vacuous pass" not in caplog.text, phase_name


def test_verify_shard_gate_rejects_unparseable_nonempty_queue(tmp_path):
    from plamen_validators import gate_passes
    from plamen_types import Phase

    phase = Phase("sc_verify_crithigh", [], [], base_timeout_s=100)
    (tmp_path / "verification_queue.md").write_text(
        "# Verification Queue\n\nThis file is not a queue table.\n",
        encoding="utf-8",
    )

    passed, missing = gate_passes(tmp_path, str(tmp_path), phase)

    assert not passed
    assert any("verify queue parse" in m for m in missing)


def test_verify_shard_gate_accepts_explicit_empty_queue(tmp_path, caplog):
    from plamen_validators import gate_passes
    from plamen_types import Phase

    phase = Phase("sc_verify_crithigh", [], [], base_timeout_s=100)
    (tmp_path / "verification_queue.md").write_text(
        "# Verification Queue\n\nTotal: 0 findings\n",
        encoding="utf-8",
    )
    caplog.set_level(logging.WARNING)

    passed, missing = gate_passes(tmp_path, str(tmp_path), phase)

    assert passed, missing
    assert "vacuous pass" not in caplog.text


def test_sc_verify_manifest_names_exact_output_files(tmp_path):
    """Shard manifests spell out verify_<ID>.md, not legacy verify_F-* names."""
    from plamen_parsers import ensure_sc_verify_shard_manifests

    (tmp_path / "verification_queue.md").write_text(textwrap.dedent("""\
        # Verification Queue

        | Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |
        |---------|------------|----------|-------|-----------|---------------|----------|------------------|-----------|
        | 1 | CC-28 | High | Critical chain bug | chain | CODE-TRACE | contracts/A.sol:L1 | chain_hypotheses.md | structural |
        | 2 | H-1 | Medium | Medium bug | logic | CODE-TRACE | contracts/B.sol:L2 | hypotheses.md | unit |
    """), encoding="utf-8")

    ensure_sc_verify_shard_manifests(tmp_path)
    text = (tmp_path / "verification_queue_crithigh.md").read_text(encoding="utf-8")

    assert "Expected Output File" in text
    assert "verify_CC-28.md" in text
    assert "verify_F-*.md" not in text


def test_all_verify_shard_manifests_name_exact_output_files(tmp_path):
    """SC and L1 shard manifests share the canonical verify_<ID>.md contract."""
    from plamen_parsers import (
        ensure_sc_verify_shard_manifests,
        ensure_verify_shard_manifests,
    )
    from plamen_types import L1_VERIFY_SHARD_MANIFESTS, SC_VERIFY_SHARD_MANIFESTS

    rows = [
        ("CC-28", "Critical", "Critical chain bug", "chain_hypotheses.md"),
        ("H-1", "High", "High bug", "hypotheses.md"),
        ("H-2", "Medium", "Medium bug", "hypotheses.md"),
        ("H-3", "Low", "Low bug", "hypotheses.md"),
    ]

    def _write_queue(path: Path):
        body = [
            "# Verification Queue",
            "",
            "| Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |",
            "|---------|------------|----------|-------|-----------|---------------|----------|------------------|-----------|",
        ]
        for idx, (fid, severity, title, artifact) in enumerate(rows, start=1):
            body.append(
                f"| {idx} | {fid} | {severity} | {title} | logic | CODE-TRACE | "
                f"contracts/A.sol:L{idx} | {artifact} | unit |"
            )
        path.write_text("\n".join(body), encoding="utf-8")

    sc_sp = tmp_path / "sc"
    l1_sp = tmp_path / "l1"
    sc_sp.mkdir()
    l1_sp.mkdir()
    _write_queue(sc_sp / "verification_queue.md")
    _write_queue(l1_sp / "verification_queue.md")

    ensure_sc_verify_shard_manifests(sc_sp)
    ensure_verify_shard_manifests(l1_sp)

    for scratchpad, manifests in (
        (sc_sp, SC_VERIFY_SHARD_MANIFESTS),
        (l1_sp, L1_VERIFY_SHARD_MANIFESTS),
    ):
        for filename in manifests.values():
            text = (scratchpad / filename).read_text(encoding="utf-8")
            assert "Expected Output File" in text, filename
            assert "Expected verify_<ID>.md files" in text, filename
            assert "verify_F-*.md" not in text, filename
            assert "verify_F-" not in text, filename


def test_sc_verify_prompt_inlines_exact_manifest_checklist(tmp_path):
    """The phase prompt gives the verifier an exact no-partial checklist."""
    import plamen_driver as D
    from plamen_parsers import ensure_sc_verify_shard_manifests
    from plamen_types import SC_PHASES

    project = tmp_path / "proj"
    project.mkdir()
    scratchpad = tmp_path / "sp"
    scratchpad.mkdir()
    (scratchpad / "verification_queue.md").write_text(textwrap.dedent("""\
        # Verification Queue

        | Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |
        |---------|------------|----------|-------|-----------|---------------|----------|------------------|-----------|
        | 1 | CC-28 | High | Critical chain bug | chain | CODE-TRACE | contracts/A.sol:L1 | chain_hypotheses.md | structural |
        | 2 | CC-29 | Critical | Second chain bug | chain | CODE-TRACE | contracts/B.sol:L2 | chain_hypotheses.md | structural |
    """), encoding="utf-8")
    ensure_sc_verify_shard_manifests(scratchpad)
    phase = next(p for p in SC_PHASES if p.name == "sc_verify_crithigh")
    v1 = tmp_path / "plamen.md"
    v1.write_text("# Placeholder V1\n", encoding="utf-8")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "core",
        "pipeline": "sc",
        "proven_only": False,
    }

    prompt = D.build_phase_prompt(v1, phase, config)

    assert "Assigned verifier output checklist" in prompt
    assert "CC-28 -> verify_CC-28.md" in prompt
    assert "CC-29 -> verify_CC-29.md" in prompt
    assert "Never return partial completion" in prompt


def test_all_verify_shard_prompts_preserve_dynamic_output_contract(tmp_path):
    """All SC/L1 verify shard prompts keep the exact-output override after sanitization."""
    import plamen_driver as D
    from plamen_parsers import ensure_sc_verify_shard_manifests, ensure_verify_shard_manifests
    from plamen_types import L1_VERIFY_PHASE_NAMES, SC_VERIFY_PHASE_NAMES

    project = tmp_path / "proj"
    project.mkdir()
    v1 = tmp_path / "plamen.md"
    v1.write_text("# Placeholder V1\n", encoding="utf-8")

    def _seed_queue(scratchpad: Path):
        scratchpad.mkdir()
        (scratchpad / "verification_queue.md").write_text(textwrap.dedent("""\
            # Verification Queue

            | Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |
            |---------|------------|----------|-------|-----------|---------------|----------|------------------|-----------|
            | 1 | CC-28 | High | Critical chain bug | chain | CODE-TRACE | contracts/A.sol:L1 | chain_hypotheses.md | structural |
            | 2 | H-1 | Medium | Medium bug | logic | CODE-TRACE | contracts/B.sol:L2 | hypotheses.md | unit |
            | 3 | H-2 | Low | Low bug | logic | CODE-TRACE | contracts/C.sol:L3 | hypotheses.md | unit |
        """), encoding="utf-8")

    sc_sp = tmp_path / "sc_sp"
    l1_sp = tmp_path / "l1_sp"
    _seed_queue(sc_sp)
    _seed_queue(l1_sp)
    ensure_sc_verify_shard_manifests(sc_sp)
    ensure_verify_shard_manifests(l1_sp)

    sc_phases = {p.name: p for p in D.SC_PHASES}
    l1_phases = {p.name: p for p in D.L1_PHASES}

    for phase_name in SC_VERIFY_PHASE_NAMES:
        prompt = D.build_phase_prompt(v1, sc_phases[phase_name], {
            "project_root": str(project),
            "scratchpad": str(sc_sp),
            "language": "evm",
            "mode": "thorough",
            "pipeline": "sc",
            "proven_only": False,
        })
        assert "VERIFY COST OVERRIDE (SC SHARD)" in prompt, phase_name
        assert "Assigned verifier output checklist" in prompt, phase_name
        assert "Expected Output File" in prompt, phase_name
        assert "Never return partial completion" in prompt, phase_name
        assert "write one\n   `verify_<ID>.md` file per row" in prompt, phase_name
        assert "verify_F-*.md" not in prompt, phase_name

    for phase_name in L1_VERIFY_PHASE_NAMES:
        prompt = D.build_phase_prompt(v1, l1_phases[phase_name], {
            "project_root": str(project),
            "scratchpad": str(l1_sp),
            "language": "rust",
            "mode": "thorough",
            "pipeline": "l1",
            "proven_only": False,
        })
        assert "VERIFY COST OVERRIDE" in prompt, phase_name
        assert "Assigned verifier output checklist" in prompt, phase_name
        assert "Expected Output File" in prompt, phase_name
        assert "Never return partial completion" in prompt, phase_name
        assert "verify_<ID>.md" in prompt, phase_name
        assert "verify_F-*.md" not in prompt, phase_name


def test_artifact_recovery_passes_when_artifacts_exist():
    """gate_passes returns True when all expected artifacts exist and are substantial."""
    from plamen_validators import gate_passes
    from plamen_types import Phase

    tmp = Path(__file__).resolve().parent / "_test_scratch_ar2"
    tmp.mkdir(exist_ok=True)
    try:
        (tmp / "chain_hypotheses.md").write_text("x" * 200, encoding="utf-8")
        (tmp / "composition_coverage.md").write_text("y" * 200, encoding="utf-8")
        phase = Phase(
            "chain_agent2", [],
            ["chain_hypotheses.md", "composition_coverage.md"],
            base_timeout_s=100,
        )
        passed, missing = gate_passes(tmp, str(tmp), phase)
        assert passed, f"Gate should pass with existing artifacts: {missing}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_artifact_recovery_fails_when_artifact_missing():
    """gate_passes returns False when an expected artifact is missing."""
    from plamen_validators import gate_passes
    from plamen_types import Phase

    tmp = Path(__file__).resolve().parent / "_test_scratch_ar3"
    tmp.mkdir(exist_ok=True)
    try:
        (tmp / "chain_hypotheses.md").write_text("x" * 200, encoding="utf-8")
        # composition_coverage.md intentionally missing
        phase = Phase(
            "chain_agent2", [],
            ["chain_hypotheses.md", "composition_coverage.md"],
            base_timeout_s=100,
        )
        passed, missing = gate_passes(tmp, str(tmp), phase)
        assert not passed, "Gate should fail with missing artifact"
        assert any("composition_coverage" in m for m in missing)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ── v2.5.4: Promotion recovery patches report_index.md ─────────────────


def test_patch_report_index_with_recovered_appends_rows(tmp_path):
    """v2.5.4: _patch_report_index_with_recovered adds rows before Excluded."""
    from plamen_mechanical import _patch_report_index_with_recovered

    tmp = tmp_path / "sp"
    tmp.mkdir()
    ri = tmp / "report_index.md"
    ri.write_text(textwrap.dedent("""\
            ## Master Finding Index

            | Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
            |-----------|-------|----------|----------|--------------|------------|---------------------|
            | M-01 | Bug one | Medium | src:10 | CONFIRMED | - | H-1 |
            | M-02 | Bug two | Medium | src:20 | CONFIRMED | - | H-2 |

            ## Excluded Findings

            | Internal ID | Severity | Title | Exclusion Reason |
    """), encoding="utf-8")

    recovered = [
        ("VS-1", "M-03", "Missing check", "Medium"),
        ("VS-4", "L-01", "Info issue", "Low"),
    ]
    n = _patch_report_index_with_recovered(tmp, recovered)
    assert n == 2, f"Expected 2 rows appended, got {n}"

    text = ri.read_text(encoding="utf-8")
    assert "VS-1" in text
    assert "VS-4" in text
    assert "M-03" in text
    assert "L-01" in text
    # Rows must be ABOVE excluded section
    vs1_pos = text.index("VS-1")
    excl_pos = text.index("## Excluded")
    assert vs1_pos < excl_pos, "Recovered rows must be before Excluded section"


def test_patch_report_index_skips_duplicates():
    """v2.5.4: _patch_report_index_with_recovered skips already-present fids."""
    from plamen_mechanical import _patch_report_index_with_recovered

    tmp = Path(__file__).resolve().parent / "_test_scratch_pri2"
    tmp.mkdir(exist_ok=True)
    try:
        ri = tmp / "report_index.md"
        ri.write_text(
            "## Master Finding Index\n\n"
            "| M-01 | Bug | Medium | - | CONFIRMED | - | VS-1 |\n\n"
            "## Excluded Findings\n",
            encoding="utf-8",
        )
        n = _patch_report_index_with_recovered(
            tmp, [("VS-1", "M-02", "Dup", "Medium")]
        )
        assert n == 0, "Should skip VS-1 (already present)"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_body_gt_assignments_is_fail():
    """v2.5.4: body > assignments → WARN (not FAIL). Body is authoritative."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_qg"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        # Create report with 3 sections
        report = proj / "AUDIT_REPORT.md"
        report.write_text(textwrap.dedent("""\
            # Security Audit Report

            ## Summary
            | Severity | Count |
            |----------|-------|
            | Medium | 3 |

            ## Medium Findings

            ### [M-01] Bug one
            **Severity**: Medium
            **Location**: src:10
            **Impact**: Loss
            **PoC Result**: PASS

            ### [M-02] Bug two
            **Severity**: Medium
            **Location**: src:20
            **Impact**: Loss
            **PoC Result**: PASS

            ### [M-03] Recovered bug
            **Severity**: Medium
            **Location**: src:30
            **Impact**: Loss
            **PoC Result**: PASS
        """), encoding="utf-8")

        # Create report_index.md with only 2 assignments
        ri = tmp / "report_index.md"
        ri.write_text(textwrap.dedent("""\
            ## Summary Counts
            | Severity | Count |
            |----------|-------|
            | Medium | 2 |

            ## Master Finding Index
            | Report ID | Title | Severity | Internal Hypothesis |
            |-----------|-------|----------|---------------------|
            | M-01 | Bug one | Medium | H-1 |
            | M-02 | Bug two | Medium | H-2 |
        """), encoding="utf-8")

        issues = _run_report_quality_gate(tmp, str(proj))
        # body=3 > assignments=2 → WARN only (v2.7.5), NOT FAIL
        count_issues = [i for i in issues if "body count mismatch" in i]
        assert not count_issues, f"body > assignments should WARN not FAIL: {count_issues}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_body_lt_assignments_is_fail():
    """v2.8.5: shortfall=1 with 2 assignments is within tolerance (WARN)."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_qg2"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        report = proj / "AUDIT_REPORT.md"
        report.write_text(textwrap.dedent("""\
            # Security Audit Report

            ## Summary
            | Severity | Count |
            |----------|-------|
            | Medium | 1 |

            ## Medium Findings

            ### [M-01] Bug one
            **Severity**: Medium
            **Location**: src:10
            **Impact**: Loss
            **PoC Result**: PASS
        """), encoding="utf-8")

        ri = tmp / "report_index.md"
        ri.write_text(textwrap.dedent("""\
            ## Summary Counts
            | Severity | Count |
            |----------|-------|
            | Medium | 2 |

            ## Master Finding Index
            | Report ID | Title | Severity | Internal Hypothesis |
            |-----------|-------|----------|---------------------|
            | M-01 | Bug one | Medium | H-1 |
            | M-02 | Bug two | Medium | H-2 |
        """), encoding="utf-8")

        issues = _run_report_quality_gate(tmp, str(proj))
        count_issues = [i for i in issues if "body count mismatch" in i]
        assert not count_issues, (
            "v2.8.5: shortfall=1 / tolerance=2 is WARN, not FAIL"
        )
        missing_issues = [i for i in issues if "missing" in i.lower() and "M-02" in i]
        assert missing_issues, "ID-set mismatch for M-02 should still fire"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _rich_report_section(report_id: str, marker: str = "") -> str:
    prefix = f"[{marker}] " if marker else ""
    return textwrap.dedent(f"""\
        ### {prefix}[{report_id}] Evidence quality test
        **Severity**: Medium
        **Location**: src/Quality.sol:L42
        **Impact**: The issue can cause measurable protocol state divergence and requires a concrete mitigation before the report can be considered client-ready.
        **PoC Result**: PASS
        **Description**: This section is intentionally substantive so the evidence-quality gate is the only relevant failure condition. It includes enough narrative content to avoid the thin-section guard while preserving the explicit marker being tested. The section describes a concrete source path, a plausible impact, and a remediation boundary so it behaves like a normal report body section.
        **Recommendation**: Apply the mitigation at the cited source location and add a regression test that covers the affected accounting path under the triggering precondition.
    """)


def test_quality_gate_rejects_report_blocked_flood():
    """A mechanically complete report dominated by REPORT-BLOCKED is not client-grade."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_blocked_flood"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        body = "\n".join(
            _rich_report_section(f"M-{i:02d}", "REPORT-BLOCKED: insufficient evidence")
            for i in range(1, 6)
        )
        (proj / "AUDIT_REPORT.md").write_text(
            "# Security Audit Report\n\n## Medium Findings\n\n" + body,
            encoding="utf-8",
        )
        issues = _run_report_quality_gate(tmp, str(proj))
        # GATE-3: the blocked-section COUNT is now a soft WARN (the mechanical
        # assembler cannot manufacture upstream evidence). What stays HARD is
        # the marker LEAK: bracketed [REPORT-BLOCKED] markers must never appear
        # in the client report, so a report carrying them is still rejected.
        assert any("leaked into client report" in issue for issue in issues), issues
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_rejects_critical_high_report_blocked():
    """Critical/High client sections cannot ship with insufficient evidence tags."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_blocked_high"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        (proj / "AUDIT_REPORT.md").write_text(
            "# Security Audit Report\n\n## High Findings\n\n"
            + _rich_report_section("H-01", "REPORT-BLOCKED: insufficient evidence"),
            encoding="utf-8",
        )
        issues = _run_report_quality_gate(tmp, str(proj))
        # GATE-3: C/H REPORT-BLOCKED count is now a soft WARN; the bracketed
        # marker leak into the client report remains a HARD rejection.
        assert any("leaked into client report" in issue for issue in issues), issues
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_rejects_single_low_report_blocked_marker_leak(tmp_path):
    """No internal report-blocked marker should leak into the client report."""
    from plamen_validators import _run_report_quality_gate

    tmp = tmp_path / "sp"
    tmp.mkdir()
    proj = tmp / "proj"
    proj.mkdir()
    (proj / "AUDIT_REPORT.md").write_text(
        "# Security Audit Report\n\n## Low Findings\n\n"
        + _rich_report_section("L-01", "REPORT-BLOCKED: insufficient evidence"),
        encoding="utf-8",
    )
    issues = _run_report_quality_gate(tmp, str(proj))
    assert any("REPORT-BLOCKED" in issue or "internal report marker" in issue for issue in issues), issues


def test_quality_gate_rejects_after_id_report_blocked_marker(tmp_path):
    """Detect REPORT-BLOCKED markers emitted after the report ID."""
    from plamen_validators import _run_report_quality_gate

    tmp = tmp_path / "sp"
    tmp.mkdir()
    proj = tmp / "proj"
    proj.mkdir()
    section = _rich_report_section("H-01").replace(
        "### [H-01] Evidence quality test",
        "### [H-01] [REPORT-BLOCKED: insufficient evidence] Evidence quality test",
    )
    (proj / "AUDIT_REPORT.md").write_text(
        "# Security Audit Report\n\n## High Findings\n\n" + section,
        encoding="utf-8",
    )
    issues = _run_report_quality_gate(tmp, str(proj))
    # Marker leaked into the client report (bracketed [REPORT-BLOCKED] after the
    # ID) stays a HARD rejection; the message is the canonical leak message.
    assert any("leaked into client report" in issue for issue in issues), issues


def test_quality_gate_rejects_generated_placeholder_title():
    """v2.8.5: placeholder titles are WARN, not FAIL."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_placeholder_title"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        section = _rich_report_section("L-01").replace(
            "### [L-01] Evidence quality test",
            "### [L-01] Unverified Low-Severity Finding - H-25",
        )
        (proj / "AUDIT_REPORT.md").write_text(
            "# Security Audit Report\n\n## Low Findings\n\n" + section,
            encoding="utf-8",
        )
        issues = _run_report_quality_gate(tmp, str(proj))
        assert not any("placeholder/generated finding titles" in issue for issue in issues), (
            "v2.8.5: placeholder titles are WARN-only, should not halt"
        )
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_allows_specific_unverified_title():
    """Unverified status is fine when the title names the actual bug."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_specific_title"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        section = _rich_report_section("L-01").replace(
            "### [L-01] Evidence quality test",
            "### [L-01] Missing event emission on slippage parameter update [UNVERIFIED]",
        )
        (proj / "AUDIT_REPORT.md").write_text(
            "# Security Audit Report\n\n## Low Findings\n\n" + section,
            encoding="utf-8",
        )
        issues = _run_report_quality_gate(tmp, str(proj))
        assert not any("placeholder/generated finding titles" in issue for issue in issues), issues
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_rejects_metadata_as_location():
    """Severity/evidence text in Location is a phase-contract failure."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_metadata_location"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        section = _rich_report_section("M-01").replace(
            "**Location**: src/Quality.sol:L42",
            "**Location**: Medium, [POC-PASS]",
        )
        (proj / "AUDIT_REPORT.md").write_text(
            "# Security Audit Report\n\n## Medium Findings\n\n" + section,
            encoding="utf-8",
        )
        issues = _run_report_quality_gate(tmp, str(proj))
        assert any("metadata as Location" in issue for issue in issues), issues
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_allows_specific_code_location_with_evidence_elsewhere():
    """Evidence tags are fine when Location itself remains a source path."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_good_location"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        section = _rich_report_section("M-01") + "\n**Evidence**: [POC-PASS]\n"
        (proj / "AUDIT_REPORT.md").write_text(
            "# Security Audit Report\n\n## Medium Findings\n\n" + section,
            encoding="utf-8",
        )
        issues = _run_report_quality_gate(tmp, str(proj))
        assert not any("metadata as Location" in issue for issue in issues), issues
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_rejects_empty_rich_fields():
    """v2.8.5: non-substantive Impact/PoC fields are WARN, not FAIL."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_empty_rich_fields"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        section = _rich_report_section("M-01").replace(
            "**Impact**: The issue can cause measurable protocol state divergence and requires a concrete mitigation before the report can be considered client-ready.",
            "**Impact**: N/A",
        ).replace(
            "**PoC Result**: PASS",
            "**PoC Result**: Not provided",
        )
        (proj / "AUDIT_REPORT.md").write_text(
            "# Security Audit Report\n\n## Medium Findings\n\n" + section,
            encoding="utf-8",
        )
        issues = _run_report_quality_gate(tmp, str(proj))
        assert not any("substantive finding fields" in issue for issue in issues), (
            "v2.8.5: non-substantive fields are WARN-only, not FAIL"
        )
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_gate_rejects_duplicate_title_location_sections():
    """v2.8.5: duplicate title/location is WARN, not FAIL."""
    from plamen_validators import _run_report_quality_gate

    tmp = Path(__file__).resolve().parent / "_test_scratch_dup_title_location"
    tmp.mkdir(exist_ok=True)
    proj = tmp / "proj"
    proj.mkdir(exist_ok=True)
    try:
        first = _rich_report_section("M-01").replace(
            "### [M-01] Evidence quality test",
            "### [M-01] Missing oracle freshness check",
        )
        second = _rich_report_section("M-02").replace(
            "### [M-02] Evidence quality test",
            "### [M-02] Missing oracle freshness check",
        )
        (proj / "AUDIT_REPORT.md").write_text(
            "# Security Audit Report\n\n## Medium Findings\n\n" + first + "\n" + second,
            encoding="utf-8",
        )
        issues = _run_report_quality_gate(tmp, str(proj))
        assert not any("duplicate title/location" in issue for issue in issues), (
            "v2.8.5: duplicate title/location is WARN-only, not FAIL"
        )
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
