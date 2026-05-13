"""v2.6.3 mode-awareness regression tests.

Locks the fix for mode-blind depth gate enforcement that caused Core/Light
L1 audits to fail on Thorough-only artifact requirements.

Run: python test_v2_6_3_mode_awareness.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_types as T  # noqa: E402
import plamen_validators as V  # noqa: E402

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


# ---------- plamen_types: l1_never_cut_groups ----------

def test_l1_never_cut_groups_light():
    groups = T.l1_never_cut_groups("light")
    names = [g[0] for g in groups]
    check(
        "L1_NCG.light-has-base",
        "depth_consensus_invariant_findings.md" in names
        and "depth_edge_case_findings.md" in names,
        f"got: {names}",
    )
    all_names_flat = [n for g in groups for n in g]
    check(
        "L1_NCG.light-no-confidence",
        "confidence_scores.md" not in all_names_flat,
        f"Light mode should NOT require confidence_scores.md: {all_names_flat}",
    )
    check(
        "L1_NCG.light-no-thorough",
        "design_stress_findings.md" not in all_names_flat
        and "perturbation_findings.md" not in all_names_flat
        and "skill_execution_gaps.md" not in all_names_flat,
        f"got thorough artifacts in light: {all_names_flat}",
    )


def test_l1_never_cut_groups_core():
    groups = T.l1_never_cut_groups("core")
    names = [g[0] for g in groups]
    check(
        "L1_NCG.core-has-base",
        "depth_consensus_invariant_findings.md" in names
        and "depth_network_surface_findings.md" in names
        and "confidence_scores.md" in names,
        f"got: {names}",
    )
    all_names_flat = [n for g in groups for n in g]
    check(
        "L1_NCG.core-no-thorough",
        "design_stress_findings.md" not in all_names_flat
        and "perturbation_findings.md" not in all_names_flat
        and "skill_execution_gaps.md" not in all_names_flat,
        f"got thorough artifacts in core: {all_names_flat}",
    )


def test_l1_never_cut_groups_thorough():
    groups = T.l1_never_cut_groups("thorough")
    all_names_flat = [n for g in groups for n in g]
    check(
        "L1_NCG.thorough-has-all",
        "depth_consensus_invariant_findings.md" in all_names_flat
        and "design_stress_findings.md" in all_names_flat
        and "perturbation_findings.md" in all_names_flat
        and "skill_execution_gaps.md" in all_names_flat
        and "confidence_scores.md" in all_names_flat,
        f"missing artifacts in thorough: {all_names_flat}",
    )


def test_l1_groups_count_matches_sc_pattern():
    """L1 base + thorough should equal the flat L1_NEVER_CUT_ARTIFACT_GROUPS."""
    flat_count = len(T.L1_NEVER_CUT_ARTIFACT_GROUPS)
    thorough_count = len(T.l1_never_cut_groups("thorough"))
    check(
        "L1_NCG.thorough-count-matches-flat",
        flat_count == thorough_count,
        f"flat={flat_count} thorough={thorough_count}",
    )


# ---------- plamen_types: sc_never_cut_groups parity ----------

def test_sc_never_cut_groups_core_no_thorough():
    groups = T.sc_never_cut_groups("core")
    all_names_flat = [n for g in groups for n in g]
    check(
        "SC_NCG.core-no-thorough",
        "design_stress_findings.md" not in all_names_flat
        and "perturbation_findings.md" not in all_names_flat
        and "skill_execution_gaps.md" not in all_names_flat,
        f"thorough artifacts leaked into SC core: {all_names_flat}",
    )


# ---------- plamen_validators: _assert_never_cut_checkpoint ----------

def test_checkpoint_core_skips_thorough_labels():
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td)
        (sp / "never_cut_checkpoint.md").write_text(
            "# Never-Cut Checkpoint\n\n"
            "- depth-consensus-invariant: SPAWNED\n"
            "- depth-network-surface: SPAWNED\n"
            "- depth-state-trace: SPAWNED\n"
            "- depth-external: SPAWNED\n"
            "- depth-edge-case: SPAWNED\n"
            "- confidence-scoring: SPAWNED\n",
            encoding="utf-8",
        )
        issues_core = V._assert_never_cut_checkpoint(sp, "core")
        issues_thorough = V._assert_never_cut_checkpoint(sp, "thorough")
        check(
            "CHECKPOINT.core-passes-without-thorough-labels",
            len(issues_core) == 0,
            f"core issues: {issues_core}",
        )
        check(
            "CHECKPOINT.thorough-fails-missing-thorough-labels",
            len(issues_thorough) > 0
            and any("design-stress" in i or "perturbation" in i or "skill-execution" in i
                     for i in issues_thorough),
            f"thorough issues: {issues_thorough}",
        )


def test_checkpoint_thorough_passes_with_all_labels():
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td)
        (sp / "never_cut_checkpoint.md").write_text(
            "# Never-Cut Checkpoint\n\n"
            "- depth-consensus-invariant: SPAWNED\n"
            "- depth-network-surface: SPAWNED\n"
            "- depth-state-trace: SPAWNED\n"
            "- depth-external: SPAWNED\n"
            "- depth-edge-case: SPAWNED\n"
            "- confidence-scoring: SPAWNED\n"
            "- design-stress: SPAWNED\n"
            "- perturbation: SPAWNED\n"
            "- skill-execution-checklist: SPAWNED\n",
            encoding="utf-8",
        )
        issues = V._assert_never_cut_checkpoint(sp, "thorough")
        check(
            "CHECKPOINT.thorough-passes-all-labels",
            len(issues) == 0,
            f"issues: {issues}",
        )


# ---------- plamen_validators: _assert_never_cut_artifacts ----------

def test_never_cut_artifacts_core_mode():
    """Core mode should not require design_stress/perturbation/skill_execution."""
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td)
        for f in [
            "depth_consensus_invariant_findings.md",
            "depth_network_surface_findings.md",
            "depth_state_trace_findings.md",
            "depth_external_findings.md",
            "depth_edge_case_findings.md",
            "confidence_scores.md",
        ]:
            (sp / f).write_text("# Findings\n\nSome content here.\n", encoding="utf-8")
        issues = V._assert_never_cut_artifacts(sp, T.l1_never_cut_groups("core"))
        check(
            "ARTIFACTS.core-passes-base-only",
            len(issues) == 0,
            f"issues: {issues}",
        )


def test_never_cut_artifacts_thorough_requires_extras():
    """Thorough mode should fail when design_stress etc are missing."""
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td)
        for f in [
            "depth_consensus_invariant_findings.md",
            "depth_network_surface_findings.md",
            "depth_state_trace_findings.md",
            "depth_external_findings.md",
            "depth_edge_case_findings.md",
            "confidence_scores.md",
        ]:
            (sp / f).write_text("# Findings\n\nSome content here.\n", encoding="utf-8")
        issues = V._assert_never_cut_artifacts(sp, T.l1_never_cut_groups("thorough"))
        check(
            "ARTIFACTS.thorough-fails-missing-extras",
            len(issues) > 0,
            f"expected failures for design_stress/perturbation/skill_execution, got: {issues}",
        )


# ---------- all tests ----------

TESTS = [
    test_l1_never_cut_groups_light,
    test_l1_never_cut_groups_core,
    test_l1_never_cut_groups_thorough,
    test_l1_groups_count_matches_sc_pattern,
    test_sc_never_cut_groups_core_no_thorough,
    test_checkpoint_core_skips_thorough_labels,
    test_checkpoint_thorough_passes_with_all_labels,
    test_never_cut_artifacts_core_mode,
    test_never_cut_artifacts_thorough_requires_extras,
]


def main() -> int:
    print(f"Running {len(TESTS)} v2.6.3 mode-awareness tests...")
    for t in TESTS:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as exc:
            global FAIL
            FAIL += 1
            print(f"  CRASH {t.__name__} :: {exc!r}")
    print(f"\n{'=' * 64}")
    print(f"  PASS: {PASS}   FAIL: {FAIL}")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
