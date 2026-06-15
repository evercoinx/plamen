"""Fixture corpus for the shared tolerant extractor `_field_anywhere` (Phase 0).

Regex-fragility remediation plan §4 L2. This corpus pins EVERY shape in the
plan's `must_cover_shapes` union, PLUS:

  * legacy-superset value-identity fixtures (everything the old single-shape
    regexes accepted still returns a value-identical result),
  * negative-control fixtures (old intended rejects still reject — catches
    over-broadening),
  * the clause-scoped negation guard (the one intentional narrowing — recall
    safe because it suppresses FEWER valid skips than the old 80-char rule),
  * the zero-harvest tripwire (label present, nothing harvested -> WARNING).

NOTHING is migrated to call `_field_anywhere` yet; this is inert substrate.

`_field_anywhere(text, labels, *, value_pattern, table_ok, negation_guard,
first_match) -> (value, shape_tag)`. Tests assert on the VALUE (load-bearing);
the shape_tag is best-effort and only spot-checked.
"""

import logging

import plamen_parsers as P


def val(text, labels, **kw):
    return P._field_anywhere(text, labels, **kw)[0]


# ════════════════════════════════════════════════════════════════════════
#  KV SEPARATORS:  :  /  =  /  |  /  bullet  /  whitespace
# ════════════════════════════════════════════════════════════════════════

def test_sep_colon():
    assert val("Severity: High", "Severity") == "High"


def test_sep_equals():
    assert val("Severity = High", "Severity") == "High"


def test_sep_dash():
    assert val("Severity - High", "Severity") == "High"


def test_sep_pipe_table_cell():
    # The #1 live miss: pipe as kv separator in a table cell.
    assert val("| Severity | High |", "Severity") == "High"


def test_sep_bullet():
    assert val("- Severity: High", "Severity") == "High"
    assert val("* Severity: High", "Severity") == "High"


# ════════════════════════════════════════════════════════════════════════
#  WRAPPERS on labels + values + ids
# ════════════════════════════════════════════════════════════════════════

def test_wrap_bold_label():
    assert val("**Severity**: High", "Severity") == "High"


def test_wrap_bold_label_colon_inside():
    assert val("**Severity:** High", "Severity") == "High"


def test_wrap_backtick_label():
    assert val("`Severity`: High", "Severity") == "High"


def test_wrap_underscore_label():
    assert val("_Severity_: High", "Severity") == "High"


def test_wrap_bracketed_label():
    assert val("[Severity]: High", "Severity") == "High"


def test_wrap_value_backtick_stripped():
    assert val("Location: `src/Vault.sol:L42`", "Location") == "src/Vault.sol:L42"


def test_wrap_leading_bullet_and_bold():
    assert val("- **Severity**: High", "Severity") == "High"


def test_wrap_indented_label():
    assert val("    Severity: High", "Severity") == "High"


def test_wrap_value_bold():
    assert val("Severity: **High**", "Severity") == "High"


# ════════════════════════════════════════════════════════════════════════
#  CASE INSENSITIVITY
# ════════════════════════════════════════════════════════════════════════

def test_case_insensitive_label():
    assert val("severity: High", "Severity") == "High"
    assert val("SEVERITY: High", "Severity") == "High"


# ════════════════════════════════════════════════════════════════════════
#  TABLE SHAPES:  vertical kv, header/row, separator rows, variable columns
# ════════════════════════════════════════════════════════════════════════

def test_table_vertical_kv():
    assert val("| Severity | High |", "Severity") == "High"


def test_table_header_then_row():
    txt = (
        "| Impact | Likelihood |\n"
        "|--------|-----------|\n"
        "| High   | Medium    |\n"
    )
    assert val(txt, "Impact") == "High"
    assert val(txt, "Likelihood") == "Medium"


def test_table_separator_row_skipped():
    txt = (
        "| Field | Value |\n"
        "| --- | --- |\n"
        "| Severity | High |\n"
    )
    # `--- | ---` is a separator, must not be mistaken for data.
    assert val(txt, "Severity") == "High"


def test_table_separator_with_literal_dashes_in_data_not_confused():
    # A real data cell containing `---` must still be readable as a value.
    txt = "| Note | --- some note --- |\n"
    assert val(txt, "Note") == "--- some note ---"


def test_table_variable_columns():
    txt = "| Severity | High | extra | columns |\n"
    assert val(txt, "Severity") == "High"


def test_table_three_col_kv_picks_first_nonempty_value():
    txt = "| Severity |  | High |\n"
    assert val(txt, "Severity") == "High"


