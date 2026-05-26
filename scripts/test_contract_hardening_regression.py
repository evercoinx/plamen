from pathlib import Path

import plamen_driver as D
from plamen_types import plamen_home
from plamen_parsers import (
    _extract_report_ids_from_body,
    _filter_sc_verification_queue_by_mode,
    _sanitize_client_body,
    parse_verification_queue_rows,
)
from plamen_mechanical import (
    _assemble_report_python,
    _build_sc_body_writer_manifests,
    _collect_raw_candidate_ledger_rows,
    _repair_report_body_from_manifest,
    _repair_sc_report_index_from_prior,
    _synth_report_section_from_verify,
)
from plamen_prompt import build_phase_prompt
from plamen_validators import (
    _validate_tier_body_against_manifest,
    _validate_report_coverage_accounting,
    _validate_report_index_inputs,
    _validate_report_index_prewrite_inputs,
    _validate_report_index_triage_safety,
    _validate_inventory_structure,
    _validate_inventory_chunk_structure,
    _run_report_quality_gate,
    _repair_report_index_dropouts,
    _write_chain_passthrough_outputs,
)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_sc_core_queue_moves_low_info_to_excluded(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "verification_queue.md",
        "| Queue # | Finding ID | Severity | Title |\n"
        "|---------|------------|----------|-------|\n"
        "| 1 | H-1 | Low | low issue |\n"
        "| 2 | H-2 | Informational | info issue |\n"
        "| 3 | H-3 | Medium | medium issue |\n",
    )

    moved = _filter_sc_verification_queue_by_mode(sp, "core")

    rows = parse_verification_queue_rows(sp)
    excluded = (sp / "verification_queue_evidence_excluded.md").read_text(
        encoding="utf-8"
    )
    assert moved == 2
    assert [r["finding id"] for r in rows] == ["H-3"]
    assert "H-1" in excluded and "H-2" in excluded


def test_resume_rewinds_completed_sc_verify_shard_missing_outputs(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "verification_queue.md",
        "| Queue # | Finding ID | Severity | Title |\n"
        "|---------|------------|----------|-------|\n"
        "| 1 | H-1 | High | missing verifier |\n",
    )
    D.ensure_sc_verify_shard_manifests(sp)
    phase = next(p for p in D.SC_PHASES if p.name == "sc_verify_crithigh")

    issues = D._resume_phase_contract_issues(sp, str(tmp_path), phase, "thorough")

    assert any("verify completion" in issue for issue in issues)


def test_verify_completion_rejects_partial_schema_on_timeout(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "verification_queue.md",
        "| Queue # | Finding ID | Severity | Title |\n"
        "|---------|------------|----------|-------|\n"
        "| 1 | H-1 | High | partial verifier |\n",
    )
    D.ensure_sc_verify_shard_manifests(sp)
    _write(sp / "verify_H-1.md", "# Verification H-1\n\npartial body\n" + ("x" * 120))

    issues = D._validate_verify_completion(sp, "sc_verify_crithigh")

    assert any("verify schema" in issue for issue in issues)


def test_spawn_manifest_rejects_duplicate_agent_outputs(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "spawn_manifest.md",
        "| Row Type | Template | Required? | Agent ID | Focus Area | Expected Output | Status |\n"
        "|----------|----------|-----------|----------|------------|-----------------|--------|\n"
        "| AGENT | A | YES | B1 | one | analysis_same.md | PENDING |\n"
        "| AGENT | B | YES | B2 | two | analysis_same.md | PENDING |\n",
    )

    issues = D._validate_spawn_manifest_schema(sp)

    assert any("unique output" in issue for issue in issues)


def test_rescan_gate_requires_percontract_scope_review(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "analysis_rescan_1.md", "# Rescan\n\n" + ("x" * 200))
    phase = next(p for p in D.SC_PHASES if p.name == "rescan")

    passed, missing = D.gate_passes(sp, str(tmp_path), phase)

    assert not passed
    assert "analysis_percontract_*.md" in missing


def test_invariants_phase_is_degradable_v1_fallback():
    sc = next(p for p in D.SC_PHASES if p.name == "invariants")
    l1 = next(p for p in D.L1_PHASES if p.name == "invariants")

    assert sc.critical is False
    assert l1.critical is False
    assert "semantic_invariants.md" in sc.expected_artifacts
    assert "semantic_invariants.md" in l1.expected_artifacts


def test_quality_observation_rows_count_as_report_body_ids():
    body = (
        "## Low Findings\n\n"
        "### [L-01] Full low\n\nbody\n\n"
        "## Quality Observations\n\n"
        "| ID | Title | Severity | Location | Class | Description |\n"
        "|----|-------|----------|----------|-------|-------------|\n"
        "| I-03 | Unused import | Info | src/Vault.sol:L5 | unused_import | Cosmetic |\n"
    )

    assert _extract_report_ids_from_body(body) == ["L-01", "I-03"]


def test_artifact_recovery_requires_owner_state(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "spawn_manifest.md", "# stale manifest\n\n" + ("x" * 100))

    ok, issues = D._phase_artifacts_have_active_owner_state(
        sp, str(tmp_path), "instantiate", "sc"
    )

    assert not ok
    assert any("no owner record" in issue for issue in issues)


def test_inventory_chunk_prompt_is_direct_execution_not_orchestration(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "inventory_chunk_a.manifest.md",
        "# inventory_chunk_a manifest\n\n"
        "| File | Estimated signals |\n"
        "|------|-------------------|\n"
        "| analysis_alpha.md | 3 |\n",
    )
    phase = next(p for p in D.SC_PHASES if p.name == "inventory_chunk_a")
    prompt = build_phase_prompt(
        plamen_home() / "commands" / "plamen.md",
        phase,
        {
            "scratchpad": str(sp),
            "project_root": str(tmp_path),
            "language": "evm",
            "mode": "thorough",
            "pipeline": "sc",
            "proven_only": False,
        },
    )

    assert "Do NOT use the Task tool or spawn child agents" in prompt
    assert "DIRECT EXECUTION CONTEXT POLICY" in prompt
    assert "Read ONLY these analysis artifacts" in prompt
    assert "analysis_alpha.md" in prompt
    assert "CONTEXT DELEGATION PROTOCOL" not in prompt
    assert "single consolidated `findings_inventory.md`" not in prompt
    assert "phase4a-inventory-base.md" not in prompt


