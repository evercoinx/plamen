"""Fix 4 tests: transitive-closure clustering after pairwise semantic dedup.

Covers the pure clustering function ``_detect_dedup_report_clusters`` and the
inventory-driven builder ``build_dedup_cluster_map``:

  1. Three findings at the SAME file+function+fix-pattern (same tier) collapse
     to ONE cluster whose Location table has 3 rows (one per member).
  2. A DIFFERENT fix-pattern (opposite-direction / antonym mechanism) at the
     same file+function stays SEPARATE (no over-merge).
  3. A DIFFERENT tier stays SEPARATE — never cross-tier, no severity blur.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from plamen_mechanical import (
    _detect_dedup_report_clusters,
    build_dedup_cluster_map,
)


# ─── record helper (schema `_dedup_same_fix_ok` consumes) ─────────────────

def _rec(fid, title, sev, loc, fix, files=("Vault.sol",)):
    return {
        "id": fid,
        "title": title,
        "severity": sev,
        "location": loc,
        "locations": {loc} if loc else set(),
        "files": set(files),
        # anchors mirror `_dedup_report_sections` (len>=4 title tokens minus
        # stopwords); computed here so the test records are self-describing.
        "anchors": {
            w.lower()
            for w in __import__("re").findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", title)
        },
        "fix_text": fix,
        "desc_text": "",
    }


# ─── Case 1: same file+function+fix-pattern → collapse to ONE ─────────────

def test_same_site_same_fix_collapses_to_one_cluster():
    recs = [
        _rec("INV-016", "access control on withdrawFor lets anyone withdraw",
             "High", "Vault.sol:withdrawFor:L120",
             "Restrict withdrawFor to the token owner using onlyOwner."),
        _rec("INV-018", "withdrawFor lacks owner check enabling unauthorized withdraw",
             "High", "Vault.sol:withdrawFor:L140",
             "Restrict withdrawFor to the token owner using onlyOwner."),
        _rec("INV-020", "Public withdrawFor allows draining owner guard gap",
             "High", "Vault.sol:withdrawFor:L160",
             "Restrict withdrawFor to the token owner using onlyOwner."),
    ]
    clusters = _detect_dedup_report_clusters(recs)
    assert len(clusters) == 1, f"expected 1 cluster, got {len(clusters)}"
    cl = clusters[0]
    assert cl["members"] == ["INV-016", "INV-018", "INV-020"], cl["members"]
    assert cl["survivor"] == "INV-016", cl["survivor"]  # highest sev, lowest ID
    assert cl["severity"] == "High", cl["severity"]
    # 3-location table: header (2 lines) + one data row per member = 3 rows.
    table = cl["location_table"]
    data_rows = [
        ln for ln in table.splitlines()
        if ln.startswith("|") and "INV-" in ln
    ]
    assert len(data_rows) == 3, f"expected 3 location rows, got {len(data_rows)}\n{table}"
    for fid in ("INV-016", "INV-018", "INV-020"):
        assert fid in table, f"{fid} absent from location table"
    assert len(cl["locations"]) == 3, cl["locations"]


# ─── Case 2: different fix-pattern (mechanism) → SEPARATE ──────────────────

def test_different_fix_pattern_stays_separate():
    # Same file + same function, but opposite-direction fix (increase vs
    # decrease) — the antonym veto inside the consolidation predicate keeps
    # them apart. This is a genuinely different bug/fix at a shared site.
    recs = [
        _rec("INV-030", "Reward accounting error in claimReward", "High",
             "Vault.sol:claimReward:L200",
             "Increase the accrued reward before the transfer."),
        _rec("INV-031", "Reward accounting error in claimReward", "High",
             "Vault.sol:claimReward:L200",
             "Decrease the accrued reward before the transfer."),
    ]
    clusters = _detect_dedup_report_clusters(recs)
    assert clusters == [], f"antonym-diverging fixes must NOT merge, got {clusters}"


def test_different_function_stays_separate():
    # Same file, DIFFERENT function → no shared root cause → separate.
    recs = [
        _rec("INV-040", "deposit accounting understates shares", "High",
             "Vault.sol:deposit:L50", "Round shares up in deposit."),
        _rec("INV-041", "redeem accounting overstates assets", "High",
             "Vault.sol:redeem:L90", "Round assets down in redeem."),
    ]
    clusters = _detect_dedup_report_clusters(recs)
    assert clusters == [], f"different functions must NOT merge, got {clusters}"


# ─── Case 3: different tier → SEPARATE (no severity blur) ──────────────────

def test_different_tier_stays_separate():
    # Identical site + function + fix-pattern, but different severity tier.
    # The same-tier gate must keep them apart (never cross-tier merge).
    recs = [
        _rec("INV-050", "access control on withdrawFor lets anyone withdraw",
             "High", "Vault.sol:withdrawFor:L120",
             "Restrict withdrawFor to the token owner using onlyOwner."),
        _rec("INV-051", "access control on withdrawFor lets anyone withdraw",
             "Medium", "Vault.sol:withdrawFor:L140",
             "Restrict withdrawFor to the token owner using onlyOwner."),
    ]
    clusters = _detect_dedup_report_clusters(recs)
    assert clusters == [], f"cross-tier pair must NOT merge, got {clusters}"


def test_mixed_tier_only_same_tier_component_survives():
    # Three High duplicates + one Medium twin at the same site: the Medium is
    # excluded from the High cluster (no severity blur), leaving one 3-member
    # High cluster.
    recs = [
        _rec("INV-016", "access control on withdrawFor lets anyone withdraw",
             "High", "Vault.sol:withdrawFor:L120",
             "Restrict withdrawFor to the owner via onlyOwner."),
        _rec("INV-018", "withdrawFor lacks owner check enabling unauthorized withdraw",
             "High", "Vault.sol:withdrawFor:L140",
             "Restrict withdrawFor to the owner via onlyOwner."),
        _rec("INV-020", "Public withdrawFor allows draining owner guard gap",
             "High", "Vault.sol:withdrawFor:L160",
             "Restrict withdrawFor to the owner via onlyOwner."),
        _rec("INV-099", "Public withdrawFor allows draining owner guard gap",
             "Medium", "Vault.sol:withdrawFor:L160",
             "Restrict withdrawFor to the owner via onlyOwner."),
    ]
    clusters = _detect_dedup_report_clusters(recs)
    assert len(clusters) == 1, f"expected 1 High cluster, got {len(clusters)}"
    assert clusters[0]["members"] == ["INV-016", "INV-018", "INV-020"], clusters[0]["members"]
    assert clusters[0]["severity"] == "High"
    assert "INV-099" not in clusters[0]["members"]


# ─── End-to-end builder over a findings_inventory.md ──────────────────────

_INV_BLOCK = """### Finding [INV-{n}]: {title}

