---
description: "Launch Plamen security audit pipeline. Usage: /plamen [core|thorough]"
---

# Plamen Audit Pipeline

## Step 0: Interactive Setup Wizard

**Shortcut handling**: Parse `$ARGUMENTS` for pre-filled values:
- If it contains "core" or "thorough", set `MODE` accordingly.
- If it contains an absolute path (e.g., `D:\...` or `/home/...`), set `PROJECT_PATH` to that path. Otherwise use cwd.
- If it contains `docs:` followed by a path or URL, set `DOCS_PATH` to that value and skip Step 0c.
- If it contains `nodocs`, set `DOCS_PATH` to empty and skip Step 0c.
- If it contains `network:` followed by a network name (e.g., `ethereum`, `arbitrum`, `optimism`, `base`, `polygon`, `bsc`, `avalanche`, or an RPC URL), set `NETWORK` to that value. Used for production verification and fork testing.
- If it contains `scope:` followed by a file path, set `SCOPE_FILE` to that path. The file should list in-scope contracts/files.
- If it contains `notes:` followed by text (up to end of arguments or next known prefix), set `SCOPE_NOTES` to that text. Passed to recon as additional audit context (e.g., "focus on vault module, ignore governance").
- If it contains `proven-only:` followed by `true` (or just `proven-only: true`), set `PROVEN_ONLY = true`. When enabled, findings whose best evidence is `[CODE-TRACE]` (no executed PoC or fuzzer counterexample) are capped at Low severity in the report. Default: false.
- If MODE, PROJECT_PATH, and DOCS_PATH (or nodocs) are all resolved, skip the entire wizard — jump directly to "Step 0d: Launch".
- If MODE is set but docs status is unknown (no `docs:` and no `nodocs`), skip to Step 0c only.
- If `$ARGUMENTS` contains "compare", jump directly to the compare flow (Step 0e). If it also contains `report:` followed by a file path, set `REPORT_PATH`. If it contains `ground_truth:` followed by a file path, set `GROUND_TRUTH_PATH`. If both are set, skip the interactive file selection in Step 0e and proceed directly.
- If `$ARGUMENTS` is empty, run the full interactive wizard starting at Step 0a.

### Step 0a: Banner + Toolchain Check + Mode Selection

First, output the banner as text (no tool calls):

```
██████╗ ██╗      █████╗ ███╗   ███╗███████╗███╗   ██╗
██╔══██╗██║     ██╔══██╗████╗ ████║██╔════╝████╗  ██║
██████╔╝██║     ███████║██╔████╔██║█████╗  ██╔██╗ ██║
██╔═══╝ ██║     ██╔══██║██║╚██╔╝██║██╔══╝  ██║╚██╗██║
██║     ███████╗██║  ██║██║ ╚═╝ ██║███████╗██║ ╚████║
╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═══╝
```

**Web3 Security Auditor** v1.0

Then run a quick toolchain probe (via Bash, all in one command):

```bash
echo "Toolchain:" && \
echo -n "  Required: " && \
(command -v claude >/dev/null 2>&1 && echo -n "✓claude " || echo -n "✗claude ") && \
(command -v python >/dev/null 2>&1 && echo -n "✓python " || echo -n "✗python ") && \
(command -v npx >/dev/null 2>&1 && echo -n "✓npx " || echo -n "✗npx ") && \
(command -v git >/dev/null 2>&1 && echo -n "✓git" || echo -n "✗git") && echo "" && \
echo -n "  EVM:      " && \
(command -v forge >/dev/null 2>&1 && echo -n "✓forge " || echo -n "○forge ") && \
(command -v slither >/dev/null 2>&1 && echo -n "✓slither " || echo -n "○slither ") && \
(command -v medusa >/dev/null 2>&1 && echo -n "✓medusa" || echo -n "○medusa") && echo "" && \
echo -n "  Solana:   " && \
(command -v solana >/dev/null 2>&1 && echo -n "✓solana " || echo -n "○solana ") && \
(command -v anchor >/dev/null 2>&1 && echo -n "✓anchor " || echo -n "○anchor ") && \
(command -v trident >/dev/null 2>&1 && echo -n "✓trident" || echo -n "○trident") && echo "" && \
echo -n "  Move:     " && \
(command -v aptos >/dev/null 2>&1 && echo -n "✓aptos " || echo -n "○aptos ") && \
(command -v sui >/dev/null 2>&1 && echo -n "✓sui" || echo -n "○sui") && echo ""
```

Display the output to the user. If any required tools (claude, python, npx, git) show ✗, warn:
> **Warning**: Missing required tools. Run `plamen setup` in your terminal to install them.

