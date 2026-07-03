"""Tests for the consolidation fix in the report_dedup mechanical phase.

Covers two additions, both inside the NON-HALTING `report_dedup` path
(`_dedup_report_python`, `critical=False`):

  F1 — Quality-Observations retabulation (`_reclassify_cosmetic_low_info_to_qo`):
       an unambiguously cosmetic Low/Info `### [X-NN]` section with NO
       security-impact signal becomes a single `## Quality Observations` table
       row; a security-relevant Low/Info finding keeps its full `###` section.
       Retabulation only — every report ID is preserved (section OR row).

  F2 — same-root-cause superset merge across AND within tiers
       (`_dedup_report_candidate_pairs` now generates same-tier pairs too): the
       merge DECISION stays gated by the unchanged `_dedup_same_fix_ok` /
       superset guards, so a weak/non-superset pair stays KEEP_SEPARATE.

Recall-safety: `_dedup_data_loss_gate` must report ZERO lost items after the
new QO pass (no IDs/locations/impacts/PoCs dropped).

Each test uses plain `assert` so pytest genuinely fails on regression.
Run: `python -m pytest scripts/test_consolidation_qo_crosstier.py -q`
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_mechanical as M  # noqa: E402


def _setup(tmp: Path, report: str, index: str = "") -> tuple[Path, Path]:
    scratch = tmp / "scratch"
    scratch.mkdir()
    proj = tmp / "proj"
    proj.mkdir()
    (proj / "AUDIT_REPORT.md").write_text(report, encoding="utf-8")
    if index:
        (scratch / "report_index.md").write_text(index, encoding="utf-8")
    return scratch, proj


# =============================================================================
# Test 1: cosmetic Low/Info -> QO row; security-relevant Low/Info -> stays a
#         full ### section.
# =============================================================================

_REPORT_QO_MIX = """# Security Audit Report - demo

## Low Findings

### [L-01] Unused import SafeMath

**Severity**: Low
**Location**: `src/Vault.sol:L5`
**Description**: SafeMath is imported but never used after the 0.8 migration.
**Recommendation**: Remove the unused import.

### [L-02] Missing access control on setFee

**Severity**: Low
**Location**: `src/Vault.sol:L88`
**Description**: setFee has no access control modifier, so any caller can change
the protocol fee.
**Impact**:
- Unauthorized fee changes drain protocol revenue.
**Recommendation**: Add an onlyOwner modifier to setFee.

## Informational Findings

### [I-01] Typo in comment

**Severity**: Informational
**Location**: `src/Vault.sol:L200`
**Description**: A typo in the NatSpec comment for withdraw.
**Recommendation**: Fix the spelling.
"""


def test_cosmetic_low_info_retabulated_security_relevant_kept():
    new_text, rows = M._reclassify_cosmetic_low_info_to_qo(_REPORT_QO_MIX)

    moved_ids = {r[0] for r in rows}
    # The two cosmetic findings move to QO rows.
    assert moved_ids == {"L-01", "I-01"}, f"unexpected QO moves: {moved_ids}"

    # Cosmetic sections are removed and a QO table is created.
    assert "### [L-01]" not in new_text, "cosmetic L-01 section must be retabulated away"
    assert "### [I-01]" not in new_text, "cosmetic I-01 section must be retabulated away"
    assert "## Quality Observations" in new_text
    assert "| L-01 |" in new_text and "| I-01 |" in new_text

    # The security-relevant Low finding keeps its full section.
    assert "### [L-02] Missing access control on setFee" in new_text, \
        "security-relevant Low/Info must keep its full section"
    assert "L-02" not in moved_ids

    # RETABULATION: no ID dropped — every original report ID appears somewhere.
    for rid in ("L-01", "L-02", "I-01"):
        assert rid in new_text, f"{rid} disappeared — retabulation must never drop an ID"


def test_qo_pass_preserves_trailing_nonfinding_section():
    """Recall-safety: a cosmetic finding that is the LAST `###` before a
    trailing non-finding section (e.g. a pre-existing `## Quality Observations`
    table or an appendix) must be retabulated WITHOUT swallowing that trailing
    section. `_dedup_report_sections` extends a section to EOF, so naive removal
    would delete the trailing content — `_finding_own_block` must bound it."""
    report = (
        "# Report\n\n## Low Findings\n\n"
        "### [L-05] Dead code in legacy helper\n\n"
        "**Severity**: Low\n**Location**: `src/X.sol:L10`\n"
        "**Description**: Unreachable legacy function.\n"
        "**Recommendation**: Remove it.\n\n"
        "## Quality Observations\n\n"
        "| ID | Title | Severity | Location | Class | Description |\n"
        "|----|-------|----------|----------|-------|-------------|\n"
        "| I-09 | Unused var | Info | src/Y.sol:L1 | Unused variables / parameters | preexisting |\n"
    )
    new_text, rows = M._reclassify_cosmetic_low_info_to_qo(report)
    assert {r[0] for r in rows} == {"L-05"}
    assert "### [L-05]" not in new_text, "cosmetic finding section must be removed"
    assert "I-09" in new_text, "pre-existing QO row must NOT be swallowed"
    assert "| L-05 |" in new_text, "new QO row must be appended"
    assert new_text.count("## Quality Observations") == 1, "no duplicate QO section"
    assert "src/X.sol:L10" in new_text, "location token must survive retabulation"


def test_no_cosmetic_findings_is_noop():
    report = (
        "# Report\n\n## Low Findings\n\n"
        "### [L-01] Missing reentrancy guard on withdraw\n\n"
        "**Severity**: Low\n**Location**: `src/Vault.sol:L9`\n"
        "**Description**: withdraw lacks a reentrancy guard.\n"
        "**Recommendation**: Add nonReentrant.\n"
    )
    new_text, rows = M._reclassify_cosmetic_low_info_to_qo(report)
    assert rows == []
    assert new_text == report, "non-cosmetic report must be returned unchanged"


# =============================================================================
# Test 2: same-root-cause pair -> superset merge into the HIGHER tier.
#   (a) cross-tier via shared source-id subset
#   (b) same-tier via matching Recommendation (same-fix gate)
# =============================================================================

_REPORT_SAME_FIX_SAME_TIER = """# Security Audit Report - demo

