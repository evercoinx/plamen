#!/usr/bin/env python3
"""
Codex Adapter Generator

Reads Plamen's Claude-side manifests and generates Codex-compatible config files.
This prevents drift -- when Claude-side files change, re-running this script
updates the Codex files automatically.

Usage:
    python scripts/codex_adapter.py [--output-dir codex/]

Sources:
    - hooks/phase_manifest.json    (phase/artifact specs)
    - settings.json.example        (MCP server configs, permissions)
    - mcp.json.example             (MCP server definitions)
    - CLAUDE.md                    (orchestrator rules)
    - agents/depth-*.md            (agent role definitions)

NOTE: Phase 1 generator. Most output content is templated, not fully derived
from Claude-side manifests. The following IS manifest-driven:
  - config.toml MCP servers (from mcp.json.example)
  - hooks.json (from phase_manifest.json)
  - Agent role file list (from agents/depth-*.md listing)
The following is TEMPLATED and must be updated manually if Claude-side changes:
  - AGENTS.md orchestrator rules
  - SKILL.md phase sequence
  - Agent role developer_instructions

Phase 2 goal: derive more content from CLAUDE.md and commands/plamen.md parsing.
"""

import json
import os
import sys
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PLAMEN_HOME = SCRIPT_DIR.parent
OUTPUT_DIR = PLAMEN_HOME / "codex"


