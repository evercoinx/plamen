from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_validators import (  # noqa: E402
    _check_dedup_decision_coverage,
    _dedup_decision_coverage_detail,
    _generate_dedup_decision_retry_hint,
    _repair_dedup_missing_dispositions,
)


def _write_pairs(sp: Path) -> None:
    (sp / "dedup_candidate_pairs.md").write_text(
        "| Pair | Finding A | Finding B |\n"
        "|---|---|---|\n"
        "| 1 | H-1 | H-2 |\n"
        "| 2 | H-3 | H-4 |\n"
        "| 3 | H-5 | H-6 |\n",
        encoding="utf-8",
    )


def test_dedup_detail_lists_only_unaccounted_candidate_rows(tmp_path: Path):
    _write_pairs(tmp_path)
    (tmp_path / "dedup_decisions.md").write_text(
        "| Pair | Decision |\n"
        "|---|---|\n"
        "| 1 | KEEP SEPARATE |\n",
        encoding="utf-8",
    )

    detail = _dedup_decision_coverage_detail(tmp_path)

    assert detail["pair_count"] == 3
    assert detail["accounted"] == 1
    assert detail["missing_count"] == 2
    assert "H-3" in detail["missing_rows"][0]
    assert "H-5" in detail["missing_rows"][1]
    assert _check_dedup_decision_coverage(tmp_path)


def test_dedup_retry_hint_preserves_existing_rows(tmp_path: Path):
    _write_pairs(tmp_path)
    (tmp_path / "dedup_decisions.md").write_text(
        "| Pair | Decision |\n"
        "|---|---|\n"
        "| 1 | MERGE |\n",
        encoding="utf-8",
    )

    hint = _generate_dedup_decision_retry_hint(tmp_path, "sc_semantic_dedup")

    assert "Repair only the missing dispositions" in hint
    assert "H-3" in hint
    assert "H-5" in hint
    assert "MERGE/GROUP/KEEP SEPARATE" in hint


def test_dedup_mechanical_repair_adds_passthrough_rows(tmp_path: Path):
    _write_pairs(tmp_path)
    (tmp_path / "dedup_decisions.md").write_text(
        "| Pair | Decision |\n|---|---|\n| 1 | KEEP SEPARATE |\n",
        encoding="utf-8",
    )

    repaired = _repair_dedup_missing_dispositions(tmp_path, "sc_semantic_dedup")

    assert repaired == 2
    assert _check_dedup_decision_coverage(tmp_path) == []
    text = (tmp_path / "dedup_decisions.md").read_text(encoding="utf-8")
    assert "Mechanical Missing Disposition Repair" in text
    assert "PASSTHROUGH" in text


def test_fresh_dedup_coverage_gap_is_repaired_without_blocking(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "_audit_started_with_markers.json").write_text("{}", encoding="utf-8")
    _write_pairs(sp)
    (sp / "dedup_decisions.md").write_text(
        "| Pair | Decision |\n|---|---|\n| 1 | KEEP SEPARATE |\n",
        encoding="utf-8",
    )
    (sp / "findings_inventory_deduped.md").write_text("x" * 200, encoding="utf-8")
    phase = D.Phase(
        name="sc_semantic_dedup",
        section_markers=[],
        expected_artifacts=["dedup_decisions.md", "findings_inventory_deduped.md"],
        base_timeout_s=1,
        min_artifact_bytes=10,
    )

    passed, missing = D._run_phase_validators(
        phase,
        {"mode": "core", "pipeline": "sc", "project_root": str(tmp_path)},
        sp,
        [],
        0,
        {},
    )

    assert passed is True, missing
    assert missing == []
    decisions = (sp / "dedup_decisions.md").read_text(encoding="utf-8")
    assert "Mechanical Missing Disposition Repair" in decisions
    assert "PASSTHROUGH" in decisions
