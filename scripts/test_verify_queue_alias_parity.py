"""Regression tests for verification queue alias parity."""
from __future__ import annotations

from pathlib import Path

from plamen_validators import _validate_verification_queue_inventory_parity


def test_excluded_hypothesis_alias_acknowledges_inventory_id(tmp_path: Path) -> None:
    (tmp_path / "findings_inventory.md").write_text(
        "\n".join(
            [
                "# Findings Inventory",
                "",
                "### Finding [INV-013]: Low issue",
                "**Severity**: Low",
                "**Location**: Contract.sol:L1",
                "**Preferred Tag**: CODE",
                "**Verdict**: CONFIRMED",
                "**Root Cause**: rc",
                "**Description**: desc",
                "**Impact**: impact",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "verification_queue.md").write_text(
        "# Verification Queue Manifest\n"
        "| Queue # | Finding ID | Expected Output File | Severity | Title |\n"
        "|---------|------------|----------------------|----------|-------|\n",
        encoding="utf-8",
    )
    (tmp_path / "verification_queue_evidence_excluded.md").write_text(
        "# Verification Queue Evidence-Excluded\n"
        "| Finding ID | Severity | Title | Exclusion Reason |\n"
        "|------------|----------|-------|------------------|\n"
        "| H-10 | Low | Low issue | Excluded from active SC verification in light mode |\n",
        encoding="utf-8",
    )
    (tmp_path / "finding_mapping.md").write_text(
        "| Hypothesis | Constituent |\n"
        "|------------|-------------|\n"
        "| H-10 | INV-013 |\n",
        encoding="utf-8",
    )

    assert _validate_verification_queue_inventory_parity(tmp_path) == []
