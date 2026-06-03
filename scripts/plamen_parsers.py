from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from plamen_types import (
    Phase, Checkpoint, SC_PHASES, L1_PHASES, log,
    L1_VERIFY_SHARD_MANIFESTS, L1_VERIFY_PHASE_NAMES,
    L1_VERIFY_CRITHIGH_PHASE_NAMES,
    SC_VERIFY_SHARD_MANIFESTS, SC_VERIFY_PHASE_NAMES,
    SC_VERIFY_CRITHIGH_PHASE_NAMES,
    _VALID_PIPELINES, _VALID_MODES,
    EVIDENCE_TAGS_PROOF, EVIDENCE_TAG_DEFAULT, EVIDENCE_TAG_NAMES_RE,
    DEPTH_EVIDENCE_TAG_RE, FINDING_BLOCK_HEADING_RE,
    SEVERITY_ORDER, SEVERITY_LETTER, SEVERITY_FROM_LETTER,
    has_mechanical_proof, normalize_severity, severity_letter_from_name,
    severity_rank,
)

__all__ = [
    "DedupSignature",
    "_manifest_row_from_cells",
    "_manifest_row_is_spawned_breadth_agent",
    "_normalize_manifest_header",
    "_split_markdown_table_row",
    "_BODY_REPORT_ID_RE",
    "_BODY_SHARD_CAPS",
    "_BRACKETED_ID_RE",
    "_CLASS_LEVEL_TITLES",
    "_CLIENT_BODY_INTERNAL_ID_RE",
    "_COVERED_STATUS_TOKENS",
    "_DEDUP_FIX_VOCAB",
    "_DEDUP_GENERIC_STOP",
    "_DEDUP_VULN_VOCAB",
    "_DEGRADED_SENTINEL_GLOBS",
    "_DEPTH_EVIDENCE_TAG_RE",
    "_DEPTH_PROMOTION_FILES",
    "_FID_BARE_RE",
    "_FID_RANGE_RE",
    "_FINDING_BLOCK_RE",
    "_FINDING_GLOBS_FOR_CITATION",
    "_FINDING_ID_EXTRACT_RE",
    "_ID_ALL_INTERNAL",
    "_ID_ALL_NONHYPO",
    "_ID_DEPTH_ALTS",
    "_ID_HYPO_ALTS",
    "_ID_NICHE_ALTS",
    "_ID_TOOL_ALTS",
    "_parse_skeptic_judge_table",
    "read_judge_decisions_json_sidecar",
    "write_judge_decisions_json_sidecar",
    "_ID_LEDGER_NAME",
    "_ID_LEDGER_SCHEMA_VERSION",
    "_id_ledger_load",
    "_id_ledger_save",
    "_id_prefix_of",
    "_title_hash",
    "id_ledger_register",
    "id_ledger_next_available",
    "id_ledger_lookup",
    "id_ledger_all_for_prefix",
    "id_ledger_all_records",
    "_HEADING_FINDING_RE",
    "_HTML_ENTITY_MAP",
    "_HTML_ENTITY_RE",
    "_INVENTORY_SOURCE_PATTERNS",
    "_INTERNAL_FINDING_ID_RE",
    "_INTERNAL_ID_RE",
    "_INVENTORY_FINDING_HEADING_RE",
    "_LLM_NORM_TABLE",
    "_LOCATION_RE",
    "_MATRIX_IMPACT_LABELS",
    "_MATRIX_IMPACT_RE",
    "_MATRIX_LIKELIHOOD_LABELS",
    "_MATRIX_LIKELIHOOD_RE",
    "_MATRIX_ONCHAIN_RE",
    "_MATRIX_TRUST_FULLY_RE",
    "_MATRIX_VIEW_FN_RE",
    "_NOTREAD_FINDING_GLOBS",
    "_PATH_CELL_EXTENSIONS",
    "_PROMOTABLE_FEEDER_ID_PATTERN",
    "_QUEUE_HEADER_ALIASES",
    "_REPORT_BULLET_RE",
    "_SCIP_REPO_MAP_FILES",
    "_SCOPE_LEFTOVER_LIB_WHITELIST",
    "_SEVERITY_CODE",
    "_SEVERITY_ORDER",
    "_SEVERITY_RE",
    "_SKEPTIC_DOWNGRADE_RE",
    "_SOURCE_IDS_LINE_RE",
    "_STEP_TRACE_GLOB",
    "_TABLE_FINDING_ID_RE",
    "_TABLE_LOCATION_RE",
    "_TABLE_SOURCE_ID_RE",
    "_TOTAL_FINDINGS_RE",
    "_UNCOVERED_STATUS_TOKENS",
    "_VERIFY_CONFIRMED_VERDICT_RE",
    "_aggregate_step_execution_gaps",
    "_apply_severity_modifiers",
    "_classify_keyword",
    "_compute_matrix_severity",
    "_consolidated_title_for",
    "_count_markdown_table_rows",
    "_dedup_generic_norm",
    "_dedup_queue_by_hypothesis",
    "_dedup_signature_for_finding",
    "_demote_severity_once",
    "_detect_dedup_clusters",
    "_artifact_has_findings",
    "_enforce_severity_matrix",
    "_expected_depth_agent_roles",
    "_extract_artifact_status",
    "_findings_section_has_body",
    "_strip_fenced_code_blocks",
    "_extract_finding_ids_from_text",
    "_extract_finding_signals",
    "_extract_first_tag",
    "_extract_gap_paths_from_markdown",
    "_extract_verifier_severity_with_adjustment",
    "_extract_h2_section",
    "_extract_ids_from_text",
    "_extract_report_ids_from_body",
    "_extract_severity_inputs",
    "_field_from_markdown",
    "_field_or_section",
    "_find_report_index_cut_for_active_recovery",
    "_first_heading_title",
    "_inventory_blocks",
    "_is_path_cell",
    "_is_reportable_verdict",
    "_is_separator_row",
    "_is_whitelisted_lib_path",
    "_line_count",
    "_llm_norm",
    "_markdown_section",
    "_match_canonical_header",
    "_merge_inventory_entries",
    "_compute_dedup_candidate_pairs",
    "_extract_chain_summaries_compact",
    "_chain_iter2_has_no_unexplored_pairs",
    "_line_ranges_overlap",
    "_parse_line_range",
    "_shared_anchor_tokens",
    "_titles_overlap_score",
    "_module_key",
    "_next_report_id_counters",
    "_norm_key",
    "_norm_loc",
    "_normalize_finding_id",
    "_normalize_matrix_label",
    "_normalize_report_id",
    "_normalize_subsystem_scope",
    "_load_scope_file_paths",
    "_path_in_scope_file",
    "_parse_chunk_heading_inventory",
    "_parse_chunk_table_inventory",
    "_parse_hypothesis_constituents",
    "_parse_depth_confidence_scores",
    "_parse_depth_finding_blocks",
    "_parse_inventory_chunk",
    "_parse_location_ref",
    "_parse_markdown_table",
    "_parse_notread_files",
    "_parse_report_index_bullets",
    "_parse_report_index_summary_counts",
    "_parse_report_index_table",
    "_report_index_assignment_text",
    "_parse_source_findings_for_ids",
    "_parse_step_trace_rows",
    "_parse_uncovered_from_ledger",
    "_path_in_subsystem_scope",
    "_phase_name_from_sentinel",
    "_project_source_index",
    "_queue_rows_from_inventory",
    "_replace_inventory_location",
    "_report_index_reportable_text",
    "_report_prefix_for_severity",
    "_resolve_inventory_location",
    "_sanitize_client_body",
    "_sanitize_client_title",
    "_sc_contract_module_key",
    "_section_for_report_id",
    "_severity_bucket",
    "_severity_name_from_text",
    "_severity_rank",
    "_split_source_id_tokens",
    "_strip_md",
    "_structural_completeness_ok",
    "_validate_source_token",
    "_verifier_status_from_text",
    "_verify_file_for_id",
    "_write_mechanical_verification_queue_from_inventory",
    "_filter_verification_queue_by_mode",
    "_filter_sc_verification_queue_by_mode",
    "_write_queue_json_sidecar",
    "_read_queue_json_sidecar",
    "_write_queue_excluded_manifest",
    "_write_queue_subset_manifest",
    "_queue_rows_from_inventory_with_exclusions",
    "compute_report_medium_shards",  # backward compat wrapper
    "compute_report_tier_shards",
    "classify_poc_testability",
    "compute_sc_verify_shards",
    "compute_verify_shards",
    "derive_tier_assignments_from_verify_queue",
    "ensure_report_medium_shards",  # backward compat wrapper
    "ensure_report_tier_shards",
    "ensure_sc_verify_shard_manifests",
    "ensure_verify_shard_manifests",
    "get_tier_assignments",
    "is_artifact_complete",
    "is_artifact_legacy_unmarked",
    "merge_report_medium_shards",  # backward compat wrapper
    "merge_report_tier_shards",
    "parse_breadth_manifest_agents",
    "parse_breadth_manifest_count",
    "parse_breadth_manifest_outputs",
    "parse_depth_manifest_count",
    "parse_inventory_shard_manifest",
    "parse_report_index_assignments",
    "parse_report_index_counts",
    "parse_verification_queue_rows",
]


# ── Unified internal-ID prefix components (v2.4.3) ──────────────────────────
# Single source of truth for ALL internal finding ID regexes. Each consumer
# combines the subsets it needs. Adding a new agent prefix means ONE edit here.

# Depth agent structural IDs (produced by depth-*, scanners, niche agents)
_ID_DEPTH_ALTS = (
    r"DEPTH-[A-Z]+-\d+|DEPTH-CI-\d+|DEPTH-NS-\d+|DEPTH-ST-\d+|DEPTH-EC-\d+|"
    r"DEPTH-DA[0-9]*-\d+|"
    r"BLIND-\d+|VS-\d+|EN-\d+|SE-\d+|"
    r"INV-\d+|DCI-\d+|DEC-\d+|DX-\d+|DN-\d+|DNS-\d+|"
    r"DA-[A-Z0-9_-]+-\d+|DA\d+-[A-Z0-9_-]+-\d+|DCOV\d*-\d+|"
    r"DST-(?:[A-Z0-9_-]+-)?\d+|PERT-\d+|PAIR-\d+|ATT-\d+|"
    r"PANIC(?:-EXPLOIT)?-\d+"
)

# Tool feeder IDs (Slither, fuzzer, scanner, sibling propagation)
_ID_TOOL_ALTS = r"SLITHER-\d+|FUZZ-\d+|MEDUSA-\d+|RSW-\d+|SP-\d+"

# Niche/injectable skill prefixes — 2-4 letter codes
_ID_NICHE_ALTS = (
    r"(?:AA|AB|AC|AL|AR|AV|BLS|BS|CBS|CCT|CFG|CI|CM|CMI|CPI|CR|CS|CT|CU|"
    r"DEP|DEX|ED|EDA|EN|EP|EPA|EVT|EX|FA|FC|FL|GO|GOV|HF|IHR|II|LC|"
    r"LEND|MG|MP|MSS|NFT|NS|OD|OF|OO|OR|P2P|PDA|PSC|PTB|PV|RE|REENT|REF|"
    r"RPC|RS|SA|SAF|SCOUT|SE|SGI|SHIFT|SIG|SL|SLS|SR|SS|SSC|ST|STATIC|STR|"
    r"T22|TF|TPS|TS|TXI|VA|VL|VS|WED|XE|XFER|ZS)-\d+"
)

# Hypothesis/chain/structural IDs (used in report index mapping).
# F1 (post-DODO hardening): the SC chain phase emits grouped-by-severity
# hypothesis IDs `HC-NN` (Critical), `HH-NN` (High), `HM-NN` (Medium),
# `HL-NN` (Low), `HI-NN` (Informational), plus multi-finding-group `GRP-NN`.
# Without these, `_normalize_finding_id` returns "" for every grouped queue
# row, `_validate_verification_queue_inventory_parity` drops them before
# constituent expansion, and 70%+ of inventory IDs appear "missing" at
# sc_verify_queue. See ~/.plamen/rules/phase4c-chain-prompt.md for the
# documented taxonomy.
_ID_HYPO_ALTS = (
    r"H-[CHMLI]?\d+|CH-\d+|L1-[CHMLI]-\d+|CC-\d+|F-\d+|[CHMLI]-\d{1,3}"
    r"|GRP-\d+|H[CHMLI]-\d+"
)

# Convenience: all internal IDs (depth + tool + niche + hypothesis)
_ID_ALL_INTERNAL = "|".join([
    _ID_DEPTH_ALTS, _ID_TOOL_ALTS, _ID_NICHE_ALTS, _ID_HYPO_ALTS,
])

# All unambiguously-internal IDs for client-body sanitization. Excludes
# bare [CHMLI]-\d{1,3} (report IDs) and H-\d+ / CH-\d+ (overlap with
# report IDs in SC). Only strips IDs that a report reader should never see.
_ID_ALL_NONHYPO = "|".join([
    _ID_DEPTH_ALTS, _ID_TOOL_ALTS, _ID_NICHE_ALTS,
    # L1-prefixed hypothesis IDs are never client-facing
    r"L1-[CHMLI]-\d+|CC-\d+|F-\d+",
])


_INVENTORY_SOURCE_PATTERNS: tuple[str, ...] = (
    "analysis_*.md",
    "analysis_rescan_*.md",
    "analysis_percontract_*.md",
    # L1 graph-sweep outputs are breadth-equivalent discovery artifacts and
    # run before inventory in thorough mode.
    "graph_sweep*.md",
    "coverage_fill_*.md",
    "panic_audit_*.md",
    "panic_audit_summary.md",
    "symmetric_pair_findings.md",
    "field_validation_matrix.md",
    "primitive_correctness_findings.md",
    "network_amplification_findings.md",
    "lifecycle_replay_findings.md",
)


# Derived allow-list for _extract_finding_ids_from_text (v2.4.9).
# Built from unified source components so a new prefix needs ONE edit above.
_FID_ALLOWED_PREFIXES: frozenset = frozenset({
    "INV", "C", "H", "M", "L", "I", "F", "CC", "CH", "L1",
    "DEPTH", "BLIND", "VS", "EN", "SE",
    "DCI", "DEC", "DX", "DN", "DNS", "DA",
    "DCOV", "DST", "DT", "DS", "DCG", "DPI", "PERT", "PAIR", "ATT", "PANIC",
    "TF", "EC", "ST", "NS", "CI",
    "SLITHER", "FUZZ", "MEDUSA", "RSW", "SP", "SCANNER",
}) | frozenset(re.findall(r"[A-Z][A-Z0-9]+", _ID_NICHE_ALTS))

_SEVERITY_RE = re.compile(
    r"(?im)"
    r"(?:"
    # Markdown finding format: `**Severity**: Medium` or `Severity: Medium`
    r"^\s*(?:[*_-]+\s*)?severity(?:[*_]+)?\s*:\s*(critical|high|medium)\b"
    r"|"
    # Table row containing a severity token as its own cell: `| Medium |`
    r"\|\s*(critical|high|medium)\s*\|"
    r")"
)


_SEPARATOR_ROW_RE = re.compile(
    r"^\|[\s:|-]+\|$"
)


def _is_separator_row(s: str) -> bool:
    """Return True if *s* is a markdown table separator row.

    A separator row consists ONLY of pipes, hyphens, colons, and whitespace
    (e.g., `|---|---|---|`). This is stricter than the old heuristic which
    checked `"---" in s` (false-positive on data containing triple hyphens)
    or stripped pipes/colons/hyphens then tested empty (false-positive on
    data cells that happen to be single hyphens like `| - | - |`).
    """
    return bool(_SEPARATOR_ROW_RE.match(s.strip()))


def _breadth_roster_text(text: str) -> str:
    """Ship B: bound breadth-manifest parsing to the `## Breadth Agents`
    section so a later table (e.g. `## Required Template Coverage`, whose header
    also matches the template+required heuristic) cannot bleed into the roster.
    This was the DODO instantiate HALT (count=16/outputs=None on a VALID
    manifest). Uses section-scoped Markdown AST (plamen_markdown.section_text);
    falls back to the full text when no `## Breadth Agents` heading exists
    (legacy manifests that put the roster table directly under `# Spawn
    Manifest`)."""
    try:
        import plamen_markdown as _mdast
        sect = _mdast.section_text(text, r"\bbreadth\s+agents?\b")
        return sect if sect.strip() else text
    except Exception:
        return text


def parse_breadth_manifest_count(scratchpad: Path) -> Optional[int]:
    """Return the number of breadth agents declared in spawn_manifest.md.

    The manifest is written by the Phase 2 instantiate LLM as a markdown
    table: `| Template | Required? | Agent ID | Focus Area | Status |`.
    Each data row is one agent the orchestrator intends to spawn.

    Returning this count lets the breadth gate use an EXACT quorum
    (equal to the expected-agent count) instead of a fixed floor of 3 —
    closing the residual hole where a Thorough audit spawning 6-9 breadth
    agents could false-pass with only 3 `analysis_*.md` files written.

    Returns None if the manifest is missing, unreadable, or contains no
    data rows — caller falls back to the hardcoded `min_artifacts_count`.
    """
    p = scratchpad / "spawn_manifest.md"
    if not p.exists():
        return None
    try:
        text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    text = _breadth_roster_text(text)  # Ship B: kill cross-section bleed
    count = 0
    in_table = False
    headers: list[str] = []
    seen_agent_ids: set[str] = set()
    for raw in text.splitlines():
        s = raw.strip()
        if not in_table:
            s_lc = s.lower()
            if s.startswith("|") and "template" in s_lc and "required" in s_lc:
                headers = [_normalize_manifest_header(c) for c in _split_markdown_table_row(s)]
                in_table = True
            continue
        if not s.startswith("|"):
            in_table = False
            headers = []
            continue
        if _is_separator_row(s):
            continue
        cells = _split_markdown_table_row(s)
        row = _manifest_row_from_cells(headers, cells)
        req = row.get("required") or row.get("required_") or row.get("required?") or (
            cells[1].strip() if len(cells) > 1 else ""
        )
        if req and re.match(r"(?i)^(?:no|n|false|skip|optional|merged)\b", req):
            continue
        if not _manifest_row_is_spawned_breadth_agent(row, cells):
            continue
        explicit_output = any(str(row.get(k, "")).strip() for k in (
            "output", "output_file", "filename", "file", "artifact",
            "expected_output", "expected_file",
        ))
        agent_key = _strip_md(row.get("agent_id", "") or row.get("agent", "")).lower()
        if agent_key and not explicit_output:
            if agent_key in seen_agent_ids:
                continue
            seen_agent_ids.add(agent_key)
        count += 1
    return count if count > 0 else None


def _split_markdown_table_row(row: str) -> list[str]:
    cells = row.strip().strip("|").split("|")
    return [_strip_md(c).strip() for c in cells]


def _normalize_manifest_header(header: str) -> str:
    header = _strip_md(header).lower()
    return re.sub(r"[^a-z0-9]+", "_", header).strip("_")


def _manifest_row_from_cells(headers: list[str], cells: list[str]) -> dict[str, str]:
    return {
        headers[i]: cells[i].strip()
        for i in range(min(len(headers), len(cells)))
    }


def _manifest_row_is_merged_or_non_output(row: dict[str, str], cells: list[str]) -> bool:
    """Return True for roster rows intentionally covered by another agent."""
    joined = " ".join(cells + list(row.values()))
    joined = re.sub(r"\s+", " ", _strip_md(joined).lower()).strip()
    if re.search(r"\bmerged\s+(?:into|with)\s+[a-z]?\d+\b", joined):
        return True
    if re.search(r"\bcovered\s+by\s+[a-z]?\d+\b", joined):
        return True
    if re.search(r"\babsorbed\s+(?:into|by)\s+[a-z]?\d+\b", joined):
        return True
    row_type = " ".join(
        str(row.get(k, ""))
        for k in (
            "type", "kind", "row_type", "category", "section", "role",
            "agent_type", "spawn_type",
        )
    )
    row_type = re.sub(r"\s+", " ", _strip_md(row_type).lower()).strip()
    if re.search(r"\b(?:skill|injectable|template|methodology|checklist|binding)\b", row_type):
        return True
    status = " ".join(
        str(row.get(k, ""))
        for k in ("status", "spawn_status", "assignment", "agent_id")
    )
    status = re.sub(r"\s+", " ", _strip_md(status).lower()).strip()
    if re.search(
        r"\b(?:inject(?:ed|ion|able)?|attached|append(?:ed)?|inherited|"
        r"methodology|skill(?:\s+only)?|not\s+spawned|no\s+separate\s+agent)\b",
        status,
    ):
        return True
    return False


def _manifest_row_is_spawned_breadth_agent(row: dict[str, str], cells: list[str]) -> bool:
    """Return True for rows that represent a breadth agent with its own output.

    `spawn_manifest.md` has drifted from a pure agent roster into a mixed
    manifest that can include required skills/injectables. Skills are binding
    methodology for a spawned agent, not standalone producers of
    `analysis_*.md`. Treat only explicit agent rows as manifest-exact output
    contracts; otherwise the gate demands files like
    `analysis_oracle_analysis.md` for a skill that was intentionally injected
    into another breadth prompt.
    """
    if _manifest_row_is_merged_or_non_output(row, cells):
        return False
    output_keys = (
        "output", "output_file", "filename", "file", "artifact",
        "expected_output", "expected_file",
    )
    explicit_outputs = [
        Path(_strip_md(str(row.get(k, ""))).strip()).name
        for k in output_keys
        if str(row.get(k, "")).strip()
    ]
    if explicit_outputs:
        return any(_is_breadth_analysis_output(name) for name in explicit_outputs)
    joined = " ".join(cells + list(row.values()))
    joined = re.sub(r"\s+", " ", _strip_md(joined).lower()).strip()
    if re.search(r"\b(?:skill|injectable|template|methodology)\b", joined) and not re.search(
        r"\b(?:agent|analysis[_ -]agent|breadth[_ -]agent|spawn(?:ed)?)\b",
        joined,
    ):
        return False
    agent_id = _strip_md(row.get("agent_id", "") or row.get("agent", "") or "")
    if re.fullmatch(r"(?i)(?:b|ba|breadth|agent)[-_ ]?\d+[a-z]?", agent_id):
        return True
    status = _strip_md(row.get("status", "") or row.get("spawn_status", "") or "").lower()
    if agent_id and re.search(r"\bspawn(?:ed)?\b|\bagent\b|\bpending\b|\bassigned\b", status):
        return True
    return False


def _is_breadth_analysis_output(filename: str) -> bool:
    """True only for files the breadth phase owns."""
    name = Path(_strip_md(filename or "").strip()).name
    reserved_prefixes = (
        "analysis_rescan_",
        "analysis_percontract_",
        "analysis_merged_into_",
        "analysis_report_",
    )
    if any(name.startswith(prefix) for prefix in reserved_prefixes):
        return False
    return bool(re.fullmatch(r"analysis_[A-Za-z0-9][A-Za-z0-9_.-]*\.md", name))


def _manifest_row_is_spawned_depth_agent(row: dict[str, str], cells: list[str]) -> bool:
    """Return True for depth manifest rows that create depth finding files.

    Depth manifests can carry supporting rows for skills, injectables,
    methodology attachments, or merged coverage. Those rows are not producers
    of `depth_*_findings.md` and must not inflate the phase's depth quorum.
    """
    if _manifest_row_is_merged_or_non_output(row, cells):
        return False
    joined = " ".join(cells + list(row.values()))
    joined = re.sub(r"\s+", " ", _strip_md(joined).lower()).strip()
    if re.search(r"\b(?:skill|injectable|template|methodology|checklist|binding)\b", joined) and not re.search(
        r"\b(?:agent|subagent|spawn(?:ed)?|depth[-_ ]agent)\b",
        joined,
    ):
        return False
    status = _strip_md(row.get("status", "") or row.get("spawn_status", "") or "").lower()
    if re.search(
        r"\b(?:inject(?:ed|ion|able)?|attached|append(?:ed)?|inherited|"
        r"methodology|skill(?:\s+only)?|not\s+spawned|no\s+separate\s+agent)\b",
        status,
    ):
        return False
    artifact = (
        row.get("expected_artifact")
        or row.get("output_file")
        or row.get("output")
        or row.get("artifact")
        or row.get("filename")
        or row.get("file")
        or ""
    )
    artifact_name = Path(_strip_md(artifact)).name
    if artifact_name:
        return bool(re.fullmatch(r"depth_[A-Za-z0-9_]+_findings\.md", artifact_name))
    agent_id = _strip_md(
        row.get("agent_id", "")
        or row.get("agent", "")
        or row.get("subagent", "")
        or row.get("role", "")
    ).lower()
    return bool(re.search(r"\bdepth[-_ ](?:token|state|edge|external|consensus|network)", agent_id))


def _slug_to_analysis_filename(value: str) -> Optional[str]:
    value = _strip_md(value).strip()
    if not value:
        return None
    explicit = re.search(r"\b([A-Za-z0-9_.-]+\.md)\b", value)
    if explicit:
        return Path(explicit.group(1)).name
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug:
        return None
    if slug.startswith("analysis_"):
        return f"{slug}.md"
    return f"analysis_{slug}.md"


def parse_breadth_manifest_outputs(scratchpad: Path) -> Optional[list[str]]:
    """Return manifest-derived breadth output filenames.

    The breadth phase is manifest-exact: the subprocess must produce the
    output file for every required row in `spawn_manifest.md`, not merely any
    N files matching `analysis_*.md`. This parser accepts the documented
    table shape and a small set of explicit output-column aliases.

    Returns None when the manifest is absent or the table cannot be parsed
    completely enough to derive one output per row. Callers may then fall back
    to the older count-based quorum.
    """
    p = scratchpad / "spawn_manifest.md"
    if not p.exists():
        return None
    try:
        text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    text = _breadth_roster_text(text)  # Ship B: kill cross-section bleed

    headers: list[str] = []
    outputs: list[str] = []
    seen: set[str] = set()
    in_table = False
    row_count = 0
    seen_agent_ids: set[str] = set()

    for raw in text.splitlines():
        s = raw.strip()
        if not in_table:
            s_lc = s.lower()
            if s.startswith("|") and "template" in s_lc and "required" in s_lc:
                headers = [_normalize_manifest_header(c) for c in _split_markdown_table_row(s)]
                in_table = True
            continue
        if not s.startswith("|"):
            in_table = False
            headers = []
            continue
        if _is_separator_row(s):
            continue
        cells = _split_markdown_table_row(s)
        if not cells:
            continue
        row_count += 1
        row = _manifest_row_from_cells(headers, cells)
        req = row.get("required") or row.get("required_") or row.get("required?")
        if req and re.match(r"(?i)^(?:no|n|false|skip|optional|merged)\b", req.strip()):
            continue
        if not _manifest_row_is_spawned_breadth_agent(row, cells):
            continue

        filename = None
        explicit_output = False
        for key in (
            "output",
            "output_file",
            "filename",
            "file",
            "artifact",
            "expected_output",
            "expected_file",
        ):
            if row.get(key):
                explicit_output = True
                filename = _slug_to_analysis_filename(row[key])
                if filename:
                    break
        agent_key = _strip_md(row.get("agent_id", "") or row.get("agent", "")).lower()
        if agent_key and not explicit_output:
            if agent_key in seen_agent_ids:
                continue
            seen_agent_ids.add(agent_key)
        if not filename:
            for key in ("focus_area", "focus", "agent_id", "template"):
                if row.get(key):
                    filename = _slug_to_analysis_filename(row[key])
                    if filename:
                        break
        if not filename:
            return None
        if not _is_breadth_analysis_output(filename):
            continue
        if filename not in seen:
            seen.add(filename)
            outputs.append(filename)

    if row_count <= 0 or not outputs:
        return None
    return outputs


def parse_breadth_manifest_agents(scratchpad: Path) -> list[dict[str, str]]:
    """Parse spawn_manifest.md and return spawned breadth agent rows
    as ``[{"agent_id", "focus_area"}, ...]``. Empty when the manifest
    is absent or has no spawned-agent rows.

    Ship 7 of the artifact-complete PTY supervision plan. Consumed by
    ``plamen_driver.shard_opengrep_obligations`` to derive per-agent
    obligation shard filenames.

    Walks the same manifest table that ``parse_breadth_manifest_outputs``
    walks (template / skill / merged-into rows are excluded by
    ``_manifest_row_is_spawned_breadth_agent``); extracts the
    ``agent_id`` and ``focus_area`` columns rather than the output
    filename. Deduplicates by case-insensitive agent_id so two manifest
    rows with the same agent never produce a redundant shard.

    Fills in defaults when only one column is present (agent_id only
    -> focus_area = agent_id, or vice versa), so the sharder always
    has both fields to slugify.
    """
    p = scratchpad / "spawn_manifest.md"
    if not p.exists():
        return []
    try:
        text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []

    out: list[dict[str, str]] = []
    headers: list[str] = []
    in_table = False
    seen_agent_ids: set[str] = set()

    for raw in text.splitlines():
        s = raw.strip()
        if not in_table:
            s_lc = s.lower()
            if s.startswith("|") and "template" in s_lc and "required" in s_lc:
                headers = [
                    _normalize_manifest_header(c)
                    for c in _split_markdown_table_row(s)
                ]
                in_table = True
            continue
        if not s.startswith("|"):
            in_table = False
            headers = []
            continue
        if _is_separator_row(s):
            continue
        cells = _split_markdown_table_row(s)
        if not cells:
            continue
        row = _manifest_row_from_cells(headers, cells)
        req = (
            row.get("required")
            or row.get("required_")
            or row.get("required?")
        )
        if req and re.match(
            r"(?i)^(?:no|n|false|skip|optional|merged)\b", req.strip()
        ):
            continue
        if not _manifest_row_is_spawned_breadth_agent(row, cells):
            continue
        agent_id = _strip_md(
            row.get("agent_id", "") or row.get("agent", "")
        ).strip()
        focus_area = _strip_md(
            row.get("focus_area", "") or row.get("focus", "")
        ).strip()
        if not agent_id and not focus_area:
            continue
        if not agent_id:
            agent_id = focus_area
        if not focus_area:
            focus_area = agent_id
        key = agent_id.lower()
        if key in seen_agent_ids:
            continue
        seen_agent_ids.add(key)
        out.append({"agent_id": agent_id, "focus_area": focus_area})
    return out


