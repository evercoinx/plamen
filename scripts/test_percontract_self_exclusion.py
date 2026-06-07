"""BUG 2 / M-04 — Phase 3c per-contract belief-based self-exclusion.

A per-contract agent is GIVEN an orchestrator-built exclusion list (finding IDs
+ locations from analysis_*.md + analysis_rescan_*.md) and told "do not
re-report exclusion-list findings". Nothing constrains the PROVENANCE of an
exclusion, so an agent self-generated an "## Exclusion List (Already Found)"
section asserting a real bug was already known when NO provided finding
contained it — silently dropping a true positive.

Fix:
- PROMPT: an exclusion is valid only if it cites a concrete entry present in the
  provided exclusion list; a believed-known bug with no provided-list referent
  MUST be emitted (rules/phase3b-rescan-prompt.md + prompts/shared/v2/...).
- GATE: _validate_percontract_self_exclusion flags referent-less exclusions and
  the driver re-emits them to analysis_percontract_reemit.md (declared into
  rescan_manifest.md so the exact gate still accepts it).

Recall-safe: the validator ONLY ever adds candidates; never removes/refutes a
finding, never hard-fails a clean run.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_validators import (  # noqa: E402
    _validate_percontract_self_exclusion,
    _rescan_manifest_exact_missing,
    gate_passes,
)

RESCAN = next(p for p in D.SC_PHASES if p.name == "rescan")


def _write(sp: Path, name: str, text: str) -> None:
    (sp / name).write_text(text, encoding="utf-8")


# A first-pass breadth finding the per-contract agent could legitimately cite.
PROVIDED_BREADTH = (
    "# Breadth Agent 1\n\n"
    "## Findings\n\n"
    "## Finding [B1-4]: Reentrancy in withdraw\n\n"
    "**Severity**: High\n"
    "**Location**: vault.sol:L120\n"
    "**Description**: classic reentrancy.\n"
)

RESCAN_FINDING = (
    "# Re-Scan Agent 1\n\n"
    "## Findings\n\n"
    "## Finding [RS1-3]: Stale price\n\n"
    "**Severity**: Medium\n"
    "**Location**: AccountEncoder.sol:L88\n"
    "**Description**: stale.\n"
)


# --------------------------------------------------------------------------
# (a) DROP-REPRODUCTION: referent-less self-exclusion -> flagged + re-emitted
# --------------------------------------------------------------------------

def test_drop_reproduction_referentless_exclusion_flagged_and_reemitted(tmp_path):
    _write(tmp_path, "analysis_1.md", PROVIDED_BREADTH)
    _write(tmp_path, "analysis_rescan_1.md", RESCAN_FINDING)
    # The agent invents an exclusion for a bug (isWritable byte-width) whose only
    # "referent" is an ID/location absent from analysis_*/analysis_rescan_*.
    _write(
        tmp_path,
        "analysis_percontract_3.md",
        "# Per-Contract Agent 3\n\n"
        "## Findings\n\n"
        "## Finding [PC3-1]: Some real finding\n\n"
        "**Severity**: Low\n"
        "**Location**: AccountEncoder.sol:L200\n"
        "**Description**: ok.\n\n"
        "## Exclusion List (Already Found - Not Duplicated)\n\n"
        "- EXCLUDED [PC3-9] isWritable byte-width mismatch at "
        "AccountEncoder.sol:L412 (dup of SL-1/CC-7)\n",
    )

    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert warnings, "referent-less exclusion must produce a warning"
    assert recovered, "the suppressed candidate must be recovered for re-emit"

    # Driver side effect writes the re-emit artifact.
    out = D._reemit_percontract_self_exclusions(tmp_path, recovered)
    assert out is not None and out.exists()
    body = out.read_text(encoding="utf-8")
    assert "## Finding [PCRE-1]" in body
    assert "[RE-EMITTED: self-excluded without real referent]" in body
    assert "AccountEncoder.sol:L412".lower() in body.lower()
    # Declared into the manifest so the exact gate accepts it.
    manifest = (tmp_path / "rescan_manifest.md").read_text(encoding="utf-8")
    assert "analysis_percontract_reemit.md" in manifest


# --------------------------------------------------------------------------
# (b) HEALTHY NO-OP: exclusion citing a real provided ID -> no flag
# --------------------------------------------------------------------------

def test_healthy_noop_real_referent_by_id(tmp_path):
    _write(tmp_path, "analysis_1.md", PROVIDED_BREADTH)
    _write(
        tmp_path,
        "analysis_percontract_2.md",
        "# Per-Contract Agent 2\n\n"
        "## Findings\n\n"
        "## Finding [PC2-1]: real one\n\n"
        "**Severity**: Medium\n"
        "**Location**: vault.sol:L5\n"
        "**Description**: x.\n\n"
        "## Exclusion List\n\n"
        "- EXCLUDED [PC2-7] reentrancy in withdraw dup of [B1-4]\n",
    )
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert warnings == []
    assert recovered == []
    assert not (tmp_path / "analysis_percontract_reemit.md").exists()


def test_healthy_noop_real_referent_by_location(tmp_path):
    _write(tmp_path, "analysis_1.md", PROVIDED_BREADTH)
    _write(tmp_path, "analysis_rescan_1.md", RESCAN_FINDING)
    _write(
        tmp_path,
        "analysis_percontract_2.md",
        "# Per-Contract Agent 2\n\n"
        "## Findings\n\n"
        "## Exclusion List\n\n"
        "- EXCLUDED [PC2-3] stale price dup of AccountEncoder.sol:L88\n",
    )
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert warnings == []
    assert recovered == []


def test_healthy_noop_real_referent_by_location_pathprefix(tmp_path):
    # Location in universe is a/b/AccountEncoder.sol:L88; citation uses basename.
    _write(tmp_path, "analysis_1.md", PROVIDED_BREADTH)
    _write(
        tmp_path,
        "analysis_rescan_1.md",
        "# Re-Scan 1\n\n## Findings\n\n"
        "## Finding [RS1-3]: stale\n\n"
        "**Severity**: Medium\n"
        "**Location**: contracts/AccountEncoder.sol:L88\n"
        "**Description**: x.\n",
    )
    _write(
        tmp_path,
        "analysis_percontract_1.md",
        "# PC 1\n\n## Findings\n\n## Exclusion List\n\n"
        "- EXCLUDED [PC1-2] dup of AccountEncoder.sol:88\n",
    )
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert warnings == []
    assert recovered == []


# --------------------------------------------------------------------------
# (c) RECALL-SAFETY: never false-fire / never crash on a clean run
# --------------------------------------------------------------------------

def test_recall_safe_no_exclusion_section(tmp_path):
    _write(tmp_path, "analysis_1.md", PROVIDED_BREADTH)
    _write(
        tmp_path,
        "analysis_percontract_1.md",
        "# PC 1\n\n## Findings\n\n"
        "## Finding [PC1-1]: only findings here\n\n"
        "**Severity**: Low\n**Location**: vault.sol:L9\n**Description**: x.\n",
    )
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert warnings == []
    assert recovered == []
    assert not (tmp_path / "analysis_percontract_reemit.md").exists()


def test_recall_safe_no_percontract_files(tmp_path):
    _write(tmp_path, "analysis_1.md", PROVIDED_BREADTH)
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert warnings == []
    assert recovered == []


def test_recall_safe_empty_scratchpad(tmp_path):
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert (warnings, recovered) == ([], [])


def test_recall_safe_unparseable_percontract_file(tmp_path):
    # Binary / non-UTF8 content must not raise.
    (tmp_path / "analysis_percontract_1.md").write_bytes(b"\xff\xfe\x00\x01garbage")
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert isinstance(warnings, list) and isinstance(recovered, list)


def test_reemit_noop_on_empty_recovered(tmp_path):
    assert D._reemit_percontract_self_exclusions(tmp_path, []) is None
    assert not (tmp_path / "analysis_percontract_reemit.md").exists()


# --------------------------------------------------------------------------
# (mixed) only the referent-less entry is flagged; the valid one is left alone
# --------------------------------------------------------------------------

def test_mixed_only_referentless_entry_flagged(tmp_path):
    _write(tmp_path, "analysis_1.md", PROVIDED_BREADTH)
    _write(
        tmp_path,
        "analysis_percontract_4.md",
        "# PC 4\n\n## Findings\n\n## Exclusion List (Already Found)\n\n"
        "- EXCLUDED [PC4-1] reentrancy dup of [B1-4]\n"
        "- EXCLUDED [PC4-2] isWritable byte-width at Enc.sol:L9 (believed known)\n",
    )
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert len(recovered) == 1, "exactly one referent-less entry expected"
    assert "isWritable" in recovered[0]["title"] or "isWritable" in recovered[0]["line_text"]
    # The validly-excluded [B1-4] entry must NOT be re-emitted.
    joined = " ".join(c["line_text"] for c in recovered)
    assert "B1-4" not in joined


# --------------------------------------------------------------------------
# GATE-ACCEPTANCE: re-emit file + manifest declaration still passes the gate
# --------------------------------------------------------------------------

def test_gate_acceptance_after_reemit_non_fresh(tmp_path):
    # Non-fresh scratchpad (no sentinel): exact gate uses presence+size.
    sub = "Checked declared scope. " * 30
    _write(tmp_path, "analysis_rescan_1.md", "# RS1\n\n## No Findings\n\n" + sub)
    _write(
        tmp_path,
        "analysis_percontract_1.md",
        "# PC1\n\n## Findings\n\n## Exclusion List\n\n"
        "- EXCLUDED [PC1-9] isWritable at Enc.sol:L9 (believed known)\n" + sub,
    )
    (tmp_path / "rescan_manifest.md").write_text(
        "# Rescan Manifest\n- analysis_rescan_1.md\n- analysis_percontract_1.md\n",
        encoding="utf-8",
    )
    warnings, recovered = _validate_percontract_self_exclusion(tmp_path)
    assert recovered
    D._reemit_percontract_self_exclusions(tmp_path, recovered)

    # Exact gate must still pass: the driver-written re-emit file is substantive,
    # marked COMPLETE, and structurally complete (has a ## Finding block).
    missing = _rescan_manifest_exact_missing(tmp_path, RESCAN)
    assert missing == [], f"exact gate stranded on: {missing}"
    passed, gmissing = gate_passes(tmp_path, str(tmp_path), RESCAN)
    assert passed, f"gate_passes(rescan) failed: {gmissing}"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
