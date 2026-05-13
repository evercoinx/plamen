"""v2.8.7: WARNING validator false-positive pattern fixes.

Regression tests for 7 fixes to WARNING-level validators that
produced false-positive warnings on valid LLM output variants.

P0: tier completeness H2 headings, inventory chunk heading flexibility
P1: recon implications bare heading, coverage ledger synonyms,
    attention repair bullet receipts, never-cut status aliases
P2: skeptic dead code removal (verified by absence of unreachable code)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import plamen_validators as V


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sp() -> Path:
    d = Path(tempfile.mkdtemp())
    return d


def _write(sp: Path, name: str, content: str) -> Path:
    p = sp / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# P0 #1: _validate_report_tier_completeness — H2 headings accepted
# ---------------------------------------------------------------------------

class TestTierCompletenessH2:
    """LLM sometimes emits ## [H-01] instead of ### [H-01]."""

    def _setup_index(self, sp: Path, counts: dict) -> None:
        lines = ["# Report Index", "", "## Summary Counts",
                 "| Severity | Count |", "|----------|-------|"]
        for sev, n in counts.items():
            lines.append(f"| {sev} | {n} |")
        lines += ["", "## Master Finding Index",
                   "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |",
                   "|-----------|-------|----------|----------|--------------|-----------|---------------------|"]
        for sev, n in counts.items():
            prefix = sev[0].upper()
            for i in range(1, n + 1):
                lines.append(f"| {prefix}-{i:02d} | Title | {sev} | file.sol:1 | VERIFIED | - | H-{i} |")
        _write(sp, "report_index.md", "\n".join(lines))

    def test_h3_still_works(self, tmp_path):
        sp = tmp_path
        self._setup_index(sp, {"High": 2})
        _write(sp, "report_critical_high.md",
               "## High Findings\n\n### [H-01] Bug one\nbody\n\n### [H-02] Bug two\nbody\n")
        issues = V._validate_report_tier_completeness(sp, "report_critical_high")
        assert issues == []

    def test_h2_accepted(self, tmp_path):
        sp = tmp_path
        self._setup_index(sp, {"High": 2})
        _write(sp, "report_critical_high.md",
               "## High Findings\n\n## [H-01] Bug one\nbody\n\n## [H-02] Bug two\nbody\n")
        issues = V._validate_report_tier_completeness(sp, "report_critical_high")
        assert issues == []

    def test_mixed_h2_h3(self, tmp_path):
        sp = tmp_path
        self._setup_index(sp, {"High": 2})
        _write(sp, "report_critical_high.md",
               "## High Findings\n\n## [H-01] Bug one\nbody\n\n### [H-02] Bug two\nbody\n")
        issues = V._validate_report_tier_completeness(sp, "report_critical_high")
        assert issues == []

    def test_h1_not_accepted(self, tmp_path):
        """H1 (#) should NOT match — only ## and ### are valid."""
        sp = tmp_path
        self._setup_index(sp, {"High": 1})
        _write(sp, "report_critical_high.md",
               "## High Findings\n\n# [H-01] Bug one\nbody\n")
        issues = V._validate_report_tier_completeness(sp, "report_critical_high")
        assert len(issues) == 1
        assert "has 0 findings" in issues[0]


# ---------------------------------------------------------------------------
# P0 #2: _validate_inventory_chunk_structure — flexible heading format
# ---------------------------------------------------------------------------

class TestInventoryChunkHeadingFormat:
    """LLMs emit ### [CC-01] Title (no 'Finding' prefix, no colon)."""

    def _chunk_with_heading(self, sp: Path, heading_fmt: str) -> list[str]:
        content = f"""# Findings inventory_chunk_a

## Per-Finding Detail

{heading_fmt}
**Source IDs**: [CS-1]
**Severity**: High
**Location**: file.sol:10
**Evidence Tag**: [CODE]
**Verdict**: CONFIRMED
**Root Cause**: Missing check
**Description**: Something wrong
**Impact**: Fund loss
"""
        _write(sp, "findings_inventory_chunk_a.md", content)
        return V._validate_inventory_chunk_structure(sp, "inventory_chunk_a")

    def test_classic_format(self, tmp_path):
        issues = self._chunk_with_heading(tmp_path, "### Finding [CC-01]: Missing validation")
        field_issues = [i for i in issues if "detail heading" not in i and "detail block" not in i]
        assert field_issues == []

    def test_no_finding_prefix(self, tmp_path):
        issues = self._chunk_with_heading(tmp_path, "### [CC-01] Missing validation")
        field_issues = [i for i in issues if "detail heading" not in i and "detail block" not in i]
        assert field_issues == []

    def test_no_colon(self, tmp_path):
        issues = self._chunk_with_heading(tmp_path, "### Finding [CC-01] Missing validation")
        field_issues = [i for i in issues if "detail heading" not in i and "detail block" not in i]
        assert field_issues == []

    def test_minimal_format(self, tmp_path):
        """### [CC-01] Title — no 'Finding', no colon."""
        issues = self._chunk_with_heading(tmp_path, "### [CC-01] Missing validation")
        field_issues = [i for i in issues if "detail heading" not in i and "detail block" not in i]
        assert field_issues == []