def test_table_disabled_when_table_ok_false():
    # With table_ok=False the table form must NOT be harvested.
    assert val("| Severity | High |", "Severity", table_ok=False) == ""


# ════════════════════════════════════════════════════════════════════════
#  HEADINGS  #{1,6}  + heading-as-value (next line)
# ════════════════════════════════════════════════════════════════════════

def test_heading_inline_h1_through_h6():
    for h in range(1, 7):
        txt = ("#" * h) + " Severity: High"
        assert val(txt, "Severity") == "High", h


def test_heading_then_next_line_value():
    txt = "### Severity\nHigh\n"
    assert val(txt, "Severity") == "High"


# ════════════════════════════════════════════════════════════════════════
#  FIELD-LABEL ALIASES  (Preferred Tag ≈ Preferred Verification ≈ Evidence Tag)
# ════════════════════════════════════════════════════════════════════════

def test_alias_preferred_tag_family():
    labels = ("Preferred Tag", "Preferred Verification", "Evidence Tag")
    assert val("Preferred Tag: [POC-PASS]", labels) == "[POC-PASS]"
    assert val("Preferred Verification: [CODE-TRACE]", labels) == "[CODE-TRACE]"
    assert val("Evidence Tag: [POC-FAIL]", labels) == "[POC-FAIL]"


def test_alias_finding_id_family():
    labels = ("Finding ID", "ID")
    assert val("Finding ID: H-22", labels) == "H-22"
    assert val("ID: H-22", labels) == "H-22"


def test_alias_longest_first_wins():
    # "Location" must win over the 3-char "loc" alias on the same cell.
    labels = ("Location", "Loc")
    assert val("Location: src/A.sol:L1", labels) == "src/A.sol:L1"


# ════════════════════════════════════════════════════════════════════════
#  SHORT-LABEL WORD-BOUNDARY (id != invalid, covered != uncovered)
# ════════════════════════════════════════════════════════════════════════

def test_short_label_id_does_not_match_invalid_substring():
    # The label "id" must not harvest from a line whose key is "invalid".
    # There is no real `id:` field here -> must return "".
    assert val("invalid: true", ("id",)) == ""


def test_short_label_id_matches_real_id_field():
    assert val("id: H-22", ("id",)) == "H-22"


# ════════════════════════════════════════════════════════════════════════
#  VALUE_PATTERN CONSTRAINT (e.g. severity enum)
# ════════════════════════════════════════════════════════════════════════

_SEV = r"\b(?:critical|high|medium|low|informational)\b"


def test_value_pattern_accepts_matching():
    assert val("Severity: High", "Severity", value_pattern=_SEV) == "High"


def test_value_pattern_rejects_then_continues_to_next_candidate():
    # First "Severity:" line has a non-enum value; second has a valid one.
    txt = "Severity: TBD\nSeverity: Medium\n"
    assert val(txt, "Severity", value_pattern=_SEV) == "Medium"


def test_value_pattern_table_matrix_axes():
    txt = (
        "| Impact | Likelihood |\n"
        "|---|---|\n"
        "| High | Medium |\n"
    )
    assert val(txt, "Impact", value_pattern=_SEV) == "High"
    assert val(txt, "Likelihood", value_pattern=_SEV) == "Medium"


# ════════════════════════════════════════════════════════════════════════
#  CLAUSE-SCOPED NEGATION GUARD (the one intentional narrowing)
# ════════════════════════════════════════════════════════════════════════

def test_negation_guard_off_by_default():
    # Without the guard, a value is returned regardless of nearby negation.
    assert val("Mock: not provided", "Mock") == "not provided"


def test_negation_guard_suppresses_same_clause():
    # "Mock: not used" — negation in the SAME clause suppresses the match.
    out = P._field_anywhere("Mock: not used here", "Mock", negation_guard=True)
    assert out[0] == ""


def test_negation_guard_does_not_fire_across_sentence_boundary():
    # The live `_valid_poc_skip` false-fire: a negation in a DIFFERENT
    # sentence than the trigger must NOT suppress. Old 80-char proximity
    # wrongly fired here; clause-scoped does not.
    txt = (
        "EXTERNAL_DEPENDENCY: yes. The harness does not exist on chain.\n"
    )
    # Trigger label "EXTERNAL_DEPENDENCY" is in clause 1; "not" is in clause 2.
    # The guard scopes to the LABEL's clause, so it must NOT suppress: a value
    # is returned (recall-safe — the old 80-char proximity rule wrongly
    # suppressed this). The guard's job is suppression, not value-trimming, so
    # the returned value is the full captured field starting with "yes".
    got = P._field_anywhere(
        txt, "EXTERNAL_DEPENDENCY", negation_guard=True
    )[0]
    assert got != "", "clause-scoped guard must not suppress cross-sentence"
    assert got.startswith("yes")