def test_recon_prompt_uses_language_split_clean_handoff_contract(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    phase = next(p for p in D.SC_PHASES if p.name == "recon")
    prompt = build_phase_prompt(
        plamen_home() / "commands" / "plamen.md",
        phase,
        {
            "scratchpad": str(sp),
            "project_root": str(tmp_path),
            "language": "evm",
            "mode": "thorough",
            "pipeline": "sc",
            "proven_only": False,
        },
    )

    assert "BEGIN STANDALONE V2 PHASE PROMPT" in prompt
    # Ship 8.14: recon now resolves to the V2 single-agent direct-execution
    # prompt (prompts/evm/v2/phase1-recon-prompt.md), not the legacy
    # multi-agent orchestrator (prompts/evm/phase1-recon-prompt.md).
    assert "prompts\\evm\\v2\\phase1-recon-prompt.md" in prompt or "prompts/evm/v2/phase1-recon-prompt.md" in prompt
    # Ship 8.14: the V2 recon body is single-agent direct-execution -- it has
    # no "ORCHESTRATOR SPLIT DIRECTIVE" (that was legacy multi-agent body) and
    # carries the DRAFT-FIRST TURN BUDGET POLICY instead.
    assert "ORCHESTRATOR SPLIT DIRECTIVE" not in prompt
    assert "TURN BUDGET POLICY" in prompt
    assert "RECON CLEAN HANDOFF CONTRACT" in prompt
    assert "CONTEXT DELEGATION PROTOCOL" in prompt
    assert "Use the Task tool for parallel subagent work" in prompt
    assert "RECON EXECUTION CONTEXT POLICY" not in prompt
    assert "Do NOT use the Task tool or spawn child agents" not in prompt
    for artifact in phase.expected_artifacts:
        assert f"`{artifact}`" in prompt
    assert "[LLM TO ENRICH]" not in prompt
    assert "[LLM TO LIST]" not in prompt
    assert "{TBD}" not in prompt
    assert "(stub)" not in prompt.lower()
    assert "Minimum-valid-stub" not in prompt
    assert "You may overwrite or enrich any expected recon artifact" in prompt


def test_l1_bake_prompt_is_direct_but_allows_tooling(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    phase = next(p for p in D.L1_PHASES if p.name == "bake")
    prompt = build_phase_prompt(
        plamen_home() / "commands" / "plamen-l1.md",
        phase,
        {
            "scratchpad": str(sp),
            "project_root": str(tmp_path),
            "language": "rust",
            "mode": "thorough",
            "pipeline": "l1",
            "proven_only": False,
        },
    )

    assert "L1 BAKE EXECUTION CONTEXT POLICY" in prompt
    assert "Do NOT use the Task tool or spawn child agents" in prompt
    assert "CONTEXT DELEGATION PROTOCOL" not in prompt
    assert "Shell/Python tooling is allowed" in prompt
    assert "Do NOT use shell/Python helper scripts" not in prompt
    assert "primitive_status.md" in prompt


def test_depth_prompt_copies_graph_contract_into_standard_agent_prompts(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    phase = next(p for p in D.SC_PHASES if p.name == "depth")
    prompt = build_phase_prompt(
        plamen_home() / "commands" / "plamen.md",
        phase,
        {
            "scratchpad": str(sp),
            "project_root": str(tmp_path),
            "language": "evm",
            "mode": "thorough",
            "pipeline": "sc",
            "proven_only": False,
        },
    )

    assert "Standard Depth Agent Graph Block" in prompt
    assert "COPY INTO EACH OF THE 4 PROMPTS" in prompt
    assert "## Graph Artifact Consumption" in prompt
    for artifact in (
        "caller_map.md",
        "callee_map.md",
        "state_write_map.md",
        "function_summary.md",
    ):
        assert f"[GRAPH-ARTIFACT: {artifact}]" in prompt
        assert f"[GRAPH-ARTIFACT: UNAVAILABLE:{artifact}]" in prompt
    assert "re-open the four standard depth output files" in prompt


def test_depth_prompts_forbid_disposition_as_severity() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase4b-depth.md").read_text(
        encoding="utf-8"
    )
    assert "Finding Severity / Disposition Contract" in shared
    assert "Never write disposition text in the severity field" in shared
    assert "N/A (absorbed into DE-2)" in shared

    prompt_root = plamen_home() / "prompts"
    for lang in ("evm", "solana", "aptos", "sui", "soroban", "go", "rust", "l1"):
        text = (prompt_root / lang / "phase4b-depth-templates.md").read_text(
            encoding="utf-8"
        )
        assert "Severity / Disposition Contract" in text, lang
        assert "Do not write `N/A`" in text, lang
        assert "absorbed into ..." in text, lang


def test_sc_verification_prompts_require_poc_testability_ledger() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase5-verification-sc.md").read_text(
        encoding="utf-8"
    )
    evm = (plamen_home() / "prompts" / "evm" / "phase5-verification-prompt.md").read_text(
        encoding="utf-8"
    )
    rules = (plamen_home() / "rules" / "phase5-poc-execution.md").read_text(
        encoding="utf-8"
    )

    for text in (shared, evm, rules):
        assert "PoC Testability" in text or "POC TESTABILITY" in text or "PoC Testability Ledger" in text
        assert "PoC Not Attempted Because" in text
        assert "NO_BUILD_ENVIRONMENT" in text
        assert "EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS" in text
        assert "STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION" in text

    assert '"no test written" is not a' in shared
    assert "Compiled: N/A" in evm
    assert "`unit` and `property` findings" in rules


def test_sc_verification_prompt_resumes_completed_rows() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase5-verification-sc.md").read_text(
        encoding="utf-8"
    )
    prompt_builder = (plamen_home() / "scripts" / "plamen_prompt.py").read_text(
        encoding="utf-8"
    )

    for text in (shared, prompt_builder):
        assert "On resume/retry" in text
        assert "Severity:" in text
        assert "Evidence Tag:" in text
        assert "Verdict:" in text
        assert "Do not rewrite" in text
        assert "completed verifier files" in text


def test_verify_path_recovery_ignores_generated_poc_test_paths(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    scip = sp / "scip"
    scip.mkdir(parents=True)
    _write(scip / "repo_map.md", "## path: AwesomeXBuyAndBurn.sol\n")
    _write(
        sp / "verify_INV-104.md",
        "# Verification: INV-104\n\n"
        "**Location**: AwesomeXBuyAndBurn.sol:L495-L522\n\n"
        "## PoC Attempt\n"
        "- Test File: D:\\Programming\\Audit\\test\\Test_H21_LoopGas.t.sol\n\n"
        "### Error Trace\n"
        "- **Location**: Test_H21_LoopGas.t.sol\n",
    )
    _write(sp / "path_unresolved.md", "stale\n")

    issues = D._validate_cited_paths_in_verify(sp)

    assert issues == []
    assert not (sp / "path_unresolved.md").exists()


def test_location_parser_prefers_production_over_poc_test_file() -> None:
    rel, line = D._parse_location_ref(
        "Test File: test/Test_H43_Uint192Cast.t.sol:L10; "
        "Vulnerable code: AwesomeXMinting.sol:L155"
    )

    assert rel == "AwesomeXMinting.sol"
    assert line == 155


def test_location_parser_does_not_truncate_method_name_to_c_file() -> None:
    rel, line = D._parse_location_ref(
        "AwesomeXBuyAndBurn.sol:_getTWAPAveragePriceX96(), OracleLibrary.consult()"
    )

    assert rel == "AwesomeXBuyAndBurn.sol"
    assert line is None


def test_report_index_retry_hint_names_severity_provenance_fix() -> None:
    issue = (
        "report_index severity provenance: L-02 maps H-17 from Medium to Low "
        "with no Trust Adj./Severity Trail reason"
    )
    hint = D._generate_report_index_retry_hint([issue])

    assert "silent severity change" in hint
    assert "Restore the row to the upstream verifier/queue severity" in hint
    assert "PROVEN(original_sev)` only when PROVEN_ONLY=true" in hint
    assert "Do NOT silently place a Medium verified finding in a Low row" in hint


def test_report_index_prompts_define_severity_provenance_contract() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase6a-report-index.md").read_text(
        encoding="utf-8"
    )
    rules = (plamen_home() / "rules" / "phase6-report-prompts.md").read_text(
        encoding="utf-8"
    )

    for text in (shared, rules):
        assert "Severity Authority Contract (READ BEFORE TIERING)" in text
        assert "Trust Adj." in text
        assert "Report indexing is a mapping task, not a new severity-assessment phase" in text
        assert "does NOT imply Low" in text
        assert "PROVEN_ONLY: false" in text
        assert "Medium verified findings into" in text
        assert "Low rows" in text
        assert "TRUSTED-ACTOR(High)" in text


