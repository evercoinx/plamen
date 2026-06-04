"""Unit tests for the raised dedup live-pair cap, aggregate-signal suppression,
and multi-round chunking in ``_compute_dedup_candidate_pairs``.

These cover ONLY the candidate-generation surface in ``plamen_parsers.py``:

  (A) The hard 24-pair limit is replaced by an env-overridable cap (default
      250). The full genuine candidate set must reach the live LLM packet.
  (B) Source-ID-subset and PERT-lineage signals are SUPPRESSED for findings
      carrying more than ``_DEDUP_AGGREGATE_SOURCE_ID_THRESHOLD`` (=4) source
      IDs — the worst false-merge class — while the pair can still surface on
      location/title/function signals.
  (C) When the live count exceeds ``_DEDUP_ROUND_CHUNK`` (=80), per-round
      sub-packets are emitted in addition to the unified round-1 file; every
      pair lands in exactly one round.

Recall safety: this function NEVER merges. Raising the cap only widens the
set of candidates the per-pair LLM judge sees; aggregate-suppression removes
only the two misfiring hints (subset/PERT) for large-aggregate findings — the
pair still surfaces via other signals, so no genuine same-defect pair is
dropped from candidacy.
"""

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


def _write_inventory(tmp_path: Path, findings: list[str]) -> Path:
    inv = tmp_path / "findings_inventory.md"
    inv.write_text(
        "# Findings Inventory\n\n" + "\n".join(findings) + "\n",
        encoding="utf-8",
    )
    return inv


def _read_pair_keys(text: str) -> set[tuple[str, str]]:
    keys = set()
    for line in text.splitlines():
        if not line.lstrip().startswith("| INV-") and not line.lstrip().startswith(
            "|INV-"
        ):
            continue
        m = re.findall(r"(INV-\d+):", line)
        if len(m) >= 2:
            keys.add((m[0], m[1]))
    return keys


def _read_signal_for_pair(text: str, a: str, b: str) -> str:
    for line in text.splitlines():
        if f"{a}:" in line and f"{b}:" in line and line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 5:
                return cells[3]
    return ""


# ───────────────────────── (A) Cap raised ─────────────────────────

def test_dedup_cap_raised_covers_full_set(tmp_path):
    """30 genuine same-line stub/full pairs must ALL appear live (not capped
    at 24); the deferred full file is absent when count <= cap."""
    findings = []
    # 30 distinct files, each with a stub + full finding at the same line →
    # 30 genuine location-overlap pairs (one per file).
    for n in range(1, 31):
        findings.append(_finding(
            f"INV-{2 * n - 1}", f"Stub of defect {n}",
            f"mod{n}.rs:fn{n}:L40-L48", "Medium", source_ids=[f"D-{n}"]))
        findings.append(_finding(
            f"INV-{2 * n}", f"Full coverage of defect {n}",
            f"mod{n}.rs:fn{n}:L40-L50", "Medium",
            source_ids=[f"D-{n}", f"D-{n}b"]))
    _write_inventory(tmp_path, findings)

    count = pp._compute_dedup_candidate_pairs(tmp_path)
    # Exactly 30 genuine pairs (each file contributes one same-function /
    # location-overlap pair). Cross-file subset does not fire (disjoint IDs).
    assert count == 30, f"expected 30 candidate pairs, got {count}"

    live_text = (tmp_path / "dedup_candidate_pairs.md").read_text(encoding="utf-8")
    live_keys = _read_pair_keys(live_text)
    assert len(live_keys) == 30, (
        f"all 30 pairs must be live under the raised cap, got {len(live_keys)}"
    )

    # No overflow → deferred full file absent.
    assert not (tmp_path / "dedup_candidate_pairs_full.md").exists()
    # Single round → no round sub-packets.
    assert not (tmp_path / "dedup_candidate_pairs_round1.md").exists()
    assert (tmp_path / "dedup_round_count.txt").read_text().strip() == "1"


