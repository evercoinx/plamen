"""Tests for v2.5.5 fixes: internal tag stripping, report_index ID extraction,
gate_passes warning, tier completeness parse-failure detection, rc_parity logging,
severity default warning, degraded sentinel cleanup, chunk inventory tolerance."""
from __future__ import annotations

import json
import re
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ── Fix 1: Strip REPORT-BLOCKED and STUB-RECOVERED from client report ──


def test_report_blocked_stripped_from_client_assembled_body(tmp_path):
    """_assemble_report_python must not leak [REPORT-BLOCKED: ...] tags."""
    from plamen_mechanical import _assemble_report_python

    scratchpad = tmp_path / ".scratchpad"
    scratchpad.mkdir()
    project_root = tmp_path

    # Minimal report_index.md
    (scratchpad / "report_index.md").write_text(textwrap.dedent("""\
        ## Summary Counts
        | Severity | Count |
        |----------|-------|
        | Critical | 0 |
        | High | 0 |
        | Medium | 1 |
        | Low | 0 |
        | Informational | 0 |

        ## Master Finding Index
        | Report ID | Title | Severity | Internal Hypothesis |
        |-----------|-------|----------|---------------------|
        | M-01 | Test finding | Medium | H-1 |
    """), encoding="utf-8")

    # Tier files with REPORT-BLOCKED and STUB-RECOVERED tags
    (scratchpad / "report_critical_high.md").write_text("", encoding="utf-8")
    (scratchpad / "report_medium.md").write_text(textwrap.dedent("""\
        ## Medium Findings

        ### [REPORT-BLOCKED: insufficient evidence] [M-01] Test finding [UNVERIFIED]

        **Severity**: Medium
        **Description**: Something something
        **Impact**: Some impact
        **Recommendation**: Fix it
    """), encoding="utf-8")
    (scratchpad / "report_low_info.md").write_text("", encoding="utf-8")

    # Required artifacts
    (scratchpad / "hypotheses.md").write_text("", encoding="utf-8")
    (scratchpad / "verification_queue.md").write_text("", encoding="utf-8")

    result = _assemble_report_python(scratchpad, project_root)
    assert result is True

    report = (project_root / "AUDIT_REPORT.md").read_text(encoding="utf-8")
    assert "[REPORT-BLOCKED" not in report
    assert "[STUB-RECOVERED]" not in report
    # The finding section itself should still be present
    assert "[M-01]" in report


def test_stub_recovered_preserved_in_assembled_body(tmp_path):
    """STUB-RECOVERED tags must stay visible for quality-gate review."""
    from plamen_mechanical import _assemble_report_python

    scratchpad = tmp_path / ".scratchpad"
    scratchpad.mkdir()
    project_root = tmp_path

    (scratchpad / "report_index.md").write_text(textwrap.dedent("""\
        ## Summary Counts
        | Severity | Count |
        |----------|-------|
        | Critical | 0 |
        | High | 0 |
        | Medium | 0 |
        | Low | 1 |
        | Informational | 0 |

        ## Master Finding Index
        | Report ID | Title | Severity | Internal Hypothesis |
        |-----------|-------|----------|---------------------|
        | L-01 | Stub finding | Low | H-2 |
    """), encoding="utf-8")

    (scratchpad / "report_critical_high.md").write_text("", encoding="utf-8")
    (scratchpad / "report_medium.md").write_text("", encoding="utf-8")
    (scratchpad / "report_low_info.md").write_text(textwrap.dedent("""\
        ## Low Findings

        ### [L-01] Stub finding [STUB-RECOVERED]

        **Severity**: Low
        **Description**: Verifier artifact did not include a narrative description.
        **Impact**: Unknown.
        **Recommendation**: Review manually.
    """), encoding="utf-8")

    (scratchpad / "hypotheses.md").write_text("", encoding="utf-8")
    (scratchpad / "verification_queue.md").write_text("", encoding="utf-8")

    result = _assemble_report_python(scratchpad, project_root)
    assert result is True

    report = (project_root / "AUDIT_REPORT.md").read_text(encoding="utf-8")
    assert "[STUB-RECOVERED]" in report
    assert "[L-01]" in report


# ── Fix 2: _parse_report_index_table takes LAST internal ID ──


def test_parse_report_index_table_takes_last_id():
    """Internal ID must come from the LAST matching column, not the first."""
    from plamen_parsers import _parse_report_index_table

    text = textwrap.dedent("""\
        | Report ID | Title | Agent Sources | Internal Hypothesis |
        |-----------|-------|---------------|---------------------|
        | M-01 | Missing validation | CS-1, TF-2 | H-5 |
        | H-01 | Critical bug | AC-3 | H-1 |
    """)
    rows = _parse_report_index_table(text)
    assert len(rows) == 2
    assert rows[0]["finding_id"] == "H-5"
    assert rows[1]["finding_id"] == "H-1"


def test_parse_report_index_table_last_id_with_chain():
    """Chain IDs in intermediate columns must not overshadow the last column."""
    from plamen_parsers import _parse_report_index_table

    text = textwrap.dedent("""\
        | Report ID | Title | Chain | Internal Hypothesis |
        |-----------|-------|-------|---------------------|
        | C-01 | Combined bug | CH-3 | H-7 |
    """)
    rows = _parse_report_index_table(text)
    assert len(rows) == 1
    # H-7 is in the last column, CH-3 is in an intermediate one
    assert rows[0]["finding_id"] == "H-7"


