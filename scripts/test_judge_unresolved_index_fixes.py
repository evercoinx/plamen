"""Tests for the four mechanical report-index / dedup / components fixes.

FIX #1 — Skeptic-Judge UNRESOLVED rulings flow into the mechanical
         report-index builder (`_write_mechanical_report_index`) even when the
         verifier text says CONFIRMED (the INV-004 case): demote once + stamp
         Trust Adj. + drive the body [UNRESOLVED] flag.
FIX #2 — the Trust Adj. token is the paren form `UNRESOLVED(<pre-demote-sev>)`
         so the body UNRESOLVED tagger regex matches.
FIX #1 SC parity — `_repair_sc_report_index_from_prior` applies the same
         judge-UNRESOLVED demotion + paren stamp.
FIX #5 — supplemental mechanical dedup: exact file:line + same severity tier
         merges at title >= 0.5; adjacent-but-different lines / different tier
         do NOT merge.
FIX #7 — Components Audited table populates when prose subsystem headings map
         to path tokens (presentation-only).

Fixtures are synthetic (NOT scratchpad copies) to avoid overfitting.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_mechanical import (  # noqa: E402
    _apply_mechanical_dedup_from_pairs,
    _repair_sc_report_index_from_prior,
    _synthesize_components_audited,
    _write_mechanical_report_index,
)


# --------------------------------------------------------------------------
# Shared fixture helpers
# --------------------------------------------------------------------------
def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def _master_index_rows(index_text: str) -> list[list[str]]:
    """Return Master Finding Index data rows as cell-lists."""
    rows: list[list[str]] = []
    in_index = False
    for line in index_text.splitlines():
        s = line.strip()
        if s.startswith("## "):
            in_index = "master finding index" in s.lower()
            continue
        if not in_index or not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        low = [c.lower() for c in cells]
        if "report id" in low or set("".join(cells)) <= set("-| :"):
            continue
        rows.append(cells)
    return rows


def _codex_l1_scratchpad(tmp_path: Path) -> Path:
    sp = tmp_path
    _write(sp / "config.json", '{"cli_backend": "codex", "mode": "thorough"}')
    # One CONFIRMED finding INV-004 in the verification queue.
    _write(
        sp / "verification_queue.md",
        "# Verification Queue\n\n"
        "| Finding ID | Severity | Title | Location | Preferred Tag |\n"
        "|------------|----------|-------|----------|---------------|\n"
        "| INV-004 | High | Block reorg invariant violated | "
        "consensus/fork_choice.rs:L120 | CODE-TRACE |\n",
    )
    # Verifier CONFIRMED — without FIX #1 this would promote at High, untagged.
    _write(
        sp / "verify_INV-004.md",
        "# Verify INV-004\n\n"
        "**Verdict**: CONFIRMED\n"
        "**Severity**: High\n"
        "**Location**: consensus/fork_choice.rs:L120\n"
        "**Evidence Tag**: [CODE-TRACE]\n\n"
        "Description of the confirmed block reorg invariant violation.\n",
    )
    # Skeptic-Judge OVERRIDES: rules INV-004 UNRESOLVED.
    _write(
        sp / "skeptic_judge_decisions.md",
        "# Skeptic-Judge Decisions\n\n"
        "| Finding ID | Original Severity | Final Severity | Decision | Rationale |\n"
        "|------------|-------------------|----------------|----------|-----------|\n"
        "| INV-004 | High | High | UNRESOLVED | Verifier and skeptic disagree on reachability |\n",
    )
    return sp


# --------------------------------------------------------------------------
# (a) codex+L1: judge UNRESOLVED over a CONFIRMED verifier demotes + paren stamp
# --------------------------------------------------------------------------
def test_fix1_judge_unresolved_demotes_confirmed(tmp_path):
    sp = _codex_l1_scratchpad(tmp_path)
    n = _write_mechanical_report_index(sp)
    assert n  # rows written
    index_text = (sp / "report_index.md").read_text(encoding="utf-8")
    rows = _master_index_rows(index_text)
    inv_rows = [r for r in rows if any("INV-004" in c for c in r)]
    assert inv_rows, f"INV-004 missing from index:\n{index_text}"
    row = inv_rows[0]
    joined = " | ".join(row)
    # Demoted once: High -> Medium.
    assert "Medium" in row, f"expected demotion to Medium, got row: {row}"
    assert "High" not in [c for c in row if c == "High"], (
        f"severity column should not still be High: {row}"
    )
    # Report ID is in the Medium tier (M-XX), proving the tier moved with sev.
    assert any(re.fullmatch(r"M-\d+", c) for c in row), (
        f"expected M-tier report ID after demotion: {row}"
    )
    # FIX #2: paren form capturing PRE-demote severity present in Trust Adj.
    assert re.search(r"UNRESOLVED\(High\)", joined), (
        f"expected paren-form UNRESOLVED(High) Trust Adj.: {joined}"
    )


# --------------------------------------------------------------------------
# (b) the paren form satisfies the body-tagger regex
# --------------------------------------------------------------------------
def test_fix2_paren_form_matches_body_tagger_regex():
    # This is the exact regex used by the body UNRESOLVED tagger (~line 2375).
    body_tagger_re = re.compile(r"\b(?:UNRESOLVED|PARTIAL)\s*\(", re.IGNORECASE)
    assert body_tagger_re.search("UNRESOLVED(High)")
    assert body_tagger_re.search("SKEPTIC-DOWNGRADE(High), UNRESOLVED(Critical)")
    # The bare token (pre-fix output) does NOT match — proving the fix matters.
    assert not body_tagger_re.search("UNRESOLVED")


def test_fix1_and_2_index_output_is_body_taggable(tmp_path):
    """The emitted Trust Adj. cell must be detectable by the body tagger."""
    sp = _codex_l1_scratchpad(tmp_path)
    _write_mechanical_report_index(sp)
    index_text = (sp / "report_index.md").read_text(encoding="utf-8")
    body_tagger_re = re.compile(r"\b(?:UNRESOLVED|PARTIAL)\s*\(", re.IGNORECASE)
    assert body_tagger_re.search(index_text), (
        "report_index Trust Adj. not body-taggable (re-degrade risk)"
    )


def test_fix1_no_double_demotion(tmp_path):
    """Verifier UNRESOLVED + judge UNRESOLVED demote AT MOST ONCE total."""
    sp = tmp_path
    _write(sp / "config.json", '{"cli_backend": "codex"}')
    _write(
        sp / "verification_queue.md",
        "| Finding ID | Severity | Title | Location | Preferred Tag |\n"
        "|------------|----------|-------|----------|---------------|\n"
        "| INV-009 | Critical | Double-source unresolved | a/b.rs:L5 | CODE-TRACE |\n",
    )
    # Verifier ALSO says UNRESOLVED.
    _write(
        sp / "verify_INV-009.md",
        "**Verdict**: UNRESOLVED\n**Severity**: Critical\n**Location**: a/b.rs:L5\n",
    )
    _write(
        sp / "skeptic_judge_decisions.md",
        "| Finding ID | Original Severity | Final Severity | Decision | Rationale |\n"
        "|------------|-------------------|----------------|----------|-----------|\n"
        "| INV-009 | Critical | Critical | UNRESOLVED | both agree unresolved |\n",
    )
    _write_mechanical_report_index(sp)
    index_text = (sp / "report_index.md").read_text(encoding="utf-8")
    rows = _master_index_rows(index_text)
    inv = [r for r in rows if any("INV-009" in c for c in r)][0]
    # Critical demoted ONCE -> High (not twice -> Medium).
    assert "High" in inv, f"expected single demotion Critical->High: {inv}"
    assert "Medium" not in inv, f"double-demotion detected: {inv}"
    # Only a single UNRESOLVED(...) token, capturing pre-demote Critical.
    joined = " | ".join(inv)
    assert joined.count("UNRESOLVED(") == 1, f"duplicate UNRESOLVED stamp: {joined}"
    assert "UNRESOLVED(Critical)" in joined, joined


# --------------------------------------------------------------------------
# (c) SC parity via _repair_sc_report_index_from_prior
# --------------------------------------------------------------------------
def _sc_prior_index_scratchpad(tmp_path: Path) -> Path:
    sp = tmp_path
    _write(sp / "config.json", '{"cli_backend": "claude", "mode": "thorough"}')
    # A prior (LLM) report_index backup that the repair reads.
    prior = (
        "# Report Index\n\n"
        "## Summary\n\n"
        "| Severity | Count |\n|----------|-------|\n| High | 1 |\n\n"
        "## Master Finding Index\n\n"
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis ID |\n"
        "|-----------|-------|----------|----------|--------------|------------|------------------------|\n"
        "| H-01 | Reorg invariant | High | src/Fork.sol:L120 | VERIFIED | - | H-7 |\n"
    )
    # The repair reads the latest backup; write the canonical name too.
    _write(sp / "report_index.md.bak", prior)
    _write(sp / "report_index.md", prior)
    # Judge rules the SAME finding UNRESOLVED.
    _write(
        sp / "skeptic_judge_decisions.md",
        "| Finding ID | Original Severity | Final Severity | Decision | Rationale |\n"
        "|------------|-------------------|----------------|----------|-----------|\n"
        "| H-7 | High | High | UNRESOLVED | contested |\n",
    )
    return sp


def test_fix1_sc_parity_repair_demotes_and_stamps(tmp_path):
    sp = _sc_prior_index_scratchpad(tmp_path)
    changed = _repair_sc_report_index_from_prior(sp)
    index_text = (sp / "report_index.md").read_text(encoding="utf-8")
    assert changed, f"SC repair reported no change:\n{index_text}"
    rows = _master_index_rows(index_text)
    target = [r for r in rows if any("H-7" in c for c in r)]
    assert target, f"H-7 row missing after SC repair:\n{index_text}"
    row = target[0]
    joined = " | ".join(row)
    # Demoted High -> Medium and re-tiered to M-XX.
    assert "Medium" in row, f"expected SC demotion to Medium: {row}"
    assert any(re.fullmatch(r"M-\d+", c) for c in row), (
        f"expected M-tier report ID after SC demotion: {row}"
    )
    assert re.search(r"UNRESOLVED\(High\)", joined), (
        f"expected SC paren-form UNRESOLVED(High): {joined}"
    )


def test_fix1_sc_parity_no_trust_column_does_not_drop_finding(tmp_path):
    """RECALL-SAFETY: when the prior LLM index has NO Trust Adj. column, a
    judge-UNRESOLVED ruling must NEVER drop the finding row. The demote may
    fire un-tokenized (acceptable: finding stays in body, one tier lower), but
    the row must still be present so the true positive survives to the report.
    """
    sp = tmp_path
    _write(sp / "config.json", '{"cli_backend": "claude", "mode": "thorough"}')
    # Prior index WITHOUT a Trust Adj. column (trust_i resolves to -1).
    prior = (
        "# Report Index\n\n"
        "## Summary\n\n"
        "| Severity | Count |\n|----------|-------|\n| Critical | 1 |\n\n"
        "## Master Finding Index\n\n"
        "| Report ID | Title | Severity | Location | Verification | Internal Hypothesis ID |\n"
        "|-----------|-------|----------|----------|--------------|------------------------|\n"
        "| C-01 | Reorg invariant | Critical | src/Fork.sol:L120 | VERIFIED | H-7 |\n"
    )
    _write(sp / "report_index.md.bak", prior)
    _write(sp / "report_index.md", prior)
    _write(
        sp / "skeptic_judge_decisions.md",
        "| Finding ID | Original Severity | Final Severity | Decision | Rationale |\n"
        "|------------|-------------------|----------------|----------|-----------|\n"
        "| H-7 | Critical | Critical | UNRESOLVED | contested |\n",
    )
    _repair_sc_report_index_from_prior(sp)
    index_text = (sp / "report_index.md").read_text(encoding="utf-8")
    rows = _master_index_rows(index_text)
    # The finding MUST still be present (no silent drop).
    target = [r for r in rows if any("H-7" in c for c in r)]
    assert target, f"RECALL VIOLATION: H-7 dropped after SC repair:\n{index_text}"
    # Demotion is at most one tier (Critical -> High) with the floor honored;
    # never demoted out of existence or below report severity.
    row = target[0]
    assert "Medium" not in row, f"unexpected double-demotion: {row}"


# --------------------------------------------------------------------------
# (d) FIX #5: exact-location + same-tier merges; adjacent/different-tier do NOT
# --------------------------------------------------------------------------
def _dedup_scratchpad(tmp_path: Path, rows: list[str]) -> Path:
    sp = tmp_path
    _write(sp / "config.json", '{"cli_backend": "codex"}')
    header = (
        "# Dedup Candidate Pairs (full)\n\n"
        "| Finding A | Finding B | Score | Signal | Same Severity? |\n"
        "|-----------|-----------|-------|--------|----------------|\n"
    )
    _write(sp / "dedup_candidate_pairs_full.md", header + "\n".join(rows) + "\n")
    return sp


def _present_inventory(sp: Path, ids: list[str]) -> None:
    lines = ["# Findings Inventory", ""]
    for i in ids:
        lines.append(f"## Finding [{i}]")
        lines.append("")
    _write(sp / "findings_inventory.md", "\n".join(lines))


def test_fix5_exact_location_same_tier_merges(tmp_path):
    # Title score 0.6 (< 1.0 old threshold) but EXACT same line range L120-130.
    sp = _dedup_scratchpad(
        tmp_path,
        [
            "| INV-001: bug | INV-002: bug variant | 0.60 | "
            "location overlap (L120-130 vs L120-130) | yes |",
        ],
    )
    _present_inventory(sp, ["INV-001", "INV-002"])
    merges = _apply_mechanical_dedup_from_pairs(
        sp, "sc_semantic_dedup", supplemental=True
    )
    assert merges == 1, "exact-location + same-tier pair should merge at title>=0.5"


def test_fix5_adjacent_lines_do_not_merge(tmp_path):
    # Same file, ADJACENT but DIFFERENT line ranges — must NOT merge.
    sp = _dedup_scratchpad(
        tmp_path,
        [
            "| INV-001: bug | INV-002: other | 0.60 | "
            "location overlap (L120-130 vs L131-140) | yes |",
        ],
    )
    _present_inventory(sp, ["INV-001", "INV-002"])
    merges = _apply_mechanical_dedup_from_pairs(
        sp, "sc_semantic_dedup", supplemental=True
    )
    assert merges == 0, "adjacent-but-different lines must NOT merge under FIX #5"


def test_fix5_different_tier_does_not_merge(tmp_path):
    # Exact same line range but DIFFERENT severity tier (same? = no).
    sp = _dedup_scratchpad(
        tmp_path,
        [
            "| INV-001: bug | INV-002: bug | 0.90 | "
            "location overlap (L120-130 vs L120-130) | no |",
        ],
    )
    _present_inventory(sp, ["INV-001", "INV-002"])
    merges = _apply_mechanical_dedup_from_pairs(
        sp, "sc_semantic_dedup", supplemental=True
    )
    assert merges == 0, "different severity tier must NOT merge"


def test_fix5_strict_title100_still_merges(tmp_path):
    # Existing stricter behavior preserved: title>=1.0 + location overlap.
    sp = _dedup_scratchpad(
        tmp_path,
        [
            "| INV-001: same | INV-002: same | 1.00 | "
            "location overlap (L10-20 vs L15-25) | yes |",
        ],
    )
    _present_inventory(sp, ["INV-001", "INV-002"])
    merges = _apply_mechanical_dedup_from_pairs(
        sp, "sc_semantic_dedup", supplemental=True
    )
    assert merges == 1, "existing strict title>=1.0 behavior must be preserved"


def test_fix5_low_title_no_exact_loc_does_not_merge(tmp_path):
    # title 0.6 with overlapping-but-not-exact ranges: neither path qualifies.
    sp = _dedup_scratchpad(
        tmp_path,
        [
            "| INV-001: x | INV-002: y | 0.60 | "
            "location overlap (L10-30 vs L20-40) | yes |",
        ],
    )
    _present_inventory(sp, ["INV-001", "INV-002"])
    merges = _apply_mechanical_dedup_from_pairs(
        sp, "sc_semantic_dedup", supplemental=True
    )
    assert merges == 0, "non-exact overlap below title 1.0 must NOT merge"


# --------------------------------------------------------------------------
# (e) FIX #7: components table populates for prose subsystem headings
# --------------------------------------------------------------------------
def test_fix7_components_table_populates_from_prose_headings(tmp_path):
    sp = tmp_path
    _write(sp / "config.json", '{"cli_backend": "codex"}')
    # Prose subsystem headings with parentheticals / spaces.
    _write(
        sp / "subsystem_map.md",
        "# Subsystem Map\n\n"
        "## Rate Limiting (mempool)\n\nDescription.\n\n"
        "## Fork Choice\n\nDescription.\n",
    )
    # Findings whose locations map to path tokens.
    _write(
        sp / "findings_inventory.md",
        "# Findings Inventory\n\n"
        "## Finding [INV-001]\n"
        "**Location**: mempool/rate_limiting.rs:L40\n\n"
        "## Finding [INV-002]\n"
        "**Location**: consensus/fork_choice.rs:L120\n\n"
        "## Finding [INV-003]\n"
        "**Location**: mempool/rate_limiting.rs:L88\n",
    )
    table = _synthesize_components_audited(sp)
    assert "Rate Limiting (mempool)" in table
    assert "Fork Choice" in table
    # Pre-fix: all-zero Covered column. Post-fix: rate_limiting has 2, fork_choice 1.
    # Parse counts per heading row.
    counts: dict[str, int] = {}
    for line in table.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip(" `") for c in line.strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and cells[0].lower() != "component":
            try:
                counts[cells[0]] = int(cells[1])
            except ValueError:
                pass
    assert counts.get("Rate Limiting (mempool)", 0) == 2, counts
    assert counts.get("Fork Choice", 0) == 1, counts
    # The whole table is not all-zero (the original bug).
    assert any(v > 0 for v in counts.values()), f"components table all-zero: {table}"
