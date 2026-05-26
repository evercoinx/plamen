"""T2-4 (byte-floor parity, SW19) + T2-6 (marker DOTALL SW15-1, allowlist SW16)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
import plamen_parsers as P  # noqa: E402
from plamen_prompt import (  # noqa: E402
    _render_expected_output_block, _LEGITIMATE_SUBPRODUCER_PATTERNS,
)


# ───────────────── T2-4: byte-floor parity ─────────────────

def test_expected_output_block_states_gate_min_not_hardcoded_200():
    breadth = next(p for p in D.SC_PHASES if p.name == "breadth")
    block = _render_expected_output_block(breadth)
    mb = getattr(breadth, "min_artifact_bytes", 100)
    assert f">= {mb} bytes" in block
    # the false hardcoded 200 must be gone from the HARD CONTRACT sentence
    assert ">= 200 bytes)" not in block


def test_instantiate_states_its_lower_floor():
    inst = next(p for p in D.SC_PHASES if p.name == "instantiate")
    block = _render_expected_output_block(inst)
    mb = getattr(inst, "min_artifact_bytes", 100)
    assert mb == 50  # instantiate override
    assert ">= 50 bytes" in block


# ───────────────── T2-6a: marker regex no longer swallows ─────────────────

def test_malformed_marker_does_not_swallow_next_marker(tmp_path):
    """A marker missing its `-->` must not let DOTALL swallow the body + the
    real COMPLETE marker (which misread a COMPLETE file as IN_PROGRESS)."""
    p = tmp_path / "a.md"
    p.write_text(
        "<!-- PLAMEN_STATUS: IN_PROGRESS\n"   # malformed: no closing -->
        "some body line\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n",  # the real, well-formed marker
        encoding="utf-8",
    )
    markers = P._extract_artifact_status(p)
    assert markers.get("STATUS") == "COMPLETE"


def test_wellformed_single_line_marker_still_parses(tmp_path):
    p = tmp_path / "b.md"
    p.write_text("body\n<!-- PLAMEN_STATUS: COMPLETE -->\n<!-- PLAMEN_FINDINGS_COUNT: 3 -->\n",
                 encoding="utf-8")
    m = P._extract_artifact_status(p)
    assert m.get("STATUS") == "COMPLETE"
    assert m.get("FINDINGS_COUNT") == "3"


# ───────────────── T2-6b: allowlist additions ─────────────────

def test_allowlist_includes_gate_owned_artifacts():
    for name in ("rescan_manifest.md", "opengrep_findings.md", "severity_binding.md"):
        assert name in _LEGITIMATE_SUBPRODUCER_PATTERNS, name
