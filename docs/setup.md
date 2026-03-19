# Setup Guide

> For detailed per-platform installation, troubleshooting, and Trident/OpenSSL/Developer Mode requirements, see **[Platform Dependencies](dependencies.md)**.

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
| Trident | Stateful fuzzing (v0.11+ — all platforms) | `cargo install trident-cli` |
| OpenSSL | Required by Trident on Windows | `winget install ShiningLight.OpenSSL.Dev` |

> **Windows users**: Two prerequisites before building Solana programs:
> 1. **Developer Mode** — required for `cargo-build-sbf` symlinks. Settings > System > For Developers > toggle ON. See [SETUP.md](../SETUP.md) Step 5b.
> 2. **OpenSSL** — required to compile Trident fuzz harness. Install via `winget install ShiningLight.OpenSSL.Dev`. The `plamen.py` wrapper auto-detects and sets `OPENSSL_DIR`/`OPENSSL_LIB_DIR`/`OPENSSL_INCLUDE_DIR`.

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

### 0. Windows: Enable Developer Mode

> **Skip on macOS/Linux.** Symlinks work without elevated privileges on Unix systems.

The installer creates symlinks from `~/.plamen/` into `~/.claude/`. On Windows, file symlinks require Developer Mode:
- **Settings UI**: Settings > System > For Developers > toggle ON
- **Admin PowerShell**: `reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f`

### 1. Clone and initialize

```bash
git clone https://github.com/PlamenTSV/plamen.git ~/.plamen
cd ~/.plamen
git submodule update --init --recursive
```

> This clones into `~/.plamen`, keeping it separate from Claude Code's `~/.claude`. The installer creates symlinks so Claude Code discovers Plamen's agents, rules, and commands. Your existing `~/.claude` settings are preserved via additive config merging.

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

If using `python plamen.py install`, config files are merged automatically (settings.json, mcp.json, CLAUDE.md). For manual setup:

```bash
cp mcp.json.example ~/.claude/mcp.json      # if ~/.claude/mcp.json doesn't exist
cp settings.json.example ~/.claude/settings.json  # if ~/.claude/settings.json doesn't exist
```

Edit `~/.claude/mcp.json` with your API keys. See [MCP Servers](mcp-servers.md) for details.

### 4. Build the RAG database

```bash
export SOLODIT_API_KEY=your_key_here   # free at solodit.cyfrin.io

cd custom-mcp/unified-vuln-db
python3 -m unified_vuln.indexer index -s solodit --max-pages 10
python3 -m unified_vuln.indexer index -s defihacklabs
python3 -m unified_vuln.indexer index -s immunefi
python3 -m unified_vuln.indexer stats   # verify
cd ../..
# Note: on Windows use 'python' instead of 'python3'
```

<details>
<summary><strong>Full build (~30 min, better RAG quality)</strong></summary>

```bash
python3 -m unified_vuln.indexer index -s solodit --max-pages 100
git clone https://github.com/SunWeb3Sec/DeFiHackLabs.git data/DeFiHackLabs
python3 -m unified_vuln.indexer index -s defihacklabs
python3 -m unified_vuln.indexer index -s immunefi
```

</details>

### 5. Verify

```bash
python3 plamen.py         # macOS / Linux
python plamen.py          # Windows
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
