"""Phase A — Tier-A gating/recall-risk migrations onto the Phase-0 substrate.

Regex-fragility remediation plan §2 Tier A + §5 Phases 1-3. Migrates the 5
gating sites (and their sibling extractors) onto `_field_anywhere` /
`parse_plamen_signals` / the shared niche + negation helpers.

For EACH site this file pins, per the NON-NEGOTIABLE CONSTRAINTS:
  * LEGACY-ACCEPTS (strict superset): every input the OLD regex accepted still
    yields a VALUE-IDENTICAL result (nothing that passed before fails now).
  * NEGATIVE CONTROLS: the old intended REJECTS still reject (no over-broadening).
  * RECALL-SAFETY: the migrated gate never drops a candidate / never adds a
    HALT; prose gates are WARNING + fallback, only mechanical L1 checks FAIL.
  * ZERO-HARVEST TRIPWIRE: empty-harvest-with-evidence logs a WARNING.

Generic shapes only — no protocol / contract / finding names.
"""

import logging
import re
import tempfile
from pathlib import Path

import plamen_parsers as P
import plamen_validators as V
import plamen_driver as D


def _scratch(**files) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, content in files.items():
        (d / name.replace("__", ".")).write_text(content, encoding="utf-8")
    return d


# ════════════════════════════════════════════════════════════════════════════
#  SITE 1 — inventory-structure HALT gate (_has_field -> _field_anywhere)
#  validators._validate_inventory_structure
# ════════════════════════════════════════════════════════════════════════════

def _legacy_has_field(block: str, *labels: str) -> bool:
    """The OLD _has_field regex, reproduced for the strict-superset proof."""
    block_lc = block.lower()
    for label in labels:
        if re.search(r"\*{0,2}" + re.escape(label.lower()) + r"\*{0,2}\s*:", block_lc):
            return True
    return False


_SITE1_LEGACY_ACCEPTS = [
    ("Severity: High", "Severity"),
    ("**Severity**: High", "Severity"),
    ("severity: high", "Severity"),
    ("**Location**: src/A.sol:L1", "Location"),
    ("Source IDs: B1-1", "Source IDs"),
    ("Preferred Tag: [CODE]", "Preferred Tag"),
]


def test_site1_legacy_accepts_still_present():
    # STRICT SUPERSET: every block the OLD colon-regex saw as having the field,
    # the NEW _field_anywhere-backed check still sees as present.
    for block, label in _SITE1_LEGACY_ACCEPTS:
        assert _legacy_has_field(block, label), f"fixture must be a legacy accept: {block!r}"
        value, _ = P._field_anywhere(block, [label], table_ok=True)
        assert value, f"strict-superset regression: {block!r} no longer harvests {label}"


def test_site1_table_cell_newly_seen():
    # The load-bearing widening: a UNIFORM-TABULAR finding block (colon-blind to
    # the old regex) now reports its fields present.
    for cell, label in [
        ("| Severity | High |", "Severity"),
        ("| Location | src/A.sol:L2 |", "Location"),
        ("| Source IDs | B1-2 |", "Source IDs"),
        ("| Preferred Tag | [CODE] |", "Preferred Tag"),
    ]:
        assert not _legacy_has_field(cell, label), "fixture should be a legacy MISS"
        value, _ = P._field_anywhere(cell, [label], table_ok=True)
        assert value, f"table cell field not seen: {cell!r}"


def test_site1_negative_control_absent_field():
    # NEGATIVE CONTROL: a field genuinely absent must NOT be fabricated.
    block = "## Finding [INV-9]: t\nTitle only, no severity field at all.\n"
    value, _ = P._field_anywhere(block, ["Severity", "Risk Level", "Level"], table_ok=True)
    assert value == ""


def test_site1_tabular_inventory_no_halt():
    # RECALL-SAFETY: a complete tabular inventory must not trip the >40% HALT.
    blocks = []
    for i in range(1, 9):
        blocks.append(
            f"## Finding [INV-{i}]: t{i}\n"
            "| Field | Value |\n| --- | --- |\n"
            "| Severity | High |\n"
            f"| Location | src/A.sol:L{i} |\n"
            f"| Source IDs | B1-{i} |\n"
            "| Preferred Tag | [CODE] |\n"
        )
    inv = "# Inv\n**Total Findings**: 8\n\n" + "\n".join(blocks)
    d = _scratch(findings_inventory__md=inv)
    issues = V._validate_inventory_structure(d)
    assert issues == [], f"tabular inventory must not HALT: {issues}"


