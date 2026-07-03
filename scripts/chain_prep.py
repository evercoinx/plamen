"""Phase 4c chain prep — mechanical producers that BOUND the chain agents' work.

The chain phase hung 50 min on a live audit because Chain Agent 1's PHASE 1
grouping and Chain Agent 2's PHASE 2 matching are *unbounded* tasks — the
prompts say "exhaustively enumerate" with no finite candidate set. The chain
prompts ALREADY reference `chain_candidate_pairs.md` and `variable_finding_map.md`
("if present, evaluate ONLY these pairs") but no code ever produced them, so the
agents always ran the unbounded fallback.

This module builds the missing producers. Each is a pure mechanical pre-pass
(no LLM) that turns an open-ended "find everything" task into a finite,
completable candidate set:

  compute_chain_candidate_pairs  -> chain_candidate_pairs.md (+ _full.md)
      Bounds Agent 2 PHASE 2: pairs of findings sharing a state variable or a
      discriminative code identifier. The agent evaluates ONLY these.
  compute_variable_finding_map   -> variable_finding_map.md
      Supports Agent 2 variable-level matching: state var -> findings touching it.
  compute_enabler_baseline       -> enabler_results.md (STEP 0a pre-filled)
      Bounds Agent 1 PHASE 0: pre-extracts the dangerous-state candidate list
      from CONFIRMED/PARTIAL findings so the agent does not re-scan the inventory.

Design rules (match the plan's constraints):
  - Best-effort. Every public function catches its own exceptions and returns
    a summary dict. NEVER raises — a producer failure must degrade to the
    chain prompt's existing fallback path, not halt the pipeline.
  - Coverage-safe. The bounded `chain_candidate_pairs.md` is the top-N
    highest-signal pairs; the complete set is mirrored to
    `chain_candidate_pairs_full.md` (same belt+suspenders pattern as
    `dedup_candidate_pairs.md` / `_full.md`). Nothing is silently dropped.
  - Additive. No existing artifact is renamed or removed. `enabler_results.md`
    is overwritten, but only AFTER `_write_chain_passthrough_outputs` has
    written its stub safety-net, and the format stays compatible.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional


# The bounded file the chain agent evaluates directly. The full set goes to
# `chain_candidate_pairs_full.md`. The bound is split to guarantee STATE pairs
# (shared mutable state — the classic chain signal) get half the budget rather
# than being crowded out by multi-identifier TYPE pairs. 70 total is generous
# vs the chain prompt's own 50-pair fallback cap and completable inside Agent
# 2's 40-min budget; the tail is preserved in the full file + chain_iter2.
_BOUNDED_PAIR_CAP = 70
_BOUNDED_PER_TABLE = 35

_SEVERITY_RANK = {
    "critical": 4, "high": 3, "medium": 2, "low": 1, "informational": 0, "info": 0,
}

# Identifier tokens too common to be discriminative — appear in most findings
# of any Solidity audit, so a shared occurrence carries no pairing signal.
_STOPWORD_IDENTIFIERS = frozenset({
    "function", "functions", "address", "addresses", "contract", "contracts",
    "transfer", "transfers", "amount", "amounts", "balance", "balances",
    "require", "return", "returns", "external", "internal", "public", "private",
    "msgsender", "msgvalue", "uint256", "bytes32", "memory", "storage",
    "should", "would", "could", "value", "values", "result", "results",
    "caller", "callers", "called", "revert", "reverts", "reverted",
})

_DISCOVERY_FIELDS = (
    "discovery_steer",
    "missing_precondition",
    "precondition_type",
    "postconditions_created",
    "postcondition_types",
    "semantic_invariant",
    "branch_preconditions",
    "terminal_mechanism",
    "composition_candidates",
)

_DISCOVERY_GENERIC_TERMS = _STOPWORD_IDENTIFIERS | frozenset({
    "access", "accounting", "arithmetic", "authorization", "balance",
    "blocked", "branch", "branches", "callback", "candidate", "candidates",
    "category", "condition", "conditions", "configuration", "created",
    "creates", "effect", "effects", "enabled", "enables", "external",
    "flow", "flows", "guard", "guards", "impact", "invariant", "issue",
    "lifecycle", "match", "mechanism", "missing", "mode", "path", "paths",
    "permission", "precondition", "preconditions", "postcondition",
    "postconditions", "precision", "reachability", "rounding", "semantic",
    "state", "status", "terminal", "timing", "token", "tokens", "type",
    "types", "unit", "units", "value", "values", "write", "writes",
    "read", "reads",
})


# ---------------------------------------------------------------------------
# Shared parsing
# ---------------------------------------------------------------------------


def _load_inventory(scratchpad: Path) -> list[dict]:
    """Parse findings_inventory.md into entry dicts. [] on any failure."""
    inv = scratchpad / "findings_inventory.md"
    if not inv.exists():
        return []
    try:
        import importlib
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        parsers = importlib.import_module("plamen_parsers")
        return parsers._parse_inventory_chunk(inv) or []
    except Exception:
        return []


def _parse_state_write_map(scratchpad: Path) -> dict[str, set[str]]:
    """Parse state_write_map.md → {state_variable_name: {contract, ...}}.

    The file groups rows under `## Contract.sol` headers with a table
    `| State Variable | Writer Function | Write Site | Access Guard |`.
    Returns a map of bare variable name → set of contracts that declare it.
    Empty dict on any failure.
    """
    path = scratchpad / "state_write_map.md"
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    out: dict[str, set[str]] = {}
    current_contract = ""
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S+?)(?:\.sol|\.rs|\.move|\.go)?\s*$", line)
        if m:
            current_contract = m.group(1)
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("`") for c in line.split("|")[1:-1]]
        if not cells:
            continue
        var = cells[0]
        # skip header / separator rows
        if not var or var.lower() in ("state variable", "variable") or set(var) <= {"-", ":"}:
            continue
        # Strip mapping/index decoration: `refundInfos[externalId]` → `refundInfos`
        bare = re.sub(r"[\[\(].*$", "", var).strip()
        bare = re.sub(r"\s*\(.*$", "", bare).strip()
        if bare:
            out.setdefault(bare, set()).add(current_contract)
    return out


_IDENT_RE = re.compile(r"\b([a-z][A-Za-z0-9]{3,}|_[a-zA-Z0-9_]{3,})\b")


def _extract_identifiers(text: str) -> set[str]:
    """Extract candidate code identifiers (camelCase / _prefixed) from text.

    Drops common non-discriminative stopwords. Used to pair findings that
    discuss the same function / variable even when locations differ.
    """
    out: set[str] = set()
    for m in _IDENT_RE.finditer(text or ""):
        tok = m.group(1)
        norm = tok.lstrip("_").lower()
        if norm in _STOPWORD_IDENTIFIERS:
            continue
        # require some camelCase or underscore structure — discriminative
        if "_" in tok or re.search(r"[a-z][A-Z]", tok):
            out.add(tok)
    return out


def _entry_text(entry: dict, field: str) -> str:
    """Return a bounded string value from an inventory entry field."""
    try:
        val = entry.get(field, "")
    except Exception:
        return ""
    if val is None:
        return ""
    if isinstance(val, (list, tuple, set)):
        val = " ".join(str(x) for x in val)
    else:
        val = str(val)
    return re.sub(r"\s+", " ", val).strip()[:500]


def _discovery_text(entry: dict) -> str:
    return " ".join(_entry_text(entry, f) for f in _DISCOVERY_FIELDS).strip()


def _extract_discovery_terms(text: str) -> set[str]:
    """Concrete terms from optional discovery metadata.

    Mechanism/category words alone are intentionally ignored so fields like
    `Discovery Steer: arithmetic rounding` do not pair unrelated findings.
    """
    terms = {tok.lower() for tok in _extract_identifiers(text)}
    for m in re.finditer(r"`([A-Za-z_][A-Za-z0-9_]{3,})`", text or ""):
        terms.add(m.group(1).lower())
    for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]{4,})\b", text or ""):
        tok = m.group(1)
        norm = tok.lower()
        if "_" in tok or re.search(r"[a-z][A-Z]", tok) or re.search(r"\d", tok):
            terms.add(norm)
    return {
        t for t in terms
        if len(t) >= 4 and t not in _DISCOVERY_GENERIC_TERMS
    }


def _extract_finding_refs(text: str) -> set[str]:
    return {
        m.group(0).upper()
        for m in re.finditer(r"\b[A-Z][A-Z0-9]{0,10}-\d+\b", text or "", re.IGNORECASE)
    }


def _entry_aliases(entry: dict, final_id: str) -> set[str]:
    """All finding IDs that may identify this entry across pipeline stages."""
    aliases: set[str] = set()
    for val in (final_id, entry.get("local_id", "")):
        if val:
            aliases |= _extract_finding_refs(str(val))
    for sid in entry.get("source_ids", []) or []:
        aliases |= _extract_finding_refs(str(sid))
    return aliases or {str(final_id).upper()}


_LOC_RE = re.compile(
    r"([A-Za-z0-9_]+\.(?:sol|rs|move|go))\s*:?\s*L?(\d+)\s*(?:-\s*L?(\d+))?"
)
# Two findings co-located within this many lines are treated as a proximity
# pair (likely the same or an adjacent function). Bare same-file with no line
# proximity and no shared identifier is NOT a candidate — in a 3-contract
# codebase that would pair nearly everything with everything.
_PROXIMITY_LINES = 60


def _extract_contracts(location: str) -> set[str]:
    """Pull the set of source-file basenames a finding's Location touches."""
    return {m.group(1) for m in _LOC_RE.finditer(location or "")}


