"""2026-06-02: Fix A — a present, substantive-but-UNIFORM (formulaic) confidence
table is advisory-only in Core/Light SC and L1 depth gates (no iter2/iter3
adaptive routing depends on differentiated scores there). It must STILL block in
Thorough, and genuine stubs (driver-synthesized placeholder / empty / no rows)
must STILL block in every mode.

These tests satisfy all the OTHER never-cut groups with substantive findings so
that ONLY the confidence group is at issue.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from plamen_validators import _validate_depth_artifact_substance


_SUBSTANTIVE_FINDING = """# __ROLE__

## Finding [__PREFIX__-1]: Reentrancy in withdraw path lets attacker drain vault

**Verdict**: CONFIRMED
**Severity**: High
**Location**: src/Vault.sol:L120-L155
**Description**: The withdraw() function transfers ETH to msg.sender before
zeroing the caller's balance, so a malicious receiver can re-enter withdraw()
and repeatedly pull funds while the recorded balance is still non-zero.
**Impact**: An attacker can drain the entire vault balance in a single
transaction by recursively re-entering the withdraw path. This is a direct
loss-of-funds vulnerability affecting every depositor.
**Evidence**:
```solidity
(bool ok,) = msg.sender.call{value: amount}("");
require(ok);
balances[msg.sender] = 0; // state update AFTER external call
```

## Finding [__PREFIX__-2]: Missing access control on setOracle()

**Verdict**: CONFIRMED
**Severity**: High
**Location**: src/Vault.sol:L210
**Description**: setOracle() lacks an onlyOwner modifier so any caller can swap
the price oracle for one they control, enabling arbitrary mispricing of
collateral and subsequent liquidation or borrowing manipulation.
**Impact**: Full economic control of the protocol's pricing surface by any
unprivileged account.
**Evidence**:
```solidity
function setOracle(address o) external { oracle = o; }
```
""".strip()


# All SC never-cut group members EXCEPT confidence — written substantively so
# only the confidence group can be flagged.
_SC_OTHER_GROUP_FILES = [
    ("depth_token_flow_findings.md", "TF"),
    ("depth_state_trace_findings.md", "ST"),
    ("depth_edge_case_findings.md", "EC"),
    ("depth_external_findings.md", "EX"),
    ("blind_spot_a_findings.md", "BLIND-A"),
    ("blind_spot_b_findings.md", "BLIND-B"),
    ("blind_spot_c_findings.md", "BLIND-C"),
    ("validation_sweep_findings.md", "VS"),
]

# L1 base + core never-cut group members EXCEPT confidence.
_L1_OTHER_GROUP_FILES = [
    ("depth_consensus_invariant_findings.md", "CI"),
    ("depth_network_surface_findings.md", "NS"),
    ("depth_state_trace_findings.md", "ST"),
    ("depth_external_findings.md", "EX"),
    ("depth_edge_case_findings.md", "EC"),
]


def _write_other_groups(sp: Path, files) -> None:
    for fname, prefix in files:
        body = _SUBSTANTIVE_FINDING.replace("__ROLE__", fname).replace(
            "__PREFIX__", prefix
        )
        # Guarantee well over the 200-byte generic-stub floor.
        body = body + "\n\n<!-- padding -->\n" + ("x" * 256)
        (sp / fname).write_text(body, encoding="utf-8")


def _confidence_table(composites) -> str:
    header = (
        "| Finding ID | Evidence | Consensus | Quality | RAG | "
        "Composite | Classification | Source |"
    )
    sep = "|---|---|---|---|---|---|---|---|"
    rows = []
    for i, c in enumerate(composites, start=1):
        cls = "CONFIDENT" if c >= 0.7 else "UNCERTAIN"
        rows.append(
            f"| TF-{i} | 0.8 | 1.0 | 0.4 | 0.3 | {c} | {cls} | depth_token_flow |"
        )
    return (
        "# Confidence Scores\n\n"
        + "\n".join([header, sep, *rows])
        + "\n"
    )


def _write_confidence(sp: Path, text: str) -> None:
    (sp / "confidence_scores.md").write_text(text, encoding="utf-8")


def test_sc_core_formulaic_confidence_nonblocking(tmp_path: Path):
    _write_other_groups(tmp_path, _SC_OTHER_GROUP_FILES)
    _write_confidence(tmp_path, _confidence_table([0.47] * 5))
    stubs = _validate_depth_artifact_substance(tmp_path, "core", "sc")
    assert not any("confidence_scores.md" in s for s in stubs), stubs


def test_sc_thorough_formulaic_confidence_blocks(tmp_path: Path):
    # Same scratchpad, thorough mode -> the formulaic confidence DOES block.
    # (Thorough adds the THOROUGH_EXTRAS groups; those are intentionally absent
    # so they would also flag, but we specifically assert confidence is flagged.)
    _write_other_groups(tmp_path, _SC_OTHER_GROUP_FILES)
    _write_confidence(tmp_path, _confidence_table([0.47] * 5))
    stubs = _validate_depth_artifact_substance(tmp_path, "thorough", "sc")
    assert any(
        "confidence_scores.md" in s and "formulaic" in s for s in stubs
    ), stubs


def test_sc_core_synthesized_placeholder_still_blocks(tmp_path: Path):
    _write_other_groups(tmp_path, _SC_OTHER_GROUP_FILES)
    placeholder = (
        "# Confidence Scores\n\n"
        "> **Status**: SYNTHESIZED\n\n"
        "Driver wrote this placeholder; no per-finding scoring was performed.\n"
    )
    _write_confidence(tmp_path, placeholder)
    stubs = _validate_depth_artifact_substance(tmp_path, "core", "sc")
    assert any("confidence_scores.md" in s for s in stubs), stubs


def test_l1_core_formulaic_confidence_nonblocking(tmp_path: Path):
    _write_other_groups(tmp_path, _L1_OTHER_GROUP_FILES)
    _write_confidence(tmp_path, _confidence_table([0.47] * 5))
    stubs = _validate_depth_artifact_substance(tmp_path, "core", "l1")
    assert not any("confidence_scores.md" in s for s in stubs), stubs


def test_sc_core_differentiated_confidence_ok(tmp_path: Path):
    # Control: differentiated composites are never flagged in any mode.
    _write_other_groups(tmp_path, _SC_OTHER_GROUP_FILES)
    _write_confidence(tmp_path, _confidence_table([0.30, 0.47, 0.55, 0.70, 0.82]))
    for mode in ("light", "core", "thorough"):
        stubs = _validate_depth_artifact_substance(tmp_path, mode, "sc")
        assert not any("confidence_scores.md" in s for s in stubs), (mode, stubs)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
