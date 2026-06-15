"""Ship D — finding-block heading unification + placeholder rejection.

Covers:
- SW07-3/5/1: depth findings use H3 `### Finding [ID]`; the driver confidence
  synth + validators stub re-check were H2-ONLY and saw zero depth findings.
  One canonical FINDING_BLOCK_HEADING_RE (H2/H3, captures ID) now.
- SW03-1: a `## Findings` section whose body is just a crash-safety placeholder
  ("(findings appended below as they are discovered)") must NOT count as work.
- SW14-2/3/4: the report assembler must classify `## [C-01]` and `### [ C-01 ]`
  (H2 + inner whitespace), not only `### [C-01]`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_types as T  # noqa: E402
import plamen_parsers as P  # noqa: E402
import plamen_driver as D  # noqa: E402


# ───────────────── canonical finding-block heading ─────────────────

def test_canonical_matches_h2_and_h3_captures_id():
    re_ = T.FINDING_BLOCK_HEADING_RE
    m2 = re_.search("## Finding [CS-1]: Reentrancy")
    m3 = re_.search("### Finding [TF-3]: Donation")
    assert m2 and m2.group(1) == "CS-1"
    assert m3 and m3.group(1) == "TF-3"


def test_parsers_and_driver_share_canonical_finding_heading():
    assert P._FINDING_BLOCK_HEADING_RE is T.FINDING_BLOCK_HEADING_RE
    assert D._FINDING_HEADING_RE is T.FINDING_BLOCK_HEADING_RE


def test_driver_iter_finding_blocks_sees_h3_depth_findings():
    text = (
        "### Finding [TF-3]: Donation inflation\n"
        "**Verdict**: CONFIRMED\n"
        "Evidence: [BOUNDARY:x=0]\n\n"
        "### Finding [TF-4]: Rounding\n"
        "**Verdict**: CONFIRMED\n"
    )
    ids = [m.group(1) for m in T.FINDING_BLOCK_HEADING_RE.finditer(text)]
    assert ids == ["TF-3", "TF-4"]


# ───────────────── SW03-1 placeholder rejection ─────────────────

def test_breadth_crash_safety_placeholder_is_not_findings():
    stub = "# Core State\n\n## Findings\n\n(findings appended below as they are discovered)\n"
    assert P._findings_section_has_body(stub) is False
    assert P._artifact_has_findings(stub) is False


def test_real_findings_section_still_counts():
    real = ("## Findings\n\nNo exploitable issues; reviewed deposit/withdraw "
            "symmetry and the fee math, all consistent.\n")
    assert P._findings_section_has_body(real) is True


def test_real_section_with_stray_placeholder_phrase_still_counts():
    mixed = ("## Findings\n\nReentrancy in withdraw() lets an attacker drain "
             "the vault before the balance update; remaining checks TBD.\n")
    # contains 'TBD' but has substantial real content -> still counts
    assert P._findings_section_has_body(mixed) is True


def test_finding_block_overrides_placeholder_section():
    both = ("## Findings\n\n(to be filled)\n\n## Finding [CS-1]: real bug\n\ndetails\n")
    assert P._artifact_has_findings(both) is True  # the block counts


# ───────────────── SW14-2/3/4 assembler heading classification ─────────────────

# The assembler's section regex is a local closure; lock its behavior by
# replicating the exact pattern and asserting it now classifies H2 + inner-ws.
_ASSEMBLER_RE = re.compile(
    r"(?im)^(?:#{2,3}\s*(?:[^\n]*\[REPORT-BLOCKED[^\]]*\]\s*)?\[\s*([CHMLI])-\d+\s*\][^\n]*|##\s+\S[^\n]*)",
)


def test_assembler_pattern_classifies_h2_and_inner_whitespace():
    for heading, want in (
        ("## [C-01] Reentrancy", "C"),
        ("### [H-02] Oracle", "H"),
        ("### [ M-03 ] Rounding", "M"),
        ("## [REPORT-BLOCKED: x] [L-04] Minor", "L"),
    ):
        m = _ASSEMBLER_RE.search(heading)
        assert m and m.group(1) == want, heading
    # a non-ID H2 heading is a boundary (group None), not a finding
    m = _ASSEMBLER_RE.search("## Critical Findings")
    assert m and m.group(1) is None


def test_assembler_local_regex_matches_replica():
    """Guard: the in-source assembler regex equals the replica above (so this
    test fails if someone reverts the H2/inner-ws fix)."""
    import plamen_mechanical as MECH
    src = Path(MECH.__file__).read_text(encoding="utf-8")
    assert r"#{2,3}\s*(?:[^\n]*\[REPORT-BLOCKED[^\]]*\]\s*)?\[\s*([CHMLI])-\d+\s*\]" in src
