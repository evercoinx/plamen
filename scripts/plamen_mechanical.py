from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
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

from plamen_types import *  # noqa: F403,F401
from plamen_parsers import *  # noqa: F403,F401
from plamen_parsers import (
    _OPTIONAL_FINDING_METADATA_FIELDS,
    _OPTIONAL_FINDING_METADATA_LABELS,
)
from plamen_validators import *  # noqa: F403,F401
from plamen_validators import (  # explicit private helpers used by SC index repair
    _collect_judge_unresolved_ids,
    _expected_report_index_severities,
    _report_index_adjustment_reason_present,
)

__all__ = [
    "_ATTENTION_REPAIR_MAX_ITEMS",
    "_apply_location_recovery",
    "_apply_mechanical_dedup_from_pairs",
    "_assemble_report_python",
    "_build_attention_repair_items",
    "_build_asset_binding_repair_items",
    "_build_body_writer_manifests",
    "_build_sc_body_writer_manifests",
    "_collect_raw_candidate_ledger_rows",
    "_extract_source_ids_from_inventory",
    "_finalize_report_tier_section",
    "_count_inventory_source_signals",
    "_extract_graph_attention_rows",
    "_inventory_location_map",
    "_inventory_source_files",
    "_location_recovery_needed",
    "_normalize_breadth_outputs",
    "_merge_recon_worker_shards",
    "_patch_report_index_with_recovered",
    "_path_security_weight",
    "_prepare_attention_repair",
    "_repair_promotion_dropouts",
    "_repair_report_body_from_assignments",
    "_repair_report_body_from_manifest",
    "_repair_sc_report_index_from_prior",
    "_tag_report_index_unresolved_sections",
    "_shard_name_for_severity",
    "_synth_report_section_from_verify",
    "_synthesize_components_audited",
    "_write_attention_repair_queue",
    "_write_asset_binding_matrix",
    "_allocate_inventory_ledger_id",
    "_write_canonical_finding_identity_map",
    "_write_candidate_semantic_facets",
    "_write_finding_records_from_inventory",
    "_write_attention_repair_skip",
    "_write_security_obligations",
    "_write_spec_expectations",
    "_write_location_recovery_skip",
    "_write_mechanical_inventory_from_chunks",
    "_write_mechanical_report_index",
    "promote_niche_to_inventory",
    "strip_codex_prepass_markers",
    "_write_mechanical_report_tier",
    "ensure_inventory_shard_plan",
    "estimate_rate_limit_wait_seconds",
    "hibernation_disabled",
    "maybe_hibernate_on_rate_limit",
    "maybe_resume_hibernation",
    "write_hibernation_marker",
    "write_inventory_chunk_placeholder",
    "write_report_tier_placeholder",
]


_RECON_CANONICAL_OUTPUTS = (
    "recon_summary.md",
    "design_context.md",
    "attack_surface.md",
    "state_variables.md",
    "function_list.md",
    "contract_inventory.md",
    "template_recommendations.md",
    "detected_patterns.md",
    "setter_list.md",
    "emit_list.md",
    "build_status.md",
)

_PREPASS_MARKER = "<!-- plamen-prepass v1: mechanical pre-pass output; safe to overwrite while marker is present -->"


def _strip_recon_worker_markers(text: str) -> str:
    """Remove worker lifecycle comments before embedding shard evidence."""
    text = re.sub(
        r"(?m)^\s*<!--\s*(?:PLAMEN_[A-Z_]+|RECON_ROLE|EXPECTED_OUTPUT):.*?-->\s*$",
        "",
        text,
    )
    return text.strip()


def _strip_prepass_marker(text: str) -> str:
    """Remove pre-pass overwrite provenance from merged canonical handoffs."""
    if not text:
        return ""
    lines = text.splitlines()
    if lines and lines[0].strip() == _PREPASS_MARKER:
        return "\n".join(lines[1:]).lstrip()
    return text


def strip_codex_prepass_markers(scratchpad: Path) -> list[str]:
    """Codex-only: drop the line-1 pre-pass marker from recon artifacts whose
    body holds durable content.

    The recon content gate treats a surviving line-1 ``_PREPASS_MARKER`` as
    proof that recon never produced a durable canonical handoff. That proxy is
    valid under Claude's whole-file ``Write`` (which always replaces line 1),
    but Codex's ``apply_patch`` makes targeted body edits that leave line 1
    untouched -- so a legitimately-enriched file keeps the marker and
    false-fails the gate, costing a full recon retry on every Codex run. The
    marker's real purpose is pre-pass resume idempotency, which is fully served
    once the recon phase has run.

    Recall-safe: only the line-1 comment is removed, never content. A file whose
    body is still a pure ``[LLM TO ENRICH]`` placeholder (pre-pass populated
    nothing real and recon did not enrich it) or is empty KEEPS its marker, so
    the gate still fails and forces a retry. Mechanically-populated files (real
    regex-extracted tables) and LLM-enriched files both have real bodies and get
    the marker stripped.

    Returns the list of artifact names whose marker was stripped.
    """
    stripped: list[str] = []
    for name in _RECON_CANONICAL_OUTPUTS:
        path = scratchpad / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = text.splitlines()
        if lines[:1] != [_PREPASS_MARKER]:
            continue  # already enriched (marker gone) or no marker present
        body = "\n".join(lines[1:])
        if not body.strip() or "[LLM TO ENRICH]" in body:
            continue  # placeholder/empty -- keep marker so the gate still fails
        try:
            path.write_text(body.lstrip("\n").rstrip() + "\n", encoding="utf-8")
            stripped.append(name)
        except Exception:
            continue
    return stripped


