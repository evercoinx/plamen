"""MODE A: close the PoC structural-escape for resource-exhaustion / unbounded-
input findings.

A deriver-injected gas-bomb finding was refuted as
STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION with NO PoC. Root cause:
  (1) classify_poc_testability had no resource-exhaustion vocabulary, so such
      findings fell through to CODE-TRACE -> structural (a no-PoC class).
  (2) _effective_poc_class honored a verifier's structural self-declaration
      verbatim, with no executable-harm floor -> the mandatory-PoC rule was
      unreachable.

Fix (generic, negation-aware, no protocol/contract/function names):
  - RESOURCE_EXHAUSTION_PATTERNS + _matches_resource_exhaustion shared helper.
  - classify_poc_testability routes a vocab match to 'property' (testable).
  - _effective_poc_class STICKY FLOOR: never honor a structural reclassification
    for a vocab match -> floor at 'property'.
  - _verifier_status_from_text SAFETY NET: a REFUTED/FALSE_POSITIVE backed only
    by a STRUCTURAL skip (no executed PoC, no [POC-FAIL]) on a vocab finding is
    demoted to UNRESOLVED so it stays in the BODY, not an excluded one-liner.

Precision guard (test b): a constant/immutable/standard-mismatch finding (NO
vocab match) stays structural and its CODE-TRACE refutation is untouched —
mirrors the spurious ENUMGAP class that must remain refuted.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_parsers as P  # noqa: E402
import plamen_validators as V  # noqa: E402


# ─────────────────────────── classify routing (fix #2) ───────────────────────

def test_classify_resource_exhaustion_routes_to_property():
    assert P.classify_poc_testability(
        "DoS via unbounded loop", "CODE-TRACE",
        "Distribution iterates over an unbounded array of recipients", "High",
    ) == "property"


def test_classify_gas_bomb_phrase_routes_to_property():
    assert P.classify_poc_testability(
        "gas griefing", "CODE-TRACE", "gas bomb: storage bloat with no size limit",
        "Medium",
    ) == "property"


def test_classify_negated_vocab_stays_structural():
    # The negation guard must prevent "no unbounded loop exists" from routing to
    # a testable class.
    assert P.classify_poc_testability(
        "cleanup note", "CODE-TRACE", "no unbounded loop exists in this path",
        "Low",
    ) == "structural"


def test_classify_const_mismatch_stays_structural_precision():
    # NO vocab match -> falls through to the structural/CODE-TRACE default,
    # exactly as before. Precision guard (the 18/18 spurious ENUMGAP class).
    assert P.classify_poc_testability(
        "constant mismatch", "CODE-TRACE",
        "immutable DECIMALS constant does not match the token standard", "Info",
    ) == "structural"


# ───────────────────── _matches_resource_exhaustion helper ───────────────────

def test_matches_helper_positive_and_negated():
    assert P._matches_resource_exhaustion("unbounded array of recipients")
    assert P._matches_resource_exhaustion("", "gas griefing in the callback")
    assert not P._matches_resource_exhaustion("there is no unbounded loop here")
    assert not P._matches_resource_exhaustion("immutable constant standard mismatch")


# ──────────────────── _effective_poc_class sticky floor (fix #3) ──────────────

_STRUCT_DECLARED = (
    "# Verification: unbounded loop gas bomb refutation\n"
    "**Verdict**: REFUTED\n"
    "### PoC Attempt\n"
    "- PoC Class: structural  (queue said property; no executable harm)\n"
    "- Attempted: NO\n"
    "- PoC Not Attempted Because: STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION\n"
)

_CONST_DECLARED = (
    "# Verification: immutable constant standard mismatch\n"
    "**Verdict**: REFUTED\n"
    "### PoC Attempt\n"
    "- PoC Class: structural  (no executable harm assertion)\n"
    "- Attempted: NO\n"
    "- PoC Not Attempted Because: STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION\n"
)


def test_effective_floor_overrides_structural_declaration():
    # Vocab match -> structural self-declaration is NOT honored; floored to a
    # testable class.
    assert V._effective_poc_class("structural", _STRUCT_DECLARED) == "property"


def test_effective_floor_keeps_existing_testable_queue_class():
    assert V._effective_poc_class("unit", _STRUCT_DECLARED) == "unit"


def test_effective_no_floor_for_non_vocab_honors_structural():
    # Precision: a non-vocab finding's structural declaration IS still honored.
    assert V._effective_poc_class("structural", _CONST_DECLARED) == "structural"


# ─────────────────── _valid_poc_skip on floored property (fix #3) ─────────────

def test_structural_skip_invalid_on_floored_property():
    assert V._valid_poc_skip(_STRUCT_DECLARED, "property") is False


# ───────────────── contract gate: mandatory PoC re-queue (test a) ─────────────

_QUEUE_HDR = (
    "| Finding ID | Severity | Title | Location | PoC Class |\n"
    "|---|---|---|---|---|\n"
)


def _write_queue(scratchpad: Path, fid: str, sev: str, title: str, poc_class: str):
    (scratchpad / "verification_queue.md").write_text(
        _QUEUE_HDR + f"| {fid} | {sev} | {title} | F.sol:1 | {poc_class} |\n",
        encoding="utf-8",
    )


def _write_verify(scratchpad: Path, fid: str, body: str):
    (scratchpad / f"verify_{fid}.md").write_text(body, encoding="utf-8")


def _gate(scratchpad: Path, fid: str, sev: str, title: str, bug_class: str,
          queue_class: str, body: str):
    _write_queue(scratchpad, fid, sev, title, queue_class)
    _write_verify(scratchpad, fid, body)
    rows = [{
        "finding id": fid, "severity": sev, "title": title,
        "bug class": bug_class, "poc class": queue_class,
    }]
    return V._validate_poc_contract_for_rows(scratchpad, rows, "thorough")


def test_gate_forces_poc_for_resource_exhaustion_structural_skip(tmp_path):
    # (a) self-declared structural resource-exhaustion finding is floored to
    # property and FAILS the STRUCTURAL skip -> mandatory PoC re-queue.
    issues = _gate(
        tmp_path, "INV-198", "High",
        title="Distribution iterates over an unbounded array of recipients",
        bug_class="DoS via unbounded loop (gas bomb)",
        queue_class="property",
        body=_STRUCT_DECLARED,
    )
    assert any("INV-198" in i for i in issues), (
        "a resource-exhaustion finding self-declared structural must be floored "
        "to property and fail the STRUCTURAL skip (mandatory-PoC), not escape"
    )


def test_gate_precision_const_codetrace_refutation_untouched(tmp_path):
    # (b) a constant/immutable/standard-mismatch finding (NO vocab) keeps the
    # structural softening and produces NO issue -> its CODE-TRACE refutation is
    # untouched. Precision guard (spurious ENUMGAP must remain refuted).
    issues = _gate(
        tmp_path, "ENUMGAP-7", "Low",
        title="immutable DECIMALS constant does not match the token standard",
        bug_class="constant mismatch",
        queue_class="structural",
        body=_CONST_DECLARED,
    )
    assert issues == [], (
        "a non-vocab structural finding must NOT be forced into a PoC; its "
        "structural CODE-TRACE refutation is untouched (precision)"
    )


# ─────────────── verdict safety net: UNRESOLVED-in-body (fix #4) ──────────────

def test_verdict_refuted_demoted_to_unresolved_for_vocab_structural_skip():
    # REFUTED + STRUCTURAL skip + vocab + NO [POC-FAIL] -> UNRESOLVED (kept in
    # body, demoted), never an excluded one-liner.
    assert P._verifier_status_from_text(_STRUCT_DECLARED) == "UNRESOLVED"


def test_verdict_refuted_preserved_for_non_vocab_precision():
    # Precision: a non-vocab structural refutation stays REFUTED (excluded).
    assert P._verifier_status_from_text(_CONST_DECLARED) == "REFUTED"


def test_verdict_refuted_preserved_when_poc_actually_failed():
    # A genuine [POC-FAIL] (verifier ran a PoC that disproved the harm) is a
    # mechanically-backed refutation and must NOT be demoted, even on vocab.
    body = (
        "# Verification: unbounded loop gas bomb\n"
        "**Verdict**: REFUTED\n"
        "### Execution Result\n"
        "- Result: FAIL\n"
        "- Evidence Tag: [POC-FAIL]\n"
        "- Note: the unbounded loop is bounded by a hard cap; gas stays flat\n"
    )
    assert P._verifier_status_from_text(body) == "REFUTED"


def test_verdict_confirmed_unchanged_for_vocab():
    body = (
        "# Verification: unbounded loop gas bomb\n"
        "**Verdict**: CONFIRMED\n"
        "### Execution Result\n- Result: PASS\n- Evidence Tag: [POC-PASS]\n"
    )
    assert P._verifier_status_from_text(body) == "CONFIRMED"
