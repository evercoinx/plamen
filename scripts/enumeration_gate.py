"""G1 + G2 — mechanical enumeration-coverage gate (ecosystem-agnostic).

The pipeline's dominant recall failure is under-enumeration: an agent analyzes a
function that writes/transfers a state symbol, reasons about ONE consumer, and
writes "SAFE" without addressing the OTHER functions that reference the same
symbol. The deep-research pass showed the only proven fix is grounding the
required set in an EXTERNAL static-analysis graph (LLMxCPG) and gating the
verdict on covering it — not self-critique or debate.

This module reads the unified `_mechanical_graph.json` (emitted by the Slither /
SCIP / Move / DAML graph providers) and:

  G1 `compute_enumeration_obligations` — for each inventory finding, derives the
     set of CO-REFERENCING functions of the symbols its function touches (the
     functions the finding's analysis ought to address). Bounded (per the
     chain_prep precedent) so it never floods.

  G2 `validate_enumeration_coverage` — mechanically diffs each obligation's
     required co-referencers against the finding's own prose. An un-addressed
     co-referencer is a COVERAGE GAP: it is appended to findings_inventory as a
     low-confidence `ENUMGAP` candidate (append-only, idempotent) so the existing
     verify-the-positives filter adjudicates it. Recall-safe: never drops, never
     halts; if the mechanical graph is absent the gate is a no-op (advisory).

No-overfit: pure graph mechanics, names no protocol.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from plamen_mechanical import _inventory_blocks  # type: ignore
except Exception:  # pragma: no cover
    _inventory_blocks = None  # type: ignore

# Bounds (mirror chain_prep's recall-safe bounding so the gate can't flood).
_MAX_VARS_PER_FINDING = 5      # only the few symbols a finding most directly touches
_MAX_COREFS_PER_VAR = 6       # cap co-referencers enumerated per symbol
_SKIP_VAR_REF_THRESHOLD = 25  # a symbol referenced by >25 fns is too common to gate on
_MAX_ENUMGAP_PER_RUN = 40     # global cap on emitted candidates


def _chain_metadata_lines(postcondition: str = "", postcondition_type: str = "",
                          missing_precondition: str = "", precondition_type: str = "") -> list[str]:
    """Render generic, chain-matchable pre/post metadata in finding-output-format
    field names so the chain phase can use an ENUMGAP candidate as an enabler.

    These are the SAME optional fields the inventory parser ingests
    (`Postconditions Created` / `Postcondition Types` / `Missing Precondition` /
    `Precondition Type`) and that chain_prep / Chain Agent match on. A deriver
    candidate is individually weak (NEEDS_VERIFICATION), but stamping the state/
    access it CREATES (postcondition) or NEEDS (missing precondition) lets it
    pair with another finding into a compound CHAIN hypothesis — which is then
    itself sent to verification. Empty fields are omitted. Recall-safe; generic
    (type tags only, no protocol names)."""
    out: list[str] = []
    if postcondition:
        out.append(f"**Postconditions Created**: {postcondition}")
        if postcondition_type:
            out.append(f"**Postcondition Types**: {postcondition_type}")
    if missing_precondition:
        out.append(f"**Missing Precondition**: {missing_precondition}")
        if precondition_type:
            out.append(f"**Precondition Type**: {precondition_type}")
    return out


def _append_inventory_blocks(inv_text: str, hdr: str, appended: list[str]) -> str:
    """Append ENUMGAP/exploration blocks to inventory text, separator-safe.

    `inv_text.rstrip()` strips the trailing newline of the prior block. When a
    sibling deriver already created the shared section, `hdr` is "" — without a
    separator the first appended '### Finding' header glues onto the previous
    block's last line and becomes invisible to `^### Finding` parsers. Inserting
    a blank-line separator when `hdr` is empty guarantees the header is always
    line-anchored. Recall-safe: never drops blocks.
    """
    return inv_text.rstrip() + (hdr if hdr else "\n\n") + "\n".join(appended) + "\n"


def _load_graph(scratchpad: Path) -> dict | None:
    p = scratchpad / "_mechanical_graph.json"
    if not p.exists():
        return None
    try:
        g = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(g, dict) or "var_refs" not in g or "functions" not in g:
        return None
    return g


def _bare_from_descriptor(d: str) -> str:
    """A descriptor is 'BareName (file:line)' or 'file:line' — return the bare
    name (or the descriptor itself when it's a plain location)."""
    return (d.split("(", 1)[0].strip() or d).strip()


def _fn_at_location(graph: dict, location: str) -> str | None:
    """Map a finding location (e.g. 'core/QOrg.sol:L330') to the enclosing
    function: same file basename, nearest function whose line <= the cited line."""
    m = re.search(r"([A-Za-z0-9_./\\-]+)\D*:?L?(\d+)", location or "")
    if not m:
        return None
    fbase = Path(m.group(1).replace("\\", "/")).name.lower()
    fline = int(m.group(2))
    best, best_line = None, -1
    for fk, info in graph["functions"].items():
        loc = str(info.get("loc", ""))
        lm = re.search(r"([A-Za-z0-9_./\\-]+)\D*:?L?(\d+)", loc)
        if not lm:
            continue
        if Path(lm.group(1).replace("\\", "/")).name.lower() != fbase:
            continue
        fnl = int(lm.group(2))
        # the ENCLOSING function = highest declaration line at-or-before the
        # cited line. (A forward slack would wrongly grab the NEXT function when
        # two are adjacent.)
        if fnl <= fline and fnl > best_line:
            best, best_line = fk, fnl
    return best


def compute_enumeration_obligations(scratchpad: Path) -> int:
    """G1. Derive per-finding co-reference obligations from the graph. Writes
    `enumeration_obligations.md` + `_enumeration_obligations.json`. Returns the
    obligation count. Never raises; a no-op when the graph or inventory is absent."""
    scratchpad = Path(scratchpad)
    graph = _load_graph(scratchpad)
    inv = scratchpad / "findings_inventory.md"
    if graph is None or _inventory_blocks is None or not inv.exists():
        return 0
    try:
        blocks = _inventory_blocks(inv.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0

    var_refs = graph["var_refs"]
    # invert: bare fn name -> set(var keys it references)
    fn_to_vars: dict[str, set] = {}
    for vk, vd in var_refs.items():
        for d in vd.get("refs", []):
            fn_to_vars.setdefault(_bare_from_descriptor(d).lower(), set()).add(vk)

    obligations: list[dict] = []
    for b in blocks:
        fid = b.get("id", "")
        loc = b.get("location", "")
        fk = _fn_at_location(graph, loc)
        if not fk:
            continue
        fbare = graph["functions"][fk].get("bare", fk.split(".")[-1]).lower()
        vars_touched = list(fn_to_vars.get(fbare, set()))[: _MAX_VARS_PER_FINDING]
        for vk in vars_touched:
            vd = var_refs.get(vk, {})
            refs = vd.get("refs", [])
            if len(refs) > _SKIP_VAR_REF_THRESHOLD:
                continue
            corefs = sorted({
                _bare_from_descriptor(d) for d in refs
                if _bare_from_descriptor(d).lower() != fbare
            })[: _MAX_COREFS_PER_VAR]
            if corefs:
                obligations.append({
                    "finding_id": fid,
                    "function": graph["functions"][fk].get("bare", fk),
                    "symbol": vd.get("bare", vk),
                    "required_corefs": corefs,
                })

    (scratchpad / "_enumeration_obligations.json").write_text(
        json.dumps({"source": graph.get("source", "?"), "obligations": obligations},
                   indent=1), encoding="utf-8")
    lines = ["# Enumeration Obligations",
             "",
             f"> Source graph: {graph.get('source', '?')}. {len(obligations)} obligation(s).",
             "> Each row: a finding analyzing `function` (which touches `symbol`) must "
             "address every co-referencing function below, or the gap becomes an "
             "ENUMGAP candidate.", "",
             "| Finding | Function | Symbol | Must also address |",
             "|---------|----------|--------|-------------------|"]
    for o in obligations:
        lines.append(f"| {o['finding_id']} | `{o['function']}` | `{o['symbol']}` | "
                     f"{', '.join('`'+c+'`' for c in o['required_corefs'])} |")
    (scratchpad / "enumeration_obligations.md").write_text("\n".join(lines) + "\n",
                                                           encoding="utf-8")
    return len(obligations)


def compute_coverage_gaps(scratchpad: Path) -> list[dict]:
    """The diff half of G2 (pure, testable): for each obligation, the required
    co-referencers NOT mentioned anywhere in the finding's own block prose."""
    scratchpad = Path(scratchpad)
    op = scratchpad / "_enumeration_obligations.json"
    inv = scratchpad / "findings_inventory.md"
    if not op.exists() or _inventory_blocks is None or not inv.exists():
        return []
    try:
        obligations = json.loads(op.read_text(encoding="utf-8", errors="replace")).get("obligations", [])
        blocks = {b["id"]: b for b in _inventory_blocks(inv.read_text(encoding="utf-8", errors="replace"))}
    except Exception:
        return []
    gaps: list[dict] = []
    for o in obligations:
        b = blocks.get(o["finding_id"])
        if not b:
            continue
        text = (b.get("block", "") or "").lower()
        missing = [c for c in o["required_corefs"] if c.lower() not in text]
        if missing:
            gaps.append({**o, "missing": missing})
    return gaps


def validate_enumeration_coverage(scratchpad: Path) -> dict:
    """G2. Compute coverage gaps and append each as a low-confidence ENUMGAP
    candidate to findings_inventory.md so the verify filter adjudicates it.
    Append-only, idempotent (receipt). Returns {gaps, emitted}. Never raises."""
    scratchpad = Path(scratchpad)
    try:
        gaps = compute_coverage_gaps(scratchpad)
    except Exception:
        return {"gaps": 0, "emitted": 0}
    if not gaps:
        return {"gaps": 0, "emitted": 0}

    inv = scratchpad / "findings_inventory.md"
    try:
        inv_text = inv.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {"gaps": len(gaps), "emitted": 0}

    receipt = scratchpad / "enumeration_gap_receipt.md"
    seen: set = set()
    if receipt.exists():
        try:
            seen = set(re.findall(r"\bENUMGAP-KEY:\s*(\S+)", receipt.read_text(encoding="utf-8", errors="replace")))
        except Exception:
            seen = set()

    max_inv = 0
    for m in re.finditer(r"\bINV-(\d+)\b", inv_text):
        try:
            max_inv = max(max_inv, int(m.group(1)))
        except ValueError:
            pass

    appended: list[str] = []
    keys: list[str] = []
    n = 0
    for g in gaps:
        for missing_fn in g["missing"]:
            key = f"{g['finding_id']}:{g['symbol']}:{missing_fn}"
            if key in seen or n >= _MAX_ENUMGAP_PER_RUN:
                continue
            n += 1
            inv_id = f"INV-{max_inv + n:03d}"
            title = (f"Unaddressed interaction: `{missing_fn}` also references "
                     f"`{g['symbol']}` (touched by `{g['function']}` in {g['finding_id']})")
            appended.extend([
                f"### Finding [{inv_id}]: {title}",
                "**Severity**: Low",
                f"**Location**: `{g['function']}` / `{missing_fn}` (shared symbol `{g['symbol']}`)",
                "**Preferred Tag**: [CODE-TRACE]",
                f"**Source IDs**: ENUMGAP (enumeration-coverage gap from {g['finding_id']}; "
                "mechanically derived from the reference graph — verifier to confirm or refute)",
                "**Verdict**: NEEDS_VERIFICATION",
                f"**Root Cause**: `{g['function']}` and `{missing_fn}` both reference "
                f"`{g['symbol']}`, but the analysis of `{g['function']}` did not address "
                f"`{missing_fn}`. Check whether their interaction over `{g['symbol']}` "
                "creates a stale-read, bricked-consumer, or accounting inconsistency.",
                f"**Description**: Enumeration-coverage gap. The reference graph shows "
                f"`{missing_fn}` also reads/writes `{g['symbol']}`; confirm the two "
                "functions are consistent or report the divergence.",
                "**Impact**: Potential cross-function inconsistency over shared state "
                "(verifier to confirm the concrete harm).",
                # Generic chain-matchable metadata: this gap both CREATES a
                # shared-state divergence (postcondition) and is a candidate
                # blocked-finding NEEDING that state to be consistent (missing
                # precondition). STATE-typed so the chain phase can pair it.
                *_chain_metadata_lines(
                    postcondition=(f"STATE: shared symbol `{g['symbol']}` may be left "
                                   f"inconsistent across `{g['function']}` / `{missing_fn}`"),
                    postcondition_type="STATE",
                    missing_precondition=(f"STATE: consistency of `{g['symbol']}` between "
                                          f"`{g['function']}` and `{missing_fn}`"),
                    precondition_type="STATE",
                ),
                "",
            ])
            keys.append(key)

    if not appended:
        return {"gaps": len(gaps), "emitted": 0}

    header = ("\n\n## Enumeration-Coverage Candidates (ENUMGAP)\n\n"
              "Mechanically-derived cross-function interactions over shared state that a "
              "finding's analysis did NOT address. Low-confidence by construction — the "
              "verify phase confirms or refutes each. Recall-safe: append-only.\n\n")
    hdr = "" if "Enumeration-Coverage Candidates (ENUMGAP)" in inv_text else header
    inv.write_text(_append_inventory_blocks(inv_text, hdr, appended), encoding="utf-8")

    rlines = ["# Enumeration Gap Receipt", ""]
    rlines += [f"ENUMGAP-KEY: {k}" for k in (sorted(seen) + keys)]
    receipt.write_text("\n".join(rlines) + "\n", encoding="utf-8")
    try:
        from plamen_mechanical import _write_finding_records_from_inventory
        _write_finding_records_from_inventory(scratchpad)
    except Exception:
        pass
    return {"gaps": len(gaps), "emitted": len(keys)}


# ─────────────────────────────────────────────────────────────────────────────
# Additional mechanical obligation-derivers.
#
# The shared-state co-reference gate above is ONE obligation type. These add more
# bug-class SHAPES that are (a) mechanically identifiable from source and (b) a
# systematic agent blind spot ("enumerated then dismissed"). Each derives an
# obligation and emits a low-confidence ENUMGAP candidate the verify filter
# prunes — same recall-safe, append-only, idempotent framework. No-overfit: every
# deriver encodes a generic pattern (HOW), never a protocol's specific bug.
#
#   critical_asset_mover (L-04 class): a protocol-critical SINGLETON asset handle
#       (a state var ending in *Id/*TokenId, depended on by >=2 functions) can be
#       moved by a same-contract GENERIC asset-mover that does not exclude it.
#   array_uniqueness     (L-10 class): a function loops a caller-supplied array
#       with a per-element value effect and NO uniqueness guard.
#   unbounded_input      (L-08 class): a caller-controlled string/bytes is stored
#       or looped with NO length bound (storage-bloat / gas-bomb DoS).
# ─────────────────────────────────────────────────────────────────────────────

_MAX_PER_DERIVER = 15   # per-deriver, per-run cap (shared global budget on top)

# ── Per-language signal registry ──────────────────────────────────────────────
# The 3 obligation-derivers are bug-class SHAPES, not Solidity idioms. A language
# appears for a vector only where that vector's shape genuinely exists (honest
# applicability — not every vector maps to every ecosystem):
#   L-04 critical-asset-mover : sol, rust, move      (NOT go node-clients / daml)
#   L-10 array-uniqueness     : sol, rust, move, go
#   L-08 unbounded-input      : sol, rust, move, go
# A vector key absent from a language's spec => that deriver skips that language.
# All param regexes use NAMED groups (?P<name>/?P<typ>) so the language-agnostic
# deriver code reads them uniformly regardless of declaration order.
def _c(p):
    return re.compile(p, re.MULTILINE)


_LANG = {
    "sol": {
        "suffix": (".sol",),
        "fn_re": _c(r"\bfunction\s+(\w+)\s*\(([^)]*)\)"),
        "array_param": _c(r"\b[\w.]+\[\]\s+(?:memory|calldata|storage)\s+(?P<name>\w+)"),
        "loop": _c(r"\b(?:for|while)\s*\("),
        "effect": _c(r"(?:safeTransferFrom|safeTransfer|transferFrom|\btransfer\b"
                     r"|\bmint\b|\bburn\b|\+=|\.push\()"),
        "uniq_guard": _c(r"(?i)\b(?:seen|unique|dedup|duplicat|sorted?|_sort)\b"),
        "str_param": _c(r"\b(?P<typ>string|bytes)\s+(?:memory|calldata)\s+(?P<name>\w+)"),
        "stored_tpl": (r"[\w.]+\s*\[[^\]]*\]\s*=\s*[^;]*\b{p}\b|\.push\(\s*{p}\b"
                       r"|=\s*\w+\s*\(\s*\{{[^}}]*\b{p}\b"),
        "lenguard_tpl": (r"(?:require|if)\b[^;{{]*(?:bytes\(\s*)?{p}\s*\)?\s*"
                         r"\.length\s*(?:<=|<|>=|>)"),
        "mover": _c(r"(?:\bI?ERC(?:20|721|1155)\s*\([^)]*\)\s*)?\.\s*"
                    r"(?:safeTransferFrom|transferFrom)\s*\(|\b_(?:safeTransfer|transfer)\s*\("),
        "id_param": _c(r"\b(?:uint256|uint|address)\s+(?:memory\s+|calldata\s+)?"
                       r"(\w*[Ii]d\b|\w*[Tt]oken\w*|to|token|asset|recipient)"),
        "asset_handle": _c(r"(?i)(?:tokenId|nftId|positionId|lpId)$|(?:Token|Nft|Position|Lp)Id$"),
    },
    "rust": {
        "suffix": (".rs",),
        "fn_re": _c(r"\bfn\s+(\w+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)"),
        "array_param": _c(r"\b(?P<name>\w+)\s*:\s*&?(?:mut\s+)?(?:Vec\s*<|\[(?![^\]\n]*;))"),
        "loop": _c(r"\bfor\b|\.iter(?:_mut)?\(\)|\.into_iter\(\)|\bwhile\b"),
        "effect": _c(r"\.transfer|transfer_from|\bmint\b|\bburn\b|\+=|\.push\("
                     r"|\bdeposit\b|\bwithdraw\b|\.set\("),
        "uniq_guard": _c(r"(?i)\b(?:seen|unique|dedup|duplicat|sort|hashset|btreeset)\b"),
        "str_param": _c(r"\b(?P<name>\w+)\s*:\s*&?(?:mut\s+)?"
                        r"(?P<typ>String|str|Vec\s*<\s*u8|\[\s*u8\s*\])"),
        "stored_tpl": (r"(?:\.set\(|\.push\(|=\s*\w+\s*\{{|extend|insert\()[^;]*\b{p}\b"),
        "lenguard_tpl": r"\b{p}\b(?:\.as_bytes\(\))?\.len\(\)\s*(?:<=|<|>=|>)",
        "mover": _c(r"\.transfer(?:_from)?\s*\(|token::transfer|TokenClient|::transfer\s*\("),
        "id_param": _c(r"\b(\w*_?id|token|asset|to|recipient)\s*:"),
        "asset_handle": _c(r"(?i)(?:token_?id|nft_?id|position_?id|lp_?id|object_?id)$"),
    },
    "move": {
        "suffix": (".move",),
        "fn_re": _c(r"\b(?:public\s*(?:\([^)]*\))?\s+|entry\s+)*fun\s+(\w+)"
                    r"\s*(?:<[^>]*>)?\s*\(([^)]*)\)"),
        "array_param": _c(r"\b(?P<name>\w+)\s*:\s*(?:&\s*(?:mut\s+)?)?vector\s*<"),
        "loop": _c(r"\bwhile\b|\bloop\b|for_each"),
        "effect": _c(r"\btransfer\b|coin::|\bmint\b|\bburn\b|\+=|vector::push"
                     r"|\bdeposit\b|\bwithdraw\b|public_transfer"),
        "uniq_guard": _c(r"(?i)\b(?:seen|unique|dedup|duplicat|sort|contains)\b"),
        "str_param": _c(r"\b(?P<name>\w+)\s*:\s*(?:&\s*)?(?P<typ>vector\s*<\s*u8|String|string)"),
        "stored_tpl": (r"(?:move_to|borrow_global_mut|vector::push|=)\s*[^;]*\b{p}\b"),
        "lenguard_tpl": (r"(?:assert!|if)\b[^;{{]*(?:vector::length|\.length)"
                         r"\([^)]*\b{p}\b[^;{{]*(?:<=|<|>=|>)"),
        "mover": _c(r"transfer::(?:public_)?transfer|coin::transfer|::transfer\s*\("),
        "id_param": _c(r"\b(\w*_?id|token|asset|to|recipient)\s*:"),
        "asset_handle": _c(r"(?i)(?:token_?id|nft_?id|object_?id|position_?id|lp_?id)$"),
    },
    "go": {
        "suffix": (".go",),
        "fn_re": _c(r"\bfunc\s+(?:\([^)]*\)\s*)?(\w+)\s*\(([^)]*)\)"),
        "array_param": _c(r"\b(?P<name>\w+)\s+\[\]\w"),
        "loop": _c(r"\bfor\b|\brange\b"),
        "effect": _c(r"\+=|append\(|\.Add\(|\btransfer\b"),
        "uniq_guard": _c(r"(?i)\b(?:seen|unique|dedup|duplicat|sort)\b|map\["),
        "str_param": _c(r"\b(?P<name>\w+)\s+(?P<typ>string|\[\]byte)\b"),
        "stored_tpl": (r"\b\w+\s*\[[^\]]*\]\s*=\s*[^;\n]*\b{p}\b|append\([^)]*\b{p}\b"
                       r"|=\s*[^;\n]*\b{p}\b"),
        "lenguard_tpl": r"len\(\s*{p}\s*\)\s*(?:<=|<|>=|>)",
        # no mover/id_param/asset_handle: L-04 N/A for Go node-clients.
    },
}
_SUPPORTED_SUFFIXES = tuple(s for spec in _LANG.values() for s in spec["suffix"])


def _locate_project_root(scratchpad: Path):
    """The SC/L1 audit scratchpad is `<project_root>/.scratchpad`; the gate is not
    handed the source tree, so derive it. Returns the dir holding any supported
    source file, or None."""
    try:
        cand = Path(scratchpad).parent
        for suf in _SUPPORTED_SUFFIXES:
            if any(cand.rglob("*" + suf)):
                return cand
    except Exception:
        pass
    return None


def _iter_functions(root: Path):
    """Yield (lang, rel_path, fn_name, params, body, line) for each PRODUCTION
    function across every supported language present (tests/mocks excluded).
    Approximate body slice (decl→next decl). Never raises; empty on any failure."""
    try:
        from recon_prepass import (_production_source_files, _read_text,
                                    _line_of, _rel)  # type: ignore
    except Exception:
        return
    for lang, spec in _LANG.items():
        try:
            files = _production_source_files(root, spec["suffix"])
        except Exception:
            continue
        fn_re = spec["fn_re"]
        for f in files:
            text = _read_text(f)
            if not text:
                continue
            decls = list(fn_re.finditer(text))
            for i, m in enumerate(decls):
                end = decls[i + 1].start() if i + 1 < len(decls) else len(text)
                try:
                    yield (lang, _rel(f, root), m.group(1), m.group(2) or "",
                           text[m.end():end], _line_of(text, m.start()))
                except Exception:
                    continue


def _emit_candidates(scratchpad: Path, candidates: list, cap: int) -> int:
    """Shared ENUMGAP emitter for every deriver. `candidates` are dicts with:
    key, title, location, source_note, root_cause, description, impact.
    Append-only to findings_inventory.md, idempotent via the SHARED receipt,
    honours `cap` new emissions (shared run budget). Returns count emitted."""
    if not candidates or cap <= 0:
        return 0
    inv = scratchpad / "findings_inventory.md"
    try:
        inv_text = inv.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    receipt = scratchpad / "enumeration_gap_receipt.md"
    seen: set = set()
    if receipt.exists():
        try:
            seen = set(re.findall(r"\bENUMGAP-KEY:\s*(\S+)",
                                  receipt.read_text(encoding="utf-8", errors="replace")))
        except Exception:
            seen = set()
    # Intra-run dedup baseline: `seen` is the persisted (cross-run) receipt set;
    # `emitted` ALSO tracks keys appended earlier in THIS call so two candidates
    # with an identical key are not double-emitted within one run (the observed
    # sibling-deriver double-emit). `seen` stays receipt-only so the receipt is
    # not double-written below.
    emitted: set = set(seen)
    max_inv = 0
    for m in re.finditer(r"\bINV-(\d+)\b", inv_text):
        try:
            max_inv = max(max_inv, int(m.group(1)))
        except ValueError:
            pass
    appended: list[str] = []
    keys: list[str] = []
    n = 0
    for c in candidates:
        if c["key"] in emitted or n >= cap:
            continue
        n += 1
        inv_id = f"INV-{max_inv + n:03d}"
        appended.extend([
            f"### Finding [{inv_id}]: {c['title']}",
            "**Severity**: Low",
            f"**Location**: {c['location']}",
            "**Preferred Tag**: [CODE-TRACE]",
            f"**Source IDs**: ENUMGAP ({c['source_note']})",
            "**Verdict**: NEEDS_VERIFICATION",
            f"**Root Cause**: {c['root_cause']}",
            f"**Description**: {c['description']}",
            f"**Impact**: {c['impact']}",
            # Generic chain-matchable pre/post metadata (per-deriver class) so a
            # weak candidate can still serve as a chain enabler. Omitted when a
            # deriver supplies none.
            *_chain_metadata_lines(
                postcondition=c.get("postcondition", ""),
                postcondition_type=c.get("postcondition_type", ""),
                missing_precondition=c.get("missing_precondition", ""),
                precondition_type=c.get("precondition_type", ""),
            ),
            "",
        ])
        keys.append(c["key"])
        emitted.add(c["key"])
    if not appended:
        return 0
    header = ("\n\n## Enumeration-Coverage Candidates (ENUMGAP)\n\n"
              "Mechanically-derived obligations a finding's analysis did NOT "
              "address. Low-confidence by construction — the verify phase confirms "
              "or refutes each. Recall-safe: append-only.\n\n")
    hdr = "" if "Enumeration-Coverage Candidates (ENUMGAP)" in inv_text else header
    inv.write_text(_append_inventory_blocks(inv_text, hdr, appended), encoding="utf-8")
    rlines = ["# Enumeration Gap Receipt", ""]
    rlines += [f"ENUMGAP-KEY: {k}" for k in (sorted(seen) + keys)]
    receipt.write_text("\n".join(rlines) + "\n", encoding="utf-8")
    try:
        from plamen_mechanical import _write_finding_records_from_inventory
        _write_finding_records_from_inventory(scratchpad)
    except Exception:
        pass
    return len(keys)


def compute_critical_asset_mover_candidates(scratchpad: Path) -> list:
    """L-04 class (sol/rust/move). A protocol-critical singleton asset handle (a
    state/storage var named like an asset id, depended on by >=2 functions) that a
    SAME-FILE generic asset-mover can move WITHOUT excluding it → the mover can
    strand every function that depends on that asset. Generic across ecosystems
    that hold movable assets; bounded to the declaring file. Go node-clients and
    DAML have no such shape and are skipped (no `mover` in their lang spec)."""
    try:
        graph = _load_graph(scratchpad)
        root = _locate_project_root(scratchpad)
        if graph is None or root is None:
            return []
        # asset-handle match = the asset_handle pattern(s) for the language(s)
        # ACTUALLY present in the project tree (one audit is one ecosystem).
        # Scoping to present languages stops e.g. the rust/move id-stem shape from
        # matching an EVM ALL-CAPS chain-id constant when the audited ecosystem is
        # EVM. Haltless fallback to the full union if detection finds nothing.
        langs_present = {
            lang for lang, spec in _LANG.items()
            if "asset_handle" in spec
            and any(next(root.rglob("*" + suf), None) for suf in spec["suffix"])
        }
        handle_res = [_LANG[l]["asset_handle"] for l in langs_present]
        if not handle_res:  # detection found nothing → preserve prior behavior
            handle_res = [spec["asset_handle"] for spec in _LANG.values()
                          if "asset_handle" in spec]
        var_refs = graph.get("var_refs", {})
        crit: dict = {}    # bare -> [dependent fns]
        for vk, vd in var_refs.items():
            bare = vd.get("bare", vk.split(".")[-1])
            refs = vd.get("refs", [])
            if (any(r.search(bare) for r in handle_res)
                    and 2 <= len(refs) <= _SKIP_VAR_REF_THRESHOLD):
                crit[bare] = sorted({_bare_from_descriptor(d) for d in refs})
        if not crit:
            return []
        # Same-file bound: which production file declares/holds each critical var?
        # (The source-tier graph keys var_refs by BARE name with no contract.)
        # Lang-agnostic: the file where the bare name appears as a word.
        decl_files: dict = {b: set() for b in crit}
        try:
            from recon_prepass import (_production_source_files, _read_text,
                                        _rel)  # type: ignore
            for f in _production_source_files(root, _SUPPORTED_SUFFIXES):
                t = _read_text(f)
                if not t:
                    continue
                rel_f = _rel(f, root)
                for b in crit:
                    if re.search(r"\b" + re.escape(b) + r"\b", t):
                        decl_files[b].add(rel_f)
        except Exception:
            pass
        out: list = []
        seen_pairs: set = set()
        for lang, rel, name, params, body, _line in _iter_functions(root):
            if len(out) >= _MAX_PER_DERIVER:
                break
            spec = _LANG[lang]
            mover = spec.get("mover")
            id_param = spec.get("id_param")
            if mover is None or id_param is None:   # L-04 N/A for this language
                continue
            if not mover.search(body) or not id_param.search(params):
                continue
            for bare, fns in crit.items():
                if decl_files.get(bare) and rel not in decl_files[bare]:
                    continue
                if re.search(r"\b" + re.escape(bare) + r"\b", body) or name in fns:
                    continue   # mover already references/excludes the critical var
                pairkey = f"{rel}:{name}:{bare}"
                if pairkey in seen_pairs:
                    continue
                seen_pairs.add(pairkey)
                dep = ", ".join(f"`{x}`" for x in fns[:6])
                out.append({
                    "key": f"ASSETMOVE:{pairkey}",
                    "title": (f"Generic asset-mover `{name}` can move the critical "
                              f"singleton `{bare}` that other functions depend on"),
                    "location": f"`{rel}` :: `{name}` (critical asset `{bare}`)",
                    "source_note": "critical-asset-mover gap; mechanically derived — verifier to confirm or refute",
                    "root_cause": (f"`{name}` transfers an asset selected by a caller "
                                   f"parameter and does not exclude `{bare}`. `{bare}` "
                                   f"is a singleton the protocol depends on (referenced "
                                   f"by {dep}). Moving it out would strand those functions."),
                    "description": (f"`{name}` is a generic asset-mover; `{bare}` is a "
                                    f"protocol-critical singleton asset. Verify `{name}` "
                                    f"cannot move `{bare}` (or that doing so does not "
                                    f"break {dep})."),
                    "impact": ("Potential permanent breakage of the dependent functions "
                               "if the critical asset is moved (verifier to confirm)."),
                    # L-04 class → STATE postcondition: the mover relocates a
                    # protocol-critical singleton out of the contract.
                    "postcondition": (f"STATE: critical singleton asset `{bare}` relocated "
                                      f"out of the contract, stranding dependent functions"),
                    "postcondition_type": "STATE",
                })
                if len(out) >= _MAX_PER_DERIVER:
                    break
        return out
    except Exception:
        return []


def compute_array_uniqueness_candidates(scratchpad: Path) -> list:
    """L-10 class (sol/rust/move/go). A function loops a caller-supplied array/
    vector/slice producing a per-element value effect with NO uniqueness guard →
    duplicate elements multiply the effect. Universal source-parse shape."""
    try:
        root = _locate_project_root(scratchpad)
        if root is None:
            return []
        out: list = []
        for lang, rel, name, params, body, _line in _iter_functions(root):
            if len(out) >= _MAX_PER_DERIVER:
                break
            spec = _LANG[lang]
            arr = spec["array_param"].search(params)
            if not arr:
                continue
            arrname = arr.group("name")
            e = re.escape(arrname)
            # Bind the per-element premise: the array must be ELEMENT-ACCESSED
            # (indexed / iterated), not merely passed wholesale to a callee. This
            # is what distinguishes a per-element value-effect loop from framework
            # plumbing arrays handed off intact (e.g. CPI signer-seeds, account
            # slices, calldata blobs) which never apply a per-element effect.
            elem_access = re.search(
                r"\b" + e + r"\s*\["
                r"|\b(?:range|in)\s+&?(?:mut\s+)?" + e + r"\b"
                r"|\b" + e + r"\s*\.\s*(?:iter|into_iter)\b"
                r"|\b(?:borrow|borrow_mut|for_each)\s*\(\s*&?(?:mut\s+)?" + e + r"\b",
                body)
            if not elem_access:
                continue
            iterates = (re.search(r"\b" + e + r"\b", body)
                        and spec["loop"].search(body))
            if not iterates or not spec["effect"].search(body):
                continue
            if spec["uniq_guard"].search(body):
                continue
            out.append({
                "key": f"ARRUNIQ:{rel}:{name}:{arrname}",
                "title": (f"`{name}` applies a per-element effect over caller array "
                          f"`{arrname}` with no uniqueness guard"),
                "location": f"`{rel}` :: `{name}` (array `{arrname}`)",
                "source_note": "array-uniqueness gap; mechanically derived — verifier to confirm or refute",
                "root_cause": (f"`{name}` loops the caller-supplied array `{arrname}` and "
                               f"performs a per-element value effect (transfer/mint/burn/"
                               f"accumulate) without validating element uniqueness. A "
                               f"repeated element has its effect applied multiple times."),
                "description": (f"Verify that passing a duplicate element in `{arrname}` "
                                f"does not double-count a payout/mint/burn/accumulation in "
                                f"`{name}` (e.g. draining a pool via repeated pro-rata credit)."),
                "impact": ("Potential multiplied value effect (e.g. over-payout / pool "
                           "drain) from duplicate array elements (verifier to confirm)."),
                # L-10 class → BALANCE/accounting postcondition: a per-element
                # value effect is applied more times than the distinct set.
                "postcondition": (f"BALANCE: per-element value effect in `{name}` applied "
                                  f"multiple times via duplicate `{arrname}` elements "
                                  "(accounting inflation)"),
                "postcondition_type": "BALANCE",
            })
        return out
    except Exception:
        return []


def compute_unbounded_input_candidates(scratchpad: Path) -> list:
    """L-08 class (sol/rust/move/go). A caller-controlled string/bytes value is
    stored on-chain with NO length bound → storage-bloat / gas-bomb DoS. Universal
    source-parse shape (Rust String/Vec<u8>, Move vector<u8>, Go []byte)."""
    try:
        root = _locate_project_root(scratchpad)
        if root is None:
            return []
        out: list = []
        for lang, rel, name, params, body, _line in _iter_functions(root):
            if len(out) >= _MAX_PER_DERIVER:
                break
            spec = _LANG[lang]
            # Sol pure/view functions cannot write storage — the stored-input
            # storage-bloat harm premise is impossible for them. The modifier
            # section (where pure/view appears) precedes the body's opening brace.
            # Recall-safe: any storage-writing function is non-pure/view.
            if lang == "sol":
                head = body[:body.find("{")] if "{" in body else body[:160]
                if re.search(r"\b(?:pure|view)\b", head):
                    continue
            for m in spec["str_param"].finditer(params):
                pname = m.group("name")
                typ = (m.groupdict().get("typ") or "input").strip()
                p = re.escape(pname)
                stored = bool(re.search(spec["stored_tpl"].format(p=p), body))
                if not stored:
                    continue
                # UPPER length bound? A non-empty (== 0) check is NOT an upper
                # bound — the templates require an inequality comparator.
                if re.search(spec["lenguard_tpl"].format(p=p), body):
                    continue
                out.append({
                    "key": f"UNBOUND:{rel}:{name}:{pname}",
                    "title": (f"`{name}` stores caller-controlled `{typ} {pname}` "
                              f"with no length bound"),
                    "location": f"`{rel}` :: `{name}` (param `{typ} {pname}`)",
                    "source_note": "unbounded-input gap; mechanically derived — verifier to confirm or refute",
                    "root_cause": (f"`{name}` accepts a caller-controlled `{typ} {pname}` and "
                                   f"stores it without a length bound. A very large value "
                                   f"bloats storage and can gas-bomb later execution that "
                                   f"reads/iterates it."),
                    "description": (f"Verify there is an upper bound on `{pname}` in `{name}`; "
                                    f"without one, an oversized `{typ}` enables storage-bloat "
                                    f"or a gas-bomb DoS on downstream execution."),
                    "impact": ("Potential storage-bloat or gas-bomb DoS bricking later "
                               "execution (verifier to confirm)."),
                    # L-08 class → liveness/EXTERNAL postcondition: an oversized
                    # stored value can brick downstream execution that reads it.
                    "postcondition": (f"EXTERNAL: oversized stored `{typ} {pname}` enables a "
                                      f"gas-bomb/liveness DoS on later execution that reads it"),
                    "postcondition_type": "EXTERNAL",
                })
                if len(out) >= _MAX_PER_DERIVER:
                    break
        return out
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Phase 4b.7 handoff: promote the depth-exploration agent's findings into the
# inventory so they flow through the SAME inventory -> chain -> verify path as
# every other finding. The exploration agent TRACES each enumeration obligation
# (boundary/variation/trace) and writes a real finding OR a reasoned clear to
# `enumgap_exploration_findings.md`; only the emitted findings (NEXP-n blocks)
# are promoted — reasoned clears live in its Coverage Record and are not
# candidates. Append-only + idempotent via a dedicated receipt. Never raises.
#
# This is the recall fix's load-bearing seam: the obligation is now EXPLORED
# (by the depth agent) before it reaches verify, instead of being handed to
# verify as a raw low-confidence candidate. If the exploration phase did not run
# (no obligations, spawn failure, degrade), this function simply finds no
# `enumgap_exploration_findings.md` and is a no-op — the pre-existing ENUMGAP
# candidates the gate already appended remain as the haltless fallback.
# ─────────────────────────────────────────────────────────────────────────────

_EXPL_HEADING_RE = re.compile(
    r"^#{2,4}\s*Finding\s*\[\s*(?P<id>[A-Za-z]{2,6}-\d+)\s*\]\s*:\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)
_EXPL_REQUIRED_FIELDS = ("Severity", "Location", "Description")


def promote_enumgap_exploration_to_inventory(scratchpad: Path) -> dict:
    """Append the depth-exploration agent's findings to findings_inventory.md as
    INV-* entries so they reach chain/verify. Idempotent via a receipt keyed on
    the source NEXP-* id. Returns {parsed, emitted}. Never raises, never halts."""
    scratchpad = Path(scratchpad)
    try:
        art = scratchpad / "enumgap_exploration_findings.md"
        inv = scratchpad / "findings_inventory.md"
        if not art.exists() or not inv.exists():
            return {"parsed": 0, "emitted": 0}
        text = art.read_text(encoding="utf-8", errors="replace")
        inv_text = inv.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {"parsed": 0, "emitted": 0}

    matches = list(_EXPL_HEADING_RE.finditer(text))
    parsed: list[dict] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        if not all(f"**{f}**" in block for f in _EXPL_REQUIRED_FIELDS):
            continue
        parsed.append({"id": m.group("id").strip(),
                       "title": m.group("title").strip(),
                       "block": block})
    if not parsed:
        return {"parsed": 0, "emitted": 0}

    receipt = scratchpad / "enumgap_exploration_promotion_receipt.md"
    promoted: set = set()
    if receipt.exists():
        try:
            promoted = set(re.findall(r"\b([A-Za-z]{2,6}-\d+)\s*->\s*INV-\d+",
                                      receipt.read_text(encoding="utf-8", errors="replace")))
        except Exception:
            promoted = set()

    new = [p for p in parsed if p["id"] not in promoted]
    if not new:
        return {"parsed": len(parsed), "emitted": 0}

    max_inv = 0
    for mm in re.finditer(r"\bINV-(\d+)\b", inv_text):
        try:
            max_inv = max(max_inv, int(mm.group(1)))
        except ValueError:
            pass

    appended: list[str] = []
    rec_lines: list[str] = []

    def _field(block: str, name: str) -> str:
        mo = re.search(r"\*\*" + name + r"\*\*\s*:\s*(.+?)(?=\n\*\*|\n##|\n#{2,4}\s|\Z)",
                       block, re.IGNORECASE | re.DOTALL)
        return (mo.group(1).strip() if mo else "").replace("\n", " ").strip()

    for n, p in enumerate(new, 1):
        inv_id = f"INV-{max_inv + n:03d}"
        sev = _field(p["block"], "Severity") or "Low"
        loc = _field(p["block"], "Location") or "UNKNOWN"
        desc = _field(p["block"], "Description") or p["title"]
        impact = _field(p["block"], "Impact") or "Verifier to confirm the concrete harm."
        rc = _field(p["block"], "Root Cause")
        tag = _field(p["block"], "Preferred Tag") or "[CODE-TRACE]"
        appended.extend([
            f"### Finding [{inv_id}]: {p['title']}",
            f"**Severity**: {sev.split()[0] if sev else 'Low'}",
            f"**Location**: {loc}",
            f"**Preferred Tag**: {tag}",
            f"**Source IDs**: {p['id']} (enumeration-obligation exploration; depth-traced "
            "from a mechanically-flagged obligation — verifier to confirm or refute)",
            "**Verdict**: NEEDS_VERIFICATION",
        ])
        if rc:
            appended.append(f"**Root Cause**: {rc}")
        appended.extend([
            f"**Description**: {desc}",
            f"**Impact**: {impact}",
            "",
        ])
        rec_lines.append(f"{p['id']} -> {inv_id}")

    header = ("\n\n## Enumeration-Obligation Exploration Findings\n\n"
              "Depth-traced findings produced by the Phase 4b.7 exploration of "
              "mechanically-flagged enumeration obligations. Each was investigated "
              "(boundary/variation/trace) before reaching verification. Recall-safe: "
              "append-only.\n\n")
    hdr = "" if "Enumeration-Obligation Exploration Findings" in inv_text else header
    try:
        inv.write_text(_append_inventory_blocks(inv_text, hdr, appended), encoding="utf-8")
    except Exception:
        return {"parsed": len(parsed), "emitted": 0}

    try:
        prior = []
        if receipt.exists():
            prior = [ln for ln in receipt.read_text(encoding="utf-8", errors="replace").splitlines()
                     if "->" in ln]
        out = ["# Enumeration-Obligation Exploration Promotion Receipt", ""]
        out += [ln.strip() for ln in prior] + rec_lines
        receipt.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:
        pass

    try:
        from plamen_mechanical import _write_finding_records_from_inventory
        _write_finding_records_from_inventory(scratchpad)
    except Exception:
        pass
    return {"parsed": len(parsed), "emitted": len(rec_lines)}


def run_enumeration_gate(scratchpad: Path) -> dict:
    """Driver entry: the co-reference gate (G1+G2) then the additional mechanical
    obligation-derivers. Best-effort, never raises, never halts.

    Budget: each deriver gets its OWN `_MAX_PER_DERIVER` slots, INDEPENDENT of the
    co-reference gate's `_MAX_ENUMGAP_PER_RUN` pool. (Sharing one pool let the
    co-ref gate, which routinely hits its 40-cap, starve every new deriver to
    zero — the exact bug that silenced L-04/L-08/L-10 in a real run.) Each pool
    is bounded and the verify-the-positives filter prunes the candidates, so the
    bounded sum (co-ref 40 + 3×15) is recall-safe."""
    scratchpad = Path(scratchpad)
    try:
        n_obl = compute_enumeration_obligations(scratchpad)
    except Exception:
        n_obl = 0
    try:
        res = validate_enumeration_coverage(scratchpad)
    except Exception:
        res = {"gaps": 0, "emitted": 0}
    emitted = int(res.get("emitted", 0))
    # Each deriver gets its own dedicated budget — never the co-ref gate's leftover.
    for fn in (compute_critical_asset_mover_candidates,
               compute_array_uniqueness_candidates,
               compute_unbounded_input_candidates):
        try:
            cands = fn(scratchpad)
            emitted += _emit_candidates(scratchpad, cands, _MAX_PER_DERIVER)
        except Exception:
            continue
    return {"obligations": n_obl, "gaps": res.get("gaps", 0), "emitted": emitted}
