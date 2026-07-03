"""Tests for v2.0.7 P1 — SEVERITY_OVERRIDE de-overload of the UNRESOLVED token.

Covers:
  P1.1  _repair_report_index_severity_provenance writes SEVERITY_OVERRIDE(...)
        instead of UNRESOLVED(...) for severity-downgrade auto-repair.
  P1.2  _severity_override_ledger.json (JSON canonical source); MD as view.
  P1.3  Token vocabulary documented in rules/phase6-report-prompts.md and
        rules/report-template.md.
  P1.4  Authenticity gate splits UNRESOLVED (Skeptic-Judge backing) from
        SEVERITY_OVERRIDE (driver-ledger backing) — neither cross-validates.

The observed driver self-contradiction (one driver function wrote
UNRESOLVED that another driver function then rejected) is the canonical
case. All fixtures are synthetic; no past-audit scratchpad copies.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_validators import (  # noqa: E402
    _check_report_index_unresolved_authenticity,
    _read_severity_override_ledger,
    _repair_report_index_severity_provenance,
    _write_severity_override_ledger,
)


def _minimal_report_index(trust_adj_cells: dict[str, str]) -> str:
    """Render a minimal report_index.md with a row per (report_id, trust_adj).

    `trust_adj_cells = {"M-01": "-", "M-02": "SEVERITY_OVERRIDE(...)"}`.
    """
    rows = [
        "# Report Index",
        "",
        "## Master Finding Index",
        "",
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |",
        "|-----------|-------|----------|----------|--------------|-----------|--------------------|",
    ]
    for rid, ta in trust_adj_cells.items():
        rid_upper = rid.upper()
        # Severity follows tier convention: M-* → Medium, L-* → Low, etc.
        tier = rid_upper[:1]
        sev = {"C": "Critical", "H": "High", "M": "Medium",
               "L": "Low", "I": "Informational"}.get(tier, "Medium")
        rows.append(
            f"| {rid_upper} | A Bug | {sev} | x.sol:1 | VERIFIED | {ta} | GRP-{rid_upper[-2:]} |"
        )
    rows.append("")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# P1.2 — JSON ledger schema + atomic write
# ---------------------------------------------------------------------------


def test_p12_write_severity_override_ledger_round_trip(tmp_path):
    """Writer → reader round-trip; payload carries the documented schema."""
    repairs = [
        {"report_id": "M-01", "internal": "GRP-01",
         "llm_severity": "Medium", "upstream_severity": "Critical",
         "action": "applied SEVERITY_OVERRIDE(upstream=Critical, llm=Medium, reason=llm-downgrade-no-judge)"},
        {"report_id": "L-03", "internal": "HM-05",
         "llm_severity": "Low", "upstream_severity": "Medium",
         "action": "applied SEVERITY_OVERRIDE(upstream=Medium, llm=Low, reason=llm-downgrade-no-judge)"},
    ]
    _write_severity_override_ledger(tmp_path, repairs)
    # JSON sidecar exists with the documented schema
    json_path = tmp_path / "_severity_override_ledger.json"
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "plamen.severity_overrides.v1"
    assert payload["row_count"] == 2
    # MD view also written
    assert (tmp_path / "severity_overrides.md").exists()
    # Reader returns the override list
    overrides = _read_severity_override_ledger(tmp_path)
    assert len(overrides) == 2
    assert overrides[0]["report_id"] == "M-01"
    assert overrides[0]["upstream_severity"] == "Critical"
    assert overrides[0]["reason"] == "llm-downgrade-no-judge"


def test_p12_read_returns_empty_on_missing_or_malformed(tmp_path):
    """No file / malformed → []; consumer treats as 'no overrides registered'."""
    assert _read_severity_override_ledger(tmp_path) == []
    (tmp_path / "_severity_override_ledger.json").write_text(
        "not even json", encoding="utf-8"
    )
    assert _read_severity_override_ledger(tmp_path) == []


def test_p12_read_rejects_wrong_schema(tmp_path):
    """Wrong schema_version → [] (reader is strict about contract)."""
    payload = {
        "schema_version": "plamen.something_else.v1",
        "overrides": [{"report_id": "M-01"}],
    }
    (tmp_path / "_severity_override_ledger.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    assert _read_severity_override_ledger(tmp_path) == []


# ---------------------------------------------------------------------------
# P1.1 — repair function emits SEVERITY_OVERRIDE (not UNRESOLVED)
# ---------------------------------------------------------------------------


def test_p11_repair_function_no_longer_writes_unresolved_token(tmp_path):
    """Codex Point 3 / observed root cause: the repair function must NEVER
    write `UNRESOLVED(...)` (Skeptic-Judge-only token). It writes the
    distinct `SEVERITY_OVERRIDE(...)` instead.

    Verifies by inspecting the function source — checks the new token
    appears and the old auto-stamped token does not.
    """
    import inspect
    src = inspect.getsource(_repair_report_index_severity_provenance)
    # New token must appear at least once in the source (the auto-emit
    # call sites).
    assert "SEVERITY_OVERRIDE" in src
    # Old auto-emit pattern `f"UNRESOLVED({source})"` or
    # `f" UNRESOLVED({source}) "` must NOT appear — only literal token
    # references in docstrings are allowed.
    # Concrete pattern check: any `UNRESOLVED(<expr>)` that's part of a
    # write/format-string in the auto-repair branches.
    import re as _re
    assert not _re.search(
        r"f[\"']\s*\|?\s*UNRESOLVED\(", src
    ), "Repair function still has an f-string writing UNRESOLVED(...) — P1.1 incomplete"


# ---------------------------------------------------------------------------
# P1.4 — authenticity gate splits UNRESOLVED vs SEVERITY_OVERRIDE
# ---------------------------------------------------------------------------


def test_p14_gate_accepts_ledger_backed_severity_override(tmp_path):
    """SEVERITY_OVERRIDE in report_index + matching ledger entry → no issues."""
    token = (
        "SEVERITY_OVERRIDE(upstream=Critical, llm=Medium, "
        "reason=llm-downgrade-no-judge)"
    )
    (tmp_path / "report_index.md").write_text(
        _minimal_report_index({"M-01": token}), encoding="utf-8"
    )
    _write_severity_override_ledger(tmp_path, [{
        "report_id": "M-01", "internal": "GRP-01",
        "llm_severity": "Medium", "upstream_severity": "Critical",
        "action": f"applied {token}",
    }])
    issues = _check_report_index_unresolved_authenticity(tmp_path)
    assert issues == [], f"Ledger-backed SEVERITY_OVERRIDE should pass: {issues}"


def test_p14_gate_rejects_unbacked_severity_override(tmp_path):
    """LLM-emitted SEVERITY_OVERRIDE without ledger backing → gate fails."""
    token = (
        "SEVERITY_OVERRIDE(upstream=High, llm=Medium, reason=manual-LLM-emit)"
    )
    (tmp_path / "report_index.md").write_text(
        _minimal_report_index({"M-99": token}), encoding="utf-8"
    )
    # NO ledger file — LLM emitted the token on its own.
    issues = _check_report_index_unresolved_authenticity(tmp_path)
    assert len(issues) == 1
    assert "M-99" in issues[0]
    assert "severity-override authenticity" in issues[0]
    assert "_severity_override_ledger.json" in issues[0]


def test_p14_gate_unresolved_and_severity_override_validated_independently(tmp_path):
    """Codex Point 2/3: the two tokens are validated by independent gate
    paths. UNRESOLVED still requires Skeptic-Judge backing; SEVERITY_OVERRIDE
    still requires driver-ledger backing. A row with one valid + one missing
    must fail only for the missing one.
    """
    (tmp_path / "report_index.md").write_text(
        _minimal_report_index({
            "M-01": "UNRESOLVED(High)",       # has Skeptic-Judge backing below
            "M-02": "SEVERITY_OVERRIDE(upstream=High, llm=Medium, reason=manual)",  # NO ledger
        }),
        encoding="utf-8",
    )
    # Provide a Skeptic-Judge file backing M-01's GRP-01 as UNRESOLVED
    (tmp_path / "skeptic_judge_decisions.md").write_text(
        "| Finding ID | OS | FS | Decision | R |\n"
        "|---|---|---|---|---|\n"
        "| GRP-01 | High | Medium | UNRESOLVED | skeptic disagreed |\n",
        encoding="utf-8",
    )
    # NO _severity_override_ledger.json → M-02 must fail.
    issues = _check_report_index_unresolved_authenticity(tmp_path)
    # Exactly one issue (M-02), the UNRESOLVED M-01 is backed.
    assert len(issues) == 1, f"Expected 1 issue (M-02 only), got: {issues}"
    assert "M-02" in issues[0]
    assert "severity-override authenticity" in issues[0]
    assert "M-01" not in issues[0]


def test_p14_gate_legacy_unresolved_authenticity_still_enforced(tmp_path):
    """Non-regression: UNRESOLVED-only audits (pre-P1, no SEVERITY_OVERRIDE
    in play) still require Skeptic-Judge backing for the UNRESOLVED token.
    """
    (tmp_path / "report_index.md").write_text(
        _minimal_report_index({"M-01": "UNRESOLVED(High)"}),
        encoding="utf-8",
    )
    # NO Skeptic-Judge artifact at all.
    issues = _check_report_index_unresolved_authenticity(tmp_path)
    assert len(issues) == 1
    assert "unresolved authenticity" in issues[0]


def test_p14_gate_clean_index_no_issues(tmp_path):
    """Bare-Trust-Adj rows (no UNRESOLVED, no SEVERITY_OVERRIDE) → 0 issues."""
    (tmp_path / "report_index.md").write_text(
        _minimal_report_index({"M-01": "-", "L-01": "-"}),
        encoding="utf-8",
    )
    issues = _check_report_index_unresolved_authenticity(tmp_path)
    assert issues == []


# ---------------------------------------------------------------------------
# P1.3 — rule documentation
# ---------------------------------------------------------------------------


def test_p13_severity_override_documented_in_phase6_rules():
    """The token vocabulary documentation lists SEVERITY_OVERRIDE."""
    text = (
        SCRIPTS_DIR.parent / "rules" / "phase6-report-prompts.md"
    ).read_text(encoding="utf-8")
    assert "SEVERITY_OVERRIDE" in text
    assert "DRIVER-ONLY" in text or "driver-only" in text.lower()


def test_p13_severity_override_documented_in_report_template():
    """The report-template lists SEVERITY_OVERRIDE alongside UNRESOLVED."""
    text = (
        SCRIPTS_DIR.parent / "rules" / "report-template.md"
    ).read_text(encoding="utf-8")
    assert "SEVERITY_OVERRIDE" in text
    assert "_severity_override_ledger.json" in text
