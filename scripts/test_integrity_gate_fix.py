"""Tests for the v2.8.17 integrity-gate fix in mechanical_verify.

Two false-downgrade grounds are corrected:

  (a) The harm-assertion detector now recognizes revert/error-expectation
      assertions (`.is_err()`, `#[should_panic]`, `.expect_err(..)`,
      `.unwrap_err()`, `matches!(.., Err(..))`, `assert_eq!(.., Err(..))`)
      in addition to positive assertion forms. Strictly additive — it can only
      REDUCE false "no assertion" downgrades.

  (b) A NO_TEST_FILE harness/file-location failure that co-occurs with a real
      harm assertion + a proof-grade claim is classified as a DISTINCT,
      non-severity-capping disposition (POC_UNVERIFIED_HARNESS) that PRESERVES
      the upstream severity and routes to NEEDS-BUILD, instead of hard-capping
      to [CODE-TRACE]. Genuine no-PoC prose still maps to [CODE-TRACE].

No protocol names appear anywhere (no-overfit): generic PoC snippets only.

Run: python -m pytest -q scripts/test_integrity_gate_fix.py
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from mechanical_verify import (  # noqa: E402
    _classify_integrity,
    _contains_harm_assertion,
    _recommended_tag,
)


# ---------------------------------------------------------------------------
# Fix (a): broadened harm-assertion detector
# ---------------------------------------------------------------------------


def test_a_recognizes_try_call_is_err():
    """`try_foo(..).is_err()` is a real harm assertion (asserts the call
    reverts) — must NOT be read as assertion-less. This is the M-12 class."""
    poc = "let res = try_fill_orders(&ctx, orders);\nassert!(res.is_err());"
    assert _contains_harm_assertion(poc) is True


def test_a_recognizes_bare_is_err_on_result():
    poc = "assert!(do_thing().is_err());"
    assert _contains_harm_assertion(poc) is True


def test_a_recognizes_is_ok_on_result():
    poc = "let ok = call_it(&mut state).is_ok();\nassert!(ok);"
    assert _contains_harm_assertion(poc) is True


def test_a_recognizes_should_panic_attribute():
    poc = "#[should_panic]\nfn test_it() { do_bad(); }"
    assert _contains_harm_assertion(poc) is True


def test_a_recognizes_should_panic_expected():
    poc = '#[should_panic(expected = "overflow")]\nfn t() { boom(); }'
    assert _contains_harm_assertion(poc) is True


def test_a_recognizes_expect_err():
    poc = 'let e = run_op(&ctx).expect_err("should have reverted");'
    assert _contains_harm_assertion(poc) is True


def test_a_recognizes_unwrap_err():
    poc = "let e = run_op(&ctx).unwrap_err();"
    assert _contains_harm_assertion(poc) is True


def test_a_recognizes_matches_err():
    poc = "assert!(matches!(run_op(&ctx), Err(Error::Unauthorized)));"
    assert _contains_harm_assertion(poc) is True


def test_a_recognizes_assert_eq_err():
    poc = "assert_eq!(run_op(&ctx), Err(Error::BadState));"
    assert _contains_harm_assertion(poc) is True


def test_a_keeps_existing_positive_assert_forms():
    """Strictly additive: positive assertion forms still count."""
    assert _contains_harm_assertion("assert!(balance == 0);") is True
    assert _contains_harm_assertion("assert_eq!(total, 100);") is True
    assert _contains_harm_assertion("assertEq(fee, 0);") is True  # Foundry
    assert _contains_harm_assertion("require.NoError(t, err)") is True  # Go


def test_a_no_assertion_prose_is_not_a_harm_assertion():
    """Prose with no assertion at all is correctly NOT recognized."""
    assert _contains_harm_assertion("The function reverts under some path.") is False
    assert _contains_harm_assertion("") is False


# ---------------------------------------------------------------------------
# Fix (b): NO_TEST_FILE harness failure vs genuine no-evidence
# ---------------------------------------------------------------------------


_VERIFY_WITH_ASSERTED_POC = (
    "**Severity**: High\n"
    "**Evidence Tag**: [POC-PASS]\n"
    "**Verdict**: CONFIRMED\n"
    "### PoC Attempt\n"
    "- Test File: tests/test_fill_reverts.rs\n"
    "- Result: PASS\n"
    "```rust\n"
    "#[test]\n"
    "fn test_fill_orders_reverts() {\n"
    "    let res = try_fill_orders(&ctx, orders);\n"
    "    assert!(res.is_err());\n"
    "}\n"
    "```\n"
)


def test_b_no_test_file_with_asserted_poc_is_not_capped():
    """NO_TEST_FILE (harness/file-location failure) + a claimed passing named
    test with a real harm assertion → DISTINCT non-capping disposition that
    preserves severity, NOT [CODE-TRACE]/[INTEGRITY-DOWNGRADE]."""
    state, tag = _classify_integrity(
        "[POC-PASS]", "NO_TEST_FILE", _VERIFY_WITH_ASSERTED_POC
    )
    assert state == "POC_UNVERIFIED_HARNESS"
    # Severity preserved: original proof tag retained, distinct disposition flag.
    assert "[POC-PASS]" in tag
    assert "[POC-UNVERIFIED-HARNESS]" in tag
    assert "[NEEDS-BUILD]" in tag
    # NOT severity-capping / NOT a verdict-flip trigger.
    assert "[CODE-TRACE]" not in tag
    assert "[INTEGRITY-DOWNGRADE]" not in tag


def test_b_no_test_file_no_poc_claim_still_code_trace():
    """Genuine no-PoC: honest non-proof prose + NO_TEST_FILE → preserved as
    [CODE-TRACE] (unchanged existing behavior)."""
    state, tag = _classify_integrity("[CODE-TRACE]", "NO_TEST_FILE", "")
    assert state == "CONSISTENT"
    assert tag == "[CODE-TRACE]"


def test_b_recommended_tag_no_test_file_unchanged():
    """The status->tag map for the genuine no-PoC path is unchanged."""
    assert _recommended_tag("NO_TEST_FILE") == "[CODE-TRACE]"


def test_b_no_test_file_proof_claim_but_no_assertion_still_downgraded():
    """Assertion-less prose that claims [POC-PASS] but has no runnable/asserted
    PoC + NO_TEST_FILE → still INFLATED_PROSE → [CODE-TRACE] (existing behavior
    preserved for genuinely assertion-less prose)."""
    prose = (
        "**Evidence Tag**: [POC-PASS]\n"
        "**Verdict**: CONFIRMED\n"
        "The exploit clearly works; no test was written.\n"
    )
    state, tag = _classify_integrity("[POC-PASS]", "NO_TEST_FILE", prose)
    assert state == "INFLATED_PROSE"
    assert "[CODE-TRACE]" in tag
    assert "[INTEGRITY-DOWNGRADE]" in tag


def test_b_backcompat_no_verify_text_downgrades():
    """Back-compat: called without verify_text (default ""), a proof claim +
    NO_TEST_FILE still downgrades (no harm assertion visible)."""
    state, tag = _classify_integrity("[POC-PASS]", "NO_TEST_FILE")
    assert state == "INFLATED_PROSE"
    assert "[INTEGRITY-DOWNGRADE]" in tag


def test_b_harness_carveout_scoped_to_no_test_file():
    """The carve-out is scoped to NO_TEST_FILE. A genuine mechanical FAIL with
    an asserted PoC is still INFLATED_PROSE (the test ran and disproved it)."""
    state, tag = _classify_integrity(
        "[POC-PASS]", "FAIL", _VERIFY_WITH_ASSERTED_POC
    )
    assert state == "INFLATED_PROSE"
    assert "[INTEGRITY-DOWNGRADE]" in tag


def test_b_compile_fail_with_assertion_not_carved_out():
    """COMPILE_FAIL is not a file-location failure; asserted PoC that won't
    compile stays INFLATED_PROSE."""
    state, _ = _classify_integrity(
        "[POC-PASS]", "COMPILE_FAIL", _VERIFY_WITH_ASSERTED_POC
    )
    assert state == "INFLATED_PROSE"