def test_negation_word_boundary_invalid_not_a_negation_for_id():
    # "invalid" contains "no"?? no — but ensure "cannot"/"isn't" boundary works
    # and that a substring like "annotation" does not count as "not".
    txt = "Status: annotation present"
    # "annotation" must NOT be read as the negation "not".
    assert P._field_anywhere(txt, "Status", negation_guard=True)[0] == (
        "annotation present"
    )


# ════════════════════════════════════════════════════════════════════════
#  TEXT NORMALIZATION (CRLF, smart quotes, HTML entities, zero-width)
# ════════════════════════════════════════════════════════════════════════

def test_norm_crlf():
    assert val("Severity: High\r\nNext line", "Severity") == "High"


def test_norm_smart_quote_in_value():
    # Smart quotes normalized to ASCII before matching.
    assert val("Note: “fenced”", "Note") == '"fenced"'


def test_norm_nbsp_separator():
    assert val("Severity: High", "Severity") == "High"


# ════════════════════════════════════════════════════════════════════════
#  ZERO-HARVEST TRIPWIRE
# ════════════════════════════════════════════════════════════════════════

def test_zero_harvest_tripwire_logs(caplog):
    # Label clearly present in the text, but in a shape we cannot extract a
    # value from (here: bare mention with no kv/table/heading value).
    txt = "This paragraph discusses Severity at length but never sets it."
    with caplog.at_level(logging.WARNING):
        out = P._field_anywhere(txt, "Severity")
    assert out[0] == ""
    assert any(
        "_field_anywhere" in r.getMessage() and "Severity" in r.getMessage()
        for r in caplog.records
    )


def test_no_tripwire_when_label_absent(caplog):
    # Label genuinely absent -> no warning (absence is normal).
    with caplog.at_level(logging.WARNING):
        out = P._field_anywhere("totally unrelated prose", "Severity")
    assert out[0] == ""
    assert not [
        r for r in caplog.records if "_field_anywhere" in r.getMessage()
    ]


# ════════════════════════════════════════════════════════════════════════
#  LEGACY-SUPERSET VALUE-IDENTITY:  every old _field_from_markdown accept
#  returns an IDENTICAL value via _field_anywhere (nothing that passed fails).
# ════════════════════════════════════════════════════════════════════════

_LEGACY_ACCEPTS = [
    ("Severity: High", "Severity"),
    ("severity: High", "Severity"),
    ("- Severity: High", "Severity"),
    ("* Severity: High", "Severity"),
    ("**Severity**: High", "Severity"),
    # NOTE: `**Severity:** High` and `` `Severity`: High `` are intentionally
    # NOT in this strict-identity list — legacy `_field_from_markdown`
    # mishandles the first (returns the junk value `** High`) and rejects the
    # second outright (backtick-wrapped labels are an ADDED shape).
    # `_field_anywhere` IMPROVES on both (recall-safe supersets, not
    # regressions); their clean behavior is pinned by
    # `test_wrap_bold_label_colon_inside` and `test_wrap_backtick_label`.
    ("# Severity: High", "Severity"),
    ("###### Severity: High", "Severity"),
    ("Severity = High", "Severity"),
    ("Severity - High", "Severity"),
    ("Severity (note): High", "Severity"),
    ("Location: src/Vault.sol:L42", "Location"),
    ("**Location**: src/Vault.sol:L42", "Location"),
]


def test_legacy_superset_value_identity():
    for text, label in _LEGACY_ACCEPTS:
        legacy = P._field_from_markdown(text, (label,))
        assert legacy, f"fixture should be a legacy accept: {text!r}"
        new = val(text, label)
        assert new == legacy, (
            f"value-identity broken for {text!r}: legacy={legacy!r} "
            f"new={new!r}"
        )


# ════════════════════════════════════════════════════════════════════════
#  NEGATIVE CONTROLS:  old intended rejects still reject (no over-broadening)
# ════════════════════════════════════════════════════════════════════════

def test_negative_control_different_label_not_matched():
    # Asking for "Impact" must not harvest a "Severity" line.
    assert val("Severity: High", "Impact") == ""


def test_negative_control_label_only_in_value_not_matched():
    # "Severity" appears only inside another field's value, not as a key.
    assert val("Title: assess the Severity later", "Severity") == ""


