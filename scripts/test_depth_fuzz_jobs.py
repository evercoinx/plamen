"""Tests for the Thorough-only depth fuzz sidecar worker rewire.

Covers the spawn/skip matrix (mode x ecosystem x build_status tool flags),
resume idempotency, degrade-continue completion semantics, and the
non-blocking guarantee (fuzz artifacts are not in the depth hard gate nor in
sc_never_cut_groups).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_types import sc_never_cut_groups  # noqa: E402


def _fresh(sp: Path) -> None:
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "_audit_started_with_markers.json").write_text("{}", encoding="utf-8")
    (sp / "findings_inventory.md").write_text(
        "# Inventory\n\nNo prior findings.\n", encoding="utf-8"
    )


def _write_build_status(sp: Path, body: str) -> None:
    (sp / "build_status.md").write_text(body, encoding="utf-8")


def _complete_standard(sp: Path, name: str, owner: str) -> None:
    (sp / name).write_text(
        f"<!-- PLAMEN_ARTIFACT: {name} -->\n"
        f"<!-- PLAMEN_OWNER: {owner} -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: depth -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n"
        f"<!-- AGENT_ROW: {owner} -->\n"
        f"<!-- EXPECTED_OUTPUT: {name} -->\n\n"
        f"# Depth Output: {name}\n\n"
        "## No Findings\n\n"
        + ("No exploitable issue was found for this assigned depth scope. " * 14)
        + "\n\n"
        "## Semantic Proof Checks\n\nNo reportable candidates required proof.\n\n"
        "## Graph Artifact Consumption\n\n"
        "- [GRAPH-ARTIFACT: UNAVAILABLE:caller_map.md] - absent\n"
        "- [GRAPH-ARTIFACT: UNAVAILABLE:callee_map.md] - absent\n"
        "- [GRAPH-ARTIFACT: UNAVAILABLE:state_write_map.md] - absent\n"
        "- [GRAPH-ARTIFACT: UNAVAILABLE:function_summary.md] - absent\n\n"
        "## Chain Summary\n\n"
        "| Finding ID | Postconditions Created | Preconditions Required | Cross-Domain Dependencies |\n"
        "| --- | --- | --- | --- |\n"
        "| none | none | none | none |\n\n"
        "<!-- PLAMEN_FINDINGS_COUNT: 0 -->\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n",
        encoding="utf-8",
    )


def _phase() -> D.Phase:
    return D.Phase(
        name="depth",
        section_markers=["Phase 4b"],
        expected_artifacts=["depth_*_findings.md"],
        base_timeout_s=60,
        min_artifact_bytes=200,
        min_artifacts_count=4,
        example_tokens=["token_flow", "state_trace", "edge_case", "external"],
    )


def _outputs(jobs):
    return [j["output"] for j in jobs]


# ──────────────────────────────────────────────────────────────────
# _depth_fuzz_jobs_if_required matrix
# ──────────────────────────────────────────────────────────────────


def test_fuzz_jobs_empty_for_non_thorough(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    for mode in ("light", "core"):
        cfg = {"pipeline": "sc", "mode": mode, "language": "evm"}
        assert D._depth_fuzz_jobs_if_required(sp, cfg) == []


def test_fuzz_jobs_empty_for_l1(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    cfg = {"pipeline": "l1", "mode": "thorough", "language": "go"}
    assert D._depth_fuzz_jobs_if_required(sp, cfg) == []


def test_fuzz_jobs_empty_for_aptos(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    cfg = {"pipeline": "sc", "mode": "thorough", "language": "aptos"}
    assert D._depth_fuzz_jobs_if_required(sp, cfg) == []


def test_fuzz_jobs_invariant_only_for_solana(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _write_build_status(sp, "trident_available: false\n")
    cfg = {"pipeline": "sc", "mode": "thorough", "language": "solana"}
    jobs = D._depth_fuzz_jobs_if_required(sp, cfg)
    assert _outputs(jobs) == ["invariant_fuzz_results.md"]
    assert jobs[0]["category"] == "fuzz"


def test_fuzz_jobs_invariant_only_for_soroban(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    cfg = {"pipeline": "sc", "mode": "thorough", "language": "soroban"}
    jobs = D._depth_fuzz_jobs_if_required(sp, cfg)
    assert _outputs(jobs) == ["invariant_fuzz_results.md"]


def test_fuzz_jobs_invariant_only_for_sui(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    cfg = {"pipeline": "sc", "mode": "thorough", "language": "sui"}
    jobs = D._depth_fuzz_jobs_if_required(sp, cfg)
    assert _outputs(jobs) == ["invariant_fuzz_results.md"]


def test_fuzz_jobs_evm_with_medusa_available(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _write_build_status(sp, "MEDUSA_AVAILABLE: true\n")
    cfg = {"pipeline": "sc", "mode": "thorough", "language": "evm"}
    jobs = D._depth_fuzz_jobs_if_required(sp, cfg)
    assert _outputs(jobs) == [
        "invariant_fuzz_results.md",
        "medusa_fuzz_findings.md",
    ]
    assert all(j["category"] == "fuzz" for j in jobs)


def test_fuzz_jobs_evm_without_medusa(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _write_build_status(sp, "MEDUSA_AVAILABLE: false\n")
    cfg = {"pipeline": "sc", "mode": "thorough", "language": "evm"}
    jobs = D._depth_fuzz_jobs_if_required(sp, cfg)
    # invariant always emitted (forge has no flag gate); medusa skipped (no fallback)
    assert _outputs(jobs) == ["invariant_fuzz_results.md"]


def test_fuzz_jobs_evm_medusa_flag_missing_means_skip(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    # No build_status.md at all -> medusa flag absent -> medusa job not emitted,
    # but invariant job still emitted so the skip is logged not silent.
    cfg = {"pipeline": "sc", "mode": "thorough", "language": "evm"}
    jobs = D._depth_fuzz_jobs_if_required(sp, cfg)
    assert _outputs(jobs) == ["invariant_fuzz_results.md"]


# ──────────────────────────────────────────────────────────────────
# _read_build_status_flag tolerance
# ──────────────────────────────────────────────────────────────────


def test_read_build_status_flag_tolerant_forms(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _write_build_status(
        sp,
        "# Build\n"
        "- **MEDUSA_AVAILABLE**: true (v1.0)\n"
        "trident_available = false\n"
        "> cargo_fuzz_available: yes\n",
    )
    assert D._read_build_status_flag(sp, "MEDUSA_AVAILABLE") is True
    assert D._read_build_status_flag(sp, "trident_available") is False
    assert D._read_build_status_flag(sp, "cargo_fuzz_available") is True
    assert D._read_build_status_flag(sp, "NOT_PRESENT") is None


def test_read_build_status_flag_missing_file(tmp_path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir(parents=True, exist_ok=True)
    assert D._read_build_status_flag(sp, "MEDUSA_AVAILABLE") is None


# ──────────────────────────────────────────────────────────────────
# _depth_worker_jobs scheduling + resume idempotency
# ──────────────────────────────────────────────────────────────────


def test_depth_worker_jobs_includes_fuzz_only_in_thorough_evm(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _write_build_status(sp, "MEDUSA_AVAILABLE: true\n")

    core = D._depth_worker_jobs(sp, {"pipeline": "sc", "mode": "core", "language": "evm"})
    assert "invariant_fuzz_results.md" not in _outputs(core)
    assert "medusa_fuzz_findings.md" not in _outputs(core)

    thorough = D._depth_worker_jobs(
        sp, {"pipeline": "sc", "mode": "thorough", "language": "evm"}
    )
    outs = _outputs(thorough)
    assert "invariant_fuzz_results.md" in outs
    assert "medusa_fuzz_findings.md" in outs


def test_depth_worker_jobs_fuzz_absent_for_aptos_and_l1(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    aptos = D._depth_worker_jobs(
        sp, {"pipeline": "sc", "mode": "thorough", "language": "aptos"}
    )
    assert "invariant_fuzz_results.md" not in _outputs(aptos)

    l1 = D._depth_worker_jobs(
        sp, {"pipeline": "l1", "mode": "thorough", "language": "go"}
    )
    assert "invariant_fuzz_results.md" not in _outputs(l1)
    assert "medusa_fuzz_findings.md" not in _outputs(l1)


def test_depth_worker_jobs_no_duplicate_fuzz_on_recall(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _write_build_status(sp, "MEDUSA_AVAILABLE: true\n")
    cfg = {"pipeline": "sc", "mode": "thorough", "language": "evm"}
    first = _outputs(D._depth_worker_jobs(sp, cfg))
    second = _outputs(D._depth_worker_jobs(sp, cfg))
    # deterministic + no duplicate fuzz rows on re-call (resume safety)
    assert first == second
    assert first.count("invariant_fuzz_results.md") == 1
    assert first.count("medusa_fuzz_findings.md") == 1


# ──────────────────────────────────────────────────────────────────
# Degrade-continue completion semantics + non-blocking guarantee
# ──────────────────────────────────────────────────────────────────


def test_fuzz_degrade_artifact_completes_job(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    phase = _phase()
    job = {
        "agent_id": "invariant-fuzz",
        "role": "invariant_fuzz",
        "output": "invariant_fuzz_results.md",
        "category": "fuzz",
        "focus": "invariant fuzz",
    }
    (sp / "invariant_fuzz_results.md").write_text(
        "<!-- PLAMEN_ARTIFACT: invariant_fuzz_results.md -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n\n"
        "# Invariant Fuzz Results\n\n"
        "## Result Status: TOOL_UNAVAILABLE\n"
        "Reason: fuzzer not installed.\n\n"
        + ("Degrade-continue artifact, no findings produced. " * 12)
        + "\n\n<!-- PLAMEN_STATUS: COMPLETE -->\n",
        encoding="utf-8",
    )
    assert D._depth_worker_output_complete(sp, phase, job) is True


def test_fuzz_missing_artifact_does_not_fail_depth_gate(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    phase = _phase()
    # Write the 4 standard depth artifacts that satisfy the hard gate; no fuzz.
    for name, owner in [
        ("depth_token_flow_findings.md", "depth-token-flow"),
        ("depth_state_trace_findings.md", "depth-state-trace"),
        ("depth_edge_case_findings.md", "depth-edge-case"),
        ("depth_external_findings.md", "depth-external"),
    ]:
        _complete_standard(sp, name, owner)
    passed, missing = D.gate_passes(sp, str(tmp_path), phase)
    assert passed, f"depth gate failed without fuzz artifacts: {missing}"


def test_fuzz_outputs_not_in_sc_never_cut_groups():
    groups = sc_never_cut_groups("thorough")
    flat = set()
    for grp in groups:
        # groups may be tuples/lists of alternative filenames
        if isinstance(grp, (list, tuple, set)):
            for item in grp:
                flat.add(str(item))
        else:
            flat.add(str(grp))
    assert "invariant_fuzz_results.md" not in flat
    assert "medusa_fuzz_findings.md" not in flat


def test_stub_missing_fuzz_outputs_on_exhaustion(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    phase = _phase()
    jobs = [
        {
            "agent_id": "depth-token-flow",
            "role": "token_flow",
            "output": "depth_token_flow_findings.md",
            "category": "standard",
            "focus": "tf",
        },
        {
            "agent_id": "invariant-fuzz",
            "role": "invariant_fuzz",
            "output": "invariant_fuzz_results.md",
            "category": "fuzz",
            "focus": "inv",
        },
    ]
    wrote = D._stub_missing_fuzz_outputs_on_exhaustion(sp, phase, jobs)
    assert wrote is True
    # fuzz output now exists and is complete
    assert (sp / "invariant_fuzz_results.md").exists()
    fuzz_job = jobs[1]
    assert D._depth_worker_output_complete(sp, phase, fuzz_job) is True
    text = (sp / "invariant_fuzz_results.md").read_text(encoding="utf-8")
    assert "## Result Status: TOOL_UNAVAILABLE" in text
    assert "<!-- PLAMEN_STATUS: COMPLETE -->" in text
    # standard job was NOT stubbed (only fuzz jobs get stub-on-exhaustion)
    assert not (sp / "depth_token_flow_findings.md").exists()


# ──────────────────────────────────────────────────────────────────
# Worker prompt fuzz branch
# ──────────────────────────────────────────────────────────────────


def test_fuzz_worker_prompt_points_at_canonical_and_forbids_spawn(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _write_build_status(sp, "trident_available: true\n")
    job = {
        "agent_id": "invariant-fuzz",
        "role": "invariant_fuzz",
        "output": "invariant_fuzz_results.md",
        "category": "fuzz",
        "focus": "invariant fuzz",
    }
    prompt = D._build_depth_worker_prompt(
        job=job,
        scratchpad=sp,
        project_root=str(tmp_path),
        config={"language": "solana", "mode": "thorough", "pipeline": "sc"},
        attempt=1,
    )
    assert "prompts/solana/v2/phase4b-invariant-fuzz.md" in prompt
    assert "Result Status:" in prompt
    assert "do NOT spawn" in prompt.lower() or "do not spawn" in prompt.lower()
    # Single output allowlist preserved
    assert "invariant_fuzz_results.md" in prompt


def test_medusa_worker_prompt_points_at_medusa_canonical(tmp_path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _write_build_status(sp, "MEDUSA_AVAILABLE: true\n")
    job = {
        "agent_id": "medusa-fuzz",
        "role": "medusa_fuzz",
        "output": "medusa_fuzz_findings.md",
        "category": "fuzz",
        "focus": "medusa fuzz",
    }
    prompt = D._build_depth_worker_prompt(
        job=job,
        scratchpad=sp,
        project_root=str(tmp_path),
        config={"language": "evm", "mode": "thorough", "pipeline": "sc"},
        attempt=1,
    )
    assert "prompts/evm/v2/phase4b-medusa-fuzz.md" in prompt
    assert "MEDUSA_AVAILABLE" in prompt
