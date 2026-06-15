"""Plamen V2 — machine-contract layer (Ship A).

Layer 1: imports plamen_markdown (Layer 0) only. Does NOT import plamen_parsers/
validators/driver, so those may import THIS without a cycle.

WHY
---
Driver-consumed STATE becomes typed and authoritative; human narrative stays
Markdown. The load-bearing rule (see plan delightful-orbiting-twilight.md):

  - JSON present + valid  -> authoritative.
  - JSON present + invalid -> ContractError with a precise, field-level message
                              (the driver turns this into a retry hint; it never
                              silently falls back to permissive Markdown).
  - JSON absent            -> legacy fallback via section-scoped Markdown AST
                              (model.from_markdown), never a full-document scan.

Each contract model owns: pydantic validation, a ``from_markdown`` section-scoped
importer (legacy fallback), and ``render_markdown`` (driver renders the human
companion from validated state, eliminating JSON<->MD divergence).

Sidecar I/O reuses the proven schema-version + source-fingerprint + idempotent
write convention from plamen_parsers.write_judge_decisions_json_sidecar.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

import plamen_markdown as M

__all__ = [
    "ContractError",
    "PlamenContract",
    "BreadthAgentRow",
    "SpawnManifest",
    "RescanManifest",
    "write_contract_sidecar",
    "read_contract_sidecar",
    "load_contract",
]

_ANALYSIS_OUTPUT_RE = re.compile(r"^analysis_[A-Za-z0-9][A-Za-z0-9_.\-]*\.md$")


class ContractError(Exception):
    """Raised when a JSON sidecar is present but invalid. The message is
    field-level and safe to surface verbatim in a retry hint."""


T = TypeVar("T", bound="PlamenContract")


class PlamenContract(BaseModel):
    """Base for all machine-contract models.

    Subclasses set ``schema_version`` and ``sidecar_name`` and implement
    ``from_markdown`` (section-scoped legacy importer) + ``render_markdown``.
    """

    model_config = ConfigDict(extra="forbid")

    # Class attributes (ClassVar => NOT pydantic fields) — overridden per
    # subclass. ClassVar keeps them off model_dump() and accessible as
    # `model_cls.sidecar_name`.
    schema_version: ClassVar[str] = "plamen.contract.v0"
    sidecar_name: ClassVar[str] = "contract.json"
    # The companion Markdown filename this contract renders / imports from.
    markdown_name: ClassVar[str] = ""

    @classmethod
    def from_markdown(cls: Type[T], md: str) -> T:  # pragma: no cover - abstract
        raise NotImplementedError

    def render_markdown(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


# ───────────────────────── Spawn manifest (Ship B) ──────────────────────────

class BreadthAgentRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_id: str
    focus_area: str
    output: str
    template: str = ""
    required: bool = True
    status: str = "QUEUED"

    @field_validator("agent_id", "focus_area", "output", mode="before")
    @classmethod
    def _strip(cls, v):
        return (str(v) if v is not None else "").strip()

    @field_validator("output")
    @classmethod
    def _valid_output(cls, v: str) -> str:
        name = Path(v).name
        if not _ANALYSIS_OUTPUT_RE.match(name):
            raise ValueError(
                f"output {v!r} is not a valid breadth artifact name "
                f"(expected analysis_<focus>.md)"
            )
        return name


class SpawnManifest(PlamenContract):
    schema_version: ClassVar[str] = "plamen.spawn_manifest.v1"
    sidecar_name: ClassVar[str] = "spawn_manifest.json"
    markdown_name: ClassVar[str] = "spawn_manifest.md"

    agents: list[BreadthAgentRow]

    @field_validator("agents")
    @classmethod
    def _nonempty_unique(cls, v: list[BreadthAgentRow]) -> list[BreadthAgentRow]:
        if not v:
            raise ValueError("spawn manifest has zero breadth agents")
        seen_out: set[str] = set()
        seen_id: set[str] = set()
        for a in v:
            if a.output in seen_out:
                raise ValueError(f"duplicate breadth output {a.output!r}")
            if a.agent_id.lower() in seen_id:
                raise ValueError(f"duplicate agent id {a.agent_id!r}")
            seen_out.add(a.output)
            seen_id.add(a.agent_id.lower())
        return v

    # Driver-facing accessors (single source for count + outputs — kills the
    # count-vs-outputs asymmetry that caused the DODO false HALT).
    def outputs(self) -> list[str]:
        return [a.output for a in self.agents]

    def count(self) -> int:
        return len(self.agents)

    @classmethod
    def from_markdown(cls, md: str) -> "SpawnManifest":
        """Legacy importer: section-scope to `## Breadth Agents`, read the first
        table, build typed rows. Only AGENT rows that are Required (not
        no/skip/merged) become spawned agents. Raises ContractError with a
        precise reason if the section/table/rows can't yield a valid manifest."""
        rows = M.first_section_table(
            md, r"\bbreadth\s+agents?\b",
            required_columns=["agent_id", "expected_output"],
        )
        if not rows:
            # Fall back to a header lacking the exact columns (precise diag).
            rows = M.first_section_table(md, r"\bbreadth\s+agents?\b")
        if not rows:
            raise ContractError(
                "no `## Breadth Agents` section with a table found in "
                "spawn_manifest.md"
            )
        agents: list[BreadthAgentRow] = []
        for r in rows:
            required = r.get("required") or r.get("required_") or ""
            if re.match(r"(?i)^\s*(?:no|n|false|skip|optional|merged)\b", required):
                continue
            out = (r.get("expected_output") or r.get("output")
                   or r.get("output_file") or r.get("expected_file") or "").strip()
            agent_id = (r.get("agent_id") or r.get("agent") or "").strip()
            focus = (r.get("focus_area") or r.get("focus") or "").strip()
            if not out and focus:
                out = f"analysis_{M.normalize_header(focus)}.md"
            if not (agent_id and out):
                # A row that does not name an agent + output is not a spawned
                # breadth agent (e.g. a stray note row); skip it rather than
                # failing the whole manifest.
                continue
            try:
                agents.append(BreadthAgentRow(
                    agent_id=agent_id, focus_area=focus or agent_id, output=out,
                    template=(r.get("template") or "").strip(),
                    required=True,
                    status=(r.get("status") or "QUEUED").strip() or "QUEUED",
                ))
            except ValidationError as e:
                raise ContractError(
                    f"breadth agent row {agent_id or '?'} invalid: "
                    f"{_first_error(e)}"
                )
        try:
            return cls(agents=agents)
        except ValidationError as e:
            raise ContractError(f"spawn manifest invalid: {_first_error(e)}")

    def render_markdown(self) -> str:
        lines = [
            "# Spawn Manifest", "",
            "<!-- Rendered by the driver from spawn_manifest.json (authoritative). -->",
            "", "## Breadth Agents", "",
            "| Agent ID | Focus Area | Template | Required? | Expected Output | Status |",
            "|----------|------------|----------|-----------|-----------------|--------|",
        ]
        for a in self.agents:
            lines.append(
                f"| {a.agent_id} | {a.focus_area} | {a.template} | "
                f"{'YES' if a.required else 'NO'} | {a.output} | {a.status} |"
            )
        lines += ["", f"**Agent count**: {self.count()}", ""]
        return "\n".join(lines)