def test_negative_control_short_label_substring_rejected():
    # 3-char "loc" must not match "blocked" or "location-ish" prose key.
    assert val("blocked: true", ("loc",)) == ""


def test_negative_control_no_separator_no_match():
    # A heading-style line "Severity High" with no separator and no value-line
    # following should not fabricate a value from the same line.
    assert val("Severity High overview\n", "Severity") == ""


# ════════════════════════════════════════════════════════════════════════
#  ID FORMATS via value_pattern + canonical category validation
#  (catalog ALL: SC H-22, L1 H-C01 / L1-H-12, chain CH-1 / CC-1, F-01,
#   verbose DEPTH-CONSENSUS-INVARIANT-1, leading-zero, filename token)
# ════════════════════════════════════════════════════════════════════════

_ID_CASES = [
    ("Finding ID: H-22", "H-22"),
    ("Finding ID: H-C01", "H-C01"),
    ("Finding ID: L1-H-12", "L1-H-12"),
    ("Finding ID: CH-1", "CH-1"),
    ("Finding ID: CC-1", "CC-1"),
    ("Finding ID: F-01", "F-01"),
    ("Finding ID: DEPTH-CONSENSUS-INVARIANT-1", "DEPTH-CONSENSUS-INVARIANT-1"),
    ("Finding ID: H-1", "H-1"),
]


def test_id_formats_extracted_raw_then_category_validated():
    # _field_anywhere returns the raw value (permissive capture); the canonical
    # ID source (_normalize_finding_id) then validates the category. This is
    # the plan's "permissive capture + category validate" contract — the
    # extractor never hand-rolls an ID-prefix enumeration of its own.
    for text, expected in _ID_CASES:
        raw = val(text, ("Finding ID", "ID"))
        assert raw == expected, (text, raw)
    # Category validation is delegated to the canonical single-source-of-truth.
    # Every ID the canonical source recognizes round-trips through the
    # extractor unchanged. (The verbose `DEPTH-CONSENSUS-INVARIANT-1` multi-
    # segment form is a known canonical-source catalog gap, NOT a
    # _field_anywhere concern — the extractor still captures it raw, above.)
    for text, expected in _ID_CASES:
        raw = val(text, ("Finding ID", "ID"))
        canon = P._normalize_finding_id(raw)
        if canon:
            assert canon == expected.upper(), (raw, canon)


def test_id_table_cell_last_column_wins():
    # Report-index style row: the internal hypothesis ID is the LAST column.
    row = "| C-01 | Title | Critical | src/A.sol | VERIFIED | - | H-22 |"
    # Asking for the trailing internal-ID column by header is a real consumer
    # pattern; here we assert the canonical extractor still finds H-22 in the
    # row (last-column-wins is enforced by _INTERNAL_ID_RE consumers, this
    # fixture documents the raw cell is reachable).
    ids = P._INTERNAL_ID_RE.findall(row)
    assert "H-22" in [i.upper() for i in ids]


# ════════════════════════════════════════════════════════════════════════
#  NUMERIC SHAPES  (1,234 / ~500 / 500 lines / 500 LOC)  — documented as
#  reachable raw values; numeric coercion is the caller's job (detection-only)
# ════════════════════════════════════════════════════════════════════════

def test_numeric_shapes_returned_raw():
    assert val("Lines: 1,234", "Lines") == "1,234"
    assert val("Lines: ~500", "Lines") == "~500"
    assert val("Lines: 500 lines", "Lines") == "500 lines"
    assert val("Lines: 500 LOC", "Lines") == "500 LOC"
    assert val("| Lines | 1,234 |", "Lines") == "1,234"


# ════════════════════════════════════════════════════════════════════════
#  HEADING SYNONYMS handled by caller alias-lists (documented contract)
# ════════════════════════════════════════════════════════════════════════

def test_heading_synonym_via_alias_list():
    labels = ("Operational Implications", "Implications")
    txt = "## Implications\nThe accounting model assumes X.\n"
    assert val(txt, labels) == "The accounting model assumes X."


# ════════════════════════════════════════════════════════════════════════
#  EMPTY / NONE INPUT
# ════════════════════════════════════════════════════════════════════════

def test_empty_inputs():
    assert P._field_anywhere("", "Severity") == ("", "none")
    assert P._field_anywhere(None, "Severity") == ("", "none")
    assert P._field_anywhere("text", "") == ("", "none")
    assert P._field_anywhere("text", ()) == ("", "none")
