"""Plamen V2 — section-scoped Markdown AST utilities (Ship A).

Layer 0: no internal plamen_* imports. Depends only on stdlib + markdown-it-py.

WHY THIS EXISTS
---------------
The recurring pipeline failure (DODO instantiate halt, swarm clusters 1-3) is
the driver parsing LLM-authored Markdown as a machine protocol with loose,
un-anchored regex: a parser locates a table by a "header has these words"
heuristic, then scans line-by-line until a non-pipe line — which silently bleeds
into the NEXT table when a later section has a similar header (e.g. the breadth
manifest parser scanning past `## Breadth Agents` into `## Required Template
Coverage`).

These helpers replace "scan-until-non-pipe-line" with **section-scoped AST
parsing**: locate the heading, take only the tokens belonging to that section
(up to the next heading of equal-or-higher level), and read the FIRST GFM table
in that section via a real Markdown token stream. A table in another section
can never affect the result.

Public API
----------
- ``section_tokens(md, heading_re, level=None)`` -> tokens of the matched section
- ``tables_in_tokens(tokens)`` -> list of tables, each a list of row dicts keyed
  by normalized header
- ``first_section_table(md, heading_re, *, level=None, required_columns=None)``
  -> the first table's rows in the matched section (``[]`` if none)
- ``normalize_header(text)`` -> canonical column key
- ``source_fingerprint(path)`` -> {mtime_ns, sha256, size} (generic; mirrors
  plamen_parsers._judge_source_fingerprint so the contract layer and parsers can
  share one fingerprint convention without an import cycle)
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from markdown_it import MarkdownIt

# CommonMark + the GFM table extension ONLY. Deliberately NOT "gfm-like":
# that preset enables linkify, which requires the optional linkify-it-py
# dependency we do not ship. Tables are all we need for machine artifacts.
_MD = MarkdownIt("commonmark").enable("table")


def normalize_header(text: str) -> str:
    """Canonicalize a table column header to a stable key: lowercase, runs of
    non-alphanumerics collapsed to a single underscore, edges stripped.

      "Expected Output" -> "expected_output"
      "Required?"       -> "required"
      "Template (Required=YES)" -> "template_required_yes"

    Mirrors plamen_parsers._normalize_manifest_header so callers migrating off
    the legacy parser get identical keys.
    """
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def _heading_level(tag: str) -> int:
    """`h2` -> 2. Returns 99 for a non-heading tag (sorts after any heading)."""
    if tag and len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
        return int(tag[1])
    return 99


def parse(md: str):
    """Parse Markdown to a token list (commonmark + tables). Never raises on
    ordinary input; returns [] on a hard tokenizer error."""
    try:
        return _MD.parse(md or "")
    except Exception:
        return []


def section_tokens(
    md: str, heading_re, *, level: Optional[int] = None
) -> list:
    """Return the token slice belonging to the FIRST heading whose text matches
    ``heading_re`` (a compiled regex or a string pattern, searched
    case-insensitively), up to — but excluding — the next heading of
    equal-or-higher level. ``[]`` if no heading matches.

    Section membership uses heading LEVEL, not raw line scanning, so a `###`
    subsection inside the matched `##` section stays included while the next
    `##`/`#` ends it.

    If ``level`` is given, only headings of exactly that level are eligible to
    match (defensive — callers usually leave it None and rely on heading text).
    """
    if isinstance(heading_re, str):
        heading_re = re.compile(heading_re, re.IGNORECASE)
    toks = parse(md)
    n = len(toks)
    start_idx = -1
    start_level = 0
    i = 0
    while i < n:
        t = toks[i]
        if t.type == "heading_open":
            lvl = _heading_level(t.tag)
            # The heading's text is the immediately following inline token.
            text = ""
            if i + 1 < n and toks[i + 1].type == "inline":
                text = toks[i + 1].content or ""
            if start_idx == -1:
                if (level is None or lvl == level) and heading_re.search(text):
                    start_idx = i
                    start_level = lvl
            else:
                # Already inside the matched section: a heading of
                # equal-or-higher level closes it.
                if lvl <= start_level:
                    return toks[start_idx:i]
        i += 1
    if start_idx == -1:
        return []
    return toks[start_idx:]


def section_text(md: str, heading_re, *, level: Optional[int] = None) -> str:
    """Return the SOURCE Markdown substring of the first heading matching
    ``heading_re``, from the heading line up to (excluding) the next heading of
    equal-or-higher level. ``""`` if no heading matches — callers then fall back
    to the full document (backward compatibility with artifacts that omit the
    section heading).

    Unlike ``section_tokens`` (which returns parsed tokens), this returns raw
    text so a caller can keep its existing line-based row parser but bounded to
    the correct section — the minimal, behavior-preserving way to kill the
    cross-section table bleed.
    """
    if isinstance(heading_re, str):
        heading_re = re.compile(heading_re, re.IGNORECASE)
    toks = parse(md)
    n = len(toks)
    lines = md.splitlines()
    start_line = -1
    start_level = 0
    i = 0
    while i < n:
        t = toks[i]
        if t.type == "heading_open" and t.map:
            lvl = _heading_level(t.tag)
            text = ""
            if i + 1 < n and toks[i + 1].type == "inline":
                text = toks[i + 1].content or ""
            if start_line == -1:
                if (level is None or lvl == level) and heading_re.search(text):
                    start_line = t.map[0]
                    start_level = lvl
            elif lvl <= start_level:
                return "\n".join(lines[start_line:t.map[0]])
        i += 1
    if start_line == -1:
        return ""
    return "\n".join(lines[start_line:])


def _table_rows(toks: list, table_start: int) -> tuple[list[dict], int]:
    """Parse one GFM table starting at ``table_start`` (a table_open token).
    Returns (rows_keyed_by_header, index_after_table_close). Header cells are
    normalized via ``normalize_header``; body cells are stripped strings."""
    n = len(toks)
    headers: list[str] = []
    rows: list[dict] = []
    in_head = False
    in_body = False
    cur_cells: list[str] = []
    i = table_start + 1
    while i < n:
        t = toks[i]
        ty = t.type
        if ty == "table_close":
            i += 1
            break
        elif ty == "thead_open":
            in_head, in_body = True, False
        elif ty == "thead_close":
            in_head = False
        elif ty == "tbody_open":
            in_body, in_head = True, False
        elif ty == "tbody_close":
            in_body = False
        elif ty == "tr_open":
            cur_cells = []
        elif ty == "tr_close":
            if in_head and not headers:
                headers = [normalize_header(c) for c in cur_cells]
            elif headers:
                row = {}
                for col, val in zip(headers, cur_cells):
                    row[col] = val
                # Preserve positional access for callers that need it.
                row["_cells"] = list(cur_cells)
                rows.append(row)
        elif ty == "inline":
            cur_cells.append((t.content or "").strip())
        i += 1
    return rows, i


def tables_in_tokens(toks: list) -> list[list[dict]]:
    """Return every GFM table found in ``toks``, each as a list of row dicts
    keyed by normalized header (plus ``_cells`` positional list)."""
    out: list[list[dict]] = []
    i = 0
    n = len(toks)
    while i < n:
        if toks[i].type == "table_open":
            rows, i = _table_rows(toks, i)
            out.append(rows)
        else:
            i += 1
    return out


def first_section_table(
    md: str,
    heading_re,
    *,
    level: Optional[int] = None,
    required_columns: Optional[list[str]] = None,
) -> list[dict]:
    """High-level helper: section-scope to the FIRST heading matching
    ``heading_re``, then return the rows of the FIRST GFM table in that section
    (keyed by normalized header). ``[]`` when the section or a table is absent.

    If ``required_columns`` is given (normalized names), the first table in the
    section that contains ALL of them is chosen (skips a leading table that is
    not the intended one). If none match, returns the first table's rows anyway
    so callers can produce a precise "missing column X" diagnostic rather than
    an opaque empty result.
    """
    sect = section_tokens(md, heading_re, level=level)
    if not sect:
        return []
    tables = tables_in_tokens(sect)
    if not tables:
        return []
    if required_columns:
        req = {normalize_header(c) for c in required_columns}
        for tbl in tables:
            if tbl and req.issubset(set(tbl[0].keys())):
                return tbl
    return tables[0]


def source_fingerprint(path: Path) -> dict:
    """Generic identity record for a source file (mtime_ns + sha256 + size),
    used by the contract layer to detect that a JSON sidecar is stale relative
    to its companion Markdown. Mirrors plamen_parsers._judge_source_fingerprint
    but lives at Layer 0 so the contract layer can import it without a cycle.
    Returns {} on read error."""
    try:
        stat = path.stat()
        data = path.read_bytes()
    except OSError:
        return {}
    return {
        "source_mtime_ns": stat.st_mtime_ns,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_size": stat.st_size,
    }
