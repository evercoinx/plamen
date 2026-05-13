from pathlib import Path

import pytest

import plamen_driver as D


def _phase(name: str, artifacts: list[str]) -> D.Phase:
    return D.Phase(name, ["Section"], artifacts, base_timeout_s=60, min_artifact_bytes=10)


def test_completed_phase_with_missing_artifact_rewinds_downstream(tmp_path: Path):
    checkpoint = D.Checkpoint(
        completed=["recon", "inventory", "report_index"],
        degraded=["report_index"],
    )
    phases = [
        _phase("recon", ["recon_summary.md"]),
        _phase("inventory", ["findings_inventory.md"]),
        _phase("report_index", ["report_index.md"]),
    ]
    (tmp_path / "recon_summary.md").write_text("substantial recon\n", encoding="utf-8")

    removed = D._reconcile_completed_checkpoint_artifacts(
        tmp_path, str(tmp_path), checkpoint, phases, "core"
    )

    assert removed == ["inventory", "report_index"]
    assert checkpoint.completed == ["recon"]
    assert checkpoint.degraded == []


def test_completed_phase_reconciliation_keeps_valid_prefix(tmp_path: Path):
    checkpoint = D.Checkpoint(completed=["recon", "inventory"], degraded=[])
    phases = [
        _phase("recon", ["recon_summary.md"]),
        _phase("inventory", ["findings_inventory.md"]),
    ]
    (tmp_path / "recon_summary.md").write_text("substantial recon\n", encoding="utf-8")
    (tmp_path / "findings_inventory.md").write_text("substantial inventory\n", encoding="utf-8")

    removed = D._reconcile_completed_checkpoint_artifacts(
        tmp_path, str(tmp_path), checkpoint, phases, "core"
    )

    assert removed == []
    assert checkpoint.completed == ["recon", "inventory"]


def test_completed_instantiate_reconciliation_rejects_bad_manifest(tmp_path: Path):
    checkpoint = D.Checkpoint(completed=["recon", "instantiate", "breadth"], degraded=[])
    phases = [
        _phase("recon", ["recon_summary.md"]),
        _phase("instantiate", ["spawn_manifest.md"]),
        _phase("breadth", ["analysis_*.md"]),
    ]
    (tmp_path / "recon_summary.md").write_text("substantial recon\n", encoding="utf-8")
    (tmp_path / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "| Template | Required? | Agent ID | Focus Area | Expected Output | Status |",
            "|---|---|---|---|---|---|",
            "| Verifier Template | YES | | | verify_.md | PENDING |",
        ]),
        encoding="utf-8",
    )

    removed = D._reconcile_completed_checkpoint_artifacts(
        tmp_path, str(tmp_path), checkpoint, phases, "core"
    )

    assert removed == ["instantiate", "breadth"]
    assert checkpoint.completed == ["recon"]


def test_recon_rc_parity_rejects_stub_handoff(tmp_path: Path):
    phase = _phase("recon", ["recon_summary.md", "build_status.md"])
    (tmp_path / "recon_summary.md").write_text(
        "# Recon Summary (stub)\n- Target: {TBD}\n- Skills to load: [LLM TO LIST]\n",
        encoding="utf-8",
    )
    (tmp_path / "build_status.md").write_text("", encoding="utf-8")

    issues = D._validate_rc_parity(phase, tmp_path, -2)

    assert any("recon_summary.md" in issue for issue in issues)
    assert any("build_status.md" in issue for issue in issues)


def test_completed_checkpoint_must_be_prefix_closed(tmp_path: Path):
    checkpoint = D.Checkpoint(completed=["report_index"], degraded=[])
    phases = [
        _phase("recon", ["recon_summary.md"]),
        _phase("inventory", ["findings_inventory.md"]),
        _phase("report_index", ["report_index.md"]),
    ]
    (tmp_path / "report_index.md").write_text("substantial report index\n", encoding="utf-8")

    removed = D._reconcile_completed_checkpoint_artifacts(
        tmp_path, str(tmp_path), checkpoint, phases, "core"
    )

    assert removed == ["report_index"]
    assert checkpoint.completed == []


def test_completed_inventory_uses_content_contract_not_just_size(tmp_path: Path):
    checkpoint = D.Checkpoint(
        completed=["recon", "inventory", "report_index"],
        degraded=["report_index"],
    )
    phases = [
        _phase("recon", ["recon_summary.md"]),
        _phase("inventory", ["findings_inventory.md"]),
        _phase("report_index", ["report_index.md"]),
    ]
    (tmp_path / "recon_summary.md").write_text("substantial recon\n", encoding="utf-8")
    (tmp_path / "findings_inventory.md").write_text(
        "# Inventory\n\nTotal Findings: 10\n\nThis file is large enough to pass a byte gate "
        "but has no detailed finding blocks.\n",
        encoding="utf-8",
    )
    (tmp_path / "report_index.md").write_text("substantial report index\n", encoding="utf-8")

    removed = D._reconcile_completed_checkpoint_artifacts(
        tmp_path, str(tmp_path), checkpoint, phases, "core"
    )

    assert removed == ["inventory", "report_index"]
    assert checkpoint.completed == ["recon"]
    assert checkpoint.degraded == []


def test_completed_report_index_requires_coverage_accounting(tmp_path: Path):
    checkpoint = D.Checkpoint(completed=["report_index"], degraded=[])
    phases = [_phase("report_index", ["report_index.md"])]
    (tmp_path / "report_index.md").write_text("substantial report index\n", encoding="utf-8")

    removed = D._reconcile_completed_checkpoint_artifacts(
        tmp_path, str(tmp_path), checkpoint, phases, "core"
    )

    assert removed == ["report_index"]
    assert checkpoint.completed == []


def test_checkpoint_load_rejects_schema_invalid_json(tmp_path: Path):
    (tmp_path / "_v2_checkpoint.json").write_text(
        '{"completed": "recon", "degraded": [], "rate_limited_at": null}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="completed must be a list"):
        D.Checkpoint.load(tmp_path)