def test_report_index_prompt_contains_severity_binding_directive(tmp_path: Path) -> None:
    """v2.7.8+: report_index prompt tells LLM to read severity_binding.md."""
    from plamen_prompt import build_phase_prompt
    from plamen_types import Phase
    phase = Phase("report_index", ["Step 6a"], ["report_index.md", "report_coverage.md"],
                  base_timeout_s=1500, model="sonnet", critical=True)
    config = {
        "project_root": str(tmp_path),
        "mode": "core",
        "pipeline": "sc",
        "scope_paths": [],
        "scratchpad": str(tmp_path),
        "language": "evm",
    }
    v1_prompt = plamen_home() / "commands" / "plamen.md"
    if not v1_prompt.exists():
        return
    prompt = build_phase_prompt(v1_prompt, phase, config)
    assert "SEVERITY BINDING" in prompt
    assert "severity_binding.md" in prompt
    assert "Trust Adj" in prompt
    assert "provenance gate" in prompt


def test_severity_binding_file_written(tmp_path: Path) -> None:
    """v2.7.8+: driver writes severity_binding.md before report_index."""
    from plamen_validators import _expected_report_index_severities

    inv = tmp_path / "findings_inventory.md"
    inv.write_text(
        "# Findings Inventory\n\n"
        "## [INV-001] Test Finding\n"
        "- **Severity**: Medium\n"
        "- **Root Cause**: test\n\n"
        "## [INV-002] Another Finding\n"
        "- **Severity**: High\n"
        "- **Root Cause**: test2\n",
        encoding="utf-8",
    )
    queue = tmp_path / "verification_queue.md"
    queue.write_text(
        "| Finding ID | Severity | Status |\n"
        "|------------|----------|--------|\n"
        "| INV-001 | Medium | active |\n"
        "| INV-002 | High | active |\n",
        encoding="utf-8",
    )
    # Write stub verify files so _enforce_severity_matrix can read them
    (tmp_path / "verify_INV-001.md").write_text(
        "**Severity**: Medium\n**Verdict**: CONFIRMED\n", encoding="utf-8"
    )
    (tmp_path / "verify_INV-002.md").write_text(
        "**Severity**: High\n**Verdict**: CONFIRMED\n", encoding="utf-8"
    )
    sev_map = _expected_report_index_severities(tmp_path)
    assert "INV-001" in sev_map or "inv-001" in {k.lower() for k in sev_map}
    assert "INV-002" in sev_map or "inv-002" in {k.lower() for k in sev_map}


def test_phase6_report_prompt_stays_below_context_warning_limit() -> None:
    claude_path = plamen_home() / "rules" / "phase6-report-prompts.md"
    codex_path = Path.home() / ".codex" / "plamen" / "rules" / "phase6-report-prompts.md"
    claude_text = claude_path.read_text(encoding="utf-8")
    assert len(claude_text) < 40_000

    # Codex side is install-state-dependent (post-`plamen install --codex`).
    # CI pytest runners don't run install, so the codex symlink target
    # is absent. Skip the parity check there; the install-smoke job
    # already verifies the codex adapter generates the file correctly.
    if not codex_path.exists():
        import pytest
        pytest.skip(
            f"{codex_path} missing — Codex side not installed on this "
            "runner; install-smoke job verifies generation"
        )
    codex_text = codex_path.read_text(encoding="utf-8")
    assert len(codex_text) < 40_000
    assert claude_text == codex_text


def test_report_index_prompts_define_conservative_client_worthiness_triage() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase6a-report-index.md").read_text(
        encoding="utf-8"
    )
    rules = (plamen_home() / "rules" / "phase6-report-prompts.md").read_text(
        encoding="utf-8"
    )

    for text in (shared, rules):
        assert "Client-Worthiness Triage (CONSERVATIVE)" in text
        assert "Never silently delete a candidate" in text
        assert "Medium+ verified candidates default to `REPORTABLE`" in text
        assert "APPENDIX_ONLY" in text
        assert "DROP_NON_SECURITY" in text
        assert "DROP_DESIGN_CONFIRMATION" in text
        assert "DROP_UNACTIONABLE_SPECULATION" in text
        assert "Excluded Findings" in text


def test_reportability_status_parser_understands_client_worthiness_tokens() -> None:
    from plamen_parsers import _is_reportable_verdict, _verifier_status_from_text

    for token in (
        "APPENDIX_ONLY",
        "DROP_FALSE_POSITIVE",
        "DROP_NON_SECURITY",
        "DROP_DESIGN_CONFIRMATION",
        "DROP_UNACTIONABLE_SPECULATION",
    ):
        assert _verifier_status_from_text(f"**Verdict**: {token}\n") == token
        assert not _is_reportable_verdict(token)

    assert _is_reportable_verdict("CONFIRMED")