## Medium Findings

### [M-13] Unchecked ERC20 return in deposit [VERIFIED]

**Severity**: Medium
**Location**: `src/Token.sol:L42`
**Description**: deposit ignores the boolean return of transferFrom.
**Impact**:
- A non-reverting failed transfer credits the user with no tokens received.
**Recommendation**: Wrap the transferFrom call in SafeERC20 safeTransferFrom so a
failed transfer reverts instead of being silently ignored by the deposit path.

### [M-37] Unchecked ERC20 return in withdraw [VERIFIED]

**Severity**: Medium
**Location**: `src/Token.sol:L77`
**Description**: withdraw ignores the boolean return of transfer.
**Recommendation**: Wrap the transferFrom call in SafeERC20 safeTransferFrom so a
failed transfer reverts instead of being silently ignored by the deposit path.
"""

_INDEX_SAME_TIER = """# Report Index

## Master Finding Index

| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
|-----------|-------|----------|----------|--------------|-----------|--------------------|
| M-13 | Unchecked ERC20 return in deposit | Medium | src/Token.sol:L42 | VERIFIED | - | H-13 |
| M-37 | Unchecked ERC20 return in withdraw | Medium | src/Token.sol:L77 | VERIFIED | - | H-37 |
"""


def test_same_tier_same_fix_pair_is_generated_as_candidate():
    """F2: same-tier pairs are now GENERATED (previously skipped)."""
    recs = M._dedup_report_sections(_REPORT_SAME_FIX_SAME_TIER)
    src = {"M-13": {"H-13"}, "M-37": {"H-37"}}
    pairs = M._dedup_report_candidate_pairs(recs, src)
    same = [p for p in pairs if {p["keep"], p["absorb"]} == {"M-13", "M-37"}]
    assert same, f"expected M-13/M-37 same-tier candidate to be generated, got {pairs}"
    assert any(s.startswith("same-fix-cross-tier") for s in same[0]["signals"]), \
        f"same-fix signal missing on same-tier pair: {same[0]['signals']}"


_REPORT_SUBSET_CROSS_TIER = """# Security Audit Report - demo

## High Findings

### [H-03] Reentrancy in withdraw [VERIFIED]

**Severity**: High
**Location**: `src/Vault.sol:L42`
**Description**: withdraw is reentrant via an external call before state update.
**Impact**:
- An attacker can drain the vault.
**Recommendation**: Apply the checks-effects-interactions pattern and add a
nonReentrant guard to withdraw.

## Low Findings

