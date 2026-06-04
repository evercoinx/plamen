"""Tests for v2.6.2 confidence quality validators and semantic dedup passthrough detection."""
from __future__ import annotations

import pytest
from pathlib import Path

from plamen_validators import (
    _validate_confidence_scores_quality,
    _validate_confidence_iter2_mandatory,
)
import plamen_driver as D


@pytest.fixture
def scratchpad(tmp_path: Path) -> Path:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    return sp


def _write_uniform_scores(sp: Path, n: int = 13, val: float = 0.635) -> None:
    lines = [
        "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
        "|------------|----------|-----------|---------|-----|-----------|----------------|",
    ]
    for i in range(1, n + 1):
        lines.append(
            f"| DX-{i} | 0.80 | 0.30 | 1.00 | 0.30 | {val} | UNCERTAIN |"
        )
    (sp / "confidence_scores.md").write_text("\n".join(lines), encoding="utf-8")


def _write_varied_scores(sp: Path) -> None:
    lines = [
        "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
        "|------------|----------|-----------|---------|-----|-----------|----------------|",
        "| DX-1 | 0.80 | 0.70 | 1.00 | 0.50 | 0.775 | CONFIDENT |",
        "| DX-2 | 0.80 | 0.30 | 0.40 | 0.30 | 0.455 | UNCERTAIN |",
        "| DX-3 | 0.80 | 0.50 | 0.70 | 0.40 | 0.620 | UNCERTAIN |",
        "| DX-4 | 1.00 | 0.80 | 1.00 | 0.90 | 0.930 | CONFIDENT |",
        "| DX-5 | 0.40 | 0.30 | 0.10 | 0.30 | 0.265 | LOW_CONFIDENCE |",
    ]
    (sp / "confidence_scores.md").write_text("\n".join(lines), encoding="utf-8")


def _write_inventory(sp: Path, findings: list[tuple[str, str]]) -> None:
    """Write a minimal findings_inventory.md with (id, severity) pairs."""
    lines = ["# Findings Inventory\n"]
    for fid, sev in findings:
        lines.append(f"## Finding [{fid}]: Test\n")
        lines.append(f"**Severity**: {sev}\n")
        lines.append(f"**Location**: test.rs:L10\n")
        lines.append("")
    (sp / "findings_inventory.md").write_text("\n".join(lines), encoding="utf-8")


