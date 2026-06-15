"""Tests for Ship 5 of the artifact-complete PTY supervision plan.

Validates ``pty_exec.parse_transcript_agentids``: the transcript parser
that correlates subagent dispatches with their returned ``agentId``
handles using the ``AGENT_ROW`` and ``EXPECTED_OUTPUT`` markers injected
into the dispatch prompt by the breadth Subagent Prompt Template.

The parser is pure (file -> dict), so tests can build synthetic JSONL
transcripts in a tmp directory. No live Claude API, no PTY, no
subprocess.

Test numbers 38-40 match the plan's ``test_agentid_mapping.py`` section.
``send_continuation`` is not directly unit-tested here -- it is exercised
indirectly by Ship 6's supervision loop tests (test_pty_supervision.py)
and by the empirical preflight in Ship 4. Its body is a verbatim mirror
of ``send_bootstrap``'s well-tested platform-conditional CR/LF block.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from pty_exec import parse_transcript_agentids  # noqa: E402


def _dispatch_event(
    tool_use_id: str,
    agent_row: str | None,
    expected_output: str | None,
    description: str = "B-row dispatch",
    name: str = "Agent",
    extra_prompt_text: str = "",
) -> dict:
    """Build a synthetic Agent/Task tool_use event. ``agent_row`` /
    ``expected_output`` are included in the prompt body iff truthy --
    that's how we simulate the dispatch-without-marker case from
    test 40."""
    parts = []
    if agent_row:
        parts.append(f"AGENT_ROW: {agent_row}")
    if expected_output:
        parts.append(f"EXPECTED_OUTPUT: {expected_output}")
    parts.append("Step 1 -- write the artifact stub, then analyze.")
    if extra_prompt_text:
        parts.append(extra_prompt_text)
    prompt = "\n".join(parts)
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": name,
                    "id": tool_use_id,
                    "input": {
                        "description": description,
                        "subagent_type": "general-purpose",
                        "prompt": prompt,
                    },
                }
            ]
        },
    }


def _result_event(tool_use_id: str, handle: str | None) -> dict:
    """Build a synthetic tool_result event. ``handle`` is the agentId;
    when None we emit a result without the SendMessage-style handle
    string (subagent finished normally)."""
    if handle:
        text = (
            f"agentId: {handle} (use SendMessage with to: '{handle}' "
            f"to continue this agent)\n<usage>tool_uses: 14</usage>"
        )
    else:
        text = "DONE: 3 findings written.\n<usage>tool_uses: 9</usage>"
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": text}],
                }
            ]
        },
    }


def _write_transcript(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fp:
        for ev in events:
            fp.write(json.dumps(ev) + "\n")


# ---------------------------------------------------------------------------
# Test 38 -- extracts AGENT_ROW marker
# ---------------------------------------------------------------------------


def test_parse_transcript_agentids_extracts_agent_row_marker(tmp_path: Path):
    """A single Agent dispatch whose prompt carries an AGENT_ROW
    marker MUST be keyed by that row in the returned mapping."""
    transcript = tmp_path / "session.jsonl"
    _write_transcript(
        transcript,
        [
            _dispatch_event(
                tool_use_id="toolu_001",
                agent_row="B3",
                expected_output="analysis_access_control.md",
                description="B3 access control breadth analysis",
            ),
            _result_event("toolu_001", handle="a39093327"),
        ],
    )

    result = parse_transcript_agentids(transcript)

    assert "B3" in result, f"missing B3 key; got {list(result.keys())!r}"
    entry = result["B3"]
    assert entry["agent_id"] == "B3"
    assert entry["expected_output"] == "analysis_access_control.md"
    assert entry["handle"] == "a39093327"
    assert "B3 access control" in entry["description"]


# ---------------------------------------------------------------------------
# Test 39 -- correlates handle to manifest row / expected output
# ---------------------------------------------------------------------------


def test_parse_transcript_agentids_correlates_handle_to_expected_output(
    tmp_path: Path,
):
    """Multiple dispatches in the same transcript MUST be correlated
    one-to-one with their tool_result handles via tool_use_id, even
    when events are interleaved (B1's result lands before B2's dispatch
    completes). Output is keyed by AGENT_ROW, not handle."""
    transcript = tmp_path / "session.jsonl"
    _write_transcript(
        transcript,
        [
            _dispatch_event(
                tool_use_id="toolu_A",
                agent_row="B1",
                expected_output="analysis_core_state.md",
                description="B1 core_state",
            ),
            _dispatch_event(
                tool_use_id="toolu_B",
                agent_row="B2",
                expected_output="analysis_cross_chain_timing.md",
                description="B2 cross_chain_timing",
            ),
            _dispatch_event(
                tool_use_id="toolu_C",
                agent_row="B3",
                expected_output="analysis_access_control.md",
                description="B3 access_control",
            ),
            # Interleaved order: B2 returns first, then B1, then B3.
            _result_event("toolu_B", handle="aBBBBB"),
            _result_event("toolu_A", handle="aAAAAA"),
            _result_event("toolu_C", handle="aCCCCC"),
        ],
    )

    result = parse_transcript_agentids(transcript)

    assert set(result.keys()) == {"B1", "B2", "B3"}
    assert result["B1"]["handle"] == "aAAAAA"
    assert result["B1"]["expected_output"] == "analysis_core_state.md"
    assert result["B2"]["handle"] == "aBBBBB"
    assert result["B2"]["expected_output"] == "analysis_cross_chain_timing.md"
    assert result["B3"]["handle"] == "aCCCCC"
    assert result["B3"]["expected_output"] == "analysis_access_control.md"
    # Verify the mapping is keyed by AGENT_ROW (not by handle): handle
    # strings must NOT appear as keys.
    assert "aAAAAA" not in result
    assert "aBBBBB" not in result
    assert "aCCCCC" not in result


# ---------------------------------------------------------------------------
# Test 40 -- dispatch without AGENT_ROW marker is not fabricated
# ---------------------------------------------------------------------------


def test_parse_transcript_agentids_handles_dispatch_without_marker(
    tmp_path: Path,
):
    """A dispatch whose prompt lacks the AGENT_ROW marker MUST NOT
    appear in the returned mapping -- the parser refuses to fabricate
    a row name from the handle or description alone. The driver's
    continuation builder treats such dispatches as opaque and respawns
    the row from scratch instead.

    Mixed scenarios in the same transcript also tested:
      - one dispatch WITH marker + result -> keyed by row
      - one dispatch WITHOUT marker + result -> absent from output
      - one dispatch WITH marker but NO result yet -> keyed by row,
        handle is empty string
    """
    transcript = tmp_path / "session.jsonl"
    _write_transcript(
        transcript,
        [
            _dispatch_event(
                tool_use_id="toolu_with",
                agent_row="B7",
                expected_output="analysis_migration.md",
                description="B7 migration",
            ),
            _dispatch_event(
                tool_use_id="toolu_without",
                agent_row=None,  # explicitly omitted -- no marker
                expected_output=None,
                description="legacy unmarked dispatch",
            ),
            _dispatch_event(
                tool_use_id="toolu_pending",
                agent_row="B8",
                expected_output="analysis_token_flow.md",
                description="B8 token_flow (no result yet)",
            ),
            _result_event("toolu_with", handle="a777"),
            _result_event("toolu_without", handle="a999"),
            # toolu_pending intentionally has no result event
        ],
    )

    result = parse_transcript_agentids(transcript)

    # B7 -- marker + result
    assert "B7" in result
    assert result["B7"]["handle"] == "a777"
    assert result["B7"]["expected_output"] == "analysis_migration.md"

    # B8 -- marker present, but no result yet -> handle is empty string
    # (the continuation builder will treat this as "named row but no
    # resumable handle; respawn the row").
    assert "B8" in result
    assert result["B8"]["handle"] == ""
    assert result["B8"]["expected_output"] == "analysis_token_flow.md"

    # The unmarked dispatch MUST NOT appear in any form: not by handle,
    # not by description, not by some synthesized key.
    for row, entry in result.items():
        assert "legacy unmarked" not in entry["description"]
        assert entry["handle"] != "a999"
    assert set(result.keys()) == {"B7", "B8"}


# ---------------------------------------------------------------------------
# Additional sanity counterparts (defensive coverage, not numbered tests)
# ---------------------------------------------------------------------------


def test_parse_transcript_agentids_missing_file_returns_empty(tmp_path: Path):
    """Plan contract: missing transcript -> ``{}``. No raised exception."""
    bogus = tmp_path / "does_not_exist.jsonl"
    assert parse_transcript_agentids(bogus) == {}


def test_parse_transcript_agentids_empty_file_returns_empty(tmp_path: Path):
    """Empty transcript (no events at all) -> ``{}``."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    assert parse_transcript_agentids(transcript) == {}


