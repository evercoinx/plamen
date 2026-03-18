# Setup Guide

## Prerequisites

### Required

| Tool | Purpose | Install |
|------|---------|---------|
| **Claude Code CLI** | AI runtime | [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code) |
| **Python 3.11+** | MCP servers, wrapper | [python.org](https://python.org) |
| **Node.js 18+** / **npx** | npm MCP servers | [nodejs.org](https://nodejs.org) |
| **Git** | Submodules, deps | [git-scm.com](https://git-scm.com) |

### Per-Language

<details>
<summary><strong>EVM/Solidity</strong></summary>

| Tool | Purpose | Install |
|------|---------|---------|
| Foundry (forge, anvil, cast) | Build, test, fork | `curl -L https://foundry.paradigm.xyz \| bash && foundryup` |
| Slither | Static analysis | `pip install slither-analyzer` |
| Medusa | Stateful fuzzing (Thorough) | [github.com/crytic/medusa](https://github.com/crytic/medusa/releases) |

</details>

<details>
<summary><strong>Solana</strong></summary>

| Tool | Purpose | Install |
|------|---------|---------|
| Solana CLI | Toolchain | [docs.anza.xyz](https://docs.anza.xyz/cli/install) |
| Anchor | Build Anchor programs | `avm install latest && avm use latest` |
| Trident | Stateful fuzzing | `cargo install trident-cli` |

> **Windows**: Enable Developer Mode before building. See [SETUP.md](../SETUP.md) Step 5b.

</details>

<details>
<summary><strong>Aptos Move</strong></summary>

| Tool | Install |
|------|---------|
| Aptos CLI | [aptos.dev/build/cli](https://aptos.dev/build/cli) |

</details>

<details>
<summary><strong>Sui Move</strong></summary>

| Tool | Install |
|------|---------|
| Sui CLI | [docs.sui.io](https://docs.sui.io/guides/developer/getting-started/sui-install) |

</details>

---

## Installation

### 1. Clone and initialize

```bash
git clone https://github.com/PlamenTSV/plamen.git ~/.claude
cd ~/.claude
git submodule update --init --recursive
```

> **Note**: This clones into `~/.claude` -- Claude Code reads config from this path. Back up any existing `~/.claude` first.

### 2. Install Python dependencies

```bash
# Wrapper
pip install -r requirements.txt

# MCP servers (~2GB download -- includes PyTorch for embeddings)
pip install -r custom-mcp/unified-vuln-db/requirements.txt
pip install -r custom-mcp/solodit-scraper/requirements.txt
pip install -r custom-mcp/defihacklabs-rag/requirements.txt
pip install -e custom-mcp/solana-fender
pip install -r custom-mcp/farofino-mcp/requirements.txt

# EVM only (requires Python 3.11+, solc)
pip install -e custom-mcp/slither-mcp
```

### 3. Configure MCP servers

```bash
cp mcp.json.example mcp.json
cp settings.json.example settings.json
```

Edit `mcp.json` with your API keys. See [MCP Servers](mcp-servers.md) for details.

### 4. Build the RAG database

```bash
export SOLODIT_API_KEY=your_key_here   # free at solodit.cyfrin.io

cd custom-mcp/unified-vuln-db
python -m unified_vuln.indexer index -s solodit --max-pages 10
python -m unified_vuln.indexer index -s defihacklabs
python -m unified_vuln.indexer index -s immunefi
python -m unified_vuln.indexer stats   # verify
cd ../..
```

<details>
<summary><strong>Full build (~30 min, better RAG quality)</strong></summary>

```bash
python -m unified_vuln.indexer index -s solodit --max-pages 100
git clone https://github.com/SunWeb3Sec/DeFiHackLabs.git data/DeFiHackLabs
python -m unified_vuln.indexer index -s defihacklabs
python -m unified_vuln.indexer index -s immunefi
```

</details>

### 5. Verify

```bash
python plamen.py
```

The startup screen runs a dependency check showing which tools are available.

---

## Permissions (settings.json)

The default `settings.json.example` auto-approves all tool calls required for autonomous auditing:

| Permission | Why Required |
|-----------|-------------|
| `Agent(*)` | Spawns all subagents. **Without this, the pipeline silently fails.** |
| `Bash(*)` | Runs `forge build/test`, `cargo test`, etc. |
| `Read(*)`, `Write(*)`, `Edit(*)` | Reads source, writes PoCs, edits scratchpad |
| `mcp__*` | All MCP server tool calls |

The deny list blocks destructive operations (`rm -rf`, `sudo`, force push).

---

## Cold Start

The first MCP tool call per Claude Code session loads ChromaDB and the embedding model into memory (1-5 minutes). Subsequent calls are instant. The pipeline handles this automatically with probe-first patterns and WebSearch fallback.