def test_report_index_triage_safety_accepts_medium_appendix_only(tmp_path: Path) -> None:
    """v2.8.5: APPENDIX_ONLY is now allowed for Medium+ exclusions."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Severity | Exclusion Reason |\n"
        "|-------------|----------|------------------|\n"
        "| H-7 | Medium | APPENDIX_ONLY: minor but still verified |\n",
    )

    assert _validate_report_index_inputs(sp) == []
    issues = _validate_report_index_triage_safety(sp)
    assert issues == [], f"APPENDIX_ONLY should be allowed for Medium+: {issues}"


def test_report_index_triage_safety_allows_low_appendix_only(tmp_path: Path) -> None:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Severity | Exclusion Reason |\n"
        "|-------------|----------|------------------|\n"
        "| H-8 | Low | APPENDIX_ONLY: non-material edge case retained for traceability |\n",
    )

    assert _validate_report_index_inputs(sp) == []


def test_report_index_triage_safety_allows_medium_non_security_or_merge(tmp_path: Path) -> None:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Severity | Exclusion Reason |\n"
        "|-------------|----------|------------------|\n"
        "| H-9 | Medium | DROP_NON_SECURITY: operational note, no security impact |\n"
        "| H-10 | High | MERGE_INTO:H-01 same root cause and impact |\n",
    )

    assert _validate_report_index_inputs(sp) == []


def test_report_index_triage_safety_allows_contested_unresolved_evidence(tmp_path: Path) -> None:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Severity | Exclusion Reason |\n"
        "|-------------|----------|------------------|\n"
        "| CONTESTED-1 | High | APPENDIX_ONLY - confidence 0.15; insufficient evidence across all four axes; no reproducible code path |\n"
        "| CONTESTED-2 | Medium | APPENDIX_ONLY - confidence 0.10; no trace to claimed overflow; unresolved after two depth iterations |\n",
    )

    assert _validate_report_index_inputs(sp) == []


def test_report_index_triage_safety_allows_evidence_unresolved_phrasing(tmp_path: Path) -> None:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Severity | Exclusion Reason |\n"
        "|-------------|----------|------------------|\n"
        "| CONTESTED-2 | Medium | APPENDIX_ONLY - confidence 0.10; probable semantic overlap with M-06 access-control aspect; evidence unresolved after two depth iterations |\n"
        "| CONTESTED-5 | Medium | APPENDIX_ONLY - confidence 0.05; near-zero evidence across all four axes |\n",
    )

    assert _validate_report_index_inputs(sp) == []


def test_codex_report_index_role_writes_index_and_coverage() -> None:
    adapter = (plamen_home() / "scripts" / "codex_adapter.py").read_text(
        encoding="utf-8"
    )

    block = adapter[adapter.index('"filename": "report-index.toml"'):]
    block = block[: block.index('"filename": "report-tier-writer.toml"')]
    assert "{SCRATCHPAD}/report_index.md" in block
    assert "{SCRATCHPAD}/report_coverage.md" in block
    assert "those two assigned output files" in block


def test_report_index_recovery_uses_exact_prompt_headings() -> None:
    validators = (plamen_home() / "scripts" / "plamen_validators.py").read_text(
        encoding="utf-8"
    )

    assert "## Master Finding Index - Mechanical Recovery" not in validators
    assert "## Excluded Findings - Mechanical Recovery" not in validators


def test_report_index_dropout_repair_inserts_into_existing_master_table(tmp_path: Path) -> None:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "verification_queue.md",
        "| Finding ID | Severity | Title | Location | Preferred Tag |\n"
        "|------------|----------|-------|----------|---------------|\n"
        "| H-1 | High | Existing issue | A.sol:L1 | CODE-TRACE |\n"
        "| H-2 | Medium | Dropped issue | B.sol:L2 | CODE-TRACE |\n",
    )
    _write(
        sp / "verify_H-1.md",
        "# Verification H-1\n\n**Verdict**: CONFIRMED\n**Severity**: High\n\n" + ("x" * 120),
    )
    _write(
        sp / "verify_H-2.md",
        "# Verification H-2\n\n**Verdict**: CONFIRMED\n**Severity**: Medium\n"
        "**Location**: B.sol:L2\n**Evidence Tag**: CODE-TRACE\n\n" + ("y" * 120),
    )
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Master Finding Index\n\n"
        "| Report ID | Title | Severity | Location | Evidence Tag | Recovery Note | Trust Adj. | Internal Hypothesis ID |\n"
        "|-----------|-------|----------|----------|--------------|---------------|------------|------------------------|\n"
        "| H-01 | Existing issue | High | A.sol:L1 | CODE-TRACE | - | from Medium to High: fixture pre-existing row | H-1 |\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Verdict | Reason |\n"
        "|-------------|---------|--------|\n",
    )

    assert _repair_report_index_dropouts(sp) == ["H-2"]
    repaired = (sp / "report_index.md").read_text(encoding="utf-8")

    assert repaired.count("## Master Finding Index") == 1
    assert "| M-01 | Dropped issue | Medium | B.sol:L2 | CODE-TRACE |" in repaired
    assert _validate_report_index_inputs(sp) == []


def test_report_index_prewrite_validation_ignores_stale_bad_index(tmp_path: Path) -> None:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "verification_queue.md",
        "| Finding ID | Severity | Title | Location | Preferred Tag |\n"
        "|------------|----------|-------|----------|---------------|\n"
        "| H-1 | High | Valid input | A.sol:L1 | CODE-TRACE |\n",
    )
    _write(
        sp / "verify_H-1.md",
        "# Verification H-1\n\n**Verdict**: CONFIRMED\n**Severity**: High\n\n" + ("x" * 120),
    )
    _write(
        sp / "report_index.md",
        "# Stale bad index\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Severity | Exclusion Reason |\n"
        "|-------------|----------|------------------|\n"
        "| H-1 | High | APPENDIX_ONLY: stale invalid decision |\n",
    )

    assert _validate_report_index_prewrite_inputs(sp) == []
    # Triage safety is now WARNING-only (v2.8.5), so _validate_report_index_inputs
    # no longer returns triage issues — it logs them instead.
    assert _validate_report_index_inputs(sp) == []


def test_raw_candidate_ledger_ignores_broad_analysis_and_eip_standards(tmp_path: Path) -> None:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "analysis_core.md",
        "# Breadth\n\nFinding AC-1 references EIP-20 and VS-7 as context only.\n",
    )
    _write(
        sp / "findings_inventory.md",
        "### Finding [INV-001]: Real queued issue\n"
        "**Severity**: High\n"
        "**Location**: A.sol:L1\n",
    )

    rows = _collect_raw_candidate_ledger_rows(sp, promoted_ids=set(), excluded_ids=set())
    joined = "\n".join(rows)

    assert "INV-001" in joined
    assert "AC-1" not in joined
    assert "EIP-20" not in joined


def test_client_sanitizer_preserves_eip_standards() -> None:
    text = "This finding concerns ERC-20 / EIP-20 compatibility, not an internal ID."

    assert _sanitize_client_body(text) == text


def test_semantic_dedup_prompt_requires_physical_passthrough_writes() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase4e-semantic-dedup.md").read_text(
        encoding="utf-8"
    )
    driver = (plamen_home() / "scripts" / "plamen_driver.py").read_text(
        encoding="utf-8"
    )

    assert "physically create safe passthrough outputs on disk" in shared
    assert "Do not merely return a summary" in shared
    assert "Only return `DONE` after `dedup_decisions.md`" in shared
    assert "pre-run passthrough safety net" in driver


def test_chain_prompt_requires_physical_handoff_writes() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase4c-chain-agent1.md").read_text(
        encoding="utf-8"
    )
    rules = (plamen_home() / "rules" / "phase4c-chain-prompt.md").read_text(
        encoding="utf-8"
    )
    driver = (plamen_home() / "scripts" / "plamen_driver.py").read_text(
        encoding="utf-8"
    )

    for text in (shared, rules):
        assert "Mandatory First Action" in text
        assert "physically create all three handoff files on disk" in text
        assert "MECHANICAL_BASELINE" in text
        assert "Only return `DONE` after `hypotheses.md`" in text
    assert "_write_chain_passthrough_outputs" in driver


def test_chain_passthrough_preserves_every_inventory_finding(tmp_path: Path) -> None:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "findings_inventory.md",
        "# Inventory\n\n"
        "### Finding [INV-001]: First issue\n"
        "**Severity**: High\n"
        "**Location**: A.sol:L1\n\n"
        "### Finding [INV-002]: Second issue\n"
        "**Severity**: Low\n"
        "**Location**: B.sol:L2\n",
    )

    written = _write_chain_passthrough_outputs(sp, "test scaffold")

    assert written == ["hypotheses.md", "finding_mapping.md", "enabler_results.md"]
    hypotheses = (sp / "hypotheses.md").read_text(encoding="utf-8")
    mapping = (sp / "finding_mapping.md").read_text(encoding="utf-8")
    enablers = (sp / "enabler_results.md").read_text(encoding="utf-8")
    assert "MECHANICAL_BASELINE" in hypotheses
    assert "INV-001" in hypotheses and "INV-002" in hypotheses
    assert "| INV-001 | H-1 | BASELINE_ONE_TO_ONE |" in mapping
    assert "| INV-002 | H-2 | BASELINE_ONE_TO_ONE |" in mapping
    assert "No new enabler paths were mechanically introduced" in enablers


def test_report_index_prompt_forbids_prior_attempt_inputs() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase6a-report-index.md").read_text(
        encoding="utf-8"
    )
    rules = (plamen_home() / "rules" / "phase6-report-prompts.md").read_text(
        encoding="utf-8"
    )

    for text in (shared, rules):
        assert "Forbidden inputs" in text
        assert "report_index.md" in text
        assert "*.attempt*" in text
        assert "_retry_quarantine/" in text
        assert "stop immediately" in text
        assert "Do NOT bulk-read raw breadth/depth/scanner artifacts" in text


def test_report_index_prompt_never_contains_future_body_writer_instructions(tmp_path: Path) -> None:
    phase = next(p for p in D.SC_PHASES if p.name == "report_index")
    v1 = tmp_path / "plamen.md"
    v1.write_text(
        "# V1\n\n"
        "## Step 6a\nindex only\n\n"
        "## Step 6b\nWrite to report_critical_high.md\n",
        encoding="utf-8",
    )
    prompt = build_phase_prompt(
        v1,
        phase,
        {
            "pipeline": "sc",
            "scratchpad": str(tmp_path / ".scratchpad"),
            "project_root": str(tmp_path),
            "language": "evm",
            "mode": "thorough",
            "proven_only": False,
        },
    )

    assert "BEGIN STANDALONE V2 PHASE PROMPT" in prompt
    assert "## Step 6b" not in prompt
    assert "FIRST ACTION" not in prompt
    assert "Use the Write tool to create" not in prompt
    assert "Forbidden outputs in this phase" in prompt
    assert "Do NOT write `report_critical_high.md`" in prompt


def test_report_tier_writer_prompt_requires_exact_driver_output_file() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase6b-tier-writers.md").read_text(
        encoding="utf-8"
    )

    assert "exact output filename assigned by the driver" in shared
    assert "report_medium_a.md" in shared
    assert "Do not infer or normalize" in shared
    assert "Write ONLY the exact driver-assigned tier output file" in shared


def test_report_assembler_prompt_forbids_pending_placeholders() -> None:
    shared = (plamen_home() / "prompts" / "shared" / "v2" / "phase6c-assembler.md").read_text(
        encoding="utf-8"
    )

    assert 'Do not write "Section pending" placeholders' in shared
    assert "fatal" in shared.lower()
    assert "Only return `DONE` after" in shared


def test_report_index_body_overflow_is_quarantine_only() -> None:
    benign, blocking = D._split_nonblocking_foreign_writes(
        "report_index",
        ["report_critical_high.md", "report_medium_a.md", "AUDIT_REPORT.md"],
    )

    assert benign == ["report_critical_high.md", "report_medium_a.md"]
    assert blocking == ["AUDIT_REPORT.md"]


def test_retry_quarantine_moves_bad_outputs_out_of_readable_scratchpad(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "report_index.md", "# bad index\n\n" + ("x" * 600))
    _write(sp / "report_coverage.md", "# bad coverage\n\n" + ("y" * 600))
    phase = next(p for p in D.SC_PHASES if p.name == "report_index")

    moved = D._quarantine_stale_on_retry(sp, phase, ["report_index failed"])

    assert sorted(moved) == ["report_coverage.md", "report_index.md"]
    assert not (sp / "report_index.md").exists()
    assert not (sp / "report_coverage.md").exists()
    assert not (sp / "report_index.md.attempt1").exists()
    assert (sp / "_retry_quarantine" / "report_index" / "report_index.md").exists()
    assert (sp / "_retry_quarantine" / "report_index" / "report_coverage.md").exists()


def test_sc_report_index_repair_restores_verifier_severity_from_quarantine(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    q = sp / "_retry_quarantine" / "report_index"
    q.mkdir(parents=True)
    _write(
        sp / "verification_queue.md",
        "| Queue # | Finding ID | Severity | Title |\n"
        "|---------|------------|----------|-------|\n"
        "| 1 | H-1 | Medium | verifier-owned severity |\n",
    )
    # NB: keep this fixture >= 100 bytes on POSIX. Python's Path.write_text
    # uses universal-newline translation, so `\n` becomes `\r\n` on Windows;
    # without the explanatory body line the file is 98 bytes on Linux/macOS
    # and fails the `_verify_file_present_for_id(min_bytes=100)` gate.
    _write(
        sp / "verify_H-1.md",
        "# Verification H-1\n\n"
        "**Verdict**: CONFIRMED\n\n"
        "**Severity**: Medium\n\n"
        "**Evidence Tag**: [CODE-TRACE]\n\n"
        "Verifier observed the described mechanism in the cited location.\n",
    )
    _write(
        q / "report_index.md.bad",
        "# Report Index\n\n"
        "## Summary Counts\n\n"
        "| Severity | Count |\n"
        "|----------|-------|\n"
        "| Critical | 0 |\n"
        "| High | 0 |\n"
        "| Medium | 0 |\n"
        "| Low | 1 |\n"
        "| Informational | 0 |\n"
        "| **Total** | **1** |\n\n"
        "## Master Finding Index\n\n"
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |\n"
        "|-----------|-------|----------|----------|--------------|------------|---------------------|\n"
        "| L-01 | Wrongly low | Low | A.sol:L1 | VERIFIED [CODE-TRACE] | - | H-1 |\n\n"
        "## Tier Assignments\n\n"
        "| Report ID | Internal Hypothesis | Verify File(s) | Notes |\n"
        "|-----------|---------------------|----------------|-------|\n"
        "| L-01 | H-1 | .scratchpad/verify_H-1.md | stale |\n",
    )
    _write(
        q / "report_coverage.md.bad",
        "# Report Coverage\n\n"
        "## Raw Candidate Ledger\n\n"
        "| Source Artifact | Candidate ID | Disposition |\n"
        "|-----------------|--------------|-------------|\n"
        "| verify_H-1.md | H-1 | PROMOTED M-01 |\n",
    )

    assert _repair_sc_report_index_from_prior(sp) == 1

    repaired = (sp / "report_index.md").read_text(encoding="utf-8")
    assert "| M-01 | Wrongly low | Medium |" in repaired
    assert "| Low | 0 |" in repaired
    assert _validate_report_index_inputs(sp) == []
    assert _validate_report_coverage_accounting(sp) == []


def test_sc_body_manifest_uses_chain_constituent_verify_files(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "verification_queue.md",
        "| Queue # | Finding ID | Severity | Title |\n"
        "|---------|------------|----------|-------|\n"
        "| 1 | H-3 | Medium | state bug |\n"
        "| 2 | H-9 | Medium | slippage bug |\n",
    )
    for fid in ("H-3", "H-9"):
        _write(
            sp / f"verify_{fid}.md",
            f"# Verification {fid}\n\n"
            "**Verdict**: CONFIRMED\n\n"
            "**Description**: verified constituent evidence.\n\n"
            "**Recommendation**: fix the constituent bug.\n",
        )
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Master Finding Index\n\n"
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |\n"
        "|-----------|-------|----------|----------|--------------|------------|---------------------|\n"
        "| H-01 | Chain finding | High | A.sol:L1 | VERIFIED (chain: H-3+H-9) | - | CH-1 |\n",
    )

    manifests = _build_sc_body_writer_manifests(sp)
    row = manifests["report_critical_high"]["findings"][0]

    assert row["finding_id"] == "CH-1"
    assert row["verify_files"] == ["verify_H-3.md", "verify_H-9.md"]
    assert row["report_blocked"] is False


def test_report_body_repair_replaces_stale_report_blocked_with_verified_section(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "body_manifests").mkdir()
    _write(
        sp / "verify_H-3.md",
        "# Verification H-3\n\n"
        "**Verdict**: CONFIRMED\n\n"
        "**Description**: verified root cause narrative.\n\n"
        "**Impact**: verified impact narrative.\n\n"
        "**Recommendation**: apply the verifier fix.\n",
    )
    _write(
        sp / "body_manifests" / "report_critical_high.json",
        '{\n'
        '  "shard": "report_critical_high",\n'
        '  "findings": [{\n'
        '    "report_id": "H-01",\n'
        '    "finding_id": "CH-1",\n'
        '    "severity": "High",\n'
        '    "title": "Verified chain finding",\n'
        '    "location": "A.sol:L1",\n'
        '    "evidence_tag": "[CODE-TRACE]",\n'
        '    "verify_files": ["verify_H-3.md"],\n'
        '    "report_blocked": false\n'
        '  }]\n'
        '}\n',
    )
    _write(
        sp / "report_critical_high.md",
        "# High Findings\n\n"
        "### [REPORT-BLOCKED: insufficient evidence] [H-01] stale blocked text\n\n"
        "Phase 5 verification did not produce evidence.\n",
    )

    assert _repair_report_body_from_manifest(sp, "report_body_writer_critical_high") == 1

    body = (sp / "report_critical_high.md").read_text(encoding="utf-8")
    assert "[REPORT-BLOCKED" not in body
    assert "verified root cause narrative" in body
    assert _validate_tier_body_against_manifest(sp, "report_critical_high") == []


def test_report_body_writer_does_not_flag_peer_tier_body_as_foreign(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(sp / "report_critical_high.md", "# Critical High\n\n### [C-01] title\n")
    before_state = {}
    phase_names = [
        "report_body_writer_medium_b",
        "report_critical_high",
        "report_medium_b",
        "report_assemble",
    ]
    phases = [
        type("P", (), {"name": name})()
        for name in phase_names
    ]

    offenders = D._detect_foreign_phase_writes(
        sp,
        str(tmp_path),
        phases,
        "report_body_writer_medium_b",
        "sc",
        before_state,
    )

    assert offenders == []


def test_legacy_tier_restores_valid_body_from_overflow(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    (sp / "body_manifests").mkdir()
    overflow = sp / "_overflow" / "report_body_writer_medium_b"
    overflow.mkdir(parents=True)
    _write(
        sp / "body_manifests" / "report_critical_high.json",
        '{\n'
        '  "shard": "report_critical_high",\n'
        '  "findings": [{\n'
        '    "report_id": "C-01",\n'
        '    "finding_id": "H-1",\n'
        '    "severity": "Critical",\n'
        '    "title": "critical title",\n'
        '    "location": "A.sol:L1",\n'
        '    "report_blocked": false\n'
        '  }]\n'
        '}\n',
    )
    _write(
        overflow / "report_critical_high.md",
        "# Critical and High Findings\n\n"
        "### [C-01] critical title\n\n"
        "**Severity**: Critical\n"
        "**Location**: A.sol:L1\n\n"
        "**Description**: valid restored body.\n",
    )

    assert D._restore_tier_body_from_overflow(sp, "report_critical_high") is True

    assert (sp / "report_critical_high.md").exists()
    assert _validate_tier_body_against_manifest(sp, "report_critical_high") == []


def test_promotion_receipts_use_verify_filename_not_body_references(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "verify_INV-088.md",
        "# Verification\n\n"
        "**Verdict**: CONFIRMED\n\n"
        "**Finding**: DX-1 is mentioned as an upstream source detail.\n\n"
        "This verifier also discusses EIP-20 compatibility and VS-7 context.\n",
    )

    receipts = D._collect_verify_promotion_receipts(sp)

    assert receipts == {"INV-088"}


def test_promotion_symmetry_matches_zero_padded_excluded_ids(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    _write(
        sp / "verify_INV-79.md",
        "# Verification INV-79\n\n**Verdict**: CONFIRMED\n\nConfirmed duplicate.\n",
    )
    _write(
        sp / "report_index.md",
        "## Master Finding Index\n\n"
        "| Report ID | Title | Severity | Internal Hypothesis |\n"
        "|---|---|---|---|\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Reason |\n"
        "|---|---|\n"
        "| INV-079 | Duplicate of L-01 |\n",
    )
    _write(project / "AUDIT_REPORT.md", "# Audit Report\n\n")

    assert D._check_promotion_symmetry(sp, str(project)) == []


def test_report_assembly_keeps_low_info_h1_shard_sections(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "report_index.md",
        "## Summary Counts\n\n"
        "| Severity | Count |\n|---|---|\n| Low | 2 |\n| Informational | 0 |\n| **Total** | **2** |\n\n"
        "## Master Finding Index\n\n"
        "| Report ID | Title | Severity | Internal Hypothesis |\n"
        "|---|---|---|---|\n"
        "| L-01 | First low | Low | H-1 |\n"
        "| L-24 | Later low | Low | H-24 |\n",
    )
    _write(sp / "report_critical_high.md", "")
    _write(sp / "report_medium.md", "")
    _write(
        sp / "report_low_info.md",
        "# Low Findings\n\n"
        "### [L-01] First low\n\n"
        "**Severity**: Low\n\n**Location**: A.sol:L1\n\n"
        "**Description**: First shard low finding body.\n\n"
        "## Low Findings\n\n"
        "### [L-24] Later low\n\n"
        "**Severity**: Low\n\n**Location**: B.sol:L2\n\n"
        "**Description**: Second shard low finding body.\n",
    )

    assert _assemble_report_python(sp, tmp_path) is True
    report = (tmp_path / "AUDIT_REPORT.md").read_text(encoding="utf-8")
    assert "### [L-01] First low" in report
    assert "### [L-24] Later low" in report


def test_inventory_chunk_structure_rejects_table_only_artifact(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "inventory_chunk_a.manifest.md",
        "# inventory_chunk_a manifest\n\n"
        "| File | Estimated signals |\n"
        "|------|-------------------|\n"
        "| analysis_alpha.md | 1 |\n",
    )
    _write(
        sp / "findings_inventory_chunk_a.md",
        "# Chunk A\n\n"
        "## Source Summary\n\n"
        "| Input File | Raw Findings |\n"
        "|---|---|\n"
        "| analysis_alpha.md | 1 |\n\n"
        "## Master Table\n\n"
        "| # | CC-ID | Source IDs | Title | Severity | Verdict | Location | Preferred Tag |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 1 | CC-01 | ALPHA-1 | Missing detail | High | CONFIRMED | Vault.sol:L1 | [CODE] |\n",
    )

    issues = D._validate_inventory_chunk_structure(sp, "inventory_chunk_a")

    assert any("0 per-finding detail" in issue for issue in issues)
    assert any("Per-Finding Detail" in issue for issue in issues)


def test_direct_later_phases_do_not_receive_generic_delegation_wrapper(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    config = {
        "scratchpad": str(sp),
        "project_root": str(tmp_path),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
        "proven_only": False,
    }
    direct_names = {
        "instantiate",
        "rag_sweep",
        "chain",
        "chain_agent2",
        "sc_verify_crithigh",
        "skeptic",
        "crossbatch",
        "report_index",
        "report_body_writer_critical_high",
        "report_assemble",
    }

    for name in direct_names:
        phase = next(p for p in D.SC_PHASES if p.name == name)
        prompt = build_phase_prompt(
            plamen_home() / "commands" / "plamen.md",
            phase,
            config,
        )

        assert "DIRECT EXECUTION CONTEXT POLICY" in prompt, name
        assert "Do NOT use the Task tool or spawn child agents" in prompt, name
        assert "CONTEXT DELEGATION PROTOCOL" not in prompt, name
        assert "Use the Task tool for parallel subagent work" not in prompt, name


def test_triage_safety_handles_parenthetical_header_suffixes(tmp_path: Path) -> None:
    """v2.8.5: LLM writes Excluded Findings headers with long parenthetical
    explanations. _cell() must still resolve the column."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "verification_queue.md",
        "| Finding ID | Severity | Title | Location | Preferred Tag |\n"
        "|------------|----------|-------|----------|---------------|\n"
        "| H-1 | High | Bug | A.sol:L1 | CODE-TRACE |\n",
    )
    _write(
        sp / "verify_H-1.md",
        "# Verification H-1\n\n**Verdict**: CONFIRMED\n**Severity**: High\n\n" + ("x" * 120),
    )
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Master Finding Index\n\n"
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |\n"
        "|-----------|-------|----------|----------|--------------|------------|---------------------|\n"
        "| H-01 | Bug | High | A.sol:L1 | VERIFIED | - | H-1 |\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Severity | Title | Exclusion Reason (APPENDIX_ONLY / DROP_* / FALSE_POSITIVE / DUPLICATE OF X-NN / MERGE_INTO X-NN only) |\n"
        "|-------------|----------|-------|---------------------------------------------------------------------------------------------------------|\n"
        "| CH-1 | High | Chain X | DROP_FALSE_POSITIVE — verified not exploitable |\n"
        "| INV-41 | Medium | Param issue | DROP_FALSE_POSITIVE — no reproducible path |\n"
        "| INV-43 | Medium | Merge target | MERGE_INTO M-08 |\n",
    )
    # Triage safety is now WARNING-only, so _validate_report_index_inputs won't halt.
    assert _validate_report_index_inputs(sp) == []
    # But the triage safety function itself should still parse correctly
    # (all exclusions are allowed, so no issues).
    issues = _validate_report_index_triage_safety(sp)
    assert issues == [], f"Expected no triage issues but got: {issues}"


