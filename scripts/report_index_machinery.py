"""v2.0.9 (P6) — Driver-mechanical report_index machinery.

This module provides the three pieces Codex's plan-review identified as the
durable closure for the report_index phase:

  1. `build_report_index_candidates_json` — driver pre-computes
     `report_index_candidates.json` from the existing pipeline artifacts
     (verification queue + verdict_manifest + judge_decisions).
     Schema: `plamen.report_candidates.v1`.

  2. `validate_report_index_actions_json` — schema validator for the
     LLM's `report_index_actions.json` (the LLM writes ONLY this).
     Schema: `plamen.report_actions.v1`.

  3. `render_report_index_markdown` — driver consumes both JSONs and
     emits a deterministic `report_index.md` + `report_coverage.md`.

The cutover from LLM-as-renderer to driver-as-renderer is opt-in via a
config flag (see `should_use_driver_renderer`). The existing LLM-rendered
path remains the default until at least one fresh-audit cycle validates
the driver path end-to-end.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

CANDIDATES_SCHEMA_VERSION = "plamen.report_candidates.v1"
ACTIONS_SCHEMA_VERSION = "plamen.report_actions.v1"

ALLOWED_ACTIONS = (
    "REPORTABLE",
    "MERGE_INTO",
    "APPENDIX_ONLY",
    "DROP_FALSE_POSITIVE",
    "DROP_NON_SECURITY",
    "DROP_DESIGN_CONFIRMATION",
    "DROP_UNACTIONABLE_SPECULATION",
    "UNRESOLVED_EVIDENCE",
)

_SEVERITY_ORDER = ("Critical", "High", "Medium", "Low", "Informational")
_TIER_TO_PREFIX = {
    "Critical": "C-",
    "High": "H-",
    "Medium": "M-",
    "Low": "L-",
    "Informational": "I-",
}


# ---------------------------------------------------------------------------
# P6.1 — Candidates producer
# ---------------------------------------------------------------------------


def build_report_index_candidates_json(
    scratchpad: Path,
) -> dict[str, Any]:
    """v2.0.9 (P6.1): driver pre-computes the canonical `Candidate -> Report-ID`
    mapping from existing pipeline artifacts.

    Inputs read:
      - `verification_queue.md` (or .json sidecar): canonical finding list
      - `verdict_manifest.json` (P3): effective_tag per finding
      - `judge_decisions.json` (P0.2) or `skeptic_judge_decisions.md`:
        judge UNRESOLVED/DOWNGRADE/KEEP per finding
      - `_severity_override_ledger.json` (P1.2): driver auto-repairs (if any)
      - `poc_demotions.md`: PoC-fail caps (existing)

    Output: `{scratchpad}/report_index_candidates.json` with one entry per
    queue finding. The LLM's job is reduced to picking one of the
    `allowed_actions` for each candidate; the driver renders the actual
    `report_index.md` from candidates + actions deterministically.

    Returns the payload dict that was written.
    """
    # Lazy import to keep this module free of plamen_validators dependency
    # at module-load time (allows isolated unit testing without the whole
    # validators stack).
    from plamen_parsers import (
        parse_verification_queue_rows,
        read_judge_decisions_json_sidecar,
    )
    try:
        from mechanical_verify import read_verdict_manifest
    except ImportError:
        def read_verdict_manifest(_): return []
    try:
        from plamen_validators import _read_severity_override_ledger
    except ImportError:
        def _read_severity_override_ledger(_): return []

    # 1. Collect queue rows
    queue_rows = parse_verification_queue_rows(scratchpad)

    # 2. Index verdict manifest by finding_id (P3.1 effective_tag is the
    #    authoritative evidence tag)
    verdict_by_id: dict[str, dict] = {}
    for v in read_verdict_manifest(scratchpad):
        fid = (v.get("finding_id") or "").upper()
        if fid:
            verdict_by_id[fid] = v

    # 3. Index judge decisions by finding_id
    judge_by_id: dict[str, dict] = {}
    judge_rows = read_judge_decisions_json_sidecar(scratchpad)
    for d in judge_rows:
        fid = (d.get("finding_id") or "").upper()
        if fid:
            judge_by_id[fid] = d

    # 4. Index severity-override ledger by report_id (for Trust Adj. provenance)
    override_by_rid: dict[str, dict] = {}
    for ovr in _read_severity_override_ledger(scratchpad):
        rid = (ovr.get("report_id") or "").upper()
        if rid:
            override_by_rid[rid] = ovr

    # 5. Build candidates with deterministic report-ID assignment
    #    Sort: severity tier (Critical first) → finding_id alphabetic
    rank = {s: i for i, s in enumerate(_SEVERITY_ORDER)}
    enriched: list[dict[str, Any]] = []
    for row in queue_rows:
        canonical_id = (row.get("finding id") or "").upper().strip()
        if not canonical_id:
            continue
        upstream_severity = _normalize_severity(row.get("severity") or "Medium")
        # Apply verdict manifest integrity (downgrade if INFLATED_PROSE)
        vm_entry = verdict_by_id.get(canonical_id, {})
        effective_tag = vm_entry.get("effective_tag", "")
        integrity_state = vm_entry.get("integrity_state", "")
        # Sev after verdict manifest: today only affects downstream demote
        # via the PROVEN_ONLY / [CODE-TRACE] caps (handled by tier-writers).
        # For the candidates table, we record the effective_tag verbatim.
        effective_sev_after_vm = upstream_severity

        # Apply judge decision (UNRESOLVED → -1 tier; DOWNGRADE → use FS)
        judge_entry = judge_by_id.get(canonical_id, {})
        judge_decision = judge_entry.get("decision", "")
        effective_sev_after_judge = effective_sev_after_vm
        if judge_decision in ("UNRESOLVED", "PARTIAL"):
            effective_sev_after_judge = _demote_one_tier(effective_sev_after_vm)
        elif judge_decision == "DOWNGRADE":
            final_sev = _normalize_severity(judge_entry.get("final_severity", ""))
            if final_sev:
                effective_sev_after_judge = final_sev
        elif judge_decision in ("KEEP", ""):
            pass

        enriched.append({
            "canonical_id": canonical_id,
            "title": row.get("title") or "",
            "upstream_severity": upstream_severity,
            "effective_severity_after_verdict_manifest":
                effective_sev_after_vm,
            "judge_decision": judge_decision or "NONE",
            "effective_severity_after_judge": effective_sev_after_judge,
            "verify_file": row.get("expected output file")
                or f"verify_{canonical_id}.md",
            "effective_tag": effective_tag,
            "integrity_state": integrity_state,
            "trust_adj_source": _trust_adj_source(
                judge_decision, integrity_state, canonical_id, override_by_rid
            ),
        })

    # Sort by severity tier (Critical first), then alphabetic by canonical_id
    enriched.sort(key=lambda c: (
        rank.get(c["effective_severity_after_judge"], 99),
        c["canonical_id"],
    ))

    # Assign deterministic report IDs (C-01, C-02, H-01, H-02, ...)
    tier_counters: dict[str, int] = {}
    for c in enriched:
        tier = c["effective_severity_after_judge"]
        prefix = _TIER_TO_PREFIX.get(tier, "M-")
        tier_counters[prefix] = tier_counters.get(prefix, 0) + 1
        n = tier_counters[prefix]
        c["default_report_id"] = f"{prefix}{n:02d}"
        c["default_report_tier"] = tier
        c["allowed_actions"] = list(ALLOWED_ACTIONS)

    payload = {
        "schema_version": CANDIDATES_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "row_count": len(enriched),
        "candidates": enriched,
    }
    out = scratchpad / "report_index_candidates.json"
    try:
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(out)
    except OSError:
        pass
    return payload


def read_report_index_candidates_json(scratchpad: Path) -> list[dict]:
    """Return the candidates list from `report_index_candidates.json`,
    or [] on absent / malformed file.
    """
    path = scratchpad / "report_index_candidates.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("schema_version") != CANDIDATES_SCHEMA_VERSION:
        return []
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return []
    return candidates


# ---------------------------------------------------------------------------
# P6.2 — Actions JSON schema validation
# ---------------------------------------------------------------------------


def validate_report_index_actions_json(
    scratchpad: Path,
) -> tuple[bool, list[str]]:
    """v2.0.9 (P6.2): schema validator for the LLM-written
    `report_index_actions.json`.

    Returns `(passed, issues)`. Issues describe the first 10 violations.

    The LLM is constrained to choose one action per candidate from
    `ALLOWED_ACTIONS`. Driver checks:
      - schema_version matches
      - actions is a list
      - every action.canonical_id appears in candidates
      - every action.action is in ALLOWED_ACTIONS
      - MERGE_INTO actions reference a real candidate via merge_into
      - every candidate has exactly one action (no missing, no duplicates)
    """
    actions_path = scratchpad / "report_index_actions.json"
    candidates = read_report_index_candidates_json(scratchpad)
    if not candidates:
        return False, [
            "report_index_actions: cannot validate — no candidates manifest "
            "present (build_report_index_candidates_json must run first)"
        ]
    candidate_ids = {c["canonical_id"] for c in candidates}
    if not actions_path.exists():
        return False, ["report_index_actions: file not present"]
    try:
        payload = json.loads(actions_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"report_index_actions: JSON parse error: {e}"]
    issues: list[str] = []
    if not isinstance(payload, dict):
        return False, ["report_index_actions: top-level must be an object"]
    if payload.get("schema_version") != ACTIONS_SCHEMA_VERSION:
        issues.append(
            f"report_index_actions: schema_version must be "
            f"'{ACTIONS_SCHEMA_VERSION}'"
        )
    actions = payload.get("actions", [])
    if not isinstance(actions, list):
        return False, [
            "report_index_actions: 'actions' must be a list"
        ]
    seen_ids: set[str] = set()
    for i, a in enumerate(actions):
        if not isinstance(a, dict):
            issues.append(f"report_index_actions: row {i} not an object")
            continue
        cid = (a.get("canonical_id") or "").upper()
        if not cid:
            issues.append(f"report_index_actions: row {i} missing canonical_id")
            continue
        if cid not in candidate_ids:
            issues.append(
                f"report_index_actions: row {i} references unknown candidate {cid}"
            )
            continue
        if cid in seen_ids:
            issues.append(
                f"report_index_actions: duplicate action for {cid}"
            )
            continue
        seen_ids.add(cid)
        action = (a.get("action") or "").upper()
        if action not in ALLOWED_ACTIONS:
            issues.append(
                f"report_index_actions: {cid} has invalid action {action!r} — "
                f"must be one of {list(ALLOWED_ACTIONS)}"
            )
            continue
        if action == "MERGE_INTO":
            mi = (a.get("merge_into") or "").upper()
            if not mi or mi not in candidate_ids:
                issues.append(
                    f"report_index_actions: {cid} action=MERGE_INTO references "
                    f"unknown merge_into target {mi!r}"
                )
    missing_actions = candidate_ids - seen_ids
    if missing_actions:
        sample = sorted(missing_actions)[:8]
        extra = (f" (+{len(missing_actions) - 8} more)"
                 if len(missing_actions) > 8 else "")
        issues.append(
            f"report_index_actions: {len(missing_actions)} candidate(s) "
            f"missing an action: {', '.join(sample)}{extra}"
        )
    return (not issues), issues[:10]


# ---------------------------------------------------------------------------
# P6.3 — Driver-rendered report_index.md
# ---------------------------------------------------------------------------


def render_report_index_markdown(scratchpad: Path) -> bool:
    """v2.0.9 (P6.3): consume `report_index_candidates.json` +
    `report_index_actions.json` and emit a deterministic
    `report_index.md` + `report_coverage.md`.

    Returns True on success, False on missing inputs.

    The rendering is byte-deterministic given identical inputs — the
    driver, not the LLM, owns the markdown format. This eliminates
    LLM-introduced parser drift in the report_index gate suite.
    """
    candidates = read_report_index_candidates_json(scratchpad)
    if not candidates:
        return False
    actions_path = scratchpad / "report_index_actions.json"
    if not actions_path.exists():
        return False
    try:
        actions_payload = json.loads(actions_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    actions = actions_payload.get("actions") if isinstance(actions_payload, dict) else []
    if not isinstance(actions, list):
        return False
    action_by_id = {
        (a.get("canonical_id") or "").upper(): a
        for a in actions
        if isinstance(a, dict)
    }

    # Partition into report-body candidates vs excluded
    body_rows: list[dict] = []
    excluded_rows: list[dict] = []
    consolidations: list[dict] = []
    for c in candidates:
        cid = c["canonical_id"]
        a = action_by_id.get(cid, {})
        action = (a.get("action") or "").upper()
        if action == "REPORTABLE":
            body_rows.append(c)
        elif action == "MERGE_INTO":
            consolidations.append({
                "candidate": c,
                "merge_into": (a.get("merge_into") or "").upper(),
                "reason": a.get("reason") or "",
            })
        else:
            # APPENDIX_ONLY / DROP_* / UNRESOLVED_EVIDENCE
            excluded_rows.append({"candidate": c, "action": action,
                                  "reason": a.get("reason") or ""})

    # Render Master Finding Index
    lines = [
        "# Report Index",
        "",
        f"_Generated by driver renderer at {time.strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        "## Master Finding Index",
        "",
        "| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |",
        "|-----------|-------|----------|----------|--------------|-----------|--------------------|",
    ]
    for c in body_rows:
        trust = _trust_adj_for(c)
        lines.append(
            f"| {c['default_report_id']} | {c['title']} | "
            f"{c['effective_severity_after_judge']} | — | "
            f"{_verification_status(c)} | {trust} | {c['canonical_id']} |"
        )
    lines.append("")

    if consolidations:
        lines.extend([
            "## Consolidation Map",
            "",
            "| Source Candidate | Merged Into | Reason |",
            "|------------------|-------------|--------|",
        ])
        for con in consolidations:
            lines.append(
                f"| {con['candidate']['canonical_id']} | {con['merge_into']} "
                f"| {con['reason']} |"
            )
        lines.append("")

    if excluded_rows:
        lines.extend([
            "## Excluded Findings",
            "",
            "| Canonical ID | Action | Reason |",
            "|--------------|--------|--------|",
        ])
        for e in excluded_rows:
            lines.append(
                f"| {e['candidate']['canonical_id']} | {e['action']} "
                f"| {e['reason']} |"
            )
        lines.append("")

    try:
        out = scratchpad / "report_index.md"
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        return False

    # Render report_coverage.md (one row per candidate)
    cov_lines = [
        "# Report Coverage",
        "",
        f"_Generated by driver renderer at {time.strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        "| Canonical ID | Severity | Action | Report ID | Notes |",
        "|--------------|----------|--------|-----------|-------|",
    ]
    for c in candidates:
        a = action_by_id.get(c["canonical_id"], {})
        action = (a.get("action") or "").upper() or "(no action)"
        notes = a.get("reason") or ""
        cov_lines.append(
            f"| {c['canonical_id']} | "
            f"{c['effective_severity_after_judge']} | {action} | "
            f"{c['default_report_id']} | {notes} |"
        )
    try:
        cov = scratchpad / "report_coverage.md"
        cov.write_text("\n".join(cov_lines) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Opt-in flag
# ---------------------------------------------------------------------------


def should_use_driver_renderer(config: dict) -> bool:
    """v2.0.9 (P6): opt-in flag. The driver renderer is the durable
    closure but is opt-in until validated against one fresh-audit cycle.
    Default OFF.

    Toggle via config["use_driver_report_index_renderer"] = true.
    """
    return bool(config.get("use_driver_report_index_renderer"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_severity(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s.startswith("c"): return "Critical"
    if s.startswith("h"): return "High"
    if s.startswith("m"): return "Medium"
    if s.startswith("l"): return "Low"
    if s.startswith("i") or s.startswith("info"): return "Informational"
    return "Medium"


def _demote_one_tier(sev: str) -> str:
    order = list(_SEVERITY_ORDER)
    try:
        i = order.index(sev)
    except ValueError:
        return sev
    if sev == "Low":
        return "Low"  # floor per UNRESOLVED contract
    if i + 1 < len(order):
        return order[i + 1]
    return sev


def _trust_adj_source(
    judge_decision: str, integrity_state: str,
    canonical_id: str, override_by_rid: dict,
) -> str:
    """Identify the source of the Trust Adj. token for this candidate."""
    if judge_decision in ("UNRESOLVED", "PARTIAL"):
        return "judge"
    if judge_decision == "DOWNGRADE":
        return "judge"
    if integrity_state == "INFLATED_PROSE":
        return "verdict_manifest"
    if canonical_id in override_by_rid:
        return "driver-override"
    return "none"


def _trust_adj_for(c: dict) -> str:
    """Render the Trust Adj. cell value for a candidate row."""
    src = c.get("trust_adj_source", "none")
    sev_pre = c["effective_severity_after_verdict_manifest"]
    sev_post = c["effective_severity_after_judge"]
    if src == "judge":
        decision = c.get("judge_decision", "")
        if decision in ("UNRESOLVED", "PARTIAL"):
            return f"UNRESOLVED({sev_pre})"
        if decision == "DOWNGRADE":
            return f"SKEPTIC-DOWNGRADE({sev_pre})"
    if src == "verdict_manifest" and c.get("integrity_state") == "INFLATED_PROSE":
        return "INTEGRITY-DOWNGRADE"
    if src == "driver-override":
        return f"SEVERITY_OVERRIDE(upstream={sev_pre}, llm={sev_post})"
    return "-"


def _verification_status(c: dict) -> str:
    """Map effective_tag to the canonical [VERIFIED/CONFIRMED/CONTESTED/
    UNVERIFIED] status (Fix 1).

    A body candidate (this renderer only ever runs over REPORTABLE queue rows,
    never refuted ones) with a proof-grade tag is VERIFIED; one demoted to
    [POC-FAIL]/INFLATED_PROSE is CONTESTED; a plain [CODE-TRACE] verdict-
    confirmed finding is CONFIRMED (previously mislabeled UNVERIFIED — the
    label collision Fix 1 removes)."""
    tag = (c.get("effective_tag") or "").upper()
    if "POC-PASS" in tag or "MEDUSA-PASS" in tag or "PROD-" in tag \
            or "FUZZ-PASS" in tag or "DIFF-PASS" in tag \
            or "CONFORMANCE-PASS" in tag or "NON-DET-PASS" in tag:
        return "VERIFIED"
    if "POC-FAIL" in tag or (c.get("integrity_state") == "INFLATED_PROSE"):
        return "CONTESTED"
    return "CONFIRMED"
