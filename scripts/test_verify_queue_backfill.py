"""Tests for the verify-queue completeness backfill.

Covers the unbreakable-resume-loop bug: when LLM queue generation drops some
inventory IDs (neither queued, evidence-excluded, nor semantic-deduped), the
verification-queue<->inventory parity validator reports a dropout, which makes
the resume reconciliation rewind the entire verify stage. Re-running the LLM
queue drops the same IDs again -> infinite rewind.

`backfill_unrouted_inventory_into_queue` converts that silent dropout into
explicit, deterministic queue rows so parity always holds.
"""

from pathlib import Path

import plamen_mechanical as M
import plamen_parsers as P
import plamen_validators as V


QUEUE_HEADER = (
    "# Verification Queue\n"
    "| Queue # | Finding ID | Severity | Title | Bug Class | Preferred Tag |\n"
    "|---------|------------|----------|-------|-----------|---------------|\n"
)


def _write_inventory(sp: Path, findings):
    """findings: list of (id, severity, title)."""
    lines = ["# Findings Inventory\n"]
    for fid, sev, title in findings:
        lines.append(f"## Finding [{fid}]: {title}\n")
        lines.append(f"**Severity**: {sev}\n")
        lines.append(f"**Location**: src/a.sol:L1\n")
        lines.append("**Description**: something is wrong.\n\n")
    (sp / "findings_inventory.md").write_text("".join(lines), encoding="utf-8")


def _write_queue(sp: Path, rows):
    """rows: list of (queue_n, id, severity, title)."""
    body = QUEUE_HEADER
    for n, fid, sev, title in rows:
        body += f"| {n} | {fid} | {sev} | {title} | Some Class | [CODE-TRACE] |\n"
    (sp / "verification_queue.md").write_text(body, encoding="utf-8")


def _make_two_missing(tmp_path) -> Path:
    """5 inventory findings, queue covers only the first 3 -> INV-004/005 missing."""
    sp = tmp_path
    _write_inventory(
        sp,
        [
            ("INV-001", "High", "Bug one"),
            ("INV-002", "Medium", "Bug two"),
            ("INV-003", "Low", "Bug three"),
            ("INV-004", "Critical", "Bug four"),
            ("INV-005", "Medium", "Bug five"),
        ],
    )
    _write_queue(
        sp,
        [
            (1, "INV-001", "High", "Bug one"),
            (2, "INV-002", "Medium", "Bug two"),
            (3, "INV-003", "Low", "Bug three"),
        ],
    )
    return sp


def test_backfill_routes_missing(tmp_path):
    sp = _make_two_missing(tmp_path)
    appended = M.backfill_unrouted_inventory_into_queue(sp)
    assert appended == ["INV-004", "INV-005"]
    # After backfill there must be NO dropout/coverage parity issue.
    issues = V._validate_verification_queue_inventory_parity(sp)
    joined = " ".join(issues).lower()
    assert "dropout" not in joined, issues
    assert "coverage" not in joined, issues


def test_backfill_idempotent(tmp_path):
    sp = _make_two_missing(tmp_path)
    first = M.backfill_unrouted_inventory_into_queue(sp)
    assert first == ["INV-004", "INV-005"]
    rows_after_first = len(P.parse_verification_queue_rows(sp))
    second = M.backfill_unrouted_inventory_into_queue(sp)
    assert second == []
    rows_after_second = len(P.parse_verification_queue_rows(sp))
    assert rows_after_first == rows_after_second == 5


def test_backfill_rows_wellformed(tmp_path):
    sp = _make_two_missing(tmp_path)
    M.backfill_unrouted_inventory_into_queue(sp)
    text = (sp / "verification_queue.md").read_text(encoding="utf-8")
    # Locate the two appended rows by ID.
    appended_lines = [
        ln for ln in text.splitlines()
        if ("INV-004" in ln or "INV-005" in ln) and ln.strip().startswith("|")
    ]
    assert len(appended_lines) == 2
    for ln in appended_lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        assert len(cells) == 6, cells
        assert "backfill" in ln.lower()
        assert "[CODE-TRACE]" in ln
    # Carries inventory severity + title.
    inv4 = next(ln for ln in appended_lines if "INV-004" in ln)
    assert "Critical" in inv4
    assert "Bug four" in inv4
    inv5 = next(ln for ln in appended_lines if "INV-005" in ln)
    assert "Medium" in inv5
    assert "Bug five" in inv5
    # parse_verification_queue_rows picks them up.
    rows = P.parse_verification_queue_rows(sp)
    ids = {r.get("finding id", "") for r in rows}
    assert "INV-004" in ids and "INV-005" in ids


def test_backfill_noop_when_complete(tmp_path):
    sp = tmp_path
    _write_inventory(
        sp,
        [
            ("INV-001", "High", "Bug one"),
            ("INV-002", "Medium", "Bug two"),
        ],
    )
    _write_queue(
        sp,
        [
            (1, "INV-001", "High", "Bug one"),
            (2, "INV-002", "Medium", "Bug two"),
        ],
    )
    before = (sp / "verification_queue.md").read_bytes()
    appended = M.backfill_unrouted_inventory_into_queue(sp)
    assert appended == []
    after = (sp / "verification_queue.md").read_bytes()
    assert before == after  # byte-unchanged


def test_backfill_noop_when_no_queue(tmp_path):
    sp = tmp_path
    _write_inventory(
        sp,
        [
            ("INV-001", "High", "Bug one"),
            ("INV-002", "Medium", "Bug two"),
        ],
    )
    # No verification_queue.md written.
    assert not (sp / "verification_queue.md").exists()
    appended = M.backfill_unrouted_inventory_into_queue(sp)
    assert appended == []  # no crash, no file created
    assert not (sp / "verification_queue.md").exists()


def test_compute_unrouted_matches_validator(tmp_path):
    sp = _make_two_missing(tmp_path)
    # The unrouted set must equal exactly what the parity validator reports.
    unrouted = set(V._compute_unrouted_inventory_ids(sp))
    issues = V._validate_verification_queue_inventory_parity(sp)
    dropout = next((i for i in issues if "dropout" in i.lower()), "")
    assert dropout, issues
    reported = set(P._FINDING_ID_EXTRACT_RE.findall(dropout.replace("_", "-")))
    reported = {r.upper() for r in reported}
    assert unrouted == {"INV-004", "INV-005"}
    assert unrouted == reported