def test_triage_safety_still_warns_on_bad_medium_plus_exclusion(tmp_path: Path) -> None:
    """v2.8.5: triage safety still detects genuinely bad exclusions."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Excluded Findings\n\n"
        "| Internal ID | Severity | Title | Exclusion Reason |\n"
        "|-------------|----------|-------|------------------|\n"
        "| H-99 | High | Dangerous | too boring to include |\n",
    )
    issues = _validate_report_index_triage_safety(sp)
    assert len(issues) == 1
    assert "triage safety" in issues[0]


# ── v2.8.5 Fix 1: _validate_inventory_structure threshold ──────────────


def _make_inventory_block(fid: str, *, has_fields: bool = True) -> str:
    fields = (
        "**Source IDs**: CS-1\n**Severity**: Medium\n"
        "**Location**: src/X.sol:L10\n**Evidence Tag**: [CODE]"
    ) if has_fields else "body text only"
    return f"## Finding [{fid}]: Title for {fid}\n{fields}\n"


def test_inventory_structure_40pct_threshold_no_halt(tmp_path: Path) -> None:
    """v2.8.5 Fix 1: 3/10 missing fields → warning, not halt."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    good = [_make_inventory_block(f"F-{i:02d}") for i in range(7)]
    bad = [_make_inventory_block(f"F-{i:02d}", has_fields=False) for i in range(7, 10)]
    _write(sp / "findings_inventory.md", "# Findings Inventory\n\n" + "\n".join(good + bad))
    issues = _validate_inventory_structure(sp)
    assert not any("missing one or more required fields" in i for i in issues), (
        f"3/10 missing should not FAIL after v2.8.5: {issues}"
    )