def test_dedup_cap_env_override_lowers(tmp_path, monkeypatch):
    """Env override caps the live set and overflows the rest to the full file
    without dropping any pair."""
    monkeypatch.setenv("PLAMEN_DEDUP_LIVE_PAIR_CAP", "10")
    findings = []
    for n in range(1, 31):
        findings.append(_finding(
            f"INV-{2 * n - 1}", f"Stub {n}",
            f"mod{n}.rs:fn{n}:L40-L48", "Medium"))
        findings.append(_finding(
            f"INV-{2 * n}", f"Full {n}",
            f"mod{n}.rs:fn{n}:L40-L50", "Medium"))
    _write_inventory(tmp_path, findings)

    count = pp._compute_dedup_candidate_pairs(tmp_path)
    assert count == 30
    live_keys = _read_pair_keys(
        (tmp_path / "dedup_candidate_pairs.md").read_text(encoding="utf-8"))
    assert len(live_keys) == 10
    full = tmp_path / "dedup_candidate_pairs_full.md"
    assert full.exists()
    full_keys = _read_pair_keys(full.read_text(encoding="utf-8"))
    # Full file is the complete set; live is a subset; nothing dropped.
    assert len(full_keys) == 30
    assert live_keys.issubset(full_keys)


# ──────────────────── (B) Aggregate suppression ────────────────────

def test_dedup_aggregate_suppression(tmp_path):
    """A finding with 15 source IDs sharing ONE id with another, no
    location/title/function overlap → NO pair emitted (subset/PERT suppressed,
    nothing else qualifies)."""
    big = [f"NS-{i}" for i in range(1, 16)]  # 15 source IDs
    findings = [
        _finding("INV-1", "Aggregate perturbation finding",
                 "peer_network_service.rs:handle:L1042", "High",
                 source_ids=big),
        _finding("INV-2", "Unrelated small finding sharing NS-3",
                 "gossip_client.rs:send:L162", "Medium",
                 source_ids=["NS-3", "OWN-1"]),
    ]
    _write_inventory(tmp_path, findings)
    count = pp._compute_dedup_candidate_pairs(tmp_path)
    assert count == 0, (
        f"subset/PERT must be suppressed for >4-source-ID finding, got {count}"
    )


def test_dedup_aggregate_suppression_still_surfaces_on_location(tmp_path):
    """Same large-aggregate pair PLUS a real location overlap → pair IS
    emitted, but its Signal cell does NOT carry the subset/PERT hint."""
    big = [f"NS-{i}" for i in range(1, 16)]
    findings = [
        _finding("INV-1", "Aggregate perturbation finding",
                 "shared.rs:handle:L100-L120", "High", source_ids=big),
        _finding("INV-2", "Co-located finding sharing NS-3",
                 "shared.rs:handle:L100-L110", "Medium",
                 source_ids=["NS-3", "OWN-1"]),
    ]
    _write_inventory(tmp_path, findings)
    count = pp._compute_dedup_candidate_pairs(tmp_path)
    assert count == 1, f"location overlap should still surface the pair, got {count}"
    text = (tmp_path / "dedup_candidate_pairs.md").read_text(encoding="utf-8")
    signal = _read_signal_for_pair(text, "INV-1", "INV-2")
    assert "location overlap" in signal
    assert "source-ID subset" not in signal, signal
    assert "PERT lineage" not in signal, signal


def test_dedup_aggregate_threshold_boundary(tmp_path):
    """Exactly 4 source IDs keeps the subset signal; 5 suppresses it."""
    # Case A: superset has exactly 4 source IDs → subset signal FIRES.
    findings_4 = [
        _finding("INV-1", "Partial view",
                 "a.rs:fn:L10", "Medium", source_ids=["D-1"]),
        _finding("INV-2", "Full view",
                 "b.rs:fn2:L20", "Medium",
                 source_ids=["D-1", "D-2", "D-3", "D-4"]),
    ]
    sp4 = tmp_path / "four"
    sp4.mkdir()
    _write_inventory(sp4, findings_4)
    assert pp._compute_dedup_candidate_pairs(sp4) == 1
    text4 = (sp4 / "dedup_candidate_pairs.md").read_text(encoding="utf-8")
    assert "source-ID subset" in text4

    # Case B: superset has 5 source IDs → subset signal SUPPRESSED, no other
    # signal qualifies → no pair.
    findings_5 = [
        _finding("INV-1", "Partial view",
                 "a.rs:fn:L10", "Medium", source_ids=["D-1"]),
        _finding("INV-2", "Full view",
                 "b.rs:fn2:L20", "Medium",
                 source_ids=["D-1", "D-2", "D-3", "D-4", "D-5"]),
    ]
    sp5 = tmp_path / "five"
    sp5.mkdir()
    _write_inventory(sp5, findings_5)
    assert pp._compute_dedup_candidate_pairs(sp5) == 0


