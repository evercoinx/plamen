"""Phase E14 — guardrails for the new permissive paths.

Codex called out two ways the round-2 changes could re-open old failure
classes if too broad. This file locks the narrow form:

A. Crossbatch / skeptic soft-pass MUST only apply when the expected ID
   set is empty. An "Overall: PASS" affirmation alone is NOT enough when
   verify IDs (or C/H IDs) exist — the file must enumerate IDs / ranges
   that actually cover the expected set. Otherwise we recreate the
   "checked 41 of 203 but said PASS" failure mode.

B. Stale degraded-sentinel cleanup MUST be checkpoint-aware. On resume,
   sentinels for phases the checkpoint still marks degraded must be
   preserved; sentinels for phases the checkpoint marks completed (or for
   which there is no checkpoint state at all) are stale and may be cleared.

Tests required by Codex spec:
  CB.soft_pass_empty_expected_ok
  CB.soft_pass_nonempty_expected_rejected
  SK.soft_pass_empty_expected_ok
  SK.soft_pass_nonempty_expected_rejected
  RESUME.keeps_degraded_sentinel_when_checkpoint_degraded
  RESUME.clears_stale_sentinel_when_checkpoint_clean

Run: `python test_phase_e_guardrails.py`
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label} :: {detail}")


# =============================================================================
# A. Crossbatch / skeptic soft-pass narrowed to empty-expected-set only.
# =============================================================================

def test_CB_soft_pass_empty_expected_ok(tmp_path: Path):
    """No verify files (clean codebase, 0 Medium+ findings). Crossbatch
    file with PASS affirmation must soft-pass."""
    sp = tmp_path
    (sp / "cross_batch_consistency.md").write_text(
        "# Cross-Batch\n\nOverall: PASS\nNo inconsistencies detected.\n",
        encoding="utf-8",
    )
    issues = D._validate_crossbatch_full_coverage(sp)
    check("CB.soft_pass_empty_expected_ok", not issues, repr(issues))


def test_CB_soft_pass_nonempty_expected_rejected(tmp_path: Path):
    """5 verify files exist; crossbatch only writes a PASS affirmation
    without enumerating any ID. MUST hard-halt — affirmation alone does
    not substitute for coverage when expected set is non-empty."""
    sp = tmp_path
    for i in range(1, 6):
        (sp / f"verify_INV-{i:03d}.md").write_text(
            f"# INV-{i:03d}\n**Verdict**: CONFIRMED\n", encoding="utf-8")
    (sp / "cross_batch_consistency.md").write_text(
        "# Cross-Batch\n\nOverall: PASS\nAll findings consistent.\n"
        "No inconsistencies detected. Inconsistencies: 0\n",
        encoding="utf-8",
    )
    issues = D._validate_crossbatch_full_coverage(sp)
    check(
        "CB.soft_pass_nonempty_expected_rejected",
        bool(issues) and "5/5" in str(issues),
        repr(issues),
    )


def test_CB_pass_phrase_supplements_coverage(tmp_path: Path):
    """When the file enumerates all expected IDs AND has a PASS phrase,
    soft-pass. PASS phrase supplements coverage, never replaces it."""
    sp = tmp_path
    for i in range(1, 4):
        (sp / f"verify_INV-{i:03d}.md").write_text(
            f"# INV-{i:03d}\n**Verdict**: CONFIRMED\n", encoding="utf-8")
    (sp / "cross_batch_consistency.md").write_text(
        "# Cross-Batch\n\nOverall: PASS\n"
        "Reviewed: INV-001, INV-002, INV-003 — all consistent.\n",
        encoding="utf-8",
    )
    issues = D._validate_crossbatch_full_coverage(sp)
    check("CB.pass_phrase_supplements_coverage", not issues, repr(issues))


def test_CB_range_coverage_with_pass_phrase(tmp_path: Path):
    """Range coverage covers expected set; PASS phrase is supplement."""
    sp = tmp_path
    for i in range(1, 6):
        (sp / f"verify_INV-{i:03d}.md").write_text(
            f"# INV-{i:03d}\n**Verdict**: CONFIRMED\n", encoding="utf-8")
    (sp / "cross_batch_consistency.md").write_text(
        "# Cross-Batch\n\nOverall: PASS\nReviewed INV-001..INV-005.\n",
        encoding="utf-8",
    )
    issues = D._validate_crossbatch_full_coverage(sp)
    check("CB.range_coverage_with_pass_phrase", not issues, repr(issues))


def test_SK_soft_pass_empty_expected_ok(tmp_path: Path):
    """No C/H verify files (only Low). Skeptic file with AGREE phrase
    must soft-pass."""
    sp = tmp_path
    # 1 Low verify file — not in skeptic's expected set.
    (sp / "verify_INV-001.md").write_text(
        "# INV-001\n**Verdict**: CONFIRMED\n**Severity**: Low\n",
        encoding="utf-8",
    )
    (sp / "skeptic_findings.md").write_text(
        "# Skeptic-Judge\n\nOverall: AGREE\nNo disagreement.\n",
        encoding="utf-8",
    )
    issues = D._validate_skeptic_full_ch_coverage(sp)
    check("SK.soft_pass_empty_expected_ok", not issues, repr(issues))


def test_SK_soft_pass_nonempty_expected_rejected(tmp_path: Path):
    """3 C/H verify files; skeptic only writes AGREE affirmation without
    enumerating any of the 3 IDs. MUST hard-halt."""
    sp = tmp_path
    (sp / "verification_queue.md").write_text("""# Q

