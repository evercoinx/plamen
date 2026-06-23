from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
import plamen_mechanical as M  # noqa: E402
from plamen_validators import _validate_recon_content_structure  # noqa: E402


def _cfg(tmp_path: Path, mode: str = "thorough") -> dict:
    project = tmp_path / "project"
    scratch = tmp_path / ".scratchpad"
    project.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    return {
        "project_root": str(project),
        "scratchpad": str(scratch),
        "language": "evm",
        "mode": mode,
        "pipeline": "sc",
    }


def _worker_shard(name: str, role: str, owner: str = "R-test") -> str:
    body = (
        f"<!-- PLAMEN_ARTIFACT: {name} -->\n"
        f"<!-- PLAMEN_OWNER: {owner} -->\n"
        "<!-- PLAMEN_STATUS: IN_PROGRESS -->\n"
        "<!-- PLAMEN_PHASE: recon -->\n"
        "<!-- PLAMEN_VERSION: 1 -->\n"
        f"<!-- RECON_ROLE: {role} -->\n"
        f"<!-- EXPECTED_OUTPUT: {name} -->\n\n"
        f"# Recon Worker {role}\n\n"
        "## Evidence\n\n"
        "Concrete source evidence covers contracts, functions, state variables, "
        "entry points, trust boundaries, build status, static detector status, "
        "required template routing, and downstream audit implications. "
        "This repeated sentence keeps the test artifact safely above gate "
        "minimums without relying on production fixtures. "
        "Concrete source evidence covers contracts, functions, state variables, "
        "entry points, trust boundaries, build status, static detector status, "
        "required template routing, and downstream audit implications.\n\n"
        "## Canonical Merge Hints\n\n"
        "- Inform the canonical recon files for this role.\n\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
    )
    return body


def test_recon_worker_complete_requires_assigned_job_markers(tmp_path: Path):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    job = D._recon_worker_jobs(cfg)[0]
    (scratch / job["output"]).write_text(
        _worker_shard(job["output"], job["role"], owner=job["agent_id"]),
        encoding="utf-8",
    )

    ok, reasons = D._recon_worker_complete(scratch, job["output"], job)
    assert ok, reasons

    wrong_job = dict(job)
    wrong_job["agent_id"] = "wrong-owner"
    ok, reasons = D._recon_worker_complete(scratch, job["output"], wrong_job)
    assert not ok
    assert "missing marker PLAMEN_OWNER: wrong-owner" in reasons


def test_recon_worker_jobs_match_documented_mode_counts(tmp_path: Path):
    assert len(D._recon_worker_jobs(_cfg(tmp_path, "light"))) == 2
    assert len(D._recon_worker_jobs(_cfg(tmp_path, "core"))) == 4
    assert len(D._recon_worker_jobs(_cfg(tmp_path, "thorough"))) == 4


def test_recon_worker_prompt_is_single_output_and_no_later_phase_leak(tmp_path: Path):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    job = D._recon_worker_jobs(cfg)[0]

    prompt = D._build_recon_worker_prompt(
        job=job,
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        attempt=1,
    )

    assert job["output"] in prompt
    assert "spawn_manifest.md" not in prompt
    assert "Use the Task tool" not in prompt
    assert "MUST spawn" not in prompt
    assert "canonical recon files. The driver merges" in prompt
    assert "Write exactly this file and no other scratchpad artifact" in prompt
    assert "## Command Boundary" in prompt
    assert "You may run at most one initial compile command" in prompt
    assert "at most two targeted build-repair" in prompt
    assert "must not run any command matching: `forge test`" in prompt
    assert "Medusa" in prompt
    assert "Do not run tests, PoCs, fuzzers" in prompt


def test_non_build_recon_roles_are_told_not_to_shell(tmp_path: Path):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    job = next(j for j in D._recon_worker_jobs(cfg) if j["role"] == "design_context")

    prompt = D._build_recon_worker_prompt(
        job=job,
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        attempt=1,
    )

    assert "For roles other than `build_static` and `context_static`: do not run shell" in prompt
    assert "Do not write design_context.md directly" in prompt