If optional tools are missing, note briefly:
> Optional tools with ○ are not installed — the pipeline degrades gracefully but coverage may be reduced. Run `plamen setup` to install.

Then proceed to mode selection using `AskUserQuestion` with previews:

```
AskUserQuestion(questions=[{
  question: "Which audit mode would you like to run?",
  header: "Mode",
  multiSelect: false,
  options: [
    {
      label: "Core (Recommended)",
      description: "Standard audit — verifies all Medium+ findings",
      preview: "~25-45 agents\n\nPipeline:\n  Breadth → Inventory → Depth (iter 1)\n  → Chains → Verify ALL Medium+\n\nSkips:\n  · Breadth re-scan (3b/3c)\n  · Depth iterations 2-3\n  · Design stress testing\n  · Invariant fuzz campaign\n  · Fuzz variants in verification\n\nScoring: 2-axis (Evidence + Analysis Quality)"
    },
    {
      label: "Thorough",
      description: "Deep audit — iterative depth, fuzz variants, re-scan",
      preview: "~35-95 agents\n\nPipeline:\n  Breadth → Re-scan (2 iters) → Per-contract\n  → Inventory → Depth (1-3 iters, Devil's Advocate)\n  → Chains → Verify ALL severities (with fuzz)\n  → Skeptic-Judge for HIGH/CRIT\n\nIncludes:\n  · Breadth re-scan + per-contract analysis\n  · Invariant fuzz campaign (EVM)\n  · Medusa stateful fuzzing (EVM, if installed)\n  · Design stress testing\n  · Skeptic-Judge adversarial verification (HIGH/CRIT)\n  · Fuzz variants in verification\n  · Low/Info findings verified\n\nScoring: 4-axis (Evidence, Consensus, Quality, RAG)"
    },
    {
      label: "Compare",
      description: "Diff a past Plamen report against a ground truth report",
      preview: "Post-audit improvement mode\n\nYou provide:\n  · Your Plamen audit report\n  · A ground truth / reference report\n\nOutputs:\n  · Finding alignment matrix\n  · Recall & precision metrics\n  · Root cause classification\n  · Targeted methodology improvements"
    }
  ]
}])
```

Set `MODE` based on the user's selection. If "Compare" is selected, jump to Step 0e.

### Step 0b: Target Project

Use `AskUserQuestion` to confirm the project directory:

```
AskUserQuestion(questions=[{
  question: "Is this the project you want to audit?",
  header: "Target",
  multiSelect: false,
  options: [
    {
      label: "Yes, use {cwd}",
      description: "Audit the current working directory"
    },
    {
      label: "No, let me specify",
      description: "I'll provide a different project path"
    }
  ]
}])
```

If the user selects "No" or "Other", ask them to type the path. Set `PROJECT_PATH` accordingly.

### Step 0c: Documentation

Use `AskUserQuestion` to ask about documentation:

```
AskUserQuestion(questions=[{
  question: "Do you have project docs that describe trust roles or actor permissions? (used to calibrate finding severity — e.g., 'admin is a 5/7 multisig with timelock')",
  header: "Docs",
  multiSelect: false,
  options: [
    {
      label: "No docs",
      description: "Trust roles will be inferred from code patterns (onlyOwner, role modifiers, etc.)"
    },
    {
      label: "Yes, local files",
      description: "Whitepaper, spec, or design doc with trust/role information"
    },
    {
      label: "Yes, a URL",
      description: "Link to docs describing trust model or actor permissions"
    }
  ]
}])
```

If the user selects local files or URL, ask them to provide the path or URL. Store as `DOCS_PATH`.

### Step 0c.5: Scope

Use `AskUserQuestion` to ask about scope constraints:

```
AskUserQuestion(questions=[{
  question: "Do you want to limit the audit scope?",
  header: "Scope",
  multiSelect: false,
  options: [
    {
      label: "Full project",
      description: "Audit everything in the target directory"
    },
    {
      label: "Scope file",
      description: "I have a scope.txt listing specific files/contracts"
    },
    {
      label: "Scope notes",
      description: "I'll describe the focus areas in plain text"
    }
  ]
}])
```

If the user selects "Scope file", ask them to provide the path. Store as `SCOPE_FILE`.
If the user selects "Scope notes", ask them to describe the focus. Store as `SCOPE_NOTES`.
If "Full project", leave both empty.

### Step 0c.6: Proven-Only Mode

Use `AskUserQuestion` to ask about severity strictness:

