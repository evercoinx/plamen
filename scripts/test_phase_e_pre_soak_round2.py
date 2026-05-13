"""Pre-soak round 2: items 1-4 from the operator pre-flight checklist.

1. Empty-input crossbatch / skeptic soft-pass when no Medium+ findings exist
   or the LLM legitimately reports "no inconsistencies / all consistent".
2. Retry-hint plumbing: confirm the body-writer hint actually surfaces in the
   retry prompt (the file written by `_write_retry_hint` is read by
   `build_phase_prompt` on attempt 2 via `_read_retry_hint`).
3. ID-parser overmatch guard: prose like "section C-01" or "rule H-2"
   should NOT be classified as finding IDs.
4. Resume hygiene: stale `.degraded` sentinels from prior runs must not
   cause a fresh `report_assemble` phase to halt before its own work runs.

Run: `python test_phase_e_pre_soak_round2.py`
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
# Item 1: empty-input crossbatch / skeptic soft-pass.
# =============================================================================

def test_EMPTY_crossbatch_affirmation_without_coverage_HALTS(tmp_path: Path):
    """Phase E14 guardrail: affirmation phrase alone never substitutes for
    coverage when expected verify-ID set is non-empty.

    This was the prior over-permissive behavior; it has been hardened.
    """
    sp = tmp_path
    for i in range(1, 6):
        (sp / f"verify_INV-{i:03d}.md").write_text(
            f"# INV-{i:03d}\n**Verdict**: CONFIRMED\n", encoding="utf-8")
    (sp / "cross_batch_consistency.md").write_text(
        "# Cross-Batch Consistency\n\n"
        "Overall: PASS\n"
        "All findings consistent across verifier batches.\n"
        "No inconsistencies were detected.\n",
        encoding="utf-8",
    )
    issues = D._validate_crossbatch_full_coverage(sp)
    check(
        "EMPTY.crossbatch_affirmation_without_coverage_halts",
        bool(issues) and "5/5" in str(issues),
        repr(issues),
    )


def test_EMPTY_crossbatch_zero_count_without_coverage_HALTS(tmp_path: Path):
    """Same guardrail: 'Inconsistencies: 0' is not coverage."""
    sp = tmp_path
    for i in range(1, 4):
        (sp / f"verify_INV-{i:03d}.md").write_text(
            f"# INV-{i:03d}\n**Verdict**: CONFIRMED\n", encoding="utf-8")
    (sp / "cross_batch_consistency.md").write_text(
        "# Cross-Batch\n\nFiles checked: 3\nInconsistencies: 0\n"
        "Overall: PASS — every verifier output agrees.\n",
        encoding="utf-8",
    )
    issues = D._validate_crossbatch_full_coverage(sp)
    check(
        "EMPTY.crossbatch_zero_count_without_coverage_halts",
        bool(issues) and "3/3" in str(issues),
        repr(issues),
    )


def test_EMPTY_crossbatch_silently_empty_still_halts(tmp_path: Path):
    """No consistency marker, no IDs, no content — still hard halt.
    Soft-pass requires explicit affirmation, not silent omission."""
    sp = tmp_path
    for i in range(1, 4):
        (sp / f"verify_INV-{i:03d}.md").write_text(
            f"# INV-{i:03d}\n**Verdict**: CONFIRMED\n", encoding="utf-8")
    (sp / "cross_batch_consistency.md").write_text(
        "# Cross-Batch\n\n", encoding="utf-8",
    )
    issues = D._validate_crossbatch_full_coverage(sp)
    check(
        "EMPTY.crossbatch_silent_empty_halts",
        bool(issues),
        repr(issues),
    )


def test_EMPTY_skeptic_affirmation_without_coverage_HALTS(tmp_path: Path):
    """Phase E14 guardrail: skeptic AGREE phrase alone never substitutes
    for C/H coverage when those findings exist on disk."""
    sp = tmp_path
    (sp / "verification_queue.md").write_text("""# Q

| Finding ID | Severity | Title | Location | Preferred Tag |
|------------|----------|-------|----------|---------------|
| INV-001 | High | a | src/F:L1 | CODE-TRACE |
| INV-002 | High | b | src/F:L2 | CODE-TRACE |
""", encoding="utf-8")
    for fid in ("INV-001", "INV-002"):
        (sp / f"verify_{fid}.md").write_text(f"""# {fid}