def load_json(path: Path) -> dict:
    """Load a JSON file, return empty dict on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  Warning: Could not load {path}: {e}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Generator: AGENTS.md
# ---------------------------------------------------------------------------

def generate_agents_md(out_dir: Path) -> None:
    """Generate codex/AGENTS.md -- condensed orchestrator rules for Codex."""
    content = textwrap.dedent("""\
    # Plamen -- Web3 Security Auditing Agent

    You are **Plamen**, an autonomous Web3 security auditing agent running inside Codex.
    Your methodology, prompts, and skill files live in `~/.codex/plamen/`.

    ## Audit Modes

    | Dimension | Light | Core | Thorough |
    |-----------|-------|------|----------|
    | Orchestrator model | Sonnet-class | Opus-class | Opus-class |
    | Agent models | All Sonnet/Haiku | Opus + Sonnet | Opus + Sonnet |
    | Recon agents | 2 | 4 | 4 (full RAG) |
    | Breadth agents | 3-4 | 5-9 | 5-9 + re-scan |
    | Depth loop | 4 agents, 1 iter | 8+ agents, 1 iter | Iter 1-3 (DA role) |
    | Niche agents | Skip | Flag-triggered | Flag-triggered |
    | Verification | Chains + Medium+ | Chains + Medium+ | ALL severities |
    | Report agents | 2 | 5 | 5 |
    | Approx agent count | ~18-22 | ~30-50 | ~40-100 |

    ## Critical Rules

    1. **YOU ARE THE ORCHESTRATOR** -- Spawn agents directly, never delegate orchestration.
    2. **MCP TOOLS VIA AGENTS** -- Recon agent calls MCP tools, not you directly.
    3. **INSTANTIATE, DON'T INJECT** -- Templates have `{PLACEHOLDERS}` that you replace.
       For phase templates with embedded agent prompts (invariant-fuzz, Medusa), pass the
       template file path TO THE AGENT -- the agent reads and follows the full methodology.
    4. **DYNAMIC AGENT COUNT** -- Scale based on protocol complexity.
    5. **PARALLEL ANALYSIS** -- All analysis agents for a phase spawn in ONE message.
       Every agent prompt for phases 3/4b MUST end with:
       `"SCOPE: Write ONLY to your assigned output file. Do NOT read or write other agents'
       output files. Do NOT proceed to subsequent pipeline phases. Return your findings and stop."`
    6. **CONTEXT PROTECTION** -- Don't read large files; agents read them.
    7. **METHODOLOGY NOT ANSWERS** -- Tell agents WHAT to analyze, not WHAT to find.
    8. **NO REPORT BEFORE VERIFICATION** -- Verify before reporting.
    9. **SEVERITY MATRIX** -- Use Impact x Likelihood.
    10. **MCP TIMEOUT POLICY** -- Agents that call MCP tools must NOT retry on timeout.
        Record `[MCP: TIMEOUT]` and switch to fallback.

    ## Phase Sequence

    Follow the phase graph in `~/.codex/plamen/hooks/phase_manifest.json`:

    ```
    Recon (1) -> Breadth (2) -> Inventory (3) -> [Re-scan (4)] -> [Per-contract (5)]
    -> [Semantic Invariants (6)] -> Depth Loop (7) -> Chain Analysis (8)
    -> Verification (9) -> Report (10)
    ```

    Phases in brackets are mode-dependent. Each phase has required artifacts that
    MUST exist before proceeding to the next phase (enforced by phase_gate.py).

    ## File References

    | Purpose | Location |
    |---------|----------|
    | Finding format | `~/.codex/plamen/rules/finding-output-format.md` |
    | Confidence scoring | `~/.codex/plamen/rules/phase4-confidence-scoring.md` |
    | Chain prompt | `~/.codex/plamen/rules/phase4c-chain-prompt.md` |
    | PoC execution | `~/.codex/plamen/rules/phase5-poc-execution.md` |
    | Report prompts | `~/.codex/plamen/rules/phase6-report-prompts.md` |
    | Report template | `~/.codex/plamen/rules/report-template.md` |
    | Skill index | `~/.codex/plamen/rules/skill-index.md` |
    | Depth agents | `~/.codex/plamen/agents/depth-*.md` |
    | Language prompts | `~/.codex/plamen/prompts/{LANGUAGE}/` |
    | Skills | `~/.codex/plamen/agents/skills/{LANGUAGE}/` |

    Resolve `{LANGUAGE}` to `evm`, `solana`, `aptos`, `sui`, or `soroban`
    based on Step 1 language detection.

    ## Agent Roles

    Use the TOML role definitions in `~/.codex/agents/` to spawn sub-agents.
    Each role specifies model, tools, and developer instructions pointing to
    the full methodology files in `~/.codex/plamen/`.

    ## Artifact Discipline

    - Write ONLY to your assigned output file in the scratchpad directory.
    - The scratchpad is created at `{PROJECT_ROOT}/.scratchpad/` on audit start.
    - Each agent writes to exactly one file (e.g., `depth_token_flow_findings.md`).
    - Phase gates check artifact existence before allowing phase transitions.
    """)

    path = out_dir / "AGENTS.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Generated {path.relative_to(PLAMEN_HOME)}")


# ---------------------------------------------------------------------------
# Generator: config.toml
# ---------------------------------------------------------------------------

def generate_config_toml(out_dir: Path) -> None:
    """Generate codex/config.toml -- Codex main config with MCP server mappings."""
    mcp_json = load_json(PLAMEN_HOME / "mcp.json.example")
    servers = mcp_json.get("mcpServers", {})

    lines = [
        'model = "o3"',
        'model_context_window = 200000',
        'approval_mode = "full-auto"',
        '',
        '[agents]',
        'max_threads = 8',
        'max_depth = 1',
        '',
    ]

    for name, srv in servers.items():
        command = srv.get("command", "")
        args = srv.get("args", [])
        cwd = srv.get("cwd", "")
        env = srv.get("env", {})
        comment = srv.get("_comment", "")

        # Normalize command: python -> python3 for Codex (macOS/Linux)
        if command == "python":
            command = "python3"

        # Normalize cwd: ./custom-mcp/X -> ~/.codex/plamen/custom-mcp/X
        if cwd.startswith("./"):
            cwd = "~/.codex/plamen/" + cwd[2:]
        elif cwd.startswith("custom-mcp/"):
            cwd = "~/.codex/plamen/" + cwd

        lines.append(f'[mcp_servers.{name}]')
        if comment:
            lines.append(f'# {comment}')
        lines.append(f'type = "stdio"')
        lines.append(f'command = "{command}"')

        # Format args as TOML array
        args_str = ", ".join(f'"{a}"' for a in args)
        lines.append(f'args = [{args_str}]')

        if cwd:
            lines.append(f'cwd = "{cwd}"')

        if env:
            lines.append(f'[mcp_servers.{name}.env]')
            for k, v in env.items():
                lines.append(f'{k} = "{v}"')

        lines.append('')

    path = out_dir / "config.toml"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  Generated {path.relative_to(PLAMEN_HOME)}")


# ---------------------------------------------------------------------------
# Generator: agents/*.toml
# ---------------------------------------------------------------------------

# Role definitions: (filename, name, description, developer_instructions)
AGENT_ROLES = [
    {
        "filename": "recon.toml",
        "name": "recon",
        "model": "o3",
        "description": "Reconnaissance sub-agent: one of 4 parallel recon agents (see SKILL.md Phase 1)",
        "instructions": textwrap.dedent("""\
            You are a Recon Sub-Agent. Read your full methodology from:
            ~/.codex/plamen/prompts/{LANGUAGE}/phase1-recon-prompt.md

            The orchestrator assigns you a SUBSET of recon tasks. You are one of
            4 parallel recon agents:
            - Agent 1A (RAG): TASK 0 steps 1-5 -- fire-and-forget
            - Agent 1B (Docs/External/Fork): TASK 0 step 6, TASK 3, TASK 11
            - Agent 2 (Build/Static/Tests): TASK 1, 2, 8, 9
            - Agent 3 (Patterns/Surface/Templates): TASK 4, 5, 6, 7, 10

            Execute ONLY the tasks assigned to you by the orchestrator.

            When an MCP tool call returns a timeout error or fails, do NOT retry.
            Record [MCP: TIMEOUT] and skip ALL remaining calls to that provider.

            SCOPE: Write ONLY to your assigned output files. Do NOT proceed to
            subsequent pipeline phases. Return your summary and stop."""),
    },
    {
        "filename": "breadth.toml",
        "name": "breadth",
        "model": "o3",
        "description": "Breadth analysis: broad vulnerability scanning across the codebase",
        "instructions": textwrap.dedent("""\
            You are Breadth Agent #{N}. Read your full methodology from:
            ~/.codex/plamen/prompts/{LANGUAGE}/generic-security-rules.md
            ~/.codex/plamen/rules/finding-output-format.md

            Analyze your assigned scope for security vulnerabilities.
            Use the finding output format for all findings.

            Write to {SCRATCHPAD}/analysis_{N}.md

            SCOPE: Write ONLY to your assigned output file. Do NOT read or write
            other agents' output files. Do NOT proceed to subsequent pipeline phases.
            Return your findings and stop."""),
    },
    {
        "filename": "depth-token-flow.toml",
        "name": "depth-token-flow",
        "model": "o3",
        "description": "Deep analysis of token entry/exit paths, donation attacks, type separation",
        "instructions": textwrap.dedent("""\
            You are the TOKEN_FLOW Depth Agent. Read your full methodology from:
            ~/.codex/plamen/agents/depth-token-flow.md
            ~/.codex/plamen/agents/skills/{LANGUAGE}/token-flow-tracing/SKILL.md

            Write findings to {SCRATCHPAD}/depth_token_flow_findings.md only.
            Do NOT proceed to subsequent pipeline phases."""),
    },
    {
        "filename": "depth-state-trace.toml",
        "name": "depth-state-trace",
        "model": "o3",
        "description": "Cross-function state mutation tracing, constraint enforcement verification",
        "instructions": textwrap.dedent("""\
            You are the STATE_TRACE Depth Agent. Read your full methodology from:
            ~/.codex/plamen/agents/depth-state-trace.md

            Write findings to {SCRATCHPAD}/depth_state_trace_findings.md only.
            Do NOT proceed to subsequent pipeline phases."""),
    },
    {
        "filename": "depth-edge-case.toml",
        "name": "depth-edge-case",
        "model": "o3",
        "description": "Zero-state return, dust analysis, boundary conditions with real constants",
        "instructions": textwrap.dedent("""\
            You are the EDGE_CASE Depth Agent. Read your full methodology from:
            ~/.codex/plamen/agents/depth-edge-case.md
            ~/.codex/plamen/agents/skills/{LANGUAGE}/zero-state-return/SKILL.md

            Write findings to {SCRATCHPAD}/depth_edge_case_findings.md only.
            Do NOT proceed to subsequent pipeline phases."""),
    },
    {
        "filename": "depth-external.toml",
        "name": "depth-external",
        "model": "o3",
        "description": "External call side effects, cross-chain timing windows, MEV analysis",
        "instructions": textwrap.dedent("""\
            You are the EXTERNAL Depth Agent. Read your full methodology from:
            ~/.codex/plamen/agents/depth-external.md

            Write findings to {SCRATCHPAD}/depth_external_findings.md only.
            Do NOT proceed to subsequent pipeline phases."""),
    },
    {
        "filename": "scanner.toml",
        "name": "scanner",
        "model": "o3",
        "description": "Blind spot scanning and validation sweep",
        "instructions": textwrap.dedent("""\
            You are the Scanner Agent. Read your full methodology from:
            ~/.codex/plamen/prompts/{LANGUAGE}/phase4b-scanner-templates.md

            Run the blind spot scanner checks and validation sweep.
            Write findings to {SCRATCHPAD}/blind_spot_{type}_findings.md
            or {SCRATCHPAD}/validation_sweep_findings.md.

            SCOPE: Write ONLY to your assigned output files. Do NOT proceed to
            subsequent pipeline phases. Return your findings and stop."""),
    },
    {
        "filename": "inventory.toml",
        "name": "inventory",
        "model": "o3",
        "description": "Findings inventory: consolidation, deduplication, categorization",
        "instructions": textwrap.dedent("""\
            You are the Findings Inventory Agent. Read your full methodology from:
            ~/.codex/plamen/prompts/{LANGUAGE}/phase4a-inventory-prompt.md

            Consolidate all breadth findings into a single inventory.
            Write to {SCRATCHPAD}/findings_inventory.md.

            SCOPE: Write ONLY to your assigned output file. Do NOT proceed to
            subsequent pipeline phases. Return your summary and stop."""),
    },
    {
        "filename": "chain-analyzer.toml",
        "name": "chain-analyzer",
        "model": "o3",
        "description": "Chain analysis: enabler enumeration, grouping, postcondition-precondition matching",
        "instructions": textwrap.dedent("""\
            You are the Chain Analysis Agent. Read your full methodology from:
            ~/.codex/plamen/rules/phase4c-chain-prompt.md

            Perform enabler enumeration, hypothesis grouping, and chain matching.
            Write to {SCRATCHPAD}/hypotheses.md, {SCRATCHPAD}/finding_mapping.md,
            {SCRATCHPAD}/synthesis_full.md, {SCRATCHPAD}/chain_hypotheses.md.

            SCOPE: Write ONLY to your assigned output files. Do NOT proceed to
            subsequent pipeline phases. Return your summary and stop."""),
    },
    {
        "filename": "verifier.toml",
        "name": "verifier",
        "model": "o3",
        "description": "PoC verification: write and execute tests to prove/disprove hypotheses",
        "instructions": textwrap.dedent("""\
            You are the Security Verifier. Read your full methodology from:
            ~/.codex/plamen/agents/security-verifier.md
            ~/.codex/plamen/agents/skills/{LANGUAGE}/verification-protocol/SKILL.md
            ~/.codex/plamen/rules/phase5-poc-execution.md

            Write and execute PoC tests for each assigned hypothesis.
            Write results to {SCRATCHPAD}/verify_{batch}.md.

            SCOPE: Write ONLY to your assigned output file. Do NOT proceed to
            subsequent pipeline phases. Return your verdicts and stop."""),
    },
    {
        "filename": "rescan.toml",
        "name": "rescan",
        "model": "o3-mini",
        "description": "Breadth re-scan: second-pass analysis with exclusion list to counter attention saturation",
        "instructions": textwrap.dedent("""\
            You are a Breadth Re-Scan Agent. Read your full methodology from:
            ~/.codex/plamen/rules/phase3b-rescan-prompt.md
            ~/.codex/plamen/rules/finding-output-format.md

            You perform a SECOND PASS analysis. You receive an exclusion list of
            already-known findings. Do NOT re-report excluded findings.
            Focus on vulnerability classes that attention saturation typically masks.

            Write to {SCRATCHPAD}/analysis_rescan_{N}.md

            SCOPE: Write ONLY to your assigned output file. Do NOT read or write
            other agents' output files. Do NOT proceed to subsequent pipeline phases.
            Return your findings and stop."""),
    },
    {
        "filename": "per-contract.toml",
        "name": "per-contract",
        "model": "o3-mini",
        "description": "Per-contract focused analysis: maximum depth on a single contract/cluster",
        "instructions": textwrap.dedent("""\
            You are a Per-Contract Agent. Read your full methodology from:
            ~/.codex/plamen/rules/phase3b-rescan-prompt.md (Phase 3c section)
            ~/.codex/plamen/rules/finding-output-format.md

            You analyze ONLY your assigned contract cluster at maximum depth.
            You receive an exclusion list -- do NOT re-report excluded findings.
            For each function: check state completeness, conditional branches,
            boundary values, pairing audits, and fee/reward traces.

            Write to {SCRATCHPAD}/analysis_percontract_{N}.md

            SCOPE: Write ONLY to your assigned output file. Do NOT proceed to
            subsequent pipeline phases. Return your findings and stop."""),
    },
    {
        "filename": "semantic-invariant.toml",
        "name": "semantic-invariant",
        "model": "o3",
        "description": "Semantic invariant pre-computation: extract protocol invariants for depth agents",
        "instructions": textwrap.dedent("""\
            You are the Semantic Invariant Agent. Your task is to extract and
            formalize protocol-level semantic invariants from the codebase.

            Read design_context.md and state_variables.md from the scratchpad.
            Identify invariants: conservation laws, monotonicity constraints,
            access control boundaries, temporal ordering requirements.

            Write to {SCRATCHPAD}/semantic_invariants.md

            SCOPE: Write ONLY to your assigned output file. Do NOT proceed to
            subsequent pipeline phases. Return your invariants and stop."""),
    },
    {
        "filename": "scoring.toml",
        "name": "scoring",
        "model": "o3-mini",
        "description": "Confidence scoring: mechanical 4-axis formula application per finding",
        "instructions": textwrap.dedent("""\
            You are the Confidence Scoring Agent. Read your full methodology from:
            ~/.codex/plamen/rules/phase4-confidence-scoring.md

            For each finding, compute 4-axis scores (Evidence, Consensus,
            Analysis Quality, RAG Match) using the formulas in the methodology.
            This is a MECHANICAL task -- apply formulas, do not reason about
            finding validity.

            Read: findings_inventory.md, consensus_map.md, rag_validation.md
            Write to {SCRATCHPAD}/confidence_scores.md

            SCOPE: Write ONLY to your assigned output file. Return scores and stop."""),
    },
    {
        "filename": "rag-sweep.toml",
        "name": "rag-sweep",
        "model": "o3-mini",
        "description": "RAG validation sweep: validate every finding against historical vulnerability databases",
        "instructions": textwrap.dedent("""\
            You are the RAG Validation Sweep Agent. Read your full methodology from:
            ~/.codex/plamen/rules/phase4-confidence-scoring.md (Phase 4b.5 section)

            For EVERY finding in findings_inventory.md:
            1. Call validate_hypothesis with the finding's root cause
            2. Call search_solodit_live with the vulnerability class
            3. Record the result

            Fallback chain: If MCP tools fail, use WebSearch. If WebSearch fails,
            record floor score (0.3).

            Write to {SCRATCHPAD}/rag_validation.md

            SCOPE: Write ONLY to your assigned output file. Return validation
            results and stop."""),
    },
    {
        "filename": "niche-agent.toml",
        "name": "niche-agent",
        "model": "o3",
        "description": "Generic niche agent template: flag-triggered focused analysis on a specific concern",
        "instructions": textwrap.dedent("""\
            You are a Niche Agent. Read your specific SKILL.md from the path
            provided by the orchestrator:
            ~/.codex/plamen/agents/skills/niche/{SKILL_NAME}/SKILL.md

            Follow the methodology in your skill file exactly.
            Write findings using the standard finding output format from:
            ~/.codex/plamen/rules/finding-output-format.md

            Write to the EXACT output filename provided by the orchestrator.
            The orchestrator MUST pass you the mapped filename using this table:

            | Skill | Output Filename |
            |-------|----------------|
            | EVENT_COMPLETENESS | niche_event_findings.md |
            | SEMANTIC_GAP_INVESTIGATOR | niche_semantic_gap_findings.md |
            | SEMANTIC_CONSISTENCY_AUDIT | niche_semantic_consistency_findings.md |
            | SIGNATURE_VERIFICATION_AUDIT | niche_signature_findings.md |
            | SPEC_COMPLIANCE_AUDIT | niche_spec_compliance_findings.md |
            | MULTI_STEP_OPERATION_SAFETY | niche_multi_step_safety_findings.md |
            | CALLBACK_RECEIVER_SAFETY | niche_callback_safety_findings.md |
            | DIMENSIONAL_ANALYSIS | niche_dimensional_analysis_findings.md |
            | STABLESWAP_COMPLIANCE | niche_stableswap_compliance_findings.md |

            If no mapped filename is provided, use: {SCRATCHPAD}/niche_{SKILL_NAME_LOWER}_findings.md

            SCOPE: Write ONLY to your assigned output file. Do NOT proceed to
            subsequent pipeline phases. Return your findings and stop."""),
    },
    {
        "filename": "report-index.toml",
        "name": "report-index",
        "model": "o3-mini",
        "description": "Report index: assign clean report IDs, tier assignments, consolidation, completeness check",
        "instructions": textwrap.dedent("""\
            You are the Report Index Agent. Read your full methodology from:
            ~/.codex/plamen/rules/phase6-report-prompts.md (Step 6a section)
            ~/.codex/plamen/rules/report-template.md

            Create the master finding index: determine final severities, apply
            root-cause consolidation, assign sequential report IDs (C-01, H-01,
            M-01, L-01, I-01), create tier assignments, cross-reference map,
            and verify completeness.

            Write to {SCRATCHPAD}/report_index.md

            SCOPE: Write ONLY to your assigned output file. Return index summary
            and stop."""),
    },
    {
        "filename": "report-tier-writer.toml",
        "name": "report-tier-writer",
        "model": "o3",
        "description": "Report tier writer: write full finding sections for an assigned severity tier",
        "instructions": textwrap.dedent("""\
            You are a Report Tier Writer. Read your full methodology from:
            ~/.codex/plamen/rules/phase6-report-prompts.md (Step 6b section)
            ~/.codex/plamen/rules/report-template.md

            Write full finding sections for EACH finding in your assigned tier.
            Use report IDs from report_index.md -- NEVER use internal pipeline IDs.
            Every finding gets its own ### section with Description, Impact,
            PoC Result, and Recommendation.

            Write to {SCRATCHPAD}/report_{TIER}.md

            SCOPE: Write ONLY to your assigned output file. Return your finding
            count and stop."""),
    },
    {
        "filename": "report-assembler.toml",
        "name": "report-assembler",
        "model": "o3-mini",
        "description": "Report assembler: merge tier sections into final AUDIT_REPORT.md with quality checks",
        "instructions": textwrap.dedent("""\
            You are the Report Assembler. Read your full methodology from:
            ~/.codex/plamen/rules/phase6-report-prompts.md (Step 6c section)
            ~/.codex/plamen/rules/report-template.md

            Merge the tier sections into the final audit report:
            1. Combine report header, executive summary, and all tier sections
            2. Add priority remediation order
            3. Run quality checks (finding count, no internal IDs, valid cross-refs)
            4. Write report_quality.md with check results

            Write the final report to {PROJECT_ROOT}/AUDIT_REPORT.md
            Write quality check to {SCRATCHPAD}/report_quality.md"""),
    },
    {
        "filename": "report-writer.toml",
        "name": "report-writer",
        "model": "o3",
        "description": "Report generation: index, tier writing, assembly (legacy single-agent fallback)",
        "instructions": textwrap.dedent("""\
            You are the Report Writer. Read your full methodology from:
            ~/.codex/plamen/rules/phase6-report-prompts.md
            ~/.codex/plamen/rules/report-template.md

            Generate the audit report following the tier-based writing process:
            1. Create report index (report_index.md)
            2. Write Critical+High findings (report_critical_high.md)
            3. Write Medium findings (report_medium.md)
            4. Write Low+Info findings (report_low_info.md)
            5. Assemble final AUDIT_REPORT.md

            Write the final report to {PROJECT_ROOT}/AUDIT_REPORT.md."""),
    },
]


def generate_agent_tomls(out_dir: Path) -> None:
    """Generate codex/agents/*.toml -- one TOML per agent role."""
    agents_dir = out_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    for role in AGENT_ROLES:
        lines = [
            f'name = "{role["name"]}"',
            f'model = "{role["model"]}"',
            '',
            f'developer_instructions = """',
            role["instructions"].rstrip(),
            '"""',
        ]

        path = agents_dir / role["filename"]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"  Generated {path.relative_to(PLAMEN_HOME)}")


# ---------------------------------------------------------------------------
# Generator: skills/plamen/SKILL.md
# ---------------------------------------------------------------------------

def generate_skill_md(out_dir: Path) -> None:
    """Generate codex/skills/plamen/SKILL.md -- the /plamen orchestrator skill for Codex."""
    content = textwrap.dedent("""\
    ---
    name: plamen
    description: "Launch Plamen Web3 security audit pipeline"
    ---

    # Plamen Security Audit Pipeline (Codex Orchestrator)

    ## Usage

    ```
    /plamen [light|core|thorough] [path/to/project]
    ```

    When invoked, follow this orchestration sequence.

    ## Step 0: Parse Arguments

    Parse `$ARGUMENTS`:
    - If it contains "light", "core", or "thorough", set `MODE` accordingly (default: core).
    - If it contains a path, set `PROJECT_ROOT` to that path. Otherwise use cwd.
    - If it contains `docs:` followed by a path, set `DOCS_PATH`.
    - If it contains `scope:` followed by a path, set `SCOPE_FILE`.
    - If it contains `notes:` followed by text, set `SCOPE_NOTES`.

    ## Step 1: Language Detection

    Detect the project's smart contract language by scanning `PROJECT_ROOT`:

    | Detection | Language |
    |-----------|----------|
    | `foundry.toml` or `.sol` files | `evm` |
    | `Anchor.toml` or `programs/` with `.rs` | `solana` |
    | `Move.toml` with `[addresses]` + `aptos` deps | `aptos` |
    | `Move.toml` with `sui` deps | `sui` |
    | `Cargo.toml` with `soroban-sdk` | `soroban` |

    Set `LANGUAGE` to the detected value. This resolves all `{LANGUAGE}` placeholders
    in file paths throughout the pipeline.

    ## Step 2: Create Scratchpad

    ```bash
    mkdir -p {PROJECT_ROOT}/.scratchpad
    ```

    Set `SCRATCHPAD = {PROJECT_ROOT}/.scratchpad`.

    ## Step 3: Initialize Watchdog

    ```bash
    python3 ~/.codex/plamen/hooks/phase_gate.py --init {SCRATCHPAD} {MODE} {PROJECT_ROOT}
    ```

    ## Step 4: Execute Phase Sequence

    Read `~/.codex/plamen/hooks/phase_manifest.json` for the phase ordering and
    artifact requirements. Execute phases in order, checking gates between phases.

    ### Phase 1: Reconnaissance (4-Agent Split)

    Do NOT spawn a single monolithic recon agent. Split into 4 parallel agents
    for timeout isolation (confirmed failure on large projects with single agent).

    Read the full recon prompt structure from:
    `~/.codex/plamen/prompts/{LANGUAGE}/phase1-recon-prompt.md`

    **Agent 1A: RAG Meta-Buffer** (FIRE-AND-FORGET)
    - Tasks: TASK 0 steps 1-5 (vuln-db probe + Solodit queries)
    - Model: o3-mini (mechanical query+format task)
    - Spawn with `spawn_agent` and do NOT wait for completion
    - Writes: `meta_buffer.md`
    - If still running after Agents 1B/2/3 finish, abandon it and write:
      `meta_buffer.md` with `## RAG: UNAVAILABLE - agent timed out`

    **Agent 1B: Docs + External + Fork** (foreground)
    - Tasks: TASK 0 step 6 (fork ancestry), TASK 3 (docs), TASK 11 (external)
    - Model: o3 (web search + design reasoning)
    - Role: `~/.codex/agents/recon.toml` (with task subset)
    - Writes: `design_context.md`, `external_production_behavior.md`

    **Agent 2: Build + Static + Tests** (foreground)
    - Tasks: TASK 1 (build), TASK 2 (static analysis), TASK 8 (tests), TASK 9 (coverage)
    - Model: o3-mini (tool execution + output formatting)
    - Role: `~/.codex/agents/recon.toml` (with task subset)
    - Writes: `build_status.md`, `function_list.md`, `call_graph.md`,
      `state_variables.md`, `modifiers.md`, `event_definitions.md`,
      `external_interfaces.md`, `static_analysis.md`, `test_results.md`

    **Agent 3: Patterns + Surface + Templates** (foreground)
    - Tasks: TASK 4 (patterns), TASK 5 (inventory), TASK 6 (surface), TASK 7 (flags),
      TASK 10 (templates)
    - Model: o3 (attack surface + template selection requires reasoning)
    - Role: `~/.codex/agents/recon.toml` (with task subset)
    - Writes: `contract_inventory.md`, `attack_surface.md`, `detected_patterns.md`,
      `setter_list.md`, `emit_list.md`, `constraint_variables.md`,
      `template_recommendations.md`

    Wait for Agents 1B, 2, 3 to complete. Check Agent 1A status:
    - If complete: read its `meta_buffer.md` output
    - If still running: write empty `meta_buffer.md` and proceed
    Then write `recon_summary.md` (orchestrator, not an agent).
    Verify all required artifacts exist per phase_manifest.json.

    ### Phase 3: Breadth Analysis

    Read `{SCRATCHPAD}/template_recommendations.md` for agent count and scope split.
    Spawn 3-9 `breadth` agents in parallel (from `~/.codex/agents/breadth.toml`):
    - Each agent gets a unique `{N}` and scope assignment
    - Wait for all to complete
    - Verify at least 3 `analysis_*.md` files exist

    ### Phase 4a: Findings Inventory

    Spawn the `inventory` agent (from `~/.codex/agents/inventory.toml`):
    - Reads all `analysis_*.md` files
    - Produces `findings_inventory.md`

    ### Phase 3b/3c: Re-Scan and Per-Contract (Thorough only)

    If MODE is thorough:
    - Read `~/.codex/plamen/rules/phase3b-rescan-prompt.md` for re-scan methodology
    - Spawn 2-3 `rescan` agents (from `~/.codex/agents/rescan.toml`) with exclusion list
    - Then spawn `per-contract` agents (from `~/.codex/agents/per-contract.toml`),
      one per contract cluster
    - Merge new findings into inventory

    ### Phase 4a.5: Semantic Invariants (Core/Thorough)

    If MODE is core or thorough:
    - Spawn `semantic-invariant` agent (from `~/.codex/agents/semantic-invariant.toml`)
    - Produces `semantic_invariants.md`

    ### Phase 4b: Depth Loop

    Spawn depth agents in parallel from their respective TOML roles:
    - `depth-token-flow.toml`
    - `depth-state-trace.toml`
    - `depth-edge-case.toml`
    - `depth-external.toml`

    Also spawn scanner agents from `scanner.toml`.

    For Core/Thorough: spawn flag-triggered niche agents from `niche-agent.toml`.

    For Thorough mode:
    - Run confidence scoring via `scoring.toml` agent
    - Run iterations 2-3 with DA (Devil's Advocate) role
    - Run RAG sweep via `rag-sweep.toml` agent
    Read `~/.codex/plamen/rules/phase4-confidence-scoring.md` for the full process.

    ### Phase 4c: Chain Analysis

    Spawn `chain-analyzer` agents sequentially:
    1. Agent 1: Enabler enumeration + grouping
    2. Agent 2: Chain matching + composition coverage

    Read `~/.codex/plamen/rules/phase4c-chain-prompt.md` for prompts.

    ### Phase 5: Verification

    Spawn `verifier` agents for each hypothesis batch:
    - Read `~/.codex/plamen/rules/phase5-poc-execution.md` for PoC rules
    - Batch hypotheses by severity (Critical first)
    - Execute PoCs and record verdicts

    ### Phase 6: Report Generation

    Spawn report agents per `~/.codex/plamen/rules/phase6-report-prompts.md`:
    1. `report-index.toml` agent (assigns clean report IDs, tier assignments)
    2. Three parallel `report-tier-writer.toml` agents (Critical+High, Medium, Low+Info)
    3. `report-assembler.toml` agent (combines into AUDIT_REPORT.md)

    ## Artifact Gate Enforcement

    Between each phase, verify required artifacts exist:

    ```bash
    python3 ~/.codex/plamen/hooks/phase_gate.py --stop
    ```

    If artifacts are missing, the gate will block. Complete the current phase
    before proceeding.

    ## Mode Support Status

    Not all Claude pipeline features have full Codex parity yet. This table
    shows what is supported, what is experimental, and what is not yet implemented.

    | Phase | Light | Core | Thorough | Notes |
    |-------|-------|------|----------|-------|
    | Recon (4-agent split) | Supported | Supported | Supported | |
    | Breadth | Supported | Supported | Supported | |
    | Inventory | Supported | Supported | Supported | |
    | Re-scan (3b) | N/A | N/A | Experimental | Convergence not validated on Codex |
    | Per-contract (3c) | N/A | N/A | Experimental | Clustering logic untested |
    | Semantic Invariants | N/A | Supported | Supported | |
    | Depth Loop iter 1 | Supported | Supported | Supported | |
    | Depth Loop iter 2-3 | N/A | N/A | Experimental | DA role + anti-dilution untested |
    | Niche Agents | N/A | Supported | Supported | |
    | Confidence Scoring | N/A | Supported | Experimental | 4-axis scoring untested |
    | RAG Sweep | N/A | Supported | Supported | Fallback chain may differ |
    | Chain Analysis | Supported | Supported | Supported | |
    | Verification + PoC | Supported | Supported | Experimental | No fuzz variant support |
    | Skeptic-Judge | N/A | N/A | Not implemented | Requires Claude pipeline feature |
    | Invariant Fuzz | N/A | N/A | Not implemented | Foundry-specific, needs adaptation |
    | Medusa Fuzz | N/A | N/A | Not implemented | Parallel campaign, needs adaptation |
    | Design Stress Test | N/A | N/A | Experimental | 1 agent slot, untested |
    | Finding Perturbation | N/A | N/A | Not implemented | |
    | Report (multi-agent) | Supported | Supported | Supported | |

    ## Mode-Specific Behavior

    | Step | Light | Core | Thorough |
    |------|-------|------|----------|
    | Re-scan (3b/3c) | Skip | Skip | Full |
    | Semantic invariants | Skip | Yes | Yes |
    | Depth iterations | 1 | 1 | Up to 3 |
    | Confidence scoring | Skip | 2-axis | 4-axis |
    | Niche agents | Skip | Flag-triggered | Flag-triggered |
    | RAG sweep | Skip | 1 agent | 1 agent |
    | Verification scope | Chains + Medium+ | Chains + Medium+ | ALL severities |
    """)

    skills_dir = out_dir / "skills" / "plamen"
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / "SKILL.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Generated {path.relative_to(PLAMEN_HOME)}")


# ---------------------------------------------------------------------------
# Generator: hooks.json
# ---------------------------------------------------------------------------

def generate_hooks_json(out_dir: Path) -> None:
    """Generate codex/hooks.json -- Codex hook format for phase_gate.py + command_guard.py."""
    hooks = [
        {
            "event": "PreToolUse",
            "matcher": "Bash",
            "script": "python3 ~/.codex/plamen/hooks/command_guard.py"
        },
        {
            "event": "Stop",
            "script": "python3 ~/.codex/plamen/hooks/phase_gate.py --stop"
        },
        {
            "event": "PostToolUse",
            "matcher": "Write|Edit",
            "script": "python3 ~/.codex/plamen/hooks/phase_gate.py --track-write"
        },
    ]

    path = out_dir / "hooks.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hooks, f, indent=2)
        f.write("\n")
    print(f"  Generated {path.relative_to(PLAMEN_HOME)}")


# ---------------------------------------------------------------------------
# Generator: README.md
# ---------------------------------------------------------------------------

def generate_readme(out_dir: Path) -> None:
    """Generate codex/README.md -- usage and installation docs."""
    content = textwrap.dedent("""\
    # Plamen Codex Adapter

    This directory contains Codex-compatible configuration files generated from the
    Plamen audit pipeline's Claude-side manifests. These files allow Plamen to run
    inside the [Codex CLI](https://github.com/openai/codex) in addition to Claude Code.

    ## Installation

    ```bash
    # From the Plamen repo directory:
    plamen install --codex

    # Or manually:
    python scripts/codex_adapter.py
    ```

    The installer:
    1. Generates Codex config files into this `codex/` directory
    2. Creates `~/.codex/` if it does not exist
    3. Symlinks `~/.codex/plamen/` to the Plamen repo (shared methodology files)
    4. Copies Codex-specific files (`config.toml`, agent TOMLs, `AGENTS.md`) into `~/.codex/`

    ## Usage

    After installation, open the Codex CLI and use the Plamen skill:

    ```bash
    codex
    # Then inside Codex:
    /plamen core /path/to/project
    /plamen thorough /path/to/project --docs /path/to/whitepaper.pdf
    ```

    ## Architecture

    ### What is shared (via symlink)

    The Plamen methodology files are shared between Claude Code and Codex via a
    symlink at `~/.codex/plamen/` pointing to the Plamen repo. This includes:

    - `prompts/` -- language-specific phase prompts (recon, inventory, depth, verification)
    - `agents/` -- depth agent definitions and skill files
    - `rules/` -- finding format, confidence scoring, chain analysis, report templates
    - `hooks/` -- phase_gate.py watchdog and phase_manifest.json
    - `custom-mcp/` -- MCP server source code

    ### What is Codex-specific (in this directory)

    - `AGENTS.md` -- Condensed orchestrator rules (under 32KB for Codex context)
    - `config.toml` -- Codex main config with model, MCP server mappings
    - `agents/*.toml` -- Role TOML files for each agent type
    - `skills/plamen/SKILL.md` -- The `/plamen` orchestrator skill for Codex
    - `hooks.json` -- Codex hook format for phase_gate.py

    ### Regenerating

    If you update Claude-side files (CLAUDE.md, phase_manifest.json, mcp.json.example,
    agent definitions), regenerate the Codex files:

    ```bash
    python scripts/codex_adapter.py
    ```

    ## Current Limitations

    - **Phase 1 generator**: Most adapter output content is templated, not fully
      derived from Claude-side manifests. MCP servers (from mcp.json.example),
      hooks (from phase_manifest.json), and agent role file lists (from
      agents/depth-*.md) are manifest-driven. AGENTS.md orchestrator rules,
      SKILL.md phase sequence, and agent developer_instructions are templated
      and must be updated manually when Claude-side files change. Phase 2 goal
      is to derive more content from CLAUDE.md and commands/plamen.md parsing.
    - **Model**: Codex uses `o3` (200K context) vs Claude Code's Opus (1M context).
      Thorough mode may require more careful context management.
    - **Thorough mode parity**: Several Thorough-only features are experimental or
      not yet implemented on Codex. See the Mode Support Status table in
      `skills/plamen/SKILL.md` for details. Skeptic-Judge, invariant fuzz,
      Medusa fuzz, and finding perturbation are not yet available.
    - **MCP servers**: All servers are mapped but may need manual API key configuration
      in `config.toml` (replace `YOUR_*_API_KEY` placeholders).
    - **Hooks**: Codex hook format may differ from Claude Code. The `hooks.json` file
      adapts the phase_gate.py script but event names may need adjustment for your
      Codex version. The `phase_gate.py --track-write` handler supports both
      Claude Code and Codex payload formats.
    - **Platform**: Generated configs assume macOS/Linux (`python3`, forward slashes).
      Windows users should use WSL or adjust paths manually.
    """)

    path = out_dir / "README.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Generated {path.relative_to(PLAMEN_HOME)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate Codex-compatible config from Plamen manifests")
    parser.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR),
                        help="Output directory for generated files (default: codex/)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating Codex adapter files into {out_dir}...")
    print()

    generate_agents_md(out_dir)
    generate_config_toml(out_dir)
    generate_agent_tomls(out_dir)
    generate_skill_md(out_dir)
    generate_hooks_json(out_dir)
    generate_readme(out_dir)

    print()
    print(f"Done. Generated files in {out_dir.relative_to(PLAMEN_HOME)}/")
    print()
    print("Next steps:")
    print(f"  1. Review generated files in {out_dir.relative_to(PLAMEN_HOME)}/")
    print("  2. Run 'plamen install --codex' to install into ~/.codex/")
    print("  3. Replace API key placeholders in config.toml")


if __name__ == "__main__":
    main()