```
AskUserQuestion(questions=[{
  question: "Enable proven-only mode? (findings without executed PoC evidence are capped at Low severity — useful for benchmark comparisons)",
  header: "Proven-Only",
  multiSelect: false,
  options: [
    {
      label: "No (default)",
      description: "Standard severity rules — manual code traces can support any severity"
    },
    {
      label: "Yes",
      description: "Unproven findings ([CODE-TRACE] only) capped at Low"
    }
  ]
}])
```

If "Yes", set `PROVEN_ONLY = true`.

### Step 0d: Launch

Output a confirmation summary:

> **Plamen {MODE}** audit
> **Target**: `{PROJECT_PATH}`
> **Network**: {NETWORK or "auto-detect"} *(only if NETWORK is set)*
> **Docs**: {docs status}
> **Scope**: {SCOPE_FILE or "full project"} *(only if SCOPE_FILE is set)*
> **Notes**: {SCOPE_NOTES} *(only if SCOPE_NOTES is set)*
> **Proven-only**: {ON/OFF} *(only if PROVEN_ONLY is true — unproven findings capped at Low)*
>
> Starting...

Then proceed to Step 1.

### Step 0e: Compare Flow

If the user selected "Compare":
1. If `REPORT_PATH` and `GROUND_TRUTH_PATH` are both set from `$ARGUMENTS`, skip to step 3.
2. Otherwise, use `AskUserQuestion` to ask for both report paths (both must be `.md` files — PDFs cannot be diffed).
3. Read both files and follow the Post-Audit Improvement Protocol from `~/.claude/rules/post-audit-improvement-protocol.md`.

Do NOT proceed to Step 1.

---

## Step 0.5: Network Resolution (EVM only)

If `NETWORK` is set and `LANGUAGE` is `evm`, resolve to an RPC URL for production verification and fork testing:

| Network | RPC URL |
|---------|---------|
| `ethereum` | `https://eth.llamarpc.com` or `$ETH_RPC_URL` env var |
| `arbitrum` | `https://arb1.arbitrum.io/rpc` or `$ARBITRUM_RPC_URL` env var |
| `optimism` | `https://mainnet.optimism.io` or `$OPTIMISM_RPC_URL` env var |
| `base` | `https://mainnet.base.org` or `$BASE_RPC_URL` env var |
| `polygon` | `https://polygon-rpc.com` or `$POLYGON_RPC_URL` env var |
| `bsc` | `https://bsc-dataseed1.binance.org` or `$BSC_RPC_URL` env var |
| `avalanche` | `https://api.avax.network/ext/bc/C/rpc` or `$AVALANCHE_RPC_URL` env var |
| Other (URL) | Use as-is |

**Priority**: Environment variable > default public RPC. Store resolved URL as `RPC_URL` — used by Phase 1 TASK 11 (production verification) and Phase 5 (fork testing with `--fork-url`).

If `NETWORK` is not set: orchestrator infers from codebase (chainId constants, deployment configs, foundry.toml `[rpc_endpoints]`). If inference fails, production verification runs without fork testing.

---

## Step 1: Language Detection

Detect the target language before anything else:

| Indicator | Language | `LANGUAGE` value |
|-----------|----------|-----------------|
| `*.sol` files + `foundry.toml` or `hardhat.config.*` | **EVM/Solidity** | `evm` |
| `*.rs` files + `Anchor.toml` or `Cargo.toml` with `solana-program`/`anchor-lang` | **Solana/Anchor** | `solana` |
| `*.rs` files + `Cargo.toml` WITHOUT `solana-program`/`anchor-lang` | **Native Solana (no Anchor)** | `solana` (with `ANCHOR=false` flag) |
| `*.move` files + `Move.toml` with `aptos_framework`/`aptos_std`/`aptos_token`/`fungible_asset` | **Aptos Move** | `aptos` |
| `*.move` files + `Move.toml` with `sui::object`/`sui::transfer`/`sui::tx_context`/`sui::coin` | **Sui Move** | `sui` |

**Detection procedure**:
1. `ls` project root for `foundry.toml`, `hardhat.config.*`, `Anchor.toml`, `Move.toml`
2. If `Move.toml` found: grep dependencies for Aptos indicators (`AptosFramework`, `aptos_framework`, `AptosStdlib`, `aptos_std`, `AptosToken`, `aptos_token`) or Sui indicators (`Sui`, `sui::object`, `sui::transfer`, `sui::tx_context`, `sui::coin`)
3. If ambiguous Move: grep `*.move` for `use aptos_framework::` (Aptos) or `use sui::` (Sui)
4. If `*.rs` files: grep `Cargo.toml` for `anchor-lang` or `solana-program`
5. If still ambiguous Rust: grep `*.rs` for `#[program]` or `#[derive(Accounts)]` (Anchor markers)
6. Set `LANGUAGE` variable: `evm`, `solana`, `aptos`, or `sui`
7. Set `ANCHOR` variable: `true` or `false` (Solana only)