def test_parse_transcript_agentids_corrupt_lines_skipped(tmp_path: Path):
    """A transcript with some invalid JSON lines must still parse the
    valid ones. Corrupt lines are skipped, not fatal."""
    transcript = tmp_path / "session.jsonl"
    with transcript.open("w", encoding="utf-8") as fp:
        fp.write("this is not json\n")
        fp.write(json.dumps(_dispatch_event("toolu_X", "B1", "analysis_x.md")) + "\n")
        fp.write("{unclosed brace\n")
        fp.write(json.dumps(_result_event("toolu_X", "aBeef")) + "\n")
        fp.write("\n")  # blank line
    result = parse_transcript_agentids(transcript)
    assert result == {
        "B1": {
            "agent_id": "B1",
            "expected_output": "analysis_x.md",
            "handle": "aBeef",
            "description": "B-row dispatch",
        }
    }


def test_parse_transcript_agentids_task_tool_name_also_accepted(tmp_path: Path):
    """In some Claude Code versions the dispatch tool is named ``Task``
    rather than ``Agent``. The parser accepts either."""
    transcript = tmp_path / "session.jsonl"
    _write_transcript(
        transcript,
        [
            _dispatch_event(
                tool_use_id="toolu_T",
                agent_row="B1",
                expected_output="analysis_core_state.md",
                name="Task",
            ),
            _result_event("toolu_T", handle="aT0"),
        ],
    )
    result = parse_transcript_agentids(transcript)
    assert "B1" in result
    assert result["B1"]["handle"] == "aT0"