def test_inventory_structure_over_40pct_still_halts(tmp_path: Path) -> None:
    """v2.8.5 Fix 1: 5/10 missing fields → still FAIL."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    good = [_make_inventory_block(f"F-{i:02d}") for i in range(5)]
    bad = [_make_inventory_block(f"F-{i:02d}", has_fields=False) for i in range(5, 10)]
    _write(sp / "findings_inventory.md", "# Findings Inventory\n\n" + "\n".join(good + bad))
    issues = _validate_inventory_structure(sp)
    assert any("missing one or more required fields" in i for i in issues), (
        f"5/10 missing should still FAIL: {issues}"
    )


# ── v2.8.5 Fix 2: _validate_inventory_chunk_structure regex heading ─────


def test_inventory_chunk_accepts_variant_detail_heading(tmp_path: Path) -> None:
    """v2.8.5 Fix 2: heading variants like '## Detail' are accepted."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "inventory_chunk_a.md",
        "# Inventory Chunk A\n\n"
        "## Detail\n\n"
        "## Finding [F-01]: Some Title\n"
        "**Source IDs**: CS-1\n**Severity**: Medium\n"
        "**Location**: src/X.sol:L10\n**Evidence Tag**: [CODE]\n",
    )
    issues = _validate_inventory_chunk_structure(sp, "inventory_chunk_a")
    assert not any("Per-Finding Detail" in i for i in issues), (
        f"Variant heading '## Detail' should be accepted: {issues}"
    )


