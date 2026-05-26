#!/usr/bin/env python3
"""Plamen v2 Recon Pre-Pass — mechanical artifact writer.

Writes filesystem-walk artifacts (inventory, state vars, function list,
build status, L1 subsystem map) plus stubs for LLM-dependent artifacts
BEFORE the LLM recon phase runs. Stdlib only. Self-contained.

Export: run_recon_prepass(config: dict) -> dict[str, str]
Status: WRITTEN | STUB | FAILED | SKIPPED
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Filesystem helpers
SKIP_DIR_NAMES = {
    "node_modules", ".git", "target", "build", "out", "artifacts", "cache",
    "dist", ".venv", "venv", "__pycache__", ".next", ".idea", ".vscode",
    "lib", "forge-cache", ".foundry", ".anchor", ".aptos", ".sui",
}

def _iter_files(root: Path, suffixes: Tuple[str, ...]) -> List[Path]:
    out: List[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        for name in filenames:
            if name.endswith(suffixes):
                out.append(Path(dirpath) / name)
    return out

def _lines_and_bytes(p: Path) -> Tuple[int, int]:
    try:
        data = p.read_bytes()
        ln = data.count(b"\n") + (0 if data.endswith(b"\n") else 1 if data else 0)
        return (ln, len(data))
    except Exception:
        return (0, 0)

def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root)).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")

def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1

# Provenance marker planted at the top of every pre-pass artifact. If the
# file still starts with this on a re-run, it means no LLM phase has rewritten
# it since — safe to overwrite. If the marker is absent, the file was
# enriched (or hand-edited) and must be preserved.
#
# Why a marker instead of a size heuristic: the prior 1.5x rule had two
# silent failure modes — (1) enriched files only slightly larger than the
# stub got clobbered on resume, (2) stale over-large artifacts from a bad
# prior run were preserved forever. A provenance marker is binary: either
# the file is our untouched mechanical output, or it is not.
_PREPASS_MARKER = "<!-- plamen-prepass v1: mechanical pre-pass output; safe to overwrite while marker is present -->"


def _write_text(p: Path, content: str) -> bool:
    """Write `content` to `p`, but preserve LLM-enriched content on resume.

    Overwrite rule (marker-based):
      - File does not exist → write.
      - File exists AND first line matches `_PREPASS_MARKER` → our own
        untouched output, overwrite with fresh content (keeps pre-pass
        idempotent across re-runs).
      - File exists AND first line does NOT match the marker → an LLM
        phase (or the user) has rewritten this file. Preserve it verbatim,
        even if the incoming pre-pass content is larger.

    The marker is prepended to every pre-pass write, so re-runs can
    recognize their own prior output without relying on file size.
    """
    try:
        stamped = _PREPASS_MARKER + "\n" + content
        if p.exists():
            try:
                head = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
            except Exception:
                head = ""
            if head != _PREPASS_MARKER:
                # File was enriched (or hand-edited) since our last write.
                # Do not clobber.
                return True
        p.write_text(stamped, encoding="utf-8")
        return True
    except Exception:
        return False

# Regex patterns
_EVM_STATE_RE = re.compile(
    r"^\s*(mapping\s*\([^)]+\)(?:\s*\[\s*\])?|uint\d*|int\d*|address(?:\s+payable)?|bytes\d*|bool|string)"
    r"\s+(?:public|private|internal)?\s*(?:immutable|constant)?\s*(\w+)\s*[;=]",
    re.MULTILINE,
)
_EVM_FN_RE = re.compile(
    r"^\s*function\s+(\w+)\s*\([^)]*\)\s*((?:\w+\s+)*)",
    re.MULTILINE,
)
_RUST_STRUCT_RE = re.compile(
    r"#\[\s*account\s*(?:\([^)]*\))?\s*\]\s*(?:pub\s+)?struct\s+\w+\s*\{([^}]*)\}",
    re.DOTALL,
)
_RUST_FIELD_RE = re.compile(r"^\s*(?:pub\s+)?(\w+)\s*:\s*([^,\n]+),?\s*$", re.MULTILINE)
_RUST_FN_RE = re.compile(r"^\s*pub(?:\s*\([^)]*\))?\s+fn\s+(\w+)", re.MULTILINE)
_MOVE_STRUCT_RE = re.compile(
    r"struct\s+(\w+)[^\{]*has\s+[\w\s,]*\b(?:key|store)\b[^\{]*\{([^}]*)\}",
    re.DOTALL,
)
_MOVE_FIELD_RE = re.compile(r"^\s*(\w+)\s*:\s*([^,\n]+),?\s*$", re.MULTILINE)
_MOVE_FN_RE = re.compile(r"^\s*(?:public(?:\([^)]*\))?\s+|entry\s+)+fun\s+(\w+)", re.MULTILINE)

_CONTRACT_MARKERS = ("#[program]", "#[contract]", "#[contractimpl]", "contractimpl!")


# Language dispatch — per-lang regex adapters; see LANG_DISPATCH below.

def _evm_state_rows(text, f, proj):
    return [
        f"| `{_rel(f, proj)}` | `{m.group(2)}` | `{m.group(1).strip()}` | {_line_of(text, m.start())} |"
        for m in _EVM_STATE_RE.finditer(text)
    ]

def _evm_fn_rows(text, f, proj):
    out = []
    for m in _EVM_FN_RE.finditer(text):
        mods = (m.group(2) or "").strip()
        vis = next((v for v in ("public", "private", "internal", "external")
                    if re.search(rf"\b{v}\b", mods)), "external")
        out.append(f"| `{_rel(f, proj)}` | `{m.group(1)}` | {vis} | {_line_of(text, m.start())} |")
    return out

def _struct_field_rows(text, f, proj, struct_re, field_re, body_group):
    out = []
    for sm in struct_re.finditer(text):
        base = _line_of(text, sm.start())
        body = sm.group(body_group)
        for fm in field_re.finditer(body):
            rel_line = base + body.count("\n", 0, fm.start())
            out.append(f"| `{_rel(f, proj)}` | `{fm.group(1)}` | `{fm.group(2).strip()}` | {rel_line} |")
    return out

def _rust_state_rows(text, f, proj):
    return _struct_field_rows(text, f, proj, _RUST_STRUCT_RE, _RUST_FIELD_RE, 1)

def _move_state_rows(text, f, proj):
    return _struct_field_rows(text, f, proj, _MOVE_STRUCT_RE, _MOVE_FIELD_RE, 2)

def _simple_fn_rows(text, f, proj, fn_re, vis_label):
    return [
        f"| `{_rel(f, proj)}` | `{m.group(1)}` | {vis_label} | {_line_of(text, m.start())} |"
        for m in fn_re.finditer(text)
    ]

def _rust_fn_rows(text, f, proj):
    return _simple_fn_rows(text, f, proj, _RUST_FN_RE, "pub")

def _move_fn_rows(text, f, proj):
    return _simple_fn_rows(text, f, proj, _MOVE_FN_RE, "public")


LANG_DISPATCH: Dict[str, dict] = {
    "evm":     {"suffix": (".sol",),  "marker": False,
                "state": _evm_state_rows,  "fn": _evm_fn_rows},
    "solana":  {"suffix": (".rs",),   "marker": True,
                "state": _rust_state_rows, "fn": _rust_fn_rows},
    "soroban": {"suffix": (".rs",),   "marker": True,
                "state": _rust_state_rows, "fn": _rust_fn_rows},
    "aptos":   {"suffix": (".move",), "marker": False,
                "state": _move_state_rows, "fn": _move_fn_rows},
    "sui":     {"suffix": (".move",), "marker": False,
                "state": _move_state_rows, "fn": _move_fn_rows},
}

def _gather_files(proj: Path, lang: str) -> List[Path]:
    cfg = LANG_DISPATCH.get(lang)
    if not cfg:
        return []
    files = _iter_files(proj, cfg["suffix"])
    if cfg["marker"]:
        files = [f for f in files if any(m in _read_text(f) for m in _CONTRACT_MARKERS)]
    return files

# SC artifact writers
def _write_contract_inventory_sc(scratch: Path, proj: Path, lang: str) -> str:
    try:
        files = _gather_files(proj, lang)
        lines = ["# Contract Inventory", "",
                 f"Pre-pass: {len(files)} file(s) discovered by filesystem walk.", "",
                 "| File | Path | Lines | Bytes |",
                 "|------|------|-------|-------|"]
        for f in sorted(files, key=lambda p: _rel(p, proj)):
            ln, bt = _lines_and_bytes(f)
            lines.append(f"| {f.name} | `{_rel(f, proj)}` | {ln} | {bt} |")
        if not files:
            lines.append("| _(no source files found)_ | - | - | - |")
        _write_text(scratch / "contract_inventory.md", "\n".join(lines) + "\n")
        return "WRITTEN"
    except Exception as e:
        _write_text(scratch / "contract_inventory.md",
                    f"# Contract Inventory\n\n[LLM TO ENRICH] pre-pass failed: {e}\n")
        return "FAILED"

def _write_table_artifact(scratch: Path, proj: Path, lang: str, kind: str) -> str:
    """kind: 'state' or 'fn' — dispatches to LANG_DISPATCH row function."""
    filename = {"state": "state_variables.md", "fn": "function_list.md"}[kind]
    title = {"state": "State Variables", "fn": "Function List"}[kind]
    header = {"state": "| File | Variable | Type | Line |",
              "fn":    "| File | Function | Visibility | Line |"}[kind]
    sep = {"state": "|------|----------|------|------|",
           "fn":    "|------|----------|------------|------|"}[kind]

    try:
        cfg = LANG_DISPATCH.get(lang)
        if not cfg:
            _write_text(scratch / filename,
                        f"# {title}\n\n[LLM TO ENRICH] Unknown language: {lang}\n")
            return "STUB"
        rows: List[str] = []
        for f in _gather_files(proj, lang):
            text = _read_text(f)
            if not text:
                continue
            rows.extend(cfg[kind](text, f, proj))

        lines = [f"# {title}", "",
                 f"Pre-pass: {len(rows)} {kind}(s) identified via regex scan.",
                 "Regex-based heuristic — LLM recon may add/correct entries.", "",
                 header, sep]
        lines.extend(rows if rows else ["| _(none found)_ | - | - | - |"])
        _write_text(scratch / filename, "\n".join(lines) + "\n")
        return "WRITTEN"
    except Exception as e:
        _write_text(scratch / filename, f"# {title}\n\n[LLM TO ENRICH] pre-pass failed: {e}\n")
        return "FAILED"

# Build status
BUILD_SPECS = {
    "evm_forge":    {"cmd": ["forge", "build", "--no-auto-detect"],    "timeout": 120},
    "evm_hardhat":  {"cmd": ["npx", "hardhat", "compile"],             "timeout": 120},
    "solana":       {"cmd": ["cargo", "build", "--release"],           "timeout": 300},
    "soroban":      {"cmd": ["cargo", "build", "--release"],           "timeout": 300},
    "aptos":        {"cmd": ["aptos", "move", "compile"],              "timeout": 120},
    "sui":          {"cmd": ["sui", "move", "build"],                  "timeout": 120},
}

def _select_build(proj: Path, lang: str) -> Optional[str]:
    if lang == "evm":
        if (proj / "foundry.toml").exists() and shutil.which("forge"):
            return "evm_forge"
        if list(proj.glob("hardhat.config.*")) and shutil.which("npx"):
            return "evm_hardhat"
        return None
    if lang in ("solana", "soroban") and shutil.which("cargo"):
        return lang
    if lang == "aptos" and shutil.which("aptos"):
        return "aptos"
    if lang == "sui" and shutil.which("sui"):
        return "sui"
    return None

def _tail(text: str, n: int = 2048) -> str:
    if not text:
        return ""
    if len(text) <= n:
        return text
    return "... [truncated] ...\n" + text[-n:]

def _write_build_status(scratch: Path, proj: Path, lang: str) -> str:
    try:
        key = _select_build(proj, lang)
        if not key:
            _write_text(scratch / "build_status.md",
                        "# Build Status\n\n"
                        f"**Tool**: (none detected for lang={lang})\n\n"
                        "**Status**: SKIPPED\n\n"
                        "No build tool / manifest detected. LLM recon may re-attempt.\n")
            return "STUB"
        spec = BUILD_SPECS[key]
        cmd = spec["cmd"]
        timeout = spec["timeout"]
        timed_out = False
        try:
            proc = subprocess.run(cmd, cwd=str(proj), timeout=timeout,
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            rc, so, se = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as e:
            rc = 124
            so = e.stdout if isinstance(e.stdout, str) else ""
            se = e.stderr if isinstance(e.stderr, str) else ""
            timed_out = True
        except FileNotFoundError:
            rc, so, se = 127, "", f"binary not found: {cmd[0]}"
        except Exception as e:
            rc, so, se = 1, "", f"exception: {e}"

        status = "SUCCESS" if rc == 0 else ("TIMEOUT" if timed_out else "FAILED")
        content = (
            "# Build Status\n\n"
            f"**Tool**: {key}\n"
            f"**Command**: `{' '.join(cmd)}`\n"
            f"**CWD**: `{proj}`\n"
            f"**Timeout**: {timeout}s\n"
            f"**Exit Code**: {rc}\n"
            f"**Status**: {status}\n\n"
            "## stdout (tail)\n```\n" + _tail(so) + "\n```\n\n"
            "## stderr (tail)\n```\n" + _tail(se) + "\n```\n"
        )
        _write_text(scratch / "build_status.md", content)
        return "WRITTEN"
    except Exception as e:
        _write_text(scratch / "build_status.md",
                    f"# Build Status\n\n**Status**: FAILED\n\nPre-pass exception: {e}\n")
        return "FAILED"

# L1 artifacts
_L1_SUBSYSTEMS = {
    "consensus": ("consensus", "fork_choice", "beacon", "slashing"),
    "p2p":       ("p2p", "network", "libp2p", "discovery"),
    "mempool":   ("txpool", "mempool", "blob_pool"),
    "rpc":       ("rpc", "engine_api", "api"),
    "state":     ("state", "storage", "pruning", "snapshot"),
    "execution": ("vm", "evm", "revm", "interpreter"),
}
_L1_SOURCE_SUFFIXES = (".go", ".rs", ".ts", ".py")
_L1_FN_GO_RE = re.compile(r"^\s*func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(", re.MULTILINE)
_L1_FN_RUST_RE = re.compile(r"^\s*pub(?:\s*\([^)]*\))?\s+(?:async\s+)?fn\s+(\w+)", re.MULTILINE)

def _dir_stats(d: Path) -> Tuple[int, int]:
    files = 0
    loc = 0
    for root, dns, fns in os.walk(d):
        dns[:] = [x for x in dns if x not in SKIP_DIR_NAMES and not x.startswith(".")]
        for fn in fns:
            if fn.endswith(_L1_SOURCE_SUFFIXES):
                files += 1
                ln, _ = _lines_and_bytes(Path(root) / fn)
                loc += ln
    return files, loc

def _write_subsystem_map_l1(scratch: Path, proj: Path) -> str:
    try:
        buckets: Dict[str, List[Path]] = {k: [] for k in _L1_SUBSYSTEMS}
        for dirpath, dirnames, _ in os.walk(proj):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
            dn = Path(dirpath).name.lower()
            for sub, kws in _L1_SUBSYSTEMS.items():
                if dn in kws:
                    buckets[sub].append(Path(dirpath))

        rows: List[str] = []
        total_files = 0
        for sub, dirs in buckets.items():
            for d in sorted(dirs):
                files, loc = _dir_stats(d)
                total_files += files
                rows.append(f"| `{_rel(d, proj)}` | {sub} | {loc} | {files} |")

        lines = ["# Subsystem Map", "",
                 f"Pre-pass: {len(rows)} dir matches ({total_files} source files).", "",
                 "| Dir | Subsystem | LOC | Files |",
                 "|-----|-----------|-----|-------|"]
        lines.extend(rows if rows else ["| _(no subsystem dirs detected)_ | - | - | - |"])
        _write_text(scratch / "subsystem_map.md", "\n".join(lines) + "\n")
        return "WRITTEN"
    except Exception as e:
        _write_text(scratch / "subsystem_map.md",
                    f"# Subsystem Map\n\n[LLM TO ENRICH] pre-pass failed: {e}\n")
        return "FAILED"

def _write_trust_boundaries_l1(scratch: Path, proj: Path) -> str:
    try:
        tops = [e for e in sorted(proj.iterdir())
                if e.is_dir() and e.name not in SKIP_DIR_NAMES and not e.name.startswith(".")]
        ext_kw = ("rpc", "p2p", "network", "api", "engine_api", "libp2p", "discovery")
        lines = ["# Trust Boundaries", "",
                 "Pre-pass stub: top-level dirs classified by name heuristic.",
                 "LLM recon MUST enrich with real trust-boundary analysis.", "",
                 "| Top-Level Dir | Classification | Notes |",
                 "|---------------|---------------|-------|"]
        for d in tops:
            cls = "external" if any(k in d.name.lower() for k in ext_kw) else "internal"
            lines.append(f"| `{d.name}` | {cls} | heuristic |")
        if not tops:
            lines.append("| _(no top-level dirs)_ | - | - |")
        _write_text(scratch / "trust_boundaries.md", "\n".join(lines) + "\n")
        return "WRITTEN"
    except Exception as e:
        _write_text(scratch / "trust_boundaries.md",
                    f"# Trust Boundaries\n\n[LLM TO ENRICH] pre-pass failed: {e}\n")
        return "FAILED"

def _write_attack_surface_l1(scratch: Path, proj: Path) -> str:
    try:
        surface_kws = set(_L1_SUBSYSTEMS["rpc"] + _L1_SUBSYSTEMS["p2p"])
        surface_dirs: List[Path] = []
        for dirpath, dirnames, _ in os.walk(proj):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
            if Path(dirpath).name.lower() in surface_kws:
                surface_dirs.append(Path(dirpath))

        rows: List[str] = []
        for d in sorted(set(surface_dirs)):
            for root, dns, fns in os.walk(d):
                dns[:] = [x for x in dns if x not in SKIP_DIR_NAMES and not x.startswith(".")]
                for fn in fns:
                    fp = Path(root) / fn
                    text = _read_text(fp)
                    if not text:
                        continue
                    if fn.endswith(".go"):
                        for m in _L1_FN_GO_RE.finditer(text):
                            rows.append(f"| `{_rel(fp, proj)}` | `{m.group(1)}` | go | {_line_of(text, m.start())} |")
                    elif fn.endswith(".rs"):
                        for m in _L1_FN_RUST_RE.finditer(text):
                            rows.append(f"| `{_rel(fp, proj)}` | `{m.group(1)}` | rust | {_line_of(text, m.start())} |")

        lines = ["# Attack Surface (L1 pre-pass)", "",
                 f"Pre-pass: {len(rows)} exported fn(s) in rpc/ and p2p/ dirs.",
                 "LLM recon MUST enrich with real attack-surface analysis.", "",
                 "| File | Function | Lang | Line |",
                 "|------|----------|------|------|"]
        lines.extend(rows if rows else ["| _(no RPC/P2P entry points found)_ | - | - | - |"])
        _write_text(scratch / "attack_surface.md", "\n".join(lines) + "\n")
        return "WRITTEN"
    except Exception as e:
        _write_text(scratch / "attack_surface.md",
                    f"# Attack Surface\n\n[LLM TO ENRICH] pre-pass failed: {e}\n")
        return "FAILED"

# Shared stubs
def _write_design_or_threat_stub(scratch: Path, pipeline: str) -> str:
    try:
        if pipeline == "l1":
            target = scratch / "threat_model.md"
            body = ("# Threat Model\n\n"
                    "[LLM TO ENRICH] Pre-pass stub. LLM recon MUST replace each section.\n\n"
                    "## Node Role / Deployment Context\n[LLM TO ENRICH]\n\n"
                    "## Trust Model\n[LLM TO ENRICH]\n\n"
                    "## Attacker Capabilities\n[LLM TO ENRICH]\n\n"
                    "## Critical Invariants\n[LLM TO ENRICH]\n\n"
                    "## Operational Implications\n[LLM TO ENRICH]\n")
        else:
            target = scratch / "design_context.md"
            body = ("# Design Context\n\n"
                    "[LLM TO ENRICH] Pre-pass stub. LLM recon MUST replace each section.\n\n"
                    "## Protocol Summary\n[LLM TO ENRICH]\n\n"
                    "## Key Invariants\n[LLM TO ENRICH]\n\n"
                    "## Operational Implications\n[LLM TO ENRICH]\n\n"
                    "## Trust Assumptions\n[LLM TO ENRICH]\n\n"
                    "## Fork Ancestry\n[LLM TO ENRICH]\n")
        return "STUB" if _write_text(target, body) else "FAILED"
    except Exception:
        return "FAILED"

def _write_sc_recon_stub(scratch: Path, name: str, body: str) -> str:
    """Write a minimal stub for SC recon artifacts not covered by mechanical extraction."""
    return "STUB" if _write_text(scratch / name, body) else "FAILED"


def _write_recon_summary_stub(scratch: Path, proj: Path, lang: str) -> str:
    body = ("# Recon Summary\n\n"
            "[LLM TO ENRICH] Pre-pass stub.\n\n"
            f"- **Target**: `{proj}`\n"
            f"- **Language**: {lang}\n"
            "- **Themes**: [LLM TO ENRICH]\n"
            "- **Risk Areas**: [LLM TO ENRICH]\n"
            "- **Recommended Lanes**: [LLM TO ENRICH — see template_recommendations.md]\n")
    return "STUB" if _write_text(scratch / "recon_summary.md", body) else "FAILED"

def _write_meta_buffer_stub(scratch: Path) -> str:
    return "STUB" if _write_text(scratch / "meta_buffer.md",
                                   "# RAG Meta Buffer\n(optional)\n") else "FAILED"

# template_recommendations.md — extract from skill-index.md
_LANG_HEADING = {
    "evm":     "## EVM Skills",
    "solana":  "## Solana Skills",
    "aptos":   "## Aptos Skills",
    "sui":     "## Sui Skills",
    "soroban": "## Soroban Skills",
    "l1":      "## L1 Skills",
}

def _extract_skill_table(text: str, heading: str) -> List[Tuple[str, str]]:
    idx = text.find(heading)
    if idx < 0:
        return []
    end = text.find("\n## ", idx + 1)
    section = text[idx:end if end > 0 else len(text)]
    out: List[Tuple[str, str]] = []
    in_table = False
    for line in section.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not in_table and cells and cells[0].lower() in ("skill", "name"):
                in_table = True
                continue
            if in_table and cells and set("".join(cells)) <= set("- :"):
                continue
            if in_table and len(cells) >= 2:
                name = cells[0].strip("`").strip()
                trigger = cells[1]
                if name and not name.startswith("_"):
                    out.append((name, trigger))
        else:
            if in_table and not s:
                in_table = False
    return out

def _write_template_recommendations(scratch: Path, skill_index: Path,
                                    lang: str, pipeline: str) -> str:
    try:
        if not skill_index.exists():
            _write_text(scratch / "template_recommendations.md",
                        "# Template Recommendations\n\n[LLM TO ENRICH] skill-index.md not found.\n")
            return "STUB"
        text = _read_text(skill_index)
        sections: List[Tuple[str, List[Tuple[str, str]]]] = []
        if pipeline == "l1":
            sections.append(("L1 Skills", _extract_skill_table(text, "## L1 Skills")))
        else:
            h = _LANG_HEADING.get(lang)
            if h:
                sections.append((f"{lang.upper()} Skills", _extract_skill_table(text, h)))
            sections.append(("Injectable Skills", _extract_skill_table(text, "## Injectable Skills")))
            sections.append(("Niche Agents", _extract_skill_table(text, "## Niche Agents")))

        lines = [
            "# Template Recommendations", "",
            "[LLM TO ENRICH] Pre-pass stub. Every row below is `Required=NO` by default.",
            "LLM recon MUST flip `Required` to **YES** for skills whose trigger pattern",
            "matches this codebase, and add rationale.", "",
            "## BINDING MANIFEST", "",
        ]
        for name, rows in sections:
            lines += [f"### {name}", "",
                      "| Skill | Trigger | Required | Rationale |",
                      "|-------|---------|----------|-----------|"]
            if not rows:
                lines.append("| _(none extracted)_ | - | - | - |")
            lines.extend(f"| `{sk}` | {trig} | NO | [LLM TO ENRICH] |" for sk, trig in rows)
            lines.append("")
        _write_text(scratch / "template_recommendations.md", "\n".join(lines))
        return "STUB"
    except Exception as e:
        _write_text(scratch / "template_recommendations.md",
                    f"# Template Recommendations\n\n[LLM TO ENRICH] pre-pass failed: {e}\n")
        return "FAILED"

# SCIP bake for Rust-based SC pipelines (v2.5.0 P1)

_RUST_ANALYZER_SCIP_TIMEOUT = 180  # seconds

def _bake_rust_scip(scratch: Path, proj: Path) -> str:
    """Run `rust-analyzer scip` on a Rust project and generate graph artifacts.

    Produces caller_map.md, callee_map.md, state_write_map.md, function_summary.md
    from the SCIP index — the same artifacts depth agents expect.

    Returns status string: WRITTEN | SKIPPED | FAILED:{reason}
    """
    if not shutil.which("rust-analyzer"):
        return "SKIPPED:rust-analyzer not found"

    cargo_toml = proj / "Cargo.toml"
    if not cargo_toml.exists():
        return "SKIPPED:no Cargo.toml"

    index_path = scratch / "scip_rust.index"

    # Run rust-analyzer scip
    try:
        proc = subprocess.run(
            ["rust-analyzer", "scip", str(proj), "--exclude-vendored-libraries"],
            cwd=str(proj),
            timeout=_RUST_ANALYZER_SCIP_TIMEOUT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        # rust-analyzer scip writes index.scip in the project root
        ra_index = proj / "index.scip"
        if proc.returncode != 0:
            return f"FAILED:rust-analyzer scip exit {proc.returncode}"
        if not ra_index.exists() or ra_index.stat().st_size < 100:
            return "FAILED:index.scip not produced or empty"
        # Move to scratchpad
        shutil.move(str(ra_index), str(index_path))
    except subprocess.TimeoutExpired:
        return f"FAILED:timeout after {_RUST_ANALYZER_SCIP_TIMEOUT}s"
    except FileNotFoundError:
        return "SKIPPED:rust-analyzer not found"
    except Exception as e:
        return f"FAILED:{e.__class__.__name__}"

    # Convert SCIP index to graph artifacts
    return _scip_to_graph_artifacts(scratch, index_path, proj)


def _scip_to_graph_artifacts(scratch: Path, index_path: Path, proj: Path) -> str:
    """Convert a SCIP index into the 4 graph artifacts depth agents consume."""
    try:
        sys_path_added = False
        scip_reader_dir = Path(os.path.expanduser("~/.claude"))
        if str(scip_reader_dir) not in sys.path:
            sys.path.insert(0, str(scip_reader_dir))
            sys_path_added = True
        try:
            from plamen_l1.scip_reader import ScipReader
        except ImportError:
            return "FAILED:scip_reader not importable (missing protobuf bindings?)"
        finally:
            if sys_path_added and str(scip_reader_dir) in sys.path:
                sys.path.remove(str(scip_reader_dir))

        reader = ScipReader(str(index_path))
        stats = reader.stats()

        if stats["definitions"] < 5:
            return f"FAILED:SCIP index has only {stats['definitions']} definitions"

        # Build caller/callee maps from SCIP references
        callers: Dict[str, List[str]] = {}  # fn_name -> [caller locations]
        callees: Dict[str, List[str]] = {}  # fn_name -> [callee locations]
        fn_info: Dict[str, dict] = {}       # fn_name -> {path, line, kind, ...}
        state_writers: Dict[str, List[str]] = {}  # var_name -> [writer locations]

        # Collect all definitions and their references
        for sym, defn_occ in reader._definitions.items():
            name = reader._extract_name_from_symbol(sym)
            if not name or name.startswith("_") and len(name) < 3:
                continue
            info = reader._symbol_info.get(sym)
            kind = info.kind if info else ""

            # Function-like symbols
            if kind in ("Function", "Method", "Constructor", "") and "()" in sym:
                path_str = defn_occ.relative_path
                fn_info[name] = {
                    "path": path_str,
                    "line": defn_occ.start_line + 1,
                    "kind": kind or "Function",
                    "signature": (info.signature if info else ""),
                }

                # Build callers from references
                refs = reader._references.get(sym, [])
                caller_locs = []
                callee_locs = []
                for ref in refs:
                    loc = f"{ref.relative_path}:L{ref.start_line + 1}"
                    caller_locs.append(loc)
                if caller_locs:
                    callers[name] = caller_locs

            # Field/variable symbols for state_write_map
            elif kind in ("Field", "Property", "Variable", ""):
                if "()" not in sym:
                    refs = reader._references.get(sym, [])
                    writer_locs = [
                        f"{ref.relative_path}:L{ref.start_line + 1}"
                        for ref in refs
                    ]
                    if writer_locs:
                        state_writers[name] = writer_locs

        # For callee_map: invert — for each function definition, find what it calls
        # by scanning references that occur within its body range
        for fn_name, fn_data in fn_info.items():
            fn_path = fn_data["path"]
            fn_line = fn_data["line"]
            # Simple heuristic: callees are other functions whose references
            # appear in the same file near this function's definition
            called = []
            for other_name, other_data in fn_info.items():
                if other_name == fn_name:
                    continue
                other_sym = None
                for s, d in reader._definitions.items():
                    if reader._extract_name_from_symbol(s) == other_name:
                        other_sym = s
                        break
                if not other_sym:
                    continue
                for ref in reader._references.get(other_sym, []):
                    if ref.relative_path == fn_path:
                        called.append(other_name)
                        break
            if called:
                callees[fn_name] = called[:20]

        # Write caller_map.md
        lines = [
            "> **Status**: POPULATED",
            "> **Source**: rust-analyzer SCIP index (v2.5.0 P1)",
            "",
            "# Caller Map",
            "",
            "| Function | Callers | Count |",
            "|----------|---------|-------|",
        ]
        for fn_name in sorted(callers.keys()):
            locs = callers[fn_name]
            lines.append(f"| `{fn_name}` | {'; '.join(locs[:10])} | {len(locs)} |")
        _write_text(scratch / "caller_map.md", "\n".join(lines))

        # Write callee_map.md
        lines = [
            "> **Status**: POPULATED",
            "> **Source**: rust-analyzer SCIP index (v2.5.0 P1)",
            "",
            "# Callee Map",
            "",
            "| Function | Callees |",
            "|----------|---------|",
        ]
        for fn_name in sorted(callees.keys()):
            clist = callees[fn_name]
            lines.append(f"| `{fn_name}` | {', '.join(clist)} |")
        _write_text(scratch / "callee_map.md", "\n".join(lines))

        # Write state_write_map.md
        lines = [
            "> **Status**: POPULATED",
            "> **Source**: rust-analyzer SCIP index (v2.5.0 P1)",
            "",
            "# State Write Map",
            "",
            "| Variable | Writer Locations | Count |",
            "|----------|-----------------|-------|",
        ]
        for var_name in sorted(state_writers.keys()):
            locs = state_writers[var_name]
            lines.append(f"| `{var_name}` | {'; '.join(locs[:10])} | {len(locs)} |")
        _write_text(scratch / "state_write_map.md", "\n".join(lines))

        # Write function_summary.md
        lines = [
            "> **Status**: POPULATED",
            "> **Source**: rust-analyzer SCIP index (v2.5.0 P1)",
            "",
            "# Function Summary",
            "",
            "| Function | File | Line | Kind | Callers | Callees |",
            "|----------|------|------|------|---------|---------|",
        ]
        for fn_name in sorted(fn_info.keys()):
            data = fn_info[fn_name]
            n_callers = len(callers.get(fn_name, []))
            n_callees = len(callees.get(fn_name, []))
            lines.append(
                f"| `{fn_name}` | {data['path']} | {data['line']} "
                f"| {data['kind']} | {n_callers} | {n_callees} |"
            )
        _write_text(scratch / "function_summary.md", "\n".join(lines))

        # Record status
        status_lines = [
            f"- SCIP_RUST_BAKE: COMPLETE",
            f"- SCIP_RUST_INDEX: {index_path}",
            f"- SCIP_DEFINITIONS: {stats['definitions']}",
            f"- SCIP_DOCUMENTS: {stats['documents']}",
            f"- SCIP_GRAPH_ARTIFACTS: caller_map.md, callee_map.md, state_write_map.md, function_summary.md",
        ]
        bs = scratch / "build_status.md"
        if bs.exists():
            try:
                existing = bs.read_text(encoding="utf-8", errors="replace")
                if not any("SCIP_RUST_BAKE" in l for l in existing.splitlines()):
                    bs.write_text(
                        existing.rstrip() + "\n\n## SCIP Bake\n" + "\n".join(status_lines) + "\n",
                        encoding="utf-8",
                    )
            except Exception:
                pass

        return f"WRITTEN:{stats['definitions']} defs, {stats['documents']} docs"

    except Exception as e:
        return f"FAILED:{e.__class__.__name__}:{e}"


# OpenGrep cross-ecosystem scanner (v2.5.0 P2)

_OPENGREP_SCAN_TIMEOUT = 300  # seconds
_OPENGREP_RULES_BASE = Path(os.path.expanduser("~/.plamen/opengrep-rules"))
_OPENGREP_RULE_REPOS = {
    "opengrep-rules": "https://github.com/opengrep/opengrep-rules.git",
    "decurity-rules": "https://github.com/Decurity/semgrep-smart-contracts.git",
    "aptos-move-rules": "https://github.com/aptos-labs/semgrep-move-rules.git",
}
_OPENGREP_LANG_RULES: Dict[str, List[str]] = {
    "evm": ["opengrep-rules/solidity", "decurity-rules/solidity/security"],
    "solana": ["opengrep-rules/rust", "decurity-rules/rust"],
    "soroban": ["opengrep-rules/rust", "decurity-rules/rust"],
    "aptos": ["aptos-move-rules/rules"],
    "sui": [],
}
_OPENGREP_LANG_EXT: Dict[str, Tuple[str, ...]] = {
    "evm": (".sol",),
    "solana": (".rs",),
    "soroban": (".rs",),
    "aptos": (".move",),
    "sui": (".move",),
}


def _ensure_opengrep_rules() -> Dict[str, Path]:
    """Clone rule repos if missing. Returns {name: local_path} for present repos."""
    _OPENGREP_RULES_BASE.mkdir(parents=True, exist_ok=True)
    available: Dict[str, Path] = {}
    for name, url in _OPENGREP_RULE_REPOS.items():
        local = _OPENGREP_RULES_BASE / name
        if local.exists() and (local / ".git").exists():
            available[name] = local
            continue
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(local)],
                timeout=60, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
            if local.exists():
                available[name] = local
        except Exception:
            pass
    return available


def _run_opengrep_scan(scratch: Path, proj: Path, lang: str) -> str:
    """Run OpenGrep scan and write results to scratchpad.

    Produces: opengrep_results.sarif (raw), opengrep_findings.md (summary).
    Returns status string: WRITTEN:{n} findings | SKIPPED:{reason} | FAILED:{reason}
    """
    if not shutil.which("opengrep"):
        return "SKIPPED:opengrep not found"

    rule_dirs = _OPENGREP_LANG_RULES.get(lang, [])
    if not rule_dirs:
        return f"SKIPPED:no OpenGrep rules for {lang}"

    # Ensure rule repos are cloned
    available_repos = _ensure_opengrep_rules()

    # Resolve rule paths
    resolved_rules: List[str] = []
    for rule_rel in rule_dirs:
        repo_name = rule_rel.split("/")[0]
        if repo_name not in available_repos:
            continue
        full_path = available_repos[repo_name] / "/".join(rule_rel.split("/")[1:])
        if full_path.exists():
            resolved_rules.append(str(full_path))

    if not resolved_rules:
        return "SKIPPED:no rule directories available"

    # Check project has relevant source files
    exts = _OPENGREP_LANG_EXT.get(lang, ())
    source_files = _iter_files(proj, exts)
    if not source_files:
        return f"SKIPPED:no {'/'.join(exts)} files in project"

    sarif_path = scratch / "opengrep_results.sarif"
    cmd = ["opengrep", "scan"]
    for rp in resolved_rules:
        cmd.extend(["-f", rp])
    cmd.extend(["--sarif-output", str(sarif_path), str(proj)])

    try:
        proc = subprocess.run(
            cmd, cwd=str(proj), timeout=_OPENGREP_SCAN_TIMEOUT,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"FAILED:timeout after {_OPENGREP_SCAN_TIMEOUT}s"
    except FileNotFoundError:
        return "SKIPPED:opengrep not found"
    except Exception as e:
        return f"FAILED:{e.__class__.__name__}"

    # opengrep returns 0 on success (even with findings), 1 on findings in some modes
    if not sarif_path.exists() or sarif_path.stat().st_size < 10:
        if proc.returncode != 0:
            return f"FAILED:exit {proc.returncode}, no SARIF produced"
        return "WRITTEN:0 findings"

    # Parse SARIF and write human-readable summary
    finding_count = _parse_opengrep_sarif(scratch, sarif_path)

    # Record in build_status.md
    bs = scratch / "build_status.md"
    if bs.exists():
        try:
            existing = bs.read_text(encoding="utf-8", errors="replace")
            if "OPENGREP" not in existing:
                bs.write_text(
                    existing.rstrip() + "\n\n## OpenGrep\n"
                    f"- OPENGREP_AVAILABLE: true\n"
                    f"- OPENGREP_FINDINGS: {finding_count}\n"
                    f"- OPENGREP_RULES: {', '.join(resolved_rules)}\n",
                    encoding="utf-8",
                )
        except Exception:
            pass

    return f"WRITTEN:{finding_count} findings"


def _parse_opengrep_sarif(scratch: Path, sarif_path: Path) -> int:
    """Parse SARIF output and write opengrep_findings.md summary. Returns finding count."""
    import json as _json

    try:
        data = _json.loads(sarif_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        _write_text(scratch / "opengrep_findings.md",
                     "# OpenGrep Findings\n\n> SARIF parse failed\n")
        return 0

    findings: List[dict] = []
    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "unknown")
            message = result.get("message", {}).get("text", "")
            level = result.get("level", "warning")
            locations = result.get("locations", [])
            loc_str = ""
            if locations:
                phys = locations[0].get("physicalLocation", {})
                art = phys.get("artifactLocation", {}).get("uri", "")
                region = phys.get("region", {})
                line = region.get("startLine", 0)
                loc_str = f"{art}:L{line}" if art else ""

            findings.append({
                "rule": rule_id,
                "message": message[:200],
                "level": level,
                "location": loc_str,
            })

    # Write summary
    lines = [
        "# OpenGrep Findings",
        "",
        f"> **Total**: {len(findings)} findings",
        f"> **Source**: OpenGrep SARIF scan (v2.5.0 P2)",
        "",
        "| # | Rule | Level | Location | Message |",
        "|---|------|-------|----------|---------|",
    ]
    for i, f in enumerate(findings, 1):
        msg = f["message"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | `{f['rule']}` | {f['level']} | `{f['location']}` | {msg} |")
    _write_text(scratch / "opengrep_findings.md", "\n".join(lines))

    return len(findings)


# Sec3 X-Ray Solana scanner (v2.5.0 P4)

_SEC3_XRAY_IMAGE = "ghcr.io/sec3-product/x-ray:latest"
_SEC3_XRAY_TIMEOUT = 600  # seconds — Docker pull + LLVM analysis can be slow
_SEC3_SARIF_FILENAME = "sec3-report.sarif"


def _run_sec3_xray(scratch: Path, proj: Path) -> str:
    """Run Sec3 X-Ray scanner via Docker and write results to scratchpad.

    Produces: sec3_results.sarif (raw), sec3_findings.md (summary).
    Returns status string: WRITTEN:{n} findings | SKIPPED:{reason} | FAILED:{reason}
    """
    if not shutil.which("docker"):
        return "SKIPPED:docker not found"

    # Verify Docker is running
    try:
        probe = subprocess.run(
            ["docker", "info"], timeout=15,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if probe.returncode != 0:
            return "SKIPPED:docker daemon not running"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "SKIPPED:docker not available"
    except Exception:
        return "SKIPPED:docker probe failed"

    # Check project has Rust/Solana source files
    source_files = _iter_files(proj, (".rs",))
    if not source_files:
        return "SKIPPED:no .rs files in project"

    # X-Ray mounts workspace and writes SARIF to the project root
    proj_posix = str(proj).replace("\\", "/")
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{proj_posix}:/workspace",
        _SEC3_XRAY_IMAGE,
        "/workspace",
    ]

    try:
        proc = subprocess.run(
            cmd, timeout=_SEC3_XRAY_TIMEOUT,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"FAILED:timeout after {_SEC3_XRAY_TIMEOUT}s"
    except FileNotFoundError:
        return "SKIPPED:docker not found"
    except Exception as e:
        return f"FAILED:{e.__class__.__name__}"

    # X-Ray writes SARIF into the workspace root
    sarif_source = proj / _SEC3_SARIF_FILENAME
    sarif_dest = scratch / "sec3_results.sarif"

    if not sarif_source.exists():
        # Some versions write to current dir or use different name
        alt_names = ["x-ray-report.sarif", "report.sarif", "xray.sarif"]
        for alt in alt_names:
            alt_path = proj / alt
            if alt_path.exists():
                sarif_source = alt_path
                break

    if not sarif_source.exists() or sarif_source.stat().st_size < 10:
        if proc.returncode != 0:
            return f"FAILED:exit {proc.returncode}, no SARIF produced"
        return "WRITTEN:0 findings"

    # Move SARIF to scratchpad
    try:
        shutil.copy2(str(sarif_source), str(sarif_dest))
        sarif_source.unlink()
    except Exception:
        pass

    if not sarif_dest.exists():
        # Fallback: if copy failed, try reading from source
        if sarif_source.exists():
            sarif_dest = sarif_source
        else:
            return "FAILED:SARIF copy failed"

    # Parse SARIF and write human-readable summary
    finding_count = _parse_sec3_sarif(scratch, sarif_dest)

    # Record in build_status.md
    bs = scratch / "build_status.md"
    if bs.exists():
        try:
            existing = bs.read_text(encoding="utf-8", errors="replace")
            if "SEC3" not in existing:
                bs.write_text(
                    existing.rstrip() + "\n\n## Sec3 X-Ray\n"
                    f"- SEC3_XRAY_AVAILABLE: true\n"
                    f"- SEC3_FINDINGS: {finding_count}\n",
                    encoding="utf-8",
                )
        except Exception:
            pass

    return f"WRITTEN:{finding_count} findings"


def _parse_sec3_sarif(scratch: Path, sarif_path: Path) -> int:
    """Parse Sec3 X-Ray SARIF output and write sec3_findings.md summary. Returns finding count."""
    import json as _json

    try:
        data = _json.loads(sarif_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        _write_text(scratch / "sec3_findings.md",
                     "# Sec3 X-Ray Findings\n\n> SARIF parse failed\n")
        return 0

    findings: List[dict] = []
    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "unknown")
            message = result.get("message", {}).get("text", "")
            level = result.get("level", "warning")
            locations = result.get("locations", [])
            loc_str = ""
            if locations:
                phys = locations[0].get("physicalLocation", {})
                art = phys.get("artifactLocation", {}).get("uri", "")
                region = phys.get("region", {})
                line = region.get("startLine", 0)
                loc_str = f"{art}:L{line}" if art else ""

            findings.append({
                "rule": rule_id,
                "message": message[:200],
                "level": level,
                "location": loc_str,
            })

    lines = [
        "# Sec3 X-Ray Findings",
        "",
        f"> **Total**: {len(findings)} findings",
        f"> **Source**: Sec3 X-Ray SARIF scan (v2.5.0 P4)",
        "",
        "| # | Rule | Level | Location | Message |",
        "|---|------|-------|----------|---------|",
    ]
    for i, f in enumerate(findings, 1):
        msg = f["message"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | `{f['rule']}` | {f['level']} | `{f['location']}` | {msg} |")
    _write_text(scratch / "sec3_findings.md", "\n".join(lines))

    return len(findings)


# Main entry point
def run_recon_prepass(config: dict) -> Dict[str, str]:
    """Write mechanical recon artifacts. Returns {artifact: status} dict."""
    results: Dict[str, str] = {}

    def _safe(name: str, fn):
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = f"FAILED:{e.__class__.__name__}"

    try:
        scratch = Path(config["scratchpad"])
        proj = Path(config["project_root"])
        lang = (config.get("language") or "evm").lower()
        pipeline = (config.get("pipeline") or "sc").lower()
    except Exception as e:
        return {"_init": f"FAILED:{e}"}

    try:
        scratch.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"_mkdir_scratch": f"FAILED:{e}"}

    skill_index = Path(os.path.expanduser("~/.claude/rules/skill-index.md"))

    if pipeline == "l1":
        _safe("subsystem_map.md",    lambda: _write_subsystem_map_l1(scratch, proj))
        _safe("trust_boundaries.md", lambda: _write_trust_boundaries_l1(scratch, proj))
        _safe("attack_surface.md",   lambda: _write_attack_surface_l1(scratch, proj))
        _safe("threat_model.md",     lambda: _write_design_or_threat_stub(scratch, pipeline))
    else:
        _safe("contract_inventory.md", lambda: _write_contract_inventory_sc(scratch, proj, lang))
        _safe("state_variables.md",    lambda: _write_table_artifact(scratch, proj, lang, "state"))
        _safe("function_list.md",      lambda: _write_table_artifact(scratch, proj, lang, "fn"))
        _safe("build_status.md",       lambda: _write_build_status(scratch, proj, lang))
        _safe("design_context.md",     lambda: _write_design_or_threat_stub(scratch, pipeline))
        # v2.8.6: stub the 4 artifacts the pre-pass previously skipped.
        # When Codex sub-agents partially fail, these stay at 0 bytes and
        # trip the recon gate.  Non-zero stubs let the pipeline degrade
        # gracefully instead of hard-failing on partial Codex output.
        _safe("attack_surface.md",     lambda: _write_sc_recon_stub(scratch, "attack_surface.md",
              "# Attack Surface\n\n[LLM TO ENRICH] Pre-pass stub.\n\n"
              "## Entry Points\n[LLM TO ENRICH]\n\n"
              "## External Dependencies\n[LLM TO ENRICH]\n"))
        _safe("detected_patterns.md",  lambda: _write_sc_recon_stub(scratch, "detected_patterns.md",
              "# Detected Patterns\n\n[LLM TO ENRICH] Pre-pass stub.\n\n"
              "## Flags\n[LLM TO ENRICH]\n"))
        _safe("setter_list.md",        lambda: _write_sc_recon_stub(scratch, "setter_list.md",
              "# Setter List\n\n[LLM TO ENRICH] Pre-pass stub.\n\n"
              "| Contract | Function | Parameter | Modifier |\n"
              "|----------|----------|-----------|----------|\n"))
        _safe("emit_list.md",          lambda: _write_sc_recon_stub(scratch, "emit_list.md",
              "# Emit List\n\n[LLM TO ENRICH] Pre-pass stub.\n\n"
              "| Contract | Event | Parameters | Emitting Function |\n"
              "|----------|-------|------------|-------------------|\n"))
        # v2.5.0 P1: SCIP bake for Rust-based chains (Solana/Soroban)
        if lang in ("solana", "soroban"):
            _safe("scip_bake", lambda: _bake_rust_scip(scratch, proj))

    _safe("template_recommendations.md",
          lambda: _write_template_recommendations(scratch, skill_index, lang, pipeline))
    _safe("recon_summary.md",
          lambda: _write_recon_summary_stub(scratch, proj, lang))
    _safe("meta_buffer.md", lambda: _write_meta_buffer_stub(scratch))

    # v2.5.0 P2: OpenGrep cross-ecosystem scanner (SC pipelines only).
    #
    # Do not run external scanners in the startup pre-pass by default. The
    # driver has not planted `_v2_checkpoint.json` or printed the first phase
    # yet, so a slow scanner looks like a dead launch. The driver runs this as
    # an optional pre-breadth step where the TUI and disk gate are already
    # active. Keep the old behavior behind an explicit escape hatch for local
    # debugging.
    run_startup_scanners = (
        os.environ.get("PLAMEN_PREPASS_EXTERNAL_SCANNERS") == "1"
        or bool(config.get("prepass_external_scanners"))
    )
    if pipeline != "l1" and run_startup_scanners:
        _safe("opengrep_scan", lambda: _run_opengrep_scan(scratch, proj, lang))

    # v2.5.0 P4: Sec3 X-Ray for Solana (Docker-based, SC only)
    if pipeline != "l1" and lang == "solana":
        _safe("sec3_xray", lambda: _run_sec3_xray(scratch, proj))

    return results


if __name__ == "__main__":
    import json as _json
    import sys as _sys
    if len(_sys.argv) != 2:
        print("Usage: python recon_prepass.py <config.json>")
        _sys.exit(2)
    cfg = _json.loads(Path(_sys.argv[1]).read_text(encoding="utf-8"))
    print(_json.dumps(run_recon_prepass(cfg), indent=2))