def test_recon_command_guard_allows_build_but_blocks_later_phase_tools(
    tmp_path: Path,
    monkeypatch,
):
    scratch = tmp_path / ".scratchpad"
    scratch.mkdir()

    def fake_which(name: str, path: str | None = None):
        if name in {"forge", "npx", "npm", "yarn", "git", "slither"}:
            return f"C:/tools/{name}.exe"
        return None

    monkeypatch.setattr(D.shutil, "which", fake_which)
    guarded = D._install_recon_command_guard(scratch, {"PATH": "C:/tools"})
    guard_dir = scratch / "_recon_command_guard"

    assert guarded["PATH"].startswith(str(guard_dir))
    assert guarded["PLAMEN_REAL_FORGE"] == "C:/tools/forge.exe"
    forge = (guard_dir / "forge").read_text(encoding="utf-8")
    assert "concurrent forge build already running" in forge
    assert "install|remappings|config|clean" in forge
    assert "test|coverage|snapshot|script" in forge
    assert "outside recon build-repair allowlist" in forge
    slither = (guard_dir / "slither").read_text(encoding="utf-8")
    assert "slither target/detector runs are not allowed" in slither
    medusa = (guard_dir / "medusa").read_text(encoding="utf-8")
    assert "fuzzer/verification command is not allowed" in medusa


def test_worker_pool_status_has_no_empty_counter_segment():
    status = D._format_worker_pool_progress_status(
        complete=0,
        total=4,
        active_outputs=[
            "recon_build_static.md",
            "recon_design_context.md",
            "recon_inventory_surface.md",
            "recon_templates_patterns.md",
        ],
        queued=0,
        phase_label="recon",
    )

    assert "worker pool:;" not in status
    assert status.startswith("worker pool: 4 running; 0 queued/missing;")
    assert "active recon_build_static, recon_design_context, recon_inventory_surface +1" in status


def test_recon_worker_merge_writes_canonical_gate_outputs(tmp_path: Path):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    for job in D._recon_worker_jobs(cfg):
        (scratch / job["output"]).write_text(
            _worker_shard(job["output"], job["role"]),
            encoding="utf-8",
        )

    written = M._merge_recon_worker_shards(scratch, cfg)

    assert set(written) >= {
        "recon_summary.md",
        "design_context.md",
        "attack_surface.md",
        "template_recommendations.md",
        "build_status.md",
    }
    phase = next(ph for ph in D.SC_PHASES if ph.name == "recon")
    passed, missing = D.gate_passes(scratch, cfg["project_root"], phase)
    assert passed, missing
    hard, soft = _validate_recon_content_structure(scratch)
    assert hard == []
    assert "spawn_manifest.md" not in (scratch / "recon_summary.md").read_text(
        encoding="utf-8"
    )


def test_recon_worker_merge_strips_prepass_overwrite_marker(tmp_path: Path):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    (scratch / "design_context.md").write_text(
        M._PREPASS_MARKER
        + "\n# Design Context\n\n[LLM TO ENRICH] stale pre-pass stub.\n",
        encoding="utf-8",
    )
    for job in D._recon_worker_jobs(cfg):
        (scratch / job["output"]).write_text(
            _worker_shard(job["output"], job["role"]),
            encoding="utf-8",
        )

    M._merge_recon_worker_shards(scratch, cfg)

    merged = (scratch / "design_context.md").read_text(encoding="utf-8")
    assert not merged.startswith(M._PREPASS_MARKER)
    assert "Recon Worker Evidence" in merged


def test_recon_worker_pool_merges_after_last_retry_completes_missing_shard(
    tmp_path: Path,
    monkeypatch,
):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    jobs = D._recon_worker_jobs(cfg)
    missing = jobs[-1]
    for job in jobs[:-1]:
        (scratch / job["output"]).write_text(
            _worker_shard(job["output"], job["role"], owner=job["agent_id"]),
            encoding="utf-8",
        )

    def fake_run_single(**kwargs):
        job = kwargs["job"]
        (scratch / job["output"]).write_text(
            _worker_shard(job["output"], job["role"], owner=job["agent_id"]),
            encoding="utf-8",
        )
        return {
            "output": job["output"],
            "rc": 0,
            "status": "complete",
            "reasons": [],
        }

    monkeypatch.setattr(D, "_run_single_recon_worker_pty", fake_run_single)
    phase = next(ph for ph in D.SC_PHASES if ph.name == "recon")

    rc = D._run_recon_worker_pool_pty(
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        phase=phase,
        base_cmd=[],
        env={},
        timeout=1,
        quiescence_s=0.1,
        attempt=1,
    )

    assert rc == 0
    assert (scratch / missing["output"]).exists()
    assert (scratch / "recon_summary.md").exists()


