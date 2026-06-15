"""Fixture corpus for the PLAMEN_SIGNALS machine-readable channel (Phase 0).

Regex-fragility remediation plan §4 L1. The PLAMEN_SIGNALS HTML-comment
channel carries single-line (or multi-line) JSON that is deterministic by
construction — no markdown shape variance is possible. These tests pin:

  * well-formed single / multiple / merged blocks
  * tolerance of missing, malformed, and non-object blocks (-> {} + log)
  * the zero-harvest tripwire (block present but nothing harvested -> WARNING)
  * text normalization is applied before matching (_llm_norm CRLF/entities)

NOTHING is migrated to call this yet; this is the inert substrate.
"""

import logging

import plamen_parsers as P


# ── well-formed ──────────────────────────────────────────────────────────

def test_single_block_basic():
    txt = '<!-- PLAMEN_SIGNALS: {"sync_gaps": 5} -->'
    assert P.parse_plamen_signals(txt) == {"sync_gaps": 5}


def test_block_with_multiple_keys():
    txt = (
        '<!-- PLAMEN_SIGNALS: {"sync_gaps":5,"accumulation_exposures":0,'
        '"conditional_writes":2,"cluster_gaps":1} -->'
    )
    assert P.parse_plamen_signals(txt) == {
        "sync_gaps": 5,
        "accumulation_exposures": 0,
        "conditional_writes": 2,
        "cluster_gaps": 1,
    }


def test_block_embedded_in_prose_and_status_marker():
    txt = (
        "# Inventory\n\nSome analysis text.\n\n"
        '<!-- PLAMEN_SIGNALS: {"niche_required": ["EVENT_COMPLETENESS"]} -->\n'
        "<!-- PLAMEN_STATUS: COMPLETE -->\n"
    )
    assert P.parse_plamen_signals(txt) == {
        "niche_required": ["EVENT_COMPLETENESS"]
    }


def test_whitespace_tolerance_in_delimiter():
    # No spaces, extra spaces, tabs around the colon and braces.
    a = '<!--PLAMEN_SIGNALS:{"x":1}-->'
    b = '<!--   PLAMEN_SIGNALS  :   {"x": 1}   -->'
    assert P.parse_plamen_signals(a) == {"x": 1}
    assert P.parse_plamen_signals(b) == {"x": 1}


def test_multiline_json_in_block():
    txt = (
        "<!-- PLAMEN_SIGNALS: {\n"
        '  "sync_gaps": 3,\n'
        '  "cluster_gaps": 0\n'
        "} -->"
    )
    assert P.parse_plamen_signals(txt) == {"sync_gaps": 3, "cluster_gaps": 0}


def test_string_and_bool_and_nested_values():
    txt = (
        '<!-- PLAMEN_SIGNALS: {"required":true,"actor":"keeper",'
        '"counts":{"a":1}} -->'
    )
    assert P.parse_plamen_signals(txt) == {
        "required": True,
        "actor": "keeper",
        "counts": {"a": 1},
    }


# ── merge semantics (last-wins, matching STATUS/ARTIFACT convention) ───────

def test_multiple_blocks_merge_left_to_right():
    txt = (
        '<!-- PLAMEN_SIGNALS: {"a":1,"b":2} -->\n'
        "filler\n"
        '<!-- PLAMEN_SIGNALS: {"b":9,"c":3} -->\n'
    )
    assert P.parse_plamen_signals(txt) == {"a": 1, "b": 9, "c": 3}


# ── tolerance of missing / malformed ──────────────────────────────────────

def test_no_block_returns_empty_no_log(caplog):
    with caplog.at_level(logging.WARNING):
        assert P.parse_plamen_signals("just prose, no signals here") == {}
    # Absence is normal — must NOT warn.
    assert not [r for r in caplog.records if "PLAMEN_SIGNALS" in r.getMessage()]


def test_none_and_empty_text():
    assert P.parse_plamen_signals(None) == {}
    assert P.parse_plamen_signals("") == {}


def test_malformed_json_skipped_and_logged(caplog):
    txt = '<!-- PLAMEN_SIGNALS: {"sync_gaps": 5,} -->'  # trailing comma
    with caplog.at_level(logging.WARNING):
        out = P.parse_plamen_signals(txt)
    assert out == {}
    # Zero-harvest-with-evidence tripwire MUST fire.
    assert any(
        "PLAMEN_SIGNALS" in r.getMessage() for r in caplog.records
    )


def test_non_object_json_skipped(caplog):
    # A JSON array is valid JSON but not a signal object.
    txt = '<!-- PLAMEN_SIGNALS: [1, 2, 3] -->'
    with caplog.at_level(logging.WARNING):
        # `[...]` does not match the `\{.*?\}` block body, so no block is seen
        # at all -> silent {} (absence). This documents the boundary.
        out = P.parse_plamen_signals(txt)
    assert out == {}


def test_non_object_object_wrapped_skipped(caplog):
    # A scalar wrapped so the brace-body regex matches but json is a non-dict.
    # `{}` parses to an empty dict (valid, no warning); pair with a malformed
    # sibling to assert the "harvested but one malformed" branch.
    txt = (
        '<!-- PLAMEN_SIGNALS: {"ok": 1} -->\n'
        '<!-- PLAMEN_SIGNALS: {bad json} -->\n'
    )
    with caplog.at_level(logging.WARNING):
        out = P.parse_plamen_signals(txt)
    assert out == {"ok": 1}
    assert any("malformed" in r.getMessage() for r in caplog.records)


def test_empty_object_is_valid_no_warning(caplog):
    with caplog.at_level(logging.WARNING):
        out = P.parse_plamen_signals('<!-- PLAMEN_SIGNALS: {} -->')
    assert out == {}
    assert not [r for r in caplog.records if "malformed" in r.getMessage()]


# ── text normalization applied before matching ────────────────────────────

def test_crlf_normalized_before_match():
    txt = '<!-- PLAMEN_SIGNALS: {"x":1} -->\r\n<!-- PLAMEN_STATUS: COMPLETE -->\r\n'
    assert P.parse_plamen_signals(txt) == {"x": 1}


def test_html_entity_in_string_value_decoded():
    # &amp; inside a JSON string value is normalized to & by _llm_norm before
    # json.loads — documents that normalization runs first.
    txt = '<!-- PLAMEN_SIGNALS: {"name":"a &amp; b"} -->'
    assert P.parse_plamen_signals(txt) == {"name": "a & b"}
