"""Plamen V2 driver — slim orchestrator.

Imports all public names from the 4 sub-modules so existing test files
that do `import plamen_driver as D` continue to work unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from plamen_types import *  # noqa: F403,F401
from plamen_parsers import *  # noqa: F403,F401
from plamen_validators import *  # noqa: F403,F401
from plamen_mechanical import *  # noqa: F403,F401
from plamen_prompt import *  # noqa: F403,F401
import plamen_display as display

# Rate-limit detection: JSON-first (structured), text-fallback (unstructured).
#
# BACKGROUND: Plain-text regex on the stdio log tail was the source of a
# $120 false-positive loop — Claude's own hallucinated prose about quota
# exhaustion post-compaction matched the regex and caused the driver to
# pause the pipeline when no API error had actually occurred.
#
# FIX: `claude -p --output-format json` writes a structured envelope as the
# final stdout chunk. Parse that envelope and trust its fields:
#   - `is_error: true` AND `api_error_status in (429, 529)` → rate limit
#   - `stop_reason: "rate_limited"` / similar → rate limit
# Only fall back to text regex if the envelope is unparseable (subprocess
# crashed before writing JSON). And in fallback, require the error signal
# to co-occur with an HTTP-style status or API error prefix — strings
# inside LLM prose don't have those.
_API_RATE_LIMIT_STATUSES = {429, 529}  # 429 Too Many Requests, 529 Overloaded


def _format_ai_model_summary(config: dict, active_phases: list[Phase], mode: str) -> str:
    """Return a concise runtime/model summary for the startup banner."""
    backend = (config.get("cli_backend") or "claude").strip().lower()
    backend_label = "Codex CLI" if backend == "codex" else "Claude Code"

    models: list[str] = []
    for phase in active_phases:
        try:
            model = phase_model(phase, mode, config)
        except Exception:
            model = (getattr(phase, "model", "") or "sonnet").strip()
        if model and model not in models:
            models.append(model)

    if not models:
        return backend_label
    if len(models) == 1:
        model_text = models[0]
    elif len(models) <= 3:
        model_text = ", ".join(models)
    else:
        model_text = ", ".join(models[:3]) + f" +{len(models) - 3} more"
    return f"{backend_label} / {model_text}"


# Text-fallback regex: only triggers when an HTTP status or structured
# error prefix is present. Claude's own prose like "quota exhausted until
# Apr 23" won't match because no 429/status-code is adjacent.
#
# Covers both 429 (rate_limit_error) and 529 (overloaded_error). The text
# fallback path fires ONLY when the JSON envelope is unparseable (crash
# before envelope). In that mode we MUST still catch overload pauses, not
# just rate limits — Anthropic returns 529 during provider-wide overload
# and the retry semantics are the same.
_STRUCTURED_RATE_LIMIT_RE = re.compile(
    r"\b(?:"
    r"429\s*(?:too\s+many\s+requests|status|http|error|rate)"
    r"|529\s*(?:overloaded|status|http|error)"
    r"|status[_ ]?code[=:\s]+(?:429|529)"
    r"|api[_ ]?error[_ ]?status[=:\s\"]+(?:429|529)"
    r"|\"type\"\s*:\s*\"rate_limit_error\""
    r"|\"type\"\s*:\s*\"overloaded_error\""
    r"|\"error\"\s*:\s*\{\s*\"type\"\s*:\s*\"(?:rate_limit|overloaded)"
    r"|rate[_ ]?limit[_ ]?error"
    r"|overloaded[_ ]?error"
    r"|anthropic.*(?:429|529)"
    r")\b",
    re.IGNORECASE,
)


# ── Codex backend constants ───────────────────────────────────────────────

_CODEX_PREAMBLE_SINGLE_AGENT = """\
## Agent Spawning (IMPORTANT)

You are running inside `codex exec` as a SINGLE-TURN non-interactive subprocess.
This phase runs as a SINGLE AGENT — do NOT use `spawn_agent`. Execute all
analysis directly in this session.

When instructions say "spawn agent" or "use Task tool" or "use Agent tool":
- If the instruction is about a PARALLEL sub-agent within your phase: perform
  the analysis yourself sequentially instead. You ARE the agent.
- If the instruction is about a SUBSEQUENT pipeline phase: STOP. Do not proceed
  to subsequent phases. The driver spawns those separately.

When instructions contain `Task(subagent_type=..., prompt="...")` blocks, treat
the PROMPT CONTENT inside those blocks as YOUR OWN analysis instructions.
Execute the analysis described in the prompt text directly.

## Model Tier Mapping

- `model="opus"` → `gpt-5.5`
- `model="sonnet"` → `gpt-5.4`
- `model="haiku"` → `gpt-5.4-mini`
- `subagent_type="general-purpose"` → you (single agent, perform the work)
- `subagent_type="security-analyzer"` → you (single agent, perform the work)

## Network & MCP

Network access is NOT available in this sandbox. Do NOT attempt curl, wget,
or any HTTP requests. MCP tools (slither, unified-vuln-db, solana-fender) are
unavailable — when instructions reference MCP tool calls, skip them and use
direct code analysis instead.
"""

_CODEX_PREAMBLE_MULTI_AGENT = """\
## Agent Spawning — MULTI-AGENT MODE (IMPORTANT)

You are running inside `codex exec` as the ORCHESTRATOR for this phase.
You MUST use `spawn_agent` to run analysis work in PARALLEL sub-agents.

### How to spawn sub-agents

When the methodology says `Task(subagent_type=..., prompt="...")` or
instructs you to "spawn agents" or "use the Task tool":

1. Use `spawn_agent` with the prompt content from the Task block.
   Each `spawn_agent` call creates a child agent that runs independently.
2. Spawn ALL independent agents in rapid succession (do not wait between
   spawns). Codex runs up to 6 agents concurrently.
3. After all agents are spawned, use `wait_agent` to block until each
   agent reaches its final status.
4. If an agent's output is needed before spawning a dependent agent,
   `wait_agent` on it first, then spawn the dependent one.

### Translation rules

| Claude Code | Codex equivalent |
|-------------|-----------------|
| `Task(subagent_type="general-purpose", model="sonnet", prompt="...")` | `spawn_agent(prompt="...")` |
| `Task(subagent_type="security-analyzer", prompt="...")` | `spawn_agent(prompt="...")` |
| `Task(subagent_type="depth-token-flow", prompt="...")` | `spawn_agent(prompt="...")` |
| Await agent results | `wait_agent(agent_id)` |
| Send follow-up to agent | `send_input(agent_id, message)` |

### Critical rules

- Sub-agents SHARE your working directory and scratchpad. Each agent
  MUST write to its own designated output file to avoid conflicts.
- Sub-agents inherit your sandbox policy and model. You cannot override
  the model per sub-agent — all children run your model.
- Do NOT spawn agents for SUBSEQUENT pipeline phases. Only spawn agents
  for work within YOUR assigned phase sections.
- If the methodology describes an ORCHESTRATOR SPLIT DIRECTIVE table
  with agent assignments, follow it: spawn one agent per assignment
  with the corresponding task scope.
- Maximum 6 concurrent agents. If the methodology calls for more,
  batch them: spawn 6, wait for completion, then spawn the next batch.
- If a sub-agent fails or times out, log the failure and continue with
  remaining agents. Do not halt the phase.

## Model Tier Mapping

- `model="opus"` → `gpt-5.5` (parent model — children inherit this)
- `model="sonnet"` → `gpt-5.4`
- `model="haiku"` → `gpt-5.4-mini`
- NOTE: All sub-agents run YOUR model. Model specifications in Task
  blocks (e.g., `model="sonnet"`) are informational only — they cannot
  be overridden per sub-agent in Codex.

## Network & MCP

Network access is NOT available in this sandbox. Do NOT attempt curl, wget,
or any HTTP requests. MCP tools (slither, unified-vuln-db, solana-fender) are
unavailable — when instructions reference MCP tool calls, skip them and use
direct code analysis instead.
"""

# Re-export from plamen_types for local use. Phases where the LLM should
# use spawn_agent for parallel sub-agents instead of sequential execution.
_CODEX_MULTI_AGENT_PHASES = CODEX_MULTI_AGENT_PHASES

_CODEX_TOOL_POSIX = """\
## Tool Translation (Codex Runtime — POSIX)

When instructions reference Claude Code tools, use these Codex equivalents:
- "Read tool" / "Read file" → `shell` tool: `cat -n <file>`
- "Write tool" / "Write file" / create a new file →
    `shell` tool with heredoc:
    ```
    cat > path/to/file.md <<'PLAMEN_EOF'
    file content here
    PLAMEN_EOF
    ```
- "Edit tool" / modify existing file → `apply_patch` tool (unified diff)
- "Grep tool" → `shell` tool: `grep -rn 'pattern' path/` or `rg 'pattern'`
- "Glob tool" → `shell` tool: `find . -name '*.rs'` or `fd -e rs`
- "Bash tool" → `shell` tool
"""

_CODEX_TOOL_WINDOWS = """\
## Tool Translation (Codex Runtime — Windows / PowerShell)

When instructions reference Claude Code tools, use these Codex equivalents:
- "Read tool" / "Read file" → `shell` tool: `Get-Content <file>` or `type <file>`
- "Write tool" / "Write file" / create a new file →
    `shell` tool with PowerShell here-string:
    ```powershell
    @"
    file content here
    "@ | Out-File -FilePath "path/to/file.md" -Encoding utf8
    ```
    Or for short content: `Set-Content -Path "file.md" -Value "content"`
- "Edit tool" / modify existing file → `apply_patch` tool (unified diff)
- "Grep tool" → `shell` tool: `Select-String -Path "*.rs" -Pattern "pattern" -Recurse`
    or `rg 'pattern'` (if ripgrep installed)
- "Glob tool" → `shell` tool: `Get-ChildItem -Recurse -Filter "*.rs"`
- "Bash tool" → `shell` tool (PowerShell)

IMPORTANT: This is a Windows PowerShell environment. Do NOT use Unix commands
like `cat`, `grep`, `find`, `sed`, `awk`. Use PowerShell equivalents above.
"""


def _codex_tool_preamble(*, multi_agent: bool = False) -> str:
    """Return platform-appropriate Codex tool preamble with agent mode section.

    multi_agent=True selects the spawn_agent-based preamble for orchestrator
    phases (breadth, depth, rescan, recon). False selects the single-agent
    preamble for reducer/formatter phases.
    """
    tool_section = (
        _CODEX_TOOL_WINDOWS if sys.platform == "win32" else _CODEX_TOOL_POSIX
    )
    agent_section = (
        _CODEX_PREAMBLE_MULTI_AGENT if multi_agent
        else _CODEX_PREAMBLE_SINGLE_AGENT
    )
    return tool_section + "\n" + agent_section

_CODEX_PRICING: dict[str, tuple[float, float]] = {
    # model: (input_per_1M_tokens, output_per_1M_tokens)
    "gpt-5.5":      (5.00, 30.00),
    "gpt-5.4":      (2.50, 15.00),
    "gpt-5.4-mini": (0.75,  4.50),
    "gpt-5.4-nano": (0.20,  1.25),
    "o3":           (2.00,  8.00),
    "o4-mini":      (1.10,  4.40),
    "gpt-4.1":      (2.00,  8.00),
    "gpt-4.1-mini": (0.40,  1.60),
    "gpt-4.1-nano": (0.10,  0.40),
}

_CODEX_RATE_LIMIT_RE = re.compile(
    r"(?:"
    r"rate_limit_exceeded"
    r"|rate_limit_error"
    r"|usage_limit_reached"
    r"|insufficient_quota"
    r"|billing_hard_limit_reached"
    r"|tokens_usage_based"
    r"|Too Many Requests"
    r"|\"type\"\s*:\s*\"rate_limit"
    r"|\"type\"\s*:\s*\"usage_limit"
    r"|\"code\"\s*:\s*\"rate_limit"
    r"|status[=:\s]+429"
    r"|HTTP\s+429"
    r"|Error:\s*429"
    r"|selected\s+model\s+is\s+at\s+capacity"
    r"|model\s+is\s+at\s+capacity"
    r")",
    re.IGNORECASE,
)


def _codex_depth_artifact_checklist(pipeline: str, mode: str) -> str:
    """Return a mandatory artifact checklist for the Codex depth phase.

    gpt-5.5 reliably spawns 1-2 sub-agents from the generic multi-agent
    preamble but misses the full set of Thorough-only sub-steps
    (confidence scoring, DST, perturbation, skill checklist). This
    checklist makes the complete spawn plan explicit and unmissable.
    """
    is_thorough = mode == "thorough"
    if pipeline == "l1":
        lines = [
            "## MANDATORY DEPTH ARTIFACT CHECKLIST (Codex — HARD GATE)",
            "",
            "The post-phase gate WILL FAIL unless ALL artifacts below exist",
            "and are ≥200 bytes. You must spawn_agent for each group.",
            "",
            "### Batch 1: Core depth agents (spawn ALL 5 in parallel)",
            "",
            "| # | spawn_agent prompt scope | Output file | Required |",
            "|---|-------------------------|-------------|----------|",
            "| 1 | depth-consensus-invariant (consensus safety, fork choice, BLS, validator lifecycle) | depth_consensus_invariant_findings.md | YES |",
            "| 2 | depth-network-surface (p2p DoS, mempool, RPC, eclipse) | depth_network_surface_findings.md | YES |",
            "| 3 | depth-state-trace (state sync, pruning, execution hardening) | depth_state_trace_findings.md | YES |",
            "| 4 | depth-external (dependency audit, cross-environment drift) | depth_external_findings.md | YES |",
            "| 5 | depth-edge-case (boundary conditions, zero-state) | depth_edge_case_findings.md | YES |",
            "",
            "After spawning all 5, use wait_agent on each.",
            "",
            "### CRITICAL: Post-wait output verification",
            "",
            "After EACH wait_agent completes, verify the output file exists",
            "and is ≥200 bytes. Codex agents can report DONE with 0-byte",
            "output (thread limit, content filter, or silent failure). For",
            "each 0-byte or missing file:",
            "1. Close the completed (failed) agent with close_agent",
            "2. Spawn a NEW agent for that role with the same prompt",
            "3. Wait and re-verify",
            "Do NOT proceed to Batch 2 until all Batch 1 files are ≥200 bytes.",
        ]
        if is_thorough:
            lines.extend([
                "",
                "### Batch 2: Thorough-only sub-steps (after Batch 1 completes)",
                "",
                "| # | spawn_agent prompt scope | Output file | Required |",
                "|---|-------------------------|-------------|----------|",
                "| 6 | Confidence scoring (4-axis per phase4-confidence-scoring.md) | confidence_scores.md | YES |",
                "| 7 | Design Stress Testing (design limits, parameter extremes) | design_stress_findings.md | YES |",
                "",
                "### Batch 3: After confidence scores exist",
                "",
                "| # | spawn_agent prompt scope | Output file | Required |",
                "|---|-------------------------|-------------|----------|",
                "| 8 | DA iteration 2 (Devil's Advocate on UNCERTAIN findings) | depth_iter2_*_findings.md | IF uncertain Medium+ |",
                "",
                "### Batch 4: Final parallel pair",
                "",
                "| # | spawn_agent prompt scope | Output file | Required |",
                "|---|-------------------------|-------------|----------|",
                "| 9 | Perturbation (DIRECTION_FLIP, TIMING_SHIFT, ACTOR_SWAP) | perturbation_findings.md | YES |",
                "| 10 | Skill Execution Checklist (verify skill steps executed) | skill_execution_gaps.md | YES |",
                "",
                "### Execution sequence",
                "",
                "```",
                "Batch 1: spawn agents 1-5 in parallel → wait_agent all",
                "Batch 2: spawn agents 6-7 in parallel → wait_agent all",
                "Batch 3: if uncertain Medium+ in confidence_scores.md → spawn agent 8 → wait_agent",
                "Batch 4: spawn agents 9-10 in parallel → wait_agent all",
                "Finally: write never_cut_checkpoint.md + depth_exit.md",
                "```",
                "",
                "FAILURE MODE: If you return after Batch 1 without spawning",
                "Batches 2-4, the gate rejects your output and forces a retry.",
                "Complete ALL batches before returning.",
            ])
        elif mode == "core":
            lines.extend([
                "",
                "### Batch 2: Core confidence scoring (after Batch 1 completes)",
                "",
                "| # | spawn_agent prompt scope | Output file | Required |",
                "|---|-------------------------|-------------|----------|",
                "| 6 | Confidence scoring (2-axis: Evidence x 0.5 + Analysis Quality x 0.5) | confidence_scores.md | YES |",
                "",
                "```",
                "Batch 1: spawn agents 1-5 in parallel → wait_agent all",
                "Batch 2: spawn agent 6 → wait_agent",
                "Finally: write never_cut_checkpoint.md + depth_exit.md",
                "```",
            ])
        else:
            # Light mode: depth agents only, no confidence scoring
            lines.extend([
                "",
                "Light mode: only Batch 1 is required. Write",
                "never_cut_checkpoint.md + depth_exit.md after all 5 complete.",
            ])
    else:
        # SC pipeline
        lines = [
            "## MANDATORY DEPTH ARTIFACT CHECKLIST (Codex — HARD GATE)",
            "",
            "The post-phase gate WILL FAIL unless ALL artifacts below exist",
            "and are ≥200 bytes. You must spawn_agent for each group.",
            "",
            "### Batch 1: Core depth agents (spawn ALL 4 in parallel)",
            "",
            "| # | spawn_agent prompt scope | Output file | Required |",
            "|---|-------------------------|-------------|----------|",
            "| 1 | depth-token-flow (token entry/exit, donation attacks) | depth_token_flow_findings.md | YES |",
            "| 2 | depth-state-trace (cross-function state mutation) | depth_state_trace_findings.md | YES |",
            "| 3 | depth-edge-case (zero-state, dust, boundary) | depth_edge_case_findings.md | YES |",
            "| 4 | depth-external (external calls, MEV, cross-chain) | depth_external_findings.md | YES |",
            "",
            "After spawning all 4, use wait_agent on each.",
            "",
            "### CRITICAL: Post-wait output verification",
            "",
            "After EACH wait_agent completes, verify the output file exists",
            "and is ≥200 bytes. Codex agents can report DONE with 0-byte",
            "output (thread limit, content filter, or silent failure). For",
            "each 0-byte or missing file:",
            "1. Close the completed (failed) agent with close_agent",
            "2. Spawn a NEW agent for that role with the same prompt",
            "3. Wait and re-verify",
            "Do NOT proceed to Batch 2 until all Batch 1 files are ≥200 bytes.",
        ]
        if is_thorough:
            lines.extend([
                "",
                "### Batch 2: Thorough-only sub-steps (after Batch 1 completes)",
                "",
                "| # | spawn_agent prompt scope | Output file | Required |",
                "|---|-------------------------|-------------|----------|",
                "| 5 | Confidence scoring (4-axis per phase4-confidence-scoring.md) | confidence_scores.md | YES |",
                "| 6 | Design Stress Testing (design limits, parameter extremes) | design_stress_findings.md | YES |",
                "",
                "### Batch 3: After confidence scores exist",
                "",
                "| # | spawn_agent prompt scope | Output file | Required |",
                "|---|-------------------------|-------------|----------|",
                "| 7 | DA iteration 2 (Devil's Advocate on UNCERTAIN findings) | depth_iter2_*_findings.md | IF uncertain Medium+ |",
                "",
                "### Batch 4: Final parallel pair",
                "",
                "| # | spawn_agent prompt scope | Output file | Required |",
                "|---|-------------------------|-------------|----------|",
                "| 8 | Perturbation (DIRECTION_FLIP, TIMING_SHIFT, ACTOR_SWAP) | perturbation_findings.md | YES |",
                "| 9 | Skill Execution Checklist (verify skill steps executed) | skill_execution_gaps.md | YES |",
                "",
                "### Execution sequence",
                "",
                "```",
                "Batch 1: spawn agents 1-4 in parallel → wait_agent all",
                "Batch 2: spawn agents 5-6 in parallel → wait_agent all",
                "Batch 3: if uncertain Medium+ in confidence_scores.md → spawn agent 7 → wait_agent",
                "Batch 4: spawn agents 8-9 in parallel → wait_agent all",
                "Finally: write never_cut_checkpoint.md + depth_exit.md",
                "```",
                "",
                "FAILURE MODE: If you return after Batch 1 without spawning",
                "Batches 2-4, the gate rejects your output and forces a retry.",
                "Complete ALL batches before returning.",
            ])
        elif mode in ("core", ""):
            # SC Core mode: 4 depth agents + 4 scanners + validation sweep +
            # confidence scoring (2-axis) per AUDIT MODES table.
            lines.extend([
                "",
                "### Batch 2: Core sub-steps (after Batch 1 completes)",
                "",
                "| # | spawn_agent prompt scope | Output file | Required |",
                "|---|-------------------------|-------------|----------|",
                "| 5 | Blind Spot Scanner A (systematic checks) | blind_spot_a_findings.md | YES |",
                "| 6 | Blind Spot Scanner B (systematic checks) | blind_spot_b_findings.md | YES |",
                "| 7 | Blind Spot Scanner C (systematic checks) | blind_spot_c_findings.md | YES |",
                "| 8 | Validation Sweep (cross-agent consistency) | validation_sweep_findings.md | YES |",
                "| 9 | Confidence scoring (2-axis: Evidence x 0.5 + Analysis Quality x 0.5) | confidence_scores.md | YES |",
                "",
                "```",
                "Batch 1: spawn agents 1-4 in parallel → wait_agent all",
                "Batch 2: spawn agents 5-9 in parallel → wait_agent all",
                "Finally: write never_cut_checkpoint.md + depth_exit.md",
                "```",
            ])
    lines.append("")
    return "\n".join(lines)


def _translate_prompt_for_codex(prompt_text: str, *,
                               phase_name: str = "",
                               pipeline: str = "",
                               mode: str = "") -> str:
    """Translate Claude-specific prompt content for Codex runtime.

    Path translation: only rewrite ~/.claude/ → ~/.codex/plamen/ when the
    target directory actually exists on disk.  Otherwise keep ~/.claude/ as-is
    — the Codex sandbox can read the entire filesystem, so the original paths
    resolve fine.

    Also strips Claude-specific references that create noise or contradiction
    in a Codex subprocess (MCP timeout mentions, AskUserQuestion references,
    "Task tool" phrasing).

    Multi-agent phases (breadth, depth, rescan, recon) get the spawn_agent
    preamble instead of the single-agent preamble, enabling parallel sub-agent
    execution within the phase.

    For the depth phase, injects a mandatory artifact checklist with an
    explicit spawn plan so gpt-5.5 produces all required artifacts on
    attempt 1 (v2.6.2).
    """
    codex_home = Path.home() / ".codex" / "plamen"
    if codex_home.is_dir():
        translated = prompt_text.replace("~/.claude/", "~/.codex/plamen/")
        if sys.platform == "win32":
            home = str(Path.home()).replace("\\", "/")
            translated = translated.replace(
                f"{home}/.claude/", "~/.codex/plamen/"
            )
            # Also handle native backslash form from plamen_home() on Windows
            home_native = str(Path.home())
            translated = translated.replace(
                f"{home_native}\\.claude\\", "~/.codex/plamen/"
            )
    else:
        raise RuntimeError(
            f"Codex backend is active but {codex_home} does not exist. "
            f"Create it as a symlink to your Plamen install "
            f"(e.g., mklink /D \"{codex_home}\" \"{Path.home() / '.claude'}\")"
        )

    translated = translated.replace("claude -p subprocess", "codex exec subprocess")
    translated = translated.replace("Claude Code's MCP timeout is 300s", "MCP tools are unavailable in this runtime")
    translated = translated.replace(
        "Claude Code's tool timeout is set to 300s (5 min) via MCP_TOOL_TIMEOUT in settings.json to accommodate ChromaDB cold start.",
        "MCP tools are unavailable in this runtime.",
    )
    translated = re.sub(
        r"do NOT call AskUserQuestion\b",
        "do NOT ask the user questions",
        translated,
    )
    is_multi = phase_name in _CODEX_MULTI_AGENT_PHASES
    if is_multi:
        # Multi-agent phases: rewrite Task tool references to spawn_agent.
        # Keep Task() block structure intact — the preamble tells the model
        # how to translate them to spawn_agent calls.
        translated = re.sub(
            r"\buse the Task tool\b",
            "use spawn_agent",
            translated,
            flags=re.IGNORECASE,
        )
    else:
        # Single-agent phases: tell model to execute directly.
        translated = re.sub(
            r"\buse the Task tool\b",
            "execute the analysis directly",
            translated,
            flags=re.IGNORECASE,
        )

    preamble = _codex_tool_preamble(multi_agent=is_multi)

    # v2.6.2: inject mandatory artifact checklist for depth phase.
    # gpt-5.5 reliably spawns the core depth agents but misses Thorough
    # sub-steps (confidence scoring, DST, perturbation, skill checklist)
    # without an explicit spawn plan.
    depth_checklist = ""
    if phase_name == "depth" and is_multi:
        depth_checklist = _codex_depth_artifact_checklist(
            pipeline or "sc", mode or "core"
        ) + "\n"

    return preamble + "\n" + depth_checklist + translated


_CODEX_CONTEXT_LIMITS: dict[str, int] = {
    # Conservative token limits per model (chars ÷ 4 ≈ tokens).
    # Codex hard-errors on exceed (not silent truncation).
    # GPT-5.5: 1M context window (opus-tier, $5/$30 per 1M tokens)
    "gpt-5.5": 800_000,
    # GPT-5.4: 1M context (previous frontier, $2.50/$15)
    "gpt-5.4": 800_000,
    # GPT-5.4-mini: 400K context (sonnet-tier, $0.75/$4.50)
    "gpt-5.4-mini": 320_000,
    # GPT-5.4-nano: 400K context (haiku-tier, $0.20/$1.25)
    "gpt-5.4-nano": 320_000,
    # Legacy (deprecated Feb 2026, API sunset Oct 2026)
    "o3": 200_000,
    "o4-mini": 200_000,
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
}


def _codex_prompt_fits(prompt: str, model: str) -> bool:
    """Check if prompt likely fits the model's context window.

    Returns True if safe, False if prompt is dangerously large.
    Uses ~4 chars/token heuristic with a 20% safety margin for
    tool outputs and response tokens.
    """
    limit = _CODEX_CONTEXT_LIMITS.get(model, 272_000)
    # Reserve 20% for response + tool outputs
    effective_limit = int(limit * 0.80)
    estimated_tokens = len(prompt) // 4
    return estimated_tokens <= effective_limit


def _codex_auth_available() -> bool:
    """Check if Codex authentication is available (OAuth or API key).

    Without auth, `codex exec` will attempt interactive browser login,
    hanging indefinitely in a subprocess with no TTY.
    """
    if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return True
    auth_path = Path.home() / ".codex" / "auth.json"
    return auth_path.exists()


def _codex_auth_is_chatgpt() -> bool:
    """Return True if Codex is authenticated via ChatGPT OAuth (not API key).

    ChatGPT-auth accounts cannot use `--model` flag — the server rejects ALL
    explicit model names with "not supported when using Codex with a ChatGPT
    account". The account's subscription tier determines the default model
    automatically (Pro → GPT-5, Plus → GPT-4.1, etc).
    """
    if os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return False
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.exists():
        return False
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        return data.get("auth_mode") == "chatgpt"
    except Exception:
        return False


def _build_codex_cmd(effective_model: str, *, needs_mcp: bool = False,
                     output_last_message: str = "",
                     writable_dirs: list[str] | None = None) -> list[str]:
    """Build the codex exec command array for a phase subprocess.

    Uses --dangerously-bypass-approvals-and-sandbox to skip Codex's built-in
    approval prompts and sandbox restrictions. Without this flag, `codex exec`
    auto-rejects all tool calls (apply_patch, shell) with "rejected by user
    approval settings" — making zero artifact writes possible. This is
    Codex's equivalent of Claude Code's --dangerously-skip-permissions.

    The Plamen driver already controls the subprocess lifecycle, timeout, and
    output validation — the external orchestration IS the sandbox.

    --skip-git-repo-check: audit targets may not be git repos (extracted archives).
    --output-last-message: writes final agent message to a file for reliable extraction.
    --add-dir: retained for documentation; harmless with bypass active.

    ChatGPT-auth accounts cannot use --model flag at all — the server rejects
    every explicit model name with "not supported when using Codex with a
    ChatGPT account". The subscription tier (Free/Plus/Pro) determines the
    default model automatically. Only API-key auth supports --model.
    """
    cmd = [CODEX_BIN, "exec"]
    # ChatGPT-auth accounts may reject --model depending on plan/token state.
    # Always try with --model first; the caller retries without it on failure
    # (see _detect_codex_model_rejection).
    cmd.extend(["--model", effective_model])
    cmd.extend([
        "--json",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
    ])
    if writable_dirs:
        for d in writable_dirs:
            cmd.extend(["--add-dir", d])
    if output_last_message:
        cmd.extend(["--output-last-message", output_last_message])
    if effective_model in ("o3", "o4-mini"):
        cmd.extend(["-c", 'model_reasoning_effort="high"'])
    cmd.append("-")  # read prompt from stdin
    return cmd


def _detect_codex_model_rejection(log_path: Path) -> bool:
    """Detect if Codex rejected the --model flag (ChatGPT account restriction).

    ChatGPT-auth accounts may reject explicit --model depending on plan state
    or token freshness. When detected, the driver retries without --model,
    letting the subscription tier determine the default model automatically.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "not supported when using Codex with a ChatGPT account" in text


