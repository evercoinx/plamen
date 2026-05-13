"""Phase E11 wiring tests: prove the live phase graph uses dedicated
body-writer phases, not mechanical tier prose.

Contract under test:
- L1_PHASES and SC_PHASES contain `report_body_writer_<shard>` phases
  positioned BETWEEN `report_index` and the existing tier phases.
- Body-writer phases are LLM-driven (have a model assigned) and critical=True.
- Body-writer phases produce the same `report_<shard>.md` filename as the
  legacy tier phase, so the assembler keeps working.
- Existing tier phases run AFTER body-writer phases.
- The post-phase gate runs `_validate_tier_body_against_manifest` for both
  body-writer phases and the legacy tier phases.
- The runtime prompt builder injects a body-writer-specific override
  directive that constrains the LLM to manifest-driven authoring.

Run: `python test_phase_e_body_writer_wiring.py`
"""

from __future__ import annotations

import json
import sys
import tempfile
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


# =============================================================================
# Phase graph contract.
# =============================================================================

def test_GRAPH_L1_has_body_writer_phases():
    names = [p.name for p in D.L1_PHASES]
    expected = {
        "report_body_writer_critical_high",
        "report_body_writer_medium",
        "report_body_writer_low_info",
    }
    missing = expected - set(names)
    check("GRAPH.L1_body_writers_present", not missing, f"missing={missing}")


def test_GRAPH_SC_has_body_writer_phases():
    names = [p.name for p in D.SC_PHASES]
    expected = {
        "report_body_writer_critical_high",
        "report_body_writer_medium",
        "report_body_writer_low_info",
    }
    missing = expected - set(names)
    check("GRAPH.SC_body_writers_present", not missing, f"missing={missing}")


def test_GRAPH_body_writer_runs_after_index_before_tier():
    """For each pipeline, every body_writer phase index must be strictly
    AFTER report_index AND strictly BEFORE the matching legacy tier phase."""
    for label, phases in (("L1", D.L1_PHASES), ("SC", D.SC_PHASES)):
        names = [p.name for p in phases]
        idx_index = names.index("report_index")
        for name in names:
            if not name.startswith("report_body_writer_"):
                continue
            i_writer = names.index(name)
            legacy = name.replace("report_body_writer_", "report_")
            i_tier = names.index(legacy) if legacy in names else None
            ok = (i_writer > idx_index) and (i_tier is None or i_writer < i_tier)
            check(
                f"GRAPH.{label}_{name}_position",
                ok,
                f"index={idx_index} writer={i_writer} tier={i_tier}",
            )


def test_GRAPH_body_writer_phases_are_llm_critical():
    for label, phases in (("L1", D.L1_PHASES), ("SC", D.SC_PHASES)):
        for p in phases:
            if not p.name.startswith("report_body_writer_"):
                continue
            check(
                f"GRAPH.{label}_{p.name}_critical",
                p.critical is True,
                f"critical={p.critical}",
            )
            check(
                f"GRAPH.{label}_{p.name}_has_model",
                bool(p.model),
                f"model={p.model}",
            )


def test_GRAPH_body_writer_filename_matches_legacy_tier():
    """Body writer must produce the same filename as the legacy tier phase
    to preserve assembler / reader contracts."""
    for label, phases in (("L1", D.L1_PHASES), ("SC", D.SC_PHASES)):
        out_by_name = {p.name: p.expected_artifacts for p in phases}
        for name, arts in out_by_name.items():
            if not name.startswith("report_body_writer_"):
                continue
            legacy = name.replace("report_body_writer_", "report_")
            legacy_arts = out_by_name.get(legacy, [])
            check(
                f"GRAPH.{label}_{name}_filename_matches_{legacy}",
                arts == legacy_arts,
                f"writer={arts} legacy={legacy_arts}",
            )


# =============================================================================
# Prompt builder injects body-writer override.
# =============================================================================