def test_recon_worker_pool_passes_pool_wide_allowed_outputs(
    tmp_path: Path,
    monkeypatch,
):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    jobs = D._recon_worker_jobs(cfg)
    expected_outputs = {job["output"] for job in jobs}
    seen: list[set[str]] = []

    def fake_run_single(**kwargs):
        job = kwargs["job"]
        seen.append(set(kwargs["allowed_outputs"]))
        assert set(kwargs["allowed_outputs"]) == expected_outputs
        assert D._worker_artifact_name_allowed(
            f"{jobs[-1]['output']}.tmp.24284.0e805d3fed86",
            set(kwargs["allowed_outputs"]),
        )
        (scratch / job["output"]).write_text(
            _worker_shard(job["output"], job["role"], owner=job["agent_id"]),
            encoding="utf-8",
        )
        return {
            "output": job["output"],
            "rc": 0,
            "status": "complete",
            "reasons": [],
        }

    monkeypatch.setattr(D, "_run_single_recon_worker_pty", fake_run_single)
    phase = next(ph for ph in D.SC_PHASES if ph.name == "recon")

    rc = D._run_recon_worker_pool_pty(
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        phase=phase,
        base_cmd=[],
        env={},
        timeout=1,
        quiescence_s=0.1,
        attempt=1,
    )

    assert rc == 0
    assert len(seen) == len(jobs)


def test_recon_worker_pool_protects_preexisting_canonical_inputs(
    tmp_path: Path,
    monkeypatch,
):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    jobs = D._recon_worker_jobs(cfg)
    (scratch / "attack_surface.md").write_text(
        "<!-- plamen-prepass v1: mechanical pre-pass output; safe to overwrite while marker is present -->\n"
        "# Attack Surface\n\n[LLM TO ENRICH]\n",
        encoding="utf-8",
    )
    seen_protected: list[set[str]] = []

    def fake_run_single(**kwargs):
        job = kwargs["job"]
        protected = set(kwargs["protected_input_names"])
        seen_protected.append(protected)
        assert "attack_surface.md" in protected
        (scratch / job["output"]).write_text(
            _worker_shard(job["output"], job["role"], owner=job["agent_id"]),
            encoding="utf-8",
        )
        return {
            "output": job["output"],
            "rc": 0,
            "status": "complete",
            "reasons": [],
        }

    monkeypatch.setattr(D, "_run_single_recon_worker_pty", fake_run_single)
    phase = next(ph for ph in D.SC_PHASES if ph.name == "recon")

    rc = D._run_recon_worker_pool_pty(
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        phase=phase,
        base_cmd=[],
        env={},
        timeout=1,
        quiescence_s=0.1,
        attempt=1,
    )

    assert rc == 0
    assert len(seen_protected) == len(jobs)


def test_recon_worker_pool_partial_merges_when_retry_budget_exhausted(
    tmp_path: Path,
    monkeypatch,
):
    # 2 of 4 shards complete on disk; the remaining 2 never reach COMPLETE,
    # so the retry budget exhausts. The haltless tail must still partial-merge
    # the completed shards, pass the canonical gate, and return 0 (not -2).
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    jobs = D._recon_worker_jobs(cfg)
    completed = jobs[:2]
    stuck = jobs[2:]
    for job in completed:
        (scratch / job["output"]).write_text(
            _worker_shard(job["output"], job["role"], owner=job["agent_id"]),
            encoding="utf-8",
        )

    def fake_run_single(**kwargs):
        # Stuck workers never produce their output; status stays incomplete so
        # the worker pool cannot finalize via the all-complete branches.
        job = kwargs["job"]
        return {
            "output": job["output"],
            "rc": -2,
            "status": "incomplete",
            "reasons": ["never reached COMPLETE"],
        }

    monkeypatch.setattr(D, "_run_single_recon_worker_pty", fake_run_single)
    phase = next(ph for ph in D.SC_PHASES if ph.name == "recon")

    rc = D._run_recon_worker_pool_pty(
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        phase=phase,
        base_cmd=[],
        env={},
        timeout=1,
        quiescence_s=0.1,
        attempt=1,
    )

    assert rc == 0
    # The 2 stuck shards are still absent; partial merge happened anyway.
    for job in stuck:
        assert not (scratch / job["output"]).exists()
    assert (scratch / "recon_summary.md").exists()
    assert (scratch / "design_context.md").exists()
    passed, missing = D.gate_passes(scratch, cfg["project_root"], phase)
    assert passed, missing


