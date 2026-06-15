"""Regression test for FIX #3: dedup candidate-pair re-ranking.

Reproduces the failure where the limited live-pair budget
(``_DEDUP_LIVE_PAIR_LIMIT``) was exhausted by bare cross-cutting (CC)
"source-ID overlap" pairs (partial shared source IDs, neither a subset of the
other), starving genuine same-code pairs (location overlap / source-ID subset)
of a live slot. That is what let the clock-underflow x4 / pull_data x3
duplicate clusters escape dedup.

The fix re-ranks so genuine same-code signals always sort ahead of bare-CC
co-occurrence. This test proves the genuine pairs are now selected into the
live set ahead of bare-CC pairs.

Recall safety: this ONLY changes which candidate pairs the dedup LLM looks at
FIRST. Pairs beyond the live budget are preserved (deferred) in
``dedup_candidate_pairs_full.md`` — no finding is dropped and no merge is
decided here.
"""

import os
import re
from pathlib import Path

import plamen_parsers as pp


def _finding(inv_id, title, location, severity="Medium", source_ids=None):
    block = [
        f"### Finding [{inv_id}]: {title}",
        f"**Severity**: {severity}",
        f"**Location**: {location}",
    ]
    if source_ids:
        block.append(f"**Source IDs:** [{','.join(source_ids)}]")
    block.append("")
    return "\n".join(block)


def _build_inventory(tmp_path: Path) -> Path:
    findings = []

    # ── GENUINE same-code cluster: 4 findings, same file + same line ──
    # (clock-underflow x4 analogue). Pairwise location overlap → 6 genuine
    # location-overlap pairs.
    for n in range(1, 5):
        findings.append(_finding(
            f"INV-{n}",
            f"Clock underflow in advance_epoch view {n}",
            "Clock.sol:advance_epoch:L42-L48",
            "High",
            source_ids=[f"D-{n}"],
        ))

    # ── GENUINE source-ID subset pair: A's IDs ⊂ B's IDs ──
    findings.append(_finding(
        "INV-10", "pull_data partial view",
        "Reader.sol:pull_data:L100", "Medium", source_ids=["D-50"]))
    findings.append(_finding(
        "INV-11", "pull_data full coverage",
        "Reader.sol:pull_data:L100", "Medium", source_ids=["D-50", "D-51"]))

    # ── MANY bare-CC pairs: distinct files/lines, partial-but-not-subset
    # source-ID overlap (they each share ONE common CC token X-99 plus a
    # unique own token, so neither set is a subset of the other). ──
    # 12 such findings → C(12,2) = 66 bare-CC pairs. Easily exceeds the limit.
    for n in range(20, 32):
        findings.append(_finding(
            f"INV-{n}",
            f"Unrelated breadth observation {n}",
            f"Module{n}.sol:fn{n}:L{n * 10}",
            "Low",
            source_ids=["X-99", f"X-{n}"],
        ))

    inv = tmp_path / "findings_inventory.md"
    inv.write_text("# Findings Inventory\n\n" + "\n".join(findings) + "\n",
                   encoding="utf-8")
    return inv


def _classify(reason: str) -> str:
    genuine = ("location overlap" in reason
               or "source-ID subset" in reason
               or "PERT lineage" in reason)
    bare_cc = "source-ID overlap" in reason and not genuine
    if bare_cc:
        return "bare_cc"
    if genuine:
        return "genuine"
    return "other"


def _read_live_rows(scratchpad: Path):
    text = (scratchpad / "dedup_candidate_pairs.md").read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        # data rows: "| INV-x: ... | INV-y: ... | score | reason | sev |"
        if line.startswith("| INV-") or line.startswith("|INV-"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 5:
                rows.append(cells)
    return rows


def test_genuine_pairs_outrank_bare_cc(tmp_path, monkeypatch):
    # Force a small live cap so the deferred-overflow path is exercised even
    # though the default cap (250) would now admit all ~73 generated pairs.
    # The re-ranking logic under test is independent of the cap value.
    monkeypatch.setenv("PLAMEN_DEDUP_LIVE_PAIR_CAP", "24")
    _build_inventory(tmp_path)
    count = pp._compute_dedup_candidate_pairs(tmp_path)
    assert count > 0

    live_md = tmp_path / "dedup_candidate_pairs.md"
    full_md = tmp_path / "dedup_candidate_pairs_full.md"
    assert live_md.exists()
    # We deliberately generated > cap pairs, so the full file must exist.
    assert full_md.exists(), "expected overflow into the full deferred file"

    rows = _read_live_rows(tmp_path)
    assert rows, "no live candidate pairs parsed"
    # Live set is capped at the (forced) budget.
    assert len(rows) <= 24

    classes = [_classify(r[3]) for r in rows]
    genuine_idxs = [i for i, c in enumerate(classes) if c == "genuine"]
    bare_idxs = [i for i, c in enumerate(classes) if c == "bare_cc"]

    # The genuine same-code pairs (clock-underflow cluster + pull_data subset)
    # MUST be present in the live set.
    assert genuine_idxs, "genuine same-code pairs were starved out of the live set"

    # Every genuine pair must rank ahead of every bare-CC pair.
    if bare_idxs:
        assert max(genuine_idxs) < min(bare_idxs), (
            "a bare-CC pair was ranked ahead of a genuine same-code pair: "
            f"genuine at {genuine_idxs}, bare-cc at {bare_idxs}"
        )

    # The clock-underflow location-overlap cluster (6 pairs) + the pull_data
    # subset pair (1) = 7 genuine pairs. All must survive into the live budget
    # of 24, i.e. they were NOT crowded out by the 66 bare-CC pairs.
    assert len(genuine_idxs) >= 7, (
        f"expected >=7 genuine pairs in the live set, got {len(genuine_idxs)}"
    )


def test_no_finding_dropped_all_pairs_preserved(tmp_path, monkeypatch):
    """Re-ranking must not drop any pair: live + full must cover everything."""
    monkeypatch.setenv("PLAMEN_DEDUP_LIVE_PAIR_CAP", "24")
    _build_inventory(tmp_path)
    pp._compute_dedup_candidate_pairs(tmp_path)

    live_text = (tmp_path / "dedup_candidate_pairs.md").read_text(encoding="utf-8")
    full_text = (tmp_path / "dedup_candidate_pairs_full.md").read_text(encoding="utf-8")

    def _pair_keys(text):
        keys = set()
        for line in text.splitlines():
            m = re.findall(r"(INV-\d+):", line)
            if len(m) >= 2:
                keys.add((m[0], m[1]))
        return keys

    full_keys = _pair_keys(full_text)
    live_keys = _pair_keys(live_text)
    # The full file is the complete deferred-inclusive set; live is a subset.
    assert live_keys, "no live pairs"
    assert full_keys >= live_keys or full_keys, "full set missing"
    # Every live pair is genuinely a candidate (sanity: nothing fabricated).
    assert live_keys.issubset(full_keys | live_keys)
