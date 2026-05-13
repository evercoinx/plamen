from __future__ import annotations

import json
from pathlib import Path

import plamen_driver as D


def _seed_queue_and_verify(
    sp: Path,
    fid: str = "INV-001",
    severity: str = "High",
    verdict: str = "CONFIRMED",
    title: str = "same root cause",
    location: str = "crates/p2p/src/lib.rs:L10",
    recommendation: str = "Apply the same bounded fix.",
    impact: str = "High",
    likelihood: str = "High",
) -> None:
    q = sp / "verification_queue.md"
    existing = q.read_text(encoding="utf-8") if q.exists() else (
        "# Verification Queue\n\n"
        "| Finding ID | Severity | Title | Location | Preferred Tag |\n"
        "|------------|----------|-------|----------|---------------|\n"
    )
    existing += f"| {fid} | {severity} | {title} | {location} | CODE-TRACE |\n"
    q.write_text(existing, encoding="utf-8")
    (sp / f"verify_{fid}.md").write_text(
        f"""# {title}
**Verdict**: {verdict}
**Severity**: {severity}
**Impact**: {impact}
**Likelihood**: {likelihood}
**Location**: {location}
**Description**: This finding describes {title} with enough detail to report.
**Recommendation**: {recommendation}
**Evidence Tag**: CODE-TRACE
""",
        encoding="utf-8",
    )


def test_contested_is_not_unresolved_demotion(tmp_path: Path):
    _seed_queue_and_verify(
        tmp_path,
        verdict="CONTESTED",
        severity="High",
        impact="High",
        likelihood="Medium",
    )

    D._write_mechanical_report_index(tmp_path)
    records = json.loads((tmp_path / "report_records.json").read_text(encoding="utf-8"))

    assert records["active"][0]["verdict"] == "CONTESTED"
    assert records["active"][0]["severity"] == "High"
    assert records["active"][0]["unresolved"] is False


def test_consolidation_map_absorbed_ids_satisfy_index_completeness(tmp_path: Path):
    for n in range(1, 4):
        _seed_queue_and_verify(
            tmp_path,
            fid=f"INV-{n:03d}",
            severity="Medium",
            title="duplicate bounded cache growth",
            location="crates/p2p/src/cache.rs:L42",
            recommendation="Add the same max_capacity guard.",
            impact="Medium",
            likelihood="Medium",
        )

    D._write_mechanical_report_index(tmp_path)
    idx = (tmp_path / "report_index.md").read_text(encoding="utf-8")
    records = json.loads((tmp_path / "report_records.json").read_text(encoding="utf-8"))

    assert len(records["active"]) == 1
    assert records["active"][0]["finding_id"] == "INV-001"
    assert records["active"][0]["absorbed_finding_ids"] == ["INV-001", "INV-002", "INV-003"]
    assert "INV-001, INV-002, INV-003" in idx
    assert D._check_index_completeness(tmp_path) == []


def test_report_coverage_ledger_uses_authoritative_candidate_sources(tmp_path: Path):
    (tmp_path / "analysis_01.md").write_text(
        "## [INV-001] Candidate\nA raw discovery item.",
        encoding="utf-8",
    )
    _seed_queue_and_verify(tmp_path, fid="INV-001", severity="Low")

    D._write_mechanical_report_index(tmp_path)
    coverage = (tmp_path / "report_coverage.md").read_text(encoding="utf-8")

    assert "## Raw Candidate Ledger" in coverage
    assert "| verification_queue.md | INV-001 | PROMOTED |" in coverage
    assert "| analysis_01.md | INV-001 | PROMOTED |" not in coverage


def test_assembled_appendix_uses_traceability_records_not_master_index_paste(tmp_path: Path):
    _seed_queue_and_verify(
        tmp_path,
        fid="INV-001",
        severity="High",
        impact="High",
        likelihood="Medium",
    )
    D._write_mechanical_report_index(tmp_path)
    D._write_mechanical_report_tier(tmp_path, "report_critical_high")

    assert D._assemble_report_python(tmp_path, str(tmp_path)) is True
    report = (tmp_path / "AUDIT_REPORT.md").read_text(encoding="utf-8")
    trace = (tmp_path / "report_traceability_internal.md").read_text(encoding="utf-8")

    assert "## Appendix A: Internal Audit Traceability" not in report
    assert "Internal Report Traceability" in trace
    assert "| H-01 | INV-001 | n/a | verify_INV-001.md | INV-001 |" in trace
    assert "## Master Finding Index" not in report


def test_empty_tier_auth_token_without_sidecar_fails(tmp_path: Path):
    (tmp_path / "report_critical_high.md").write_text(
        "# Critical and High Findings\n\n"
        "_No findings of this severity tier._\n\n"
        "<!-- Empty-Tier-Auth: PLAMEN-DRIVER-AUTHENTIC-EMPTY-TIER -->\n",
        encoding="utf-8",
    )

    issues = D._validate_tier_body_against_manifest(tmp_path, "report_critical_high")

    assert issues
    assert "body_manifests missing" in issues[0]


def test_stale_verify_none_does_not_mask_malformed_nonempty_queue(tmp_path: Path):
    (tmp_path / "verification_queue.md").write_text(
        "# Verification Queue\n\nThis is not the queue table, but it is not empty.\n",
        encoding="utf-8",
    )
    (tmp_path / "verify_NONE.md").write_text(
        "# No findings\n\nTotal: 0 findings\n" + ("x" * 120),
        encoding="utf-8",
    )

    issues = D._validate_report_index_inputs(tmp_path)

    assert issues
    assert "schema invalid" in issues[0]
