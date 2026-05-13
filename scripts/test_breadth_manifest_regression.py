from pathlib import Path
import importlib.util

import plamen_driver as D


def test_gate_summary_reports_small_artifacts_in_bytes(tmp_path: Path):
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    (sp / "tiny.md").write_text("x" * 80, encoding="utf-8")
    phase = D.Phase(
        "tiny", ["Section"], ["tiny.md"],
        base_timeout_s=60, min_artifact_bytes=10,
    )

    summary = D._format_gate_summary(phase, sp, {"project_root": str(project)})

    assert "80B" in summary
    assert "0KB" not in summary


def test_breadth_manifest_ignores_non_analysis_output_rows(tmp_path: Path):
    sp = tmp_path
    (sp / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Expected Output | Status | Type |",
            "|----------|-----------|----------|------------|-----------------|--------|------|",
            "| Core State | YES | B1 | Core State | analysis_core_state.md | PENDING | agent |",
            "| Verifier Template | YES |  |  | verify_.md | PENDING | template |",
            "| External | YES | B2 | External Integrations | analysis_external_integrations.md | PENDING | agent |",
        ]),
        encoding="utf-8",
    )

    outputs = D.parse_breadth_manifest_outputs(sp)

    assert outputs == [
        "analysis_core_state.md",
        "analysis_external_integrations.md",
    ]


def test_breadth_manifest_excludes_reserved_analysis_subfamilies(tmp_path: Path):
    sp = tmp_path
    (sp / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Expected Output | Status | Type |",
            "|----------|-----------|----------|------------|-----------------|--------|------|",
            "| Core State | YES | B1 | Core State | analysis_core_state.md | PENDING | agent |",
            "| Rescan | YES | B2 | Rescan | analysis_rescan_1.md | PENDING | agent |",
            "| Per Contract | YES | B3 | Per Contract | analysis_percontract_vault.md | PENDING | agent |",
            "| Merged | YES | B4 | Merged | analysis_merged_into_b1.md | PENDING | agent |",
        ]),
        encoding="utf-8",
    )

    assert D.parse_breadth_manifest_outputs(sp) == ["analysis_core_state.md"]


def test_breadth_gate_never_reports_verify_artifact_as_missing(tmp_path: Path):
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    (sp / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Expected Output | Status | Type |",
            "|----------|-----------|----------|------------|-----------------|--------|------|",
            "| Core State | YES | B1 | Core State | analysis_core_state.md | PENDING | agent |",
            "| Verifier Template | YES |  |  | verify_.md | PENDING | template |",
        ]),
        encoding="utf-8",
    )
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")

    passed, missing = D.gate_passes(sp, str(project), phase)

    assert not passed
    assert any("analysis_core_state.md" in item for item in missing)
    assert not any("verify_.md" in item for item in missing)


def test_instantiate_schema_gate_rejects_template_only_manifest(tmp_path: Path):
    (tmp_path / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Expected Output | Status |",
            "|----------|-----------|----------|------------|-----------------|--------|",
            "| Verifier Template | YES |  |  | verify_.md | PENDING |",
        ]),
        encoding="utf-8",
    )

    issues = D._validate_spawn_manifest_schema(tmp_path)

    assert issues
    assert "schema invalid" in issues[0]
    assert "verify_.md" in issues[0]


def test_instantiate_schema_gate_accepts_agent_rows_with_outputs(tmp_path: Path):
    (tmp_path / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Row Type | Template | Required? | Agent ID | Focus Area | Expected Output | Status |",
            "|----------|----------|-----------|----------|------------|-----------------|--------|",
            "| AGENT | Core State | YES | B1 | Core State | analysis_core_state.md | QUEUED |",
        ]),
        encoding="utf-8",
    )

    assert D._validate_spawn_manifest_schema(tmp_path) == []


def test_breadth_manifest_scans_past_preliminary_template_tables(tmp_path: Path):
    (tmp_path / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "## Required Template Coverage",
            "| Template | Required? | Coverage |",
            "|----------|-----------|----------|",
            "| ORACLE_ANALYSIS | YES | covered by B1 |",
            "| FLASH_LOAN_INTERACTION | YES | covered by B2 |",
            "",
            "## Breadth Agents",
            "| Row Type | Template | Required? | Agent ID | Focus Area | Expected Output | Status |",
            "|----------|----------|-----------|----------|------------|-----------------|--------|",
            "| AGENT | ORACLE_ANALYSIS | YES | B1 | oracle | analysis_oracle.md | QUEUED |",
            "| AGENT | FLASH_LOAN_INTERACTION | YES | B2 | flash_loan | analysis_flash_loan.md | QUEUED |",
        ]),
        encoding="utf-8",
    )

    assert D.parse_breadth_manifest_count(tmp_path) == 2
    assert D.parse_breadth_manifest_outputs(tmp_path) == [
        "analysis_oracle.md",
        "analysis_flash_loan.md",
    ]
    assert D._validate_spawn_manifest_schema(tmp_path) == []