# ──────────────────── (C) Multi-round chunking ────────────────────

def test_dedup_multiround_chunking(tmp_path, monkeypatch):
    """200 candidate pairs with chunk=80 → round1/2/3 sub-packets + matching
    focus inventories + unified round-1 dedup_candidate_pairs.md. Union of
    round rows == full sorted set, no pair duplicated across rounds."""
    # Force a cap high enough to admit all 200 live, and chunk at 80.
    monkeypatch.setenv("PLAMEN_DEDUP_LIVE_PAIR_CAP", "500")
    monkeypatch.setattr(pp, "_DEDUP_ROUND_CHUNK", 80)

    findings = []
    # 200 distinct files, each a same-function pair → 200 genuine pairs.
    for n in range(1, 201):
        findings.append(_finding(
            f"INV-{2 * n - 1}", f"Stub {n}",
            f"file{n}.rs:fn{n}:L40-L48", "Medium"))
        findings.append(_finding(
            f"INV-{2 * n}", f"Full {n}",
            f"file{n}.rs:fn{n}:L40-L50", "Medium"))
    _write_inventory(tmp_path, findings)

    count = pp._compute_dedup_candidate_pairs(tmp_path)
    assert count == 200, f"expected 200 pairs, got {count}"

    # 200 / 80 → 3 rounds (80, 80, 40).
    assert (tmp_path / "dedup_round_count.txt").read_text().strip() == "3"

    unified = tmp_path / "dedup_candidate_pairs.md"
    r1 = tmp_path / "dedup_candidate_pairs_round1.md"
    r2 = tmp_path / "dedup_candidate_pairs_round2.md"
    r3 = tmp_path / "dedup_candidate_pairs_round3.md"
    assert unified.exists() and r1.exists() and r2.exists() and r3.exists()
    assert not (tmp_path / "dedup_candidate_pairs_round4.md").exists()

    # Matching focus inventories.
    assert (tmp_path / "dedup_focus_inventory.md").exists()
    assert (tmp_path / "dedup_focus_inventory_round1.md").exists()
    assert (tmp_path / "dedup_focus_inventory_round2.md").exists()
    assert (tmp_path / "dedup_focus_inventory_round3.md").exists()

    keys_unified = _read_pair_keys(unified.read_text(encoding="utf-8"))
    keys_r1 = _read_pair_keys(r1.read_text(encoding="utf-8"))
    keys_r2 = _read_pair_keys(r2.read_text(encoding="utf-8"))
    keys_r3 = _read_pair_keys(r3.read_text(encoding="utf-8"))

    # Unified == round 1 (single-round consumer compatibility).
    assert keys_unified == keys_r1
    assert len(keys_r1) == 80
    assert len(keys_r2) == 80
    assert len(keys_r3) == 40

    # No pair appears in two rounds.
    assert keys_r1.isdisjoint(keys_r2)
    assert keys_r1.isdisjoint(keys_r3)
    assert keys_r2.isdisjoint(keys_r3)

    # Union of all rounds == the full sorted candidate set (200 pairs).
    union = keys_r1 | keys_r2 | keys_r3
    assert len(union) == 200


def test_dedup_no_round_subpackets_under_chunk(tmp_path):
    """When the live count is <= chunk size, NO round sub-packets are written —
    only the unified single-round file."""
    findings = []
    for n in range(1, 6):  # 5 genuine pairs
        findings.append(_finding(
            f"INV-{2 * n - 1}", f"Stub {n}", f"f{n}.rs:fn{n}:L40-L48", "Medium"))
        findings.append(_finding(
            f"INV-{2 * n}", f"Full {n}", f"f{n}.rs:fn{n}:L40-L50", "Medium"))
    _write_inventory(tmp_path, findings)
    assert pp._compute_dedup_candidate_pairs(tmp_path) == 5
    assert (tmp_path / "dedup_candidate_pairs.md").exists()
    assert not (tmp_path / "dedup_candidate_pairs_round1.md").exists()
    assert (tmp_path / "dedup_round_count.txt").read_text().strip() == "1"