**Tree architecture — path resolution**:
- **Language-specific prompts**: `~/.claude/prompts/{LANGUAGE}/`
- **Shared rules**: `~/.claude/rules/`
- **Skills**: `~/.claude/agents/skills/{LANGUAGE}/`
- **Injectable skills**: `~/.claude/agents/skills/injectable/`
- **Niche agents**: `~/.claude/agents/skills/niche/`
- **Depth agents**: `~/.claude/agents/depth-*.md`

---

## WORKFLOW OVERVIEW

> **ARCHITECTURE**: Recon (+ Fork Ancestry) → Instantiation → Parallel Breadth → Inventory (+ Side Effect Trace) → [Thorough: Re-Scan] → Semantic Invariants → Adaptive Depth Loop → Iterative Chain Analysis (+ Enabler Enumeration) → Verification → Report

| Phase | Agent(s) | Output | Mode |
|-------|----------|--------|------|
| **Phase 1** | 1 Recon Agent | All artifacts + RAG meta-buffer + production behavior + template recommendations | Both |
| **Phase 2** | Orchestrator | Instantiated prompts for analysis | Both |
| **Phase 3** | N Breadth Agents (parallel) | Findings files (with preconditions/postconditions) | Both |
| **Phase 3b** | Breadth Re-Scan (sonnet, 2-3 agents, max 2 iterations) + Per-Contract Analysis | New findings masked by attention saturation | Thorough only |
| **Phase 4a** | Inventory Agent (+ side effect trace audit) | Findings inventory, side effect traces, static analysis promotions, REFUTED list, gate status | Both |
| **Phase 4a.5** | Semantic Invariant Agent (sonnet) | `semantic_invariants.md` — write-site lists + semantic invariants + annotations | Both (Pass 1 only in Core; Pass 1+2 in Thorough) |
| **Phase 4b** | Adaptive Depth Loop | Deep analysis + confidence scores + targeted re-analysis | Both (scope differs by mode) |
| **Phase 4c** | Iterative Chain Analysis (1-2 iterations) | Enabler paths + grouped hypotheses + chain hypotheses | Both |
| **Phase 5** | Verifiers (parallel) | PoC tests | Both (scope differs by mode) |
| **Phase 6a** | Index Agent (haiku) | Report index with clean IDs + tier assignments | Both |
| **Phase 6a.1** | Orchestrator inline | Completeness assert | Both |
| **Phase 6b** | 3 Tier Writers (parallel: opus C+H, sonnet M, sonnet L+I) | Finding sections per severity tier | Both |
| **Phase 6c** | Assembler Agent (haiku/sonnet) | Final AUDIT_REPORT.md | Both |

---

## Phase 1: Reconnaissance

### Step 1: Spawn Recon Agent
**Read full prompt from**: `~/.claude/prompts/{LANGUAGE}/phase1-recon-prompt.md`

Replace placeholders: `{path}`, `{scratchpad}`, `{docs_path_or_url_if_provided}`, `{network_if_provided}`, `{scope_file_if_provided}`, `{scope_notes_if_provided}`

> **Note**: Recon includes RAG meta-buffer (TASK 0), fork ancestry research, production verification (TASK 11 — MANDATORY), static analysis fallback chain, and template recommendations with BINDING MANIFEST.

### After Recon Returns
1. Verify artifacts exist: `ls {scratchpad}/`
2. Read: `recon_summary.md`, `template_recommendations.md`, `attack_surface.md`
3. **RAG resilience check**: If `meta_buffer.md` does not exist or is empty:
   - Spawn lightweight RAG-retry agent (haiku, <2 min, 3 queries only):
     1. get_common_vulnerabilities(protocol_type)
     2. get_attack_vectors(primary_pattern)
     3. search_solodit_live(protocol_category=[category], quality_score=3, max_results=10)
   - Write results to meta_buffer.md
   - If retry also fails: proceed with empty meta_buffer.md
4. **Hard gate**: ALL artifacts must exist before Phase 2

---

## Phase 2: Orchestrator Instantiation

### Step 2a: Determine Agent Count
| Condition | Agent Count |
|-----------|-------------|
| Simple (<5 deps, <2000 lines) | 2 agents |
| Medium (5-10 deps, 2000-5000 lines) | 4-5 agents |
| Complex (>10 deps or >5000 lines) | 5-7 agents |

**Minimum always**: 1 core state, 1 access control, 1 per major external dep (overrides Simple tier if needed)