# ───────────────────────── Rescan manifest (Ship C) ─────────────────────────

_RESCAN_OUTPUT_RE = re.compile(
    r"^analysis_(?:rescan|percontract)_[A-Za-z0-9][A-Za-z0-9_.\-]*\.md$"
)


class RescanManifest(PlamenContract):
    schema_version: ClassVar[str] = "plamen.rescan_manifest.v1"
    sidecar_name: ClassVar[str] = "rescan_manifest.json"
    markdown_name: ClassVar[str] = "rescan_manifest.md"

    outputs_declared: list[str]

    @field_validator("outputs_declared")
    @classmethod
    def _valid(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for f in v:
            name = Path(str(f).strip()).name
            # Accept hyphen + dot (the SW04-4 hole): core-vault, v1.2 etc.
            if not _RESCAN_OUTPUT_RE.match(name):
                raise ValueError(
                    f"declared output {f!r} is not a valid rescan/per-contract "
                    f"artifact name"
                )
            cleaned.append(name)
        if not cleaned:
            raise ValueError("rescan manifest declares zero outputs")
        return cleaned

    @classmethod
    def from_markdown(cls, md: str) -> "RescanManifest":
        names = re.findall(
            r"\b(analysis_(?:rescan|percontract)_[A-Za-z0-9][A-Za-z0-9_.\-]*\.md)\b",
            md or "",
        )
        # Drop the glob exemplar form if present; dedupe preserving order.
        seen: set[str] = set()
        out: list[str] = []
        for n in names:
            if "*" in n or n in seen:
                continue
            seen.add(n)
            out.append(n)
        if not out:
            raise ContractError(
                "rescan_manifest.md declares no concrete analysis_rescan_*/"
                "analysis_percontract_* output filenames"
            )
        try:
            return cls(outputs_declared=out)
        except ValidationError as e:
            raise ContractError(f"rescan manifest invalid: {_first_error(e)}")

    def render_markdown(self) -> str:
        lines = ["# Rescan Manifest", "",
                 "<!-- Rendered by the driver from rescan_manifest.json. -->", ""]
        for n in self.outputs_declared:
            lines.append(f"- {n}")
        return "\n".join(lines) + "\n"


# ─────────────────────────── sidecar I/O ────────────────────────────────────

def _first_error(e: ValidationError) -> str:
    try:
        err = e.errors()[0]
        loc = ".".join(str(x) for x in err.get("loc", []))
        return f"{loc}: {err.get('msg', 'invalid')}"
    except Exception:
        return str(e)


def write_contract_sidecar(
    scratchpad: Path, model: PlamenContract, *, source_md: Optional[Path] = None
) -> Path:
    """Write ``<sidecar_name>`` from a validated model. Idempotent (skips a
    byte-identical re-write modulo ``generated_at``). Embeds the companion
    Markdown fingerprint when ``source_md`` is given. Returns the sidecar path."""
    sidecar = scratchpad / model.sidecar_name
    payload = {
        "schema_version": model.schema_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **model.model_dump(),
    }
    if source_md is not None and source_md.exists():
        payload.update(M.source_fingerprint(source_md))
    content = json.dumps(payload, indent=2, sort_keys=True)
    if sidecar.exists():
        try:
            existing = sidecar.read_text(encoding="utf-8", errors="replace")

            def _norm(s: str) -> str:
                return re.sub(r'"generated_at":\s*"[^"]*"',
                              '"generated_at": "<ts>"', s).rstrip()
            if _norm(existing) == _norm(content):
                return sidecar
        except Exception:
            pass
    tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    tmp.write_text(content + "\n", encoding="utf-8")
    tmp.replace(sidecar)
    return sidecar


def read_contract_sidecar(
    scratchpad: Path, model_cls: Type[T], *, source_md: Optional[Path] = None
) -> Optional[T]:
    """Read + validate ``<sidecar_name>``. Returns the model on success.

    Returns None when the sidecar is ABSENT (caller falls back to
    ``from_markdown``). Raises ContractError when the sidecar is PRESENT but
    invalid (wrong schema, stale fingerprint, or schema-validation failure) —
    the driver must hard-fail with a precise hint, NEVER silently fall back.
    """
    sidecar = scratchpad / model_cls.sidecar_name
    if not sidecar.exists():
        return None
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        raise ContractError(f"{model_cls.sidecar_name} is not valid JSON: {e}")
    if not isinstance(raw, dict):
        raise ContractError(f"{model_cls.sidecar_name} is not a JSON object")
    if raw.get("schema_version") != model_cls.schema_version:
        raise ContractError(
            f"{model_cls.sidecar_name} schema_version "
            f"{raw.get('schema_version')!r} != expected "
            f"{model_cls.schema_version!r}"
        )
    if source_md is not None and source_md.exists():
        cur = M.source_fingerprint(source_md)
        for k in ("source_mtime_ns", "source_sha256", "source_size"):
            if raw.get(k) != cur.get(k):
                raise ContractError(
                    f"{model_cls.sidecar_name} is stale: {k} does not match "
                    f"{source_md.name} (re-derive the JSON from the current "
                    f"Markdown or rewrite both)"
                )
    fields = {k: v for k, v in raw.items()
              if k not in ("schema_version", "generated_at",
                           "source_mtime_ns", "source_sha256", "source_size")}
    try:
        return model_cls.model_validate(fields)
    except ValidationError as e:
        raise ContractError(
            f"{model_cls.sidecar_name} failed schema validation: "
            f"{_first_error(e)}"
        )


def load_contract(
    scratchpad: Path, model_cls: Type[T], *, markdown: Optional[str] = None
) -> Optional[T]:
    """Uniform contract resolution (the load-bearing fallback ladder):

      1. JSON sidecar present + valid -> return it (authoritative).
      2. JSON sidecar present + invalid -> raise ContractError (hard fail).
      3. JSON absent + markdown given  -> from_markdown (section-scoped AST).
      4. JSON absent + no markdown      -> None.
    """
    md_companion = scratchpad / model_cls.markdown_name if model_cls.markdown_name else None
    obj = read_contract_sidecar(scratchpad, model_cls, source_md=md_companion)
    if obj is not None:
        return obj
    if markdown is None and md_companion is not None and md_companion.exists():
        try:
            markdown = md_companion.read_text(encoding="utf-8", errors="replace")
        except Exception:
            markdown = None
    if markdown is None:
        return None
    return model_cls.from_markdown(markdown)