### [L-09] Reentrancy in withdraw (low-severity restatement) [VERIFIED]

**Severity**: Low
**Location**: `src/Vault.sol:L42`
**Description**: withdraw external call ordering.
**Recommendation**: Apply the checks-effects-interactions pattern and add a
nonReentrant guard to withdraw.
"""

_INDEX_SUBSET_CROSS_TIER = """# Report Index

## Master Finding Index

| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
|-----------|-------|----------|----------|--------------|-----------|--------------------|
| H-03 | Reentrancy in withdraw | High | src/Vault.sol:L42 | VERIFIED | - | H-7 |
| L-09 | Reentrancy in withdraw | Low | src/Vault.sol:L42 | VERIFIED | - | H-7 |
"""


def test_cross_tier_subset_merges_into_higher_tier():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scratch, proj = _setup(tmp, _REPORT_SUBSET_CROSS_TIER, index=_INDEX_SUBSET_CROSS_TIER)
        ok = M._dedup_report_python(scratch, str(proj))
        assert ok is True
        delivered = (proj / "AUDIT_REPORT.md").read_text(encoding="utf-8")
        # Survivor is the HIGHER tier (H-03); the Low restatement is absorbed.
        assert "### [H-03]" in delivered, "higher-tier survivor must remain"
        assert "### [L-09]" not in delivered, "absorbed lower-tier heading must go"
        assert "Consolidated from L-09" in delivered
        # Lossless against the true original.
        assert M._dedup_data_loss_gate(_REPORT_SUBSET_CROSS_TIER, delivered) == []
        mapping = (scratch / "report_dedup_mapping.md").read_text(encoding="utf-8")
        assert "MERGE" in mapping
        assert "DATA-LOSS GATE: PASS" in mapping


# =============================================================================
# Test 3: weak / non-superset pair -> KEEP_SEPARATE (merge decision stays
#         strict even though candidate generation widened).
# =============================================================================

_REPORT_WEAK_PAIR = """# Security Audit Report - demo

## Medium Findings

### [M-01] Increase the minimum deposit floor [VERIFIED]

**Severity**: Medium
**Location**: `src/Vault.sol:L42`
**Description**: The deposit floor is too low and enables dust spam.
**Recommendation**: Increase the minimum deposit amount to a safe floor value.

### [M-02] Decrease the maximum withdrawal cap [VERIFIED]

**Severity**: Medium
**Location**: `src/Vault.sol:L42`
**Description**: The withdrawal cap is too high.
**Recommendation**: Decrease the maximum withdrawal amount to a safe ceiling value.
"""

_INDEX_WEAK_PAIR = """# Report Index

## Master Finding Index

| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
|-----------|-------|----------|----------|--------------|-----------|--------------------|
| M-01 | Increase the minimum deposit floor | Medium | src/Vault.sol:L42 | VERIFIED | - | H-1 |
| M-02 | Decrease the maximum withdrawal cap | Medium | src/Vault.sol:L42 | VERIFIED | - | H-2 |
"""


def test_weak_non_superset_pair_keeps_separate():
    """Shared LOCATION but opposite-direction fixes (antonym) + distinct
    source IDs → the same-fix gate vetoes, so the pair stays KEEP_SEPARATE."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scratch, proj = _setup(tmp, _REPORT_WEAK_PAIR, index=_INDEX_WEAK_PAIR)
        ok = M._dedup_report_python(scratch, str(proj))
        assert ok is True
        delivered = (proj / "AUDIT_REPORT.md").read_text(encoding="utf-8")
        # Both findings survive — no merge.
        assert "### [M-01]" in delivered and "### [M-02]" in delivered
        assert "Consolidated from" not in delivered
        mapping = (scratch / "report_dedup_mapping.md").read_text(encoding="utf-8")
        assert "KEEP_SEPARATE" in mapping
        assert "MERGE" not in mapping.replace("Merges proposed: 0", "")


# =============================================================================
# Test 4: _dedup_data_loss_gate passes (no IDs/content dropped) after the new
#         QO retabulation pass runs end-to-end through the phase.
# =============================================================================

_INDEX_QO_MIX = """# Report Index

## Master Finding Index

| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
|-----------|-------|----------|----------|--------------|-----------|--------------------|
| L-01 | Unused import SafeMath | Low | src/Vault.sol:L5 | VERIFIED | - | H-1 |
| L-02 | Missing access control on setFee | Low | src/Vault.sol:L88 | VERIFIED | - | H-2 |
| I-01 | Typo in comment | Informational | src/Vault.sol:L200 | VERIFIED | - | H-3 |
"""


