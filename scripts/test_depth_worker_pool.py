from __future__ import annotations

import json
import sys
import time
import threading
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402


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


def _fresh(sp: Path) -> None:
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "_audit_started_with_markers.json").write_text("{}", encoding="utf-8")
    (sp / "findings_inventory.md").write_text(
        "# Inventory\n\nNo prior findings.\n", encoding="utf-8"
    )


def _complete(sp: Path, name: str, owner: str) -> None:
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


def test_depth_worker_jobs_are_mode_and_pipeline_aware(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)

    sc_light = D._depth_worker_jobs(sp, {"pipeline": "sc", "mode": "light"})
    assert [job["output"] for job in sc_light] == [
        "depth_token_flow_findings.md",
        "depth_state_trace_findings.md",
        "depth_edge_case_findings.md",
        "depth_external_findings.md",
    ]

    sc_core = D._depth_worker_jobs(sp, {"pipeline": "sc", "mode": "core"})
    outputs = {job["output"] for job in sc_core}
    assert "blind_spot_a_findings.md" in outputs
    assert "validation_sweep_findings.md" in outputs
    assert "design_stress_findings.md" not in outputs

    l1_thorough = D._depth_worker_jobs(sp, {"pipeline": "l1", "mode": "thorough"})
    l1_outputs = {job["output"] for job in l1_thorough}
    assert "depth_consensus_invariant_findings.md" in l1_outputs
    assert "depth_network_surface_findings.md" in l1_outputs
    assert "skill_execution_checklist.md" in l1_outputs


def test_depth_worker_batch_rate_limit_fails_fast(tmp_path: Path, monkeypatch):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    stop_event = threading.Event()
    cancel_called = {"value": False}

    jobs = [
        {
            "agent_id": "depth-token-flow",
            "role": "token_flow",
            "output": "depth_token_flow_findings.md",
            "category": "core",
            "focus": "token flow",
        },
        {
            "agent_id": "depth-state-trace",
            "role": "state_trace",
            "output": "depth_state_trace_findings.md",
            "category": "core",
            "focus": "state trace",
        },
    ]

    def _fake_worker(**kwargs):
        output = kwargs["job"]["output"]
        if output == "depth_token_flow_findings.md":
            return {"output": output, "rc": 1, "status": "rate_limited"}
        stop_event.wait(timeout=10)
        return {"output": output, "rc": -2, "status": "incomplete"}

    def _fake_cancel(pending_futs, executor):
        cancel_called["value"] = True
        stop_event.set()

    monkeypatch.setattr(D, "_run_single_depth_worker_pty", _fake_worker)
    monkeypatch.setattr(D, "_cancel_pending_worker_futures", _fake_cancel)

    started = time.time()
    rc, results = D._run_depth_worker_batch(
        scratchpad=sp,
        project_root=str(tmp_path),
        config={"mode": "light", "language": "evm", "pipeline": "sc"},
        phase=_phase(),
        base_cmd=["claude", "--session-id", "base"],
        env={},
        timeout=1.0,
        quiescence_s=0.0,
        jobs=jobs,
        attempt=1,
        pool_started=time.time(),
        retry_reasons_by_output={},
    )

    assert rc == 1
    assert cancel_called["value"]
    assert any(r.get("status") == "rate_limited" for r in results)
    assert time.time() - started < 2.0