**Breadth-to-depth redirect**: When actual breadth agent count is below the Medium baseline (4), the saved slots increase the depth budget floor: `depth_floor = 12 + (4 - actual_breadth_count)`.

### Step 2a.1: Merge Hierarchy (when required templates exceed target count)

| Priority | Merge | Rationale |
|----------|-------|-----------|
| M1 | TEMPORAL_PARAMETER_STALENESS + core state agent | Cached params are state mutations |
| M2 | SEMI_TRUSTED_ROLES + access control agent | Roles are access control |
| M3 | SHARE_ALLOCATION_FAIRNESS + core state agent | Allocation fairness is state correctness |
| M4 | ECONOMIC_DESIGN_AUDIT + core state agent | Monetary params are state correctness |
| M5 | EXTERNAL_PRECONDITION_AUDIT + external dependency agent | External preconditions are external dep analysis |

**Rules**: Never merge two skills both requiring >5 analysis steps. Never merge across incompatible domains. **Never merge FLASH_LOAN_INTERACTION or ORACLE_ANALYSIS with any other skill.** **Max 3 templates per agent (including injectables).**

### Step 2b: Instantiate Templates
For each template in `template_recommendations.md`:
1. Read template from `~/.claude/agents/skills/{LANGUAGE}/{TEMPLATE_NAME}.md`
2. Replace `{PLACEHOLDERS}` with instantiation parameters
3. **Conditional loading**: Strip sections wrapped in `<!-- LOAD_IF: FLAG -->...<!-- END_LOAD_IF: FLAG -->` when the flag was NOT detected
4. Compose agent prompt with instantiated template

### Step 2b.1: Load Injectable Skills (Split Delivery)
1. Read protocol type from `{scratchpad}/template_recommendations.md` → `## Injectable Skills`
2. For each recommended injectable: Read from `~/.claude/agents/skills/injectable/{SKILL_NAME}.md`
3. **Breadth agents**: Extract ONLY section headers + key questions (1-line per section, ~200 tokens max)
4. **Depth agents (Phase 4b)**: Generate specific investigation questions per depth domain. Spawn **dedicated Injectable Investigation Agents** (sonnet, 1 per domain) IN PARALLEL with main depth agents
5. Injectable skills spawn up to 4 dedicated sonnet agents (1 per domain), each costing 1 depth budget slot

### Step 2c: Agent Prompt Structure
```
You are Analysis Agent #{N}: {FOCUS_AREA}

## Protocol Context
{Brief from design_context.md}

## Your Analysis Task
{INSTANTIATED_TEMPLATE}

## Analysis Strategy — Targeted Sweeps
Do NOT attempt to find all vulnerability types in a single pass.
Instead, for each vulnerability class in your methodology:
1. Sweep the ENTIRE scope for THIS class specifically
2. Write findings for this class before moving on
3. Proceed to the next vulnerability class

## Artifacts Available
{list scratchpad files}

## Output Requirements
Write to {SCRATCHPAD}/analysis_{focus_area}.md
Use finding IDs: [{PREFIX}-1], [{PREFIX}-2]...
```

### Step 2d: Spawn Verification Gate (MANDATORY)

**BEFORE spawning agents**:
1. Read BINDING MANIFEST from `{scratchpad}/template_recommendations.md`
2. Verify agent queued for EACH template marked `Required: YES`
3. If ANY required template missing → **HALT and add**

**Write spawn manifest** to `{scratchpad}/spawn_manifest.md`:
```markdown
# Spawn Manifest
| Template | Required? | Agent ID | Focus Area | Status |
|----------|-----------|----------|------------|--------|
**Gate Check**: All REQUIRED templates have agents? [YES/NO]
```

---

## Phase 3: Parallel Analysis

**CRITICAL**: Spawn ALL analysis agents in a SINGLE message as parallel Task calls.

After all return:
1. Verify: `ls {scratchpad}/analysis_*`
2. **Post-spawn verification**: For each REQUIRED template in spawn manifest:
   - `{scratchpad}/analysis_{focus_area}.md` exists
   - File contains findings (not empty/error)
   - Template methodology was applied
3. If ANY required file missing → **Re-spawn that agent before Phase 4a**
4. Update spawn_manifest.md with completion status
5. Do NOT read analysis files — inventory agent reads them

### Phase 3b: Breadth Re-Scan (THOROUGH mode only)

**Skip entirely in Core mode.**

**Read full prompt from**: `~/.claude/rules/phase3b-rescan-prompt.md`