def _extract_locations(location: str) -> dict[str, list[tuple[int, int]]]:
    """Parse a Location field into {file: [(start_line, end_line), ...]}."""
    out: dict[str, list[tuple[int, int]]] = {}
    for m in _LOC_RE.finditer(location or ""):
        f = m.group(1)
        start = int(m.group(2))
        end = int(m.group(3)) if m.group(3) else start
        if end < start:
            start, end = end, start
        out.setdefault(f, []).append((start, end))
    return out


def _line_proximity(a_locs: dict[str, list[tuple[int, int]]],
                    b_locs: dict[str, list[tuple[int, int]]]) -> bool:
    """True if A and B touch the same file within _PROXIMITY_LINES lines."""
    for f, a_ranges in a_locs.items():
        b_ranges = b_locs.get(f)
        if not b_ranges:
            continue
        for (a0, a1) in a_ranges:
            for (b0, b1) in b_ranges:
                # gap between the two line ranges (0 if they overlap)
                gap = max(0, max(a0, b0) - min(a1, b1))
                if gap <= _PROXIMITY_LINES:
                    return True
    return False


def _finding_state_vars(entry: dict, state_vars: dict[str, set[str]]) -> set[str]:
    """State variables a finding touches — var names appearing word-bounded
    in its root cause or description."""
    blob = f"{entry.get('root_cause', '')} {entry.get('description', '')}"
    touched: set[str] = set()
    for var in state_vars:
        if re.search(rf"\b{re.escape(var)}\b", blob):
            touched.add(var)
    return touched


