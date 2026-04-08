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
