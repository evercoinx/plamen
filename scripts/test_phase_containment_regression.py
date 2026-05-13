"""Regression tests for verifier overreach into future report phases.

Run: `python test_phase_containment_regression.py`
"""

from __future__ import annotations

import sys
import tempfile
import subprocess
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_types import plamen_home  # noqa: E402

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


def _config(project: Path, scratchpad: Path) -> dict:
    return {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "rust",
        "mode": "thorough",
        "pipeline": "l1",
        "proven_only": False,
    }


def _seed_queue(sp: Path, ids: list[tuple[str, str]]):
    lines = [
        "# Verification Queue",
        "",
        "| Finding ID | Severity | Title | Location | Preferred Tag |",
        "|------------|----------|-------|----------|---------------|",
    ]
    for fid, sev in ids:
        lines.append(f"| {fid} | {sev} | Title {fid} | crates/p2p/src/lib.rs:L1 | CODE-TRACE |")
    (sp / "verification_queue.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_verify(sp: Path, fid: str, sev: str):
    (sp / f"verify_{fid}.md").write_text(
        f"""# {fid}
**Title**: Title {fid}
**Verdict**: CONFIRMED
**Severity**: {sev}
**Location**: crates/p2p/src/lib.rs:L1
**Evidence Tag**: [CODE-TRACE]
**Description**: verified
**Recommendation**: fix it
""",
        encoding="utf-8",
    )


def _substantial(title: str) -> str:
    return (
        f"# {title}\n\n"
        "Deterministic regression fixture with enough body text to clear "
        "the driver's min_artifact_bytes gate. "
        + ("padding " * 30)
        + "\n"
    )


def test_PROMPT_l1_verify_shard_does_not_see_future_step5_subphases(tmp_path: Path):
    v1 = tmp_path / "plamen-l1.md"
    v1.write_text(
        "# L1\n\n"
        "## Step 5: Verification\n\n"
        "Verifier row template: write verify files only.\n\n"
        "### Step 5.3: Completeness Assert\n\n"
        "This future aggregate text must be withheld.\n\n"
        "### Step 5.4: Cross-batch Consistency\n\n"
        "Spawn crossbatch and write cross_batch_consistency.md.\n\n"
        "### Step 5.5: THOROUGH-ONLY: Skeptic-Judge\n\n"
        "Spawn skeptic and write skeptic_findings.md.\n\n"
        "### Step 5.6: Aggregate verify_core.md\n\n"
        "Write verify_core.md.\n\n"
        "## Step 6: Report\n\n"
        "Write AUDIT_REPORT.md.\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.L1_PHASES if p.name == "verify_low_c")
    prompt = D.build_phase_prompt(v1, phase, _config(project, scratchpad))
    check(
        "PROMPT.verify_keeps_row_template",
        "Verifier row template" in prompt,
        prompt[:500],
    )
    forbidden = [
        "### Step 5.3",
        "### Step 5.4",
        "### Step 5.5",
        "### Step 5.6",
        "verification_queue_medium_b.md",
        "verification_queue_low.md",
        "cross_batch_consistency.md",
        "skeptic_findings.md",
        "Write AUDIT_REPORT.md",
    ]
    hits = [token for token in forbidden if token in prompt]
    check(
        "PROMPT.verify_future_subphases_withheld",
        not hits and "Downstream Step 5 sub-phases intentionally withheld" in prompt,
        f"hits={hits}",
    )
    check(
        "PROMPT.l1_verify_no_generic_task_wrapper",
        "Use the Task tool for parallel subagent work" not in prompt
        and "Do not create child agents" in prompt,
        prompt[:1200],
    )


def test_PROMPT_sc_verify_shard_does_not_import_broad_language_phase5(tmp_path: Path):
    v1 = tmp_path / "plamen.md"
    v1.write_text("# SC\n\n## Phase 5: Verification\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.SC_PHASES if p.name == "sc_verify_crithigh")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }
    prompt = D.build_phase_prompt(v1, phase, config)
    forbidden = [
        "phase5-verification-prompt.md",
        "verify_batch_",
        "Phase 5.1",
        "Phase 5.2",
        "skeptic_",
        "judge_",
        "cross_batch",
        "report_index.md",
        "AUDIT_REPORT.md",
    ]
    hits = [token for token in forbidden if token in prompt]
    check(
        "PROMPT.sc_verify_shard_no_broad_phase5_import",
        not hits and "phase5-poc-execution.md" in prompt,
        f"hits={hits}",
    )
    assert not hits


def test_PROMPT_standalone_wrapper_and_runtime_placeholders_are_resolved(tmp_path: Path):
    v1 = tmp_path / "v1.md"
    v1.write_text("## Step 6c: Assembler\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.L1_PHASES if p.name == "report_assemble")

    prompt = D.build_phase_prompt(v1, phase, _config(project, scratchpad))

    check(
        "PROMPT.standalone_wrapper_label",
        "BEGIN STANDALONE V2 PHASE PROMPT" in prompt
        and "BEGIN V1 ORCHESTRATOR PROMPT" not in prompt,
        prompt[:1200],
    )
    unresolved_runtime = [
        token for token in ("{SCRATCHPAD}", "{PROJECT_ROOT}", "{PROJECT_PATH}", "{LANGUAGE}", "{MODE}", "{PIPELINE}")
        if token in prompt
    ]
    check(
        "PROMPT.runtime_placeholders_resolved",
        not unresolved_runtime,
        f"unresolved={unresolved_runtime}",
    )


def test_PROMPT_l1_verify_aggregate_uses_aggregate_prompt(tmp_path: Path):
    v1 = tmp_path / "v1.md"
    v1.write_text("## Step 5.6: Aggregate verify_core.md\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.L1_PHASES if p.name == "verify_aggregate")

    prompt = D.build_phase_prompt(v1, phase, _config(project, scratchpad))

    bad = [
        "Process the assigned rows directly",
        "verify_{FINDING_ID}.md",
        "shard manifest assigned",
    ]
    hits = [token for token in bad if token in prompt]
    check(
        "PROMPT.l1_verify_aggregate_is_aggregate_only",
        not hits and "write ONLY\n`verify_core.md`" in prompt,
        f"hits={hits}",
    )


def test_PROMPT_sc_depth_excludes_rag_sweep_subphase(tmp_path: Path):
    v1 = tmp_path / "plamen.md"
    v1.write_text("## Phase 4: Synthesis, Adaptive Depth, Chain Analysis\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.SC_PHASES if p.name == "depth")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }

    prompt = D.build_phase_prompt(v1, phase, config)

    forbidden = [
        "## Phase 4b.5: RAG Validation Sweep",
        "Spawn sonnet RAG sweep agent",
    ]
    hits = [token for token in forbidden if token in prompt]
    check(
        "PROMPT.sc_depth_rag_sweep_body_withheld",
        not hits
        and "Do NOT execute Phase 4b.5 / RAG Validation Sweep" in prompt
        and "Do NOT write `rag_validation.md`" in prompt,
        f"hits={hits}",
    )


def test_PROMPT_sc_depth_rejects_executable_future_artifact_mentions(tmp_path: Path):
    text = (
        "# Depth\n\n"
        "Write verification_queue.md after depth.\n"
        "Do NOT write `rag_validation.md`.\n"
    )
    issues = D._find_prompt_phase_boundary_violations(text, "depth")
    check(
        "PROMPT.depth_future_artifact_preflight_blocks_positive_write",
        any("verification_queue.md" in item for item in issues)
        and not any("rag_validation.md" in item for item in issues),
        repr(issues),
    )
    assert any("verification_queue.md" in item for item in issues)
    assert not any("rag_validation.md" in item for item in issues)


def test_PROMPT_sc_depth_rendered_prompt_has_no_executable_downstream_tokens(tmp_path: Path):
    v1 = tmp_path / "plamen.md"
    v1.write_text("## Phase 4: Synthesis, Adaptive Depth, Chain Analysis\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.SC_PHASES if p.name == "depth")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }

    prompt = D.build_phase_prompt(v1, phase, config)
    forbidden = [
        "## Phase 4b.5: RAG Validation Sweep",
        "RAG deep search during the depth loop",
        "goto Phase 4c",
        "phase4b-loop.md",
        "verify_H",
    ]
    hits = [token for token in forbidden if token in prompt]
    check(
        "PROMPT.depth_no_transitive_downstream_leakage",
        not hits
        and "## FORBIDDEN OUTPUT FILES (HARD PHASE BOUNDARY)" in prompt
        and "MUST NOT write `chain_summaries_compact.md`" in prompt
        and "MUST NOT write `verification_queue.md`" in prompt
        and not D._find_prompt_phase_boundary_violations(prompt, "depth"),
        f"hits={hits}",
    )
    assert not hits
    assert "MUST NOT write `chain_summaries_compact.md`" in prompt
    assert "MUST NOT write `verification_queue.md`" in prompt
    assert not D._find_prompt_phase_boundary_violations(prompt, "depth")


def test_PROMPT_sc_depth_enforces_language_template_and_graph_contract(tmp_path: Path):
    v1 = tmp_path / "plamen.md"
    v1.write_text("## Phase 4: Synthesis, Adaptive Depth, Chain Analysis\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.SC_PHASES if p.name == "depth")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }

    prompt = D.build_phase_prompt(v1, phase, config)

    required = [
        "~/.claude/prompts/evm/phase4b-depth-templates.md",
        "Language-Specific Depth Template Binding",
        "graph-artifact section verbatim",
        "caller_map.md",
        "callee_map.md",
        "state_write_map.md",
        "function_summary.md",
        "[GRAPH-ARTIFACT: UNAVAILABLE:<file>]",
        "depth_external_findings.md",
        "blind_spot_a_findings.md",
        "blind_spot_b_findings.md",
        "blind_spot_c_findings.md",
        "validation_sweep_findings.md",
        "confidence_scores.md",
        "phase4b-scoring.md",
        "initial Phase 4b confidence scoring",
        "distinct from the later `final_scoring` phase",
        "No scoreable findings found after depth",
        "adaptive_loop_log.md",
        "design_stress_findings.md",
        "perturbation_findings.md",
        "skill_execution_gaps.md",
        "first-attempt completion requirement",
    ]
    missing = [token for token in required if token not in prompt]
    check(
        "PROMPT.sc_depth_generated_prompt_matches_gate_contract",
        not missing,
        f"missing={missing}",
    )
    assert not missing


def test_PROMPT_phase4b_required_manifests_are_depth_owned_only(tmp_path: Path):
    root = plamen_home() / "prompts"
    languages = ["evm", "solana", "aptos", "sui", "soroban"]
    forbidden = [
        "rag_validation.md",
        "confidence_distribution.md",
        "phase4b_manifest.md",
        "symmetric_pairs.md",
        "blind_spot_scanner_a_findings.md",
        "blind_spot_scanner_b_findings.md",
        "blind_spot_scanner_c_findings.md",
    ]
    required = [
        "blind_spot_a_findings.md",
        "blind_spot_b_findings.md",
        "blind_spot_c_findings.md",
        "validation_sweep_findings.md",
        "confidence_scores.md",
        "adaptive_loop_log.md",
    ]
    failures = []
    for lang in languages:
        path = root / lang / "phase4b-required-artifacts.md"
        text = path.read_text(encoding="utf-8")
        bad = [token for token in forbidden if token in text]
        missing = [token for token in required if token not in text]
        if bad or missing:
            failures.append(f"{lang}: bad={bad} missing={missing}")
    check(
        "PROMPT.phase4b_required_manifest_has_no_later_phase_or_stale_files",
        not failures,
        "; ".join(failures),
    )
    assert not failures


def test_PROMPT_breadth_excludes_rescan_outputs(tmp_path: Path):
    v1 = tmp_path / "plamen.md"
    v1.write_text("## Phase 3: Parallel Analysis\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }

    prompt = D.build_phase_prompt(v1, phase, config)

    forbidden = [
        "Breadth Re-Scan",
        "Phase 3b",
        "analysis_rescan_",
        "analysis_percontract_",
    ]
    hits = [token for token in forbidden if token in prompt]
    check(
        "PROMPT.breadth_later_phase_text_stripped",
        not hits
        and "manifest-derived breadth files named" in prompt
        and "`analysis_<focus_area>.md`" in prompt
        and "OPEN_OUTPUTS is non-empty" in prompt
        and "spawn ONLY those missing or stub breadth" in prompt
        and "bounded batches of at most 6 parallel Task calls" in prompt,
        f"hits={hits}",
    )
    loop_ok = (
        "Manifest completion loop" in prompt
        and "OPEN_OUTPUTS" in prompt
        and "Completion is\nmanifest-exact, not batch-exact" in prompt
        and "Ignore rows whose type/status/role says" in prompt
        and "`skill`, `injectable`, `template`, `methodology`" in prompt
    )
    check("PROMPT.breadth_has_hard_completion_loop", loop_ok, prompt[:1600])
    assert not hits
    assert loop_ok


def test_GATE_breadth_is_manifest_exact_not_generic_quorum(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (scratchpad / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Status |",
            "|----------|-----------|----------|------------|--------|",
            "| arithmetic | YES | agent_1 | Arithmetic Safety | PENDING |",
            "| reentrancy | YES | agent_2 | Reentrancy | PENDING |",
            "| erc20 | YES | agent_3 | ERC20 Safety | PENDING |",
        ]),
        encoding="utf-8",
    )
    body = "# Finding\n\n" + ("substantial content " * 20)
    (scratchpad / "analysis_arithmetic_safety.md").write_text(body, encoding="utf-8")
    (scratchpad / "analysis_reentrancy.md").write_text(body, encoding="utf-8")
    (scratchpad / "analysis_oracle.md").write_text(body, encoding="utf-8")
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")

    passed, missing = D.gate_passes(scratchpad, str(project), phase)

    check(
        "GATE.breadth_manifest_exact_rejects_extra_file_quorum",
        not passed
        and any("manifest-exact incomplete" in item for item in missing)
        and any("analysis_erc20_safety.md" in item for item in missing),
        f"passed={passed} missing={missing}",
    )
    assert not passed
    assert any("analysis_erc20_safety.md" in item for item in missing)


def test_GATE_breadth_ignores_manifest_rows_merged_into_other_agents(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (scratchpad / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Status |",
            "|----------|-----------|----------|------------|--------|",
            "| arithmetic | YES | B1 | Arithmetic Safety | PENDING |",
            "| oracle | YES | B2 | Merged into B1 | MERGED INTO B1 |",
            "| reentrancy | YES | B6 | Reentrancy | PENDING |",
            "| erc20 | MERGED INTO B6 | B7 | ERC20 Safety | MERGED INTO B6 |",
            "| gas | YES | B8 | Gas DoS | PENDING |",
        ]),
        encoding="utf-8",
    )
    body = "# Finding\n\n" + ("substantial content " * 20)
    (scratchpad / "analysis_arithmetic_safety.md").write_text(body, encoding="utf-8")
    (scratchpad / "analysis_reentrancy.md").write_text(body, encoding="utf-8")
    (scratchpad / "analysis_gas_dos.md").write_text(body, encoding="utf-8")
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")

    expected = D.parse_breadth_manifest_outputs(scratchpad)
    count = D.parse_breadth_manifest_count(scratchpad)
    passed, missing = D.gate_passes(scratchpad, str(project), phase)

    check(
        "GATE.breadth_merged_manifest_rows_are_not_output_contracts",
        passed
        and expected == [
            "analysis_arithmetic_safety.md",
            "analysis_reentrancy.md",
            "analysis_gas_dos.md",
        ]
        and count == 3
        and not any("analysis_merged_into" in item for item in missing),
        f"passed={passed} missing={missing} expected={expected} count={count}",
    )
    assert passed
    assert expected == [
        "analysis_arithmetic_safety.md",
        "analysis_reentrancy.md",
        "analysis_gas_dos.md",
    ]
    assert count == 3


def test_GATE_breadth_ignores_skill_rows_in_spawn_manifest(tmp_path: Path):
    """Required skill/injectable rows are methodology, not standalone analysis files."""
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (scratchpad / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Status | Type |",
            "|----------|-----------|----------|------------|--------|------|",
            "| Core State | YES | B1 | Core State | PENDING | agent |",
            "| External Integrations | YES | B2 | External Integrations | PENDING | agent |",
            "| FLASH_LOAN_INTERACTION_EXTERNAL | YES | B1 | flash_loan_interaction_external | injected into B1 | skill |",
            "| ORACLE_ANALYSIS | YES | B1 | oracle_analysis | injected into B1 | skill |",
            "| TOKEN_FLOW_TRACING | YES | B1 | token_flow_tracing | injected into B1 | skill |",
            "| ZERO_STATE_RETURN | YES | B2 | zero_state_return | injected into B2 | injectable |",
            "| STAKING_RECEIPT_TOKENS | YES | B2 | staking_receipt_tokens | injected into B2 | skill |",
            "| SEMI_TRUSTED_ROLES | YES | B2 | semi_trusted_roles | injected into B2 | skill |",
            "| TEMPORAL_PARAMETER_STALENESS | YES | B2 | temporal_parameter_staleness | injected into B2 | skill |",
            "| SHARE_ALLOCATION_FAIRNESS | YES | B2 | share_allocation_fairness | injected into B2 | skill |",
            "| ECONOMIC_DESIGN_AUDIT | YES | B2 | economic_design_audit | injected into B2 | skill |",
            "| EXTERNAL_PRECONDITION_AUDIT | YES | B2 | external_precondition_audit | injected into B2 | skill |",
            "| FORK_ANCESTRY | YES | B2 | fork_ancestry | injected into B2 | skill |",
            "| VERIFICATION_PROTOCOL | YES | B2 | verification_protocol | injected into B2 | skill |",
        ]),
        encoding="utf-8",
    )
    body = "# Finding\n\n" + ("substantial content " * 20)
    (scratchpad / "analysis_core_state.md").write_text(body, encoding="utf-8")
    (scratchpad / "analysis_external_integrations.md").write_text(body, encoding="utf-8")
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")

    expected = D.parse_breadth_manifest_outputs(scratchpad)
    count = D.parse_breadth_manifest_count(scratchpad)
    passed, missing = D.gate_passes(scratchpad, str(project), phase)

    forbidden = [
        "analysis_flash_loan_interaction_external.md",
        "analysis_oracle_analysis.md",
        "analysis_token_flow_tracing.md",
        "analysis_zero_state_return.md",
        "analysis_staking_receipt_tokens.md",
        "analysis_semi_trusted_roles.md",
        "analysis_temporal_parameter_staleness.md",
        "analysis_share_allocation_fairness.md",
        "analysis_economic_design_audit.md",
        "analysis_external_precondition_audit.md",
        "analysis_fork_ancestry.md",
        "analysis_verification_protocol.md",
    ]

    check(
        "GATE.breadth_skill_rows_are_not_output_contracts",
        passed
        and expected == ["analysis_core_state.md", "analysis_external_integrations.md"]
        and count == 2
        and not any(name in " ".join(missing) for name in forbidden),
        f"passed={passed} missing={missing} expected={expected} count={count}",
    )
    assert passed, missing
    assert expected == ["analysis_core_state.md", "analysis_external_integrations.md"]
    assert count == 2


def test_GATE_breadth_collapses_duplicate_agent_id_template_rows(tmp_path: Path):
    """Old manifests may bind many required templates to one spawned agent."""
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (scratchpad / "spawn_manifest.md").write_text(
        "\n".join([
            "# Spawn Manifest",
            "",
            "| Template | Required? | Agent ID | Focus Area | Status |",
            "|----------|-----------|----------|------------|--------|",
            "| Core State | YES | B1 | Core State | PENDING |",
            "| FLASH_LOAN_INTERACTION_EXTERNAL | YES | B1 | flash_loan_interaction_external | PENDING |",
            "| ORACLE_ANALYSIS | YES | B1 | oracle_analysis | PENDING |",
            "| External Integrations | YES | B2 | External Integrations | PENDING |",
            "| TOKEN_FLOW_TRACING | YES | B2 | token_flow_tracing | PENDING |",
        ]),
        encoding="utf-8",
    )
    body = "# Finding\n\n" + ("substantial content " * 20)
    (scratchpad / "analysis_core_state.md").write_text(body, encoding="utf-8")
    (scratchpad / "analysis_external_integrations.md").write_text(body, encoding="utf-8")
    phase = next(p for p in D.SC_PHASES if p.name == "breadth")

    expected = D.parse_breadth_manifest_outputs(scratchpad)
    count = D.parse_breadth_manifest_count(scratchpad)
    passed, missing = D.gate_passes(scratchpad, str(project), phase)

    check(
        "GATE.breadth_duplicate_agent_id_rows_are_one_output_contract",
        passed
        and expected == ["analysis_core_state.md", "analysis_external_integrations.md"]
        and count == 2
        and not any("analysis_oracle_analysis" in item for item in missing),
        f"passed={passed} missing={missing} expected={expected} count={count}",
    )
    assert passed, missing
    assert expected == ["analysis_core_state.md", "analysis_external_integrations.md"]
    assert count == 2


def test_GATE_depth_manifest_count_ignores_merged_rows(tmp_path: Path):
    scratchpad = tmp_path / ".scratchpad"
    scratchpad.mkdir()
    (scratchpad / "phase4b_manifest.md").write_text(
        "\n".join([
            "# Phase 4b Manifest",
            "",
            "| Agent | Role | Expected Artifact | Status |",
            "|---|---|---|---|",
            "| depth-token-flow | Token flow | depth_token_flow_findings.md | REQUIRED |",
            "| depth-oracle | Merged into depth-token-flow | depth_oracle_findings.md | MERGED INTO D1 |",
            "| depth-state-trace | State trace | depth_state_trace_findings.md | REQUIRED |",
            "| scanner-extra | Covered by D2 | scanner_extra_findings.md | COVERED BY D2 |",
            "| depth-edge-case | Edge case | depth_edge_case_findings.md | REQUIRED |",
        ]),
        encoding="utf-8",
    )

    count = D.parse_depth_manifest_count(scratchpad)

    check(
        "GATE.depth_merged_manifest_rows_are_not_quorum_contracts",
        count == 3,
        f"count={count}",
    )
    assert count == 3


def test_GATE_depth_manifest_count_ignores_skill_rows(tmp_path: Path):
    scratchpad = tmp_path / ".scratchpad"
    scratchpad.mkdir()
    (scratchpad / "phase4b_manifest.md").write_text(
        "\n".join([
            "# Phase 4b Manifest",
            "",
            "| Agent | Role | Expected Artifact | Status | Type |",
            "|---|---|---|---|---|",
            "| depth-token-flow | Token flow | depth_token_flow_findings.md | REQUIRED | agent |",
            "| depth-state-trace | State trace | depth_state_trace_findings.md | REQUIRED | agent |",
            "| depth-edge-case | Edge case | depth_edge_case_findings.md | REQUIRED | agent |",
            "| depth-external | External | depth_external_findings.md | REQUIRED | agent |",
            "| ORACLE_ANALYSIS | oracle_analysis | oracle_analysis.md | injected into depth-token-flow | skill |",
            "| TOKEN_FLOW_TRACING | token_flow_tracing | token_flow_tracing.md | attached to depth-token-flow | injectable |",
            "| confidence-scoring | Post-depth support | confidence_scores.md | REQUIRED | checklist |",
        ]),
        encoding="utf-8",
    )

    count = D.parse_depth_manifest_count(scratchpad)

    check(
        "GATE.depth_skill_rows_are_not_quorum_contracts",
        count == 4,
        f"count={count}",
    )
    assert count == 4


def test_PROMPT_rescan_runs_before_inventory_and_owns_only_additional_analysis(tmp_path: Path):
    v1 = tmp_path / "plamen.md"
    v1.write_text("## Phase 3b: Breadth Re-Scan\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.SC_PHASES if p.name == "rescan")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }

    prompt = D.build_phase_prompt(v1, phase, config)

    forbidden = [
        "findings_inventory.md",
        "inventory merge",
        "verification_queue.md",
        "AUDIT_REPORT.md",
        "depth_*",
    ]
    hits = [token for token in forbidden if token.lower() in prompt.lower()]
    check(
        "PROMPT.rescan_pre_inventory_contract",
        not hits
        and "This phase runs after first-pass breadth and before inventory" in prompt
        and "analysis_rescan_*.md" in prompt,
        f"hits={hits}",
    )


def test_PROMPT_inventory_chunk_no_final_output_contract(tmp_path: Path):
    v1 = tmp_path / "v1.md"
    v1.write_text("## Step 4a: Finding Inventory\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (scratchpad / "inventory_chunk_a.manifest.md").write_text(
        "- analysis_a.md\n", encoding="utf-8"
    )
    phase = next(p for p in D.L1_PHASES if p.name == "inventory_chunk_a")

    prompt = D.build_phase_prompt(v1, phase, _config(project, scratchpad))

    forbidden = [
        "Append to `findings_inventory.md`",
        "writes to `{SCRATCHPAD}/findings_inventory.md`",
        f"writes to `{scratchpad}/findings_inventory.md`",
    ]
    hits = [token for token in forbidden if token in prompt]
    check(
        "PROMPT.inventory_chunk_owns_chunk_artifact",
        not hits and "Write ONLY `findings_inventory_chunk_a.md`" in prompt,
        f"hits={hits}",
    )


def test_PROMPT_l1_inventory_contract_matches_phase_order(tmp_path: Path):
    v1 = tmp_path / "plamen-l1.md"
    v1.write_text("## Step 4a: Finding Inventory\n\nlegacy\n", encoding="utf-8")
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (scratchpad / "inventory_chunk_a.manifest.md").write_text(
        "- analysis_consensus.md\n", encoding="utf-8"
    )
    phase = next(p for p in D.L1_PHASES if p.name == "inventory_chunk_a")
    prompt = D.build_phase_prompt(v1, phase, _config(project, scratchpad))
    ok = (
        "L1 has no breadth phase" not in prompt
        and "`analysis_*.md` files from the L1 breadth phase" in prompt
        and "Depth and\n  niche outputs are promoted into inventory later" in prompt
    )
    check("PROMPT.l1_inventory_reflects_current_phase_order", ok, prompt[:1800])
    assert ok


def test_CONTRACT_graph_sweep_optional_outputs_are_owned(tmp_path: Path):
    sp = tmp_path / "scratch"
    sp.mkdir()
    owned = D._owned_artifact_patterns("l1", sp)["graph_sweeps"]
    required = {
        "field_validation_matrix.md",
        "primitive_correctness_findings.md",
        "network_amplification_findings.md",
        "lifecycle_replay_findings.md",
    }
    ok = required.issubset(set(owned))
    check("CONTRACT.graph_sweep_conditional_outputs_owned", ok, f"owned={owned}")
    assert ok


def test_CONTRACT_expected_artifacts_are_owned_by_phase(tmp_path: Path):
    import fnmatch

    failures = []
    for pipeline, phases in (("sc", D.SC_PHASES), ("l1", D.L1_PHASES)):
        sp = tmp_path / pipeline
        sp.mkdir()
        (sp / "verification_queue.md").write_text(
            "| Finding ID | Severity | Title | Location | Preferred Tag |\n"
            "|---|---|---|---|---|\n"
            "| INV-001 | High | t | src/x:L1 | CODE-TRACE |\n",
            encoding="utf-8",
        )
        owned = D._owned_artifact_patterns(pipeline, sp)
        for phase in phases:
            if phase.name in D.L1_VERIFY_PHASE_NAMES or phase.name in D.SC_VERIFY_PHASE_NAMES:
                continue
            phase_owned = owned.get(phase.name, [])
            patterns = list(phase.expected_artifacts or [])
            for group in getattr(phase, "any_of", []) or []:
                patterns.extend(group)
            for pattern in patterns:
                expected = "../AUDIT_REPORT.md" if pattern == "AUDIT_REPORT.md" else pattern
                if not any(
                    fnmatch.fnmatch(expected, own) or fnmatch.fnmatch(own, expected)
                    for own in phase_owned
                ):
                    failures.append((pipeline, phase.name, pattern, phase_owned))
    check("CONTRACT.expected_artifacts_have_phase_owner", not failures, repr(failures[:5]))
    assert not failures


def test_CONTRACT_dynamic_report_shards_are_owned(tmp_path: Path):
    import fnmatch

    failures = []
    for pipeline, phases in (("sc", D.SC_PHASES), ("l1", D.L1_PHASES)):
        sp = tmp_path / f"{pipeline}_report_shards"
        manifest_dir = sp / "body_manifests"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "report_medium_a.json").write_text(
            '{"findings":[]}', encoding="utf-8"
        )
        (manifest_dir / "report_medium_b.json").write_text(
            '{"findings":[]}', encoding="utf-8"
        )
        expanded = D.expand_shard_phases(phases, sp)
        owned = D._owned_artifact_patterns(pipeline, sp)
        by_name = {phase.name: phase for phase in expanded}
        for name in (
            "report_body_writer_medium_a",
            "report_body_writer_medium_b",
            "report_medium_a",
            "report_medium_b",
        ):
            phase = by_name.get(name)
            if not phase:
                failures.append((pipeline, name, "missing expanded phase"))
                continue
            phase_owned = owned.get(name, [])
            for pattern in phase.expected_artifacts:
                if not any(
                    fnmatch.fnmatch(pattern, own) or fnmatch.fnmatch(own, pattern)
                    for own in phase_owned
                ):
                    failures.append((pipeline, name, pattern, phase_owned))
    check("CONTRACT.dynamic_report_shards_are_owned", not failures, repr(failures[:5]))
    assert not failures


def test_CONTRACT_semantic_dedup_noop_writes_expected_outputs(tmp_path: Path):
    cases = [
        (
            "l1",
            D.L1_PHASES,
            "semantic_dedup",
            "verification_queue.md",
            "verification_queue_deduped.md",
        ),
        (
            "sc",
            D.SC_PHASES,
            "sc_semantic_dedup",
            "findings_inventory.md",
            "findings_inventory_deduped.md",
        ),
    ]
    failures = []
    for pipeline, phases, phase_name, source_name, target_name in cases:
        project = tmp_path / pipeline
        scratchpad = project / ".scratchpad"
        scratchpad.mkdir(parents=True)
        source_body = _substantial(source_name)
        (scratchpad / source_name).write_text(source_body, encoding="utf-8")

        written = D._write_semantic_dedup_skip_outputs(scratchpad, phase_name)
        phase = next(p for p in phases if p.name == phase_name)
        passed, missing = D.gate_passes(scratchpad, str(project), phase)

        if not passed:
            failures.append((pipeline, "gate", missing))
        if "dedup_decisions.md" not in written or target_name not in written:
            failures.append((pipeline, "written", written))
        if source_body != (scratchpad / target_name).read_text(encoding="utf-8"):
            failures.append((pipeline, "source_not_preserved", target_name))
    check("CONTRACT.semantic_dedup_noop_writes_expected_outputs", not failures, repr(failures))
    assert not failures


def test_CONTRACT_empty_verify_queue_skip_writes_canonical_queue(tmp_path: Path):
    cases = [
        ("l1", D.L1_PHASES, "verify_queue"),
        ("sc", D.SC_PHASES, "sc_verify_queue"),
    ]
    failures = []
    for pipeline, phases, phase_name in cases:
        project = tmp_path / f"{pipeline}_verify_queue"
        scratchpad = project / ".scratchpad"
        scratchpad.mkdir(parents=True)
        (scratchpad / "findings_inventory.md").write_text(
            "# Findings Inventory\n\nNo findings.\n",
            encoding="utf-8",
        )
        empty, reason = D.is_verification_queue_empty(scratchpad, pipeline)
        if not empty:
            failures.append((pipeline, "not_empty", reason))
            continue
        written = D._write_empty_verification_queue(scratchpad, reason)
        phase = next(p for p in phases if p.name == phase_name)
        passed, missing = D.gate_passes(scratchpad, str(project), phase)
        if not passed:
            failures.append((pipeline, "gate", missing))
        queue_text = (scratchpad / "verification_queue.md").read_text(encoding="utf-8")
        if "Total: 0 findings" not in queue_text:
            failures.append((pipeline, "missing_total_zero", queue_text[:120]))
        if "verification_queue_evidence_excluded.md" not in written:
            failures.append((pipeline, "missing_excluded_sidecar", written))
    check("CONTRACT.empty_verify_queue_skip_writes_canonical_queue", not failures, repr(failures))
    assert not failures


def test_POLICY_validator_rejects_foreign_phase_writes_even_when_own_gate_passes(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    phase = next(p for p in D.L1_PHASES if p.name == "inventory_prepare")
    before = D._snapshot_file_state(scratchpad, str(project))
    (scratchpad / "inventory_shard_plan.md").write_text(
        _substantial("Inventory Shard Plan"), encoding="utf-8"
    )
    (scratchpad / "semantic_invariants.md").write_text(
        _substantial("Rogue Future Invariants"), encoding="utf-8"
    )
    passed, missing = D._run_phase_validators(
        phase,
        _config(project, scratchpad),
        scratchpad,
        D.L1_PHASES,
        0,
        before,
    )
    ok = (
        not passed
        and any("phase containment:" in str(item) for item in missing)
        and not (scratchpad / "semantic_invariants.md").exists()
        and (scratchpad / "_overflow" / "inventory_prepare" / "semantic_invariants.md").exists()
    )
    detail = f"passed={passed} missing={missing}"
    if not ok:
        print(
            "  FAIL  POLICY.containment_failure_blocks_phase_completion"
            f" :: {detail}"
        )
        raise AssertionError(
            "Expected implementation change: _run_phase_validators must set "
            "passed=False and include a phase-containment missing item whenever "
            "a phase writes later-phase artifacts, even if gate_passes() was true."
        )
    check("POLICY.containment_failure_blocks_phase_completion", True, detail)


def test_BOUNDARY_fake_claude_foreign_write_is_hard_failure_signal(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    project.mkdir()
    scratchpad.mkdir()

    fake_py = tmp_path / "fake_claude.py"
    fake_py.write_text(
        """
from pathlib import Path
import json
import os
import re
import sys

prompt = sys.stdin.read()
match = re.search(r"running the \\*\\*(.*?)\\*\\* phase", prompt)
phase = match.group(1) if match else "unknown"
scratch = Path(os.environ["PLAMEN_SCRATCHPAD"])
body = "# fake claude artifact\\n\\n" + ("padding " * 40) + "\\n"
if phase == "inventory_prepare":
    (scratch / "inventory_shard_plan.md").write_text(body, encoding="utf-8")
    (scratch / "semantic_invariants.md").write_text(body, encoding="utf-8")
print(json.dumps({"result": "x" * 700, "usage": {"input_tokens": 1, "output_tokens": 1}}))
""".lstrip(),
        encoding="utf-8",
    )
    fake_cmd = tmp_path / "fake_claude.cmd"
    fake_cmd.write_text(
        f'@echo off\r\n"{sys.executable}" "{fake_py}" %*\r\n',
        encoding="utf-8",
    )

    phase = next(p for p in D.L1_PHASES if p.name == "inventory_prepare")
    config = _config(project, scratchpad)
    before = D._snapshot_file_state(scratchpad, str(project))
    old_bin = D.CLAUDE_BIN
    D.CLAUDE_BIN = str(fake_cmd)
    try:
        rc = D.run_phase(phase, config, attempt=1)
    finally:
        D.CLAUDE_BIN = old_bin

    passed, missing = D._run_phase_validators(
        phase, config, scratchpad, D.L1_PHASES, rc, before
    )
    ok = (
        rc == 0
        and not passed
        and any("phase containment:" in str(item) for item in missing)
        and (scratchpad / "_overflow" / "inventory_prepare" / "semantic_invariants.md").exists()
    )
    detail = f"rc={rc} passed={passed} missing={missing}"
    if not ok:
        print(
            "  FAIL  BOUNDARY.fake_claude_containment_hard_failure"
            f" :: {detail}"
        )
        raise AssertionError(
            "Expected implementation change: the real subprocess boundary must "
            "surface foreign later-phase writes as a failed phase validation, "
            "not as a clean pass with quarantine-only side effects."
        )
    check("BOUNDARY.fake_claude_containment_hard_failure", True, detail)


def test_CONTAINMENT_rogue_report_artifacts_detected_and_quarantined(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    before = D._snapshot_file_state(scratchpad, str(project))
    (project / "AUDIT_REPORT.md").write_text("# rogue report\n", encoding="utf-8")
    (scratchpad / "report_index.md").write_text("# rogue index\n", encoding="utf-8")
    offenders = D._detect_foreign_phase_writes(
        scratchpad,
        str(project),
        D.L1_PHASES,
        "verify_low_c",
        "l1",
        before,
    )
    check(
        "CONTAINMENT.detects_project_root_report",
        "../AUDIT_REPORT.md" in offenders,
        repr(offenders),
    )
    check(
        "CONTAINMENT.detects_scratchpad_report_index",
        "report_index.md" in offenders,
        repr(offenders),
    )
    moved = D._quarantine_foreign_phase_writes(
        scratchpad,
        str(project),
        "verify_low_c",
        offenders,
    )
    check(
        "CONTAINMENT.quarantines_project_root_report",
        "../AUDIT_REPORT.md" in moved
        and not (project / "AUDIT_REPORT.md").exists()
        and (scratchpad / "_overflow" / "verify_low_c" / "AUDIT_REPORT.md").exists(),
        f"moved={moved}",
    )
    check(
        "CONTAINMENT.quarantines_scratchpad_report_index",
        "report_index.md" in moved
        and not (scratchpad / "report_index.md").exists()
        and (scratchpad / "_overflow" / "verify_low_c" / "report_index.md").exists(),
        f"moved={moved}",
    )


def test_CONTAINMENT_depth_static_denylist_catches_unmanifested_downstream_files(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    before = D._snapshot_file_state(scratchpad, str(project))
    offenders_expected = {
        "chain_summaries_compact.md",
        "rag_validation.md",
        "hypotheses.md",
        "finding_mapping.md",
        "enabler_results.md",
        "chain_hypotheses.md",
        "composition_coverage.md",
        "synthesis_full.md",
        "verification_queue.md",
        "verify_H1.md",
        "verify_CH1.md",
        "report_index.md",
    }
    for name in offenders_expected:
        (scratchpad / name).write_text(_substantial(name), encoding="utf-8")
    offenders = set(D._detect_foreign_phase_writes(
        scratchpad, str(project), D.SC_PHASES, "depth", "sc", before
    ))
    check(
        "CONTAINMENT.depth_detects_reported_boundary_crossing_files",
        offenders_expected <= offenders,
        f"missing={sorted(offenders_expected - offenders)} offenders={sorted(offenders)}",
    )
    assert offenders_expected <= offenders


def test_CONTAINMENT_live_abort_on_depth_downstream_artifact(tmp_path: Path):
    scratchpad = tmp_path / ".scratchpad"
    scratchpad.mkdir()
    script = (
        "import pathlib, sys, time\n"
        "sp = pathlib.Path(sys.argv[1])\n"
        "time.sleep(0.3)\n"
        "(sp / 'verification_queue.md').write_text('x' * 300, encoding='utf-8')\n"
        "time.sleep(20)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script, str(scratchpad)])
    start = time.time()
    rc = D._wait_with_heartbeat(
        proc,
        timeout=10,
        scratchpad=scratchpad,
        phase_name="depth",
        start_time=start,
        protected_patterns=D._protected_phase_write_patterns("depth"),
    )
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
        proc.wait(timeout=5)
    check(
        "CONTAINMENT.depth_live_abort_on_verification_queue",
        rc == -4 and proc.poll() is not None,
        f"rc={rc} poll={proc.poll()}",
    )
    assert rc == -4


def test_GRAPH_l1_low_verify_shards_are_critical():
    low = [
        p for p in D.L1_PHASES
        if p.name in {"verify_low_a", "verify_low_b", "verify_low_c", "verify_low_d"}
    ]
    noncritical = [p.name for p in low if not p.critical]
    check(
        "GRAPH.low_verify_shards_critical",
        len(low) == 4 and not noncritical,
        f"count={len(low)} noncritical={noncritical}",
    )


def test_STARTUP_rogue_report_without_assemble_is_quarantined(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    (project / "AUDIT_REPORT.md").write_text("# rogue report\n", encoding="utf-8")
    cp = D.Checkpoint(completed=["skeptic"], degraded=["skeptic"])
    dst = D._quarantine_report_without_completed_assemble(
        scratchpad, str(project), cp
    )
    check(
        "STARTUP.rogue_report_quarantined_without_assemble",
        dst is not None
        and not (project / "AUDIT_REPORT.md").exists()
        and dst.exists()
        and "startup_report_guard" in str(dst),
        repr(dst),
    )


def test_STARTUP_report_kept_when_assemble_completed(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    report = project / "AUDIT_REPORT.md"
    report.write_text("# legitimate report\n", encoding="utf-8")
    cp = D.Checkpoint(completed=["report_assemble"], degraded=[])
    dst = D._quarantine_report_without_completed_assemble(
        scratchpad, str(project), cp
    )
    check(
        "STARTUP.report_kept_when_assemble_completed",
        dst is None and report.exists(),
        repr(dst),
    )


def test_VERIFY_CORE_aggregate_uses_tolerant_field_parser(tmp_path: Path):
    sp = tmp_path / "scratch"
    sp.mkdir()
    (sp / "verify_INV-001.md").write_text(
        "# verify\n\n"
        "**Verdict:** CONFIRMED\n"
        "**Evidence Tag:** [CODE-TRACE]\n"
        "**Location:** `crates/p2p/src/foo.rs:L42`\n",
        encoding="utf-8",
    )
    (sp / "verify_INV-002.md").write_text(
        "# verify\n\n"
        "- **Status**: FALSE_POSITIVE\n"
        "- **Evidence Tags**: [POC-PASS]\n"
        "- **Location**: crates/p2p/src/bar.rs:L9\n",
        encoding="utf-8",
    )
    written = D._generate_verify_core_if_missing(sp)
    text = (sp / "verify_core.md").read_text(encoding="utf-8")
    check("VERIFY_CORE.aggregate_written", written, text)
    check(
        "VERIFY_CORE.bold_colon_verdict_parsed",
        "| INV-001 | CONFIRMED | [CODE-TRACE] | crates/p2p/src/foo.rs:L42 |" in text,
        text,
    )
    check(
        "VERIFY_CORE.status_alias_parsed",
        "| INV-002 | FALSE_POSITIVE | [POC-PASS] | crates/p2p/src/bar.rs:L9 |" in text,
        text,
    )


def test_SKEPTIC_manifest_enumerates_all_ch_ids_and_prompt_references_it(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    ids = [
        ("INV-095", "High"),
        ("INV-103", "High"),
        ("INV-104", "Critical"),
        ("INV-105", "High"),
        ("INV-200", "Medium"),
    ]
    _seed_queue(scratchpad, ids)
    for fid, sev in ids:
        _write_verify(scratchpad, fid, sev)
    rows = D._write_skeptic_manifest(scratchpad)
    got = {r["finding_id"] for r in rows}
    expected = {"INV-095", "INV-103", "INV-104", "INV-105"}
    check("SKEPTIC.manifest_exact_ch_ids", got == expected, f"got={got}")
    v1 = tmp_path / "plamen-l1.md"
    v1.write_text(
        "# L1\n\n## Step 5.5: Skeptic-Judge\n\n"
        "Spawn skeptic vaguely.\n",
        encoding="utf-8",
    )
    phase = next(p for p in D.L1_PHASES if p.name == "skeptic")
    prompt = D.build_phase_prompt(v1, phase, _config(project, scratchpad))
    check(
        "SKEPTIC.prompt_mentions_manifest_contract",
        "skeptic_manifest.json" in prompt
        and "Cover EVERY `finding_id`" in prompt
        and "Do NOT spawn subagents" in prompt,
        prompt,
    )


def test_PROMPT_skeptic_uses_aggregate_contract_not_shard_artifacts(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    v1_sc = tmp_path / "plamen.md"
    v1_sc.write_text("# SC\n\n## Phase 5.1: Skeptic-Judge\n\nlegacy\n", encoding="utf-8")
    v1_l1 = tmp_path / "plamen-l1.md"
    v1_l1.write_text("# L1\n\n## Step 5.5: Skeptic-Judge\n\nlegacy\n", encoding="utf-8")

    sc_phase = next(p for p in D.SC_PHASES if p.name == "skeptic")
    l1_phase = next(p for p in D.L1_PHASES if p.name == "skeptic")
    sc_config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }
    l1_config = _config(project, scratchpad)

    prompts = [
        D.build_phase_prompt(v1_sc, sc_phase, sc_config),
        D.build_phase_prompt(v1_l1, l1_phase, l1_config),
    ]
    failures = []
    for prompt in prompts:
        if "skeptic_findings.md" not in prompt:
            failures.append("missing skeptic_findings.md")
        if "skeptic_judge_decisions.md" not in prompt:
            failures.append("missing skeptic_judge_decisions.md")
        if "skeptic_manifest.json" not in prompt:
            failures.append("missing skeptic_manifest.json")
        if "Cover EVERY `finding_id`" not in prompt:
            failures.append("missing manifest coverage mandate")
        for forbidden in [
            "writes to `{SCRATCHPAD}/skeptic_{finding_id}.md`",
            "Judge writes to `{SCRATCHPAD}/judge_{finding_id}.md`",
            "spawn a skeptic agent",
            "Spawn a haiku judge agent",
        ]:
            if forbidden in prompt:
                failures.append(f"forbidden {forbidden}")
    check(
        "SKEPTIC.prompt_uses_aggregate_outputs",
        not failures,
        "; ".join(failures),
    )


def test_PROMPT_skeptic_static_gate_requires_both_aggregate_outputs():
    sc_phase = next(p for p in D.SC_PHASES if p.name == "skeptic")
    l1_phase = next(p for p in D.L1_PHASES if p.name == "skeptic")

    check(
        "SKEPTIC.sc_static_gate_exact_outputs",
        sc_phase.expected_artifacts == ["skeptic_findings.md", "skeptic_judge_decisions.md"],
        repr(sc_phase.expected_artifacts),
    )
    check(
        "SKEPTIC.l1_static_gate_exact_outputs",
        l1_phase.expected_artifacts == ["skeptic_findings.md", "skeptic_judge_decisions.md"],
        repr(l1_phase.expected_artifacts),
    )
    assert sc_phase.expected_artifacts == ["skeptic_findings.md", "skeptic_judge_decisions.md"]
    assert l1_phase.expected_artifacts == ["skeptic_findings.md", "skeptic_judge_decisions.md"]


def test_PROMPT_crossbatch_and_verify_aggregate_allow_readonly_upstream_inputs(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    v1 = tmp_path / "plamen.md"
    v1.write_text("# SC\n\n## Phase 5: Verification\n\nlegacy\n", encoding="utf-8")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }

    cross = D.build_phase_prompt(
        v1, next(p for p in D.SC_PHASES if p.name == "crossbatch"), config
    )
    aggregate = D.build_phase_prompt(
        v1, next(p for p in D.SC_PHASES if p.name == "sc_verify_aggregate"), config
    )
    ok = (
        "You MAY read\nupstream verification artifacts" in cross
        and "MUST NOT modify\nthose inputs" in cross
        and "Do NOT read or write other agents' output files" not in cross
        and "You MAY read upstream\n`verify_*.md` files" in aggregate
        and "MUST\nNOT modify those inputs" in aggregate
    )
    check("PROMPT.aggregate_phases_have_readonly_scope", ok, cross + "\n---\n" + aggregate)
    assert ok


def test_PROMPT_later_aggregation_templates_allow_readonly_upstream_inputs(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    v1 = tmp_path / "plamen.md"
    v1.write_text(
        "# SC\n\n## Phase 4: Synthesis, Adaptive Depth, Chain Analysis\n\nlegacy\n",
        encoding="utf-8",
    )
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }
    chain1 = D.build_phase_prompt(
        v1, next(p for p in D.SC_PHASES if p.name == "chain"), config
    )
    chain2 = D.build_phase_prompt(
        v1, next(p for p in D.SC_PHASES if p.name == "chain_agent2"), config
    )
    prompt_dir = SCRIPTS_DIR.parent / "prompts" / "shared" / "v2"
    aggregate_templates = [
        "phase4a-inventory-merge.md",
        "phase4a5-invariants.md",
        "phase4b-scoring.md",
        "phase4b-final-scoring.md",
        "phase4b-rescore.md",
        "phase4b-skill-checklist.md",
        "phase4b-perturbation.md",
        "phase4b-variable-map.md",
        "phase4b5-rag-sweep.md",
        "phase4c-chain-agent1.md",
        "phase4c-chain-agent2.md",
        "phase4c-chain-iter2.md",
    ]
    failures = []
    for name in aggregate_templates:
        text = (prompt_dir / name).read_text(encoding="utf-8")
        if "Do NOT read or write other agents' output files" in text:
            failures.append(f"{name}: generic no-read scope")
        if "MAY read" not in text or "MUST NOT modify" not in text:
            failures.append(f"{name}: missing read-only upstream scope")
    for label, prompt in [("chain", chain1), ("chain_agent2", chain2)]:
        if "Do NOT read or write other agents' output files" in prompt:
            failures.append(f"{label}: generated prompt has generic no-read scope")
        if "MAY read" not in prompt or "MUST NOT modify" not in prompt:
            failures.append(f"{label}: generated prompt missing read-only scope")
    check(
        "PROMPT.later_aggregators_have_readonly_scope",
        not failures,
        "; ".join(failures),
    )
    assert not failures


def test_PROMPT_report_index_matches_current_sc_l1_contracts(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    v1_sc = tmp_path / "plamen.md"
    v1_sc.write_text("# SC\n\n## Phase 6: Report\n\nlegacy\n", encoding="utf-8")
    v1_l1 = tmp_path / "plamen-l1.md"
    v1_l1.write_text("# L1\n\n## Step 6: Report\n\nlegacy\n", encoding="utf-8")
    common = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "mode": "thorough",
        "proven_only": False,
    }
    sc_prompt = D.build_phase_prompt(
        v1_sc,
        next(p for p in D.SC_PHASES if p.name == "report_index"),
        {**common, "language": "evm", "pipeline": "sc"},
    )
    l1_prompt = D.build_phase_prompt(
        v1_l1,
        next(p for p in D.L1_PHASES if p.name == "report_index"),
        {**common, "language": "rust", "pipeline": "l1"},
    )
    failures = []
    for label, prompt in [("sc", sc_prompt), ("l1", l1_prompt)]:
        if "SC pipelines (v2.1.2+) do NOT produce" in prompt:
            failures.append(f"{label}: stale SC verify_core absence statement")
        for required in [
            "Both current SC and L1 pipelines normally produce",
            "subsystem_map.md",
            "primitive_status.md",
            "design_context.md or threat_model.md",
            "build_status.md or primitive_status.md",
        ]:
            if required not in prompt:
                failures.append(f"{label}: missing {required}")
    check("PROMPT.report_index_sc_l1_inputs_current", not failures, "; ".join(failures))
    assert not failures


def test_PROMPT_confidence_scores_preserve_depth_feeder_ids(tmp_path: Path):
    project = tmp_path / "project"
    scratchpad = project / ".scratchpad"
    scratchpad.mkdir(parents=True)
    v1 = tmp_path / "plamen.md"
    v1.write_text("# SC\n\n## Phase 4b: Adaptive Depth Loop\n\nlegacy\n", encoding="utf-8")
    config = {
        "project_root": str(project),
        "scratchpad": str(scratchpad),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }
    prompt = D.build_phase_prompt(
        v1, next(p for p in D.SC_PHASES if p.name == "depth"), config
    )
    scoring_prompt = (SCRIPTS_DIR.parent / "prompts" / "shared" / "v2" / "phase4b-scoring.md").read_text(
        encoding="utf-8"
    )
    (scratchpad / "confidence_scores.md").write_text(
        "| Finding ID | Evidence | Consensus | Quality | RAG | Composite | Classification |\n"
        "|------------|----------|-----------|---------|-----|-----------|----------------|\n"
        "| DCI-3 | 0.80 | 0.60 | 0.70 | 0.30 | 0.71 | CONFIDENT |\n",
        encoding="utf-8",
    )
    scores = D._parse_depth_confidence_scores(scratchpad)
    ok = (
        "preserve original depth/scanner/niche" in prompt.lower()
        and "do not collapse those rows into only mapped `inv-*`" in prompt.lower()
        and "Preserve depth/scanner/niche IDs" in scoring_prompt
        and scores.get("DCI-3") == 0.71
    )
    check("PROMPT.confidence_scores_preserve_feeder_ids", ok, f"prompt={prompt[-800:]} scores={scores}")
    assert ok


def test_SKEPTIC_retry_hint_names_missing_ids(tmp_path: Path):
    sp = tmp_path / "scratch"
    sp.mkdir()
    ids = [("INV-103", "High"), ("INV-104", "Critical"), ("INV-105", "High")]
    _seed_queue(sp, ids)
    for fid, sev in ids:
        _write_verify(sp, fid, sev)
    D._write_skeptic_manifest(sp)
    (sp / "skeptic_findings.md").write_text(
        "## INV-103\nAGREE\n", encoding="utf-8"
    )
    hint = D._generate_skeptic_retry_hint(sp)
    check(
        "SKEPTIC.retry_hint_missing_ids",
        "INV-104" in hint and "INV-105" in hint and "INV-103" not in hint,
        hint,
    )


def test_RESUME_overflow_rewinds_completed_phase_and_downstream(tmp_path: Path):
    sp = tmp_path / "scratch"
    (sp / "_overflow" / "verify_low_c").mkdir(parents=True)
    (sp / "_overflow" / "verify_low_c" / "report_index.md").write_text(
        "rogue", encoding="utf-8"
    )
    cp = D.Checkpoint(
        completed=["verify_low_b", "verify_low_c", "verify_low_d", "verify_aggregate", "crossbatch"],
        degraded=[],
    )
    removed = D._rewind_completed_after_overflow(sp, cp, D.L1_PHASES)
    check(
        "RESUME.overflow_rewinds_from_contaminated_phase",
        removed == ["verify_low_c", "verify_low_d", "verify_aggregate", "crossbatch"]
        and cp.completed == ["verify_low_b"],
        f"removed={removed} completed={cp.completed}",
    )


TESTS_TMP = [
    test_PROMPT_l1_verify_shard_does_not_see_future_step5_subphases,
    test_PROMPT_sc_verify_shard_does_not_import_broad_language_phase5,
    test_PROMPT_standalone_wrapper_and_runtime_placeholders_are_resolved,
    test_PROMPT_l1_verify_aggregate_uses_aggregate_prompt,
    test_PROMPT_sc_depth_excludes_rag_sweep_subphase,
    test_PROMPT_sc_depth_rejects_executable_future_artifact_mentions,
    test_PROMPT_sc_depth_rendered_prompt_has_no_executable_downstream_tokens,
    test_PROMPT_sc_depth_enforces_language_template_and_graph_contract,
    test_PROMPT_phase4b_required_manifests_are_depth_owned_only,
    test_PROMPT_breadth_excludes_rescan_outputs,
    test_GATE_breadth_is_manifest_exact_not_generic_quorum,
    test_PROMPT_rescan_runs_before_inventory_and_owns_only_additional_analysis,
    test_PROMPT_inventory_chunk_no_final_output_contract,
    test_PROMPT_l1_inventory_contract_matches_phase_order,
    test_CONTRACT_graph_sweep_optional_outputs_are_owned,
    test_CONTRACT_expected_artifacts_are_owned_by_phase,
    test_CONTRACT_dynamic_report_shards_are_owned,
    test_CONTRACT_semantic_dedup_noop_writes_expected_outputs,
    test_CONTRACT_empty_verify_queue_skip_writes_canonical_queue,
    test_POLICY_validator_rejects_foreign_phase_writes_even_when_own_gate_passes,
    test_BOUNDARY_fake_claude_foreign_write_is_hard_failure_signal,
    test_CONTAINMENT_rogue_report_artifacts_detected_and_quarantined,
    test_CONTAINMENT_depth_static_denylist_catches_unmanifested_downstream_files,
    test_CONTAINMENT_live_abort_on_depth_downstream_artifact,
    test_STARTUP_rogue_report_without_assemble_is_quarantined,
    test_STARTUP_report_kept_when_assemble_completed,
    test_VERIFY_CORE_aggregate_uses_tolerant_field_parser,
    test_SKEPTIC_manifest_enumerates_all_ch_ids_and_prompt_references_it,
    test_PROMPT_skeptic_uses_aggregate_contract_not_shard_artifacts,
    test_PROMPT_crossbatch_and_verify_aggregate_allow_readonly_upstream_inputs,
    test_PROMPT_later_aggregation_templates_allow_readonly_upstream_inputs,
    test_PROMPT_report_index_matches_current_sc_l1_contracts,
    test_PROMPT_confidence_scores_preserve_depth_feeder_ids,
    test_SKEPTIC_retry_hint_names_missing_ids,
    test_RESUME_overflow_rewinds_completed_phase_and_downstream,
]

TESTS_BASIC = [
    test_GRAPH_l1_low_verify_shards_are_critical,
]


def main() -> int:
    print("Running phase-containment regression tests...")
    for test in TESTS_TMP:
        print(f"\n[{test.__name__}]")
        try:
            with tempfile.TemporaryDirectory() as td:
                test(Path(td))
        except Exception as exc:
            global FAIL
            FAIL += 1
            print(f"  CRASH {test.__name__} :: {exc!r}")
    for test in TESTS_BASIC:
        print(f"\n[{test.__name__}]")
        try:
            test()
        except Exception as exc:
            FAIL += 1
            print(f"  CRASH {test.__name__} :: {exc!r}")
    print(f"\n{'=' * 64}")
    print(f"  PASS: {PASS}   FAIL: {FAIL}")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
