"""Regression test for the spawn_manifest validator's prose-tolerance fix.

Background: an observed audit halt at Phase 2 (instantiate). The LLM
produced a structurally-correct spawn_manifest.md AGENT table with no
forbidden artifacts, then added a helpful `## Phase 3b/3c Artifacts
(NOT breadth AGENT rows)` section in PROSE bullet points to explicitly
clarify what wasn't in the table. The pre-fix validator scanned the
whole file for forbidden filenames and got false-positives on the
prose mentions.

Fix scopes the scan to actual markdown table rows (lines starting and
containing `|` separators), so prose bullets and explanatory paragraphs
are ignored.

These tests lock in:
  - Prose mentions of forbidden filenames don't trigger the gate
  - Forbidden filenames in actual table ROWS still trigger the gate
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))


_BREADTH_TABLE_HEADER = (
    "| Row Type | Template | Required? | Agent ID | Focus Area | Expected Output | Status |\n"
    "|----------|----------|-----------|----------|------------|-----------------|--------|\n"
)


def _write(p: Path, body: str) -> None:
    p.write_text(body, encoding="utf-8")


def test_prose_mention_of_forbidden_artifact_does_not_fail(tmp_path: Path):
    """The failure mode verbatim: a `## Phase 3b/3c Artifacts` block
    with bullet-list mentions of analysis_rescan_*.md / analysis_percontract_*.md
    explaining what the table does NOT contain. Pre-fix: validator halted.
    Post-fix: validator passes."""
    import plamen_validators as V

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "spawn_manifest.md",
        "# Spawn Manifest\n\n"
        "## Breadth Agents\n\n"
        + _BREADTH_TABLE_HEADER
        + "| AGENT | CORE_STATE | YES | B1 | core_state | analysis_core_state.md | QUEUED |\n"
        + "| AGENT | TOKEN_FLOW | YES | B2 | token_flow | analysis_token_flow.md | QUEUED |\n"
        + "\n"
        "## Phase 3b/3c Artifacts (NOT breadth AGENT rows — produced by separate re-scan/per-contract phases)\n\n"
        "Per the pipeline gate contract: rescan and per-contract artifacts are produced "
        "by Phase 3b and Phase 3c respectively, AFTER the first-pass breadth phase completes. "
        "They are not spawned here.\n\n"
        "- analysis_rescan_*.md → Phase 3b (re-scan agents, up to 2 iterations × 3 agents)\n"
        "- analysis_percontract_*.md → Phase 3c (per-contract agents, 1 per contract cluster)\n",
    )
    issues = V._validate_spawn_manifest_schema(sp)
    assert issues == [], (
        f"Prose bullets describing what the table does NOT contain should "
        f"not trigger the forbidden-row gate. Got: {issues}"
    )


def test_forbidden_artifact_in_actual_agent_row_still_fails(tmp_path: Path):
    """Sanity: the IMPORTANT failure mode (LLM tries to schedule a
    forbidden artifact as a real AGENT row) must still be caught."""
    import plamen_validators as V

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "spawn_manifest.md",
        "# Spawn Manifest\n\n"
        "## Breadth Agents\n\n"
        + _BREADTH_TABLE_HEADER
        + "| AGENT | CORE_STATE | YES | B1 | core_state | analysis_core_state.md | QUEUED |\n"
        # FORBIDDEN — analysis_rescan in an actual AGENT row.
        + "| AGENT | RESCAN_HACK | YES | B2 | rescan | analysis_rescan_1.md | QUEUED |\n",
    )
    issues = V._validate_spawn_manifest_schema(sp)
    assert issues, "Real forbidden row must be caught"
    assert any("non-breadth artifact" in i for i in issues)


def test_forbidden_artifact_in_arbitrary_table_row_still_fails(tmp_path: Path):
    """Catch the case where the LLM puts a forbidden artifact in a
    table cell of a non-AGENT-table (e.g. a side table they invented).
    The forbidden contract is "must not be in ANY pipe-delimited row,"
    not just the main AGENT table — because the validator can't
    structurally distinguish which table is which when the LLM is
    creative with formatting."""
    import plamen_validators as V

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "spawn_manifest.md",
        "# Spawn Manifest\n\n"
        "## Breadth Agents\n\n"
        + _BREADTH_TABLE_HEADER
        + "| AGENT | CORE_STATE | YES | B1 | core_state | analysis_core_state.md | QUEUED |\n\n"
        "## Verification Pipeline\n\n"
        "| Phase | Artifact | When |\n"
        "|-------|----------|------|\n"
        "| 5 | verify_FOO.md | post-depth |\n",
    )
    issues = V._validate_spawn_manifest_schema(sp)
    assert issues, (
        "Forbidden artifact in any pipe-delimited row should fail — the "
        "LLM creating a 'helpful' side table with verify_* is a real bug "
        "class the pre-fix validator caught."
    )


def test_separator_rows_are_not_counted_as_violations(tmp_path: Path):
    """Markdown separator row `|---|---|` must not somehow contain
    forbidden patterns — sanity that the separator filter works."""
    import plamen_validators as V

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "spawn_manifest.md",
        "# Spawn Manifest\n\n"
        "## Breadth Agents\n\n"
        + _BREADTH_TABLE_HEADER
        + "| AGENT | CORE_STATE | YES | B1 | core_state | analysis_core_state.md | QUEUED |\n",
    )
    issues = V._validate_spawn_manifest_schema(sp)
    assert issues == []


def test_code_fence_mentions_with_pipe_chars_still_flagged(tmp_path: Path):
    """KNOWN LIMITATION (not a regression): if the LLM puts a fake
    `| ... |` row INSIDE a code fence to illustrate a wrong example,
    the line-based scanner can't distinguish that from a real table row
    and will flag it. Acceptable because:
      (a) LLMs almost never write `do NOT do this` illustrative tables
          in spawn_manifest.md
      (b) The fix is to use bullet-list prose for examples (which the
          LLM actually did), and prose is now tolerated
      (c) Code-fence tracking adds state machine complexity that risks
          breaking other parsers — explicitly out of scope
    Lock the known limitation in so a future test author doesn't claim
    code-fence tolerance and break something else trying to add it."""
    import plamen_validators as V

    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    _write(
        sp / "spawn_manifest.md",
        "# Spawn Manifest\n\n"
        "## Breadth Agents\n\n"
        + _BREADTH_TABLE_HEADER
        + "| AGENT | CORE_STATE | YES | B1 | core_state | analysis_core_state.md | QUEUED |\n\n"
        "## Examples of WRONG manifest content\n\n"
        "```\n"
        "| AGENT | BAD | YES | X | x | analysis_rescan_1.md | QUEUED |\n"
        "```\n",
    )
    issues = V._validate_spawn_manifest_schema(sp)
    assert issues, "Pipe-delimited content inside code fence currently DOES flag — see test docstring for why this is acceptable"