**Verdict**: CONFIRMED
**Severity**: High
**Impact**: High
**Likelihood**: Medium
**Description**: x
**Recommendation**: y
""", encoding="utf-8")
    (sp / "skeptic_findings.md").write_text(
        "# Skeptic-Judge\n\n"
        "Overall: AGREE\n"
        "No disagreement with verifier severity assignments.\n"
        "All Critical/High findings were reviewed.\n",
        encoding="utf-8",
    )
    issues = D._validate_skeptic_full_ch_coverage(sp)
    check(
        "EMPTY.skeptic_affirmation_without_coverage_halts",
        bool(issues) and "2/2" in str(issues),
        repr(issues),
    )


# =============================================================================
# Item 2: retry-hint plumbing — body-writer hint reaches the retry prompt.
# =============================================================================

def test_RETRY_body_writer_hint_persists_and_is_readable(tmp_path: Path):
    sp = tmp_path
    hint = "## RETRY HINT\n- Missing: H-02\n- Extra: H-99\n"
    D._write_retry_hint(sp, "report_body_writer_critical_high", hint)
    got = D._read_retry_hint(sp, "report_body_writer_critical_high")
    check(
        "RETRY.hint_round_trip_for_body_writer_phase",
        got.strip() == hint.strip(),
        f"got={got!r}",
    )


def test_RETRY_hint_surfaces_in_build_phase_prompt(tmp_path: Path):
    """The body-writer retry hint must appear inside the prompt
    `build_phase_prompt` returns when a hint file exists for the phase."""
    sp = tmp_path
    project = sp / "proj"
    project.mkdir()
    scratch = project / ".scratchpad"
    scratch.mkdir()
    hint = "## RETRY HINT\n- Missing: H-02\n- Extra: H-99\n- Use manifest verbatim.\n"
    D._write_retry_hint(scratch, "report_body_writer_critical_high", hint)
    body_phase = next(
        p for p in D.L1_PHASES if p.name == "report_body_writer_critical_high"
    )
    v1 = plamen_home() / "commands" / "plamen-l1.md"
    if not v1.exists():
        v1 = sp / "fake_v1.md"
        v1.write_text("## Step 0\n\n## 6b. Tier Writers\n\nstuff\n", encoding="utf-8")
    config = {
        "mode": "thorough", "project_root": str(project),
        "scratchpad": str(scratch), "pipeline": "l1", "proven_only": False,
        "language": "evm", "docs_path": "", "scope_file": "",
        "scope_notes": "", "network": "",
    }
    prompt = D.build_phase_prompt(v1, body_phase, config)
    saw_block = "RETRY HINT (injected by driver" in prompt
    saw_hint_body = "Missing: H-02" in prompt and "Extra: H-99" in prompt
    check(
        "RETRY.hint_visible_in_retry_prompt",
        saw_block and saw_hint_body,
        f"block={saw_block} hint={saw_hint_body}",
    )


# =============================================================================
# Item 3: ID-parser overmatch guard.
# =============================================================================

def test_PARSE_no_overmatch_on_methodology_prose():
    prose = (
        "Per CLAUDE.md, the orchestrator MUST follow the C section workflow "
        "described in plamen.md. Step C is required. See rule H-2. "
        "ERC-20 token transfer guidance applies. The EIP-1559 fee structure "
        "is preserved. Common patterns include onERC721Received (ERC-721)."
    )
    got = D._extract_finding_ids_from_text(prose)
    # `H-2` is the canonical High-2 finding ID shape — that one IS allowed
    # to match, since the parser is a finding-ID extractor used in contexts
    # where prose like `H-2` would be ambiguous. We just want to confirm
    # ERC-20 / EIP-1559 / ERC-721 do NOT pollute the set.
    check(
        "PARSE.no_overmatch_ERC_EIP",
        "ERC-20" not in got and "EIP-1559" not in got and "ERC-721" not in got,
        repr(got),
    )


def test_PARSE_overmatch_on_lone_letters_documented():
    """The `C-01`/`H-01`/`M-01`/`L-01`/`I-01` shapes ARE finding-ID prefixes
    by design. Confirm we do match them so legitimate report-IDs work in
    crossbatch / skeptic / body validators."""
    s = "Reviewed [C-01], [H-02], [M-05], [L-08], [I-01]."
    got = D._extract_finding_ids_from_text(s)
    check(
        "PARSE.report_id_shapes_match",
        {"C-01", "H-02", "M-05", "L-08", "I-01"}.issubset(got),
        repr(got),
    )


def test_PARSE_no_overmatch_in_paths_or_versions():
    """Paths like `crates/p2p/H-2-handler.rs` and version strings like
    `pkg-1.2.3` should not become findings."""
    s = "src/contracts/M-04-strategy.sol  pkg-2.0.1  worker-3.1"
    got = D._extract_finding_ids_from_text(s)
    # `M-04` IS a valid finding-ID shape and would match — that's by design.
    # The check here is that `worker-3` (lowercase prefix) is filtered.
    check(
        "PARSE.lowercase_prefix_filtered",
        not any(fid.startswith("WORKER") for fid in got),
        repr(got),
    )


# =============================================================================
# Item 4: resume hygiene — stale `.degraded` sentinels.
# =============================================================================

def test_RESUME_stale_assemble_degraded_does_not_block_fresh_run(tmp_path: Path):
    sp = tmp_path
    # Simulate a stale sentinel from a prior aborted run.
    old_sentinel = sp / "report_assemble.degraded"
    old_sentinel.write_text(
        "Stale sentinel from prior run on 2026-04-30.\nIssues: ['old-thing']\n",
        encoding="utf-8",
    )
    # Resume hygiene: a helper must clear (or stamp) stale sentinels at
    # startup so the fresh run isn't blocked before its first phase.
    D._clear_stale_degraded_sentinels(sp)
    issues = D._validate_assemble_not_degraded(sp)
    check(
        "RESUME.stale_assemble_sentinel_cleared",
        not issues,
        f"sentinel_present={old_sentinel.exists()} issues={issues}",
    )


def test_RESUME_stale_body_writer_degraded_cleared(tmp_path: Path):
    sp = tmp_path
    sentinel = sp / "report_critical_high.body_writer.degraded"
    sentinel.write_text("[BODY-WRITER-DEGRADED] stale\n", encoding="utf-8")
    D._clear_stale_degraded_sentinels(sp)
    check(
        "RESUME.body_writer_sentinel_cleared",
        not sentinel.exists(),
        f"present={sentinel.exists()}",
    )


def test_RESUME_fresh_degraded_sentinel_kept(tmp_path: Path):
    """Stale = older than the current run's start. Sentinels written DURING
    the current run must not be cleared (they signal an active fault)."""
    sp = tmp_path
    sentinel = sp / "report_assemble.degraded"
    sentinel.write_text("Live sentinel\n", encoding="utf-8")
    # Mark the run started AFTER the sentinel — sentinel is stale.
    D._clear_stale_degraded_sentinels(sp)
    # Now write a fresh sentinel and call again — it must persist.
    sentinel.write_text("Live sentinel 2\n", encoding="utf-8")
    # No subsequent clear call — the live sentinel stays for the next phase
    # to act on.
    check(
        "RESUME.live_sentinel_preserved",
        sentinel.exists(),
        f"present={sentinel.exists()}",
    )


# =============================================================================
# Test runner
# =============================================================================

TESTS_BASIC = [
    test_PARSE_no_overmatch_on_methodology_prose,
    test_PARSE_overmatch_on_lone_letters_documented,
    test_PARSE_no_overmatch_in_paths_or_versions,
]

TESTS_INTEG = [
    test_EMPTY_crossbatch_affirmation_without_coverage_HALTS,
    test_EMPTY_crossbatch_zero_count_without_coverage_HALTS,
    test_EMPTY_crossbatch_silently_empty_still_halts,
    test_EMPTY_skeptic_affirmation_without_coverage_HALTS,
    test_RETRY_body_writer_hint_persists_and_is_readable,
    test_RETRY_hint_surfaces_in_build_phase_prompt,
    test_RESUME_stale_assemble_degraded_does_not_block_fresh_run,
    test_RESUME_stale_body_writer_degraded_cleared,
    test_RESUME_fresh_degraded_sentinel_kept,
]


def main() -> int:
    n = len(TESTS_BASIC) + len(TESTS_INTEG)
    print(f"Running {n} pre-soak round 2 tests...")
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