# ---------------------------------------------------------------------------
# P1 #3: _validate_recon_content_structure — bare "implications"
# ---------------------------------------------------------------------------

class TestReconImplications:
    """LLMs sometimes write '## Implications' instead of '## Operational Implications'."""

    def test_operational_implications(self, tmp_path):
        sp = tmp_path
        _write(sp, "design_context.md",
               "# Design Context\n\n## Key Invariants\nSome invariants\n\n"
               "## Operational Implications\nSome implications\n")
        hard, _ = V._validate_recon_content_structure(sp)
        assert not any("Operational Implications" in h for h in hard)

    def test_bare_implications(self, tmp_path):
        sp = tmp_path
        _write(sp, "design_context.md",
               "# Design Context\n\n## Key Invariants\nSome invariants\n\n"
               "## Implications\nSome implications\n")
        hard, _ = V._validate_recon_content_structure(sp)
        assert not any("Operational Implications" in h for h in hard)

    def test_no_implications_still_fails(self, tmp_path):
        sp = tmp_path
        _write(sp, "design_context.md",
               "# Design Context\n\n## Key Invariants\nSome invariants\n\n"
               "## Analysis Results\nStuff\n")
        hard, _ = V._validate_recon_content_structure(sp)
        assert any("Operational Implications" in h for h in hard)


# ---------------------------------------------------------------------------
# P1 #4: _validate_report_coverage_accounting — ledger heading synonyms
# ---------------------------------------------------------------------------

class TestCoverageLedgerSynonyms:

    def _coverage_with_heading(self, sp: Path, heading: str) -> list[str]:
        content = f"""# Report Coverage Audit

{heading}
| Source File | Candidate ID | Severity Signal | Status | Report ID |
|-------------|-------------|-----------------|--------|-----------|
| depth_token_flow_findings.md | TF-01 | High | PROMOTED | H-01 |
"""
        _write(sp, "report_coverage.md", content)
        return V._validate_report_coverage_accounting(sp)

    def test_raw_candidate_ledger(self, tmp_path):
        issues = self._coverage_with_heading(tmp_path, "## Raw Candidate Ledger")
        unaccounted = [i for i in issues if "UNACCOUNTED" in i]
        assert unaccounted == []

    def test_candidate_ledger(self, tmp_path):
        issues = self._coverage_with_heading(tmp_path, "## Candidate Ledger")
        unaccounted = [i for i in issues if "UNACCOUNTED" in i]
        assert unaccounted == []

    def test_coverage_ledger(self, tmp_path):
        issues = self._coverage_with_heading(tmp_path, "## Coverage Ledger")
        unaccounted = [i for i in issues if "UNACCOUNTED" in i]
        assert unaccounted == []

    def test_promotion_ledger(self, tmp_path):
        issues = self._coverage_with_heading(tmp_path, "## Promotion Ledger")
        unaccounted = [i for i in issues if "UNACCOUNTED" in i]
        assert unaccounted == []

    def test_unrelated_heading_not_matched(self, tmp_path):
        """A heading that doesn't match any synonym should not enter the ledger."""
        content = """# Report Coverage Audit

## Some Other Section
| Source File | Candidate ID | Severity Signal | Status | Report ID |
|-------------|-------------|-----------------|--------|-----------|
| depth.md | TF-01 | High | UNACCOUNTED | - |
"""
        _write(tmp_path, "report_coverage.md", content)
        issues = V._validate_report_coverage_accounting(tmp_path)
        unaccounted = [i for i in issues if "UNACCOUNTED" in i]
        assert unaccounted == []


# ---------------------------------------------------------------------------
# P1 #5: _validate_attention_repair — bullet-list receipt fallback
# ---------------------------------------------------------------------------