def test_data_loss_gate_passes_after_qo_pass():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scratch, proj = _setup(tmp, _REPORT_QO_MIX, index=_INDEX_QO_MIX)
        ok = M._dedup_report_python(scratch, str(proj))
        assert ok is True
        delivered = (proj / "AUDIT_REPORT.md").read_text(encoding="utf-8")

        # Mechanical zero-data-loss gate against the TRUE original must be clean.
        assert M._dedup_data_loss_gate(_REPORT_QO_MIX, delivered) == [], \
            "QO retabulation must not lose any location/impact/PoC content"

        # Cosmetic findings became QO rows; security finding kept its section.
        assert "## Quality Observations" in delivered
        assert "| L-01 |" in delivered and "| I-01 |" in delivered
        assert "### [L-02] Missing access control on setFee" in delivered

        # Every report ID is still accounted for somewhere (no silent drop).
        for rid in ("L-01", "L-02", "I-01"):
            assert rid in delivered, f"{rid} lost — retabulation must preserve every ID"

        # Mapping records the QO count and a PASS.
        mapping = (scratch / "report_dedup_mapping.md").read_text(encoding="utf-8")
        assert "Quality-Observation retabulations: 2" in mapping
        assert "DATA-LOSS GATE: PASS" in mapping


def test_phase_is_haltless_on_internal_error(monkeypatch):
    """report_dedup is critical=False: an internal exception in the new QO pass
    must degrade gracefully (return True, original report untouched)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scratch, proj = _setup(tmp, _REPORT_QO_MIX, index=_INDEX_QO_MIX)

        def _boom(_text):
            raise RuntimeError("forced QO failure")

        monkeypatch.setattr(M, "_reclassify_cosmetic_low_info_to_qo", _boom)
        ok = M._dedup_report_python(scratch, str(proj))
        assert ok is True, "phase must not halt on QO internal error"
        delivered = (proj / "AUDIT_REPORT.md").read_text(encoding="utf-8")
        # Degraded gracefully: original report still has its full sections.
        assert "### [L-01]" in delivered and "### [L-02]" in delivered


# =============================================================================
# Candidate broadening (option B): SAME-SOURCE-FILE pairs whose titles share no
# anchor and whose location lines differ now reach the STRICT same-fix gate.
# Only candidate generation widens; the merge decision is unchanged.
# =============================================================================

_REPORT_SAME_FILE_SAME_FIX = """# Security Audit Report - demo

## Medium Findings

### [M-20] Bridge accounting drifts on partial fills [VERIFIED]

**Severity**: Medium
**Location**: `src/Bridge.sol:L88`
**Description**: partial fills are not reconciled against the moved balance.
**Recommendation**: Recompute the stored reserve from the actual transferred
balance after each settlement and revert when the post-settlement invariant
does not hold.

## Low Findings

### [L-15] Stored reserve never reconciled after settlement [VERIFIED]