def test_inventory_chunk_accepts_per_finding_details_plural(tmp_path: Path) -> None:
    """v2.8.5 Fix 2: '## Per-Finding Details' (plural, hyphenated) is accepted."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "inventory_chunk_a.md",
        "# Inventory Chunk A\n\n"
        "## Per-Finding Details\n\n"
        "## Finding [F-01]: Some Title\n"
        "**Source IDs**: CS-1\n**Severity**: Medium\n"
        "**Location**: src/X.sol:L10\n**Evidence Tag**: [CODE]\n",
    )
    issues = _validate_inventory_chunk_structure(sp, "inventory_chunk_a")
    assert not any("Per-Finding Detail" in i for i in issues), (
        f"Variant heading '## Per-Finding Details' should be accepted: {issues}"
    )


# ── v2.8.5 Fix 3: _validate_report_coverage_accounting substring column ─


def test_coverage_accounting_accepts_verbose_status_header(tmp_path: Path) -> None:
    """v2.8.5 Fix 3: header 'Coverage Status' matches via substring."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "report_coverage.md",
        "# Report Coverage Audit\n\n"
        "## Raw Candidate Ledger\n"
        "| Source File | Candidate | Severity | Coverage Status | Report ID |\n"
        "|------------|-----------|----------|----------------|-----------|\n"
        "| depth_tf.md | TF-1 | Medium | PROMOTED | M-01 |\n"
        "| depth_tf.md | TF-2 | Low | DROP_FALSE_POSITIVE | - |\n",
    )
    _write(
        sp / "report_index.md",
        "# Report Index\n\n"
        "## Master Finding Index\n"
        "| Report ID | Title | Severity | Internal Hypothesis |\n"
        "|-----------|-------|----------|--------------------|\n"
        "| M-01 | Bug | Medium | H-1 |\n",
    )
    issues = _validate_report_coverage_accounting(sp)
    assert not any("status column" in i.lower() for i in issues), (
        f"Substring 'status' in 'Coverage Status' should be detected: {issues}"
    )