def _count_markdown_table_rows(text: str,
                               header_predicate,
                               row_skip_predicate=None) -> Optional[int]:
    """Return the number of data rows in the first matching markdown table.

    header_predicate receives the stripped header row and decides whether the
    table is the one we want. Returns None when no matching table is found.
    """
    count = 0
    in_table = False
    saw_matching_header = False
    for raw in text.splitlines():
        s = raw.strip()
        if not in_table:
            if s.startswith("|") and header_predicate(s):
                in_table = True
                saw_matching_header = True
            continue
        if not s.startswith("|"):
            break
        if _is_separator_row(s):
            continue
        cells = _split_markdown_table_row(s)
        if row_skip_predicate and row_skip_predicate(s, cells):
            continue
        count += 1
    if not saw_matching_header:
        return None
    return count if count > 0 else None


def parse_depth_manifest_count(scratchpad: Path) -> Optional[int]:
    """Return the number of declared depth-loop agents, if a manifest exists.

    Preferred source is `phase4b_manifest.md` written by the L1 depth loop.
    As a loose fallback, inspect `spawn_manifest.md` and count rows that look
    depth/post-depth specific. Returns None when no parseable manifest exists.
    """
    candidates = [
        scratchpad / "phase4b_manifest.md",
        scratchpad / "spawn_manifest.md",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if p.name == "phase4b_manifest.md":
            count = 0
            in_table = False
            headers: list[str] = []
            for raw in text.splitlines():
                s = raw.strip()
                if not in_table:
                    s_lc = s.lower()
                    if s.startswith("|") and "agent" in s_lc and (
                        "role" in s_lc or "model" in s_lc or "artifact" in s_lc
                    ):
                        headers = [_normalize_manifest_header(c) for c in _split_markdown_table_row(s)]
                        in_table = True
                    continue
                if not s.startswith("|"):
                    break
                if _is_separator_row(s):
                    continue
                cells = _split_markdown_table_row(s)
                row = _manifest_row_from_cells(headers, cells)
                req = row.get("required") or row.get("required_") or row.get("required?") or row.get("status", "")
                if req and re.match(r"(?i)^(?:no|n|false|skip|optional|merged|covered)\b", req.strip()):
                    continue
                if not _manifest_row_is_spawned_depth_agent(row, cells):
                    continue
                count += 1
            if count > 0:
                return count
            continue

        lowered_lines = text.splitlines()
        count = 0
        in_table = False
        headers: list[str] = []
        for raw in lowered_lines:
            s = raw.strip()
            if not in_table:
                s_lc = s.lower()
                if s.startswith("|") and "template" in s_lc and "required" in s_lc:
                    headers = [_normalize_manifest_header(c) for c in _split_markdown_table_row(s)]
                    in_table = True
                continue
            if not s.startswith("|"):
                break
            if _is_separator_row(s):
                continue
            cells = _split_markdown_table_row(s)
            row = _manifest_row_from_cells(headers, cells)
            req = row.get("required") or row.get("required_") or row.get("required?") or (
                cells[1].strip() if len(cells) > 1 else ""
            )
            if req and re.match(r"(?i)^(?:no|n|false|skip|optional|merged)\b", req):
                continue
            if not _manifest_row_is_spawned_depth_agent(row, cells):
                continue
            count += 1
        if count > 0:
            return count
    return None


def parse_report_index_counts(scratchpad: Path) -> dict[str, int]:
    """Return report-tier counts inferred from report_index.md.

    v2.1.7 — INVERSION FIX of v2.1.6.

    v2.1.6's positive-scoping approach (find `## Master Finding Index`
    section, count IDs only inside it) was over-aggressive: when L1
    `report_index.md` used a different section heading (e.g., `## Promoted
    Findings`) or a multi-section structure (master table + tier-assignment
    subsections), the regex returned partial or zero counts, causing
    `report_medium_a/b` and `report_low_info` to silently write 0-finding
    placeholders. Real bug → silently lost ~25 findings from the L1 run.

    v2.1.7 inverts the strategy: count IDs in the WHOLE FILE except the
    Appendix / Excluded Findings / Internal Audit Traceability section.
    Appendix-cut is more robust than master-section-find because:
    - The appendix has consistent canonical names (Appendix A/B, Excluded
      Findings, Internal Audit Traceability, Hypothesis Traceability).
    - Body content can be in many shapes (single Master table, severity-
      grouped subsections, tier-assignment lists, mixed) — counting all
      first-column IDs in the body is correct regardless of layout.

    The AwesomeX double-count bug (Appendix A internal hypothesis IDs
    leaking into the count) is still fixed because the cut happens BEFORE
    appendix content. Anti-regression test in tests/ would compare both
    layouts.
    """
    counts = {
        "critical_high": 0,
        "medium": 0,
        "low_info": 0,
    }
    # v2.3.3 — single source of truth: derive counts from `get_tier_assignments`.
    # Pre-v2.3.3 this function had its own table-only regex parser that returned
    # 0 when the Index Agent emitted bullet-list narrative (Irys L1 v2.3.x). The
    # silent-zero contributed to the empty-AUDIT_REPORT failure: tier writers
    # were dispatched with 0 expected findings → emitted placeholders. Reusing
    # `get_tier_assignments` ensures counts and assignments are NEVER out of
    # sync, and inherits the layered fallback (table → bullets → verify-queue
    # mechanical derivation).
    rows, _source = get_tier_assignments(scratchpad)
    for a in rows:
        prefix = a["severity"][:1].upper()
        if prefix in ("C", "H"):
            counts["critical_high"] += 1
        elif prefix == "M":
            counts["medium"] += 1
        elif prefix in ("L", "I"):
            counts["low_info"] += 1
    return counts


def _parse_markdown_table(text: str, required_headers: list[str]) -> tuple[list[str], list[list[str]]]:
    """Return (headers, rows) from the first markdown table matching headers."""
    lines = text.splitlines()
    i = 0
    required = [h.lower() for h in required_headers]
    while i < len(lines):
        header = lines[i].strip()
        if not header.startswith("|"):
            i += 1
            continue
        headers = [c.strip() for c in header.strip("|").split("|")]
        headers_lc = [h.lower() for h in headers]
        if not all(any(req in h for h in headers_lc) for req in required):
            i += 1
            continue
        if i + 1 >= len(lines):
            break
        sep = lines[i + 1].strip()
        if not sep.startswith("|"):
            i += 1
            continue
        rows: list[list[str]] = []
        j = i + 2
        while j < len(lines):
            row = lines[j].strip()
            if not row.startswith("|"):
                break
            if _is_separator_row(row):
                j += 1
                continue
            rows.append([c.strip() for c in row.strip("|").split("|")])
            j += 1
        return headers, rows
    return [], []


def _parse_markdown_tables(text: str, required_headers: list[str]) -> list[tuple[list[str], list[list[str]]]]:
    """Return all markdown tables matching the required header substrings."""
    lines = text.splitlines()
    i = 0
    required = [h.lower() for h in required_headers]
    out: list[tuple[list[str], list[list[str]]]] = []
    while i < len(lines):
        header = lines[i].strip()
        if not header.startswith("|"):
            i += 1
            continue
        headers = [c.strip() for c in header.strip("|").split("|")]
        headers_lc = [h.lower() for h in headers]
        if not all(any(req in h for h in headers_lc) for req in required):
            i += 1
            continue
        if i + 1 >= len(lines) or not lines[i + 1].strip().startswith("|"):
            i += 1
            continue
        rows: list[list[str]] = []
        j = i + 2
        while j < len(lines):
            row = lines[j].strip()
            if not row.startswith("|"):
                break
            if _is_separator_row(row):
                j += 1
                continue
            rows.append([c.strip() for c in row.strip("|").split("|")])
            j += 1
        if rows:
            out.append((headers, rows))
        i = max(j, i + 1)
    return out


_QUEUE_HEADER_ALIASES = {
    # canonical -> tuple of substring aliases the LLM might emit. Match is
    # case-insensitive substring against the header cell, so e.g.
    # "Preferred Verification" matches "preferred verification".
    "queue": ("queue", "q#", "queue number", "#"),
    "finding id": ("finding id", "hypothesis id", "finding", "id"),
    "severity": ("severity", "sev"),
    "title": ("title", "description", "summary"),
    "preferred tag": (
        "preferred tag", "preferred verification", "evidence tag",
        "evidence tags", "preferred evidence", "verification tag", "tag",
    ),
    "location": ("location", "path", "file", "loc"),
    "bug class": ("bug class", "category", "class"),
    "primary artifact": ("primary artifact", "artifact", "source artifact"),
    "poc class": ("poc class", "poc_class", "testability", "poc category"),
}


# v2.4.3: derived from unified _ID_* components above.
_FINDING_ID_EXTRACT_RE = re.compile(
    r"\b(" + _ID_ALL_INTERNAL + r")\b",
    re.IGNORECASE,
)


def _normalize_finding_id(raw: str) -> str:
    """Extract a stable finding/issue ID from common markdown/table forms.

    Defensive against LLM output drift via _llm_norm (called inline below
    to avoid forward-reference; mirrors the canonical helper at line ~2318).
    """
    s = (raw or "")
    # Inline mini-norm for forward-reference safety: line endings + curly quotes
    # + em-dash. Full _llm_norm is called downstream; this just keeps ID
    # extraction working when raw heading lines arrive with drift.
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("—", "-").replace("–", "-").replace("−", "-")
    s = s.replace("‘", "'").replace("’", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace(" ", " ")
    s = s.strip()
    if not s:
        return ""
    link = re.match(r"^\s*\[([^\]]+)\]\([^)]*\)\s*$", s)
    if link:
        s = link.group(1)
    s = s.strip("`*_[]() ")
    m = _FINDING_ID_EXTRACT_RE.search(s.replace("_", "-"))
    return m.group(1).upper() if m else ""


def _match_canonical_header(header_lc: str) -> Optional[str]:
    """Return the canonical key whose alias-set best matches a header cell.

    Longest-alias-first to avoid `loc` capturing a `location` cell. Returns
    None if no alias matches — the column is then dropped during parsing.
    Short aliases (<=3 chars) use word-boundary matching to prevent false
    positives like "id" matching "invalid" or "valid".
    """
    best: Optional[tuple[str, int]] = None
    for canonical, aliases in _QUEUE_HEADER_ALIASES.items():
        for alias in aliases:
            if len(alias) <= 3:
                if not re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", header_lc):
                    continue
            else:
                if alias not in header_lc:
                    continue
            if best is None or len(alias) > best[1]:
                best = (canonical, len(alias))
    return best[0] if best else None


def parse_verification_queue_rows(scratchpad: Path) -> list[dict[str, str]]:
    """Parse verification_queue.md into structured rows.

    v2.3.5 P1 — header-alias tolerance. Pre-v2.3.5 required exact substring
    matches for all 6 expected columns; if the LLM wrote `"Preferred
    Verification"` instead of `"Preferred Tag"` (alias documented in v2.2.1
    verifier-schema fix), `_parse_markdown_table` returned `headers=[]` and
    the entire queue parsed empty. Result: every verify shard had zero
    rows → zero `verify_*.md` files → silent halt at verify completion gate.

    Strategy: only require `severity` as the gate header (the most stable
    canonical name) and map every other column to its canonical key via
    `_QUEUE_HEADER_ALIASES`. Downstream consumers continue to read
    `entry.get("finding id")` etc. — the canonical-key contract is
    preserved regardless of the LLM's literal column-name choice.
    """
    p = scratchpad / "verification_queue.md"
    json_rows = _read_queue_json_sidecar(p)
    if json_rows:
        try:
            if not p.exists() or p.with_suffix(".json").stat().st_mtime_ns >= p.stat().st_mtime_ns:
                return json_rows
        except Exception:
            return json_rows
    if not p.exists():
        return json_rows
    try:
        text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return json_rows
    # Anchor on a single highly-stable header. The alias map handles the rest.
    headers, rows = _parse_markdown_table(text, ["severity"])
    if not headers:
        if json_rows:
            log.warning(
                "verification_queue.md has no parseable severity table; "
                "using existing JSON sidecar"
            )
            return json_rows
        return []
    headers_lc = [h.strip().lower() for h in headers]
    # Reject tables that don't even have a finding-id column under any alias.
    if not any(_match_canonical_header(h) == "finding id" for h in headers_lc):
        if json_rows:
            log.warning(
                "verification_queue.md has no finding-id column; using "
                "existing JSON sidecar"
            )
            return json_rows
        return []
    key_map: dict[int, str] = {}
    for idx, h in enumerate(headers_lc):
        canonical = _match_canonical_header(h)
        key_map[idx] = canonical if canonical else f"col_{idx}"
    parsed: list[dict[str, str]] = []
    for row in rows:
        entry: dict[str, str] = {}
        for idx, cell in enumerate(row):
            key = key_map.get(idx, f"col_{idx}")
            entry[key] = cell
        fid = _normalize_finding_id(entry.get("finding id") or "") or (entry.get("finding id") or "").strip()
        entry["finding id"] = fid
        sev = (entry.get("severity") or "").strip()
        if fid and sev:
            parsed.append(entry)
    if not parsed and json_rows:
        log.warning(
            "verification_queue.md parsed empty; using existing JSON sidecar"
        )
        return json_rows
    return parsed


def _severity_bucket(sev: str) -> str:
    n = normalize_severity(sev)
    return "info" if n == "Informational" else n.lower()


def compute_verify_shards(scratchpad: Path) -> dict[str, list[dict[str, str]]]:
    rows = parse_verification_queue_rows(scratchpad)
    crit_high = [r for r in rows if _severity_bucket(r.get("severity", "")) in {"critical", "high"}]
    medium = [r for r in rows if _severity_bucket(r.get("severity", "")) == "medium"]
    # v2.2.2 Fix 4: low_info shard. Pre-v2.2.2 Low and Informational
    # findings were silently dropped from verification — verify_queue
    # produced shards for crithigh/medium only. CLAUDE.md Thorough Mode
    # table mandates "ALL severities (with fuzz)"; pre-v2.2.2 behavior
    # contradicted methodology. Live impact (Irys L1 v2.2.0 first run):
    # 7 H-L01..H-L07 hypotheses never verified; H-L01 was a confirmed
    # human-GT match (High 4 - Invalid blocks not removed). Recall lost.
    low_info = [
        r for r in rows
        if _severity_bucket(r.get("severity", "")) in {"low", "info"}
    ]
    def assign_chunks(names: list[str], items: list[dict[str, str]], target: int) -> dict[str, list[dict[str, str]]]:
        out = {name: [] for name in names}
        if not items:
            return out
        chunk_count = min(len(names), max(1, math.ceil(len(items) / max(target, 1))))
        idx = 0
        for i, name in enumerate(names[:chunk_count]):
            remaining_chunks = chunk_count - i
            remaining_items = len(items) - idx
            take = math.ceil(remaining_items / max(remaining_chunks, 1))
            out[name] = items[idx:idx + take]
            idx += take
        if idx < len(items):
            out[names[-1]].extend(items[idx:])
        return out

    shards = {}
    shards.update(assign_chunks(
        list(L1_VERIFY_CRITHIGH_PHASE_NAMES),
        crit_high,
        8,
    ))
    shards.update(assign_chunks(
        ["verify_medium_a", "verify_medium_b", "verify_medium_c", "verify_medium_d", "verify_medium_e", "verify_medium_f"],
        medium,
        12,
    ))
    shards.update(assign_chunks(
        ["verify_low_a", "verify_low_b", "verify_low_c", "verify_low_d"],
        low_info,
        18,
    ))
    return shards


def _queue_sidecar_path(path: Path) -> Path:
    return path.with_suffix(".json")


def _canonical_queue_row(row: dict[str, str]) -> dict[str, str]:
    fid = _normalize_finding_id(row.get("finding id") or "") or str(row.get("finding id", "") or "").strip()
    return {
        "queue #": str(row.get("queue #", "") or ""),
        "finding id": fid,
        "expected output file": str(
            row.get("expected output file")
            or (f"verify_{fid}.md" if fid else "")
        ),
        "severity": normalize_severity(row.get("severity", "") or "Medium"),
        "title": str(row.get("title", "") or ""),
        "bug class": str(row.get("bug class", "") or ""),
        "preferred tag": str(row.get("preferred tag", "") or row.get("evidence tag", "") or ""),
        "location": str(row.get("location", "") or ""),
        "primary artifact": str(row.get("primary artifact", "") or ""),
        "poc class": str(row.get("poc class", "") or "structural"),
        "exclusion reason": str(row.get("exclusion reason", "") or ""),
    }


def _canonical_queue_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return only queue rows with a real finding identity.

    Queue manifests are machine contracts for later verify phases. A blank or
    malformed ID must be dropped here instead of becoming `verify_.md`, which
    then poisons unrelated phase gates.
    """
    canonical: list[dict[str, str]] = []
    dropped = 0
    for row in rows:
        item = _canonical_queue_row(row)
        if not item.get("finding id"):
            dropped += 1
            continue
        canonical.append(item)
    if dropped:
        log.warning(
            "dropped %s verification queue row(s) with blank finding id before writing manifest",
            dropped,
        )
    return canonical


def _write_queue_json_sidecar(path: Path, rows: list[dict[str, str]], *, kind: str) -> None:
    canonical = _canonical_queue_rows(rows)
    payload = {
        "schema_version": "plamen.verification_queue.v1",
        "kind": kind,
        "source_markdown": path.name,
        "row_count": len(canonical),
        "rows": canonical,
    }
    sidecar = _queue_sidecar_path(path)
    content = json.dumps(payload, indent=2, sort_keys=True)
    if sidecar.exists():
        try:
            if sidecar.read_text(encoding="utf-8", errors="replace") == content:
                return
        except Exception:
            pass
    sidecar.write_text(content + "\n", encoding="utf-8")


# v2.0.5 (P0.1, Codex fix 1): allowed Decision tokens for the skeptic-judge
# table. Any other token in the Decision column means this is NOT a skeptic-
# judge decision row — could be an unrelated 4+ column table elsewhere in
# the same file (Evidence Integrity Notes, etc).
_SKEPTIC_JUDGE_ALLOWED_DECISIONS = frozenset({
    "KEEP", "DOWNGRADE", "UNRESOLVED", "PARTIAL", "DISMISS",
})


def _parse_skeptic_judge_table(text: str) -> list[dict]:
    """v2.0.5 (P0.1): shared parser for the current skeptic_judge_decisions.md
    table format.

    The v2 skeptic prompt at `~/.plamen/prompts/shared/v2/phase5-skeptic.md`
    instructs the LLM to write decisions as a single pipe-delimited table:

        | Finding ID | Original Severity | Final Severity | Decision | Rationale |

    Pre-v2.0.5 the only table consumer was `_collect_judge_downgrade_map`
    (DOWNGRADE rows only). `_collect_judge_unresolved_ids` required
    `Verdict|Decision: UNRESOLVED|PARTIAL` prose, missing the table entirely
    and silently returning an empty set — root cause of the 2026-05-21 halt
    where every legitimate UNRESOLVED stamp failed authenticity.

    Lives in `plamen_parsers` (not `plamen_validators`) to respect the
    parsers→validators dependency direction.

    **Codex fix (P0.1 hardening):** validates BOTH (a) column 1 normalizes
    to a real internal finding ID via `_normalize_finding_id`, AND (b)
    column 4 is in `_SKEPTIC_JUDGE_ALLOWED_DECISIONS`. Without these
    checks, the parser over-matched unrelated 4+ column tables later in
    the file (e.g. an `Evidence Integrity Notes` table can invent
    `finding_id="Category"`, `decision="SUMMARY"`).

    Returns one dict per data row. Header / separator rows and rows that
    fail validation are silently skipped. Returns [] if no validated
    rows are found.
    """
    from plamen_types import normalize_severity
    rows: list[dict] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        # PARSE-1: preserve empty interior cells so positional reads stay
        # aligned to the documented 5-column contract. The prior empty-filter
        # collapsed a blank cell and shifted every later index, silently
        # dropping DOWNGRADE/UNRESOLVED rulings whose Final-Severity (or any
        # interior) cell was blank. Matches the empties-preserving split used by
        # every sibling manifest/table parser.
        cells = [c.strip() for c in s.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        first = cells[0]
        # Skip header row ("| Finding ID | ...") and separator ("|---|---|...")
        if first.startswith("-") or first.lower().startswith("finding"):
            continue
        decision = cells[3].upper().replace("*", "").strip()
        # Codex fix 1a: only accept canonical skeptic-judge decision tokens.
        # Rejects unrelated 4+ column tables whose 4th cell isn't a decision.
        if decision not in _SKEPTIC_JUDGE_ALLOWED_DECISIONS:
            continue
        # Codex fix 1b: column 1 must normalize to a recognized finding ID.
        # Rejects rows where the first cell is prose like "Category" or
        # "code-trace" or any other non-ID token.
        normalized_id = _normalize_finding_id(first)
        if not normalized_id:
            continue
        rows.append({
            "finding_id": first,
            "original_severity": normalize_severity(cells[1]) or cells[1],
            "final_severity": normalize_severity(cells[2]) or cells[2],
            "decision": decision,
            "rationale": cells[4] if len(cells) > 4 else "",
        })
    return rows


# ---------------------------------------------------------------------------
# v2.0.6 (P2): canonical ID ledger
# ---------------------------------------------------------------------------
#
# `_id_ledger.json` records every internal finding ID minted during a single
# audit, with the phase and attempt that produced it. The ledger gives the
# driver three things:
#
# 1. Collision detection across phase retries (chain attempt 1 minted
#    GRP-01 = title-A; attempt 2 tries to mint GRP-01 = title-B → COLLISION).
# 2. Next-available-ID allocation for the chain-prompt directive (so the
#    LLM knows which numbers are taken before it mints).
# 3. Consumer-side validation (sc_verify_queue / report_index should only
#    reference IDs the ledger has recorded — catches stale-markdown drift).
#
# Lives in plamen_parsers (not plamen_validators) so prompts AND validators
# can both consume it without violating the parsers→validators direction.


_ID_LEDGER_NAME = "_id_ledger.json"
_ID_LEDGER_SCHEMA_VERSION = "plamen.id_ledger.v1"


def _id_ledger_path(scratchpad: Path) -> Path:
    return scratchpad / _ID_LEDGER_NAME


def _title_hash(title: str) -> str:
    """v2.0.6 (P2): canonical content-hash for a finding title.

    Normalizes by lowercasing, collapsing whitespace, and stripping
    common ID/punctuation framing so legitimate retry-with-same-content
    produces an identical hash while different-content rewrites produce
    a different hash (collision detection).
    """
    import hashlib
    import re as _re
    s = (title or "").strip().lower()
    # Strip leading ID-like prefixes ("GRP-01:", "Finding [HC-02]:")
    s = _re.sub(r"^\s*(?:finding\s+)?\[?[a-z]{1,8}-\d+[a-z0-9-]*\]?\s*[:.]?\s*", "", s)
    # Collapse all whitespace
    s = _re.sub(r"\s+", " ", s)
    # Strip a few common punctuation chars that don't affect meaning
    s = s.strip(" -_:`*")
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _id_ledger_load(scratchpad: Path) -> dict:
    """Load the ID ledger from disk. Returns the canonical empty shape
    if the file doesn't exist or is malformed (so callers can append
    without needing to special-case first-write).
    """
    path = _id_ledger_path(scratchpad)
    empty = {
        "schema_version": _ID_LEDGER_SCHEMA_VERSION,
        "allocations": [],
    }
    if not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return empty
    if not isinstance(payload, dict):
        return empty
    if payload.get("schema_version") != _ID_LEDGER_SCHEMA_VERSION:
        return empty
    allocations = payload.get("allocations")
    if not isinstance(allocations, list):
        return empty
    return {
        "schema_version": _ID_LEDGER_SCHEMA_VERSION,
        "allocations": allocations,
    }


def _id_ledger_save(scratchpad: Path, ledger: dict) -> None:
    """Atomic save via temp-file rename (mirrors `_write_artifact_state`)."""
    path = _id_ledger_path(scratchpad)
    payload = {
        "schema_version": _ID_LEDGER_SCHEMA_VERSION,
        "allocations": ledger.get("allocations", []),
    }
    content = json.dumps(payload, indent=2, sort_keys=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _id_prefix_of(finding_id: str) -> str:
    """Extract the prefix segment of a finding ID (e.g., GRP-01 → 'GRP-').

    Returns "" for unrecognized formats.
    """
    import re as _re
    m = _re.match(r"^([A-Za-z]{1,8}-)\d+", (finding_id or "").strip().upper())
    return m.group(1) if m else ""


def id_ledger_register(
    scratchpad: Path,
    *,
    finding_id: str,
    owner_phase: str,
    owner_attempt: int,
    owning_artifact: str,
    title: str,
) -> dict:
    """v2.0.6 (P2): register an ID allocation in the ledger.

    Returns a dict with:
      - "status": "REGISTERED" | "REUSED" | "COLLISION"
      - "existing": the prior allocation record if REUSED/COLLISION, else None
      - "current": the input parameters as a record

    Semantics:
      - REGISTERED: ID not previously in ledger → new allocation recorded.
      - REUSED: ID already in ledger with the SAME title_hash → legitimate
        re-allocation (e.g. chain retry with same root cause) — no-op.
      - COLLISION: ID already in ledger with a DIFFERENT title_hash →
        the caller MUST fail the phase.

    The ledger file is written on every REGISTER (atomic). REUSED and
    COLLISION do NOT modify the ledger.
    """
    finding_id = (finding_id or "").strip().upper()
    if not finding_id:
        return {"status": "REGISTERED", "existing": None, "current": None}
    new_hash = _title_hash(title)
    ledger = _id_ledger_load(scratchpad)
    for record in ledger.get("allocations", []):
        if record.get("id", "").upper() == finding_id:
            if record.get("title_hash") == new_hash:
                return {"status": "REUSED", "existing": record, "current": None}
            return {"status": "COLLISION", "existing": record, "current": {
                "id": finding_id,
                "owner_phase": owner_phase,
                "owner_attempt": owner_attempt,
                "owning_artifact": owning_artifact,
                "title_preview": (title or "")[:120],
                "title_hash": new_hash,
            }}
    new_record = {
        "id": finding_id,
        "prefix": _id_prefix_of(finding_id),
        "owner_phase": owner_phase,
        "owner_attempt": owner_attempt,
        "owning_artifact": owning_artifact,
        "title_hash": new_hash,
        "title_preview": (title or "")[:120],
        "allocated_at": datetime.now(timezone.utc).isoformat(),
    }
    ledger["allocations"].append(new_record)
    _id_ledger_save(scratchpad, ledger)
    return {"status": "REGISTERED", "existing": None, "current": new_record}


def id_ledger_next_available(scratchpad: Path, prefix: str) -> str:
    """Return the next-available ID for `prefix` (e.g., 'GRP-' → 'GRP-04'
    if GRP-01..GRP-03 are allocated). Caller's responsibility to pass
    the correct prefix shape (must end in '-' and contain only letters
    before the dash).
    """
    import re as _re
    if not prefix or not prefix.endswith("-"):
        return ""
    ledger = _id_ledger_load(scratchpad)
    max_num = 0
    pattern = _re.compile(rf"^{_re.escape(prefix)}(\d+)$", _re.IGNORECASE)
    for record in ledger.get("allocations", []):
        m = pattern.match(record.get("id", ""))
        if m:
            try:
                n = int(m.group(1))
                if n > max_num:
                    max_num = n
            except ValueError:
                pass
    # Pad to 2 digits if the prefix conventionally uses 2-digit numbering,
    # else keep natural width. The chain prompt vocabulary uses 2-digit
    # padding by convention (HC-01, GRP-01, HH-02, etc.).
    return f"{prefix}{max_num + 1:02d}"


def id_ledger_lookup(scratchpad: Path, finding_id: str) -> dict | None:
    """Return the ledger record for `finding_id`, or None if not registered."""
    fid = (finding_id or "").strip().upper()
    if not fid:
        return None
    ledger = _id_ledger_load(scratchpad)
    for record in ledger.get("allocations", []):
        if record.get("id", "").upper() == fid:
            return record
    return None


def id_ledger_all_for_prefix(scratchpad: Path, prefix: str) -> list[dict]:
    """Return all ledger records whose ID has the given prefix."""
    ledger = _id_ledger_load(scratchpad)
    return [r for r in ledger.get("allocations", [])
            if r.get("id", "").upper().startswith(prefix.upper())]


def id_ledger_all_records(scratchpad: Path) -> list[dict]:
    """Return all ledger records (sorted by allocated_at)."""
    ledger = _id_ledger_load(scratchpad)
    records = list(ledger.get("allocations", []))
    records.sort(key=lambda r: r.get("allocated_at", ""))
    return records


def _judge_source_fingerprint(src: Path) -> dict:
    """v2.0.5 (P0.2, Codex fix 2): identity record for the source markdown
    that lets the sidecar reader detect changes within the same mtime
    second.

    Returns `{source_mtime_ns: int, source_sha256: str, source_size: int}`.
    Used by both writer (stored in JSON) and reader (compared to current
    source state). All three fields must match for the sidecar to be
    trusted.
    """
    import hashlib
    try:
        stat = src.stat()
        data = src.read_bytes()
    except OSError:
        return {}
    return {
        "source_mtime_ns": stat.st_mtime_ns,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_size": stat.st_size,
    }


def write_judge_decisions_json_sidecar(scratchpad: Path) -> int:
    """v2.0.5 (P0.2): write `{scratchpad}/judge_decisions.json` from the
    table format of `skeptic_judge_decisions.md`.

    Idempotent: if the JSON sidecar already exists and matches what
    would be written, leaves it alone (preserves mtime). The JSON is
    the canonical machine-readable source — consumers prefer it over
    re-parsing the markdown.

    Codex hardening (P0.2): the sidecar embeds `source_mtime_ns` and
    `source_sha256` so the reader can detect changes within the same
    mtime second (the previous 1-second tolerance silently returned
    stale data when the source was rewritten immediately after the
    sidecar was created).

    Returns the number of decisions written (0 if no source file, no
    valid table rows, or write failed).

    Schema: `plamen.judge_decisions.v1`.
    """
    src = scratchpad / "skeptic_judge_decisions.md"
    if not src.exists():
        return 0
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    rows = _parse_skeptic_judge_table(text)
    if not rows:
        return 0
    decisions = []
    for r in rows:
        decisions.append({
            "finding_id": r["finding_id"],
            "original_severity": r["original_severity"],
            "final_severity": r["final_severity"],
            "decision": r["decision"],
            "rationale": r["rationale"],
        })
    fingerprint = _judge_source_fingerprint(src)
    payload = {
        "schema_version": "plamen.judge_decisions.v1",
        "source_markdown": src.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "row_count": len(decisions),
        "decisions": decisions,
        # Codex fix 2: identity fingerprint of the source markdown.
        "source_mtime_ns": fingerprint.get("source_mtime_ns"),
        "source_sha256": fingerprint.get("source_sha256"),
        "source_size": fingerprint.get("source_size"),
    }
    sidecar = scratchpad / "judge_decisions.json"
    content = json.dumps(payload, indent=2, sort_keys=True)
    if sidecar.exists():
        try:
            existing = sidecar.read_text(encoding="utf-8", errors="replace")
            # Strip the `generated_at` field for content-equality check so
            # idempotent re-writes don't bump mtime needlessly. Also
            # normalize trailing whitespace — the writer appends "\n";
            # the in-memory `content` doesn't have it yet.
            import re as _re
            def _norm(s: str) -> str:
                return _re.sub(
                    r'"generated_at":\s*"[^"]*"',
                    '"generated_at": "<ts>"',
                    s,
                ).rstrip()
            if _norm(existing) == _norm(content):
                return len(decisions)
        except Exception:
            pass
    try:
        sidecar.write_text(content + "\n", encoding="utf-8")
    except OSError:
        return 0
    return len(decisions)


def read_judge_decisions_json_sidecar(scratchpad: Path) -> list[dict]:
    """v2.0.5 (P0.2): read `judge_decisions.json` if present AND the
    embedded source fingerprint matches the current
    `skeptic_judge_decisions.md`. Returns [] otherwise (caller falls
    back to markdown parsing).

    Codex hardening: pre-Codex this function used a 1-second mtime
    tolerance which accepted stale sidecars when the source was
    rewritten immediately after sidecar creation. The new check
    compares (mtime_ns, size, sha256) — accepts only on EXACT match.
    Legacy sidecars without fingerprint fields are rejected (caller
    re-writes them via the writer above).
    """
    sidecar = scratchpad / "judge_decisions.json"
    src = scratchpad / "skeptic_judge_decisions.md"
    if not sidecar.exists():
        return []
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("schema_version") != "plamen.judge_decisions.v1":
        return []
    # Codex fix 2: require the embedded source fingerprint to match the
    # current source markdown. If any field is missing (legacy sidecar)
    # or differs (source was rewritten), reject the sidecar so the
    # caller falls back to MD parsing.
    if src.exists():
        current = _judge_source_fingerprint(src)
        for k in ("source_mtime_ns", "source_sha256", "source_size"):
            if payload.get(k) != current.get(k):
                return []
    else:
        # Source missing but sidecar exists — accept the sidecar
        # (the gate caller will fall through and handle the missing
        # source separately).
        pass
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return []
    return decisions


def _read_queue_json_sidecar(path: Path) -> list[dict[str, str]]:
    sidecar = _queue_sidecar_path(path)
    if not sidecar.exists():
        return []
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if payload.get("schema_version") != "plamen.verification_queue.v1":
        return []
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        row = {str(k): str(v) for k, v in item.items()}
        fid = _normalize_finding_id(row.get("finding id", "")) or row.get("finding id", "").strip()
        if not fid:
            continue
        row["finding id"] = fid
        row["severity"] = normalize_severity(row.get("severity", "") or "Medium")
        out.append(row)
    declared = payload.get("row_count")
    if isinstance(declared, int) and declared != len(out):
        log.warning(
            "%s row_count mismatch in JSON sidecar: declared=%s parsed=%s",
            sidecar.name, declared, len(out),
        )
    return out


def _write_queue_subset_manifest(path: Path, rows: list[dict[str, str]]):
    rows = _canonical_queue_rows(rows)
    header = (
        "# Verification Queue Manifest\n"
        "| Queue # | Finding ID | Expected Output File | Severity | Title | Bug Class | Preferred Tag | Location | Primary Artifact | PoC Class |\n"
        "|---------|------------|----------------------|----------|-------|-----------|---------------|----------|------------------|-----------|\n"
    )
    body = []
    for row in rows:
        fid = row.get("finding id", "")
        body.append(
            "| {queue} | {finding} | verify_{finding}.md | {severity} | {title} | {bug_class} | {tag} | {location} | {artifact} | {poc_class} |".format(
                queue=row.get("queue #", ""),
                finding=fid,
                severity=row.get("severity", ""),
                title=row.get("title", ""),
                bug_class=row.get("bug class", ""),
                tag=row.get("preferred tag", ""),
                location=row.get("location", ""),
                artifact=row.get("primary artifact", ""),
                poc_class=row.get("poc class", "structural"),
            )
        )
    footer = (
        f"\nTotal: {len(rows)} findings | Expected verify_<ID>.md files: {len(rows)}\n"
    )
    content = header + "\n".join(body) + footer
    if path.exists():
        try:
            if path.read_text(encoding="utf-8", errors="replace") == content:
                _write_queue_json_sidecar(path, rows, kind="active")
                return
        except Exception:
            pass
    path.write_text(content, encoding="utf-8")
    _write_queue_json_sidecar(path, rows, kind="active")


def _write_queue_excluded_manifest(path: Path, rows: list[dict[str, str]]):
    """Write the explicit non-active side of the verification route."""
    rows = _canonical_queue_rows(rows)
    header = (
        "# Verification Queue Evidence-Excluded\n"
        "| Finding ID | Severity | Title | Exclusion Reason |\n"
        "|------------|----------|-------|------------------|\n"
    )
    body = []
    for row in rows:
        body.append(
            "| {finding} | {severity} | {title} | {reason} |".format(
                finding=row.get("finding id", ""),
                severity=row.get("severity", ""),
                title=row.get("title", ""),
                reason=row.get("exclusion reason", "Excluded from active verification"),
            )
        )
    footer = f"\nTotal: {len(rows)} excluded finding(s)\n"
    content = header + "\n".join(body) + footer
    if path.exists():
        try:
            if path.read_text(encoding="utf-8", errors="replace") == content:
                _write_queue_json_sidecar(path, rows, kind="excluded")
                return
        except Exception:
            pass
    path.write_text(content, encoding="utf-8")
    _write_queue_json_sidecar(path, rows, kind="excluded")


def ensure_verify_shard_manifests(scratchpad: Path) -> dict[str, list[dict[str, str]]]:
    shards = compute_verify_shards(scratchpad)
    for phase_name, rows in shards.items():
        _write_queue_subset_manifest(scratchpad / L1_VERIFY_SHARD_MANIFESTS[phase_name], rows)
    return shards


def compute_sc_verify_shards(scratchpad: Path) -> dict[str, list[dict[str, str]]]:
    """SC variant of compute_verify_shards with SC-prefixed phase names."""
    rows = parse_verification_queue_rows(scratchpad)
    crit_high = [r for r in rows if _severity_bucket(r.get("severity", "")) in {"critical", "high"}]
    medium = [r for r in rows if _severity_bucket(r.get("severity", "")) == "medium"]
    low_info = [
        r for r in rows
        if _severity_bucket(r.get("severity", "")) in {"low", "info"}
    ]
    def assign_chunks(names: list[str], items: list[dict[str, str]], target: int) -> dict[str, list[dict[str, str]]]:
        out = {name: [] for name in names}
        if not items:
            return out
        chunk_count = min(len(names), max(1, math.ceil(len(items) / max(target, 1))))
        idx = 0
        for i, name in enumerate(names[:chunk_count]):
            remaining_chunks = chunk_count - i
            remaining_items = len(items) - idx
            take = math.ceil(remaining_items / max(remaining_chunks, 1))
            out[name] = items[idx:idx + take]
            idx += take
        if idx < len(items):
            out[names[-1]].extend(items[idx:])
        return out

    shards = {}
    shards.update(assign_chunks(
        list(SC_VERIFY_CRITHIGH_PHASE_NAMES),
        crit_high,
        3,
    ))
    sc_medium_names = [k for k in SC_VERIFY_SHARD_MANIFESTS if k.startswith("sc_verify_medium")]
    sc_low_names = [k for k in SC_VERIFY_SHARD_MANIFESTS if k.startswith("sc_verify_low")]
    shards.update(assign_chunks(sc_medium_names, medium, 12))
    shards.update(assign_chunks(sc_low_names, low_info, 18))
    return shards


def ensure_sc_verify_shard_manifests(scratchpad: Path) -> dict[str, list[dict[str, str]]]:
    shards = compute_sc_verify_shards(scratchpad)
    for phase_name, rows in shards.items():
        _write_queue_subset_manifest(scratchpad / SC_VERIFY_SHARD_MANIFESTS[phase_name], rows)
    return shards


def classify_poc_testability(bug_class: str, preferred_tag: str, title: str, severity: str) -> str:
    """Classify a finding's testability for PoC routing.

    Returns one of: 'unit', 'property', 'integration', 'structural'

    This is MECHANICAL — no LLM needed. Pattern-match on bug class + keywords.
    """
    bc = (bug_class or "").lower()
    tag = (preferred_tag or "").lower()
    title_lc = (title or "").lower()

    structural_patterns = [
        "toctou", "crash-recovery", "crash recovery", "timing", "race condition",
        "cross-client", "non-determinism", "nondeterminism", "eclipse",
        "network partition", "byzantine",
        "missing setter", "no admin setter", "has no admin setter",
        "no setter", "missing function", "absent function",
        "event emission missing", "missing event", "no event",
        "event missing", "without emitting events", "without event",
        "emit no event", "emits no event", "no events",
        "no admin setter", "missing admin setter", "without a setter",
    ]
    if any(p in bc or p in title_lc for p in structural_patterns):
        if "map" in title_lc and ("iter" in title_lc or "order" in title_lc):
            return "property"
        return "structural"

    # VERIF-3: property/accounting/invariant bugs are multi-step and must be
    # property-class, not unit -- mislabeling them 'unit' forces an impossible
    # single-call harness and a false mandatory-PoC demand. Property is defined
    # BEFORE unit so a unit match via a BROAD noun (fee/share/deposit/...) that
    # CO-OCCURS with a strong property signal routes to property. NARROW,
    # unambiguous unit signals (overflow, access control, onlyOwner, ...) always
    # win regardless.
    property_patterns = [
        "state corruption", "invariant", "accumulator", "counter",
        "monotonic", "idempotent", "commutativ",
        "reentrancy", "accounting", "liquidation", "oracle", "price",
        "collateral", "debt", "ltv", "solvency", "interest", "reward",
        "custody", "escrow", "residual", "dust", "share price",
    ]
    narrow_unit_patterns = [
        "panic", "unwrap", "overflow", "underflow", "arithmetic",
        "validation", "bounds check", "off-by-one", "division by zero",
        "index out of", "assertion", "type cast", "truncat",
        "access control", "permission", "onlyowner", "only owner",
        "onlycreator", "unauthorized", "public withdraw", "missing guard",
        "slippage", "minout", "min out", "minreturn", "min return",
    ]
    broad_unit_nouns = [
        "fee", "rounding", "share", "deposit", "withdraw", "approve",
        "allowance", "transferfrom", "transfer from",
    ]
    narrow_unit_hit = any(p in bc or p in title_lc for p in narrow_unit_patterns)
    broad_unit_hit = any(p in bc or p in title_lc for p in broad_unit_nouns)
    property_hit = any(p in bc or p in title_lc for p in property_patterns)
    if narrow_unit_hit:
        return "unit"
    if broad_unit_hit:
        # A broad financial noun alone is unit; combined with a property signal
        # (accounting/invariant/reward/...) it is a property-class invariant bug.
        return "property" if property_hit else "unit"

    if "poc-pass" in tag or "poc" in tag:
        return "unit"

    if property_hit:
        return "property"

    if "fuzz" in tag or "non-det" in tag:
        return "property"

    integration_patterns = [
        "rpc", "network", "p2p", "multi-component", "integration",
        "handshake", "connection", "endpoint", "api surface",
    ]
    if any(p in bc or p in title_lc for p in integration_patterns):
        return "integration"

    if "lsp" in tag or "code-trace" in tag:
        return "structural"

    return "structural"


def _queue_rows_from_inventory_with_exclusions(
    scratchpad: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Convert inventory blocks into active and evidence-excluded queue rows.

    The verification queue is a routing artifact, not a reasoning task. Letting
    an LLM summarize it caused catastrophic over-cutting: a prior L1 run had
    276 inventory IDs but the queue agent emitted only 20 rows plus a prose
    placeholder. This function makes promotion loss mechanically impossible by
    routing every parsed inventory block to exactly one route.
    """
    inv_path = scratchpad / "findings_inventory.md"
    if not inv_path.exists():
        return [], []
    try:
        blocks = _inventory_blocks(_llm_norm(inv_path.read_text(encoding="utf-8", errors="replace")))
    except Exception:
        return [], []
    rows: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in blocks:
        fid = _normalize_finding_id(block.get("id", ""))
        if not fid or fid in seen:
            continue
        raw = block.get("block", "")
        verdict = _field_from_markdown(raw, ("Verdict", "Final Verdict", "Status"))
        seen.add(fid)
        severity = _severity_name_from_text(raw, {})
        preferred = (
            _field_from_markdown(raw, ("Preferred Tag", "Preferred Evidence", "Evidence Tag"))
            or "CODE-TRACE"
        )
        bug_class = (
            _field_from_markdown(raw, ("Bug Class", "Root Cause", "Class", "Category"))
            or block.get("title", "")
            or "Unclassified"
        )
        source = (
            block.get("source_ids", "")
            or _field_from_markdown(raw, ("Primary Artifact", "Source Artifact", "Artifact"))
            or "findings_inventory.md"
        )
        title_val = block.get("title", "") or fid
        bug_class_val = _strip_md(bug_class)
        preferred_tag_val = _strip_md(preferred).strip("[]") or "CODE-TRACE"
        poc_class = classify_poc_testability(bug_class_val, preferred_tag_val, title_val, severity)
        rows.append({
            "queue #": str(len(rows) + 1),
            "finding id": fid,
            "severity": severity,
            "title": title_val,
            "bug class": bug_class_val,
            "preferred tag": preferred_tag_val,
            "location": block.get("location", "") or _field_from_markdown(raw, ("Location", "Locations")),
            "primary artifact": _strip_md(source),
            "poc class": poc_class,
        })
        if verdict:
            status = _verifier_status_from_text(f"**Verdict**: {verdict}")
            if not _is_reportable_verdict(status):
                rows[-1]["exclusion reason"] = f"Inventory verdict {status}"
                excluded.append(rows.pop())
    return rows, excluded


def _queue_rows_from_inventory(scratchpad: Path) -> list[dict[str, str]]:
    """Convert reportable inventory blocks into canonical verification rows."""
    rows, _excluded = _queue_rows_from_inventory_with_exclusions(scratchpad)
    return rows


def _write_mechanical_verification_queue_from_inventory(scratchpad: Path) -> int:
    """Write verification_queue.md directly from findings_inventory.md."""
    rows, excluded = _queue_rows_from_inventory_with_exclusions(scratchpad)
    _write_queue_subset_manifest(scratchpad / "verification_queue.md", rows)
    _write_queue_excluded_manifest(
        scratchpad / "verification_queue_evidence_excluded.md",
        excluded,
    )
    return len(rows)


def _filter_verification_queue_by_mode(
    scratchpad: Path,
    mode: str,
    *,
    pipeline_label: str,
) -> int:
    """Remove Low/Info rows from active verification outside Thorough mode.

    Low verifier shards only exist in Thorough mode for both SC and L1. Keeping
    Low/Info rows in the active queue for Light/Core creates an impossible
    contract: no phase owns their `verify_<ID>.md` files, but aggregate parity
    still expects them.
    Preserve traceability by moving them to the explicit evidence-excluded
    sidecar/markdown artifact instead of silently dropping them.
    """
    if mode == "thorough":
        return 0
    rows = parse_verification_queue_rows(scratchpad)
    if not rows:
        return 0
    keep: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for row in rows:
        bucket = _severity_bucket(row.get("severity", ""))
        if bucket in {"low", "info"}:
            item = dict(row)
            item["exclusion reason"] = (
                f"Excluded from active {pipeline_label} verification in {mode} mode "
                "(Low/Info verify shards run only in Thorough mode)"
            )
            excluded.append(item)
        else:
            keep.append(row)
    if not excluded:
        return 0
    existing_excluded = _read_queue_json_sidecar(
        scratchpad / "verification_queue_evidence_excluded.md"
    )
    seen = {r.get("finding id", "") for r in existing_excluded}
    combined = list(existing_excluded)
    for row in excluded:
        fid = row.get("finding id", "")
        if fid and fid not in seen:
            combined.append(row)
            seen.add(fid)
    _write_queue_subset_manifest(scratchpad / "verification_queue.md", keep)
    _write_queue_excluded_manifest(
        scratchpad / "verification_queue_evidence_excluded.md",
        combined,
    )
    return len(excluded)


def _filter_sc_verification_queue_by_mode(scratchpad: Path, mode: str) -> int:
    """Backward-compatible SC wrapper used by existing tests/imports."""
    return _filter_verification_queue_by_mode(
        scratchpad,
        mode,
        pipeline_label="SC",
    )


# ---------------------------------------------------------------------------
# v2.4.8: Hypothesis-aware verify queue dedup
# ---------------------------------------------------------------------------

_HYPO_HEADING_RE = re.compile(
    r"^\s*#{2,4}\s+(?:(?:Chain\s+)?Hypothesis\s+)?"
    r"(\bH-[CHMLI]?\d+\b|\bCH-\d+\b|\bL1-[CHMLI]-\d+\b"
    r"|\bGRP-\d+\b|\bH[CHMLI]-\d+\b)",  # F1: SC grouped + severity-bucketed
    re.MULTILINE | re.IGNORECASE,
)


def _parse_hypothesis_constituents(scratchpad: Path) -> dict[str, list[str]]:
    """Parse hypothesis → constituent finding ID mapping.

    Tries finding_mapping.md first (table: constituent → hypothesis).
    Falls back to hypotheses.md (section headings + body scan for INV-* IDs).
    Returns {hypothesis_id: [constituent_id, ...]}.
    """
    mapping: dict[str, list[str]] = {}

    # --- Source 1: finding_mapping.md (preferred, written by Chain Agent 1)
    fm = scratchpad / "finding_mapping.md"
    if fm.exists():
        try:
            text = _llm_norm(fm.read_text(encoding="utf-8", errors="replace"))
            # Expected format: table rows with finding ID in one column,
            # hypothesis ID in another. Scan for both.
            for line in text.splitlines():
                if not line.strip().startswith("|"):
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) < 2:
                    continue
                # Find all internal IDs in the row
                ids_in_row: list[str] = []
                hypo_in_row: list[str] = []
                for cell in cells:
                    for m in re.finditer(r"\b((?:" + _ID_ALL_INTERNAL + r"))\b", cell, re.IGNORECASE):
                        fid = m.group(1).upper()
                        if re.match(r"^(?:" + _ID_HYPO_ALTS + r")$", fid, re.IGNORECASE):
                            hypo_in_row.append(fid)
                        else:
                            ids_in_row.append(fid)
                for h in hypo_in_row:
                    mapping.setdefault(h, []).extend(ids_in_row)
        except Exception:
            pass

    # --- Source 2: hypotheses.md (section-based parse)
    hyp = scratchpad / "hypotheses.md"
    if not hyp.exists():
        return mapping
    try:
        text = _llm_norm(hyp.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return mapping

    # Source 2a: hypotheses.md tables. Chain hypotheses are commonly table
    # rows (`CH-1 | ... | H-10, H-11 | ...`) rather than section headings.
    # Do not skip this just because finding_mapping.md produced H->INV rows.
    try:
        for headers, rows in _parse_markdown_tables(text, ["Hypothesis ID", "Source Findings"]):
            keys = [_norm_key(h) for h in headers]
            h_idx = keys.index("hypothesis id") if "hypothesis id" in keys else -1
            s_idx = keys.index("source findings") if "source findings" in keys else -1
            if h_idx < 0 or s_idx < 0:
                continue
            for row in rows:
                if max(h_idx, s_idx) >= len(row):
                    continue
                hypo_id = _normalize_finding_id(row[h_idx]) or row[h_idx].strip().upper()
                if not hypo_id:
                    continue
                constituents: list[str] = []
                for m in re.finditer(r"\b(" + _ID_ALL_INTERNAL + r")\b", row[s_idx], re.IGNORECASE):
                    fid = m.group(1).upper()
                    if fid != hypo_id and fid not in constituents:
                        constituents.append(fid)
                if constituents:
                    existing = mapping.setdefault(hypo_id, [])
                    existing.extend(c for c in constituents if c not in existing)
    except Exception:
        pass

    # Source 2b: split by hypothesis headings
    headings = list(_HYPO_HEADING_RE.finditer(text))
    for i, hm in enumerate(headings):
        hypo_id = hm.group(1).upper()
        start = hm.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section = text[start:end]
        # Extract all non-hypothesis internal IDs from the section body
        constituents: list[str] = []
        for m in re.finditer(r"\b(" + _ID_ALL_INTERNAL + r")\b", section, re.IGNORECASE):
            fid = m.group(1).upper()
            if not re.match(r"^(?:" + _ID_HYPO_ALTS + r")$", fid) and fid not in constituents:
                constituents.append(fid)
        if constituents:
            mapping.setdefault(hypo_id, []).extend(
                c for c in constituents if c not in mapping.get(hypo_id, [])
            )

    return mapping


_CHAIN_SUMMARY_HEADING_RE = re.compile(
    r"^##\s+Chain\s+Summary\b", re.MULTILINE | re.IGNORECASE
)


def _chain_iter2_has_no_unexplored_pairs(scratchpad: Path) -> bool:
    """Pre-spawn early-exit signal for phase `chain_iter2`.

    Returns True when there's nothing for iteration 2 to do — i.e. either
    `composition_coverage.md` is missing (chain phase didn't produce it,
    which is itself a soft-degraded state — defer rather than spawn an
    LLM with no input), OR the coverage map's Explored? column shows no
    NO rows that are cross-class AND have at least one Medium+ side.

    Per rules/phase4c-chain-prompt.md ITERATIVE_CHAIN_COMPOSITION:
    "If Agent 2 reported 0 new chains AND 0 unexplored cross-class
    Medium+ pairs → skip iteration 2."

    Conservative on parse failure: returns True (skip) rather than spawn
    an LLM that would then have no work. The soft phase model means a
    false-positive skip is cheap (we lose 0 chains we couldn't find
    anyway); a false-negative spawn wastes ~$1-2 of sonnet time.
    """
    coverage = scratchpad / "composition_coverage.md"
    if not coverage.exists():
        return True
    try:
        text = coverage.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return True
    # Parse the coverage table. Header should include Finding A, Finding B,
    # Explored?, Result, Notes. We look for table rows whose `Explored?`
    # cell is `NO` (case-insensitive) AND at least one severity column on
    # either side mentions Critical/High/Medium. Tolerant of column
    # ordering and exact header wording.
    unexplored_medium_plus = 0
    in_table = False
    header_keys: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            in_table = False
            header_keys = []
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and all(set(c) <= {"-", ":", " "} for c in cells):
            # Markdown separator row → previous line was the header.
            continue
        norm = [re.sub(r"[^a-z0-9]+", "", c.lower()) for c in cells]
        if "findinga" in norm or "findingb" in norm or "explored" in norm:
            in_table = True
            header_keys = norm
            continue
        if not in_table or not header_keys:
            continue
        # Look for explored column = NO
        try:
            explored_idx = header_keys.index("explored")
        except ValueError:
            try:
                explored_idx = header_keys.index("exploredq")
            except ValueError:
                continue
        if explored_idx >= len(cells):
            continue
        explored_val = cells[explored_idx].strip().lower()
        if explored_val not in ("no", "n", "false", "pending", "unexplored"):
            continue
        # Severity heuristic: if any cell in the row mentions Critical/High/Medium
        # → count as Medium+ unexplored.
        row_text = " ".join(cells).lower()
        if re.search(r"\b(critical|high|medium)\b", row_text):
            unexplored_medium_plus += 1
            if unexplored_medium_plus > 0:
                return False
    return unexplored_medium_plus == 0


def _extract_chain_summaries_compact(scratchpad: Path) -> int:
    """Extract ## Chain Summary sections from depth/scanner findings into a compact file.

    Writes {scratchpad}/chain_summaries_compact.md. Returns the number of
    source files that contributed at least one section.

    This is the V2 driver's mechanical implementation of the "Pre-Step:
    Chain Summary Extraction" described in phase4c-chain-prompt.md.
    """
    source_globs = [
        "depth_*_findings.md",
        "blind_spot_*_findings.md",
        "scanner_*_findings.md",
        "validation_sweep_findings.md",
        "niche_*_findings.md",
        "design_stress_findings.md",
        "sibling_propagation_findings.md",
    ]
    sections: list[str] = []
    contributors = 0
    for glob_pat in source_globs:
        for f in sorted(scratchpad.glob(glob_pat)):
            try:
                text = _llm_norm(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            # Find all ## Chain Summary sections
            matches = list(_CHAIN_SUMMARY_HEADING_RE.finditer(text))
            if not matches:
                continue
            contributors += 1
            for i, m in enumerate(matches):
                start = m.start()
                # Section extends until the next ## heading or EOF
                end_match = re.search(r"^##\s+", text[m.end():], re.MULTILINE)
                end = m.end() + end_match.start() if end_match else len(text)
                section_text = text[start:end].rstrip()
                if section_text:
                    sections.append(f"### Source: {f.name}\n\n{section_text}\n")

    out = scratchpad / "chain_summaries_compact.md"
    if sections:
        out.write_text(
            "# Chain Summaries (extracted by driver)\n\n"
            + "\n---\n\n".join(sections)
            + "\n",
            encoding="utf-8",
        )
    else:
        out.write_text(
            "# Chain Summaries (extracted by driver)\n\n"
            "No ## Chain Summary sections found in depth/scanner artifacts.\n",
            encoding="utf-8",
        )
    return contributors


def _dedup_queue_by_hypothesis(scratchpad: Path) -> int:
    """Collapse verification queue rows that share the same hypothesis.

    For each group of INV-* rows mapping to the same H-N hypothesis,
    keep one representative row (highest severity, hypothesis ID as the
    finding ID). Rewrites verification_queue.md in place.

    Returns the number of rows removed.
    """
    queue_path = scratchpad / "verification_queue.md"
    if not queue_path.exists():
        return 0

    mapping = _parse_hypothesis_constituents(scratchpad)
    if not mapping:
        return 0

    # Build reverse map: constituent_id → hypothesis_id
    constituent_to_hypo: dict[str, str] = {}
    for hypo_id, constituents in mapping.items():
        for cid in constituents:
            # First mapping wins (a finding shouldn't be in two hypotheses)
            if cid not in constituent_to_hypo:
                constituent_to_hypo[cid] = hypo_id

    # Read current queue rows
    rows = parse_verification_queue_rows(scratchpad)
    if not rows:
        return 0

    # Group rows by hypothesis (unmapped rows stay solo)
    groups: dict[str, list[dict[str, str]]] = {}
    solo: list[dict[str, str]] = []
    for row in rows:
        fid = (row.get("finding id") or "").upper()
        hypo = constituent_to_hypo.get(fid)
        if hypo:
            groups.setdefault(hypo, []).append(row)
        else:
            solo.append(row)

    # Collapse each hypothesis group into one representative row
    collapsed: list[dict[str, str]] = []
    for hypo_id, group_rows in sorted(groups.items()):
        if len(group_rows) == 1:
            # Single constituent — keep as-is but relabel to hypothesis ID
            rep = dict(group_rows[0])
            rep["finding id"] = hypo_id
            collapsed.append(rep)
        else:
            # Multiple constituents — pick highest severity, merge context
            group_rows.sort(key=lambda r: -_severity_rank(r.get("severity", "")))
            rep = dict(group_rows[0])
            rep["finding id"] = hypo_id
            # Aggregate title: use the first (highest-sev) constituent's title
            # Aggregate location: list unique locations
            locations = []
            for r in group_rows:
                loc = r.get("location", "").strip()
                if loc and loc not in locations:
                    locations.append(loc)
            if len(locations) > 1:
                rep["location"] = locations[0] + f" (+{len(locations)-1} more)"
            collapsed.append(rep)

    # Combine: collapsed hypothesis rows + solo rows, sorted by severity
    final = collapsed + solo
    final.sort(key=lambda r: -_severity_rank(r.get("severity", "")))
    # Renumber
    for i, row in enumerate(final, 1):
        row["queue #"] = str(i)

    original_count = len(rows)
    _write_queue_subset_manifest(queue_path, final)
    return original_count - len(final)


# v2.4.3: derived from unified _ID_* components. Callers run this on
# report-index table cells (non-zero positions) so [CHMLI]-\d{1,3}
# matches internal hypothesis IDs, not report IDs in column 0.
_INTERNAL_ID_RE = re.compile(
    r"\b(" + _ID_ALL_INTERNAL + r")\b", re.IGNORECASE
)


_REPORT_BULLET_RE = re.compile(
    r"^\s*[-*]\s*([CHMLI]-\d+)\s*[:.]",
    re.MULTILINE,
)


def _parse_report_index_table(text: str) -> list[dict[str, str]]:
    """Format 1: canonical Markdown table form. Returns [] if no rows match."""
    id_re = re.compile(r"^[\*\[`_]*([CHMLI]-\d+)\b")
    out: list[dict[str, str]] = []
    seen: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2:
            continue
        m = id_re.match(cells[0])
        if not m:
            continue
        report_id = m.group(1)
        finding_id = ""
        for cell in reversed(cells[1:]):
            ids = _INTERNAL_ID_RE.findall(cell)
            if ids:
                finding_id = "+".join(dict.fromkeys(i.upper() for i in ids))
                break
        row = {
            "report_id": report_id,
            "finding_id": finding_id,
            "severity": report_id[0],
        }
        prev = seen.get(report_id)
        sig = (finding_id, report_id[0])
        if prev is not None:
            # Duplicate routing tables in report_index.md must not become
            # duplicate client findings. Keep the first assignment; conflicting
            # duplicates are caught later by report quality/completeness gates.
            continue
        seen[report_id] = sig
        out.append(row)
    return out


def _report_index_assignment_text(text: str) -> str:
    """Return the canonical assignment section of report_index.md.

    The Index Agent may include both:
      - `## Master Finding Index`: semantic report-ID assignments
      - `## Tier Assignments`: writer routing metadata repeating the same IDs

    Only the Master Finding Index is the report-body cardinality contract.
    Parsing the routing tables as assignments doubles every ID and causes
    phantom body-writer shards.
    """
    m = re.search(r"(?im)^\s*#{1,4}\s+Master\s+Finding\s+Index\b[^\n]*$", text)
    if not m:
        return text
    start = m.start()
    next_heading = re.search(r"(?im)^\s*#{1,4}\s+(?!Master\s+Finding\s+Index\b).+$", text[m.end():])
    end = m.end() + next_heading.start() if next_heading else len(text)
    return text[start:end]


def _report_index_reportable_text(text: str) -> str:
    """Return only the reportable-assignment part of report_index.md.

    Excluded / refuted / false-positive sections often still contain report-ID
    looking tokens. Treating those as active assignments caused the assembler
    to restore excluded findings into the client-visible body.
    """
    cut_re = re.compile(
        r"(?im)^\s*#{1,4}\s+.*(?:excluded|false\s*positive|refuted|appendix|"
        r"consolidation map|non-reportable|not reportable|traceability).*$"
    )
    m = cut_re.search(text)
    return text[:m.start()] if m else text


def _parse_report_index_bullets(text: str) -> list[dict[str, str]]:
    """Format 2: bullet form `- C-01: Title (L1-C-01)` / `- C-01: Title (L1-C-01, downgraded ...)`.

    The Index Agent's narrative form observed in Irys L1 v2.3.x. Recovers
    per-finding mappings where the LLM emitted them; range bullets like
    `- H-01 through H-20: ...` are intentionally NOT parsed (no per-finding
    mapping) and drop to the mechanical fallback.
    """
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        m = _REPORT_BULLET_RE.match(s)
        if not m:
            continue
        report_id = m.group(1)
        if report_id in seen:
            continue
        # Skip range form like `- H-01 through H-20:`
        if re.search(r"\bthrough\s+[CHMLI]-\d+", s, re.IGNORECASE):
            continue
        # Find first internal-ID inside (...) on the line
        finding_id = ""
        paren = re.search(r"\(([^()]*)\)", s)
        if paren:
            mi = _INTERNAL_ID_RE.search(paren.group(1))
            if mi:
                finding_id = mi.group(1)
        seen.add(report_id)
        out.append({
            "report_id": report_id,
            "finding_id": finding_id,
            "severity": report_id[0],
        })
    return out


def parse_report_index_assignments(scratchpad: Path) -> list[dict[str, str]]:
    """Parse report_index.md into report-id/finding-id assignments.

    v2.3.3 — LAYERED FORMAT TOLERANCE.

    Tries two formats in priority order, returning whichever yields rows:
      1. Canonical Markdown table:
         ``| C-01 | Title | Critical | ... | L1-C-01 | ...``
      2. Bullet form (Index Agent narrative observed Irys L1 v2.3.x):
         ``- C-01: Title (L1-C-01)`` / ``- C-01: Title (L1-C-01, downgraded ...)``

    Range form (``- H-01 through H-20: ...``) is intentionally NOT parsed —
    it has no per-finding mapping. Empty return triggers the mechanical
    fallback in `get_tier_assignments`, which derives assignments from
    `verification_queue.md` (structured, driver-owned).

    v2.1.9 — Permissive prefix markers in table form.
    v2.3.3 — Bullet-form fallback added after Irys L1 run produced an
    empty AUDIT_REPORT.md when the Index Agent emitted bullet narrative
    instead of the canonical table. The empty deliverable was caused by
    silent-zero assignment dispatch.
    """
    p = scratchpad / "report_index.md"
    if not p.exists():
        return []
    try:
        text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    text = _report_index_reportable_text(text)
    text = _report_index_assignment_text(text)

    rows = _parse_report_index_table(text)
    if rows:
        return rows
    return _parse_report_index_bullets(text)


def _parse_report_index_summary_counts(scratchpad: Path) -> dict[str, int]:
    """Parse the Index Agent's explicit per-severity summary counts.

    This is intentionally independent of ``get_tier_assignments``. The
    assignment merger uses it to decide whether the Index Agent already emitted
    a complete, authoritative reportable set. When it did, mechanical
    verify-queue fallback rows must NOT be appended: the queue still contains
    refuted/excluded/pre-consolidation items that would inflate tier writers.
    """
    p = scratchpad / "report_index.md"
    if not p.exists():
        return {}
    try:
        text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    text = _report_index_reportable_text(text)

    counts = {"C": 0, "H": 0, "M": 0, "L": 0, "I": 0}
    found = False
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip().strip("*") for c in s.strip("|").split("|")]
        if len(cells) < 2:
            continue
        label = cells[0].lower()
        value = cells[1].replace(",", "")
        m = re.search(r"\b(\d+)\b", value)
        if not m:
            continue
        n = int(m.group(1))
        if label.startswith("critical"):
            counts["C"] = n
            found = True
        elif label.startswith("high"):
            counts["H"] = n
            found = True
        elif label.startswith("med"):
            counts["M"] = n
            found = True
        elif label.startswith("low"):
            counts["L"] = n
            found = True
        elif label.startswith("info"):
            counts["I"] = n
            found = True
    return counts if found else {}


_SKEPTIC_DOWNGRADE_RE = re.compile(
    r"\b(L1-[CHMLI]-\d+|H-[CHMLI]?\d+|CH-\d+|CC-\d+|F-\d+)\b[^\n→]{0,160}?"
    r"(?:Crit(?:ical)?|High|Med(?:ium)?|Low|Info(?:rmational)?)\s*"
    r"(?:→|->|to)\s*"
    r"(Crit(?:ical)?|High|Med(?:ium)?|Low|Info(?:rmational)?)",
    re.IGNORECASE,
)


def derive_tier_assignments_from_verify_queue(
    scratchpad: Path,
) -> list[dict[str, str]]:
    """Mechanical fallback for tier assignments. Driver-deterministic.

    Used when `parse_report_index_assignments` returns empty (LLM emitted
    a narrative or range-bullet form the parser cannot decompose into
    per-finding mappings). Derives assignments from structured artifacts
    the driver already trusts:

      - `verification_queue.md` rows (one per finding-id + severity)
      - `skeptic_judge_decisions.md` for severity downgrades, if present

    Output schema matches `parse_report_index_assignments`: list of
    `{report_id, finding_id, severity}` dicts. Report IDs are sequential
    per severity (`C-01`, `C-02`, ..., `H-01`, ...). Skips consolidation
    (one queue row → one report finding) — semantic consolidation is
    LLM work and must not be silently invented.

    Empty return = the fallback also has nothing to work with (no verify
    queue or no rows). Caller should hard-fail rather than dispatch
    placeholder tier writers.
    """
    rows = parse_verification_queue_rows(scratchpad)
    if not rows:
        return []

    # Apply skeptic-judge severity downgrades, if any.
    sj = scratchpad / "skeptic_judge_decisions.md"
    downgrades: dict[str, str] = {}
    if sj.exists():
        try:
            sj_text = _llm_norm(sj.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            sj_text = ""
        for m in _SKEPTIC_DOWNGRADE_RE.finditer(sj_text):
            fid = m.group(1)
            new_sev = m.group(2).strip()[:1].upper()
            if new_sev in "CHMLI":
                downgrades[fid] = new_sev

    sev_order = "CHMLI"
    by_sev: dict[str, list[str]] = {s: [] for s in sev_order}
    for row in rows:
        fid = (row.get("finding id") or "").strip()
        if not fid:
            continue
        vp = _verify_file_for_id(scratchpad, fid)
        try:
            vtxt = _llm_norm(vp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            vtxt = ""
        if not _is_reportable_verdict(_verifier_status_from_text(vtxt)):
            continue
        # Final severity: skeptic-judge override wins; else queue severity.
        queue_sev = (row.get("severity") or "").strip()
        sev_letter = downgrades.get(fid)
        if not sev_letter:
            sev_letter = queue_sev[:1].upper() if queue_sev else "M"
        if sev_letter not in by_sev:
            sev_letter = "M"
        by_sev[sev_letter].append(fid)

    out: list[dict[str, str]] = []
    for sev_letter in sev_order:
        for idx, fid in enumerate(by_sev[sev_letter], start=1):
            out.append({
                "report_id": f"{sev_letter}-{idx:02d}",
                "finding_id": fid,
                "severity": sev_letter,
            })
    return out


def get_tier_assignments(
    scratchpad: Path,
) -> tuple[list[dict[str, str]], str]:
    """Deterministic tier assignments with layered fallback + merge.

    Returns ``(assignments, source)`` where source is one of:
      * ``"index"``        — Index Agent rows fully cover the verify queue
      * ``"verify-queue"`` — mechanical only (Index Agent produced nothing parseable)
      * ``"merged"``       — Index Agent rows kept + mechanical fills the gap for
                              findings the Index Agent didn't enumerate per-finding
                              (e.g. `H-01 through H-20` range-bullet shorthand)
      * ``"empty"``        — no source produced rows; caller should hard-fail

    **Merge rule** (the architectural fix): the Index Agent's per-finding
    rows (consolidations, severity-adjustments) win where present; mechanical
    rows fill in for verify-queue findings the Index Agent didn't enumerate.
    Both signals contribute, neither alone is load-bearing.

    Pre-v2.3.3 the dispatch read `parse_report_index_assignments` directly.
    On Irys L1 v2.3.x the parser returned 3 rows (Crit bullets only — the
    Index Agent had collapsed High/Medium/Low to range shorthand). Those 3
    flowed straight to dispatch, the 55 unenumerated findings were silently
    dropped → empty `AUDIT_REPORT.md`. The merge prevents this entire class.
    """
    index_rows = parse_report_index_assignments(scratchpad)
    queue_rows = derive_tier_assignments_from_verify_queue(scratchpad)

    if not index_rows and not queue_rows:
        return [], "empty"
    if not queue_rows:
        return index_rows, "index"
    if not index_rows:
        return queue_rows, "verify-queue"

    # If the Index Agent emitted explicit summary counts and its per-finding
    # rows match those counts, the index is complete and authoritative. Do not
    # append verify-queue rows: the queue is pre-report and may include refuted,
    # excluded, downgraded, or consolidated items that the Index Agent
    # deliberately removed from the reportable body. This exact false merge
    # inflated Irys L1 C/H from 61 to 76 and triggered an unnecessary Opus
    # retry after the tier writer had correctly completed all assignments.
    summary_counts = _parse_report_index_summary_counts(scratchpad)
    if summary_counts:
        row_counts = {s: 0 for s in "CHMLI"}
        for a in index_rows:
            sev = (a.get("severity") or "")[:1].upper()
            if sev in row_counts:
                row_counts[sev] += 1
        if all(row_counts[s] == summary_counts.get(s, 0) for s in "CHMLI"):
            return index_rows, "index"

    # If Index Agent rows have no finding-id mappings at all, merge cannot
    # dedupe across sources — adding queue rows would double-count. Fall back
    # to mechanical alone. This protects SC where the Index Agent emits
    # report-IDs without explicit per-row internal IDs (bullet form: `- C-01:
    # Title` with no `(internal-id)` annotation).
    index_with_id = [a for a in index_rows if a.get("finding_id")]
    if not index_with_id:
        return queue_rows, "verify-queue"

    # Both sources have rows. Diff by finding_id. Index Agent is authoritative
    # for findings it enumerated; mechanical fills in for the rest.
    index_finding_ids: set[str] = set()
    for a in index_with_id:
        ids = _INTERNAL_ID_RE.findall(a.get("finding_id", ""))
        if ids:
            index_finding_ids.update(i.upper() for i in ids)
        else:
            index_finding_ids.add(a["finding_id"])
    queue_finding_ids = {a["finding_id"] for a in queue_rows}
    missing_from_index = queue_finding_ids - index_finding_ids
    if not missing_from_index:
        return index_rows, "index"

    # Merge. Continue per-severity numbering past the Index Agent's max.
    merged = list(index_rows)
    seen_report_ids = {a["report_id"] for a in index_rows}
    next_seq: dict[str, int] = {}
    for s in "CHMLI":
        next_seq[s] = max(
            (
                int(a["report_id"].split("-", 1)[1])
                for a in index_rows
                if a["severity"] == s and re.fullmatch(r"\d+", a["report_id"].split("-", 1)[1] if "-" in a["report_id"] else "")
            ),
            default=0,
        )
    for q in queue_rows:
        if q["finding_id"] in index_finding_ids:
            continue
        sev = q["severity"]
        next_seq[sev] = next_seq.get(sev, 0) + 1
        report_id = f"{sev}-{next_seq[sev]:02d}"
        while report_id in seen_report_ids:
            next_seq[sev] += 1
            report_id = f"{sev}-{next_seq[sev]:02d}"
        seen_report_ids.add(report_id)
        merged.append({
            "report_id": report_id,
            "finding_id": q["finding_id"],
            "severity": sev,
        })
    return merged, "merged"


_TIER_SEVERITY_MAP: dict[str, tuple[str, ...]] = {
    "critical_high": ("C", "H"),
    "medium": ("M",),
    "low_info": ("L", "I"),
}

_TIER_EMPTY_HEADER: dict[str, str] = {
    "critical_high": "## Critical Findings\n\n## High Findings\n\n",
    "medium": "## Medium Findings\n\n",
    "low_info": "## Low Findings\n\n## Informational Findings\n\n",
}


def compute_report_tier_shards(
    scratchpad: Path, tier_base: str,
) -> dict[str, list[dict[str, str]]]:
    """Split tier assignments into shards based on _BODY_SHARD_CAPS."""
    sevs = _TIER_SEVERITY_MAP.get(tier_base, ())
    if not sevs:
        return {}
    rows, _source = get_tier_assignments(scratchpad)
    assignments = [a for a in rows if a["severity"] in sevs]
    cap = _BODY_SHARD_CAPS.get(f"report_{tier_base}", 30)
    if len(assignments) <= cap:
        return {f"report_{tier_base}_a": assignments}
    n_shards = (len(assignments) + cap - 1) // cap
    chunk = (len(assignments) + n_shards - 1) // n_shards
    result: dict[str, list[dict[str, str]]] = {}
    for i in range(n_shards):
        suffix = chr(ord("a") + i)
        slice_rows = assignments[i * chunk : (i + 1) * chunk]
        if slice_rows:
            result[f"report_{tier_base}_{suffix}"] = slice_rows
    return result


def compute_report_medium_shards(scratchpad: Path) -> dict[str, list[dict[str, str]]]:
    return compute_report_tier_shards(scratchpad, "medium")


def ensure_report_tier_shards(
    scratchpad: Path, tier_base: str,
) -> dict[str, list[dict[str, str]]]:
    """Compute shards and write per-shard assignment manifests."""
    shards = compute_report_tier_shards(scratchpad, tier_base)
    for phase_name, rows in shards.items():
        manifest = scratchpad / f"{phase_name}_assignments.md"
        lines = [
            f"# {phase_name} assignments",
            "| Report ID | Finding ID |",
            "|-----------|------------|",
        ]
        lines.extend(
            f"| {row['report_id']} | {row['finding_id']} |"
            for row in rows
        )
        content = "\n".join(lines) + "\n"
        if manifest.exists():
            try:
                if manifest.read_text(encoding="utf-8", errors="replace") == content:
                    continue
            except Exception:
                pass
        manifest.write_text(content, encoding="utf-8")
    return shards


def ensure_report_medium_shards(scratchpad: Path) -> dict[str, list[dict[str, str]]]:
    return ensure_report_tier_shards(scratchpad, "medium")


def merge_report_tier_shards(scratchpad: Path, tier_base: str) -> None:
    """Merge report_{tier_base}_[a-z].md shard files into report_{tier_base}.md.

    Safe no-op: if no shard files exist (tier was not split), the base
    file is left untouched to avoid clobbering unsharded body-writer output.
    """
    parts = []
    for p in sorted(scratchpad.glob(f"report_{tier_base}_[a-z].md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            continue
        if text:
            parts.append(text)
    if not parts:
        return
    merged = "\n\n".join(parts).strip() + "\n"
    (scratchpad / f"report_{tier_base}.md").write_text(merged, encoding="utf-8")


def merge_report_medium_shards(scratchpad: Path) -> None:
    merge_report_tier_shards(scratchpad, "medium")


# v2.3.11: report_assemble is now Python-native (driver owns plumbing).
#
# The prior LLM-driven assemble phase thrashed for 1+ hour on a 225KB
# concatenation job that needed zero semantic reasoning. Per the V2 layer
# doctrine in CLAUDE.md ("Python driver owns runtime policy [...]. V1
# prompts own methodology"), concatenating tier files is plumbing, not
# methodology — should never have been LLM work.
#
# This function reads the tier files + report_index.md, generates the
# Executive Summary + Priority Remediation Order mechanically from the
# Master Finding Index counts and rows, and assembles AUDIT_REPORT.md
# per `~/.claude/rules/report-template.md`. Finishes in <1 second.
#
# The existing post-assemble quality gate (`_run_report_quality_gate`,
# `_check_promotion_symmetry`, etc.) still runs against the Python
# output. Mechanical assembly produces canonicalized output that
# satisfies the gates by construction.
def _extract_h2_section(text: str, header_substr: str) -> str:
    """Return the body of a `## {header_substr}...` section up to next H2.

    Tolerates trailing words in the heading (e.g., "## Summary Counts"
    matches header_substr="Summary"). Empty if no such section.
    Case-insensitive. Accepts H2 or H3 (`##`/`###`) to tolerate LLM
    heading-level drift. H1 is excluded because it is typically a
    document title that contains section names as substrings.
    """
    pattern = re.compile(
        r"^#{2,3}\s+" + re.escape(header_substr) + r"[^\n]*\n((?:.|\n)*?)(?=\n##(?!#)|\Z)",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------
# LLM output normalization layer (defensive, applied at every parser entry).
#
# Rationale: a fresh codebase audit can produce an LLM output format we
# haven't observed before — smart quotes, em-dashes, HTML entities, CRLF,
# zero-width chars, non-breaking spaces. If parsers are written against
# strict ASCII formats, each new format breaks an audit. The structural fix
# is to normalize input ONCE at every parser boundary into a canonical form,
# then parse with strict ASCII-only regexes.
#
# This function is idempotent: normalize(normalize(x)) == normalize(x). It
# only converts encoding-level variants; semantic content is preserved.
# --------------------------------------------------------------------------
_LLM_NORM_TABLE = {
    # Curly quotes -> ASCII
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    # Dashes -> hyphen
    "–": "-", "—": "-", "―": "-", "−": "-",
    # Ellipsis
    "…": "...",
    # Spaces
    " ": " ", " ": " ", " ": " ", " ": " ", "　": " ",
    # Zero-width / format chars (delete)
    "​": "", "‌": "", "‍": "", "⁠": "", "﻿": "",
    "­": "",
    # Bullet variants -> dash (preserves list semantics)
    "•": "-", "‣": "-", "◦": "-", "⁃": "-",
}


_HTML_ENTITY_RE = re.compile(r"&(?:#x([0-9A-Fa-f]+)|#(\d+)|(amp|lt|gt|quot|apos|nbsp));")


_HTML_ENTITY_MAP = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ",
}


def _llm_norm(text: str) -> str:
    """Idempotent normalization of LLM output for parser robustness.

    Closes the structural failure mode where a new codebase produces an
    LLM-output format variant (smart quote, HTML entity, CRLF) that breaks
    a parser written against ASCII LF. Wired into every parser entry point.
    """
    if not text:
        return text or ""
    s = text
    # Line endings first (so multi-line regexes work uniformly).
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # HTML entity decode (cheap, common LLM artifact). Loop to convergence
    # so double-encoded inputs `&amp;gt;` -> `&gt;` -> `>` are fully decoded.
    def _ent_repl(m: re.Match) -> str:
        if m.group(3):
            return _HTML_ENTITY_MAP.get(m.group(3), m.group(0))
        try:
            if m.group(1):
                cp = int(m.group(1), 16)
            else:
                cp = int(m.group(2))
            if 0 <= cp <= 0x10FFFF:
                return chr(cp)
        except (ValueError, OverflowError):
            pass
        return m.group(0)
    prev = None
    while prev != s:
        prev = s
        s = _HTML_ENTITY_RE.sub(_ent_repl, s)
    # Code-point translation AFTER entity decode so chars produced by entity
    # decoding (e.g. `&#xfeff;` -> `﻿` -> deleted as zero-width BOM)
    # also get normalized. Required for idempotence.
    s = s.translate(str.maketrans(_LLM_NORM_TABLE))
    return s


def _field_from_markdown(text: str, labels: tuple[str, ...]) -> str:
    """Extract a simple `Label: value` field from markdown."""
    text = _llm_norm(text)
    for label in labels:
        m = re.search(
            rf"(?im)^\s*(?:[-*]\s*|#{{1,6}}\s+)?(?:\*\*)?{re.escape(label)}(?:\*\*)?"
            rf"(?:\s*\([^)]*\))?\s*(?::|-|=)\s*(.+)$",
            text,
        )
        if m:
            return m.group(1).strip().strip("`")
    return ""


_OPTIONAL_FINDING_METADATA_LABELS: dict[str, tuple[str, ...]] = {
    "discovery_steer": ("Discovery Steer", "Discovery Steering"),
    "missing_precondition": ("Missing Precondition", "Missing Preconditions"),
    "precondition_type": ("Precondition Type", "Precondition Types"),
    "postconditions_created": (
        "Postconditions Created",
        "Postcondition Created",
        "Postconditions",
        "Postcondition",
    ),
    "postcondition_types": ("Postcondition Types", "Postcondition Type"),
    "semantic_invariant": ("Semantic Invariant", "Semantic Invariants"),
    "branch_preconditions": ("Branch Preconditions", "Branch Precondition"),
    "terminal_mechanism": ("Terminal Mechanism", "Terminal Mechanisms"),
    "composition_candidates": ("Composition Candidates", "Composition Candidate"),
}

_OPTIONAL_FINDING_METADATA_FIELDS: tuple[str, ...] = tuple(
    _OPTIONAL_FINDING_METADATA_LABELS.keys()
)


def _optional_finding_metadata_defaults() -> dict[str, str]:
    return {field: "" for field in _OPTIONAL_FINDING_METADATA_FIELDS}


def _optional_finding_metadata_field_for_label(label: str) -> str:
    key = _norm_key(label)
    for field, labels in _OPTIONAL_FINDING_METADATA_LABELS.items():
        if any(key == _norm_key(alias) for alias in labels):
            return field
    return ""


def _extract_optional_finding_metadata(text: str) -> dict[str, str]:
    out = _optional_finding_metadata_defaults()
    for field, labels in _OPTIONAL_FINDING_METADATA_LABELS.items():
        val = _field_from_markdown(text, labels)
        if val:
            out[field] = _strip_md(val)
    return out


def _first_heading_title(text: str) -> str:
    m = re.search(r"(?m)^#{1,4}\s+(?:Finding\s*)?(?:\[[^\]]+\]\s*)?(.+)$", text)
    return m.group(1).strip() if m else ""


def _sanitize_client_title(title: str) -> str:
    """Remove internal pipeline IDs from client-facing titles/headings."""
    s = title or ""
    internal = _INTERNAL_FINDING_ID_RE.pattern
    # Drop parenthetical/bracketed notes whose only purpose is an internal
    # trace reference, e.g. "(Depth Validation of INV-002)".
    s = re.sub(
        r"\s*[\(\[][^)\]\n]{0,120}?\b(?:" + internal + r")\b[^)\]\n]{0,120}?[\)\]]",
        "",
        s,
        flags=re.IGNORECASE,
    )
    # Titles should retain the client-facing claim, not a generic trace token.
    # Body prose can replace internal IDs with "upstream finding"; headings
    # containing that phrase are placeholders and fail report title quality.
    s = re.sub(r"\b(?:" + internal + r")\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(?i)\b(?:and|or|of)\s+(?:and|or|of)\b", " ", s)
    s = re.sub(r"(?i)\bduplicate\s+of\s*(?:/|\band\b|\bor\b)?\s*$", "duplicate", s)
    # Strip a leading agent-finding-ID prefix the mechanical index recovery
    # sometimes leaves on titles, e.g. "/ EXT-001: ..." or "STATE-001 - ...".
    s = re.sub(r"^[\s/]*[A-Z]{2,6}-\d{1,4}\s*[:\-–—]\s*", "", s)
    s = s.lstrip("/ ")
    s = re.sub(r"\s+", " ", s).strip(" -–—:/")
    return s or "Verified finding"


# v2.4.3: derived from _ID_ALL_NONHYPO — all internal IDs except bare
# [CHMLI]-\d{1,3} (which are report IDs in the client-facing body).
_CLIENT_BODY_INTERNAL_ID_RE = re.compile(
    r"\b(" + _ID_ALL_NONHYPO + r"|CH-\d+|H-[CHMLI]\d+|H-(?:[1-9]|\d{3,}))\b",
    re.IGNORECASE,
)


def _sanitize_client_body(text: str) -> str:
    """Remove internal pipeline IDs from client-facing report prose."""
    clean = re.sub(
        r"\bverify_[A-Za-z0-9_\-\[\].]+\.md\b",
        "verifier artifact",
        text or "",
        flags=re.IGNORECASE,
    )
    clean = _CLIENT_BODY_INTERNAL_ID_RE.sub("upstream finding", clean)
    # Drop internal-status narration the body writer sometimes leaks into prose.
    # The manifest's report_blocked flag is meant to drive a heading tag the
    # assembler strips, NOT client-facing sentences. Remove whole sentences
    # mentioning the internal status so no dangling fragment remains.
    clean = re.sub(
        r"(?is)(?:(?<=[.!?])\s+|^)[^.!?\n]*(?:report[\s-]?blocked|shard\s+inputs?)[^.!?\n]*[.!?]",
        " ",
        clean,
    )
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean.strip()


def _markdown_section(text: str, headings: tuple[str, ...], max_chars: int = 3500) -> str:
    """Extract a markdown H2/H3 section body by heading aliases."""
    if not text:
        return ""
    aliases = [re.escape(h) for h in headings]
    pat = re.compile(
        r"(?ims)^#{2,4}\s+(?:" + "|".join(aliases) + r")\b[^\n]*\n"
        r"(.*?)(?=^#{2,4}\s+\S|\Z)"
    )
    m = pat.search(text)
    if not m:
        return ""
    body = m.group(1).strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n\n_(Truncated; see verifier artifact for full trace.)_"
    return _sanitize_client_body(body)


def _field_or_section(
    text: str,
    field_labels: tuple[str, ...],
    section_headings: tuple[str, ...],
    fallback: str = "",
    max_chars: int = 3500,
) -> str:
    """Extract a report field from verifier markdown, preferring sections."""
    section = _markdown_section(text, section_headings, max_chars=max_chars)
    if section:
        return section
    field = _field_from_markdown(text, field_labels)
    if field:
        return _sanitize_client_body(field[:max_chars].strip())
    # Many report/verifier artifacts use bold field labels as block headers:
    # `**PoC Result**:\n```...\n````. `_field_from_markdown` intentionally
    # reads only same-line values, so recover the following block here.
    aliases = "|".join(re.escape(label) for label in field_labels)
    block_re = re.compile(
        rf"(?ims)^\s*(?:[-*]\s*)?(?:\*\*)?(?:{aliases})(?:\*\*)?"
        rf"\s*(?::|-|=)\s*\n(.*?)(?=^\s*(?:[-*]\s*)?(?:\*\*)?[A-Z][A-Za-z0-9 /_-]{{1,80}}"
        rf"(?:\*\*)?\s*(?::|-|=)\s*$|^#{1,4}\s+\S|\Z)"
    )
    m = block_re.search(text or "")
    if m:
        return _sanitize_client_body(m.group(1)[:max_chars].strip())
    return fallback


def parse_inventory_shard_manifest(scratchpad: Path, phase_name: str) -> list[str]:
    manifest = scratchpad / f"{phase_name}.manifest.md"
    if not manifest.exists():
        return []
    files: list[str] = []
    try:
        text = _llm_norm(manifest.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return files
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|") or _is_separator_row(s):
            continue
        s_up = s.upper()
        if "FILE" in s_up and ("ROLE" in s_up or "MODEL" in s_up or "STATUS" in s_up):
            continue
        parts = [c.strip() for c in s.strip("|").split("|")]
        if len(parts) >= 2 and parts[0].endswith(".md"):
            files.append(parts[0])
    return files


_SEVERITY_ORDER = {s.lower(): severity_rank(s) for s in SEVERITY_ORDER}
_SEVERITY_ORDER["info"] = 0

_SEVERITY_CODE = {s.lower(): severity_letter_from_name(s) for s in SEVERITY_ORDER}
_SEVERITY_CODE["info"] = "I"


def _strip_md(text: str) -> str:
    s = (text or "").strip()
    s = s.replace("`", "").replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", s).strip()


def _norm_loc(text: str) -> str:
    return _strip_md(text).replace("\\", "/")


def _norm_key(text: str) -> str:
    s = re.sub(r"\s*\(.*", "", _strip_md(text))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_SUFFIX_STRIP_RE = re.compile(r"(tion|ing|ness|ment|able|ible|ful|less|ous|ive|ed|er|ly|es|s)$")


def _stem_token(t: str) -> str:
    """Aggressive-enough suffix stripping without a synonym map."""
    if len(t) <= 4:
        return t
    return _SUFFIX_STRIP_RE.sub("", t)


def _title_tokens(text: str) -> set[str]:
    """Tokenize a finding title for overlap scoring."""
    stop = frozenset({
        "the", "a", "an", "in", "on", "at", "to", "for", "of", "via",
        "and", "or", "but", "not", "is", "are", "was", "were", "be",
        "has", "have", "had", "does", "do", "did", "can", "could",
        "may", "might", "will", "would", "shall", "should",
        "all", "any", "each", "every", "some", "no", "none",
        "when", "during", "after", "before", "between", "from",
        "only", "also", "still", "yet", "with", "without", "by",
        "this", "that", "which", "its", "into", "if", "then",
        "using", "uses", "used", "create", "leads", "results",
        "allows", "causes", "triggers", "instead", "about",
    })
    raw = set(re.sub(r"[^a-z0-9_]+", " ", text.lower()).split()) - stop
    return {_stem_token(t) for t in raw if t}


def _titles_overlap_score(a: str, b: str) -> float:
    """Score how likely two finding titles describe the same root cause.

    Uses max-containment ratio with suffix stripping (no synonym map).
    Returns a float in [0.0, 1.0]. Callers pick their own threshold.

    IMPORTANT: This function measures TITLE SIMILARITY, not "same bug".
    Two genuinely different bugs CAN have similar titles (e.g., "panic via
    unwrap in X" vs "panic via unwrap in Y"). Callers MUST combine the
    score with additional context (same file, same function) before making
    merge decisions. Used for CANDIDATE IDENTIFICATION, not final merges.
    """
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    if not intersection:
        return 0.0
    containment = max(len(intersection) / len(ta), len(intersection) / len(tb))
    # Anchor boost: shared specific identifiers (function/struct names
    # containing underscores, or long camelCase >8 chars). A shared
    # specific identifier is a strong signal of the same code area.
    anchor_a = {t for t in ta if "_" in t}
    anchor_b = {t for t in tb if "_" in t}
    if anchor_a & anchor_b:
        containment = min(containment + 0.20, 1.0)
    return containment


def _shared_anchor_tokens(a: str, b: str) -> set[str]:
    """Return specific identifiers (function/struct names) shared by both titles."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    anchor_a = {t for t in ta if "_" in t}
    anchor_b = {t for t in tb if "_" in t}
    return anchor_a & anchor_b


_LINE_RANGE_RE = re.compile(r":L?(\d+)(?:\s*[-–]\s*L?(\d+))?")


def _parse_line_range(location: str) -> tuple[int, int] | None:
    """Extract (start, end) line range from a location string.

    Handles: ``file.rs:L40``, ``file.rs:L40-L65``, ``file.rs:40-65``.
    Returns None if no line info found.  Single-line → (N, N).
    """
    m = _LINE_RANGE_RE.search(location)
    if not m:
        return None
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    if end < start:
        start, end = end, start
    return (start, end)


def _line_ranges_overlap(a: tuple[int, int], b: tuple[int, int],
                         proximity: int = 15) -> bool:
    """True if two line ranges overlap or are within ``proximity`` lines."""
    return a[0] <= b[1] + proximity and b[0] <= a[1] + proximity


def _extract_ids_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for tok in re.findall(r"\b[A-Z][A-Z0-9]{0,6}-\d+\b", text or ""):
        if tok not in seen:
            seen.add(tok)
            ordered.append(tok)
    return ordered


def _extract_first_tag(text: str) -> str:
    m = re.search(r"(\[[A-Z0-9\-]+\])", text or "")
    return m.group(1) if m else ""


def _parse_source_findings_for_ids(path: Path) -> list[dict[str, str]]:
    try:
        text = _llm_norm(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    findings: list[dict[str, str]] = []
    cur: Optional[dict[str, str]] = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^###\s+Finding\s+\[([A-Z][A-Z0-9]{0,6}-\d+)\]", s)
        if m:
            if cur:
                findings.append(cur)
            cur = {"id": m.group(1), "title": s, "location": ""}
            continue
        if cur:
            m = re.match(r"^\*\*Location\*\*:\s*(.+)$", s, re.IGNORECASE)
            if m:
                cur["location"] = _norm_loc(m.group(1))
    if cur:
        findings.append(cur)
    return findings


def _parse_chunk_heading_inventory(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    entries: list[dict[str, object]] = []
    starts = [
        idx for idx, line in enumerate(lines)
        if re.match(r"^\s*#{2,4}\s+Finding\b", line)
    ]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(lines)
        block = [x.rstrip() for x in lines[start:end] if x.strip()]
        if not block:
            continue
        heading = block[0].strip()
        title = re.sub(r"^#{2,4}\s+(?:Finding\s+\[[^\]]+\]:\s*)?", "", heading).strip()
        title = re.sub(r"\s*-\s*(Critical|High|Medium|Low|Informational|Info)\s*$", "", title, flags=re.I)
        entry: dict[str, object] = {
            "title": _strip_md(title),
            "severity": "",
            "location": "",
            "source_ids": [],
            "preferred_tag": "",
            "verdict": "",
            "root_cause": "",
            "description": "",
            "impact": "",
            **_optional_finding_metadata_defaults(),
        }
        m = re.match(r"^#{2,4}\s+Finding\s+\[([^\]]+)\]:?", heading)
        if m:
            entry["local_id"] = m.group(1)
        else:
            m = re.match(r"^#{2,4}\s+Finding\s+([A-Z][A-Z0-9-]*-\d+)\b", heading)
            if m:
                entry["local_id"] = m.group(1)
        for row in block[1:]:
            # Format-tolerant field extraction: handles bullets (- / *),
            # bold wrapping (**Label**: / **Label:**), and absence of bold.
            fm = re.match(
                r"^\s*[-*]?\s*(?:\*\*)?([^:*]+?)(?:\*\*)?"
                r"\s*(?:\([^)]*\))?\s*:\s*(.*)",
                row,
            )
            if not fm:
                continue
            label_lc = fm.group(1).strip().lower()
            val_raw = fm.group(2).strip()
            if label_lc == "location":
                entry["location"] = _norm_loc(val_raw)
            elif label_lc == "severity":
                sev_val = _strip_md(val_raw)
                if _non_reportable_marker(sev_val):
                    entry["severity"] = "Informational"
                    if not entry.get("verdict"):
                        entry["verdict"] = "REFUTED"
                elif _ambiguous_na_marker(sev_val):
                    entry["severity"] = "Informational"
                    if not entry.get("verdict"):
                        entry["verdict"] = "UNRESOLVED"
                else:
                    entry["severity"] = sev_val.capitalize()
            elif label_lc == "verdict":
                entry["verdict"] = _strip_md(val_raw)
            elif label_lc in (
                "evidence", "preferred tag", "preferred verification",
                "evidence tag", "evidence tags",
            ):
                entry["preferred_tag"] = _extract_first_tag(val_raw) or _strip_md(val_raw)
            elif label_lc == "root cause":
                entry["root_cause"] = _strip_md(val_raw)
            elif label_lc == "impact":
                entry["impact"] = _strip_md(val_raw)
            elif label_lc == "description":
                entry["description"] = _strip_md(val_raw)
            elif label_lc in ("source ids", "source id"):
                entry["source_ids"] = _extract_ids_from_text(val_raw)
            else:
                opt_field = _optional_finding_metadata_field_for_label(label_lc)
                if opt_field:
                    entry[opt_field] = _strip_md(val_raw)
        if _non_reportable_marker(str(entry.get("severity", ""))) or _non_reportable_marker(str(entry.get("verdict", ""))):
            entry["severity"] = "Informational"
            if not entry.get("verdict"):
                entry["verdict"] = "REFUTED"
        elif _ambiguous_na_marker(str(entry.get("severity", ""))):
            entry["severity"] = "Informational"
            if not entry.get("verdict"):
                entry["verdict"] = "UNRESOLVED"
        entries.append(entry)
    return entries


def _parse_chunk_table_inventory(text: str) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for headers, rows in _parse_markdown_tables(text, ["title", "severity", "location"]):
        key_map = {idx: _norm_key(h) for idx, h in enumerate(headers)}
        for row in rows:
            entry: dict[str, object] = {
                "title": "",
                "severity": "",
                "location": "",
                "source_ids": [],
                "preferred_tag": "",
                "verdict": "",
                "root_cause": "",
                "description": "",
                "impact": "",
                **_optional_finding_metadata_defaults(),
            }
            for idx, cell in enumerate(row):
                key = key_map.get(idx, "")
                val = _strip_md(cell)
                if "finding id" in key or key == "id":
                    entry["local_id"] = val
                elif "title" in key:
                    entry["title"] = val
                elif "severity" in key:
                    if _non_reportable_marker(val):
                        entry["severity"] = "Informational"
                        if not entry.get("verdict"):
                            entry["verdict"] = "REFUTED"
                    elif _ambiguous_na_marker(val):
                        entry["severity"] = "Informational"
                        if not entry.get("verdict"):
                            entry["verdict"] = "UNRESOLVED"
                    else:
                        entry["severity"] = val.capitalize()
                elif "location" in key:
                    entry["location"] = _norm_loc(val)
                elif "source id" in key or key == "source":
                    entry["source_ids"] = _extract_ids_from_text(val)
                elif "evidence" in key:
                    entry["preferred_tag"] = _extract_first_tag(val) or val
                elif "verdict" in key:
                    entry["verdict"] = val
                elif "root cause" in key:
                    entry["root_cause"] = val
                elif "description" in key:
                    entry["description"] = val
                elif "impact" in key:
                    entry["impact"] = val
                elif "vulnerability class" in key and not entry["root_cause"]:
                    entry["root_cause"] = val
                else:
                    opt_field = _optional_finding_metadata_field_for_label(key)
                    if opt_field:
                        entry[opt_field] = val
            if _non_reportable_marker(str(entry.get("severity", ""))) or _non_reportable_marker(str(entry.get("verdict", ""))):
                entry["severity"] = "Informational"
                if not entry.get("verdict"):
                    entry["verdict"] = "REFUTED"
            elif _ambiguous_na_marker(str(entry.get("severity", ""))):
                entry["severity"] = "Informational"
                if not entry.get("verdict"):
                    entry["verdict"] = "UNRESOLVED"
            if entry["title"] or entry["location"]:
                entries.append(entry)
    return entries


def _parse_inventory_chunk(path: Path) -> list[dict[str, object]]:
    try:
        text = _llm_norm(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    parsed = _parse_chunk_table_inventory(text) + _parse_chunk_heading_inventory(text)
    merged: dict[tuple[str, str], dict[str, object]] = {}
    order: list[tuple[str, str]] = []
    for entry in parsed:
        local = _normalize_finding_id(str(entry.get("local_id", "")))
        if local:
            key = ("id", local)
        else:
            key = (
                _norm_key(str(entry.get("title", ""))),
                _norm_loc(str(entry.get("location", ""))),
            )
        if not key[0] or key in {("", ""), ("id", "")}:
            continue
        if key not in merged:
            merged[key] = entry
            order.append(key)
            continue
        cur = merged[key]
        for field in (
            "title", "severity", "location", "preferred_tag", "verdict",
            "root_cause", "description", "impact", "local_id",
            *_OPTIONAL_FINDING_METADATA_FIELDS,
        ):
            if not cur.get(field) and entry.get(field):
                cur[field] = entry.get(field)
            elif len(str(entry.get(field, ""))) > len(str(cur.get(field, ""))) and field in {"root_cause", "description", "impact", *_OPTIONAL_FINDING_METADATA_FIELDS}:
                cur[field] = entry.get(field)
        cur_ids = list(cur.get("source_ids", []) or [])
        for sid in list(entry.get("source_ids", []) or []):
            if sid not in cur_ids:
                cur_ids.append(sid)
        cur["source_ids"] = cur_ids
    return [merged[k] for k in order]


def _severity_rank(sev: str) -> int:
    return _SEVERITY_ORDER.get((sev or "").strip().lower(), -1)


def _merge_inventory_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[tuple[str, str], dict[str, object]] = {}
    order: list[tuple[str, str]] = []
    for entry in entries:
        loc = _norm_loc(str(entry.get("location", "")))
        title_key = _norm_key(str(entry.get("title", "")))
        # Conservative merge: only coalesce exact title+location duplicates.
        # A single location can contain multiple sibling bugs (loop early-exit,
        # >= vs ==, missing field check). Root-cause-only merging overcut those
        # into one row, hiding true positives before verification. Duplicates
        # are cheaper than false drops; later consolidation can merge proven
        # duplicates after verification.
        key = (loc, title_key)
        if not loc or not title_key:
            continue
        if key not in merged:
            source_ids = list(entry.get("source_ids", []))
            local_id = entry.get("local_id")
            if local_id and local_id not in source_ids:
                source_ids.append(local_id)
            merged[key] = {
                "title": entry.get("title", ""),
                "severity": entry.get("severity", ""),
                "location": loc,
                "source_ids": source_ids,
                "preferred_tag": entry.get("preferred_tag", ""),
                "verdict": entry.get("verdict", ""),
                "root_cause": entry.get("root_cause", ""),
                "description": entry.get("description", ""),
                "impact": entry.get("impact", ""),
                **{
                    field: entry.get(field, "")
                    for field in _OPTIONAL_FINDING_METADATA_FIELDS
                },
            }
            order.append(key)
            continue
        cur = merged[key]
        if _severity_rank(str(entry.get("severity", ""))) > _severity_rank(str(cur.get("severity", ""))):
            cur["severity"] = entry.get("severity", "")
        if not cur.get("preferred_tag") and entry.get("preferred_tag"):
            cur["preferred_tag"] = entry.get("preferred_tag", "")
        if not cur.get("verdict") and entry.get("verdict"):
            cur["verdict"] = entry.get("verdict", "")
        for field in (
            "root_cause", "description", "impact", "title",
            *_OPTIONAL_FINDING_METADATA_FIELDS,
        ):
            if len(str(entry.get(field, ""))) > len(str(cur.get(field, ""))):
                cur[field] = entry.get(field, "")
        local_id = entry.get("local_id")
        if local_id and local_id not in cur["source_ids"]:
            cur["source_ids"].append(local_id)
        for fid in entry.get("source_ids", []):
            if fid not in cur["source_ids"]:
                cur["source_ids"].append(fid)
    items = [merged[k] for k in order]
    items.sort(key=lambda e: (-_severity_rank(str(e.get("severity", ""))), _norm_key(str(e.get("title", "")))))
    return items


_DEDUP_LIVE_PAIR_LIMIT = 24


def _compute_dedup_candidate_pairs(scratchpad: Path) -> int:
    """Identify candidate duplicate pairs in findings_inventory.md.

    Groups findings by file, then pairs by THREE independent signals:
      1. **Location overlap** (primary): same file + line ranges within 15 lines
      2. **Title overlap** (secondary): same file + ≥0.50 token overlap or anchor
      3. **Function-name match** (tertiary): same file + same function name
         extracted from Location field (e.g., ``Contract.sol:functionName:L42``)

    Location overlap catches the hard case: agents describing the same code
    from different angles with completely different vocabulary.  Title overlap
    catches the easy case: near-identical rewordings.  Function-name match
    catches findings targeting the same function but at different line offsets
    (e.g., entry check vs exit path of the same function).

    NEVER merges findings — only identifies candidates for LLM review.
    Returns the number of candidate pairs written.
    """
    inv = scratchpad / "findings_inventory.md"
    if not inv.exists():
        return 0
    try:
        inv_text = _llm_norm(inv.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0

    if inv_text and not inv_text.endswith("\n"):
        inv_text += "\n"

    # Extract (inv_id, title, location, severity, source_ids) for each finding
    findings: list[dict] = []
    for m in re.finditer(
        r"#{2,4}\s+(?:Finding\s+)?\[((?:INV|F)-\d+)\]:?\s*(.+?)(?:\n|$)"
        r"((?:.*\n)*?)"
        r"(?=#{2,4}\s+(?:Finding\s+)?\[(?:INV|F)-|\Z)",
        inv_text,
    ):
        inv_id = m.group(1)
        title = m.group(2).strip()
        body = m.group(3)
        loc_m = re.search(r"\*\*Location\*\*:\s*(.+)", body, re.IGNORECASE)
        sev_m = re.search(r"\*\*Severity\*\*:\s*(\w+)", body, re.IGNORECASE)
        loc = loc_m.group(1).strip() if loc_m else ""
        sev = sev_m.group(1).strip() if sev_m else ""
        norm = _norm_loc(loc)
        file_part = re.sub(r":L?\d+.*$", "", norm)
        line_range = _parse_line_range(norm)
        # Extract function name from Location field patterns like:
        #   Contract.sol:functionName:L42  or  src/lib.rs:my_func:L10-L20
        #   or  `Contract.sol:functionName` (backtick-wrapped)
        func_name = ""
        func_m = re.search(
            r"[./\w]+\.(?:sol|rs|move|ts|go):"
            r"([a-zA-Z_][a-zA-Z0-9_]*)"
            r"(?::?L?\d|[`\s,|]|$)",
            loc,
        )
        if func_m:
            candidate = func_m.group(1)
            # Filter out line references (L42, Line10) and keywords
            if (
                candidate.lower() not in ("l", "line", "lines")
                and not re.fullmatch(r"[Ll]\d+", candidate)
            ):
                func_name = candidate
        # Extract source IDs (e.g. [D-58,D-59] or [PERT-3]) using the same
        # format-tolerant contract as inventory parity.
        source_ids: set[str] = set()
        for src_line in body.splitlines():
            src_m = _SOURCE_IDS_LINE_RE.match(src_line)
            if src_m:
                source_ids = set(_split_source_id_tokens(src_m.group(1)))
                break
        findings.append({
            "id": inv_id, "title": title, "location": loc,
            "severity": sev, "file": file_part,
            "_lines": line_range, "_source_ids": source_ids,
            "_func": func_name,
            "_block": f"### Finding [{inv_id}]: {title}\n{body}".rstrip(),
        })

    # Group by file (for location/title/anchor signals — same-file only)
    file_groups: dict[str, list[int]] = {}
    for idx, f in enumerate(findings):
        if f["file"] and f["file"] != "unknown":
            file_groups.setdefault(f["file"], []).append(idx)

    # Use a set of sorted (id_a, id_b) tuples to deduplicate —
    # a pair can qualify on MULTIPLE signals.
    seen_pairs: set[tuple[str, str]] = set()
    pairs: list[tuple[dict, dict, float, str]] = []

    # ── Same-file signals: location overlap, title overlap, anchor ──
    for file_part, indices in file_groups.items():
        for i, idx_a in enumerate(indices):
            for idx_b in indices[i + 1:]:
                fa, fb = findings[idx_a], findings[idx_b]
                pair_key = (fa["id"], fb["id"])
                if pair_key in seen_pairs:
                    continue

                reasons: list[str] = []

                # Signal 1: location overlap (primary)
                lr_a, lr_b = fa["_lines"], fb["_lines"]
                if lr_a and lr_b and _line_ranges_overlap(lr_a, lr_b):
                    reasons.append(
                        f"location overlap (L{lr_a[0]}-{lr_a[1]} vs L{lr_b[0]}-{lr_b[1]})"
                    )

                # Signal 2: title overlap (secondary)
                score = _titles_overlap_score(fa["title"], fb["title"])
                anchors = _shared_anchor_tokens(fa["title"], fb["title"])
                if score >= 0.50:
                    reasons.append(f"title overlap {score:.2f}")
                elif anchors:
                    reasons.append(
                        f"shared identifier: {', '.join(sorted(anchors))}"
                    )

                # Signal 5: function-name match (tertiary)
                if fa["_func"] and fb["_func"] and fa["_func"] == fb["_func"]:
                    reasons.append(f"same function: {fa['_func']}")

                if reasons:
                    seen_pairs.add(pair_key)
                    pairs.append((fa, fb, score, " + ".join(reasons)))

    # ── Cross-file signals: source-ID subset, PERT-* lineage ──
    # These fire across ANY pair (same or different file).
    _PERT_RE = re.compile(r"^PERT-\d+$", re.IGNORECASE)
    for i in range(len(findings)):
        for j in range(i + 1, len(findings)):
            fa, fb = findings[i], findings[j]
            pair_key = (fa["id"], fb["id"])
            if pair_key in seen_pairs:
                continue

            cross_reasons: list[str] = []

            # Signal 3: source-ID subset — if A's source IDs are a
            # non-empty proper subset of B's (or vice versa), A is
            # likely a partial view of the same bug that B covers
            # more completely.
            sa, sb = fa["_source_ids"], fb["_source_ids"]
            if sa and sb:
                if sa < sb:
                    cross_reasons.append(
                        f"source-ID subset ({', '.join(sorted(sa))} ⊂ {', '.join(sorted(sb))})"
                    )
                elif sb < sa:
                    cross_reasons.append(
                        f"source-ID subset ({', '.join(sorted(sb))} ⊂ {', '.join(sorted(sa))})"
                    )
                elif sa & sb and sa != sb:
                    overlap = sa & sb
                    cross_reasons.append(
                        f"source-ID overlap ({', '.join(sorted(overlap))} shared)"
                    )

            # Signal 4: PERT-* lineage — a PERT finding is a documented
            # derivative of a parent depth finding. If A's source IDs
            # contain a PERT-* token and B's source IDs contain the
            # parent of that PERT (or vice versa), they are lineage-linked.
            # Also pair if both source sets reference overlapping depth IDs
            # AND one contains PERT-*.
            pert_a = any(_PERT_RE.match(s) for s in sa) if sa else False
            pert_b = any(_PERT_RE.match(s) for s in sb) if sb else False
            if (pert_a or pert_b) and sa & sb:
                cross_reasons.append("PERT lineage (shared depth source IDs)")

            if cross_reasons:
                seen_pairs.add(pair_key)
                score = _titles_overlap_score(fa["title"], fb["title"])
                pairs.append((fa, fb, score, " + ".join(cross_reasons)))

    if not pairs:
        (scratchpad / "dedup_candidate_pairs.md").write_text(
            "# Dedup Candidate Pairs\n\nNo candidate duplicate pairs found.\n",
            encoding="utf-8",
        )
        return 0

    # Sort: source-ID/PERT first, then location-overlap, then title score
    def _sort_key(p: tuple) -> tuple:
        has_src = "source-ID" in p[3] or "PERT" in p[3]
        has_loc = "location overlap" in p[3]
        return (-int(has_src), -int(has_loc), -p[2])

    sorted_pairs = sorted(pairs, key=_sort_key)
    live_pairs = sorted_pairs[:_DEDUP_LIVE_PAIR_LIMIT]

    # Write candidate pairs file
    lines = [
        "# Dedup Candidate Pairs",
        "",
        f"{len(live_pairs)} candidate pair(s) identified for LLM review.",
        "Pairs are identified by five independent signals:",
        "- **Source-ID subset**: one finding's depth source IDs are a proper subset of the other's (strongest — mechanical proof of containment)",
        "- **PERT lineage**: perturbation finding shares depth source IDs with parent (strongest — documented derivative)",
        "- **Location overlap**: same file + line ranges within 15 lines (primary for same-file)",
        "- **Title overlap / shared identifiers**: same file + ≥0.50 token overlap (secondary)",
        "- **Function-name match**: same file + same function name from Location field (tertiary)",
        "",
        "| Finding A | Finding B | Title Score | Signal(s) | Same Sev? |",
        "|-----------|-----------|-------------|-----------|-----------|",
    ]
    if len(live_pairs) < len(pairs):
        lines[3:3] = [
            "",
            f"Bounded work packet: showing top {len(live_pairs)} of {len(pairs)} candidate pair(s).",
            "The full candidate set is preserved in `dedup_candidate_pairs_full.md`.",
            "Treat omitted pairs as deferred, not silently discarded.",
        ]

    for fa, fb, score, reason in live_pairs:
        same_sev = "Yes" if fa["severity"].lower() == fb["severity"].lower() else "No"
        lines.append(
            f"| {fa['id']}: {fa['title'][:50]} | "
            f"{fb['id']}: {fb['title'][:50]} | "
            f"{score:.2f} | {reason} | {same_sev} |"
        )
    lines.append("")

    (scratchpad / "dedup_candidate_pairs.md").write_text(
        "\n".join(lines), encoding="utf-8",
    )
    if len(live_pairs) < len(pairs):
        full_lines = [
            line
            for idx, line in enumerate(lines)
            if idx not in {3, 4, 5, 6}
        ]
        full_lines[2] = f"{len(pairs)} candidate pair(s) identified for LLM review."
        if full_lines and full_lines[-1] == "":
            full_lines.pop()
        for fa, fb, score, reason in sorted_pairs[_DEDUP_LIVE_PAIR_LIMIT:]:
            same_sev = "Yes" if fa["severity"].lower() == fb["severity"].lower() else "No"
            full_lines.append(
                f"| {fa['id']}: {fa['title'][:50]} | "
                f"{fb['id']}: {fb['title'][:50]} | "
                f"{score:.2f} | {reason} | {same_sev} |"
            )
        full_lines.append("")
        (scratchpad / "dedup_candidate_pairs_full.md").write_text(
            "\n".join(full_lines), encoding="utf-8",
        )

    focus_ids = {
        item["id"]
        for fa, fb, _score, _reason in live_pairs
        for item in (fa, fb)
    }
    if focus_ids:
        focus_lines = [
            "# Dedup Focus Inventory",
            "",
            "This bounded file contains the full finding bodies for the IDs "
            "referenced by `dedup_candidate_pairs.md`. Use it for semantic "
            "review before falling back to the full inventory.",
            "",
        ]
        for f in findings:
            if f["id"] in focus_ids:
                focus_lines.append(str(f.get("_block", "")).rstrip())
                focus_lines.append("")
        (scratchpad / "dedup_focus_inventory.md").write_text(
            "\n".join(focus_lines).rstrip() + "\n",
            encoding="utf-8",
        )
    return len(pairs)


_DEPTH_PROMOTION_FILES = (
    "depth_consensus_invariant_findings.md",
    "depth_state_trace_findings.md",
    "depth_edge_case_findings.md",
    "depth_external_findings.md",
    # v2.8.9: depth_token_flow is a CORE SC depth channel (DT-* ids) but was
    # absent from this list (the list was authored L1-first, where token-flow
    # does not load), so DT-* depth-only findings were never read by the
    # promotion bridge and silently dropped. Added for SC parity.
    "depth_token_flow_findings.md",
    "depth_network_surface_findings.md",
    "depth_iter2_*_findings.md",
    "depth_iter3_*_findings.md",
    "depth_da_*_findings.md",
    "design_stress_findings.md",
    "perturbation_findings.md",
    "attention_repair_findings.md",
    # SC subproducer/feeders. These are written by nested scanner, niche,
    # rescan, per-contract, fuzz, and validation agents; they must receive the
    # same feeder->inventory parity treatment as L1 depth outputs.
    "analysis_rescan_*.md",
    "analysis_percontract_*.md",
    "blind_spot_*_findings.md",
    "scanner_*_findings.md",
    "niche_*_findings.md",
    "validation_sweep_findings.md",
    "scanner_validation_findings.md",
    "sibling_propagation_findings.md",
    "medusa_fuzz_findings.md",
    "trident_fuzz_findings.md",
    "cargo_fuzz_findings.md",
)


_PROMOTABLE_FEEDER_ID_PATTERN = (
    r"(?:"
    # L1/depth/feeders
    r"DCI-\d+|DEC-\d+|DST-\d+|DX-\d+|DN-\d+|"
    r"DNS-\d+|DA-[A-Z0-9_-]+-\d+|DA\d+-[A-Z0-9_-]+-\d+|"
    r"PERT-\d+|ATT-\d+|"
    # SC depth/scanner channels that actually emit DS-/DE-/DT-/BLIND- ids
    # (v2.8.9: these prefixes were ABSENT, so the depth-promotion bridge and its
    # receipt gate parsed 0 findings from depth_state_trace / depth_edge_case /
    # depth_token_flow / blind_spot_* and silently dropped depth-only findings —
    # incl. a CONFIRMED High on the DODO run. DST-=design_stress and DEC-/DX- are
    # distinct; DS-/DE-/DT- require a digit so they cannot match inside DST-/DEC-/DX-N.)
    r"DS-\d+|DE-\d+|DT-\d+|BLIND-[A-Z]?-?\d+|"
    # SC scanner/fuzz/tool outputs
    r"SLITHER-\d+|FUZZ-\d+|MEDUSA-\d+|RSW-\d+|SP-\d+|"
    # SC niche/injectable skill prefixes. Deliberately excludes public report
    # IDs C/H/M/L/I-N so client-facing report IDs are not treated as internal
    # feeder IDs by leak/promotion gates.
    r"(?:AA|AB|AC|AL|AR|AV|BLS|BS|CBS|CCT|CFG|CI|CM|CMI|CPI|CR|CS|CT|CU|"
    r"DEP|DEX|ED|EDA|EIP|EN|EP|EPA|EVT|EX|FA|FC|FL|GO|GOV|HF|IHR|II|LC|"
    r"LEND|MG|MP|MSS|NFT|NS|OD|OF|OO|OR|P2P|PDA|PSC|PTB|PV|RE|REENT|REF|"
    r"RPC|RS|SA|SAF|SCOUT|SE|SGI|SHIFT|SIG|SL|SLS|SR|SS|SSC|ST|STATIC|STR|"
    r"T22|TF|TPS|TS|TXI|VA|VL|VS|WED|XE|XFER|ZS)-\d+"
    r")"
)


def _parse_depth_confidence_scores(scratchpad: Path) -> dict[str, float]:
    scores: dict[str, float] = {}
    for p in sorted(scratchpad.glob("confidence_scores*.md")):
        try:
            text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        table_id_idx: int | None = None
        table_composite_idx: int | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if cells and all(set(c) <= {"-", ":"} for c in cells):
                    continue
                norm_headers = [
                    re.sub(r"[^a-z0-9]+", "", c.lower()) for c in cells
                ]
                if "findingid" in norm_headers and "composite" in norm_headers:
                    table_id_idx = norm_headers.index("findingid")
                    table_composite_idx = norm_headers.index("composite")
                    continue
                if (
                    table_id_idx is not None
                    and table_composite_idx is not None
                    and len(cells) > max(table_id_idx, table_composite_idx)
                ):
                    ids = re.findall(
                        r"\b" + _PROMOTABLE_FEEDER_ID_PATTERN + r"\b",
                        cells[table_id_idx],
                    )
                    m = re.search(
                        r"(?<![A-Za-z0-9.\-])(?:0?\.\d+|1\.0+)(?![A-Za-z0-9.])",
                        cells[table_composite_idx],
                    )
                    if ids and m:
                        score = float(m.group(0))
                        for fid in ids:
                            scores[fid] = max(scores.get(fid, 0.0), score)
                        continue
            elif stripped:
                table_id_idx = None
                table_composite_idx = None
            ids = re.findall(r"\b" + _PROMOTABLE_FEEDER_ID_PATTERN + r"\b", line)
            if not ids:
                continue
            # Closes F-PROM-02: require a decimal point so the trailing `1`
            # in `DCI-1` is not parsed as confidence=1.0. A confidence column
            # always uses dotted form (0.85, 1.0). Bare integers are
            # ambiguous with finding-ID suffixes.
            nums = [
                float(x)
                for x in re.findall(
                    r"(?<![A-Za-z0-9.\-])(?:0?\.\d+|1\.0+)(?![A-Za-z0-9.])",
                    line,
                )
            ]
            if nums:
                score = max(nums)
                for fid in ids:
                    scores[fid] = max(scores.get(fid, 0.0), score)
    return scores


def _parse_depth_finding_blocks(path: Path) -> list[dict[str, str]]:
    try:
        text = _llm_norm(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    lines = text.splitlines()
    starts = []
    heading_re = re.compile(
        r"^\s*#{2,4}\s+(?:Finding\s*)?\[?"
        r"(" + _PROMOTABLE_FEEDER_ID_PATTERN + r")\]?",
        re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        if heading_re.search(line):
            starts.append(i)
    out: list[dict[str, str]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        m = heading_re.search(lines[start])
        if not m:
            continue
        fid = m.group(1).upper()
        title = re.sub(r"^\s*#{2,4}\s+", "", lines[start]).strip()
        title = re.sub(r"^(?:Finding\s*)?\[?" + re.escape(fid) + r"\]?\s*:?\s*", "", title, flags=re.I).strip()
        title = title or fid
        sev = _field_from_markdown(block, ("Severity", "Final Severity")) or "Medium"
        loc = _field_from_markdown(block, ("Location", "Locations"))
        if not loc:
            lm = re.search(
                r"\b([A-Za-z0-9_./\\-]+\.(?:rs|go|sol|move|py|c|cpp|cc|h|hpp|java|ts|js):L?\d+)\b",
                block,
            )
            loc = lm.group(1) if lm else "unknown"
        tag = _field_from_markdown(
            block, ("Preferred Tag", "Evidence Tag", "Evidence Tags", "Evidence")
        )
        verdict = _field_from_markdown(block, ("Verdict", "Final Verdict", "Status"))
        sev_clean = _strip_md(sev)
        verdict_clean = _strip_md(verdict)
        if _non_reportable_marker(sev_clean) or _non_reportable_marker(verdict_clean):
            sev_clean = "Informational"
            if not verdict_clean:
                verdict_clean = "REFUTED"
        elif _ambiguous_na_marker(sev_clean):
            sev_clean = "Informational"
            if not verdict_clean:
                verdict_clean = "UNRESOLVED"
        desc = _field_from_markdown(block, ("Description", "Root Cause", "Impact"))
        if not desc:
            body_lines = [
                x.strip() for x in block.splitlines()[1:]
                if x.strip() and not x.strip().startswith("|")
            ]
            desc = " ".join(body_lines[:3])[:600] if body_lines else "Depth finding promoted for verification."
        # Extract referenced depth IDs from the block body (excluding self).
        # PERT findings reference their parent (e.g. DCI-3), DA iter2 may
        # reference iter1 IDs.  These become additional Source IDs on promotion
        # so dedup's source-ID-subset / PERT-lineage signals fire correctly.
        _ref_ids = set(
            re.findall(r"\b" + _PROMOTABLE_FEEDER_ID_PATTERN + r"\b", block)
        )
        _ref_ids.discard(fid)
        out.append({
            "id": fid,
            "title": _strip_md(title),
            "severity": sev_clean.capitalize(),
            "location": _norm_loc(loc),
            "preferred_tag": _extract_first_tag(tag) or _strip_md(tag) or "CODE-TRACE",
            "verdict": verdict_clean,
            "description": _strip_md(desc),
            "source_file": path.name,
            "_referenced_ids": sorted(_ref_ids),
            **_extract_optional_finding_metadata(block),
        })
    return out


# --- v2.3.0 SCIP experiment: coverage gap + path-existence gates -----------
#
# Two driver-side gates exercising the existing SCIP prebake artifacts to
# address two of the three RC buckets identified in the v2.2.2 post-mortem:
#
#   Bucket A — subsystem coverage gaps (~20 misses on Irys L1 v2.2.2):
#     Recon-flagged in-scope source files received zero depth-agent
#     citations. Driver enumerates source files via the SCIP repo_map,
#     diffs against citation set, surfaces uncited Medium+ files for iter2.
#
#   Bucket C — path hallucination (~3 GT-finding losses on same run):
#     Verify pool had 30% locations corrected (path mismatches) per
#     cross_batch_consistency. Some real bugs killed as FP because the
#     cited path didn't exist. Driver pre-checks every cited path against
#     the SCIP-indexed file set before verify spawns.
#
# Generic across L1 and SC. No protocol-specific knowledge. Reads only
# already-existing prebake artifacts (scip/repo_map.md / repo_map_full.md)
# plus the depth/breadth/scanner finding outputs.
#
# Validation strategy: SOFT in this v2.3.0 round — gates write directive
# files but only emit informational issues (no hard fail). Next post-
# mortem measures whether the directives close any of the residual gap.
# If yes, hard-gate them in v2.3.1.
_SCIP_REPO_MAP_FILES = ("repo_map.md", "repo_map_full.md")


_FINDING_GLOBS_FOR_CITATION = (
    "analysis_*.md",
    "analysis_rescan_*.md",
    "analysis_percontract_*.md",
    "coverage_fill_*.md",
    "panic_audit_*.md",
    "panic_audit_summary.md",
    "symmetric_pair_findings.md",
    "field_validation_matrix.md",
    "primitive_correctness_findings.md",
    "network_amplification_findings.md",
    "lifecycle_replay_findings.md",
    "attention_repair*.md",
    "attention_repair_rows_*.md",
    "findings_inventory*.md",
    "depth_*_findings.md",
    "depth_iter2_*_findings.md",
    "depth_iter3_*_findings.md",
    "depth_da_*_findings.md",
    "attention_repair*.md",
    "attention_repair_rows_*.md",
    "breadth_*_findings.md",
    "blind_spot_*_findings.md",
    "scanner_*_findings.md",
    "niche_*_findings.md",
    "validation_sweep_findings.md",
    "scanner_validation_findings.md",
    "design_stress_findings.md",
    "perturbation_findings.md",
    "verify_*.md",
)


def _extract_gap_paths_from_markdown(text: str) -> list[str]:
    path_re = re.compile(
        r"\b([A-Za-z0-9_./\\-]+\.(?:rs|go|sol|move|py|c|cpp|cc|h|hpp|java|ts|js))\b"
    )
    out: list[str] = []
    seen: set[str] = set()
    for m in path_re.finditer(text):
        p = m.group(1).replace("\\", "/").strip("`")
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


# --- v2.2.0 A.1: skill-step execution trace gate ---------------------------
#
# Failure mode (post-mortem RC-AGENT class, ~22 of 46 misses on Irys L1):
# depth agents inherit 6-12 skills and produce 7-15 findings, but the
# findings concentrate on 2-3 skills per agent. Other inherited skills get
# zero attention and entire numbered sections (e.g. RPC_SURFACE_AUDIT §1-6,
# GOSSIP_CACHE_INVARIANCE §1-6) are never executed. The existing
# `skill_execution_gaps.md` is LLM-judged AFTER the fact and unreliable.
#
# A.1 makes execution mechanically traceable:
#   1. Each Thorough depth agent writes `step_execution_trace_{agent}.md`
#      with one pipe-row per (skill, step) it inherited:
#        | Skill | Step | Executed | Evidence | Result |
#      Allowed Executed values: yes / partial / no
#      Evidence MUST be a `file:line` token (or `-` for skip with reason).
#   2. The driver aggregates all traces into
#      `step_execution_gaps_mechanical.md` listing every (skill, step)
#      with Executed != yes — this is the iter2 directive.
#   3. The existing LLM-driven `phase4b-skill-checklist.md` agent now
#      reads the mechanical aggregate first and only synthesizes for
#      what the trace doesn't already cover.
#
# Generic across L1 and SC. The skill section structure (`## N. Title` +
# `Tag: \`[TAG-NAME]\``) is already present in 162 sections / 140 tags
# across L1 skills — no skill rewrites needed. SC skills follow the same
# convention; the gate works there too once SC depth-loop adopts the
# §STEP-TRACE directive.
_STEP_TRACE_GLOB = "step_execution_trace_*.md"


def _parse_step_trace_rows(text: str) -> list[dict[str, str]]:
    """Parse a step-execution-trace markdown file into row dicts.

    Tolerates header variations and case. Recognized columns (in any
    order, identified by header text): Skill, Step, Executed, Evidence,
    Result. Returns rows that contain at least Skill + Step + Executed.
    """
    rows: list[dict[str, str]] = []
    lines = text.splitlines()
    headers: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        # Skip pure-separator rows (---|---|---).
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        if not headers:
            # First non-separator pipe row defines the header set.
            lc = [c.lower() for c in cells]
            if any("skill" in c for c in lc) and any("step" in c for c in lc):
                headers = lc
                continue
            # If no recognizable header yet, ignore the row.
            continue
        row = {}
        for i, cell in enumerate(cells):
            if i < len(headers):
                for known in ("skill", "step", "executed", "evidence", "result"):
                    if known in headers[i]:
                        row[known] = cell
                        break
        if "skill" in row and "step" in row and "executed" in row:
            rows.append(row)
    return rows


def _aggregate_step_execution_gaps(
    scratchpad: Path,
) -> tuple[list[dict[str, str]], list[str]]:
    """Aggregate all step_execution_trace_*.md files into a gap list.

    Returns:
        (gaps, agent_names_with_traces)
        gaps: list of {agent, skill, step, executed, evidence, result}
              dicts where executed != "yes". Includes "no", "partial",
              and any other non-yes value.
        agent_names_with_traces: file-name-derived agent identifiers
              (so the caller can detect missing traces).
    """
    gaps: list[dict[str, str]] = []
    agents: list[str] = []
    for f in sorted(scratchpad.glob(_STEP_TRACE_GLOB)):
        agent = f.name.replace("step_execution_trace_", "").replace(
            ".md", ""
        )
        agents.append(agent)
        try:
            text = _llm_norm(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for row in _parse_step_trace_rows(text):
            if row.get("executed", "").lower() != "yes":
                gaps.append({
                    "agent": agent,
                    "skill": row.get("skill", ""),
                    "step": row.get("step", ""),
                    "executed": row.get("executed", ""),
                    "evidence": row.get("evidence", ""),
                    "result": row.get("result", ""),
                })
    return gaps, agents


def _expected_depth_agent_roles(scratchpad: Path) -> list[str]:
    """Infer which depth agents the run actually used from finding files.

    Strategy: list every `depth_{role}_findings.md` (NOT iter2/iter3/da
    variants) and return the role names. Avoids re-parsing
    template_recommendations.md or hardcoding role lists — generic.

    Exclusion: gap-fill remediation agents (`depth_coverage_*_findings.md`)
    are spawned by the orchestrator in response to the v2.3.0 NOTREAD
    priority coverage gate — they cite uncovered files mechanically rather
    than execute skill methodology, so the §STEP-TRACE directive does not
    apply to them. Including them in the expected-role list creates a
    gate-vs-gate collision (NOTREAD forces them into existence; step-trace
    then punishes them for not having a trace). They are still subject to
    their own telemetry gate (≥2 pre-baked SCIP reads per agent).
    """
    roles: list[str] = []
    for f in sorted(scratchpad.glob("depth_*_findings.md")):
        name = f.name
        # Exclude iteration / Devil's-Advocate variants. Both the abbreviated
        # (`iter2`) and spelled-out (`iteration2`) tokens must be listed —
        # `iteration2` does NOT contain the substring `iter2`, so an
        # un-canonicalized `depth_edge_case_iteration2_findings.md` would
        # otherwise be mis-parsed as a phantom role `edge_case_iteration2`
        # (observed on the DODO audit graph-consumption warning).
        if any(
            tok in name for tok in (
                "iter2", "iter3", "iteration2", "iteration3",
                "_da_", "depth_da",
            )
        ):
            continue
        if name == "depth_findings.md":
            continue
        # depth_{role}_findings.md → role
        role = name[len("depth_"): -len("_findings.md")]
        if not role:
            continue
        # Non-methodology gap-fill agents (see docstring).
        if role.startswith("coverage_"):
            continue
        roles.append(role)
    return roles


# Ship A: single source of truth in plamen_types (was a divergent local copy).
_DEPTH_EVIDENCE_TAG_RE = DEPTH_EVIDENCE_TAG_RE


# --- v2.2.0 A.4: NOTREAD priority coverage gate ----------------------------
#
# Recon's `scope_leftover.md` lists every in-scope source file with one of:
#   READ     — opened and analyzed by recon
#   STUB     — subsystem noted, internals not read
#   NOTREAD  — not opened (these are depth-agent priorities by construction)
#
# Failure mode observed in Irys L1 v2.1.7: 13 NOTREAD priority files, only ~7
# received any depth coverage. The other 6 silently went unaudited (a class of
# RC-SCOPE misses per post-mortem). v2.2.0 fix: after iter1 depth, identify
# any NOTREAD file with zero citations across all depth/breadth/scanner
# finding outputs and surface as a directive for the next phase to address.
# Generic across L1 and SC (recon writes the same schema for all targets).
_NOTREAD_FINDING_GLOBS = (
    "depth_*_findings.md",
    "depth_iter2_*_findings.md",
    "depth_iter3_*_findings.md",
    "depth_da_*_findings.md",
    "breadth_*_findings.md",
    "blind_spot_*_findings.md",
    "scanner_*_findings.md",
    "niche_*_findings.md",
    "validation_sweep_findings.md",
    "scanner_validation_findings.md",
)


_PATH_CELL_EXTENSIONS = (
    ".rs", ".go", ".sol", ".move", ".py", ".c", ".h", ".cpp", ".cc",
    ".java", ".ts", ".js",
)


_UNCOVERED_STATUS_TOKENS = {"NOTREAD", "NOT_READ", "UNREAD", "UNCOVERED", "MISSED"}


# Coverage status tokens that mean "this file IS covered" (Schema 1 dialect).
# When a row contains any of these, it is NOT uncovered regardless of empty
# Acknowledged column — prevents Schema-2 fallback from misclassifying
# Schema-1 READ/STUB rows.
_COVERED_STATUS_TOKENS = {"READ", "STUB", "CITED", "COVERED", "ANALYZED"}


def _is_path_cell(cell: str) -> str | None:
    """Return a normalized path if `cell` looks like a source file path."""
    stripped = cell.strip("`").strip()
    if not stripped or " " in stripped:
        return None
    stripped = stripped.replace("\\", "/")
    if "/" in stripped or stripped.endswith(_PATH_CELL_EXTENSIONS):
        return stripped
    return None


def _parse_notread_files(scope_leftover_text: str) -> list[str]:
    """Extract paths flagged uncovered by recon's scope_leftover.md.

    Tolerates schema variation observed in production:
      Schema 1 (L1 numbered):    | # | File | Coverage | Notes |   with NOTREAD cell
      Schema 2 (recon template): | File | LOC | Reason | Acknowledged |   no ack = uncovered
      Schema 3 (variants):       NOT_READ / UNREAD / UNCOVERED / MISSED in any cell

    v2.2.3 widening — pre-v2.2.3 only matched literal "NOTREAD" string.
    Live failure mode (Irys L1 v2.2.0): the recon-prompt template schema
    (Schema 2) doesn't use NOTREAD at all; rows without ACKNOWLEDGED are
    the uncovered ones. Parser missed them entirely.

    Strategy:
      1. If row has any uncovered-status token → uncovered.
      2. ELSE if row matches Schema 2 (path-cell + LOC-cell + ack-cell where
         ack is empty/blank/`-`) → uncovered.
      3. ELSE skip.
    """
    files: list[str] = []
    for line in scope_leftover_text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if _is_separator_row(s):
            continue
        upper = s.upper()
        # Skip the column-header row of either schema.
        if (
            ("FILE" in upper and "COVERAGE" in upper)
            or ("FILE" in upper and "ACKNOWLEDGED" in upper)
            or ("FILE" in upper and "REASON" in upper and "LOC" in upper)
        ):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2:
            continue

        # Schema 1 / 3: explicit uncovered-status cell.
        if any(c.upper() in _UNCOVERED_STATUS_TOKENS for c in cells):
            for cell in cells:
                p = _is_path_cell(cell)
                if p:
                    files.append(p)
                    break
            continue

        # Schema 1 dialect short-circuit: if any cell is a covered-status
        # token (READ / STUB / CITED / COVERED / ANALYZED), the row is
        # explicitly covered — do NOT fall through to Schema 2's
        # empty-ack heuristic which would misclassify Schema 1 rows.
        if any(c.upper() in _COVERED_STATUS_TOKENS for c in cells):
            continue

        # Schema 2: | File | LOC | Reason | Acknowledged | with empty ack.
        # Heuristic: path-cell + at least one numeric-LOC cell + last cell
        # is empty / "-" / not starting with ACK-family token.
        path = None
        loc_seen = False
        for cell in cells:
            p = _is_path_cell(cell)
            if p and not path:
                path = p
                continue
            # v2.3.5 P4: tolerate "1,234" (thousands separator), "~500"
            # (approximate), "500 lines"/"500 LOC" (with units). Pre-v2.3.5
            # `re.fullmatch(r"\d{1,7}", cell)` rejected all of these → row
            # fell through schema detection → NOTREAD priority gaps silently
            # under-reported.
            if cell and re.fullmatch(
                r"[~≈]?\s*[\d,]{1,9}(?:\s*(?:lines?|LOC|loc))?", cell.strip()
            ):
                loc_seen = True
        if not path or not loc_seen:
            continue
        last = cells[-1].strip()
        last_lc = last.lower()
        ack_ok = (
            last.upper().startswith("ACKNOWLEDGED")
            or last.upper().startswith("ACK")
            or "leftover-ack" in last_lc
            or "cited" in last_lc
            or "covered" in last_lc
            or "✓" in last
        )
        if ack_ok:
            continue
        # Not acknowledged + has path + has LOC → treat as uncovered.
        files.append(path)
    return files


def _parse_uncovered_from_ledger(ledger_text: str) -> list[str]:
    """Extract paths from `file_coverage_ledger.md`'s `## Uncovered Files` section.

    Schema (per ~/.claude/prompts/l1/phase1-recon-prompt.md):
      ## Uncovered Files (MUST resolve before depth)
      | File | LOC | Top-Level Module | Proposed Action |
      | eth/downloader/skeleton.go | 420 | eth | ADD citation ... |
    """
    files: list[str] = []
    section_re = re.compile(
        r"(?im)^##\s+Uncovered\s+Files.*?(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = section_re.search(ledger_text)
    if not m:
        return files
    for line in m.group(0).splitlines():
        s = line.strip()
        if not s.startswith("|") or _is_separator_row(s):
            continue
        upper = s.upper()
        if "FILE" in upper and ("LOC" in upper or "MODULE" in upper):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        for cell in cells:
            p = _is_path_cell(cell)
            if p:
                files.append(p)
                break
    return files


# --- v2.1.2: end-silent-degradation helpers ---------------------------------
# Foundry / npm library paths that are always out-of-scope by convention.
# scope_leftover entries under these paths are auto-acknowledged without
# requiring a human/LLM-authored ACK string. Prevents the AwesomeX-class
# false recon degradation where forge-std / v2-periphery / v3-core entries
# triggered the coverage gate despite being obvious out-of-scope deps.
_SCOPE_LEFTOVER_LIB_WHITELIST = (
    # Generic non-production/test-support paths.
    "test/",
    "tests/",
    "testing/",
    "testdata/",
    "fixtures/",
    "fixture/",
    "examples/",
    "benches/",
    "bench/",
    "crates/testing-",
    "crates/testing_",
    "crates/test-",
    "crates/test_",
    "crates/test-utils/",
    "crates/test_utils/",
    "crates/mock-",
    "crates/mock_",
    "lib/forge-std/",
    "lib/openzeppelin-contracts/",
    "lib/openzeppelin-contracts-upgradeable/",
    "lib/v2-periphery/",
    "lib/v2-core/",
    "lib/v3-periphery/",
    "lib/v3-core/",
    "lib/solmate/",
    "lib/solady/",
    "lib/prb-math/",
    "lib/chainlink/",
    "lib/ccip/",
    "lib/murky/",
    "lib/ds-test/",
    "lib/permit2/",
    "node_modules/",
    "dependencies/",
    "vendor/",
)


def _is_whitelisted_lib_path(file_name: str) -> bool:
    """Return True when `file_name` sits under a known out-of-scope lib dir.

    Path separators are normalized so Windows-style `lib\\forge-std\\...` and
    POSIX `lib/forge-std/...` both match.
    """
    norm = file_name.replace("\\", "/").lstrip("./")
    return any(norm.startswith(p) for p in _SCOPE_LEFTOVER_LIB_WHITELIST)


# --- v2.2.0 A.2: promotion-receipt symmetry gate ---------------------------
#
# Failure mode (post-mortem RC-PIPELINE-DROPOUT class): a verifier emits
# CONFIRMED for a finding, but the report tier writer never produces a body
# section for it AND the index agent never records it as FALSE_POSITIVE in
# Appendix A. The finding is silently lost between Phase 5 and Phase 6.
# v2.1.9's quality gate compares body section counts to summary counts but
# does not assert "every CONFIRMED verifier verdict reaches the report."
#
# A.2 closes this by:
#   1. Mining all verify_*.md files for CONFIRMED verdicts and the finding
#      IDs they cover (regex-extracted, no agent ceremony).
#   2. Diffing that set against (body finding refs ∪ Appendix-A excluded ∪
#      consolidated-into).
#   3. Symmetric difference > 0 → gate fail with a delta-injected retry
#      hint listing the dropped IDs by name (uses the v2.1.6 retry-hint
#      mechanism so the next tier-writer attempt has the missing IDs).
#
# Generic across L1 and SC. Reads only what verifiers and report-writers
# already produce. No new artifacts required from agents.
# v2.3.5 P3: tolerate leading whitespace, bullet markers, and bold wrappers.
# The standard finding-output-format.md prescribes `**Verdict**: CONFIRMED`
# (bold), and tier writers / inventory blocks routinely emit `- Verdict: ...`
# (bullet) or `  Verdict: ...` (indented inside a section). The pre-v2.3.5
# `^Verdict` anchor missed every one of these valid forms, silently dropping
# CONFIRMED IDs from `_collect_verify_promotion_receipts` and any downstream
# consumer that depends on this regex.
_VERIFY_CONFIRMED_VERDICT_RE = re.compile(
    r"^\s*[-*]?\s*(?:\*{1,2})?(?:Verdict|verdict|VERDICT)(?:\*{1,2})?"
    r"\s*[:=]\s*(?:\*{1,2})?\s*(?:`)?CONFIRMED\b",
    re.MULTILINE | re.IGNORECASE,
)


# v2.4.3: derived from unified _ID_ALL_INTERNAL (single source of truth).
_INTERNAL_FINDING_ID_RE = _INTERNAL_ID_RE


def _verifier_status_from_text(text: str) -> str:
    """Best-effort verifier status extraction for report-index recovery.

    Closes F-VRF-01: an empty/whitespace verifier file used to default to
    `CONFIRMED`, manufacturing a passing verdict from missing data. Empty body
    now returns `UNRESOLVED` so the finding is demoted/flagged for human
    review rather than promoted as evidence.

    Defensive: passes input through `_llm_norm` so smart quotes, em-dashes,
    HTML entities, CRLF, etc. don't break verdict extraction on a fresh
    codebase whose LLM happens to emit a format variant we haven't seen.
    """
    text = _llm_norm(text)
    if not (text and text.strip()):
        return "UNRESOLVED"
    field = _field_from_markdown(text, ("Verdict", "Final Verdict", "Status"))
    if field:
        raw = field.strip().strip("`*_").upper()
        status_re = re.compile(
            r"(?<![A-Z])("
            r"APPENDIX[_\s-]*ONLY|DROP[_\s-]*(?:FALSE[_\s-]*POSITIVE|NON[_\s-]*SECURITY|DESIGN[_\s-]*CONFIRMATION|UNACTIONABLE[_\s-]*SPECULATION)|"
            r"SCHEMA[_\s-]*INVALID|LOCATION[_\s-]*INVALID|"
            r"FALSE[_\s-]*POSITIVE|REFUTED|INFEASIBLE|"
            r"CONTESTED|PARTIAL|UNRESOLVED|DUPLICATE|CONSOLIDATED|"
            r"TRUE[_\s-]*POSITIVE|CONFIRMED|VALID"
            r")(?![A-Z])",
            re.IGNORECASE,
        )
        m_field = status_re.search(raw)
        if m_field:
            tok = m_field.group(1).upper().replace(" ", "_").replace("-", "_")
            tok = re.sub(r"_+", "_", tok)
            if tok in ("TRUE_POSITIVE", "VALID"):
                return "CONFIRMED"
            if tok == "CONSOLIDATED":
                return "DUPLICATE"
            if tok == "PARTIAL":
                return "UNRESOLVED"
            return tok
        return raw.replace(" ", "_").replace("-", "_")
    m = re.search(
        r"(?i)\b(APPENDIX[_\s-]*ONLY|DROP[_\s-]*(?:FALSE[_\s-]*POSITIVE|NON[_\s-]*SECURITY|DESIGN[_\s-]*CONFIRMATION|UNACTIONABLE[_\s-]*SPECULATION)|"
        r"SCHEMA_INVALID|LOCATION_INVALID|FALSE\s*POSITIVE|FALSE_POSITIVE|REFUTED|INFEASIBLE|"
        r"CONTESTED|PARTIAL|UNRESOLVED|DUPLICATE|CONSOLIDATED|"
        r"TRUE\s*POSITIVE|TRUE_POSITIVE|CONFIRMED|VALID)\b",
        text or "",
    )
    if not m:
        # Closes BS.header-only / BS.no-verdict-tokens: a verify file with
        # content but NO verdict token (e.g., a markdown header alone, or
        # only Evidence Tag / Severity fields) used to default to CONFIRMED.
        # That's silent semantic manufacturing — same class as the empty-
        # body bug. Treat absence-of-verdict as UNRESOLVED so downstream
        # demotes the finding and flags it for human review.
        return "UNRESOLVED"
    tok = m.group(1).upper().replace(" ", "_")
    if tok in ("TRUE_POSITIVE", "VALID"):
        return "CONFIRMED"
    if tok == "CONSOLIDATED":
        return "DUPLICATE"
    if tok == "PARTIAL":
        return "UNRESOLVED"
    return tok


def _severity_name_from_text(text: str, queue_row: dict[str, str]) -> str:
    sev = (
        _field_from_markdown(text, ("Severity", "Final Severity"))
        or queue_row.get("severity", "")
        or "Medium"
    ).strip()
    return normalize_severity(sev)


def _report_prefix_for_severity(severity: str) -> str:
    return severity_letter_from_name(severity)


def _verify_file_for_id(scratchpad: Path, finding_id: str) -> Path:
    """Return the existing verifier file for an ID across naming variants."""
    fid = _normalize_finding_id(finding_id) or (finding_id or "").strip()
    if not fid:
        return scratchpad / "__invalid_verify_id__.md"
    for name in (
        f"verify_{fid}.md",
        f"verify_F-{fid}.md",
        f"verify_F_{fid}.md",
        f"verify_[{fid}].md",
    ):
        p = scratchpad / name
        if p.exists():
            return p
    return scratchpad / f"verify_{fid}.md"


def _next_report_id_counters(scratchpad: Path) -> dict[str, int]:
    counters = {"C": 0, "H": 0, "M": 0, "L": 0, "I": 0}
    for row in parse_report_index_assignments(scratchpad):
        rid = (row.get("report_id") or "").strip().upper()
        m = re.match(r"^([CHMLI])-(\d+)$", rid)
        if m:
            counters[m.group(1)] = max(counters[m.group(1)], int(m.group(2)))
    return counters


def _find_report_index_cut_for_active_recovery(text: str) -> int:
    """Insert active recovery rows before excluded/non-reportable sections."""
    cut_re = re.compile(
        r"(?im)^\s*#{1,4}\s+.*(?:excluded|false\s*positive|refuted|appendix|"
        r"consolidation map|non-reportable|not reportable|traceability).*$"
    )
    m = cut_re.search(text)
    return m.start() if m else len(text)


def _is_reportable_verdict(status: str) -> bool:
    status = (status or "").upper()
    if any(tok in status for tok in (
        "APPENDIX_ONLY",
        "DROP_FALSE_POSITIVE",
        "DROP_NON_SECURITY",
        "DROP_DESIGN_CONFIRMATION",
        "DROP_UNACTIONABLE_SPECULATION",
        "FALSE_POSITIVE",
        "REFUTED",
        "INFEASIBLE",
        "SCHEMA_INVALID",
        "LOCATION_INVALID",
    )):
        return False
    if "DUPLICATE" in status or "CONSOLIDATED" in status:
        return False
    return True


def _demote_severity_once(severity: str) -> str:
    """Demote one tier with Low and Informational floors.

    Closes F-SEV-02: pre-fix logic capped index at `len(order) - 2 = 3` which
    inflated `Informational` (idx 4) to `Low` (idx 3). Per report-template.md
    A.3, both Low and Informational are floors — they do not demote further.
    Ordering: Critical > High > Medium > Low; Informational is its own floor.
    """
    order = list(SEVERITY_ORDER)
    sev = normalize_severity(severity)
    try:
        idx = order.index(sev)
    except ValueError:
        return "Medium"
    # Floor: Low and Informational stay where they are.
    if idx >= 3:
        return order[idx]
    return order[idx + 1]


# =============================================================================
# Phase B: Severity Matrix Enforcement (per ~/.claude/rules/report-template.md)
#
# Severity = Impact x Likelihood, then downgrade modifiers stack:
#   1) on-chain-only exploit: -1 tier (only when impact is on-chain confined)
#   2) view-function-only impact: cap at Medium
#   3) fully-trusted actor must act maliciously: -1 tier (floor: Informational)
#
# When a verify_*.md provides Impact + Likelihood, the matrix is authoritative
# and overrides any LLM-emitted Severity. When matrix data is absent, fall back
# to current behaviour (preserve queue/LLM severity) so legacy verify files
# continue to work.
# =============================================================================
_MATRIX_IMPACT_LABELS = {"high", "medium", "low", "informational", "info"}


_MATRIX_LIKELIHOOD_LABELS = {"high", "medium", "low"}


def _normalize_matrix_label(value: str | None, allowed: set[str]) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s == "info":
        s = "informational"
    if s not in allowed and s.replace("informational", "info") not in allowed:
        return None
    if s.startswith("info"):
        return "Informational"
    if s.startswith("high"):
        return "High"
    if s.startswith("med"):
        return "Medium"
    if s.startswith("low"):
        return "Low"
    return None


def _compute_matrix_severity(impact: str | None, likelihood: str | None) -> str | None:
    """Return matrix severity for given Impact x Likelihood, or None if unparseable.

    Per report-template.md table:
        High x High   -> Critical
        High x Medium -> High
        High x Low    -> Medium
        Medium x High -> High
        Medium x Med  -> Medium
        Medium x Low  -> Medium
        Low x High    -> Medium
        Low x Medium  -> Low
        Low x Low     -> Low
        Informational x * -> Informational
    """
    i = _normalize_matrix_label(impact, _MATRIX_IMPACT_LABELS)
    l = _normalize_matrix_label(likelihood, _MATRIX_LIKELIHOOD_LABELS)
    if i is None or l is None:
        return None
    if i == "Informational":
        return "Informational"
    table = {
        ("High", "High"): "Critical",
        ("High", "Medium"): "High",
        ("High", "Low"): "Medium",
        ("Medium", "High"): "High",
        ("Medium", "Medium"): "Medium",
        ("Medium", "Low"): "Medium",
        ("Low", "High"): "Medium",
        ("Low", "Medium"): "Low",
        ("Low", "Low"): "Low",
    }
    return table.get((i, l))


def _apply_severity_modifiers(severity: str, modifiers: dict[str, bool]) -> str:
    """Apply downgrade modifiers in fixed order: onchain_only, view_function, fully_trusted.

    - onchain_only: -1 tier (Low/Informational are floors)
    - view_function: cap at Medium (do not promote anything below Medium up)
    - fully_trusted: -1 tier with Informational as floor
    """
    sev = severity
    if modifiers.get("onchain_only"):
        sev = _demote_severity_once(sev)
    if modifiers.get("view_function"):
        # Cap at Medium - severities at or below Medium pass through.
        order = list(SEVERITY_ORDER)
        try:
            idx = order.index(sev)
        except ValueError:
            idx = 2  # default to Medium
        if idx < 2:  # Critical or High -> cap at Medium
            sev = "Medium"
    if modifiers.get("fully_trusted"):
        # Per report-template.md, fully-trusted modifier has Informational as
        # the only floor: Low demotes to Informational, Informational stays.
        order = list(SEVERITY_ORDER)
        try:
            idx = order.index(sev)
        except ValueError:
            idx = 2
        if idx < len(order) - 1:
            sev = order[idx + 1]
    return sev


_MATRIX_IMPACT_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?\*{0,2}Impact\*{0,2}\s*:\s*(High|Medium|Low|Informational|Info)\b",
    re.IGNORECASE | re.MULTILINE,
)


_MATRIX_LIKELIHOOD_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?\*{0,2}Likelihood\*{0,2}\s*:\s*(High|Medium|Low)\b",
    re.IGNORECASE | re.MULTILINE,
)


# DODO May-2026 fix: the original `fully[-\s]?trusted` pattern matched
# explanatory PROSE that REJECTS applying the modifier (e.g., verifier
# wrote "the severity discount for fully-trusted actors applies only
# when… [we don't apply it here]"). This caused a false-positive -1 tier
# demotion in `_apply_severity_modifiers` → driver expected Medium for
# verify_H-9.md but verifier wrote High → provenance gate halted.
# Fix: require an EXPLICIT structured field marker, not free prose. The
# verifier must affirmatively assert the modifier via a recognized
# line-anchored format. Free mentions of "fully-trusted" in narrative
# discussion are ignored.
_MATRIX_TRUST_FULLY_RE = re.compile(
    # Affirmative explicit forms only — narrative mentions of
    # "fully-trusted" in prose discussion don't match. The verifier
    # must use one of these structured patterns to opt into the
    # -1 tier modifier:
    #   `**Trust**: FULLY_TRUSTED`
    #   `**Modifier**: FULLY_TRUSTED`
    #   `Trust Modifier: fully-trusted`
    #   `Trust Adj.: TRUSTED-ACTOR(...)`
    #   `Severity Modifier: fully-trusted`
    #   `Actor: fully-trusted`
    #   `[TRUSTED-ACTOR]` tag
    #   `applies fully-trusted -1 tier`
    r"(?:^\s*(?:\*\*)?(?:Trust\s*Adj\.?|Trust|Modifier|"
    r"Trust\s*Modifier|Severity\s*Modifier|Actor)(?:\*\*)?\s*:?\s*"
    r"(?:FULLY[_\s-]TRUSTED|fully[-\s]?trusted|TRUSTED-ACTOR))|"
    r"(?:\[TRUSTED-ACTOR\])|"
    r"(?:applies\s+(?:the\s+)?fully[-\s]?trusted)",
    re.IGNORECASE | re.MULTILINE,
)


_MATRIX_VIEW_FN_RE = re.compile(
    r"view[-\s]?function[-\s]?only|view[-\s]?function\s+impact",
    re.IGNORECASE,
)


_MATRIX_ONCHAIN_RE = re.compile(
    r"on[-\s]?chain[-\s]?only|on[-\s]?chain\s+only\s+attack|on[-\s]?chain[-\s]?only\s+exploit",
    re.IGNORECASE,
)


def _extract_severity_inputs(verify_text: str) -> dict:
    """Parse Impact, Likelihood, and modifier flags from a verify_*.md body.

    Only literal Impact/Likelihood lines on a leading-key form (`Impact: High`)
    are recognized. Modifier flags are detected by phrase scan in the body --
    these are advisory tags emitted by the verifier methodology, not formal
    fields. Missing data returns empty / None values for graceful fallback.
    """
    text = verify_text or ""
    impact = None
    likelihood = None
    m = _MATRIX_IMPACT_RE.search(text)
    if m:
        impact = m.group(1)
    m = _MATRIX_LIKELIHOOD_RE.search(text)
    if m:
        likelihood = m.group(1)
    modifiers = {
        "onchain_only": bool(_MATRIX_ONCHAIN_RE.search(text)),
        "view_function": bool(_MATRIX_VIEW_FN_RE.search(text)),
        "fully_trusted": bool(_MATRIX_TRUST_FULLY_RE.search(text)),
    }
    return {"impact": impact, "likelihood": likelihood, "modifiers": modifiers}


_SEVERITY_ADJUSTMENT_PATTERNS = (
    # `High (adjusted to Medium — reason)` — most common verifier idiom
    re.compile(
        r"^\s*(?:Critical|High|Medium|Low|Informational|Info)\s*"
        r"[\(\[][^)\]]*?\b(?:adjusted|demoted|upgraded|downgraded|capped|"
        r"reduced|raised|moved|now|→|->|=>)\s*(?:to\s+)?"
        r"(Critical|High|Medium|Low|Informational|Info)\b",
        re.IGNORECASE,
    ),
    # `High → Medium` or `High -> Medium` or `High => Medium`
    re.compile(
        r"^\s*(?:Critical|High|Medium|Low|Informational|Info)\s*"
        r"(?:→|->|=>)\s*"
        r"(Critical|High|Medium|Low|Informational|Info)\b",
        re.IGNORECASE,
    ),
)


def _extract_verifier_severity_with_adjustment(raw: str) -> str:
    """Return the FINAL severity the verifier intended, after any inline
    adjustment they documented.

    The verifier prompt allows authored adjustments inline in the
    `Severity:` field (e.g. ``**Severity:** High (adjusted to Medium —
    external precondition required)``). Naive parsing reads "High" (the
    first token) and inflates the expected severity; the provenance
    gate then rejects the LLM's correctly-downgraded report row.

    This helper recognizes the documented adjustment idioms and returns
    the POST-adjustment value. If no adjustment is found, returns the
    field verbatim for downstream `normalize_severity` to handle.

    Added in response to the DODO May-2026 audit halt where
    `verify_H-20.md` wrote `Severity: High (adjusted to Medium —
    external precondition required; see below)` — verifier intent was
    Medium, driver computed High, LLM correctly wrote Medium per the
    intent, provenance gate misclassified as LLM-fault.
    """
    text = (raw or "").strip()
    if not text:
        return text
    # The upstream `_field_from_markdown` extractor can leak markdown
    # decoration (`**`, leading `-`, backticks) when verifiers write
    # `**Severity:** High (...)` — strip those so the adjustment-pattern
    # regex can anchor cleanly on the severity word.
    cleaned = re.sub(r"^[\s*`_\-–—:]+", "", text)
    cleaned = re.sub(r"[\s*`_]+$", "", cleaned)
    for pat in _SEVERITY_ADJUSTMENT_PATTERNS:
        m = pat.search(cleaned)
        if m:
            return m.group(1)
    return text


def _enforce_severity_matrix(verify_text: str, queue_row: dict[str, str]) -> str:
    """Compute expected severity from verify text and queue row.

    Priority (post-DODO refinement, asymmetric and intentional):

    1. Matrix (Impact × Likelihood + modifiers) when both axes are present.
    2. Verifier's explicit `**Severity**:` field when LOWER than the matrix
       computation — the verifier has context (atomic revert, design
       intent) the mechanical matrix cannot capture, AND a verifier
       under-rating is a deliberate authored downgrade that needs no
       Trust Adj. token.
    3. Verifier's inline-adjustment notation (e.g. `Severity: High
       (adjusted to Medium — reason)`) is honored as the verifier's
       intent — the post-adjustment value wins.
    4. Queue-row severity as final fallback with E7 conservative
       downgrade.

    **Why NOT symmetric (verifier wins both directions)?** The DODO
    May-2026 audit's H-9 case looked like a symmetric-rule problem
    (verifier said High, matrix said Medium due to a prose match on
    `fully-trusted`). The real fix was in
    `_MATRIX_TRUST_FULLY_RE` — tightening the trust-modifier detector
    so it requires an explicit structured marker, not free prose.
    With that fix, the verifier explicitly rejecting the modifier in
    narrative no longer false-triggers the demotion. The asymmetric
    contract (matrix corrects LLM over-rating) is preserved, which
    catches the more common failure mode of verifier severity
    inflation seen in the DODO grader output (6/7 FOUND verdicts
    over-rated severity vs ground truth).
    """
    inputs = _extract_severity_inputs(verify_text)
    base = _compute_matrix_severity(inputs.get("impact"), inputs.get("likelihood"))
    verifier_sev_raw = _field_from_markdown(
        verify_text or "", ("Severity", "Final Severity"),
    )
    # Apply inline-adjustment recognition before normalizing. The
    # verifier may have written `High (adjusted to Medium — reason)`;
    # honor the post-adjustment value as the verifier's intent.
    verifier_sev_resolved = _extract_verifier_severity_with_adjustment(
        verifier_sev_raw
    )
    verifier_sev = (
        normalize_severity(verifier_sev_resolved) if verifier_sev_resolved else ""
    )
    if base is not None:
        mods = dict(inputs.get("modifiers", {}))
        # CALIBRATION (recover the good-coverage-class Critical): the on-chain-only
        # -1 modifier applies ONLY when impact is CONFINED to on-chain state
        # (report-template.md). Impact:High is defined as "direct fund loss /
        # permanent lock" -- that is NOT confined, so the modifier must not pull a
        # High-impact finding down (later DODO runs lost their Critical to exactly
        # this spurious on-chain demotion of a verified High x High theft). This
        # only PREVENTS a demotion of an already-High-impact finding; it never
        # promotes, so it cannot re-inflate the verifier-over-rating class the
        # asymmetric matrix is designed to correct.
        if _normalize_matrix_label(inputs.get("impact"), _MATRIX_IMPACT_LABELS) == "High":
            mods.pop("onchain_only", None)
        matrix_final = _apply_severity_modifiers(base, mods)
        if verifier_sev and verifier_sev != matrix_final:
            v_rank = severity_rank(verifier_sev)
            m_rank = severity_rank(matrix_final)
            # Verifier wins ONLY when LOWER than the matrix. Higher-than-
            # matrix verifier severity is interpreted as LLM over-rating
            # and the matrix corrects it.
            if v_rank < m_rank and v_rank >= 0:
                return verifier_sev
        return matrix_final
    # No matrix axes — fall back to explicit verifier field or queue row.
    recovered = normalize_severity(
        (verifier_sev or queue_row.get("severity", "") or "Medium").strip()
    )
    if recovered in ("Critical", "High") and not verifier_sev:
        return "Medium"
    return recovered


# =============================================================================
# Phase C: Duplicate / root-cause consolidation gate.
#
# Per report-template.md and phase6 STEP 1.5, findings sharing the same root
# cause must be consolidated. Threshold for an automatic consolidation is 3+
# findings sharing (severity, fix_pattern, vuln_class). The driver performs
# this mechanically so the LLM cannot silently inflate finding counts via
# repeated low-severity issues.
# =============================================================================
class DedupSignature:
    __slots__ = ("severity", "fix_pattern", "vuln_class")

    def __init__(self, severity: str, fix_pattern: str, vuln_class: str):
        self.severity = severity
        self.fix_pattern = fix_pattern
        self.vuln_class = vuln_class

    def key(self) -> tuple[str, str, str]:
        return (
            (self.severity or "").strip().lower(),
            (self.fix_pattern or "").strip().lower(),
            (self.vuln_class or "").strip().lower(),
        )

    def __repr__(self) -> str:
        return f"DedupSignature(sev={self.severity}, fix={self.fix_pattern}, class={self.vuln_class})"


_DEDUP_VULN_VOCAB: list[tuple[str, str]] = [
    ("event emission", "missing_event"),
    ("missing event", "missing_event"),
    ("no event", "missing_event"),
    ("event missing", "missing_event"),
    ("reentrancy", "reentrancy"),
    ("integer overflow", "overflow"),
    ("integer underflow", "overflow"),
    ("overflow", "overflow"),
    ("underflow", "overflow"),
    ("zero-value", "missing_validation"),
    ("zero value", "missing_validation"),
    ("input validation", "missing_validation"),
    ("missing validation", "missing_validation"),
    ("missing check", "missing_validation"),
    ("staleness", "staleness"),
    ("stale data", "staleness"),
    ("stale price", "staleness"),
    ("access control", "access_control"),
    ("missing access", "access_control"),
    ("authorization missing", "access_control"),
    ("centralization", "centralization"),
    ("denial of service", "dos"),
    (" dos ", "dos"),
    ("front-run", "front_run"),
    ("frontrun", "front_run"),
    ("front run", "front_run"),
    ("price manipulation", "price_manipulation"),
    ("oracle manipulation", "oracle_manipulation"),
    ("rounding", "rounding"),
    ("precision loss", "rounding"),
    ("storage collision", "storage_collision"),
    ("storage layout", "storage_collision"),
]


_DEDUP_FIX_VOCAB: list[tuple[str, str]] = [
    ("emit an event", "emit_event"),
    ("emit event", "emit_event"),
    ("emit a", "emit_event"),
    ("event in", "emit_event"),
    ("zero-value validation", "zero_validation"),
    ("zero value validation", "zero_validation"),
    ("zero validation", "zero_validation"),
    ("non-zero check", "zero_validation"),
    ("require(.*!= 0", "zero_validation"),
    ("input validation", "input_validation"),
    ("reentrancyguard", "reentrancy_guard"),
    ("reentrancy guard", "reentrancy_guard"),
    ("nonreentrant", "reentrancy_guard"),
    ("safemath", "checked_arith"),
    ("checked arithmetic", "checked_arith"),
    ("checked math", "checked_arith"),
    ("safe casting", "checked_arith"),
    ("staleness check", "staleness_check"),
    ("max staleness", "staleness_check"),
    ("freshness check", "staleness_check"),
    ("only owner", "access_control_check"),
    ("onlyowner", "access_control_check"),
    ("onlyrole", "access_control_check"),
    ("access control", "access_control_check"),
    ("oracle check", "oracle_check"),
    ("rounding", "rounding_fix"),
    ("validation", "validation"),
    ("require ", "validation"),
]


_CLASS_LEVEL_TITLES: dict[str, str] = {
    "missing_event": "Missing event emission on admin state changes",
    "reentrancy": "Missing reentrancy protection across affected functions",
    "overflow": "Unchecked arithmetic operations",
    "missing_validation": "Admin setters lack input validation",
    "staleness": "External data source freshness not validated",
    "access_control": "Privileged operations lack access control",
    "centralization": "Excessive privileges concentrated in trusted role",
    "dos": "Denial-of-service vectors via unbounded operations",
    "front_run": "Operations exposed to transaction front-running",
    "price_manipulation": "Price feed manipulation vectors",
    "oracle_manipulation": "Oracle manipulation vectors",
    "rounding": "Precision loss from rounding direction",
    "storage_collision": "Storage layout collision risks",
}


# Quality observation vocabulary — unambiguously cosmetic classes that get
# megasection (compact table) treatment in the report.  Anything with
# plausible security impact MUST NOT appear here.
_QUALITY_OBSERVATION_VOCAB: list[tuple[str, str]] = [
    ("dead code", "dead_code"),
    ("unreachable code", "dead_code"),
    ("unused code", "dead_code"),
    ("unused import", "unused_import"),
    ("unused variable", "unused_variable"),
    ("unused parameter", "unused_variable"),
    ("unused return", "unused_variable"),
    ("naming inconsistenc", "naming"),
    ("naming convention", "naming"),
    ("inconsistent naming", "naming"),
    ("variable naming", "naming"),
    ("function naming", "naming"),
    ("typo", "typo"),
    ("spelling", "typo"),
    ("grammar", "typo"),
    ("magic number", "magic_number"),
    ("hardcoded constant", "magic_number"),
    ("hard-coded constant", "magic_number"),
    ("missing natspec", "missing_docs"),
    ("missing documentation", "missing_docs"),
    ("missing comment", "missing_docs"),
    ("undocumented", "missing_docs"),
    ("code style", "code_style"),
    ("formatting", "code_style"),
    ("gas optimization", "gas_optimization"),
    ("gas efficiency", "gas_optimization"),
    ("gas saving", "gas_optimization"),
    ("redundant code", "redundant_code"),
    ("redundant check", "redundant_code"),
    ("unnecessary check", "redundant_code"),
    ("shadow", "shadowing"),
    ("variable shadow", "shadowing"),
]

_QUALITY_CLASS_TITLES: dict[str, str] = {
    "dead_code": "Dead / unreachable code",
    "unused_import": "Unused imports",
    "unused_variable": "Unused variables / parameters",
    "naming": "Naming inconsistencies",
    "typo": "Typos and spelling",
    "magic_number": "Magic numbers / hardcoded constants",
    "missing_docs": "Missing documentation",
    "code_style": "Code style and formatting",
    "gas_optimization": "Gas optimization opportunities",
    "redundant_code": "Redundant code / checks",
    "shadowing": "Variable shadowing",
}


def classify_quality_observation(title: str, severity: str) -> str:
    """Return a quality-observation class if this finding is cosmetic, else ''."""
    if severity.lower() not in ("low", "informational", "info"):
        return ""
    return _classify_keyword(title, _QUALITY_OBSERVATION_VOCAB)


def _classify_keyword(text: str, vocab: list[tuple[str, str]]) -> str:
    if not text:
        return ""
    tl = " " + text.lower() + " "
    # Match longest needle first to avoid early shorter-match short-circuit.
    for needle, canon in sorted(vocab, key=lambda x: -len(x[0])):
        if needle in tl:
            return canon
    return ""


_DEDUP_GENERIC_STOP = {
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
    "is", "are", "be", "with", "by", "as", "from", "this", "that",
}


def _dedup_generic_norm(text: str) -> str:
    """Fallback canonical form when no vocabulary token matches."""
    if not text:
        return ""
    tl = re.sub(r"`[^`]*`", " ", text.lower())  # strip code spans
    tl = re.sub(r"\b[a-z][a-zA-Z0-9_]{6,}\b", " ", tl)  # strip long ident-like tokens
    tl = re.sub(r"[^a-z0-9 ]+", " ", tl)
    toks = [t for t in tl.split() if t and t not in _DEDUP_GENERIC_STOP and len(t) > 2]
    return "_".join(toks[:3]) if toks else ""


def _dedup_signature_for_finding(
    text: str,
    severity: str,
    hint_title: str | None = None,
) -> DedupSignature:
    """Compute the dedup signature for a verify_*.md body.

    `hint_title` lets the caller inject the queue-row title when the verify
    file's H1 is just an internal ID like `# INV-001`.
    """
    rec = _field_or_section(
        text,
        ("Recommendation", "Suggested Fix", "Suggested fix", "Fix", "Mitigation"),
        ("Recommendation", "Suggested Fix", "Suggested fix", "Fix", "Mitigation"),
        fallback="",
    )
    title = _first_heading_title(text) or ""
    desc = _field_or_section(
        text,
        ("Description", "Summary", "Root Cause"),
        ("Description", "Summary", "Analysis", "Code Trace", "Root Cause"),
        fallback="",
    )
    parts = [title, desc]
    if hint_title:
        parts.append(hint_title)
    title_blob = " ".join(p for p in parts if p).strip()
    vuln = _classify_keyword(title_blob, _DEDUP_VULN_VOCAB)
    fix = _classify_keyword(rec, _DEDUP_FIX_VOCAB)
    if not vuln:
        vuln = _dedup_generic_norm(title_blob) or "unspecified"
    if not fix:
        fix = _dedup_generic_norm(rec) or "unspecified"
    return DedupSignature(severity=severity, fix_pattern=fix, vuln_class=vuln)


def _detect_dedup_clusters(scratchpad: Path, threshold: int = 3) -> list[dict]:
    """Group active verifications by dedup signature; return clusters >= threshold.

    Returns a list of dicts: {signature: DedupSignature, finding_ids: [...]}.
    Excluded findings (REFUTED / FALSE_POSITIVE) are not clustered.
    """
    rows = parse_verification_queue_rows(scratchpad)
    if not rows:
        return []
    by_key: dict[tuple, list[tuple[str, DedupSignature]]] = {}
    for row in rows:
        fid = (row.get("finding id") or "").strip()
        if not fid:
            continue
        vp = _verify_file_for_id(scratchpad, fid)
        try:
            vtxt = _llm_norm(vp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            vtxt = ""
        status = _verifier_status_from_text(vtxt)
        if not _is_reportable_verdict(status):
            continue
        severity = _enforce_severity_matrix(vtxt, row)
        unresolved = any(tok in status for tok in ("UNRESOLVED", "PARTIAL"))
        if unresolved:
            severity = _demote_severity_once(severity)
        hint = (row.get("title") or "").strip()
        sig = _dedup_signature_for_finding(vtxt, severity=severity, hint_title=hint)
        by_key.setdefault(sig.key(), []).append((fid, sig))

    clusters: list[dict] = []
    for k, members in by_key.items():
        if len(members) >= threshold:
            clusters.append({
                "signature": members[0][1],
                "finding_ids": [m[0] for m in members],
                "key": k,
            })
    return clusters


def _consolidated_title_for(sig: DedupSignature) -> str:
    """Pick a class-level title for a consolidated finding."""
    return _CLASS_LEVEL_TITLES.get(
        sig.vuln_class.lower(),
        f"{sig.vuln_class.replace('_', ' ').title()} (consolidated)",
    )


# =============================================================================
# Phase D: LLM body writer manifest + post-write validator.
#
# Manifests are emitted per shard from the verified records. They are the
# single source of truth for what the LLM body writer is allowed to produce:
# every report finding has its evidence bound (location, evidence tag, verify
# file, description, recommendation). After the LLM writes its body file the
# driver validates coverage (no missing), no-extras (no hallucinations) and
# evidence integrity (locations match), then halts on any drift.
#
# Findings whose verify file lacks usable Description AND Recommendation are
# tagged `report_blocked` — the body MUST surface a `[REPORT-BLOCKED:` tag in
# the section header so a human reviewer sees the gap instead of placeholder
# prose.
# =============================================================================
_BODY_SHARD_CAPS = {
    "report_critical_high": 15,
    "report_medium": 20,
    "report_low_info": 30,
}


# Body validator -----------------------------------------------------------
# Bracketed report ID like `[M-01]` or `[ m-01 ]` - bounded to prevent
# matching internal IDs like INV-001 or H-1234567.
_BODY_REPORT_ID_RE = re.compile(r"\[\s*([CHMLI])-(\d{1,3})\s*\]", re.IGNORECASE)


def _normalize_report_id(raw: str) -> str:
    m = _BODY_REPORT_ID_RE.fullmatch(f"[{raw.strip()}]")
    if not m:
        return raw.strip().upper()
    return f"{m.group(1).upper()}-{int(m.group(2)):02d}"


def _extract_report_ids_from_body(body: str) -> list[str]:
    """Pull report body IDs from finding headers and Quality rows.

    Header IDs are the normal full-section format. Low/Info cosmetic-only
    findings may also be represented in the prompt-sanctioned
    `## Quality Observations` table, where the first column is the report ID.
    IDs mentioned in prose remain excluded because they are cross-references.
    """
    _HEADER_ID_RE = re.compile(
        r"(?im)^#{1,3}\s*(?:\[REPORT-BLOCKED[^\]]*\]\s*)?\[\s*([CHMLI])-(\d{1,3})\s*\]"
    )
    found = []
    for m in _HEADER_ID_RE.finditer(body or ""):
        found.append(f"{m.group(1).upper()}-{int(m.group(2)):02d}")
    qo = re.search(r"(?im)^##\s+Quality\s+Observations\b", body or "")
    if qo:
        qo_text = (body or "")[qo.end():]
        end = re.search(r"(?m)^##\s+", qo_text)
        if end:
            qo_text = qo_text[:end.start()]
        for m in re.finditer(r"(?m)^\|\s*([LI])-(\d{1,3})\s*\|", qo_text):
            found.append(f"{m.group(1).upper()}-{int(m.group(2)):02d}")
    return found


def _section_for_report_id(body: str, report_id: str) -> str:
    """Return the slice of body from the report_id heading to the next finding.

    Tolerant to case and whitespace inside the brackets.
    Also tolerates a ``[REPORT-BLOCKED: ...]`` prefix before the report ID
    bracket — the body-writer prompt instructs the LLM to prefix blocked
    findings this way.
    """
    # Match the heading line: `## [REPORT-BLOCKED: ...] [X-NN] ...`
    # or plain `## [X-NN] ...`
    pat = re.compile(
        rf"(?im)^#{{1,3}}\s*(?:\[REPORT-BLOCKED[^\]]*\]\s*)?\[\s*{re.escape(report_id[0])}-0*{int(report_id.split('-')[1])}\s*\][^\n]*\n",
    )
    m = pat.search(body or "")
    if not m:
        qo = re.search(r"(?im)^##\s+Quality\s+Observations\b", body or "")
        if not qo:
            return ""
        qo_text = (body or "")[qo.end():]
        end = re.search(r"(?m)^##\s+", qo_text)
        if end:
            qo_text = qo_text[:end.start()]
        row_pat = re.compile(
            rf"(?im)^\|\s*{re.escape(report_id[0])}-0*{int(report_id.split('-')[1])}\s*\|[^\n]*$"
        )
        row = row_pat.search(qo_text)
        return row.group(0) if row else ""
    start = m.start()
    # RPT-3: Stop at the next report finding header OR a known STRUCTURAL tier/
    # report heading -- never at an arbitrary `##`. In-finding subheadings like
    # `## Impact` / `## PoC Result` are legitimate (the field extractor supports
    # section-style fields), so terminating on any H2 truncated the finding and
    # produced false "missing substantive Impact/PoC" errors. Cross-finding
    # isolation (X-01 must not borrow X-02's section) is preserved by the
    # next-finding-header branch and the closed structural-heading set.
    end_m = re.search(
        r"(?im)^#{1,2}\s+(?:Critical|High|Medium|Low|Informational|"
        r"Quality\s+Observations|Appendix|Priority\s+Remediation|"
        r"Excluded|Summary)\b"
        r"|^#{3}\s*(?:\[REPORT-BLOCKED[^\]]*\]\s*)?\[\s*[CHMLI]-\d{1,3}\s*\]",
        (body or "")[m.end():],
    )
    end = m.end() + end_m.start() if end_m else len(body or "")
    return (body or "")[start:end]


_FINDING_BLOCK_RE = re.compile(
    r"^\s*(?:##|###)\s+(?:Finding\s+)?\[?([A-Z][A-Z0-9]{0,6}-\d+)\]?",
    re.MULTILINE,
)


_BRACKETED_ID_RE = re.compile(r"\[([A-Z][A-Z0-9]{0,6}-\d+)\]")


_TABLE_FINDING_ID_RE = re.compile(r"(?im)^\|\s*([A-Z][A-Z0-9]{0,6}-\d+)\s*\|")


_TABLE_SOURCE_ID_RE = re.compile(r"(?im)\b([A-Z][A-Z0-9]{0,6}-\d+)\b")


_TABLE_LOCATION_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.(?:sol|rs|go|move):L?\d+)")


def _non_reportable_marker(text: str) -> bool:
    return bool(re.search(
        r"\b(?:refuted|false[_\s-]*positive|infeasible|not\s+applicable|"
        r"absorbed(?:\s+into)?|duplicate|deduplicated|merged(?:\s+into)?|"
        r"not\s+reportable|no\s+finding)\b",
        text or "",
        re.IGNORECASE,
    ))


def _ambiguous_na_marker(text: str) -> bool:
    return bool(re.fullmatch(
        r"\s*(?:n/?a|not\s+available|unknown)(?:\s*\([^)]*\))?\s*",
        text or "",
        re.IGNORECASE,
    ))


_LOCATION_RE = re.compile(
    r"(?:\*\*)?Location(?:\*\*)?\s*:\s*`?([^\n`]+?)`?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


_SOURCE_IDS_LINE_RE = re.compile(
    r"^\s*[-*]?\s*(?:"
    r"\*\*Source IDs?:\*\*|"
    r"\*\*Source IDs?\*\*\s*:|"
    r"Source IDs?\s*:"
    r")\s*(.+)$",
    re.IGNORECASE,
)


_HEADING_FINDING_RE = re.compile(
    r"^\s*(?:##|###)\s+Finding\b[^\n]*$", re.MULTILINE
)


_INVENTORY_FINDING_HEADING_RE = re.compile(
    r"^\s*###\s+Finding\s+\[[^\]]+\]:", re.MULTILINE
)


_TOTAL_FINDINGS_RE = re.compile(
    r"\*{0,2}Total\s+Findings\*{0,2}\s*:?\*{0,2}\s*[:\|]?\s*(\d+)",
    re.IGNORECASE,
)


def _inventory_blocks(text: str) -> list[dict[str, str]]:
    """Return inventory finding blocks with stable IDs and raw markdown.

    Input is normalized via `_llm_norm` so drift formats (smart quotes,
    em-dash, CRLF, HTML entities, NBSP, zero-width chars) don't fragment
    or hide finding blocks.

    Fence-aware: headings INSIDE triple-backtick or triple-tilde code blocks
    are NOT treated as finding starts. LLM outputs frequently include code
    examples that contain markdown-style headings (e.g., a "before/after"
    sample). Pre-fence-awareness, those got counted as phantom findings.
    """
    text = _llm_norm(text)
    lines = text.splitlines()
    starts: list[tuple[int, str]] = []
    in_fence = False
    fence_marker: str | None = None
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        # Triple-backtick / triple-tilde fence toggle. Match opening/closing
        # by the same marker char to be permissive about info strings
        # (`'''solidity`, `'''diff`).
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = None
            continue
        if in_fence:
            continue
        if not re.match(r"^\s*#{2,4}\s+", line):
            continue
        fid = _normalize_finding_id(line)
        if fid:
            starts.append((idx, fid))
    out: list[dict[str, str]] = []
    for i, (start, fid) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        title = re.sub(r"^\s*#{2,4}\s+", "", lines[start]).strip()
        title = re.sub(r"(?i)^Finding\b\s*", "", title).strip()
        title = re.sub(rf"^\[?\s*{re.escape(fid)}\s*\]?", "", title, flags=re.IGNORECASE).strip()
        title = re.sub(r"^\s*[:=\-–—#]+\s*", "", title).strip()
        out.append({
            "id": fid,
            "title": _strip_md(title),
            "block": block,
            "location": _field_from_markdown(block, ("Location", "Locations")),
            "source_ids": _field_from_markdown(block, ("Source IDs", "Source ID")),
        })
    return out


def _project_source_index(project_root: str) -> dict[str, list[Path]]:
    """Map basename -> source files, excluding build/dependency junk."""
    root = Path(project_root)
    index: dict[str, list[Path]] = {}
    ex_dirs = {
        ".git", "target", "node_modules", ".scratchpad", "artifacts",
        "vendor", "dist", "build", "__pycache__",
    }
    suffixes = {
        ".rs", ".go", ".sol", ".move", ".py", ".c", ".h", ".cpp", ".cc",
        ".hpp", ".ts", ".js", ".jsx", ".tsx",
    }
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in suffixes:
            continue
        rel_parts = set(p.relative_to(root).parts)
        if rel_parts & ex_dirs:
            continue
        index.setdefault(p.name, []).append(p)
    return index


def _is_support_location_path(path: str) -> bool:
    """True for tests/mocks/harnesses that should not be primary locations."""
    p = (path or "").replace("\\", "/").strip().lower()
    if not p:
        return False
    wrapped = f"/{p.lstrip('/')}"
    leaf = p.rsplit("/", 1)[-1]
    markers = (
        "/test/", "/tests/", "/testdata/", "/testing/",
        "/mock/", "/mocks/", "/mocked/",
        "/harness/", "/harnesses/",
        "/fixture/", "/fixtures/",
        "/script/", "/scripts/",
    )
    return (
        any(marker in wrapped for marker in markers)
        or leaf.endswith((".t.sol", ".s.sol", "_test.go", "_tests.rs", ".test.ts", ".spec.ts"))
        or leaf.startswith(("test_", "mock", "stub", "fake"))
        or "harness" in leaf
    )


def _parse_location_ref(location: str) -> tuple[str, int | None]:
    """Extract a path + line from a Location field.

    Closes F-FIELD-01: when multiple paths appear in one Location field
    (e.g., "see foo.rs as background, real bug at bar.rs:L20"), prefer the
    one with an explicit line number — it is almost always the actual
    finding location. Fall back to the first path-only match when no
    annotated path exists.

    Defensive against LLM drift: normalize before parsing.
    """
    location = _llm_norm(location)
    loc = (location or "").strip().strip("`")
    loc = re.sub(r"(?i)\b(?:at|in|file)\s*[:=]\s*", "", loc)
    matches = list(re.finditer(
        r"([A-Za-z0-9_./\\-]+\.(?:cairo|move|hpp|cpp|tsx|jsx|sol|rs|go|py|cc|ts|js|vy|c|h))"
        r"(?![A-Za-z0-9_])(?:\s*(?::L?|#L?|line\s+|L)(\d+))?",
        loc,
        re.IGNORECASE,
    ))
    if not matches:
        return "", None
    production_matches = [
        m for m in matches
        if not _is_support_location_path(m.group(1))
    ]
    search_matches = production_matches or matches
    for m in search_matches:
        if m.group(2):
            return m.group(1).replace("\\", "/"), int(m.group(2))
    m = search_matches[0]
    return m.group(1).replace("\\", "/"), int(m.group(2)) if m.group(2) else None


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except Exception:
        return 0


def _resolve_inventory_location(
    project_root: str,
    source_index: dict[str, list[Path]],
    location: str,
) -> tuple[str, str, str]:
    """Return (status, resolved_location, reason)."""
    rel, line = _parse_location_ref(location)
    if not rel:
        return "LOCATION_INVALID", "", "no parseable source path"
    root = Path(project_root)
    cand = root / rel
    if cand.exists() and cand.is_file():
        n = _line_count(cand)
        if line and n and line > n:
            return "LOCATION_INVALID", rel, f"line {line} exceeds file length {n}"
        return "OK", f"{rel}:L{line}" if line else rel, "path exists"
    matches = source_index.get(Path(rel).name, [])
    if len(matches) == 1:
        try:
            new_rel = matches[0].relative_to(root).as_posix()
        except Exception:
            new_rel = str(matches[0]).replace("\\", "/")
        n = _line_count(matches[0])
        if line and n and line > n:
            return "LOCATION_INVALID", new_rel, f"unique basename but line {line} exceeds file length {n}"
        return "RECOVERED_BASENAME", f"{new_rel}:L{line}" if line else new_rel, "unique basename recovery"
    if len(matches) > 1:
        return "LOCATION_AMBIGUOUS", "", f"basename matches {len(matches)} files"
    return "LOCATION_INVALID", "", "file not found"


def _split_source_id_tokens(raw: str) -> list[str]:
    raw = (raw or "").strip()
    raw = raw.strip("[]")
    raw = re.sub(r"(?i)\b(?:Source IDs?|Sources?|Provenance|Origin)\b\s*[:=-]?\s*", "", raw)
    toks = re.split(r",|\n|;|\s+\+\s+|\s+and\s+", raw)
    return [t.strip().strip("`[] ") for t in toks if t.strip().strip("`[] ")]


def _validate_source_token(token: str, scratchpad: Path) -> tuple[str, str]:
    """Validate an inventory Source ID token against scratchpad artifacts."""
    tok = token.strip()
    if not tok:
        return "SOURCE_INVALID", "empty token"
    m = re.match(r"^([A-Za-z0-9_.-]+\.md)(?::|#|::)(.+)$", tok)
    if m:
        p = scratchpad / m.group(1)
        label = m.group(2).strip()
        if not p.exists():
            return "SOURCE_INVALID", f"{m.group(1)} missing"
        try:
            txt = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return "SOURCE_INVALID", f"{m.group(1)} unreadable"
        if label and label in txt:
            return "OK", "file label found"
        # Agents sometimes cite a slug they derived from a finding title
        # rather than a literal anchor. If every slug word appears nearby in
        # the source artifact, treat it as weak but usable provenance.
        words = [
            w.lower() for w in re.split(r"[^A-Za-z0-9]+", label)
            if len(w) >= 4
        ]
        low = txt.lower()
        if words and all(w in low for w in words[:4]):
            return "SOURCE_UNVERIFIED", "slug words found, literal label absent"
        return "SOURCE_INVALID", f"label `{label}` not found in {m.group(1)}"

    # Plain upstream IDs are valid if they occur in any non-inventory artifact.
    norm_id = _normalize_finding_id(tok) or tok
    if re.fullmatch(r"[A-Z][A-Z0-9_-]{0,24}-\d+", norm_id):
        for p in scratchpad.glob("*.md"):
            if p.name.startswith("findings_inventory") or p.name in {
                "verification_queue.md", "report_index.md", "AUDIT_REPORT.md",
            }:
                continue
            try:
                if norm_id in _llm_norm(p.read_text(encoding="utf-8", errors="replace")):
                    return "OK", f"found in {p.name}"
            except Exception:
                continue
        return "SOURCE_UNVERIFIED", "plain ID not found in upstream artifacts"
    # Non-empty free-form provenance should not by itself kill a lead. It is
    # weak evidence, but paired with a real code location it is enough to keep
    # the row in verification.
    return "SOURCE_UNVERIFIED", "free-form source token"


def _replace_inventory_location(text: str, finding_id: str, new_location: str) -> str:
    fid = _normalize_finding_id(finding_id) or finding_id
    lines = text.splitlines()
    starts = [
        i for i, line in enumerate(lines)
        if re.match(r"^\s*#{2,4}\s+", line) and _normalize_finding_id(line) == fid
    ]
    if not starts:
        return text
    start = starts[0]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^\s*#{2,4}\s+", lines[i]) and _normalize_finding_id(lines[i]):
            end = i
            break
    loc_re = re.compile(r"^(\s*(?:[-*]\s*)?(?:\*\*)?Location(?:\*\*)?\s*(?::|-|=)\s*)(.*)$", re.IGNORECASE)
    for i in range(start, end):
        m = loc_re.match(lines[i])
        if m:
            lines[i] = m.group(1) + new_location
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return text


def _extract_finding_signals(text: str) -> tuple[set[str], int]:
    """Return (normalized-ID set, loose-finding-block count).

    Primary signal: explicit bracketed / heading IDs. Secondary signal:
    `## Finding ...` or `### Finding ...` heading blocks with no parseable
    ID attached — still proof that a finding was written. A `**Location**:`
    row is folded into the ID set when no ID regex hit exists for that
    block, so agents that omit the `[XX-N]` prefix still produce a signal.
    """
    ids: set[str] = set()
    for m in _BRACKETED_ID_RE.finditer(text):
        ids.add(m.group(1))
    for m in _FINDING_BLOCK_RE.finditer(text):
        ids.add(m.group(1))
    for line in text.splitlines():
        m = _SOURCE_IDS_LINE_RE.match(line)
        if m:
            for tok in re.findall(r"\b[A-Z][A-Z0-9]{0,6}-\d+\b", m.group(1)):
                ids.add(tok)
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9]{0,6}-\d+", cells[0]):
            ids.add(cells[0])
        for cell in cells[1:]:
            for m in _TABLE_SOURCE_ID_RE.finditer(cell):
                ids.add(m.group(1))
    blocks = len(_HEADING_FINDING_RE.findall(text)) + len(_TABLE_FINDING_ID_RE.findall(text))
    return ids, blocks


# Phase E14 guardrail: checkpoint-aware sentinel cleanup. Stale `.degraded`
# sentinels from PRIOR aborted runs must not block a fresh start, but
# sentinels for phases the CURRENT checkpoint still marks degraded must be
# preserved — they are the only visible reason the prior process halted.
#
# Decision matrix:
#   sentinel exists,  phase in checkpoint.degraded   -> KEEP (active fault)
#   sentinel exists,  phase in checkpoint.completed  -> CLEAR (stale debris)
#   sentinel exists,  phase in neither               -> CLEAR (orphan from
#                                                       pre-checkpoint abort)
_DEGRADED_SENTINEL_GLOBS = (
    "report_assemble.degraded",
    "report_index.degraded",
    "report_*_body_writer.degraded",
    "*.body_writer.degraded",
    "verify_queue.degraded",
)


def _phase_name_from_sentinel(sentinel_name: str) -> str:
    """Map sentinel filename to the phase name that owns it.

    Examples:
      `report_assemble.degraded` -> `report_assemble`
      `report_critical_high.body_writer.degraded` -> `report_critical_high`
      `verify_queue.degraded` -> `verify_queue`
    """
    name = sentinel_name
    if name.endswith(".degraded"):
        name = name[: -len(".degraded")]
    if name.endswith(".body_writer"):
        name = name[: -len(".body_writer")]
    return name


# Phase E11 follow-up #2: robust finding-ID extraction. Substring search
# false-passes (overlap of unrelated IDs) and false-halts (range syntax,
# markdown-link wrapping). This function returns a normalized set of IDs
# from a free-form text blob, supporting:
#   - Bare IDs:           INV-001
#   - Bracketed IDs:      [INV-001]
#   - Markdown links:     [INV-001](path/to/verify_INV-001.md)
#   - Comma lists:        INV-005, INV-006, INV-007
#   - Range expansion:    INV-001..INV-150  (preserves leading-zero padding)
# Range cap defaults to 10000 to prevent pathological pollution.
_FID_RANGE_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]*)-(\d+)\s*\.\.\s*\1-(\d+)\b",
    re.IGNORECASE,
)


_FID_BARE_RE = re.compile(r"\b([A-Z][A-Z0-9_]*)-(\d+)\b", re.IGNORECASE)


def _extract_finding_ids_from_text(text: str, range_cap: int = 10000) -> set[str]:
    """Extract canonical finding IDs from free-form text.

    Returns a set of `{PREFIX}-{NNNN}` strings using upper-case prefix and
    the original numeric width (preserves leading-zero padding for the
    starting endpoint of a range).
    """
    if not text:
        return set()
    ids: set[str] = set()
    consumed_spans: list[tuple[int, int]] = []
    # Pass 1: range expansion. Consume range-bounded spans so the bare
    # regex on pass 2 doesn't double-count the endpoints.
    for m in _FID_RANGE_RE.finditer(text):
        prefix = m.group(1).upper()
        start = int(m.group(2))
        end = int(m.group(3))
        if end < start:
            start, end = end, start
        # Preserve leading-zero width from the start endpoint.
        width = max(len(m.group(2)), len(m.group(3)))
        n = end - start + 1
        if n > range_cap:
            n = range_cap
            end = start + n - 1
        for i in range(start, end + 1):
            ids.add(f"{prefix}-{i:0{width}d}")
        consumed_spans.append(m.span())

    # Pass 2: bare IDs (skipping consumed range spans).
    for m in _FID_BARE_RE.finditer(text):
        s, e = m.span()
        if any(cs <= s and e <= ce for cs, ce in consumed_spans):
            continue
        prefix = m.group(1).upper()
        # Filter out non-finding patterns: things like `EIP-1234` or
        # `ERC-20` would also match. Use a permissive heuristic: require
        # the prefix to be a typical finding-ID prefix or already seen
        # from a range. Conservative allow-list keeps us close to
        # existing behavior.
        prefix_ok = prefix in _FID_ALLOWED_PREFIXES
        if not prefix_ok:
            continue
        if prefix == "EIP" and len(m.group(2)) > 3:
            continue
        ids.add(f"{prefix}-{m.group(2)}")
    return ids


def _module_key(rel_path: str) -> str:
    """Bucket a repo-relative path into a coverage-module key.

    Uses 2 path segments when the path has 3+ parts (two dirs + file). This
    handles mono-repo layouts where the first segment is a container:

      crates/types/src/lib.rs       -> "crates/types"
      crates/api-client/src/lib.rs  -> "crates/api-client"
      eth/downloader/handler.go     -> "eth/downloader"
      x/staking/keeper/msg.go       -> "x/staking"
      cmd/geth/main.go              -> "cmd/geth"

    Falls back to 1 segment when the path is only 2 parts:

      core/state.go                 -> "core"
      consensus/engine.go           -> "consensus"

    Root-level files bucket as "_root".
    """
    parts = rel_path.split("/")
    if len(parts) >= 3:
        return parts[0] + "/" + parts[1]
    if len(parts) == 2:
        return parts[0]
    return "_root"


def _sc_contract_module_key(rel_path: str) -> str:
    """Bucket SC source paths by contract/program domain."""
    rel = rel_path.replace("\\", "/").lstrip("./")
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return "_root"
    if parts[0] in {"contracts", "src", "sources"}:
        if len(parts) >= 3:
            return parts[0] + "/" + parts[1]
        return parts[0]
    if parts[0] == "programs" and len(parts) >= 2:
        return parts[0] + "/" + parts[1]
    if len(parts) >= 3:
        return parts[0] + "/" + parts[1]
    if len(parts) == 2:
        return parts[0]
    return "_root"


def _normalize_subsystem_scope(scope: str | None) -> str:
    """Normalize a config subsystem scope to a repo-relative POSIX prefix."""
    raw = (scope or "").strip().strip("`\"'")
    if not raw:
        return ""
    raw = raw.replace("\\", "/").lstrip("./")
    return raw.rstrip("/")


def _path_in_subsystem_scope(rel_path: str, scope_prefix: str) -> bool:
    if not scope_prefix:
        return True
    rel = rel_path.replace("\\", "/").lstrip("./").lower()
    pfx = scope_prefix.replace("\\", "/").lstrip("./").lower()
    return rel == pfx or rel.startswith(pfx + "/")


def _load_scope_file_paths(scope_file: str | None) -> set[str]:
    """Parse the wizard's scope file into a set of file identifiers.

    Accepts any of the wizard's documented formats (mirrors the parser in
    `plamen.estimate_cost`):

      - bare paths:        `src/contracts/Vault.sol`
      - markdown tables:   `| GatewaySend.sol | 301 lines |`
      - bullet lists:      `- contracts/Vault.sol`

    Returns a lowercase set containing both each full POSIX-normalised
    relative path AND each bare basename, so coverage gates can match by
    either citation form. Returns an empty set if `scope_file` is empty,
    missing, or unreadable — callers should treat an empty set as
    "no scope file provided, walk everything".

    Used by the recon coverage / subsystem coverage validators to narrow
    the universe of substantial-module-must-be-cited checks when the user
    has explicitly listed audit-scope files. Without this consultation,
    a 200-contract repo with a 5-file scope list still false-trips the
    gate for every uncited bucket.
    """
    if not scope_file:
        return set()
    try:
        if not os.path.isfile(scope_file):
            return set()
    except (OSError, TypeError):
        return set()

    names: set[str] = set()
    try:
        with open(scope_file, "r", encoding="utf-8", errors="ignore") as sf:
            for line in sf:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("//"):
                    continue
                for m in re.findall(r"[\w/\\.-]+\.(?:sol|rs|move|go|vy)", line):
                    norm = m.replace("\\", "/").lstrip("./").lower()
                    if norm:
                        names.add(norm)
                        # Also register the bare basename — citations may
                        # appear as `Vault.sol` without a path prefix.
                        bn = norm.rsplit("/", 1)[-1]
                        if bn:
                            names.add(bn)
    except Exception:
        return set()
    return names


def _path_in_scope_file(rel_path: str, scope_names: set[str]) -> bool:
    """Return True when `rel_path` (POSIX, repo-relative) matches a scope
    file entry. Empty `scope_names` means no scope file → match everything."""
    if not scope_names:
        return True
    rel = rel_path.replace("\\", "/").lstrip("./").lower()
    if rel in scope_names:
        return True
    bn = rel.rsplit("/", 1)[-1]
    if bn in scope_names:
        return True
    # Suffix match: scope file says `contracts/Vault.sol`, walker found
    # `src/contracts/Vault.sol`. The reverse (walker found shorter than
    # scope) is also legal — scope `contracts/Vault.sol` should match
    # `contracts/Vault.sol` directly (handled by direct-equality above).
    for n in scope_names:
        if "/" in n and (rel.endswith("/" + n) or n.endswith("/" + rel)):
            return True
    return False


# ===========================================================================
# Artifact marker helpers (Ship 1 of artifact-complete PTY supervision)
# ===========================================================================
#
# Spawned-worker artifacts (analysis_*.md, depth_*_findings.md, niche_*_findings.md,
# verify_*.md, tier writer outputs) carry HTML-comment markers that record the
# write-lifecycle of the file:
#
#   <!-- PLAMEN_ARTIFACT: analysis_access_control.md -->
#   <!-- PLAMEN_OWNER: B3 -->
#   <!-- PLAMEN_STATUS: IN_PROGRESS -->
#   <!-- PLAMEN_PHASE: breadth -->
#   <!-- PLAMEN_VERSION: 1 -->
#   ... body ...
#   <!-- PLAMEN_STATUS: COMPLETE -->
#   <!-- PLAMEN_FINDINGS_COUNT: 7 -->
#
# Helpers in this section are pure (no driver state, no I/O beyond reading the
# given path). Wiring into Phase / gate_passes happens in Ship 3.

# T2-6 (SW15-1): no re.DOTALL. PLAMEN markers are single-line HTML comments;
# with DOTALL a malformed/missing `-->` let the value `(.*?)` swallow the body
# AND the next marker, misclassifying a COMPLETE file as IN_PROGRESS. Bounding
# the value to one line confines the damage of a malformed marker to its line.
_PLAMEN_MARKER_RE = re.compile(
    r"<!--\s*PLAMEN_([A-Z][A-Z0-9_]*)\s*:\s*(.*?)\s*-->",
)


def _strip_fenced_code_blocks(text: str) -> str:
    """Ship 8.18: remove fenced code blocks (``` or ~~~) before PLAMEN-marker
    parsing.

    Prompt/documentation EXAMPLES place marker comments like
    ``<!-- PLAMEN_STATUS: COMPLETE -->`` inside code fences. If an agent echoes
    such an example into its artifact, the marker parser (and legacy-unmarked
    detection) would treat the EXAMPLE as a real status marker -- poisoning the
    last-wins resolution (a fenced COMPLETE could mask a real IN_PROGRESS, or a
    fenced marker could make a genuinely legacy file look fresh-format). Real
    markers an agent writes live in the body, not inside fences, so dropping
    fenced content before parsing is safe and matches intent.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue  # drop the fence delimiter line itself
        if not in_fence:
            out.append(line)
    return "\n".join(out)

_NO_FINDINGS_HEADING_RE = re.compile(
    r"^##\s+.*\b(No\s+Findings|Negative\s+Result)\b",
    re.MULTILINE | re.IGNORECASE,
)

_OBLIGATION_RECEIPTS_HEADING_RE = re.compile(
    r"^##\s+.*\bObligation\s+Receipts\b",
    re.MULTILINE | re.IGNORECASE,
)

# Ship 8.2: an artifact "has findings" if it shows a `## Findings` section
# OR at least one finding block. Finding blocks are `## Finding [ID]`
# (breadth) or `### Finding [ID]` (depth) -- 2 or 3 hashes, then "Finding",
# whitespace, "[". Case-insensitive (review note). The block regex does NOT
# match the `## Findings` section heading ("Findings" has no whitespace+"["
# after "Finding"), so the two regexes are disjoint.
# Ship D: single source of truth in plamen_types (H2/H3, captures the ID).
_FINDING_BLOCK_HEADING_RE = FINDING_BLOCK_HEADING_RE
_FINDINGS_SECTION_RE = re.compile(
    r"^##\s+Findings\b",
    re.MULTILINE | re.IGNORECASE,
)


_FINDINGS_BODY_MIN_CHARS = 15

# Ship D (SW03-1): crash-safety / "to be filled" placeholder bodies that an LLM
# leaves under a `## Findings` heading. Matched substrings are stripped before
# the substance re-check, so they cannot masquerade as completed analysis.
_FINDINGS_PLACEHOLDER_BODY_RE = re.compile(
    r"\(?\s*(?:findings?\s+(?:will\s+be\s+)?appended\b[^)\n]*"
    r"|(?:findings?\s+)?appended\s+below\b[^)\n]*"
    r"|to\s+be\s+(?:filled|added|appended|determined|populated)\b[^)\n]*"
    r"|no\s+findings?\s+(?:yet|recorded\s+yet)\b[^)\n]*"
    r"|placeholder\b[^)\n]*"
    r"|as\s+they\s+are\s+discovered\b[^)\n]*"
    r"|TBD\b|TODO\b)\s*\)?",
    re.IGNORECASE,
)


def _findings_section_has_body(text: str) -> bool:
    """Ship 8.18: True iff a `## Findings` section exists AND has substantive
    body content (>= _FINDINGS_BODY_MIN_CHARS non-whitespace chars between the
    heading and the next `## ` heading / EOF). A BARE `## Findings` shell --
    the heading with nothing under it -- returns False. This closes the hole
    where an empty `## Findings` heading counted as completed work while
    preserving the Ship 8.2 widening (a real `## Findings` section with prose
    still counts, without requiring a `## Finding [` block)."""
    m = _FINDINGS_SECTION_RE.search(text)
    if not m:
        return False
    nl = text.find("\n", m.end())
    if nl == -1:
        return False  # heading is the last line -> no body
    rest = text[nl + 1:]
    nxt = re.search(r"^##\s+\S", rest, re.MULTILINE)
    body = rest[: nxt.start()] if nxt else rest
    if len("".join(body.split())) < _FINDINGS_BODY_MIN_CHARS:
        return False
    # Ship D (SW03-1): reject KNOWN placeholder bodies. The breadth crash-safety
    # stub writes `## Findings\n\n(findings appended below as they are
    # discovered)` (>15 chars), which Ship 8.18's length-only check accepted as
    # real work -> an empty COMPLETE-marked breadth artifact silently passed.
    # Strip placeholder phrases, then re-test substance: a REAL section that
    # merely mentions such a phrase alongside real findings still passes.
    substantive = _FINDINGS_PLACEHOLDER_BODY_RE.sub("", body)
    return len("".join(substantive.split())) >= _FINDINGS_BODY_MIN_CHARS


def _artifact_has_findings(text: str) -> bool:
    """True iff the artifact body shows at least one `## Finding [` /
    `### Finding [` block OR a `## Findings` section WITH substantive body.

    Ship 8.2 findings-present signal (block OR section), tightened by Ship 8.18
    to reject a BARE `## Findings` shell (heading, no body) -- which previously
    counted as completed work."""
    return bool(
        _FINDING_BLOCK_HEADING_RE.search(text)
        or _findings_section_has_body(text)
    )


def _extract_artifact_status(path: Path) -> dict[str, str]:
    """Parse <!-- PLAMEN_* --> comments from `path`.

    Multiple occurrences of the same key collapse to the LAST value
    (final-write-wins semantics: e.g. an IN_PROGRESS line followed by a
    COMPLETE line yields STATUS=COMPLETE).

    Returns an empty dict when the file is missing, unreadable, or contains
    no PLAMEN_* markers. Keys are uppercase without the PLAMEN_ prefix
    (e.g. STATUS, OWNER, FINDINGS_COUNT).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    # Ship 8.18: ignore markers inside fenced code blocks (examples) so a
    # fenced exemplar cannot poison last-wins status resolution.
    text = _strip_fenced_code_blocks(text)
    result: dict[str, str] = {}
    for m in _PLAMEN_MARKER_RE.finditer(text):
        key = m.group(1).strip()
        val = m.group(2).strip()
        # Last occurrence wins because dict overwrite preserves order.
        result[key] = val
    return result


def is_artifact_complete(path: Path, min_bytes: int) -> bool:
    """Return True iff PLAMEN_STATUS == "COMPLETE" AND the file is at
    least `min_bytes` long.

    False when the file is missing, unreadable, below `min_bytes`, lacks a
    PLAMEN_STATUS marker, or has STATUS != COMPLETE. The size guard makes
    COMPLETE necessary but not sufficient — Ship 3 layers structural
    completeness checks on top for the breadth gate.
    """
    try:
        if not path.exists():
            return False
        if path.stat().st_size < min_bytes:
            return False
    except Exception:
        return False
    markers = _extract_artifact_status(path)
    return markers.get("STATUS") == "COMPLETE"


def is_artifact_legacy_unmarked(path: Path) -> bool:
    """Return True iff the file exists with substantive content but
    contains NO ``PLAMEN_*`` comment marker of ANY kind.

    Ship 8.2 (RC2 fix): the prior implementation keyed legacy detection
    off the single ``PLAMEN_ARTIFACT`` marker. That misclassified
    fresh-format files that carry OTHER markers (``PLAMEN_STATUS``,
    ``PLAMEN_FINDINGS_COUNT``, agent-improvised ``PLAMEN_AGENT`` /
    ``PLAMEN_FOCUS``) but happen to omit ``PLAMEN_ARTIFACT`` -- on a
    fresh audit those were wrongly routed to IN_PROGRESS and halted the
    breadth phase (DODO 2026-05-22). A file with any PLAMEN marker is a
    fresh-format file; its completion is judged by status + structure,
    not by legacy detection.

    Uses the canonical ``_PLAMEN_MARKER_RE`` (the same comment-form regex
    ``_extract_artifact_status`` uses) so "has a PLAMEN marker" means the
    same thing across the codebase: a marker the parser cannot see is a
    marker that does not exist. A prose mention of a marker name does NOT
    match -- the regex requires the full ``<!-- PLAMEN_X: ... -->`` comment.

    Legacy/pre-marker artifacts (genuinely no markers) are still detected
    and tolerated on resumed scratchpads with a warning log.
    """
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    # Ship 8.18: a marker that only appears inside a fenced example does not
    # make a file "fresh-format" -- strip fences before deciding legacy status.
    text = _strip_fenced_code_blocks(text)
    return not bool(_PLAMEN_MARKER_RE.search(text))


def _structural_completeness_ok(
    path: Path,
    *,
    required_headings: tuple[str, ...] | list[str] = (),
    require_findings_count_marker: bool = False,
    placeholder_strings: tuple[str, ...] | list[str] = (),
    require_obligation_receipts_if_shard_exists: Optional[Path] = None,
) -> tuple[bool, list[str]]:
    """Run structural completeness checks against `path` and return
    `(ok, reasons)` where `ok` is True only when every applicable check
    passes. ANY entry in `reasons` hard-fails the artifact -- there is no
    such thing as a "soft warning reason" here.

    Ship 8.2 reduces the contract to its semantic minimum so the gate
    accepts genuinely-complete work without demanding ceremonial marker
    schemas that real agents reproduce inconsistently:

    - required_headings: each entry must appear as a `## ` heading.
      Retained for callers that genuinely need a specific heading;
      breadth/depth now pass `()` and rely on the findings rule below.
    - FINDINGS RULE (replaces the old literal-`## Findings` requirement
      AND the FINDINGS_COUNT==0 branch): the artifact must EITHER show
      findings (`## Findings` section OR >=1 `## Finding [` / `### Finding [`
      block) OR carry an explicit `## No Findings` / `## Negative Result`
      rationale. An artifact with neither is empty/incomplete.
    - placeholder_strings: none of these substrings may remain at COMPLETE
      time (still rejects TODO:/FILL_ME/<placeholder>).

    COMPATIBILITY NO-OPS (Ship 8.2): the following parameters are retained
    ONLY for signature/test stability and have NO effect on the verdict.
    They MUST NOT append any reason (reasons hard-fail):
    - require_findings_count_marker: PLAMEN_FINDINGS_COUNT is now
      informational metadata; the findings decision uses block detection,
      not the count. Passing True changes nothing. (Removing the hard
      requirement eliminates the attempt-1 wasted retry the canonical
      DODO files hit when they omitted the count.)
    - require_obligation_receipts_if_shard_exists: receipt coverage is
      owned solely by `_check_opengrep_obligation_coverage` (warning-only,
      non-blocking). A missing `## Obligation Receipts` section MUST NOT
      hard-fail the artifact gate. Passing a shard path changes nothing.

    The return shape `(ok, reasons)` lets the caller surface every failure
    reason in the gate's detail string at once.
    """
    # require_findings_count_marker and require_obligation_receipts_if_shard_exists
    # are intentionally unused (compatibility no-ops -- see docstring).
    _ = (require_findings_count_marker, require_obligation_receipts_if_shard_exists)

    reasons: list[str] = []

    try:
        if not path.exists():
            return False, ["file missing"]
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - I/O failure path
        return False, [f"file unreadable: {exc}"]

    for heading in required_headings:
        pattern = re.compile(
            r"^##\s+" + re.escape(heading) + r"\b",
            re.MULTILINE | re.IGNORECASE,
        )
        if not pattern.search(text):
            reasons.append(f"missing required heading: ## {heading}")

    for placeholder in placeholder_strings:
        if placeholder and placeholder in text:
            reasons.append(f"unresolved placeholder string present: {placeholder!r}")

    # Findings rule: has findings OR an explicit no-findings rationale.
    if not _artifact_has_findings(text) and not _NO_FINDINGS_HEADING_RE.search(text):
        reasons.append(
            "no '## Finding [' / '### Finding [' blocks (and no "
            "'## Findings' section) and no '## No Findings' / "
            "'## Negative Result' rationale -- artifact is empty/incomplete"
        )

    return (len(reasons) == 0, reasons)