def _detect_codex_model_not_available(log_path: Path) -> bool:
    """Detect if Codex failed because the requested model isn't on the user's plan.

    API-key accounts that lack access to a specific model (e.g., gpt-5.5
    requires a higher-tier plan) get a 404 "model not found" or 403 "access
    denied" that is distinct from a credential failure. This must be checked
    BEFORE _detect_codex_auth_error, which would otherwise misclassify it as
    a permanent auth failure instead of a recoverable model-downgrade scenario.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(
        r"(?:model.*(?:not\s+found|does\s+not\s+exist|not\s+available)"
        r"|(?:not\s+found|does\s+not\s+exist).*model"
        r"|access.*denied.*model|model.*access.*denied"
        r"|you\s+do\s+not\s+have\s+access.*model"
        r"|insufficient.*(?:plan|tier|quota).*model"
        r"|status[=:\s]+404.*model|model.*status[=:\s]+404"
        r"|The\s+model\s+`[^`]+`\s+does\s+not\s+exist)",
        text, re.IGNORECASE,
    ))


def _detect_codex_model_capacity(log_path: Path) -> bool:
    """Detect transient Codex/OpenAI selected-model capacity failures."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(
        r"(?:selected\s+model\s+is\s+at\s+capacity|model\s+is\s+at\s+capacity)",
        text,
        re.IGNORECASE,
    ))


def _codex_next_fallback_model(current_model: str, attempted: list[str] | None = None) -> Optional[str]:
    """Return the next configured Codex fallback model after a capacity miss."""
    attempted_set = {m for m in (attempted or []) if m}
    attempted_set.add(current_model)
    for candidate in _CODEX_FALLBACK_MODEL_ORDER:
        if candidate and candidate not in attempted_set:
            return candidate
    return None


def _build_codex_cmd_no_model(*, needs_mcp: bool = False,
                              output_last_message: str = "",
                              writable_dirs: list[str] | None = None) -> list[str]:
    """Build codex exec command WITHOUT --model flag (ChatGPT-auth fallback).

    Uses --dangerously-bypass-approvals-and-sandbox for same reason as
    _build_codex_cmd — without it, all tool calls are auto-rejected.
    """
    cmd = [
        CODEX_BIN, "exec",
        "--json",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
    ]
    if writable_dirs:
        for d in writable_dirs:
            cmd.extend(["--add-dir", d])
    if output_last_message:
        cmd.extend(["--output-last-message", output_last_message])
    cmd.append("-")
    return cmd


def _detect_codex_cli_crash(log_path: Path) -> bool:
    """Detect if Codex subprocess crashed due to invalid CLI arguments.

    CLI argument errors (e.g., --disallowedTools not recognized) are permanent
    failures — retrying with identical args is pointless. The driver should
    surface the error instead of burning retry budget.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(
        r"(?:unexpected argument|unrecognized option|Usage: codex exec"
        r"|error:.*found\b.*tip:)",
        text, re.IGNORECASE,
    ))


def _detect_codex_auth_error(log_path: Path) -> bool:
    """Check if a Codex subprocess failed due to authentication issues.

    Auth errors (401/403, token expiry) should NOT trigger rate-limit pause
    logic — they need re-authentication, not backoff.

    IMPORTANT: Call _detect_codex_model_not_available BEFORE this function.
    Model-not-available (404/403 for missing model access) would otherwise
    match the 401/403 patterns here and cause a permanent halt instead of
    a graceful model downgrade.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # Exclude model/capacity/rate-limit patterns from auth classification.
    # Codex JSON logs include the full audit prompt and model transcript, so
    # words like "Unauthorized" commonly appear as vulnerability text. Auth
    # matching must be anchored to actual provider/CLI error fields.
    if _detect_codex_model_not_available(log_path) or _CODEX_RATE_LIMIT_RE.search(text):
        return False
    return bool(re.search(
        r"(?:status[=:\s]+401|HTTP\s+401"
        r"|(?:\"(?:type|code|message)\"\s*:\s*\"[^\"]*(?:unauthorized|invalid_api_key|authentication|auth)[^\"]*\")"
        r"|(?:error|api error|provider error|codex error)[^\r\n]{0,160}(?:unauthorized|invalid_api_key|token[^\r\n]{0,40}expired|authentication[^\r\n]{0,40}failed|auth[^\r\n]{0,40}error))",
        text, re.IGNORECASE,
    ))


def _detect_codex_rate_limit(log_path: Path, returncode: int) -> bool:
    """Check if a Codex subprocess failed due to rate limiting.

    Checks JSONL output regardless of returncode because Codex may report
    usage_limit_reached in the event stream with rc=0 (graceful stop).

    Returns False for auth errors (401) — those need re-auth, not backoff.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # Auth errors are NOT rate limits — discriminate early
    if _detect_codex_auth_error(log_path):
        return False
    if returncode == 0:
        # On success, only check for plan-cap exhaustion (not transient 429s)
        return bool(re.search(r"usage_limit_reached|billing_hard_limit_reached",
                              text, re.IGNORECASE))
    return bool(_CODEX_RATE_LIMIT_RE.search(text))


def _detect_codex_context_exceeded(log_path: Path) -> bool:
    """Check if a Codex subprocess failed because prompt exceeded context window.

    Codex hard-errors (not truncates) when input exceeds the model's context.
    This needs different handling than rate limits: shrink prompt or use bigger model.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(
        r"exceeds.*context.window|context_length_exceeded|max_tokens_exceeded"
        r"|maximum context length",
        text, re.IGNORECASE,
    ))


def _detect_codex_content_filter(log_path: Path) -> bool:
    """Detect if a Codex subprocess was killed by OpenAI's content safety filter.

    The safety filter flags security audit prompts as "cybersecurity risk" and
    terminates the turn before any subagent work can proceed.  The error is
    nondeterministic — a retry with a slightly different prompt shape (e.g. the
    retry hint prepended) often passes.  Treated as transient: the driver gets
    one bonus retry that does NOT consume the normal retry budget.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(
        r"flagged for (?:possible )?cybersecurity risk"
        r"|Trusted Access for Cyber",
        text, re.IGNORECASE,
    ))


def _precreate_codex_artifacts(phase: "Phase", scratchpad: Path) -> None:
    """Seed empty files for expected artifacts so apply_patch has targets.

    Codex's apply_patch tool cannot create new files — it only modifies
    existing ones. By pre-creating each expected artifact as an empty file,
    the model can use apply_patch to write content into them. Files that
    already have content (from a prior attempt or phase) are left untouched.
    Glob patterns with wildcards are expanded to a single representative
    filename using the phase's example_tokens if available.
    """
    for pattern in phase.expected_artifacts:
        if "*" in pattern or "?" in pattern:
            if phase.example_tokens:
                for token in phase.example_tokens:
                    concrete = pattern.replace("*", token, 1)
                    target = scratchpad / concrete
                    if not target.exists():
                        try:
                            target.write_text("", encoding="utf-8")
                        except OSError:
                            pass
        else:
            target = scratchpad / pattern
            if not target.exists():
                try:
                    target.write_text("", encoding="utf-8")
                except OSError:
                    pass


def _synthesize_depth_lifecycle_artifacts(
    scratchpad: Path, pipeline: str, *, force: bool = False,
    mode: str = "core",
) -> list[str]:
    """Auto-generate never_cut_checkpoint.md and depth_exit.md from disk state.

    Codex/GPT models reliably produce finding files but frequently omit the
    lifecycle metadata artifacts the gate requires, or write them in a format
    the validator rejects.  These are mechanically derivable from what's on
    disk — no LLM reasoning needed.

    When *force* is True (Codex backend), always overwrite — the driver's
    mechanical version is more reliable than whatever the LLM wrote.

    v2.6.3: mode-aware — Thorough-only roles (design-stress, perturbation,
    skill-execution-checklist) are only included when mode == "thorough".

    Returns list of files synthesized (for logging).
    """
    synthesized: list[str] = []

    # --- never_cut_checkpoint.md ---
    ncc = scratchpad / "never_cut_checkpoint.md"
    if force or not ncc.exists():
        if pipeline == "l1":
            role_file_map = {
                "depth-consensus-invariant": "depth_consensus_invariant_findings.md",
                "depth-network-surface": "depth_network_surface_findings.md",
                "depth-state-trace": "depth_state_trace_findings.md",
                "depth-external": "depth_external_findings.md",
                "depth-edge-case": "depth_edge_case_findings.md",
                "confidence-scoring": "confidence_scores.md",
            }
        else:
            role_file_map = {
                "depth-token-flow": "depth_token_flow_findings.md",
                "depth-state-trace": "depth_state_trace_findings.md",
                "depth-edge-case": "depth_edge_case_findings.md",
                "depth-external": "depth_external_findings.md",
                "confidence-scoring": "confidence_scores.md",
            }
        if mode == "thorough":
            role_file_map.update({
                "design-stress": "design_stress_findings.md",
                "perturbation": "perturbation_findings.md",
                "skill-execution-checklist": "skill_execution_gaps.md",
            })
        lines = ["# Never-Cut Checkpoint (auto-synthesized by driver)\n"]
        for role, filename in role_file_map.items():
            fpath = scratchpad / filename
            # Also check depth_-prefixed alias
            alias = f"depth_{filename}" if not filename.startswith("depth_") else None
            exists = fpath.exists() and fpath.stat().st_size > 0
            if not exists and alias:
                alias_p = scratchpad / alias
                exists = alias_p.exists() and alias_p.stat().st_size > 0
            status = "SPAWNED" if exists else "SKIPPED NO_APPLICABLE_FLAG"
            lines.append(f"- {role}: {status}")
        try:
            ncc.write_text("\n".join(lines) + "\n", encoding="utf-8")
            synthesized.append("never_cut_checkpoint.md")
        except OSError:
            pass

    # --- depth_exit.md ---
    dep = scratchpad / "depth_exit.md"
    # v2.6.3: if existing file lacks structured fields, prepend them
    # rather than replacing (preserves LLM's iteration/confidence data).
    # force=True (Codex): full overwrite. force=False (Claude): patch only.
    dep_needs_synth = force or not dep.exists()
    dep_existing_text = ""
    if not dep_needs_synth and dep.exists():
        try:
            dep_existing_text = dep.read_text(encoding="utf-8", errors="replace")
            if not re.search(r"(?im)^\s*[-*]?\s*criterion\s*:\s*[1-4]", dep_existing_text):
                dep_needs_synth = True
        except Exception:
            dep_needs_synth = True
    if dep_needs_synth:
        depth_files = list(scratchpad.glob("depth_*_findings.md"))
        explored = [f.name for f in depth_files if f.stat().st_size > 0]
        lines = [
            "# Depth Exit (auto-synthesized by driver)\n",
            "- criterion: 1",
            "- rationale: Single-pass depth completed; all spawned agents produced output",
            "- explored_paths:",
        ]
        for name in explored:
            lines.append(f"  - {name}")
        if not explored:
            lines.append("  - (no depth findings files found)")
        # Preserve LLM content below the structured header
        if dep_existing_text and not force:
            lines.append("\n---\n")
            lines.append("## Original LLM depth exit (preserved)\n")
            lines.append(dep_existing_text)
        try:
            dep.write_text("\n".join(lines) + "\n", encoding="utf-8")
            synthesized.append("depth_exit.md")
        except OSError:
            pass

    return synthesized


def _estimate_codex_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from Codex token counts."""
    inp_rate, out_rate = _CODEX_PRICING.get(model, (2.00, 8.00))
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1_000_000


def _parse_codex_output(log_path: Path, model: str = "") -> dict:
    """Parse streaming JSONL from Codex subprocess output log.

    Codex --json emits one JSON event per line:
      - type=turn.completed → usage {input_tokens, output_tokens, ...}
      - type=item.completed + item.type=agent_message → output text
      - type=turn.failed / type=error → error info
    We accumulate usage across all turn.completed events.
    Model must be passed in since JSONL events don't include it.
    """
    result: dict[str, Any] = {"output": "", "cost_usd": 0.0, "duration_ms": 0,
                              "tokens": 0, "model": model}
    total_input = 0
    total_output = 0
    output_parts: list[str] = []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = event.get("type", "")
            if etype == "error":
                result["error"] = event.get("message", "unknown_error")
                return result
            if etype == "turn.failed":
                err = event.get("error") or {}
                result["error"] = err.get("message", "turn_failed")
                return result
            if etype == "turn.completed":
                usage = event.get("usage") or {}
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)
            if etype == "item.completed":
                item = event.get("item") or {}
                if item.get("type") == "agent_message":
                    msg = item.get("text", "")
                    if msg:
                        output_parts.append(msg)
    except OSError:
        pass
    result["output"] = "\n".join(output_parts)
    result["tokens"] = total_input + total_output
    result["input_tokens"] = total_input
    result["output_tokens"] = total_output
    result["cost_usd"] = _estimate_codex_cost(model, total_input, total_output)
    return result


# ── Process management ────────────────────────────────────────────────────