def test_parse_transcript_agentids_html_comment_form_works(tmp_path: Path):
    """The dispatch prompt commonly contains BOTH a plain
    ``AGENT_ROW: B3`` header AND an HTML-comment-form
    ``<!-- AGENT_ROW: B3 -->`` inside the file-body Write template
    block. The parser must capture the row identically either way --
    the regex stops at the first whitespace OR HTML-comment-closer
    boundary."""
    transcript = tmp_path / "session.jsonl"
    # Build a dispatch where ONLY the HTML-comment form is present
    # (no bare header). Simulates a hypothetical drift where the
    # template carried only file-body markers.
    html_only_prompt = (
        "Step 1: Write file with body:\n"
        "  <!-- AGENT_ROW: B5 -->\n"
        "  <!-- EXPECTED_OUTPUT: analysis_cross_chain_msg.md -->\n"
        "Step 2: ..."
    )
    ev = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Agent",
                    "id": "toolu_html",
                    "input": {
                        "description": "B5 cross_chain_msg",
                        "prompt": html_only_prompt,
                    },
                }
            ]
        },
    }
    _write_transcript(transcript, [ev, _result_event("toolu_html", handle="aH5")])

    result = parse_transcript_agentids(transcript)
    assert "B5" in result
    assert result["B5"]["expected_output"] == "analysis_cross_chain_msg.md"
    assert result["B5"]["handle"] == "aH5"