**Severity**: {sev}
**Location**: {loc}
**Description**: {desc}
**Impact**: Anyone can withdraw another user's balance.
**Recommendation**: {fix}
"""


def _write_inventory(scratch: Path, blocks: list[dict]) -> None:
    parts = ["# Findings Inventory", ""]
    for b in blocks:
        parts.append(_INV_BLOCK.format(**b))
    (scratch / "findings_inventory.md").write_text("\n".join(parts), encoding="utf-8")


def test_build_cluster_map_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        _write_inventory(scratch, [
            dict(n="016", title="access control on withdrawFor lets anyone withdraw",
                 sev="High", loc="Vault.sol:withdrawFor:L120",
                 desc="withdrawFor is unguarded.",
                 fix="Restrict withdrawFor to the owner using onlyOwner."),
            dict(n="018", title="withdrawFor lacks owner check enabling unauthorized withdraw",
                 sev="High", loc="Vault.sol:withdrawFor:L140",
                 desc="withdrawFor is unguarded.",
                 fix="Restrict withdrawFor to the owner using onlyOwner."),
            dict(n="020", title="Public withdrawFor allows draining owner guard gap",
                 sev="High", loc="Vault.sol:withdrawFor:L160",
                 desc="withdrawFor is unguarded.",
                 fix="Restrict withdrawFor to the owner using onlyOwner."),
            # Unrelated single finding — should NOT cluster.
            dict(n="030", title="Unbounded loop in distribute causes gas griefing",
                 sev="Medium", loc="Rewards.sol:distribute:L88",
                 desc="Loop over all users.",
                 fix="Cap the loop length in distribute."),
        ])
        n = build_dedup_cluster_map(scratch)
        assert n == 1, f"expected 1 cluster, got {n}"
        out = (scratch / "dedup_cluster_map.md").read_text(encoding="utf-8")
        assert "CLUSTER: INV-016, INV-018, INV-020" in out, out
        assert "**Clusters**: 1" in out
        # 3-row location table present.
        rows = [ln for ln in out.splitlines() if ln.startswith("|") and "INV-" in ln]
        assert len(rows) == 3, f"expected 3 table rows, got {len(rows)}\n{out}"
        assert "INV-030" not in out.split("CLUSTER:")[1], "unrelated finding leaked into cluster"


def test_build_cluster_map_no_clusters_writes_empty():
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        _write_inventory(scratch, [
            dict(n="040", title="deposit understates shares", sev="High",
                 loc="Vault.sol:deposit:L50", desc="x", fix="Round up in deposit."),
            dict(n="041", title="redeem overstates assets", sev="Medium",
                 loc="Vault.sol:redeem:L90", desc="y", fix="Round down in redeem."),
        ])
        n = build_dedup_cluster_map(scratch)
        assert n == 0, f"expected 0 clusters, got {n}"
        out = (scratch / "dedup_cluster_map.md").read_text(encoding="utf-8")
        assert "**Clusters**: 0" in out
        assert "No co-referent clusters detected" in out


def test_build_cluster_map_missing_inventory_is_noop():
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        assert build_dedup_cluster_map(scratch) == 0
        assert not (scratch / "dedup_cluster_map.md").exists()


if __name__ == "__main__":
    tests = [f for f in dir() if f.startswith("test_")]
    passed = failed = 0
    for name in sorted(tests):
        print(f"\n--- {name} ---")
        try:
            globals()[name]()
            print("  PASS")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            traceback.print_exc()
            failed += 1
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{'=' * 40}\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


def test_same_function_opposite_direction_not_clustered():
    """Two DISTINCT bugs in the SAME function with near-identical fixes must NOT
    cluster when the titles are opposite-direction (Fix 4 hardening: to_ray
    accepts-negative vs reverts-on-overflow)."""
    from plamen_mechanical import _detect_dedup_report_clusters
    recs = [
        {"id": "INV-153", "title": "to_ray silently accepts negative input poisoning state",
         "severity": "Medium", "locations": {"math.rs:to_ray:L10"}, "files": {"math.rs"},
         "anchors": {"to_ray"}, "fix_text": "validate the to_ray input bounds",
         "desc_text": "to_ray accepts a negative external rate"},
        {"id": "INV-154", "title": "to_ray reverts on overflow for realistic large balances",
         "severity": "Medium", "locations": {"math.rs:to_ray:L10"}, "files": {"math.rs"},
         "anchors": {"to_ray"}, "fix_text": "validate the to_ray input bounds",
         "desc_text": "to_ray reverts on overflow"},
    ]
    clusters = _detect_dedup_report_clusters(recs)
    assert clusters == [], f"opposite-direction same-fn must not cluster, got {clusters}"


def test_different_function_same_fix_not_clustered():
    """Same fix wording across DIFFERENT functions must NOT cluster (Fix 4
    hardening: deposit reconciliation vs redeem reconciliation)."""
    from plamen_mechanical import _detect_dedup_report_clusters
    recs = [
        {"id": "INV-132", "title": "redeem/withdraw never verify delivered amount",
         "severity": "Medium", "locations": {"w.rs:redeem:L10"}, "files": {"w.rs"},
         "anchors": {"redeem", "withdraw"}, "fix_text": "add before/after balance reconciliation",
         "desc_text": "redeem does not reconcile"},
        {"id": "INV-151", "title": "deposit has no tolerance for drift",
         "severity": "Medium", "locations": {"w.rs:deposit:L40"}, "files": {"w.rs"},
         "anchors": {"deposit"}, "fix_text": "add before/after balance reconciliation",
         "desc_text": "deposit does not reconcile"},
    ]
    clusters = _detect_dedup_report_clusters(recs)
    assert clusters == [], f"different-function same-fix must not cluster, got {clusters}"
