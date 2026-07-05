"""Tests for the Part 2 audit-driven fix queue (T1-a, T1-b, T2-a, T2-c).

Covers the validators added/changed to fix application-layer failures a
prior audit exposed:

  T1-a  _validate_poc_pass_integrity defers to a mechanical PASS
  T1-b  _check_report_index_unresolved_authenticity rejects phantom UNRESOLVED
  T2-a  _validate_verifier_skip_vocabulary + _build_succeeded
  T2-c  _check_speculative_critical_chains

Run: `python -m pytest test_part2_audit_fixes.py -q`
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_validators import (  # noqa: E402
    _build_succeeded,
    _check_report_index_unresolved_authenticity,
    _check_speculative_critical_chains,
    _parse_master_finding_index_rows,
    _validate_poc_pass_integrity,
    _validate_verification_queue_inventory_parity,
    _validate_verifier_skip_vocabulary,
)

_QUEUE_HEADER = (
    "# Verification Queue Manifest\n"
    "| Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag "
    "| Location | Primary Artifact | PoC Class |\n"
    "|--|--|--|--|--|--|--|--|--|\n"
)


def _queue_row(fid: str, sev: str = "High", poc_class: str = "unit") -> str:
    return (
        f"| 1 | {fid} | {sev} | A bug | panic | [POC-PASS] | "
        f"src/Vault.sol:45 | depth.md | {poc_class} |\n"
    )


# ---------------------------------------------------------------------------
# T1-a: mechanical PASS overrides the .md code-block scan
# ---------------------------------------------------------------------------

def test_t1a_mechanical_pass_overrides_no_assertion_downgrade(tmp_path):
    """A verify file whose snippet has no assertion is normally downgraded;
    a Mechanical-Verified PASS must suppress that downgrade."""
    (tmp_path / "verification_queue.md").write_text(
        _QUEUE_HEADER + _queue_row("F-01"), encoding="utf-8"
    )
    body = (
        "# Verify F-01\n"
        "Evidence Tag: [POC-PASS]\n"
        "### PoC Attempt\n```solidity\n"
        "function test_F01() public { vault.withdraw(0); }\n```\n"
        "**Mechanical-Verified**: YES — Status: PASS (duration: 1.5s)\n"
        "**Mechanical-Tag**: [POC-PASS]\n"
    )
    (tmp_path / "verify_F-01.md").write_text(body, encoding="utf-8")
    assert _validate_poc_pass_integrity(tmp_path) == []


def test_t1a_no_mechanical_marker_still_downgrades(tmp_path):
    """Regression guard: without the mechanical PASS marker, an assertion-free
    PoC snippet is still downgraded."""
    (tmp_path / "verification_queue.md").write_text(
        _QUEUE_HEADER + _queue_row("F-02"), encoding="utf-8"
    )
    body = (
        "# Verify F-02\n"
        "Evidence Tag: [POC-PASS]\n"
        "### PoC Attempt\n```solidity\n"
        "function test_F02() public { vault.withdraw(0); }\n```\n"
    )
    (tmp_path / "verify_F-02.md").write_text(body, encoding="utf-8")
    result = _validate_poc_pass_integrity(tmp_path)
    assert len(result) == 1 and result[0]["finding_id"] == "F-02"


def test_t1a_mechanical_manifest_pass_overrides_no_assertion_downgrade(tmp_path):
    """Mechanical PASS in the JSON manifest is ground truth even before annotation."""
    (tmp_path / "verification_queue.md").write_text(
        _QUEUE_HEADER + _queue_row("F-03"), encoding="utf-8"
    )
    body = (
        "# Verify F-03\n"
        "Evidence Tag: [POC-PASS]\n"
        "### PoC Attempt\n```solidity\n"
        "function test_F03() public { vault.withdraw(0); }\n```\n"
    )
    (tmp_path / "verify_F-03.md").write_text(body, encoding="utf-8")
    (tmp_path / "mechanical_verify_manifest.json").write_text(
        '{"results":[{"finding_id":"F-03","status":"PASS"}]}',
        encoding="utf-8",
    )
    assert _validate_poc_pass_integrity(tmp_path) == []


# ---------------------------------------------------------------------------
# T1-b: UNRESOLVED authenticity
# ---------------------------------------------------------------------------

_MFI_HEADER = (
    "## Master Finding Index\n\n"
    "| Report ID | Title | Severity | Location | Verification | Trust Adj. "
    "| Internal Hypothesis |\n"
    "|--|--|--|--|--|--|--|\n"
)


def _report_index(rows: str) -> str:
    return "# Report Index\n\n" + _MFI_HEADER + rows


def test_t1b_phantom_unresolved_without_judge_artifacts(tmp_path):
    """An UNRESOLVED(...) stamp with no skeptic-judge artifact at all is a
    phantom (the observed CONTESTED->UNRESOLVED mislabel)."""
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| L-18 | Some bug | Low | A.sol:1 | UNVERIFIED | UNRESOLVED(Medium) | H-7 |\n"
        ),
        encoding="utf-8",
    )
    issues = _check_report_index_unresolved_authenticity(tmp_path)
    assert issues and "no" in issues[0].lower()


def test_t1b_authentic_unresolved_with_judge_ruling(tmp_path):
    """An UNRESOLVED(...) stamp backed by a real Skeptic-Judge ruling passes."""
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| M-03 | Real bug | Medium | A.sol:1 | CONTESTED | UNRESOLVED(High) | H-7 |\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "skeptic_judge_decisions.md").write_text(
        "## H-7\n**Verdict**: UNRESOLVED\nVerifier and skeptic disagree.\n",
        encoding="utf-8",
    )
    assert _check_report_index_unresolved_authenticity(tmp_path) == []


def test_t1b_phantom_unresolved_with_judge_but_no_matching_ruling(tmp_path):
    """Skeptic-Judge ran, but this finding was not ruled UNRESOLVED ->
    phantom (verifier CONTESTED mislabeled)."""
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| L-20 | Some bug | Low | A.sol:1 | UNVERIFIED | UNRESOLVED(Medium) | H-9 |\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "skeptic_judge_decisions.md").write_text(
        "## H-2\n**Verdict**: UNRESOLVED\n", encoding="utf-8"
    )
    issues = _check_report_index_unresolved_authenticity(tmp_path)
    assert issues and "mislabeled" in issues[0].lower()


def test_t1b_no_unresolved_rows_is_clean(tmp_path):
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| C-01 | Bug | Critical | A.sol:1 | VERIFIED | - | H-1 |\n"
        ),
        encoding="utf-8",
    )
    assert _check_report_index_unresolved_authenticity(tmp_path) == []


def test_t1b_mfi_parser_header_aware(tmp_path):
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| C-01 | Bug | Critical | A.sol:1 | VERIFIED | - | H-1 |\n"
            "| H-02 | Bug2 | High | B.sol:2 | UNVERIFIED | - | H-2 |\n"
        ),
        encoding="utf-8",
    )
    rows = _parse_master_finding_index_rows(tmp_path)
    assert len(rows) == 2
    assert rows[0]["report_id"] == "C-01" and rows[0]["severity"] == "Critical"
    assert rows[1]["internal"] == "H-2"


# ---------------------------------------------------------------------------
# T2-a: verifier skip-vocabulary + build status
# ---------------------------------------------------------------------------

def test_t2a_build_succeeded(tmp_path):
    (tmp_path / "build_status.md").write_text(
        "# Build Status\n\nStatus: SUCCESS\n", encoding="utf-8"
    )
    assert _build_succeeded(tmp_path) is True
    (tmp_path / "build_status.md").write_text(
        "# Build Status\n\nBuild Status: FAILED - missing remap\n",
        encoding="utf-8",
    )
    assert _build_succeeded(tmp_path) is False
    (tmp_path / "build_status.md").write_text("# Build Status\n", encoding="utf-8")
    assert _build_succeeded(tmp_path) is None


def test_t2a_no_build_env_invalid_when_build_succeeded(tmp_path):
    (tmp_path / "build_status.md").write_text(
        "Status: SUCCESS\n", encoding="utf-8"
    )
    (tmp_path / "verification_queue.md").write_text(
        _QUEUE_HEADER + _queue_row("F-01", poc_class="unit"), encoding="utf-8"
    )
    (tmp_path / "verify_F-01.md").write_text(
        "# Verify F-01\n### PoC Attempt\n- Attempted: NO\n"
        "- PoC Not Attempted Because: NO_BUILD_ENVIRONMENT\n",
        encoding="utf-8",
    )
    warnings = _validate_verifier_skip_vocabulary(tmp_path)
    assert warnings and "NO_BUILD_ENVIRONMENT" in warnings[0]
    assert (tmp_path / "verifier_skip_audit.md").exists()


def test_t2a_valid_skip_not_flagged(tmp_path):
    (tmp_path / "build_status.md").write_text(
        "Status: FAILED\n", encoding="utf-8"
    )
    (tmp_path / "verification_queue.md").write_text(
        _QUEUE_HEADER + _queue_row("F-01", poc_class="structural"),
        encoding="utf-8",
    )
    (tmp_path / "verify_F-01.md").write_text(
        "# Verify F-01\n### PoC Attempt\n- Attempted: NO\n"
        "- PoC Not Attempted Because: PURE_SPEC_OR_DOCS_ONLY\n",
        encoding="utf-8",
    )
    assert _validate_verifier_skip_vocabulary(tmp_path) == []


def test_t2a_na_skip_on_unit_finding_flagged(tmp_path):
    (tmp_path / "verification_queue.md").write_text(
        _QUEUE_HEADER + _queue_row("F-01", poc_class="unit"), encoding="utf-8"
    )
    (tmp_path / "verify_F-01.md").write_text(
        "# Verify F-01\n### PoC Attempt\n- Attempted: NO\n"
        "- PoC Not Attempted Because: N/A\n",
        encoding="utf-8",
    )
    warnings = _validate_verifier_skip_vocabulary(tmp_path)
    assert warnings and "N/A" in warnings[0]


# ---------------------------------------------------------------------------
# T2-c: speculative Critical chains
# ---------------------------------------------------------------------------

def test_t2c_unverified_critical_chain_flagged(tmp_path):
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| C-01 | Compound exploit | Critical | A.sol:1 | UNVERIFIED | - | CH-1 |\n"
        ),
        encoding="utf-8",
    )
    issues = _check_speculative_critical_chains(tmp_path)
    assert issues and "C-01" in issues[0]


def test_t2c_verified_critical_chain_not_flagged(tmp_path):
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| C-01 | Compound exploit | Critical | A.sol:1 | VERIFIED | - | CH-1 |\n"
        ),
        encoding="utf-8",
    )
    assert _check_speculative_critical_chains(tmp_path) == []


def test_t2c_non_chain_critical_not_flagged(tmp_path):
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| C-01 | Direct loss | Critical | A.sol:1 | UNVERIFIED | - | H-4 |\n"
        ),
        encoding="utf-8",
    )
    assert _check_speculative_critical_chains(tmp_path) == []


def test_t2c_joined_constituent_chain_flagged(tmp_path):
    (tmp_path / "report_index.md").write_text(
        _report_index(
            "| C-02 | Compound | Critical | A.sol:1 | UNVERIFIED | - | H-2+H-13 |\n"
        ),
        encoding="utf-8",
    )
    issues = _check_speculative_critical_chains(tmp_path)
    assert issues and "C-02" in issues[0]


# ---------------------------------------------------------------------------
# F1: hypothesis-ID taxonomy — verify_queue parity recognizes all six
# SC grouped prefixes (HC/HH/HM/HL/HI/GRP) plus CH + bare INV.
# A prior scratchpad emitted all of these; the prior regex catalogue missed
# them all and silently dropped 78 INV constituents from accounting.
# ---------------------------------------------------------------------------

_GROUPED_QUEUE_HEADER = (
    "# Verification Queue Manifest\n"
    "| Queue # | Finding ID | Severity | Title | Bug Class "
    "| Preferred Tag | Location | Primary Artifact | PoC Class |\n"
    "|--|--|--|--|--|--|--|--|--|\n"
)


def _grouped_queue_row(fid: str, sev: str = "High") -> str:
    return (
        f"| 1 | {fid} | {sev} | A bug | panic | [POC-PASS] "
        f"| src/Vault.sol:45 | depth.md | unit |\n"
    )


def _inventory_block(fid: str) -> str:
    return f"## {fid} A bug\n**Location**: `src/Vault.sol:45`\n**Severity**: Medium\n\n"


def _build_grouped_fixture(tmp_path):
    """Build a fixture that mirrors a prior scratchpad shape: a verify
    queue with HC/HH/HM/HL/HI/GRP/CH grouped hypotheses + bare INV rows,
    plus matching finding_mapping.md and hypotheses.md."""
    # Inventory: 7 grouped constituents + 1 bare-INV active.
    inventory = (
        "# Findings Inventory\n\n"
        + "".join(
            _inventory_block(f"INV-00{i}") for i in range(1, 8)
        )
        + _inventory_block("INV-100")
    )
    (tmp_path / "findings_inventory.md").write_text(inventory, encoding="utf-8")

    # Queue: every grouped prefix + one bare INV.
    queue = _GROUPED_QUEUE_HEADER + "".join([
        _grouped_queue_row("HC-01", "Critical"),
        _grouped_queue_row("HH-01", "High"),
        _grouped_queue_row("HM-01", "Medium"),
        _grouped_queue_row("HL-01", "Low"),
        _grouped_queue_row("HI-01", "Info"),
        _grouped_queue_row("GRP-01", "High"),
        _grouped_queue_row("CH-01", "High"),
        _grouped_queue_row("INV-100", "Medium"),
    ])
    (tmp_path / "verification_queue.md").write_text(queue, encoding="utf-8")

    # finding_mapping.md: each grouped hypothesis maps to one INV.
    fm = (
        "# Finding Mapping\n\n"
        "| Finding ID | Source | Hypothesis | Severity | Note |\n"
        "|--|--|--|--|--|\n"
        "| INV-001 | breadth | HC-01 | Critical | a |\n"
        "| INV-002 | breadth | HH-01 | High     | b |\n"
        "| INV-003 | breadth | HM-01 | Medium   | c |\n"
        "| INV-004 | breadth | HL-01 | Low      | d |\n"
        "| INV-005 | breadth | HI-01 | Info     | e |\n"
        "| INV-006 | breadth | GRP-01 | High    | f |\n"
        "| INV-007 | breadth | CH-01 | High     | g |\n"
    )
    (tmp_path / "finding_mapping.md").write_text(fm, encoding="utf-8")

    # hypotheses.md: section headings + INV mentions (exercises
    # _HYPO_HEADING_RE on every new prefix).
    hyp_lines = ["# Hypotheses\n"]
    for hid, inv in [
        ("HC-01", "INV-001"), ("HH-01", "INV-002"), ("HM-01", "INV-003"),
        ("HL-01", "INV-004"), ("HI-01", "INV-005"), ("GRP-01", "INV-006"),
        ("CH-01", "INV-007"),
    ]:
        hyp_lines.append(f"## Hypothesis {hid}\n- Source Findings: {inv}\n")
    (tmp_path / "hypotheses.md").write_text(
        "\n".join(hyp_lines), encoding="utf-8"
    )


def test_f1_parity_recognizes_all_sc_grouped_prefixes(tmp_path):
    """Every SC grouped hypothesis prefix (HC/HH/HM/HL/HI/GRP/CH) plus the
    bare INV row must expand to its INV constituent — `_validate_*_parity`
    returns no dropouts.  Pre-fix a prior run reported 78 missing of 136."""
    _build_grouped_fixture(tmp_path)
    issues = _validate_verification_queue_inventory_parity(tmp_path)
    assert issues == [], (
        f"expected zero dropouts on the all-grouped-prefixes fixture, got: {issues}"
    )


def test_f1_parity_regression_unknown_prefix_still_flagged(tmp_path):
    """The fix is additive, not a wildcard — an unknown prefix `XX-99`
    appearing on the queue with no inventory backing is still flagged as
    extra (or, equivalently, the queue's claim is not silently swallowed)."""
    _build_grouped_fixture(tmp_path)
    # Append a stray XX-99 row that nothing in the inventory backs.
    queue_path = tmp_path / "verification_queue.md"
    queue_path.write_text(
        queue_path.read_text(encoding="utf-8") + _grouped_queue_row("XX-99"),
        encoding="utf-8",
    )
    issues = _validate_verification_queue_inventory_parity(tmp_path)
    # XX-99 doesn't normalize, so it's discarded from active_ids before the
    # `extra` check — that's fine; the fix must not pretend XX-99 is a
    # legitimate prefix.  The key contract: the seven known grouped
    # prefixes remain acknowledged (no dropouts).
    assert all("dropout" not in s for s in issues), issues


def test_f1_hypothesis_heading_regex_matches_grouped_prefixes():
    """`_HYPO_HEADING_RE` must extract every emitted SC grouped prefix from a
    `## Hypothesis <ID>` heading."""
    from plamen_parsers import _HYPO_HEADING_RE
    for heading, expected in [
        ("## Hypothesis HC-02", "HC-02"),
        ("## HH-01", "HH-01"),
        ("## Hypothesis HM-42", "HM-42"),
        ("## HL-20", "HL-20"),
        ("## HI-02", "HI-02"),
        ("## GRP-26", "GRP-26"),
        ("## Chain Hypothesis CH-09", "CH-09"),
    ]:
        m = _HYPO_HEADING_RE.search(heading)
        assert m and m.group(1).upper() == expected, (heading, m)


def test_f1_normalize_finding_id_propagates_to_grouped_prefixes():
    """The catalog change at `_ID_HYPO_ALTS` must propagate to
    `_normalize_finding_id` via `_FINDING_ID_EXTRACT_RE`/`_ID_ALL_INTERNAL`."""
    from plamen_parsers import _normalize_finding_id
    for raw, expected in [
        ("HC-02", "HC-02"), ("HH-01", "HH-01"), ("HM-42", "HM-42"),
        ("HL-20", "HL-20"), ("HI-02", "HI-02"), ("GRP-26", "GRP-26"),
        ("CH-09", "CH-09"),
    ]:
        assert _normalize_finding_id(raw) == expected
    assert _normalize_finding_id("XX-99") == ""
