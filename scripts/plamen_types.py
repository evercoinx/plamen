"""Plamen V2 driver — shared types, constants, and phase definitions.

Layer 0: no internal plamen_* imports. All other modules depend on this.
"""
import functools
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "CLAUDE_BIN", "CODEX_BIN", "Checkpoint",
    "plamen_home",
    "EVIDENCE_TAGS_PROOF", "EVIDENCE_TAGS_TRACE", "EVIDENCE_TAGS_FAIL",
    "EVIDENCE_TAGS_ALL", "EVIDENCE_TAG_DEFAULT", "EVIDENCE_TAG_NAMES_RE",
    "EXIT_CONFIG_MISSING", "EXIT_DEGRADED", "EXIT_ERROR",
    "EXIT_HIBERNATING", "EXIT_RATE_LIMITED", "EXIT_SUCCESS",
    "CODEX_MULTI_AGENT_PHASES",
    "L1_NEVER_CUT_ARTIFACT_GROUPS", "L1_PHASES",
    "L1_VERIFY_CRITHIGH_PHASE_NAMES", "L1_VERIFY_PHASE_NAMES",
    "L1_VERIFY_SHARD_MANIFESTS", "PLAMEN_OPUS_MODEL", "Phase",
    "SC_NEVER_CUT_BASE", "SC_NEVER_CUT_CORE_EXTRAS",
    "SC_NEVER_CUT_THOROUGH_EXTRAS", "SC_PHASES",
    "SC_VERIFY_CRITHIGH_PHASE_NAMES", "SC_VERIFY_PHASE_NAMES",
    "SC_VERIFY_SHARD_MANIFESTS",
    "SEVERITY_ORDER", "SEVERITY_LETTER", "SEVERITY_FROM_LETTER",
    "_CODEX_MODEL_MAP", "_CODEX_FALLBACK_MODEL_ORDER",
    "_NEVER_CUT_SKIP_REASONS", "_PHASE_NAME_RE",
    "_VALID_MODES", "_VALID_PIPELINES", "_valid_report_shard_suffix",
    "_resolve_claude_bin", "_resolve_codex_bin", "_resolve_codex_model_alias",
    "_resolve_model_alias",
    "_EXPANDABLE_TIERS",
    "expand_shard_phases",
    "has_mechanical_proof", "normalize_severity", "try_normalize_severity",
    "severity_letter_from_name", "severity_rank",
    "l1_never_cut_groups",
    "log", "phase_model", "sc_never_cut_groups", "validate_phase_graph",
]

# --- Constants ---

def _resolve_claude_bin() -> str:
    """Return the platform-appropriate claude binary path.

    Windows npm installs `claude.cmd`, not `claude`. Python's subprocess
    without shell=True does NOT auto-append `.cmd`, so we have to find it.
    """
    override = os.environ.get("CLAUDE_BIN")
    if override:
        return override
    import shutil
    # Try each candidate; first one found wins.
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    # Last resort — let the caller's FileNotFoundError propagate with a
    # clear message.
    return "claude"

CLAUDE_BIN = _resolve_claude_bin()


def _resolve_codex_bin() -> str:
    """Find the Codex CLI binary. Returns empty string if not installed."""
    override = os.environ.get("CODEX_BIN")
    if override:
        return override
    import shutil
    for name in ("codex", "codex.cmd", "codex.exe"):
        found = shutil.which(name)
        if found:
            return found
    return ""


CODEX_BIN = _resolve_codex_bin()


@functools.lru_cache(maxsize=1)
def plamen_home() -> Path:
    """Plamen installation root. Single source of truth for all path resolution.

    Resolution: PLAMEN_HOME env -> script-relative -> ~/.claude fallback.
    """
    env = os.environ.get("PLAMEN_HOME", "").strip()
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    candidate = Path(__file__).resolve().parent.parent
    for marker in ("scripts", "rules", "prompts"):
        if (candidate / marker).is_dir():
            return candidate
    return Path.home() / ".claude"


# Pin expensive Opus phases to 4.6 by default. Claude Code's bare `opus`
# alias tracks latest, which moved to 4.7 and materially increased usage with
# weak marginal audit lift. Override only when explicitly benchmarking.
PLAMEN_OPUS_MODEL = os.environ.get("PLAMEN_OPUS_MODEL", "claude-opus-4-6").strip()


def _resolve_model_alias(model: str) -> str:
    m = (model or "").strip()
    if m == "opus":
        return PLAMEN_OPUS_MODEL or "claude-opus-4-6"
    return m or "sonnet"


_CODEX_MODEL_MAP: dict[str, str] = {
    "opus": os.environ.get("PLAMEN_CODEX_OPUS_MODEL", "gpt-5.5"),
    "sonnet": os.environ.get("PLAMEN_CODEX_SONNET_MODEL", "gpt-5.4"),
    "haiku": os.environ.get("PLAMEN_CODEX_HAIKU_MODEL", "gpt-5.4-mini"),
}

_CODEX_FALLBACK_MODEL_ORDER: tuple[str, ...] = tuple(dict.fromkeys(
    m.strip()
    for m in (
        os.environ.get("PLAMEN_CODEX_FALLBACK_MODELS", "")
        or ",".join([
            _CODEX_MODEL_MAP["sonnet"],
            _CODEX_MODEL_MAP["haiku"],
            "gpt-5.4-mini",
            "gpt-5.4-nano",
        ])
    ).split(",")
    if m.strip()
))


def _resolve_codex_model_alias(model: str) -> str:
    """Map Plamen tier aliases (opus/sonnet/haiku) to Codex-compatible models.

    Concrete model IDs (gpt-5.5, o3, etc.) pass through unchanged.
    Unknown aliases default to the sonnet-tier model.
    """
    m = (model or "").strip().lower()
    if m in _CODEX_MODEL_MAP:
        return _CODEX_MODEL_MAP[m]
    if m in ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "o3", "o4-mini"):
        return model.strip()
    return _CODEX_MODEL_MAP["sonnet"]

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_RATE_LIMITED = 2    # user should re-run when quota refreshes
EXIT_DEGRADED = 3        # pipeline finished with >N degraded phases
EXIT_CONFIG_MISSING = 4
EXIT_HIBERNATING = 42    # long wait detected; resume after wake_at_utc

log = logging.getLogger("plamen")