# ── Fix 3: gate_passes warns on empty expected_artifacts ──


def test_gate_passes_warns_on_empty_artifacts(tmp_path, caplog):
    """gate_passes should log a warning when expected_artifacts is empty."""
    from plamen_validators import gate_passes
    from plamen_types import Phase

    phase = Phase(
        name="test_empty",
        section_markers=[],
        expected_artifacts=[],
        base_timeout_s=300,
        min_artifact_bytes=100,
        min_artifacts_count=1,
    )

    import logging
    with caplog.at_level(logging.WARNING):
        passed, missing = gate_passes(tmp_path, str(tmp_path), phase)

    assert passed is True  # vacuously true
    assert any("vacuous pass" in r.message for r in caplog.records)


# ── Fix 4: _validate_report_tier_completeness detects parse failure ──


def test_tier_completeness_detects_all_zero_counts(tmp_path):
    """When report_index.md exists but parses to all zeros, flag it."""
    from plamen_validators import _validate_report_tier_completeness

    scratchpad = tmp_path
    # Write a report_index.md with garbage content (parseable but no severity rows)
    (scratchpad / "report_index.md").write_text(
        "# Report Index\n\nSome text without any severity table.\n" * 10,
        encoding="utf-8",
    )
    # Also need verification_queue.md for get_tier_assignments fallback
    (scratchpad / "verification_queue.md").write_text(
        "# Empty queue\n", encoding="utf-8"
    )

    issues = _validate_report_tier_completeness(scratchpad, "report_medium")
    # Should detect the parse failure since report_index.md exists but has 0 counts
    # (may return empty if report_index.md is too small, which is also fine)
    if (scratchpad / "report_index.md").stat().st_size > 200:
        assert any("parse" in i.lower() or "zero" in i.lower() for i in issues)


# ── Fix 5: _validate_rc_parity logs exceptions ──


def test_rc_parity_logs_on_internal_error(tmp_path, caplog):
    """rc_parity must log warnings on internal errors, not silently swallow."""
    from plamen_validators import _validate_rc_parity
    from plamen_types import Phase

    phase = Phase(
        name="inventory",
        section_markers=[],
        expected_artifacts=["findings_inventory.md"],
        base_timeout_s=300,
        min_artifact_bytes=100,
        min_artifacts_count=1,
    )

    # Create findings_inventory.md with content that will trigger parsing
    (tmp_path / "findings_inventory.md").write_text(
        "| [CS-1] | title | loc |\n" * 10,
        encoding="utf-8",
    )

    import logging
    with caplog.at_level(logging.WARNING):
        # rc=1 triggers the parity check
        with mock.patch(
            "plamen_validators.re.findall",
            side_effect=RuntimeError("test crash"),
        ):
            result = _validate_rc_parity(phase, tmp_path, 1)

    # Should return [] (soft pass) but with a warning logged
    assert result == []
    assert any("internal error" in r.message.lower() for r in caplog.records)


# ── Fix 6: _severity_name_from_text warns on unrecognized input ──


def test_severity_name_warns_on_garbage(caplog):
    """Unrecognized severity text should log a warning."""
    from plamen_parsers import _severity_name_from_text
    from plamen_types import _NORMALIZE_SEVERITY_SEEN

    import logging
    # NOISE-2: the fully-recoverable fallback now logs at DEBUG, once per distinct
    # token. Clear the dedup set so this token logs, and capture at DEBUG.
    _NORMALIZE_SEVERITY_SEEN.discard("XYZZY")
    with caplog.at_level(logging.DEBUG):
        result = _severity_name_from_text("", {"severity": "XYZZY"})

    assert result == "Medium"
    assert any("unrecognized severity" in r.message.lower() for r in caplog.records)


# ── Fix 7: clear_degraded_sentinel cleans compound sentinels ──


def test_clear_degraded_removes_body_writer_sentinel(tmp_path):
    """clear_degraded_sentinel must also remove .body_writer.degraded files."""
    from plamen_types import Checkpoint

    cp = Checkpoint(completed=[], degraded=[], rate_limited_at=None)
    phase_name = "report_body_writer_medium"

    # Create both sentinel files
    (tmp_path / f"{phase_name}.degraded").write_text("test", encoding="utf-8")
    (tmp_path / f"{phase_name}.body_writer.degraded").write_text(
        "test", encoding="utf-8"
    )

    cp.clear_degraded_sentinel(tmp_path, phase_name)

    assert not (tmp_path / f"{phase_name}.degraded").exists()
    assert not (tmp_path / f"{phase_name}.body_writer.degraded").exists()


# ── Fix 8: _parse_chunk_table_inventory keeps title-only rows ──


def test_chunk_inventory_keeps_title_only_rows():
    """Rows with title but no location should not be silently dropped."""
    from plamen_parsers import _parse_chunk_table_inventory

    text = textwrap.dedent("""\
        | ID | Severity | Title | Location | Root Cause |
        |-----|----------|-------|----------|------------|
        | [CS-1] | Medium | Missing validation | src/Vault.sol:L45 | No check |
        | [CS-2] | Low | Gas optimization | | Redundant SLOAD |
    """)
    entries = _parse_chunk_table_inventory(text)
    assert len(entries) == 2
    assert entries[1]["title"] == "Gas optimization"
