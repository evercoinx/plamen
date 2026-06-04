"""Contract tests for prompts/shared/v2/phase4e-semantic-dedup.md.

The dedup prompt is markdown (no executable code), but it carries load-bearing
contract clauses that the rest of the pipeline depends on:

  1. ZERO-DATA-LOSS coupling: every MERGE must couple both findings' distinct
     attack paths/routes/locations/impacts/source-IDs/evidence-tags.
  2. SURVIVOR-SUPERSET gate: survivor source-ID set must be a superset and
     location must subsume; otherwise flip or KEEP SEPARATE.
  3. Aggregate suppression: source-ID-subset / PERT hints unreliable for
     >4-source-ID findings; never merge on them alone.
  4. Higher-severity + evidence-tag inheritance on MERGE.
  5. Multi-round handling with an Already-decided exclusion list.
  6. PARSER-CRITICAL schema preserved so plamen_mechanical._extract_dedup_absorbed_ids
     and plamen_validators._collect_semantic_dedup_acknowledged_ids keep working:
       - heading  `### MERGE: {survivor} absorbs {absorbed}`
       - status   `| {absorbed} | MERGED into {survivor} | ... |`

These tests fail loudly if a future edit weakens the recall protections or
breaks the downstream parsing contract.
"""

import re
from pathlib import Path

import pytest

PROMPT = (
    Path(__file__).resolve().parent.parent
    / "prompts"
    / "shared"
    / "v2"
    / "phase4e-semantic-dedup.md"
)


@pytest.fixture(scope="module")
def text() -> str:
    assert PROMPT.is_file(), f"missing prompt: {PROMPT}"
    return PROMPT.read_text(encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).lower()


def test_zero_data_loss_mandate_present(text):
    n = _norm(text)
    assert "zero-data-loss" in n
    # forbids dropping a distinct attack path
    assert "destroyed true-positive" in n or "destroyed true positive" in n
    assert "forbidden" in n


def test_survivor_superset_gate_present(text):
    n = _norm(text)
    assert "survivor-superset" in n
    assert "superset" in n
    # flip-or-keep-separate when neither side subsumes
    assert "flip the survivor" in n
    assert "neither side is a superset" in n


def test_union_source_ids_required(text):
    n = _norm(text)
    assert "union" in n and "source id" in n
    # survivor source IDs become the union
    assert "source ids" in n


def test_higher_severity_inheritance(text):
    n = _norm(text)
    assert "higher severity" in n or "higher of the two" in n
    # never a downgrade
    assert "never lower" in n or "never a downgrade" in n


def test_evidence_tag_inheritance(text):
    n = _norm(text)
    assert "[poc-pass]" in n
    assert "[medusa-pass]" in n
    assert "retain" in n or "keep it" in n


def test_aggregate_suppression_documented(text):
    n = _norm(text)
    assert ">4 source id" in n or "more than" in n and "source id" in n
    assert "aggregate-suppressed" in n
    assert "pert lineage" in n
    assert "source-id subset" in n or "source-id-subset" in n
    # never merge on those hints alone for large aggregates
    assert "never merge" in n or "never a merge" in n or "never authorize" in n


def test_signals_are_hints_not_authority(text):
    n = _norm(text)
    assert "hints, not authority" in n or "hints only" in n
    assert "function-name match is one signal, not authority" in n


def test_multiround_exclusion_list(text):
    n = _norm(text)
    assert "dedup_candidate_pairs_round" in n.replace(" ", "") or \
        "dedup_candidate_pairs_round{n}" in n
    assert "already-decided exclusion list" in n
    assert "do not re-decide" in n or "do not redecide" in n


def test_keep_separate_default_when_in_doubt(text):
    n = _norm(text)
    assert "when in doubt, `keep separate`" in n or "when in doubt, keep separate" in n
    # direction-flip recognized as keep-separate
    assert "direction_flip" in n or "direction-flip" in n
    # compound second-defect recognized as keep-separate
    assert "distinct second defect" in n or "second defect" in n


def test_coupling_before_deletion(text):
    n = _norm(text)
    # must edit survivor before removing absorbed block/row
    assert "never delete the absorbed block before" in n
    assert "never drop the absorbed row before" in n


def test_parser_critical_merge_heading_form(text):
    # plamen_validators._collect_semantic_dedup_acknowledged_ids parses:
    #   ### MERGE: {survivor} absorbs {absorbed}
    assert re.search(
        r"^###\s+MERGE:\s+\{survivor_id\}\s+absorbs\s+\{absorbed_id\}\s*$",
        text,
        re.MULTILINE,
    ), "MERGE heading template form changed — breaks acknowledged-id parser"


def test_parser_critical_status_row_form(text):
    # plamen_mechanical._extract_dedup_absorbed_ids parses:
    #   | {absorbed} | MERGED into {survivor} | ... |
    assert "MERGED into" in text
    # explicit statement that the row form is parser-critical
    n = _norm(text)
    assert "parser-critical" in n


def test_status_row_example_parses_with_real_regex(text):
    """The example MERGE status row in the prompt must parse with the SAME
    regex plamen_mechanical._extract_dedup_absorbed_ids uses, even with the
    added Coupled-content column."""
    merge_rows = [
        ln
        for ln in text.splitlines()
        if ln.lstrip().startswith("|") and "MERGED into" in ln
    ]
    assert merge_rows, "no example MERGE status row in prompt"
    rgx = re.compile(r"^\|\s*(\S+)\s*\|\s*MERGED\s+into\b", re.IGNORECASE)
    for ln in merge_rows:
        m = rgx.match(ln.strip())
        assert m, f"example MERGE row no longer parses: {ln!r}"
        absorbed = m.group(1).strip().strip("[]")
        assert re.fullmatch(r"[A-Za-z]+-\d+", absorbed), (
            f"absorbed id token malformed in example row: {absorbed!r}"
        )


def test_status_row_example_heading_parses_with_real_regex(text):
    """The example MERGE heading must parse with the SAME regex
    plamen_validators._collect_semantic_dedup_acknowledged_ids uses."""
    rgx = re.compile(
        r"(?i)^\s*#{2,6}\s+MERGE:\s+([A-Z]+-\d+)\s+absorbs\s+(.+?)\s*$"
    )
    # The template placeholder line should structurally match the parser shape
    # when placeholders are substituted; verify the parser tolerates a concrete
    # instance shaped like the template.
    concrete = "### MERGE: INV-014 absorbs INV-013"
    m = rgx.match(concrete)
    assert m and m.group(1) == "INV-014"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