def _entry_id(entry: dict, idx: int) -> str:
    return str(entry.get("local_id") or f"INV-{idx:03d}").strip()


# ---------------------------------------------------------------------------
# Producer 1 — chain_candidate_pairs.md
# ---------------------------------------------------------------------------


def compute_chain_candidate_pairs(scratchpad: Path) -> dict:
    """Write chain_candidate_pairs.md (bounded) + chain_candidate_pairs_full.md.

    A pair (A, B) is a candidate when A and B share at least one signal:
      - a state variable (STATE Pairs table) — strongest signal
      - a discriminative code identifier (TYPE Pairs table)
      - the same source file with line ranges within 60 lines (TYPE Pairs)
      - optional discovery metadata with a concrete shared term or explicit
        finding reference; generic mechanism-only overlap is not a signal

    The bounded file holds the top _BOUNDED_PAIR_CAP pairs ranked by signal
    strength + combined severity (cross-class pairs prioritized). The full
    file holds every candidate. The chain agent evaluates ONLY the bounded
    file; chain_iter2 + composition_coverage cover the tail.
    """
    try:
        scratchpad = Path(scratchpad)
        entries = _load_inventory(scratchpad)
        if len(entries) < 2:
            return {"status": "skipped", "reason": "fewer than 2 findings",
                    "pairs": 0}
        state_vars = _parse_state_write_map(scratchpad)

        # Pre-compute per-finding signal sets.
        meta: list[dict] = []
        for idx, e in enumerate(entries, start=1):
            blob = f"{e.get('root_cause', '')} {e.get('description', '')} {e.get('title', '')}"
            discovery = _discovery_text(e)
            meta.append({
                "id": _entry_id(e, idx),
                "aliases": _entry_aliases(e, _entry_id(e, idx)),
                "severity": str(e.get("severity") or "Medium"),
                "sev_rank": _SEVERITY_RANK.get(str(e.get("severity") or "medium").strip().lower(), 2),
                "title": re.sub(r"\s+", " ", str(e.get("title") or "")).strip()[:90],
                "location": str(e.get("location") or ""),
                "locs": _extract_locations(str(e.get("location") or "")),
                "state": _finding_state_vars(e, state_vars),
                "idents": _extract_identifiers(blob),
                "discovery_terms": _extract_discovery_terms(discovery),
                "discovery_refs": _extract_finding_refs(discovery),
            })

        state_pairs: list[dict] = []
        type_pairs: list[dict] = []
        for i in range(len(meta)):
            for j in range(i + 1, len(meta)):
                a, b = meta[i], meta[j]
                if a["id"] == b["id"]:
                    continue
                shared_state = a["state"] & b["state"]
                shared_ident = a["idents"] & b["idents"]
                proximate = _line_proximity(a["locs"], b["locs"])
                shared_discovery = a["discovery_terms"] & b["discovery_terms"]
                explicit_discovery_ref = (
                    bool(a["discovery_refs"] & b["aliases"])
                    or bool(b["discovery_refs"] & a["aliases"])
                )
                # A candidate needs a REAL signal: shared state variable,
                # shared discriminative identifier, line proximity, or a
                # concrete discovery term/reference. Bare
                # same-file (no proximity) is NOT a signal — in a 3-contract
                # codebase that pairs everything. Generic mechanism-only
                # discovery overlap is also not a signal.
                if not (
                    shared_state or shared_ident or proximate
                    or shared_discovery or explicit_discovery_ref
                ):
                    continue
                cross_class = a["sev_rank"] != b["sev_rank"]
                score = (
                    3 * len(shared_state)
                    + 2 * len(shared_ident)
                    + 2 * len(shared_discovery)
                    + (2 if explicit_discovery_ref else 0)
                    + (1 if proximate else 0)
                    + (1 if cross_class else 0)
                    + (a["sev_rank"] + b["sev_rank"]) / 10.0
                )
                row = {
                    "a": a["id"], "b": b["id"], "score": score,
                    "shared_state": sorted(shared_state),
                    "shared_ident": sorted(shared_ident)[:4],
                    "shared_discovery": sorted(shared_discovery)[:4],
                    "discovery_ref": explicit_discovery_ref,
                    "a_sev": a["severity"], "b_sev": b["severity"],
                    "a_title": a["title"], "b_title": b["title"],
                }
                if shared_state:
                    state_pairs.append(row)
                else:
                    type_pairs.append(row)

        state_pairs.sort(key=lambda r: r["score"], reverse=True)
        type_pairs.sort(key=lambda r: r["score"], reverse=True)
        all_pairs = state_pairs + type_pairs

        def _fmt_table(title: str, rows: list[dict]) -> list[str]:
            out = [
                f"### {title}",
                "",
                "| Finding A | A Severity | Finding B | B Severity | Shared Signal |",
                "|-----------|-----------|-----------|-----------|---------------|",
            ]
            for r in rows:
                parts: list[str] = []
                if r["shared_state"]:
                    parts.append("state: " + ", ".join(r["shared_state"][:3]))
                if r["shared_ident"]:
                    parts.append("ident: " + ", ".join(r["shared_ident"][:3]))
                if r["shared_discovery"]:
                    parts.append("discovery: " + ", ".join(r["shared_discovery"][:3]))
                if r["discovery_ref"]:
                    parts.append("discovery: explicit finding reference")
                sig = "; ".join(parts) if parts else "co-located (same file)"
                out.append(
                    f"| {r['a']} | {r['a_sev']} | {r['b']} | {r['b_sev']} | {sig} |"
                )
            if not rows:
                out.append("| (none) | - | - | - | - |")
            out.append("")
            return out

        # Bounded file: guarantee each table up to _BOUNDED_PER_TABLE of its
        # own top-scored pairs, then top up from the larger pool to the total
        # cap so a thin table doesn't waste budget.
        bounded_state = state_pairs[:_BOUNDED_PER_TABLE]
        bounded_type = type_pairs[:_BOUNDED_PER_TABLE]
        remaining = _BOUNDED_PAIR_CAP - len(bounded_state) - len(bounded_type)
        if remaining > 0:
            leftovers = sorted(
                state_pairs[len(bounded_state):] + type_pairs[len(bounded_type):],
                key=lambda r: r["score"], reverse=True,
            )[:remaining]
            bounded_state += [r for r in leftovers if r["shared_state"]]
            bounded_type += [r for r in leftovers if not r["shared_state"]]
        bounded = bounded_state + bounded_type
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")

        header = [
            "# Chain Candidate Pairs",
            "",
            "**Status**: MECHANICAL_PREFILTER",
            f"**Generated At**: {stamp}",
            f"**Total candidate pairs**: {len(all_pairs)} "
            f"(STATE: {len(state_pairs)}, TYPE: {len(type_pairs)})",
            f"**Bounded set below**: top {len(bounded)} by signal strength. "
            "Chain Agent 2 evaluates ONLY the pairs in this file. The complete "
            "set is in chain_candidate_pairs_full.md; chain_iter2 covers the tail.",
            "",
        ]
        body = (
            _fmt_table("STATE Pairs", bounded_state)
            + _fmt_table("TYPE Pairs", bounded_type)
        )
        (scratchpad / "chain_candidate_pairs.md").write_text(
            "\n".join(header + body), encoding="utf-8"
        )

        full_header = [
            "# Chain Candidate Pairs — Full Set",
            "",
            "**Status**: MECHANICAL_PREFILTER_FULL",
            f"**Generated At**: {stamp}",
            f"**Total candidate pairs**: {len(all_pairs)}",
            "",
        ]
        full_body = (
            _fmt_table("STATE Pairs", state_pairs)
            + _fmt_table("TYPE Pairs", type_pairs)
        )
        (scratchpad / "chain_candidate_pairs_full.md").write_text(
            "\n".join(full_header + full_body), encoding="utf-8"
        )
        return {
            "status": "ok",
            "pairs": len(all_pairs),
            "bounded": len(bounded),
            "state_pairs": len(state_pairs),
            "type_pairs": len(type_pairs),
        }
    except Exception as exc:  # never raise — best-effort
        return {"status": "error", "error": str(exc), "pairs": 0}


