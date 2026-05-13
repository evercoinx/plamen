from pathlib import Path

import plamen_driver as D


def _queue(sp: Path, rows: list[tuple[str, str]]) -> None:
    lines = [
        "# Verification Queue",
        "",
        "| Finding ID | Severity | Title | Location | Preferred Tag |",
        "|------------|----------|-------|----------|---------------|",
    ]
    for fid, sev in rows:
        lines.append(f"| {fid} | {sev} | {fid} title | src/{fid}.sol:L1 | CODE-TRACE |")
    (sp / "verification_queue.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _verify(sp: Path, fid: str, sev: str = "High") -> None:
    (sp / f"verify_{fid}.md").write_text(
        "\n".join([
            f"# Verify {fid}",
            f"**Title**: {fid} title",
            "**Verdict**: CONFIRMED",
            f"**Severity**: {sev}",
            f"**Location**: src/{fid}.sol:L1",
            "**Evidence Tag**: [CODE-TRACE]",
            "**Description**: confirmed",
            "**Impact**: impact",
            "**Recommendation**: fix",
            "",
        ]),
        encoding="utf-8",
    )


def test_crossbatch_coverage_ignores_stale_manifest_and_uses_current_verify_files(tmp_path: Path):
    sp = tmp_path
    _queue(sp, [("H-1", "High"), ("H-2", "Medium")])
    _verify(sp, "H-1", "High")
    _verify(sp, "H-2", "Medium")
    (sp / "crossbatch_manifest.json").write_text(
        '{"phase":"crossbatch","required_count":1,'
        '"findings":[{"finding_id":"H-1","verify_file":"verify_H-1.md"}]}',
        encoding="utf-8",
    )
    (sp / "cross_batch_consistency.md").write_text(
        "# Crossbatch\n\nH-1: consistent.\n",
        encoding="utf-8",
    )

    issues = D._validate_crossbatch_full_coverage(sp)

    assert issues
    assert "H-2" in issues[0]


def test_skeptic_coverage_ignores_stale_manifest_and_uses_current_ch_verify_files(tmp_path: Path):
    sp = tmp_path
    _queue(sp, [("H-1", "High"), ("H-2", "Critical"), ("H-3", "Medium")])
    _verify(sp, "H-1", "High")
    _verify(sp, "H-2", "Critical")
    _verify(sp, "H-3", "Medium")
    (sp / "skeptic_manifest.json").write_text(
        '{"phase":"skeptic","required_count":1,'
        '"findings":[{"finding_id":"H-1"}]}',
        encoding="utf-8",
    )
    (sp / "skeptic_findings.md").write_text("H-1: reviewed.\n", encoding="utf-8")
    (sp / "skeptic_judge_decisions.md").write_text("H-1: agree.\n", encoding="utf-8")

    issues = D._validate_skeptic_full_ch_coverage(sp)

    assert issues
    assert "H-2" in issues[0]


def test_normalize_severity_accepts_markdown_and_refuted_na_variants() -> None:
    assert D.normalize_severity("** Informational") == "Informational"
    assert D.normalize_severity("** Low") == "Low"
    assert D.normalize_severity("N/a (refuted)") == "Informational"
    assert D.normalize_severity("N/a (absorbed into de-2)") == "Informational"
    assert D.normalize_severity("duplicate / merged into INV-004") == "Informational"
    assert D.normalize_severity("Already captured in inv-025. not re-reported.") == "Informational"
    assert D.normalize_severity("Already captured in inv-001 (critical). not re-reported.") == "Informational"
    assert D.normalize_severity("Covered by H-01; same root cause.") == "Informational"
    assert D.normalize_severity("Retained in appendix only") == "Informational"
    assert D.normalize_severity("Severity: **High**") == "High"
    assert D.normalize_severity("High, [CODE-TRACE]") == "High"
    assert D.try_normalize_severity("Already captured in H-01 (critical)") is None