# ── Evidence tag vocabulary (v2.6.0) ──────────────────────────────────────
# Single source of truth. Adding a new evidence tag means ONE edit here.
EVIDENCE_TAGS_PROOF: frozenset[str] = frozenset({
    "[POC-PASS]", "[MEDUSA-PASS]", "[FUZZ-PASS]",
    "[NON-DET-PASS]", "[DIFF-PASS]", "[CONFORMANCE-PASS]",
})
EVIDENCE_TAGS_TRACE: frozenset[str] = frozenset({"[CODE-TRACE]", "[LSP-TRACE]"})
EVIDENCE_TAGS_FAIL: frozenset[str] = frozenset({"[POC-FAIL]"})
EVIDENCE_TAGS_ALL: frozenset[str] = EVIDENCE_TAGS_PROOF | EVIDENCE_TAGS_TRACE | EVIDENCE_TAGS_FAIL
EVIDENCE_TAG_DEFAULT = "CODE-TRACE"
EVIDENCE_TAG_NAMES_RE = "|".join(sorted(t.strip("[]") for t in EVIDENCE_TAGS_ALL))


def has_mechanical_proof(text: str) -> bool:
    """True if *text* contains any proof-grade evidence tag."""
    return any(tag in text for tag in EVIDENCE_TAGS_PROOF)


# ── Severity vocabulary (v2.6.0) ──────────────────────────────────────────
SEVERITY_ORDER: tuple[str, ...] = (
    "Critical", "High", "Medium", "Low", "Informational",
)
SEVERITY_LETTER: dict[str, str] = {s: s[0] for s in SEVERITY_ORDER}
SEVERITY_FROM_LETTER: dict[str, str] = {v: k for k, v in SEVERITY_LETTER.items()}
_SEVERITY_ALIASES: dict[str, str] = {
    "info": "Informational",
    "informational": "Informational",
    "low": "Low",
    "medium": "Medium",
    "med": "Medium",
    "high": "High",
    "critical": "Critical",
    "crit": "Critical",
}


def _clean_severity_text(raw: str) -> str:
    """Strip Markdown/table noise around a possible severity label."""
    s = str(raw or "").strip()
    s = re.sub(r"^\s*(?:[-*+]\s+)+", "", s)
    s = re.sub(r"^\s*\*{1,3}\s*", "", s)
    s = re.sub(r"\s*\*{1,3}\s*$", "", s)
    s = s.strip(" \t\r\n`'\"[]()")
    label_m = re.match(r"(?i)^(?:severity|final severity|resulting tier)\s*[:=-]\s*(.+)$", s)
    if label_m:
        s = label_m.group(1).strip(" \t\r\n`'\"[]()")
        s = re.sub(r"^\s*\*{1,3}\s*", "", s)
        s = re.sub(r"\s*\*{1,3}\s*$", "", s)
        s = s.strip(" \t\r\n`'\"[]()")
    return s


def try_normalize_severity(raw: str) -> str | None:
    """Canonicalize only strings that actually present a severity label."""
    s = _clean_severity_text(raw)
    if not s:
        return None
    sl = s.lower()
    exact = _SEVERITY_ALIASES.get(sl)
    if exact:
        return exact
    lead = re.match(r"(?i)^(critical|crit|high|medium|med|low|informational|info)\b", s)
    if lead:
        return _SEVERITY_ALIASES[lead.group(1).lower()]
    return None


def _looks_like_nonseverity_prose(s: str) -> bool:
    """True for status/provenance prose accidentally supplied as severity."""
    sl = (s or "").lower()
    if not sl:
        return False
    if re.fullmatch(r"(?:n/?a|not\s+available|unknown)(?:\s*\([^)]*\))?", sl):
        return True
    if len(re.findall(r"[a-z0-9]+", sl)) > 1:
        return True
    if re.search(r"[.;:]|\b(?:inv|h|ch|cc|ac|tf|de|dx)-?\d+\b", sl, re.IGNORECASE):
        return True
    return False


def normalize_severity(raw: str) -> str:
    """Canonicalize a severity string to one of SEVERITY_ORDER values."""
    s = str(raw or "").strip()
    if not s:
        log.warning("normalize_severity: empty input, defaulting to Medium")
        return "Medium"
    parsed = try_normalize_severity(s)
    if parsed:
        return parsed
    if _looks_like_nonseverity_prose(s):
        return "Informational"
    # LLM/table output often leaks Markdown decorations into a cell value:
    # `** Low`, `**Low**`, `` `Informational` ``, `- High`, or
    # `Severity: **High**`. Strip presentation syntax before severity routing
    # so cosmetic formatting never changes triage or phase scope.
    s = re.sub(r"^\s*(?:[-*+]\s+)+", "", s)
    s = re.sub(r"^\s*\*{1,3}\s*", "", s)
    s = re.sub(r"\s*\*{1,3}\s*$", "", s)
    s = s.strip(" \t\r\n`'\"“”‘’[]()")
    label_m = re.match(r"(?i)^(?:severity|final severity|resulting tier)\s*[:=-]\s*(.+)$", s)
    if label_m:
        s = label_m.group(1).strip(" \t\r\n`'\"“”‘’[]()")
        s = re.sub(r"^\s*\*{1,3}\s*", "", s)
        s = re.sub(r"\s*\*{1,3}\s*$", "", s)
        s = s.strip(" \t\r\n`'\"“”‘’[]()")
    sl = s.lower()
    if re.search(
        r"\b(?:not\s+applicable|refuted|false[_\s-]*positive|infeasible|"
        r"absorbed(?:\s+into)?|duplicate|deduplicated|merged(?:\s+into)?|"
        r"subsumed(?:\s+by)?|already\s+captured|already\s+reported|"
        r"captured\s+in|not\s+re-?reported|not\s+reported\s+separately|"
        r"not\s+independently\s+reported|not\s+reportable|no\s+finding)\b",
        sl,
    ):
        return "Informational"
    if re.fullmatch(r"(?:n/?a|not\s+available|unknown)(?:\s*\([^)]*\))?", sl):
        return "Informational"
    exact = _SEVERITY_ALIASES.get(sl)
    if exact:
        return exact
    for canonical in SEVERITY_ORDER:
        if sl.startswith(canonical[:3].lower()):
            return canonical
    log.warning(f"normalize_severity: unrecognized severity {raw!r}, defaulting to Medium")
    return "Medium"


def severity_letter_from_name(raw: str) -> str:
    """Return the single-letter code for a severity name."""
    return SEVERITY_LETTER.get(normalize_severity(raw), "M")


def severity_rank(raw: str) -> int:
    """Return an integer rank (4=Critical .. 0=Informational, -1=unknown)."""
    sev = normalize_severity(raw)
    try:
        return len(SEVERITY_ORDER) - 1 - SEVERITY_ORDER.index(sev)
    except ValueError:
        return -1


