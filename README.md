# Plamen — Web3 Security Auditor for Claude Code

An autonomous smart contract security audit agent for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Orchestrates **15–95 specialized AI agents** across **8 phases** to produce comprehensive security audit reports — from reconnaissance to verified PoC exploits.

Supports **EVM/Solidity**, **Solana/Anchor**, **Aptos Move**, and **Sui Move** via a tree architecture with shared rules and language-specific analysis branches.

Built for **Claude Opus 4.6** (1M context). Works on **Max** (Core/Thorough) and **Pro** (Light mode) subscriptions.

---

## Quick Start

> **Shell**: All commands use Unix syntax. On **Windows**, use **Git Bash** (included with [Git for Windows](https://git-scm.com)). On **macOS/Linux**, use your regular terminal. If `pip`/`python` don't work, try `pip3`/`python3` instead.

> **Why `~/.claude`?** Claude Code reads its configuration, slash commands, agent definitions, and MCP server settings from `~/.claude/`. This is **not configurable** — the repo must be cloned to this exact location for `/plamen` and all pipeline features to work.

> **Existing Claude Code users**: `~/.claude` will be overwritten. Back up your existing config first:
> ```bash
> mv ~/.claude ~/.claude.backup
> ```

```bash
# 1. Clone into ~/.claude (required — Claude Code reads config from this path)
git clone https://github.com/PlamenTSV/plamen.git ~/.claude
cd ~/.claude

# 2. Initialize submodules (slither-mcp, farofino-mcp)
git submodule update --init --recursive

# 3. Install Python deps
pip install -r requirements.txt

# 4. Install MCP server deps (~2GB download — includes PyTorch for embeddings)
pip install -r custom-mcp/unified-vuln-db/requirements.txt
pip install -r custom-mcp/solodit-scraper/requirements.txt
pip install -r custom-mcp/defihacklabs-rag/requirements.txt
pip install -e custom-mcp/solana-fender
pip install -r custom-mcp/farofino-mcp/requirements.txt

# 4b. EVM users only — install slither MCP (requires Python 3.11+, solc)
pip install -e custom-mcp/slither-mcp    # skip if not auditing Solidity

# 5. Configure MCP servers + API keys (BEFORE building RAG)
cp mcp.json.example mcp.json
cp settings.json.example settings.json
# Edit mcp.json — add your API keys (see Configuration below)
# At minimum, get a free Solodit key: https://solodit.cyfrin.io
# Then set it for the current shell:
export SOLODIT_API_KEY=your_key_here     # needed for step 6

# 6. Build the RAG vulnerability database (~5 min, requires internet)
#    Without SOLODIT_API_KEY: only ~700 entries indexed (vs ~4000 with it)
cd custom-mcp/unified-vuln-db
python -m unified_vuln.indexer index -s solodit --max-pages 10
python -m unified_vuln.indexer index -s defihacklabs
python -m unified_vuln.indexer index -s immunefi
cd ../..

# 7. Run (terminal wrapper with interactive UI)
python plamen.py
# Or from Claude Code: /plamen
# Or add ~/.claude to PATH and just type: plamen
```

You'll need a smart contract project to audit (e.g., a Foundry or Hardhat project). The Setup menu inside the wrapper can install chain-specific tools (Foundry, Solana, Aptos, Sui) for you.

> **Having trouble?** Open Claude Code and paste the contents of [`SETUP.md`](SETUP.md) — it contains step-by-step instructions that Claude Code can follow to install everything for you automatically.

---

## Table of Contents

- [Architecture](#architecture)
- [Audit Modes](#audit-modes)
- [How It Works](#how-it-works)
- [Language Support](#language-support)
- [Skill System](#skill-system)
- [Security Rules](#security-rules)
- [MCP Servers](#mcp-servers)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Building the RAG Database](#building-the-rag-database)
- [Two Ways to Run](#two-ways-to-run)
- [Repository Structure](#repository-structure)
- [Severity Matrix](#severity-matrix)
- [Cost Estimation](#cost-estimation)
- [Contributing](#contributing)
- [License](#license)

---

## Architecture

```
                          ┌─────────────────────────────────┐
                          │       ORCHESTRATOR (CLAUDE.md)   │
                          │  Detects language, reads phase   │
                          │  prompts, spawns agents,         │
                          │  enforces gates                  │
                          └──────────┬──────────────────────┘
                                     │
          ┌──────────────────────────┼──────────────────────────┐
          ▼                          ▼                          ▼
   ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
   │  Phase 1     │          │  Phase 2     │          │  Phase 3     │
   │  RECON       │───────►  │  INSTANTIATE │───────►  │  BREADTH     │
   │  (4 agents)  │          │  (orchestr.) │          │  (2-7 agents)│
   └──────────────┘          └──────────────┘          └──────┬───────┘
                                                              │
          ┌───────────────────────────────────────────────────┘
          ▼
   ┌──────────────┐     ┌──────────────┐     ┌─────────────────┐
   │  Phase 3b    │     │  Phase 3c    │     │  Phase 4a       │
   │  RE-SCAN     │──►  │  PER-CONTRACT│──►  │  INVENTORY      │
   │  (sonnet,    │     │  (sonnet,    │     │  + Side Effect  │
   │   2 iters)   │     │   1/cluster) │     │  Trace Audit    │
   └──────────────┘     └──────────────┘     └──────┬──────────┘
                                                    │
          ┌─────────────────────────────────────────┘
          ▼
   ┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
   │  Phase 4a.5  │     │  Phase 4b       │     │  Phase 4c    │
   │  SEMANTIC    │──►  │  DEPTH LOOP     │──►  │  CHAIN       │
   │  INVARIANTS  │     │  (8+ agents ×   │     │  ANALYSIS    │
   │  (sonnet)    │     │   1-3 iters)    │     │  + Enablers  │
   └──────────────┘     │  + Niche agents │     └──────┬───────┘
                        │  + Inv. Fuzz    │            │
                        │  + Medusa Fuzz  │            │
                        │  + Design Stress│            │
                        └─────────────────┘            │
                                                       │
          ┌────────────────────────────────────────────┘
          ▼
   ┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
   │  Phase 5     │     │  Phase 5.1   │     │  Phase 6         │
   │  VERIFY      │──►  │  SKEPTIC-    │──►  │  REPORT          │
   │  (N verifier │     │  JUDGE       │     │  Index → 3 Tier  │
   │   agents)    │     │  (Thorough)  │     │  Writers →       │
   └──────────────┘     └──────────────┘     │  Assembler       │
                                              └──────────────────┘
                                                      │
                                                      ▼
                                               AUDIT_REPORT.md
```

The workflow is fully autonomous — provide a smart contract project and optionally documentation. The orchestrator detects the language, loads the appropriate branch, and handles everything from pattern detection to PoC verification to final report assembly.

---

## Audit Modes

| Dimension | Light | Core | Thorough |
|-----------|-------|------|----------|
| **Target plan** | **Pro** | Max | Max |
| Agent models | All Sonnet/Haiku | Opus + Sonnet | Opus + Sonnet |
| Recon | 2 sonnet (no RAG) | 4 agents | 4 agents (full RAG) |
| Breadth | 2-3 sonnet | 2-7 opus | 2-7 opus |
| Re-scan (3b/3c) | Skip | Skip | Full (2 iter + per-contract) |
| Depth loop | 4 merged sonnet, iter 1 | 8+ agents, iter 1 | Iter 1-3 (Devil's Advocate) |
| Niche agents | Skip | Flag-triggered | Flag-triggered |
| Semantic invariants | Skip (state consistency tradeoff) | Pass 1 | Pass 1 + Pass 2 |
| Confidence scoring | None (verdicts only) | 2-axis | 4-axis |
| RAG Sweep | Skip | 1 haiku | 1 haiku |
| Invariant / Medusa fuzz | Skip | Skip | Yes (EVM) |
| Chain analysis | 1 sonnet (merged) | 2 agents | 2 agents + iteration 2 |
| Verification (PoC) | Medium+ (sonnet) | Medium+ | ALL severities + fuzz |
| Skeptic-Judge | Skip | Skip | HIGH/CRIT |
| Report | 2 agents | 5 agents | 5 agents |
| Agent count | **~15-18** | ~25-45 | ~35-95 |

**Proven-only mode** (`--proven-only`): Available in all modes. Caps findings with only `[CODE-TRACE]` evidence (no executed PoC or fuzzer counterexample) at Low severity. Useful for benchmark comparisons where only mechanically proven findings should drive severity.

---

## How It Works

### Phase 1: Reconnaissance (4 parallel agents)

Split into 4 agents to prevent timeout:
- **Agent 1A (sonnet)**: RAG queries — unified-vuln-db, Solodit live search
- **Agent 1B (opus)**: Documentation parsing, fork ancestry research, trust model extraction
- **Agent 2 (sonnet)**: Build environment, static analysis (Slither → Farofino/Aderyn → grep fallback), test suite
- **Agent 3 (opus)**: Pattern detection, attack surface mapping, template recommendations with BINDING MANIFEST

Produces 17+ scratchpad artifacts consumed by all downstream phases.

### Phase 2: Instantiation (orchestrator)

Reads the BINDING MANIFEST, resolves skill templates, applies merge hierarchy (max 3 skills/agent), and composes agent prompts with instantiated parameters.

### Phase 3: Parallel Breadth Analysis (2-7 agents)

All agents spawned in a single message. Each runs a targeted sweep per vulnerability class across its scope, producing findings with precondition/postcondition analysis.

### Phase 3b/3c: Re-Scan + Per-Contract (Thorough only)

- **Re-scan**: 2-3 sonnet agents re-analyze with an exclusion list of known findings. Counters LLM attention saturation.
- **Per-contract**: 1 agent per contract/cluster at maximum depth. Zero distraction from other contracts.

### Phase 4a: Inventory + Side Effect Trace

Consolidates all findings, promotes static analysis results, performs side effect trace audit on external token interactions.

### Phase 4a.5: Semantic Invariant Pre-Computation

Sonnet agent enumerates write sites, defines semantic invariants, detects mirror variables, flags conditional writes and accumulation exposures. Pass 2 (Thorough) traces consequences recursively.

### Phase 4b: Adaptive Depth Loop (8+ agents × 1-3 iterations)

**Iteration 1** (always): 4 depth agents + 3 blind spot scanners + validation sweep + niche agents, all in parallel.

| Depth Agent | Model | Focus |
|-------------|-------|-------|
| depth-token-flow | opus | Balance invariants, mint/burn, transfer side effects |
| depth-state-trace | opus | Cross-function state mutation, constraint enforcement |
| depth-edge-case | sonnet | Boundary values, zero state, overflow, first-user |
| depth-external | sonnet | External call effects, oracle integrity, cross-chain timing |

| Scanner | Focus |
|---------|-------|
| Blind Spot A | External token coverage, parameter governance, msg.value loops, returnbomb |
| Blind Spot B | Guards, visibility, inheritance, override safety |
| Blind Spot C | Role lifecycle, capability exposure, reachability |
| Validation Sweep | Write completeness, struct validation, sibling propagation |

**Niche agents** (flag-triggered, 1 budget slot each):
- EVENT_COMPLETENESS — event emission coverage
- SEMANTIC_GAP_INVESTIGATOR — sync gaps, accumulation exposure, conditional writes
- SPEC_COMPLIANCE_AUDIT — spec-to-code compliance
- SIGNATURE_VERIFICATION_AUDIT — replay, malleability, EIP-712, nonces
- SEMANTIC_CONSISTENCY_AUDIT — cross-contract unit mismatches, formula drift, magic numbers

**Invariant fuzzing** (EVM Thorough only):
- Foundry invariant fuzz campaign (from semantic invariants)
- Medusa stateful fuzz campaign (parallel, standalone harness, 15-min timeout)

**Iterations 2-3** (Thorough): Devil's Advocate agents re-examine uncertain findings with structural adversarial role, contrastive path summaries, fresh MCP calls.

**Confidence scoring** (haiku, batched): 4-axis model (Evidence × 0.25 + Consensus × 0.25 + Analysis Quality × 0.3 + RAG Match × 0.2). Routes findings to CONFIDENT/UNCERTAIN/LOW_CONFIDENCE with severity-weighted spawn priority.

### Phase 4c: Chain Analysis (2 sequential agents)

- **Agent 1**: Exhaustive enabler enumeration (5 actor categories per dangerous state), finding grouping with anti-absorption rules
- **Agent 2**: Postcondition→precondition chain matching, composition coverage map, RAG validation for chains

### Phase 5: Verification (parallel verifiers)

Mandatory PoC execution. Every finding gets:
1. Written PoC from language-specific templates
2. Compiled and executed (max 5 retry attempts with structured recovery)
3. Fuzz variant (Medium+, Thorough mode)
4. Evidence tagged: `[POC-PASS]`, `[POC-FAIL]`, `[CODE-TRACE]`, `[MEDUSA-PASS]`

Pre-PoC feasibility gates (Reachability + Math Bounds) prevent wasted verification effort.

### Phase 5.1: Skeptic-Judge (Thorough, HIGH/CRIT only)

After standard verification:
1. **Skeptic** (sonnet): INVERSION MANDATE — if standard said CONFIRMED, skeptic tries to REFUTE, and vice versa
2. If skeptic AGREES → high confidence (dual-confirmed)
3. If skeptic DISAGREES → **Judge** (haiku): "prove it or lose it" — stronger mechanical evidence wins

### Phase 6: Report Generation (5 agents)

- **Index Agent** (haiku): Clean ID assignment, root-cause consolidation, tier assignment, strict mode demotion
- **3 Tier Writers** (parallel): Opus for Critical+High, Sonnet for Medium, Sonnet for Low+Info
- **Assembler** (haiku/sonnet): Merges sections, quality checks, writes `AUDIT_REPORT.md`

---

## Language Support

Tree architecture — shared root with language-specific branches. No file contains content for more than one language.

| Language | Skills | Build | Static Analysis | Fuzz | On-chain |
|----------|--------|-------|----------------|------|----------|
| **EVM/Solidity** | 18 | Foundry, Hardhat | Slither MCP, Farofino/Aderyn, grep | Foundry invariant, Medusa | etherscan, fork testing |
| **Solana/Anchor** | 19 | Anchor, cargo-build-sbf | Fender MCP, grep | Trident, proptest | Helius |
| **Aptos Move** | 21 | aptos move compile | Move Prover, grep | Boundary-value parameterized | — |
| **Sui Move** | 21 | sui move build | grep | Boundary-value parameterized | — |

Language detection is automatic (Step 0) based on config files and source file patterns.

---

## Skill System

Skills are methodology files loaded into agents at instantiation time. Three tiers:

### Standard Skills (per-language tree)

Always-available skills triggered by pattern flags from recon. Examples: `ORACLE_ANALYSIS`, `SEMI_TRUSTED_ROLES`, `TOKEN_FLOW_TRACING`, `FLASH_LOAN_INTERACTION`.

### Injectable Skills (protocol-type-specific)

Loaded only when recon classifies the protocol as a matching type. Appended to existing agents (no new agent spawned):

| Skill | Trigger |
|-------|---------|
| VAULT_ACCOUNTING | `vault` protocol type |
| ACCOUNT_ABSTRACTION_SECURITY | ERC-4337, EntryPoint, UserOperation |
| NFT_PROTOCOL_SECURITY | ERC721/1155 with marketplace/staking/collateral |
| GOVERNANCE_ATTACK_VECTORS | Governor, Timelock, voting, proposal |
| OUTCOME_DETERMINISM | Finite-pool selection with depletion fallback + time-gated actions |

### Niche Agents (flag-triggered standalone)

Spawn as independent agents (1 depth budget slot each). Used when a concern needs dedicated focus:

| Agent | Trigger | Checks |
|-------|---------|--------|
| EVENT_COMPLETENESS | `MISSING_EVENT` | Event coverage, parameter accuracy, cross-contract gaps |
| SEMANTIC_GAP_INVESTIGATOR | Semantic invariant flags | SYNC_GAP, ACCUMULATION_EXPOSURE, CONDITIONAL, CLUSTER_GAP |
| SPEC_COMPLIANCE_AUDIT | `HAS_DOCS` | Spec-to-code compliance, testable claim verification |
| SIGNATURE_VERIFICATION_AUDIT | `HAS_SIGNATURES` | Replay, malleability, EIP-712, permit, nonces |
| SEMANTIC_CONSISTENCY_AUDIT | `HAS_MULTI_CONTRACT` | Unit mismatches, formula drift, magic number consistency |

---

## Security Rules

16 rules (R1–R16) enforced across all agents:

| Rule | Name | Summary |
|------|------|---------|
| R1 | External Return Types | Verify all external call return values |
| R2 | Keeper/Admin Griefability | Check both directions of privileged action abuse |
| R3 | Transfer Side Effects | Document token type and side effects |
| R4 | Adversarial Assumption | CONTESTED/unknown → assume adversarial |
| R5 | Combinatorial Impact | N-entity systems need combinatorial analysis |
| R6 | Bidirectional Role | Semi-trusted roles analyzed in both directions |
| R7 | Donation-based DoS | Check thresholds vulnerable to donations |
| R8 | Cached Parameters | Multi-step ops with stale external state |
| R9 | Stranded Assets | Check recovery paths for locked funds |
| R10 | Worst-State Severity | Use worst realistic state, not current snapshot |
| R11 | Unsolicited Token Transfer | Trace impact of uninitiated transfers |
| R12 | Exhaustive Enabler Enum | 5 actor categories per dangerous state |
| R13 | Anti-Normalization | "By design" is not a valid severity dismissal |
| R14 | Cross-Variable Invariant | Aggregate variables, constraint coherence, setter regression |
| R15 | Flash Loan Precondition | Flash-loan-accessible state manipulation |
| R16 | Oracle Integrity | Staleness, decimals, zero, failure modes |

---

## MCP Servers

Plamen uses 9 MCP servers configured in `mcp.json`. 2 are bundled in `custom-mcp/`, 2 are git submodules, 5 are npm packages. Two additional bundled libraries (`solodit-scraper`, `defihacklabs-rag`) serve as data sources for the RAG database.

### Bundled (custom-mcp/)

| Server | Purpose | Required? |
|--------|---------|-----------|
| **unified-vuln-db** | RAG vulnerability database — Solodit, DeFiHackLabs, Immunefi. Semantic search, hypothesis validation, root cause analysis | **Required** |
| **solodit-scraper** | Solodit API scraper with SQLite cache, rate limiting | Required by unified-vuln-db |
| **defihacklabs-rag** | DeFiHackLabs exploit analysis with ChromaDB embeddings | Optional (enriches RAG) |
| **solana-fender** | Solana program static security analysis | Optional (Solana only) |

### Submodules (custom-mcp/)

| Server | Purpose | Required? |
|--------|---------|-----------|
| **[slither-mcp](https://github.com/trailofbits/slither-mcp)** | Slither static analyzer by Trail of Bits | Optional (EVM, falls back to grep) |
| **[farofino-mcp](https://github.com/italoag/farofino-mcp)** | Aderyn + pattern analysis fallback | Optional (EVM, when Slither fails) |

### npm Packages (installed on demand via npx)

| Server | Purpose | API Key? |
|--------|---------|----------|
| **foundry-suite** | Anvil fork testing, Forge scripts, Heimdall bytecode | No |
| **evm-chain-data** | On-chain ABI/state queries via Etherscan | Optional (free key) |
| **tavily-search** | Web search for fork ancestry + documentation | Optional (free key) |
| **helius** | Solana on-chain account/transaction data | Optional (free key) |
| **memory** | Persistent memory across sessions | No |

---

## Prerequisites

### Required

| Tool | Purpose | Install |
|------|---------|---------|
| **Claude Code CLI** | The AI runtime | [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code) |
| **Python 3.11+** | MCP servers, plamen.py wrapper | [python.org](https://python.org) |
| **Node.js 18+** / **npx** | npm MCP servers (foundry-suite, tavily, etc.) | [nodejs.org](https://nodejs.org) |
| **Git** | Dependency resolution, submodules | [git-scm.com](https://git-scm.com) |

### Per-Language (install what you need)

**EVM/Solidity:**

| Tool | Purpose | Install |
|------|---------|---------|
| Foundry (forge, anvil, cast) | Build, test, fork testing | `curl -L https://foundry.paradigm.xyz \| bash && foundryup` |
| Slither | Static analysis | `pip install slither-analyzer` |
| Medusa | Stateful fuzzing (Thorough mode) | [github.com/crytic/medusa](https://github.com/crytic/medusa/releases) |

**Solana:**

| Tool | Purpose | Install |
|------|---------|---------|
| Solana CLI | Toolchain, account dumps | [docs.anza.xyz](https://docs.anza.xyz/cli/install) |
| Anchor | Build Anchor programs | `avm install latest && avm use latest` |
| Trident | Stateful fuzzing | `cargo install trident-cli` |

**Aptos Move:**

| Tool | Purpose | Install |
|------|---------|---------|
| Aptos CLI | Build, test, prove | [aptos.dev/build/cli](https://aptos.dev/build/cli) |

**Sui Move:**

| Tool | Purpose | Install |
|------|---------|---------|
| Sui CLI | Build, test | [docs.sui.io](https://docs.sui.io/guides/developer/getting-started/sui-install) |

---

## Installation

### 1. Clone and initialize

```bash
git clone https://github.com/PlamenTSV/plamen.git ~/.claude
cd ~/.claude
git submodule update --init --recursive
```

> **Note**: This clones into `~/.claude` which is where Claude Code looks for its configuration. If you already have a `~/.claude` directory, back it up first.

### 2. Install Python dependencies

```bash
# Plamen wrapper
pip install -r requirements.txt

# MCP servers (~2GB download — includes PyTorch for embeddings)
pip install -r custom-mcp/unified-vuln-db/requirements.txt
pip install -r custom-mcp/solodit-scraper/requirements.txt
pip install -r custom-mcp/defihacklabs-rag/requirements.txt
pip install -e custom-mcp/solana-fender
pip install -r custom-mcp/farofino-mcp/requirements.txt

# EVM users only (requires Python 3.11+, solc)
pip install -e custom-mcp/slither-mcp
```

### 3. Configure MCP servers and API keys

```bash
cp mcp.json.example mcp.json
cp settings.json.example settings.json
```

Edit `mcp.json` with your API keys. See [Configuration](#configuration). At minimum, get a free [Solodit API key](https://solodit.cyfrin.io) — it's needed to index the largest RAG data source (3400+ findings).

The default `settings.json.example` auto-approves all tool calls required for autonomous auditing. **Critical permissions** — removing any of these will break the pipeline:

| Permission | Why Required |
|-----------|-------------|
| `Agent(*)` | Spawns all subagents (depth, scanner, verifier, chain analysis). **Without this, the pipeline silently fails.** |
| `Bash(*)` | Runs `forge build/test`, `cargo test`, `aptos move test`, etc. |
| `Read(*)`, `Write(*)`, `Edit(*)` | Reads source code, writes PoC tests, edits scratchpad artifacts |
| `mcp__*` | All MCP server tool calls (Slither, RAG, Solodit, Foundry, etc.) |

The deny list blocks destructive operations (`rm -rf`, `sudo`, force push). Review and adjust if desired.

### 4. Build the RAG database

Set your Solodit key first, then build:

```bash
export SOLODIT_API_KEY=your_key_here
```

See [Building the RAG Database](#building-the-rag-database) for full instructions.

### 5. Verify installation

```bash
python plamen.py
```

The startup screen runs a dependency check showing which tools are available.

---

## Configuration

### mcp.json

Copy `mcp.json.example` to `mcp.json` and configure:

The example below shows a subset. Copy `mcp.json.example` for the full 9-server configuration including `solana-fender`, `farofino`, and `memory`.

```json
{
  "mcpServers": {
    "slither-analyzer": {
      "command": "slither-mcp",
      "args": []
    },
    "unified-vuln-db": {
      "command": "python",
      "args": ["-m", "unified_vuln.server"],
      "cwd": "./custom-mcp/unified-vuln-db"
    },
    "foundry-suite": {
      "command": "npx",
      "args": ["-y", "@pranesh.asp/foundry-mcp-server"],
      "env": { "RPC_URL": "YOUR_RPC_URL" }
    },
    "evm-chain-data": {
      "command": "npx",
      "args": ["-y", "@mcpdotdirect/evm-mcp-server"],
      "env": { "ETHERSCAN_API_KEY": "YOUR_KEY" }
    },
    "tavily-search": {
      "command": "npx",
      "args": ["-y", "tavily-mcp"],
      "env": { "TAVILY_API_KEY": "YOUR_KEY" }
    },
    "helius": {
      "command": "npx",
      "args": ["-y", "helius-mcp@latest"],
      "env": { "HELIUS_API_KEY": "YOUR_KEY" }
    }
  }
}
```

**Path notes**: The `cwd` fields use relative paths (`./custom-mcp/...`). Claude Code resolves these relative to `~/.claude/`. If you installed elsewhere, use absolute paths.

### API Keys

| Key | Where to Get | Cost | Used For |
|-----|-------------|------|----------|
| Solodit | [solodit.cyfrin.io](https://solodit.cyfrin.io) | Free | RAG database indexing (3400+ findings) + live search |
| Etherscan | [etherscan.io/apis](https://etherscan.io/apis) | Free | Contract ABI verification |
| Tavily | [tavily.com](https://tavily.com) | Free tier | Fork ancestry web search, RAG fallback |
| Helius | [helius.dev](https://helius.dev) | Free tier | Solana on-chain data |
| RPC URL | Alchemy, Infura, or public | Free/Paid | Fork testing |

All keys are optional. The pipeline degrades gracefully — missing keys mean reduced coverage, not failure. You can leave `YOUR_*` placeholders in `mcp.json` and the pipeline will skip those services.

> **Recommended**: Get the free Solodit API key — it provides 3400+ indexed findings vs ~700 without it. The Tavily key is also recommended as the WebSearch fallback when RAG MCP tools are slow on first use.

---

## Building the RAG Database

The unified-vuln-db MCP server uses a ChromaDB vector database populated from three sources. Build it before your first audit.

### Quick build (~5 minutes)

```bash
cd custom-mcp/unified-vuln-db

# Index from all sources
python -m unified_vuln.indexer index -s solodit --max-pages 10
python -m unified_vuln.indexer index -s defihacklabs
python -m unified_vuln.indexer index -s immunefi

# Verify
python -m unified_vuln.indexer stats
```

### Full build (~30 minutes, better RAG quality)

```bash
# Index ALL Solodit findings (thousands of pages)
python -m unified_vuln.indexer index -s solodit --max-pages 100

# Clone DeFiHackLabs dataset for deeper coverage
git clone https://github.com/SunWeb3Sec/DeFiHackLabs.git data/DeFiHackLabs
python -m unified_vuln.indexer index -s defihacklabs

# Immunefi
python -m unified_vuln.indexer index -s immunefi
```

### Rebuild from scratch

```bash
python -m unified_vuln.indexer clear
# Then run the index commands above
```

The `data/` directory (ChromaDB, caches) is gitignored. Each user builds their own database.

### Cold start note

The first MCP tool call in each Claude Code session loads the ChromaDB database and sentence-transformers embedding model into memory. This can take 1-5 minutes depending on your hardware. Subsequent calls are instant. The pipeline handles this automatically — the recon agent's probe-first pattern detects slow MCP responses and falls back to WebSearch (Tavily) or code-level analysis.

---

## Two Ways to Run

Plamen can be launched from its **dedicated terminal wrapper** or from **inside Claude Code**. Both go through the same audit pipeline — the difference is how you start it.

### Option A: Terminal Wrapper (recommended for first-time setup)

The terminal wrapper is a standalone Rich + InquirerPy application that handles dependency checking, tool installation, cost estimation, and launches Claude Code for you.

```bash
plamen
```

This opens an interactive UI with arrow-key menus:

```
 ██████╗ ██╗      █████╗ ███╗   ███╗███████╗███╗   ██╗
 ██╔══██╗██║     ██╔══██╗████╗ ████║██╔════╝████╗  ██║
 ██████╔╝██║     ███████║██╔████╔██║█████╗  ██╔██╗ ██║
 ██╔═══╝ ██║     ██╔══██║██║╚██╔╝██║██╔══╝  ██║╚██╗██║
 ██║     ███████╗██║  ██║██║ ╚═╝ ██║███████╗██║ ╚████║
 ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═══╝

⬡ Web3 Security Auditor  v1.0.0

> Select audit mode:
    Light      15-18 agents | Pro plan  | best under 3k LOC
    Core       25-45 agents | Max plan  | ALL severities
    Thorough   35-95 agents | Max plan  | ALL severities + fuzz
    ──────────
    Compare    variable     | DELTA report
    Setup      install tools + build RAG DB
```

The **Setup** option shows your full toolchain status and lets you install missing tools (Foundry, Solana, Aptos, Sui, Medusa, etc.) and build the RAG database — all from the same UI, with automatic prerequisite detection and cross-platform support (Windows, macOS, Linux).

After selecting a mode, the wrapper walks you through target selection, documentation, scope, proven-only mode, shows a cost estimate, then hands off to Claude Code.

**To make `plamen` available as a command**, add `~/.claude` to your PATH:

```bash
# Unix/macOS — add to ~/.bashrc or ~/.zshrc
export PATH="$HOME/.claude:$PATH"

# Windows — run once in PowerShell
[System.Environment]::SetEnvironmentVariable("Path", "$env:USERPROFILE\.claude;" + $env:Path, "User")
```

Or run directly without PATH setup:

```bash
python ~/.claude/plamen.py          # any platform
./plamen.sh                         # Unix/macOS
plamen.bat                          # Windows
```

**CLI fast path** (skip the wizard):

```bash
plamen core /path/to/project --docs whitepaper.pdf
plamen thorough /path/to/project --scope scope.txt --network ethereum --proven-only
plamen setup                        # just run the installer
```

### Option B: Inside Claude Code (`/plamen` command)

If you're already in a Claude Code session, type `/plamen` to launch the audit wizard directly:

```
> /plamen
```

This presents a mode selection dialog with previews inside Claude Code, then walks you through target, docs, scope, and launches the full pipeline — all within the same session.

You can also skip the wizard with arguments:

```
> /plamen core /path/to/project docs: /path/to/docs
> /plamen thorough /path/to/project scope: scope.txt proven-only: true
> /plamen compare report: audit.md ground_truth: reference.md
```

### When to use which

| | Terminal Wrapper (`plamen`) | Claude Code (`/plamen`) |
|---|---|---|
| **First time** | Use this — Setup installs tools + builds RAG | Need tools already installed |
| **Cost estimate** | Shows token/cost estimate before launch | No estimate |
| **Dependency check** | Full toolchain box with install option | Basic toolchain probe |
| **Daily use** | Quick CLI: `plamen core .` | Quick command: `/plamen core .` |
| **Already in Claude** | Opens new Claude session | Uses current session |

---

## Repository Structure

```
~/.claude/
├── CLAUDE.md                          # Orchestrator — mode table, critical rules, file refs
├── plamen.py                          # Terminal wrapper (Rich + InquirerPy)
├── plamen.sh / plamen.bat             # Launcher scripts
├── VERSION                            # Semantic version
│
├── commands/
│   └── plamen.md                      # /plamen slash command — wizard + full workflow
│
├── rules/                             # Shared rules (all languages)
│   ├── finding-output-format.md       # Finding template, Rules Applied, Depth Evidence Tags
│   ├── phase3b-rescan-prompt.md       # Breadth re-scan (Thorough)
│   ├── phase4-confidence-scoring.md   # 4-axis scoring, anti-dilution, convergence
│   ├── phase4c-chain-prompt.md        # Chain analysis — enabler enum + chain matching
│   ├── phase5-poc-execution.md        # Mandatory PoC execution protocol
│   ├── phase6-report-prompts.md       # Report pipeline — Index → Tier Writers → Assembler
│   ├── report-template.md             # Report format, severity matrix, consolidation
│   ├── skill-index.md                 # Master skill registry (all trees)
│   └── post-audit-improvement-protocol.md  # Compare mode methodology
│
├── agents/                            # Agent definitions (language-agnostic)
│   ├── depth-token-flow.md
│   ├── depth-state-trace.md
│   ├── depth-edge-case.md
│   ├── depth-external.md
│   ├── security-analyzer.md
│   └── security-verifier.md
│
├── prompts/                           # Language-specific prompts
│   ├── evm/                           # 10 files (includes invariant-fuzz)
│   ├── solana/                        # 9 files
│   ├── aptos/                         # 9 files
│   └── sui/                           # 9 files
│   # Each tree contains:
│   #   phase1-recon-prompt.md         — 4-agent recon with BINDING MANIFEST
│   #   phase4a-inventory-prompt.md    — inventory + side effect trace
│   #   phase4b-loop.md               — adaptive depth loop orchestration
│   #   phase4b-depth-templates.md    — 4 depth agent prompts
│   #   phase4b-scanner-templates.md  — 3 scanners + validation sweep + design stress
│   #   phase5-verification-prompt.md — verifier + skeptic-judge (EVM)
│   #   generic-security-rules.md     — R1-R16 enforcement
│   #   self-check-checklists.md      — per-phase quality gates
│   #   mcp-tools-reference.md        — MCP tool usage guide
│
├── agents/skills/
│   ├── evm/                           # 18 EVM skill templates
│   ├── solana/                        # 19 Solana skill templates
│   ├── aptos/                         # 21 Aptos skill templates
│   ├── sui/                           # 21 Sui skill templates
│   ├── injectable/                    # 5 protocol-type-specific skills
│   └── niche/                         # 5 flag-triggered niche agents
│
├── custom-mcp/                        # MCP servers
│   ├── unified-vuln-db/               # RAG vulnerability database (code only, data/ gitignored)
│   │   ├── unified_vuln/
│   │   │   ├── server.py              # MCP server entry point
│   │   │   ├── indexer.py             # CLI: index, clear, stats
│   │   │   ├── database.py            # ChromaDB interface
│   │   │   ├── schema.py              # Vulnerability data model
│   │   │   ├── chunking.py            # Document chunking
│   │   │   └── sources/               # Data source adapters
│   │   │       ├── solodit.py         # Solodit API + content parser
│   │   │       ├── defihacklabs.py    # DeFiHackLabs exploit corpus
│   │   │       ├── immunefi.py        # Immunefi bug bounty data
│   │   │       └── huggingface.py     # Disabled (diluted results)
│   │   ├── requirements.txt
│   │   └── setup.py
│   ├── solodit-scraper/               # Solodit API scraper with SQLite cache
│   │   ├── solodit_mcp/
│   │   │   ├── server.py
│   │   │   ├── scraper.py
│   │   │   └── database.py
│   │   └── requirements.txt
│   ├── defihacklabs-rag/              # DeFiHackLabs ChromaDB embeddings
│   │   ├── defihacklabs_mcp/
│   │   │   ├── server.py
│   │   │   └── indexer.py
│   │   └── requirements.txt
│   ├── solana-fender/                 # Solana static analysis
│   │   ├── solana_fender_mcp/
│   │   │   ├── __init__.py
│   │   │   └── __main__.py
│   │   └── setup.py
│   ├── farofino-mcp/                  # [submodule] Aderyn + pattern analysis
│   └── slither-mcp/                   # [submodule] Trail of Bits Slither
│
├── mcp.json.example                   # MCP server config template
├── settings.json.example             # Permissions config template
├── requirements.txt                   # Python deps (Rich, InquirerPy)
├── .gitmodules                        # Submodule refs (farofino, slither)
└── .gitignore
```

---

## Severity Matrix

Impact × Likelihood:

| | **Likelihood: High** | **Likelihood: Medium** | **Likelihood: Low** |
|---|---|---|---|
| **Impact: High** (direct fund loss) | **Critical** | **High** | **Medium** |
| **Impact: Medium** (conditional fund loss) | **High** | **Medium** | **Medium** |
| **Impact: Low** (non-fund) | **Medium** | **Low** | **Low** |
| **Impact: Info** (quality, style) | **Informational** | **Informational** | **Informational** |

Downgrade modifiers: on-chain-only exploit (−1 tier), view-function-only (cap Medium), fully-trusted actor required (−1 tier, floor Info).

---

## Cost Estimation

The `plamen.py` wrapper estimates token usage before launch, accounting for multi-turn context accumulation per agent. Estimates displayed in the launch summary:

- **Input/Output tokens** (millions)
- **API cost** (USD, at current Anthropic pricing)
- **Weekly plan usage** (% of Pro, Max x5, and Max x20 allowances)

Estimates are rough — actual usage varies with protocol complexity and finding count. Run `/cost` after an audit for actuals.

---

## Evidence Tags

Findings carry evidence tags that determine confidence scoring:

| Tag | Weight | Meaning |
|-----|--------|---------|
| `[PROD-ONCHAIN]` | 1.0 | Verified against production on-chain state |
| `[PROD-SOURCE]` | 0.9 | Verified against production source code |
| `[PROD-FORK]` | 0.9 | Verified on Anvil fork of production |
| `[MEDUSA-PASS]` | 1.0 | Medusa fuzzer found counterexample |
| `[POC-PASS]` | 1.0 | PoC compiled, executed, assertions passed |
| `[POC-FAIL]` | — | PoC executed but assertions failed |
| `[CODE]` | 0.8 | Code-level evidence with specific locations |
| `[CODE-TRACE]` | 0.6 | Manual trace with concrete values, no execution (caps at CONTESTED) |
| `[DOC]` | 0.4 | Documentation-based evidence |
| `[MOCK]` | 0.2 | Mock-based (not production-representative) |
| `[EXT-UNV]` | 0.1 | External/unverified claim |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short:

1. Fork the repository
2. Create a feature branch
3. Follow the anti-bloat gates from `post-audit-improvement-protocol.md`
4. Submit a PR with the template

---

## License

[MIT](LICENSE)

---

## Acknowledgments

- [Trail of Bits](https://github.com/trailofbits) — Slither MCP server
- [Farofino](https://github.com/italoag/farofino-mcp) — Aderyn integration
- [SunWeb3Sec](https://github.com/SunWeb3Sec/DeFiHackLabs) — DeFiHackLabs exploit corpus
- [Solodit](https://solodit.xyz) — Audit finding database
- [Anthropic](https://anthropic.com) — Claude Code runtime