| Finding ID | Severity | Title | Location | Preferred Tag |
|------------|----------|-------|----------|---------------|
| INV-001 | High | a | src/F:L1 | CODE-TRACE |
| INV-002 | High | b | src/F:L2 | CODE-TRACE |
| INV-003 | Critical | c | src/F:L3 | CODE-TRACE |
""", encoding="utf-8")
    for fid, sev in (("INV-001", "High"), ("INV-002", "High"), ("INV-003", "Critical")):
        (sp / f"verify_{fid}.md").write_text(f"""# {fid}
**Verdict**: CONFIRMED
**Severity**: {sev}
**Impact**: High
**Likelihood**: Medium
**Description**: x
**Recommendation**: y
""", encoding="utf-8")
    (sp / "skeptic_findings.md").write_text(
        "# Skeptic-Judge\n\nOverall: AGREE\nNo disagreement.\n",
        encoding="utf-8",
    )
    issues = D._validate_skeptic_full_ch_coverage(sp)
    check(
        "SK.soft_pass_nonempty_expected_rejected",
        bool(issues) and "3/3" in str(issues),
        repr(issues),
    )


def test_SK_manifest_requires_both_aggregate_outputs(tmp_path: Path):
    """A partial retry that writes only skeptic_findings.md must still fail."""
    sp = tmp_path
    (sp / "skeptic_manifest.json").write_text(json.dumps({
        "phase": "skeptic",
        "required_count": 3,
        "findings": [
            {"finding_id": "H-2"},
            {"finding_id": "CH-5"},
            {"finding_id": "CH-1"},
        ],
    }), encoding="utf-8")
    (sp / "skeptic_findings.md").write_text(
        "## H-2 - a\n## CH-5 - b\n## CH-1 - c\n",
        encoding="utf-8",
    )

    issues = D._validate_skeptic_full_ch_coverage(sp)

    assert issues
    assert "skeptic_judge_decisions.md missing" in issues[0]


def test_SK_manifest_requires_each_id_in_each_output(tmp_path: Path):
    """Combined coverage is insufficient; both skeptic files must cover all IDs."""
    sp = tmp_path
    (sp / "skeptic_manifest.json").write_text(json.dumps({
        "phase": "skeptic",
        "required_count": 3,
        "findings": [
            {"finding_id": "H-2"},
            {"finding_id": "CH-5"},
            {"finding_id": "CH-1"},
        ],
    }), encoding="utf-8")
    (sp / "skeptic_findings.md").write_text(
        "## H-2 - a\n## CH-5 - b\n## CH-1 - c\n",
        encoding="utf-8",
    )
    (sp / "skeptic_judge_decisions.md").write_text(
        "| Finding ID | Original Severity | Final Severity | Decision | Rationale |\n"
        "|------------|-------------------|----------------|----------|-----------|\n"
        "| H-2 | High | High | KEEP | ok |\n",
        encoding="utf-8",
    )

    issues = D._validate_skeptic_full_ch_coverage(sp)

    assert issues
    assert "skeptic_judge_decisions.md missing 2/3" in issues[0]


def test_SK_manifest_full_outputs_ok(tmp_path: Path):
    """The manifest coverage gate passes when both aggregate outputs cover all IDs."""
    sp = tmp_path
    ids = ["H-2", "CH-5", "CH-1"]
    (sp / "skeptic_manifest.json").write_text(json.dumps({
        "phase": "skeptic",
        "required_count": len(ids),
        "findings": [{"finding_id": fid} for fid in ids],
    }), encoding="utf-8")
    (sp / "skeptic_findings.md").write_text(
        "\n".join(f"## {fid} - reviewed" for fid in ids),
        encoding="utf-8",
    )
    (sp / "skeptic_judge_decisions.md").write_text(
        "| Finding ID | Original Severity | Final Severity | Decision | Rationale |\n"
        "|------------|-------------------|----------------|----------|-----------|\n"
        + "\n".join(f"| {fid} | High | High | KEEP | ok |" for fid in ids),
        encoding="utf-8",
    )

    issues = D._validate_skeptic_full_ch_coverage(sp)

    assert not issues


# =============================================================================
# B. Checkpoint-aware sentinel cleanup.
# =============================================================================

def _seed_checkpoint(sp: Path, completed: list[str], degraded: list[str]):
    (sp / "_v2_checkpoint.json").write_text(
        json.dumps({
            "completed": completed,
            "degraded": degraded,
            "rate_limited_at": None,
        }),
        encoding="utf-8",
    )


def test_RESUME_keeps_degraded_sentinel_when_checkpoint_degraded(tmp_path: Path):
    """Sentinel for a phase the checkpoint still marks degraded MUST
    survive cleanup — it's the visible reason the prior run halted."""
    sp = tmp_path
    _seed_checkpoint(sp, completed=[], degraded=["report_assemble"])
    sentinel = sp / "report_assemble.degraded"
    sentinel.write_text("Real degraded reason from prior run.\n",
                        encoding="utf-8")
    cleared = D._clear_stale_degraded_sentinels(sp)
    check(
        "RESUME.keeps_degraded_sentinel_when_checkpoint_degraded",
        sentinel.exists() and "report_assemble.degraded" not in cleared,
        f"present={sentinel.exists()} cleared={cleared}",
    )