def _write_depth_findings(sp: Path, findings: list[tuple[str, str]]) -> None:
    """Write a depth_state_trace_findings.md with (id, severity) finding blocks.

    Used to exercise severity resolution from the depth-findings namespace
    (the same agent-finding IDs as confidence_scores.md), independent of
    findings_inventory.md's ID namespace.
    """
    lines = ["# Depth State-Trace Findings\n"]
    for fid, sev in findings:
        lines.append(f"### Finding [{fid}]: Test finding\n")
        lines.append(f"**Severity**: {sev}\n")
        lines.append(f"**Location**: src/foo.rs:L42\n")
        lines.append("**Description**: Test depth finding.\n")
        lines.append("")
    (sp / "depth_state_trace_findings.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


# --- _validate_confidence_scores_quality ---

class TestConfidenceScoresQuality:
    def test_uniform_13_findings_thorough(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=13, val=0.635)
        issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        assert len(issues) == 1
        assert "identical composite" in issues[0]
        assert "13 findings" in issues[0]

    def test_uniform_4_findings_thorough(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=4, val=0.500)
        issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        assert len(issues) == 1
        assert "identical composite" in issues[0]

    def test_uniform_3_findings_below_threshold(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=3, val=0.500)
        issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        assert issues == []

    def test_varied_scores_pass(self, scratchpad: Path):
        _write_varied_scores(scratchpad)
        issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        assert issues == []

    def test_core_mode_skips(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=13, val=0.635)
        issues = _validate_confidence_scores_quality(scratchpad, "core")
        assert issues == []

    def test_light_mode_skips(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=13, val=0.635)
        issues = _validate_confidence_scores_quality(scratchpad, "light")
        assert issues == []

    def test_two_values_8plus_findings(self, scratchpad: Path):
        lines = [
            "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
            "|------------|----------|-----------|---------|-----|-----------|----------------|",
        ]
        for i in range(1, 5):
            lines.append(f"| DX-{i} | 0.80 | 0.30 | 1.00 | 0.30 | 0.635 | UNCERTAIN |")
        for i in range(5, 10):
            lines.append(f"| DX-{i} | 0.80 | 0.30 | 1.00 | 0.50 | 0.675 | UNCERTAIN |")
        (scratchpad / "confidence_scores.md").write_text("\n".join(lines), encoding="utf-8")
        issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        assert len(issues) == 1
        assert "2 distinct" in issues[0]

    def test_missing_file_passes(self, scratchpad: Path):
        issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        assert issues == []


# --- _validate_confidence_iter2_mandatory ---

class TestConfidenceIter2Mandatory:
    def test_uncertain_medium_no_iter2(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=5, val=0.500)
        _write_inventory(scratchpad, [
            ("DX-1", "High"),
            ("DX-2", "Medium"),
            ("DX-3", "Medium"),
            ("DX-4", "Low"),
            ("DX-5", "Informational"),
        ])
        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert len(issues) == 1
        assert "3 uncertain Medium+" in issues[0]

    def test_uncertain_low_only_no_issue(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=3, val=0.500)
        _write_inventory(scratchpad, [
            ("DX-1", "Low"),
            ("DX-2", "Low"),
            ("DX-3", "Informational"),
        ])
        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert issues == []

    def test_all_confident_no_issue(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=5, val=0.800)
        _write_inventory(scratchpad, [
            ("DX-1", "High"),
            ("DX-2", "High"),
            ("DX-3", "Medium"),
            ("DX-4", "Medium"),
            ("DX-5", "Low"),
        ])
        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert issues == []

    def test_iter2_files_present_no_issue(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=5, val=0.500)
        _write_inventory(scratchpad, [
            ("DX-1", "High"),
            ("DX-2", "High"),
            ("DX-3", "Medium"),
            ("DX-4", "Medium"),
            ("DX-5", "Low"),
        ])
        (scratchpad / "depth_da_state_trace_findings.md").write_text(
            "# DA findings\n## Finding [DA-1]: Test\n", encoding="utf-8"
        )
        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert issues == []

    def test_depth_iter2_files_also_accepted(self, scratchpad: Path):
        _write_uniform_scores(scratchpad, n=5, val=0.500)
        _write_inventory(scratchpad, [
            ("DX-1", "High"),
            ("DX-2", "Medium"),
            ("DX-3", "Medium"),
            ("DX-4", "Low"),
            ("DX-5", "Low"),
        ])
        (scratchpad / "depth_iter2_edge_case_findings.md").write_text(
            "# Iter 2 findings\n", encoding="utf-8"
        )
        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert issues == []

    def test_missing_inventory_and_no_depth_fails_closed(self, scratchpad: Path):
        # No inventory AND no depth findings -> every uncertain ID has an
        # unresolvable severity -> fail CLOSED (fire iter-2 for recall safety)
        # rather than silently skip the mandated iteration.
        _write_uniform_scores(scratchpad, n=5, val=0.500)
        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert len(issues) == 1
        assert "unresolvable severity" in issues[0]

    def test_missing_scores_passes(self, scratchpad: Path):
        _write_inventory(scratchpad, [("DX-1", "High")])
        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert issues == []

    def test_real_irys_pattern(self, scratchpad: Path):
        """Reproduce the exact Irys L1 failure pattern: 13 uniform UNCERTAIN
        findings with High/Medium severities and no iter2 artifacts."""
        ids = [
            "DEPTH-CONSENSUS-INVARIANT-1", "DEPTH-CONSENSUS-INVARIANT-2",
            "DEPTH-EDGE-CASE-1", "DX-1", "DX-2", "DX-3", "DX-4",
            "DEPTH-NETWORK-SURFACE-1", "DEPTH-NETWORK-SURFACE-2",
            "DEPTH-STATE-TRACE-1", "DEPTH-STATE-TRACE-2",
            "NSG-1", "NSG-2",
        ]
        sevs = [
            "High", "High", "High", "High", "Medium", "Medium", "Medium",
            "High", "Medium", "High", "Medium", "Medium", "Medium",
        ]
        lines = [
            "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
            "|------------|----------|-----------|---------|-----|-----------|----------------|",
        ]
        for fid in ids:
            lines.append(f"| {fid} | 0.80 | 0.30 | 1.00 | 0.30 | 0.635 | UNCERTAIN |")
        (scratchpad / "confidence_scores.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        _write_inventory(scratchpad, list(zip(ids, sevs)))

        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert len(issues) == 1
        assert "13 uncertain Medium+" in issues[0]
        assert "iter2/DA artifacts" in issues[0]

    def test_mismatched_namespace_resolves_from_depth(self, scratchpad: Path):
        """Regression: confidence_scores uses DS-* IDs; findings_inventory uses
        an UNRELATED INV-* namespace so the old inventory ID-join misses every
        uncertain ID. Severity must resolve from depth_state_trace_findings.md
        and the mandated iter-2 must fire (BEFORE this fix it silently
        returned [] — fail-open)."""
        lines = [
            "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
            "|------------|----------|-----------|---------|-----|-----------|----------------|",
            "| DS-1 | 0.80 | 0.30 | 1.00 | 0.30 | 0.500 | UNCERTAIN |",
            "| DS-2 | 0.80 | 0.30 | 1.00 | 0.30 | 0.500 | UNCERTAIN |",
        ]
        (scratchpad / "confidence_scores.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        # Inventory uses an entirely different ID namespace -> ID-join misses.
        _write_inventory(scratchpad, [("INV-1", "Low")])
        # Depth findings share the DS-* namespace (lower/mixed case to lock in
        # case-insensitive resolution).
        _write_depth_findings(scratchpad, [("ds-1", "High"), ("DS-2", "Medium")])

        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert len(issues) == 1
        assert "2 uncertain Medium+" in issues[0]

    def test_mismatched_namespace_unresolved_fails_closed(self, scratchpad: Path):
        """Mismatched namespace AND no depth file AND no inventory match ->
        severity unresolvable -> fail CLOSED (fire iter-2 for recall safety)."""
        lines = [
            "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
            "|------------|----------|-----------|---------|-----|-----------|----------------|",
            "| DS-1 | 0.80 | 0.30 | 1.00 | 0.30 | 0.500 | UNCERTAIN |",
        ]
        (scratchpad / "confidence_scores.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        _write_inventory(scratchpad, [("INV-1", "Low")])

        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert len(issues) == 1
        assert "unresolvable severity" in issues[0]

    def test_mismatched_namespace_all_below_medium_no_issue(self, scratchpad: Path):
        """Mismatched namespace, depth file resolves ALL uncertain IDs to
        below-Medium -> [] (no over-spawn; Rule 3a still respected)."""
        lines = [
            "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
            "|------------|----------|-----------|---------|-----|-----------|----------------|",
            "| DS-1 | 0.80 | 0.30 | 1.00 | 0.30 | 0.500 | UNCERTAIN |",
            "| DS-2 | 0.80 | 0.30 | 1.00 | 0.30 | 0.500 | UNCERTAIN |",
        ]
        (scratchpad / "confidence_scores.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        _write_inventory(scratchpad, [("INV-1", "Low")])
        _write_depth_findings(scratchpad, [("DS-1", "Low"), ("DS-2", "Informational")])

        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert issues == []

    def test_mismatched_namespace_medium_but_da_artifact_short_circuits(self, scratchpad: Path):
        """Depth file resolves Medium+ AND a DA artifact exists -> [] (existing
        da_files glob short-circuit still suppresses re-run)."""
        lines = [
            "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
            "|------------|----------|-----------|---------|-----|-----------|----------------|",
            "| DS-1 | 0.80 | 0.30 | 1.00 | 0.30 | 0.500 | UNCERTAIN |",
        ]
        (scratchpad / "confidence_scores.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        _write_inventory(scratchpad, [("INV-1", "Low")])
        _write_depth_findings(scratchpad, [("DS-1", "High")])
        (scratchpad / "depth_da_state_trace_findings.md").write_text(
            "# DA findings\n### Finding [DA-1]: Test\n", encoding="utf-8"
        )

        issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert issues == []


# --- v2.8.1: stub scores above 0.7 must still force iter2 in thorough ---

class TestStubAboveThresholdForcesIter2:
    """When the scoring agent stamps all findings with above-0.7 values
    (e.g., 0.730/0.810), _validate_confidence_iter2_mandatory returns []
    because no score is < 0.7.  The v2.8.1 driver logic chains the stub
    detection into iter2 enforcement so the gate still fails."""

    def _write_above_threshold_stubs(self, sp: Path, n: int = 91) -> None:
        lines = [
            "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |",
            "|------------|----------|-----------|---------|-----|-----------|----------------|",
        ]
        for i in range(1, n + 1):
            val = 0.730 if i % 2 == 0 else 0.810
            lines.append(
                f"| DX-{i} | 0.80 | 0.70 | 1.00 | 0.70 | {val:.3f} | CONFIDENT |"
            )
        (sp / "confidence_scores.md").write_text("\n".join(lines), encoding="utf-8")

    def test_stub_detected_but_iter2_check_passes(self, scratchpad: Path):
        """Confirm the gap: stub detector fires, iter2 check doesn't."""
        self._write_above_threshold_stubs(scratchpad)
        _write_inventory(scratchpad, [(f"DX-{i}", "High") for i in range(1, 92)])

        quality_issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        assert quality_issues, "stub detector should fire on 2-value 91-finding scores"

        iter2_issues = _validate_confidence_iter2_mandatory(scratchpad)
        assert iter2_issues == [], "all scores > 0.7 so iter2 check alone sees nothing"

    def test_driver_chains_stub_into_iter2_enforcement(self, scratchpad: Path):
        """The driver-level chaining (v2.8.1) must force iter2 when stubs
        are above 0.7 and no DA/iter2 files exist."""
        self._write_above_threshold_stubs(scratchpad)
        _write_inventory(scratchpad, [(f"DX-{i}", "High") for i in range(1, 92)])

        quality_issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        iter2_issues = _validate_confidence_iter2_mandatory(scratchpad)

        # Simulate the v2.8.1 driver logic
        if not iter2_issues and quality_issues:
            da_files = (
                list(scratchpad.glob("depth_da_*_findings.md"))
                + list(scratchpad.glob("depth_iter2_*_findings.md"))
            )
            if not da_files:
                iter2_issues = [
                    "confidence scores are formulaic stubs; "
                    "iter2 mandatory to produce real per-finding analysis"
                ]

        assert iter2_issues, "combined logic should force iter2"
        assert "formulaic stubs" in iter2_issues[0]

    def test_driver_chains_skips_when_da_files_exist(self, scratchpad: Path):
        """If DA/iter2 artifacts exist, the stub is informational only."""
        self._write_above_threshold_stubs(scratchpad)
        _write_inventory(scratchpad, [(f"DX-{i}", "High") for i in range(1, 92)])
        (scratchpad / "depth_da_state_trace_findings.md").write_text(
            "# DA findings", encoding="utf-8"
        )

        quality_issues = _validate_confidence_scores_quality(scratchpad, "thorough")
        iter2_issues = _validate_confidence_iter2_mandatory(scratchpad)

        # Same driver logic
        if not iter2_issues and quality_issues:
            da_files = (
                list(scratchpad.glob("depth_da_*_findings.md"))
                + list(scratchpad.glob("depth_iter2_*_findings.md"))
            )
            if not da_files:
                iter2_issues = ["stubs"]

        assert iter2_issues == [], "DA files present — stub warning is non-blocking"

    def test_core_mode_not_affected(self, scratchpad: Path):
        """Core mode doesn't enforce iter2 at all, stubs or not."""
        self._write_above_threshold_stubs(scratchpad)
        quality_issues = _validate_confidence_scores_quality(scratchpad, "core")
        assert quality_issues == [], "core mode skips stub detection"