_NEVER_CUT_SKIP_REASONS = {
    "NO_APPLICABLE_FLAG",
    "LANGUAGE_LANE_NOT_DETECTED",
    "EMPTY_SCOPE_AFTER_MANIFEST",
}

# v2.3.4 — `depth_`-prefixed aliases. Orchestrators legitimately group
# iteration-1 supplementary outputs (perturbation, design stress) under the
# `depth_*_findings.md` naming convention to align with the 5 standard depth
# agents. Pre-v2.3.4 the never-cut gate hard-failed on the prefix drift,
# halting the pipeline despite the agent having spawned and produced output.
# Same nondeterminism class as v2.3.1 coverage-fill drift — the gate's
# canonical-name expectation collided with a valid orchestrator filename
# choice. Each group accepts either the canonical or `depth_`-prefixed form.
L1_NEVER_CUT_ARTIFACT_GROUPS = [
    ["depth_consensus_invariant_findings.md"],
    ["depth_network_surface_findings.md"],
    ["depth_state_trace_findings.md"],
    ["depth_external_findings.md"],
    ["depth_edge_case_findings.md"],
    ["design_stress_findings.md", "depth_design_stress_findings.md"],
    ["perturbation_findings.md", "depth_perturbation_findings.md"],
    ["confidence_scores.md"],
    ["skill_execution_gaps.md", "skill_execution_checklist.md"],
]

# v2.6.3 — L1 mode-aware never-cut groups (mirrors SC 3-tier pattern).
# Light requires only the 5 standard depth agents (no confidence scoring).
# Core adds confidence_scores.md (2-axis scoring).
# Thorough adds design stress, perturbation, and skill execution checklist.
L1_NEVER_CUT_BASE = [
    ["depth_consensus_invariant_findings.md"],
    ["depth_network_surface_findings.md"],
    ["depth_state_trace_findings.md"],
    ["depth_external_findings.md"],
    ["depth_edge_case_findings.md"],
]
L1_NEVER_CUT_CORE_EXTRAS = [
    ["confidence_scores.md"],
]
L1_NEVER_CUT_THOROUGH_EXTRAS = [
    ["design_stress_findings.md", "depth_design_stress_findings.md"],
    ["perturbation_findings.md", "depth_perturbation_findings.md"],
    ["skill_execution_gaps.md", "skill_execution_checklist.md"],
]


def l1_never_cut_groups(mode: str) -> list:
    """Return the never-cut artifact groups for L1 depth phase by mode."""
    groups = list(L1_NEVER_CUT_BASE)
    if mode in ("core", "thorough"):
        groups = groups + L1_NEVER_CUT_CORE_EXTRAS
    if mode == "thorough":
        groups = groups + L1_NEVER_CUT_THOROUGH_EXTRAS
    return groups

# SC (smart-contract) never-cut groups are mode-aware. The 4 standard
# depth agents run in every SC mode (Light/Core/Thorough) per the AUDIT
# MODES table; validation sweep + 2-axis confidence scoring run in
# Core/Thorough; design stress + perturbation + skill execution +
# 4-axis confidence run only in Thorough. The Light set is the dHEDGE-
# class floor — catches the "orchestrator merged depth agents to save
# context" failure mode mechanically.
SC_NEVER_CUT_BASE = [
    ["depth_token_flow_findings.md"],
    ["depth_state_trace_findings.md"],
    ["depth_edge_case_findings.md"],
    ["depth_external_findings.md"],
]
SC_NEVER_CUT_CORE_EXTRAS = [
    ["blind_spot_a_findings.md"],
    ["blind_spot_b_findings.md"],
    ["blind_spot_c_findings.md"],
    ["validation_sweep_findings.md", "scanner_validation_findings.md"],
    ["confidence_scores.md"],
]
SC_NEVER_CUT_THOROUGH_EXTRAS = [
    # v2.3.4: same `depth_`-prefix tolerance as L1.
    ["design_stress_findings.md", "depth_design_stress_findings.md"],
    ["perturbation_findings.md", "depth_perturbation_findings.md"],
    ["skill_execution_gaps.md", "skill_execution_checklist.md"],
]


def sc_never_cut_groups(mode: str) -> list:
    """Return the never-cut artifact groups for SC depth phase by mode."""
    groups = list(SC_NEVER_CUT_BASE)
    if mode in ("core", "thorough"):
        groups = groups + SC_NEVER_CUT_CORE_EXTRAS
    if mode == "thorough":
        groups = groups + SC_NEVER_CUT_THOROUGH_EXTRAS
    return groups


L1_VERIFY_SHARD_MANIFESTS = {
    "verify_crithigh": "verification_queue_crithigh.md",
    "verify_high_b": "verification_queue_high_b.md",
    "verify_high_c": "verification_queue_high_c.md",
    "verify_high_d": "verification_queue_high_d.md",
    "verify_high_e": "verification_queue_high_e.md",
    "verify_high_f": "verification_queue_high_f.md",
    "verify_high_g": "verification_queue_high_g.md",
    "verify_high_h": "verification_queue_high_h.md",
    "verify_high_i": "verification_queue_high_i.md",
    "verify_high_j": "verification_queue_high_j.md",
    "verify_medium_a": "verification_queue_medium_a.md",
    "verify_medium_b": "verification_queue_medium_b.md",
    "verify_medium_c": "verification_queue_medium_c.md",
    "verify_medium_d": "verification_queue_medium_d.md",
    "verify_medium_e": "verification_queue_medium_e.md",
    "verify_medium_f": "verification_queue_medium_f.md",
    "verify_low_a": "verification_queue_low_a.md",
    "verify_low_b": "verification_queue_low_b.md",
    "verify_low_c": "verification_queue_low_c.md",
    "verify_low_d": "verification_queue_low_d.md",
}
L1_VERIFY_PHASE_NAMES = tuple(L1_VERIFY_SHARD_MANIFESTS.keys())
L1_VERIFY_CRITHIGH_PHASE_NAMES = (
    "verify_crithigh", "verify_high_b", "verify_high_c",
    "verify_high_d", "verify_high_e", "verify_high_f",
    "verify_high_g", "verify_high_h", "verify_high_i", "verify_high_j",
)

