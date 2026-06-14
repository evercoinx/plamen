"""Regression: coverage-ledger parser must not treat prose containing the word
"candidate" as a table header.

Live failure (DODO Opus rerun): the LLM coverage ledger had data rows whose
Reason column said e.g. "auto-mapped depth candidate, no verifier verdict".
_collect_report_coverage_acknowledged_ids detected "candidate" anywhere in a
row and treated that DATA row as a header, flipping id_col to the reason
column. Every following row's ID was then read from the wrong column, so real
acknowledgments (INV-139 -> H-115, etc.) were lost -> the completeness gate
falsely reported dropped IDs -> report_index halted. Fix: only treat a cell as
an ID header when it is an actual header LABEL, not prose containing the word.
"""
import tempfile
from pathlib import Path

import plamen_validators as V


def _write(sp, body):
    (sp / "report_coverage.md").write_text(body, encoding="utf-8")


def test_data_row_reason_with_candidate_word_is_not_a_header():
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d)
        _write(sp,
            "# Report Coverage\n\n"
            "## Raw Candidate Ledger\n"
            "| Source File | Candidate ID / Label | Severity Signal | Status | Report ID / Refutation / Reason |\n"
            "|---|---|---|---|---|\n"
            "| finding_mapping.md | DX-1 | (none) | APPENDIX_ONLY | H-121; auto-mapped depth candidate, no verdict |\n"
            "| finding_mapping.md | INV-139 | - | PROMOTED | H-115 -> L-46 |\n"
            "| finding_mapping.md | INV-001 | - | PROMOTED | H-114 -> H-13 |\n"
        )
        ack = V._collect_report_coverage_acknowledged_ids(sp)
        # The row whose Reason contains "candidate" must NOT corrupt parsing:
        # INV-139 and INV-001 (in the real Candidate-ID column) must be acknowledged.
        assert "INV-139" in ack, ack
        assert "INV-001" in ack, ack
        assert "DX-1" in ack, ack


def test_real_header_still_detected():
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d)
        _write(sp,
            "## Coverage Ledger\n"
            "| Candidate ID | Status | Reason |\n"
            "|---|---|---|\n"
            "| INV-7 | PROMOTED | mapped |\n"
        )
        ack = V._collect_report_coverage_acknowledged_ids(sp)
        assert "INV-7" in ack, ack


def test_unaccounted_still_excluded():
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d)
        _write(sp,
            "## Raw Candidate Ledger\n"
            "| Source File | Candidate ID / Label | Severity Signal | Status | Reason |\n"
            "|---|---|---|---|---|\n"
            "| x | INV-9 | - | UNACCOUNTED | not handled |\n"
            "| x | INV-8 | - | PROMOTED | ok |\n"
        )
        ack = V._collect_report_coverage_acknowledged_ids(sp)
        assert "INV-8" in ack
        assert "INV-9" not in ack  # UNACCOUNTED is not an acknowledgment


def test_backfill_into_midfile_ledger_is_parser_visible():
    """When a ledger section already exists mid-file (followed by later
    sections), backfilled rows must land INSIDE that section so the parser
    reads them. (The EOF-append bug put them after later sections -> invisible.)
    Validated directly on the acknowledged-ID parser rather than the seed path.
    """
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d)
        _write(sp,
            "## Raw Candidate Ledger\n"
            "| Source File | Candidate ID / Label | Severity Signal | Status | Reason |\n"
            "|---|---|---|---|---|\n"
            "| m | H-1 | High | PROMOTED | H-01 |\n"
            "## Promotion Failures Repaired\n| a | b | c |\n|---|---|---|\n"
        )
        # simulate a backfill row inserted INTO the ledger section (the fix),
        # vs the old bug which appended after "## Promotion Failures Repaired".
        text = (sp / "report_coverage.md").read_text(encoding="utf-8")
        row = "| mechanical-dropout-backfill | INV-50 | unknown | DEFERRED | backfilled |"
        # insert before the next "## " after the ledger header
        import re
        m = re.search(r"(?m)^##\s+", text[text.index("## Raw Candidate Ledger") + 5:])
        cut = text.index("## Raw Candidate Ledger") + 5 + m.start()
        fixed = text[:cut].rstrip("\n") + "\n" + row + "\n\n" + text[cut:]
        (sp / "report_coverage.md").write_text(fixed, encoding="utf-8")
        ack = V._collect_report_coverage_acknowledged_ids(sp)
        assert "INV-50" in ack, ack  # row inside the ledger is parser-visible


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