# ── v2.8.5 Fixes 5+6: quality gate WARN downgrades ──────────────────────


def _make_minimal_report(sections: list[tuple[str, str]], *, dangling_ref: str = "") -> str:
    """Build a minimal AUDIT_REPORT.md with given (tier_char, title) sections."""
    body = "# Security Audit Report - Test\n\n## Summary\n\n"
    body += "| Severity | Count |\n|----------|-------|\n"
    from collections import Counter
    tier_counter = Counter(t for t, _ in sections)
    for tc in ("C", "H", "M", "L", "I"):
        name = {"C": "Critical", "H": "High", "M": "Medium", "L": "Low", "I": "Informational"}[tc]
        body += f"| {name} | {tier_counter.get(tc, 0)} |\n"
    for i, (tc, title) in enumerate(sections, 1):
        rid = f"{tc}-{i:02d}"
        body += (
            f"\n### [{rid}] {title}\n\n"
            f"**Severity**: Medium\n**Location**: `src/X.sol:L{i * 10}`\n\n"
            f"**Description**:\nThis is a real finding with substantial content "
            f"that exceeds four hundred characters of meaningful text describing "
            f"the vulnerability in detail so that the thin-section check does not "
            f"trigger as a false positive while we test other quality sub-checks.\n\n"
            f"**Impact**:\nFunds at risk.\n\n"
            f"**PoC Result**:\n[POC-PASS] test passed.\n\n"
        )
        if dangling_ref and i == 1:
            body += f"See {dangling_ref} for details.\n\n"
    return body


def _make_report_index(sections: list[tuple[str, str]]) -> str:
    hdr = (
        "# Report Index\n\n## Master Finding Index\n"
        "| Report ID | Title | Severity | Internal Hypothesis |\n"
        "|-----------|-------|----------|--------------------|\n"
    )
    for i, (tc, title) in enumerate(sections, 1):
        rid = f"{tc}-{i:02d}"
        sev = {"C": "Critical", "H": "High", "M": "Medium", "L": "Low", "I": "Informational"}[tc]
        hdr += f"| {rid} | {title} | {sev} | H-{i} |\n"
    hdr += "\n## Tier Assignments\n\n### Medium Tier\n"
    for i, (tc, title) in enumerate(sections, 1):
        hdr += f"- {tc}-{i:02d}: {title}\n"
    return hdr


def test_quality_gate_duplicate_title_location_is_warn_only(tmp_path: Path) -> None:
    """v2.8.5 Fix 5: duplicate title+location → WARN, no pipeline halt."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    pr = tmp_path / "project"
    pr.mkdir()
    secs = [("M", "Same Bug"), ("M", "Same Bug")]
    report = _make_minimal_report(secs)
    report = report.replace("`src/X.sol:L20`", "`src/X.sol:L10`")
    _write(pr / "AUDIT_REPORT.md", report)
    _write(sp / "report_index.md", _make_report_index(secs))
    issues = _run_report_quality_gate(sp, str(pr))
    assert not any("duplicate" in i.lower() for i in issues), (
        f"Duplicate title+location should be WARN, not halt: {issues}"
    )


def test_quality_gate_dangling_crossref_is_warn_only(tmp_path: Path) -> None:
    """v2.8.5 Fix 6: dangling cross-references → WARN, no pipeline halt."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    pr = tmp_path / "project"
    pr.mkdir()
    secs = [("M", "Real Bug")]
    report = _make_minimal_report(secs, dangling_ref="H-99")
    _write(pr / "AUDIT_REPORT.md", report)
    _write(sp / "report_index.md", _make_report_index(secs))
    issues = _run_report_quality_gate(sp, str(pr))
    assert not any("cross" in i.lower() and "reference" in i.lower() for i in issues), (
        f"Dangling cross-ref should be WARN, not halt: {issues}"
    )


# ── v2.8.5 Fix 7: body_assignment_count tolerance ───────────────────────


def test_quality_gate_small_body_shortfall_is_warn(tmp_path: Path) -> None:
    """v2.8.5 Fix 7: shortfall of 1 → WARN, not FAIL."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    pr = tmp_path / "project"
    pr.mkdir()
    secs = [("M", "Bug A"), ("M", "Bug B")]
    report = _make_minimal_report(secs)
    _write(pr / "AUDIT_REPORT.md", report)
    idx = _make_report_index(secs + [("M", "Bug C")])
    _write(sp / "report_index.md", idx)
    issues = _run_report_quality_gate(sp, str(pr))
    assert not any("body count mismatch" in i for i in issues), (
        f"Shortfall of 1 should be within tolerance: {issues}"
    )


def test_quality_gate_large_body_shortfall_still_halts(tmp_path: Path) -> None:
    """v2.8.5 Fix 7: shortfall of 5 on 10 assignments → still FAIL."""
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    pr = tmp_path / "project"
    pr.mkdir()
    secs = [("M", f"Bug {chr(65+i)}") for i in range(5)]
    report = _make_minimal_report(secs)
    _write(pr / "AUDIT_REPORT.md", report)
    idx = _make_report_index(secs + [("M", f"Bug {chr(65+i)}") for i in range(5, 10)])
    _write(sp / "report_index.md", idx)
    issues = _run_report_quality_gate(sp, str(pr))
    assert any("body count mismatch" in i for i in issues), (
        f"Shortfall of 5/10 should still FAIL: {issues}"
    )


def test_resume_inventory_parity_not_checked(tmp_path: Path):
    """v2.8.6: inventory parity must NOT run on resume.

    The merge receipt becomes stale after depth promotion adds blocks.
    Re-checking it on resume causes cascading rewinds of 20+ phases.
    """
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    # Inventory with 10 blocks
    inv = "# Findings Inventory\n\n" + "\n".join(
        f"## Finding [INV-{i:03d}]: Bug {i}\n**Severity**: Medium\n"
        f"**Location**: src/A.sol:L{i}\n**Source IDs**: CS-{i}\n"
        f"**Evidence Tag**: [CODE]\n"
        for i in range(1, 11)
    )
    _write(sp / "findings_inventory.md", inv)
    # Merge receipt says only 5 merged (stale — depth promotion added 5 more)
    _write(
        sp / "inventory_merge_receipt.md",
        "# Inventory Merge Receipt\n\n"
        "**Parsed**: 5\n**Merged**: 5\n**Dedup skipped**: 0\n",
    )
    # Upstream chunk
    _write(
        sp / "inventory_chunk_a.md",
        "# Chunk A\n" + "\n".join(
            f"## Finding [CS-{i}]: Bug {i}\n" for i in range(1, 6)
        ),
    )
    phase = next(p for p in D.SC_PHASES if p.name == "inventory")
    issues = D._resume_phase_contract_issues(sp, str(tmp_path), phase, "thorough")
    parity_issues = [i for i in issues if "mechanical merge receipt mismatch" in i]
    assert parity_issues == [], (
        f"inventory parity should NOT run on resume but got: {parity_issues}"
    )
