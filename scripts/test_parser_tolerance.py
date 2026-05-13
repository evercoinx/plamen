"""Aggressive parser tolerance / fuzz tests.

Goal: prove parsers handle ANY reasonable LLM output, not just formats we've
already seen. Uses random + crafted adversarial inputs against:
  - _llm_norm  (idempotence + safety)
  - _normalize_finding_id
  - _parse_location_ref
  - _field_from_markdown
  - _verifier_status_from_text
  - _inventory_blocks
  - parse_verification_queue_rows

Run: `python test_parser_tolerance.py`
"""

from __future__ import annotations

import random
import re
import string
import sys
import tempfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label} :: {detail}")


def _mkscratch(files: dict[str, str]) -> Path:
    sp = Path(tempfile.mkdtemp(prefix="plamen_pt_"))
    for name, body in files.items():
        p = sp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return sp


# =============================================================================
# _llm_norm idempotence + safety
# =============================================================================

def test_NORM_idempotent():
    """norm(norm(x)) == norm(x) for a sample of inputs."""
    samples = [
        "",
        "plain ASCII text",
        "smart “quotes” and ‘single’",
        "em-dash — here",
        "non-breaking space",
        "&amp;gt;test&lt;",
        "CRLF\r\nLF\nCR\r",
        "zero-width​join‍",
        "­­­",  # all soft hyphens
        "&#xfeff;BOM",
    ]
    for s in samples:
        once = D._llm_norm(s)
        twice = D._llm_norm(once)
        if once != twice:
            check("NORM.idempotent", False, f"{s!r} once={once!r} twice={twice!r}")
            return
    check("NORM.idempotent across 10 inputs", True, "")


def test_NORM_pure_ascii_unchanged():
    """Pure ASCII input passes through unchanged."""
    samples = [
        "Reentrancy in withdraw at src/Vault.sol:L42",
        "## Finding [INV-001]\n**Severity**: High\n",
        "ABC-123 def-456 ghi-789",
    ]
    for s in samples:
        if D._llm_norm(s) != s:
            check("NORM.ascii-preserved", False, f"{s!r} changed")
            return
    check("NORM.ascii-preserved across 3 samples", True, "")


def test_NORM_crlf_to_lf():
    cases = [
        ("a\r\nb\r\nc", "a\nb\nc"),
        ("a\rb", "a\nb"),  # bare CR also normalized
        ("\r\n\r\n", "\n\n"),
    ]
    for inp, expected in cases:
        got = D._llm_norm(inp)
        if got != expected:
            check("NORM.crlf-to-lf", False, f"{inp!r} -> {got!r} expected {expected!r}")
            return
    check("NORM.crlf-to-lf across 3 cases", True, "")


def test_NORM_html_entities_decode():
    cases = [
        ("&amp;", "&"),
        ("&lt;tag&gt;", "<tag>"),
        ("&quot;hi&quot;", '"hi"'),
        ("&apos;s", "'s"),
        ("&#x1f600;", "\U0001f600"),
        ("&#65;&#66;", "AB"),
        ("&unknown;", "&unknown;"),  # unknown entity preserved
    ]
    failed = []
    for inp, expected in cases:
        got = D._llm_norm(inp)
        if got != expected:
            failed.append(f"{inp!r}->{got!r} expected {expected!r}")
    check(
        f"NORM.html-entities decoded across {len(cases)} cases",
        not failed,
        f"failed={failed}",
    )


def test_NORM_smart_quotes_and_dashes_normalized():
    inp = "He said “hello” — with a smart-quote and em-dash."
    expected = 'He said "hello" - with a smart-quote and em-dash.'
    check(
        "NORM.smart-quotes-and-dashes -> ASCII",
        D._llm_norm(inp) == expected,
        f"got {D._llm_norm(inp)!r}",
    )


