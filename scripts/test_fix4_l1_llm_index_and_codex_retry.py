"""FIX #4 (L1 LLM-first report_index + mechanical backstop) and the Codex-only
extended retry budget for RECOVERING content phases.

Both fixes live in plamen_driver.py and are recall-safety constrained:

  FIX #4 — L1 report_index previously ALWAYS used the deterministic mechanical
  builder, so the LLM Index Agent's STEP 1.5 root-cause consolidation (which
  consumes [LIKELY-DUP] hints + dedup_candidate_pairs.md) never ran. The fix
  lets the LLM consolidation run FIRST, with `_write_mechanical_report_index`
  retained as the DETERMINISTIC BACKSTOP when the LLM index is
  missing/invalid/incomplete — so NO finding can vanish. SC behavior is
  unchanged.

  CODEX RETRY BUDGET — Codex single-pass workers under-cover content phases on
  the first run and recover when re-prompted. The fix grants the RECOVERING
  content phases (recon, breadth, inventory, inventory_chunk_*) up to 3 attempts
  ONLY when cli_backend == "codex". Claude (and every other backend), and every
  non-recovering phase (verify/report/skeptic/depth/chain/...), keep the
  unchanged retry-once-then-degrade budget of 2.

These tests reproduce the original failure modes (LLM consolidation never ran;
2nd Codex flake degraded) and prove they are fixed without weakening any gate.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import plamen_driver as d


# --------------------------------------------------------------------------- #
# Fixture builders (mirror test_l1_report_index_haltless_parity.py format)     #
# --------------------------------------------------------------------------- #
def _write_queue(sp: Path, ids_sevs: list[tuple[str, str]]) -> None:
    lines = [
        "# Verification Queue",
        "",
        "| Finding ID | Title | Severity | Preferred Tag |",
        "|------------|-------|----------|---------------|",
    ]
    for fid, sev in ids_sevs:
        lines.append(f"| {fid} | Some bug in {fid} | {sev} | CODE-TRACE |")
    (sp / "verification_queue.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _write_verify(sp: Path, fid: str, severity: str, verdict: str = "CONFIRMED") -> None:
    body = (
        f"# Verification: {fid}\n\n"
        f"**Verdict**: {verdict}\n"
        f"**Severity**: {severity}\n"
        f"**Location**: src/Mod.go:L100-L140\n\n"
        "## Finding\n\n"
        f"Detailed analysis of {fid}: the function fails to validate its input "
        "before applying a state transition, which a caller can exploit. "
        + ("x" * 200)
        + "\n\n### Execution Result\n"
        "- Evidence Tag: [CODE-TRACE]\n"
    )
    (sp / f"verify_{fid}.md").write_text(body, encoding="utf-8")


def _write_report_index(sp: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """rows = (report_id, severity, trust_adj, internal_id)."""
    lines = [
        "# Report Index",
        "",
        "## Summary Counts",
        "",
        "| Severity | Count |",
        "|----------|-------|",
        f"| Total | {len(rows)} |",
        "",
        "## Master Finding Index",
        "",
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis ID |",
        "|-----------|-------|----------|----------|--------------|------------|------------------------|",
    ]
    for rid, sev, trust, internal in rows:
        lines.append(
            f"| {rid} | Bug {internal} | {sev} | src/Mod.go:L100 | VERIFIED | {trust} | {internal} |"
        )
    (sp / "report_index.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# FIX #4 (a): _llm_report_index_is_usable detects a valid LLM index            #
# --------------------------------------------------------------------------- #
def test_valid_llm_index_is_usable(tmp_path: Path):
    """A complete, parseable LLM index that accounts for every verify ID is
    'usable' — the LLM consolidation output is kept, the mechanical backstop is
    NOT used."""
    sp = tmp_path
    _write_queue(sp, [("H-01", "Medium"), ("H-02", "High")])
    _write_verify(sp, "H-01", "Medium")
    _write_verify(sp, "H-02", "High")
    _write_report_index(
        sp, [("H-01", "Medium", "-", "H-01"), ("H-02", "High", "-", "H-02")]
    )
    assert d._llm_report_index_is_usable(sp) is True


# --------------------------------------------------------------------------- #
# FIX #4 (b)/(c): missing / garbage / incomplete LLM index -> backstop          #
# --------------------------------------------------------------------------- #
def test_missing_llm_index_is_not_usable(tmp_path: Path):
    sp = tmp_path
    _write_queue(sp, [("H-01", "Medium")])
    _write_verify(sp, "H-01", "Medium")
    # No report_index.md at all.
    assert d._llm_report_index_is_usable(sp) is False


def test_garbage_llm_index_is_not_usable(tmp_path: Path):
    sp = tmp_path
    _write_queue(sp, [("H-01", "Medium")])
    _write_verify(sp, "H-01", "Medium")
    # Header-only / no parseable Master Finding Index rows.
    (sp / "report_index.md").write_text(
        "# Report Index\n\n(garbage, no table here)\n" + ("z" * 300),
        encoding="utf-8",
    )
    assert d._llm_report_index_is_usable(sp) is False


def test_incomplete_llm_index_dropping_a_finding_is_not_usable(tmp_path: Path):
    """An LLM index that silently drops a verified finding must be rejected as
    not-usable so the mechanical backstop re-includes it (no silent drop)."""
    sp = tmp_path
    _write_queue(sp, [("H-01", "Medium"), ("H-02", "High")])
    _write_verify(sp, "H-01", "Medium")
    _write_verify(sp, "H-02", "High")
    # Index omits H-02 entirely (neither indexed nor excluded).
    _write_report_index(sp, [("H-01", "Medium", "-", "H-01")])
    assert d._llm_report_index_is_usable(sp) is False


def test_mechanical_backstop_retains_all_findings_on_garbage_index(tmp_path: Path):
    """When the LLM index is unusable, the deterministic mechanical builder is
    the backstop and MUST retain every verified finding — nothing vanishes."""
    sp = tmp_path
    ids = [("H-01", "Medium"), ("H-02", "High"), ("H-03", "Low")]
    _write_queue(sp, ids)
    for fid, sev in ids:
        _write_verify(sp, fid, sev)
    (sp / "report_index.md").write_text(
        "# Report Index\n\ngarbage\n" + ("z" * 300), encoding="utf-8"
    )
    assert d._llm_report_index_is_usable(sp) is False

    active = d._write_mechanical_report_index(sp)
    assert active == 3
    text = (sp / "report_index.md").read_text(encoding="utf-8")
    for fid, _ in ids:
        assert fid in text, f"mechanical backstop dropped {fid}"


# --------------------------------------------------------------------------- #
# FIX #4 — driver wiring: LLM-first with mechanical backstop, SC untouched     #
# --------------------------------------------------------------------------- #
def _driver_source() -> str:
    return Path(d.__file__).read_text(encoding="utf-8")


def test_l1_report_index_defers_to_llm_with_backstop_guard():
    """The L1 report_index prework must no longer UNCONDITIONALLY mechanically
    write + continue. It must (a) probe the LLM index usability, and (b) guard
    the mechanical builder behind a backstop flag so cases (a)/(b) fall through
    to the LLM subprocess."""
    src = _driver_source()
    l1_start = src.index(
        'if config["pipeline"] == "l1" and phase.name == "report_index":'
    )
    l1_end = src.index('if phase.name.startswith("report_body_writer_")', l1_start)
    region = src[l1_start:l1_end]
    assert "_llm_report_index_is_usable" in region, (
        "L1 report_index must probe LLM index usability (LLM-first)"
    )
    assert "_run_mechanical_backstop" in region, (
        "L1 report_index must guard the mechanical builder behind a backstop flag"
    )
    # The mechanical builder is still present as the deterministic backstop.
    assert "_write_mechanical_report_index" in region


def test_sc_report_index_path_unchanged_no_backstop_flag():
    """SC behavior must be untouched: the SC report_index repair-from-prior path
    must NOT reference the new L1-only backstop flag."""
    src = _driver_source()
    sc_start = src.index(
        'if config["pipeline"] == "sc" and phase.name == "report_index":'
    )
    # Bound the SC block at the L1 backstop-flag default that immediately
    # precedes the L1 branch (the flag init line is L1-only scaffolding).
    sc_end = src.index(
        "# FIX #4: default the L1 backstop flag", sc_start,
    )
    sc_block = src[sc_start:sc_end]
    assert "_run_mechanical_backstop" not in sc_block, (
        "SC report_index path must not be touched by the L1 backstop flag"
    )
    assert "_repair_sc_report_index_from_prior" in src


# --------------------------------------------------------------------------- #
# CODEX RETRY BUDGET — backend- and phase-scoped                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "phase_name",
    ["recon", "breadth", "inventory", "inventory_chunk_a", "inventory_chunk_b",
     "inventory_chunk_c"],
)
def test_codex_recovering_phases_get_three_attempts(phase_name: str):
    assert d._codex_max_attempts_for_phase("codex", phase_name) == 3, (
        f"Codex RECOVERING phase {phase_name} must get 3 attempts"
    )
    assert d._is_codex_extra_retry_phase(phase_name) is True


@pytest.mark.parametrize(
    "phase_name",
    ["recon", "breadth", "inventory", "inventory_chunk_a"],
)
def test_claude_recovering_phases_keep_two_attempts(phase_name: str):
    """Claude (and the default backend) must keep the unchanged 2-attempt
    retry-once-then-degrade budget for the SAME phases."""
    assert d._codex_max_attempts_for_phase("claude", phase_name) == 2
    assert d._codex_max_attempts_for_phase(None, phase_name) == 2
    assert d._codex_max_attempts_for_phase("", phase_name) == 2


@pytest.mark.parametrize(
    "phase_name",
    ["verify", "verify_aggregate", "report_index", "report_critical_high",
     "skeptic", "crossbatch", "depth", "chain", "rag_sweep", "invariants"],
)
def test_non_recovering_phases_keep_two_attempts_even_on_codex(phase_name: str):
    """The extra budget is phase-scoped: verify/report/skeptic/depth/chain/etc.
    keep their existing 2-attempt budget even under Codex."""
    assert d._codex_max_attempts_for_phase("codex", phase_name) == 2, (
        f"non-recovering phase {phase_name} must NOT get the Codex extra budget"
    )
    assert d._is_codex_extra_retry_phase(phase_name) is False


def test_codex_backend_is_case_insensitive():
    assert d._codex_max_attempts_for_phase("Codex", "breadth") == 3
    assert d._codex_max_attempts_for_phase("CODEX", "breadth") == 3


def test_codex_extra_retry_constant_is_three():
    assert d._CODEX_EXTRA_RETRY_MAX_ATTEMPTS == 3


def test_codex_extra_retry_loop_wired_into_driver():
    """The driver retry loop must use the budget helper, gated on cli_backend,
    so the extra attempt only re-runs the SAME gated phase (never relaxes a
    gate)."""
    src = _driver_source()
    assert "_codex_max_attempts_for_phase(" in src, (
        "driver must consult the Codex retry-budget helper in the retry loop"
    )
    # The extra-attempt block re-runs the same gated phase and re-validates.
    loop_idx = src.index("_codex_max_attempts_for_phase(\n")
    window = src[loop_idx:loop_idx + 3000]
    assert "run_phase(phase, config, attempt=_codex_attempt)" in window, (
        "extra attempt must re-run the same phase via run_phase"
    )
    assert "_run_phase_validators(" in window, (
        "extra attempt must re-run the full phase validators (no gate relaxed)"
    )


# --------------------------------------------------------------------------- #
# FIX #4 — adversarial recall-safety: STEP 1.5 consolidation is NOT a drop      #
# --------------------------------------------------------------------------- #
def test_llm_consolidated_finding_is_usable_not_a_silent_drop(tmp_path: Path):
    """The genuinely-new behavior FIX #4 unlocks is the LLM Index Agent's
    STEP 1.5 root-cause consolidation. A finding the LLM merges into another
    (recorded in the Consolidation Map) must be treated as ACCOUNTED-FOR — it
    is still represented in the report, so the index is 'usable' and the
    mechanical backstop must NOT fire and re-split it.

    This proves the new consolidation path does not register as a silent drop
    (which would otherwise force the backstop and defeat the whole fix)."""
    sp = tmp_path
    _write_queue(sp, [("H-01", "Medium"), ("H-02", "Medium")])
    _write_verify(sp, "H-01", "Medium")
    _write_verify(sp, "H-02", "Medium")
    # LLM Master Index keeps H-01 as the canonical row and consolidates H-02
    # into it via the Consolidation Map (same root cause / same fix pattern).
    idx = [
        "# Report Index",
        "",
        "## Master Finding Index",
        "",
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis ID |",
        "|-----------|-------|----------|----------|--------------|------------|------------------------|",
        "| M-01 | Missing input validation | Medium | src/Mod.go:L100 | VERIFIED | - | H-01 |",
        "",
        "## Consolidation Map",
        "",
        "| Report ID | Consolidated From | Consolidation Reason |",
        "|-----------|-------------------|----------------------|",
        "| M-01 | H-01, H-02 | Same fix pattern: add zero-value validation |",
    ]
    (sp / "report_index.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
    # H-02 is absorbed, not dropped -> index is usable, backstop NOT used.
    assert d._llm_report_index_is_usable(sp) is True


# --------------------------------------------------------------------------- #
# FIX #4 — shard expansion is added on BOTH L1 completion paths, L1-gated       #
# --------------------------------------------------------------------------- #
def test_shard_expansion_added_on_l1_completion_paths_only():
    """FIX #4 adds expand_shard_phases() on the L1 LLM-authored completion path
    and the L1 artifact-recovery auto-complete path. Both additions MUST be
    gated on pipeline == 'l1' so the SC completion paths are untouched."""
    src = _driver_source()
    # The L1-completion shard-expansion additions reference FIX #4 + the L1
    # pipeline guard right next to expand_shard_phases.
    fix4_l1_expansions = [
        i for i in range(len(src))
        if src.startswith("phases[:] = expand_shard_phases(phases, scratchpad)", i)
    ]
    assert fix4_l1_expansions, "expand_shard_phases call sites must exist"
    # At least the two new L1-gated additions must each sit inside a block that
    # guards on pipeline == 'l1' (the new code) — verify the L1 guard text and a
    # FIX #4 marker co-occur in the driver around shard expansion.
    assert src.count('config.get("pipeline") == "l1"') >= 2, (
        "FIX #4 must add L1-gated shard-expansion guards"
    )
    # The SC normal-completion shard expansion stays the != 'l1' branch.
    assert 'phase.name == "report_index"\n            and config.get("pipeline") != "l1"' in src, (
        "SC normal-completion shard expansion must remain != 'l1' gated"
    )


def test_expand_shard_phases_is_idempotent(tmp_path: Path):
    """FIX #4 can call expand_shard_phases on more than one L1 completion path.
    A second call after expansion must be a no-op (no duplicate/dropped phases)
    so the additions cannot reorder or lose downstream phases."""
    from plamen_types import Phase

    sp = tmp_path
    md = sp / "body_manifests"
    md.mkdir()
    # Two shards for the critical_high tier.
    (md / "report_critical_high_a.json").write_text("{}", encoding="utf-8")
    (md / "report_critical_high_b.json").write_text("{}", encoding="utf-8")

    phases = [
        Phase("report_index", [], ["report_index.md"], 600),
        Phase("report_body_writer_critical_high", ["6b"], ["report_critical_high.md"], 600),
        Phase("report_critical_high", ["6b.1"], ["report_critical_high.md"], 600),
        Phase("report_assemble", [], ["AUDIT_REPORT.md"], 600),
    ]
    once = d.expand_shard_phases(list(phases), sp)
    twice = d.expand_shard_phases(list(once), sp)
    assert [p.name for p in once] == [p.name for p in twice], (
        "expand_shard_phases must be idempotent across repeated FIX #4 calls"
    )
    # The non-tier phases (report_index, report_assemble) survive unchanged.
    assert "report_index" in {p.name for p in twice}
    assert "report_assemble" in {p.name for p in twice}


# --------------------------------------------------------------------------- #
# RECALL-SAFETY: the extra-retry block is a strict no-op for Claude / non-      #
# recovering phases, and NEVER relaxes a gate (re-runs same phase + same        #
# validators, degrade remains reachable).                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "backend,phase_name",
    [
        # Claude on a recovering phase: budget 2 -> while-loop body unreachable.
        ("claude", "breadth"),
        ("claude", "recon"),
        ("claude", "inventory_chunk_a"),
        (None, "breadth"),
        ("", "breadth"),
        # Codex on a NON-recovering phase: budget 2 -> while-loop body unreachable.
        ("codex", "verify"),
        ("codex", "report_index"),
        ("codex", "skeptic"),
        ("codex", "depth"),
        ("codex", "chain"),
    ],
)
def test_extra_retry_is_strict_noop_when_budget_two(backend, phase_name):
    """When the budget resolves to 2 the while-guard `_codex_attempt(2) <
    budget(2)` is False, so the extra-attempt body NEVER executes. This proves
    the change is a strict no-op for Claude, the default backend, and every
    non-recovering phase even under Codex — i.e. it cannot change their
    behavior at all."""
    budget = d._codex_max_attempts_for_phase(backend, phase_name)
    assert budget == 2, (
        f"{backend!r}/{phase_name} must keep the 2-attempt budget"
    )
    # Replicate the driver's exact loop guard with attempts 1+2 consumed.
    codex_attempt = 2
    iterations = 0
    while codex_attempt < budget:  # mirrors the driver guard
        codex_attempt += 1
        iterations += 1
    assert iterations == 0, (
        "extra-retry body must NOT execute when budget == 2 (strict no-op)"
    )


def test_extra_retry_loop_runs_before_degrade_and_relaxes_nothing():
    """The extra-attempt block must sit BEFORE the unchanged degrade/halt block
    so the degrade floor stays reachable, and it must re-run the SAME phase +
    the SAME full validators (no gate relaxed, so no true-positive finding can
    be dropped). Also asserts the block is entered only under a real gate
    failure (`if not passed:`) and only widens the budget via the backend-gated
    helper."""
    src = _driver_source()
    # Locate the extra-retry budget block and the degrade error log.
    budget_idx = src.index("_codex_budget = _codex_max_attempts_for_phase(")
    degrade_idx = src.index('degraded after 2 attempts: missing')
    assert budget_idx < degrade_idx, (
        "Codex extra-retry block must run BEFORE the degrade block so the "
        "degrade floor (ship-degraded / halt) remains reachable"
    )
    window = src[budget_idx:degrade_idx]
    # Entered only on a genuine gate failure: the line immediately preceding the
    # budget computation is `if not passed:`.
    head = src[:budget_idx]
    assert head.rstrip().rsplit("\n", 1)[-1].strip() == "if not passed:", (
        "extra-retry budget must be guarded by `if not passed:` (only fires "
        "on a real gate failure)"
    )
    # Re-runs the SAME phase and re-validates with the SAME validators.
    assert "run_phase(phase, config, attempt=_codex_attempt)" in window
    assert "_run_phase_validators(" in window
    # NEVER relaxes a gate: no gate-bypass / faked-pass inside the block.
    for forbidden in (
        "passed = True",          # would fake a pass
        "missing = []",           # would erase the unmet-gate list
        "gate_relax",
        "skip_validators",
    ):
        assert forbidden not in window, (
            f"extra-retry block must not contain {forbidden!r} — it must not "
            f"relax/bypass any gate"
        )
    # The extra attempt re-snapshots the containment baseline and re-quarantines
    # overreach (same protection as the attempt-2 retry path), so foreign writes
    # are not hidden.
    assert "_snapshot_file_state(" in window
    assert "_quarantine_phase_overreach(" in window


def test_extra_retry_does_not_touch_verify_shard_or_dedup_paths():
    """Recovering phases (recon/breadth/inventory) are disjoint from the
    verify-shard PoC-repair and semantic_dedup early-`continue` paths, so the
    extra-retry budget cannot interfere with their preservation logic."""
    for shard_phase in ("verify", "verify_aggregate", "semantic_dedup",
                         "sc_semantic_dedup"):
        assert d._is_codex_extra_retry_phase(shard_phase) is False
        assert d._codex_max_attempts_for_phase("codex", shard_phase) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
