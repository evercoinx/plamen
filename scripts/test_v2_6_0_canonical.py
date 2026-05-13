"""Tests for v2.6.0: canonical evidence tag + severity definitions in plamen_types.py.

Validates that:
1. All consumers delegate to the canonical definitions (no inline reimplementations)
2. Adding a tag to EVIDENCE_TAGS_PROOF automatically propagates to all call sites
3. Severity normalization is consistent across all entry points
4. Round 3 bug fixes work correctly
"""
from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── Evidence tag canonical definitions ─────────────────────────────────────


def test_evidence_tags_proof_contains_all_six():
    from plamen_types import EVIDENCE_TAGS_PROOF
    expected = {
        "[POC-PASS]", "[MEDUSA-PASS]", "[FUZZ-PASS]",
        "[NON-DET-PASS]", "[DIFF-PASS]", "[CONFORMANCE-PASS]",
    }
    assert EVIDENCE_TAGS_PROOF == expected


def test_evidence_tags_all_is_union():
    from plamen_types import (
        EVIDENCE_TAGS_ALL, EVIDENCE_TAGS_PROOF,
        EVIDENCE_TAGS_TRACE, EVIDENCE_TAGS_FAIL,
    )
    assert EVIDENCE_TAGS_ALL == EVIDENCE_TAGS_PROOF | EVIDENCE_TAGS_TRACE | EVIDENCE_TAGS_FAIL


def test_has_mechanical_proof_catches_all_proof_tags():
    from plamen_types import has_mechanical_proof, EVIDENCE_TAGS_PROOF
    for tag in EVIDENCE_TAGS_PROOF:
        assert has_mechanical_proof(f"some text {tag} more text"), f"missed {tag}"


def test_has_mechanical_proof_rejects_non_proof():
    from plamen_types import has_mechanical_proof
    assert not has_mechanical_proof("[CODE-TRACE]")
    assert not has_mechanical_proof("[POC-FAIL]")
    assert not has_mechanical_proof("[LSP-TRACE]")
    assert not has_mechanical_proof("")


def test_evidence_tag_names_re_matches_all():
    from plamen_types import EVIDENCE_TAG_NAMES_RE, EVIDENCE_TAGS_ALL
    pat = re.compile(r"\[(" + EVIDENCE_TAG_NAMES_RE + r")\]")
    for tag in EVIDENCE_TAGS_ALL:
        assert pat.match(tag), f"regex missed {tag}"


# ── Severity canonical definitions ────────────────────────────────────────


def test_normalize_severity_canonical():
    from plamen_types import normalize_severity
    assert normalize_severity("Critical") == "Critical"
    assert normalize_severity("high") == "High"
    assert normalize_severity("med") == "Medium"
    assert normalize_severity("Medium") == "Medium"
    assert normalize_severity("low") == "Low"
    assert normalize_severity("info") == "Informational"
    assert normalize_severity("Informational") == "Informational"
    assert normalize_severity("crit") == "Critical"


def test_normalize_severity_edge_cases():
    from plamen_types import normalize_severity
    assert normalize_severity("") == "Medium"
    assert normalize_severity("  ") == "Medium"
    assert normalize_severity("CRITICAL") == "Critical"
    assert normalize_severity("HIGH ") == "High"
    assert normalize_severity("garbage") == "Medium"


def test_severity_letter_from_name():
    from plamen_types import severity_letter_from_name
    assert severity_letter_from_name("Critical") == "C"
    assert severity_letter_from_name("high") == "H"
    assert severity_letter_from_name("Medium") == "M"
    assert severity_letter_from_name("low") == "L"
    assert severity_letter_from_name("info") == "I"


def test_severity_rank_ordering():
    from plamen_types import severity_rank
    assert severity_rank("Critical") == 4
    assert severity_rank("High") == 3
    assert severity_rank("Medium") == 2
    assert severity_rank("Low") == 1
    assert severity_rank("Informational") == 0
    assert severity_rank("Critical") > severity_rank("High") > severity_rank("Medium")