def test_site1_colon_inventory_still_passes():
    # STRICT SUPERSET end-to-end: the legacy colon-form inventory still passes.
    blocks = []
    for i in range(1, 9):
        blocks.append(
            f"## Finding [INV-{i}]: t{i}\n"
            "**Severity**: High\n"
            f"**Location**: src/A.sol:L{i}\n"
            f"**Source IDs**: B1-{i}\n"
            "**Preferred Tag**: [CODE]\n"
        )
    inv = "# Inv\n**Total Findings**: 8\n\n" + "\n".join(blocks)
    d = _scratch(findings_inventory__md=inv)
    assert V._validate_inventory_structure(d) == []


def test_site1_prose_incomplete_still_halts_without_l1():
    # RECALL-SAFETY (no over-relaxation): a genuinely field-less inventory still
    # hard-fails when no L1 signal asserts completeness.
    blocks = [f"## Finding [INV-{i}]: t{i}\nNo fields here at all.\n" for i in range(1, 9)]
    inv = "# Inv\n**Total Findings**: 8\n\n" + "\n".join(blocks)
    d = _scratch(findings_inventory__md=inv)
    assert V._validate_inventory_structure(d), "field-less inventory should still HALT"


def test_site1_l1_signal_overrides_prose_no_halt():
    # L1 AUTHORITATIVE SOURCE: a PLAMEN_SIGNALS completeness assertion overrides
    # the prose count and prevents the HALT.
    blocks = [f"## Finding [INV-{i}]: t{i}\nNo fields here.\n" for i in range(1, 9)]
    inv = (
        "# Inv\n**Total Findings**: 8\n\n"
        + "\n".join(blocks)
        + '\n<!-- PLAMEN_SIGNALS: {"inventory_fields_complete": true} -->\n'
    )
    d = _scratch(findings_inventory__md=inv)
    assert V._validate_inventory_structure(d) == []

    inv2 = inv.replace(
        '<!-- PLAMEN_SIGNALS: {"inventory_fields_complete": true} -->',
        '<!-- PLAMEN_SIGNALS: {"inventory_missing_fields": 0} -->',
    )
    d2 = _scratch(findings_inventory__md=inv2)
    assert V._validate_inventory_structure(d2) == []


# ════════════════════════════════════════════════════════════════════════════
#  SITE 2 — niche spawn-manifest (heading synonym + Required-cell alias)
#  driver._required_niche_worker_jobs  +  validators._niche_tokens_from_required_table
# ════════════════════════════════════════════════════════════════════════════

def test_site2_heading_legacy_accepts():
    # STRICT SUPERSET: every heading the OLD `^##+\s+Niche Agents\b` matched
    # still matches.
    for h in ("## Niche Agents", "### Niche Agents", "#### Niche Agents"):
        assert re.match(r"^##+\s+Niche Agents\b", h, re.IGNORECASE), h
        assert P._niche_heading_match(h)


def test_site2_heading_newly_accepted():
    for h in ("# Niche Agents", "## Niche Agent Manifest", "## Niche Analysis Agents"):
        assert P._niche_heading_match(h), h


def test_site2_heading_negative_controls():
    for h in ("## Depth Agents", "## Breadth Agents", "Niche Agents inline prose"):
        assert not P._niche_heading_match(h), h


def test_site2_required_legacy_accepts():
    # STRICT SUPERSET: the OLD `"YES" in cell.upper()` accepts still accept.
    for cell in ("YES", "yes", "  YES  ", "**YES**", "YES (always)"):
        assert "YES" in cell.upper()
        assert P._niche_required_cell_yes(cell), cell


def test_site2_required_newly_accepted():
    for cell in ("Required", "required", "Y", "True", "Mandatory", "✓", "✔", "☑", "✅"):
        assert P._niche_required_cell_yes(cell), cell


def test_site2_required_negative_controls():
    # NEGATIVE CONTROLS + negation guard (recall-safe: a falsely-skipped lane is
    # the worse error, so widening defaults to spawn — but a clear NO/negation
    # must still NOT spawn).
    for cell in ("NO", "no", "Optional", "-", "", "N", "Maybe", "Yet"):
        assert not P._niche_required_cell_yes(cell), cell
    for cell in ("not required", "no, skip this lane", "never required"):
        assert not P._niche_required_cell_yes(cell), cell


def test_site2_consumer_spawns_alias_rows():
    sm = (
        "# Spawn Manifest\n"
        "# Niche Agents\n"
        "| Niche Agent | Focus | Required | Agent ID | Output |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| EVENT_COMPLETENESS | e | Required | a1 | niche_event_completeness_findings.md |\n"
        "| SIGNATURE_VERIFICATION_AUDIT | s | ✓ | a2 | niche_signature_verification_audit_findings.md |\n"
        "| MULTI_STEP_OPERATION_SAFETY | m | NO | a3 | niche_x.md |\n"
    )
    d = _scratch(spawn_manifest__md=sm)
    outs = sorted(j["output"] for j in D._required_niche_worker_jobs(d))
    assert any("event_completeness" in o for o in outs)
    assert any("signature" in o for o in outs)
    assert not any("niche_x" in o for o in outs)