# ---------------------------------------------------------------------------
# Producer 2 — variable_finding_map.md
# ---------------------------------------------------------------------------


def compute_variable_finding_map(scratchpad: Path) -> dict:
    """Write variable_finding_map.md: state variable → findings touching it.

    Lets Chain Agent 2 do variable-level matching without the grep fallback.
    """
    try:
        scratchpad = Path(scratchpad)
        entries = _load_inventory(scratchpad)
        state_vars = _parse_state_write_map(scratchpad)
        if not entries or not state_vars:
            # Still write a header so the prompt sees a real (if empty) file.
            (scratchpad / "variable_finding_map.md").write_text(
                "# Variable → Finding Map\n\n"
                "**Status**: MECHANICAL_PREFILTER\n\n"
                "No state variables or no findings parsed — Chain Agent 2 "
                "should fall back to grep-based variable matching.\n",
                encoding="utf-8",
            )
            return {"status": "skipped", "reason": "no vars or no findings",
                    "variables": 0}

        var_to_findings: dict[str, list[str]] = {}
        for idx, e in enumerate(entries, start=1):
            fid = _entry_id(e, idx)
            for var in _finding_state_vars(e, state_vars):
                var_to_findings.setdefault(var, []).append(fid)

        lines = [
            "# Variable → Finding Map",
            "",
            "**Status**: MECHANICAL_PREFILTER",
            f"**Generated At**: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
            "",
            "| State Variable | Contract(s) | Findings Touching It |",
            "|----------------|-------------|----------------------|",
        ]
        rows = 0
        for var in sorted(var_to_findings):
            fids = sorted(set(var_to_findings[var]))
            if not fids:
                continue
            contracts = ", ".join(sorted(state_vars.get(var, set()))) or "-"
            lines.append(f"| {var} | {contracts} | {', '.join(fids)} |")
            rows += 1
        if rows == 0:
            lines.append("| (no variable touched by 2+ findings) | - | - |")
        (scratchpad / "variable_finding_map.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        return {"status": "ok", "variables": rows}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "variables": 0}


# ---------------------------------------------------------------------------
# Producer 3 — enabler_results.md STEP 0a baseline
# ---------------------------------------------------------------------------


_UNVERIFIED_VERDICTS = frozenset({
    "NEEDS_VERIFICATION", "NEEDSVERIFICATION", "NEEDS VERIFICATION",
    "LOW_CONFIDENCE", "LOW CONFIDENCE", "UNVERIFIED", "UNCONFIRMED",
})


def _is_unverified_enabler(entry: dict) -> bool:
    """A mechanically-derived, individually-weak candidate (ENUMGAP / deriver /
    enumeration-obligation exploration) usable as a LOW-CONFIDENCE chain enabler.

    Identified by an unverified verdict OR an ENUMGAP/NEXP source-id tag. These
    never enter the CONFIRMED/PARTIAL/CONTESTED dangerous-state baseline (they
    are unproven at chain time), but a postcondition they CREATE can still enable
    another finding into a compound chain. Any such chain is itself sent to
    verification, so precision is preserved by the existing verify filter."""
    verdict = str(entry.get("verdict") or "").strip().upper()
    if verdict in _UNVERIFIED_VERDICTS:
        return True
    blob = " ".join(str(s) for s in (entry.get("source_ids") or []))
    return bool(re.search(r"\bENUMGAP\b|\bNEXP-\d+", blob, re.IGNORECASE))


def compute_enabler_baseline(scratchpad: Path) -> dict:
    """Overwrite enabler_results.md with a STEP 0a dangerous-state baseline.

    Pre-extracts every CONFIRMED/PARTIAL/CONTESTED finding into the STEP 0a
    table so Chain Agent 1 does NOT re-scan the inventory — it takes this
    finite list as given and fills the STEP 0b 5-actor reachability table.

    ALSO surfaces mechanically-derived ENUMGAP/deriver candidates (unverified)
    as a separate LOW-CONFIDENCE potential-enabler table, carrying any pre/post
    metadata they stamped. This lets a weak candidate act as a chain enabler at
    chain time (which runs BEFORE verify); the resulting chain is a HYPOTHESIS
    that goes to verification like every chain, so a spurious enabler yields a
    spurious chain that verify refutes — precision-bounded, recall-positive.

    Runs AFTER `_write_chain_passthrough_outputs` (which writes a stub
    enabler_results.md). If this producer fails, the stub remains and the
    chain phase still gate-passes — degradation, not halt.
    """
    try:
        scratchpad = Path(scratchpad)
        entries = _load_inventory(scratchpad)
        dangerous = [
            e for e in entries
            if str(e.get("verdict") or "").strip().upper()
            in ("CONFIRMED", "PARTIAL", "CONTESTED")
        ]
        unverified = [e for e in entries if _is_unverified_enabler(e)][:40]
        if not dangerous and not unverified:
            return {"status": "skipped", "reason": "no CONFIRMED/PARTIAL findings",
                    "states": 0}

        lines = [
            "# Enabler Results",
            "",
            "**Status**: MECHANICAL_BASELINE_STEP0A",
            f"**Generated At**: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
            "",
            "Chain Agent 1: STEP 0a (dangerous-state extraction) is PRE-FILLED "
            "below from CONFIRMED/PARTIAL/CONTESTED findings. Do NOT re-scan the "
            "inventory for dangerous states — take this list as the complete "
            "STEP 0a set. Your job is STEP 0b: for each row, fill the 5-actor "
            "reachability table, and STEP 0c cross-state interactions.",
            "",
            "## STEP 0a: Dangerous States (mechanical baseline)",
            "",
            "| Finding ID | Severity | Location | Dangerous State (root cause) |",
            "|------------|----------|----------|------------------------------|",
        ]
        if dangerous:
            for idx, e in enumerate(dangerous, start=1):
                fid = _entry_id(e, idx)
                sev = str(e.get("severity") or "Medium")
                loc = re.sub(r"\s+", " ", str(e.get("location") or "UNKNOWN")).replace("|", "/")
                rc = re.sub(r"\s+", " ", str(e.get("root_cause") or e.get("title") or "")).replace("|", "/")
                lines.append(f"| {fid} | {sev} | {loc[:120]} | {rc[:200]} |")
        else:
            lines.append("| (none CONFIRMED/PARTIAL/CONTESTED at chain time) | - | - | - |")

        # Low-confidence potential enablers: mechanically-derived ENUMGAP/deriver
        # candidates. Unverified at chain time, so NOT in the dangerous-state
        # baseline, but a postcondition they CREATE can enable another finding.
        # Chain Agent 1/2 MAY use these as candidate enablers (postcondition
        # providers) — any resulting chain is itself sent to verification, which
        # refutes spurious enabler chains. Recall-safe, precision-bounded.
        lines += [
            "",
            "## STEP 0a-LC: Low-Confidence Potential Enablers (unverified — ENUMGAP/derivers)",
            "",
            "These are mechanically-derived, individually-WEAK candidates "
            "(verdict NEEDS_VERIFICATION). They are NOT proven dangerous states. "
            "Use them ONLY as candidate ENABLERS: if a postcondition below "
            "creates the precondition another finding needs, build a "
            "LOW-CONFIDENCE chain hypothesis. Every such chain MUST go to "
            "verification — a spurious enabler yields a chain that verify "
            "refutes, so precision is preserved. Do NOT promote these to the "
            "dangerous-state baseline.",
            "",
            "| Finding ID | Severity | Location | Postcondition Created (type) | Missing Precondition (type) |",
            "|------------|----------|----------|------------------------------|-----------------------------|",
        ]
        if unverified:
            for idx, e in enumerate(unverified, start=1):
                fid = _entry_id(e, idx)
                sev = str(e.get("severity") or "Low")
                loc = re.sub(r"\s+", " ", str(e.get("location") or "UNKNOWN")).replace("|", "/")
                post = re.sub(r"\s+", " ", str(e.get("postconditions_created") or "")).replace("|", "/")
                post_t = re.sub(r"\s+", " ", str(e.get("postcondition_types") or "")).replace("|", "/")
                pre = re.sub(r"\s+", " ", str(e.get("missing_precondition") or "")).replace("|", "/")
                pre_t = re.sub(r"\s+", " ", str(e.get("precondition_type") or "")).replace("|", "/")
                if not post and not pre:
                    # No stamped metadata — fall back to the root cause so the
                    # candidate is still visible/matchable by prose.
                    post = re.sub(r"\s+", " ", str(e.get("root_cause") or e.get("title") or "")).replace("|", "/")
                post_cell = (f"{post[:160]}" + (f" ({post_t[:24]})" if post_t else "")) or "-"
                pre_cell = (f"{pre[:120]}" + (f" ({pre_t[:24]})" if pre_t else "")) if pre else "-"
                lines.append(f"| {fid} | {sev} | {loc[:120]} | {post_cell or '-'} | {pre_cell} |")
        else:
            lines.append("| (none) | - | - | - | - |")

        lines += [
            "",
            "## STEP 0b: 5-Actor Reachability (Chain Agent 1 fills this)",
            "",
            "For each dangerous state above, enumerate which of the 5 actor "
            "categories can reach it: external attacker (permissionless), "
            "semi-trusted role, natural operation, external event, user action "
            "sequence. Create [EN-N] findings for reachable-but-uncovered paths.",
            "",
            "## STEP 0c: Cross-State Interactions (Chain Agent 1 fills this)",
            "",
        ]
        (scratchpad / "enabler_results.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        return {"status": "ok", "states": len(dangerous),
                "low_confidence_enablers": len(unverified)}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "states": 0}


# ---------------------------------------------------------------------------
# Driver entry point — runs all three, best-effort
# ---------------------------------------------------------------------------


def run_chain_prep(scratchpad: Path) -> dict:
    """Run all three producers. Never raises. Returns a per-producer summary."""
    scratchpad = Path(scratchpad)
    return {
        "candidate_pairs": compute_chain_candidate_pairs(scratchpad),
        "variable_map": compute_variable_finding_map(scratchpad),
        "enabler_baseline": compute_enabler_baseline(scratchpad),
    }


__all__ = [
    "compute_chain_candidate_pairs",
    "compute_variable_finding_map",
    "compute_enabler_baseline",
    "run_chain_prep",
]
