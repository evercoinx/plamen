"""Fix 4: report-stage cross-tier same-location candidate list.

``_compute_report_dedup_candidate_pairs`` reads report_index.md's Master Finding
Index and emits ``report_dedup_candidate_pairs.md`` listing every CROSS-TIER pair
whose FIRST Location range matches within ±3 lines on both endpoints on the same
file. It is a CANDIDATE HINT generator only — it NEVER merges anything (the
report_dedup_agent's same-root-cause + same-fix test + the Python zero-loss gate
remain the sole merge authority). These tests assert the ±3 tolerance is honored
(neither loosened nor tightened), cross-tier is enforced, the flagship
identical-location twin surfaces, distinct-mechanism same-location pairs surface
as candidates only, and the helper mutates nothing but its own hint file.
"""
from pathlib import Path

from plamen_parsers import (
    _compute_report_dedup_candidate_pairs,
    _parse_report_index_master_rows,
    _report_index_first_location,
    _llm_norm,
)


_INDEX = """# Report Index

## Master Finding Index

| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
|-----------|-------|----------|----------|--------------|-----------|--------------------|
| H-01 | Public `withdraw` lacks access modifier | High | GatewayTransferNative.sol:286-304 | VERIFIED | - | HH-11 |
| M-06 | `withdraw` permissionless-path defect | Medium | GatewayTransferNative.sol:286-304 | VERIFIED | - | H-36 |
| M-22 | Public `withdraw`/`onRevert` refund-path interaction | Medium | GatewayTransferNative.sol:286-304,607-638 | CONTESTED | - | H-02 |
| H-04 | claimRefund non-EVM branch guard self-satisfies | High | GatewayTransferNative.sol:661-680; GatewayCrossChain.sol:571-590 | VERIFIED | - | HH-02 |
| L-14 | claimRefund emits event fields after delete | Low | GatewayTransferNative.sol:661-679,674-678 | VERIFIED | - | HL-07 |
| M-10 | decompressAccounts OOB read | Medium | libraries/AccountEncoder.sol:19-56 | VERIFIED | - | HM-04 |
| L-26 | decompressAccounts contested facets | Low | AccountEncoder.sol:19-56 | CONTESTED | - | HM-01 |
| M-01 | Fee/accounting asymmetry in claimRefund | Medium | GatewayTransferNative.sol:661-680 | VERIFIED | - | H-21 |
| M-27 | claimRefund CEI ordering / reentrancy gap | Medium | GatewayTransferNative.sol:661-680 | VERIFIED | - | HH-05 |
| I-07 | GatewaySend general code-quality observation | Informational | GatewaySend.sol (file) | VERIFIED | - | H-116 |
| L-07 | Shared constant across gateways | Low | GatewayCrossChain.sol:19 | VERIFIED | - | H-63 |
| H-05 | bytes20 truncation | High | GatewayCrossChain.sol:291 | VERIFIED | - | HH-15 |

## Tier Assignments

### Critical+High Tier
- H-01
- H-04
- H-05

### Medium Tier
- M-06 at GatewayTransferNative.sol:286-304
"""


def _write_index(scratchpad: Path, text: str = _INDEX) -> None:
    (scratchpad / "report_index.md").write_text(text, encoding="utf-8")


def _parse_pairs(scratchpad: Path) -> set[frozenset[str]]:
    """Parse the emitted hint table into a set of {A,B} report-ID pairs."""
    out: set[frozenset[str]] = set()
    txt = (scratchpad / "report_dedup_candidate_pairs.md").read_text(
        encoding="utf-8"
    )
    import re
    for line in txt.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        a = re.match(r"([CHMLI]-\d+)", cells[0])
        b = re.match(r"([CHMLI]-\d+)", cells[1])
        if a and b:
            out.add(frozenset((a.group(1), b.group(1))))
    return out


# --------------------------------------------------------------------------
# First-location extraction
# --------------------------------------------------------------------------
def test_first_location_takes_first_site_only():
    base, lr = _report_index_first_location(
        "GatewayTransferNative.sol:530-537, :425-432; GatewayCrossChain.sol swap"
    )
    assert base == "gatewaytransfernative.sol"
    assert lr == (530, 537)


def test_first_location_single_line():
    base, lr = _report_index_first_location("GatewayCrossChain.sol:291")
    assert base == "gatewaycrosschain.sol"
    assert lr == (291, 291)


def test_first_location_basename_strips_dir():
    base, lr = _report_index_first_location("libraries/AccountEncoder.sol:19-56")
    assert base == "accountencoder.sol"
    assert lr == (19, 56)


def test_first_location_no_range_returns_none():
    base, lr = _report_index_first_location("GatewaySend.sol (file)")
    assert base == ""
    assert lr is None


# --------------------------------------------------------------------------
# Master Finding Index parsing (header-aware, section-bounded)
# --------------------------------------------------------------------------
def test_master_rows_ignore_tier_assignments():
    rows = _parse_report_index_master_rows(_llm_norm(_INDEX))
    ids = [r["report_id"] for r in rows]
    # Every Master Finding Index row parsed exactly once ...
    assert ids.count("M-06") == 1
    assert ids.count("H-01") == 1
    # ... and the Tier Assignments bullets are NOT parsed as findings.
    assert len(rows) == 12