def _merge_recon_worker_shards(scratchpad: Path, config: dict) -> list[str]:
    """Merge driver-owned recon worker shards into canonical recon artifacts.

    The worker-pool architecture deliberately forbids recon workers from
    writing canonical phase outputs or later-phase files. This merge is the
    single deterministic bridge back to the existing pipeline contract: all
    downstream phases still see the same recon file names and formats, while
    recon itself gets the documented multi-role coverage without a coordinator
    session that can leak into instantiate/breadth/depth.

    Existing deterministic prepass artifacts are preserved and enriched rather
    than replaced. That keeps Slither/OpenGrep/parser-derived inventories as the
    authority when present and uses the LLM shards as contextual analysis.
    """
    shard_names = (
        "recon_build_static.md",
        "recon_design_context.md",
        "recon_inventory_surface.md",
        "recon_templates_patterns.md",
    )
    shards: dict[str, str] = {}
    for name in shard_names:
        path = scratchpad / name
        if not path.exists():
            continue
        try:
            shards[name] = _strip_recon_worker_markers(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except Exception:
            shards[name] = ""

    # Light mode has two merged roles. Map them into the four conceptual
    # buckets so the canonical synthesis below is mode-independent.
    build = shards.get("recon_build_static.md", "")
    design = shards.get("recon_design_context.md", "") or build
    inventory = shards.get("recon_inventory_surface.md", "")
    templates = shards.get("recon_templates_patterns.md", "") or inventory

    def _read_existing(name: str) -> str:
        path = scratchpad / name
        if not path.exists():
            return ""
        try:
            return _strip_prepass_marker(
                path.read_text(encoding="utf-8", errors="replace")
            ).strip()
        except Exception:
            return ""

    def _section(title: str, body: str) -> str:
        body = (body or "").strip()
        if not body:
            body = "No additional worker evidence was produced for this section."
        return f"## {title}\n\n{body}\n"

    def _existing_or_fallback(name: str, fallback_title: str, fallback_body: str) -> str:
        existing = _read_existing(name)
        if existing and len(existing.encode("utf-8", errors="ignore")) >= 100:
            return (
                existing.rstrip()
                + "\n\n"
                + _section("Recon Worker Addendum", fallback_body)
            ).strip()
        return (
            f"# {fallback_title}\n\n"
            + _section("Recon Worker Evidence", fallback_body)
        ).strip()

    def _write(name: str, text: str) -> None:
        text = text.strip() + "\n"
        if len(text.encode("utf-8", errors="ignore")) < 120:
            text += (
                "\n## Merge Note\n\n"
                "This file was generated by the deterministic recon worker "
                "merge. Downstream phases should treat it as a scoped recon "
                "handoff and inspect source files directly for confirmation.\n"
            )
        (scratchpad / name).write_text(text, encoding="utf-8")

    mode = str(config.get("mode") or "core")
    language = str(config.get("language") or "evm")
    project_root = str(config.get("project_root") or "")

    _write(
        "build_status.md",
        _existing_or_fallback(
            "build_status.md",
            "Build and Static Analysis Status",
            build,
        ),
    )

    design_text = _existing_or_fallback(
        "design_context.md",
        "Design Context",
        design,
    )
    if not re.search(r"(?im)^#+\s+.*operational\s+implications", design_text):
        design_text += (
            "\n\n## Operational Implications\n\n"
            "Recon workers did not isolate a separate operational implications "
            "section. Downstream agents must derive operational impact from the "
            "design, inventory, dependency, and attack-surface evidence above.\n"
        )
    if not re.search(r"(?im)^#+\s+.*invariant", design_text):
        design_text += (
            "\n\n## Key Invariants\n\n"
            "Recon workers did not isolate a separate invariant list. Breadth "
            "and depth agents must derive protocol invariants from entry points, "
            "state transitions, accounting flows, permissions, and external "
            "dependencies in the recon artifacts.\n"
        )
    _write("design_context.md", design_text)

    _write(
        "attack_surface.md",
        _existing_or_fallback(
            "attack_surface.md",
            "Attack Surface",
            inventory,
        ),
    )
    _write(
        "contract_inventory.md",
        _existing_or_fallback(
            "contract_inventory.md",
            "Contract Inventory",
            inventory,
        ),
    )
    _write(
        "function_list.md",
        _existing_or_fallback(
            "function_list.md",
            "Function List",
            inventory,
        ),
    )
    _write(
        "state_variables.md",
        _existing_or_fallback(
            "state_variables.md",
            "State Variables",
            inventory,
        ),
    )
    _write(
        "setter_list.md",
        _existing_or_fallback(
            "setter_list.md",
            "Setter List",
            inventory,
        ),
    )
    _write(
        "emit_list.md",
        _existing_or_fallback(
            "emit_list.md",
            "Event Emission List",
            inventory,
        ),
    )
    _write(
        "detected_patterns.md",
        _existing_or_fallback(
            "detected_patterns.md",
            "Detected Patterns",
            templates or inventory,
        ),
    )
    _write(
        "template_recommendations.md",
        _existing_or_fallback(
            "template_recommendations.md",
            "Template Recommendations",
            templates or inventory,
        ),
    )

    summary_parts = [
        "# Recon Summary",
        "",
        "## Run Context",
        "",
        f"- Pipeline: {config.get('pipeline', 'sc')}",
        f"- Mode: {mode}",
        f"- Language: {language}",
        f"- Project root: `{project_root}`",
        "",
        "## Worker Coverage",
        "",
    ]
    for name in shard_names:
        path = scratchpad / name
        if path.exists():
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            summary_parts.append(f"- `{name}`: present ({size} bytes)")
        else:
            summary_parts.append(f"- `{name}`: not present in this mode")
    summary_parts.extend([
        "",
        "## Canonical Handoff",
        "",
        "The Python driver merged isolated recon worker shards into the canonical "
        "recon artifacts consumed by instantiate, breadth, depth, verification, "
        "and report phases. Recon workers did not write later-phase artifacts.",
        "",
        "## Key Evidence Pointers",
        "",
        "Breadth and depth agents should read `design_context.md`, "
        "`attack_surface.md`, `contract_inventory.md`, `function_list.md`, "
        "`state_variables.md`, `detected_patterns.md`, and "
        "`template_recommendations.md` before deriving analysis scope.",
        "",
        _section("Build/Static Worker Summary", build)[:5000],
        "",
        _section("Design Worker Summary", design)[:5000],
        "",
        _section("Inventory Worker Summary", inventory)[:5000],
        "",
        _section("Template/Pattern Worker Summary", templates)[:5000],
    ])
    _write("recon_summary.md", "\n".join(summary_parts))
    return list(_RECON_CANONICAL_OUTPUTS)


def _synthesize_components_audited(scratchpad: Path) -> str:
    """Build a conservative Components Audited table from mechanical ledgers.

    Report quality should not depend on an LLM Index Agent remembering to emit
    a Components Audited section. Prefer `file_coverage_ledger.md` because it
    carries per-file status; fall back to headings from `subsystem_map.md`, then
    to file entries in `contract_inventory.md` for small SC projects.
    """
    ledger = scratchpad / "file_coverage_ledger.md"
    rows: dict[str, dict[str, int]] = {}
    if ledger.exists():
        try:
            text = _llm_norm(ledger.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            text = ""
        for line in text.splitlines():
            s = line.strip()
            if not s.startswith("|") or _is_separator_row(s):
                continue
            upper = s.upper()
            if re.search(r"\bFILE\b", upper) and (
                re.search(r"\bSTATUS\b", upper) or re.search(r"\bCOVERAGE\b", upper)
            ):
                continue
            cells = [c.strip(" `") for c in s.strip("|").split("|")]
            path = None
            status = "Catalogued"
            for cell in cells:
                p = _is_path_cell(cell)
                if p and not path:
                    path = p
                c = cell.upper()
                if c in {"COVERED", "READ", "CITED", "ACKNOWLEDGED", "LEFTOVER", "NOTREAD", "UNREAD", "UNCOVERED"}:
                    status = c.title()
            if not path:
                continue
            component = _module_key(path)
            bucket = rows.setdefault(
                component,
                {"files": 0, "covered": 0, "ack": 0, "leftover": 0},
            )
            bucket["files"] += 1
            if status.upper() in {"COVERED", "READ", "CITED"}:
                bucket["covered"] += 1
            elif status.upper() == "ACKNOWLEDGED":
                bucket["ack"] += 1
            elif status.upper() in {"LEFTOVER", "NOTREAD", "UNREAD", "UNCOVERED"}:
                bucket["leftover"] += 1
        if rows:
            lines = [
                "| Component | Files Catalogued | Covered | Acknowledged | Leftover |",
                "|-----------|------------------|---------|--------------|----------|",
            ]
            for component in sorted(rows):
                r = rows[component]
                lines.append(
                    f"| `{component}` | {r['files']} | {r['covered']} | "
                    f"{r['ack']} | {r['leftover']} |"
                )
            return "\n".join(lines)

    subsystem = scratchpad / "subsystem_map.md"
    if subsystem.exists():
        try:
            text = _llm_norm(subsystem.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            text = ""
        headings = []
        for m in re.finditer(r"(?m)^#{2,4}\s+(.+)$", text):
            h = m.group(1).strip()
            if h and len(h) < 120:
                headings.append(h)
        if headings:
            # D6 fix: compute Covered counts by matching inventory finding
            # locations to component (heading) prefixes. Pre-fix the table
            # had only Component + Source columns and no count — producing
            # the all-zero Covered column observed in the Irys report.
            covered_per_component: dict[str, int] = {h: 0 for h in headings}
            inv = scratchpad / "findings_inventory.md"
            if inv.exists():
                try:
                    inv_text = _llm_norm(inv.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    inv_text = ""
                for block in _inventory_blocks(inv_text):
                    loc = block.get("location", "") or ""
                    rel, _line = _parse_location_ref(loc)
                    if not rel:
                        continue
                    rel_norm = rel.replace("\\", "/").lstrip("./")
                    # Component matches if heading is a prefix of the path,
                    # OR if heading contains a path-segment that matches.
                    for h in headings:
                        h_norm = h.replace("\\", "/").lstrip("./").strip("`")
                        if (
                            rel_norm.startswith(h_norm + "/")
                            or rel_norm == h_norm
                            or h_norm in rel_norm
                        ):
                            covered_per_component[h] += 1
                            break  # Each finding counts once.
            lines = [
                "| Component | Covered (findings) | Coverage Source |",
                "|-----------|--------------------|-----------------|",
            ]
            for h in headings[:40]:
                lines.append(
                    f"| `{h}` | {covered_per_component.get(h, 0)} | subsystem_map.md |"
                )
            return "\n".join(lines)

    inventory = scratchpad / "contract_inventory.md"
    if inventory.exists():
        try:
            text = _llm_norm(inventory.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            text = ""
        cut = re.search(r"(?im)^##\s+Out[- ]of[- ]Scope\b", text)
        scoped_text = text[: cut.start()] if cut else text
        paths: list[str] = []
        for m in re.finditer(r"`([^`]+\.(?:sol|rs|go|move|cairo|vy|ts|js|py))`", scoped_text, re.IGNORECASE):
            path = m.group(1).replace("\\", "/").strip()
            if path and path not in paths:
                paths.append(path)
        if paths:
            lines = [
                "| Component | Source | Status |",
                "|-----------|--------|--------|",
            ]
            for path in paths[:60]:
                lines.append(f"| `{Path(path).name}` | `{path}` | In scope |")
            return "\n".join(lines)

    return ""


def _looks_like_code_location(value: str) -> bool:
    """Return True for source-code locations suitable for client findings."""
    v = (value or "").strip().strip("`")
    if not v:
        return False
    rel, _line = _parse_location_ref(v)
    if rel:
        return True
    if re.search(
        r"\.(?:cairo|move|hpp|cpp|tsx|jsx|sol|rs|go|py|cc|ts|js|vy|c|h)"
        r"(?::|#|$)",
        v,
        re.IGNORECASE,
    ):
        return True
    if re.search(
        r"\b(?:src|contracts|programs|sources|move|crates|packages|modules)/",
        v,
        re.IGNORECASE,
    ):
        return True
    return False


def _is_test_or_mock_location(value: str) -> bool:
    """Return True for PoC/test/harness paths that are not primary locations."""
    v = (value or "").replace("\\", "/").lower()
    name = Path(v.split(":", 1)[0]).name.lower()
    return (
        "/test/" in f"/{v}"
        or "/tests/" in f"/{v}"
        or "/mock" in f"/{v}"
        or "/mocks/" in f"/{v}"
        or name.startswith("test_")
        or name.endswith(".t.sol")
        or name.startswith("mock")
    )


def _extract_code_location_from_text(text: str) -> str:
    """Extract the first source-code location from verifier/report prose.

    Priority:
      1. An explicit `Location:` field whose value is a non-test source path.
      2. The first regex-matched source path in the prose that is NOT a
         test/mock file.
      3. The explicit `Location:` field even if not strictly a "looks like
         code" match — sometimes verifiers write `Location: src/Foo.sol`
         in narrative prose without the strict pattern.

    Returns empty string when the only candidates are test/mock paths.
    Pre-fix (May-2026 DODO audit), the function fell through to
    `candidates[0]` UNFILTERED when all candidates were test/mock — a
    STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION verify file that mentioned
    only the test harness produced `Location: VerifyCritHigh.t.sol` in
    the manifest, which then leaked into the body writer's output, the
    final report, and a useless client-facing "location" pointing at a
    test file rather than the source bug site. Downstream builders have
    an inventory/index fallback for empty locations; returning a
    known-bad test path actively defeats that fallback.
    """
    loc = (
        _field_from_markdown(
            text,
            ("Location", "Primary Location", "Affected Location", "Affected Locations"),
        )
        or ""
    ).strip()
    if loc and _looks_like_code_location(loc) and not _is_test_or_mock_location(loc):
        return loc
    candidates: list[str] = []
    for pattern in (
        r"\b(?:src|contracts|programs|sources|move|crates|packages|modules)"
        r"/[A-Za-z0-9_./-]+\.(?:cairo|move|hpp|cpp|tsx|jsx|sol|rs|go|py|cc|ts|js|vy|c|h)"
        r"(?![A-Za-z0-9_])(?::L?\d+(?:[-:]\d+)?)?",
        r"\b[A-Za-z0-9_./-]+\.(?:cairo|move|hpp|cpp|tsx|jsx|sol|rs|go|py|cc|ts|js|vy|c|h)"
        r"(?![A-Za-z0-9_])(?::L?\d+(?:[-:]\d+)?)?",
    ):
        for m in re.finditer(pattern, text or "", re.IGNORECASE):
            cand = m.group(0).strip("`")
            if cand not in candidates:
                candidates.append(cand)
    for cand in candidates:
        if not _is_test_or_mock_location(cand):
            return cand
    if loc and _looks_like_code_location(loc) and not _is_test_or_mock_location(loc):
        return loc
    # All remaining candidates are test/mock paths. Return empty so the
    # downstream builder can consult the inventory / report_index row
    # for the actual source location instead of writing a test file path
    # into the finding's Location field.
    return ""


def _verified_claim_title(verify_text: str) -> str:
    """Extract the client-facing claim title from a verifier artifact heading."""
    for m in re.finditer(r"(?m)^#{1,4}\s+(.+)$", verify_text or ""):
        title = m.group(1).strip()
        title = re.sub(
            r"(?i)^verification\s*:\s*(?:[A-Z]+-\d+\s*(?:[-:\u2013\u2014]\s*)?)?",
            "",
            title,
        ).strip(" -:\u2013\u2014")
        title = _sanitize_client_title(title)
        if title.lower() in {
            "description", "finding summary", "summary", "root cause",
            "analysis", "code trace", "impact", "recommendation",
            "suggested fix", "poc attempt", "execution result",
        }:
            continue
        if title and not _is_placeholder_report_title(title):
            return title
    return ""


def _title_tokens_for_conflict(title: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "into", "that", "this", "called",
        "missing", "incorrect", "allows", "causes", "leads", "verified",
        "finding", "contract", "function",
    }
    return {
        tok
        for tok in re.findall(r"[a-z0-9]{3,}", (title or "").lower())
        if tok not in stop
    }


def _titles_conflict(index_title: str, verify_title: str) -> bool:
    """Detect when report_index title and verifier title describe different bugs."""
    a = _title_tokens_for_conflict(index_title)
    b = _title_tokens_for_conflict(verify_title)
    if not a or not b:
        return False
    overlap = len(a & b)
    return overlap / max(1, min(len(a), len(b))) < 0.25


def _inventory_location_map(scratchpad: Path) -> dict[str, str]:
    """Map inventory IDs and source IDs to original inventory locations."""
    records_by_id, records_by_source = _load_finding_record_maps(scratchpad)
    out: dict[str, str] = {}
    for mapping in (records_by_id, records_by_source):
        for key, rec in mapping.items():
            loc = (rec.get("location") or "").strip()
            if (
                loc
                and _looks_like_code_location(loc)
                and not _is_test_or_mock_location(loc)
                and key not in out
            ):
                out[key] = loc

    inv = scratchpad / "findings_inventory.md"
    if not inv.exists():
        return out
    try:
        text = _llm_norm(inv.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    for block in _inventory_blocks(text):
        loc = (block.get("location") or "").strip()
        if not _looks_like_code_location(loc) or _is_test_or_mock_location(loc):
            continue
        keys: list[str] = []
        fid = _normalize_finding_id(block.get("id", "")) or block.get("id", "")
        if fid:
            keys.append(fid)
        for sid in _extract_finding_ids_from_text(block.get("source_ids", "")):
            keys.append(sid)
        for key in keys:
            norm = _normalize_finding_id(key) or key
            if norm and norm not in out:
                out[norm] = loc
    return out


def _records_from_inventory_text(text: str) -> list[dict[str, object]]:
    """Return structured finding records from `findings_inventory.md`.

    This is the immutable identity ledger for downstream phases. Markdown stays
    the human-readable artifact, but report/queue plumbing should not have to
    re-derive identity fields from prose at every phase boundary.
    """
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for block in _inventory_blocks(text):
        fid = _normalize_finding_id(block.get("id", "")) or block.get("id", "")
        if not fid or fid in seen:
            continue
        raw = block.get("block", "")
        source_ids = []
        for sid in _extract_finding_ids_from_text(block.get("source_ids", "")):
            norm = _normalize_finding_id(sid) or sid
            if norm and norm not in source_ids:
                source_ids.append(norm)
        severity = _severity_name_from_text(raw, {})
        preferred = (
            _field_from_markdown(raw, ("Preferred Tag", "Preferred Evidence", "Evidence Tag"))
            or EVIDENCE_TAG_DEFAULT
        )
        root_cause = _field_or_section(
            raw,
            ("Root Cause", "Vulnerability Class", "Bug Class", "Class"),
            ("Root Cause", "Vulnerability Class", "Bug Class", "Class"),
            fallback="",
            max_chars=3000,
        )
        description = _field_or_section(
            raw,
            ("Description", "Details", "Summary"),
            ("Description", "Details", "Summary", "Analysis"),
            fallback="",
            max_chars=5000,
        )
        impact = _field_or_section(
            raw,
            ("Impact", "Risk"),
            ("Impact", "Risk", "Security Impact"),
            fallback="",
            max_chars=3000,
        )
        records.append({
            "inventory_id": fid,
            "source_ids": source_ids,
            "title": block.get("title", "") or fid,
            "severity": severity,
            "location": block.get("location", "") or _field_from_markdown(raw, ("Location", "Locations")),
            "preferred_tag": _extract_first_tag(preferred) or _strip_md(preferred) or EVIDENCE_TAG_DEFAULT,
            "verdict": _field_from_markdown(raw, ("Verdict", "Final Verdict", "Status")),
            "root_cause": root_cause,
            "description": description,
            "impact": impact,
            "raw_block_len": len(raw),
        })
        seen.add(fid)
    return records


def _write_finding_records_from_inventory(scratchpad: Path) -> int:
    """Write `finding_records.json` from the current inventory markdown."""
    inv = scratchpad / "findings_inventory.md"
    if not inv.exists():
        return 0
    try:
        text = _llm_norm(inv.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0
    records = _records_from_inventory_text(text)
    if not records:
        return 0
    payload = {
        "schema_version": "plamen.finding_records.v1",
        "source": "findings_inventory.md",
        "records": records,
    }
    try:
        (scratchpad / "finding_records.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return 0
    return len(records)


_CANONICAL_FINDING_ID_MAP_NAME = "_canonical_finding_ids.json"
_CANONICAL_FINDING_ID_SCHEMA = "plamen.canonical_finding_ids.v1"
_UNMAPPED_ID_TOKENS_NAME = "_unmapped_id_tokens.json"
_UNMAPPED_ID_SCHEMA = "plamen.unmapped_id_tokens.v1"

_CANONICAL_ID_PRODUCER_PATTERNS: tuple[str, ...] = (
    "analysis_*.md",
    "analysis_rescan_*.md",
    "analysis_percontract_*.md",
    "graph_sweep*.md",
    "coverage_fill_*.md",
    "panic_audit_*.md",
    "panic_audit_summary.md",
    "symmetric_pair_findings.md",
    "field_validation_matrix.md",
    "primitive_correctness_findings.md",
    "network_amplification_findings.md",
    "lifecycle_replay_findings.md",
    "findings_inventory.md",
    "findings_inventory_chunk_*.md",
    "depth_*_findings.md",
    "blind_spot_*_findings.md",
    "validation_sweep_findings.md",
    "niche_*_findings.md",
    "medusa_fuzz_findings.md",
    "invariant_fuzz_results.md",
    "design_stress_findings.md",
    "depth_design_stress_findings.md",
    "perturbation_findings.md",
    "depth_perturbation_findings.md",
    "attention_repair_summary.md",
    "rag_validation.md",
    "hypotheses.md",
    "chain_hypotheses.md",
    "chain_iteration2.md",
    "post_verify_new_observations.md",
    "skeptic_findings.md",
    "cross_batch_consistency.md",
    "report_index.md",
)

_CANONICAL_ID_SKIP_PREFIXES: tuple[str, ...] = (
    "_prompt_", "_stdio_", "_continuation_", "_retry_", "_canonical_",
)

_GENERIC_ID_TOKEN_RE = re.compile(
    r"\b[A-Z]{2,12}[A-Z0-9]*(?:-[A-Z0-9_]+)*-\d+\b"
)
_COMMON_NON_FINDING_ID_RE = re.compile(
    r"^(?:ERC|EIP|BIP|RFC|CAIP|SLIP|CVE|CWE|PR|IP|HTTP|TLS)-\d+",
    re.IGNORECASE,
)


def _canonical_id_norm(value: str) -> str:
    return re.sub(r"\s+", " ", _strip_md(value or "")).strip().lower()


def _canonical_finding_hash(parts: dict[str, str]) -> str:
    immutable = {
        "artifact": parts.get("artifact", ""),
        "local_id": _canonical_id_norm(parts.get("local_id", "")),
        "title": _canonical_id_norm(parts.get("title", "")),
        "location": _canonical_id_norm(parts.get("location", "")),
        "root_cause": _canonical_id_norm(parts.get("root_cause", "")),
        "source_ids": _canonical_id_norm(parts.get("source_ids", "")),
    }
    blob = json.dumps(immutable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _iter_finding_blocks_with_meta(text: str) -> list[dict[str, Any]]:
    matches = list(FINDING_BLOCK_HEADING_RE.finditer(text or ""))
    out: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        line_end = text.find("\n", match.start(), end)
        if line_end < 0:
            line_end = end
        heading = text[match.start():line_end]
        title = ""
        m_title = re.search(r"\]\s*[:\-–—]?\s*(.+)$", heading)
        if m_title:
            title = _strip_md(m_title.group(1)).strip()
        out.append({
            "local_id": match.group(1).strip(),
            "title": title,
            "block": block,
            "offset": start,
        })
    return out


def _producer_artifact_paths_for_identity(scratchpad: Path) -> list[Path]:
    seen: set[str] = set()
    paths: list[Path] = []
    for pattern in _CANONICAL_ID_PRODUCER_PATTERNS:
        for p in sorted(scratchpad.glob(pattern)):
            if not p.is_file():
                continue
            if p.name in seen or p.name.startswith(_CANONICAL_ID_SKIP_PREFIXES):
                continue
            if p.name.startswith("analysis_merged_into_"):
                continue
            seen.add(p.name)
            paths.append(p)
    return paths


def _canonical_identity_records_from_artifact(path: Path) -> list[dict[str, Any]]:
    try:
        text = _llm_norm(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    records: list[dict[str, Any]] = []
    for item in _iter_finding_blocks_with_meta(text):
        block = str(item.get("block") or "")
        local_id_raw = str(item.get("local_id") or "").strip()
        local_id = _normalize_finding_id(local_id_raw) or local_id_raw
        title = str(item.get("title") or "").strip()
        if not title:
            title = _field_from_markdown(block, ("Title", "Finding", "Issue")) or local_id
        severity = normalize_severity(_field_from_markdown(block, ("Severity", "Risk Level", "Level")))
        location = _field_from_markdown(block, ("Location", "Locations", "Code Location", "File"))
        root_cause = _field_from_markdown(block, ("Root Cause", "Cause", "Invariant Broken"))
        source_ids = _field_from_markdown(
            block,
            ("Source IDs", "Source ID", "Sources", "Constituent Findings", "Internal Finding IDs"),
        )
        parts = {
            "artifact": path.name,
            "local_id": local_id,
            "title": title,
            "location": location,
            "root_cause": root_cause,
            "source_ids": source_ids,
        }
        digest = _canonical_finding_hash(parts)
        referenced = sorted({
            m.group(1).upper()
            for m in _INTERNAL_FINDING_ID_RE.finditer(block)
            if m.group(1).upper() != local_id.upper()
        })
        records.append({
            "canonical_id": "CID-" + digest[:16].upper(),
            "fingerprint": "sha256:" + digest,
            "artifact": path.name,
            "offset": int(item.get("offset") or 0),
            "local_id": local_id,
            "local_id_raw": local_id_raw,
            "title": title,
            "severity": severity,
            "location": location,
            "root_cause": root_cause,
            "source_ids_text": source_ids,
            "referenced_ids": referenced,
            "raw_block_len": len(block),
        })
    return records


def _collect_unmapped_id_tokens(scratchpad: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for p in sorted(scratchpad.glob("*.md")):
        if not p.is_file() or p.name.startswith(_CANONICAL_ID_SKIP_PREFIXES):
            continue
        try:
            text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for match in _GENERIC_ID_TOKEN_RE.finditer(text):
            token = match.group(0).upper()
            if _COMMON_NON_FINDING_ID_RE.match(token):
                continue
            if _INTERNAL_FINDING_ID_RE.fullmatch(token):
                continue
            key = (p.name, token)
            if key in seen:
                continue
            seen.add(key)
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 80)
            context = re.sub(r"\s+", " ", text[start:end]).strip()
            rows.append({
                "artifact": p.name,
                "token": token,
                "context": context[:240],
            })
    return rows


def _write_canonical_finding_identity_map(
    scratchpad: Path,
    *,
    phase_name: str = "",
    pipeline: str = "",
    mode: str = "",
) -> int:
    """Write deterministic finding-identity sidecars without mutating artifacts.

    This is the first step toward Python-owned final IDs: producers may still
    emit local IDs, but the driver records a stable content fingerprint and
    CID alias for every parseable finding block. Nothing is dropped or merged.
    """
    records: list[dict[str, Any]] = []
    for path in _producer_artifact_paths_for_identity(scratchpad):
        records.extend(_canonical_identity_records_from_artifact(path))
    records.sort(key=lambda r: (str(r.get("artifact")), int(r.get("offset") or 0), str(r.get("local_id"))))
    payload = {
        "schema_version": _CANONICAL_FINDING_ID_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_phase": phase_name,
        "pipeline": pipeline,
        "mode": mode,
        "record_count": len(records),
        "records": records,
    }
    try:
        (scratchpad / _CANONICAL_FINDING_ID_MAP_NAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        return 0

    unmapped = _collect_unmapped_id_tokens(scratchpad)
    try:
        (scratchpad / _UNMAPPED_ID_TOKENS_NAME).write_text(
            json.dumps(
                {
                    "schema_version": _UNMAPPED_ID_SCHEMA,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "last_phase": phase_name,
                    "token_count": len(unmapped),
                    "tokens": unmapped,
                },
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
    return len(records)


def _load_finding_record_maps(
    scratchpad: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    """Return maps keyed by inventory ID and original source IDs."""
    path = scratchpad / "finding_records.json"
    inv = scratchpad / "findings_inventory.md"
    if inv.exists() and (not path.exists() or path.stat().st_mtime < inv.stat().st_mtime):
        _write_finding_records_from_inventory(scratchpad)
    if not path.exists():
        return {}, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}, {}
    records = payload.get("records", [])
    if not isinstance(records, list):
        return {}, {}
    by_id: dict[str, dict[str, object]] = {}
    by_source: dict[str, dict[str, object]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        fid = _normalize_finding_id(str(rec.get("inventory_id", ""))) or str(rec.get("inventory_id", "")).strip()
        if fid and fid not in by_id:
            by_id[fid] = rec
        for sid in rec.get("source_ids", []) or []:
            norm = _normalize_finding_id(str(sid)) or str(sid).strip()
            if norm and norm not in by_source:
                by_source[norm] = rec
    return by_id, by_source


def _finding_record_for_ids(
    scratchpad: Path,
    finding_ids: list[str],
    maps: tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]] | None = None,
) -> dict[str, object]:
    by_id, by_source = maps if maps is not None else _load_finding_record_maps(scratchpad)
    for fid in finding_ids:
        norm = _normalize_finding_id(fid) or fid
        if norm in by_id:
            return by_id[norm]
        if norm in by_source:
            return by_source[norm]
    return {}


def _is_placeholder_report_title(title: str) -> bool:
    """Return True for generated titles that should inherit source identity."""
    t = re.sub(r"\s+", " ", (title or "").strip()).lower()
    if not t:
        return True
    if t in {"verified finding", "upstream finding", "excluded finding"}:
        return True
    return bool(re.fullmatch(
        r"(?:unverified|verified)?\s*(?:critical|high|medium|low|informational)"
        r"(?:-|\s)+severity finding(?:\s*-\s*[A-Z][A-Z0-9-]*\d+)?",
        t,
        re.IGNORECASE,
    ))


def _record_title(record: dict[str, object]) -> str:
    title = _sanitize_client_title(str(record.get("title", "") or "").strip())
    return "" if _is_placeholder_report_title(title) else title


def _inventory_location_for_ids(
    scratchpad: Path,
    finding_ids: list[str],
    location_by_id: dict[str, str] | None = None,
) -> str:
    """Recover original inventory location for report/verify IDs."""
    loc_map = location_by_id if location_by_id is not None else _inventory_location_map(scratchpad)
    if not loc_map:
        return ""
    candidates: list[str] = []
    for fid in finding_ids:
        norm = _normalize_finding_id(fid) or fid
        if norm:
            candidates.append(norm)
    try:
        hyp_map = _parse_hypothesis_constituents(scratchpad)
    except Exception:
        hyp_map = {}
    for fid in list(candidates):
        for child in hyp_map.get(fid, []) or []:
            norm = _normalize_finding_id(child) or child
            if norm and norm not in candidates:
                candidates.append(norm)
    for fid in candidates:
        loc = loc_map.get(fid, "")
        if _looks_like_code_location(loc) and not _is_test_or_mock_location(loc):
            return loc
    return ""


def _build_internal_traceability_from_records(scratchpad: Path) -> str:
    """Build internal traceability from deterministic report_records.json."""
    records_path = scratchpad / "report_records.json"
    if not records_path.exists():
        return ""
    try:
        data = json.loads(records_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    active = data.get("active") or []
    excluded = data.get("excluded") or []
    lines = [
        "# Internal Report Traceability",
        "",
        "| Report ID | Internal Hypothesis | Chain | Verification | Agent Sources |",
        "|-----------|---------------------|-------|--------------|---------------|",
    ]
    for rec in active:
        rid = str(rec.get("report_id") or "").strip()
        if not rid:
            continue
        ids = rec.get("absorbed_finding_ids") or [rec.get("finding_id")]
        ids = _clean_finding_id_list(ids)
        verify_refs = ", ".join(f"verify_{i}.md" for i in ids) or "n/a"
        source_refs = ", ".join(ids) or "n/a"
        chain_refs = ", ".join(i for i in ids if i.upper().startswith("CH-")) or "n/a"
        lines.append(
            f"| {rid} | {', '.join(ids).replace('|', '/')} | "
            f"{chain_refs.replace('|', '/')} | "
            f"{verify_refs.replace('|', '/')} | {source_refs.replace('|', '/')} |"
        )
    lines.extend([
        "",
        "### Excluded Findings",
        "",
        "| Internal ID | Severity | Title | Exclusion Reason |",
        "|-------------|----------|-------|------------------|",
    ])
    for rec in excluded:
        lines.append(
            f"| {str(rec.get('finding_id', '')).replace('|', '/')} | "
            f"{str(rec.get('severity', '')).replace('|', '/')} | "
            f"{str(rec.get('title', '')).replace('|', '/')} | "
            f"{str(rec.get('reason', rec.get('verdict', ''))).replace('|', '/')} |"
        )
    return "\n".join(lines).strip()


def _build_client_excluded_appendix_from_records(scratchpad: Path) -> str:
    """Build client-facing excluded findings without internal IDs."""
    records_path = scratchpad / "report_records.json"
    if not records_path.exists():
        return ""
    try:
        data = json.loads(records_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    excluded = data.get("excluded") or []
    if not excluded:
        return ""
    lines = [
        "| Severity | Title | Exclusion Reason |",
        "|----------|-------|------------------|",
    ]
    for rec in excluded:
        title = _sanitize_client_title(str(rec.get("title", "") or "Excluded finding"))
        reason = _sanitize_client_body(str(rec.get("reason", rec.get("verdict", "")) or ""))
        lines.append(
            f"| {str(rec.get('severity', '')).replace('|', '/')} | "
            f"{title.replace('|', '/')} | "
            f"{reason.replace('|', '/')} |"
        )
    return "\n".join(lines).strip()


def _synth_report_section_from_verify(
    scratchpad: Path,
    report_id: str,
    finding_id: str,
    queue_row: dict[str, str],
    unresolved: bool,
) -> str:
    """Build a body section from verified artifacts when a tier writer omits it.

    This is deterministic report repair, not new audit analysis: it only
    promotes a finding already present in the verification queue and, when
    available, its verifier output.
    """
    verify_path = _verify_file_for_id(scratchpad, finding_id)
    try:
        verify_text = _llm_norm(verify_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        verify_text = ""
    record = _finding_record_for_ids(scratchpad, [finding_id])

    # Closes F-RPT-01: detect when verify file is missing/empty so the
    # synthesized section is tagged STUB-RECOVERED rather than silently
    # manufacturing CONFIRMED/CODE-TRACE semantics from absent evidence.
    stub_recovered = not (verify_text and verify_text.strip())

    title = (
        (queue_row.get("title") or "").strip()
        or str(record.get("title", "") if record else "").strip()
        or _verified_claim_title(verify_text)
        or _first_heading_title(verify_text)
        or "Verified finding"
    )
    title = _sanitize_client_title(re.sub(r"\s+", " ", title).strip())
    if record and _is_placeholder_report_title(title):
        title = _record_title(record) or title
    severity = SEVERITY_FROM_LETTER.get(report_id[:1], normalize_severity(queue_row.get("severity", "Medium")))
    raw_verdict = _field_from_markdown(verify_text, ("Verdict",))
    verdict = _sanitize_client_body(raw_verdict) if raw_verdict else (
        "UNRESOLVED" if stub_recovered else "CONFIRMED"
    )
    preferred_tag = (
        _field_from_markdown(verify_text, ("Preferred Tag", "Preferred Evidence"))
        or queue_row.get("preferred tag", "")
        or EVIDENCE_TAG_DEFAULT
    )
    evidence_tag = (
        _field_from_markdown(verify_text, ("Evidence Tag", "Evidence"))
        or preferred_tag
    )
    location_candidates = [
        _extract_code_location_from_text(verify_text) if verify_text else "",
        queue_row.get("location", "").strip(),
        str(record.get("location", "") if record else "").strip(),
        _field_from_markdown(verify_text, ("Location", "Primary Location")),
    ]
    location = ""
    for cand in location_candidates:
        cand = (cand or "").strip()
        if cand and _looks_like_code_location(cand) and not _is_test_or_mock_location(cand):
            location = cand
            break
    if not location:
        for cand in location_candidates:
            cand = (cand or "").strip()
            if cand:
                location = cand
                break
    location = _sanitize_client_body(location or "See verification artifact")
    bug_class = _sanitize_client_body(queue_row.get("bug class", "").strip()) or "verified issue"
    suffixes: list[str] = []
    if stub_recovered:
        suffixes.append("[STUB-RECOVERED]")
    if unresolved:
        suffixes.append("[UNRESOLVED - needs human review]")
    header_suffix = (" " + " ".join(suffixes)) if suffixes else ""
    unresolved_note = (
        "\n**Human review**: Skeptic-Judge marked this finding UNRESOLVED/PARTIAL; "
        "severity was retained/demoted per report policy and the finding remains "
        "in the body.\n"
        if unresolved else ""
    )
    description = _field_or_section(
        verify_text,
        ("Description", "Finding Summary", "Summary", "Root Cause"),
        ("Finding Summary", "Analysis", "Code Trace", "Description", "Root Cause"),
        fallback=(
            queue_row.get("description", "").strip()
            or str(record.get("description", "") if record else "").strip()
            or str(record.get("root_cause", "") if record else "").strip()
            or queue_row.get("root cause", "").strip()
            or "Verifier artifact did not include a narrative description."
        ),
    )
    impact = _field_or_section(
        verify_text,
        ("Impact", "Combined Impact", "Risk"),
        ("Combined Impact", "Impact", "Risk", "Security Impact"),
        fallback=(
            queue_row.get("impact", "").strip()
            or str(record.get("impact", "") if record else "").strip()
            or "Verifier evidence ties the cited code path to the assigned severity; review the code trace and severity rationale for exploitability details."
        ),
        max_chars=2500,
    )
    poc_result = _field_or_section(
        verify_text,
        ("PoC Result", "Execution Output", "Test Output", "Proof"),
        ("PoC Result", "Execution Output", "Test Output", "Proof", "Reproduction"),
        fallback="No executable PoC was recorded; verifier relied on code trace evidence.",
        max_chars=2500,
    )
    recommendation = _field_or_section(
        verify_text,
        ("Recommendation", "Suggested Fix", "Fix", "Mitigation"),
        ("Suggested Fix", "Recommendation", "Mitigation", "Fix"),
        fallback="Apply the verifier-recommended mitigation and add a regression test for the cited path.",
        max_chars=3000,
    )
    severity_rationale = _field_or_section(
        verify_text,
        ("Severity Rationale", "Severity rationale"),
        ("Severity Rationale", "Exploitability", "Precondition", "Preconditions"),
        fallback=f"Verified `{bug_class}` finding with {evidence_tag} evidence.",
        max_chars=1800,
    )
    description = _sanitize_client_body(description)
    impact = _sanitize_client_body(impact)
    poc_result = _sanitize_client_body(poc_result)
    recommendation = _sanitize_client_body(recommendation)
    generic_fallback_markers = (
        "Verifier artifact did not include",
        "Impact was not separately summarized",
        "Verifier evidence ties the cited code path",
        "No executable PoC was recorded; verifier relied on code trace evidence",
        "Apply the verifier-recommended mitigation",
        "Apply the verifier-recommended mitigation and add a regression test",
    )
    insufficient_body_evidence = (
        stub_recovered
        or any(marker in description for marker in generic_fallback_markers)
        or any(marker in impact for marker in generic_fallback_markers)
        or any(marker in poc_result for marker in generic_fallback_markers)
        or any(marker in recommendation for marker in generic_fallback_markers)
        or not _is_substantive_body_evidence(description)
        or not _is_substantive_body_evidence(impact)
        or (
            report_id[:1].upper() in {"C", "H", "M"}
            and not _is_substantive_body_evidence(poc_result)
        )
    )
    heading_prefix = (
        "[REPORT-BLOCKED: insufficient verifier evidence] "
        if insufficient_body_evidence else ""
    )

    confidence_parts = []
    if evidence_tag and evidence_tag not in ("N/A", "unknown"):
        confidence_parts.append(f"PoC: {_sanitize_client_body(evidence_tag)}")
    confidence_level = "HIGH" if has_mechanical_proof(evidence_tag or "") else "MEDIUM"
    confidence_line = f"**Confidence**: {confidence_level}"
    if confidence_parts:
        confidence_line += f" ({', '.join(confidence_parts)})"

    return "\n".join([
        f"### {heading_prefix}[{report_id}] {title}{header_suffix}",
        "",
        f"**Severity**: {severity}",
        f"**Verdict**: {verdict}",
        f"**Location**: {location}",
        confidence_line,
        unresolved_note.rstrip(),
        "",
        "**Description**:",
        description,
        "",
        "**Impact**:",
        impact,
        "",
        "**PoC Result**:",
        poc_result,
        "",
        "**Recommendation**:",
        recommendation,
        "",
    ]).replace("\n\n\n", "\n\n").strip()


def _repair_report_body_from_assignments(body: str, scratchpad: Path) -> str:
    """Append missing assigned report sections and apply UNRESOLVED flags."""
    assignments, _source = get_tier_assignments(scratchpad)
    if not assignments:
        return body

    queue_rows = {
        (r.get("finding id") or "").strip(): r
        for r in parse_verification_queue_rows(scratchpad)
        if (r.get("finding id") or "").strip()
    }
    unresolved_ids = _collect_judge_unresolved_ids(scratchpad)

    # First, tag existing assigned body sections that correspond to unresolved
    # internal IDs but lack the required body marker.
    unresolved_report_ids = {
        a.get("report_id", "")
        for a in assignments
        if a.get("finding_id", "") in unresolved_ids
    }
    fixed_lines: list[str] = []
    header_re = re.compile(r"^(###\s*\[([CHMLI]-\d+)\].*)$")
    for line in body.splitlines():
        m = header_re.match(line)
        if m and m.group(2) in unresolved_report_ids and "[UNRESOLVED" not in line:
            line = line + " [UNRESOLVED - needs human review]"
        fixed_lines.append(line)
    body = "\n".join(fixed_lines)

    present = set(re.findall(r"^###\s*(?:\[REPORT-BLOCKED[^\]]*\]\s*)?\[([CHMLI]-\d+)\]", body, re.MULTILINE))
    missing_sections: list[str] = []
    for a in assignments:
        rid = a.get("report_id", "")
        fid = a.get("finding_id", "")
        if not rid or not fid or rid in present:
            continue
        missing_sections.append(
            _synth_report_section_from_verify(
                scratchpad,
                rid,
                fid,
                queue_rows.get(fid, {}),
                fid in unresolved_ids,
            )
        )
        present.add(rid)
    if not missing_sections:
        return body

    insertion = (
        "\n\n".join(missing_sections)
        + "\n\n---\n\n"
    )
    marker = "\n## Priority Remediation Order"
    if marker in body:
        return body.replace(marker, "\n" + insertion + "## Priority Remediation Order", 1)
    return body.rstrip() + "\n\n---\n\n" + insertion


def _synth_report_section_from_manifest_row(scratchpad: Path, row: dict[str, object]) -> str:
    report_id = str(row.get("report_id", "") or "").strip().upper()
    title = _sanitize_client_title(str(row.get("title", "") or "Verified finding"))
    severity = normalize_severity(str(row.get("severity", "") or report_id[:1]))
    location = _sanitize_client_body(str(row.get("location", "") or "See verification artifacts"))
    evidence = _sanitize_client_body(str(row.get("evidence_tag", "") or EVIDENCE_TAG_DEFAULT))
    verify_files = [str(v) for v in (row.get("verify_files") or []) if str(v).strip()]

    desc_parts: list[str] = []
    impact_parts: list[str] = []
    poc_parts: list[str] = []
    rec_parts: list[str] = []
    verdicts: list[str] = []
    for vf in verify_files:
        p = scratchpad / vf
        if not p.exists():
            continue
        try:
            txt = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            txt = ""
        if not txt:
            continue
        verdict = _verifier_status_from_text(txt)
        if verdict:
            verdicts.append(verdict)
        desc = _field_or_section(
            txt,
            ("Description", "Finding Summary", "Summary", "Root Cause"),
            ("Finding Summary", "Analysis", "Code Trace", "Description", "Root Cause"),
            fallback="",
            max_chars=1800,
        )
        impact = _field_or_section(
            txt,
            ("Impact", "Combined Impact", "Risk"),
            ("Combined Impact", "Impact", "Risk", "Security Impact"),
            fallback="",
            max_chars=1200,
        )
        poc = _field_or_section(
            txt,
            ("PoC Result", "Execution Output", "Test Output", "Proof"),
            ("PoC Result", "Execution Output", "Test Output", "Proof", "Reproduction"),
            fallback="",
            max_chars=1200,
        )
        rec = _field_or_section(
            txt,
            ("Recommendation", "Suggested Fix", "Fix", "Mitigation"),
            ("Suggested Fix", "Recommendation", "Mitigation", "Fix"),
            fallback="",
            max_chars=1400,
        )
        label = vf.replace("|", "/")
        if _is_substantive_body_evidence(desc):
            desc_parts.append(f"From `{label}`:\n{_sanitize_client_body(desc)}")
        if _is_substantive_body_evidence(impact):
            impact_parts.append(f"From `{label}`:\n{_sanitize_client_body(impact)}")
        if _is_substantive_body_evidence(poc):
            poc_parts.append(f"From `{label}`:\n{_sanitize_client_body(poc)}")
        if _is_substantive_body_evidence(rec):
            rec_parts.append(f"From `{label}`:\n{_sanitize_client_body(rec)}")

    desc = "\n\n".join(desc_parts) or _sanitize_client_body(str(row.get("description", "") or "Verified evidence is listed in the shard manifest."))
    impact = "\n\n".join(impact_parts) or "Impact follows from the verified constituent evidence and report-index severity assignment."
    poc = "\n\n".join(poc_parts) or "No executable PoC output was recorded; verifier relied on code trace evidence."
    rec = "\n\n".join(rec_parts) or _sanitize_client_body(str(row.get("recommendation", "") or "Apply the mitigation described by the verifier artifacts and add regression coverage."))
    verdict = " / ".join(dict.fromkeys(verdicts)) or "CONFIRMED"
    confidence = "HIGH" if has_mechanical_proof(evidence) or any("POC-PASS" in p for p in poc_parts) else "MEDIUM"

    return "\n".join([
        f"### [{report_id}] {title}",
        "",
        f"**Severity**: {severity}",
        f"**Verdict**: {verdict}",
        f"**Location**: {location}",
        f"**Confidence**: {confidence} ({evidence})",
        "",
        "**Description**:",
        desc,
        "",
        "**Impact**:",
        impact,
        "",
        "**PoC Result**:",
        poc,
        "",
        "**Recommendation**:",
        rec,
        "",
    ]).replace("\n\n\n", "\n\n").strip()


def _replace_report_section(body: str, report_id: str, replacement: str) -> str:
    section = _section_for_report_id(body, report_id)
    if not section:
        return body.rstrip() + "\n\n" + replacement.strip() + "\n"
    start = body.find(section)
    if start < 0:
        return body
    end = start + len(section)
    return body[:start].rstrip() + "\n\n" + replacement.strip() + "\n\n" + body[end:].lstrip()


def _sanitize_undefined_report_references(body: str) -> str:
    """Remove report-like IDs from prose when no matching body section exists."""
    defined = {
        m.group(1).upper()
        for m in re.finditer(
            r"(?im)^###\s*(?:\[REPORT-BLOCKED[^\]]*\]\s*)?\[([CHMLI]-\d+)\]",
            body or "",
        )
    }

    def repl(match: re.Match[str]) -> str:
        prefix = match.group(1)
        rid = _normalize_report_id(match.group(2))
        if rid in defined:
            return match.group(0)
        return f"{prefix} the related finding"

    return re.sub(
        r"(?i)\b(duplicate of|duplicates|same as|absorbed by|absorbs|related to|"
        r"cross-reference(?:d)? to)\s+(?:\[\s*)?([CHMLI]-\d{1,3})(?:\s*\])?",
        repl,
        body or "",
    )


def _section_has_substantive_report_body(section: str) -> bool:
    text = re.sub(r"(?im)^###\s+.*$", "", section or "")
    if len(text.strip()) < 350:
        return False
    if not _is_substantive_body_evidence(text):
        return False
    return bool(re.search(
        r"(?im)^\*\*(?:Description|Impact|Recommendation|PoC Result|Evidence Tag)\*\*\s*:",
        section or "",
    ))


def _normalize_report_blocked_markers(body: str) -> tuple[str, int]:
    """Downgrade stale REPORT-BLOCKED headings to ordinary UNVERIFIED prose.

    `REPORT-BLOCKED` is a pipeline failure marker, not a synonym for "no
    executable verifier file." If a section already has client-usable body
    text, location, impact, and recommendation, keep it in the report as
    low-confidence/unverified rather than poisoning final report quality.
    """
    out: list[str] = []
    last = 0
    changed = 0
    pat = re.compile(
        r"(?im)^###\s+(?P<head>[^\n]*\[REPORT-BLOCKED[^\]]*\][^\n]*)\n"
    )
    matches = list(pat.finditer(body or ""))
    for i, m in enumerate(matches):
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(body or "")
        section = (body or "")[m.start():section_end]
        out.append((body or "")[last:m.start()])
        heading = m.group("head")
        if (
            re.search(r"\[[CHMLI]-\d+\]", heading, re.IGNORECASE)
            and _section_has_substantive_report_body(section)
        ):
            clean_heading = re.sub(
                r"\s*\[REPORT-BLOCKED[^\]]*\]\s*",
                " ",
                heading,
                flags=re.IGNORECASE,
            )
            clean_heading = re.sub(r"\s+", " ", clean_heading).strip()
            out.append("### " + clean_heading + "\n")
            changed += 1
        else:
            out.append(m.group(0))
        last = m.end()
    out.append((body or "")[last:])
    return "".join(out), changed


def _repair_report_body_from_manifest(scratchpad: Path, phase_name: str) -> int:
    if not phase_name.startswith("report_body_writer_"):
        return 0
    shard_key = phase_name.replace("report_body_writer_", "report_")
    manifest_path = scratchpad / "body_manifests" / f"{shard_key}.json"
    body_path = scratchpad / f"{shard_key}.md"
    if not manifest_path.exists() or not body_path.exists():
        return 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        body = body_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    repaired = 0
    for row in manifest.get("findings", []) or []:
        rid = str(row.get("report_id", "") or "").upper()
        if not rid or row.get("report_blocked"):
            continue
        section = _section_for_report_id(body, rid)
        loc = str(row.get("location", "") or "").strip()
        needs_location_repair = bool(
            loc and section and not _location_present_in_body(loc, section)
        )
        if "[REPORT-BLOCKED" not in section.upper() and not needs_location_repair:
            continue
        body = _replace_report_section(
            body,
            rid,
            _synth_report_section_from_manifest_row(scratchpad, row),
        )
        repaired += 1
    if repaired:
        body_path.write_text(body.rstrip() + "\n", encoding="utf-8")
    return repaired


def _normalize_tier_report_blocked_markers(scratchpad: Path, filenames: list[str]) -> int:
    total = 0
    for name in filenames:
        p = scratchpad / name
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        new_text, changed = _normalize_report_blocked_markers(text)
        if changed:
            p.write_text(new_text.rstrip() + "\n", encoding="utf-8")
            total += changed
    return total


def _patch_report_index_with_recovered(
    scratchpad: Path,
    recovered: list[tuple[str, str, str, str]],
) -> int:
    """Append promotion-recovered finding rows to report_index.md.

    v2.5.4: Without this, _repair_promotion_dropouts adds body sections
    that get_tier_assignments() doesn't know about → body_assignment_count
    FAIL (61 vs 58) and _check_promotion_symmetry can't find the internal
    IDs → promotion_receipt FAIL. Both create an infinite degraded loop.

    Each tuple is ``(finding_id, report_id, title, severity)``.
    Returns number of rows actually appended (skips duplicates).
    """
    if not recovered:
        return 0
    ri = scratchpad / "report_index.md"
    if not ri.exists():
        return 0
    try:
        text = ri.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    new = [
        (fid, rid, title, sev)
        for fid, rid, title, sev in recovered
        if not re.search(rf"(?<!\w){re.escape(fid)}(?!\w)", text)
    ]
    if not new:
        return 0

    rows = []
    for fid, rid, title, sev in new:
        title_clean = title.replace("|", "/").strip()[:80]
        rows.append(
            f"| {rid} | {title_clean} | {sev} | - | [CODE-TRACE] | "
            f"CONFIRMED | RECOVERED | {fid} |"
        )

    block = "\n".join(rows) + "\n"

    cut_re = re.compile(
        r"(?m)^\s*#{1,4}\s+(?:Excluded|Tier\s+Assignment|Consolidation\s+Map|"
        r"Cross-Reference|Appendix|Promotion\s+Failure|Report\s+Coverage)",
        re.IGNORECASE,
    )
    m = cut_re.search(text)
    if m:
        text = text[: m.start()] + "\n" + block + "\n" + text[m.start() :]
    else:
        text = text.rstrip() + "\n\n" + block

    try:
        ri.write_text(text, encoding="utf-8")
    except Exception:
        return 0
    return len(new)


def _repair_promotion_dropouts(body: str, scratchpad: Path) -> str:
    """Synthesize sections for CONFIRMED verify findings not in the report.

    v2.5.2: Catches the VS-*/EN-* dropout class where the Index Agent
    omitted a CONFIRMED finding from the Master Finding Index, causing
    _repair_report_body_from_assignments to miss it (no tier assignment).
    This pass uses verify receipts directly — if a finding was CONFIRMED
    in a verify file and doesn't appear in the body, synthesize a section.

    v2.5.4: Also patches report_index.md with recovered mappings so that
    get_tier_assignments() and _check_promotion_symmetry() see them.
    Without this, body sections=61 vs assignments=58 → infinite FAIL loop.
    """
    receipts = _collect_verify_promotion_receipts(scratchpad)
    if not receipts:
        return body

    assignments, _ = get_tier_assignments(scratchpad)
    assigned_internal = {
        a.get("finding_id", "") for a in assignments if a.get("finding_id")
    }

    from plamen_parsers import _INTERNAL_FINDING_ID_RE
    appendix_cut = body.find("## Appendix A")
    body_pre_appendix = body[:appendix_cut] if appendix_cut > 0 else body
    present_internal = set(_INTERNAL_FINDING_ID_RE.findall(body_pre_appendix))

    present_report = set(re.findall(
        r"^###\s*(?:\[REPORT-BLOCKED[^\]]*\]\s*)?\[([CHMLI]-\d+)\]",
        body, re.MULTILINE,
    ))

    queue_rows = {
        (r.get("finding id") or "").strip(): r
        for r in parse_verification_queue_rows(scratchpad)
        if (r.get("finding id") or "").strip()
    }

    dropped: list[str] = []
    for fid in sorted(receipts):
        if fid in assigned_internal:
            continue
        if fid in present_internal:
            continue
        dropped.append(fid)

    if not dropped:
        return body

    next_ids: dict[str, int] = {}
    for rid in present_report:
        prefix = rid[0]
        num = int(rid.split("-")[1])
        next_ids[prefix] = max(next_ids.get(prefix, 0), num + 1)
    index_counters = _next_report_id_counters(scratchpad)
    for prefix, max_num in index_counters.items():
        next_ids[prefix] = max(next_ids.get(prefix, 0), max_num + 1)

    unresolved_ids = _collect_judge_unresolved_ids(scratchpad)
    missing_sections: list[str] = []
    recovered_mappings: list[tuple[str, str, str, str]] = []
    for fid in dropped:
        qrow = queue_rows.get(fid, {})
        verify_path = _verify_file_for_id(scratchpad, fid)
        try:
            verify_text = verify_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            verify_text = ""
        sev = _severity_name_from_text(verify_text, qrow)
        prefix = severity_letter_from_name(sev)
        num = next_ids.get(prefix, 1)
        next_ids[prefix] = num + 1
        report_id = f"{prefix}-{num:02d}"
        section = _synth_report_section_from_verify(
            scratchpad, report_id, fid, qrow, fid in unresolved_ids,
        )
        missing_sections.append(section)
        present_report.add(report_id)
        title = _sanitize_client_title(
            (qrow.get("title") or "Verified finding").strip()
        )
        recovered_mappings.append((fid, report_id, title, sev))
        log.info(f"[report_assemble] promotion self-heal: {fid} → [{report_id}]")

    n_patched = _patch_report_index_with_recovered(scratchpad, recovered_mappings)
    if n_patched:
        log.info(
            f"[report_assemble] patched report_index.md with "
            f"{n_patched} recovered mapping(s)"
        )

    insertion = (
        "\n\n".join(missing_sections)
        + "\n\n---\n\n"
    )
    marker = "\n## Priority Remediation Order"
    if marker in body:
        return body.replace(marker, "\n" + insertion + "## Priority Remediation Order", 1)
    return body.rstrip() + "\n\n---\n\n" + insertion


def _finalize_report_tier_section(section: str, id_to_title: dict) -> str:
    """Rewrite generic/broken finding headings from the canonical index title
    and strip internal-status prose. Preserves a trailing verification-status
    tag ([VERIFIED]/[UNVERIFIED]/[CONTESTED]); drops a [REPORT-BLOCKED ...] tag."""
    if not section:
        return section
    head_re = re.compile(
        r"^(#{2,3})\s*(?:\[REPORT-BLOCKED[^\]]*\]\s*)?\[\s*([CHMLI]-\d+)\s*\]\s*(.*?)\s*$"
    )
    status_re = re.compile(
        r"(?i)(\[(?:VERIFIED|UNVERIFIED|CONTESTED|VERIFICATION NOT EXECUTED|REPORT-BLOCKED[^\]]*)\])\s*$"
    )
    out = []
    for ln in section.split("\n"):
        m = head_re.match(ln)
        if not m:
            out.append(ln)
            continue
        level, rid, rest = m.group(1), m.group(2), m.group(3)
        status = ""
        sm = status_re.search(rest)
        title_part = rest
        if sm:
            tag = sm.group(1)
            if not re.match(r"(?i)\[REPORT-BLOCKED", tag):
                status = " " + tag
            title_part = rest[:sm.start()].strip()
        title_clean = _sanitize_client_title(title_part)
        if (not title_clean
                or title_clean.lower() in {"verification", "untitled", "finding", "verified finding"}
                or len(title_clean) < 8):
            title_clean = id_to_title.get(rid) or title_clean or "Verified finding"
        out.append(f"{level} [{rid}] {title_clean}{status}".rstrip())
    return _sanitize_client_body("\n".join(out))


def _assemble_report_python(
    scratchpad: Path, project_root: str
) -> bool:
    """Mechanical AUDIT_REPORT.md assembly. No LLM call.

    Reads tier files + report_index.md from `scratchpad`. Generates
    Executive Summary + Priority Remediation Order from `report_index.md`'s
    Summary counts and Master Finding Index rows. Writes AUDIT_REPORT.md
    at `project_root` and `report_quality.md` in scratchpad.

    Returns True on success. Hard-fails (returns False) if no tier files
    exist — that's a real upstream problem, not something this layer
    should silently paper over.
    """
    idx_path = scratchpad / "report_index.md"
    crit_high_path = scratchpad / "report_critical_high.md"
    medium_path = scratchpad / "report_medium.md"
    low_info_path = scratchpad / "report_low_info.md"
    audit_report_path = Path(project_root) / "AUDIT_REPORT.md"

    def _read(p: Path) -> str:
        try:
            return _llm_norm(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else ""
        except Exception:
            return ""

    idx_text = _read(idx_path)
    if idx_text and not (scratchpad / "report_records.json").exists():
        try:
            _build_sc_body_writer_manifests(scratchpad)
        except Exception as exc:
            log.warning(f"[report_assemble] report_records recovery failed: {exc!r}")
    crit_high_text = _read(crit_high_path)
    medium_text = _read(medium_path)
    low_info_text = _read(low_info_path)
    _normalize_tier_report_blocked_markers(
        scratchpad,
        [
            "report_critical_high.md",
            "report_medium.md",
            "report_medium_a.md",
            "report_medium_b.md",
            "report_low_info.md",
            "report_low_info_a.md",
            "report_low_info_b.md",
        ],
    )
    crit_high_text = _read(crit_high_path)
    medium_text = _read(medium_path)
    low_info_text = _read(low_info_path)

    if not (crit_high_text or medium_text or low_info_text):
        log.error(
            "[report_assemble] no tier files found in scratchpad — "
            "cannot assemble AUDIT_REPORT.md"
        )
        return False

    project_name = Path(project_root).name
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Parse Report Header Info from report_index.md -----------------------
    # The LLM Index Agent writes a richer header block (Language/Version, Build
    # Status, Static Analysis Status, Scope, Auditor).  Extract these so the
    # assembled report includes them on separate lines rather than the minimal
    # fallback.  Keys are matched case-insensitively; values are stripped of
    # leading/trailing markdown bold markers.
    header_info: dict[str, str] = {}
    header_section = _extract_h2_section(idx_text, "Report Header Info")
    for hm in re.finditer(
        r"[-*]\s*\*?\*?([^:*]+?)\*?\*?\s*:\s*(.+)", header_section
    ):
        key = hm.group(1).strip()
        val = re.sub(r"^\*\*|\*\*$", "", hm.group(2).strip()).strip()
        if val:
            header_info[key.lower()] = val
    if "project name" in header_info:
        project_name = header_info["project name"]

    # --- Parse counts from report_index.md ## Summary section ---------------
    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Informational": 0}
    summary_body = _extract_h2_section(idx_text, "Summary")
    for sev_label in counts:
        m = re.search(
            rf"\*{{0,2}}{sev_label}\*{{0,2}}\s*[:\|]\s*(\d+)",
            summary_body, re.IGNORECASE,
        )
        if m:
            counts[sev_label] = int(m.group(1))
    total_count = sum(counts.values())

    # --- Parse Master Finding Index rows for Priority Remediation Order ---
    # Tolerant to either `| C-01 | Title | Severity | ...` (canonical table)
    # or `- C-01: Title (severity)` (bullet form, fallback).
    finding_rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    index_scope = _extract_h2_section(idx_text, "Master Finding Index")
    if not index_scope:
        # Fallback for older/freeform index files: never let excluded rows feed
        # client-facing remediation order.
        index_scope = re.split(
            r"(?im)^##\s+Excluded Findings\b", idx_text, maxsplit=1
        )[0]
    for m in re.finditer(
        r"^\|\s*\[?\*?\*?([CHMLI]-\d+)\*?\*?\]?\s*\|\s*([^|]+?)\s*\|",
        index_scope, re.MULTILINE,
    ):
        rid = m.group(1)
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        finding_rows.append({"id": rid, "title": _sanitize_client_title(m.group(2).strip())})
    # Severity-prefix sort (C, H, M, L, I) for the priority order.
    sev_priority = {"C": 0, "H": 1, "M": 2, "L": 3, "I": 4}
    finding_rows.sort(
        key=lambda r: (
            sev_priority.get(r["id"][0], 9),
            int(re.findall(r"\d+", r["id"])[0]) if re.findall(r"\d+", r["id"]) else 0,
        )
    )
    # Canonical report-ID -> title map used to repair generic/broken tier
    # headings during section finalization.
    id_to_title = {r["id"]: r["title"] for r in finding_rows}

    # --- Generate Executive Summary mechanically -----------------------------
    top_crits = [r for r in finding_rows if r["id"].startswith("C-")][:5]
    if top_crits:
        crit_bullets = "\n".join(
            f"- **{r['id']}** — {r['title']}" for r in top_crits
        )
    else:
        crit_bullets = "_No Critical findings._"

    exec_summary = (
        f"This security audit examined `{project_name}`. The audit "
        f"identified **{total_count} findings**: "
        f"{counts['Critical']} Critical, {counts['High']} High, "
        f"{counts['Medium']} Medium, {counts['Low']} Low, "
        f"{counts['Informational']} Informational.\n\n"
        f"The most severe issues:\n\n{crit_bullets}\n\n"
        f"Findings should be addressed in the **Priority Remediation Order** "
        f"at the end of this report. Critical and High findings warrant "
        f"immediate attention before any further deployment activity."
    )

    # --- Generate Priority Remediation Order ---------------------------------
    sev_urgency = {
        "C": "Immediate", "H": "Before launch",
        "M": "Before launch", "L": "Recommended", "I": "Recommended",
    }
    if finding_rows:
        remediation = "\n".join(
            f"{i}. **{r['id']}** — {r['title']} *({sev_urgency.get(r['id'][0], 'Recommended')})*"
            for i, r in enumerate(finding_rows, 1)
        )
    else:
        remediation = "_No findings to remediate._"

    # --- Summary table -------------------------------------------------------
    summary_table = (
        "| Severity | Count |\n"
        "|----------|-------|\n"
        + "\n".join(f"| {sev} | {n} |" for sev, n in counts.items())
        + f"\n| **Total** | **{total_count}** |"
    )

    # --- Optional sections from report_index.md ------------------------------
    components_block = (
        _extract_h2_section(idx_text, "Components Audited")
        or _synthesize_components_audited(scratchpad)
    )
    appendix_block = _build_client_excluded_appendix_from_records(scratchpad)
    internal_traceability = _build_internal_traceability_from_records(scratchpad)
    if internal_traceability:
        try:
            (scratchpad / "report_traceability_internal.md").write_text(
                internal_traceability + "\n", encoding="utf-8"
            )
        except Exception as exc:
            log.warning(f"[report_assemble] internal traceability write failed: {exc}")

    # --- Split tier files into per-severity sections -------------------------
    def _sections_by_prefix(text: str, prefixes: set[str]) -> str:
        # Ship D (SW14-2/3/4): accept H2 AND H3 report-ID headings, and inner
        # whitespace `[ C-01 ]`. The old `###`-only / no-whitespace form dropped
        # a `## [C-01]` (or `### [ C-01 ]`) finding from assembly while the
        # count gate still counted it -> finding silently vanished.
        headers = list(re.finditer(
            r"(?im)^(?:#{2,3}\s*(?:[^\n]*\[REPORT-BLOCKED[^\]]*\]\s*)?\[\s*([CHMLI])-\d+\s*\][^\n]*|##\s+\S[^\n]*)",
            text or "",
        ))
        parts: list[str] = []
        for i, hm in enumerate(headers):
            end = headers[i + 1].start() if i + 1 < len(headers) else len(text or "")
            if hm.group(1) and hm.group(1).upper() in prefixes:
                parts.append((text or "")[hm.start():end].strip())
        return "\n\n".join(parts).strip()

    crit_section = _sections_by_prefix(crit_high_text, {"C"})
    high_section = _sections_by_prefix(crit_high_text, {"H"})
    low_section = _sections_by_prefix(low_info_text, {"L"})
    info_section = _sections_by_prefix(low_info_text, {"I"})
    # Medium tier file may have multiple H1/H2 headers because
    # `merge_report_medium_shards` naively concatenates shard files
    # (each with its own `# Medium Findings` H1, and shard B sometimes
    # has its own `## Medium Findings` H2). Strip ALL of these so we
    # own the section structure exactly once.
    medium_clean = re.sub(
        r"^#\s+(?:Medium\s+Findings|Medium)[^\n]*\n+", "",
        medium_text.strip(), flags=re.MULTILINE,
    )
    medium_clean = re.sub(
        r"^##\s+Medium\s+Findings[^\n]*\n+", "",
        medium_clean, flags=re.MULTILINE,
    ).strip()

    # Rewrite generic/broken finding headings from the canonical index title
    # and strip internal-status prose leaked into bodies.
    crit_section = _finalize_report_tier_section(crit_section, id_to_title)
    high_section = _finalize_report_tier_section(high_section, id_to_title)
    medium_clean = _finalize_report_tier_section(medium_clean, id_to_title)
    low_section = _finalize_report_tier_section(low_section, id_to_title)
    info_section = _finalize_report_tier_section(info_section, id_to_title)

    # --- Assemble per ~/.claude/rules/report-template.md ---------------------
    auditor = header_info.get("auditor", "Automated Security Analysis (Plamen V2)")
    scope = header_info.get("scope", project_root)
    header_fields = [
        f"**Date**: {header_info.get('date', today)}",
        f"**Auditor**: {auditor}",
        f"**Scope**: {scope}",
    ]
    for extra_key, label in (
        ("language/version", "Language/Version"),
        ("language", "Language"),
        ("build status", "Build Status"),
        ("static analysis status", "Static Analysis Status"),
    ):
        if extra_key in header_info:
            header_fields.append(f"**{label}**: {header_info[extra_key]}")

    parts: list[str] = [
        f"# Security Audit Report — {project_name}",
        "",
        *header_fields,
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        exec_summary,
        "",
        "## Summary",
        "",
        summary_table,
    ]
    if components_block:
        parts.extend(["", "### Components Audited", "", components_block])
    parts.extend(["", "---", ""])
    if crit_section:
        parts.extend(["## Critical Findings", "", crit_section, ""])
    if high_section:
        parts.extend(["## High Findings", "", high_section, ""])
    if crit_section or high_section:
        parts.extend(["---", ""])
    if medium_clean:
        # Medium tier file already has its own `## Medium Findings` heading
        # in most cases; only add one if it's missing.
        if not re.match(r"^##\s+Medium\s+Findings", medium_clean):
            parts.append("## Medium Findings")
            parts.append("")
        parts.extend([medium_clean, "", "---", ""])
    quality_section = _extract_h2_section(low_info_text, "Quality Observations")
    if low_section:
        parts.extend(["## Low Findings", "", low_section, ""])
    if info_section:
        parts.extend(["## Informational Findings", "", info_section, ""])
    if quality_section:
        parts.extend(["## Quality Observations", "", quality_section, ""])
    if low_section or info_section or quality_section:
        parts.extend(["---", ""])
    parts.extend(["## Priority Remediation Order", "", remediation, ""])
    if appendix_block:
        parts.extend(["", "---", "", "## Appendix A: Excluded Findings", ""])
        parts.extend([appendix_block, ""])

    body = "\n".join(parts)
    body = _tag_report_index_unresolved_sections(body, idx_text, scratchpad)
    # Do not synthesize client-facing body sections during assembly. Missing
    # assigned sections, promotion dropouts, or low-evidence verifier outputs
    # are quality failures that must be fixed upstream by the index/body-writer
    # phases. Auto-writing body prose here hides omissions and creates bloated
    # reports on small scopes.
    body = _sanitize_client_body(body)
    body = _sanitize_undefined_report_references(body)

    # --- Mechanical sanitization (canonical-by-construction) -----------------
    # Strip control characters per `~/.claude/rules/phase6-report-prompts.md`
    # quality-gate spec: form-feed, ANSI escapes, null bytes, etc.
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", body)
    # Strip ANSI escape sequences if any leaked through.
    body = re.sub(r"\x1b\[[0-9;]*[mGKH]", "", body)
    # Internal report-blocked markers are useful for tier/body gates, but must
    # never leak into the client-facing report. Keep UNVERIFIED/CONTESTED prose.
    body = re.sub(r"\s*\[REPORT-BLOCKED[^\]]*\]\s*", " ", body, flags=re.IGNORECASE)
    body = re.sub(r"[ \t]{2,}", " ", body)

    # --- Write outputs -------------------------------------------------------
    try:
        audit_report_path.write_text(body, encoding="utf-8")
    except Exception as e:
        log.error(f"[report_assemble] AUDIT_REPORT.md write failed: {e}")
        return False

    finding_section_count = len(
        re.findall(r"^###\s*\[[CHMLI]-\d+\]", body, re.MULTILINE)
    )
    # Count megasection rows (Quality Observations table)
    appendix_start = body.find("## Appendix")
    quality_body = body[:appendix_start] if appendix_start >= 0 else body
    quality_row_count = len(
        re.findall(r"^\|\s*[CHMLI]-\d+\s*\|", quality_body, re.MULTILINE)
    )
    total_findings_in_report = finding_section_count + quality_row_count
    quality = [
        "# Report Quality Check (Python-assembled, v2.4.2)",
        "",
        f"- Source: tier files concatenated mechanically; no LLM round-trip.",
        f"- Body bytes: {len(body):,}",
        f"- Finding sections (### [X-NN]): {finding_section_count}",
        f"- Quality observation rows: {quality_row_count}",
        f"- Total findings in report: {total_findings_in_report}",
        f"- Summary total: {total_count}",
        f"- Section presence: "
        f"crit={'YES' if crit_section else 'NO'} | "
        f"high={'YES' if high_section else 'NO'} | "
        f"medium={'YES' if medium_clean else 'NO'} | "
        f"low={'YES' if low_section else 'NO'} | "
        f"info={'YES' if info_section else 'NO'} | "
        f"quality_obs={'YES' if quality_section else 'NO'}",
        f"- Components Audited block: {'YES' if components_block else 'NO'}",
        f"- Appendix A traceability: {'YES' if appendix_block else 'NO'}",
        f"- Excluded Findings: {'YES' if '### Excluded Findings' in appendix_block else 'NO'}",
    ]
    try:
        (scratchpad / "report_quality.md").write_text(
            "\n".join(quality) + "\n", encoding="utf-8"
        )
    except Exception:
        pass
    log.info(
        f"[report_assemble] python-assembled {audit_report_path.name} "
        f"({len(body):,} bytes, {finding_section_count} finding sections, "
        f"no LLM round-trip)"
    )
    return True


def _report_index_unresolved_report_ids(
    index_text: str,
    scratchpad: Path | None = None,
) -> set[str]:
    """Return report IDs whose report-index Trust Adj. requires UNRESOLVED.

    Prefer the Skeptic-Judge decision files as the source of truth. The report
    index is a routing table, and some rows can carry advisory `PARTIAL(...)`
    trust text for reasons other than a Judge UNRESOLVED/PARTIAL ruling. When
    judge artifacts are available, only tag rows whose internal IDs intersect a
    real Judge UNRESOLVED/PARTIAL decision.
    """
    ids: set[str] = set()
    judge_unresolved: set[str] = set()
    if scratchpad is not None:
        try:
            judge_unresolved = {x.upper() for x in _collect_judge_unresolved_ids(scratchpad)}
        except Exception:
            judge_unresolved = set()
    section = _extract_h2_section(index_text or "", "Master Finding Index")
    if not section:
        return ids
    col_idx: dict[str, int] = {}
    for line in section.splitlines():
        s = line.strip()
        if not s.startswith("|") or _is_separator_row(s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        lower = [c.lower() for c in cells]
        if "report id" in lower or "trust adj." in lower or "trust adj" in lower:
            for i, name in enumerate(lower):
                if "report" in name and "id" in name:
                    col_idx["report_id"] = i
                elif "trust" in name and "adj" in name:
                    col_idx["trust_adj"] = i
                elif "internal" in name and ("hypothesis" in name or "id" in name):
                    col_idx["internal"] = i
            continue
        if not col_idx:
            continue
        rid_i = col_idx.get("report_id", 0)
        trust_i = col_idx.get("trust_adj")
        if trust_i is None or rid_i >= len(cells) or trust_i >= len(cells):
            continue
        rid_m = re.search(r"\b([CHMLI]-\d{1,3})\b", cells[rid_i], re.IGNORECASE)
        if not rid_m:
            continue
        trust = cells[trust_i]
        if re.search(r"\b(?:UNRESOLVED|PARTIAL)\s*\(", trust, re.IGNORECASE):
            internal_i = col_idx.get("internal")
            internal = cells[internal_i] if internal_i is not None and internal_i < len(cells) else ""
            internal_ids = {m.group(1).upper() for m in _INTERNAL_FINDING_ID_RE.finditer(internal)}
            if judge_unresolved and internal_ids and not (internal_ids & judge_unresolved):
                continue
            ids.add(rid_m.group(1).upper())
    return ids


def _tag_report_index_unresolved_sections(
    body: str,
    index_text: str,
    scratchpad: Path | None = None,
) -> str:
    """Add `[UNRESOLVED]` to assigned body headings that require it."""
    unresolved_ids = _report_index_unresolved_report_ids(index_text, scratchpad)
    if not unresolved_ids:
        return body

    def repl(match: re.Match[str]) -> str:
        line = match.group(0)
        rid = match.group(1).upper()
        if rid not in unresolved_ids or re.search(r"\[UNRESOLVED\b", line, re.IGNORECASE):
            return line
        return line.rstrip() + " [UNRESOLVED]"

    pattern = re.compile(
        r"(?im)^###\s*(?:\[REPORT-BLOCKED[^\]]*\]\s*)?\[\s*([CHMLI]-\d{1,3})\s*\][^\n]*$"
    )
    return pattern.sub(repl, body or "")


def _inventory_source_files(scratchpad: Path) -> list[Path]:
    seen: set[str] = set()
    files: list[Path] = []
    for pattern in _INVENTORY_SOURCE_PATTERNS:
        for p in sorted(scratchpad.glob(pattern)):
            if p.name in seen:
                continue
            seen.add(p.name)
            files.append(p)
    return files


def _count_inventory_source_signals(path: Path) -> int:
    try:
        text = _llm_norm(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0
    ids, blocks = _extract_finding_signals(text)
    return max(blocks, len(ids), 1 if text.strip() else 0)


def ensure_inventory_shard_plan(scratchpad: Path, target_per_shard: int = 70,
                                max_shards: int = 3) -> dict[str, list[dict[str, object]]]:
    """Create inventory shard manifests from breadth analysis files."""
    source_files = _inventory_source_files(scratchpad)
    weighted = []
    total = 0
    for p in source_files:
        signals = _count_inventory_source_signals(p)
        weighted.append({"path": p.name, "signals": signals})
        total += signals

    if not weighted:
        num_shards = 1
    else:
        num_shards = max(1, min(max_shards, math.ceil(total / max(target_per_shard, 1))))

    shard_names = [f"inventory_chunk_{c}" for c in ("a", "b", "c")[:num_shards]]
    shard_entries = {name: [] for name in ("inventory_chunk_a", "inventory_chunk_b", "inventory_chunk_c")}
    shard_totals = {name: 0 for name in shard_entries}

    for item in sorted(weighted, key=lambda x: (-int(x["signals"]), str(x["path"]))):
        active = sorted(shard_names, key=lambda name: (shard_totals[name], name))
        chosen = active[0]
        shard_entries[chosen].append(item)
        shard_totals[chosen] += int(item["signals"])

    plan_lines = [
        "# Inventory Shard Plan",
        "",
        f"- Total breadth analysis files: {len(weighted)}",
        f"- Total source finding signals: {total}",
        f"- Target signals per shard: {target_per_shard}",
        f"- Active shard count: {num_shards}",
        "",
    ]
    for shard_name in ("inventory_chunk_a", "inventory_chunk_b", "inventory_chunk_c"):
        manifest = scratchpad / f"{shard_name}.manifest.md"
        rows = shard_entries[shard_name]
        lines = [
            f"# {shard_name} manifest",
            "",
            f"- Output: findings_{shard_name}.md",
            f"- Assigned files: {len(rows)}",
            f"- Estimated signals: {shard_totals[shard_name]}",
            "",
            "| File | Estimated signals |",
            "|------|-------------------|",
        ]
        lines.extend(
            f"| {row['path']} | {row['signals']} |"
            for row in rows
        )
        lines.append("")
        content = "\n".join(lines)
        if not manifest.exists() or manifest.read_text(encoding="utf-8", errors="replace") != content:
            manifest.write_text(content, encoding="utf-8")
        plan_lines.append(
            f"- {shard_name}: {len(rows)} files / {shard_totals[shard_name]} signals"
        )

    plan_text = "\n".join(plan_lines) + "\n"
    plan_path = scratchpad / "inventory_shard_plan.md"
    if not plan_path.exists() or plan_path.read_text(encoding="utf-8", errors="replace") != plan_text:
        plan_path.write_text(plan_text, encoding="utf-8")
    return shard_entries


def write_inventory_chunk_placeholder(scratchpad: Path, phase_name: str, reason: str):
    out_name = f"findings_{phase_name}.md"
    p = scratchpad / out_name
    p.write_text(
        f"# {phase_name}: N/A\n\n"
        f"No analysis files were assigned to this shard.\n\n"
        f"- Reason: {reason}\n"
        f"- Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8",
    )


def _allocate_inventory_ledger_id(
    scratchpad: Path,
    *,
    preferred_id: str,
    owner_phase: str,
    owning_artifact: str,
    title: str,
) -> str:
    """Register an INV id, allocating a fresh one if a stale retry collided."""
    candidate = preferred_id
    for _ in range(1000):
        try:
            result = id_ledger_register(
                scratchpad,
                finding_id=candidate,
                owner_phase=owner_phase,
                owner_attempt=1,
                owning_artifact=owning_artifact,
                title=title,
            )
        except Exception as e:
            log.debug(f"[{owner_phase}] ledger register skipped for {candidate}: {e}")
            return candidate
        if result.get("status") != "COLLISION":
            return candidate
        try:
            nums = [
                int(m.group(1))
                for rec in id_ledger_all_records(scratchpad)
                for m in [re.match(r"^INV-(\d+)$", str(rec.get("id", "")).upper())]
                if m
            ]
            candidate = f"INV-{(max(nums) if nums else 0) + 1:03d}"
        except Exception:
            m = re.search(r"(\d+)$", candidate)
            n = int(m.group(1)) + 1 if m else 1
            candidate = f"INV-{n:03d}"
    return candidate


def _write_mechanical_inventory_from_chunks(scratchpad: Path) -> tuple[int, int]:
    """Write final findings_inventory.md from completed inventory chunks.

    L1 runs can produce hundreds of chunk findings. A fourth LLM merge pass has
    repeatedly timed out or short-circuited on a partial file. This deterministic
    merge preserves every parsed chunk source ID and performs only safe
    root-cause deduplication by normalized location + root-cause/title.
    """
    chunk_paths = sorted(scratchpad.glob("findings_inventory_chunk_*.md"))
    entries: list[dict[str, object]] = []
    for p in chunk_paths:
        entries.extend(_parse_inventory_chunk(p))
    merged = _merge_inventory_entries(entries)
    if not merged:
        return 0, 0

    counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Informational": 0}
    for item in merged:
        sev = _severity_name_from_text("", {"severity": str(item.get("severity", ""))})
        counts[sev] = counts.get(sev, 0) + 1

    lines = [
        "# Finding Inventory",
        "",
        "Generated mechanically from `findings_inventory_chunk_*.md` to avoid",
        "LLM merge truncation. Source IDs are preserved for parity checks.",
        "",
        "## Summary",
        "",
        "| Severity | Count |",
        "|----------|-------|",
    ]
    for sev in ("Critical", "High", "Medium", "Low", "Informational"):
        lines.append(f"| {sev} | {counts.get(sev, 0)} |")
    lines.extend([
        f"| Total | {len(merged)} |",
        "",
        "## Findings",
        "",
    ])

    for i, item in enumerate(merged, start=1):
        sev = _severity_name_from_text("", {"severity": str(item.get("severity", ""))})
        title = _strip_md(str(item.get("title", ""))) or "Untitled finding"
        loc = _norm_loc(str(item.get("location", ""))) or "UNKNOWN"
        tag = (
            _extract_first_tag(str(item.get("preferred_tag", "")))
            or _strip_md(str(item.get("preferred_tag", "")))
            or EVIDENCE_TAG_DEFAULT
        )
        source_ids = []
        for sid in item.get("source_ids", []) or []:
            norm = _normalize_finding_id(str(sid)) or _strip_md(str(sid))
            if norm and norm not in source_ids:
                source_ids.append(norm)
        if item.get("local_id"):
            local = _normalize_finding_id(str(item.get("local_id"))) or _strip_md(str(item.get("local_id")))
            if local and local not in source_ids:
                source_ids.append(local)
        source_text = ", ".join(source_ids) if source_ids else "SOURCE_UNVERIFIED"
        root = _strip_md(str(item.get("root_cause", ""))) or title
        desc = _strip_md(str(item.get("description", ""))) or root
        impact = _strip_md(str(item.get("impact", ""))) or "Impact requires verifier confirmation."
        verdict = _strip_md(str(item.get("verdict", ""))) or "NEEDS_VERIFICATION"
        optional_lines: list[str] = []
        for field in _OPTIONAL_FINDING_METADATA_FIELDS:
            val = _strip_md(str(item.get(field, ""))).strip()
            if not val:
                continue
            label = _OPTIONAL_FINDING_METADATA_LABELS[field][0]
            optional_lines.append(f"**{label}**: {val}")
        inv_id = _allocate_inventory_ledger_id(
            scratchpad,
            preferred_id=f"INV-{i:03d}",
            owner_phase="inventory",
            owning_artifact="findings_inventory.md",
            title=title,
        )
        # v2.0.6 (P2.2): register the INV allocation in the ID ledger.
        # Inventory consolidation is mechanical / driver-owned, so true
        # collisions cannot happen here — but the registration makes
        # the IDs visible to the consumer backstop gate (P2.5) and to
        # downstream phases that allocate from the same namespace.
        try:
            from plamen_parsers import id_ledger_register
            id_ledger_register(
                scratchpad,
                finding_id=inv_id,
                owner_phase="inventory",
                owner_attempt=1,
                owning_artifact="findings_inventory.md",
                title=title,
            )
        except Exception as e:
            log.debug(f"[inventory] ledger register skipped for {inv_id}: {e}")
        lines.extend([
            f"### Finding [{inv_id}]: {title}",
            f"**Severity**: {sev}",
            f"**Location**: {loc}",
            f"**Preferred Tag**: {tag}",
            f"**Source IDs**: {source_text}",
            f"**Verdict**: {verdict}",
            f"**Root Cause**: {root}",
            f"**Description**: {desc}",
            f"**Impact**: {impact}",
            *optional_lines,
            "",
        ])

    (scratchpad / "findings_inventory.md").write_text("\n".join(lines), encoding="utf-8")
    _write_finding_records_from_inventory(scratchpad)
    receipt = [
        "# Mechanical Inventory Merge Receipt",
        "",
        f"Chunk files: {len(chunk_paths)}",
        f"Parsed chunk findings: {len(entries)}",
        f"Merged inventory findings: {len(merged)}",
        "",
    ]
    (scratchpad / "inventory_merge_receipt.md").write_text("\n".join(receipt), encoding="utf-8")
    return len(entries), len(merged)


# v2.x: Niche finding ID prefixes recognized by promote_niche_to_inventory.
# Match the headings produced by niche agents in niche_*_findings.md files.
# Standard prefixes: NSC (semantic consistency), NDA (dimensional analysis),
# NEC (event completeness), NSGI (semantic gap investigator), and any future
# NXX-NN form a niche agent may emit. Driver call site: after depth phase
# completion, before sc_semantic_dedup runs.
_NICHE_FINDING_HEADING_RE = re.compile(
    r"^#{2,4}\s*Finding\s*\[\s*(?P<id>[A-Z]{2,6}-\d+)\s*\]\s*:\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)

# Heuristics for filtering false-positive "finding-shaped" sections produced
# by methodology / processing sections inside niche files (e.g., the
# "## Processing Protocol Execution" preamble that lists checks but is not
# itself a finding). Real findings have ALL of these fields within ~50 lines
# of the heading: Severity, Location, Description, Impact.
_NICHE_REQUIRED_FIELDS = ("Severity", "Location", "Description")


def _parse_niche_findings(scratchpad: Path) -> list[dict[str, str]]:
    """Parse all niche_*_findings.md files into structured finding entries.

    Each niche agent writes findings in the standard finding-output-format.md
    template (## Finding [NSC-N]: Title + **Severity** / **Location** / etc).
    This parser is conservative: it requires the standard required fields to
    be present near the heading, rejecting methodology preambles and tables.

    Returns a list of dicts with keys: source_file, source_id, title, severity,
    location, preferred_tag, description, impact, evidence, raw_block.
    """
    entries: list[dict[str, str]] = []
    for niche_path in sorted(scratchpad.glob("niche_*_findings.md")):
        try:
            text = niche_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        matches = list(_NICHE_FINDING_HEADING_RE.finditer(text))
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[start:end]
            # Require all key fields present to filter out non-finding sections
            if not all(f"**{field}**" in block for field in _NICHE_REQUIRED_FIELDS):
                continue
            fid = m.group("id").strip()
            title = m.group("title").strip()
            sev_match = re.search(
                r"\*\*Severity\*\*\s*:\s*([A-Za-z]+)", block, re.IGNORECASE
            )
            loc_match = re.search(
                r"\*\*Location\*\*\s*:\s*(.+?)(?=\n\*\*|\n\n|$)",
                block, re.IGNORECASE | re.DOTALL,
            )
            tag_match = re.search(
                r"\*\*Preferred\s*Tag\*\*\s*:\s*(\[[A-Z\-]+\])",
                block, re.IGNORECASE,
            )
            desc_match = re.search(
                r"\*\*Description\*\*\s*:\s*(.+?)(?=\n\*\*[A-Z]|\n##|\Z)",
                block, re.IGNORECASE | re.DOTALL,
            )
            impact_match = re.search(
                r"\*\*Impact\*\*\s*:\s*(.+?)(?=\n\*\*[A-Z]|\n##|\Z)",
                block, re.IGNORECASE | re.DOTALL,
            )
            entries.append({
                "source_file": niche_path.name,
                "source_id": fid,
                "title": title,
                "severity": (sev_match.group(1).strip() if sev_match else "Medium"),
                "location": (
                    _norm_loc(loc_match.group(1).strip()) if loc_match else "UNKNOWN"
                ),
                "preferred_tag": (
                    tag_match.group(1).strip() if tag_match else "[CODE-TRACE]"
                ),
                "description": (
                    _strip_md(desc_match.group(1).strip())[:1500]
                    if desc_match else title
                ),
                "impact": (
                    _strip_md(impact_match.group(1).strip())[:800]
                    if impact_match else "Impact requires verifier confirmation."
                ),
                "raw_block": block,
            })
    return entries


def promote_niche_to_inventory(scratchpad: Path) -> tuple[int, int]:
    """Promote niche agent findings into findings_inventory.md as INV-* entries.

    Niche agents (semantic consistency, dimensional analysis, event
    completeness, semantic gap investigator) write to niche_*_findings.md
    during depth phase iteration 1. These files reach chain analysis via
    chain_summaries_compact.md but do NOT enter findings_inventory.md, which
    is the source of truth for verification_queue.md. Result: niche findings
    are never verified or reported.

    This function appends niche findings to findings_inventory.md, continuing
    the INV-NNN numbering. It is idempotent: a receipt file tracks already-
    promoted niche source IDs so a re-run after retry does not duplicate.

    Call site: post-depth phase completion in plamen_driver.py, after
    _canonicalize_depth_iter_filenames but before sc_semantic_dedup runs.

    Returns (parsed_count, appended_count).
    """
    inventory_path = scratchpad / "findings_inventory.md"
    if not inventory_path.exists():
        return 0, 0
    receipt_path = scratchpad / "niche_promotion_receipt.md"
    already_promoted: set[str] = set()
    if receipt_path.exists():
        try:
            for line in receipt_path.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.search(r"\b([A-Z]{2,6}-\d+)\s*->\s*INV-\d+", line)
                if m:
                    already_promoted.add(m.group(1))
        except Exception:
            pass

    parsed = _parse_niche_findings(scratchpad)
    new_entries = [e for e in parsed if e["source_id"] not in already_promoted]
    if not new_entries:
        return len(parsed), 0

    # Find highest existing INV-NNN to continue numbering
    try:
        inv_text = inventory_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return len(parsed), 0
    max_inv = 0
    for m in re.finditer(r"\bINV-(\d+)\b", inv_text):
        try:
            max_inv = max(max_inv, int(m.group(1)))
        except ValueError:
            continue

    # Build appended entries
    appended_lines: list[str] = []
    mapping_lines: list[str] = []
    for idx, entry in enumerate(new_entries, start=1):
        inv_n = max_inv + idx
        title = entry["title"] or "Untitled niche finding"
        inv_id = _allocate_inventory_ledger_id(
            scratchpad,
            preferred_id=f"INV-{inv_n:03d}",
            owner_phase="niche_promotion",
            owning_artifact="findings_inventory.md",
            title=title,
        )
        # v2.0.6 (P2.2): register the niche-promoted INV in the ledger.
        try:
            from plamen_parsers import id_ledger_register
            id_ledger_register(
                scratchpad,
                finding_id=inv_id,
                owner_phase="niche_promotion",
                owner_attempt=1,
                owning_artifact="findings_inventory.md",
                title=title,
            )
        except Exception as e:
            log.debug(f"[niche] ledger register skipped for {inv_id}: {e}")
        sev = _severity_name_from_text("", {"severity": entry["severity"]})
        loc = entry["location"]
        tag = entry["preferred_tag"]
        desc = entry["description"] or title
        impact = entry["impact"]
        appended_lines.extend([
            f"### Finding [{inv_id}]: {title}",
            f"**Severity**: {sev}",
            f"**Location**: {loc}",
            f"**Preferred Tag**: {tag}",
            f"**Source IDs**: {entry['source_id']} (niche-promoted from {entry['source_file']})",
            f"**Verdict**: NEEDS_VERIFICATION",
            f"**Root Cause**: {title}",
            f"**Description**: {desc}",
            f"**Impact**: {impact}",
            "",
        ])
        mapping_lines.append(
            f"- {entry['source_id']} -> {inv_id} "
            f"({entry['source_file']}: {title[:80]})"
        )

    # Append to inventory (preserve trailing newline behavior)
    section_header = (
        "\n\n## Niche-Promoted Findings\n\n"
        "Findings discovered by niche agents (semantic consistency, "
        "dimensional analysis, event completeness, semantic gap investigator) "
        "during depth phase iteration 1. Promoted post-depth so they reach "
        "the verification queue and chain analysis with the same first-class "
        "status as breadth/depth findings.\n\n"
    )
    existing = inv_text.rstrip()
    new_text = existing + section_header + "\n".join(appended_lines) + "\n"
    inventory_path.write_text(new_text, encoding="utf-8")

    # Receipt for idempotency
    receipt_lines = [
        "# Niche Promotion Receipt",
        "",
        f"Parsed niche findings: {len(parsed)}",
        f"Already promoted (prior attempt): {len(already_promoted)}",
        f"Newly appended this run: {len(new_entries)}",
        "",
        "## Source-to-Inventory ID Mapping",
        "",
    ]
    receipt_lines.extend(mapping_lines)
    receipt_lines.append("")
    # Preserve prior mappings across retries
    if receipt_path.exists():
        try:
            prior_text = receipt_path.read_text(encoding="utf-8", errors="replace")
            # Append rather than overwrite to preserve prior-run history
            receipt_path.write_text(
                prior_text.rstrip() + "\n\n## Re-Run\n\n" + "\n".join(receipt_lines),
                encoding="utf-8",
            )
        except Exception:
            receipt_path.write_text("\n".join(receipt_lines), encoding="utf-8")
    else:
        receipt_path.write_text("\n".join(receipt_lines), encoding="utf-8")

    # Re-write finding records sidecar so downstream consumers see niche entries
    try:
        _write_finding_records_from_inventory(scratchpad)
    except Exception:
        pass
    return len(parsed), len(new_entries)


_ATTENTION_REPAIR_MAX_ITEMS = 32


_SECURITY_OBLIGATION_RULES: tuple[dict[str, object], ...] = (
    {
        "class": "asset_binding",
        "pattern": r"\b(?:asset|token|coin|amount|balance|transfer|swap|route|path|toToken|fromToken|target)\b",
        "question": "Are asset-in, asset-out, recipient, and amount fields bound to trusted execution context before value moves?",
    },
    {
        "class": "swap_execution",
        "pattern": r"\b(?:swap|router|pool|pair|quote|min(?:imum)?(?:Amount)?Out|slippage|path|reserve)\b",
        "question": "Can swap execution, pool selection, min-out checks, or approval/execution amounts diverge from the value path?",
    },
    {
        "class": "refund_revert",
        "pattern": r"\b(?:refund|revert|rollback|onRevert|failed|return(?:ed)?\s+funds?|fallback)\b",
        "question": "Is the refund recipient derived from authenticated source context and the original asset custody path?",
    },
    {
        "class": "cross_domain_message",
        "pattern": r"\b(?:bridge|cross[-_ ]?chain|gateway|message|payload|decode|encode|source|sender|chainid|xcall|cpi)\b",
        "question": "Are decoded message fields, source chain, and source sender authenticated before privileged state/value effects?",
    },
    {
        "class": "native_wrapped_asset",
        "pattern": r"\b(?:native|wrapped|wrap|unwrap|deposit|withdraw|msg\.value|payable|sentinel|WETH|W[ A-Z0-9_]*|gas token)\b",
        "question": "Are native-asset and token-contract branches separated so approve/transfer/wrap/unwrap/accounting cannot mismatch?",
    },
    {
        "class": "external_call_surface",
        "pattern": r"\b(?:call|delegatecall|staticcall|callback|hook|receiver|external\s+call|arbitrary\s+target|target\s+address)\b",
        "question": "Can untrusted call targets, callbacks, hooks, or reentrant external effects violate state or value assumptions?",
    },
    {
        "class": "privileged_exit",
        "pattern": r"\b(?:admin|owner|governance|role|permission|onlyOwner|withdraw|sweep|rescue|emergency|upgrade)\b",
        "question": "Are privileged exits, rescue paths, and upgrades access-controlled and constrained to intended assets/recipients?",
    },
    {
        "class": "encoding_schema",
        "pattern": r"\b(?:abi\.decode|decode|deserialize|serialize|bytes\d*|address\(|cast|length|schema|struct|layout|endianness)\b",
        "question": "Do encoded/decoded schemas preserve field widths, ordering, permissions, and address formats across boundaries?",
    },
)


_FACET_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("asset-binding-mismatch", r"\b(?:mismatch|not\s+match|does\s+not\s+match|diverge|different)\b.*\b(?:asset|token|coin|amount|target|recipient|params|decoded|input|output)\b|\b(?:asset|token|coin|amount|target|recipient|params|decoded|input|output)\b.*\b(?:mismatch|not\s+match|does\s+not\s+match|diverge|different)\b"),
    ("swap-skip-or-divergence", r"\b(?:skip|empty|bypass|not\s+execute|without\s+swap|swapData|swap)\b"),
    ("refund-provenance", r"\b(?:refund|revert|rollback|failed)\b.*\b(?:recipient|sender|source|address|provenance)\b"),
    ("source-authentication", r"\b(?:source\s+sender|sender|origin|source\s+chain|chainid|authenticat|verify)\b"),
    ("native-token-branch", r"\b(?:native|wrapped|wrap|unwrap|msg\.value|payable|sentinel|WETH|W[A-Z0-9_]+)\b"),
    ("approval-execution-amount", r"\b(?:approve|allowance)\b.*\b(?:amount|fee|deduct|full|original|mismatch)\b|\b(?:fee|deduct)\b.*\b(?:approve|swap|amount)\b"),
    ("slippage-minout", r"\b(?:slippage|min(?:imum)?(?:Amount)?Out|minReturn|amountOutMin|sandwich|MEV)\b"),
    ("pool-or-route-trust", r"\b(?:pool|pair|router|route|reserve|oracle|quote)\b.*\b(?:exist|select|trust|manipulat|check)\b"),
    ("access-control", r"\b(?:public|external|onlyOwner|role|access\s+control|permission|admin|owner|withdraw|sweep)\b"),
    ("encoding-width-or-layout", r"\b(?:bytes\d*|length|cast|decode|encode|deserialize|layout|struct|writable|signer|permission)\b"),
)


def _read_security_signal_text(scratchpad: Path) -> str:
    names = (
        "recon_summary.md", "design_context.md", "attack_surface.md",
        "detected_patterns.md", "template_recommendations.md",
        "contract_inventory.md", "external_interfaces.md",
        "integration_points.md", "function_summary.md", "caller_map.md",
        "callee_map.md", "state_write_map.md", "opengrep_findings.md",
        "findings_inventory.md",
    )
    chunks: list[str] = []
    for name in names:
        p = scratchpad / name
        if not p.exists() or not p.is_file():
            continue
        try:
            chunks.append(p.read_text(encoding="utf-8", errors="replace")[:120_000])
        except Exception:
            continue
    return "\n".join(chunks)


def _write_security_obligations(scratchpad: Path, mode: str = "core") -> int:
    """Write generic feature-triggered audit obligations.

    These are target-shape obligations, not benchmark hints. They are derived
    from recon/graph/static artifacts and ask broad methodology questions that
    downstream agents must answer or carry forward.
    """
    signal_text = _llm_norm(_read_security_signal_text(scratchpad))
    out = scratchpad / "security_obligations.md"
    if not signal_text.strip():
        out.write_text(
            "# Security Obligations\n\n"
            "**Status**: SKIPPED - no recon or graph signal artifacts available.\n",
            encoding="utf-8",
        )
        return 0

    obligations: list[dict[str, str]] = []
    for rule in _SECURITY_OBLIGATION_RULES:
        pattern = str(rule["pattern"])
        matches = list(re.finditer(pattern, signal_text, re.IGNORECASE))
        if not matches:
            continue
        snippets: list[str] = []
        for m in matches[:3]:
            start = max(0, m.start() - 80)
            end = min(len(signal_text), m.end() + 80)
            snippet = re.sub(r"\s+", " ", signal_text[start:end]).strip()
            if snippet:
                snippets.append(snippet.replace("|", "/"))
        obligations.append({
            "id": f"SO-{len(obligations) + 1:03d}",
            "class": str(rule["class"]),
            "question": str(rule["question"]),
            "signals": " ; ".join(snippets[:3]) or "(signal present)",
        })

    lines = [
        "# Security Obligations",
        "",
        "Generated mechanically from recon, graph, inventory, and static-analysis "
        "signals. These are generic vulnerability-class obligations. They are "
        "not expected findings and must not be treated as protocol-specific "
        "answers.",
        "",
        f"**Mode**: {mode}",
        f"**Count**: {len(obligations)}",
        "",
        "| Obligation ID | Class | Audit Question | Trigger Signals |",
        "|---------------|-------|----------------|-----------------|",
    ]
    if obligations:
        for item in obligations:
            lines.append(
                f"| {item['id']} | {item['class']} | {item['question']} | {item['signals']} |"
            )
    else:
        lines.append("| n/a | none | No generic feature obligations triggered. | n/a |")
    lines.extend([
        "",
        "## Receipt Contract",
        "",
        "When a later phase directly evaluates an obligation, it may emit:",
        "",
        "`[OBLIG:security_obligations.md:<SO-ID>] STATUS:R|D|C KEY:<summary> -> <finding_id|reason|phase>`",
        "",
        "`R` means reported, `D` means dismissed with evidence, and `C` means "
        "carried to a named later phase. Missing receipts are telemetry unless "
        "a dedicated phase explicitly consumes this file.",
    ])
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(obligations)


def _parse_security_obligation_items(scratchpad: Path) -> list[dict[str, str]]:
    path = scratchpad / "security_obligations.md"
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    items: list[dict[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|") or _is_separator_row(s):
            continue
        cells = [c.strip().strip("`") for c in s.strip("|").split("|")]
        if len(cells) < 4 or cells[0].lower().startswith("obligation"):
            continue
        if not re.fullmatch(r"SO-\d{3}", cells[0], re.IGNORECASE):
            continue
        items.append({
            "id": cells[0].upper(),
            "class": cells[1],
            "question": cells[2],
            "signals": cells[3],
        })
    return items


_ASSET_BINDING_SIGNAL_FILES: tuple[str, ...] = (
    "design_context.md", "attack_surface.md", "detected_patterns.md",
    "function_list.md", "contract_inventory.md", "template_recommendations.md",
    "analysis_token_flow.md", "analysis_external_dependencies.md",
    "analysis_core_state.md", "analysis_access_control.md",
    "depth_token_flow_findings.md", "depth_external_findings.md",
    "depth_state_trace_findings.md", "depth_edge_case_findings.md",
    "findings_inventory.md", "hypotheses.md", "chain_hypotheses.md",
    "verification_queue.md",
)

_ASSET_BINDING_COVERAGE_FILES: tuple[str, ...] = (
    "findings_inventory.md", "hypotheses.md", "chain_hypotheses.md",
    "verification_queue.md", "attention_repair_summary.md",
    "attention_repair_findings.md",
)

_BINDING_FIELD_CLASS: dict[str, str] = {
    "asset": "token",
    "inputasset": "token",
    "outputasset": "token",
    "token": "token",
    "inputtoken": "token",
    "outputtoken": "token",
    "fromtoken": "token",
    "totoken": "token",
    "targetzrc20": "token",
    "zrc20": "token",
    "gaszrc20": "token",
    "collateral": "token",
    "debttoken": "token",
    "amount": "amount",
    "fromtokenamount": "amount",
    "outputamount": "amount",
    "targetamount": "amount",
    "minamountout": "amount",
    "minreturnamount": "amount",
    "expretrunamount": "amount",
    "expreturnamount": "amount",
    "msg.value": "amount",
    "fee": "amount",
    "receiver": "recipient",
    "recipient": "recipient",
    "sender": "recipient",
    "walletaddress": "recipient",
    "assetto": "recipient",
    "to": "recipient",
    "refundrecipient": "recipient",
    "sourcesender": "provenance",
    "sourcechain": "provenance",
    "chainid": "provenance",
    "context.sender": "provenance",
}

_BINDING_PAIR_TEMPLATES: tuple[tuple[str, str, str, str], ...] = (
    ("toToken", "targetZRC20", "token", "swap output token must match withdrawal/refund asset"),
    ("fromToken", "zrc20", "token", "swap input token must match bridged or deposited asset"),
    ("asset", "targetZRC20", "token", "gateway asset must match decoded withdrawal target"),
    ("outputToken", "targetZRC20", "token", "output token must match withdrawal target"),
    ("fromTokenAmount", "amount", "amount", "swap input amount must match actual held amount"),
    ("msg.value", "amount", "amount", "native value must match declared bridge/swap amount"),
    ("outputAmount", "targetAmount", "amount", "post-swap amount must match amount approved/withdrawn"),
    ("minReturnAmount", "outputAmount", "amount", "minimum output/slippage check must bind to actual output"),
    ("assetTo", "receiver", "recipient", "router output recipient must match intended receiver"),
    ("walletAddress", "receiver", "recipient", "refund wallet must map to intended receiver"),
    ("sender", "receiver", "recipient", "source sender must not be confused with refund receiver"),
    ("sourceSender", "context.sender", "provenance", "message sender must be authenticated to gateway context"),
)


def _canonical_binding_base(raw: str) -> str:
    s = str(raw or "").strip().strip("`")
    if not s:
        return ""
    if s == "msg.value":
        return s
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s


def _binding_class(raw: str) -> str:
    base = _canonical_binding_base(raw)
    key = base.lower()
    if raw == "context.sender":
        key = "context.sender"
    return _BINDING_FIELD_CLASS.get(key, "")


def _read_asset_binding_signal_text(scratchpad: Path) -> str:
    chunks: list[str] = []
    for name in _ASSET_BINDING_SIGNAL_FILES:
        p = scratchpad / name
        if not p.exists() or not p.is_file():
            continue
        try:
            chunks.append(f"\n\n# {name}\n")
            chunks.append(p.read_text(encoding="utf-8", errors="replace")[:160_000])
        except Exception:
            continue
    return _llm_norm("\n".join(chunks))


def _read_asset_binding_coverage_text(scratchpad: Path) -> str:
    chunks: list[str] = []
    for name in _ASSET_BINDING_COVERAGE_FILES:
        p = scratchpad / name
        if not p.exists() or not p.is_file():
            continue
        try:
            chunks.append(f"\n\n# {name}\n")
            chunks.append(p.read_text(encoding="utf-8", errors="replace")[:180_000])
        except Exception:
            continue
    return _llm_norm("\n".join(chunks))


def _extract_binding_fields(text: str) -> dict[str, dict[str, object]]:
    """Extract value-flow fields without assuming a protocol-specific schema."""
    fields: dict[str, dict[str, object]] = {}

    def add(name: str, source: str) -> None:
        display = name.strip()
        base = _canonical_binding_base(display)
        cls = _binding_class(display)
        if not base or not cls:
            return
        key = base.lower()
        rec = fields.setdefault(key, {
            "base": base,
            "class": cls,
            "forms": set(),
            "sources": set(),
        })
        rec["forms"].add(display)  # type: ignore[index,union-attr]
        rec["sources"].add(source)  # type: ignore[index,union-attr]

    for m in re.finditer(
        r"\b(params|decoded|context|message|payload|data|refundInfo|revertInfo)"
        r"\.([A-Za-z_][A-Za-z0-9_]*)\b",
        text,
    ):
        add(f"{m.group(1)}.{m.group(2)}", "dotted")
    if re.search(r"\bmsg\.value\b", text):
        add("msg.value", "native")
    bare_names = (
        "asset", "token", "fromToken", "toToken", "targetZRC20", "zrc20",
        "gasZRC20", "outputToken", "inputToken", "amount", "fromTokenAmount",
        "outputAmount", "targetAmount", "minAmountOut", "minReturnAmount",
        "fee", "receiver", "recipient", "sender", "walletAddress", "assetTo",
        "sourceSender", "sourceChain", "chainId",
    )
    for name in bare_names:
        if re.search(rf"\b{re.escape(name)}\b", text):
            add(name, "bare")

    normalized: dict[str, dict[str, object]] = {}
    for key, rec in fields.items():
        normalized[key] = {
            "base": rec["base"],
            "class": rec["class"],
            "forms": sorted(rec["forms"]),  # type: ignore[arg-type]
            "sources": sorted(rec["sources"]),  # type: ignore[arg-type]
        }
    return normalized


def _binding_domain_flags(text: str) -> list[str]:
    flags: list[str] = []
    if re.search(r"\b(?:bridge|cross[-_\s]?chain|gateway|onCall|onRevert|onAbort)\b", text, re.I):
        flags.append("cross-chain")
    if re.search(r"\b(?:swap|router|mixSwap|amountOut|minReturn|slippage|pool|pair)\b", text, re.I):
        flags.append("swap-router")
    if re.search(r"\b(?:refund|revertMessage|claimRefund|refundInfo)\b", text, re.I):
        flags.append("refund")
    if re.search(r"\b(?:native|wrapped|msg\.value|WETH|WZETA|sentinel)\b", text, re.I):
        flags.append("native-wrapped")
    if re.search(r"\b(?:vault|share|deposit|withdraw|redeem|asset)\b", text, re.I):
        flags.append("asset-accounting")
    if re.search(r"\b(?:borrow|repay|liquidat|collateral|debt)\b", text, re.I):
        flags.append("lending")
    return sorted(set(flags))


_BINDING_PAIR_RELATION_RE = re.compile(
    r"(?:"
    r"==|!=|=|<->|->|"
    r"\b(?:mismatch(?:es|ed)?|diverg(?:e|es|ed|ence)|"
    r"not\s+match(?:es|ed)?|does\s+not\s+match|"
    r"not\s+(?:validated|bound|checked|compared)|"
    r"missing\s+(?:validation|binding|check)|"
    r"validated\s+against|checked\s+against|compared\s+against|"
    r"bound\s+to|binds?\s+to|matches?|equals?|same\s+as|"
    r"consistent\s+with|consistency|must\s+equal|should\s+equal|"
    r"source\s+of\s+truth|derived\s+from|unreachable|impossible|"
    r"mutually\s+exclusive)\b"
    r")",
    re.IGNORECASE,
)


def _binding_term_forms(raw: str) -> set[str]:
    value = str(raw or "").strip().strip("`")
    base = _canonical_binding_base(value)
    return {term for term in (value, base) if term}


def _binding_claim_units(blob: str) -> list[str]:
    """Split text into local claim units so distant mentions do not bind."""
    units: list[str] = []
    for line in str(blob or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if "|" in s:
            units.append(s[:1200])
            continue
        parts = re.split(r"(?<=[.;:!?])\s+", s)
        for part in parts:
            part = part.strip()
            if part:
                units.append(part[:1200])
    return units


def _binding_pair_claims_relationship(blob: str, a: str, b: str) -> bool:
    """Require both exact fields/bases and a relation in the same local claim."""
    terms_a = {t.lower() for t in _binding_term_forms(a)}
    terms_b = {t.lower() for t in _binding_term_forms(b)}
    if not terms_a or not terms_b:
        return False
    for unit in _binding_claim_units(blob):
        low = unit.lower()
        if not _BINDING_PAIR_RELATION_RE.search(unit):
            continue
        if any(t in low for t in terms_a) and any(t in low for t in terms_b):
            return True
    return False


def _active_binding_pair_covered(coverage_text: str, a: str, b: str) -> bool:
    """Return true when an active candidate/report explicitly binds both fields."""
    if not coverage_text:
        return False

    # Split into finding-like chunks first so unrelated global mentions do not
    # count as coverage. Fall back to a short-window search for JSON/queue rows.
    chunks = re.split(r"(?im)^#{2,3}\s+Finding\s+\[[^\]\n]+\]", coverage_text)
    for chunk in chunks:
        if _binding_pair_claims_relationship(chunk[:6000], a, b):
            return True
    for line in coverage_text.splitlines():
        if "|" in line or line.lstrip().startswith(("-", "*")):
            if _binding_pair_claims_relationship(line, a, b):
                return True
    return False


def _representative_binding_form(fields: dict[str, dict[str, object]], base: str) -> str:
    rec = fields.get(base.lower()) or {}
    forms = [str(x) for x in rec.get("forms", [])]
    dotted = [f for f in forms if "." in f]
    return (dotted or forms or [base])[0]


def _build_asset_binding_rows(scratchpad: Path) -> tuple[list[dict[str, object]], list[str]]:
    signal_text = _read_asset_binding_signal_text(scratchpad)
    if not signal_text.strip():
        return [], []
    fields = _extract_binding_fields(signal_text)
    domains = _binding_domain_flags(signal_text)
    coverage_text = _read_asset_binding_coverage_text(scratchpad)
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for a_base, b_base, cls, rationale in _BINDING_PAIR_TEMPLATES:
        if a_base.lower() not in fields or b_base.lower() not in fields:
            continue
        # Keep domain packs audit-shape aware. Amount/token/recipient pairs are
        # only useful when the protocol has a value-moving surface.
        if cls in {"token", "amount", "recipient"} and not (
            {"cross-chain", "swap-router", "refund", "asset-accounting", "lending"} & set(domains)
        ):
            continue
        a_form = _representative_binding_form(fields, a_base)
        b_form = _representative_binding_form(fields, b_base)
        key = tuple(sorted((a_form.lower(), b_form.lower())))
        if key in seen:
            continue
        seen.add(key)
        covered = _active_binding_pair_covered(coverage_text, a_form, b_form)
        gap_id = f"AB-{len(rows) + 1:03d}"
        rows.append({
            "id": gap_id,
            "class": cls,
            "field_a": a_form,
            "field_b": b_form,
            "status": "covered" if covered else "gap",
            "rationale": rationale,
            "domains": domains,
            "question": (
                f"Is `{a_form}` explicitly bound to `{b_form}` before value "
                "moves, or is a mismatch reported as a candidate finding?"
            ),
        })
    return rows, domains


def _write_asset_binding_matrix(scratchpad: Path, mode: str = "core") -> tuple[int, int]:
    """Write a deterministic value-field binding matrix.

    This is a generic discovery backstop. It does not assert findings and does
    not block a phase. In Thorough mode, gap rows can be consumed by attention
    repair as bounded questions.
    """
    rows, domains = _build_asset_binding_rows(scratchpad)
    payload = {
        "schema_version": "plamen.asset_binding_matrix.v1",
        "mode": mode,
        "domains": domains,
        "row_count": len(rows),
        "gap_count": sum(1 for r in rows if r.get("status") == "gap"),
        "rows": rows,
    }
    (scratchpad / "asset_binding_matrix.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Asset Binding Matrix",
        "",
        "Driver-generated semantic binding obligations for value-moving fields. "
        "Rows are generic field-pair questions, not expected findings. A `gap` "
        "means no active inventory/hypothesis/report row was found that mentions "
        "both fields together.",
        "",
        f"**Mode**: {mode}",
        f"**Detected Domains**: {', '.join(domains) or 'none'}",
        f"**Rows**: {len(rows)}",
        f"**Gaps**: {sum(1 for r in rows if r.get('status') == 'gap')}",
        "",
        "| ID | Class | Field A | Field B | Status | Obligation |",
        "|----|-------|---------|---------|--------|------------|",
    ]
    if rows:
        for row in rows:
            lines.append(
                f"| {row['id']} | {row['class']} | `{row['field_a']}` | "
                f"`{row['field_b']}` | {row['status']} | {row['rationale']} |"
            )
    else:
        lines.append("| n/a | n/a | - | - | none | No value-binding pairs triggered. |")
    (scratchpad / "asset_binding_matrix.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    _write_obligation_ledger(scratchpad, mode, rows, domains)
    return len(rows), int(payload["gap_count"])


def _composition_obligation_rows(scratchpad: Path) -> list[dict[str, object]]:
    path = scratchpad / "composition_coverage.md"
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    rows: list[dict[str, object]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not re.search(r"\bCH-\d{1,4}\b", line, re.IGNORECASE):
            continue
        if not re.search(
            r"\b(?:UPGRADE|COMPOSED|CHAIN[-_ ]?UPGRADE|cross[-_ ]?user|"
            r"fund\s+loss|theft|drain|strand(?:s|ed|ing)?|freez(?:e|es|ing)|lock(?:s|ed|ing)?)\b",
            line,
            re.IGNORECASE,
        ):
            continue
        ids = sorted(set(m.group(0).upper() for m in re.finditer(r"\bCH-\d{1,4}\b", line, re.I)))
        rid = ids[0] if ids else f"CH-L{line_no}"
        sev = "High" if re.search(r"\b(?:critical|high|theft|drain|fund\s+loss)\b", line, re.I) else "Medium"
        # A composition row where the chain agent EXPLICITLY declined to promote a
        # formal CH ID ("CH-13 would be ... noting but not assigning formal CH ID")
        # must not mint an *active* retention obligation: there is no chain to
        # preserve, and the report-index retention gate would otherwise demand
        # coverage by an obligation token that names a hypothetical. Mint it as a
        # pre-closed row so the ledger still records it without forcing a retry.
        # Anchored decline detection (v2.8.8 hardening): the non-promotion
        # phrase MUST be tied to a chain/CH referent, so a real fund-loss chain
        # line that merely contains "declining"/"not assigning blame"/etc. is
        # NOT silently marked covered. Bare `declin\w*` was dropped for this
        # reason (re-audit flagged it as a recall-regression over-match).
        declined = bool(re.search(
            r"(?:noting\s+but\s+not\s+assign\w*|"
            r"not\s+assign\w*\s+(?:a\s+)?(?:formal\s+)?(?:ch\b|chain\b|ch[-\s]?id\b)|"
            r"no\s+formal\s+ch[-\s]?id\b|"
            r"not\s+(?:a\s+)?(?:formal\s+|standalone\s+)?(?:formal\s+)?chain\b|"
            r"not\s+promot\w*\s+(?:to\s+)?(?:a\s+)?(?:formal\s+)?(?:ch\b|chain\b))",
            line,
            re.IGNORECASE,
        ))
        rows.append({
            "id": f"OBL-CHAIN-{rid}",
            "class": "chain_upgrade_retention",
            "source_id": rid,
            "status": "covered" if declined else "active",
            "severity_signal": sev,
            "source": "composition_coverage.md",
            "evidence": f"composition_coverage.md:L{line_no}",
            "target": line.strip()[:800],
            "closure_reason": (
                "chain agent explicitly declined to promote a formal CH ID "
                "(hypothetical/non-chain composition)" if declined else ""
            ),
            "absorbing_id": "",
        })
    return rows


def _write_obligation_ledger(
    scratchpad: Path,
    mode: str,
    asset_rows: list[dict[str, object]] | None = None,
    domains: list[str] | None = None,
) -> int:
    """Write a typed, protocol-neutral obligation ledger.

    The ledger is a deterministic retention contract, not a detector. Classes
    appear only when the audit artifacts trigger them, so protocols without
    routers, native assets, bridges, or chain compositions can legitimately
    have zero rows for those classes.
    """
    obligations: list[dict[str, object]] = []
    for row in asset_rows or []:
        rid = str(row.get("id") or "")
        if not rid:
            continue
        status = str(row.get("status") or "").lower()
        cls = "exact_value_binding"
        field_a = str(row.get("field_a") or "")
        field_b = str(row.get("field_b") or "")
        obligations.append({
            "id": f"OBL-{rid}",
            "class": cls,
            "source_id": rid,
            "status": "active" if status == "gap" else "covered",
            "severity_signal": "Medium" if status == "gap" else "Informational",
            "field_a": field_a,
            "field_b": field_b,
            "source": "asset_binding_matrix.md",
            "evidence": f"{rid} in asset_binding_matrix.md",
            "target": f"{field_a} <-> {field_b}",
            "closure_reason": "",
            "absorbing_id": "",
            "domains": row.get("domains") or domains or [],
        })
    obligations.extend(_composition_obligation_rows(scratchpad))

    payload = {
        "schema_version": "plamen.obligation_ledger.v1",
        "mode": mode,
        "domains": sorted(set(domains or [])),
        "row_count": len(obligations),
        "active_count": sum(1 for r in obligations if r.get("status") == "active"),
        "obligations": obligations,
    }
    (scratchpad / "obligation_ledger.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Obligation Ledger",
        "",
        "Driver-generated typed retention obligations. These rows are "
        "questions/receipts, not expected findings. Empty class sets are valid "
        "when the protocol shape does not trigger them.",
        "",
        "| ID | Class | Status | Severity Signal | Target | Source |",
        "|----|-------|--------|-----------------|--------|--------|",
    ]
    if obligations:
        for row in obligations:
            target = str(row.get("target") or "").replace("|", "/")
            lines.append(
                f"| {row.get('id')} | {row.get('class')} | {row.get('status')} | "
                f"{row.get('severity_signal')} | `{target}` | {row.get('evidence')} |"
            )
    else:
        lines.append("| n/a | n/a | none | n/a | No obligations triggered. | n/a |")
    (scratchpad / "obligation_ledger.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return len(obligations)


def _build_asset_binding_repair_items(scratchpad: Path, limit: int = 8) -> list[dict[str, str]]:
    path = scratchpad / "asset_binding_matrix.json"
    if not path.exists():
        try:
            _write_asset_binding_matrix(scratchpad)
        except Exception:
            return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    items: list[dict[str, str]] = []
    for row in payload.get("rows", []) or []:
        if str(row.get("status", "")).lower() != "gap":
            continue
        rid = str(row.get("id", "AB-???"))
        a = str(row.get("field_a", "field_a"))
        b = str(row.get("field_b", "field_b"))
        target = f"{rid}: {a} <-> {b}"
        reason = str(row.get("question") or row.get("rationale") or "unresolved asset binding")
        items.append({
            "kind": "asset-binding-gap",
            "target": target,
            "reason": reason,
            "source": "asset_binding_matrix.md",
            "evidence": f"{rid} in asset_binding_matrix.md",
        })
        if len(items) >= limit:
            break
    return items


def _extract_skill_execution_repair_items(scratchpad: Path, limit: int = 8) -> list[dict[str, str]]:
    path = scratchpad / "skill_execution_checklist.md"
    if not path.exists():
        return []
    try:
        text = _llm_norm(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    items: list[dict[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|") or _is_separator_row(s):
            continue
        cells = [c.strip().strip("`") for c in s.strip("|").split("|")]
        if len(cells) < 5 or cells[0].lower() == "skill":
            continue
        status = cells[2].upper()
        if "PARTIAL" not in status and "NOT_EXECUTED" not in status:
            continue
        skill = cells[0]
        agent = cells[1]
        gap = cells[4]
        items.append({
            "kind": "skill-execution-gap",
            "target": skill,
            "reason": f"{status}: required methodology gap for {agent}: {gap}",
            "source": "skill_execution_checklist.md",
            "evidence": f"{skill} row in skill_execution_checklist.md",
        })
        if len(items) >= limit:
            break
    return items


def _extract_candidate_facets(text: str) -> dict[str, list[str]]:
    norm = _llm_norm(text or "")
    mechanisms: list[str] = []
    for name, pattern in _FACET_KEYWORDS:
        if re.search(pattern, norm, re.IGNORECASE | re.DOTALL):
            mechanisms.append(name)
    functions = sorted({
        m.group(1)
        for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", norm)
        if m.group(1) not in {"if", "require", "assert", "revert", "return"}
    })[:12]
    fields = sorted({
        m.group(1)
        for m in re.finditer(r"\b(?:params|decoded|message|payload|data)\.([A-Za-z_][A-Za-z0-9_]*)\b", norm)
    })[:16]
    branch_terms = sorted({
        token
        for token in (
            "empty-input", "zero-value", "unauthenticated-source",
            "mismatched-parameter", "skipped-execution", "native-branch",
            "unbounded-decoding",
        )
        if (
            (token == "empty-input" and re.search(r"\bempty|length\s*==\s*0|\.length\s*==\s*0\b", norm, re.IGNORECASE))
            or (token == "zero-value" and re.search(r"\bzero|0\s*(?:amount|address|value)\b", norm, re.IGNORECASE))
            or (token == "unauthenticated-source" and re.search(r"\bunauth|missing\s+(?:sender|source).*validat|source\s+sender\b", norm, re.IGNORECASE))
            or (token == "mismatched-parameter" and re.search(r"\bmismatch|not\s+match|different\b", norm, re.IGNORECASE))
            or (token == "skipped-execution" and re.search(r"\bskip|bypass|not\s+execute|without\b", norm, re.IGNORECASE))
            or (token == "native-branch" and re.search(r"\bnative|wrapped|msg\.value|payable|sentinel\b", norm, re.IGNORECASE))
            or (token == "unbounded-decoding" and re.search(r"\bdecode|bytes|length|cast|deserialize\b", norm, re.IGNORECASE))
        )
    })
    return {
        "mechanisms": mechanisms,
        "entrypoints": functions,
        "decoded_fields": fields,
        "branch_conditions": branch_terms,
    }


def _write_candidate_semantic_facets(scratchpad: Path) -> int:
    rows = parse_verification_queue_rows(scratchpad)
    if not rows:
        return 0
    finding_record_maps = _load_finding_record_maps(scratchpad)
    records: list[dict[str, object]] = []
    for row in rows:
        fid = (row.get("finding id") or "").strip()
        if not fid:
            continue
        record = _finding_record_for_ids(scratchpad, [fid], finding_record_maps)
        parts = [
            fid,
            row.get("severity", ""),
            row.get("title", ""),
            row.get("location", ""),
        ]
        if record:
            for key in ("title", "root_cause", "description", "impact", "location"):
                val = record.get(key)
                if val:
                    parts.append(str(val))
        vp = _verify_file_for_id(scratchpad, fid)
        if vp.exists():
            try:
                parts.append(vp.read_text(encoding="utf-8", errors="replace")[:80_000])
            except Exception:
                pass
        facets = _extract_candidate_facets("\n".join(parts))
        records.append({
            "id": fid,
            "severity": normalize_severity(row.get("severity", "")),
            "title": row.get("title", ""),
            "location": row.get("location", ""),
            "facets": facets,
        })
    if not records:
        return 0
    payload = {
        "schema_version": "plamen.candidate_semantic_facets.v1",
        "candidate_count": len(records),
        "candidates": records,
    }
    (scratchpad / "candidate_semantic_facets.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Candidate Semantic Facets",
        "",
        "Driver-extracted semantic hints for preservation checks. These facets "
        "are not findings; they are compact reminders of mechanism, branch, "
        "field, and entrypoint details that must survive merge/dedup/reporting.",
        "",
        "| Candidate ID | Severity | Mechanisms | Branch Conditions | Decoded Fields | Entrypoints |",
        "|--------------|----------|------------|-------------------|----------------|-------------|",
    ]
    for rec in records:
        facets = rec["facets"]
        assert isinstance(facets, dict)
        lines.append(
            f"| {rec['id']} | {rec['severity']} | "
            f"{', '.join(facets.get('mechanisms', [])) or '-'} | "
            f"{', '.join(facets.get('branch_conditions', [])) or '-'} | "
            f"{', '.join(facets.get('decoded_fields', [])) or '-'} | "
            f"{', '.join(facets.get('entrypoints', [])) or '-'} |"
        )
    (scratchpad / "candidate_semantic_facets.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return len(records)


def _path_security_weight(path: str) -> int:
    """Rank uncovered files so repair stays bounded and security-relevant."""
    p = path.lower().replace("\\", "/")
    weights = (
        ("consensus", 9), ("validation", 9), ("block", 8), ("header", 8),
        ("transaction", 8), ("tx", 7), ("mempool", 7), ("p2p", 7),
        ("peer", 7), ("gossip", 7), ("network", 6), ("rpc", 6),
        ("api", 5), ("merkle", 8), ("proof", 8), ("vdf", 8),
        ("difficulty", 7), ("epoch", 6), ("fork", 6), ("cache", 5),
        ("storage", 5), ("database", 4), ("config", 2), ("test", -8),
    )
    return sum(w for token, w in weights if token in p)


def _spec_support_role(path: str) -> str:
    p = (path or "").replace("\\", "/").lower()
    leaf = p.rsplit("/", 1)[-1]
    if "harness" in leaf or "/harness" in p:
        return "harness invariant / fuzz expectation"
    if "/mock" in p or leaf.startswith(("mock", "fake", "stub")):
        return "mocked external dependency assumption"
    if leaf.endswith((".t.sol", ".test.ts", ".spec.ts", "_test.go", "_tests.rs")) or "/test" in p:
        return "test expectation / intended behavior"
    if "/script" in p or leaf.endswith(".s.sol"):
        return "deployment/script assumption"
    return "support/spec evidence"


def _write_spec_expectations(scratchpad: Path, support_paths: list[str] | set[str]) -> None:
    """Materialize support files as expectation evidence, not coverage debt.

    Attention repair should not spend audit budget proving mocks, tests, and
    harnesses safe. They are still valuable inputs: downstream agents can use
    them as claims to prove or break against production code.
    """
    paths = sorted({str(p).replace("\\", "/") for p in support_paths if str(p).strip()})
    out = scratchpad / "spec_expectations.md"
    if not paths:
        try:
            out.write_text(
                "# Spec Expectations\n\n"
                "**Status**: SKIPPED - no test/mock/harness support files were indexed.\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        return
    lines = [
        "# Spec Expectations",
        "",
        "These files are excluded from production coverage-obligation queues "
        "such as attention_repair. Use them as specification evidence only: "
        "tests describe intended behavior, mocks describe assumed external "
        "behavior, and harnesses describe invariants to prove or break against "
        "production code. Do not report findings against these files unless "
        "they affect deployment, verification validity, or hide a production "
        "false negative.",
        "",
        "| # | File | Role | How downstream agents should use it |",
        "|---|------|------|--------------------------------------|",
    ]
    for i, path in enumerate(paths, 1):
        role = _spec_support_role(path)
        lines.append(
            f"| {i} | `{path}` | {role} | Derive expectations, then test "
            "production code against those expectations. |"
        )
    try:
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _extract_graph_attention_rows(scratchpad: Path, limit: int = 12) -> list[dict[str, str]]:
    """Harvest uncertain graph rows that deserve a narrow repair look."""
    rows: list[dict[str, str]] = []
    files = (
        "field_validation_matrix.md",
        "primitive_correctness_findings.md",
        "network_amplification_findings.md",
        "lifecycle_replay_findings.md",
        "panic_audit_summary.md",
    )
    signal_re = re.compile(
        r"\b(NEEDS_REVIEW|UNKNOWN|PARTIAL\s+GAP|PARTIAL|WEAK|GAP|MISSING|"
        r"Missing\?\s*\|\s*(?:YES|TRUE)|EXPLOITABLE)\b",
        re.IGNORECASE,
    )
    for name in files:
        p = scratchpad / name
        if not p.exists():
            continue
        try:
            text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|") or _is_separator_row(stripped):
                continue
            if not signal_re.search(stripped):
                continue
            evidence = ", ".join(_extract_gap_paths_from_markdown(stripped)[:3])
            rows.append({
                "kind": "graph-row",
                "target": stripped[:500],
                "reason": f"uncertain or unsafe verdict in {name}",
                "source": name,
                "evidence": evidence or "row-level evidence required",
            })
            if len(rows) >= limit:
                return rows
    return rows


def _build_attention_repair_items(scratchpad: Path, mode: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen_targets: set[str] = set()

    def add(kind: str, target: str, reason: str, source: str, evidence: str = "") -> None:
        key = f"{kind}:{target}"
        if target and key not in seen_targets:
            items.append({
                "kind": kind,
                "target": target,
                "reason": reason,
                "source": source,
                "evidence": evidence or target,
            })
            seen_targets.add(key)

    # Generic discovery obligations are cheap to derive and intentionally
    # protocol-agnostic. They become active repair rows only in Thorough mode;
    # Light/Core still get the sidecar for downstream prompt context without
    # adding a new LLM repair spend.
    try:
        _write_security_obligations(scratchpad, mode)
    except Exception:
        pass
    if mode == "thorough":
        for obligation in _parse_security_obligation_items(scratchpad)[:10]:
            add(
                "security-obligation",
                obligation["id"],
                f"{obligation['class']}: {obligation['question']}",
                "security_obligations.md",
                obligation.get("signals", ""),
            )
        try:
            _write_asset_binding_matrix(scratchpad, mode)
            for item in _build_asset_binding_repair_items(scratchpad)[:8]:
                add(
                    item["kind"],
                    item["target"],
                    item["reason"],
                    item["source"],
                    item.get("evidence", ""),
                )
        except Exception:
            pass
        try:
            for item in _extract_skill_execution_repair_items(scratchpad)[:8]:
                add(
                    item["kind"],
                    item["target"],
                    item["reason"],
                    item["source"],
                    item.get("evidence", ""),
                )
        except Exception:
            pass
        try:
            for issue in _check_perturbation_block_per_finding(scratchpad):
                add(
                    "missing-perturbation-block",
                    issue[:240],
                    "depth finding lacks required sibling/field/direction/actor perturbation table",
                    "depth perturbation validator",
                    issue,
                )
        except Exception:
            pass

    if mode != "thorough":
        return items[:_ATTENTION_REPAIR_MAX_ITEMS]

    gap_file = scratchpad / "notread_priority_gaps.md"
    if gap_file.exists():
        try:
            text = _llm_norm(gap_file.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            text = ""
        if "All NOTREAD priority files received" not in text and "Status**: SKIPPED" not in text:
            for p in _extract_gap_paths_from_markdown(text):
                add("notread-file", p, "recon marked file NOTREAD and depth cited no evidence", gap_file.name)

    cov = _compute_scip_coverage_sets(scratchpad)
    _write_spec_expectations(
        scratchpad,
        cov.get("spec_support_indexed", set()) if isinstance(cov, dict) else set(),
    )
    uncited = list(cov.get("uncited", []))
    if uncited:
        ranked = sorted(
            uncited,
            key=lambda p: (-_path_security_weight(str(p)), str(p)),
        )
        for p in ranked[:16]:
            if _path_security_weight(str(p)) <= 0 and len(items) >= 8:
                break
            add(
                "uncited-security-file",
                str(p),
                "strict SCIP citation coverage did not touch this security-relevant file",
                "scip/repo_map.md",
            )

    for row in _extract_graph_attention_rows(scratchpad):
        add(row["kind"], row["target"], row["reason"], row["source"], row["evidence"])

    return items[:_ATTENTION_REPAIR_MAX_ITEMS]


def _write_attention_repair_queue(scratchpad: Path, items: list[dict[str, str]]) -> None:
    lines = [
        "# Attention Repair Queue",
        "",
        "This is a deterministic, bounded repair queue. It is not a second",
        "breadth/depth pass. Audit only these rows, preserve SAFE verdicts,",
        "and emit findings only with file:line evidence.",
        "",
        "MANDATORY RECEIPT CONTRACT: attention_repair_summary.md must contain",
        "one table row per queue row. The Queue #, Kind, and Target cells must",
        "copy this queue exactly. For path targets, copy the full relative path",
        "instead of a basename or folder summary. The Evidence cell must cite",
        "the same target path again with file:line evidence, or mark the row",
        "NEEDS_HUMAN if the source file is unavailable.",
        "",
        "ASSET-BINDING CONTRACT: for `asset-binding-gap` rows, Evidence/Notes",
        "must include one local `PAIR_CLAIM:` that names both queued fields",
        "exactly and states equality, explicit binding check, mismatch,",
        "unreachable path, or impossible pair. SAFE asset-binding rows also",
        "need `SAFE_REASON:EXPLICIT_EQUALITY`,",
        "`SAFE_REASON:EXPLICIT_BINDING_CHECK`, `SAFE_REASON:UNREACHABLE_PATH`,",
        "or `SAFE_REASON:IMPOSSIBLE_PAIR`. Do not use standalone revert,",
        "no-balance, residual-balance, or self-punishing reasoning as SAFE.",
        "",
        "| # | Kind | Target | Reason | Source | Evidence hint |",
        "|---|------|--------|--------|--------|---------------|",
    ]
    for i, item in enumerate(items, 1):
        target = str(item["target"]).replace("|", "\\|")
        reason = str(item["reason"]).replace("|", "\\|")
        source = str(item["source"]).replace("|", "\\|")
        evidence = str(item.get("evidence", "")).replace("|", "\\|")
        lines.append(f"| {i} | {item['kind']} | `{target}` | {reason} | `{source}` | `{evidence}` |")
    (scratchpad / "attention_repair_queue.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _write_attention_repair_skip(scratchpad: Path, reason: str) -> None:
    (scratchpad / "attention_repair_summary.md").write_text(
        "# Attention Repair\n\n"
        "**Status**: SKIPPED\n\n"
        f"Reason: {reason}\n",
        encoding="utf-8",
    )


def _prepare_attention_repair(scratchpad: Path, mode: str) -> tuple[bool, str]:
    items = _build_attention_repair_items(scratchpad, mode)
    if not items:
        return False, f"mode={mode}; no NOTREAD, strict-coverage, or graph-row repair queue"
    _write_attention_repair_queue(scratchpad, items)
    return True, f"{len(items)} bounded repair item(s)"


def _normalize_breadth_outputs(scratchpad: Path) -> list[str]:
    """Breadth output compatibility shim (A5).

    Rename `findings_breadth_*.md` → `analysis_*.md` when the agent produced
    the older name convention. The v1.2.1 Track B fix (`_render_expected_output_block`)
    emits a HARD CONTRACT directive telling the LLM to use `analysis_*.md`, but
    LLMs occasionally drift anyway. Rather than falsely halt a run where the
    work was completed correctly, auto-rename before the gate globs.

    Logs the rename to `violations.md` so it is still tracked as a
    contract-drift event worth noticing in post-mortems. Returns the list of
    (old, new) pairs that were renamed.
    """
    renamed: list[str] = []
    fb_files = sorted(scratchpad.glob("findings_breadth_*.md"))
    if not fb_files:
        return renamed
    existing_analysis = {p.name for p in scratchpad.glob("analysis_*.md")}
    for fb in fb_files:
        # Derive target name by replacing the prefix.
        new_name = fb.name.replace("findings_breadth_", "analysis_", 1)
        if new_name in existing_analysis:
            # Both names exist — don't clobber; the gate will accept the
            # analysis_*.md one and the findings_breadth_*.md one becomes
            # orphan content. Log but don't move.
            renamed.append(f"{fb.name} -> SKIPPED (target {new_name} exists)")
            continue
        target = scratchpad / new_name
        try:
            fb.rename(target)
            renamed.append(f"{fb.name} -> {new_name}")
        except Exception as e:
            renamed.append(f"{fb.name} -> FAILED ({e})")
    if renamed:
        try:
            vp = scratchpad / "violations.md"
            with vp.open("a", encoding="utf-8") as f:
                f.write("\n## Breadth filename drift (A5 shim)\n")
                for r in renamed:
                    f.write(f"- {r}\n")
        except Exception:
            pass
    return renamed


_SOURCE_IDS_RE = re.compile(
    r"\*\*Source\s+IDs?\*\*\s*:\s*(.+)", re.IGNORECASE
)


def _extract_dedup_absorbed_ids(scratchpad: Path) -> set[str]:
    """Extract finding IDs that were absorbed by semantic/mechanical dedup.

    Parses ``dedup_decisions.md`` for two formats:
    - LLM semantic dedup: ``| absorbed_id | MERGED into <survivor> |``
    - Mechanical fallback/supplement: ``| MECHANICAL_MERGE | absorbed_id | keep_id |``
      or ``| MECHANICAL_SUPPLEMENT | absorbed_id | keep_id |``

    These IDs are no longer standalone candidates — they are accounted for via
    the absorbing survivor.
    """
    absorbed: set[str] = set()
    for name in ("dedup_decisions.md",):
        path = scratchpad / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Format 1: LLM semantic dedup — | absorbed_id | MERGED into survivor |
        for m in re.finditer(
            r"^\|\s*(\S+)\s*\|\s*MERGED\s+into\b",
            text,
            re.MULTILINE | re.IGNORECASE,
        ):
            token = m.group(1).strip().strip("[]")
            if token and _INTERNAL_ID_RE.fullmatch(token):
                absorbed.add(token.upper())
        # Format 2: mechanical — | MECHANICAL_MERGE/SUPPLEMENT | absorbed_id | keep |
        for m in re.finditer(
            r"^\|\s*MECHANICAL_(?:MERGE|SUPPLEMENT)\s*\|\s*(\S+)\s*\|",
            text,
            re.MULTILINE | re.IGNORECASE,
        ):
            token = m.group(1).strip().strip("[]")
            if token and _INTERNAL_ID_RE.fullmatch(token):
                absorbed.add(token.upper())
    return absorbed


def _extract_source_ids_from_inventory(scratchpad: Path) -> set[str]:
    """Extract all Source IDs referenced by inventory findings.

    Inventory entries have ``**Source IDs**: CC-01, GC-4, DE-3`` lines.
    These are upstream breadth-level IDs already consolidated INTO INV-XXX
    findings — provenance metadata, not standalone candidates.
    """
    source_ids: set[str] = set()
    for name in ("findings_inventory.md", "findings_inventory_deduped.md"):
        path = scratchpad / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _SOURCE_IDS_RE.finditer(text):
            for token in re.split(r"[,;\s]+", m.group(1).strip()):
                token = token.strip().strip("[]")
                if token and _INTERNAL_ID_RE.fullmatch(token):
                    source_ids.add(token.upper())
    return source_ids


def _collect_raw_candidate_ledger_rows(
    scratchpad: Path, promoted_ids: set[str], excluded_ids: set[str]
) -> list[str]:
    """Build report_coverage candidate trace rows from authoritative feeders.

    This ledger is a hard gate, so it must not scan broad prose artifacts.
    Depth/breadth/scanner files contain examples, cross-references, historical
    IDs, standards identifiers, and intentionally unqueued leads. Treat those
    as advisory analysis context, not report-index candidates. The reportable
    candidate set begins at inventory/queue/verification mapping boundaries.
    """
    # Source IDs in inventory entries are provenance metadata for promoted
    # findings — they are implicitly accounted, not standalone candidates.
    implicit_ids = _extract_source_ids_from_inventory(scratchpad)
    effective_promoted = promoted_ids | implicit_ids
    # Dedup-absorbed IDs were merged into surviving findings by the semantic
    # or mechanical dedup phase — they are accounted, not standalone.
    dedup_absorbed = _extract_dedup_absorbed_ids(scratchpad)

    # Only scan authoritative candidate sources — inventory and verification
    # files.  Chain analysis artifacts (hypotheses, chain_hypotheses,
    # finding_mapping) are structural groupings of inventory IDs, not candidate
    # sources.  Backup/evidence-excluded queues are pre-dedup snapshots whose
    # IDs are accounted via the dedup pipeline.
    source_globs = [
        "findings_inventory.md",
        "findings_inventory_deduped.md",
        "verification_queue*.md",
        "verify_core.md",
    ]
    _SKIP_SUFFIXES = ("_evidence_excluded.md", "_pre_dedup.md")
    seen: set[tuple[str, str]] = set()
    rows: list[str] = []
    for pattern in source_globs:
        for path in sorted(scratchpad.glob(pattern), key=lambda p: p.name.lower()):
            if not path.is_file():
                continue
            if any(path.name.endswith(sfx) for sfx in _SKIP_SUFFIXES):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            ids = sorted({m.group(1).upper() for m in _INTERNAL_ID_RE.finditer(text)})
            for fid in ids:
                key = (path.name, fid)
                if key in seen:
                    continue
                seen.add(key)
                if fid in effective_promoted:
                    disposition = "PROMOTED"
                elif fid in excluded_ids:
                    disposition = "EXCLUDED"
                elif fid in dedup_absorbed:
                    disposition = "MERGED"
                else:
                    disposition = "AUTO_EXCLUDED"
                rows.append(
                    f"| {path.name.replace('|', '/')} | {fid} | {disposition} |"
                )
    return rows


def _load_poc_demotion_caps(scratchpad: Path) -> dict[str, dict[str, str]]:
    """Return finding-ID severity caps from poc_demotions.md."""
    path = scratchpad / "poc_demotions.md"
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    caps: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        if cells[0].lower() == "finding id" or set(cells[0]) <= {"-"}:
            continue
        caps[cells[0]] = {
            "capped_at": normalize_severity(cells[2]),
            "poc_class": cells[3],
            "reason": cells[4],
        }
    return caps


def _apply_mechanical_dedup_from_pairs(
    scratchpad: Path,
    phase_name: str,
    *,
    supplemental: bool = False,
) -> int:
    """Mechanical dedup from pre-computed candidate pairs.

    When ``supplemental=False`` (default): fallback when LLM dedup fails twice.
    Reads ``dedup_candidate_pairs.md``, merges ONLY source-ID subset / PERT
    lineage + same-severity pairs, writes ``*_deduped.md`` for later swap.

    When ``supplemental=True``: runs AFTER successful LLM dedup + artifact swap.
    Reads ``dedup_candidate_pairs_full.md`` (the complete candidate set),
    accepts ONLY location-overlap + title >= 1.00 + same-severity pairs
    (source-ID/PERT are too noisy for deferred pairs — they share agent provenance,
    not root cause), skips LLM-evaluated live pairs and already-absorbed IDs,
    modifies the swapped file in-place, and appends to ``dedup_decisions.md``.

    Returns the number of merges applied.
    """
    # --- file selection ---
    if supplemental:
        pairs_file = scratchpad / "dedup_candidate_pairs_full.md"
        if not pairs_file.exists():
            pairs_file = scratchpad / "dedup_candidate_pairs.md"
    else:
        pairs_file = scratchpad / "dedup_candidate_pairs.md"
    if not pairs_file.exists():
        return 0
    try:
        text = pairs_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    # --- supplemental: load IDs currently present in the target file ---
    present_ids: set[str] | None = None
    live_pairs: set[frozenset[str]] | None = None
    if supplemental:
        if phase_name == "semantic_dedup":
            _target = scratchpad / "verification_queue.md"
        elif phase_name == "sc_semantic_dedup":
            _target = scratchpad / "findings_inventory.md"
        else:
            return 0
        if not _target.exists():
            return 0
        _target_text = _target.read_text(encoding="utf-8", errors="replace")
        present_ids = set()
        for _tl in _target_text.splitlines():
            for _m in re.finditer(r"\b((?:INV|F)-\d+)\b", _tl):
                present_ids.add(_m.group(1))
        # Build exclusion set from the live pairs file (LLM already evaluated)
        _live_file = scratchpad / "dedup_candidate_pairs.md"
        if _live_file.exists():
            live_pairs = set()
            for _ll in _live_file.read_text(encoding="utf-8", errors="replace").splitlines():
                _ll = _ll.strip()
                if not _ll.startswith("|"):
                    continue
                _lc = [c.strip() for c in _ll.split("|")]
                _lc = [c for c in _lc if c]
                if len(_lc) < 2 or _lc[0].startswith("-") or _lc[0].lower().startswith("finding"):
                    continue
                _la = re.match(r"((?:INV|F)-\d+)", _lc[0])
                _lb = re.match(r"((?:INV|F)-\d+)", _lc[1])
                if _la and _lb:
                    live_pairs.add(frozenset([_la.group(1), _lb.group(1)]))

    # Parse table rows: | id_a: title | id_b: title | score | signal | same? |
    merge_pairs: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c]
        if len(cells) < 5:
            continue
        if cells[0].startswith("-") or cells[0].lower().startswith("finding"):
            continue
        signal = cells[3].lower()
        same_sev = cells[4].strip().lower()
        if same_sev != "yes":
            continue
        if supplemental:
            title_score = 0.0
            try:
                title_score = float(cells[2])
            except (ValueError, IndexError):
                pass
            has_strong = "location overlap" in signal and title_score >= 1.0
        else:
            has_strong = "source-id subset" in signal or "pert lineage" in signal
        if not has_strong:
            continue
        id_a_m = re.match(r"((?:INV|F)-\d+)", cells[0])
        id_b_m = re.match(r"((?:INV|F)-\d+)", cells[1])
        if not id_a_m or not id_b_m:
            continue
        id_a = id_a_m.group(1)
        id_b = id_b_m.group(1)
        # Skip pairs where either ID was already removed by prior dedup
        if present_ids is not None:
            if id_a not in present_ids or id_b not in present_ids:
                continue
        # Skip pairs already evaluated by LLM dedup (live set)
        if live_pairs is not None and frozenset([id_a, id_b]) in live_pairs:
            continue
        # Absorb direction:
        # - source-ID subset with ⊂: absorb A (the subset side) into B
        # - PERT lineage: absorb B (the PERT-sourced variant)
        # - location overlap (supplemental): absorb higher INV# into lower
        if "subset" in signal and "⊂" in signal:
            absorb, keep = id_a, id_b
        elif supplemental and "location overlap" in signal:
            num_a = int(re.search(r"\d+", id_a).group())
            num_b = int(re.search(r"\d+", id_b).group())
            if num_a > num_b:
                absorb, keep = id_a, id_b
            else:
                absorb, keep = id_b, id_a
        else:
            absorb, keep = id_b, id_a
        merge_pairs.append((absorb, keep, signal))

    if not merge_pairs:
        return 0

    # Deduplicate: if a finding is absorbed into multiple keepers, pick first
    absorbed_set: set[str] = set()
    final_merges: list[tuple[str, str, str]] = []
    for absorb, keep, sig in merge_pairs:
        if absorb in absorbed_set or keep in absorbed_set or absorb == keep:
            continue
        absorbed_set.add(absorb)
        final_merges.append((absorb, keep, sig))

    # --- write / append dedup decisions ---
    if supplemental:
        dec_path = scratchpad / "dedup_decisions.md"
        existing = ""
        if dec_path.exists():
            existing = dec_path.read_text(encoding="utf-8", errors="replace")
        supp_lines = [
            "",
            "---",
            "",
            "## Supplemental Mechanical Dedup",
            "",
            "**Status**: MECHANICAL_SUPPLEMENT",
            "",
            f"Applied {len(final_merges)} supplemental merge(s) from full "
            "candidate pair set (location overlap + title >= 1.00 + same "
            "severity only).",
            "",
            "| Action | Absorbed | Into | Signal |",
            "|--------|----------|------|--------|",
        ]
        for absorb, keep, sig in final_merges:
            supp_lines.append(
                f"| MECHANICAL_SUPPLEMENT | {absorb} | {keep} | {sig} |"
            )
        supp_lines.append("")
        dec_path.write_text(
            existing.rstrip("\n") + "\n" + "\n".join(supp_lines),
            encoding="utf-8",
        )
    else:
        dec_lines = [
            "# Semantic Dedup Decisions",
            "",
            "**Status**: MECHANICAL_FALLBACK",
            "",
            f"LLM dedup failed twice. Applied {len(final_merges)} conservative "
            "mechanical merge(s) from pre-computed candidate pairs "
            "(source-ID subset or PERT lineage + same severity only).",
            "",
            "## Decisions",
            "",
            "| Action | Absorbed | Into | Signal |",
            "|--------|----------|------|--------|",
        ]
        for absorb, keep, sig in final_merges:
            dec_lines.append(f"| MECHANICAL_MERGE | {absorb} | {keep} | {sig} |")
        dec_lines.append("")
        (scratchpad / "dedup_decisions.md").write_text(
            "\n".join(dec_lines), encoding="utf-8",
        )

    # --- remove absorbed finding rows from the target file ---
    if supplemental:
        # In-place: modify the already-swapped file
        if phase_name == "semantic_dedup":
            target = scratchpad / "verification_queue.md"
        elif phase_name == "sc_semantic_dedup":
            target = scratchpad / "findings_inventory.md"
        else:
            return len(final_merges)
    else:
        # Fallback: copy source to deduped target
        if phase_name == "semantic_dedup":
            source = scratchpad / "verification_queue.md"
            target = scratchpad / "verification_queue_deduped.md"
        elif phase_name == "sc_semantic_dedup":
            source = scratchpad / "findings_inventory.md"
            target = scratchpad / "findings_inventory_deduped.md"
        else:
            return len(final_merges)
        if not source.exists():
            return len(final_merges)

    read_path = target if supplemental else source
    if not read_path.exists():
        return len(final_merges)

    body = read_path.read_text(encoding="utf-8", errors="replace")
    # Remove absorbed finding rows from pipe-delimited tables.
    # Only check the row's primary Finding ID (first ID in the line),
    # NOT the full line — titles/descriptions may reference other IDs.
    absorbed_ids = {absorb for absorb, _, _ in final_merges}
    out_lines: list[str] = []
    for line in body.splitlines():
        skip = False
        if line.strip().startswith("|"):
            row_id_m = re.search(r"\b((?:INV|F)-\d+)\b", line)
            if row_id_m and row_id_m.group(1) in absorbed_ids:
                skip = True
        if not skip:
            out_lines.append(line)
    deduped = "\n".join(out_lines)
    if not deduped.endswith("\n"):
        deduped += "\n"
    target.write_text(deduped, encoding="utf-8")

    return len(final_merges)


def _cap_severity_at(severity: str, capped_at: str) -> str:
    """Apply a maximum severity cap without upgrading lower severities."""
    order = ["Critical", "High", "Medium", "Low", "Informational"]
    sev = normalize_severity(severity)
    cap = normalize_severity(capped_at)
    if order.index(sev) < order.index(cap):
        return cap
    return sev


def _write_mechanical_report_index(scratchpad: Path) -> int:
    """Build report_index.md deterministically from verifier artifacts.

    This removes the LLM index from the critical path: verifier status decides
    body vs Appendix A; Python assigns report IDs and writes a parseable index.
    """
    try:
        _write_candidate_semantic_facets(scratchpad)
    except Exception as exc:
        log.warning(f"[report_index] candidate semantic facets skipped: {exc!r}")
    rows = parse_verification_queue_rows(scratchpad)
    if not rows:
        return 0
    counters = {"C": 0, "H": 0, "M": 0, "L": 0, "I": 0}
    raw_active: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    poc_caps = _load_poc_demotion_caps(scratchpad)
    judge_downgrades = _collect_judge_downgrade_map(scratchpad)
    finding_record_maps = _load_finding_record_maps(scratchpad)

    for row in rows:
        fid = (row.get("finding id") or "").strip()
        if not fid:
            continue
        record = _finding_record_for_ids(scratchpad, [fid], finding_record_maps)
        vp = _verify_file_for_id(scratchpad, fid)
        try:
            vtxt = _llm_norm(vp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            vtxt = ""
        status = _verifier_status_from_text(vtxt)
        # Phase B: matrix is authoritative when Impact + Likelihood are present.
        # Falls back to LLM/queue severity otherwise (back-compat).
        severity = _enforce_severity_matrix(vtxt, row)
        unresolved = any(tok in status for tok in ("UNRESOLVED", "PARTIAL"))
        adjustments: list[str] = []
        if unresolved:
            severity = _demote_severity_once(severity)
            adjustments.append("UNRESOLVED")
        judge_sev = judge_downgrades.get(fid)
        if judge_sev and not unresolved:
            capped = _cap_severity_at(severity, judge_sev)
            if capped != severity:
                adjustments.append(f"SKEPTIC-DOWNGRADE({severity})")
                severity = capped
        poc_cap = poc_caps.get(fid)
        if poc_cap:
            capped = _cap_severity_at(severity, poc_cap["capped_at"])
            if capped != severity:
                adjustments.append(f"POC_FAIL_CAP:{poc_cap['capped_at']}")
            severity = capped
        title = (
            row.get("title", "").strip()
            or str(record.get("title", "") if record else "").strip()
            or _first_heading_title(vtxt)
            or "Verified finding"
        )
        title = _sanitize_client_title(re.sub(r"\s+", " ", title).replace("|", "/").strip())
        if record and _is_placeholder_report_title(title):
            title = _record_title(record) or title
        location = (
            row.get("location", "").strip()
            or str(record.get("location", "") if record else "").strip()
            or _field_from_markdown(vtxt, ("Location", "Primary Location"))
            or f"verify_{fid}.md"
        ).replace("|", "/")
        evidence = (
            _field_from_markdown(vtxt, ("Evidence Tag", "Evidence Tags", "Evidence"))
            or _field_from_markdown(vtxt, ("Preferred Tag", "Preferred Evidence"))
            or row.get("preferred tag", "")
            or EVIDENCE_TAG_DEFAULT
        ).replace("|", "/")

        if not _is_reportable_verdict(status):
            excluded.append({
                "finding_id": fid,
                "severity": severity,
                "title": title,
                "verdict": status,
                "reason": f"{status} in verify_{fid}.md",
            })
            continue
        # Phase C: pre-compute dedup signature from verify body for clustering.
        sig = _dedup_signature_for_finding(vtxt, severity=severity, hint_title=title)
        raw_active.append({
            "finding_id": fid,
            "severity": severity,
            "title": title,
            "location": location,
            "evidence": evidence,
            "verdict": status,
            "unresolved": bool(unresolved),
            "severity_adjustments": adjustments,
            "_sig_key": sig.key(),
            "_sig_vuln": sig.vuln_class,
            "_sig_fix": sig.fix_pattern,
        })

    # ----- Phase C: cluster raw_active by signature, threshold >= 3 -----
    consolidation_map: list[dict] = []
    by_key: dict[tuple, list[dict]] = {}
    for r in raw_active:
        by_key.setdefault(r["_sig_key"], []).append(r)
    consolidated: list[dict] = []
    consumed_ids: set[str] = set()
    for key, members in by_key.items():
        if len(members) >= 3:
            sig = DedupSignature(
                severity=members[0]["severity"],
                fix_pattern=members[0]["_sig_fix"],
                vuln_class=members[0]["_sig_vuln"],
            )
            absorbed = [m["finding_id"] for m in members]
            class_title = _consolidated_title_for(sig)
            locations = " ; ".join(sorted({m["location"] for m in members}))
            # Use strongest evidence (mechanical proof > trace > default)
            best_evidence = members[0]["evidence"]
            for m in members[1:]:
                if has_mechanical_proof(m["evidence"]):
                    best_evidence = m["evidence"]
                    break
            # Use most conservative verdict (CONFIRMED > PARTIAL > rest)
            _VERDICT_RANK = {"CONFIRMED": 3, "PARTIAL": 2, "CONTESTED": 1}
            best_verdict = max(
                members, key=lambda m: _VERDICT_RANK.get(m["verdict"], 0)
            )["verdict"]
            # Preserve unresolved if ANY member is unresolved.
            any_unresolved = any(bool(m.get("unresolved")) for m in members)
            consolidated.append({
                "finding_id": absorbed[0],
                "severity": members[0]["severity"],
                "title": class_title,
                "location": locations,
                "evidence": best_evidence,
                "verdict": best_verdict,
                "unresolved": any_unresolved,
                "severity_adjustments": sorted({
                    adj
                    for m in members
                    for adj in (m.get("severity_adjustments") or [])
                }),
                "absorbed_finding_ids": absorbed,
            })
            consolidation_map.append({
                "title": class_title,
                "severity": members[0]["severity"],
                "vuln_class": sig.vuln_class,
                "fix_pattern": sig.fix_pattern,
                "absorbed_finding_ids": absorbed,
            })
            consumed_ids.update(absorbed)

    active: list[dict[str, str]] = []
    # Keep all non-clustered raw rows + the consolidated rows, preserving order.
    for r in raw_active:
        if r["finding_id"] in consumed_ids:
            continue
        active.append({
            "finding_id": r["finding_id"],
            "severity": r["severity"],
            "title": r["title"],
            "location": r["location"],
            "evidence": r["evidence"],
            "verdict": r["verdict"],
            "unresolved": r["unresolved"],
            "severity_adjustments": r.get("severity_adjustments", []),
            "absorbed_finding_ids": [],
        })
    active.extend(consolidated)

    # Re-sort by severity rank then by original verdict order (stable).
    sev_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Informational": 4}
    active.sort(key=lambda r: sev_rank.get(r["severity"], 99))

    # Assign report IDs after consolidation.
    for r in active:
        prefix = _report_prefix_for_severity(r["severity"])
        counters[prefix] += 1
        r["report_id"] = f"{prefix}-{counters[prefix]:02d}"

    lines = [
        "# Report Index",
        "",
        "## Summary",
        "",
        "| Severity | Count |",
        "|----------|-------|",
        f"| Critical | {counters['C']} |",
        f"| High | {counters['H']} |",
        f"| Medium | {counters['M']} |",
        f"| Low | {counters['L']} |",
        f"| Informational | {counters['I']} |",
        f"| Total | {sum(counters.values())} |",
        "",
        "## Master Finding Index",
        "",
        "| Report ID | Title | Severity | Location | Evidence Tag | Verdict | Trust Adj. | Internal Hypothesis ID |",
        "|-----------|-------|----------|----------|--------------|---------|------------|------------------------|",
    ]
    for r in active:
        adj = ", ".join(r.get("severity_adjustments") or [])
        lines.append(
            f"| {r['report_id']} | {r['title']} | {r['severity']} | {r['location']} | "
            f"{r['evidence']} | {r['verdict']} | {adj} | {r['finding_id']} |"
        )
    if consolidation_map:
        lines.extend([
            "",
            "## Consolidation Map",
            "",
            "| Report ID | Title | Severity | Absorbed Findings | Reason |",
            "|-----------|-------|----------|-------------------|--------|",
        ])
        for cm in consolidation_map:
            # Find report ID by matching absorbed list.
            report_id = ""
            for r in active:
                if (
                    r.get("absorbed_finding_ids")
                    and set(r["absorbed_finding_ids"]) == set(cm["absorbed_finding_ids"])
                ):
                    report_id = r["report_id"]
                    break
            lines.append(
                f"| {report_id} | {cm['title']} | {cm['severity']} | "
                f"{', '.join(cm['absorbed_finding_ids'])} | "
                f"Same fix pattern ({cm['fix_pattern']}) and severity |"
            )
    lines.extend([
        "",
        "## Excluded Findings",
        "",
        "| Internal ID | Severity | Title | Exclusion Reason |",
        "|-------------|----------|-------|------------------|",
    ])
    for r in excluded:
        lines.append(
            f"| {r['finding_id']} | {r['severity']} | {r['title']} | {r['reason'].replace('|', '/')} |"
        )
    lines.append("")
    (scratchpad / "report_index.md").write_text("\n".join(lines), encoding="utf-8")
    (scratchpad / "report_records.json").write_text(
        json.dumps(
            {
                "active": active,
                "excluded": excluded,
                "consolidation_map": consolidation_map,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    coverage_lines = [
        "# Report Coverage",
        "",
        "Deterministic report-index coverage emitted by `_write_mechanical_report_index`.",
        "",
        "## Counts",
        "",
        f"- Verification queue rows parsed: {len(rows)}",
        f"- Active report rows: {len(active)}",
        f"- Excluded rows: {len(excluded)}",
        f"- Consolidation clusters: {len(consolidation_map)}",
        "",
        "## Active Trace",
        "",
        "| Report ID | Internal Finding IDs | Severity | Verdict | Evidence Tag |",
        "|-----------|----------------------|----------|---------|--------------|",
    ]
    for r in active:
        ids = r.get("absorbed_finding_ids") or [r["finding_id"]]
        coverage_lines.append(
            f"| {r['report_id']} | {', '.join(ids).replace('|', '/')} | "
            f"{r['severity']} | {r['verdict']} | {r['evidence']} |"
        )
    coverage_lines.extend([
        "",
        "## Excluded Trace",
        "",
        "| Internal Finding ID | Severity | Verdict / Reason |",
        "|---------------------|----------|------------------|",
    ])
    for r in excluded:
        coverage_lines.append(
            f"| {r['finding_id']} | {r['severity']} | {r['reason'].replace('|', '/')} |"
        )
    promoted_ids = {
        fid.upper()
        for r in active
        for fid in (r.get("absorbed_finding_ids") or [r["finding_id"]])
    }
    excluded_ids = {r["finding_id"].upper() for r in excluded}
    raw_rows = _collect_raw_candidate_ledger_rows(
        scratchpad, promoted_ids, excluded_ids
    )
    coverage_lines.extend([
        "",
        "## Raw Candidate Ledger",
        "",
        "| Source Artifact | Candidate ID | Disposition |",
        "|-----------------|--------------|-------------|",
    ])
    if raw_rows:
        coverage_lines.extend(raw_rows)
    else:
        coverage_lines.append("| _No raw candidate IDs found_ | n/a | n/a |")
    coverage_lines.append("")
    (scratchpad / "report_coverage.md").write_text(
        "\n".join(coverage_lines),
        encoding="utf-8",
    )
    for _tier in ("critical_high", "medium", "low_info"):
        ensure_report_tier_shards(scratchpad, _tier)
    # Phase E5 prerequisite: emit body-writer manifests so the next phase
    # (tier writer, mechanical or LLM) has a verified-evidence anchor. The
    # manifest dir doubles as the validator's source of truth.
    try:
        _build_body_writer_manifests(scratchpad)
    except Exception as exc:
        log.warning(f"[report_index] body-writer manifest emission failed: {exc!r}")
    return len(active)


def _latest_report_index_backup(scratchpad: Path, filename: str) -> Path | None:
    candidates: list[Path] = []
    direct = scratchpad / filename
    if direct.exists() and direct.stat().st_size > 0:
        candidates.append(direct)
    qdir = scratchpad / "_retry_quarantine" / "report_index"
    if qdir.exists():
        candidates.extend(
            p for p in qdir.glob(f"{filename}*") if p.is_file() and p.stat().st_size > 0
        )
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _split_markdown_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _join_markdown_row(cells: list[str]) -> str:
    return "| " + " | ".join((c or "").replace("|", "/").strip() for c in cells) + " |"


def _report_id_replace_once(text: str, rid_map: dict[str, str]) -> str:
    if not rid_map:
        return text
    old_ids = sorted((re.escape(k) for k in rid_map), key=len, reverse=True)
    pat = re.compile(r"(?<![A-Z0-9-])(" + "|".join(old_ids) + r")(?![A-Z0-9-])")
    return pat.sub(lambda m: rid_map.get(m.group(1), m.group(1)), text)


def _replace_or_insert_summary_counts(text: str, counts: dict[str, int]) -> str:
    total = sum(counts.values())
    summary = "\n".join([
        "## Summary Counts",
        "",
        "| Severity | Count |",
        "|----------|-------|",
        f"| Critical | {counts.get('Critical', 0)} |",
        f"| High | {counts.get('High', 0)} |",
        f"| Medium | {counts.get('Medium', 0)} |",
        f"| Low | {counts.get('Low', 0)} |",
        f"| Informational | {counts.get('Informational', 0)} |",
        f"| **Total** | **{total}** |",
    ])
    m = re.search(
        r"(?ms)^##\s+Summary(?:\s+Counts)?\b.*?(?=^---\s*$\n\n^##|\n\n^##\s+Master\s+Finding\s+Index\b)",
        text,
    )
    if m:
        return text[:m.start()] + summary + "\n\n" + text[m.end():].lstrip()
    anchor = re.search(r"(?m)^##\s+Master\s+Finding\s+Index\b", text)
    if anchor:
        return text[:anchor.start()] + summary + "\n\n---\n\n" + text[anchor.start():]
    return summary + "\n\n---\n\n" + text


def _build_tier_assignments_from_index_rows(rows: list[dict[str, object]]) -> str:
    def verify_files_for(internal: str) -> str:
        ids = [
            _normalize_finding_id(fid) or fid
            for fid in _INTERNAL_FINDING_ID_RE.findall(internal or "")
        ]
        paths = [f".scratchpad/verify_{fid}.md" for fid in ids if not fid.startswith("CH-")]
        return ", ".join(paths) if paths else "(see constituent verification files)"

    groups = [
        ("Critical+High Tier (for Opus writer)", {"Critical", "High"}),
        ("Medium Tier (for Sonnet writer)", {"Medium"}),
        ("Low+Info Tier (for Sonnet writer)", {"Low", "Informational"}),
    ]
    out = ["## Tier Assignments", ""]
    for title, severities in groups:
        out.extend([
            f"### {title}",
            "",
            "| Report ID | Internal Hypothesis | Verify File(s) | Notes |",
            "|-----------|---------------------|----------------|-------|",
        ])
        for row in rows:
            if str(row["severity"]) not in severities:
                continue
            internal = str(row.get("internal", "")).strip()
            notes = str(row.get("verification", "") or row.get("trust_adj", "") or "Mapped from Master Finding Index")
            out.append(
                f"| {row['report_id']} | {internal} | {verify_files_for(internal)} | {notes.replace('|', '/')} |"
            )
        out.append("")
    return "\n".join(out).rstrip()


def _replace_section(text: str, heading_re: str, replacement: str) -> str:
    m = re.search(heading_re, text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return text.rstrip() + "\n\n---\n\n" + replacement.strip() + "\n"
    next_heading = re.search(r"(?m)^##\s+", text[m.end():])
    end = m.end() + next_heading.start() if next_heading else len(text)
    return text[:m.start()] + replacement.strip() + "\n" + text[end:]


def _replace_completeness_assertion(text: str, rows: list[dict[str, object]]) -> str:
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for row in rows:
        sev = str(row.get("severity", ""))
        if sev in counts:
            counts[sev] += 1
    receipt = "\n".join([
        "## Completeness Assertion",
        "",
        "```",
        "REPORT_INDEX_REPAIRED_BY_PYTHON: true",
        f"Reportable rows: {sum(counts.values())}",
        f"Critical: {counts['Critical']}",
        f"High: {counts['High']}",
        f"Medium: {counts['Medium']}",
        f"Low: {counts['Low']}",
        f"Informational: {counts['Informational']}",
        "Coverage accounting remains in report_coverage.md.",
        "```",
    ])
    return _replace_section(text, r"(?m)^##\s+Completeness\s+Assertion\b.*$", receipt)


def _repair_sc_report_index_from_prior(scratchpad: Path) -> int:
    """Repair an SC LLM report index when only mechanical contracts failed.

    SC report indexing is a semantic consolidation step, so replacing it with
    the L1 deterministic index can discard useful titles and grouping. Preserve
    the LLM index, then mechanically repair contract violations whose source of
    truth is already known to Python: report-ID tier, final severity, summary
    counts, and writer routing.
    """
    source = _latest_report_index_backup(scratchpad, "report_index.md")
    if source is None:
        return 0
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    lines = text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        lowered = line.lower()
        if line.lstrip().startswith("|") and "report id" in lowered and "severity" in lowered:
            header_idx = i
            break
    if header_idx < 0 or header_idx + 1 >= len(lines):
        return 0
    end_idx = header_idx + 2
    while end_idx < len(lines) and lines[end_idx].lstrip().startswith("|"):
        end_idx += 1

    headers = _split_markdown_row(lines[header_idx])
    keys = [_norm_key(h) for h in headers]
    try:
        rid_i = keys.index("report id")
    except ValueError:
        return 0
    sev_i = keys.index("severity") if "severity" in keys else -1
    verification_i = keys.index("verification") if "verification" in keys else -1
    trust_i = next(
        (i for i, k in enumerate(keys) if k in {"trust adj", "trust adjustment", "severity trail", "sev trail", "adjustment"}),
        -1,
    )
    internal_i = next(
        (i for i, k in enumerate(keys) if k in {"internal hypothesis", "internal hypothesis id", "internal id", "finding id", "hypothesis"}),
        -1,
    )
    if sev_i < 0 or internal_i < 0:
        return 0

    expected_by_id = _expected_report_index_severities(scratchpad)
    severity_rank_map = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    rows: list[dict[str, object]] = []
    changed = False
    for original_order, line in enumerate(lines[header_idx + 2:end_idx]):
        cells = _split_markdown_row(line)
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        rid = (_normalize_report_id(cells[rid_i]) or cells[rid_i]).upper()
        if not re.fullmatch(r"[CHMLI]-\d+", rid or "", re.IGNORECASE):
            continue
        internal = cells[internal_i]
        ids = [
            _normalize_finding_id(fid) or fid
            for fid in _INTERNAL_FINDING_ID_RE.findall(internal)
        ]
        current_sev = normalize_severity(cells[sev_i])
        expected = [expected_by_id[fid] for fid in ids if fid in expected_by_id]
        target_sev = current_sev
        if expected:
            source_sev = min(expected, key=lambda sev: severity_rank_map.get(sev, 99))
            reason_values = [
                cells[trust_i] if trust_i >= 0 and trust_i < len(cells) else "",
                cells[verification_i] if verification_i >= 0 and verification_i < len(cells) else "",
                cells[sev_i],
            ]
            if current_sev != source_sev and not _report_index_adjustment_reason_present(*reason_values):
                target_sev = source_sev
                cells[sev_i] = source_sev
                changed = True
        rows.append({
            "old_report_id": rid,
            "report_id": rid,
            "severity": target_sev,
            "cells": cells,
            "order": original_order,
            "internal": internal,
            "verification": cells[verification_i] if verification_i >= 0 and verification_i < len(cells) else "",
            "trust_adj": cells[trust_i] if trust_i >= 0 and trust_i < len(cells) else "",
        })

    if not rows:
        return 0

    counters = {sev: 0 for sev in SEVERITY_ORDER}
    ordered: list[dict[str, object]] = []
    for sev in SEVERITY_ORDER:
        sev_rows = [r for r in rows if r["severity"] == sev]
        sev_rows.sort(key=lambda r: int(r["order"]))
        for row in sev_rows:
            counters[sev] += 1
            new_rid = f"{SEVERITY_LETTER[sev]}-{counters[sev]:02d}"
            if row["old_report_id"] != new_rid:
                changed = True
            row["report_id"] = new_rid
            cells = list(row["cells"])
            cells[rid_i] = new_rid
            cells[sev_i] = sev
            row["cells"] = cells
            ordered.append(row)

    table_lines = [
        _join_markdown_row(headers),
        lines[header_idx + 1],
        *[_join_markdown_row(list(row["cells"])) for row in ordered],
    ]
    rid_map = {
        str(row["old_report_id"]): str(row["report_id"])
        for row in ordered
        if row["old_report_id"] != row["report_id"]
    }
    table_text = "\n".join(table_lines)
    before_table = _report_id_replace_once("\n".join(lines[:header_idx]), rid_map)
    after_table = _report_id_replace_once("\n".join(lines[end_idx:]), rid_map)
    repaired = before_table.rstrip() + "\n\n" + table_text + "\n\n" + after_table.lstrip()
    repaired = _replace_or_insert_summary_counts(repaired, counters)
    repaired = _replace_section(
        repaired,
        r"(?m)^##\s+Tier\s+Assignments\b.*$",
        _build_tier_assignments_from_index_rows(ordered),
    )
    repaired = _replace_completeness_assertion(repaired, ordered)
    if changed:
        repaired += "\n## Mechanical Repair Receipt\n\n"
        repaired += "- Repaired report IDs and severity tiers from verifier/queue authority.\n"
        repaired += "- Rebuilt summary counts and tier assignments from Master Finding Index.\n"
        if rid_map:
            repaired += "- Report ID remap: " + ", ".join(f"{k}->{v}" for k, v in rid_map.items()) + "\n"

    (scratchpad / "report_index.md").write_text(repaired, encoding="utf-8")

    coverage_source = _latest_report_index_backup(scratchpad, "report_coverage.md")
    if coverage_source is not None:
        try:
            cov = coverage_source.read_text(encoding="utf-8", errors="replace")
            cov = _report_id_replace_once(cov, rid_map)
            (scratchpad / "report_coverage.md").write_text(cov, encoding="utf-8")
        except Exception:
            pass
    elif not (scratchpad / "report_coverage.md").exists():
        (scratchpad / "report_coverage.md").write_text(
            "# Report Coverage\n\nMechanical SC report-index repair had no prior coverage ledger.\n",
            encoding="utf-8",
        )

    try:
        _build_sc_body_writer_manifests(scratchpad)
    except Exception as exc:
        log.warning(f"[report_index] SC body manifest rebuild after repair failed: {exc!r}")
    return len(ordered)


def _shard_name_for_severity(severity: str) -> str:
    sev = normalize_severity(severity)
    if sev in ("Critical", "High"):
        return "report_critical_high"
    if sev == "Medium":
        return "report_medium"
    return "report_low_info"


def _parse_report_index_excluded_records(scratchpad: Path) -> list[dict[str, str]]:
    """Parse client-safe excluded records from an LLM-authored report index."""
    idx = scratchpad / "report_index.md"
    if not idx.exists():
        return []
    try:
        text = _llm_norm(idx.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    m = re.search(r"(?ims)^##\s+Excluded\s+Findings\b.*?(?=^##\s|\Z)", text)
    if not m:
        return []
    section = m.group(0)
    records: list[dict[str, str]] = []
    headers, rows = _parse_markdown_table(section, [])
    if headers:
        keys = [_norm_key(h) for h in headers]
        for row in rows:
            d = {keys[i]: row[i].strip() for i in range(min(len(keys), len(row)))}
            raw_id = (
                d.get("internal id")
                or d.get("internal hypothesis id")
                or d.get("internal hypothesis")
                or d.get("finding id")
                or d.get("id")
                or ""
            )
            fid = _normalize_finding_id(raw_id) or raw_id.strip()
            title = _sanitize_client_title(d.get("title", "") or "Excluded finding")
            reason = _sanitize_client_body(
                d.get("exclusion reason")
                or d.get("reason")
                or d.get("verdict")
                or d.get("status")
                or "Excluded by report index"
            )
            severity = normalize_severity(d.get("severity", "") or "Informational")
            if fid or title:
                records.append({
                    "finding_id": fid,
                    "severity": severity,
                    "title": title,
                    "reason": reason,
                    "verdict": reason,
                })
    if records:
        return records
    seen: set[str] = set()
    for match in _INTERNAL_FINDING_ID_RE.finditer(section):
        fid = _normalize_finding_id(match.group(1)) or match.group(1)
        if fid in seen:
            continue
        seen.add(fid)
        records.append({
            "finding_id": fid,
            "severity": "Informational",
            "title": "Excluded finding",
            "reason": "Excluded by report index",
            "verdict": "Excluded by report index",
        })
    return records


def _clean_finding_id_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize and drop blank finding IDs before deriving artifact names."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        fid = _normalize_finding_id(str(raw or "")) or str(raw or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        out.append(fid)
    return out


def _body_writer_poc_result_field(verify_text: str) -> str:
    """Extract verifier PoC/result text for report body manifests."""
    return _field_or_section(
        verify_text or "",
        (
            "PoC Result",
            "Execution Result",
            "Execution Output",
            "Test Output",
            "Proof",
        ),
        (
            "PoC Result",
            "Execution Result",
            "Execution Output",
            "Test Output",
            "Proof",
            "Reproduction",
            "PoC Attempt",
            "PoC Execution",
        ),
        fallback="",
        max_chars=2500,
    ).strip()


def _build_body_writer_manifests(scratchpad: Path) -> dict[str, dict]:
    """Emit per-shard manifests anchored to verified evidence.

    Splits shards when their finding count exceeds the per-tier cap. Cap names
    follow the convention `report_xxx_a`, `report_xxx_b`, etc.
    """
    records_path = scratchpad / "report_records.json"
    if not records_path.exists():
        return {}
    try:
        records = json.loads(records_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    active = records.get("active", [])
    inventory_location_by_id = _inventory_location_map(scratchpad)
    finding_record_maps = _load_finding_record_maps(scratchpad)
    # Group rows by tier shard.
    grouped: dict[str, list[dict]] = {
        "report_critical_high": [],
        "report_medium": [],
        "report_low_info": [],
    }
    for row in active:
        sev = row.get("severity", "")
        shard = _shard_name_for_severity(sev)
        verify_files = []
        absorbed = _clean_finding_id_list(row.get("absorbed_finding_ids") or [])
        row_finding_ids = _clean_finding_id_list([row.get("finding_id", "")])
        row_finding_id = row_finding_ids[0] if row_finding_ids else ""
        if absorbed:
            verify_files = [f"verify_{fid}.md" for fid in absorbed]
        else:
            if row_finding_id:
                verify_files = [f"verify_{row_finding_id}.md"]
        record = _finding_record_for_ids(
            scratchpad,
            absorbed or ([row_finding_id] if row_finding_id else []),
            finding_record_maps,
        )
        # Pull narrative seed from the primary verify file, but evaluate
        # evidence completeness across every absorbed verifier file.
        primary_verify_path = (
            (scratchpad / verify_files[0]) if verify_files else None
        )
        verify_text = ""
        if primary_verify_path and primary_verify_path.exists():
            try:
                verify_text = _llm_norm(primary_verify_path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                verify_text = ""
        desc, rec = _body_writer_evidence_fields(verify_text)
        poc_result = _body_writer_poc_result_field(verify_text)
        if (not desc or not _is_substantive_body_evidence(desc)) and record:
            desc = str(
                record.get("description")
                or record.get("root_cause")
                or record.get("impact")
                or ""
            )
        location = (row.get("location", "") or "").strip()
        if not _looks_like_code_location(location) or _is_test_or_mock_location(location):
            location = ""
        if not location:
            for vf in verify_files:
                p = scratchpad / vf
                if not p.exists():
                    continue
                try:
                    txt = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    txt = ""
                location = _extract_code_location_from_text(txt)
                if location and not _is_test_or_mock_location(location):
                    break
                location = ""
        if not location:
            location = _inventory_location_for_ids(
                scratchpad,
                absorbed or ([row_finding_id] if row_finding_id else []),
                inventory_location_by_id,
            )
        verify_statuses = []
        report_blocked = not bool(verify_files)
        for vf in verify_files:
            p = scratchpad / vf
            txt = ""
            if p.exists():
                try:
                    txt = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    txt = ""
            missing = (not p.exists()) or _is_evidence_missing_for_body(txt)
            verify_statuses.append({"file": vf, "exists": p.exists(), "evidence_missing": bool(missing)})
            if missing:
                report_blocked = True
        grouped[shard].append({
            "report_id": row.get("report_id", ""),
            "finding_id": row_finding_id,
            "severity": row.get("severity", ""),
            "title": (
                _record_title(record)
                if record and _is_placeholder_report_title(str(row.get("title", "")))
                else row.get("title", "")
            ) or str(record.get("title", "") if record else ""),
            "location": location,
            "evidence_tag": row.get("evidence", EVIDENCE_TAG_DEFAULT),
            "verify_file": verify_files[0] if verify_files else "",
            "verify_files": verify_files,
            "verify_statuses": verify_statuses,
            "description": desc,
            "poc_result": poc_result,
            "recommendation": rec,
            "report_blocked": bool(report_blocked),
        })

    manifests: dict[str, dict] = {}
    for shard, rows in grouped.items():
        if not rows:
            continue
        cap = _BODY_SHARD_CAPS.get(shard, 30)
        if len(rows) <= cap:
            manifests[shard] = {"shard": shard, "findings": rows}
        else:
            # Split into _a/_b/_c ... shards balanced by cap.
            n = len(rows)
            n_shards = (n + cap - 1) // cap
            chunk = (n + n_shards - 1) // n_shards
            for i in range(n_shards):
                suffix = chr(ord("a") + i)
                slice_rows = rows[i * chunk : (i + 1) * chunk]
                if not slice_rows:
                    continue
                name = f"{shard}_{suffix}"
                manifests[name] = {"shard": name, "findings": slice_rows}

    # Persist manifests so subsequent phases can read them deterministically.
    out_dir = scratchpad / "body_manifests"
    try:
        out_dir.mkdir(exist_ok=True)
        for old in out_dir.glob("report_*.json"):
            if old.stem not in manifests:
                try:
                    old.unlink()
                except Exception:
                    pass
                for stale in (
                    scratchpad / f"{old.stem}.md",
                    scratchpad / f"{old.stem}_assignments.md",
                ):
                    try:
                        stale.unlink(missing_ok=True)
                    except Exception:
                        pass
        for name, m in manifests.items():
            (out_dir / f"{name}.json").write_text(
                json.dumps(m, indent=2),
                encoding="utf-8",
            )
    except Exception:
        pass
    return manifests


def _build_sc_body_writer_manifests(scratchpad: Path) -> dict[str, dict]:
    """Build body-writer manifests for SC pipelines from LLM-written report_index.md.

    L1 uses _write_mechanical_report_index -> _build_body_writer_manifests.
    SC receives an LLM-authored report_index.md, so this function bridges the
    gap: it parses report_index.md via get_tier_assignments, enriches each row
    with title/location/evidence from structured finding records and verifier
    files, then writes both body_manifests/*.json and report_records.json for
    downstream assembly/traceability.
    """
    assignments, source = get_tier_assignments(scratchpad)
    if not assignments:
        return {}

    # --- Enrich from report_index.md table cells (title, location) ---
    idx_path = scratchpad / "report_index.md"
    title_map: dict[str, str] = {}
    location_map: dict[str, str] = {}
    verification_map: dict[str, str] = {}
    if idx_path.exists():
        try:
            idx_text = _llm_norm(idx_path.read_text(encoding="utf-8", errors="replace"))
            idx_text = _report_index_reportable_text(idx_text)
            idx_text = _report_index_assignment_text(idx_text)
        except Exception:
            idx_text = ""
        id_re = re.compile(r"^[\*\[`_]*([CHMLI]-\d+)\b")
        header_map: dict[str, int] = {}
        for line in idx_text.splitlines():
            s = line.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) < 2:
                continue
            lowered = [re.sub(r"\s+", " ", c.strip().lower()) for c in cells]
            if "report id" in lowered:
                header_map = {name: idx for idx, name in enumerate(lowered)}
                continue
            m = id_re.match(cells[0])
            if not m:
                continue
            rid = m.group(1)
            title_idx = header_map.get("title", 1)
            location_idx = (
                header_map.get("location")
                if header_map
                else 3
            )
            title_map[rid] = (
                cells[title_idx].strip() if title_idx < len(cells) else ""
            )
            location_map[rid] = (
                cells[location_idx].strip()
                if location_idx is not None and location_idx < len(cells)
                else ""
            )
            verification_idx = (
                header_map.get("verification")
                if header_map
                else None
            )
            if verification_idx is not None and verification_idx < len(cells):
                verification_map[rid] = cells[verification_idx].strip()

    def _extract_internal_ids(value: str) -> list[str]:
        ids = [
            _normalize_finding_id(m.group(1)) or m.group(1)
            for m in _INTERNAL_FINDING_ID_RE.finditer(value or "")
        ]
        if ids:
            return list(dict.fromkeys(ids))
        out: list[str] = []
        for tok in re.split(r"[,;+]\s*", value or ""):
            fid = _normalize_finding_id(tok)
            if fid:
                out.append(fid)
        return list(dict.fromkeys(out))

    try:
        hypothesis_constituents = _parse_hypothesis_constituents(scratchpad)
    except Exception:
        hypothesis_constituents = {}

    def _verification_cell_verify_ids(report_id: str) -> list[str]:
        """Return concrete verifier IDs explicitly named by the index row."""
        ids: list[str] = []
        for fid in _extract_internal_ids(verification_map.get(report_id, "")):
            if fid.startswith("CH-"):
                continue
            if _verify_file_for_id(scratchpad, fid).exists():
                ids.append(fid)
        return list(dict.fromkeys(ids))

    def _constituent_verify_ids(report_id: str, finding_ids: list[str]) -> list[str]:
        """Resolve report-level chain IDs to the verifier files that prove them.

        Report IDs like H-01 may map to CH-1, but there is no `verify_CH-1.md`.
        The canonical evidence lives in constituent verifier files referenced by
        the report-index Verification cell, e.g. `H-3+H-9`. Treat those as the
        body writer evidence source instead of marking the report blocked.
        """
        explicit_verified = _verification_cell_verify_ids(report_id)
        if explicit_verified:
            return explicit_verified
        expanded = list(finding_ids)
        for fid in list(finding_ids):
            if not fid.startswith("CH-"):
                continue
            constituents: list[str] = []
            for cid in _extract_internal_ids(verification_map.get(report_id, "")):
                if cid.startswith("CH-") or cid in expanded:
                    continue
                if _verify_file_for_id(scratchpad, cid).exists():
                    constituents.append(cid)
            for cid in hypothesis_constituents.get(fid, []) or []:
                if cid.startswith("CH-") or cid in expanded or cid in constituents:
                    continue
                if _verify_file_for_id(scratchpad, cid).exists():
                    constituents.append(cid)
            if constituents:
                return list(dict.fromkeys([
                    cid for cid in expanded if not cid.startswith("CH-")
                ] + constituents))
        return expanded

    queue_location_by_id: dict[str, str] = {}
    inventory_location_by_id = _inventory_location_map(scratchpad)
    finding_record_maps = _load_finding_record_maps(scratchpad)
    try:
        for qrow in parse_verification_queue_rows(scratchpad):
            qfid = (
                qrow.get("finding id")
                or qrow.get("hypothesis id")
                or qrow.get("id")
                or ""
            ).strip()
            qloc = (
                qrow.get("location")
                or qrow.get("primary location")
                or qrow.get("source location")
                or ""
            ).strip()
            norm = _normalize_finding_id(qfid) or qfid
            if (
                norm
                and qloc
                and _looks_like_code_location(qloc)
                and not _is_test_or_mock_location(qloc)
            ):
                queue_location_by_id[norm] = qloc
    except Exception:
        queue_location_by_id = {}

    # --- Build per-finding records ---
    grouped: dict[str, list[dict]] = {
        "report_critical_high": [],
        "report_medium": [],
        "report_low_info": [],
    }
    active_records: list[dict[str, object]] = []
    _SEV_EXPAND = {
        "C": "Critical", "H": "High", "M": "Medium",
        "L": "Low", "I": "Informational",
    }
    for row in assignments:
        report_id = row.get("report_id", "")
        if not re.fullmatch(r"[CHMLI]-\d+", report_id or "", re.IGNORECASE):
            continue
        report_id = report_id.upper()
        finding_id = row.get("finding_id", "")
        finding_ids = _extract_internal_ids(finding_id)
        is_chain_report = any(fid.startswith("CH-") for fid in finding_ids)
        verify_ids = _constituent_verify_ids(report_id, finding_ids)
        sev_letter = row.get("severity", "")
        sev_full = _SEV_EXPAND.get(sev_letter, sev_letter)
        shard = _shard_name_for_severity(sev_full)

        title = title_map.get(report_id, "")
        location = location_map.get(report_id, "")
        record = _finding_record_for_ids(scratchpad, verify_ids, finding_record_maps)

        verify_text = ""
        primary_fid = verify_ids[0] if verify_ids else ""
        if primary_fid:
            vf = _verify_file_for_id(scratchpad, primary_fid)
            if vf.exists():
                try:
                    verify_text = _llm_norm(vf.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    pass

        verify_title = _verified_claim_title(verify_text) if verify_text else ""
        if (
            verify_title
            and len(verify_ids) == 1
            and not is_chain_report
            and (not title or _titles_conflict(title, verify_title))
        ):
            title = verify_title
        elif not title and verify_title:
            title = verify_title
        if record and _is_placeholder_report_title(title):
            title = _record_title(record) or title
        desc, rec = _body_writer_evidence_fields(verify_text)
        poc_result = _body_writer_poc_result_field(verify_text)
        if (not desc or not _is_substantive_body_evidence(desc)) and record:
            desc = str(
                record.get("description")
                or record.get("root_cause")
                or record.get("impact")
                or ""
            )
        evidence_tag = (
            _field_from_markdown(verify_text, ("Evidence Tag", "Evidence", "Preferred Tag", "Preferred Evidence"))
            or str(record.get("preferred_tag", "") if record else "")
            or EVIDENCE_TAG_DEFAULT
        ).strip()
        if location and (not _looks_like_code_location(location) or _is_test_or_mock_location(location)):
            location = ""
        verify_location = _extract_code_location_from_text(verify_text) if verify_text else ""
        verify_location_authoritative = bool(verify_location and verify_text)
        if not verify_location:
            for fid in finding_ids:
                vf = _verify_file_for_id(scratchpad, fid)
                if not vf.exists():
                    continue
                try:
                    candidate_text = _llm_norm(vf.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    candidate_text = ""
                verify_location = _extract_code_location_from_text(candidate_text)
                if verify_location:
                    verify_location_authoritative = True
                    break
        if not verify_location:
            for fid in verify_ids:
                norm = _normalize_finding_id(fid) or fid
                verify_location = queue_location_by_id.get(norm, "")
                if verify_location:
                    break
        if not verify_location and record:
            candidate = str(record.get("location", "") or "")
            if _looks_like_code_location(candidate) and not _is_test_or_mock_location(candidate):
                verify_location = candidate
        if not verify_location:
            verify_location = _inventory_location_for_ids(
                scratchpad,
                verify_ids,
                inventory_location_by_id,
            )
        if verify_location and len(verify_ids) == 1 and verify_location_authoritative and not is_chain_report:
            if not location:
                location = verify_location
            else:
                loc_files = {
                    Path(m.group(0).split(":", 1)[0].replace("\\", "/")).name.lower()
                    for m in re.finditer(
                        r"[A-Za-z0-9_./-]+\.(?:cairo|move|hpp|cpp|tsx|jsx|sol|rs|go|py|cc|ts|js|vy|c|h)",
                        location,
                        re.IGNORECASE,
                    )
                }
                verify_files_seen = {
                    Path(m.group(0).split(":", 1)[0].replace("\\", "/")).name.lower()
                    for m in re.finditer(
                        r"[A-Za-z0-9_./-]+\.(?:cairo|move|hpp|cpp|tsx|jsx|sol|rs|go|py|cc|ts|js|vy|c|h)",
                        verify_location,
                        re.IGNORECASE,
                    )
                }
                if verify_files_seen and loc_files and not (verify_files_seen & loc_files):
                    location = verify_location
                elif (
                    verify_files_seen
                    and (verify_files_seen & loc_files)
                    and re.search(r":L?\d", verify_location)
                    and not re.search(r":L?\d", location)
                ):
                    location = verify_location
        if not location and verify_location:
            location = verify_location
        verify_files = []
        for fid in verify_ids:
            vf = _verify_file_for_id(scratchpad, fid)
            verify_files.append(vf.name if vf.exists() else f"verify_{fid}.md")
        verify_statuses = []
        report_blocked = not bool(verify_files)
        verdict = ""
        for fid in verify_ids:
            vf = _verify_file_for_id(scratchpad, fid)
            txt = ""
            if vf.exists():
                try:
                    txt = _llm_norm(vf.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    txt = ""
            if not verdict and txt:
                verdict = _verifier_status_from_text(txt)
            missing = (not vf.exists()) or _is_evidence_missing_for_body(txt)
            verify_statuses.append({"file": f"verify_{fid}.md", "exists": vf.exists(), "evidence_missing": bool(missing)})
            if missing:
                report_blocked = True

        manifest_row = {
            "report_id": report_id,
            "finding_id": finding_id,
            "severity": sev_full,
            "title": title,
            "location": location,
            "evidence_tag": evidence_tag,
            "verify_file": verify_files[0] if verify_files else "",
            "verify_files": verify_files,
            "verify_statuses": verify_statuses,
            "description": desc,
            "poc_result": poc_result,
            "recommendation": rec,
            "report_blocked": bool(report_blocked),
        }
        grouped[shard].append(manifest_row)
        active_records.append({
            "report_id": report_id,
            "finding_id": primary_fid or finding_id,
            "severity": sev_full,
            "title": title,
            "location": location,
            "evidence": evidence_tag,
            "verdict": verdict or "CONFIRMED",
            "unresolved": bool((verdict or "").upper() in {"UNRESOLVED", "PARTIAL"}),
            "severity_adjustments": [],
            "absorbed_finding_ids": finding_ids if len(finding_ids) > 1 else [],
            "report_blocked": bool(report_blocked),
        })

    # --- Write manifests (same format as L1 _build_body_writer_manifests) ---
    manifests: dict[str, dict] = {}
    for shard, rows in grouped.items():
        if not rows:
            continue
        cap = _BODY_SHARD_CAPS.get(shard, 30)
        if len(rows) <= cap:
            manifests[shard] = {"shard": shard, "findings": rows}
        else:
            n = len(rows)
            n_shards = (n + cap - 1) // cap
            chunk = (n + n_shards - 1) // n_shards
            for i in range(n_shards):
                suffix = chr(ord("a") + i)
                slice_rows = rows[i * chunk : (i + 1) * chunk]
                if not slice_rows:
                    continue
                name = f"{shard}_{suffix}"
                manifests[name] = {"shard": name, "findings": slice_rows}

    out_dir = scratchpad / "body_manifests"
    try:
        out_dir.mkdir(exist_ok=True)
        for old in out_dir.glob("report_*.json"):
            if old.stem not in manifests:
                try:
                    old.unlink()
                except Exception:
                    pass
                for stale in (
                    scratchpad / f"{old.stem}.md",
                    scratchpad / f"{old.stem}_assignments.md",
                ):
                    try:
                        stale.unlink(missing_ok=True)
                    except Exception:
                        pass
        for name, m in manifests.items():
            (out_dir / f"{name}.json").write_text(
                json.dumps(m, indent=2),
                encoding="utf-8",
            )
        excluded = _parse_report_index_excluded_records(scratchpad)
        (scratchpad / "report_records.json").write_text(
            json.dumps(
                {
                    "schema_version": "plamen.report_records.v1",
                    "source": "report_index.md",
                    "active": active_records,
                    "excluded": excluded,
                    "consolidation_map": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        log = logging.getLogger("plamen.mechanical")
        log.warning("_build_sc_body_writer_manifests: manifest/records write failed: %s", exc)
    return manifests


def _write_mechanical_report_tier(scratchpad: Path, phase_name: str) -> int:
    """Write tier markdown from verified records; no LLM report writer needed.

    Phase A D1/D2 fix: emit per-severity `## {Severity} Findings` H2 headers
    inside the tier file so `_extract_h2_section` in the assembler routes
    each finding to the correct section. Pre-fix, the tier file had only an
    H1 title and the assembler couldn't split — every C/H finding ended up
    pulled under the Medium section.
    """
    assignments, _source = get_tier_assignments(scratchpad)
    records_by_report_id: dict[str, dict] = {}
    records_path = scratchpad / "report_records.json"
    if records_path.exists():
        try:
            records = json.loads(records_path.read_text(encoding="utf-8"))
            for row in records.get("active", []):
                rid = row.get("report_id")
                if rid:
                    records_by_report_id[rid] = row
        except Exception:
            records_by_report_id = {}
    if records_by_report_id:
        assignments = [
            {
                "report_id": rid,
                "finding_id": row.get("finding_id", ""),
                "severity": severity_letter_from_name(row.get("severity", "")),
                "absorbed_finding_ids": row.get("absorbed_finding_ids") or [],
                "title": row.get("title", ""),
                "evidence": row.get("evidence", ""),
            }
            for rid, row in sorted(
                records_by_report_id.items(),
                key=lambda item: (
                    {"C": 0, "H": 1, "M": 2, "L": 3, "I": 4}.get(item[0][0], 99),
                    item[0],
                ),
            )
        ]
    if not assignments:
        return 0
    queue_rows = {
        (r.get("finding id") or "").strip(): r
        for r in parse_verification_queue_rows(scratchpad)
        if (r.get("finding id") or "").strip()
    }
    if phase_name == "report_critical_high":
        selected = [a for a in assignments if a["severity"] in ("C", "H")]
        filename = "report_critical_high.md"
        title = "# Critical and High Findings"
        per_sev_headers = (("C", "Critical Findings"), ("H", "High Findings"))
    elif (_shard_m := re.match(r"^report_(critical_high|medium|low_info)_[a-z]$", phase_name)):
        _shard_tier = _shard_m.group(1)
        shards = ensure_report_tier_shards(scratchpad, _shard_tier)
        selected = shards.get(phase_name, [])
        filename = f"{phase_name}.md"
        _titles_map = {
            "critical_high": ("# Critical and High Findings", (("C", "Critical Findings"), ("H", "High Findings"))),
            "medium": ("# Medium Findings", (("M", "Medium Findings"),)),
            "low_info": ("# Low and Informational Findings", (("L", "Low Findings"), ("I", "Informational Findings"))),
        }
        title, per_sev_headers = _titles_map[_shard_tier]
    elif phase_name == "report_medium":  # SC unsharded medium tier
        selected = [a for a in assignments if a["severity"] == "M"]
        filename = "report_medium.md"
        title = "# Medium Findings"
        per_sev_headers = (("M", "Medium Findings"),)
    elif phase_name == "report_low_info":
        selected = [a for a in assignments if a["severity"] in ("L", "I")]
        filename = "report_low_info.md"
        title = "# Low and Informational Findings"
        per_sev_headers = (("L", "Low Findings"), ("I", "Informational Findings"))
    else:
        return 0

    parts = [title, ""]
    unresolved_ids = _collect_judge_unresolved_ids(scratchpad)

    for sev_letter, header_text in per_sev_headers:
        bucket = [a for a in selected if a["severity"] == sev_letter]
        if not bucket:
            continue
        parts.extend([f"## {header_text}", ""])
        for a in bucket:
            fid = a.get("finding_id", "")
            source_ids = a.get("absorbed_finding_ids") or []
            if isinstance(source_ids, str):
                source_ids = [s.strip() for s in source_ids.split(",") if s.strip()]
            if not source_ids:
                source_ids = [fid] if fid else []
            primary_source = source_ids[0] if source_ids else fid
            target_evidence = str(a.get("evidence") or "").strip()
            if len(source_ids) > 1 and target_evidence:
                for source_id in source_ids:
                    try:
                        src_txt = _llm_norm(
                            _verify_file_for_id(scratchpad, source_id).read_text(
                                encoding="utf-8", errors="replace"
                            )
                        )
                    except Exception:
                        src_txt = ""
                    src_evidence = (
                        _field_from_markdown(src_txt, ("Evidence Tag", "Evidence Tags", "Evidence"))
                        or _field_from_markdown(src_txt, ("Preferred Tag", "Preferred Evidence"))
                        or ""
                    ).strip()
                    if src_evidence == target_evidence:
                        primary_source = source_id
                        break
            # D7 fix: also flag UNRESOLVED when the verifier verdict itself
            # is UNRESOLVED (not only when skeptic-judge ruled UNRESOLVED).
            # Body tag is a triage signal regardless of UNRESOLVED origin.
            verdicts: list[str] = []
            for source_id in source_ids:
                verify_path = _verify_file_for_id(scratchpad, source_id)
                try:
                    vtxt = _llm_norm(verify_path.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    vtxt = ""
                verdicts.append(_verifier_status_from_text(vtxt))
            is_unresolved = any(
                source_id in unresolved_ids for source_id in source_ids
            ) or any(v == "UNRESOLVED" for v in verdicts)
            section = _synth_report_section_from_verify(
                scratchpad,
                a.get("report_id", ""),
                primary_source,
                queue_rows.get(fid, {"finding id": fid, "severity": sev_letter}),
                is_unresolved,
            )
            if len(source_ids) > 1:
                evidence_lines = ["", "**Consolidated Evidence Sources**:"]
                for source_id in source_ids:
                    evidence_lines.append(f"- verify_{source_id}.md")
                section = section.rstrip() + "\n" + "\n".join(evidence_lines) + "\n"
            parts.append(section)
            parts.append("")

    if not selected:
        write_report_tier_placeholder(scratchpad, filename, "0 findings assigned in report_index.md")
    else:
        (scratchpad / filename).write_text("\n".join(parts).strip() + "\n", encoding="utf-8")
    return len(selected)


def estimate_rate_limit_wait_seconds(stdio_log: Path) -> Optional[int]:
    """Best-effort parse of a provider-advised retry/reset window."""
    if not stdio_log.exists():
        return None
    try:
        with stdio_log.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    patterns = [
        (re.compile(r"(?i)retry[-_ ]after[=:\s]+(\d+)\s*(seconds?|secs?|s)\b"), 1),
        (re.compile(r"(?i)retry[-_ ]after[=:\s]+(\d+)\s*(minutes?|mins?|m)\b"), 60),
        (re.compile(r"(?i)try again in\s+(\d+)\s*(seconds?|secs?|s)\b"), 1),
        (re.compile(r"(?i)try again in\s+(\d+)\s*(minutes?|mins?|m)\b"), 60),
        (re.compile(r"(?i)wait\s+(\d+)\s*(seconds?|secs?|s)\b"), 1),
        (re.compile(r"(?i)wait\s+(\d+)\s*(minutes?|mins?|m)\b"), 60),
    ]
    for rx, mult in patterns:
        m = rx.search(tail)
        if m:
            try:
                return int(m.group(1)) * mult
            except Exception:
                pass

    m = re.search(r"(?i)resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", tail)
    if m:
        try:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = m.group(3).lower()
            if hour == 12:
                hour = 0
            if ampm == "pm":
                hour += 12
            now = datetime.now().astimezone()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            return int((target - now).total_seconds())
        except Exception:
            return None

    return None


def write_hibernation_marker(scratchpad: Path, phase_name: str,
                             wake_at_utc: datetime, attempt: int) -> None:
    marker = scratchpad / ".hibernating"
    marker.write_text(json.dumps({
        "wake_at_utc": wake_at_utc.astimezone(timezone.utc).isoformat(),
        "last_phase": phase_name,
        "attempt_count": attempt,
    }, indent=2), encoding="utf-8")


def hibernation_disabled() -> bool:
    """Return whether rate-limit auto-sleep is disabled.

    Default is disabled. A pipeline should return control to the operator on
    rate limit instead of planting `.hibernating` and fighting manual resumes.
    Opt in explicitly with `PLAMEN_HIBERNATE=1`.
    """
    if os.environ.get("PLAMEN_NO_HIBERNATE", "").lower() in {
        "1", "true", "yes", "on"
    }:
        return True
    return os.environ.get("PLAMEN_HIBERNATE", "").lower() not in {
        "1", "true", "yes", "on"
    }


def maybe_resume_hibernation(scratchpad: Path) -> Optional[int]:
    """Handle an existing hibernation marker before any phase work starts."""
    if hibernation_disabled():
        marker = scratchpad / ".hibernating"
        if marker.exists():
            marker.unlink(missing_ok=True)
            print("[no-sleep] cleared .hibernating marker", file=sys.stderr)
        return None

    marker = scratchpad / ".hibernating"
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        wake_at = datetime.fromisoformat(data["wake_at_utc"])
        if wake_at.tzinfo is None:
            wake_at = wake_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
    except Exception:
        marker.unlink(missing_ok=True)
        return None

    if wake_at > now:
        remaining = int((wake_at - now).total_seconds())
        mins = max(1, remaining // 60)
        print("\n" + "=" * 60, file=sys.stderr)
        print("Pipeline HIBERNATING -- waiting for provider reset window.",
              file=sys.stderr)
        print(f"  Wait ~{mins} minute(s), then re-run the same command.",
              file=sys.stderr)
        print("  To skip the wait:  add --force to the command",
              file=sys.stderr)
        print("  All progress is saved -- resumes from last completed phase.",
              file=sys.stderr)
        print("=" * 60 + "\n", file=sys.stderr)
        return EXIT_HIBERNATING

    marker.unlink(missing_ok=True)
    return None


def maybe_hibernate_on_rate_limit(scratchpad: Path, phase_name: str,
                                  attempt: int) -> Optional[int]:
    if hibernation_disabled():
        return None

    stdio = scratchpad / f"_stdio_{phase_name}.log"
    wait_s = estimate_rate_limit_wait_seconds(stdio)
    if wait_s is not None and wait_s > 300:
        wake_at = datetime.now(timezone.utc) + timedelta(seconds=wait_s)
        write_hibernation_marker(scratchpad, phase_name, wake_at, attempt)
        print("\n" + "=" * 60, file=sys.stderr)
        print("Pipeline HIBERNATING -- long rate-limit/reset window detected.",
              file=sys.stderr)
        print(f"  Wake-at UTC: {wake_at.isoformat()}", file=sys.stderr)
        print("  To skip the wait:  re-run with --force", file=sys.stderr)
        print("  All progress is saved -- resumes from last completed phase.",
              file=sys.stderr)
        print("=" * 60 + "\n", file=sys.stderr)
        return EXIT_HIBERNATING
    return None


def write_report_tier_placeholder(scratchpad: Path, tier_filename: str,
                                  reason: str) -> None:
    p = scratchpad / tier_filename
    p.write_text(
        f"# Report Tier: N/A\n\n"
        f"No findings were assigned to this tier.\n\n"
        f"- Reason: {reason}\n"
        f"- Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8",
    )


def _write_location_recovery_skip(scratchpad: Path, reason: str) -> None:
    (scratchpad / "location_recovery.md").write_text(
        "# Location Recovery\n\n"
        f"SKIPPED: {reason}\n",
        encoding="utf-8",
    )


def _location_recovery_needed(scratchpad: Path, project_root: str) -> tuple[bool, str]:
    records = _validate_inventory_evidence(scratchpad, project_root)
    bad = [
        fid for fid, r in records.items()
        if r.get("location_status") not in ("OK", "RECOVERED_BASENAME")
    ]
    if not bad:
        return False, "all inventory locations resolve mechanically"
    return True, f"{len(bad)} unresolved inventory location(s)"


def _apply_location_recovery(scratchpad: Path, project_root: str) -> list[str]:
    """Apply `location_recovery.md` rows with verdict RECOVERED."""
    p = scratchpad / "location_recovery.md"
    inv = scratchpad / "findings_inventory.md"
    if not p.exists() or not inv.exists():
        return []
    try:
        text = _llm_norm(p.read_text(encoding="utf-8", errors="replace"))
        inv_text = _llm_norm(inv.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    headers, rows = _parse_markdown_table(text, ["finding id", "verdict", "new location"])
    if not headers:
        return []
    keys = [_norm_key(h) for h in headers]
    source_index = _project_source_index(project_root)
    applied: list[str] = []
    new_text = inv_text
    for row in rows:
        d = {keys[i]: row[i].strip() for i in range(min(len(keys), len(row)))}
        fid = _normalize_finding_id(d.get("finding id", "")) or d.get("finding id", "")
        verdict = d.get("verdict", "").upper()
        loc = d.get("new location", "")
        if not fid or "RECOVERED" not in verdict or not loc:
            continue
        st, resolved, _reason = _resolve_inventory_location(project_root, source_index, loc)
        if st not in ("OK", "RECOVERED_BASENAME"):
            continue
        new_text = _replace_inventory_location(new_text, fid, resolved or loc)
        applied.append(fid)
    if applied and new_text != inv_text:
        inv.write_text(new_text, encoding="utf-8")
        _validate_inventory_evidence(scratchpad, project_root)
    return applied
