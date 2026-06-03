"""Fix #8 (P2) — Codex attempt-1 churn reduction via PROMPT enforcement.

These tests lock in two prompt directives so they cannot silently regress:

(a) The L1 recon prompt (`prompts/l1/phase1-recon-prompt.md`) must carry a
    pre-DONE coverage directive instructing the agent to enumerate every
    top-level module/crate with >=10 source files and either cite at least one
    file from it OR acknowledge it in scope_leftover.md before returning DONE.
    This pre-empts the `_validate_recon_coverage` gate that otherwise costs a
    retry (e.g. "crates/database 17 files not cited").

(b) The shared L1 verification prompt
    (`prompts/shared/v2/phase5-verification-l1.md`) must state that for rust/go
    node-client findings with NO local build/fork/harness the honest ledger
    answer is `Attempted: NO` with a real blocker, and that a code-trace is NOT
    `Attempted: YES`. This kills the bimodal attempt-1 guess in both directions.

Both directives are gate-prompt guidance only — they MUST NOT weaken any gate
or instruct the model to skip analysis. The tests below assert presence of the
guidance plus its non-weakening framing.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_types import plamen_home  # noqa: E402

ROOT = plamen_home()

RECON_PROMPT = ROOT / "prompts" / "l1" / "phase1-recon-prompt.md"
VERIFY_PROMPT = ROOT / "prompts" / "shared" / "v2" / "phase5-verification-l1.md"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Fix #8(a): L1 recon pre-DONE coverage directive
# ---------------------------------------------------------------------------

def test_recon_prompt_has_pre_done_coverage_directive():
    text = _read(RECON_PROMPT)
    # The directive lives in / near the Return protocol so it is read just
    # before the agent emits DONE.
    assert "Pre-DONE coverage gate" in text, (
        "Missing the pre-DONE coverage gate directive in the L1 recon prompt"
    )
    # It must reference the >=10-file module threshold (the gate's trigger).
    assert ("≥10 source files" in text) or (">=10 source files" in text) or (
        "≥10 files" in text
    ), "Pre-DONE directive must reference the >=10-file module threshold"
    # It must offer the two-way resolution: CITED or ACKNOWLEDGED in scope_leftover.
    lowered = text.lower()
    assert "scope_leftover.md" in lowered
    assert "acknowledged" in lowered
    assert "cited" in lowered


def test_recon_pre_done_directive_references_the_coverage_gate():
    text = _read(RECON_PROMPT)
    # Must name the actual driver gate so the intent (pre-empt the retry) is clear.
    assert "_validate_recon_coverage" in text, (
        "Pre-DONE directive should name the _validate_recon_coverage gate it pre-empts"
    )
    # The directive must appear before the DONE return line so it is enforced pre-DONE.
    idx_directive = text.find("Pre-DONE coverage gate")
    idx_done = text.find("Return ONLY: `DONE: L1 Recon Agent")
    assert idx_directive != -1 and idx_done != -1
    assert idx_directive < idx_done, (
        "Pre-DONE coverage directive must precede the DONE return line"
    )


def test_recon_pre_done_directive_is_backend_agnostic():
    text = _read(RECON_PROMPT)
    # The directive section must not gate itself behind a specific backend
    # (it should help both Claude and Codex, cost neither).
    start = text.find("Pre-DONE coverage gate")
    end = text.find("Return ONLY: `DONE: L1 Recon Agent")
    section = text[start:end]
    assert "codex" not in section.lower(), (
        "Pre-DONE coverage directive must stay backend-agnostic"
    )


# ---------------------------------------------------------------------------
# Fix #8(b): L1 verify ledger honesty directive
# ---------------------------------------------------------------------------

def test_verify_prompt_ledger_honesty_directive_present():
    text = _read(VERIFY_PROMPT)
    assert "Ledger honesty" in text, (
        "Missing the ledger-honesty directive in the L1 verification prompt"
    )
    lowered = text.lower()
    # No build/fork/harness -> Attempted: NO with a real blocker.
    assert "no_build_environment" in lowered
    assert "external_dependency_no_fork_or_address" in lowered
    # Code-trace is NOT Attempted: YES.
    assert "code-trace" in lowered
    assert "attempted: no" in lowered
    assert "attempted: yes" in lowered


def test_verify_ledger_directive_does_not_weaken_gate_or_skip_analysis():
    text = _read(VERIFY_PROMPT)
    start = text.find("Ledger honesty")
    # Section runs up to the Schema rules block that follows it.
    end = text.find("**Schema rules**:", start)
    assert start != -1 and end != -1 and end > start
    section = text[start:end]
    lowered = section.lower()
    # Must explicitly state it does NOT instruct skipping analysis.
    assert "does not instruct you to skip analysis" in lowered or (
        "not instruct you to skip analysis" in lowered
    )
    # Must reaffirm the no-penalty / never-halt degrade so it can't be read as a gate change.
    assert "without penalty" in lowered
    assert "never halt" in lowered or "never halts" in lowered
    # Must address BOTH directions of the bimodal guess.
    assert "to look thorough" in lowered  # don't over-claim Attempted: YES
    assert "to avoid" in lowered          # don't under-claim Attempted: NO


def test_existing_post_write_verification_still_present():
    # 1616fca added the PoC ledger schema + post-write read-back; assert it
    # remains so our edit didn't displace prior anti-regression content.
    text = _read(VERIFY_PROMPT)
    assert "POST-WRITE VERIFICATION" in text
    assert "### PoC Attempt" in text
    assert "### Execution Result" in text


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