# SC verify shards: SC projects can still produce many High hypotheses in
# thorough mode. Keep Critical/High shards small enough that each verification
# subprocess can write progress before long-context API failures.
SC_VERIFY_SHARD_MANIFESTS = {
    "sc_verify_crithigh": "verification_queue_crithigh.md",
    "sc_verify_high_b": "verification_queue_high_b.md",
    "sc_verify_high_c": "verification_queue_high_c.md",
    "sc_verify_high_d": "verification_queue_high_d.md",
    "sc_verify_high_e": "verification_queue_high_e.md",
    "sc_verify_high_f": "verification_queue_high_f.md",
    "sc_verify_high_g": "verification_queue_high_g.md",
    "sc_verify_high_h": "verification_queue_high_h.md",
    "sc_verify_high_i": "verification_queue_high_i.md",
    "sc_verify_high_j": "verification_queue_high_j.md",
    "sc_verify_medium_a": "verification_queue_medium_a.md",
    "sc_verify_medium_b": "verification_queue_medium_b.md",
    "sc_verify_medium_c": "verification_queue_medium_c.md",
    "sc_verify_medium_d": "verification_queue_medium_d.md",
    "sc_verify_low_a": "verification_queue_low_a.md",
    "sc_verify_low_b": "verification_queue_low_b.md",
}
SC_VERIFY_PHASE_NAMES = tuple(SC_VERIFY_SHARD_MANIFESTS.keys())
SC_VERIFY_CRITHIGH_PHASE_NAMES = (
    "sc_verify_crithigh", "sc_verify_high_b",
    "sc_verify_high_c", "sc_verify_high_d",
    "sc_verify_high_e", "sc_verify_high_f",
    "sc_verify_high_g", "sc_verify_high_h",
    "sc_verify_high_i", "sc_verify_high_j",
)

# Phases where the Codex backend should use spawn_agent for parallel sub-agents
# instead of running everything sequentially as a single agent. These are the
# orchestrator phases that spawn multiple analysis agents in the Claude pipeline.
CODEX_MULTI_AGENT_PHASES: frozenset[str] = frozenset({
    "recon",
    "breadth",
    "rescan",
    "depth",
})


# --- Dataclasses ---

@dataclass
class Phase:
    name: str                          # "recon", "breadth", etc.
    section_markers: list              # Headings to tell the LLM to run, e.g. ["## Step 1", "## Step 2"]
    expected_artifacts: list           # Glob patterns, e.g. ["recon_summary.md", "depth_*_findings.md"]
    base_timeout_s: int                # Base wall-clock timeout for this phase
    model: str = "sonnet"              # claude -p --model value for Core/Thorough. Light forces sonnet.
    needs_mcp: bool = False            # Only rag_sweep
    modes: set = field(default_factory=lambda: {"light", "core", "thorough"})
    min_artifact_bytes: int = 100      # Gate fails if any matched file is smaller
    min_artifacts_count: int = 1       # For glob patterns: require at least N substantial
                                       # matches. Default 1 (any match passes). Set >1 on
                                       # phases where one solo artifact would be a silent
                                       # degradation (e.g. only one analysis_*.md when
                                       # Thorough requested 5-9 breadth agents).
    critical: bool = False             # If True, pipeline HALTS on degrade (not continues).
                                       # Set on phases whose output is a hard prerequisite for
                                       # the rest of the pipeline (breadth/depth/verify — no
                                       # findings = no report).
    any_of: list = field(default_factory=list)
                                       # List of OR-groups. Each inner list is a set of glob
                                       # patterns where AT LEAST ONE must match (OR within the
                                       # group). ALL outer groups must be satisfied (AND across
                                       # groups). Evaluated by gate_passes() in addition to
                                       # expected_artifacts. Use for naming-convention flux
                                       # (e.g. verify_F_*.md vs verify_F-*.md) where either
                                       # shape alone should count as complete.
    example_tokens: list = field(default_factory=list)
                                       # Per-phase authoritative substitution tokens for `*` in
                                       # expected_artifacts globs. When set, `_render_expected_
                                       # output_block` emits example filenames using these tokens
                                       # INSTEAD of numeric-shard defaults. Fixes the v2.1.3-
                                       # observed drift class where depth agents produced
                                       # `depth_01_token_flow_findings.md` because the driver's
                                       # auto-generated examples were `depth_01_findings.md`.
                                       # Per LLM instruction-following research (Min et al. 2022
                                       # "Rethinking the Role of Demonstrations", Lu et al. 2022
                                       # "Fantastically Ordered Prompts"), few-shot examples
                                       # anchor output format more strongly than declarative
                                       # rules, so accurate examples beat any amount of "MUST
                                       # NOT DRIFT" prose. Leave empty for phases where numeric
                                       # shards are the intended convention (breadth, rescan).


def phase_model(phase: Phase, mode: str, config: Optional[dict] = None) -> str:
    """Resolve effective model for this phase under the given audit mode.

    Light mode forces all phases to sonnet regardless of phase.model
    (Light is a Pro-plan-compatible budget; opus is Max-plan).
    Core/Thorough honor the phase-level model.
    For Codex backend, maps tier aliases to OpenAI model IDs.
    """
    if config and config.get("cli_backend") == "codex":
        tier = "sonnet" if mode == "light" else (phase.model or "sonnet")
        resolved = _resolve_codex_model_alias(tier)
        phase_fallbacks = config.get("_codex_phase_model_fallbacks") or {}
        if isinstance(phase_fallbacks, dict) and phase.name in phase_fallbacks:
            return phase_fallbacks[phase.name]
        # If a model was found unavailable, downgrade only phases that would
        # use it — sonnet/haiku-tier phases keep their natural model.
        unavail = config.get("_codex_model_unavailable")
        if unavail and resolved == unavail:
            return config.get("_codex_model_fallback", _CODEX_MODEL_MAP.get("sonnet", "gpt-5.4"))
        return resolved
    if mode == "light":
        return "sonnet"
    if config and phase.name == "breadth":
        override = (
            config.get("breadth_model_override")
            or os.environ.get("PLAMEN_BREADTH_MODEL_OVERRIDE")
            or ""
        ).strip()
        if override:
            return _resolve_model_alias(override)
    return _resolve_model_alias(phase.model)