def test_site2_producer_consumer_agree():
    sm = (
        "## Niche Agents\n"
        "| Niche Agent | Focus | Required |\n"
        "| --- | --- | --- |\n"
        "| EVENT_COMPLETENESS | e | Required |\n"
        "| SIGNATURE_VERIFICATION_AUDIT | s | ✓ |\n"
        "| DIMENSIONAL_ANALYSIS | d | NO |\n"
    )
    toks = V._niche_tokens_from_required_table(sm)
    assert toks == {"EVENT_COMPLETENESS", "SIGNATURE_VERIFICATION_AUDIT"}


def test_site2_l1_signal_union_adds_niche():
    # L1 AUTHORITATIVE SOURCE: a recon-declared required_niches signal is unioned
    # in (recall-safe: only ADDS).
    tr = (
        '<!-- PLAMEN_SIGNALS: {"required_niches": ["EVENT_COMPLETENESS"]} -->\n'
    )
    d = _scratch(template_recommendations__md=tr)
    got = V._signal_declared_required_niches(d)
    assert got == {"EVENT_COMPLETENESS"}


def test_site2_l1_signal_ignores_noncanonical():
    tr = '<!-- PLAMEN_SIGNALS: {"required_niches": ["not a token", "lowercase"]} -->\n'
    d = _scratch(template_recommendations__md=tr)
    assert V._signal_declared_required_niches(d) == set()


# ════════════════════════════════════════════════════════════════════════════
#  SITE 3 — severity matrix (_MATRIX_*_RE -> _field_anywhere(value_pattern=enum))
#  parsers._extract_severity_inputs
# ════════════════════════════════════════════════════════════════════════════

# Legacy-accept inputs: the assertion below proves VALUE-IDENTITY directly
# against the legacy `_MATRIX_*_RE.group(1)`, so casing/leading-word behavior is
# pinned to exactly what the old regex produced (no hand-coded expectation).
_SITE3_IMPACT_LEGACY = [
    "Impact: High",
    "**Impact**: Medium",
    "- Impact: Low",
    "* Impact: Informational",
    "impact: high",
    "Impact: High (direct fund loss)",  # leading-word value-identity
]
_SITE3_LIKELIHOOD_LEGACY = [
    "Likelihood: High",
    "**Likelihood**: Medium",
    "- Likelihood: Low",
]


def test_site3_impact_legacy_value_identity():
    for text in _SITE3_IMPACT_LEGACY:
        m = P._MATRIX_IMPACT_RE.search(text)
        assert m, f"fixture must be a legacy accept: {text!r}"
        # STRICT SUPERSET: new extractor value == legacy regex group(1).
        assert P._extract_severity_inputs(text)["impact"] == m.group(1), text


def test_site3_likelihood_legacy_value_identity():
    for text in _SITE3_LIKELIHOOD_LEGACY:
        m = P._MATRIX_LIKELIHOOD_RE.search(text)
        assert m, f"fixture must be a legacy accept: {text!r}"
        assert P._extract_severity_inputs(text)["likelihood"] == m.group(1), text


def test_site3_table_forms_newly_extracted():
    hdr = "| Impact | Likelihood |\n|---|---|\n| High | Medium |\n"
    r = P._extract_severity_inputs(hdr)
    assert r["impact"] == "High" and r["likelihood"] == "Medium"
    kv = "| Impact | High |\n| Likelihood | Medium |\n"
    r = P._extract_severity_inputs(kv)
    assert r["impact"] == "High" and r["likelihood"] == "Medium"
    # legacy regex was table-blind:
    assert P._MATRIX_IMPACT_RE.search(hdr) is None
    assert P._MATRIX_IMPACT_RE.search(kv) is None


def test_site3_negative_control_no_enum_value():
    # NEGATIVE CONTROL: a non-enum value is not harvested (matches legacy:
    # the old regex required the enum word too).
    assert P._extract_severity_inputs("Impact: to be assessed")["impact"] is None
    # Likelihood does NOT accept Informational (legacy enum excluded it).
    assert P._extract_severity_inputs("Likelihood: Informational")["likelihood"] is None


def test_site3_missing_axis_returns_none():
    r = P._extract_severity_inputs("no matrix here")
    assert r["impact"] is None and r["likelihood"] is None