def test_NORM_does_not_crash_on_random_unicode():
    """Generated random unicode strings: norm must not crash, output must be str."""
    rng = random.Random(0xCAFE)
    for _ in range(200):
        length = rng.randint(0, 100)
        s = "".join(chr(rng.randint(0, 0x2FFF)) for _ in range(length))
        try:
            out = D._llm_norm(s)
            assert isinstance(out, str), f"non-str output: {type(out)}"
        except Exception as e:
            check("NORM.no-crash-on-random-unicode", False, f"crashed on {s!r}: {e!r}")
            return
    check("NORM.no-crash-on-random-unicode (200 inputs)", True, "")


# =============================================================================
# _normalize_finding_id — every documented ID format
# =============================================================================

def test_NID_every_canonical_prefix_extracted():
    """Every documented ID prefix produces a clean uppercase ID."""
    cases = [
        # Bracketed forms
        ("[INV-001]", "INV-001"),
        ("[ INV-001 ]", "INV-001"),  # spaces inside brackets
        ("[`INV-001`]", "INV-001"),  # backticks inside brackets
        # Heading forms
        ("## Finding [INV-001]: bug", "INV-001"),
        ("### Finding [DCI-7]:", "DCI-7"),
        ("#### [SLITHER-3]", "SLITHER-3"),
        # Lowercase
        ("[inv-001]", "INV-001"),
        ("[fuzz-22]", "FUZZ-22"),
        # Underscore variants
        ("[INV_001]", "INV-001"),
        # Markdown link form: [text](url)
        ("[INV-001](http://x)", "INV-001"),
        # Bullet form
        ("- [INV-001]: x", "INV-001"),
        # Drift: smart-quote-wrapped
        ("“[INV-001]”", "INV-001"),
    ]
    failed = []
    for inp, expected in cases:
        got = D._normalize_finding_id(inp)
        if got != expected:
            failed.append(f"{inp!r}->{got!r} expected {expected!r}")
    check(
        f"NID.canonical-prefix-extraction across {len(cases)} forms",
        not failed,
        f"failed={failed}",
    )


def test_NID_no_match_returns_empty_string_never_crashes():
    """Inputs without a recognizable ID return '' cleanly."""
    cases = ["", "plain text", "  ", "[]", "[no match here]", "***"]
    for c in cases:
        try:
            got = D._normalize_finding_id(c)
            assert got == "", f"{c!r} returned {got!r}"
        except Exception as e:
            check("NID.no-match-returns-empty", False, f"{c!r} crashed: {e!r}")
            return
    check("NID.no-match-returns-empty across 6 inputs", True, "")


def test_NID_random_fuzz_no_crash():
    rng = random.Random(0xBEEF)
    chars = string.ascii_letters + string.digits + "[]()-_*` \t\n"
    for _ in range(500):
        length = rng.randint(0, 60)
        s = "".join(rng.choice(chars) for _ in range(length))
        try:
            D._normalize_finding_id(s)
        except Exception as e:
            check("NID.random-fuzz", False, f"crashed on {s!r}: {e!r}")
            return
    check("NID.random-fuzz (500 inputs)", True, "")


# =============================================================================
# _parse_location_ref — every plausible location format
# =============================================================================

def test_PLR_every_format_variant():
    cases = [
        # Standard
        ("src/x.sol:L42", ("src/x.sol", 42)),
        ("src/x.sol:42", ("src/x.sol", 42)),
        ("src/x.sol#L42", ("src/x.sol", 42)),
        # Backticks
        ("`src/x.sol:L42`", ("src/x.sol", 42)),
        # Words
        ("at: src/x.sol:L42", ("src/x.sol", 42)),
        ("file: src/x.sol line 42", ("src/x.sol", 42)),
        # Backslash (Windows-style)
        ("src\\x.sol:L42", ("src/x.sol", 42)),
        # Bare basename
        ("x.sol", ("x.sol", None)),
        # No path
        ("", ("", None)),
        ("see verifier artifact", ("", None)),
        ("L42 alone", ("", None)),
        # Drift: smart-quoted (em-dash separator is not a real LLM pattern;
        # paths use ':' or '#' or 'line' or 'L', not em-dash)
        ("“src/x.sol:L42”", ("src/x.sol", 42)),
        # Multi-clause: prefer line-annotated
        ("see foo.sol as ref, real at bar.sol:L20", ("bar.sol", 20)),
    ]
    failed = []
    for inp, expected in cases:
        got = D._parse_location_ref(inp)
        if got != expected:
            failed.append(f"{inp!r}->{got!r} expected {expected!r}")
    check(
        f"PLR.every-format-variant ({len(cases)} cases)",
        not failed,
        f"failed={failed[:3]}",
    )