**Flow**: Phase 4a inventory runs first (produces exclusion list), then re-scan loop (sonnet, 2-3 agents, max 2 iterations, exit on 0 new findings above Info), then per-contract analysis (3c), then inventory merges new findings before Phase 4a.5.

---

## Phase 4: Synthesis, Adaptive Depth, Chain Analysis

**Read prompts from the corresponding phase file:**

| Step | Prompt File | Agent | Trigger |
|------|-------------|-------|---------|
| 4a | `~/.claude/prompts/{LANGUAGE}/phase4a-inventory-prompt.md` | Inventory (+ side effect trace) | Always |
| 3b | `~/.claude/rules/phase3b-rescan-prompt.md` | Breadth Re-Scan (sonnet) | Thorough only (after 4a) |
| 4a.5 | (inline below) | Semantic Invariant Agent (sonnet) | Always |
| 4b (loop) | `~/.claude/prompts/{LANGUAGE}/phase4b-loop.md` | Orchestrator | Always |
| 4b (depth) | `~/.claude/prompts/{LANGUAGE}/phase4b-depth-templates.md` | 4 Depth Agents | Always |
| 4b (scanners) | `~/.claude/prompts/{LANGUAGE}/phase4b-scanner-templates.md` | 3 Scanners + Validation + Design Stress | Always |
| 4c | `~/.claude/rules/phase4c-chain-prompt.md` | Chain Analysis (+ enabler enumeration) | Always |
| 5 | `~/.claude/prompts/{LANGUAGE}/phase5-verification-prompt.md` + `~/.claude/rules/phase5-poc-execution.md` | Verifiers (with PoC execution) | Both (scope differs) |
| 5.5 | (orchestrator inline) | Post-verification finding extraction | Always |
| 6a-c | `~/.claude/rules/phase6-report-prompts.md` | Index → Tier Writers → Assembler | Always |

### Gate Enforcement

**After Step 4a**: Read `{scratchpad}/phase4_gates.md`
- **Gate 1 BLOCKED** (missing agents): MUST re-spawn before Step 4b
- **VIOLATION**: Proceeding past BLOCKED gate without resolution

### Phase 4a.5: Semantic Invariant Pre-Computation

> **Purpose**: Enumerate write sites, define semantic invariants, group variables into semantic clusters. Pass 2 (Thorough only) reverses direction for function→cluster coverage and recursive stale-read traces.
> **Models**: Pass 1 sonnet, Pass 2 sonnet (sequential)

Spawn between Phase 4a (inventory) and Phase 4b (depth loop).

**Pass 1 Agent** (Variable → Write Sites + Semantic Clustering):

```
Task(subagent_type="general-purpose", model="sonnet", prompt="
You are Semantic Invariant Agent — Pass 1. You enumerate write sites, define semantic invariants, and group variables into semantic clusters.

## Your Inputs
Read:
- {SCRATCHPAD}/state_variables.md (all state variables from recon)
- {SCRATCHPAD}/function_list.md (all functions)
- Source files referenced in state_variables.md

## Your Task

For EACH accumulator, snapshot, or total-tracking variable in state_variables.md:

1. **Enumerate write sites**: Use grep to find ALL locations that write to this variable.
2. **State the semantic invariant**: In ONE sentence, what SHOULD this variable represent?
3. **Enumerate value-changing functions**: Find ALL functions that change the UNDERLYING VALUE the variable tracks — whether or not they update the variable.
4. **Annotate conditional writes**: For each write site, check if the write is inside a conditional block. If YES, annotate as CONDITIONAL(condition_expression).
4a. **Detect asymmetric branches**: For each CONDITIONAL write, check if the SAME function also writes UNCONDITIONALLY to a different tracking variable. If YES, flag as ASYMMETRIC_BRANCH.
5. **Detect mirror variables**: Identify variable PAIRS tracking the same concept in different storage. For each pair, list ALL functions that write to EITHER. If any function writes to one but not the other → flag as SYNC_GAP.
6. **Flag time-weighted accumulation inputs**: For (value x time_delta) calculations, note controllable inputs and whether time_delta can grow unboundedly. Flag as ACCUMULATION_EXPOSURE if both true.

## Semantic Clustering

Group ALL enumerated variables into semantic clusters — groups of variables collectively representing a single domain or lifecycle. For each cluster, identify which functions write ALL members (full-write) vs only SOME members (partial-write).

## Output

Write to {SCRATCHPAD}/semantic_invariants.md:

### Main Table
| Variable | Contract/Module | Semantic Invariant | Write Sites (with CONDITIONAL annotations) | Value-Changing Functions | Potential Gaps |

### Mirror Variable Pairs
| Variable A | Variable B | Same Concept | Functions Writing A Only | Functions Writing B Only | Sync Gaps |

### Time-Weighted Accumulators
| Accumulator | Formula Pattern | Controllable Input | Time Source | Unbounded Delta? | Exposure |

### Semantic Clusters
| Cluster Name | Variables | Lifecycle Functions | Full-Write Functions | Partial-Write Functions |

Return: 'DONE: {N} variables, {M} gaps, {C} conditional, {S} sync_gaps, {A} accumulation, {K} clusters'
")
```