def test_recon_worker_timeout_uses_full_scaled_budget_not_2400_cap(
    tmp_path: Path,
    monkeypatch,
):
    # The per-worker timeout must equal max(900, scaled) — no 2400 cap — so a
    # large scaled budget reaches the worker, and a tiny scaled budget floors
    # at 900 (parity with breadth/rescan/depth).
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    jobs = D._recon_worker_jobs(cfg)
    seen_timeouts: list[float] = []

    def make_fake(record: list[float]):
        def fake_run_single(**kwargs):
            record.append(kwargs["timeout"])
            job = kwargs["job"]
            (scratch / job["output"]).write_text(
                _worker_shard(job["output"], job["role"], owner=job["agent_id"]),
                encoding="utf-8",
            )
            return {
                "output": job["output"],
                "rc": 0,
                "status": "complete",
                "reasons": [],
            }

        return fake_run_single

    phase = next(ph for ph in D.SC_PHASES if ph.name == "recon")

    large = 9000.0
    monkeypatch.setattr(D, "_run_single_recon_worker_pty", make_fake(seen_timeouts))
    rc = D._run_recon_worker_pool_pty(
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        phase=phase,
        base_cmd=[],
        env={},
        timeout=large,
        quiescence_s=0.1,
        attempt=1,
    )
    assert rc == 0
    assert seen_timeouts, "expected at least one worker invocation"
    # No 2400 cap: a large scaled budget passes through verbatim.
    assert all(t == large for t in seen_timeouts)
    assert all(t > 2400 for t in seen_timeouts)

    # Reset for the floor case.
    for job in jobs:
        (scratch / job["output"]).unlink(missing_ok=True)
    tiny_timeouts: list[float] = []
    monkeypatch.setattr(D, "_run_single_recon_worker_pty", make_fake(tiny_timeouts))
    rc = D._run_recon_worker_pool_pty(
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        phase=phase,
        base_cmd=[],
        env={},
        timeout=5,
        quiescence_s=0.1,
        attempt=1,
    )
    assert rc == 0
    assert tiny_timeouts
    # Tiny scaled budget floors at 900.
    assert all(t == 900 for t in tiny_timeouts)


def test_recon_inventory_surface_prompt_builds_on_mechanical_no_reenumeration(
    tmp_path: Path,
):
    cfg = _cfg(tmp_path, "thorough")
    scratch = Path(cfg["scratchpad"])
    job = next(
        j for j in D._recon_worker_jobs(cfg) if j["role"] == "inventory_surface"
    )

    prompt = D._build_recon_worker_prompt(
        job=job,
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        attempt=1,
    )

    # The three mechanical enumeration filenames appear in readable-inputs.
    assert "contract_inventory.md" in prompt
    assert "function_list.md" in prompt
    assert "state_variables.md" in prompt
    # Enumeration Gaps recall guard + generic-mechanism tokens.
    assert "Enumeration Gaps" in prompt
    assert "inline assembly" in prompt
    assert "delegatecall" in prompt
    assert "fallback()/receive()" in prompt
    # No longer instructs full source re-enumeration.
    assert "DO NOT re-enumerate" in prompt


def test_depth_worker_pool_finalizes_when_last_attempt_completes_rows(
    tmp_path: Path,
    monkeypatch,
):
    cfg = _cfg(tmp_path, "core")
    cfg["pty_continuation_budget"] = 1
    scratch = Path(cfg["scratchpad"])
    job = {
        "agent_id": "depth-token-flow",
        "role": "token_flow",
        "output": "depth_token_flow_findings.md",
        "category": "standard",
        "focus": "token flow",
    }
    open_sequence = [[job], [], []]

    monkeypatch.setattr(D, "_depth_worker_jobs", lambda sp, config: [job])
    monkeypatch.setattr(
        D,
        "_depth_open_jobs",
        lambda sp, phase, jobs: open_sequence.pop(0) if open_sequence else [],
    )
    monkeypatch.setattr(
        D,
        "_run_depth_worker_batch",
        lambda **kwargs: (0, [{"output": job["output"], "status": "complete"}]),
    )
    monkeypatch.setattr(D, "_synthesize_depth_lifecycle_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(D, "_depth_da_job_if_required", lambda sp, config: [])
    monkeypatch.setattr(D, "gate_passes", lambda sp, root, phase: (True, []))

    rc = D._run_depth_worker_pool_pty(
        scratchpad=scratch,
        project_root=cfg["project_root"],
        config=cfg,
        phase=next(ph for ph in D.SC_PHASES if ph.name == "depth"),
        base_cmd=[],
        env={},
        timeout=1,
        quiescence_s=0.1,
        attempt=1,
    )

    assert rc == 0
