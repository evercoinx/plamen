"""V1→V2 phase wiring drift detector.

Background: an observed audit recall regression vs the v1.0.5
benchmark was traced to THREE phases that were documented in V1's
mode matrix + prompts but never wired into V2's deterministic driver:

  - Phase 4a.5 Pass 2 (Recursive Semantic Gap Trace) — Thorough
  - Phase 4c iteration 2 (Chain Composition) — Thorough
  - Phase 5.5 (Post-Verification Finding Extraction) — Thorough

All three had inline spawn templates in `commands/plamen.md` AND mode
matrix references, but no `Phase(...)` entry in `SC_PHASES`/`L1_PHASES`.
The V2 driver section-extracts phase sections by Phase entry; if no
Phase entry exists, the content is dead code.

This test enumerates ALL ## Phase / ## Step section headings in
`commands/plamen.md` and reports any that have no matching Phase
section_marker. Default: WARN (does not fail the test) — the user
direction is "consistent structure that does not halt future
improvements." If a future audit reveals another orphaned phase, the
warning will surface it without blocking development. If you want
strict CI enforcement, set PLAMEN_STRICT_PHASE_WIRING=1 in the test
environment.

NOTE: Many ## Step N headings are setup/wizard sub-sections that V2's
Python driver handles natively and don't need section_markers. The
heuristic filters to plausible PIPELINE phases only (Phase N pattern,
plus Step 4+ which are V1's "pipeline" steps; Step 0-3 are wizard).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_types import SC_PHASES, L1_PHASES, plamen_home  # noqa: E402


# Headings that are documentation-only (wizard, mode notes, etc.) and
# correctly NOT wired as Phase() entries. Filter these out of the
# orphan check so we don't false-alarm on documentation structure.
_KNOWN_NON_PIPELINE_PATTERNS = (
    # Wizard / setup
    re.compile(r"^Step\s+-?\d*[a-z]?(\.\d+)?:\s+(?!.*Verification|.*Aggregate|.*Skeptic|.*Crossbatch|.*Report|.*Depth|.*Inventory|.*Invariant|.*Chain|.*Recon|.*Breadth|.*Rescan|.*Per[- ]Contract|.*Synthesis|.*Adaptive|.*Post[- ]Verification)"),
    # Mode notes / section descriptors (not phases)
    re.compile(r"^Phase\s+\d+:\s+Report Generation$"),  # parent for tier writers
)


def _is_pipeline_phase_heading(heading: str) -> bool:
    """Return True if this heading is plausibly a PIPELINE phase that
    should have a `Phase(...)` entry in the driver."""
    h = heading.strip()
    if not re.match(r"^(Phase|Step)\s+\d", h):
        # Probably "Phase 4a.5 Pass 2: ..." or "Phase 4c Iteration 2: ..."
        # — those ARE pipeline phases. Allow if it starts with Phase.
        if re.match(r"^Phase\s+\d+[a-z]?\.\d+\s+(Pass|Iteration|Iter)", h):
            return True
        # Step 4a.5 Pass 2 / Step 5.5b / etc.
        if re.match(r"^Step\s+\d+[a-z]?\.\d+[a-z]?:\s+(?:Pass|Iteration|Iter|Post[- ]Verification|Recursive|Aggregate)", h):
            return True
        return False
    for pat in _KNOWN_NON_PIPELINE_PATTERNS:
        if pat.match(h):
            return False
    # Filter out wizard steps (Step 0-3) — they're handled by V2 driver natively
    m = re.match(r"^Step\s+(-?\d+)", h)
    if m:
        try:
            num = int(m.group(1))
        except ValueError:
            return False
        if num < 4:
            return False
    return True


def _collect_doc_pipeline_headings(text: str) -> set[str]:
    out = set()
    for m in re.finditer(r"^#{2,4}\s+(.+?)$", text, re.MULTILINE):
        h = m.group(1).strip()
        if _is_pipeline_phase_heading(h):
            out.add(h)
    return out


def _collect_wired_markers() -> set[str]:
    wired: set[str] = set()
    for ph in list(SC_PHASES) + list(L1_PHASES):
        for m in (ph.section_markers or []):
            wired.add(m.strip())
    return wired


def _strip_parentheticals(text: str) -> str:
    """Remove `(...)` and `[...]` clauses for cosmetic match tolerance."""
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*", " ", text).strip()


def _heading_matches_any_marker(heading: str, markers: set[str]) -> bool:
    """Soft match: a documented heading is considered wired if ANY phase
    marker is a substring of the heading OR vice versa. Parentheticals
    are stripped before matching so wording differences like:

       doc:     'Phase 3b: Breadth Re-Scan (THOROUGH mode only)'
       marker:  'Phase 3b: Breadth Re-Scan (+ Phase 3c per-contract sub-step)'

    don't generate false-positive orphans. Both stripped to
    'Phase 3b: Breadth Re-Scan' and match cleanly."""
    h = heading.strip()
    h_bare = _strip_parentheticals(h)
    for m in markers:
        m_norm = m.strip()
        if not m_norm:
            continue
        if m_norm in h or h in m_norm:
            return True
        m_bare = _strip_parentheticals(m_norm)
        if m_bare and (m_bare in h_bare or h_bare in m_bare):
            return True
    return False


def test_no_orphaned_pipeline_phases_in_commands_plamen_md():
    """Default: WARN on orphans (don't halt). Strict mode (env var
    `PLAMEN_STRICT_PHASE_WIRING=1`) makes this a hard fail for CI."""
    plamen_md = (plamen_home() / "commands" / "plamen.md").read_text(
        encoding="utf-8",
    )
    doc_headings = _collect_doc_pipeline_headings(plamen_md)
    wired = _collect_wired_markers()
    orphans = sorted(
        h for h in doc_headings
        if not _heading_matches_any_marker(h, wired)
    )
    if not orphans:
        return  # clean
    msg = (
        f"\n{len(orphans)} documented pipeline phase(s) in "
        "commands/plamen.md have no matching Phase() entry in "
        "SC_PHASES or L1_PHASES — the V1→V2 wiring-gap class that "
        "caused a recall regression:\n\n"
        + "\n".join(f"  • {o}" for o in orphans)
    )
    strict = os.environ.get("PLAMEN_STRICT_PHASE_WIRING", "0") == "1"
    if strict:
        raise AssertionError(msg + "\n\n(Strict mode — set PLAMEN_STRICT_PHASE_WIRING=0 to downgrade to warning.)")
    # Default: warn but don't fail. Print to stderr so pytest shows it.
    print(msg, file=sys.stderr)


def test_known_orphans_now_wired():
    """Explicit positive lock: the 3 phases we just wired must be
    findable by the orphan check (i.e. they should NOT appear in the
    orphan list). If they regress and get unwired again, this test
    fires loud."""
    wired = _collect_wired_markers()
    for required in (
        "Phase 4a.5 Pass 2",
        "Phase 4c Iteration 2",
        "Phase 5.5",
    ):
        match = any(required in m for m in wired)
        assert match, (
            f"Expected wired marker matching {required!r}, none found. "
            "The V1→V2 wiring fix has regressed; see "
            "tests/test_invariants_pass2_wiring.py, "
            "test_chain_iter2_wiring.py, "
            "test_post_verify_extract_wiring.py for the originals."
        )