**Pass 2 Agent** (THOROUGH mode only — Function → Cluster Coverage + Recursive Gap Trace):

```
Task(subagent_type="general-purpose", model="sonnet", prompt="
You are Semantic Invariant Agent — Pass 2. You reverse the analysis direction: for each function, check which clusters it touches incompletely, then recursively trace consequences of stale reads.

## Your Inputs
Read:
- {SCRATCHPAD}/semantic_invariants.md (Pass 1 output)
- {SCRATCHPAD}/function_list.md
- Source files for all Partial-Write Functions from the Semantic Clusters table

## STEP 1: Cluster Coverage Audit

For each Partial-Write Function in the Semantic Clusters table:
1. Which cluster members does it write? Which does it SKIP?
2. For each skipped member: describe in ONE factual sentence WHY it is skipped. This is a FACTUAL ANNOTATION — do NOT judge whether the skip is safe.
3. Flag ALL skips as CLUSTER_GAP — no exceptions.

## STEP 2: Recursive Consequence Trace

For each CLUSTER_GAP, SYNC_GAP, and CONDITIONAL where the skip path is reachable:
1. **Level 0**: Identify the stale variable and the function that leaves it stale
2. **Level 1**: Find ALL functions that READ the stale variable. What value do they produce stale vs correct?
3. **Level 2**: For each Level 1 reader that WRITES a different variable using the stale-derived value, find readers of THAT variable.
4. **Level 3**: Repeat one more level. If error still propagates → flag as DEEP_PROPAGATION.

## STEP 3: Cross-Verify Pass 1 Write Sites

For each function in function_list.md that Pass 1 did NOT list as a write site for ANY variable:
1. Read the function source
2. Check: does it write to ANY state variable from the Main Table?
3. If YES and Pass 1 missed it → add as MISSED_WRITE_SITE

## STEP 4: Branch Path Completeness

For each function with >=2 branches:
1. List variables written on EACH branch path
2. If any branch writes a variable that another branch does NOT → flag as BRANCH_ASYMMETRY
3. For each asymmetry: is the missing write a stale-read source for any consumer?

## Output

Append to {SCRATCHPAD}/semantic_invariants.md:

### Cluster Coverage Gaps
| Function | Cluster | Written Members | Skipped Members | Skip Context (factual) | Flag |

### Recursive Consequence Traces
| Gap Source | Stale Variable | L0 Function | L1 Readers → Impact | L2 Readers → Impact | L3? | Max Window |

### Missed Write Sites (Cross-Verification)
| Variable | Missed Function | Write Type |

### Branch Path Asymmetries
| Function | Condition | Written on True | Written on False | Consumer Impact |

Return: 'DONE: {G} cluster_gaps, {T} consequence traces ({D} deep_propagation), {W} missed_write_sites, {B} branch_asymmetries'
")
```

### Phase 4b: Adaptive Depth Loop

> **Reference**: `~/.claude/rules/phase4-confidence-scoring.md` for scoring model, anti-dilution rules, and convergence criteria.

The orchestrator runs the full loop autonomously:

1. **Iteration 1 (ALWAYS)**: Spawn ALL 8 standard agents + niche agents in parallel:
   - 4 depth agents (token-flow, state-trace, edge-case, external)
   - Blind Spot Scanner A (Tokens & Parameters)
   - Blind Spot Scanner B (Guards, Visibility & Inheritance + Override Safety)
   - Blind Spot Scanner C (Role Lifecycle, Capability Exposure & Reachability)
   - Validation Sweep Agent
   - **Niche agents**: For each REQUIRED niche agent in `template_recommendations.md` → `Niche Agents` section, read its definition from `~/.claude/agents/skills/niche/{NAME}.md` and spawn alongside depth agents. Each niche agent = 1 budget slot.
   - **Timeout split-and-retry**: If any agent times out, split its findings into 2 "lite" agents (max 3 findings each, no static analyzer, max 5 files). 2 lite agents = 1 budget unit.