**Severity**: Low
**Location**: `src/Bridge.sol:L201`
**Description**: reserve bookkeeping diverges after a settlement.
**Recommendation**: Recompute the stored reserve from the actual transferred
balance after each settlement and revert when the post-settlement invariant
does not hold.
"""

_REPORT_SAME_FILE_DIFFERENT_FIX = _REPORT_SAME_FILE_SAME_FIX.replace(
    "**Recommendation**: Recompute the stored reserve from the actual transferred\n"
    "balance after each settlement and revert when the post-settlement invariant\n"
    "does not hold.\n\n## Low Findings",
    "**Recommendation**: Add a nonReentrant modifier to the deposit entrypoint so\n"
    "concurrent reentrant calls cannot interleave the bridge state machine.\n\n"
    "## Low Findings",
)


def test_same_file_divergent_title_reaches_same_fix_gate():
    """Broadening: same-file pair (no shared anchor, different lines) now reaches
    the strict same-fix gate via the file signal; identical Recommendation ->
    MERGE candidate; survivor is the higher-severity finding."""
    recs = M._dedup_report_sections(_REPORT_SAME_FILE_SAME_FIX)
    src = {"M-20": {"H-20"}, "L-15": {"H-15"}}  # distinct -> no source-id-subset
    pairs = M._dedup_report_candidate_pairs(recs, src)
    p = [x for x in pairs if {x["keep"], x["absorb"]} == {"M-20", "L-15"}]
    assert p, f"same-file divergent-title pair not generated: {pairs}"
    assert any("same-fix-cross-tier[file]" in s for s in p[0]["signals"]), p[0]["signals"]
    assert p[0]["keep"] == "M-20", "survivor must be the higher-severity finding"


def test_same_file_different_fix_gets_no_same_fix_flag():
    """Recall guard: same file but DIFFERENT Recommendation must NOT earn the
    same-fix merge flag (the strict gate, unchanged, vetoes it)."""
    recs = M._dedup_report_sections(_REPORT_SAME_FILE_DIFFERENT_FIX)
    src = {"M-20": {"H-20"}, "L-15": {"H-15"}}
    pairs = M._dedup_report_candidate_pairs(recs, src)
    for x in pairs:
        if {x["keep"], x["absorb"]} == {"M-20", "L-15"}:
            assert not any("same-fix-cross-tier" in s for s in x["signals"]), \
                f"different-fix pair must not carry same-fix flag: {x['signals']}"


# =============================================================================
# Test 6: identical-location cross-tier dupe — the exact shape the early
#         same-fix branch targets. Two agents flag the SAME bug at the IDENTICAL
#         `file:Lstart-Lend` token (one Low, one Info), with thin / divergent
#         Recommendations and NO antonym conflict. The Jaccard floor and the
#         thin-recommendation veto are bypassed by the identical-location
#         branch, so the pair MERGES; the survivor is the higher tier (Low).
# =============================================================================

_REPORT_IDENTICAL_LOC_CROSSTIER = """# Security Audit Report - demo

## Low Findings

### [L-10] Return value of the transfer call is not checked

**Severity**: Low
**Location**: `src/Module.sol:L100-L120`
**Description**: The external call return value is ignored in this routine.
**Recommendation**: Handle the return value.

## Informational Findings

### [I-07] Unchecked external call result

**Severity**: Informational
**Location**: `src/Module.sol:L100-L120`
**Description**: The same external call result is silently discarded.
**Recommendation**: Validate it.
"""


def test_identical_location_crosstier_dupe_merges_at_higher_tier():
    """Identical `file:Lstart-Lend` token, distinct source IDs, thin/divergent
    Recommendations, no antonym -> the identical-location same-fix branch
    bypasses the Jaccard floor and thin-fix veto, so the pair is a MERGE
    candidate whose survivor is the higher-severity (Low over Info) finding."""
    recs = M._dedup_report_sections(_REPORT_IDENTICAL_LOC_CROSSTIER)
    src = {"L-10": {"H-10"}, "I-07": {"H-7"}}  # distinct -> no source-id-subset
    pairs = M._dedup_report_candidate_pairs(recs, src)
    p = [x for x in pairs if {x["keep"], x["absorb"]} == {"L-10", "I-07"}]
    assert p, f"identical-location cross-tier pair not generated: {pairs}"
    assert any("identical-location cross-tier" in s for s in p[0]["signals"]), \
        p[0]["signals"]
    assert p[0]["keep"] == "L-10", "survivor must be the higher-severity finding"


def test_dedup_same_fix_ok_identical_location_thin_recs_merge():
    """Unit-level: thin/empty Recommendation pair at the same location passes the
    same-fix gate via the early identical-location branch (no antonym)."""
    a = {"locations": {"src/Module.sol:L100-L120"}, "fix_text": "Fix it.",
         "desc_text": "An external call result is discarded."}
    b = {"locations": {"src/Module.sol:L100-L120"}, "fix_text": "",
         "desc_text": "The same external call result is discarded."}
    ok, reason = M._dedup_same_fix_ok(a, b)
    assert ok is True, reason
    assert "identical-location cross-tier" in reason


def test_dedup_same_fix_ok_identical_location_antonym_still_vetoes():
    """The antonym veto survives inside the early branch: identical location but
    opposite-direction fixes still stay separate (falls back to desc text)."""
    a = {"locations": {"src/Module.sol:L100-L120"}, "fix_text": "",
         "desc_text": "Increase the minimum bound to a safe floor."}
    b = {"locations": {"src/Module.sol:L100-L120"}, "fix_text": "",
         "desc_text": "Decrease the maximum bound to a safe ceiling."}
    ok, reason = M._dedup_same_fix_ok(a, b)
    assert ok is False, reason
    assert "antonym" in reason