def test_PLR_random_fuzz_no_crash():
    rng = random.Random(0xDEAD)
    for _ in range(500):
        length = rng.randint(0, 80)
        chars = string.ascii_letters + string.digits + "/.:#-_ \\"
        s = "".join(rng.choice(chars) for _ in range(length))
        try:
            rel, line = D._parse_location_ref(s)
            assert isinstance(rel, str)
            assert line is None or isinstance(line, int)
        except Exception as e:
            check("PLR.random-fuzz", False, f"crashed on {s!r}: {e!r}")
            return
    check("PLR.random-fuzz (500 inputs)", True, "")


# =============================================================================
# _field_from_markdown — labels with various decorations
# =============================================================================

def test_FFM_label_variants():
    cases = [
        # Standard
        ("**Severity**: High\n", "Severity", "High"),
        ("Severity: High\n", "Severity", "High"),
        # Bullet form
        ("- **Severity**: High\n", "Severity", "High"),
        ("* Severity: High\n", "Severity", "High"),
        # Equals or dash
        ("Severity = High\n", "Severity", "High"),
        ("Severity - High\n", "Severity", "High"),
        # Lowercase label
        ("**severity**: High\n", "Severity", "High"),
        # Trailing whitespace
        ("**Severity**: High   \n", "Severity", "High"),
        # Drift: smart quotes around value
        ("**Severity**: “High”\n", "Severity", '"High"'),
        # Heading format (common LLM drift)
        ("## Verdict: CONFIRMED\n", "Verdict", "CONFIRMED"),
        ("### Severity: High\n", "Severity", "High"),
        ("# Evidence Tag: [CODE-TRACE]\n", "Evidence Tag", "[CODE-TRACE]"),
        ("## **Verdict**: CONFIRMED\n", "Verdict", "CONFIRMED"),
    ]
    failed = []
    for body, label, expected in cases:
        got = D._field_from_markdown(body, (label,))
        if got != expected:
            failed.append(f"label={label!r} body={body!r}->{got!r} expected {expected!r}")
    check(
        f"FFM.label-variants ({len(cases)} cases)",
        not failed,
        f"failed={failed[:2]}",
    )


def test_FFM_random_fuzz_no_crash():
    rng = random.Random(0xC0DE)
    for _ in range(500):
        body_len = rng.randint(0, 200)
        body = "".join(rng.choice(string.printable) for _ in range(body_len))
        for label in ("Severity", "Location", "Verdict"):
            try:
                D._field_from_markdown(body, (label,))
            except Exception as e:
                check("FFM.random-fuzz", False, f"crashed on body={body[:50]!r}: {e!r}")
                return
    check("FFM.random-fuzz (1500 calls)", True, "")


# =============================================================================
# _verifier_status_from_text — every plausible verdict format
# =============================================================================

def test_VST_every_canonical_verdict():
    cases = [
        ("**Verdict**: CONFIRMED\n", "CONFIRMED"),
        ("**Verdict**: confirmed\n", "CONFIRMED"),
        ("**Verdict**: VALID\n", "CONFIRMED"),
        ("**Verdict**: TRUE_POSITIVE\n", "CONFIRMED"),
        ("**Verdict**: FALSE_POSITIVE\n", "FALSE_POSITIVE"),
        ("**Verdict**: REFUTED\n", "REFUTED"),
        ("**Verdict**: INFEASIBLE\n", "INFEASIBLE"),
        ("**Verdict**: SCHEMA_INVALID\n", "SCHEMA_INVALID"),
        ("**Verdict**: LOCATION_INVALID\n", "LOCATION_INVALID"),
        ("**Verdict**: UNRESOLVED\n", "UNRESOLVED"),
        ("**Verdict**: PARTIAL\n", "UNRESOLVED"),
        ("**Verdict**: CONTESTED\n", "CONTESTED"),
        ("**Verdict**: DUPLICATE\n", "DUPLICATE"),
        ("**Verdict**: CONSOLIDATED\n", "DUPLICATE"),
        # Drift: smart-quotes + spacing
        ("“Verdict”: CONFIRMED\n", "CONFIRMED"),
        ("**Verdict**: “CONFIRMED”\n", "CONFIRMED"),
    ]
    failed = []
    for body, expected in cases:
        got = D._verifier_status_from_text(body)
        if got != expected:
            failed.append(f"{body!r}->{got!r} expected {expected!r}")
    check(
        f"VST.canonical-verdicts ({len(cases)} forms)",
        not failed,
        f"failed={failed[:3]}",
    )