def test_severity_from_letter_roundtrip():
    from plamen_types import SEVERITY_LETTER, SEVERITY_FROM_LETTER
    for name, letter in SEVERITY_LETTER.items():
        assert SEVERITY_FROM_LETTER[letter] == name


# ── Consumer delegation tests ─────────────────────────────────────────────


def test_parsers_severity_bucket_delegates():
    """_severity_bucket must return consistent results with normalize_severity."""
    from plamen_parsers import _severity_bucket
    assert _severity_bucket("Critical") == "critical"
    assert _severity_bucket("HIGH") == "high"
    assert _severity_bucket("med") == "medium"
    assert _severity_bucket("low") == "low"
    assert _severity_bucket("Informational") == "info"
    assert _severity_bucket("info") == "info"
    assert _severity_bucket("") == "medium"


def test_parsers_severity_name_from_text_delegates():
    from plamen_parsers import _severity_name_from_text
    assert _severity_name_from_text("**Severity**: Critical", {}) == "Critical"
    assert _severity_name_from_text("", {"severity": "high"}) == "High"
    assert _severity_name_from_text("", {}) == "Medium"
    assert _severity_name_from_text("**Severity**: info", {}) == "Informational"


def test_normalize_severity_strips_markdown_cell_noise():
    """Markdown/table decoration around a severity must not inflate to Medium."""
    from plamen_types import normalize_severity

    cases = {
        "** Informational": "Informational",
        "** Low": "Low",
        "**Low**": "Low",
        "`Low`": "Low",
        "- Low": "Low",
        "Severity: **High**": "High",
        "Final Severity = **Critical**": "Critical",
        "Resulting tier: **Informational**": "Informational",
    }
    for raw, expected in cases.items():
        assert normalize_severity(raw) == expected


def test_parsers_report_prefix_delegates():
    from plamen_parsers import _report_prefix_for_severity
    assert _report_prefix_for_severity("Critical") == "C"
    assert _report_prefix_for_severity("High") == "H"
    assert _report_prefix_for_severity("Medium") == "M"
    assert _report_prefix_for_severity("Low") == "L"
    assert _report_prefix_for_severity("Informational") == "I"
    assert _report_prefix_for_severity("info") == "I"


def test_parsers_severity_order_dict_consistent():
    from plamen_parsers import _SEVERITY_ORDER
    assert _SEVERITY_ORDER["critical"] == 4
    assert _SEVERITY_ORDER["high"] == 3
    assert _SEVERITY_ORDER["medium"] == 2
    assert _SEVERITY_ORDER["low"] == 1
    assert _SEVERITY_ORDER["informational"] == 0
    assert _SEVERITY_ORDER["info"] == 0


def test_parsers_severity_code_dict_consistent():
    from plamen_parsers import _SEVERITY_CODE
    assert _SEVERITY_CODE["critical"] == "C"
    assert _SEVERITY_CODE["high"] == "H"
    assert _SEVERITY_CODE["medium"] == "M"
    assert _SEVERITY_CODE["low"] == "L"
    assert _SEVERITY_CODE["informational"] == "I"
    assert _SEVERITY_CODE["info"] == "I"


def test_mechanical_shard_name_consistent():
    from plamen_mechanical import _shard_name_for_severity
    assert _shard_name_for_severity("Critical") == "report_critical_high"
    assert _shard_name_for_severity("High") == "report_critical_high"
    assert _shard_name_for_severity("Medium") == "report_medium"
    assert _shard_name_for_severity("Low") == "report_low_info"
    assert _shard_name_for_severity("Informational") == "report_low_info"
    assert _shard_name_for_severity("info") == "report_low_info"
    assert _shard_name_for_severity("crit") == "report_critical_high"


# ── Round 3 bug fixes ─────────────────────────────────────────────────────