# ════════════════════════════════════════════════════════════════════════════
#  SITE 4 — findings heading (#{1,6} tolerant)
#  parsers._NO_FINDINGS_HEADING_RE / _FINDINGS_SECTION_RE
# ════════════════════════════════════════════════════════════════════════════

def test_site4_no_findings_legacy_h2_still_matches():
    # STRICT SUPERSET: every H2 form the OLD regex matched still matches.
    for t in ("## No Findings", "## Negative Result — nothing found",
              "##  No Findings: clean"):
        assert re.search(r"^##\s+.*\b(No\s+Findings|Negative\s+Result)\b", t,
                         re.MULTILINE | re.IGNORECASE), t
        assert P._NO_FINDINGS_HEADING_RE.search(t), t


def test_site4_no_findings_drift_newly_matched():
    for t in ("# No Findings", "### No Findings", "###### Negative Result"):
        assert P._NO_FINDINGS_HEADING_RE.search(t), t


def test_site4_findings_section_levels():
    for h in ("# Findings", "## Findings", "### Findings"):
        assert P._FINDINGS_SECTION_RE.search(h + "\nbody " * 5), h


def test_site4_findings_section_disjoint_from_block_heading():
    # A `## Finding [ID]` block heading must NOT be read as the `## Findings`
    # section (the disjointness invariant).
    assert not P._FINDINGS_SECTION_RE.search("## Finding [H-1]: title")


def test_site4_structural_gate_accepts_drifted_negative_result():
    # RECALL-SAFETY: a complete artifact whose negative-result rationale drifted
    # to H1/H3 is no longer false-failed by the completion gate.
    d = Path(tempfile.mkdtemp())
    p = d / "depth_x_findings.md"
    p.write_text("# Depth\n### No Findings\nNothing reportable in scope.\n", encoding="utf-8")
    ok, reasons = P._structural_completeness_ok(p)
    assert ok, reasons


# ════════════════════════════════════════════════════════════════════════════
#  SITE 5 — non-reportable marker (clause-scoped negation guard)  RECALL-CRITICAL
#  parsers._non_reportable_marker
# ════════════════════════════════════════════════════════════════════════════

_SITE5_TRUE_MARKERS = [
    "REFUTED", "false positive", "false_positive", "infeasible",
    "not applicable", "absorbed", "absorbed into H-3", "duplicate",
    "deduplicated", "merged", "merged into M-2", "not reportable", "no finding",
]


def test_site5_true_markers_still_fire():
    # STRICT SUPERSET: every ungoverned non-reportable marker still demotes.
    for t in _SITE5_TRUE_MARKERS:
        assert P._non_reportable_marker(t), t


def test_site5_negated_markers_suppressed():
    # RECALL-CRITICAL: a negation governing the marker keeps the finding
    # reportable (the live "NOT a duplicate" demotion bug).
    for t in (
        "NOT a duplicate",
        "this is not refuted",
        "not a false positive",
        "this was never a duplicate",
        "no, not a duplicate of anything",
    ):
        assert not P._non_reportable_marker(t), t


def test_site5_cross_clause_negation_does_not_suppress():
    # The clause scoping: a marker in clause 1 and an unrelated negation in
    # clause 2 must STILL demote (the negation does not govern the marker).
    assert P._non_reportable_marker("This is a duplicate. The harness does not exist.")
    assert P._non_reportable_marker("Verdict: REFUTED. No PoC was attempted.")


def test_site5_self_negating_markers_not_self_suppressed():
    # The markers that legitimately CONTAIN a negation ("not applicable",
    # "not reportable", "no finding") must still fire — their own negation must
    # not self-suppress them.
    for t in ("not applicable", "not reportable", "no finding"):
        assert P._non_reportable_marker(t), t


def test_site5_negative_control_word_boundary():
    # NEGATIVE CONTROL: a substring like "duplicated" inside an unrelated word
    # must not over-fire beyond the intended markers. "predicate" contains no
    # whole marker word.
    assert not P._non_reportable_marker("the predicate evaluates true")
    assert not P._non_reportable_marker("Severity: High")


# ════════════════════════════════════════════════════════════════════════════
#  ZERO-HARVEST TRIPWIRE (cross-site): empty harvest + evidence -> WARNING
# ════════════════════════════════════════════════════════════════════════════

def test_zero_harvest_tripwire_site1(caplog):
    with caplog.at_level(logging.WARNING):
        out, _ = P._field_anywhere(
            "This block discusses Severity but never sets it.",
            ["Severity"], table_ok=True,
        )
    assert out == ""
    assert any("_field_anywhere" in r.getMessage() for r in caplog.records)