def test_breadth_early_completion_requires_all_manifest_outputs(tmp_path: Path):
    (tmp_path / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Row Type | Template | Required? | Agent ID | Focus Area | Expected Output | Status |",
            "|----------|----------|-----------|----------|------------|-----------------|--------|",
            "| AGENT | ORACLE_ANALYSIS | YES | B1 | oracle | analysis_oracle.md | QUEUED |",
            "| AGENT | TOKEN_FLOW_TRACING | YES | B2 | token_flow | analysis_token_flow.md | QUEUED |",
        ]),
        encoding="utf-8",
    )
    phase = D.Phase(
        "breadth", [], ["analysis_*.md"],
        base_timeout_s=60, min_artifact_bytes=100,
    )
    (tmp_path / "analysis_oracle.md").write_text("x" * 200, encoding="utf-8")

    assert D._breadth_manifest_complete_reason(tmp_path, phase) is None

    (tmp_path / "analysis_token_flow.md").write_text("y" * 200, encoding="utf-8")

    reason = D._breadth_manifest_complete_reason(tmp_path, phase)
    assert reason is not None
    assert "all 2 manifest breadth outputs" in reason


def test_depth_manifest_ignores_wrong_phase_output_rows(tmp_path: Path):
    sp = tmp_path
    (sp / "phase4b_manifest.md").write_text(
        "\n".join([
            "# Phase 4b Manifest",
            "",
            "| Agent | Role | Expected Artifact | Status | Type |",
            "|---|---|---|---|---|",
            "| depth-token-flow | Token flow | depth_token_flow_findings.md | REQUIRED | agent |",
            "| depth-state-trace | State trace | depth_state_trace_findings.md | REQUIRED | agent |",
            "| rag template | RAG sweep | rag_validation.md | REQUIRED | template |",
            "| verify template | Verify | verify_.md | REQUIRED | template |",
        ]),
        encoding="utf-8",
    )

    assert D.parse_depth_manifest_count(sp) == 2


def test_breadth_rescan_overreach_is_quarantined_not_blocking(tmp_path: Path):
    project = tmp_path / "project"
    sp = project / ".scratchpad"
    sp.mkdir(parents=True)
    (sp / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Expected Output | Status | Type |",
            "|----------|-----------|----------|------------|-----------------|--------|------|",
            "| Core State | YES | B1 | Core State | analysis_core_state.md | PENDING | agent |",
        ]),
        encoding="utf-8",
    )
    (sp / "analysis_core_state.md").write_text(
        "# Core State\n\n" + ("substantial breadth output " * 20),
        encoding="utf-8",
    )
    before = D._snapshot_file_state(sp, str(project))
    (sp / "analysis_rescan_1.md").write_text(
        "# Re-scan Overreach\n\n" + ("should be quarantined " * 20),
        encoding="utf-8",
    )
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")
    config = {
        "project_root": str(project),
        "pipeline": "sc",
        "mode": "thorough",
    }

    passed, missing = D._run_phase_validators(
        phase, config, sp, D.SC_PHASES, 0, before
    )

    assert passed
    assert missing == []
    assert not (sp / "analysis_rescan_1.md").exists()
    assert (sp / "_overflow" / "breadth" / "analysis_rescan_1.md").exists()


def test_watchdog_breadth_glob_excludes_rescan_family(tmp_path: Path):
    hook_path = Path(__file__).resolve().parents[1] / "hooks" / "phase_gate.py"
    spec = importlib.util.spec_from_file_location("phase_gate_under_test", hook_path)
    phase_gate = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(phase_gate)

    sp = tmp_path
    (sp / "analysis_rescan_1.md").write_text("x" * 300, encoding="utf-8")
    manifest = {
        "phases": {
            "breadth": {
                "order": 1,
                "required_artifacts": ["analysis_*.md"],
                "min_required_glob_matches": 1,
                "min_file_bytes": 100,
            },
            "rescan": {
                "order": 2,
                "required_artifacts": ["analysis_rescan_*.md"],
                "min_required_glob_matches": 1,
                "min_file_bytes": 100,
            },
        }
    }

    current, _ = phase_gate.detect_current_phase(str(sp), manifest, "thorough")
    assert current == "breadth"

    (sp / "analysis_core_state.md").write_text("x" * 300, encoding="utf-8")
    current, _ = phase_gate.detect_current_phase(str(sp), manifest, "thorough")
    assert current == "complete"
