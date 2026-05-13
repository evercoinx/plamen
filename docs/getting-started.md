# Getting Started

> **⚠️ Do NOT paste this file or setup.md into Claude Code.** Follow these instructions in your terminal. Pasting into Claude Code causes autonomous command execution including the optional RAG build (~6GB RAM).

> Just installed Plamen? This page tells you exactly what to do next — what's required, what's optional, and how to run your first audit.

## What did install do?

`plamen install` (or `plamen setup`) set up:

| Component | What it is | Status after install |
|-----------|-----------|---------------------|
| **Symlinks** | Links Plamen's agents, rules, commands, and hooks into `~/.claude/` (and `~/.codex/plamen/` if `--codex`) | Done |
| **Config** | Merged permissions, env vars, MCP servers, and hook triggers into your Claude Code / Codex config | Done |
| **Watchdog** | Pipeline enforcement hooks that prevent the orchestrator from skipping steps during audits | Done (runs automatically, zero overhead outside audits) |
| **Core Python deps** | `rich`, `InquirerPy` (wrapper UI) | Done |
| **MCP server deps** | slither-mcp, solana-fender, farofino-mcp | Done |
| **Chain toolchains** | Foundry, Solana CLI, Anchor, Aptos, Sui, etc. | Only if you selected them |
| **RAG database** | Vulnerability knowledge base (PyTorch + embeddings) | **Not installed** — separate step |

## What do I actually need?

### Required for all audits

These are installed automatically. If any are missing, `plamen` will tell you.

- **Claude Code** (`claude` in PATH) or **Codex CLI** (`codex` in PATH)
- **Python 3.11-3.12** (`python` / `python3`)
- **Node.js 18+** (`npx`, `npm`)
- **Git**

### Required per chain (install only what you audit)

You do **not** need all chain tools. Install only the ones for your target:

| I'm auditing... | I need | Install command |
|-----------------|--------|-----------------|
| **EVM / Solidity** | Foundry (forge) | `plamen setup` → select EVM |
| **Solana / Anchor** | Solana CLI + Anchor | `plamen setup` → select Solana |
| **Aptos Move** | Aptos CLI | `plamen setup` → select Move |
| **Sui Move** | Sui CLI | `plamen setup` → select Move |
| **Soroban / Stellar** | Stellar CLI + Rust | `plamen setup` → select Soroban |
| **L1 / Node Client** | Go or Rust + scip-go/rust-analyzer | `plamen setup` → select L1 |

> **Slither** (EVM static analysis) and **Medusa** (EVM stateful fuzzing) are recommended but optional. The pipeline works without them — it just has less static analysis coverage.

### Optional: RAG vulnerability database (~6GB RAM required)

RAG gives the pipeline historical vulnerability pattern matching — it searches a local database of 4k+ past audit findings (from Solodit, DeFiHackLabs, Immunefi bug bounties, and Immunefi audit competitions). The pipeline works without it (falls back to web search), but RAG improves finding quality.

> **Resource warning**: RAG build loads PyTorch + sentence-transformers + ChromaDB. Peak RAM: ~4-6GB. On machines with ≤8GB total RAM, close other applications first or skip this step entirely.

```bash
# Build the RAG database (~10-20 min, CPU + RAM intensive)
export SOLODIT_API_KEY=your_key_here    # free at solodit.cyfrin.io (recommended)
plamen rag
```

You can always build it later. Run the same command to rebuild after updates.

### Optional: API keys

Set in `~/.claude/mcp.json` or `~/.codex/plamen/mcp.json` (edit the file, replace `YOUR_*` placeholders):

| Key | What it does | Impact if missing | Get it |
|-----|-------------|-------------------|--------|
| `SOLODIT_API_KEY` | Indexes Solodit findings into RAG | RAG database will be smaller (misses 3400+ Solodit findings) | [solodit.cyfrin.io](https://solodit.cyfrin.io) (free) |
| `ETHERSCAN_API_KEY` | Fetches verified source code on-chain | No production source verification (EVM only) | [etherscan.io/apis](https://etherscan.io/apis) (free) |
| `TAVILY_API_KEY` | Web search fallback when RAG fails | Falls back to Claude's built-in web search | [tavily.com](https://tavily.com) (free tier) |
| `HELIUS_API_KEY` | Solana on-chain data | No Solana account inspection | [helius.dev](https://helius.dev) (free tier) |
| RPC URL | Ethereum fork testing | No fork-mode PoC verification (EVM only) | Alchemy, Infura, or `https://eth.llamarpc.com` |

**None of these are required.** The pipeline runs without any API keys — it just has less production verification and RAG coverage.

## Run your first audit

### Option A: Terminal (recommended for first time)

```bash
plamen
```

The interactive wizard walks you through: mode selection → target project → docs → scope → cost estimate → launch.

### Option B: One-liner

```bash
plamen core /path/to/your/project
```

### Option C: Inside Claude Code

```
/plamen
```

### Option D: Inside Codex CLI

```
$plamen core /path/to/project
```

Requires prior Codex setup: `plamen install --codex`.

## What mode should I pick?

| Mode | When to use | Plan needed | Time |
|------|-------------|-------------|------|
| **Light** | Quick scan, small codebases (<3k lines), Pro plan | Pro | ~15-30 min |
| **Core** | Standard audit, most projects | Max | ~30-90 min |
| **Thorough** | High-value audit, complex DeFi, want fuzzing | Max | ~1-3 hours |

Start with **Light** if you're on a Pro plan or just trying it out. Use **Core** for real audits.

> **L1 mode**: For node client / infrastructure audits, use `plamen l1 [light|core|thorough]`. Same mode tiers apply.

## Verify everything works

Run `plamen setup` at any time to see your toolchain status:

```
  ╭────────────────────────────────────────────────────╮
  │  Toolchain                                         │
  │                                                    │
  │  ✓claude  ✓python  ✓npx  ✓git                  ok │
  ├────────────────────────────────────────────────────┤
  │  EVM      ✓forge ✓slither ○medusa             2/3 │
  │  Solana   ○solana ○anchor ○trident             0/3 │
  │  Move     ○aptos ○sui                          0/2 │
  │  Soroban  ○stellar                             0/1 │
  ├────────────────────────────────────────────────────┤
  │  RAG DB   vulnerability knowledge base   not built │
  ╰────────────────────────────────────────────────────╯
```

- **✓** = installed
- **○** = not installed (optional — install only what you need)
- **RAG DB** = run `plamen rag` to build

## Updating

After pulling new versions:

```bash
cd ~/.plamen && git pull && plamen install
```

`git pull` alone updates symlinked files (agents, rules, skills, prompts), but `CLAUDE.md` (the orchestrator's rules) is an injected copy — not a symlink. Without `plamen install`, the orchestrator follows stale rules while everything else is updated. `plamen` will warn you if it detects a version mismatch.

See [updating.md](updating.md) for details on what auto-updates and what doesn't.

## Troubleshooting

See [dependencies.md](dependencies.md) for platform-specific fixes (Windows Developer Mode, macOS hnswlib, Python version issues, etc.).
