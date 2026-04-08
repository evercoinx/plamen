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
        "description": "Reconnaissance: build environment, pattern detection, attack surface mapping",
        "instructions": textwrap.dedent("""\
            You are the Recon Agent. Read your full methodology from:
            ~/.codex/plamen/prompts/{LANGUAGE}/phase1-recon-prompt.md

            Your tasks:
            1. Build the project and record status
            2. Detect code patterns and flags
            3. Ingest documentation
            4. Map the attack surface

            Write artifacts to {SCRATCHPAD}/:
            - design_context.md, attack_surface.md, build_status.md
            - function_list.md, state_variables.md, contract_inventory.md
            - template_recommendations.md, detected_patterns.md
            - setter_list.md, emit_list.md, recon_summary.md

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
        "filename": "report-writer.toml",
        "name": "report-writer",
        "model": "o3",
        "description": "Report generation: index, tier writing, assembly",
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

    ### Phase 1: Reconnaissance

    Spawn the `recon` agent (from `~/.codex/agents/recon.toml`):
    - Replace `{LANGUAGE}` with the detected language
    - Replace `{SCRATCHPAD}` with the scratchpad path
    - Wait for completion
    - Verify all required artifacts exist per phase_manifest.json

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
    - Spawn re-scan agents, then per-contract agents
    - Merge new findings into inventory

    ### Phase 4a.5: Semantic Invariants (Core/Thorough)

    If MODE is core or thorough:
    - Spawn invariant analysis agent
    - Produces `semantic_invariants.md`

    ### Phase 4b: Depth Loop

    Spawn depth agents in parallel from their respective TOML roles:
    - `depth-token-flow.toml`
    - `depth-state-trace.toml`
    - `depth-edge-case.toml`
    - `depth-external.toml`

    Also spawn scanner agents from `scanner.toml`.

    For Thorough mode: run confidence scoring, iterations 2-3, RAG sweep.
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

    Spawn `report-writer` agents per `~/.codex/plamen/rules/phase6-report-prompts.md`:
    1. Index agent (assigns report IDs)
    2. Three parallel tier writers (Critical+High, Medium, Low+Info)
    3. Assembler (combines into AUDIT_REPORT.md)

    ## Artifact Gate Enforcement

    Between each phase, verify required artifacts exist:

    ```bash
    python3 ~/.codex/plamen/hooks/phase_gate.py --stop
    ```

    If artifacts are missing, the gate will block. Complete the current phase
    before proceeding.

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
    """Generate codex/hooks.json -- Codex hook format for phase_gate.py."""
    hooks = [
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

    - **Model**: Codex uses `o3` (200K context) vs Claude Code's Opus (1M context).
      Thorough mode may require more careful context management.
    - **MCP servers**: All servers are mapped but may need manual API key configuration
      in `config.toml` (replace `YOUR_*_API_KEY` placeholders).
    - **Hooks**: Codex hook format may differ from Claude Code. The `hooks.json` file
      adapts the phase_gate.py script but event names may need adjustment for your
      Codex version.
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