2. **Score all findings**: Spawn haiku scoring agent → `confidence_scores.md`
   - **Core mode**: 2-axis scoring (Evidence x 0.5 + Analysis Quality x 0.5)
   - **Thorough mode**: 4-axis scoring (Evidence x 0.25 + Consensus x 0.25 + Analysis Quality x 0.3 + RAG Match x 0.2)
   - CONFIDENT (>= 0.7): no more depth needed
   - UNCERTAIN (0.4-0.7): targeted depth
   - LOW CONFIDENCE (< 0.4): targeted depth + production verification + RAG deep search

3. **Iteration 2**:
   - **Core mode**: Skip iteration 2 entirely. Uncertain findings proceed to chain analysis and verification as-is.
   - **Thorough mode**: Spawn targeted Devil's Advocate depth agents per domain for ALL uncertain findings. Hard DA role: agents are structurally adversarial. Severity-weighted budget: spawn_priority = (1 - confidence) * severity_weight.
   - Anti-dilution: evidence-only finding cards, max 5 per agent
   - Re-score with new-evidence-only rule
   - **Loop dynamics detection**: Classify as CONTRACTIVE/OSCILLATORY/EXPLORATORY. If OSCILLATORY → force CONTESTED, exit.

4. **Iteration 3 (Thorough mode only, if still uncertain and progress was made)**: Final targeted pass
   - Force remaining < 0.4 to CONTESTED verdict
   - Write `adaptive_loop_log.md`

5. **Post-verification error trace feedback**: After Phase 5, if verifiers returned CONTESTED with error traces AND budget remains, spawn targeted depth with error traces as investigation questions (AD-6).

**Convergence**: Hard cap 3 iterations (Core: 1), dynamic budget cap `min(max(12, ceil(findings/5)+7), 20)`, progress check after each iteration.

6. **Budget redirect (Thorough mode only)**: If `remaining_budget >= 3` at loop exit, spawn Design Stress Testing Agent.

### Phase 5.1: Skeptic-Judge Verification (Thorough mode only, HIGH/CRIT)

> **Read templates from**: `~/.claude/prompts/{LANGUAGE}/phase5-verification-prompt.md` → "Skeptic-Judge Verification" section

After ALL standard Phase 5 verifiers complete:
1. Identify all HIGH/CRIT findings with standard verdicts
2. For EACH, spawn a skeptic agent (sonnet) with INVERSION MANDATE
3. If skeptic AGREES → final verdict = standard verdict (high confidence)
4. If skeptic DISAGREES → spawn haiku judge ("prove it or lose it" — stronger mechanical evidence wins)
5. Apply final verdict per the ruling table in the verification prompt

**Skip entirely in Core mode.**

### Phase 5.5: Post-Verification Finding Extraction

After ALL verifiers complete:
1. Read all `verify_*.md` files in the scratchpad
2. Extract any `[VER-NEW-*]` observations from "New Observations" sections
3. For each: check if already covered by an existing hypothesis
4. If NOT covered: create a new hypothesis and add to `hypotheses.md`
5. Assign severity using the standard matrix
6. These do NOT require re-verification

---

## FINDING OUTPUT FORMAT

**Full format in**: `~/.claude/rules/finding-output-format.md` — ALL agents MUST read this file and use its format for findings. Includes finding template, Rules Applied table (R4-R16), enforcement rules, and Depth Evidence Tags.

---

## GENERIC SECURITY RULES

**Full rules (R1-R16) in**: `~/.claude/prompts/{LANGUAGE}/generic-security-rules.md` — agents MUST read this file. Key enforcement: CONTESTED → adversarial assumption (R4), REFUTED → requires chain analysis for enablers first (R12).

---

## SELF-CHECK

**Full checklists in**: `~/.claude/prompts/{LANGUAGE}/self-check-checklists.md` — orchestrator MUST read and verify before Phase 5.

Quick checks before verification:
- [ ] All external deps identified?
- [ ] All patterns detected?
- [ ] Fork ancestry research completed?
- [ ] Static analysis fallback used if primary analyzer failed?
- [ ] Production fetch completed?
- [ ] FLASH_LOAN_INTERACTION skill instantiated if FLASH_LOAN or FLASH_LOAN_EXTERNAL flag?
- [ ] ORACLE_ANALYSIS skill instantiated if ORACLE flag?
- [ ] Inventory agent completed side effect trace audit?
- [ ] Static analysis findings promoted?
- [ ] Adaptive depth loop completed?
- [ ] Confidence scores computed?
- [ ] Adaptive loop converged?
- [ ] Chain analysis completed enabler enumeration?
- [ ] Worst-state severity used? (Rule 10)
- [ ] Anti-normalization check applied? (Rule 13)
- [ ] Post-verification finding extraction completed?
