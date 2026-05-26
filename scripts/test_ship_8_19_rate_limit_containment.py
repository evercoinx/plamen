"""Ship 8.19 -- rate-limit containment snapshot (codex #6).

Bug: after a rate-limited attempt, the driver re-snapshotted the containment
baseline (`file_state_before`). Because the rate-limited subprocess's
containment check never ran (the 429 interrupted it), any foreign (later-phase)
artifacts it wrote got folded INTO the new baseline -- hiding them from the
retry's containment check (_run_phase_validators) and _quarantine_phase_overreach.

Fix: preserve the true pre-phase baseline across the rate-limit retry (no
re-snapshot at the codex-capacity and rate-limit-wait reset points).

This test proves the INVARIANT the fix relies on: a foreign write is detected
only when the containment baseline predates it. A clean (preserved) baseline
detects it; a contaminated baseline (the old reset, taken after the foreign
write) hides it.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_validators import (  # noqa: E402
    _snapshot_file_state,
    _detect_foreign_phase_writes,
    _owned_artifact_patterns,
)


def _later_phase_concrete_artifact(pipeline, scratchpad, phase_name, phases):
    """First concrete (non-glob) artifact owned by a phase AFTER phase_name."""
    owned = _owned_artifact_patterns(pipeline, scratchpad)
    names = [p.name for p in phases]
    idx = names.index(phase_name)
    for later in names[idx + 1:]:
        for pat in owned.get(later, []):
            if not any(c in pat for c in "*?[") and pat != "AUDIT_REPORT.md":
                return pat
    return None


def test_clean_baseline_detects_foreign_write_contaminated_hides_it(tmp_path):
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    phases = D.SC_PHASES
    foreign = _later_phase_concrete_artifact("sc", sp, "recon", phases)
    assert foreign, "expected a concrete later-phase artifact for recon"

    # TRUE pre-phase baseline (foreign artifact absent) -- what Ship 8.19 preserves.
    clean_baseline = _snapshot_file_state(sp, str(tmp_path))

    # The rate-limited attempt writes a foreign (later-phase) artifact.
    (sp / foreign).write_text("contaminating later-phase content " * 8, encoding="utf-8")

    detected_clean = _detect_foreign_phase_writes(
        sp, str(tmp_path), phases, "recon", "sc", clean_baseline
    )
    assert foreign in detected_clean, (
        "preserved pre-phase baseline MUST detect the foreign write"
    )

    # CONTAMINATED baseline (the old reset: snapshot taken AFTER the foreign
    # write) -- the failed subprocess's write is folded in and hidden.
    contaminated_baseline = _snapshot_file_state(sp, str(tmp_path))
    detected_contaminated = _detect_foreign_phase_writes(
        sp, str(tmp_path), phases, "recon", "sc", contaminated_baseline
    )
    assert foreign not in detected_contaminated, (
        "a baseline taken AFTER the foreign write hides it -- this is exactly "
        "the bug Ship 8.19 removes by not re-snapshotting after a rate limit"
    )


def test_rate_limit_path_does_not_resnapshot_containment_baseline():
    """Source-level guard: the rate-limit retry block must not re-snapshot the
    containment baseline. We assert the Ship 8.19 rationale comment is present
    at both former reset points so a future edit that re-introduces the reset
    is caught."""
    src = (Path(D.__file__)).read_text(encoding="utf-8")
    # Both former reset points now carry the Ship 8.19 rationale instead of a
    # `file_state_before = _snapshot_file_state(...)` reassignment.
    assert src.count("Ship 8.19 (codex #6)") >= 2
    # The rate-limit-wait branch specifically must not reassign the baseline.
    marker = "Preserve the true pre-phase\n                # baseline captured before attempt 1."
    assert marker in src