def test_RESUME_clears_stale_sentinel_when_checkpoint_clean(tmp_path: Path):
    """Sentinel for a phase the checkpoint marks completed (no longer
    in degraded list) is stale debris from an earlier abort and may be
    cleared."""
    sp = tmp_path
    _seed_checkpoint(sp, completed=["report_assemble"], degraded=[])
    sentinel = sp / "report_assemble.degraded"
    sentinel.write_text("Old debris.\n", encoding="utf-8")
    cleared = D._clear_stale_degraded_sentinels(sp)
    check(
        "RESUME.clears_stale_sentinel_when_checkpoint_clean",
        not sentinel.exists()
        and any("report_assemble.degraded" in c for c in cleared),
        f"present={sentinel.exists()} cleared={cleared}",
    )


def test_RESUME_clears_orphan_sentinel_with_no_checkpoint_entry(tmp_path: Path):
    """Sentinel exists but no checkpoint mention (no completed, no
    degraded) — debris from an aborted run before checkpoint was saved.
    Clear it."""
    sp = tmp_path
    _seed_checkpoint(sp, completed=[], degraded=[])
    sentinel = sp / "report_assemble.degraded"
    sentinel.write_text("Orphan debris.\n", encoding="utf-8")
    cleared = D._clear_stale_degraded_sentinels(sp)
    check(
        "RESUME.clears_orphan_sentinel",
        not sentinel.exists(),
        f"present={sentinel.exists()} cleared={cleared}",
    )


def test_RESUME_body_writer_sentinel_keyed_to_legacy_phase(tmp_path: Path):
    """`report_critical_high.body_writer.degraded` is written by the
    legacy `report_critical_high` phase handler. If that phase is in the
    checkpoint's degraded list, the sentinel must persist."""
    sp = tmp_path
    _seed_checkpoint(sp, completed=[], degraded=["report_critical_high"])
    sentinel = sp / "report_critical_high.body_writer.degraded"
    sentinel.write_text("Body-writer degraded reason.\n", encoding="utf-8")
    D._clear_stale_degraded_sentinels(sp)
    check(
        "RESUME.body_writer_sentinel_keyed_to_legacy_phase",
        sentinel.exists(),
        f"present={sentinel.exists()}",
    )


def test_RESUME_returns_paths_of_kept_and_cleared(tmp_path: Path):
    """The helper's return value should report what was cleared, for
    telemetry. Kept sentinels are not in the cleared list."""
    sp = tmp_path
    _seed_checkpoint(
        sp,
        completed=["verify_queue"],
        degraded=["report_assemble"],
    )
    keep = sp / "report_assemble.degraded"
    keep.write_text("keep\n", encoding="utf-8")
    drop = sp / "verify_queue.degraded"
    drop.write_text("drop\n", encoding="utf-8")

    cleared = D._clear_stale_degraded_sentinels(sp)
    check(
        "RESUME.returns_correct_paths",
        keep.exists() and not drop.exists()
        and any("verify_queue.degraded" in c for c in cleared)
        and not any("report_assemble.degraded" in c for c in cleared),
        f"cleared={cleared}",
    )


# =============================================================================
# Test runner
# =============================================================================

TESTS = [
    test_CB_soft_pass_empty_expected_ok,
    test_CB_soft_pass_nonempty_expected_rejected,
    test_CB_pass_phrase_supplements_coverage,
    test_CB_range_coverage_with_pass_phrase,
    test_SK_soft_pass_empty_expected_ok,
    test_SK_soft_pass_nonempty_expected_rejected,
    test_RESUME_keeps_degraded_sentinel_when_checkpoint_degraded,
    test_RESUME_clears_stale_sentinel_when_checkpoint_clean,
    test_RESUME_clears_orphan_sentinel_with_no_checkpoint_entry,
    test_RESUME_body_writer_sentinel_keyed_to_legacy_phase,
    test_RESUME_returns_paths_of_kept_and_cleared,
]


def main() -> int:
    print(f"Running {len(TESTS)} guardrail tests...")
    for t in TESTS:
        print(f"\n[{t.__name__}]")
        try:
            with tempfile.TemporaryDirectory() as td:
                t(Path(td))
        except Exception as exc:
            global FAIL
            FAIL += 1
            print(f"  CRASH {t.__name__} :: {exc!r}")
    print(f"\n{'=' * 64}")
    print(f"  PASS: {PASS}   FAIL: {FAIL}")
    print('=' * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
