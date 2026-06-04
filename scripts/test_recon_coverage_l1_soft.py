"""L1 recon-coverage is a SOFT, recall-preserving check (no retry/halt).

L1 recon (even opus-4.8) repeatedly leaves low-interest infra crates
(tooling/tui/database/utils) uncited across all hinted retries, so the hard
recon-coverage gate just burns 3 attempts + a HALT panel before FC4
auto-completes anyway. For L1 the check is downgraded to non-blocking: the
uncovered modules are RECORDED in scope_leftover.md (visible + flagged for
depth, never silently dropped) and warned, but do not block/retry. SC keeps the
hard gate (SC projects lack these infra crates and do not churn here).
"""
from pathlib import Path

import plamen_driver as d


def _driver_source() -> str:
    return Path(d.__file__).read_text(encoding="utf-8")


def test_record_helper_appends_and_is_idempotent(tmp_path: Path):
    sp = tmp_path
    (sp / "scope_leftover.md").write_text(
        "# Scope Leftover\n\nACKNOWLEDGED: crates/consensus (in-scope, covered)\n",
        encoding="utf-8",
    )
    issues = ["crates/tooling (10 files)", "crates/database (15 files)"]
    n = d._record_recon_uncovered_in_scope_leftover(sp, issues)
    assert n == 2
    txt = (sp / "scope_leftover.md").read_text(encoding="utf-8")
    # original content preserved
    assert "crates/consensus (in-scope, covered)" in txt
    # both uncovered modules recorded, flagged as auto-recorded for review
    assert "AUTO-RECORDED: recon-uncovered" in txt
    assert "crates/tooling (10 files)" in txt
    assert "crates/database (15 files)" in txt
    assert "auto-recorded (recon did not classify)" in txt

    # idempotent: a second call this run does not duplicate the block
    n2 = d._record_recon_uncovered_in_scope_leftover(sp, issues)
    assert n2 == 0
    txt2 = (sp / "scope_leftover.md").read_text(encoding="utf-8")
    assert txt2.count("AUTO-RECORDED: recon-uncovered") == 1


def test_record_helper_creates_file_if_missing(tmp_path: Path):
    sp = tmp_path  # no scope_leftover.md yet
    n = d._record_recon_uncovered_in_scope_leftover(sp, ["crates/tui (26 files)"])
    assert n == 1
    assert "crates/tui (26 files)" in (sp / "scope_leftover.md").read_text(encoding="utf-8")


def test_l1_recon_coverage_soft_sc_hard_branch_in_driver():
    """The recon-coverage gate must branch: L1 -> soft (record + warn, no
    passed=False), SC -> hard (passed=False + missing). Source-grep so the
    split cannot silently regress."""
    src = _driver_source()
    anchor = src.index("coverage_issues = _validate_recon_coverage(")
    region = src[anchor:anchor + 2600]
    # L1 soft path: records to scope_leftover and warns NON-BLOCKING
    assert '_record_recon_uncovered_in_scope_leftover(' in region, (
        "L1 recon-coverage must record uncovered modules (recall-preserving)"
    )
    assert "NON-BLOCKING, L1" in region, "L1 recon-coverage must be non-blocking"
    # SC hard path preserved: still sets passed=False + 'recon coverage:' missing
    assert 'config["pipeline"] == "l1"' in region, "must branch on L1"
    assert '"recon coverage: " + "; ".join(coverage_issues)' in region, (
        "SC recon-coverage must keep the hard gate (passed=False + missing)"
    )


def test_recon_is_in_extended_retry_but_l1_coverage_no_longer_blocks():
    # recon still gets the hinted retry budget for genuine content gaps
    # (missing required artifacts), but the L1 *coverage* sub-check no longer
    # feeds `missing`, so it cannot consume those retries on its own.
    assert d._is_codex_extra_retry_phase("recon") is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