def test_parsers_section_for_report_id_preserves_h3_subsections():
    """v2.6.0 fix: #{1,3} → #{1,2} end boundary keeps H3 sub-sections.

    The end boundary is H1/H2 (severity tier headers). H3/H4 sub-sections
    within a finding are preserved in the extracted section.
    """
    from plamen_parsers import _section_for_report_id
    body = textwrap.dedent("""\
        ## Medium Findings

        ### [M-01] Some Finding [VERIFIED]

        **Severity**: Medium

        #### Sub-analysis

        Some detail here.

        ## Low Findings

        ### [L-01] Next Finding
    """)
    section = _section_for_report_id(body, "M-01")
    assert "Sub-analysis" in section
    assert "Some detail here" in section
    assert "[L-01]" not in section
    assert "Low Findings" not in section


def test_mechanical_empty_severity_no_crash():
    """v2.6.0 P1-4 fix: empty/whitespace severity must not IndexError."""
    from plamen_types import normalize_severity, severity_letter_from_name
    assert normalize_severity("  ") == "Medium"
    assert severity_letter_from_name("  ") == "M"
    assert normalize_severity("") == "Medium"


def test_parsers_summary_count_startswith(tmp_path):
    """v2.6.0 fix: summary count matches 'Critical Findings' form."""
    from plamen_parsers import _parse_report_index_summary_counts
    scratchpad = tmp_path / ".scratchpad"
    scratchpad.mkdir()
    (scratchpad / "report_index.md").write_text(textwrap.dedent("""\
        | Severity | Count |
        |----------|-------|
        | Critical Findings | 2 |
        | High | 1 |
        | Medium | 3 |
        | Low | 0 |
        | Informational | 5 |
    """), encoding="utf-8")
    counts = _parse_report_index_summary_counts(scratchpad)
    assert counts.get("C") == 2, f"Critical count wrong: {counts}"


def test_mechanical_quality_row_excludes_appendix(tmp_path):
    """v2.6.0 P1-5 fix: quality_row_count must not count Appendix A rows."""
    body = textwrap.dedent("""\
        ## Quality Observations

        | ID | Title | Severity |
        |----|-------|----------|
        | I-01 | Unused import | Info |

        ## Appendix A: Internal Audit Traceability

        | Report ID | Internal Hypothesis |
        |-----------|---------------------|
        | C-01 | H-1 |
        | H-01 | H-2 |
    """)
    appendix_start = body.find("## Appendix")
    quality_body = body[:appendix_start] if appendix_start >= 0 else body
    quality_rows = re.findall(r"^\|\s*[CHMLI]-\d+\s*\|", quality_body, re.MULTILINE)
    all_rows = re.findall(r"^\|\s*[CHMLI]-\d+\s*\|", body, re.MULTILINE)
    assert len(quality_rows) == 1, "Should only count I-01 from Quality Observations"
    assert len(all_rows) == 3, "Without fix, would count appendix rows too"


def test_no_inline_evidence_tag_literals_in_consumers():
    """Structural test: no consumer file should have inline proof-tag string literals.

    After v2.6.0, all proof-tag checks must go through has_mechanical_proof()
    or EVIDENCE_TAGS_PROOF. This test greps for the old pattern to prevent regression.
    """
    scripts_dir = Path(__file__).resolve().parent
    consumer_files = [
        "plamen_validators.py",
        "plamen_driver.py",
    ]
    inline_pattern = re.compile(
        r'''["']\[(?:POC-PASS|MEDUSA-PASS|FUZZ-PASS|NON-DET-PASS)\]["']'''
        r'''\s+in\s+'''
    )
    violations = []
    for fname in consumer_files:
        fpath = scripts_dir / fname
        if not fpath.exists():
            continue
        for i, line in enumerate(fpath.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if inline_pattern.search(line):
                violations.append(f"{fname}:{i}: {line.strip()}")
    assert not violations, (
        "Inline proof-tag literals found in consumer files (should use "
        "has_mechanical_proof() or EVIDENCE_TAGS_PROOF):\n" +
        "\n".join(violations)
    )