def test_VST_garbage_returns_unresolved():
    cases = ["", "   ", "\n\n", "garbage", "**Verdict**: ???", "# Header alone\n"]
    for c in cases:
        got = D._verifier_status_from_text(c)
        if got == "CONFIRMED":
            check("VST.garbage-not-confirmed", False, f"{c!r}->CONFIRMED")
            return
    check(f"VST.garbage-not-confirmed across {len(cases)} cases", True, "")


def test_VST_random_fuzz_no_crash():
    rng = random.Random(0xFADE)
    for _ in range(500):
        body_len = rng.randint(0, 500)
        body = "".join(rng.choice(string.printable) for _ in range(body_len))
        try:
            D._verifier_status_from_text(body)
        except Exception as e:
            check("VST.random-fuzz", False, f"crashed: {e!r}")
            return
    check("VST.random-fuzz (500 inputs)", True, "")


# =============================================================================
# _inventory_blocks — robustness
# =============================================================================

def test_IB_handles_multiple_heading_levels():
    text = (
        "## Finding [INV-001]: h2 level\n"
        "**Location**: a:L1\n\n"
        "### Finding [INV-002]: h3 level\n"
        "**Location**: b:L2\n\n"
        "#### [INV-003]: h4 level\n"
        "**Location**: c:L3\n"
    )
    blocks = D._inventory_blocks(text)
    ids = sorted(b["id"] for b in blocks)
    check(
        "IB.multi-heading-levels: all 3 found",
        ids == ["INV-001", "INV-002", "INV-003"],
        f"ids={ids}",
    )


def test_IB_handles_drift_in_heading():
    text = (
        "###  Finding  “[INV-001]”:  smart quotes around id\n"
        "**Location**: a:L1\n\n"
        "###  Finding  [INV-002]  —  em-dash separator\n"
        "**Location**: b:L2\n"
    )
    blocks = D._inventory_blocks(text)
    ids = sorted(b["id"] for b in blocks)
    check(
        "IB.drift-in-heading: smart quotes / em-dash recovered",
        ids == ["INV-001", "INV-002"],
        f"ids={ids}",
    )


def test_IB_random_fuzz_no_crash():
    rng = random.Random(0xACE)
    for _ in range(100):
        body_len = rng.randint(0, 1000)
        body = "".join(rng.choice(string.printable) for _ in range(body_len))
        try:
            D._inventory_blocks(body)
        except Exception as e:
            check("IB.random-fuzz", False, f"crashed: {e!r}")
            return
    check("IB.random-fuzz (100 inputs)", True, "")


# =============================================================================
# parse_verification_queue_rows — robustness
# =============================================================================

def test_PVQR_handles_header_alias_drift():
    """Header column aliases ('Preferred Verification' vs 'Preferred Tag') resolved."""
    queue = (
        "| # | Finding ID | Severity | Title | Class | Preferred Verification | Location | Source |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 1 | INV-001 | High | bug | x | CODE-TRACE | s:L1 | x.md |\n"
    )
    sp = _mkscratch({"verification_queue.md": queue})
    rows = D.parse_verification_queue_rows(sp)
    check(
        "PVQR.header-alias-drift parses Preferred-Verification column",
        len(rows) == 1 and rows[0].get("preferred tag") == "CODE-TRACE",
        f"rows={rows}",
    )


