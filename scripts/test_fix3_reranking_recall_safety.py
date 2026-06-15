"""Recall-safety verification for FIX #3 (dedup candidate-pair re-ranking).

FIX #3 re-ranks the candidate pairs handed to the dedup LLM so genuine
same-code signals (location overlap, source-ID subset, PERT lineage) always
sort ahead of bare cross-cutting "source-ID overlap" co-occurrence pairs. The
old key matched the substring ``"source-ID"`` which also matched the bare-CC
``"source-ID overlap"`` reason, giving provenance noise top priority and
starving genuine pairs out of the live budget.

These tests assert the change is RECALL-SAFE:

1. Re-ranking changes ONLY ORDER — never which pairs exist. ``len(pairs)`` is
   returned unchanged and every pair lands in live + full combined.
2. A genuine source-ID-subset pair that the OLD key would have crowded out of
   the live budget now lands in the live file, so the DETERMINISTIC mechanical
   backstop (which reads ``dedup_candidate_pairs.md`` and merges subset/PERT
   pairs) still fires on it. This is the concrete recall path: re-ranking
   ensures genuine same-code dups are still caught.
3. Bare-CC "source-ID overlap" pairs are NEVER merged by the mechanical
   backstop (no behavior change there) — re-ranking does not create merges,
   it only re-orders the LLM work packet.
"""

from pathlib import Path

import plamen_parsers as pp
import plamen_mechanical as pm


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


def _build_inventory_with_starving_bare_cc(tmp_path: Path):
    """One genuine subset pair + enough bare-CC pairs to blow the budget.

    With the OLD sort key (``has_src = "source-ID" in reason``) the bare-CC
    pairs would tie with / outrank the genuine subset pair and crowd it out of
    the 24-slot live budget. With the fix, the genuine subset pair is first.
    """
    findings = []

    # Genuine source-ID subset pair. DIFFERENT files so the same-file
    # location-overlap loop does not claim the pair first — the reason string
    # must be the cross-file "source-ID subset (...)" signal, which is what the
    # non-supplemental deterministic backstop merges on. A's IDs ⊂ B's IDs,
    # SAME severity → mechanically mergeable by the backstop.
    findings.append(_finding(
        "INV-1", "epoch underflow partial",
        "Clock.sol:advance_epoch:L42", "High", source_ids=["D-7"]))
    findings.append(_finding(
        "INV-2", "epoch underflow full",
        "ClockView.sol:read_epoch:L90", "High", source_ids=["D-7", "D-8"]))

    # Many bare-CC pairs: distinct files, each shares ONE common token X-99
    # plus a unique own token (neither set a subset of the other).
    # C(40,2) = 780 bare-CC pairs >> the 24 live budget.
    for n in range(20, 60):
        findings.append(_finding(
            f"INV-{n}",
            f"Unrelated breadth observation {n}",
            f"Module{n}.sol:fn{n}:L{n * 10}",
            "Low",
            source_ids=["X-99", f"X-{n}"]))

    inv = tmp_path / "findings_inventory.md"
    inv.write_text("# Findings Inventory\n\n" + "\n".join(findings) + "\n",
                   encoding="utf-8")


def _live_pair_keys(tmp_path: Path):
    text = (tmp_path / "dedup_candidate_pairs.md").read_text(encoding="utf-8")
    import re
    keys = set()
    for line in text.splitlines():
        if line.strip().startswith("|"):
            ids = re.findall(r"(INV-\d+):", line)
            if len(ids) >= 2:
                keys.add(frozenset(ids[:2]))
    return keys


def test_genuine_subset_pair_lands_in_live_file(tmp_path):
    """The genuine subset pair must be in the LIVE file despite 780 bare-CC."""
    _build_inventory_with_starving_bare_cc(tmp_path)
    total = pp._compute_dedup_candidate_pairs(tmp_path)
    assert total > pp._DEDUP_LIVE_PAIR_LIMIT, "test must overflow the budget"

    live_keys = _live_pair_keys(tmp_path)
    assert frozenset(["INV-1", "INV-2"]) in live_keys, (
        "genuine source-ID-subset pair was starved out of the live set by "
        "bare-CC provenance noise — FIX #3 re-ranking failed"
    )


def test_mechanical_backstop_merges_genuine_subset_after_reranking(tmp_path):
    """Recall path: the deterministic backstop reads the re-ranked live file
    and still merges the genuine subset pair (i.e. the dup is still caught).
    """
    _build_inventory_with_starving_bare_cc(tmp_path)
    pp._compute_dedup_candidate_pairs(tmp_path)

    # Non-supplemental mechanical fallback reads dedup_candidate_pairs.md and
    # merges source-ID subset / PERT pairs (same severity).
    merges = pm._apply_mechanical_dedup_from_pairs(
        tmp_path, "sc_semantic_dedup", supplemental=False)

    assert merges >= 1, (
        "the genuine subset pair was not merged by the mechanical backstop — "
        "re-ranking must keep it in the live file the backstop reads"
    )

    decisions = (tmp_path / "dedup_decisions.md").read_text(encoding="utf-8")
    # The subset side (INV-1) is absorbed into the superset side (INV-2).
    assert "INV-1" in decisions and "INV-2" in decisions
    assert "subset" in decisions.lower()

    # Recall safety: NO bare-CC pair was merged. None of the INV-20..59
    # findings may be absorbed (they share only provenance, not root cause).
    deduped = (tmp_path / "findings_inventory_deduped.md").read_text(
        encoding="utf-8")
    for n in range(20, 60):
        assert f"[INV-{n}]" in deduped, (
            f"bare-CC finding INV-{n} was wrongly dropped — re-ranking must "
            "NOT create merges, only re-order"
        )


def test_reranking_preserves_total_pair_count(tmp_path):
    """Re-ranking is order-only: the returned total equals live+full union."""
    _build_inventory_with_starving_bare_cc(tmp_path)
    total = pp._compute_dedup_candidate_pairs(tmp_path)

    import re
    live = (tmp_path / "dedup_candidate_pairs.md").read_text(encoding="utf-8")
    full = (tmp_path / "dedup_candidate_pairs_full.md").read_text(
        encoding="utf-8")

    def _keys(text):
        out = set()
        for line in text.splitlines():
            if line.strip().startswith("|"):
                ids = re.findall(r"(INV-\d+):", line)
                if len(ids) >= 2:
                    out.add(frozenset(ids[:2]))
        return out

    union = _keys(live) | _keys(full)
    # The full file is the complete set; its row count must equal `total`.
    assert len(union) == total, (
        f"re-ranking changed the pair population: union={len(union)} "
        f"total={total} — a pair was dropped or fabricated"
    )