# --------------------------------------------------------------------------
# Candidate-pair generation
# --------------------------------------------------------------------------
def test_flagship_identical_location_twin_surfaces(tmp_path: Path):
    _write_index(tmp_path)
    n = _compute_report_dedup_candidate_pairs(tmp_path)
    assert n > 0
    pairs = _parse_pairs(tmp_path)
    # The flagship: a High and a Medium at the exact same lines.
    assert frozenset(("H-01", "M-06")) in pairs


def test_all_emitted_pairs_are_cross_tier(tmp_path: Path):
    _write_index(tmp_path)
    _compute_report_dedup_candidate_pairs(tmp_path)
    for pair in _parse_pairs(tmp_path):
        tiers = {rid[0] for rid in pair}
        assert len(tiers) == 2, f"same-tier pair leaked: {pair}"


def test_distinct_mechanism_same_location_is_candidate_not_merged(tmp_path: Path):
    """H-01 and M-22 sit at the same lines but are different mechanisms.

    The helper surfaces the pair as a CANDIDATE — it must NOT decide the merge.
    Merge authority stays with the LLM proposer + zero-loss gate.
    """
    _write_index(tmp_path)
    _compute_report_dedup_candidate_pairs(tmp_path)
    pairs = _parse_pairs(tmp_path)
    assert frozenset(("H-01", "M-22")) in pairs
    # The helper writes ONLY its hint file — it never mutates report_index.md
    # (the source of truth) and never produces a merge-decisions/mapping file.
    assert (tmp_path / "report_index.md").read_text(encoding="utf-8") == _INDEX
    assert not (tmp_path / "report_dedup_mapping.md").exists()
    assert not (tmp_path / "AUDIT_REPORT.md").exists()


def test_cross_file_same_lines_not_paired(tmp_path: Path):
    """M-10 (AccountEncoder 19-56) must NOT pair with H-05 (CrossChain 291).

    Same lines only counts on the SAME file. M-10/L-26 (both AccountEncoder
    19-56, cross-tier) MUST pair despite one carrying a `libraries/` prefix.
    """
    _write_index(tmp_path)
    _compute_report_dedup_candidate_pairs(tmp_path)
    pairs = _parse_pairs(tmp_path)
    assert frozenset(("M-10", "L-26")) in pairs
    assert frozenset(("M-10", "H-05")) not in pairs


def test_tolerance_boundary_pm3(tmp_path: Path):
    """±3 both endpoints qualifies; ±4 on either endpoint does not."""
    idx = """# Report Index

## Master Finding Index

| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
|-----------|-------|----------|----------|--------------|-----------|--------------------|
| H-01 | anchor | High | A.sol:100-120 | VERIFIED | - | X-1 |
| M-01 | within +3 both | Medium | A.sol:103-123 | VERIFIED | - | X-2 |
| M-02 | start +4 (out) | Medium | A.sol:104-120 | VERIFIED | - | X-3 |
| M-03 | end +4 (out) | Medium | A.sol:100-124 | VERIFIED | - | X-4 |
| M-04 | exact | Medium | A.sol:100-120 | VERIFIED | - | X-5 |
"""
    _write_index(tmp_path, idx)
    _compute_report_dedup_candidate_pairs(tmp_path)
    pairs = _parse_pairs(tmp_path)
    assert frozenset(("H-01", "M-01")) in pairs   # +3/+3 → in
    assert frozenset(("H-01", "M-04")) in pairs   # exact → in
    assert frozenset(("H-01", "M-02")) not in pairs  # +4 start → out
    assert frozenset(("H-01", "M-03")) not in pairs  # +4 end → out


def test_no_index_returns_zero_no_file(tmp_path: Path):
    n = _compute_report_dedup_candidate_pairs(tmp_path)
    assert n == 0
    assert not (tmp_path / "report_dedup_candidate_pairs.md").exists()


def test_no_candidates_writes_empty_marker(tmp_path: Path):
    idx = """# Report Index

## Master Finding Index

| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
|-----------|-------|----------|----------|--------------|-----------|--------------------|
| H-01 | lone high | High | A.sol:10-20 | VERIFIED | - | X-1 |
| M-01 | far medium | Medium | A.sol:500-520 | VERIFIED | - | X-2 |
"""
    _write_index(tmp_path, idx)
    n = _compute_report_dedup_candidate_pairs(tmp_path)
    assert n == 0
    body = (tmp_path / "report_dedup_candidate_pairs.md").read_text(
        encoding="utf-8"
    )
    assert "No cross-tier same-location candidate pairs" in body


def test_cap_never_exceeded(tmp_path: Path):
    from plamen_parsers import _REPORT_DEDUP_CANDIDATE_CAP
    # Build a pathological index: many cross-tier findings at the same lines.
    lines = [
        "# Report Index", "", "## Master Finding Index", "",
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |",
        "|--|--|--|--|--|--|--|",
    ]
    # Alternate H/M tiers so every pair is cross-tier, all at A.sol:10-20.
    for i in range(1, 40):
        tier = "H" if i % 2 else "M"
        lines.append(f"| {tier}-{i:02d} | f{i} | X | A.sol:10-20 | VERIFIED | - | X-{i} |")
    _write_index(tmp_path, "\n".join(lines) + "\n")
    n = _compute_report_dedup_candidate_pairs(tmp_path)
    assert n <= _REPORT_DEDUP_CANDIDATE_CAP