@dataclass
class Checkpoint:
    completed: list = field(default_factory=list)
    degraded: list = field(default_factory=list)
    rate_limited_at: Optional[str] = None
    config: Optional[dict] = None

    @classmethod
    def load(cls, scratchpad: Path) -> "Checkpoint":
        p = scratchpad / "_v2_checkpoint.json"
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            # A corrupt checkpoint is not equivalent to a fresh run. Treating
            # it as empty causes resume to replay phases against stale
            # artifacts and can silently mix old/new state. Preserve the bad
            # file for forensics and force the operator to choose --fresh or
            # repair it explicitly.
            backup = p.with_suffix(f".corrupt-{int(time.time())}.json")
            try:
                p.replace(backup)
            except Exception:
                backup = p
            raise RuntimeError(
                f"Corrupt checkpoint {p}; moved to {backup}. "
                "Restart with a clean scratchpad or restore a valid checkpoint."
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Invalid checkpoint {p}: root must be an object")

        def _string_list(key: str) -> list[str]:
            value = data.get(key, [])
            if not isinstance(value, list):
                raise RuntimeError(f"Invalid checkpoint {p}: {key} must be a list")
            if not all(isinstance(item, str) and item for item in value):
                raise RuntimeError(
                    f"Invalid checkpoint {p}: {key} entries must be non-empty strings"
                )
            if len(set(value)) != len(value):
                raise RuntimeError(f"Invalid checkpoint {p}: {key} contains duplicates")
            return list(value)

        rate_limited_at = data.get("rate_limited_at")
        if rate_limited_at is not None and not isinstance(rate_limited_at, str):
            raise RuntimeError(
                f"Invalid checkpoint {p}: rate_limited_at must be null or string"
            )
        cfg = data.get("config")
        if cfg is not None and not isinstance(cfg, dict):
            cfg = None
        return cls(
            completed=_string_list("completed"),
            degraded=_string_list("degraded"),
            rate_limited_at=rate_limited_at,
            config=cfg,
        )

    def validate_phase_names(self, phase_names: set[str]) -> list[str]:
        """Return checkpoint entries that do not belong to the active graph."""
        unknown: list[str] = []
        for key, values in (("completed", self.completed), ("degraded", self.degraded)):
            for name in values:
                if name not in phase_names:
                    unknown.append(f"{key}:{name}")
        if self.rate_limited_at and self.rate_limited_at not in phase_names:
            unknown.append(f"rate_limited_at:{self.rate_limited_at}")
        return unknown

    def save(self, scratchpad: Path):
        # v2.3.6 F1: atomic write via temp + rename. Pre-v2.3.6 a SIGKILL /
        # OOM-kill / power loss between `open()` and `close()` could leave
        # the JSON file truncated. `Checkpoint.load()` then catches the
        # parse error and returns a fresh empty checkpoint → resume re-runs
        # every prior phase from scratch. `os.replace()` is atomic on POSIX
        # and same-volume on Windows since Python 3.3.
        p = scratchpad / "_v2_checkpoint.json"
        tmp = p.with_suffix(".json.tmp")
        data: dict = {
            "completed": self.completed,
            "degraded": self.degraded,
            "rate_limited_at": self.rate_limited_at,
        }
        if self.config is not None:
            data["config"] = self.config
        payload = json.dumps(data, indent=2)
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(p)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def mark_completed(self, phase_name: str):
        """Record a successful phase commit. Clears any stale `degraded`
        entry for the same phase so resume-after-degrade runs don't leave
        false-positive markers in the final checkpoint."""
        if phase_name not in self.completed:
            self.completed.append(phase_name)
        if phase_name in self.degraded:
            self.degraded = [d for d in self.degraded if d != phase_name]

    def clear_degraded_sentinel(self, scratchpad: Path, phase_name: str):
        """Delete stale on-disk degrade markers after a successful retry.

        Shutdown reconciles `*.degraded` sentinels back into the checkpoint.
        Without deleting phase sentinels on success, a run can complete
        cleanly and still exit degraded because an old marker is re-synced.

        v2.5.5: also removes compound sentinels (e.g. `.body_writer.degraded`)
        that would otherwise be re-synced at shutdown.
        """
        for suffix in (f"{phase_name}.degraded", f"{phase_name}.body_writer.degraded"):
            try:
                (scratchpad / suffix).unlink(missing_ok=True)
            except Exception:
                pass


# --- Phase graph validator ---


_VALID_PIPELINES = {"sc", "l1"}
_VALID_MODES = {"light", "core", "thorough"}
_PHASE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_phase_graph(phases: list, mode: str, pipeline: str) -> list[str]:
    """Static validation of a (phases, mode, pipeline) triple.

    Closes the architectural defect where a mode/language combination could
    ship a broken phase list and the bug only manifests mid-audit. Catches:
      - duplicate phase names
      - phase names with bad characters
      - phase whose `modes` set excludes the active mode (handled by caller
        skip, but warned if set is empty entirely)
      - phase with no expected_artifacts AND no any_of (silent-pass risk)
      - phase with negative or absurd timeouts
      - pipeline name not in the canonical set
      - mode not in the canonical set
      - empty phase list

    Verify-shard phases (`verify_crithigh`, `verify_high_*`, `verify_medium_*`,
    `verify_low_*`) are exempt from the empty-artifacts check because their
    contract is manifest-driven: artifacts are declared in the per-shard
    `verify_*_manifest.md` files written by `ensure_verify_shard_manifests`,
    not statically in the Phase dataclass. Their downstream gate is the
    `_collect_verify_promotion_receipts` + `_validate_verification_queue_inventory_parity`
    pair which enforces the actual contract.

    Returns a list of issue strings. Empty list = graph is valid.
    """
    _verify_shard_re = re.compile(
        r"^(?:sc_)?verify_(crithigh|high_[a-j]|medium_[a-f]|low_[a-d])$"
    )
    issues: list[str] = []
    if pipeline not in _VALID_PIPELINES:
        issues.append(f"pipeline={pipeline!r} not in {_VALID_PIPELINES}")
    if mode not in _VALID_MODES:
        issues.append(f"mode={mode!r} not in {_VALID_MODES}")
    if not phases:
        issues.append("phase list is empty")
        return issues

    seen_names: dict[str, int] = {}
    any_phase_in_mode = False
    for idx, phase in enumerate(phases):
        name = getattr(phase, "name", None)
        if not name or not isinstance(name, str):
            issues.append(f"phase[{idx}] has invalid name: {name!r}")
            continue
        if not _PHASE_NAME_RE.match(name):
            issues.append(f"phase[{idx}] name {name!r} not [a-z][a-z0-9_]*")
        if name in seen_names:
            issues.append(
                f"duplicate phase name {name!r} at indices "
                f"{seen_names[name]} and {idx}"
            )
        seen_names[name] = idx

        modes_set = getattr(phase, "modes", None)
        if not modes_set:
            issues.append(f"phase {name!r} has empty modes set")
        elif mode in modes_set:
            any_phase_in_mode = True

        timeout = getattr(phase, "base_timeout_s", None)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            issues.append(f"phase {name!r} has invalid timeout: {timeout!r}")
        elif timeout > 7200:  # 2 hours upper bound for any single phase
            issues.append(
                f"phase {name!r} timeout {timeout}s exceeds 2-hour ceiling"
            )

        expected = getattr(phase, "expected_artifacts", []) or []
        any_of = getattr(phase, "any_of", []) or []
        if not expected and not any_of and not _verify_shard_re.match(name):
            issues.append(
                f"phase {name!r} declares no expected_artifacts AND no any_of"
                " (silent-pass risk)"
            )

        # Each expected_artifacts entry should be a non-empty string with no
        # unsanitized whitespace.
        for art in expected:
            if not isinstance(art, str) or not art.strip():
                issues.append(
                    f"phase {name!r} expected_artifact {art!r} is invalid"
                )
                break

        # any_of must be a list of lists/tuples of strings (OR-groups).
        for grp_idx, grp in enumerate(any_of):
            if not isinstance(grp, (list, tuple)) or not grp:
                issues.append(
                    f"phase {name!r} any_of[{grp_idx}] is not a non-empty list"
                )
                continue
            for art in grp:
                if not isinstance(art, str) or not art.strip():
                    issues.append(
                        f"phase {name!r} any_of[{grp_idx}] has invalid entry {art!r}"
                    )
                    break

        sec_markers = getattr(phase, "section_markers", []) or []
        if not sec_markers:
            issues.append(
                f"phase {name!r} has no section_markers (LLM cannot locate phase)"
            )

    if not any_phase_in_mode:
        issues.append(
            f"no phase in {pipeline!r} pipeline runs in mode {mode!r}"
        )

    return issues


_EXPANDABLE_TIERS = ("critical_high", "medium", "low_info")


def _valid_report_shard_suffix(suffix: str) -> bool:
    """True for generated body-writer shard suffixes only.

    Body manifest discovery is a phase graph contract, not a free-form glob.
    Accept only the shard names emitted by the report index splitter (`a`,
    `b`, ...). Files such as `report_medium_assignments.json` are support
    artifacts and must never expand into runnable phases.
    """
    return bool(re.fullmatch(r"[a-z]", suffix or ""))


def expand_shard_phases(phases: list, scratchpad: Path) -> list:
    """Replace sentinel tier phases with per-shard phases based on actual manifests.

    Called by the driver after report_index completes (which creates
    body_manifests/). For each expandable tier (critical_high, medium,
    low_info), scans body_manifests/ for report_{tier}_*.json files and
    generates one body-writer + one confirmation phase per shard.  The
    merge sentinel is kept as-is (driver handles merge at runtime).

    Tiers whose manifest is unsuffixed (finding count within cap) are
    left as-is.
    """
    manifest_dir = scratchpad / "body_manifests"
    tier_suffixes: dict[str, list[str]] = {}
    for tier in _EXPANDABLE_TIERS:
        suffixes: list[str] = []
        if manifest_dir.is_dir():
            prefix = f"report_{tier}_"
            for f in sorted(manifest_dir.glob(f"report_{tier}_*.json")):
                suffix = f.stem[len(prefix):]
                if _valid_report_shard_suffix(suffix):
                    suffixes.append(suffix)
        if not suffixes and (manifest_dir / f"report_{tier}.json").exists():
            continue
        if suffixes:
            tier_suffixes[tier] = suffixes

    if not tier_suffixes:
        return phases

    bw_sentinels = {f"report_body_writer_{t}" for t in tier_suffixes}
    confirm_sentinels = {f"report_{t}" for t in tier_suffixes}

    result: list = []
    for phase in phases:
        matched_tier = None
        for t in tier_suffixes:
            if phase.name == f"report_body_writer_{t}":
                matched_tier = t
                for s in tier_suffixes[t]:
                    result.append(Phase(
                        f"report_body_writer_{t}_{s}",
                        list(phase.section_markers),
                        [f"report_{t}_{s}.md"],
                        base_timeout_s=phase.base_timeout_s,
                        model=phase.model or "sonnet",
                        critical=True,
                    ))
                break
            elif phase.name == f"report_{t}" and phase.name not in (
                f"report_{t}_merge" for _ in [0]
            ):
                is_confirm = any(
                    "6b.1" in m or "6b" in m
                    for m in phase.section_markers
                )
                if is_confirm:
                    matched_tier = t
                    for s in tier_suffixes[t]:
                        result.append(Phase(
                            f"report_{t}_{s}",
                            list(phase.section_markers),
                            [f"report_{t}_{s}.md"],
                            base_timeout_s=phase.base_timeout_s,
                            model=phase.model or "haiku",
                            critical=True,
                        ))
                    break
        if matched_tier is None:
            result.append(phase)

    expanded = ", ".join(
        f"{t}={len(ss)}" for t, ss in tier_suffixes.items()
    )
    log.info(f"[expand_shard_phases] expanded tiers: {expanded}")
    return result


# --- Phase lists ---

SC_PHASES = [
    # SC uses `## Phase N` for pipeline phases, `## Step N` for wizard/setup.
    # Recon groups Step 1 (language detect) + Step 1.5 (scratchpad) + Phase 1 (recon).
    Phase("recon", ["Step 1: Language Detection", "Step 1.5: Scratchpad",
                    "Phase 1: Reconnaissance"],
          ["recon_summary.md", "design_context.md", "attack_surface.md",
           "state_variables.md", "function_list.md", "contract_inventory.md",
           "template_recommendations.md", "detected_patterns.md",
           "setter_list.md", "emit_list.md", "build_status.md"],
          base_timeout_s=1500, critical=True),
    Phase("instantiate", ["Phase 2: Orchestrator Instantiation"],
          ["spawn_manifest.md"],
          base_timeout_s=600, min_artifact_bytes=50, model="sonnet", critical=True),
    Phase("breadth", ["Phase 3: Parallel Analysis"],
          ["analysis_*.md"],
          base_timeout_s=5400, model="sonnet", critical=True,
          min_artifacts_count=3),
    # Per-contract/scope-review output is part of the rescan phase contract
    # in Thorough mode; inventory consumes both families in one build.
    Phase("rescan", ["Phase 3b: Breadth Re-Scan (+ Phase 3c per-contract sub-step)"],
          ["analysis_rescan_*.md", "analysis_percontract_*.md"],
          base_timeout_s=2400, modes={"thorough"}, critical=True),
    Phase("inventory_prepare", ["Phase 4: Synthesis, Adaptive Depth, Chain Analysis"],
          ["inventory_shard_plan.md"],
          base_timeout_s=60, critical=True, model="haiku"),
    Phase("inventory_chunk_a", ["Phase 4: Synthesis, Adaptive Depth, Chain Analysis"],
          ["findings_inventory_chunk_a.md"],
          base_timeout_s=2400, critical=True, model="sonnet"),
    Phase("inventory_chunk_b", ["Phase 4: Synthesis, Adaptive Depth, Chain Analysis"],
          ["findings_inventory_chunk_b.md"],
          base_timeout_s=2400, critical=True, model="sonnet"),
    Phase("inventory_chunk_c", ["Phase 4: Synthesis, Adaptive Depth, Chain Analysis"],
          ["findings_inventory_chunk_c.md"],
          base_timeout_s=2400, critical=True, model="sonnet"),
    Phase("inventory", ["Phase 4: Synthesis, Adaptive Depth, Chain Analysis"],
          ["findings_inventory.md"],
          base_timeout_s=2400, critical=True),
    Phase("invariants", ["Phase 4a.5: Semantic Invariant"],
          ["semantic_invariants.md"],
          base_timeout_s=1200, modes={"core", "thorough"}, critical=False),
    Phase("depth", ["Phase 4b: Adaptive Depth Loop"],
          ["depth_*_findings.md"],
          base_timeout_s=3600, model="opus", critical=True,
          min_artifacts_count=4,
          example_tokens=[
              "token_flow", "state_trace", "edge_case", "external",
          ]),
    Phase("attention_repair", ["Phase 4b.4: Attention Repair"],
          ["attention_repair_summary.md"],
          base_timeout_s=1500, model="sonnet", critical=True,
          modes={"thorough"}),
    Phase("rag_sweep", ["Phase 4b.5: RAG Validation"],
          ["rag_validation.md"],
          base_timeout_s=1200, needs_mcp=True, model="sonnet",
          modes={"core", "thorough"}, critical=True),
    Phase("sc_semantic_dedup", ["Phase 4e: Semantic Dedup"],
          ["dedup_decisions.md", "findings_inventory_deduped.md"],
          base_timeout_s=1500, model="sonnet", critical=True),
    Phase("chain", ["Phase 4: Synthesis, Adaptive Depth, Chain Analysis"],
          ["hypotheses.md", "finding_mapping.md", "enabler_results.md"],
          base_timeout_s=1500, critical=True),
    Phase("chain_agent2", ["Phase 4: Synthesis, Adaptive Depth, Chain Analysis"],
          ["chain_hypotheses.md", "composition_coverage.md", "synthesis_full.md"],
          base_timeout_s=1200, critical=True),
    # v2.4.1: SC verify sharded like L1. Monolithic verify phase hit 2700s
    # ceiling on 81 hypotheses (3 .sol files, Thorough mode), verifying only
    # ~32/81 before timeout -> parity check failure -> pipeline halt. Sharding
    # gives each severity tier its own subprocess + timeout budget.
    Phase("sc_verify_queue", ["Phase 5: Verification"],
          ["verification_queue.md"],
          base_timeout_s=600, critical=True, model="haiku"),
    Phase("sc_verify_crithigh", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_b", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_c", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_d", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_e", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_f", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_g", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_h", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_i", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_high_j", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_medium_a", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_medium_b", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_medium_c", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_medium_d", ["Phase 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("sc_verify_low_a", ["Phase 5: Verification"],
          [],
          base_timeout_s=1800, critical=True, modes={"thorough"}, model="sonnet"),
    Phase("sc_verify_low_b", ["Phase 5: Verification"],
          [],
          base_timeout_s=1800, critical=True, modes={"thorough"}, model="sonnet"),
    Phase("sc_verify_aggregate", ["Phase 5: Verification"],
          ["verify_core.md"],
          base_timeout_s=900, critical=True, model="haiku"),
    Phase("skeptic", ["Phase 5.1: Skeptic-Judge"],
          ["skeptic_findings.md", "skeptic_judge_decisions.md"],
          base_timeout_s=1800, modes={"thorough"},
          critical=True,
          example_tokens=["H-01", "C-01", "CH-01"]),
    Phase("crossbatch", ["Phase 5.2: Cross-Batch Consistency"],
          ["cross_batch_consistency.md"],
          # v2.3.14: upgraded from haiku to sonnet. Haiku fails to
          # enumerate all verify IDs on large audits (7/124 on Irys L1).
          base_timeout_s=900, model="sonnet",
          modes={"core", "thorough"},
          critical=True),
    Phase("report_index", ["Step 6a: Index Agent", "Step 6a.1: Index Completeness"],
          ["report_index.md", "report_coverage.md"],
          base_timeout_s=1500, model="sonnet", critical=True),
    Phase("report_body_writer_critical_high", ["Step 6b: Tier Writers"],
          ["report_critical_high.md"],
          base_timeout_s=2400, model="sonnet", critical=True),
    Phase("report_body_writer_medium", ["Step 6b: Tier Writers"],
          ["report_medium.md"],
          base_timeout_s=2400, model="sonnet", critical=True),
    Phase("report_body_writer_low_info", ["Step 6b: Tier Writers"],
          ["report_low_info.md"],
          base_timeout_s=2400, model="sonnet", critical=True),
    Phase("report_critical_high", ["Step 6b: Tier Writers"],
          ["report_critical_high.md"],
          base_timeout_s=300, model="haiku", critical=True),
    Phase("report_critical_high_merge", ["Step 6b: Tier Writers"],
          ["report_critical_high.md"],
          base_timeout_s=120, model="haiku", critical=True),
    Phase("report_medium", ["Step 6b: Tier Writers"],
          ["report_medium.md"],
          base_timeout_s=300, model="haiku", critical=True),
    Phase("report_medium_merge", ["Step 6b: Tier Writers"],
          ["report_medium.md"],
          base_timeout_s=120, model="haiku", critical=True),
    Phase("report_low_info", ["Step 6b: Tier Writers"],
          ["report_low_info.md"],
          base_timeout_s=300, model="haiku", critical=True),
    Phase("report_low_info_merge", ["Step 6b: Tier Writers"],
          ["report_low_info.md"],
          base_timeout_s=120, model="haiku", critical=True),
    Phase("report_assemble", ["Step 6c: Assembler"],
          ["AUDIT_REPORT.md"],
          base_timeout_s=1800, model="sonnet", critical=True),
]

L1_PHASES = [
    Phase("bake", ["Step 1.5: Phase 0.5 Bake"],
          ["primitive_status.md"],
          base_timeout_s=1800),
    Phase("recon", ["Step 2: L1 Recon"],
          ["recon_summary.md", "threat_model.md", "subsystem_map.md",
           "attack_surface.md", "trust_boundaries.md", "template_recommendations.md",
           "scope_leftover.md"],
          base_timeout_s=1500, critical=True),
    Phase("breadth", ["Step 3: Breadth"],
          ["analysis_*.md"],
          base_timeout_s=5400, model="sonnet", critical=True,
          min_artifacts_count=3),
    Phase("graph_sweeps", ["Step 4a.6: Graph-Sharded Audit Sweeps"],
          ["graph_sweep_summary.md"],
          base_timeout_s=1800, model="sonnet", critical=True,
          modes={"thorough"}),
    Phase("inventory_prepare", ["Step 4a: Finding Inventory"],
          ["inventory_shard_plan.md"],
          base_timeout_s=60, critical=True, model="haiku"),
    Phase("inventory_chunk_a", ["Step 4a: Finding Inventory"],
          ["findings_inventory_chunk_a.md"],
          base_timeout_s=1800, critical=True, model="sonnet"),
    Phase("inventory_chunk_b", ["Step 4a: Finding Inventory"],
          ["findings_inventory_chunk_b.md"],
          base_timeout_s=1800, critical=True, model="sonnet"),
    Phase("inventory_chunk_c", ["Step 4a: Finding Inventory"],
          ["findings_inventory_chunk_c.md"],
          base_timeout_s=1800, critical=True, model="sonnet"),
    Phase("inventory", ["Step 4a: Finding Inventory"],
          ["findings_inventory.md"],
          base_timeout_s=3000, critical=True, model="sonnet"),
    Phase("location_recovery", ["Step 4a: Finding Inventory"],
          ["location_recovery.md"],
          base_timeout_s=900, critical=True, model="sonnet",
          modes={"thorough"}),
    Phase("invariants", ["Step 4a.5: Semantic Invariants"],
          ["semantic_invariants.md"],
          base_timeout_s=1200, critical=False, modes={"core", "thorough"}),
    Phase("depth", ["Step 4b: Depth Loop"],
          ["depth_*_findings.md"],
          base_timeout_s=3600, model="opus", critical=True,
          min_artifacts_count=3,
          example_tokens=[
              "consensus_invariant", "network_surface", "state_trace",
              "edge_case", "external",
          ]),
    Phase("attention_repair", ["Step 4b.5: Attention Repair"],
          ["attention_repair_summary.md"],
          base_timeout_s=1500, model="sonnet", critical=True,
          modes={"thorough"}),
    Phase("rag_sweep", ["Step 4b.6: RAG Validation"],
          ["rag_validation.md"],
          base_timeout_s=1200, needs_mcp=True, model="sonnet", critical=True,
          modes={"core", "thorough"}),
    Phase("verify_queue", ["Step 4d: Verification Queue Manifest"],
          ["verification_queue.md"],
          base_timeout_s=600, critical=True, model="haiku"),
    Phase("semantic_dedup", ["Step 4e: Semantic Dedup"],
          ["dedup_decisions.md", "verification_queue_deduped.md"],
          base_timeout_s=1500, model="sonnet", critical=True),
    Phase("verify_crithigh", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_b", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_c", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_d", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_e", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_f", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_g", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_h", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_i", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_high_j", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_medium_a", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_medium_b", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_medium_c", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_medium_d", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_medium_e", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_medium_f", ["Step 5: Verification"],
          [],
          base_timeout_s=2100, critical=True, model="sonnet"),
    Phase("verify_low_a", ["Step 5: Verification"],
          [],
          base_timeout_s=1800, critical=True, modes={"thorough"}, model="sonnet"),
    Phase("verify_low_b", ["Step 5: Verification"],
          [],
          base_timeout_s=1800, critical=True, modes={"thorough"}, model="sonnet"),
    Phase("verify_low_c", ["Step 5: Verification"],
          [],
          base_timeout_s=1800, critical=True, modes={"thorough"}, model="sonnet"),
    Phase("verify_low_d", ["Step 5: Verification"],
          [],
          base_timeout_s=1800, critical=True, modes={"thorough"}, model="sonnet"),
    Phase("verify_aggregate", ["Step 5.6: Aggregate verify_core.md"],
          ["verify_core.md"],
          base_timeout_s=900, critical=True, model="haiku"),
    Phase("skeptic", ["Step 5.5: Skeptic-Judge"],
          ["skeptic_findings.md", "skeptic_judge_decisions.md"],
          base_timeout_s=1800, modes={"thorough"}, critical=True),
    Phase("crossbatch", ["Step 5.4: Cross-batch Consistency"],
          ["cross_batch_consistency.md"],
          base_timeout_s=900, model="sonnet", critical=True,
          modes={"core", "thorough"}),
    Phase("report_index", ["6a. Index Agent", "6a.1: Index Completeness Gate"],
          ["report_index.md", "report_coverage.md"],
          base_timeout_s=1500, model="sonnet", critical=True),
    # Body writer + confirmation + merge sentinels for all three tiers.
    # expand_shard_phases() replaces sentinels with per-shard phases when
    # the manifest builder splits a tier beyond its _BODY_SHARD_CAPS cap.
    Phase("report_body_writer_critical_high", ["6b. Tier Writers"],
          ["report_critical_high.md"],
          base_timeout_s=2400, model="sonnet", critical=True),
    Phase("report_body_writer_medium", ["6b. Tier Writers"],
          ["report_medium.md"],
          base_timeout_s=2400, model="sonnet", critical=True),
    Phase("report_body_writer_low_info", ["6b. Tier Writers"],
          ["report_low_info.md"],
          base_timeout_s=2400, model="sonnet", critical=True),
    Phase("report_critical_high", ["6b. Tier Writers", "6b.1: Tier File Completeness Gate"],
          ["report_critical_high.md"],
          base_timeout_s=300, model="haiku", critical=True),
    Phase("report_critical_high_merge", ["6b.1: Tier File Completeness Gate"],
          ["report_critical_high.md"],
          base_timeout_s=120, model="haiku", critical=True),
    Phase("report_medium", ["6b. Tier Writers", "6b.1: Tier File Completeness Gate"],
          ["report_medium.md"],
          base_timeout_s=300, model="haiku", critical=True),
    Phase("report_medium_merge", ["6b.1: Tier File Completeness Gate"],
          ["report_medium.md"],
          base_timeout_s=120, model="haiku", critical=True),
    Phase("report_low_info", ["6b. Tier Writers", "6b.1: Tier File Completeness Gate"],
          ["report_low_info.md"],
          base_timeout_s=300, model="haiku", critical=True),
    Phase("report_low_info_merge", ["6b.1: Tier File Completeness Gate"],
          ["report_low_info.md"],
          base_timeout_s=120, model="haiku", critical=True),
    Phase("report_assemble", ["6c. Assembler",
                              "Step 6.5: Mechanical Report Gates",
                              "Step 6.6: Report Preservation"],
          ["AUDIT_REPORT.md"],
          base_timeout_s=2400, model="sonnet", critical=True),
]