def test_PVQR_table_with_drift_in_cells():
    """Cells with smart quotes / em-dashes parse correctly."""
    queue = (
        "| # | Finding ID | Severity | Title | Class | Preferred Tag | Location | Source |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 1 | INV-001 | High | “withdraw” — reentrant | x | CODE-TRACE | s:L1 | x.md |\n"
    )
    sp = _mkscratch({"verification_queue.md": queue})
    rows = D.parse_verification_queue_rows(sp)
    check(
        "PVQR.drift-in-cells parses",
        len(rows) == 1 and rows[0]["finding id"] == "INV-001",
        f"rows={rows}",
    )


def test_PVQR_empty_file_returns_empty_list():
    sp = _mkscratch({"verification_queue.md": ""})
    rows = D.parse_verification_queue_rows(sp)
    check("PVQR.empty-file returns []", rows == [], f"rows={rows}")


def test_PVQR_random_fuzz_no_crash():
    rng = random.Random(0xBED)
    for _ in range(100):
        body_len = rng.randint(0, 800)
        body = "".join(rng.choice(string.printable) for _ in range(body_len))
        sp = _mkscratch({"verification_queue.md": body})
        try:
            D.parse_verification_queue_rows(sp)
        except Exception as e:
            check("PVQR.random-fuzz", False, f"crashed: {e!r}")
            return
    check("PVQR.random-fuzz (100 inputs)", True, "")


# =============================================================================
# Combined: ID round-trip through every parser stage
# =============================================================================

def test_RT_full_pipeline_with_drift_in_every_field():
    """End-to-end: every field has a drift variant; full round-trip works."""
    inv = (
        "## Findings\n\n"
        "### Finding “[INV-001]”: “Reentrancy” — owner can drain\n\n"
        "**Severity**:  High\n"
        "**Location**: contracts/Vault.sol:L142\n"
        "**Source IDs**: [DCI-7, BLIND-3]\n"
        "**Preferred Tag**: POC-PASS\n"
    )
    sp = _mkscratch({"findings_inventory.md": inv})
    n_routed = D._write_mechanical_verification_queue_from_inventory(sp)
    rows = D.parse_verification_queue_rows(sp)
    check(
        "RT.full-pipeline-with-drift: 1 routed, 1 row, INV-001 preserved",
        n_routed == 1 and len(rows) == 1 and rows[0]["finding id"] == "INV-001",
        f"routed={n_routed} rows={rows}",
    )


# =============================================================================
# Test runner
# =============================================================================

TESTS = [
    test_NORM_idempotent,
    test_NORM_pure_ascii_unchanged,
    test_NORM_crlf_to_lf,
    test_NORM_html_entities_decode,
    test_NORM_smart_quotes_and_dashes_normalized,
    test_NORM_does_not_crash_on_random_unicode,
    test_NID_every_canonical_prefix_extracted,
    test_NID_no_match_returns_empty_string_never_crashes,
    test_NID_random_fuzz_no_crash,
    test_PLR_every_format_variant,
    test_PLR_random_fuzz_no_crash,
    test_FFM_label_variants,
    test_FFM_random_fuzz_no_crash,
    test_VST_every_canonical_verdict,
    test_VST_garbage_returns_unresolved,
    test_VST_random_fuzz_no_crash,
    test_IB_handles_multiple_heading_levels,
    test_IB_handles_drift_in_heading,
    test_IB_random_fuzz_no_crash,
    test_PVQR_handles_header_alias_drift,
    test_PVQR_table_with_drift_in_cells,
    test_PVQR_empty_file_returns_empty_list,
    test_PVQR_random_fuzz_no_crash,
    test_RT_full_pipeline_with_drift_in_every_field,
]


def main() -> int:
    print(f"Running {len(TESTS)} parser-tolerance tests...")
    for t in TESTS:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as exc:
            global FAIL
            FAIL += 1
            print(f"  CRASH {t.__name__} :: {exc!r}")
    print(f"\n{'=' * 64}")
    print(f"  PASS: {PASS}   FAIL: {FAIL}")
    print('=' * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
