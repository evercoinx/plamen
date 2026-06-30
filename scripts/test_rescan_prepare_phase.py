"""rescan_prepare split — mechanical plan + pure worker-pool executor.

Item 4 (Design A) mirrors `inventory_prepare`: a cheap deterministic planning
phase (`rescan_prepare`) writes `rescan_manifest.md` BEFORE any rescan worker
spawns, so the `rescan` phase stays a pure bounded worker-pool executor and
never has to plan-and-execute in one overloaded coordinator on large codebases.

Recall-invariant: the per-worker methodology (phase3b-rescan.md), the EXCLUSION
SOURCE RULE, and the two output families (analysis_rescan_*, analysis_percontract_*)
are untouched. This phase only ENUMERATES the concrete output filenames.

All fixtures are synthetic/neutral (ExampleVault/ExampleToken).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_mechanical as M  # noqa: E402
import plamen_prompt as P  # noqa: E402
import plamen_types as T  # noqa: E402
from plamen_types import Checkpoint, validate_phase_graph  # noqa: E402
from plamen_validators import (  # noqa: E402
    _parse_rescan_manifest_files,
    _rescan_manifest_exact_missing,
    gate_passes,
)

RESCAN_PREPARE = next(p for p in T.SC_PHASES if p.name == "rescan_prepare")
RESCAN = next(p for p in T.SC_PHASES if p.name == "rescan")


def _write_inventory(sp: Path, scoped: list[str], out_of_scope: list[str] | None = None):
    lines = [
        "# Contract Inventory",
        "",
        "| Contract | Path |",
        "|----------|------|",
    ]
    lines += [f"| C | `{p}` |" for p in scoped]
    if out_of_scope:
        lines += ["", "## Out of Scope", "", "| Contract | Path |", "|----------|------|"]
        lines += [f"| X | `{p}` |" for p in out_of_scope]
    (sp / "contract_inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# ensure_rescan_manifest: content + round-trip
# --------------------------------------------------------------------------

def test_manifest_roundtrips_and_yields_percontract_row(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    _write_inventory(sp, ["src/ExampleVault.sol", "src/ExampleToken.sol"])
    path = M.ensure_rescan_manifest(sp, {})
    assert path == sp / "rescan_manifest.md"
    text = path.read_text(encoding="utf-8")

    files = _parse_rescan_manifest_files(text)
    # 2-3 rescan rows + >=1 percontract row.
    rescan_rows = [f for f in files if f.startswith("analysis_rescan_")]
    percontract_rows = [f for f in files if f.startswith("analysis_percontract_")]
    assert 2 <= len(rescan_rows) <= 3, files
    assert len(percontract_rows) >= 1, files
    assert "analysis_percontract_ExampleVault.md" in percontract_rows
    assert "analysis_percontract_ExampleToken.md" in percontract_rows


def test_manifest_never_emits_glob_form(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    _write_inventory(sp, ["src/ExampleVault.sol"])
    text = M.ensure_rescan_manifest(sp, {}).read_text(encoding="utf-8")
    # No glob exemplars anywhere in the emitted manifest.
    assert "analysis_rescan_*.md" not in text
    assert "analysis_percontract_*.md" not in text
    assert "*" not in text


def test_out_of_scope_files_excluded(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    _write_inventory(
        sp,
        ["src/ExampleVault.sol"],
        out_of_scope=["src/IgnoredLib.sol"],
    )
    files = _parse_rescan_manifest_files(
        M.ensure_rescan_manifest(sp, {}).read_text(encoding="utf-8")
    )
    assert "analysis_percontract_ExampleVault.md" in files
    assert "analysis_percontract_IgnoredLib.md" not in files


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------

def test_idempotent_rewrite_is_byte_identical(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    _write_inventory(sp, ["src/ExampleVault.sol", "src/ExampleToken.sol"])
    first = M.ensure_rescan_manifest(sp, {}).read_text(encoding="utf-8")
    # A re-run on an unchanged scratchpad must not change content.
    second = M.ensure_rescan_manifest(sp, {}).read_text(encoding="utf-8")
    assert first == second


# --------------------------------------------------------------------------
# Sparse / empty contract_inventory fallback
# --------------------------------------------------------------------------

def test_empty_inventory_falls_back_to_scope_review(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    # No contract_inventory.md at all.
    files = _parse_rescan_manifest_files(
        M.ensure_rescan_manifest(sp, {}).read_text(encoding="utf-8")
    )
    assert files.count("analysis_percontract_scope_review.md") == 1
    assert not any(
        f.startswith("analysis_percontract_") and f != "analysis_percontract_scope_review.md"
        for f in files
    )


def test_sparse_inventory_no_code_paths_falls_back_to_scope_review(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    # Inventory exists but lists no code-file paths (sparse).
    (sp / "contract_inventory.md").write_text(
        "# Contract Inventory\n\nNo contracts catalogued yet.\n", encoding="utf-8"
    )
    files = _parse_rescan_manifest_files(
        M.ensure_rescan_manifest(sp, {}).read_text(encoding="utf-8")
    )
    assert "analysis_percontract_scope_review.md" in files


# --------------------------------------------------------------------------
# Full gate: manifest + worker outputs satisfy the rescan exact gate
# --------------------------------------------------------------------------

def test_manifest_then_worker_outputs_pass_rescan_gate(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    _write_inventory(sp, ["src/ExampleVault.sol"])
    text = M.ensure_rescan_manifest(sp, {}).read_text(encoding="utf-8")
    declared = _parse_rescan_manifest_files(text)
    # Simulate the worker pool filling each declared output substantively.
    body = "# Rescan\n\n## No new findings\n\n" + ("Checked declared scope. " * 90)
    for name in declared:
        (sp / name).write_text(body, encoding="utf-8")
    assert _rescan_manifest_exact_missing(sp, RESCAN) == []
    passed, missing = gate_passes(sp, str(tmp_path), RESCAN)
    assert passed is True, missing


# --------------------------------------------------------------------------
# Phase wiring
# --------------------------------------------------------------------------

def test_rescan_prepare_is_thorough_only_mechanical():
    assert RESCAN_PREPARE.modes == {"thorough"}
    assert RESCAN_PREPARE.expected_artifacts == ["rescan_manifest.md"]
    assert RESCAN_PREPARE.base_timeout_s == 60
    assert RESCAN_PREPARE.critical is True
    # Not present outside thorough mode.
    core_names = [p.name for p in T.SC_PHASES if "core" in p.modes]
    assert "rescan_prepare" not in core_names


def test_rescan_prepare_runs_before_rescan_in_graph():
    names = [p.name for p in T.SC_PHASES]
    assert names.index("rescan_prepare") < names.index("rescan")


def test_phase_graph_valid_with_rescan_prepare():
    for mode in ("thorough", "core", "light"):
        assert validate_phase_graph(T.SC_PHASES, mode, "sc") == [], mode


def test_rescan_prepare_prompt_is_mechanical_stub():
    hp = P.plamen_home()
    v1 = hp / "commands" / "plamen.md"
    cfg = {
        "language": "evm", "mode": "thorough", "pipeline": "sc",
        "project_root": str(hp), "scratchpad": str(hp / "x"),
    }
    prompt = P.build_phase_prompt(v1, RESCAN_PREPARE, cfg)
    assert "DRIVER-CONTRACT-ERROR: rescan_prepare" in prompt
    assert "RESCAN PREPARE OVERRIDE" in prompt
    # The per-worker rescan methodology must NOT leak into this mechanical stub.
    assert "EXCLUSION SOURCE RULE" not in prompt
    assert P._is_direct_execution_phase("rescan_prepare", "sc") is True
    assert "rescan_prepare" in P._OVERRIDE_SELF_CONTAINED_PHASES


# --------------------------------------------------------------------------
# Checkpoint-resume regression: an OLD thorough checkpoint without
# rescan_prepare in its graph must resume WITHOUT being archived as corrupt.
# --------------------------------------------------------------------------

def test_old_checkpoint_without_rescan_prepare_resumes_clean(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    # An OLD thorough checkpoint: completed recon..rescan, but the phase graph
    # at the time had no rescan_prepare. The new SC_PHASES adds rescan_prepare.
    old = Checkpoint(
        completed=["recon", "instantiate", "breadth", "rescan"],
        degraded=[],
    )
    old.save(sp)

    # Load must NOT raise (no JSON corruption) and must not archive the file.
    reloaded = Checkpoint.load(sp)
    assert not (sp / "_v2_checkpoint.corrupt").exists()
    assert reloaded.completed == ["recon", "instantiate", "breadth", "rescan"]

    # Graph-drift reconciliation: validate_phase_names flags only checkpoint
    # entries NOT in the active graph. A NEW graph phase (rescan_prepare) is a
    # forward addition, never an "unknown" entry — so this returns empty and
    # the resume path does not treat the old checkpoint as corrupt.
    active_names = {p.name for p in T.SC_PHASES if "thorough" in p.modes}
    assert "rescan_prepare" in active_names
    assert reloaded.validate_phase_names(active_names) == []
