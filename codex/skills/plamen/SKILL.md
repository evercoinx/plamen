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

## Step 0.5: Interactive Setup (when no arguments given)

If MODE was not specified in arguments:

1. Display: "Plamen Web3 Security Auditor -- Codex Runtime"
2. Ask the user: "Which audit mode? [light/core/thorough] (default: core)"
3. Wait for response. Set MODE accordingly. If empty or unrecognized, default to core.
4. Confirm: "Starting {MODE} audit on {PROJECT_ROOT}"

If a path was not specified, use cwd and confirm:
"Target: {cwd} -- correct? [y/n]"
If the user answers "n", ask for the correct path before proceeding.

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

## Step 1.5: Platform Detection

Detect the host shell. This determines how you run commands throughout the audit:

```powershell
# PowerShell (Windows)
$IS_WINDOWS = $true
$PY = "python"
```
```bash
# Bash (macOS/Linux)
IS_WINDOWS=false
PY="python3"
```

**Use AGENTS.md Platform Awareness table** for all shell commands in this skill.
Never use `grep`, `rg`, `find`, `wc`, `cat`, `fc` raw on Windows — translate
to PowerShell equivalents.

## Step 2: Create Scratchpad

```powershell
# PowerShell
New-Item -ItemType Directory -Force "{PROJECT_ROOT}/.scratchpad" | Out-Null
```
```bash
# Bash
mkdir -p "{PROJECT_ROOT}/.scratchpad"
```

Set `SCRATCHPAD = {PROJECT_ROOT}/.scratchpad`.

## Step 2.5: Git Check

```powershell
# Check if target is a git repo — skip git steps if not
git rev-parse --is-inside-work-tree 2>$null
$IS_GIT = ($LASTEXITCODE -eq 0)
```

If `$IS_GIT` is false, skip ALL git commands (log, rev-list, blame, diff) throughout the audit.
Use file-system analysis only.

## Step 3: Initialize Watchdog

```powershell
# PowerShell
& $PY ~/.codex/plamen/hooks/phase_gate.py --init "$SCRATCHPAD" $MODE "$PROJECT_ROOT"
```
```bash
# Bash
$PY ~/.codex/plamen/hooks/phase_gate.py --init "$SCRATCHPAD" "$MODE" "$PROJECT_ROOT"
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
- Model: defined in agent role TOML (lightweight model -- mechanical query+format task)
- Spawn with `spawn_agent` and do NOT wait for completion
- Writes: `meta_buffer.md`
- If still running after Agents 1B/2/3 finish, abandon it and write:
  `meta_buffer.md` with `## RAG: UNAVAILABLE - agent timed out`

**Agent 1B: Docs + External + Fork** (foreground)
- Tasks: TASK 0 step 6 (fork ancestry), TASK 3 (docs), TASK 11 (external)
- Model: defined in agent role TOML (global model -- web search + design reasoning)
- Role: `~/.codex/agents/recon.toml` (with task subset)
- Writes: `design_context.md`, `external_production_behavior.md`

**Agent 2: Build + Static + Tests** (foreground)
- Tasks: TASK 1 (build), TASK 2 (static analysis), TASK 8 (tests), TASK 9 (coverage)
- Model: defined in agent role TOML (lightweight model -- tool execution + output formatting)
- Role: `~/.codex/agents/recon.toml` (with task subset)
- Writes: `build_status.md`, `function_list.md`, `call_graph.md`,
  `state_variables.md`, `modifiers.md`, `event_definitions.md`,
  `external_interfaces.md`, `static_analysis.md`, `test_results.md`

**Agent 3: Patterns + Surface + Templates** (foreground)
- Tasks: TASK 4 (patterns), TASK 5 (inventory), TASK 6 (surface), TASK 7 (flags),
  TASK 10 (templates)
- Model: defined in agent role TOML (global model -- attack surface + template selection requires reasoning)
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
Spawn breadth agents in batches of max 6 (from `~/.codex/agents/breadth.toml`):
- Each agent gets a unique `{N}` and scope assignment
- If 7+ agents needed: spawn agents 1-6, wait for all to complete, then spawn 7+
- Wait for all batches to complete
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

Spawn in 2 batches to respect the 8-thread limit:

**Batch 1** (4 agents): Spawn depth agents from their respective TOML roles:
- `depth-token-flow.toml`
- `depth-state-trace.toml`
- `depth-edge-case.toml`
- `depth-external.toml`
Wait for all 4 to complete.

**Batch 2** (up to 6 agents): Spawn scanners + niche agents:
- Scanner agents from `scanner.toml`
- For Core/Thorough: flag-triggered niche agents from `niche-agent.toml`
Wait for all to complete.

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

Spawn `verifier` agents in batches of 6 for each hypothesis batch:
- Read `~/.codex/plamen/rules/phase5-poc-execution.md` for PoC rules
- Batch hypotheses by severity (Critical first)
- If more than 6 hypotheses: spawn verifiers 1-6, wait, then spawn 7+
- Execute PoCs and record verdicts

### Phase 6: Report Generation

Spawn report agents sequentially per `~/.codex/plamen/rules/phase6-report-prompts.md`:
1. `report-index.toml` agent (1 agent -- assigns clean report IDs, tier assignments). Wait for completion.
2. Three parallel `report-tier-writer.toml` agents (Critical+High, Medium, Low+Info). Wait for all 3.
3. `report-assembler.toml` agent (1 agent -- combines into AUDIT_REPORT.md). Wait for completion.

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