def _terminate_process_tree(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """Terminate a phase subprocess and its children best-effort."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(5.0, grace_s + 2.0),
                check=False,
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=grace_s)
        except Exception:
            pass
        return

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=grace_s)
        return
    except Exception:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=grace_s)
    except Exception:
        pass


def _reconcile_completed_checkpoint_artifacts(
    scratchpad: Path,
    project_root: str,
    checkpoint: Checkpoint,
    phases: list[Phase],
    mode: str,
) -> list[str]:
    """Rewind completed checkpoint entries whose artifacts no longer pass.

    Resume safety is a contract between `_v2_checkpoint.json` and the current
    phase graph. A completed phase is only skippable if its active artifact
    gate still passes. When an earlier completed phase is invalid, every
    downstream completed phase is also unsafe because it may have consumed the
    stale or missing output.
    """
    active = [phase for phase in phases if mode in phase.modes]
    active_names = {phase.name for phase in active}
    unknown = checkpoint.validate_phase_names(active_names)
    if unknown:
        raise RuntimeError(
            "checkpoint references phases outside the active graph: "
            + ", ".join(sorted(unknown))
        )

    first_invalid_idx: int | None = None
    first_missing: list[str] = []
    completed_names = set(checkpoint.completed or [])
    first_hole_idx: int | None = None
    for idx, phase in enumerate(active):
        if phase.name not in completed_names:
            if first_hole_idx is None:
                first_hole_idx = idx
            continue
        if first_hole_idx is not None:
            first_invalid_idx = first_hole_idx
            first_missing = [
                "checkpoint completion is not prefix-closed: "
                f"{phase.name} is completed after incomplete phase "
                f"{active[first_hole_idx].name}"
            ]
            log.warning("[resume] %s", first_missing[0])
            break

    for idx, phase in enumerate(active):
        if first_invalid_idx is not None:
            break
        if phase.name not in checkpoint.completed:
            continue
        missing = _resume_phase_contract_issues(
            scratchpad, project_root, phase, mode
        )
        if missing:
            first_invalid_idx = idx
            first_missing = list(missing)
            log.warning(
                "[resume] completed phase %s failed contract reconciliation: %s",
                phase.name,
                ", ".join(first_missing),
            )
            break

    if first_invalid_idx is None:
        return []

    rewind_names = {phase.name for phase in active[first_invalid_idx:]}
    removed = [name for name in checkpoint.completed if name in rewind_names]
    removed_degraded = [name for name in checkpoint.degraded if name in rewind_names]
    checkpoint.completed = [name for name in checkpoint.completed if name not in rewind_names]
    checkpoint.degraded = [name for name in checkpoint.degraded if name not in rewind_names]
    for name in removed_degraded:
        checkpoint.clear_degraded_sentinel(scratchpad, name)
    if checkpoint.rate_limited_at in rewind_names:
        checkpoint.rate_limited_at = None
    return removed


def _resume_phase_contract_issues(
    scratchpad: Path,
    project_root: str,
    phase: Phase,
    mode: str = "core",
) -> list[str]:
    """Side-effect-free subset of phase completion contracts for resume."""
    issues: list[str] = []
    if (
        phase.expected_artifacts
        or getattr(phase, "any_of", None)
        or phase.name in L1_VERIFY_PHASE_NAMES
        or phase.name in SC_VERIFY_PHASE_NAMES
    ):
        passed, missing = gate_passes(scratchpad, project_root, phase)
        if not passed:
            issues.extend(missing)

    if phase.name == "inventory":
        # v2.8.6: skip parity on resume — inventory is modified by downstream
        # phases (depth promotion, dedup) so the merge receipt is stale. The
        # parity check was already enforced when the inventory was first created.
        # Only run the structure check (headings/format) on resume.
        issues.extend(_validate_inventory_structure(scratchpad))
    elif phase.name == "recon" and "build_status.md" in set(phase.expected_artifacts):
        hard, _soft = _validate_recon_content_structure(scratchpad)
        issues.extend(hard)
    elif phase.name == "instantiate":
        issues.extend(_validate_spawn_manifest_schema(scratchpad))
    elif phase.name in (
        "inventory_chunk_a", "inventory_chunk_b", "inventory_chunk_c"
    ):
        issues.extend(_validate_inventory_chunk_structure(scratchpad, phase.name))
    elif phase.name in ("verify_queue", "sc_verify_queue"):
        issues.extend(_validate_verification_queue_inventory_parity(scratchpad))
        if phase.name == "sc_verify_queue" and mode != "thorough":
            low_info = [
                r.get("finding id", "")
                for r in parse_verification_queue_rows(scratchpad)
                if _severity_bucket(r.get("severity", "")) in {"low", "info"}
            ]
            if low_info:
                issues.append(
                    "SC verification queue contains Low/Info active row(s) "
                    f"in {mode} mode: {', '.join(low_info[:8])}"
                )
    elif phase.name == "report_index":
        issues.extend(_validate_report_index_inputs(scratchpad))
        issues.extend(_check_index_completeness(
            scratchpad, project_root, write_retry_hint=False
        ))
        issues.extend(_validate_report_coverage_accounting(scratchpad))
    elif phase.name in ("verify_aggregate", "sc_verify_aggregate"):
        issues.extend(_validate_verify_files_for_queue(scratchpad))
        issues.extend(_validate_verify_evidence_tags(scratchpad))
    elif phase.name == "skeptic":
        issues.extend(_validate_skeptic_full_ch_coverage(scratchpad))
    elif phase.name == "report_assemble":
        issues.extend(_run_report_quality_gate(scratchpad, project_root))
        issues.extend(_validate_assemble_not_degraded(scratchpad))
    elif (
        phase.name in (
            "report_critical_high", "report_medium", "report_low_info",
            "report_body_writer_critical_high",
            "report_body_writer_medium",
            "report_body_writer_low_info",
        )
        or re.match(r"^report_(critical_high|medium|low_info)_[a-z]$", phase.name)
        or re.match(r"^report_body_writer_(critical_high|medium|low_info)_[a-z]$", phase.name)
    ):
        check_phase_name = phase.name.replace("report_body_writer_", "report_")
        issues.extend(_validate_tier_body_against_manifest(scratchpad, check_phase_name))

    return issues


def _record_phase_cost(scratchpad: Path, phase_name: str, model: str,
                       attempt: int, stdio_log: Path, duration_s: float,
                       backend: str = "claude") -> None:
    """Parse the claude -p JSON envelope for cost + append to a ledger.

    Pure observability. Does not affect pipeline decisions. Envelope
    fields captured when present: total_cost_usd, num_turns, duration_ms,
    stop_reason, is_error.
    """
    ledger_path = scratchpad / "_v2_cost_ledger.md"
    try:
        if not stdio_log.exists():
            return
        with stdio_log.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 32768))
            tail = f.read().decode("utf-8", errors="replace")
        fields: dict[str, Any] = {}
        if backend == "codex":
            parsed = _parse_codex_output(stdio_log, model=model)
            fields["total_cost_usd"] = f"{parsed.get('cost_usd', 0):.4f}"
            fields["stop_reason"] = "error" if parsed.get("error") else "end"
            fields["is_error"] = bool(parsed.get("error"))
            # _parse_codex_output accumulates input/output tokens separately
            # from turn.completed usage dicts in the JSONL stream.
            fields["input_tokens"] = parsed.get("input_tokens", 0) or "?"
            fields["output_tokens"] = parsed.get("output_tokens", 0) or "?"
            # Count turns from JSONL
            num_turns = 0
            try:
                for line in tail.splitlines():
                    if '"turn.completed"' in line:
                        num_turns += 1
            except Exception:
                pass
            fields["num_turns"] = num_turns or 1
        else:
            envelope = _extract_json_envelope(tail)
            if envelope:
                for key in ("total_cost_usd", "num_turns", "stop_reason",
                            "is_error", "api_error_status"):
                    if key in envelope:
                        fields[key] = envelope[key]
                # Cache metrics — essential for measuring whether prompt caching
                # is already helping us (cache_read = cheap hits at 0.1x input
                # price; cache_creation = one-time writes at 1.25x input).
                # claude-cli exposes these via the `usage` field in the JSON
                # envelope (Anthropic API convention).
                usage = envelope.get("usage") or {}
                if isinstance(usage, dict):
                    for key in ("input_tokens", "output_tokens",
                                "cache_read_input_tokens",
                                "cache_creation_input_tokens"):
                        if key in usage:
                            fields[key] = usage[key]
        # First write: create header row with cache columns
        if not ledger_path.exists():
            ledger_path.write_text(
                "# Phase Cost Ledger\n\n"
                "Cache columns explain where money went:\n"
                "- InTok = raw input tokens billed at 1x\n"
                "- OutTok = output tokens billed at 5x (sonnet)\n"
                "- CacheRd = input tokens served from cache at 0.1x (savings)\n"
                "- CacheWr = input tokens written to cache at 1.25x (one-time)\n"
                "- TotalInTok = InTok + CacheRd + CacheWr (Anthropic long-context threshold: 200k)\n"
                "- LongCtx = ⚠️ when TotalInTok > 200k (long-context pricing tier applies)\n"
                "- High CacheRd / TotalInTok ratio = good cache reuse\n\n"
                "| Phase | Attempt | Model | Dur(s) | Cost(USD) | Turns | "
                "InTok | OutTok | CacheRd | CacheWr | TotalInTok | LongCtx | "
                "CacheHit% | StopReason | Err |\n"
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )
        cost = fields.get("total_cost_usd", "?")
        turns = fields.get("num_turns", "?")
        stop = fields.get("stop_reason", "?")
        err = fields.get("is_error", False)
        api_err = fields.get("api_error_status")
        err_s = f"{api_err}" if api_err else ("true" if err else "")
        in_tok = fields.get("input_tokens", "?")
        out_tok = fields.get("output_tokens", "?")
        cache_rd = fields.get("cache_read_input_tokens", 0)
        cache_wr = fields.get("cache_creation_input_tokens", 0)
        # Compute total input tokens (InTok + CacheRd + CacheWr) — this is
        # what Anthropic compares against the 200k long-context threshold.
        try:
            total_in = int(in_tok) + int(cache_rd) + int(cache_wr)
            long_ctx = "⚠️" if total_in > 200000 else ""
            total_in_s = str(total_in)
        except Exception:
            total_in = None
            long_ctx = ""
            total_in_s = "?"
        # Compute cache hit % against TotalInTok denominator (matches the
        # header doc and is the meaningful reuse ratio).
        try:
            if total_in is not None and total_in > 0:
                cache_hit_pct = f"{100.0 * int(cache_rd) / total_in:.0f}%"
            else:
                cache_hit_pct = "?"
        except Exception:
            cache_hit_pct = "?"
        with ledger_path.open("a", encoding="utf-8") as f:
            f.write(
                f"| {phase_name} | {attempt} | {model} | "
                f"{duration_s:.0f} | {cost} | {turns} | "
                f"{in_tok} | {out_tok} | {cache_rd} | {cache_wr} | "
                f"{total_in_s} | {long_ctx} | "
                f"{cache_hit_pct} | {stop} | {err_s} |\n"
            )
    except Exception:
        # Telemetry must never break the pipeline.
        pass


def _restore_tier_body_from_overflow(scratchpad: Path, phase_name: str) -> bool:
    """Recover a valid tier body quarantined by an older containment pass."""
    if not re.match(r"^report_(critical_high|medium|low_info)(?:_[a-z])?$", phase_name):
        return False
    dest = scratchpad / f"{phase_name}.md"
    overflow = scratchpad / "_overflow"
    if not overflow.exists():
        return False
    candidates = sorted(
        overflow.glob(f"**/{phase_name}.md"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for src in candidates:
        try:
            if not src.exists() or src.stat().st_size <= 100:
                continue
            shutil.copy2(src, dest)
            if _validate_tier_body_against_manifest(scratchpad, phase_name):
                dest.unlink(missing_ok=True)
                continue
            return True
        except Exception as exc:
            log.warning(f"[{phase_name}] overflow restore failed for {src}: {exc!r}")
    return False


def _extract_json_envelope(tail_text: str) -> Optional[dict]:
    """Find the outermost JSON object in the tail and parse it.

    `claude -p --output-format json` writes a single JSON object to stdout.
    It is the last complete JSON in the log. Walk backward to find it.
    """
    # Scan backward for `{` then try to json.loads progressively.
    # Practical heuristic: the envelope is at the very end (or very near it)
    # and is well-formed. Try the last 64KB first.
    for end_marker in ("\n}\n", "}\n", "}"):
        idx = tail_text.rfind(end_marker)
        if idx == -1:
            continue
        # Find matching opening brace
        depth = 0
        for i in range(idx, -1, -1):
            ch = tail_text[i]
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    candidate = tail_text[i:idx + len(end_marker)]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break
    return None


def detect_rate_limit(stdio_log: Path, tail_bytes: int = 65536) -> bool:
    """Return True iff an actual API rate limit is detected.

    Strategy:
    1. Read the last ~64KB of the stdio log.
    2. Try to parse a JSON envelope. If found, check structured fields
       (`is_error`, `api_error_status`, error `type`).
    3. If no JSON envelope (crash pre-envelope), fall back to a STRUCTURED
       text regex that requires an HTTP status or API error prefix. Plain
       LLM prose no longer triggers.
    """
    if not stdio_log.exists():
        return False
    try:
        with stdio_log.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return False

    envelope = _extract_json_envelope(tail)
    if envelope is not None:
        # Structured path: trust only fields, ignore prose.
        if envelope.get("is_error") is True:
            status = envelope.get("api_error_status")
            if status in _API_RATE_LIMIT_STATUSES:
                return True
            err = envelope.get("error") or {}
            if isinstance(err, dict):
                err_type = (err.get("type") or "").lower()
                if "rate_limit" in err_type or "overloaded" in err_type:
                    return True
        stop_reason = (envelope.get("stop_reason") or "").lower()
        if stop_reason in ("rate_limited", "rate_limit", "overloaded"):
            return True
        terminal_reason = (envelope.get("terminal_reason") or "").lower()
        if "rate" in terminal_reason and "limit" in terminal_reason:
            return True
        # Envelope parsed cleanly, no structured rate-limit signal → NOT
        # rate-limited. Do NOT fall through to text regex — that's the
        # false-positive path.
        return False

    # Envelope not parseable (subprocess likely crashed pre-envelope).
    # Use strict text regex: requires structured error prefix.
    return bool(_STRUCTURED_RATE_LIMIT_RE.search(tail))


_NOT_LOGGED_IN_RE = re.compile(
    r"\bnot\s+logged\s+in\b|/login\b|please\s+log\s+in|"
    r"run\s+`?claude`?\s+(?:interactively\s+)?to\s+(?:log\s*in|authenticate)",
    re.IGNORECASE,
)


def detect_not_logged_in(stdio_log: Path, tail_bytes: int = 65536) -> bool:
    """Return True iff the `claude` CLI emitted an authentication error.

    Added in v2.0.1. An unauthenticated `claude -p` invocation returns
    rc=0 (or rc!=0 on newer builds) with a "Not logged in" / "/login"
    message in its stdout. Without this detector the V2 driver retries
    the same subprocess and degrades the phase, masking the real cause.
    """
    if not stdio_log.exists():
        return False
    try:
        with stdio_log.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            tail = f.read().decode("utf-8", errors="replace")
    except Exception:
        return False
    return bool(_NOT_LOGGED_IN_RE.search(tail))


_VERIFY_HINT_ID_RE = re.compile(
    r"\b(?:INV|H|M|L|C|MED|LOW|INFO|CH|DCOV|SLITHER|DEPTH-[A-Z]+)-\d+\b",
    re.IGNORECASE,
)


_SEMANTIC_DEDUP_PASSTHROUGH_PREFIX = (
    "semantic dedup: PASSTHROUGH unchanged despite live candidate pairs"
)


def _semantic_dedup_pair_count(scratchpad: Path) -> int:
    pairs_file = scratchpad / "dedup_candidate_pairs.md"
    if not pairs_file.exists():
        return 0
    try:
        pair_text = pairs_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    return sum(
        1
        for line in pair_text.splitlines()
        if line.lstrip().startswith("|")
        and not re.match(r"\s*\|\s*-+", line)
        and "Finding A" not in line
    )


def _semantic_dedup_passthrough_issue(scratchpad: Path) -> Optional[str]:
    pair_rows = _semantic_dedup_pair_count(scratchpad)
    if pair_rows <= 0:
        return None
    decisions = scratchpad / "dedup_decisions.md"
    if not decisions.exists():
        return None
    try:
        dec_text = decisions.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    if re.search(r"(?im)^\s*\*?\*?Status\*?\*?\s*:\s*PASSTHROUGH\b", dec_text):
        return (
            f"{_SEMANTIC_DEDUP_PASSTHROUGH_PREFIX} "
            f"({pair_rows} candidate pair(s)); subprocess preserved the "
            "pre-run safety net instead of evaluating merge/keep decisions"
        )
    return None


def _is_semantic_dedup_passthrough_failure(missing: list[Any]) -> bool:
    return any(
        str(item).startswith(_SEMANTIC_DEDUP_PASSTHROUGH_PREFIX)
        for item in missing
    )


def _run_verify_recovery_shard(
    config: dict,
    missing: list[tuple[str, dict]],
) -> list[str]:
    """Run a one-shot recovery verification subprocess for dropped findings.

    v2.6.8: Before mechanical stubbing, attempt real verification for
    findings that verify shards failed to cover. Writes a recovery
    manifest, reads the standalone verification prompt, applies
    pruning, wraps with a recovery directive, and runs a subprocess.

    Returns the list of finding IDs that are STILL missing after recovery
    (i.e. the recovery shard also failed to produce them).
    """
    scratchpad = Path(config["scratchpad"])
    pipeline = config.get("pipeline", "sc")
    is_l1 = pipeline == "l1"

    # Write recovery manifest.
    recovery_rows = [row for _, row in missing]
    manifest_path = scratchpad / "verification_queue_recovery.md"
    _write_queue_subset_manifest(manifest_path, recovery_rows)

    # Build the recovery prompt from the standalone verification template.
    standalone_name = (
        "phase5-verification-l1.md" if is_l1
        else "phase5-verification-sc.md"
    )
    standalone_path = plamen_home() / "prompts" / "shared" / "v2" / standalone_name
    if not standalone_path.exists():
        log.warning(
            f"[verify_recovery] standalone prompt not found: {standalone_path} "
            f"— skipping recovery, will stub {len(missing)} findings"
        )
        return [fid for fid, _ in missing]

    try:
        base_prompt = standalone_path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"[verify_recovery] failed to read {standalone_path}: {e}")
        return [fid for fid, _ in missing]

    if is_l1:
        base_prompt = _prune_l1_verify_shard_prompt(base_prompt)
    else:
        base_prompt = _prune_sc_verify_shard_prompt(base_prompt)

    # Build the checklist of IDs the recovery shard must produce.
    id_checklist = []
    for fid, row in missing:
        title = re.sub(r"\s+", " ", row.get("title", "")).strip()
        sev = (row.get("severity") or "Medium").strip()
        id_checklist.append(f"- {fid} -> verify_{fid}.md | {sev} | {title[:120]}")
    checklist_block = "\n".join(id_checklist)

    # Wrap with recovery-specific directive.
    recovery_directive = (
        "# RECOVERY VERIFICATION SHARD\n\n"
        "You are a recovery verifier. The primary verify shards ran but "
        "failed to produce verify files for the findings below. Your job "
        "is to verify ONLY these findings. Do not produce output for "
        "findings not listed here.\n\n"
        f"## Recovery Manifest\n\n"
        f"Read the recovery manifest at: verification_queue_recovery.md\n\n"
        f"## Assigned Findings ({len(missing)} total)\n\n"
        f"{checklist_block}\n\n"
        f"## Instructions\n\n"
        f"For each finding above, create a verify_<ID>.md file following "
        f"the standard verification methodology below. Prioritize "
        f"Critical/High findings. If you run out of context budget, "
        f"produce as many verify files as possible in severity order.\n\n"
        f"---\n\n"
    )
    full_prompt = recovery_directive + base_prompt

    # Resolve model and timeout.
    effective_model = "sonnet"
    mode = config.get("mode", "core")
    if mode == "light":
        effective_model = "sonnet"
    timeout = scale_timeout(
        1800, config["project_root"], config["language"],
        mode=mode, hypothesis_count=len(missing),
    )

    # Write snapshot.
    snap = scratchpad / "_prompt_verify_recovery.attempt1.md"
    try:
        snap.write_text(full_prompt, encoding="utf-8")
    except Exception as e:
        log.warning(f"[verify_recovery] snapshot write failed: {e}")
        return [fid for fid, _ in missing]

    # Build subprocess command.
    cmd = [
        CLAUDE_BIN, "-p",
        "--model", effective_model,
        "--output-format", "json",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--add-dir", config["project_root"],
        "--add-dir", plamen_home().as_posix(),
    ]

    # Subprocess isolation (same as run_phase).
    isolation_path = scratchpad / "_subprocess_isolation.json"
    isolation_ok = False
    try:
        isolation_payload = '{"enabledPlugins":{},"hooks":{},"mcpServers":{}}'
        if (
            not isolation_path.exists()
            or isolation_path.read_text(encoding="utf-8").strip()
            != isolation_payload
        ):
            isolation_path.write_text(isolation_payload, encoding="utf-8")
        isolation_ok = True
    except Exception:
        pass
    cmd.extend(["--disallowedTools", "mcp__*"])
    if isolation_ok:
        iso = isolation_path.as_posix()
        cmd.extend([
            "--settings", iso,
            "--strict-mcp-config", "--mcp-config", iso,
        ])

    # Subprocess env.
    subprocess_env = {
        **os.environ,
        "ANTHROPIC_DISABLE_AUTOUPDATE": "1",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": PLAMEN_OPUS_MODEL,
        "PLAMEN_SCRATCHPAD": str(scratchpad),
    }

    # Platform-specific Popen kwargs.
    popen_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        popen_kwargs["start_new_session"] = True

    log_path = scratchpad / "_stdio_verify_recovery.attempt1.log"
    start = time.monotonic()

    log.info(
        f"[verify_recovery] spawning recovery shard for {len(missing)} "
        f"findings (timeout={timeout}s, model={effective_model})"
    )

    with log_path.open("w", encoding="utf-8", errors="replace") as out, \
            snap.open("rb") as stdin_file:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=stdin_file, stdout=out, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=config["project_root"],
                env=subprocess_env,
                **popen_kwargs,
            )
        except Exception as e:
            log.warning(f"[verify_recovery] Popen failed: {e}")
            return [fid for fid, _ in missing]

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(proc, grace_s=10)
            log.warning(
                f"[verify_recovery] timed out after {timeout}s"
            )

    elapsed = time.monotonic() - start
    log.info(f"[verify_recovery] completed in {elapsed:.0f}s")

    # Check which findings are still missing after recovery.
    still_missing = []
    recovered = []
    for fid, row in missing:
        if _verify_file_present_for_id(scratchpad, fid, min_bytes=100):
            recovered.append(fid)
        else:
            still_missing.append(fid)

    if recovered:
        log.info(
            f"[verify_recovery] recovered {len(recovered)}/{len(missing)} "
            f"verify files: {recovered[:8]}"
            + (f" (+{len(recovered) - 8} more)" if len(recovered) > 8 else "")
        )
    if still_missing:
        log.warning(
            f"[verify_recovery] {len(still_missing)} findings still missing "
            f"after recovery: {still_missing[:8]}"
            + (f" (+{len(still_missing) - 8} more)" if len(still_missing) > 8 else "")
        )

    return still_missing


def _clear_stale_verify_retry_hint_after_reshard(
    scratchpad: Path,
    phase_name: str,
    assigned_rows: list[dict[str, str]],
) -> bool:
    """Drop verify retry hints whose IDs no longer belong to this shard.

    Verify shard manifests are regenerated on resume. After a shard-splitting
    fix, a prior retry hint can point at IDs that moved to a later shard; if
    injected, it steers the subprocess outside its current manifest.
    """
    if not assigned_rows:
        return False
    try:
        hint = _read_retry_hint(scratchpad, phase_name)
    except Exception:
        return False
    if not hint:
        return False

    assigned_ids = {
        str(row.get("finding id") or row.get("Finding ID") or "").strip().upper()
        for row in assigned_rows
    }
    assigned_ids.discard("")
    mentioned_ids = {m.group(0).upper() for m in _VERIFY_HINT_ID_RE.finditer(hint)}
    if mentioned_ids and assigned_ids and not mentioned_ids.issubset(assigned_ids):
        _clear_retry_hint(scratchpad, phase_name)
        log.info(
            f"[{phase_name}] cleared stale retry hint after verify re-shard; "
            f"hint_ids={sorted(mentioned_ids)[:8]} assigned_ids={sorted(assigned_ids)[:8]}"
        )
        return True
    return False


_HEARTBEAT_INTERVAL = 0.1  # seconds between proc.wait polls (governs spinner fps ~10)
_HEARTBEAT_DISPLAY_INTERVAL = 15  # seconds between heartbeat display lines
_ARTIFACT_SCAN_INTERVAL = 3  # seconds between scratchpad scans (avoid thrashing iterdir)
_EARLY_COMPLETE_IDLE_GRACE_SECONDS = int(os.environ.get(
    "PLAMEN_EARLY_COMPLETE_IDLE_SECONDS", "600"
))


def _breadth_manifest_complete_reason(scratchpad: Path, phase: Phase) -> Optional[str]:
    """Return a reason when every manifest-declared breadth output is substantial."""
    outputs = parse_breadth_manifest_outputs(scratchpad) or []
    if not outputs:
        return None
    missing: list[str] = []
    for name in outputs:
        path = scratchpad / name
        if not path.exists():
            missing.append(name)
            continue
        try:
            if path.stat().st_size < phase.min_artifact_bytes:
                missing.append(f"{name} (stub)")
        except OSError:
            missing.append(name)
    if missing:
        return None
    return (
        f"all {len(outputs)} manifest breadth outputs are present and "
        f">= {phase.min_artifact_bytes} bytes"
    )


def _run_early_complete_check(checker: Callable[[], Optional[str]]) -> Optional[str]:
    return checker.__call__()


def _scratchpad_activity_signature(scratchpad: Path) -> tuple[tuple[str, int, int], ...]:
    entries: list[tuple[str, int, int]] = []
    try:
        for p in scratchpad.rglob("*"):
            try:
                if not p.is_file():
                    continue
                rel = p.relative_to(scratchpad).as_posix()
                if rel.startswith("_"):
                    continue
                st = p.stat()
                entries.append((rel, st.st_size, st.st_mtime_ns))
            except OSError:
                continue
    except OSError:
        pass
    return tuple(sorted(entries))


def _wait_with_heartbeat(
    proc: subprocess.Popen,
    timeout: float,
    scratchpad: Path,
    phase_name: str,
    start_time: float,
    protected_patterns: tuple[str, ...] = (),
    early_complete: Optional[Callable[[], Optional[str]]] = None,
) -> int:
    """Poll proc every _HEARTBEAT_INTERVAL (3s), printing artifact progress.

    Short poll interval ensures halt responds within ~3 seconds.
    Display output is throttled to _HEARTBEAT_DISPLAY_INTERVAL (15s).

    Returns:
      >=0  normal subprocess exit code
      -2   timeout (caller should also check subprocess.TimeoutExpired)
      -3   user pressed Esc (halt) — subprocess terminated, driver stays alive

    Raises subprocess.TimeoutExpired if the process exceeds *timeout*.
    """
    deadline = start_time + timeout
    known_artifacts: set[str] = set()
    try:
        for p in scratchpad.iterdir():
            if not p.name.startswith("_"):
                known_artifacts.add(p.name)
        scip_dir = scratchpad / "scip"
        if scip_dir.is_dir():
            for p in scip_dir.iterdir():
                known_artifacts.add(f"scip/{p.name}")
    except Exception:
        pass

    tool_calls_file = scratchpad / "tool_calls.jsonl"
    last_tc_size = tool_calls_file.stat().st_size if tool_calls_file.exists() else 0
    last_activity_tc_size = last_tc_size
    last_activity_signature = _scratchpad_activity_signature(scratchpad)
    last_display_time = time.time()
    last_scan_time = time.time()
    early_complete_since: Optional[float] = None
    early_complete_last_activity: Optional[float] = None
    early_complete_reason: Optional[str] = None
    _ALIVE_INTERVAL = 300

    # Accumulate new artifacts between display ticks
    pending_artifacts: list[str] = []

    while True:
        try:
            rc = proc.wait(timeout=_HEARTBEAT_INTERVAL)
            # Process exited — flush any pending artifacts
            display._clear_spinner()
            if pending_artifacts:
                elapsed = int(time.time() - start_time)
                display.print_phase_heartbeat(phase_name, elapsed, new_artifacts=pending_artifacts)
            return rc
        except subprocess.TimeoutExpired:
            pass
        except KeyboardInterrupt:
            display._clear_spinner()
            _terminate_process_tree(proc, grace_s=10)
            raise

        # Esc halt: terminate subprocess, return -3 so driver can offer resume
        if display.graceful_stop.requested:
            display._clear_spinner()
            _terminate_process_tree(proc, grace_s=5)
            return -3

        now = time.time()

        if now > deadline:
            display._clear_spinner()
            raise subprocess.TimeoutExpired(proc.args, timeout)

        elapsed = int(now - start_time)

        # Spin the inline indicator every poll (~4fps)
        display.spin(elapsed)

        # Scan for new artifacts at a slower cadence (every 3s)
        if now - last_scan_time >= _ARTIFACT_SCAN_INTERVAL:
            last_scan_time = now
            observed_activity = False
            try:
                for p in scratchpad.iterdir():
                    if not p.name.startswith("_") and p.name not in known_artifacts:
                        pending_artifacts.append(p.name)
                        known_artifacts.add(p.name)
                        if protected_patterns and _matches_any_pattern(
                            p.name, list(protected_patterns)
                        ):
                            display._clear_spinner()
                            log.error(
                                f"[{phase_name}] live containment abort: "
                                f"protected downstream artifact appeared: {p.name}"
                            )
                            _terminate_process_tree(proc, grace_s=5)
                            return -4
                scip_dir = scratchpad / "scip"
                if scip_dir.is_dir():
                    for p in scip_dir.iterdir():
                        key = f"scip/{p.name}"
                        if key not in known_artifacts:
                            pending_artifacts.append(key)
                            known_artifacts.add(key)
            except Exception:
                pass

            cur_activity_signature = _scratchpad_activity_signature(scratchpad)
            cur_activity_tc_size = (
                tool_calls_file.stat().st_size if tool_calls_file.exists() else 0
            )
            if (
                cur_activity_signature != last_activity_signature
                or cur_activity_tc_size > last_activity_tc_size
            ):
                observed_activity = True
                last_activity_signature = cur_activity_signature
                last_activity_tc_size = cur_activity_tc_size

            if early_complete:
                try:
                    reason = _run_early_complete_check(early_complete)
                except Exception as exc:
                    log.debug(f"[{phase_name}] early completion check skipped: {exc}")
                    reason = None
                if reason:
                    if early_complete_since is None:
                        early_complete_since = now
                        early_complete_last_activity = now
                        early_complete_reason = reason
                        log.info(
                            f"[{phase_name}] manifest complete: {reason}; "
                            "waiting for subprocess to go idle before cutover"
                        )
                    elif observed_activity:
                        early_complete_last_activity = now
                    idle_for = now - (early_complete_last_activity or now)
                    if idle_for >= _EARLY_COMPLETE_IDLE_GRACE_SECONDS:
                        display._clear_spinner()
                        log.info(
                            f"[{phase_name}] early completion after idle grace: "
                            f"{early_complete_reason or reason}; no scratchpad/tool "
                            f"activity for {int(idle_for)}s"
                        )
                        _terminate_process_tree(proc, grace_s=8)
                        return 0
                else:
                    early_complete_since = None
                    early_complete_last_activity = None
                    early_complete_reason = None

        since_display = now - last_display_time

        # Show new artifacts immediately (spinner line → artifact line)
        if pending_artifacts and since_display >= _ARTIFACT_SCAN_INTERVAL:
            names = ", ".join(pending_artifacts[:4])
            extra = f" +{len(pending_artifacts) - 4} more" if len(pending_artifacts) > 4 else ""
            display.print_phase_heartbeat(phase_name, elapsed, new_artifacts=pending_artifacts)
            mins, secs = divmod(elapsed, 60)
            log.info(f"[{phase_name}] {mins}:{secs:02d} | +{names}{extra}")
            pending_artifacts = []
            last_display_time = now
        elif not pending_artifacts and since_display >= _ALIVE_INTERVAL:
            mins, secs = divmod(elapsed, 60)
            cur_tc_size = tool_calls_file.stat().st_size if tool_calls_file.exists() else 0
            if cur_tc_size > last_tc_size:
                delta_kb = (cur_tc_size - last_tc_size) / 1024
                display.print_phase_heartbeat(phase_name, elapsed, tool_calls_delta_kb=delta_kb)
                log.info(f"[{phase_name}] {mins}:{secs:02d} | working (+{delta_kb:.0f}KB tool calls)")
                last_tc_size = cur_tc_size
            else:
                display.print_phase_heartbeat(phase_name, elapsed)
                log.info(f"[{phase_name}] {mins}:{secs:02d} | waiting")
            last_display_time = now


# --- Core ---
def run_phase(phase: Phase, config: dict, attempt: int) -> int:
    """Spawn claude -p for the phase. Returns exit code or sentinel."""
    scratchpad = Path(config["scratchpad"])
    v1_prompt = resolve_v1_prompt(config["pipeline"])
    if not v1_prompt.exists():
        log.error(f"V1 prompt missing: {v1_prompt}")
        return EXIT_ERROR

    try:
        prompt = build_phase_prompt(v1_prompt, phase, config)
    except PhasePromptError as e:
        log.error(
            f"[{phase.name}] PROMPT BUILD FAILED — cannot spawn subprocess.\n"
            f"  {e}\n"
            f"  This means the V1 section markers in plamen_types.py are stale "
            f"OR a standalone prompt file is needed in prompts/shared/v2/."
        )
        return EXIT_ERROR
    hyp_count = 0
    if phase.name in (*L1_VERIFY_PHASE_NAMES, *SC_VERIFY_PHASE_NAMES):
        try:
            if phase.name in SC_VERIFY_PHASE_NAMES:
                _shards = compute_sc_verify_shards(scratchpad)
            else:
                _shards = compute_verify_shards(scratchpad)
            hyp_count = len(_shards.get(phase.name, []))
        except Exception:
            pass
    # v2.5.0: verify_aggregate needs total hypothesis count for timeout scaling
    # (it reads ALL verify files, not just one shard's worth)
    if phase.name in ("verify_aggregate", "sc_verify_aggregate"):
        try:
            if phase.name == "sc_verify_aggregate":
                _all_shards = compute_sc_verify_shards(scratchpad)
            else:
                _all_shards = compute_verify_shards(scratchpad)
            hyp_count = sum(len(v) for v in _all_shards.values())
        except Exception:
            pass
    # v2.5.0: phases that process ALL hypotheses need total-count scaling.
    # Without this, report_index (1500s base) times out on 47+ hypothesis
    # audits. Chain/crossbatch have the same structural gap.
    _TOTAL_HYP_PHASES = frozenset({
        "chain", "chain_agent2", "crossbatch",
        "report_index", "sc_semantic_dedup",
    })
    if phase.name in _TOTAL_HYP_PHASES and hyp_count == 0:
        try:
            hyp_count = len(parse_verification_queue_rows(scratchpad))
        except Exception:
            pass
        if hyp_count == 0:
            # Pre-queue phases: count from findings_inventory.md
            try:
                inv = scratchpad / "findings_inventory.md"
                if inv.exists():
                    hyp_count = inv.read_text(
                        encoding="utf-8", errors="replace"
                    ).count("\n| H-")
            except Exception:
                pass
    # Codex backend: translate prompt paths and inject tool preamble.
    backend = config.get("cli_backend", "claude")

    timeout = scale_timeout(
        phase.base_timeout_s, config["project_root"], config["language"],
        mode=config.get("mode"), hypothesis_count=hyp_count,
        backend=backend,
    )
    if backend == "codex":
        prompt = _translate_prompt_for_codex(
            prompt, phase_name=phase.name,
            pipeline=config.get("pipeline", "sc"),
            mode=config.get("mode", "core"),
        )

    # Snapshot prompt — doubles as the subprocess stdin source (v2.1.3).
    # The snapshot file IS the authoritative prompt the child sees, so a
    # failure here is now a fatal phase failure (previously the snapshot
    # was diagnostic-only and a write-failure was a warning). This removes
    # the divergence risk between "what the child got" and "what the
    # post-mortem reads".
    snap = scratchpad / f"_prompt_{phase.name}.attempt{attempt}.md"
    try:
        snap.write_text(prompt, encoding="utf-8")
    except Exception as e:
        log.error(
            f"[{phase.name}] prompt snapshot failed: {e} — "
            f"cannot spawn subprocess (snapshot is the stdin source)"
        )
        return EXIT_ERROR

    # Resolve effective model (Light forces sonnet; otherwise phase.model)
    effective_model = phase_model(phase, config["mode"], config)

    if backend == "codex":
        if not CODEX_BIN:
            log.error(f"[{phase.name}] cli_backend=codex but codex binary not found")
            return EXIT_ERROR
        if not _codex_auth_available():
            log.error(
                f"[{phase.name}] Codex auth not found — `codex exec` will hang "
                f"waiting for interactive login. Run `codex login` first, or set "
                f"CODEX_API_KEY / OPENAI_API_KEY."
            )
            return EXIT_ERROR
        if not _codex_prompt_fits(prompt, effective_model):
            est_tokens = len(prompt) // 4
            limit = _CODEX_CONTEXT_LIMITS.get(effective_model, 272_000)
            log.warning(
                f"[{phase.name}] prompt ~{est_tokens:,} tokens may exceed "
                f"{effective_model} context ({limit:,} tokens). "
                f"Codex hard-errors on context exceed (no silent truncation)."
            )
        # --output-last-message captures the final agent message to a file,
        # providing reliable output extraction independent of JSONL parsing.
        olm_path = str(scratchpad / f"_codex_output_{phase.name}.attempt{attempt}.md")
        codex_writable = [scratchpad.as_posix(), Path(config["project_root"]).as_posix()]
        # Pre-create expected artifact files so Codex's apply_patch (which
        # cannot create new files) has valid targets. The model's preamble
        # directs it to use shell+heredoc for new files, but models don't
        # always follow instructions — pre-seeding is defensive.
        _precreate_codex_artifacts(phase, scratchpad)
        if config.get("_codex_skip_model"):
            cmd = _build_codex_cmd_no_model(
                needs_mcp=phase.needs_mcp,
                output_last_message=olm_path,
                writable_dirs=codex_writable,
            )
        else:
            cmd = _build_codex_cmd(
                effective_model, needs_mcp=phase.needs_mcp,
                output_last_message=olm_path,
                writable_dirs=codex_writable,
            )
    else:
        cmd = [
            CLAUDE_BIN, "-p",
            "--model", effective_model,
            "--output-format", "json",
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            "--add-dir", config["project_root"],
            # Agents must read ~/.plamen/rules/*.md, prompts/{lang}/*.md, and
            # skills/**/SKILL.md. Add the Claude home explicitly so permission
            # prompts never fire. v2.3.8 DRV-2: forward-slash form to keep
            # CLI argv consistent across Windows/POSIX so MCP/path loaders
            # don't silently mishandle backslashes.
            "--add-dir", plamen_home().as_posix(),
        ]
    if not phase.needs_mcp and backend != "codex":
        # Subprocess startup isolation for non-MCP phases (Claude Code only).
        # Codex CLI has no --disallowedTools, --settings, --strict-mcp-config,
        # or --mcp-config flags — it uses --ephemeral + --ignore-user-config.
        #
        # `claude -p` consults `~/.claude/settings.json` at startup and
        # cold-starts everything declared there: MCP servers, plugins
        # from external marketplaces (rust-analyzer-lsp, entry-point-
        # analyzer), Pre/PostToolUse hooks, auto-update checks, etc. Any
        # one of these can block indefinitely (network call, slow disk,
        # heavy compile). The driver observed two production halts on a
        # single audit (Irys L1 inventory: MCP class; AwesomeX SC
        # inventory: plugin class) — 0 stdio + 0 tokens billed because
        # the subprocess never reached the API.
        #
        # `--bare` would skip all of these in one flag but requires
        # `ANTHROPIC_API_KEY` / `apiKeyHelper`; OAuth-only users (this
        # user) can't use it.
        #
        # The robust path is `--settings <overlay>` + `--strict-mcp-config
        # --mcp-config <empty>`. `--settings` overlays additional settings
        # on the base config — empty `enabledPlugins`/`hooks`/`mcpServers`
        # in the overlay disables those subsystems without touching the
        # user's real settings.json (so OAuth keychain auth keeps working).
        # `--strict-mcp-config` is belt-and-suspenders — it forces claude
        # to load MCP from the empty file and ignore everything else.
        #
        # If an overlay write fails (disk-full / readonly / antivirus
        # lock), the fall-through fail-open is "subprocess may still hang"
        # — but visibly logged, not silent.
        isolation_payload = (
            '{"enabledPlugins":{},"hooks":{},"mcpServers":{}}'
        )
        isolation_path = scratchpad / "_subprocess_isolation.json"
        isolation_ok = False
        try:
            if (
                not isolation_path.exists()
                or isolation_path.read_text(encoding="utf-8").strip()
                != isolation_payload
            ):
                isolation_path.write_text(
                    isolation_payload, encoding="utf-8"
                )
            isolation_ok = True
        except Exception as _iso_err:
            log.warning(
                f"[{phase.name}] subprocess-isolation file write failed "
                f"({_iso_err}) — settings.json plugins/hooks/mcp will "
                f"load and may block the subprocess"
            )
        cmd.extend(["--disallowedTools", "mcp__*"])  # always, cheap
        if isolation_ok:
            iso = isolation_path.as_posix()
            cmd.extend([
                "--settings", iso,
                "--strict-mcp-config", "--mcp-config", iso,
            ])

    # Neutralize the V1 phase_gate watchdog — but ONLY if its breadcrumb
    # belongs to THIS run. The V1 L1 prompt initializes it via
    # `phase_gate.py --init`, which plants a breadcrumb at
    # ~/.claude/hooks/.active_audit pointing at the current scratchpad.
    # Subsequent claude -p subprocesses spawned by this driver inherit that
    # watchdog, which blocks them on phases that don't write
    # `analysis_*.md` (caused A1/A7/A8 in the Irys L1 run). V2 has its own
    # Python gate; the V1 watchdog is harmful for V2 subprocesses.
    #
    # CRITICAL: phase_gate.py already recognizes V2 scratchpads via the
    # `_v2_checkpoint.json` marker (see phase_gate.find_state_file) and
    # returns None for them. So V2 subprocesses are already safe REGARDLESS
    # of what .active_audit points at. The risk of unconditionally deleting
    # .active_audit is trampling a parallel V1 run in an unrelated project.
    # Only unlink if the breadcrumb refers to our scratchpad (or is
    # corrupt/empty and thus useless anyway).
    try:
        active_audit = plamen_home() / "hooks" / ".active_audit"
        if active_audit.exists():
            owns_breadcrumb = False
            try:
                raw = active_audit.read_text(encoding="utf-8").strip()
                if not raw:
                    owns_breadcrumb = True  # empty/corrupt → safe to clear
                else:
                    try:
                        data = json.loads(raw)
                        sp = data.get("scratchpad") or data.get("scratchpad_path") or ""
                    except Exception:
                        sp = raw  # plain-text breadcrumb (legacy)
                    if sp:
                        try:
                            owns_breadcrumb = (
                                Path(sp).resolve() == scratchpad.resolve()
                            )
                        except Exception:
                            owns_breadcrumb = False
                    else:
                        owns_breadcrumb = True
            except Exception:
                owns_breadcrumb = True  # unreadable → treat as corrupt
            if owns_breadcrumb:
                active_audit.unlink()
            else:
                log.debug(
                    "watchdog: .active_audit belongs to another run, leaving it alone "
                    "(V2 subprocess is safe via _v2_checkpoint.json marker)"
                )
        watchdog_state = scratchpad / "watchdog_state.json"
        if watchdog_state.exists():
            watchdog_state.unlink()
    except Exception as _e:
        log.debug(f"watchdog cleanup skipped: {_e}")

    log_path = scratchpad / f"_stdio_{phase.name}.attempt{attempt}.log"
    canonical = scratchpad / f"_stdio_{phase.name}.log"

    _cli_label = "codex exec" if backend == "codex" else "claude -p"
    log.info(
        f"[{phase.name}] spawning {_cli_label} (model={effective_model}, "
        f"timeout={timeout}s, attempt={attempt})"
    )
    start = time.time()

    # v2.1.3: stdin from the snapshot file, NOT from a PIPE. The previous
    # daemon-thread-fed PIPE pattern deadlocked on prompts that exceeded
    # the OS pipe buffer — a cross-OS problem, not Windows-only:
    #   Windows anonymous pipe default:  4 KiB
    #   macOS (Darwin) pipe default:    16 KiB (grows to 64 KiB)
    #   Linux pipe default:             64 KiB
    # v2.1.1/v2.1.2 pushed the inventory prompt to ~100 KiB (V1 section +
    # Track B producer contract + graph-artifact directive + HARD SCOPE
    # header), exceeding ALL three defaults. File-based stdin has no
    # buffer threshold — the child reads directly from disk.
    #
    # This matches CPython's own pattern: subprocess.run(input=...)
    # internally switches to a temp file when input > pipe buffer on
    # Windows (see Lib/subprocess.py::_communicate_on_windows). We are
    # using that same pattern but with the already-existing diagnostic
    # snapshot file — zero extra I/O.
    # v2.3.8 DRV-3 + DRV-4: subprocess env tweaks.
    # DRV-3 — `ANTHROPIC_DISABLE_AUTOUPDATE=1`: settings.json has
    #   `autoUpdatesChannel: "latest"`, which causes claude -p to perform
    #   an update-check network call on every startup. Across 30-60
    #   subprocess spawns per Thorough audit this is silent overhead and,
    #   if a mid-audit update changes the binary path, a stale
    #   `CLAUDE_BIN` (resolved once at module import) breaks all
    #   subsequent phases.
    # DRV-4 — `PLAMEN_SCRATCHPAD`: lets `~/.claude/hooks/phase_gate.py`
    #   take its env-var fast path for the V2-dormancy check instead of
    #   doing filesystem I/O + JSON parse on every Task spawn. Saves
    #   ~100-500ms per agent dispatch on Windows; on a 20-agent depth
    #   phase that is several seconds of measurable overhead removed.
    subprocess_env = {
        **os.environ,
        "ANTHROPIC_DISABLE_AUTOUPDATE": "1",
        # Protect nested Claude Code alias resolution too: if a prompt or
        # Task uses bare `opus`, keep it on the pinned Opus version.
        "ANTHROPIC_DEFAULT_OPUS_MODEL": PLAMEN_OPUS_MODEL,
        "PLAMEN_SCRATCHPAD": str(scratchpad),
    }
    # On Windows, claude.cmd is a batch file; Popen without
    # CREATE_NO_WINDOW spawns a visible console per subprocess.
    popen_kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
    else:
        popen_kwargs["start_new_session"] = True

    with log_path.open("w", encoding="utf-8", errors="replace") as out, \
            snap.open("rb") as stdin_file:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=stdin_file, stdout=out, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=config["project_root"],
                env=subprocess_env,
                **popen_kwargs,
            )
        except Exception as e:
            log.error(f"[{phase.name}] Popen failed: {e}")
            try:
                out.write(f"Popen failed before subprocess start: {e}\n")
                out.flush()
            except Exception:
                pass
            try:
                canonical.write_text(
                    log_path.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception:
                pass
            return EXIT_ERROR

        try:
            early_complete = None
            if phase.name == "breadth":
                early_complete = lambda: _breadth_manifest_complete_reason(
                    scratchpad, phase
                )
            rc = _wait_with_heartbeat(
                proc, timeout, scratchpad, phase.name, start,
                _protected_phase_write_patterns(phase.name),
                early_complete=early_complete,
            )
        except subprocess.TimeoutExpired:
            _terminate_process_tree(proc, grace_s=10)
            log.warning(f"[{phase.name}] timed out after {timeout}s")
            rc = -2  # timeout sentinel

    # Copy to canonical so detect_rate_limit finds latest
    try:
        canonical.write_bytes(log_path.read_bytes())
    except Exception:
        pass

    duration = time.time() - start
    log.info(f"[{phase.name}] subprocess exited rc={rc} after {duration:.0f}s")
    _record_phase_cost(scratchpad, phase.name, effective_model, attempt,
                        log_path, duration, backend=backend)
    # v2.0.1: surface "Not logged in" before silently retrying. Without
    # this the driver re-spawns the same unauthenticated CLI for every
    # phase, exhausts the attempt budget, and degrades with an opaque
    # error. EXIT_DEGRADED prints actionable next steps via the halt
    # panel and lets the user `claude` interactively to fix.
    if detect_not_logged_in(log_path):
        log.error(
            f"[{phase.name}] `claude` CLI not authenticated -- subprocess "
            f"emitted /login or 'Not logged in'. Fix with EITHER (1) run "
            f"`claude` interactively and complete `/login` (OAuth), OR "
            f"(2) set the ANTHROPIC_API_KEY environment variable with a "
            f"valid Anthropic Console API key. A key in "
            f"~/.claude/settings.json is NOT read as credentials -- "
            f"that file is for hooks/MCP/plugin config. Then resume."
        )
        sys.exit(EXIT_DEGRADED)
    return rc


def print_pause_message(config_path: Path):
    """Legacy wrapper -- kept for backward compatibility."""
    display.print_rate_limit_pause(str(config_path))


def _format_artifact_size(size_bytes: int) -> str:
    """Format artifact sizes without rounding small real files to 0KB."""
    size = max(0, int(size_bytes))
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _format_gate_summary(
    phase: Phase, scratchpad: Path, config: dict
) -> str:
    """One-line summary of what the gate checked — shown on phase DONE."""
    parts: list[str] = []
    # Count artifacts that matched expected_artifacts
    for pattern in phase.expected_artifacts:
        if pattern == "AUDIT_REPORT.md":
            p = Path(config["project_root"]) / "AUDIT_REPORT.md"
            if p.exists():
                parts.append(
                    f"AUDIT_REPORT.md ({_format_artifact_size(p.stat().st_size)})"
                )
            continue
        is_glob = any(ch in pattern for ch in "*?[")
        matches = list(scratchpad.glob(pattern))
        substantial = [m for m in matches if m.stat().st_size >= phase.min_artifact_bytes]
        if is_glob:
            parts.append(f"{pattern}: {len(substantial)} files")
        elif substantial:
            parts.append(
                f"{substantial[0].name} "
                f"({_format_artifact_size(substantial[0].stat().st_size)})"
            )
    if not parts:
        return ""
    return "gate: " + ", ".join(parts)


def _existing_later_phase_artifacts(
    scratchpad: Path,
    project_root: str,
    phases: list[Phase],
    phase_name: str,
    pipeline: str,
) -> list[str]:
    """Deprecated presence-based recovery check.

    Artifact recovery runs before launching a subprocess, so any later-phase
    files present at this point are, by definition, pre-existing scratchpad
    state. Treating their mere presence as current-phase overreach caused valid
    `rag_sweep` outputs to fail because old chain/verify artifacts were still
    on disk. Runtime containment is enforced by `_detect_foreign_phase_writes`,
    which compares the pre-launch snapshot with post-attempt state.

    Durable artifact ownership now lives in `_artifact_state.json`; future
    recovery logic should consult that ledger for downstream invalidation
    policy instead of quarantining files by presence.
    """
    del scratchpad, project_root, phases, phase_name, pipeline
    return []


def _split_nonblocking_foreign_writes(
    phase_name: str,
    foreign_writes: list[str],
) -> tuple[list[str], list[str]]:
    """Split foreign writes into quarantine-only and blocking violations."""
    from fnmatch import fnmatch

    benign: list[str] = []
    blocking: list[str] = []
    if phase_name == "report_index":
        for name in foreign_writes:
            if (
                fnmatch(name, "report_critical_high*.md")
                or fnmatch(name, "report_medium*.md")
                or fnmatch(name, "report_low_info*.md")
            ):
                benign.append(name)
            else:
                blocking.append(name)
        return benign, blocking
    if phase_name != "breadth":
        return [], list(foreign_writes)
    for name in foreign_writes:
        if fnmatch(name, "analysis_rescan_*.md") or fnmatch(
            name, "analysis_percontract_*.md"
        ):
            benign.append(name)
        else:
            blocking.append(name)
    return benign, blocking


def _has_containment_failure(missing: list[Any]) -> bool:
    return any(str(item).startswith("phase containment:") for item in missing)


def _generate_containment_retry_hint(phase_name: str, missing: list[Any]) -> str:
    lines = [
        "## RETRY HINT - phase containment violation",
        "",
        "The previous attempt wrote one or more later-phase artifacts. The "
        "offending files were quarantined by the driver. Retry ONLY the "
        f"`{phase_name}` phase and do not recreate quarantined later-phase "
        "outputs.",
        "",
        "Gate failure:",
    ]
    lines.extend(
        f"- MUST NOT recreate offending later-phase artifact from prior failure: {item}"
        for item in missing
    )
    if phase_name == "depth":
        lines.extend([
            "",
            "Depth retry boundary:",
            "- Reuse existing depth-owned outputs that are already present.",
            "- Produce or repair only missing Phase 4b depth artifacts.",
            "- MUST NOT write `rag_validation.md`.",
            "- MUST NOT write `chain_summaries_compact.md`.",
            "- MUST NOT write `hypotheses.md`, `finding_mapping.md`, "
            "`enabler_results.md`, `chain_hypotheses.md`, "
            "`composition_coverage.md`, or `synthesis_full.md`.",
            "- MUST NOT write `verification_queue.md`, "
            "`verification_queue_*.md`, `verify_*.md`, or `verify_core.md`.",
            "- MUST NOT write `skeptic_*.md`, `cross_batch_consistency.md`, "
            "`report_index.md`, `report_*.md`, or `AUDIT_REPORT.md`.",
            "- If inherited prompt text asks for chain-summary extraction, "
            "chain analysis, verification, or report work, stop instead.",
        ])
    elif phase_name.startswith("inventory_chunk"):
        chunk_letter = phase_name.rsplit("_", 1)[-1] if "_" in phase_name else "?"
        lines.extend([
            "",
            f"Inventory chunk retry boundary (`{phase_name}`):",
            f"- Write ONLY `findings_inventory_chunk_{chunk_letter}.md`.",
            "- MUST NOT write `findings_inventory.md` — that file is owned "
            "by the later inventory-merge phase.",
            "- MUST NOT write `inventory_evidence_validation.md` or "
            "`inventory_merge_receipt.md`.",
            "- If the inherited prompt text asks you to produce a final "
            "consolidated inventory, STOP — that is the merge phase's job.",
        ])
    return "\n".join(lines) + "\n"


def _run_phase_validators(
    phase: Phase,
    config: dict,
    scratchpad: Path,
    phases: list,
    rc: int,
    file_state_before: dict,
    violations_before: int = 0,
) -> tuple[bool, list[str]]:
    """Run ALL phase-specific validators and side-effects after gate_passes().

    Returns (passed, missing) — the enriched gate result after all phase-
    specific checks have been applied.

    Called from BOTH attempt 1 and attempt 2 in main() to eliminate the
    retry-block duplication class (v2.3.13).  Every validator that checks
    phase output correctness MUST live here — not inline in main().
    """
    # Codex/GPT models produce more concise output. Relax byte thresholds
    # to avoid false gate failures on structurally complete but terse artifacts.
    original_min_bytes = phase.min_artifact_bytes
    if config.get("cli_backend") == "codex" and phase.min_artifact_bytes > 50:
        phase.min_artifact_bytes = max(50, phase.min_artifact_bytes // 2)
    effective_min_bytes = phase.min_artifact_bytes
    passed, missing = gate_passes(scratchpad, config["project_root"], phase)
    phase.min_artifact_bytes = original_min_bytes

    # v2.8.6: Codex recon resilience for partial sub-agent failures.
    # Codex spawns collab workers that can hit capacity/thread limits,
    # leaving some artifacts at 0 bytes while others succeed.  Separate
    # core artifacts (required for pipeline) from supplementary ones
    # (enrichment that breadth agents can discover organically).
    # If only supplementary artifacts failed, write fallback content and
    # proceed instead of burning a retry on the same capacity issue.
    _RECON_SUPPLEMENTARY = {
        "attack_surface.md", "detected_patterns.md",
        "setter_list.md", "emit_list.md",
    }
    if (
        phase.name == "recon"
        and not passed
        and missing
    ):
        hard_missing = []
        soft_missing = []
        for item in missing:
            name = str(item).split()[0].split("(")[0].strip()
            if name in _RECON_SUPPLEMENTARY:
                soft_missing.append(item)
            else:
                hard_missing.append(item)
        if soft_missing and not hard_missing:
            # All failures are supplementary — write fallback content
            # and let the pipeline proceed.
            for name in _RECON_SUPPLEMENTARY:
                p = scratchpad / name
                try:
                    if not p.exists() or p.stat().st_size < effective_min_bytes:
                        title = name.replace(".md", "").replace("_", " ").title()
                        p.write_text(
                            f"# {title}\n\n"
                            "[LLM recon did not produce this artifact. "
                            "Breadth agents will discover this information "
                            "organically from source code analysis.]\n",
                            encoding="utf-8",
                        )
                except Exception:
                    pass
            passed = True
            missing = []
            log.warning(
                "[recon] supplementary artifacts degraded (non-blocking, "
                "fallback written): %s",
                "; ".join(str(x) for x in soft_missing),
            )
        elif soft_missing and hard_missing:
            # Core artifacts also failed — keep only core failures in
            # the gate result so the retry hint targets the real problem.
            missing = hard_missing

    # rc-parity check: subprocess died mid-write → file exists but corrupt.
    if rc != 0:
        parity_issues = _validate_rc_parity(
            phase, scratchpad, rc,
            backend=config.get("cli_backend", "claude"),
        )
        if parity_issues:
            passed = False
            missing = list(missing) + [
                f"rc={rc} parity: " + "; ".join(parity_issues)
            ]

    # --- phase containment: foreign-write detection ---
    # This must run before phase-specific quality gates. A later-phase write
    # means the subprocess broke its execution boundary; all other diagnostics
    # are secondary until that boundary is fixed.
    foreign_writes = _detect_foreign_phase_writes(
        scratchpad, config["project_root"], phases, phase.name,
        config["pipeline"], file_state_before
    )
    if foreign_writes:
        benign_foreign, blocking_foreign = _split_nonblocking_foreign_writes(
            phase.name, foreign_writes
        )
        moved_foreign = _quarantine_foreign_phase_writes(
            scratchpad, config["project_root"], phase.name,
            foreign_writes
        )
        if moved_foreign:
            log.warning(
                f"[{phase.name}] quarantined foreign later-phase "
                f"artifact(s): {moved_foreign[:10]}"
            )
        if benign_foreign:
            log.warning(
                f"[{phase.name}] quarantined non-blocking future-phase "
                f"artifact(s): {benign_foreign[:10]}"
            )
        if blocking_foreign:
            passed = False
            missing = list(missing) + [
                "phase containment: wrote later-phase artifacts: "
                + ", ".join(blocking_foreign[:10])
            ]
            return passed, missing

    # --- instantiate: validate spawn_manifest.md at the producer boundary ---
    if phase.name == "instantiate" and passed:
        manifest_issues = _validate_spawn_manifest_schema(scratchpad)
        if manifest_issues:
            passed = False
            missing = list(missing) + manifest_issues
            _write_retry_hint(
                scratchpad,
                phase.name,
                "\n".join([
                    "## RETRY HINT - spawn_manifest.md schema invalid",
                    "",
                    "Rewrite spawn_manifest.md as a markdown table whose spawned "
                    "breadth-agent rows are parseable by the pipeline gate.",
                    "",
                    "Required row contract:",
                    "- Include Template and Required columns in the header.",
                    "- Each spawned breadth agent row must be required and must "
                    "derive or explicitly name a first-pass analysis_*.md output.",
                    "- Do not put verify_*.md, analysis_rescan_*.md, "
                    "analysis_percontract_*.md, or analysis_merged_into_*.md "
                    "in first-pass breadth output rows.",
                    "",
                    "Gate failure:",
                    *[f"- {issue}" for issue in manifest_issues],
                    "",
                ]),
            )

    # --- inventory chunks: validate the chunk contract, not just file size ---
    if phase.name in (
        "inventory_chunk_a", "inventory_chunk_b", "inventory_chunk_c"
    ) and passed:
        chunk_issues = _validate_inventory_chunk_structure(scratchpad, phase.name)
        if chunk_issues:
            passed = False
            missing = list(missing) + [
                "inventory chunk structure: " + "; ".join(chunk_issues)
            ]
            _write_retry_hint(
                scratchpad,
                phase.name,
                "\n".join([
                    "## RETRY HINT - inventory chunk incomplete",
                    "",
                    "Rewrite the shard output as a complete direct-execution "
                    "inventory chunk. Do not spawn subagents and do not use "
                    "shell/Python helper scripts.",
                    "",
                    "Required sections:",
                    "- ## Source Summary",
                    "- ## Master Table",
                    "- ## Per-Finding Detail",
                    "",
                    "Every master-table row needs a matching "
                    "`### Finding [CC-NN]:` detail block with Source IDs, "
                    "Severity, Location, Preferred Tag, Verdict, Root Cause, "
                    "Description, and Impact.",
                    "",
                    "`**Impact**:` is mandatory for CONFIRMED, PARTIAL, "
                    "REFUTED, and Informational findings. Precondition "
                    "Analysis is allowed only after the mandatory fields and "
                    "does not replace Impact. Do not return until every "
                    "`### Finding [CC-NN]` block contains a literal "
                    "`**Impact**:` line.",
                    "",
                    "Gate failure:",
                    *[f"- {issue}" for issue in chunk_issues],
                    "",
                ]),
            )

    # --- report_index: completeness gate ---
    if phase.name == "report_index" and passed:
        index_issues = _check_index_completeness(
            scratchpad, config["project_root"]
        )
        if index_issues:
            repaired = _repair_report_index_dropouts(scratchpad)
            if repaired:
                log.info(
                    "[report_index] mechanically recovered "
                    f"{len(repaired)} dropped verify ID(s) into "
                    "report_index.md"
                )
                index_issues = _check_index_completeness(
                    scratchpad, config["project_root"]
                )
            if index_issues:
                passed = False
                missing = list(missing) + [
                    "index completeness: " + "; ".join(index_issues)
                ]
                hint = _generate_report_index_retry_hint(index_issues)
                if hint:
                    _write_retry_hint(scratchpad, phase.name, hint)
        # Phase E2: report_index rejects unverified queue rows.
        idx_in = _validate_report_index_inputs(scratchpad)
        if idx_in:
            passed = False
            missing = list(missing) + idx_in
            hint = _generate_report_index_retry_hint(idx_in)
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)
        coverage_issues = _validate_report_coverage_accounting(scratchpad)
        if coverage_issues:
            passed = False
            missing = list(missing) + coverage_issues

    # --- report_assemble: quality gate + degraded sentinel check ---
    if phase.name == "report_assemble" and passed:
        quality_issues = _run_report_quality_gate(
            scratchpad, config["project_root"]
        )
        if quality_issues:
            passed = False
            missing = list(missing) + [
                "report quality: " + "; ".join(quality_issues)
            ]
            hint = _generate_assemble_retry_hint(
                scratchpad, config["project_root"]
            )
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)
    if phase.name == "report_assemble":
        assemble_deg = _validate_assemble_not_degraded(scratchpad)
        if assemble_deg:
            passed = False
            missing = list(missing) + assemble_deg

    # --- verify_aggregate: mechanical fallback + path/parity/evidence ---
    # v2.4.3: SC verify_aggregate now routes through here too (was bypassed).
    if phase.name in ("verify_aggregate", "sc_verify_aggregate") and passed:
        if _generate_verify_core_if_missing(scratchpad):
            log.info("[verify_aggregate] verify_core.md generated mechanically")
        path_issues = _validate_cited_paths_in_verify(scratchpad)
        if path_issues:
            log.info(f"[verify_aggregate] {path_issues[0]}")
        parity_issues = _validate_verify_files_for_queue(scratchpad, min_bytes=effective_min_bytes)
        if parity_issues:
            passed = False
            missing = list(missing) + parity_issues
        tag_issues = _validate_verify_evidence_tags(scratchpad, min_bytes=effective_min_bytes)
        if tag_issues:
            passed = False
            missing = list(missing) + tag_issues
        # v2.3.14: retry hint for verify_aggregate
        if not passed:
            all_va_issues = (parity_issues or []) + (tag_issues or [])
            hint = _generate_verify_aggregate_retry_hint(all_va_issues)
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)

    # --- v2.4.0: PoC classification gates (post-verify, pre-report) ---
    if phase.name in ("verify_aggregate", "sc_verify_aggregate") and passed:
        mode = config.get("mode", "core")
        poc_warnings = _validate_poc_attempt_coverage(scratchpad, mode)
        if poc_warnings:
            viol_path = scratchpad / "violations.md"
            existing = ""
            if viol_path.exists():
                try:
                    existing = viol_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            with open(viol_path, "a", encoding="utf-8") as f:
                if not existing:
                    f.write("# Pipeline Violations\n\n")
                f.write("## PoC Attempt Coverage (v2.4.0)\n\n")
                for w in poc_warnings:
                    f.write(f"- {w}\n")
                f.write("\n")
            log.info(f"[{phase.name}] PoC attempt coverage: {len(poc_warnings)} warning(s) logged to violations.md")
        integrity_issues = _validate_poc_pass_integrity(scratchpad)
        if integrity_issues:
            log.info(f"[{phase.name}] PoC integrity: {len(integrity_issues)} finding(s) downgraded from [POC-PASS] to [CODE-TRACE]")
            for issue in integrity_issues:
                vf = _find_verify_file(scratchpad, issue["finding_id"])
                if vf:
                    try:
                        vf_content = vf.read_text(encoding="utf-8", errors="replace")
                        reason = issue["reason"]
                        for tag in EVIDENCE_TAGS_PROOF:
                            if tag in vf_content:
                                vf_content = vf_content.replace(tag, f"[CODE-TRACE] (was {tag}, integrity downgrade: {reason})")
                        vf.write_text(vf_content, encoding="utf-8")
                    except Exception:
                        pass
        demotions = _apply_poc_fail_demotions(scratchpad, mode)
        if demotions:
            log.info(f"[{phase.name}] PoC demotions: {len(demotions)} finding(s) capped via poc_demotions.md")

    # --- crossbatch: quality + full coverage + SC-mode parity/evidence ---
    if phase.name == "crossbatch" and passed:
        _write_crossbatch_manifest(scratchpad)
        appended_ids = _append_crossbatch_coverage_ledger(scratchpad)
        if appended_ids:
            log.info(
                f"[crossbatch] appended coverage ledger for "
                f"{len(appended_ids)} verifier ID(s) omitted from the agent output"
            )
        cbq_issues = _validate_crossbatch_quality(scratchpad)
        if cbq_issues:
            log.warning(
                "[crossbatch] quality (non-blocking): %s",
                "; ".join(cbq_issues),
            )
        cb_cov = _validate_crossbatch_full_coverage(scratchpad)
        if cb_cov:
            passed = False
            missing = list(missing) + cb_cov
            hint = _generate_crossbatch_retry_hint(scratchpad)
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)
        if config["pipeline"] != "l1":
            par = _validate_verify_files_for_queue(scratchpad, min_bytes=effective_min_bytes)
            if par:
                passed = False
                missing = list(missing) + par
            tag = _validate_verify_evidence_tags(scratchpad, min_bytes=effective_min_bytes)
            if tag:
                log.warning(
                    "[crossbatch] evidence tags (non-blocking): %s",
                    "; ".join(tag),
                )

    # --- skeptic: scope + full C/H coverage ---
    if phase.name == "skeptic" and passed:
        skeptic_issues = _validate_skeptic_scope(scratchpad)
        if skeptic_issues:
            passed = False
            missing = list(missing) + skeptic_issues
        sk_cov = _validate_skeptic_full_ch_coverage(scratchpad)
        if sk_cov:
            passed = False
            missing = list(missing) + sk_cov

    # --- graph_sweeps ---
    if phase.name == "graph_sweeps" and config["pipeline"] == "l1" and passed:
        gs_hard, gs_soft = _validate_graph_sweeps(
            scratchpad, config.get("mode", "core"),
            min_bytes=phase.min_artifact_bytes,
        )
        for w in gs_soft:
            log.warning("[graph_sweeps] %s", w)
        if gs_hard:
            passed = False
            missing = list(missing) + [
                "graph sweeps: " + "; ".join(gs_hard)
            ]

    # --- attention_repair ---
    if phase.name == "attention_repair" and passed:
        ar_hard, ar_soft = _validate_attention_repair(
            scratchpad, config.get("mode", "core")
        )
        for w in ar_soft:
            log.warning("[attention_repair] %s", w)
        if ar_hard:
            passed = False
            missing = list(missing) + [
                "attention repair: " + "; ".join(ar_hard)
            ]

    # --- semantic_dedup (L1): swap deduped queue and rebuild shard manifests ---
    if phase.name == "semantic_dedup" and passed:
        deduped = scratchpad / "verification_queue_deduped.md"
        orig = scratchpad / "verification_queue.md"
        if deduped.exists() and deduped.stat().st_size > 100:
            backup = scratchpad / "verification_queue_pre_dedup.md"
            if orig.exists():
                shutil.copy2(orig, backup)
            shutil.copy2(deduped, orig)
            rows = parse_verification_queue_rows(scratchpad)
            if rows:
                _write_queue_json_sidecar(orig, rows, kind="active")
            shards = ensure_verify_shard_manifests(scratchpad)
            shard_count = sum(len(v) for v in shards.values())
            log.info(
                f"[semantic_dedup] swapped deduped queue into "
                f"verification_queue.md; rebuilt {len(shards)} shard "
                f"manifest(s) with {shard_count} total row(s)"
            )
        else:
            log.info("[semantic_dedup] no deduped queue produced; keeping original")

    # --- sc_semantic_dedup (SC): swap deduped inventory before chain analysis ---
    if phase.name == "sc_semantic_dedup" and passed:
        deduped = scratchpad / "findings_inventory_deduped.md"
        orig = scratchpad / "findings_inventory.md"
        if deduped.exists() and deduped.stat().st_size > 100:
            backup = scratchpad / "findings_inventory_pre_dedup.md"
            if orig.exists():
                if not (scratchpad / "findings_inventory_base.md").exists():
                    _write_inventory_base_snapshot(scratchpad)
                shutil.copy2(orig, backup)
            shutil.copy2(deduped, orig)
            _write_finding_records_from_inventory(scratchpad)
            log.info(
                "[sc_semantic_dedup] swapped deduped inventory into "
                "findings_inventory.md (original backed up to "
                "findings_inventory_pre_dedup.md)"
            )
        else:
            log.info("[sc_semantic_dedup] no deduped inventory produced; keeping original")

    # --- supplemental mechanical dedup for full candidate set ---
    if phase.name in ("semantic_dedup", "sc_semantic_dedup") and passed:
        try:
            n_supp = _apply_mechanical_dedup_from_pairs(
                scratchpad, phase.name, supplemental=True,
            )
            if n_supp > 0:
                log.info(
                    f"[{phase.name}] supplemental dedup merged {n_supp} "
                    f"additional pair(s) from full candidate set"
                )
                if phase.name == "semantic_dedup":
                    rows = parse_verification_queue_rows(scratchpad)
                    if rows:
                        _write_queue_json_sidecar(
                            scratchpad / "verification_queue.md",
                            rows,
                            kind="active",
                        )
                    ensure_verify_shard_manifests(scratchpad)
                elif phase.name == "sc_semantic_dedup":
                    _write_finding_records_from_inventory(scratchpad)
        except Exception as exc:
            log.warning(f"[{phase.name}] supplemental dedup failed: {exc}")

    # --- recon: Slither materialization + coverage + scope_leftover ---
    if phase.name == "recon":
        if config["pipeline"] == "sc":
            generated = _materialize_sc_slither_flat_files(scratchpad)
            if generated:
                log.info(
                    "[recon] materialized SC Slither flat files: "
                    + ", ".join(generated)
                )
        coverage_issues = _validate_recon_coverage(
            scratchpad,
            config["project_root"],
            config.get("language", ""),
            config.get("subsystem_scope"),
            backend=config.get("cli_backend", "claude"),
            scope_file=config.get("scope_file"),
        )
        if coverage_issues:
            passed = False
            missing = list(missing) + [
                "recon coverage: " + "; ".join(coverage_issues)
            ]
        content_hard, content_soft = _validate_recon_content_structure(
            scratchpad, backend=config.get("cli_backend", "claude"),
        )
        if content_hard:
            passed = False
            missing = list(missing) + [
                "recon content: " + "; ".join(content_hard)
            ]
        if content_soft:
            log.warning(
                "[recon] content format (non-blocking): %s",
                "; ".join(content_soft),
            )
        if config["pipeline"] == "l1":
            leftover_issues = _validate_scope_leftover(
                scratchpad,
                config.get("subsystem_scope"),
                backend=config.get("cli_backend", "claude"),
            )
            if leftover_issues:
                log.warning(
                    "[scope_leftover] uncovered files (non-blocking): %s",
                    ", ".join(leftover_issues),
                )

    # --- inventory: parity + structure + evidence ---
    if phase.name == "inventory":
        parity_issues = _validate_inventory_parity(scratchpad)
        if parity_issues:
            passed = False
            missing = list(missing) + [
                "inventory parity: " + "; ".join(parity_issues)
            ]
        structure_issues = _validate_inventory_structure(scratchpad)
        if structure_issues:
            passed = False
            missing = list(missing) + [
                "inventory structure: " + "; ".join(structure_issues)
            ]
        else:
            _validate_inventory_evidence(
                scratchpad, config["project_root"]
            )
            _write_finding_records_from_inventory(scratchpad)
            _write_inventory_base_snapshot(scratchpad)
        # v2.3.14: retry hint for inventory
        if not passed:
            inv_issues = (parity_issues or []) + (structure_issues or [])
            hint = _generate_inventory_retry_hint(inv_issues)
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)

    # --- location_recovery: apply recovered locations ---
    if phase.name == "location_recovery" and passed and config["pipeline"] == "l1":
        applied = _apply_location_recovery(
            scratchpad, config["project_root"]
        )
        if applied:
            _write_finding_records_from_inventory(scratchpad)
            log.info(
                f"[location_recovery] applied {len(applied)} recovered "
                "location(s) to findings_inventory.md"
            )

    # --- verify_queue: evidence filter + shard manifests + parity ---
    if phase.name == "verify_queue" and passed and config["pipeline"] == "l1":
        removed = _filter_verification_queue_by_evidence(scratchpad)
        if removed:
            log.info(
                f"[verify_queue] removed {len(removed)} evidence-invalid "
                "finding(s) before verify shards"
            )
        ensure_verify_shard_manifests(scratchpad)
        queue_issues = _validate_verification_queue_inventory_parity(scratchpad)
        if queue_issues:
            passed = False
            missing = list(missing) + [
                "verification queue parity: " + "; ".join(queue_issues)
            ]
            hint = _generate_verify_queue_retry_hint(queue_issues)
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)

    # --- L1/SC verify shards: completion + cited paths ---
    if phase.name in L1_VERIFY_PHASE_NAMES or phase.name in SC_VERIFY_PHASE_NAMES:
        verify_issues = _validate_verify_completion(scratchpad, phase.name)
        if verify_issues:
            passed = False
            missing = list(missing) + verify_issues
            hint = _generate_verify_shard_retry_hint(verify_issues)
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)
        path_issues = _validate_cited_paths_in_verify(scratchpad)
        if path_issues:
            passed = False
            missing = list(missing) + [
                "verify location recovery: " + "; ".join(path_issues)
            ]

    # --- tier phases (legacy confirmation + body writers): completeness + body validation ---
    _is_tier_or_bw = (
        phase.name in (
            "report_critical_high", "report_medium", "report_low_info",
            "report_body_writer_critical_high",
            "report_body_writer_medium",
            "report_body_writer_low_info",
        )
        or re.match(r"^report_(critical_high|medium|low_info)_[a-z]$", phase.name)
        or re.match(r"^report_body_writer_(critical_high|medium|low_info)_[a-z]$", phase.name)
    )
    if _is_tier_or_bw:
        if not phase.name.startswith("report_body_writer_"):
            tier_issues = _validate_report_tier_completeness(scratchpad, phase.name)
            if tier_issues:
                passed = False
                missing = list(missing) + tier_issues
                hint = _generate_tier_retry_hint(scratchpad, phase.name)
                if hint:
                    _write_retry_hint(scratchpad, phase.name, hint)
        check_phase_name = phase.name.replace("report_body_writer_", "report_")
        body_issues = _validate_tier_body_against_manifest(
            scratchpad, check_phase_name
        )
        if body_issues and phase.name.startswith("report_body_writer_"):
            repaired = _repair_report_body_from_manifest(scratchpad, phase.name)
            if repaired:
                log.info(
                    f"[{phase.name}] mechanically repaired {repaired} "
                    "stale REPORT-BLOCKED body section(s) from manifest evidence"
                )
                body_issues = _validate_tier_body_against_manifest(
                    scratchpad, check_phase_name
                )
        if body_issues and phase.name.startswith("report_body_writer_"):
            hint = _generate_body_writer_retry_hint(scratchpad, phase.name)
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)
        if body_issues:
            passed = False
            missing = list(missing) + body_issues

    # --- depth (L1): full validator suite ---
    if phase.name == "depth" and config["pipeline"] == "l1":
        # v2.6.3: synthesize lifecycle artifacts for ALL backends.
        # Codex: force-overwrite (mechanical version more reliable).
        # Claude: fill missing only (LLM may write prose-format files
        # that fail the structured validator).
        _synth_force = config.get("cli_backend") == "codex"
        synth = _synthesize_depth_lifecycle_artifacts(
            scratchpad, config["pipeline"], force=_synth_force,
            mode=config.get("mode", "core"),
        )
        if synth:
            log.info(
                f"[{phase.name}] auto-synthesized lifecycle artifacts: "
                f"{', '.join(synth)}"
            )
        offenders = _scan_for_halt_and_gatefail(
            scratchpad, violations_offset=violations_before
        )
        if offenders:
            passed = False
            missing = list(missing) + [
                "depth policy violation: " + ", ".join(offenders)
            ]
        # v2.6.3: mode-aware never-cut enforcement (mirrors SC gate)
        _mode = config.get("mode", "core")
        never_cut_missing = _assert_never_cut_artifacts(
            scratchpad, l1_never_cut_groups(_mode)
        )
        if never_cut_missing:
            passed = False
            missing = list(missing) + [
                "never-cut artifacts missing: " + ", ".join(never_cut_missing)
            ]
        # v2.6.3: checkpoint labels are Thorough-only for design-stress/
        # perturbation/skill-execution; skip checkpoint gate in Light/Core.
        if _mode == "thorough":
            checkpoint_issues = _assert_never_cut_checkpoint(scratchpad, _mode)
            if checkpoint_issues:
                passed = False
                missing = list(missing) + [
                    "never-cut checkpoint invalid: " + ", ".join(checkpoint_issues)
                ]
        exit_issues = _validate_depth_exit(scratchpad)
        if exit_issues:
            log.warning(
                "[depth] exit metadata (non-blocking): %s",
                ", ".join(exit_issues),
            )
        coverage_issues = _validate_depth_coverage(scratchpad, _mode)
        if coverage_issues:
            log.warning(
                "[depth] iter2 coverage (non-blocking): %s",
                "; ".join(coverage_issues),
            )
        notread_issues = _check_notread_priority_coverage(scratchpad, _mode)
        if notread_issues and _mode == "thorough":
            log.info(
                "[depth] notread priority queued for attention_repair: "
                + "; ".join(notread_issues)
            )
        step_trace_issues = _check_step_execution_traces(scratchpad, _mode)
        if step_trace_issues:
            log.warning(
                "[depth] step trace (non-blocking): %s",
                "; ".join(step_trace_issues),
            )
        # v2.5.0 P0: graph-artifact consumption enforcement
        graph_issues = _check_graph_artifact_consumption(
            scratchpad, _mode
        )
        if graph_issues:
            log.warning(
                "[depth] graph consumption (non-blocking): %s",
                "; ".join(graph_issues),
            )
        # v2.6.2: detect formulaic stub confidence scores
        conf_quality_issues = _validate_confidence_scores_quality(
            scratchpad, _mode
        )
        if conf_quality_issues:
            log.warning(
                f"[{phase.name}] confidence stub: "
                + "; ".join(conf_quality_issues)
            )
        # v2.6.3: iter2 is mandatory only in Thorough mode
        conf_iter2_issues: list[str] = []
        if _mode == "thorough":
            conf_iter2_issues = _validate_confidence_iter2_mandatory(scratchpad)
            # v2.8.1: stub scores above 0.7 fool the iter2 check into
            # thinking all findings are CONFIDENT.  When the stub detector
            # fires, force iter2 regardless of the score values.
            if not conf_iter2_issues and conf_quality_issues:
                da_files = (
                    list(scratchpad.glob("depth_da_*_findings.md"))
                    + list(scratchpad.glob("depth_iter2_*_findings.md"))
                )
                if not da_files:
                    conf_iter2_issues = [
                        "confidence scores are formulaic stubs "
                        f"({'; '.join(conf_quality_issues)}); "
                        "iter2 mandatory to produce real per-finding analysis"
                    ]
            if conf_iter2_issues:
                passed = False
                missing = list(missing) + [
                    "confidence iter2: " + "; ".join(conf_iter2_issues)
                ]
        cov_issues = _compute_subsystem_coverage_gap(
            scratchpad, _mode
        )
        if cov_issues:
            log.info(f"[{phase.name}] {cov_issues[0]}")
        # v2.3.14: retry hint for depth (L1)
        if not passed:
            depth_issues = (
                (never_cut_missing or [])
                + (conf_iter2_issues or [])
            )
            hint = _generate_depth_retry_hint(
                depth_issues,
                backend=config.get("cli_backend", "claude"),
            )
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)

    # --- depth (SC): full validator suite ---
    elif phase.name == "depth" and config["pipeline"] == "sc":
        # v2.6.3: synthesize for all backends (same rationale as L1)
        _synth_force_sc = config.get("cli_backend") == "codex"
        synth = _synthesize_depth_lifecycle_artifacts(
            scratchpad, config["pipeline"], force=_synth_force_sc,
            mode=config.get("mode", "core"),
        )
        if synth:
            log.info(
                f"[{phase.name}] auto-synthesized lifecycle artifacts: "
                f"{', '.join(synth)}"
                )
        offenders = _scan_for_halt_and_gatefail(
            scratchpad, violations_offset=violations_before
        )
        if offenders:
            passed = False
            missing = list(missing) + [
                "depth policy violation: " + ", ".join(offenders)
            ]
        never_cut_missing = _assert_never_cut_artifacts(
            scratchpad, sc_never_cut_groups(config.get("mode", "core"))
        )
        if never_cut_missing:
            passed = False
            missing = list(missing) + [
                "never-cut artifacts missing: " + ", ".join(never_cut_missing)
            ]
        # v2.4.7: L1 checkpoint validator removed from SC gate — SC prompt
        # writes checkpoint_postdepth.md (not the L1 filename), and the
        # artifact check above already enforces NEVER-CUT via individual files.
        semantic_gap_issues = _validate_semantic_gap_niche(
            scratchpad, config.get("mode", "core")
        )
        if semantic_gap_issues:
            passed = False
            missing = list(missing) + [
                "semantic-gap niche: " + "; ".join(semantic_gap_issues)
            ]
        iter_issues = _validate_depth_iterations(
            scratchpad, config.get("mode", "core")
        )
        if iter_issues:
            passed = False
            missing = list(missing) + [
                "depth iteration invariant: " + "; ".join(iter_issues)
            ]
        sc_cov_issues = _validate_sc_subsystem_coverage(
            scratchpad, config.get("mode", "core")
        )
        if sc_cov_issues:
            passed = False
            missing = list(missing) + [
                "SC subsystem coverage: " + "; ".join(sc_cov_issues)
            ]
        coverage_issues = _validate_depth_coverage(
            scratchpad, config.get("mode", "core")
        )
        if coverage_issues:
            log.warning(
                "[depth] iter2 coverage (non-blocking): %s",
                "; ".join(coverage_issues),
            )
        notread_issues = _check_notread_priority_coverage(
            scratchpad, config.get("mode", "core")
        )
        if notread_issues and config.get("mode") == "thorough":
            log.info(
                "[depth] notread priority queued for attention_repair: "
                + "; ".join(notread_issues)
            )
        step_trace_issues = _check_step_execution_traces(
            scratchpad, config.get("mode", "core")
        )
        if step_trace_issues:
            log.warning(
                "[depth] step trace (non-blocking): %s",
                "; ".join(step_trace_issues),
            )
        # v2.5.0 P0: graph-artifact consumption enforcement
        graph_issues = _check_graph_artifact_consumption(
            scratchpad, config.get("mode", "core")
        )
        if graph_issues:
            log.warning(
                "[depth] graph consumption (non-blocking): %s",
                "; ".join(graph_issues),
            )
        # v2.6.2: detect formulaic stub confidence scores
        _sc_mode = config.get("mode", "core")
        conf_quality_issues = _validate_confidence_scores_quality(
            scratchpad, _sc_mode
        )
        if conf_quality_issues:
            log.warning(
                f"[{phase.name}] confidence stub: "
                + "; ".join(conf_quality_issues)
            )
        # v2.6.3: iter2 is mandatory only in Thorough mode
        conf_iter2_issues: list[str] = []
        if _sc_mode == "thorough":
            conf_iter2_issues = _validate_confidence_iter2_mandatory(scratchpad)
            # v2.8.1: stub scores above 0.7 fool the iter2 check — see L1 block
            if not conf_iter2_issues and conf_quality_issues:
                da_files = (
                    list(scratchpad.glob("depth_da_*_findings.md"))
                    + list(scratchpad.glob("depth_iter2_*_findings.md"))
                )
                if not da_files:
                    conf_iter2_issues = [
                        "confidence scores are formulaic stubs "
                        f"({'; '.join(conf_quality_issues)}); "
                        "iter2 mandatory to produce real per-finding analysis"
                    ]
            if conf_iter2_issues:
                passed = False
                missing = list(missing) + [
                    "confidence iter2: " + "; ".join(conf_iter2_issues)
                ]
        cov_issues = _compute_subsystem_coverage_gap(
            scratchpad, _sc_mode
        )
        if cov_issues:
            log.info(f"[{phase.name}] {cov_issues[0]}")
        # v2.3.14: retry hint for depth (SC)
        if not passed:
            depth_issues = (
                (never_cut_missing or [])
                + (semantic_gap_issues or [])
                + (iter_issues or [])
                + (sc_cov_issues or [])
                + (conf_iter2_issues or [])
            )
            hint = _generate_depth_retry_hint(
                depth_issues,
                backend=config.get("cli_backend", "claude"),
            )
            if hint:
                _write_retry_hint(scratchpad, phase.name, hint)

    # --- late containment check removed (v2.6.2) ---
    # The early containment check (line ~1694) already detects all LLM-written
    # foreign artifacts using the pre-subprocess file_state_before snapshot.
    # This late check was redundant for LLM overstepping and false-positived
    # on files legitimately written by the driver's own Python post-processing
    # (e.g., ensure_verify_shard_manifests in semantic_dedup/verify_queue).
    # Quarantine of LLM-written foreign files is handled by the early check.

    return passed, missing


def _phase_has_fresh_expected_artifact(
    phase: Phase,
    scratchpad: Path,
    project_root: str,
    before_state: dict[str, tuple[int, int]],
) -> bool:
    """Return True if this attempt wrote a substantial expected artifact.

    Empty stdio is a weak failure signal. It catches real API/resumption
    misfires, but some Claude runs can still produce valid artifacts while
    leaving a tiny log. Use file-state deltas to avoid retrying a phase that
    actually wrote fresh, gate-checkable output.
    """
    from fnmatch import fnmatch

    patterns = list(phase.expected_artifacts or [])
    if not patterns:
        return False
    min_size = int(getattr(phase, "min_artifact_size_bytes", 100) or 100)
    after_state = _snapshot_file_state(scratchpad, project_root)
    for name, meta in after_state.items():
        if meta[1] < min_size:
            continue
        if before_state.get(name) == meta:
            continue
        for pattern in patterns:
            key = "../AUDIT_REPORT.md" if pattern == "AUDIT_REPORT.md" else pattern
            if fnmatch(name, key):
                return True
    return False


def _purge_scratchpad(scratchpad: Path, config: dict) -> None:
    """Delete all generated artifacts in the scratchpad and reset checkpoint."""
    import shutil

    # Close the log file handler so Windows releases the file lock.
    for handler in log.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            log.removeHandler(handler)

    for item in scratchpad.iterdir():
        if item.name.startswith("."):
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except PermissionError:
            pass


def main():
    # Terminal: WARNING+ only (keep TUI clean).
    # File: everything (INFO+) for debugging via `tail -f _plamen.log`.
    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setLevel(logging.WARNING)
    _stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          datefmt="%H:%M:%S")
    )
    logging.basicConfig(
        level=logging.INFO,
        handlers=[_stderr_handler],
    )

    if len(sys.argv) < 2:
        print(
            "usage: plamen_driver.py <config.json> "
            "[--force] [--no-sleep|--no-hibernate]",
            file=sys.stderr,
        )
        sys.exit(EXIT_CONFIG_MISSING)

    args = sys.argv[1:]
    no_sleep_flags = {"--no-sleep", "--no-hibernate", "--ignore-hibernation"}
    force_resume = ("--force" in args) or (os.environ.get("PLAMEN_FORCE") == "1")
    no_sleep = any(a in no_sleep_flags for a in args) or force_resume
    fresh_restart = "--fresh" in args
    if no_sleep:
        os.environ["PLAMEN_NO_HIBERNATE"] = "1"
    args = [a for a in args
            if a not in {"--force", "--fresh"} and a not in no_sleep_flags]

    config_path = Path(args[0])
    if not config_path.exists():
        print(f"config not found: {config_path}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_MISSING)

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"config parse error: {e}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_MISSING)

    # Required keys
    for key in ("project_root", "scratchpad", "language", "mode", "pipeline"):
        if key not in config:
            print(f"config missing required key: {key}", file=sys.stderr)
            sys.exit(EXIT_CONFIG_MISSING)

    scratchpad = Path(config["scratchpad"])
    scratchpad.mkdir(parents=True, exist_ok=True)

    # File log so users can `tail -f .scratchpad/_plamen.log` from another
    # terminal while the driver runs in the background.
    _file_handler = logging.FileHandler(
        scratchpad / "_plamen.log", encoding="utf-8"
    )
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                          datefmt="%H:%M:%S")
    )
    log.addHandler(_file_handler)

    if force_resume or fresh_restart:
        marker = scratchpad / ".hibernating"
        if marker.exists():
            marker.unlink()
            print("[force/fresh] cleared .hibernating marker", file=sys.stderr)
    elif no_sleep:
        marker = scratchpad / ".hibernating"
        if marker.exists():
            marker.unlink()
            print("[no-sleep] cleared .hibernating marker", file=sys.stderr)
    else:
        hibernate_exit = maybe_resume_hibernation(scratchpad)
        if hibernate_exit is not None:
            sys.exit(hibernate_exit)

    if fresh_restart:
        log.info("[fresh] wiping scratchpad for fresh restart")
        _purge_scratchpad(scratchpad, config)
        # Re-add file handler since _purge_scratchpad closes it
        _file_handler = logging.FileHandler(
            scratchpad / "_plamen.log", encoding="utf-8"
        )
        _file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                              datefmt="%H:%M:%S")
        )
        log.addHandler(_file_handler)
        log.info("[fresh] scratchpad purged, starting from phase 1")

    # Mechanical pre-pass (writes inventory/variables/functions/build_status/subsystems)
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from recon_prepass import run_recon_prepass
        status = run_recon_prepass(config)
        log.info(f"[pre-pass] {status}")
    except Exception as e:
        log.warning(f"[pre-pass] failed: {e} -- LLM recon will write all artifacts")

    try:
        checkpoint = Checkpoint.load(scratchpad)
    except RuntimeError as exc:
        log.error(f"[checkpoint] failed to load checkpoint: {exc}")
        sys.exit(EXIT_DEGRADED)
    checkpoint.config = config
    if checkpoint.completed:
        log.info(f"[resume] skipping completed phases: {checkpoint.completed}")
    quarantined_report = _quarantine_report_without_completed_assemble(
        scratchpad, config["project_root"], checkpoint
    )
    if quarantined_report:
        log.warning(
            "[startup] quarantined project-root AUDIT_REPORT.md because "
            "`report_assemble` has not completed: "
            f"{quarantined_report}"
        )
    # Phase E13 item #4: clear stale `.degraded` sentinels from prior aborted
    # runs before the first phase fires. Sentinels written DURING this run
    # (by phase handlers like report_assemble) remain — only pre-existing
    # ones are removed.
    cleared_sentinels = _clear_stale_degraded_sentinels(scratchpad)
    if cleared_sentinels:
        log.info(
            f"[startup] cleared {len(cleared_sentinels)} stale degraded "
            f"sentinel(s) from prior run: {', '.join(cleared_sentinels)}"
        )
    # Plant V2 marker BEFORE the first phase spawns. The phase_gate hook
    # (~/.claude/hooks/phase_gate.py) detects `_v2_checkpoint.json` and
    # stays dormant so it doesn't fight the driver's phase-scoped model.
    # Must happen BEFORE run_phase because the first phase's first Stop
    # hook fires before any phase writes artifacts.
    checkpoint.save(scratchpad)

    phases = L1_PHASES if config["pipeline"] == "l1" else SC_PHASES
    mode = config["mode"]

    # Phase-graph startup validation. Closes the architectural defect where a
    # mode/language combination could ship a broken phase list (duplicate
    # names, mode-empty phase, malformed expected_artifacts, sentinel timeout)
    # and the bug only manifests mid-audit. Halt before any phase work.
    graph_issues = validate_phase_graph(phases, mode, config["pipeline"])
    if graph_issues:
        log.error(f"[startup] phase graph invalid for mode={mode} pipeline={config['pipeline']}:")
        for issue in graph_issues:
            log.error(f"[startup]   - {issue}")
        sys.exit(EXIT_DEGRADED)

    # Refresh SC body manifests before expansion in resume runs. The manifests
    # are derived from report_index.md, so parser/gate fixes can invalidate the
    # prior shard shape; stale report_medium_a/b JSON files must not keep the
    # old phase graph alive.
    if config.get("pipeline") != "l1" and (scratchpad / "report_index.md").exists():
        try:
            _build_sc_body_writer_manifests(scratchpad)
        except Exception as exc:
            log.warning(f"[startup] SC body manifest refresh failed: {exc!r}")

    # Expand tier sentinel phases if manifests already exist (resume case).
    phases[:] = expand_shard_phases(phases, scratchpad)

    active_after_expand = {p.name for p in phases if mode in p.modes}
    stale_dynamic_report_re = re.compile(
        r"^report(?:_body_writer)?_(?:critical_high|medium|low_info)_[a-z]$"
    )
    stale_checkpoint_report_names = [
        name for name in list(checkpoint.completed) + list(checkpoint.degraded)
        if stale_dynamic_report_re.match(name) and name not in active_after_expand
    ]
    if stale_checkpoint_report_names:
        stale_set = set(stale_checkpoint_report_names)
        checkpoint.completed = [n for n in checkpoint.completed if n not in stale_set]
        checkpoint.degraded = [n for n in checkpoint.degraded if n not in stale_set]
        for name in stale_set:
            checkpoint.clear_degraded_sentinel(scratchpad, name)
        checkpoint.save(scratchpad)
        log.warning(
            "[startup] removed stale report shard checkpoint entries after "
            "manifest refresh: " + ", ".join(sorted(stale_set))
        )

    # Rewind AFTER expansion so shard names (e.g. report_body_writer_critical_high_c2)
    # are visible in the phase list — pre-expansion only sentinel names exist.
    rewound = _rewind_completed_after_overflow(scratchpad, checkpoint, phases)
    if rewound:
        checkpoint.save(scratchpad)
        log.warning(
            "[startup] rewound completed checkpoint entries because prior "
            "phase-containment overflow exists: " + ", ".join(rewound)
        )

    active_phases = [p for p in phases if mode in p.modes]
    try:
        artifact_rewound = _reconcile_completed_checkpoint_artifacts(
            scratchpad, config["project_root"], checkpoint, phases, mode
        )
    except RuntimeError as exc:
        log.error(f"[checkpoint] invalid resume state: {exc}")
        sys.exit(EXIT_DEGRADED)
    if artifact_rewound:
        checkpoint.save(scratchpad)
        log.warning(
            "[startup] rewound completed checkpoint entries because their "
            "artifact gates no longer pass: " + ", ".join(artifact_rewound)
        )
        active_phases = [p for p in phases if mode in p.modes]

    display.graceful_stop.install()
    display.pause_toggle.start()

    completed_count = sum(1 for p in active_phases if p.name in checkpoint.completed)
    remaining_count = len(active_phases) - completed_count
    ai_model = _format_ai_model_summary(config, active_phases, mode)
    display.print_banner(
        config["pipeline"], mode, config["project_root"],
        remaining_count, completed_count, str(scratchpad), ai_model,
    )

    prev_phase: Optional[str] = None
    skipped_names: list[str] = []
    _halted = False
    _rate_limit_halt = False

    for phase in phases:
        if phase.name in checkpoint.completed:
            skipped_names.append(phase.name)
            log.info(f"[{phase.name}] already completed -- skipping")
            continue
        if mode not in phase.modes:
            skipped_names.append(phase.name)
            log.info(f"[{phase.name}] not in {mode} mode -- skipping")
            continue

        if skipped_names:
            display.print_skipped_summary(skipped_names)
            skipped_names = []

        # Compute phase index within active (mode-filtered) phases once,
        # so both conditional-skip display and print_phase_start use it.
        total_active = len(active_phases)
        phase_idx = next(
            (i for i, p in enumerate(active_phases) if p.name == phase.name),
            0,
        )

        # ── Bake phase: mechanical pre-write fallback ────────────────
        # primitive_status.md records SCIP/opengrep/ast-grep availability.
        # Pre-write the fallback so the gate passes even if the LLM fails
        # to probe tools or they aren't installed. Both Claude and Codex
        # then try to improve it via shell probes (Codex has full shell
        # access via --dangerously-bypass-approvals-and-sandbox).
        if phase.name == "bake":
            _PRIMITIVE_FALLBACK = (
                "SCIP_GO_REUSED=false\n"
                "SCIP_GO_AVAILABLE=false\n"
                "SCIP_RUST_REUSED=false\n"
                "SCIP_RUST_AVAILABLE=false\n"
                "SCIP_PREBAKE_COMPLETE=false\n"
                "SCIP_PREBAKE_FILES=0\n"
                "OPENGREP_AVAILABLE=false\n"
                "AST_GREP_AVAILABLE=false\n"
            )
            prim = scratchpad / "primitive_status.md"
            if not prim.exists():
                prim.write_text(_PRIMITIVE_FALLBACK, encoding="utf-8")
                log.info("[bake] pre-wrote primitive_status.md with fallback content")

        if phase.name == "inventory_prepare":
            ensure_inventory_shard_plan(
                scratchpad,
                int(config.get("inventory_target_per_shard", 70)),
                int(config.get("inventory_max_shards", 3)),
            )
            checkpoint.mark_completed(phase.name)
            checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
            checkpoint.save(scratchpad)
            log.info("[inventory_prepare] wrote inventory shard plan/manifests")
            display.print_phase_skipped(
                phase_idx + 1, total_active, phase.name,
                "mechanical (Python-only)",
            )
            continue

        if phase.name in (
            "inventory_chunk_a", "inventory_chunk_b", "inventory_chunk_c"
        ):
            shard_files = parse_inventory_shard_manifest(scratchpad, phase.name)
            manifest_path = scratchpad / f"{phase.name}.manifest.md"
            if manifest_path.exists() and not shard_files:
                try:
                    manifest_text = manifest_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    manifest_text = ""
                explicit_empty = bool(
                    re.search(r"\b(0\s+assigned|0\s+source|no\s+assigned|empty\s+shard)\b",
                              manifest_text, re.IGNORECASE)
                    or re.search(r"\bAssigned\s+files\s*:\s*0\b", manifest_text, re.IGNORECASE)
                    or (
                        "FILE" in manifest_text.upper()
                        and (
                            "ROLE" in manifest_text.upper()
                            or "STATUS" in manifest_text.upper()
                            or "MODEL" in manifest_text.upper()
                        )
                    )
                )
                if not explicit_empty:
                    (scratchpad / f"{phase.name}.degraded").write_text(
                        f"[MANIFEST-SCHEMA-INVALID] {manifest_path.name} exists but "
                        "contains no parseable assigned source files.\n",
                        encoding="utf-8",
                    )
                    if phase.name not in checkpoint.degraded:
                        checkpoint.degraded.append(phase.name)
                    checkpoint.save(scratchpad)
                    log.error(f"[{phase.name}] shard manifest schema invalid")
                    if phase.critical:
                        display.print_failure_diagnosis(
                            phase.name, str(scratchpad),
                            [f"shard manifest schema invalid: {manifest_path.name}"],
                            config,
                        )
                        sys.exit(EXIT_DEGRADED)
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "manifest schema invalid (degraded)",
                    )
                    continue
            if not shard_files:
                write_inventory_chunk_placeholder(
                    scratchpad, phase.name, "0 assigned analysis files in shard manifest"
                )
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info(f"[{phase.name}] N/A (0 assigned analysis files) -- writing placeholder and skipping")
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    "0 assigned files in shard",
                )
                continue

        if phase.name == "inventory":
            shard_outputs = []
            for name in (
                "findings_inventory_chunk_a.md",
                "findings_inventory_chunk_b.md",
                "findings_inventory_chunk_c.md",
            ):
                p = scratchpad / name
                if p.exists() and p.stat().st_size >= 100:
                    shard_outputs.append(p)
            if len(shard_outputs) >= 2:
                parsed, merged = _write_mechanical_inventory_from_chunks(scratchpad)
                if merged > 0:
                    _validate_inventory_evidence(scratchpad, config["project_root"])
                    parity_issues = _validate_inventory_parity(scratchpad)
                    if not parity_issues:
                        checkpoint.mark_completed(phase.name)
                        checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                        checkpoint.save(scratchpad)
                        log.info(
                            f"[inventory] mechanically merged {parsed} chunk findings "
                            f"into {merged} inventory findings"
                        )
                        display.print_phase_skipped(
                            phase_idx + 1, total_active, phase.name,
                            f"mechanical merge ({merged} findings from chunks)",
                        )
                        continue
                    log.error(
                        f"[inventory] mechanical chunk merge failed parity: {parity_issues}"
                    )
            if len(shard_outputs) == 1:
                target = scratchpad / "findings_inventory.md"
                target.write_text(
                    shard_outputs[0].read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
                _write_finding_records_from_inventory(scratchpad)
                # v2.4.3: run parity + evidence validators instead of blindly continuing
                _validate_inventory_evidence(scratchpad, config["project_root"])
                parity_issues = _validate_inventory_parity(scratchpad)
                if not parity_issues:
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info("[inventory] single-shard inventory copied directly to findings_inventory.md")
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "mechanical (single-shard copy)",
                    )
                    continue
                log.warning(f"[inventory] single-shard copy failed parity: {parity_issues}")

        if config["pipeline"] == "l1" and phase.name == "graph_sweeps":
            cov_issues = _compute_subsystem_coverage_gap(
                scratchpad, config.get("mode", "core")
            )
            if cov_issues:
                log.info(f"[graph_sweeps] {cov_issues[0]}")
            needed, reason = _graph_sweeps_needed(
                scratchpad, config.get("mode", "core")
            )
            if not needed:
                _write_graph_sweeps_skip(scratchpad, reason)
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info(f"[graph_sweeps] N/A ({reason}) -- writing skip summary")
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    f"N/A ({reason})",
                )
                continue

        if phase.name == "attention_repair":
            needed, reason = _prepare_attention_repair(
                scratchpad, config.get("mode", "core")
            )
            if not needed:
                _write_attention_repair_skip(scratchpad, reason)
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info(f"[attention_repair] N/A ({reason}) -- writing skip summary")
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    f"N/A ({reason})",
                )
                continue
            log.info(f"[attention_repair] queued {reason}")
            existing_hard, _existing_soft = _validate_attention_repair(
                scratchpad, config.get("mode", "core")
            )
            if not existing_hard:
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info("[attention_repair] existing artifacts validate -- skipping rerun")
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    "existing artifacts validate",
                )
                continue

        if config.get("pipeline") == "l1" and phase.name == "location_recovery":
            needed, reason = _location_recovery_needed(
                scratchpad, config["project_root"]
            )
            if not needed:
                _write_location_recovery_skip(scratchpad, reason)
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info(f"[location_recovery] N/A ({reason}) -- writing skip summary")
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    f"N/A ({reason})",
                )
                continue
            log.info(f"[location_recovery] queued {reason}")

        # v2.5.0: SC depth promotion runs before sc_semantic_dedup (all modes).
        # Previously anchored to attention_repair (Thorough-only), which left
        # Light/Core operating on stale inventory for dedup + chain.
        if phase.name == "sc_semantic_dedup" and config.get("pipeline") == "sc":
            promoted = _promote_depth_findings_to_inventory(scratchpad)
            if promoted:
                log.info(
                    f"[sc_semantic_dedup] promoted {len(promoted)} depth "
                    "finding(s) into findings_inventory.md before dedup"
                )
            n_pairs = _compute_dedup_candidate_pairs(scratchpad)
            if n_pairs:
                log.info(f"[sc_semantic_dedup] {n_pairs} dedup candidate pair(s) written")
            dedup_issues = _validate_depth_promotion_dedup(scratchpad)
            for issue in dedup_issues:
                log.warning(f"[sc_semantic_dedup] {issue}")

        if phase.name == "attention_repair" and config.get("pipeline") == "sc":
            # Thorough-only: additional validation after promotion already happened
            pass

        # v2.4.9: Mechanical extraction of chain summaries before chain phase.
        # The chain prompt requires chain_summaries_compact.md (orchestrator-owned).
        if phase.name == "chain" and config.get("pipeline") == "sc":
            _extract_chain_summaries_compact(scratchpad)
            written = _write_chain_passthrough_outputs(
                scratchpad,
                "pre-run scaffold safety net; Chain Agent 1 may overwrite "
                "these artifacts with grouped hypotheses and enabler analysis",
            )
            log.info(
                f"[chain] wrote deterministic handoff scaffold before "
                f"Chain Agent 1 subprocess: {written}"
            )

        if config["pipeline"] == "l1" and phase.name == "verify_queue":
            promoted = _promote_depth_findings_to_inventory(scratchpad)
            if promoted:
                log.info(
                    f"[verify_queue] promoted {len(promoted)} depth finding(s) "
                    "into findings_inventory.md before queue generation"
                )
            n_pairs = _compute_dedup_candidate_pairs(scratchpad)
            if n_pairs:
                log.info(f"[verify_queue] {n_pairs} dedup candidate pair(s) written")
            dedup_issues = _validate_depth_promotion_dedup(scratchpad)
            for issue in dedup_issues:
                log.warning(f"[verify_queue] {issue}")
            _validate_inventory_evidence(scratchpad, config["project_root"])
            depth_promotion_issues = _validate_depth_promotion_receipt(scratchpad)
            if depth_promotion_issues:
                log.error("[verify_queue] " + "; ".join(depth_promotion_issues))
                (scratchpad / "verify_queue.degraded").write_text(
                    "Depth promotion failed before verification queue.\n"
                    + "\n".join(depth_promotion_issues)
                    + "\n",
                    encoding="utf-8",
                )
                if "verify_queue" not in checkpoint.degraded:
                    checkpoint.degraded.append("verify_queue")
                checkpoint.save(scratchpad)
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad), depth_promotion_issues, config,
                )
                sys.exit(EXIT_DEGRADED)
            _write_finding_records_from_inventory(scratchpad)
            routed = _write_mechanical_verification_queue_from_inventory(scratchpad)
            removed = _filter_verification_queue_by_evidence(scratchpad)
            shards = ensure_verify_shard_manifests(scratchpad)
            queue_issues = _validate_verification_queue_inventory_parity(scratchpad)
            if queue_issues:
                log.error("[verify_queue] " + "; ".join(queue_issues))
                (scratchpad / "verify_queue.degraded").write_text(
                    "Verification queue parity failed.\n"
                    + "\n".join(queue_issues)
                    + "\n",
                    encoding="utf-8",
                )
                if "verify_queue" not in checkpoint.degraded:
                    checkpoint.degraded.append("verify_queue")
                checkpoint.save(scratchpad)
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad), queue_issues, config,
                )
                sys.exit(EXIT_DEGRADED)
            active = len(parse_verification_queue_rows(scratchpad))
            shard_count = sum(len(v) for v in shards.values())
            checkpoint.mark_completed(phase.name)
            checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
            checkpoint.save(scratchpad)
            extra = f"; evidence-excluded {len(removed)}" if removed else ""
            log.info(
                f"[verify_queue] mechanically routed {routed} inventory "
                f"finding(s) into {active} active queue row(s) across "
                f"{len(shards)} shard manifest(s), shard rows={shard_count}{extra}"
            )
            display.print_phase_skipped(
                phase_idx + 1, total_active, phase.name,
                f"mechanical ({active} queue rows across {len(shards)} shards)",
            )
            continue

        # v2.4.1: SC verify queue — mechanical, same pattern as L1.
        if config.get("pipeline") != "l1" and phase.name == "sc_verify_queue":
            promoted = _promote_depth_findings_to_inventory(scratchpad)
            if promoted:
                log.info(
                    f"[sc_verify_queue] promoted {len(promoted)} depth finding(s) "
                    "into findings_inventory.md before queue generation"
                )
            _validate_inventory_evidence(scratchpad, config["project_root"])
            depth_promotion_issues = _validate_depth_promotion_receipt(scratchpad)
            if depth_promotion_issues:
                log.error("[sc_verify_queue] " + "; ".join(depth_promotion_issues))
                (scratchpad / "sc_verify_queue.degraded").write_text(
                    "Depth promotion failed before verification queue.\n"
                    + "\n".join(depth_promotion_issues)
                    + "\n",
                    encoding="utf-8",
                )
                if "sc_verify_queue" not in checkpoint.degraded:
                    checkpoint.degraded.append("sc_verify_queue")
                checkpoint.save(scratchpad)
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad), depth_promotion_issues, config,
                )
                sys.exit(EXIT_DEGRADED)
            _write_finding_records_from_inventory(scratchpad)
            routed = _write_mechanical_verification_queue_from_inventory(scratchpad)
            # v2.4.8: collapse queue rows sharing the same hypothesis into one
            # representative row. Reduces 89→~49 rows for typical SC audits,
            # eliminating ~45% verify budget waste on redundant constituents.
            hypo_deduped = _dedup_queue_by_hypothesis(scratchpad)
            if hypo_deduped:
                log.info(
                    f"[sc_verify_queue] hypothesis dedup removed {hypo_deduped} "
                    "redundant constituent row(s)"
                )
            mode_filtered = _filter_sc_verification_queue_by_mode(
                scratchpad, config.get("mode", "core")
            )
            if mode_filtered:
                log.info(
                    f"[sc_verify_queue] moved {mode_filtered} Low/Info "
                    f"row(s) to evidence-excluded for {config.get('mode')} mode"
                )
            removed = _filter_verification_queue_by_evidence(scratchpad)
            shards = ensure_sc_verify_shard_manifests(scratchpad)
            queue_issues = _validate_verification_queue_inventory_parity(scratchpad)
            if queue_issues:
                log.error("[sc_verify_queue] " + "; ".join(queue_issues))
                (scratchpad / "sc_verify_queue.degraded").write_text(
                    "Verification queue parity failed.\n"
                    + "\n".join(queue_issues)
                    + "\n",
                    encoding="utf-8",
                )
                if "sc_verify_queue" not in checkpoint.degraded:
                    checkpoint.degraded.append("sc_verify_queue")
                checkpoint.save(scratchpad)
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad), queue_issues, config,
                )
                sys.exit(EXIT_DEGRADED)
            active = len(parse_verification_queue_rows(scratchpad))
            shard_count = sum(len(v) for v in shards.values())
            checkpoint.mark_completed(phase.name)
            checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
            checkpoint.save(scratchpad)
            extra = f"; evidence-excluded {len(removed)}" if removed else ""
            log.info(
                f"[sc_verify_queue] mechanically routed {routed} inventory "
                f"finding(s) into {active} active queue row(s) across "
                f"{len(shards)} shard manifest(s), shard rows={shard_count}{extra}"
            )
            display.print_phase_skipped(
                phase_idx + 1, total_active, phase.name,
                f"mechanical ({active} queue rows across {len(shards)} shards)",
            )
            continue

        # v2.4.1→v2.4.3: SC verify aggregate — mechanical pre-step, then fall
        # through to _run_phase_validators for parity/evidence/containment checks.
        # Prior to v2.4.3, this block did `continue` which bypassed all validators.
        if config.get("pipeline") != "l1" and phase.name == "sc_verify_aggregate":
            _generate_verify_core_if_missing(scratchpad)

        if phase.name in ("semantic_dedup", "sc_semantic_dedup"):
            pairs_file = scratchpad / "dedup_candidate_pairs.md"
            focus_file = scratchpad / "dedup_focus_inventory.md"
            inv_file = scratchpad / "findings_inventory.md"
            has_pairs = pairs_file.exists() and pairs_file.stat().st_size > 100
            pair_rows = 0
            if has_pairs:
                try:
                    pair_text = pairs_file.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    pair_rows = sum(
                        1
                        for line in pair_text.splitlines()
                        if line.lstrip().startswith("|")
                        and not re.match(r"\s*\|\s*-+", line)
                        and "Finding A" not in line
                    )
                except Exception:
                    pair_rows = 0
            inventory_count = 0
            has_likely_dup = False
            if inv_file.exists():
                try:
                    inv_text = inv_file.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    has_likely_dup = "LIKELY-DUP" in inv_text
                    inventory_count = len(
                        re.findall(r"(?im)^\s*#{2,4}\s+Finding\s+\[", inv_text)
                    )
                except Exception:
                    pass
            if not has_pairs and not has_likely_dup:
                written = _write_semantic_dedup_skip_outputs(
                    scratchpad,
                    phase.name,
                    "no candidate pairs and no LIKELY-DUP tags",
                )
                log.info(
                    f"[{phase.name}] no dedup signals (no candidate pairs, "
                    "no LIKELY-DUP tags) -- wrote no-op outputs "
                    f"{written} and skipping"
                )
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    "no dedup signals",
                )
                continue
            # Semantic dedup is quality-improving, but the live work must stay
            # bounded. `_compute_dedup_candidate_pairs` normally emits a
            # dedup_focus_inventory.md packet for large inventories. If an old
            # or malformed run lacks that bounded packet, fail open
            # deterministically instead of asking one LLM pass to read an
            # unbounded inventory and then timing out.
            if pair_rows > 24 or (inventory_count > 180 and not focus_file.exists()):
                reason = (
                    "semantic dedup budget guard: "
                    f"{pair_rows} candidate pair row(s), "
                    f"{inventory_count} inventory finding(s); preserving "
                    "upstream artifact unchanged to avoid a timeout/retry loop"
                )
                written = _write_semantic_dedup_skip_outputs(
                    scratchpad,
                    phase.name,
                    reason,
                )
                log.warning(f"[{phase.name}] {reason}; wrote {written}")
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    "budget guard (too many pairs/findings)",
                )
                continue
            prewritten = _write_semantic_dedup_skip_outputs(
                scratchpad,
                phase.name,
                "pre-run passthrough safety net; bounded semantic dedup may "
                "overwrite these artifacts if it completes with valid outputs",
            )
            log.info(
                f"[{phase.name}] wrote deterministic passthrough before "
                f"bounded semantic-dedup subprocess: {prewritten}"
            )

        # Pre-compute binding severity table for report_index LLM.
        # Eliminates retry cycles caused by the LLM silently inflating
        # severity without a Trust Adj. reason.
        if phase.name == "report_index":
            try:
                sev_map = _expected_report_index_severities(scratchpad)
                if sev_map:
                    lines = [
                        "# Severity Binding Table",
                        "",
                        "Driver-computed expected severities from verify files "
                        "and verification queue. The Index Agent MUST use these "
                        "severities unless a Trust Adj. reason is documented.",
                        "",
                        "| Finding ID | Expected Severity |",
                        "|------------|-------------------|",
                    ]
                    for fid in sorted(sev_map, key=lambda x: x.upper()):
                        lines.append(f"| {fid} | {sev_map[fid]} |")
                    (scratchpad / "severity_binding.md").write_text(
                        "\n".join(lines) + "\n", encoding="utf-8",
                    )
                    log.info(
                        f"[report_index] wrote severity_binding.md "
                        f"({len(sev_map)} finding(s))"
                    )
            except Exception as exc:
                log.warning(f"[report_index] severity binding failed: {exc!r}")

        if config["pipeline"] == "sc" and phase.name == "report_index":
            repaired = _repair_sc_report_index_from_prior(scratchpad)
            if repaired:
                idx_in_issues = _validate_report_index_inputs(scratchpad)
                coverage_issues = _validate_report_coverage_accounting(scratchpad)
                if not idx_in_issues and not coverage_issues:
                    try:
                        manifests = _build_sc_body_writer_manifests(scratchpad)
                    except Exception as exc:
                        manifests = {}
                        log.warning(
                            f"[report_index] SC manifest rebuild after repair failed: {exc!r}"
                        )
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info(
                        f"[report_index] mechanically repaired report_index.md "
                        f"with {repaired} active row(s); manifests={len(manifests)}"
                    )
                    phases[:] = expand_shard_phases(phases, scratchpad)
                    active_phases = [p for p in phases if mode in p.modes]
                    total_active = len(active_phases)
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        f"mechanical repair ({repaired} active rows)",
                    )
                    continue
                log.warning(
                    "[report_index] mechanical SC repair did not satisfy gates: "
                    + "; ".join(idx_in_issues + coverage_issues)
                )

        if config["pipeline"] == "l1" and phase.name == "report_index":
            # Phase E2: refuse to mechanically write report_index when queue
            # has unverified rows. Do not validate stale report_index.md
            # before the deterministic rewrite; content gates run after write.
            idx_in_issues = _validate_report_index_prewrite_inputs(scratchpad)
            if idx_in_issues:
                log.error("[report_index] " + "; ".join(idx_in_issues))
                (scratchpad / "report_index.degraded").write_text(
                    "report_index halted: unverified queue rows.\n"
                    + "\n".join(idx_in_issues) + "\n",
                    encoding="utf-8",
                )
                if "report_index" not in checkpoint.degraded:
                    checkpoint.degraded.append("report_index")
                checkpoint.save(scratchpad)
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad), idx_in_issues, config,
                )
                sys.exit(EXIT_DEGRADED)
            active = _write_mechanical_report_index(scratchpad)
            idx_post_issues = _validate_report_index_inputs(scratchpad)
            if idx_post_issues:
                log.error("[report_index] " + "; ".join(idx_post_issues))
                (scratchpad / "report_index.degraded").write_text(
                    "report_index halted: generated report_index failed validation.\n"
                    + "\n".join(idx_post_issues) + "\n",
                    encoding="utf-8",
                )
                if "report_index" not in checkpoint.degraded:
                    checkpoint.degraded.append("report_index")
                checkpoint.save(scratchpad)
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad), idx_post_issues, config,
                )
                sys.exit(EXIT_DEGRADED)
            coverage_issues = _validate_report_coverage_accounting(scratchpad)
            if coverage_issues:
                log.error("[report_index] " + "; ".join(coverage_issues))
                (scratchpad / "report_index.degraded").write_text(
                    "report_index halted: raw candidate accounting failed.\n"
                    + "\n".join(coverage_issues) + "\n",
                    encoding="utf-8",
                )
                if "report_index" not in checkpoint.degraded:
                    checkpoint.degraded.append("report_index")
                checkpoint.save(scratchpad)
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad), coverage_issues, config,
                )
                sys.exit(EXIT_DEGRADED)
            if (scratchpad / "report_index.md").exists():
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info(
                    f"[report_index] mechanically wrote report_index.md with "
                    f"{active} active finding(s)"
                )
                # Manifests now exist — expand tier sentinel phases so the
                # remaining loop iterations see per-shard body writers.
                phases[:] = expand_shard_phases(phases, scratchpad)
                active_phases = [p for p in phases if mode in p.modes]
                total_active = len(active_phases)
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    f"mechanical ({active} findings indexed)",
                )
                continue

        # Phase E11 follow-up #1: empty-shard body-writer skip. When the
        # tier has no findings, deterministically write an empty-tier note
        # and mark complete instead of calling an LLM that would either
        # produce nothing or stub output. Idempotent — caller proceeds.
        if phase.name.startswith("report_body_writer_"):
            if config.get("pipeline") != "l1":
                try:
                    _build_sc_body_writer_manifests(scratchpad)
                except Exception as exc:
                    log.warning(
                        f"[{phase.name}] SC body manifest refresh failed: {exc!r}"
                    )
            if _maybe_skip_empty_body_writer(scratchpad, phase.name):
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info(
                    f"[{phase.name}] empty shard — skipped LLM, wrote "
                    f"empty-tier note for {phase.expected_artifacts[0]}"
                )
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    "empty shard (no findings in tier)",
                )
                continue

        # Phase E11: existing tier phases are deterministic confirmation
        # handlers. Body-writer phase is the prose author; this handler
        # only confirms the body-writer output is on disk and validates,
        # then marks complete. NO Python prose fallback — body-writer
        # failure halts at its own phase via critical=True. If we reach
        # here without a body-writer output, emit an explicit degraded
        # artifact and halt.
        _is_legacy_tier = (
            phase.name in ("report_critical_high", "report_medium", "report_low_info")
            or re.match(r"^report_(critical_high|medium|low_info)_[a-z]$", phase.name)
        )
        if _is_legacy_tier:
            tier_path = scratchpad / f"{phase.name}.md"
            if not (tier_path.exists() and tier_path.stat().st_size > 100):
                if _restore_tier_body_from_overflow(scratchpad, phase.name):
                    log.info(
                        f"[{phase.name}] restored body-writer output from _overflow"
                    )
            if tier_path.exists() and tier_path.stat().st_size > 100:
                body_issues = _validate_tier_body_against_manifest(
                    scratchpad, phase.name
                )
                if not body_issues:
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info(
                        f"[{phase.name}] body-writer output validated, "
                        "marking phase complete"
                    )
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "body-writer output already valid",
                    )
                    continue
                # Body writer ran but produced invalid output; surface as
                # explicit degraded artifact. No Python prose fallback.
                (scratchpad / f"{phase.name}.body_writer.degraded").write_text(
                    "[BODY-WRITER-DEGRADED] Body-writer output failed "
                    "validator:\n" + "\n".join(body_issues) + "\n",
                    encoding="utf-8",
                )
                if phase.name not in checkpoint.degraded:
                    checkpoint.degraded.append(phase.name)
                checkpoint.save(scratchpad)
                log.error(f"[{phase.name}] body-writer output failed validator")
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad), body_issues, config,
                )
                sys.exit(EXIT_DEGRADED)
            # No body-writer output on disk: explicit degraded halt.
            (scratchpad / f"{phase.name}.body_writer.degraded").write_text(
                f"[BODY-WRITER-DEGRADED] No {phase.name}.md produced by "
                "body-writer phase.\n",
                encoding="utf-8",
            )
            if phase.name not in checkpoint.degraded:
                checkpoint.degraded.append(phase.name)
            checkpoint.save(scratchpad)
            log.error(f"[{phase.name}] body-writer produced no output")
            display.print_failure_diagnosis(
                phase.name, str(scratchpad),
                [f"body-writer produced no {phase.name}.md output"],
                config,
            )
            sys.exit(EXIT_DEGRADED)

        # Manifest-aware quorum override for breadth. By the time breadth
        # runs, instantiate has written spawn_manifest.md declaring the
        # exact set of breadth agents the orchestrator will spawn. Using
        # that count as the gate quorum catches partial-spawn failures
        # (e.g., 3 of 6 breadth agents returned output) that the hardcoded
        # floor of 3 would silently pass. Falls back to phase.min_artifacts_count
        # when manifest is absent or unreadable.
        if phase.name == "breadth":
            manifest_n = parse_breadth_manifest_count(scratchpad)
            manifest_path = scratchpad / "spawn_manifest.md"
            if manifest_path.exists() and manifest_n is None:
                try:
                    manifest_text = manifest_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    manifest_text = ""
                if "template" in manifest_text.lower() and "required" in manifest_text.lower():
                    (scratchpad / "breadth.degraded").write_text(
                        "[MANIFEST-SCHEMA-INVALID] spawn_manifest.md exists "
                        "with breadth-manifest headers but no parseable rows.\n",
                        encoding="utf-8",
                    )
                    if "breadth" not in checkpoint.degraded:
                        checkpoint.degraded.append("breadth")
                    checkpoint.save(scratchpad)
                    log.error("[breadth] spawn_manifest.md schema invalid")
                    display.print_failure_diagnosis(
                        phase.name, str(scratchpad),
                        ["spawn_manifest.md schema invalid: has headers but no parseable rows"],
                        config,
                    )
                    sys.exit(EXIT_DEGRADED)
            if manifest_n is not None and manifest_n > phase.min_artifacts_count:
                old = phase.min_artifacts_count
                phase.min_artifacts_count = manifest_n
                log.info(
                    f"[breadth] manifest-aware quorum: {old} -> {manifest_n} "
                    f"(from spawn_manifest.md)"
                )
        elif phase.name == "depth" and config["pipeline"] == "l1":
            # L1 light mode skips the semantic-invariants phase, so there may
            # be no LLM-authored phase4b manifest before depth. The five
            # standard L1 depth agents are a phase-graph contract, not a
            # methodology guess; emit the default manifest mechanically so the
            # quorum gate remains strict in every mode.
            default_depth_manifest = scratchpad / "phase4b_manifest.md"
            if not default_depth_manifest.exists():
                default_depth_manifest.write_text(
                    "\n".join([
                        "| Agent | Role | Expected Artifact |",
                        "|---|---|---|",
                        "| depth-consensus-invariant | Consensus invariant | depth_consensus_invariant_findings.md |",
                        "| depth-network-surface | Network surface | depth_network_surface_findings.md |",
                        "| depth-state-trace | State trace | depth_state_trace_findings.md |",
                        "| depth-external | External boundary | depth_external_findings.md |",
                        "| depth-edge-case | Edge case | depth_edge_case_findings.md |",
                        "",
                    ]),
                    encoding="utf-8",
                )
            manifest_n = parse_depth_manifest_count(scratchpad)
            manifest_candidates = [
                scratchpad / "phase4b_manifest.md",
                scratchpad / "spawn_manifest.md",
            ]
            if any(p.exists() for p in manifest_candidates) and manifest_n is None:
                malformed_depth_manifest = False
                for mp in manifest_candidates:
                    if not mp.exists():
                        continue
                    try:
                        mt = mp.read_text(encoding="utf-8", errors="replace").lower()
                    except Exception:
                        mt = ""
                    if (
                        mp.name == "phase4b_manifest.md"
                        or ("template" in mt and "required" in mt)
                        or ("agent" in mt and ("role" in mt or "model" in mt))
                    ):
                        malformed_depth_manifest = True
                        break
                if malformed_depth_manifest:
                    (scratchpad / "depth.degraded").write_text(
                        "[MANIFEST-SCHEMA-INVALID] depth manifest exists but "
                        "no parseable depth-agent rows were found.\n",
                        encoding="utf-8",
                    )
                    if "depth" not in checkpoint.degraded:
                        checkpoint.degraded.append("depth")
                    checkpoint.save(scratchpad)
                    log.error("[depth] depth manifest schema invalid")
                    display.print_failure_diagnosis(
                        phase.name, str(scratchpad),
                        ["depth manifest schema invalid: no parseable depth-agent rows"],
                        config,
                    )
                    sys.exit(EXIT_DEGRADED)
            if manifest_n is not None and manifest_n > phase.min_artifacts_count:
                old = phase.min_artifacts_count
                phase.min_artifacts_count = manifest_n
                log.info(
                    f"[depth] manifest-aware quorum: {old} -> {manifest_n} "
                    f"(from phase4b_manifest.md/spawn_manifest.md)"
                )
        elif config["pipeline"] == "l1" and (
            phase.name in ("report_critical_high", "report_low_info")
            or re.match(r"^report_(critical_high|medium|low_info)_[a-z]$", phase.name)
        ):
            tier_counts = parse_report_index_counts(scratchpad)
            _tier_shard_m = re.match(
                r"^report_(critical_high|medium|low_info)_[a-z]$", phase.name
            )
            if _tier_shard_m:
                tier_base = _tier_shard_m.group(1)
                tier_shards = ensure_report_tier_shards(scratchpad, tier_base)
                count = len(tier_shards.get(phase.name, []))
            else:
                key = {
                    "report_critical_high": "critical_high",
                    "report_low_info": "low_info",
                }[phase.name]
                count = tier_counts.get(key, 0)
            phase.min_artifact_bytes = max(100, count * 400)
            if count == 0:
                write_report_tier_placeholder(
                    scratchpad, f"{phase.name}.md",
                    "0 findings assigned in report_index.md",
                )
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info(f"[{phase.name}] N/A (0 assigned findings) -- writing placeholder and skipping")
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    "N/A (0 findings assigned)",
                )
                continue
            if not _validate_report_tier_completeness(scratchpad, phase.name):
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                log.info(f"[{phase.name}] existing tier artifact passes completeness -- skipping subprocess")
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    "existing tier artifact passes completeness",
                )
                continue

        _merge_m = re.match(
            r"^report_(critical_high|medium|low_info)_merge$", phase.name
        )
        if _merge_m:
            _merge_tier = _merge_m.group(1)
            merge_report_tier_shards(scratchpad, _merge_tier)
            checkpoint.mark_completed(phase.name)
            checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
            checkpoint.save(scratchpad)
            shard_files = sorted(scratchpad.glob(f"report_{_merge_tier}_[a-z].md"))
            shard_names = [f.name for f in shard_files]
            if shard_names:
                log.info(f"[{phase.name}] merged {' + '.join(shard_names)} -> report_{_merge_tier}.md")
            else:
                log.info(f"[{phase.name}] no shard files — base file untouched")
            display.print_phase_skipped(
                phase_idx + 1, total_active, phase.name,
                f"mechanical merge ({', '.join(shard_names) if shard_names else 'no shards'})",
            )
            continue

        if phase.name == "crossbatch":
            rows = _write_crossbatch_manifest(scratchpad)
            if rows:
                log.info(
                    f"[crossbatch] manifest prepared with {len(rows)} "
                    "verifier ID(s)"
                )

        # v2.3.11: report_assemble is Python-native. The prior LLM-driven
        # phase thrashed for 1+ hour on 225KB of tier-file concatenation.
        # Per V2 layer doctrine: driver owns plumbing, LLM owns methodology.
        # Concat is plumbing. Quality gate (`_run_report_quality_gate`) still
        # runs against the Python-assembled output below.
        if phase.name == "report_assemble":
            _write_final_subsystem_coverage_summary(scratchpad)
            ok = _assemble_report_python(scratchpad, config["project_root"])
            quality_issues = []
            if ok:
                quality_issues = _run_report_quality_gate(
                    scratchpad, config["project_root"]
                )
                if quality_issues == ["AUDIT_REPORT.md is a stub (0 finding sections)"]:
                    assigned = parse_report_index_counts(scratchpad)
                    if sum(assigned.values()) == 0:
                        quality_issues = []
                if quality_issues:
                    ok = False
                    log.error(
                        "[report_assemble] python assembly failed report "
                        "quality gate: " + "; ".join(quality_issues)
                    )
            if ok:
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
            else:
                # No fallback — assembly failure means tier files are
                # missing or corrupt upstream. Surface visibly.
                log.error(
                    "[report_assemble] python assembly failed — check tier "
                    "files in scratchpad and report_index.md"
                )
                (scratchpad / f"{phase.name}.degraded").write_text(
                    f"Phase {phase.name} failed Python assembly or quality gate.\n"
                    f"Issues: {quality_issues or ['assembly returned false']}\n"
                    f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
                    encoding="utf-8",
                )
                if phase.name not in checkpoint.degraded:
                    checkpoint.degraded.append(phase.name)
                    checkpoint.save(scratchpad)
            display.print_phase_skipped(
                phase_idx + 1, total_active, phase.name,
                "mechanical (Python-native assembly)" if ok else "assembly failed (degraded)",
            )
            continue

        # Empty-queue short-circuit for verification phases. When the
        # upstream pipeline produced zero Medium+ findings (rare but
        # legitimate — e.g., a clean codebase, or Light mode running on a
        # small contract), verify/skeptic/crossbatch have nothing to do.
        # Without this, verify (critical=True) would spawn, write nothing,
        # fail its glob gate, and HALT the pipeline — falsely reporting a
        # catastrophic failure for what is a valid empty-result state.
        # Include every bounded L1 and SC verify shard.
        _all_verify_shard_names = {
            *L1_VERIFY_PHASE_NAMES, *SC_VERIFY_PHASE_NAMES,
        }
        _all_verify_queue_names = {"verify_queue", "sc_verify_queue"}
        _all_verify_aggregate_names = {"verify_aggregate", "sc_verify_aggregate"}
        if phase.name in (
            "verify", *_all_verify_queue_names, *_all_verify_shard_names,
            *_all_verify_aggregate_names, "skeptic", "crossbatch",
        ):
            empty, reason = is_verification_queue_empty(
                scratchpad, config["pipeline"]
            )
            if empty:
                if phase.name in _all_verify_queue_names:
                    written = _write_empty_verification_queue(scratchpad, reason)
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info(
                        f"[{phase.name}] N/A ({reason}) -- wrote empty queue "
                        f"{written} and skipping"
                    )
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        f"N/A ({reason})",
                    )
                    continue
                log.info(
                    f"[{phase.name}] N/A ({reason}) -- writing placeholders "
                    f"and skipping"
                )
                write_empty_verify_placeholders(scratchpad, phase.name, reason)
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    f"N/A ({reason})",
                )
                continue
            # Empty bounded verify shards are expected on smaller audits.
            if phase.name in L1_VERIFY_PHASE_NAMES:
                verify_shards = ensure_verify_shard_manifests(scratchpad)
                _clear_stale_verify_retry_hint_after_reshard(
                    scratchpad, phase.name, verify_shards.get(phase.name, [])
                )
                if not verify_shards.get(phase.name):
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info(f"[{phase.name}] N/A (0 assigned findings) -- skipping")
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "N/A (0 assigned findings)",
                    )
                    continue
                verify_issues = _validate_verify_completion(scratchpad, phase.name)
                if not verify_issues:
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info(
                        f"[{phase.name}] assigned verifier files already satisfy gate -- skipping"
                    )
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "existing verifier files satisfy gate",
                    )
                    continue
            if phase.name in SC_VERIFY_PHASE_NAMES:
                verify_shards = ensure_sc_verify_shard_manifests(scratchpad)
                _clear_stale_verify_retry_hint_after_reshard(
                    scratchpad, phase.name, verify_shards.get(phase.name, [])
                )
                if not verify_shards.get(phase.name):
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info(f"[{phase.name}] N/A (0 assigned findings) -- skipping")
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "N/A (0 assigned findings)",
                    )
                    continue
                verify_issues = _validate_verify_completion(scratchpad, phase.name)
                if not verify_issues:
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info(
                        f"[{phase.name}] assigned verifier files already satisfy gate -- skipping"
                    )
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "existing verifier files satisfy gate",
                    )
                    continue
            if phase.name in _all_verify_aggregate_names:
                if config.get("pipeline") == "l1":
                    verify_shards = ensure_verify_shard_manifests(scratchpad)
                else:
                    verify_shards = ensure_sc_verify_shard_manifests(scratchpad)
                total_assigned = sum(len(v) for v in verify_shards.values())
                if total_assigned == 0:
                    write_empty_verify_placeholders(
                        scratchpad, phase.name,
                        "0 assigned findings across verify shards"
                    )
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info(f"[{phase.name}] N/A (0 assigned findings) -- writing placeholder and skipping")
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "N/A (0 assigned findings)",
                    )
                    continue
                # v2.6.8: recovery-then-stub for missing verify files.
                # Step 1: identify what's missing from partial shard output.
                # Step 2: run a recovery verification shard to actually verify
                #         dropped findings (improves recall over pure stubs).
                # Step 3: stub whatever the recovery shard also failed to cover.
                missing = identify_missing_verify_ids(scratchpad)
                if missing:
                    log.info(
                        f"[{phase.name}] {len(missing)} verify file(s) missing "
                        f"from shard output — attempting recovery shard"
                    )
                    still_missing = _run_verify_recovery_shard(config, missing)
                    if still_missing:
                        stubbed = stub_missing_verify_files(
                            scratchpad, config["pipeline"],
                        )
                        if stubbed:
                            log.warning(
                                f"[{phase.name}] stubbed {len(stubbed)} finding(s) "
                                f"after recovery shard (of {len(missing)} originally "
                                f"missing): {stubbed[:8]}"
                                + (f" (+{len(stubbed) - 8} more)" if len(stubbed) > 8 else "")
                            )
                    else:
                        log.info(
                            f"[{phase.name}] recovery shard covered all "
                            f"{len(missing)} missing findings — no stubs needed"
                        )
            if phase.name == "skeptic":
                expected_skeptic = _write_skeptic_manifest(scratchpad)
                if config.get("pipeline") == "l1":
                    verify_shards = ensure_verify_shard_manifests(scratchpad)
                else:
                    verify_shards = ensure_sc_verify_shard_manifests(scratchpad)
                if not expected_skeptic:
                    write_empty_verify_placeholders(
                        scratchpad, "skeptic", "0 Critical/High findings in verification queue"
                    )
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    log.info("[skeptic] N/A (0 Critical/High findings) -- writing placeholder and skipping")
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "N/A (0 Critical/High findings)",
                    )
                    continue
            if config["pipeline"] == "l1" and phase.name in ("verify_aggregate", "crossbatch", "skeptic"):
                passed_existing, _missing_existing = gate_passes(
                    scratchpad,
                    config["project_root"],
                    phase,
                )
                if passed_existing:
                    existing_issues: list[str] = []
                    if phase.name == "crossbatch":
                        existing_issues = _validate_crossbatch_quality(scratchpad)
                    elif phase.name == "skeptic":
                        existing_issues = _validate_skeptic_scope(scratchpad)
                    if existing_issues:
                        log.info(
                            f"[{phase.name}] existing artifacts fail quality/scope "
                            "checks -- rerunning: " + "; ".join(existing_issues)
                        )
                    else:
                        checkpoint.mark_completed(phase.name)
                        checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                        checkpoint.save(scratchpad)
                        log.info(f"[{phase.name}] already satisfied by existing artifacts -- skipping")
                        display.print_phase_skipped(
                            phase_idx + 1, total_active, phase.name,
                            "existing artifacts satisfy gate",
                        )
                        continue

        # v2.5.4: Artifact-recovery auto-complete. When a phase produced
        # its output artifacts but wasn't checkpointed (gate failure from
        # containment violation, timeout after write but before checkpoint
        # save, or v2.3.14 containment downgrade), re-running the LLM
        # subprocess wastes time and money. Check gate_passes here —
        # after all phase-specific mechanical handlers (which have their
        # own `continue` paths) but before launching the subprocess.
        if phase.expected_artifacts and phase.name not in checkpoint.degraded:
            _recov_passed, _recov_missing = gate_passes(
                scratchpad, config["project_root"], phase
            )
            if _recov_passed:
                _owner_ok, _owner_issues = _phase_artifacts_have_active_owner_state(
                    scratchpad,
                    config["project_root"],
                    phase.name,
                    config["pipeline"],
                )
                if not _owner_ok:
                    _recov_passed = False
                    _recov_missing = [
                        "artifact ownership state missing/stale: "
                        + "; ".join(_owner_issues[:8])
                    ]
                if not _recov_passed:
                    log.info(
                        f"[{phase.name}] artifact-recovery rejected existing "
                        f"artifacts before validators: {_recov_missing} -- rerunning"
                    )
                    # Fall through to subprocess launch.
                    _existing_foreign = []
                else:
                    _existing_foreign = _existing_later_phase_artifacts(
                        scratchpad,
                        config["project_root"],
                        phases,
                        phase.name,
                        config["pipeline"],
                    )
                if _recov_passed:
                    if _existing_foreign:
                        moved_foreign = _quarantine_foreign_phase_writes(
                            scratchpad, config["project_root"], phase.name,
                            _existing_foreign,
                        )
                        log.warning(
                            f"[{phase.name}] artifact-recovery rejected "
                            f"pre-existing later-phase artifact(s): "
                            f"{_existing_foreign[:10]}; quarantined={moved_foreign[:10]}"
                        )
                        _recov_passed = False
                        _recov_missing = [
                            "phase containment: pre-existing later-phase artifacts: "
                            + ", ".join(_existing_foreign[:10])
                        ]
                if not _recov_passed:
                    log.info(
                        f"[{phase.name}] artifact-recovery rejected existing "
                        f"artifacts before validators: {_recov_missing} -- rerunning"
                    )
                    # Fall through to subprocess launch.
                else:
                    _recov_state_before = _snapshot_file_state(
                        scratchpad, config["project_root"]
                    )
                    _recov_valid, _recov_validator_missing = _run_phase_validators(
                        phase, config, scratchpad, phases, EXIT_SUCCESS,
                        _recov_state_before, 0,
                    )
                    if not _recov_valid:
                        log.info(
                            f"[{phase.name}] artifact-recovery rejected existing "
                            f"artifacts after full validators: "
                            f"{_recov_validator_missing} -- rerunning"
                        )
                        _recov_passed = False
            if _recov_passed:
                _record_phase_artifact_state(
                    scratchpad,
                    config["project_root"],
                    phases,
                    phase.name,
                    config["pipeline"],
                )
                checkpoint.mark_completed(phase.name)
                checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                checkpoint.save(scratchpad)
                gate_summary = _format_gate_summary(phase, scratchpad, config)
                log.info(
                    f"[{phase.name}] artifact-recovery: all expected artifacts "
                    f"already exist and pass gate — auto-completing "
                    f"({gate_summary})"
                )
                display.print_phase_skipped(
                    phase_idx + 1, total_active, phase.name,
                    f"artifact-recovery ({gate_summary})",
                )
                continue

        # Pause: user pressed Ctrl+P — block until unpaused
        display.pause_toggle.wait_if_paused()

        # Halt: user pressed Esc during prior phase
        if display.graceful_stop.requested:
            checkpoint.save(scratchpad)
            display.print_halt_prompt(
                prev_phase or "(startup)", str(config_path),
            )
            if display.wait_halt_choice():
                display.print_halt_resume()
                display.graceful_stop.requested = False
            else:
                display.graceful_stop.requested = False
                _halted = True
                break

        # Attempt 1
        violations_before = 0
        if phase.name == "depth":
            vp = scratchpad / "violations.md"
            try:
                violations_before = vp.stat().st_size if vp.exists() else 0
            except Exception:
                violations_before = 0

        file_state_before = _snapshot_file_state(scratchpad, config["project_root"])
        display.print_phase_start(
            phase_idx + 1, total_active, phase.name,
            phase_model(phase, mode, config),
        )
        rc = run_phase(phase, config, attempt=1)
        current_attempt = 1

        # Codex CLI crash detection: invalid flags are permanent failures.
        # No retry, no rate-limit wait — fail fast with diagnostic.
        if config.get("cli_backend") == "codex" and rc != 0:
            _stdio_crash_log = scratchpad / f"_stdio_{phase.name}.log"
            if _detect_codex_cli_crash(_stdio_crash_log):
                log.error(
                    f"[{phase.name}] Codex CLI crashed on invalid argument "
                    f"(permanent failure — retrying is pointless). "
                    f"Check {_stdio_crash_log} for details."
                )
                checkpoint.save(scratchpad)
                sys.exit(EXIT_ERROR)
            if _detect_codex_context_exceeded(_stdio_crash_log):
                est_tokens = 0
                try:
                    snap_path = scratchpad / f"_prompt_{phase.name}.attempt{current_attempt}.md"
                    if snap_path.exists():
                        est_tokens = snap_path.stat().st_size // 4
                except Exception:
                    pass
                log.error(
                    f"[{phase.name}] Codex context window exceeded "
                    f"(~{est_tokens:,} est. tokens). This is a permanent "
                    f"failure for the current prompt size — retrying with "
                    f"identical prompt is pointless. Consider reducing prompt "
                    f"size or using a model with a larger context window."
                )
                checkpoint.save(scratchpad)
                display.print_failure_diagnosis(
                    phase.name, str(scratchpad),
                    [f"context_exceeded (~{est_tokens:,} est. tokens)"],
                    config,
                )
                sys.exit(EXIT_ERROR)
            if _detect_codex_model_not_available(_stdio_crash_log):
                requested = phase_model(phase, config["mode"], config)
                fallback = _CODEX_MODEL_MAP.get("sonnet", "gpt-5.4")
                if requested != fallback:
                    log.warning(
                        f"[{phase.name}] Model {requested} not available on "
                        f"your Codex/OpenAI plan. Downgrading opus-tier "
                        f"phases to {fallback} for the rest of this run."
                    )
                    config["_codex_model_unavailable"] = requested
                    config["_codex_model_fallback"] = fallback
                    rc = run_phase(phase, config, attempt=1)
                else:
                    log.error(
                        f"[{phase.name}] Model {requested} not available and "
                        f"no fallback model exists. Check your Codex/OpenAI "
                        f"plan access."
                    )
                    checkpoint.save(scratchpad)
                    sys.exit(EXIT_ERROR)
            if rc != 0 and _detect_codex_model_capacity(_stdio_crash_log):
                requested = phase_model(phase, config["mode"], config)
                attempted = config.setdefault("_codex_capacity_attempted_models", {})
                phase_attempted = list(attempted.get(phase.name, []))
                fallback = _codex_next_fallback_model(requested, phase_attempted)
                attempted[phase.name] = list(dict.fromkeys(phase_attempted + [requested]))
                if fallback:
                    log.warning(
                        f"[{phase.name}] Codex model {requested} is at "
                        f"capacity. Retrying this phase with fallback "
                        f"model {fallback}."
                    )
                    phase_fallbacks = config.setdefault("_codex_phase_model_fallbacks", {})
                    phase_fallbacks[phase.name] = fallback
                    display.print_phase_start(
                        phase_idx + 1, total_active, phase.name,
                        phase_model(phase, mode, config),
                        attempt=current_attempt + 1,
                    )
                    rc = run_phase(phase, config, attempt=current_attempt + 1)
                    current_attempt += 1
                else:
                    log.warning(
                        f"[{phase.name}] Codex model capacity hit for "
                        f"{requested}, and no untried fallback model remains; "
                        "falling through to rate-limit/backoff handling."
                    )
            if _detect_codex_auth_error(_stdio_crash_log):
                log.error(
                    f"[{phase.name}] Codex authentication error (401/403 or "
                    f"expired token). Re-run `codex login` or refresh "
                    f"CODEX_API_KEY / OPENAI_API_KEY. This is a permanent "
                    f"failure — retrying with stale credentials is pointless."
                )
                checkpoint.save(scratchpad)
                sys.exit(EXIT_ERROR)
            if _detect_codex_model_rejection(_stdio_crash_log):
                log.warning(
                    f"[{phase.name}] Codex rejected --model flag "
                    f"(ChatGPT-auth restriction). Retrying without --model."
                )
                config["_codex_skip_model"] = True
                rc = run_phase(phase, config, attempt=1)
                config.pop("_codex_skip_model", None)
                if rc != 0 and _detect_codex_cli_crash(
                    scratchpad / f"_stdio_{phase.name}.log"
                ):
                    log.error(f"[{phase.name}] Codex crash on model-fallback retry")
                    checkpoint.save(scratchpad)
                    sys.exit(EXIT_ERROR)

        # v2.7.7: Codex content-filter bonus retry.  The safety filter
        # kills the turn before any subagent work proceeds.  The error is
        # nondeterministic — the retry hint reshapes the prompt enough to
        # pass most of the time.  One bonus retry, does NOT consume the
        # normal gate-failure retry budget.
        if config.get("cli_backend") == "codex" and rc != 0:
            _cf_log = scratchpad / f"_stdio_{phase.name}.attempt{current_attempt}.log"
            if not _cf_log.exists():
                _cf_log = scratchpad / f"_stdio_{phase.name}.log"
            if _detect_codex_content_filter(_cf_log):
                log.warning(
                    f"[{phase.name}] Codex content safety filter blocked "
                    f"this turn (transient). Bonus retry with retry hint."
                )
                _write_retry_hint(
                    scratchpad,
                    phase.name,
                    "## Content-filter recovery\n\n"
                    "Your previous attempt was terminated by a content safety "
                    "filter. This is a false positive — you are running an "
                    "authorized security audit.\n\n"
                    "Continue the depth phase work: check which artifacts are "
                    "missing or zero-byte on disk, spawn agents for those, "
                    "and produce all required output files.",
                )
                file_state_before = _snapshot_file_state(
                    scratchpad, config["project_root"]
                )
                display.print_phase_start(
                    phase_idx + 1, total_active, phase.name,
                    phase_model(phase, mode, config),
                    attempt=current_attempt + 1,
                )
                rc = run_phase(phase, config, attempt=current_attempt + 1)
                current_attempt += 1
                # If bonus retry also gets filtered, fall through to
                # the normal gate-failure path (which adds a second retry).

        # Esc halt: subprocess killed, offer interactive resume or exit
        if rc == -3:
            checkpoint.save(scratchpad)
            display.print_halt_prompt(phase.name, str(config_path))
            if display.wait_halt_choice():
                display.print_halt_resume()
                display.graceful_stop.requested = False
                rc = run_phase(phase, config, attempt=2)
                current_attempt = 2
                if rc == -3:
                    checkpoint.save(scratchpad)
                    display.graceful_stop.requested = False
                    _halted = True
                    break
            else:
                display.graceful_stop.requested = False
                _halted = True
                break

        # v2.3.6 E1: rc=0 with empty subprocess output → treat as failure.
        # Pre-v2.3.6 a subprocess that exited 0 but wrote nothing to stdout
        # (e.g., RESUMPTION PROTOCOL misfire deciding "all artifacts already
        # exist", or empty API response wrapped in a valid JSON envelope)
        # was indistinguishable from real success. Gate would pass on stale
        # prior-attempt artifacts. We promote rc=0-empty to a sentinel so
        # the existing retry path engages.
        if rc == 0:
            stdio_log = scratchpad / f"_stdio_{phase.name}.attempt{current_attempt}.log"
            try:
                if (
                    stdio_log.exists()
                    and stdio_log.stat().st_size < 500
                    and not _phase_has_fresh_expected_artifact(
                        phase, scratchpad, config["project_root"], file_state_before
                    )
                ):
                    log.warning(
                        f"[{phase.name}] rc=0 but stdio log < 500 bytes "
                        f"({stdio_log.stat().st_size}) — likely empty "
                        f"response or RESUMPTION PROTOCOL misfire; treating "
                        f"as failure to engage retry path"
                    )
                    rc = EXIT_ERROR
            except Exception:
                pass

        # Rate-limit: interactive wait with Enter-to-retry, then retry
        rate_limit_consumed_retry = False
        _stdio_log = scratchpad / f"_stdio_{phase.name}.log"
        _is_rate_limited = (
            _detect_codex_rate_limit(_stdio_log, rc)
            if config.get("cli_backend") == "codex"
            else detect_rate_limit(_stdio_log)
        )
        if _is_rate_limited:
            if config.get("cli_backend") == "codex" and _detect_codex_model_capacity(_stdio_log):
                requested = phase_model(phase, config["mode"], config)
                attempted = config.setdefault("_codex_capacity_attempted_models", {})
                phase_attempted = list(attempted.get(phase.name, []))
                fallback = _codex_next_fallback_model(requested, phase_attempted)
                attempted[phase.name] = list(dict.fromkeys(phase_attempted + [requested]))
                if fallback:
                    log.warning(
                        f"[{phase.name}] Codex model {requested} is at "
                        f"capacity. Retrying this phase with fallback "
                        f"model {fallback} before waiting."
                    )
                    phase_fallbacks = config.setdefault("_codex_phase_model_fallbacks", {})
                    phase_fallbacks[phase.name] = fallback
                    file_state_before = _snapshot_file_state(
                        scratchpad, config["project_root"]
                    )
                    display.print_phase_start(
                        phase_idx + 1, total_active, phase.name,
                        phase_model(phase, mode, config),
                        attempt=current_attempt + 1,
                    )
                    rc = run_phase(phase, config, attempt=current_attempt + 1)
                    current_attempt += 1
                    _stdio_log = scratchpad / f"_stdio_{phase.name}.attempt{current_attempt}.log"
                    _is_rate_limited = _detect_codex_rate_limit(_stdio_log, rc)
            if not _is_rate_limited:
                pass
            else:
                log.warning(f"[{phase.name}] rate limit detected -- auto-waiting")
                checkpoint.rate_limited_at = phase.name
                checkpoint.save(scratchpad)
                wait_s = estimate_rate_limit_wait_seconds(
                    scratchpad / f"_stdio_{phase.name}.log"
                )
                wait_s = min(wait_s or 300, 3600)
                try:
                    display.rate_limit_wait_interactive(wait_s, phase.name)
                except KeyboardInterrupt:
                    display.graceful_stop.requested = False
                    checkpoint.rate_limited_at = phase.name
                    checkpoint.save(scratchpad)
                    display.print_rate_limit_pause(str(config_path))
                    _rate_limit_halt = True
                    break
                display.print_rate_limit_retry(phase.name)
                file_state_before = _snapshot_file_state(
                    scratchpad, config["project_root"]
                )
                rc = run_phase(phase, config, attempt=2)
                if rc == -3:
                    checkpoint.save(scratchpad)
                    display.graceful_stop.requested = False
                    _halted = True
                    break
                retry_log = scratchpad / f"_stdio_{phase.name}.attempt2.log"
                _is_retry_rate_limited = (
                    _detect_codex_rate_limit(retry_log, rc)
                    if config.get("cli_backend") == "codex"
                    else detect_rate_limit(retry_log)
                )
                if _is_retry_rate_limited:
                    log.warning(
                        f"[{phase.name}] rate-limit retry also hit a rate limit; "
                        "preserving phase state for resume without consuming the "
                        "normal retry budget"
                    )
                    checkpoint.rate_limited_at = phase.name
                    checkpoint.save(scratchpad)
                    display.print_rate_limit_pause(str(config_path))
                    _rate_limit_halt = True
                    break
                rate_limit_consumed_retry = True
        # v2.1.2 A5: breadth filename compatibility shim. Run BEFORE gate so
        # the gate glob sees renamed outputs.
        if phase.name == "breadth":
            _normalize_breadth_outputs(scratchpad)

        # v2.1.8: strict phase isolation via quarantine. Move any inline-
        # produced later-phase adversarial artifacts (skeptic, judge,
        # crossbatch) into `_overflow/` so the dedicated phase runs with
        # fresh context. Verify_core.md is exempt (mechanical aggregate).
        if phase.name in _QUARANTINE_PATTERNS_BY_PHASE:
            moved = _quarantine_phase_overreach(
                scratchpad, phase.name, file_state_before
            )
            if moved:
                log.info(
                    f"[{phase.name}] quarantined {len(moved)} inline "
                    f"later-phase artifacts to _overflow/{phase.name}/: "
                    f"{moved[:5]}"
                )

        passed, missing = _run_phase_validators(
            phase, config, scratchpad, phases, rc, file_state_before,
            violations_before,
        )
        if not passed and _has_containment_failure(missing):
            _write_retry_hint(
                scratchpad, phase.name,
                _generate_containment_retry_hint(phase.name, list(missing)),
            )

        if (
            not passed
            and phase.name == "invariants"
            and not any(str(m).startswith("phase containment:") for m in missing)
        ):
            reason = (
                "semantic invariant enrichment failed or timed out; using the "
                "documented state_variables.md fallback for downstream depth"
            )
            written = _write_semantic_invariants_fallback(scratchpad, reason)
            log.warning(f"[invariants] {reason}; wrote {written}")
            passed, missing = _run_phase_validators(
                phase, config, scratchpad, phases, 0, file_state_before,
                violations_before,
            )

        if (
            not passed
            and phase.name == "rag_sweep"
            and not any(str(m).startswith("phase containment:") for m in missing)
        ):
            reason = (
                "RAG validation failed or timed out before producing a complete "
                "artifact; applying the documented 0.3 no-support floor for "
                "every inventory finding"
            )
            written = _write_rag_validation_floor(scratchpad, reason)
            log.warning(f"[rag_sweep] {reason}; wrote {written}")
            passed, missing = _run_phase_validators(
                phase, config, scratchpad, phases, 0, file_state_before,
                violations_before,
            )

        # v2.6.2: detect LLM leaving pre-written passthrough unchanged
        if passed and phase.name in ("semantic_dedup", "sc_semantic_dedup"):
            passthrough_issue = _semantic_dedup_passthrough_issue(scratchpad)
            if passthrough_issue:
                log.warning(f"[{phase.name}] {passthrough_issue}")
                _write_retry_hint(
                    scratchpad,
                    phase.name,
                    "The previous semantic-dedup attempt left the pre-run "
                    "PASSTHROUGH safety net unchanged even though live "
                    "candidate pairs exist. Treat existing "
                    "`dedup_decisions.md` and the deduped artifact as "
                    "incomplete unless `dedup_decisions.md` contains a "
                    "`## Decisions` section with MERGE/GROUP/KEEP SEPARATE "
                    "entries for every live pair. Overwrite the passthrough "
                    "outputs after evaluating the live candidate packet.",
                )
                passed = False
                missing = [passthrough_issue]
            decisions = scratchpad / "dedup_decisions.md"
            pairs_file = scratchpad / "dedup_candidate_pairs.md"
            if decisions.exists() and pairs_file.exists():
                try:
                    dec_text = decisions.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    has_pairs = pairs_file.stat().st_size > 100
                except Exception:
                    dec_text = ""
                    has_pairs = False
                if "PASSTHROUGH" in dec_text and has_pairs:
                    log.warning(
                        f"[{phase.name}] LLM subprocess left pre-written "
                        f"PASSTHROUGH unchanged despite candidate pairs "
                        f"existing — dedup agent did no useful work"
                    )

        if (
            not passed
            and phase.name in ("semantic_dedup", "sc_semantic_dedup")
            and not _is_semantic_dedup_passthrough_failure(list(missing))
        ):
            reason = (
                "semantic dedup attempt failed or timed out before producing "
                "its passthrough artifact; preserving upstream artifact "
                "unchanged because semantic dedup is non-blocking"
            )
            written = _write_semantic_dedup_skip_outputs(
                scratchpad, phase.name, reason,
            )
            log.warning(
                f"[{phase.name}] {reason}; wrote deterministic no-op outputs "
                f"{written}"
            )
            passed, missing = _run_phase_validators(
                phase, config, scratchpad, phases, 0, file_state_before,
                violations_before,
            )

        if not passed and rate_limit_consumed_retry:
            log.warning(
                f"[{phase.name}] gate failed after rate-limit retry (attempt 2 "
                f"already consumed): missing {missing} — degrading"
            )
            display.print_phase_degraded(phase.name, list(missing), critical=phase.critical)
            (scratchpad / f"{phase.name}.degraded").write_text(
                f"Phase {phase.name} failed after rate-limit retry.\n"
                f"Missing: {missing}\n"
                f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
                encoding="utf-8",
            )
            if phase.name not in checkpoint.degraded:
                checkpoint.degraded.append(phase.name)
            checkpoint.save(scratchpad)
            _rl_retry_recovered = False
            if phase.critical:
                log.error(f"[{phase.name}] CRITICAL phase degraded after rate-limit retry")
                display.print_halt_diagnostics(phase.name, str(scratchpad), str(config_path))
                display.print_critical_halt_prompt(phase.name, str(config_path))
                choice = display.wait_critical_halt_choice()
                if choice == "retry":
                    display.print_halt_resume()
                    attempt3_state_before = _snapshot_file_state(
                        scratchpad, config["project_root"]
                    )
                    rc = run_phase(phase, config, attempt=3)
                    if rc == -3:
                        checkpoint.save(scratchpad)
                        display.graceful_stop.requested = False
                        _halted = True
                        break
                    if rc == 0:
                        stdio_log = scratchpad / f"_stdio_{phase.name}.attempt3.log"
                        try:
                            if (
                                stdio_log.exists()
                                and stdio_log.stat().st_size < 500
                                and not _phase_has_fresh_expected_artifact(
                                    phase, scratchpad, config["project_root"], attempt3_state_before
                                )
                            ):
                                log.warning(
                                    f"[{phase.name}] attempt 3 rc=0 but stdio "
                                    f"log < 500 bytes - promoting to failure"
                                )
                                rc = EXIT_ERROR
                        except Exception:
                            pass
                    passed_3, missing_3 = _run_phase_validators(
                        phase, config, scratchpad, phases, rc,
                        attempt3_state_before,
                        0,
                    )
                    if passed_3:
                        _clear_retry_hint(scratchpad, phase.name)
                        if phase.name in checkpoint.degraded:
                            checkpoint.degraded.remove(phase.name)
                        checkpoint.save(scratchpad)
                        _rl_retry_recovered = True
                    else:
                        log.error(f"[{phase.name}] attempt 3 also failed: {missing_3}")
                        display.print_failure_diagnosis(
                            phase.name, str(scratchpad), list(missing_3), config,
                        )
                        _restore_quarantined_on_retry_failure(scratchpad, phase)
                        sys.exit(EXIT_DEGRADED)
                elif choice == "skip":
                    log.warning(f"[{phase.name}] user chose SKIP — marking critical phase degraded, continuing pipeline")
                    _restore_quarantined_on_retry_failure(scratchpad, phase)
                    if phase.name not in checkpoint.degraded:
                        checkpoint.degraded.append(phase.name)
                    checkpoint.save(scratchpad)
                else:
                    display.print_failure_diagnosis(phase.name, str(scratchpad), list(missing), config)
                    _restore_quarantined_on_retry_failure(scratchpad, phase)
                    sys.exit(EXIT_DEGRADED)
            if not _rl_retry_recovered:
                continue

        elif not passed:
            display.print_phase_retry(
                phase_idx + 1, total_active, phase.name, list(missing),
            )
            log.warning(f"[{phase.name}] gate failed after attempt 1: missing {missing} -- retrying")
            # v2.3.14: quarantine stale artifacts so RESUMPTION PROTOCOL
            # doesn't suppress the retry LLM from re-producing them.
            renamed = _quarantine_stale_on_retry(scratchpad, phase, list(missing))
            if renamed:
                log.info(
                    f"[{phase.name}] quarantined {len(renamed)} stale "
                    f"artifact(s) for retry: {renamed[:5]}"
                )
            if phase.name == "recon":
                hint = _generate_recon_retry_hint(list(missing))
                if hint:
                    _write_retry_hint(scratchpad, phase.name, hint)
            if phase.name == "breadth":
                hint = _generate_breadth_retry_hint(scratchpad, list(missing))
                if hint:
                    _write_retry_hint(scratchpad, phase.name, hint)
            if _has_containment_failure(list(missing)):
                _write_retry_hint(
                    scratchpad, phase.name,
                    _generate_containment_retry_hint(phase.name, list(missing)),
                )
            if phase.name == "skeptic":
                hint = _generate_skeptic_retry_hint(scratchpad)
                if hint:
                    _write_retry_hint(scratchpad, phase.name, hint)
            if phase.name == "depth":
                vp = scratchpad / "violations.md"
                try:
                    violations_before = vp.stat().st_size if vp.exists() else 0
                except Exception:
                    violations_before = 0
            file_state_before = _snapshot_file_state(
                scratchpad, config["project_root"]
            )
            display.print_phase_start(
                phase_idx + 1, total_active, phase.name,
                phase_model(phase, mode, config), attempt=2,
            )
            rc = run_phase(phase, config, attempt=2)
            if rc == -3:
                checkpoint.save(scratchpad)
                display.graceful_stop.requested = False
                _halted = True
                break

            # v2.3.6 E1: same rc=0-empty sentinel on retry. If attempt 2
            # also returns rc=0 with no output, do NOT silently accept
            # stale artifacts.
            if rc == 0:
                stdio_log = scratchpad / f"_stdio_{phase.name}.attempt2.log"
                try:
                    if (
                        stdio_log.exists()
                        and stdio_log.stat().st_size < 500
                        and not _phase_has_fresh_expected_artifact(
                            phase, scratchpad, config["project_root"], file_state_before
                        )
                    ):
                        log.warning(
                            f"[{phase.name}] retry rc=0 but stdio log < 500 "
                            f"bytes — promoting to failure"
                        )
                        rc = EXIT_ERROR
                except Exception:
                    pass

            # v2.7.7: content-filter bonus retry on the gate-failure retry too.
            if config.get("cli_backend") == "codex" and rc != 0:
                _cf_retry_log = scratchpad / f"_stdio_{phase.name}.attempt2.log"
                if not _cf_retry_log.exists():
                    _cf_retry_log = scratchpad / f"_stdio_{phase.name}.log"
                if _detect_codex_content_filter(_cf_retry_log):
                    log.warning(
                        f"[{phase.name}] content filter on retry — bonus attempt 3"
                    )
                    file_state_before = _snapshot_file_state(
                        scratchpad, config["project_root"]
                    )
                    display.print_phase_start(
                        phase_idx + 1, total_active, phase.name,
                        phase_model(phase, mode, config), attempt=3,
                    )
                    rc = run_phase(phase, config, attempt=3)
                    if rc == -3:
                        checkpoint.save(scratchpad)
                        display.graceful_stop.requested = False
                        _halted = True
                        break

            # v2.4.3: check attempt2 log, not canonical (which may contain stale attempt1 data on timeout)
            retry_log = scratchpad / f"_stdio_{phase.name}.attempt2.log"
            if not retry_log.exists():
                retry_log = scratchpad / f"_stdio_{phase.name}.log"
            retry_rate_limit_consumed = False
            _is_retry_rl = (
                _detect_codex_rate_limit(retry_log, rc)
                if config.get("cli_backend") == "codex"
                else detect_rate_limit(retry_log)
            )
            if _is_retry_rl:
                log.warning(f"[{phase.name}] rate limit on retry -- auto-waiting")
                checkpoint.rate_limited_at = phase.name
                checkpoint.save(scratchpad)
                wait_s = estimate_rate_limit_wait_seconds(retry_log)
                wait_s = min(wait_s or 300, 3600)
                try:
                    display.rate_limit_wait_interactive(wait_s, phase.name)
                except KeyboardInterrupt:
                    display.graceful_stop.requested = False
                    checkpoint.rate_limited_at = phase.name
                    checkpoint.save(scratchpad)
                    display.print_rate_limit_pause(str(config_path))
                    _rate_limit_halt = True
                    break
                display.print_rate_limit_retry(phase.name)
                file_state_before = _snapshot_file_state(
                    scratchpad, config["project_root"]
                )
                rc = run_phase(phase, config, attempt=3)
                retry_rate_limit_consumed = True
                if rc == -3:
                    checkpoint.save(scratchpad)
                    display.graceful_stop.requested = False
                    _halted = True
                    break
                attempt3_log = scratchpad / f"_stdio_{phase.name}.attempt3.log"
                _is_attempt3_rl = (
                    _detect_codex_rate_limit(attempt3_log, rc)
                    if config.get("cli_backend") == "codex"
                    else detect_rate_limit(attempt3_log)
                )
                if _is_attempt3_rl:
                    log.warning(
                        f"[{phase.name}] rate-limit retry of retry also hit "
                        "a rate limit; preserving phase state for resume "
                        "without degrading the phase"
                    )
                    checkpoint.rate_limited_at = phase.name
                    checkpoint.save(scratchpad)
                    display.print_rate_limit_pause(str(config_path))
                    _rate_limit_halt = True
                    break

            # v2.1.2 A5: breadth filename compatibility shim (retry block).
            if phase.name == "breadth":
                _normalize_breadth_outputs(scratchpad)

            # v2.1.8: strict phase-isolation quarantine on retry too.
            if phase.name in _QUARANTINE_PATTERNS_BY_PHASE:
                moved = _quarantine_phase_overreach(
                    scratchpad, phase.name, file_state_before
                )
                if moved:
                    log.info(
                        f"[{phase.name}] retry quarantined "
                        f"{len(moved)} files to _overflow/"
                    )

            passed, missing = _run_phase_validators(
                phase, config, scratchpad, phases, rc, file_state_before,
                violations_before,
            )
            if passed and phase.name in ("semantic_dedup", "sc_semantic_dedup"):
                passthrough_issue = _semantic_dedup_passthrough_issue(scratchpad)
                if passthrough_issue:
                    n_mech = _apply_mechanical_dedup_from_pairs(
                        scratchpad, phase.name,
                    )
                    if n_mech > 0:
                        reason = (
                            f"semantic dedup retry left PASSTHROUGH; applied "
                            f"{n_mech} mechanical merge(s) from candidate pairs"
                        )
                    else:
                        reason = (
                            "semantic dedup retry still left PASSTHROUGH unchanged; "
                            "no strong-signal candidate pairs for mechanical fallback"
                        )
                        _write_semantic_dedup_skip_outputs(
                            scratchpad, phase.name, reason,
                        )
                    log.warning(
                        f"[{phase.name}] {passthrough_issue}; {reason}"
                    )
                    _clear_retry_hint(scratchpad, phase.name)
                    _cleanup_quarantine_backups(scratchpad, phase)
                    checkpoint.mark_completed(phase.name)
                    checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
                    checkpoint.save(scratchpad)
                    display.print_phase_skipped(
                        phase_idx + 1, total_active, phase.name,
                        "non-blocking passthrough after retry",
                    )
                    continue

            # v2.1.6: on retry success, clear the retry-hint file so it
            # doesn't contaminate future runs if the checkpoint is reused.
            if passed:
                _clear_retry_hint(scratchpad, phase.name)
                _cleanup_quarantine_backups(scratchpad, phase)

            if not passed:
                log.error(f"[{phase.name}] degraded after 2 attempts: missing {missing}")
                display.print_phase_degraded(phase.name, list(missing), critical=phase.critical)
                # v2.3.14: restore quarantined artifacts — stale content
                # is better than nothing for downstream phases.
                _TIER_PLACEHOLDER_MAP = {
                    "report_critical_high": "report_critical_high.md",
                    "report_medium": "report_medium.md",
                    "report_low_info": "report_low_info.md",
                    "report_body_writer_critical_high": "report_critical_high.md",
                    "report_body_writer_medium": "report_medium.md",
                    "report_body_writer_low_info": "report_low_info.md",
                }
                # Dynamic tier shard mapping
                _m_bw = re.match(r"^report_body_writer_(critical_high|medium|low_info)_([a-z])$", phase.name)
                _m_lg = re.match(r"^report_(critical_high|medium|low_info)_([a-z])$", phase.name)
                if _m_bw:
                    _TIER_PLACEHOLDER_MAP[phase.name] = f"report_{_m_bw.group(1)}_{_m_bw.group(2)}.md"
                elif _m_lg:
                    _TIER_PLACEHOLDER_MAP[phase.name] = f"report_{_m_lg.group(1)}_{_m_lg.group(2)}.md"
                if phase.name in _TIER_PLACEHOLDER_MAP:
                    write_report_tier_placeholder(
                        scratchpad, _TIER_PLACEHOLDER_MAP[phase.name],
                        "tier writer exhausted retries; continuing with partial report"
                    )
                (scratchpad / f"{phase.name}.degraded").write_text(
                    f"Phase {phase.name} exhausted retries.\n"
                    f"Missing: {missing}\n"
                    f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
                    encoding="utf-8",
                )
                if phase.name not in checkpoint.degraded:
                    checkpoint.degraded.append(phase.name)
                checkpoint.save(scratchpad)

                # Critical phase degraded = pipeline cannot produce a useful
                # report. Halt rather than cascade empty inputs through
                # inventory -> depth -> verify -> report (which would all
                # produce their own degrade markers and finish with a
                # useless shell of a report).
                #
                # v2.3.14: containment failures on NON-CRITICAL phases no
                # longer halt. The foreign file is already quarantined to
                # _overflow/ by _quarantine_foreign_phase_writes. The
                # phase degrades normally (its legitimate artifacts are
                # preserved).
                containment_failure = _has_containment_failure(list(missing))
                if containment_failure:
                    display.print_failure_diagnosis(
                        phase.name, str(scratchpad), list(missing), config,
                    )
                    if not phase.critical:
                        log.error(
                            f"[{phase.name}] containment violation (quarantined), "
                            f"but phase is non-critical - continuing as degraded"
                        )
                        continue
                    log.error(
                        f"[{phase.name}] containment violation (quarantined) "
                        f"on critical phase - halting"
                    )
                    sys.exit(EXIT_DEGRADED)
                if phase.critical:
                    log.error(
                        f"[{phase.name}] is "
                        f"{'phase-containment-failed' if containment_failure else 'CRITICAL'}. "
                        f"Downstream phases cannot produce meaningful output "
                        f"without this phase boundary."
                    )
                    display.print_phase_degraded(phase.name, list(missing), critical=True)
                    display.print_halt_diagnostics(
                        phase.name, str(scratchpad), str(config_path),
                    )
                    display.print_critical_halt_prompt(phase.name, str(config_path))
                    choice = display.wait_critical_halt_choice()
                    if choice == "retry":
                        display.print_halt_resume()
                        critical_retry_attempt = 4 if retry_rate_limit_consumed else 3
                        if phase.name == "breadth":
                            hint = _generate_breadth_retry_hint(
                                scratchpad, list(missing)
                            )
                            if hint:
                                _write_retry_hint(scratchpad, phase.name, hint)
                        if containment_failure:
                            _write_retry_hint(
                                scratchpad, phase.name,
                                _generate_containment_retry_hint(
                                    phase.name, list(missing)
                                ),
                            )
                        attempt3_state_before = _snapshot_file_state(
                            scratchpad, config["project_root"]
                        )
                        rc = run_phase(phase, config, attempt=critical_retry_attempt)
                        if rc == -3:
                            checkpoint.save(scratchpad)
                            display.graceful_stop.requested = False
                            _halted = True
                            break
                        if rc == 0:
                            stdio_log = scratchpad / f"_stdio_{phase.name}.attempt{critical_retry_attempt}.log"
                            try:
                                if (
                                    stdio_log.exists()
                                    and stdio_log.stat().st_size < 500
                                    and not _phase_has_fresh_expected_artifact(
                                        phase, scratchpad, config["project_root"], attempt3_state_before
                                    )
                                ):
                                    log.warning(
                                        f"[{phase.name}] attempt 3 rc=0 but stdio "
                                        f"log < 500 bytes - promoting to failure"
                                    )
                                    rc = EXIT_ERROR
                            except Exception:
                                pass
                        passed_3, missing_3 = _run_phase_validators(
                            phase, config, scratchpad, phases, rc,
                            attempt3_state_before,
                            0,
                        )
                        if passed_3:
                            _clear_retry_hint(scratchpad, phase.name)
                            _cleanup_quarantine_backups(scratchpad, phase)
                            if phase.name in checkpoint.degraded:
                                checkpoint.degraded.remove(phase.name)
                            checkpoint.save(scratchpad)
                            # Fall through to mark_completed below
                        else:
                            log.error(f"[{phase.name}] attempt 3 also failed: {missing_3}")
                            display.print_failure_diagnosis(
                                phase.name, str(scratchpad), list(missing_3), config,
                            )
                            _restore_quarantined_on_retry_failure(scratchpad, phase)
                            sys.exit(EXIT_DEGRADED)
                    elif choice == "skip":
                        log.warning(f"[{phase.name}] user chose SKIP — marking critical phase degraded, continuing pipeline")
                        _restore_quarantined_on_retry_failure(scratchpad, phase)
                        if phase.name not in checkpoint.degraded:
                            checkpoint.degraded.append(phase.name)
                        checkpoint.save(scratchpad)
                        continue
                    else:
                        display.print_failure_diagnosis(
                            phase.name, str(scratchpad), list(missing), config,
                        )
                        _restore_quarantined_on_retry_failure(scratchpad, phase)
                        sys.exit(EXIT_DEGRADED)

        checkpoint.mark_completed(phase.name)
        checkpoint.clear_degraded_sentinel(scratchpad, phase.name)
        _record_phase_artifact_state(
            scratchpad,
            config["project_root"],
            phases,
            phase.name,
            config["pipeline"],
        )
        if checkpoint.rate_limited_at == phase.name:
            checkpoint.rate_limited_at = None
        _clear_retry_hint(scratchpad, phase.name)
        checkpoint.save(scratchpad)

        # SC report_index: build body-writer manifests + expand shard phases.
        # L1 does this in its mechanical path (line ~1701); SC's LLM-authored
        # report_index needs the same treatment after the gate passes.
        if (
            phase.name == "report_index"
            and config.get("pipeline") != "l1"
        ):
            try:
                built = _build_sc_body_writer_manifests(scratchpad)
                if built:
                    phases[:] = expand_shard_phases(phases, scratchpad)
                    active_phases = [p for p in phases if mode in p.modes]
                    log.info(
                        f"[report_index] SC manifests built for "
                        f"{len(built)} shard(s), phases expanded"
                    )
                else:
                    log.warning(
                        "[report_index] SC manifest build returned empty — "
                        "body writers will use LLM-only mode"
                    )
            except Exception as exc:
                log.warning(
                    f"[report_index] SC manifest build failed: {exc!r} — "
                    f"body writers will use LLM-only mode"
                )

        # Gate summary: show what was checked so success isn't silent
        gate_summary = _format_gate_summary(phase, scratchpad, config)
        display.print_phase_done(
            phase_idx + 1, total_active, phase.name, gate_summary,
        )
        log.info(f"[{phase.name}] complete")
        if gate_summary:
            log.info(f"[{phase.name}] {gate_summary}")
        prev_phase = phase.name

    if skipped_names:
        display.print_skipped_summary(skipped_names)

    if _rate_limit_halt:
        sys.exit(EXIT_RATE_LIMITED)

    if _halted:
        display.graceful_stop.requested = False
        display.print_purge_prompt(str(scratchpad))
        if display.wait_purge_choice():
            _purge_scratchpad(scratchpad, config)
            display.print_purge_done(str(scratchpad))
        display.print_exit_clean()
        sys.exit(0)

    # v2.1.6: reconcile on-disk `.degraded` sentinels into the checkpoint
    # JSON. Fixes the Irys L1 observation where `_v2_checkpoint.json.degraded
    # == []` misleadingly disagreed with 3 `.degraded` files on disk, so a
    # reader of only the JSON saw a falsely-clean run.
    newly_synced = _sync_degraded_sentinels_to_checkpoint(scratchpad, checkpoint)
    if newly_synced:
        log.warning(
            f"Synced {len(newly_synced)} on-disk .degraded sentinels into "
            f"checkpoint: {newly_synced}"
        )
        checkpoint.save(scratchpad)

    report_path = Path(config["project_root"]) / "AUDIT_REPORT.md"
    report_str = str(report_path) if report_path.exists() else None
    snap_str = None
    if report_path.exists():
        log.info(f"Report written to {report_path}")
        snap = _snapshot_report_timestamped(config["project_root"])
        if snap:
            log.info(f"Timestamped snapshot: {snap}")
            snap_str = str(snap)

    if checkpoint.degraded:
        log.warning(f"Pipeline complete with {len(checkpoint.degraded)} degraded phases: {checkpoint.degraded}")
    else:
        log.info("Pipeline complete -- no degraded phases")

    display.print_pipeline_complete(
        checkpoint.degraded, report_path=report_str, snapshot_path=snap_str,
    )

    sys.exit(EXIT_SUCCESS if not checkpoint.degraded else EXIT_DEGRADED)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        display.print_interrupt()
        sys.exit(130)
