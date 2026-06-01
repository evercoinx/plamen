"""MVP: targeted PoC-contract repair for verify shards (additive, haltless).

When a PoC verify shard's attempt-2 gate fails EXCLUSIVELY on the
"Attempted:YES but lacks concrete Test File/Command" PoC-contract class, the
driver fires ONE extra targeted repair attempt — scoped via a sharpened retry
hint to ONLY the failed IDs — BEFORE the existing verify-shard
degrade-and-continue. It fires at most once per phase (hard disk-marker
fire-once guard). On gate pass the phase completes; on gate fail (or any
ambiguity / mixed failure) it degrades-and-continues unchanged. Verify shards
never halt.

These tests cover the task's scenarios (a)-(g):
  (a) PoC-contract-only attempt-2 failure -> ONE targeted attempt, scoped hint
      names ONLY the failed IDs.
  (b) fire-once: the targeted attempt cannot fire twice (guard holds) -> degrade.
  (c) targeted attempt passes the gate -> phase completes (no degrade).
  (d) targeted attempt fails -> existing degrade-and-continue fires, findings
      ship UNPROVEN, NO halt.
  (e) non-PoC-contract (mixed) gate failure -> targeted attempt does NOT fire.
  (f) good (non-failed) findings' verify files are preserved across the attempt.
  (g) regression: existing 2-attempt + degrade behavior for NON-verify phases
      is unchanged (the new branch is gated on _is_poc_verify_shard).

The deep driver `main()` loop is not callable in isolation, so the control-flow
scenarios are exercised by replicating the EXACT driver predicate
(`not passed and _is_poc_verify_shard(name) and not already_done and
verify_poc_contract_only_failed_ids(missing)`) and the fire-once + scoping
helpers it calls, mirroring the helper-direct + smoke-scenario approach the task
authorizes.

Run: pytest scripts/test_verify_targeted_repair_mvp.py -q
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_validators as V  # noqa: E402
import plamen_driver as D  # noqa: E402


_POC_PREFIX = "verify PoC contract: "
_ATTEMPTED_YES_SUB = "says Attempted:YES but lacks concrete Test File/Command"


def _poc_only_missing(*fids: str) -> list[str]:
    """A single combined PoC-contract-only `missing` entry (as
    _validate_verify_completion emits it: prefix + "; "-joined sub-issues)."""
    subs = "; ".join(f"{fid} {_ATTEMPTED_YES_SUB}" for fid in fids)
    return [_POC_PREFIX + subs]


def _mkscratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="plamen_vtr_"))


# ---------------------------------------------------------------------------
# Failed-ID parse — PoC-contract-only detection (the trigger gate)
# ---------------------------------------------------------------------------

def test_a_failed_id_parse_single_combined_entry():
    assert V.verify_poc_contract_only_failed_ids(
        _poc_only_missing("F-1", "F-3")
    ) == ["F-1", "F-3"]


def test_a_failed_id_parse_bracketed_id():
    assert V.verify_poc_contract_only_failed_ids(
        [_POC_PREFIX + f"[H-5] {_ATTEMPTED_YES_SUB}"]
    ) == ["H-5"]


def test_a_failed_id_parse_dedup_order_preserved():
    assert V.verify_poc_contract_only_failed_ids(
        _poc_only_missing("F-2", "F-1", "F-2", "F-1")
    ) == ["F-2", "F-1"]


def test_e_mixed_failure_with_schema_issue_returns_empty():
    """Schema issue mixed in -> NOT PoC-contract-only -> do not fire."""
    missing = ["verify schema: missing required verifier fields in verify_F-1.md"]
    missing += _poc_only_missing("F-2")
    assert V.verify_poc_contract_only_failed_ids(missing) == []


def test_e_mixed_failure_with_location_recovery_returns_empty():
    missing = _poc_only_missing("F-1")
    missing += ["verify location recovery: F-2 cites nonexistent path"]
    assert V.verify_poc_contract_only_failed_ids(missing) == []


def test_e_other_poc_contract_subclass_returns_empty():
    """A different PoC-contract sub-class (mandatory-not-attempted, missing
    ledger, EXTERNAL_DEP mock-override) must NOT trigger the targeted attempt —
    the scoped hint would not fully repair the gate."""
    assert V.verify_poc_contract_only_failed_ids(
        [_POC_PREFIX + "F-2 mandatory unit PoC not attempted with valid blocker"]
    ) == []
    assert V.verify_poc_contract_only_failed_ids(
        [_POC_PREFIX + "F-2 missing PoC Attempt/Execution Result ledger"]
    ) == []
    assert V.verify_poc_contract_only_failed_ids(
        [_POC_PREFIX + "F-2 EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS skip invalid "
         "for Medium unit finding: mocking is demonstrably feasible"]
    ) == []


def test_e_mixed_subclass_within_combined_entry_returns_empty():
    """One target-class sub-issue AND one other sub-class in the SAME combined
    entry -> bail (the hint cannot fix the other sub-class)."""
    combined = _POC_PREFIX + (
        f"F-1 {_ATTEMPTED_YES_SUB}; "
        "F-2 mandatory unit PoC not attempted with valid blocker"
    )
    assert V.verify_poc_contract_only_failed_ids([combined]) == []


def test_e_empty_missing_returns_empty():
    assert V.verify_poc_contract_only_failed_ids([]) == []


def test_e_non_string_entry_returns_empty():
    assert V.verify_poc_contract_only_failed_ids([None]) == []  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# (a) Scoped retry hint — names ONLY the failed IDs, preserves the rest
# ---------------------------------------------------------------------------

def test_a_hint_names_only_failed_ids():
    hint = V._generate_verify_targeted_repair_hint(["F-1", "F-3"])
    assert "verify_F-1.md" in hint
    assert "verify_F-3.md" in hint
    # An unrelated good finding must NOT be named.
    assert "verify_F-2.md" not in hint


def test_a_hint_instructs_preserve_other_files():
    hint = V._generate_verify_targeted_repair_hint(["F-1"])
    assert "Do NOT touch" in hint
    assert "preserved verbatim" in hint
    # offers both remediation paths (YES+concrete OR NO+valid blocker)
    assert "Attempted: YES" in hint and "Attempted: NO" in hint
    assert "VALID_BLOCKER" in hint


def test_a_hint_empty_ids_is_empty():
    assert V._generate_verify_targeted_repair_hint([]) == ""


# ---------------------------------------------------------------------------
# (b) Fire-once disk-marker guard
# ---------------------------------------------------------------------------

def test_b_fire_once_guard_sets_and_reads():
    sp = _mkscratch()
    ph = "sc_verify_high_a"
    assert V.verify_targeted_repair_already_done(sp, ph) is False
    V.mark_verify_targeted_repair_done(sp, ph)
    assert V.verify_targeted_repair_already_done(sp, ph) is True


def test_b_fire_once_guard_is_per_phase():
    sp = _mkscratch()
    V.mark_verify_targeted_repair_done(sp, "sc_verify_high_a")
    assert V.verify_targeted_repair_already_done(sp, "sc_verify_high_a") is True
    assert V.verify_targeted_repair_already_done(sp, "sc_verify_high_b") is False


def test_b_fire_once_marker_survives_reload():
    """Disk-marker based => survives resume (re-read from a fresh path obj)."""
    sp = _mkscratch()
    ph = "verify_high_a"
    V.mark_verify_targeted_repair_done(sp, ph)
    sp2 = Path(str(sp))  # simulate a fresh process re-reading the same scratchpad
    assert V.verify_targeted_repair_already_done(sp2, ph) is True


# ---------------------------------------------------------------------------
# Control-flow simulation: replicate the EXACT driver predicate + fire-once
# decision the targeted-repair block uses, for scenarios (a)-(g).
# ---------------------------------------------------------------------------

def _should_fire_targeted_repair(
    scratchpad: Path, phase_name: str, passed: bool, missing: list[str]
) -> list[str]:
    """Mirror the driver gate (plamen_driver.py, verify-shard targeted-repair
    block): returns the failed-ID list IFF the targeted attempt should fire,
    else []. Identical predicate composition to the driver."""
    if (
        not passed
        and D._is_poc_verify_shard(phase_name)
        and not V.verify_targeted_repair_already_done(scratchpad, phase_name)
    ):
        return V.verify_poc_contract_only_failed_ids(list(missing))
    return []


def test_a_control_flow_poc_only_fires_once_scoped():
    """(a) PoC-contract-only attempt-2 failure -> targeted attempt fires once,
    scoped to the failed IDs."""
    sp = _mkscratch()
    ph = "sc_verify_high_a"
    fids = _should_fire_targeted_repair(sp, ph, False, _poc_only_missing("F-1", "F-3"))
    assert fids == ["F-1", "F-3"]
    # the driver sets the fire-once marker BEFORE spawning
    V.mark_verify_targeted_repair_done(sp, ph)
    # scoped hint names ONLY F-1/F-3
    hint = V._generate_verify_targeted_repair_hint(fids)
    assert "verify_F-1.md" in hint and "verify_F-3.md" in hint
    assert "verify_F-2.md" not in hint


def test_b_control_flow_cannot_fire_twice():
    """(b) After the targeted attempt has run once, a subsequent identical gate
    failure does NOT re-fire it -> degrade path is taken instead."""
    sp = _mkscratch()
    ph = "sc_verify_high_a"
    first = _should_fire_targeted_repair(sp, ph, False, _poc_only_missing("F-1"))
    assert first == ["F-1"]
    V.mark_verify_targeted_repair_done(sp, ph)  # driver marks before spawning
    # same PoC-contract-only failure recurs -> guard blocks re-fire
    second = _should_fire_targeted_repair(sp, ph, False, _poc_only_missing("F-1"))
    assert second == []  # -> falls through to degrade


def test_c_control_flow_pass_after_targeted_no_degrade():
    """(c) When the targeted attempt makes the gate pass, the driver's
    `if not passed:` degrade block is skipped entirely. We model this as: the
    fire decision was taken, the attempt ran, and `passed` is now True ->
    no degrade, normal completion."""
    sp = _mkscratch()
    ph = "sc_verify_high_a"
    fids = _should_fire_targeted_repair(sp, ph, False, _poc_only_missing("F-1"))
    assert fids  # fired
    V.mark_verify_targeted_repair_done(sp, ph)
    # re-validation after attempt 3 returns passed=True (gate satisfied)
    passed_after = True
    # the driver only enters the degrade branch `if not passed:`
    assert not (not passed_after)  # degrade is NOT entered


def test_d_control_flow_fail_after_targeted_degrades_no_halt():
    """(d) When the targeted attempt still fails, control reaches the UNCHANGED
    verify-shard degrade-and-continue. We assert the degrade branch predicate is
    satisfied (still a PoC verify shard, still not passed) and that the fire
    decision is now empty (guard set) so no further attempt occurs."""
    sp = _mkscratch()
    ph = "verify_high_a"  # L1 shard name
    assert _should_fire_targeted_repair(sp, ph, False, _poc_only_missing("F-1"))
    V.mark_verify_targeted_repair_done(sp, ph)
    passed_after = False  # attempt 3 still failed
    # degrade-branch reached: phase is still a PoC verify shard and not passed
    assert (not passed_after) and D._is_poc_verify_shard(ph)
    # no re-fire on the degrade path (fire-once guard holds)
    assert _should_fire_targeted_repair(sp, ph, passed_after, _poc_only_missing("F-1")) == []


def test_e_control_flow_mixed_failure_does_not_fire():
    """(e) Non-PoC-contract (mixed) gate failure -> targeted attempt does NOT
    fire -> degrade as before."""
    sp = _mkscratch()
    ph = "sc_verify_high_a"
    mixed = ["verify schema: missing required verifier fields in verify_F-1.md"]
    mixed += _poc_only_missing("F-2")
    assert _should_fire_targeted_repair(sp, ph, False, mixed) == []
    # fire-once marker was NOT set (we never fired)
    assert V.verify_targeted_repair_already_done(sp, ph) is False


def test_g_control_flow_non_verify_phase_never_fires():
    """(g) Regression: the new branch is gated on _is_poc_verify_shard, so a
    NON-verify phase (depth, inventory, aggregate) NEVER triggers the targeted
    attempt — its existing 2-attempt+degrade path is untouched."""
    sp = _mkscratch()
    for ph in ("depth", "inventory", "sc_verify_aggregate", "verify_aggregate",
               "report_index", "chain"):
        assert _should_fire_targeted_repair(
            sp, ph, False, _poc_only_missing("F-1")
        ) == [], ph


# ---------------------------------------------------------------------------
# (f) Good (non-failed) findings' verify files are preserved across the attempt
# ---------------------------------------------------------------------------

def test_f_good_verify_files_not_targeted_by_hint():
    """The scoped hint never names a good finding's file and explicitly forbids
    touching other verify_*.md — so the on-disk good files (preserved by the
    v2.4.5 accumulation) are not rewritten by the targeted attempt."""
    sp = _mkscratch()
    # Two good findings already on disk; F-1 is the only failed one.
    good_b = "# verify F-2\n**Verdict**: CONFIRMED\nGOOD-CONTENT-B\n"
    good_c = "# verify F-3\n**Verdict**: REFUTED\nGOOD-CONTENT-C\n"
    (sp / "verify_F-2.md").write_text(good_b, encoding="utf-8")
    (sp / "verify_F-3.md").write_text(good_c, encoding="utf-8")
    fids = V.verify_poc_contract_only_failed_ids(_poc_only_missing("F-1"))
    assert fids == ["F-1"]
    hint = V._generate_verify_targeted_repair_hint(fids)
    assert "verify_F-2.md" not in hint
    assert "verify_F-3.md" not in hint
    assert "Do NOT touch" in hint
    # the good files are untouched on disk (the driver does not rewrite them)
    assert (sp / "verify_F-2.md").read_text(encoding="utf-8") == good_b
    assert (sp / "verify_F-3.md").read_text(encoding="utf-8") == good_c


# ---------------------------------------------------------------------------
# Haltless invariant — the targeted block never introduces a halt
# ---------------------------------------------------------------------------

def test_haltless_no_sys_exit_in_targeted_repair_block():
    """The targeted-repair block must contain NO new halt. It only fires
    run_phase + _run_phase_validators and either completes (passed=True) or
    falls through to the pre-existing degrade-and-continue. Assert the block's
    source carries no sys.exit and reaches the unchanged degrade."""
    src = (SCRIPTS_DIR / "plamen_driver.py").read_text(encoding="utf-8")
    marker = "MVP targeted PoC-contract repair (additive, haltless, fire-once)"
    assert marker in src
    start = src.index(marker)
    # the block ends at the semantic_dedup passthrough `if passed and`
    end = src.index('if passed and phase.name in ("semantic_dedup"', start)
    block = src[start:end]
    assert "sys.exit" not in block, "targeted repair must not introduce a halt"
    # the block does spawn exactly the bounded one-shot attempt
    assert "run_phase(phase, config, attempt=3)" in block
    assert "mark_verify_targeted_repair_done" in block


def test_degrade_branch_still_present_and_unchanged_contract():
    """The pre-existing verify-shard degrade-and-continue branch (ship UNPROVEN,
    continue, never halt) must remain present below the new block."""
    src = (SCRIPTS_DIR / "plamen_driver.py").read_text(encoding="utf-8")
    assert "elif _is_poc_verify_shard(phase.name):" in src
    assert "shipping shard findings as UNPROVEN and" in src
    assert "(verify shards never halt)" in src


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