def test_depth_worker_prompt_is_single_artifact_allowlist(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    job = D._depth_worker_jobs(sp, {"pipeline": "sc", "mode": "light"})[0]

    prompt = D._build_depth_worker_prompt(
        job=job,
        scratchpad=sp,
        project_root=str(tmp_path),
        config={"language": "evm", "mode": "light", "pipeline": "sc"},
        attempt=1,
    )

    assert "AGENT_ROW: depth-token-flow" in prompt
    assert "EXPECTED_OUTPUT: depth_token_flow_findings.md" in prompt
    assert "PLAMEN_STATUS: IN_PROGRESS" in prompt
    assert "PLAMEN_STATUS: COMPLETE" in prompt
    assert "do not spawn" in prompt.lower()
    assert "Task(" not in prompt
    assert "run_in_background" not in prompt
    forbidden = [
        "rag_sweep",
        "verification",
        "report_",
        "AUDIT_REPORT.md",
    ]
    hits = [token for token in forbidden if token.lower() in prompt.lower()]
    assert not hits


def test_depth_worker_prompt_makes_perturbation_gate_top_level_contract(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    job = D._depth_worker_jobs(sp, {"pipeline": "sc", "mode": "thorough"})[0]

    prompt = D._build_depth_worker_prompt(
        job=job,
        scratchpad=sp,
        project_root=str(tmp_path),
        config={"language": "evm", "mode": "thorough", "pipeline": "sc"},
        attempt=1,
    )

    assert "hard structural gate" in prompt
    assert "### Perturbation Block - <finding_id>" in prompt
    assert "A separate `perturbation_findings.md`" in prompt
    assert "self-check" in prompt


def test_depth_worker_retry_prompt_repairs_all_perturbation_blocks(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    job = D._depth_worker_jobs(sp, {"pipeline": "sc", "mode": "thorough"})[0]

    prompt = D._build_depth_worker_prompt(
        job=job,
        scratchpad=sp,
        project_root=str(tmp_path),
        config={"language": "evm", "mode": "thorough", "pipeline": "sc"},
        attempt=2,
        retry_reasons=[
            "status=structural_fail",
            "missing perturbation block(s) for Medium+ CONFIRMED finding(s): DT-3",
        ],
    )

    assert "Perturbation Repair Is Mandatory" in prompt
    assert "Repair ALL Medium/Critical/High CONFIRMED findings" in prompt
    assert "If you rename, split, merge, or add" in prompt
    assert "Do not mark the file COMPLETE" in prompt


def test_depth_worker_contract_rejects_cross_row_owner(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    jobs = D._depth_worker_jobs(sp, {"pipeline": "sc", "mode": "light"})
    (sp / "_depth_worker_pool_contract.json").write_text(
        json.dumps({
            "phase": "depth",
            "canonical_outputs": [job["output"] for job in jobs],
            "jobs": jobs,
        }),
        encoding="utf-8",
    )
    _complete(sp, "depth_token_flow_findings.md", "depth-state-trace")

    statuses = {
        row["name"]: row
        for row in D.compute_depth_row_statuses(sp, _phase())
    }

    row = statuses["depth_token_flow_findings.md"]
    assert row["status"] == "structural_fail"
    assert any("PLAMEN_OWNER" in reason for reason in row["reasons"])
    assert any("AGENT_ROW" in reason for reason in row["reasons"])


def test_depth_worker_gate_rejects_missing_perturbation_block(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    _complete(sp, "depth_state_trace_findings.md", "depth-state-trace")
    _complete(sp, "depth_edge_case_findings.md", "depth-edge-case")
    _complete(sp, "depth_external_findings.md", "depth-external")
    (sp / "depth_token_flow_findings.md").write_text(
        "<!-- PLAMEN_ARTIFACT: depth_token_flow_findings.md -->\n"
        "<!-- PLAMEN_OWNER: depth-token-flow -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: depth -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n"
        "<!-- AGENT_ROW: depth-token-flow -->\n"
        "<!-- EXPECTED_OUTPUT: depth_token_flow_findings.md -->\n\n"
        "# Depth Token Flow Findings\n\n"
        "## Finding [DT-1]: Missing comparison\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: Medium\n\n"
        + ("Detailed trace evidence. " * 30)
        + "\n\n<!-- PLAMEN_STATUS: COMPLETE -->\n",
        encoding="utf-8",
    )

    statuses = {
        row["name"]: row
        for row in D.compute_depth_row_statuses(sp, _phase())
    }

    row = statuses["depth_token_flow_findings.md"]
    assert row["status"] == "structural_fail"
    assert any("missing perturbation block" in reason for reason in row["reasons"])


def test_depth_worker_gate_accepts_inline_perturbation_block(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    (sp / "depth_token_flow_findings.md").write_text(
        "<!-- PLAMEN_ARTIFACT: depth_token_flow_findings.md -->\n"
        "<!-- PLAMEN_OWNER: depth-token-flow -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: depth -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n"
        "<!-- AGENT_ROW: depth-token-flow -->\n"
        "<!-- EXPECTED_OUTPUT: depth_token_flow_findings.md -->\n\n"
        "# Depth Token Flow Findings\n\n"
        "## Finding [DT-1]: Missing comparison\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: Medium\n\n"
        + ("Detailed trace evidence. " * 30)
        + "\n\n### Perturbation Block - DT-1\n"
        "| Operator | Target | Result |\n"
        "| --- | --- | --- |\n"
        "| SIBLING | sibling function | Same invariant fails |\n\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n",
        encoding="utf-8",
    )
    _complete(sp, "depth_state_trace_findings.md", "depth-state-trace")
    _complete(sp, "depth_edge_case_findings.md", "depth-edge-case")
    _complete(sp, "depth_external_findings.md", "depth-external")

    statuses = {
        row["name"]: row
        for row in D.compute_depth_row_statuses(sp, _phase())
    }

    assert statuses["depth_token_flow_findings.md"]["status"] == "complete"


def test_depth_worker_pool_runs_only_open_standard_rows(tmp_path: Path, monkeypatch):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    jobs = D._depth_worker_jobs(sp, {"pipeline": "sc", "mode": "light"})
    _complete(sp, "depth_state_trace_findings.md", "depth-state-trace")
    _complete(sp, "depth_edge_case_findings.md", "depth-edge-case")
    _complete(sp, "depth_external_findings.md", "depth-external")
    calls: list[str] = []

    def _fake_worker(**kwargs):
        job = kwargs["job"]
        calls.append(job["output"])
        _complete(sp, job["output"], job["agent_id"])
        return {"output": job["output"], "rc": 0, "status": "complete"}

    monkeypatch.setattr(D, "_run_single_depth_worker_pty", _fake_worker)

    rc = D._run_depth_worker_pool_pty(
        scratchpad=sp,
        project_root=str(tmp_path),
        config={"mode": "light", "language": "evm", "pipeline": "sc"},
        phase=_phase(),
        base_cmd=["claude", "--session-id", "base"],
        env={},
        timeout=1.0,
        quiescence_s=0.0,
        attempt=1,
    )

    assert rc == 0
    assert calls == ["depth_token_flow_findings.md"]


def test_depth_worker_input_snapshot_restores_prior_phase_artifact(tmp_path: Path):
    sp = tmp_path / ".scratchpad"
    _fresh(sp)
    prior = sp / "analysis_percontract_3.md"
    prior.write_text("original prior-phase analysis\n", encoding="utf-8")
    output_names = {"depth_token_flow_findings.md"}

    snapshot = D._snapshot_worker_input_artifacts(sp, output_names)
    prior.write_text("worker-corrupted prior-phase analysis\n", encoding="utf-8")

    restored = D._restore_worker_input_artifacts(sp, snapshot)

    assert restored == ["analysis_percontract_3.md"]
    assert prior.read_text(encoding="utf-8") == "original prior-phase analysis\n"
