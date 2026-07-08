"""Tests for chain_prep.py — the Phase 4c chain-bounding mechanical producers.

Background: the chain phase hung 50 min on a live audit because Chain Agent 1's
PHASE 1 grouping and Agent 2's PHASE 2 matching are unbounded. The chain prompts
reference `chain_candidate_pairs.md` / `variable_finding_map.md` ("evaluate ONLY
these pairs") but nothing produced them. `chain_prep.py` builds those producers.

These tests lock in:
  1. Each producer emits a well-formed file from a realistic fixture.
  2. A pair with a real shared signal (state var / identifier / proximity)
     appears; a provably-unrelated pair does not.
  3. The bounded `chain_candidate_pairs.md` is capped and balanced; the full
     set is complete in `chain_candidate_pairs_full.md`.
  4. Graceful degradation: missing/malformed inputs → empty output, no raise.
  5. Idempotency: re-running produces identical results.

Run: `pytest scripts/test_chain_prep.py -v`
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _cp():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    if "chain_prep" in sys.modules:
        del sys.modules["chain_prep"]
    return importlib.import_module("chain_prep")


def _write_inventory(sp: Path, findings: list[dict]) -> None:
    """findings: list of {id, severity, location, verdict, root_cause, description}."""
    lines = ["# Findings Inventory", "", "## Findings", ""]
    for f in findings:
        lines.append(f"### Finding [{f['id']}]: {f.get('title', f['id'] + ' title')}")
        if f.get("source_ids"):
            lines.append(f"**Source IDs**: {f['source_ids']}")
        lines.append(f"**Severity**: {f.get('severity', 'Medium')}")
        lines.append(f"**Location**: {f.get('location', 'X.sol:L1')}")
        lines.append(f"**Verdict**: {f.get('verdict', 'CONFIRMED')}")
        lines.append(f"**Root Cause**: {f.get('root_cause', '')}")
        lines.append(f"**Description**: {f.get('description', '')}")
        lines.append(f"**Impact**: {f.get('impact', 'some impact')}")
        for label, key in (
            ("Discovery Steer", "discovery_steer"),
            ("Missing Precondition", "missing_precondition"),
            ("Precondition Type", "precondition_type"),
            ("Postconditions Created", "postconditions_created"),
            ("Postcondition Types", "postcondition_types"),
            ("Semantic Invariant", "semantic_invariant"),
            ("Branch Preconditions", "branch_preconditions"),
            ("Terminal Mechanism", "terminal_mechanism"),
            ("Composition Candidates", "composition_candidates"),
        ):
            if f.get(key):
                lines.append(f"**{label}**: {f[key]}")
        lines.append("")
    (sp / "findings_inventory.md").write_text("\n".join(lines), encoding="utf-8")


def _write_state_write_map(sp: Path, contract: str, variables: list[str]) -> None:
    lines = ["# State Write Map", "", f"## {contract}.sol", "",
             "| State Variable | Writer Function | Write Site | Access Guard |",
             "|----------------|-----------------|------------|--------------|"]
    for v in variables:
        lines.append(f"| {v} | someWriter | L10 | onlyOwner |")
    (sp / "state_write_map.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Producer 1 — chain_candidate_pairs
# ---------------------------------------------------------------------------


def test_candidate_pairs_shared_state_var(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", ["refundInfos", "balances"])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L100",
         "root_cause": "claimRefund deletes refundInfos before transfer",
         "description": "refundInfos mapping mutated unsafely"},
        {"id": "INV-002", "severity": "Medium", "location": "Vault.sol:L300",
         "root_cause": "onAbort writes refundInfos with wrong length",
         "description": "refundInfos stored from abort context"},
    ])
    out = cp.compute_chain_candidate_pairs(tmp_path)
    assert out["status"] == "ok"
    assert out["pairs"] >= 1
    text = (tmp_path / "chain_candidate_pairs.md").read_text(encoding="utf-8")
    # The two findings share state var refundInfos → must be a STATE pair
    assert "INV-001" in text and "INV-002" in text
    assert "refundInfos" in text


def test_candidate_pairs_excludes_provably_unrelated(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", ["balances"])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L100",
         "root_cause": "balances underflow in withdraw",
         "description": "the withdraw path corrupts balances"},
        {"id": "INV-002", "severity": "Low", "location": "Router.sol:L9000",
         "root_cause": "unrelated typo in a comment",
         "description": "cosmetic only, distinct file, distinct everything"},
    ])
    out = cp.compute_chain_candidate_pairs(tmp_path)
    # No shared state, no shared identifier, different files far apart → 0 pairs
    assert out["pairs"] == 0
    full = (tmp_path / "chain_candidate_pairs_full.md").read_text(encoding="utf-8")
    assert "INV-001 |" not in full or "INV-002" not in full.split("INV-001")[-1][:50]


def test_candidate_pairs_line_proximity(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", [])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L100-110",
         "root_cause": "issue alpha", "description": "distinct wording one"},
        {"id": "INV-002", "severity": "Low", "location": "Vault.sol:L130",
         "root_cause": "issue beta", "description": "distinct wording two"},
    ])
    out = cp.compute_chain_candidate_pairs(tmp_path)
    # L100-110 and L130 are within 60 lines → proximity pair
    assert out["pairs"] >= 1


def test_candidate_pairs_far_apart_same_file_not_paired(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", [])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L100",
         "root_cause": "issue alpha", "description": "distinct wording one"},
        {"id": "INV-002", "severity": "Low", "location": "Vault.sol:L9000",
         "root_cause": "issue beta", "description": "distinct wording two"},
    ])
    out = cp.compute_chain_candidate_pairs(tmp_path)
    # Same file but 8900 lines apart, no shared state/identifier → not a candidate
    assert out["pairs"] == 0


def test_candidate_pairs_generic_discovery_signal_does_not_pair(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", [])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L100",
         "root_cause": "issue alpha", "description": "distinct wording one",
         "discovery_steer": "arithmetic rounding terminal effect"},
        {"id": "INV-002", "severity": "Medium", "location": "Router.sol:L9000",
         "root_cause": "issue beta", "description": "distinct wording two",
         "discovery_steer": "arithmetic rounding terminal effect"},
    ])
    out = cp.compute_chain_candidate_pairs(tmp_path)
    assert out["pairs"] == 0


def test_candidate_pairs_concrete_discovery_term_pairs(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", [])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L100",
         "root_cause": "issue alpha", "description": "distinct wording one",
         "discovery_steer": "branch creates `pendingShares` mismatch"},
        {"id": "INV-002", "severity": "Medium", "location": "Router.sol:L9000",
         "root_cause": "issue beta", "description": "distinct wording two",
         "missing_precondition": "`pendingShares` already nonzero"},
    ])
    out = cp.compute_chain_candidate_pairs(tmp_path)
    assert out["pairs"] == 1
    text = (tmp_path / "chain_candidate_pairs.md").read_text(encoding="utf-8")
    assert "discovery: pendingshares" in text


def test_candidate_pairs_explicit_discovery_ref_matches_source_id_alias(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", [])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "source_ids": "CS-1", "severity": "High",
         "location": "Vault.sol:L100", "root_cause": "issue alpha",
         "description": "distinct wording one",
         "discovery_steer": "candidate ID CS-2 may provide missing state"},
        {"id": "INV-002", "source_ids": "CS-2", "severity": "Medium",
         "location": "Router.sol:L9000", "root_cause": "issue beta",
         "description": "distinct wording two"},
    ])
    out = cp.compute_chain_candidate_pairs(tmp_path)
    assert out["pairs"] == 1
    text = (tmp_path / "chain_candidate_pairs.md").read_text(encoding="utf-8")
    assert "discovery: explicit finding reference" in text


def test_candidate_pairs_bounded_cap_and_balance(tmp_path):
    cp = _cp()
    # 30 findings all sharing one state var → many STATE pairs
    _write_state_write_map(tmp_path, "Vault", ["sharedVar"])
    findings = [
        {"id": f"INV-{i:03d}", "severity": "Medium", "location": f"Vault.sol:L{i*5}",
         "root_cause": f"distinct rootcause sharedVar token{i}",
         "description": f"sharedVar touched here uniqueWord{i}"}
        for i in range(1, 31)
    ]
    _write_inventory(tmp_path, findings)
    out = cp.compute_chain_candidate_pairs(tmp_path)
    assert out["status"] == "ok"
    assert out["bounded"] <= cp._BOUNDED_PAIR_CAP
    # full set must be >= bounded
    assert out["pairs"] >= out["bounded"]


def test_candidate_pairs_fewer_than_two_findings(tmp_path):
    cp = _cp()
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L1",
         "root_cause": "lonely", "description": "only one finding"},
    ])
    out = cp.compute_chain_candidate_pairs(tmp_path)
    assert out["status"] == "skipped"
    assert out["pairs"] == 0


# ---------------------------------------------------------------------------
# Producer 2 — variable_finding_map
# ---------------------------------------------------------------------------


def test_variable_finding_map_basic(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", ["refundInfos", "feePercent"])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L1",
         "root_cause": "refundInfos deleted early",
         "description": "refundInfos mutation"},
        {"id": "INV-002", "severity": "Medium", "location": "Vault.sol:L2",
         "root_cause": "feePercent has no upper bound",
         "description": "feePercent unchecked"},
        {"id": "INV-003", "severity": "Low", "location": "Vault.sol:L3",
         "root_cause": "feePercent retroactive on refundInfos",
         "description": "both feePercent and refundInfos involved"},
    ])
    out = cp.compute_variable_finding_map(tmp_path)
    assert out["status"] == "ok"
    text = (tmp_path / "variable_finding_map.md").read_text(encoding="utf-8")
    assert "refundInfos" in text and "feePercent" in text
    # refundInfos row should list INV-001 and INV-003
    refund_line = next(l for l in text.splitlines() if l.startswith("| refundInfos"))
    assert "INV-001" in refund_line and "INV-003" in refund_line


def test_variable_finding_map_no_state_map_writes_header(tmp_path):
    cp = _cp()
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L1",
         "root_cause": "x", "description": "y"},
    ])
    # no state_write_map.md
    out = cp.compute_variable_finding_map(tmp_path)
    assert out["status"] == "skipped"
    assert (tmp_path / "variable_finding_map.md").exists()  # header still written


# ---------------------------------------------------------------------------
# Producer 3 — enabler_baseline
# ---------------------------------------------------------------------------


def test_enabler_baseline_prefills_step0a(tmp_path):
    cp = _cp()
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L100",
         "verdict": "CONFIRMED", "root_cause": "dangerous state alpha"},
        {"id": "INV-002", "severity": "Medium", "location": "Vault.sol:L200",
         "verdict": "PARTIAL", "root_cause": "dangerous state beta"},
        {"id": "INV-003", "severity": "Low", "location": "Vault.sol:L300",
         "verdict": "REFUTED", "root_cause": "not dangerous - refuted"},
    ])
    out = cp.compute_enabler_baseline(tmp_path)
    assert out["status"] == "ok"
    # CONFIRMED + PARTIAL counted; REFUTED excluded
    assert out["states"] == 2
    text = (tmp_path / "enabler_results.md").read_text(encoding="utf-8")
    assert "MECHANICAL_BASELINE_STEP0A" in text
    assert "INV-001" in text and "INV-002" in text
    assert "INV-003" not in text  # refuted not a dangerous state
    assert "STEP 0a" in text and "STEP 0b" in text


def test_enabler_baseline_no_confirmed(tmp_path):
    cp = _cp()
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "Low", "location": "Vault.sol:L1",
         "verdict": "REFUTED", "root_cause": "refuted"},
    ])
    out = cp.compute_enabler_baseline(tmp_path)
    assert out["status"] == "skipped"
    assert out["states"] == 0


# ---------------------------------------------------------------------------
# Degradation + idempotency
# ---------------------------------------------------------------------------


def test_all_producers_no_inventory(tmp_path):
    """No findings_inventory.md → all producers degrade, none raise."""
    cp = _cp()
    out = cp.run_chain_prep(tmp_path)
    assert out["candidate_pairs"]["status"] in ("skipped", "ok", "error")
    assert out["variable_map"]["status"] in ("skipped", "ok", "error")
    assert out["enabler_baseline"]["status"] in ("skipped", "ok", "error")
    # The key contract: no exception escaped — run_chain_prep returned a dict.
    assert isinstance(out, dict)


def test_malformed_inventory_does_not_raise(tmp_path):
    cp = _cp()
    (tmp_path / "findings_inventory.md").write_text(
        "this is not a valid inventory \x00\x01 garbage |||",
        encoding="utf-8",
    )
    out = cp.run_chain_prep(tmp_path)  # must not raise
    assert isinstance(out, dict)
    assert "candidate_pairs" in out


def test_idempotency(tmp_path):
    cp = _cp()
    _write_state_write_map(tmp_path, "Vault", ["refundInfos"])
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L100",
         "root_cause": "refundInfos issue", "description": "refundInfos a"},
        {"id": "INV-002", "severity": "Medium", "location": "Vault.sol:L120",
         "root_cause": "refundInfos issue two", "description": "refundInfos b"},
    ])
    a = cp.run_chain_prep(tmp_path)
    pairs_a = a["candidate_pairs"]["pairs"]
    text_a = (tmp_path / "chain_candidate_pairs.md").read_text(encoding="utf-8")
    b = cp.run_chain_prep(tmp_path)
    pairs_b = b["candidate_pairs"]["pairs"]
    text_b = (tmp_path / "chain_candidate_pairs.md").read_text(encoding="utf-8")
    assert pairs_a == pairs_b
    # Body identical except the timestamp line
    def _strip_ts(t):
        return "\n".join(l for l in t.splitlines() if not l.startswith("**Generated At**"))
    assert _strip_ts(text_a) == _strip_ts(text_b)


def test_enabler_baseline_overwrites_passthrough_stub(tmp_path):
    """compute_enabler_baseline must replace the _write_chain_passthrough_outputs
    stub, not append to it."""
    cp = _cp()
    # Simulate the driver's stub write
    (tmp_path / "enabler_results.md").write_text(
        "# Enabler Results\n\n**Status**: MECHANICAL_BASELINE\n\n"
        "No new enabler paths were mechanically introduced by this scaffold.\n",
        encoding="utf-8",
    )
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Vault.sol:L1",
         "verdict": "CONFIRMED", "root_cause": "real dangerous state"},
    ])
    cp.compute_enabler_baseline(tmp_path)
    text = (tmp_path / "enabler_results.md").read_text(encoding="utf-8")
    assert "MECHANICAL_BASELINE_STEP0A" in text
    assert "No new enabler paths were mechanically introduced" not in text


# ---------------------------------------------------------------------------
# Fix 7 Part B — CROSS-DOMAIN-DEP → STEP-0a-LC enabler harvester
# ---------------------------------------------------------------------------


def _write_depth_findings(sp: Path, name: str, body: str) -> None:
    (sp / name).write_text(body, encoding="utf-8")


def test_cross_domain_harvest_substantive_only(tmp_path):
    """Substantive [CROSS-DOMAIN-DEP: domain — detail] tags become enablers;
    bare domain-only tags and the `none` admission are skipped."""
    cp = _cp()
    _write_depth_findings(tmp_path, "depth_external_findings.md", (
        "### Finding [DX-1]\n"
        "**Location**: Bridge.sol:L42\n"
        "Analysis. [CROSS-DOMAIN-DEP: external — destination VM deserialization "
        "scheme decides whether the payload decodes]\n\n"
        "### Finding [DX-2]\n"
        "**Location**: Bridge.sol:L88\n"
        "Bare tag. [CROSS-DOMAIN-DEP: external]\n\n"
        "### Finding [DX-3]\n"
        "**Location**: Bridge.sol:L120\n"
        "In-scope. [CROSS-DOMAIN-DEP: none — fully in-scope permissionless theft]\n"
    ))
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Bridge.sol:L42",
         "verdict": "CONFIRMED", "root_cause": "real dangerous state"},
    ])
    harv = cp._harvest_cross_domain_enablers(tmp_path, cp._load_inventory(tmp_path))
    dets = [h["detail"] for h in harv]
    assert len(harv) == 1, dets
    assert "destination VM deserialization" in harv[0]["detail"]
    assert harv[0]["finding_id"] == "DX-1"
    assert harv[0]["domain"] == "external"
    # bare + none must NOT appear
    assert all("none" != h["domain"] for h in harv)
    assert all("fully in-scope" not in h["detail"] for h in harv)


def test_cross_domain_harvest_dedup_by_locus_detail(tmp_path):
    """Identical (locus, detail) tags in two files collapse to one enabler."""
    cp = _cp()
    common = ("### Finding [DE-1]\n**Location**: X.sol:L10\n"
              "[CROSS-DOMAIN-DEP: token-flow — pooled residual provides drained funds]\n")
    _write_depth_findings(tmp_path, "depth_token_flow_findings.md", common)
    _write_depth_findings(tmp_path, "depth_edge_case_findings.md", common)
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "X.sol:L10",
         "verdict": "CONFIRMED", "root_cause": "rc"},
    ])
    harv = cp._harvest_cross_domain_enablers(tmp_path, cp._load_inventory(tmp_path))
    assert len(harv) == 1


def test_cross_domain_harvest_dedup_vs_axisgap_provenance(tmp_path):
    """A CROSS-DOMAIN-DEP tag at a locus already covered by an M2 AXISGAP
    provenance-gap candidate is not re-emitted (append-only dedup)."""
    cp = _cp()
    _write_depth_findings(tmp_path, "depth_external_findings.md", (
        "### Finding [DX-9]\n**Location**: Y.sol:L55\n"
        "[CROSS-DOMAIN-DEP: external — assumes freshness of an off-domain value]\n"
    ))
    _write_inventory(tmp_path, [
        # An AXISGAP provenance candidate at the SAME locus (Y.sol:L55).
        {"id": "INV-050", "severity": "Low", "location": "Y.sol:L55",
         "verdict": "NEEDS_VERIFICATION",
         "source_ids": "AXISGAP:AXIS-9 (multi-axis coverage meta-pass)",
         "root_cause": "provenance axis unexamined at hot function",
         "description": "provenance freshness gap"},
    ])
    entries = cp._load_inventory(tmp_path)
    assert cp._axisgap_provenance_loci(entries)  # locus is recognized
    harv = cp._harvest_cross_domain_enablers(tmp_path, entries)
    assert harv == []


def test_cross_domain_harvest_cap_40(tmp_path):
    """The harvester never emits more than _MAX_CROSS_DOMAIN_ENABLERS."""
    cp = _cp()
    blocks = []
    for i in range(60):
        blocks.append(
            f"### Finding [DX-{i}]\n**Location**: F.sol:L{i}\n"
            f"[CROSS-DOMAIN-DEP: external — distinct off-domain assumption number {i}]\n"
        )
    _write_depth_findings(tmp_path, "depth_external_findings.md", "\n".join(blocks))
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "F.sol:L1",
         "verdict": "CONFIRMED", "root_cause": "rc"},
    ])
    harv = cp._harvest_cross_domain_enablers(tmp_path, cp._load_inventory(tmp_path))
    assert len(harv) <= cp._MAX_CROSS_DOMAIN_ENABLERS == 40


def test_enabler_baseline_writes_cross_domain_table(tmp_path):
    """compute_enabler_baseline emits the CROSS-DOMAIN-DEP enabler sub-table and
    counts them; bare tags do not appear."""
    cp = _cp()
    _write_depth_findings(tmp_path, "depth_external_findings.md", (
        "### Finding [DX-1]\n**Location**: Bridge.sol:L42\n"
        "[CROSS-DOMAIN-DEP: external — destination VM deserialization scheme]\n"
        "### Finding [DX-2]\n**Location**: Bridge.sol:L88\n"
        "[CROSS-DOMAIN-DEP: external]\n"
    ))
    _write_inventory(tmp_path, [
        {"id": "INV-001", "severity": "High", "location": "Bridge.sol:L42",
         "verdict": "CONFIRMED", "root_cause": "real dangerous state"},
    ])
    out = cp.compute_enabler_baseline(tmp_path)
    assert out["status"] == "ok"
    assert out["cross_domain_enablers"] == 1
    text = (tmp_path / "enabler_results.md").read_text(encoding="utf-8")
    assert "Cross-Domain Dependency Enablers" in text
    assert "destination VM deserialization scheme" in text
    assert "DX-1" in text