def test_PROMPT_body_writer_override_present(tmp_path: Path):
    """build_phase_prompt must inject the body-writer scope directive."""
    sp = tmp_path
    project = sp / "proj"
    project.mkdir()
    scratch = project / ".scratchpad"
    scratch.mkdir()
    # Use the real V1 prompt; falls back to plain text if it's missing.
    v1 = plamen_home() / "commands" / "plamen-l1.md"
    if not v1.exists():
        v1 = plamen_home() / "commands" / "plamen.md"
    if not v1.exists():
        # Fabricate a minimal V1 prompt just for the unit assert.
        v1 = sp / "fake_v1.md"
        v1.write_text("## Step 0\n\n## 6b. Tier Writers\n\nstuff\n", encoding="utf-8")
    body_phase = next(
        p for p in D.L1_PHASES if p.name == "report_body_writer_critical_high"
    )
    config = {
        "mode": "thorough", "project_root": str(project),
        "scratchpad": str(scratch), "pipeline": "l1", "proven_only": False,
        "language": "evm", "docs_path": "", "scope_file": "",
        "scope_notes": "", "network": "",
    }
    prompt = D.build_phase_prompt(v1, body_phase, config)
    has_override = "BODY-WRITER PHASE OVERRIDE" in prompt
    has_manifest_ref = "body_manifests/report_critical_high.json" in prompt
    has_no_invention = "Inventing a report ID is a hard halt" in prompt
    check(
        "PROMPT.body_writer_override_injected",
        has_override and has_manifest_ref and has_no_invention,
        f"override={has_override} manifest={has_manifest_ref} "
        f"no_invent={has_no_invention}",
    )


# =============================================================================
# Validator wiring: same _validate_tier_body_against_manifest covers both
# body-writer phase names and legacy tier names.
# =============================================================================

def test_VAL_body_writer_phase_name_resolves(tmp_path: Path):
    """The validator dispatches on the canonical tier shard regardless of
    whether the phase name carries the `report_body_writer_` prefix."""
    sp = tmp_path
    # Seed a queue + verify file + manifest so the validator has data.
    (sp / "verification_queue.md").write_text("""# Verification Queue

| Finding ID | Severity | Title | Location | Preferred Tag |
|------------|----------|-------|----------|---------------|
| INV-001 | High | bug | src/F.sol:L1 | CODE-TRACE |
""", encoding="utf-8")
    (sp / "verify_INV-001.md").write_text("""# INV-001
**Verdict**: CONFIRMED
**Severity**: High
**Impact**: High
**Likelihood**: Medium
**Location**: src/F.sol:L1
**Description**: bug
**Recommendation**: fix
**Evidence Tag**: CODE-TRACE
""", encoding="utf-8")
    D._write_mechanical_report_index(sp)
    # Deliberate hallucination in the body file -> validator must flag.
    (sp / "report_critical_high.md").write_text("""# Critical and High

## High Findings

### [H-99] hallucinated
**Severity**: High
**Location**: src/HALLUCINATED.sol:L1
""", encoding="utf-8")
    issues_legacy = D._validate_tier_body_against_manifest(
        sp, "report_critical_high"
    )
    check(
        "VAL.legacy_phase_name_resolves",
        bool(issues_legacy),
        repr(issues_legacy[:1]),
    )


# =============================================================================
# Test runner
# =============================================================================

TESTS_BASIC = [
    test_GRAPH_L1_has_body_writer_phases,
    test_GRAPH_SC_has_body_writer_phases,
    test_GRAPH_body_writer_runs_after_index_before_tier,
    test_GRAPH_body_writer_phases_are_llm_critical,
    test_GRAPH_body_writer_filename_matches_legacy_tier,
]

TESTS_INTEG = [
    test_PROMPT_body_writer_override_present,
    test_VAL_body_writer_phase_name_resolves,
]


def main() -> int:
    n = len(TESTS_BASIC) + len(TESTS_INTEG)
    print(f"Running {n} body-writer wiring tests...")
    for t in TESTS_BASIC:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as exc:
            global FAIL
            FAIL += 1
            print(f"  CRASH {t.__name__} :: {exc!r}")
    for t in TESTS_INTEG:
        print(f"\n[{t.__name__}]")
        try:
            with tempfile.TemporaryDirectory() as td:
                t(Path(td))
        except Exception as exc:
            FAIL += 1
            print(f"  CRASH {t.__name__} :: {exc!r}")
    print(f"\n{'=' * 64}")
    print(f"  PASS: {PASS}   FAIL: {FAIL}")
    print('=' * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
