"""Regression: resume backfill must NOT rewind already-completed verify shards.

The committed verify-queue backfill (c3a54b5) routed dropped inventory IDs into
the ACTIVE verification_queue.md at resume startup. When the verify shards had
ALREADY completed (they verified the queue as it stood), adding new active rows
retroactively made those shards look incomplete — the resume reconciliation
reported "wrote 1/4 verifier files; missing INV-002, INV-004, INV-006" and
rewound the ENTIRE verify stage + skeptic + crossbatch. That is the failure the
user hit on L1 Thorough.

Its prior test (test_verify_queue_no_resume_revert.py) did NOT catch it because
it wrote the verify files AFTER the active backfill, so nothing was ever missing.
This test reproduces the real ORDER: verify files exist for the covered set
(verify completed), THEN the resume backfill runs. The fix routes the dropped IDs
to the EXCLUDED ledger (acknowledge for parity, no verify-file demand) so no
completed shard is invalidated.
"""
from pathlib import Path

import plamen_driver as D
import plamen_mechanical as M
import plamen_parsers as P
import plamen_types as T
import plamen_validators as V
from test_verify_queue_no_resume_revert import (
    _write_inventory,
    _write_partial_canonical_queue,
    _write_verify_files,
    _verify_phases_l1_thorough,
    _COVERED,
    _UNROUTED,
)


def _setup_completed_verify(sp: Path) -> None:
    """Inventory (16) + active queue (13) + verify files for the 13 ONLY.

    Mirrors a run where verify_queue had a 3-ID dropout and the verify shards
    completed against the 13-row queue (so 3 inventory IDs are unrouted AND
    unverified) — exactly the state at the user's resume.
    """
    _write_inventory(sp)
    _write_partial_canonical_queue(sp)   # 13 active rows + JSON sidecar
    _write_verify_files(sp)              # verify_<id>.md for the 13 active rows only


def test_excluded_backfill_closes_parity_without_expanding_active(tmp_path: Path):
    sp = tmp_path
    _setup_completed_verify(sp)
    # dropout present, seen through the JSON sidecar
    assert V._compute_unrouted_inventory_ids(sp) == _UNROUTED
    active_before = {r["finding id"] for r in P.parse_verification_queue_rows(sp)}
    assert active_before == set(_COVERED)

    bf = M.backfill_unrouted_inventory_into_queue(sp, route="excluded")
    assert set(bf) == set(_UNROUTED)

    # parity now closed (dropped IDs acknowledged via the excluded ledger)
    assert V._compute_unrouted_inventory_ids(sp) == []
    # ACTIVE queue UNCHANGED — not expanded, so no new verify-file demand
    active_after = {r["finding id"] for r in P.parse_verification_queue_rows(sp)}
    assert active_after == set(_COVERED)
    # verify-file completeness still clean (13 active all have verify files;
    # the 3 excluded IDs do NOT demand verify_<id>.md)
    assert V._validate_verify_files_for_queue(sp) == []


def test_excluded_backfill_no_reconciliation_rewind(tmp_path: Path):
    """The load-bearing assertion: resume reconciliation rewinds NOTHING."""
    sp = tmp_path
    _setup_completed_verify(sp)
    phases = _verify_phases_l1_thorough()
    completed = [ph.name for ph in phases]
    ckpt = T.Checkpoint(completed=list(completed))

    M.backfill_unrouted_inventory_into_queue(sp, route="excluded")
    removed = D._reconcile_completed_checkpoint_artifacts(
        sp, str(sp), ckpt, phases, "thorough"
    )
    assert removed == [], f"resume rewound completed phases: {removed}"
    for n in completed:
        assert n in ckpt.completed


def test_active_backfill_WOULD_rewind_control(tmp_path: Path):
    """CONTROL (non-vacuous): the OLD active route reintroduces the rewind cause.

    Proves the test actually exercises the failure — with route='active' the
    dropped IDs land in the active queue, demand verify files that don't exist,
    and the verify-completeness check (the thing that drove the rewind) fires.
    """
    sp = tmp_path
    _setup_completed_verify(sp)
    M.backfill_unrouted_inventory_into_queue(sp, route="active")
    active_after = {r["finding id"] for r in P.parse_verification_queue_rows(sp)}
    assert set(_UNROUTED) <= active_after          # active queue expanded
    issues = V._validate_verify_files_for_queue(sp)
    joined = " ".join(issues)
    assert any(u in joined for u in _UNROUTED), (
        "control expected verify-completeness to report the newly-active, "
        f"unverified IDs as missing; got: {issues}"
    )


def test_excluded_backfill_idempotent(tmp_path: Path):
    sp = tmp_path
    _setup_completed_verify(sp)
    first = M.backfill_unrouted_inventory_into_queue(sp, route="excluded")
    assert set(first) == set(_UNROUTED)
    second = M.backfill_unrouted_inventory_into_queue(sp, route="excluded")
    assert second == []
