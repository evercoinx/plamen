"""CHANGE 2 — discovery-framing directive presence assertions.

Locks in two HOW-to-look discovery directives so a future edit cannot silently
drop them:

  (i) attacker-only / anti-self-refutation framing must reach BOTH breadth and
      depth discovery (prompts/shared/v2/phase3-breadth.md and
      prompts/shared/v2/phase4b-depth.md).
 (ii) cross-contract weaponization must remain an EXTENSION of the existing
      Sibling Propagation Agent (prompts/evm/phase4b-scanner-templates.md), not
      a new parallel block.

These are pure text-presence checks — no driver import needed.

Run: `python -m pytest scripts/test_change2_discovery_framing.py -q`
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BREADTH = REPO / "prompts" / "shared" / "v2" / "phase3-breadth.md"
DEPTH = REPO / "prompts" / "shared" / "v2" / "phase4b-depth.md"
SCANNER = REPO / "prompts" / "evm" / "phase4b-scanner-templates.md"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_breadth_has_discovery_stance():
    """Attacker-framing reaches breadth discovery workers."""
    body = _read(BREADTH).lower()
    assert "discovery stance" in body
    assert "amplify" in body
    assert "verifier/skeptic gate" in body


def test_depth_has_discovery_stance():
    """Attacker-framing reaches depth discovery workers."""
    body = _read(DEPTH).lower()
    assert "discovery stance" in body
    assert "amplify" in body


def test_amplify_filter_separation_preserved():
    """Discovery blocks defer refutation to the gate; they do not add
    refutation logic, and the existing depth ANCHORING REJECTION LIST is
    untouched (it lives in the language depth template, not these files)."""
    depth = _read(DEPTH)
    # The new discovery block must point refutation at the gate, not perform it.
    assert "ANCHORING REJECTION LIST" in depth
    # No filter verdicts introduced into the discovery-stance section itself.
    stance_start = depth.find("### Discovery Stance")
    stance_end = depth.find("### Standard Depth Agent Semantic Proof Block")
    assert 0 <= stance_start < stance_end
    stance_block = depth[stance_start:stance_end]
    assert "REFUTED" not in stance_block


def test_weaponization_extends_sibling_propagation():
    """Cross-contract weaponization is an extension inside the existing
    Sibling Propagation Agent section, not a new top-level agent block."""
    body = _read(SCANNER)
    sib_start = body.find("## Sibling Propagation Agent")
    assert sib_start >= 0
    # Section ends at the next top-level agent heading.
    sib_end = body.find("## Design Stress Testing Agent", sib_start)
    assert sib_end > sib_start
    section = body[sib_start:sib_end]
    assert "AND contracts in scope" in section
    assert "audit miss" in section
    # No new parallel weaponization agent section was added.
    assert "## Cross-Contract Weaponization Agent" not in body