class TestAttentionRepairBulletReceipts:

    def _setup_repair(self, sp: Path, summary_content: str) -> tuple[list[str], list[str]]:
        _write(sp, "attention_repair_queue.md",
               "| # | Kind | Details |\n|---|------|--------|\n"
               "| 1 | uncited | path/file.sol |\n"
               "| 2 | notread | path/other.sol |\n")
        _write(sp, "attention_repair_summary.md", summary_content)
        _write(sp, "attention_repair_findings.md",
               "# Findings\npath/file.sol analyzed\npath/other.sol analyzed\n")
        return V._validate_attention_repair(sp, "thorough")

    def test_table_receipts(self, tmp_path):
        summary = ("# Summary\n"
                   "| # | Verdict |\n|---|--------|\n"
                   "| 1 | SAFE |\n| 2 | NO_FINDING |\n")
        hard, _ = self._setup_repair(tmp_path, summary)
        receipt_issues = [h for h in hard if "missing queue receipt" in h]
        assert receipt_issues == []

    def test_bullet_receipts(self, tmp_path):
        summary = ("# Summary\nverdict present\n"
                   "- 1: SAFE - no issue found\n"
                   "- 2: NO_FINDING - already handled\n")
        hard, _ = self._setup_repair(tmp_path, summary)
        receipt_issues = [h for h in hard if "missing queue receipt" in h]
        assert receipt_issues == []

    def test_star_bullet_receipts(self, tmp_path):
        summary = ("# Summary\nverdict present\n"
                   "* 1. SAFE no issue\n"
                   "* 2. NO_FINDING checked\n")
        hard, _ = self._setup_repair(tmp_path, summary)
        receipt_issues = [h for h in hard if "missing queue receipt" in h]
        assert receipt_issues == []


# ---------------------------------------------------------------------------
# P1 #6: _match_label_status — COMPLETED/DONE/RAN/YES aliases
# ---------------------------------------------------------------------------

class TestNeverCutStatusAliases:

    def test_spawned(self):
        text = "depth-state-trace: SPAWNED depth_state_trace_findings.md"
        result = V._match_label_status(text, "depth-state-trace")
        assert result is not None
        assert result[0] == "SPAWNED"

    def test_completed_normalized(self):
        text = "depth-state-trace: COMPLETED depth_state_trace_findings.md"
        result = V._match_label_status(text, "depth-state-trace")
        assert result is not None
        assert result[0] == "SPAWNED"

    def test_done_normalized(self):
        text = "- depth-edge-case: DONE depth_edge_case_findings.md"
        result = V._match_label_status(text, "depth-edge-case")
        assert result is not None
        assert result[0] == "SPAWNED"

    def test_ran_normalized(self):
        text = "| depth-external | RAN | depth_external_findings.md |"
        result = V._match_label_status(text, "depth-external")
        assert result is not None
        assert result[0] == "SPAWNED"

    def test_yes_normalized(self):
        text = "confidence-scoring: YES confidence_scores.md"
        result = V._match_label_status(text, "confidence-scoring")
        assert result is not None
        assert result[0] == "SPAWNED"

    def test_skipped_unchanged(self):
        text = "design-stress: SKIPPED MODE_LIGHT"
        result = V._match_label_status(text, "design-stress")
        assert result is not None
        assert result[0] == "SKIPPED"

    def test_checkpoint_integration(self, tmp_path):
        """Full integration: _assert_never_cut_checkpoint accepts COMPLETED."""
        sp = tmp_path
        lines = []
        for label in ["depth-consensus-invariant", "depth-network-surface",
                       "depth-state-trace", "depth-external", "depth-edge-case",
                       "confidence-scoring"]:
            lines.append(f"{label}: COMPLETED some_file.md")
        _write(sp, "never_cut_checkpoint.md", "\n".join(lines))
        issues = V._assert_never_cut_checkpoint(sp, mode="core")
        assert issues == []

    def test_checkpoint_mixed_statuses(self, tmp_path):
        """Mix of SPAWNED, DONE, COMPLETED, RAN — all accepted."""
        sp = tmp_path
        lines = [
            "depth-consensus-invariant: SPAWNED file.md",
            "depth-network-surface: DONE file.md",
            "depth-state-trace: COMPLETED file.md",
            "depth-external: RAN file.md",
            "depth-edge-case: YES file.md",
            "confidence-scoring: SPAWNED file.md",
        ]
        _write(sp, "never_cut_checkpoint.md", "\n".join(lines))
        issues = V._assert_never_cut_checkpoint(sp, mode="core")
        assert issues == []


# ---------------------------------------------------------------------------
# P2 #7: Dead code removal — verified by inspecting source
# ---------------------------------------------------------------------------

class TestSkepticDeadCodeRemoved:
    """Verify the unreachable code after return in _validate_skeptic_full_ch_coverage is gone."""

    def test_no_unreachable_sk_ids(self):
        import inspect
        src = inspect.getsource(V._validate_skeptic_full_ch_coverage)
        # The dead code had `sk_ids = _extract_finding_ids_from_text(blob)` after a return
        assert "sk_ids" not in src
        assert "absent from skeptic artifacts" not in src
