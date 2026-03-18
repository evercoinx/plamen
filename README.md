# Plamen

Autonomous smart contract security auditor for [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

Orchestrates 15-95 AI agents across 8 phases to produce audit reports with verified PoC exploits. Supports **EVM/Solidity**, **Solana/Anchor**, **Aptos Move**, and **Sui Move**.

---

## Quick Start

> **Windows**: Use [Git Bash](https://git-scm.com). **Existing `~/.claude`**: Back up first (`mv ~/.claude ~/.claude.backup`).

```bash
# Clone (must be ~/.claude — Claude Code reads config from here)
git clone https://github.com/PlamenTSV/plamen.git ~/.claude
cd ~/.claude
git submodule update --init --recursive

# Install deps
pip install -r requirements.txt
pip install -r custom-mcp/unified-vuln-db/requirements.txt
pip install -r custom-mcp/solodit-scraper/requirements.txt
pip install -r custom-mcp/defihacklabs-rag/requirements.txt
pip install -e custom-mcp/solana-fender
pip install -r custom-mcp/farofino-mcp/requirements.txt
pip install -e custom-mcp/slither-mcp              # EVM only (needs Python 3.11+)

# Configure
cp mcp.json.example mcp.json                       # edit with your API keys
cp settings.json.example settings.json

# Build RAG database (~5 min)
export SOLODIT_API_KEY=your_key_here                # free at solodit.cyfrin.io
cd custom-mcp/unified-vuln-db
python -m unified_vuln.indexer index -s solodit --max-pages 10
python -m unified_vuln.indexer index -s defihacklabs
python -m unified_vuln.indexer index -s immunefi
cd ../..

# Run
python plamen.py
```

> **Automated setup**: Open Claude Code and paste the contents of [`SETUP.md`](SETUP.md) — Claude will install everything for you.

---

## Audit Modes

| Mode | Plan | Agents | Key Features |
|------|------|--------|-------------|
| **Light** | Pro | ~15-18 | Fast scan, all Sonnet, no fuzzing |
| **Core** | Max | ~25-45 | Full depth, PoC verification for Medium+ |
| **Thorough** | Max | ~35-95 | Iterative depth, invariant fuzzing, Medusa, skeptic-judge |

See [docs/audit-modes.md](docs/audit-modes.md) for the full comparison.

---

## How to Run

**Terminal wrapper** (recommended — includes setup, cost estimation):

```bash
plamen                                              # interactive wizard
plamen core /path/to/project                        # skip wizard
plamen thorough /path/to/project --proven-only      # strict evidence mode
plamen setup                                        # install tools only
```

**Inside Claude Code**:

```
> /plamen core
> /plamen thorough docs: whitepaper.pdf scope: scope.txt
```

See [docs/usage.md](docs/usage.md) for PATH setup and all CLI options.

---

## Supported Chains

| Language | Build Tool | Static Analysis | Fuzzing |
|----------|-----------|----------------|---------|
| **EVM/Solidity** | Foundry, Hardhat | Slither, Aderyn | Foundry invariant, Medusa |
| **Solana/Anchor** | Anchor, cargo-build-sbf | Fender | Trident, proptest |
| **Aptos Move** | aptos CLI | Move Prover | Parameterized tests |
| **Sui Move** | sui CLI | -- | Parameterized tests |

Language detection is automatic based on config files.

---

## Prerequisites

**Required**: Claude Code CLI, Python 3.11+, Node.js 18+, Git

**Per-language**: Install the build tools for your chain (Foundry for EVM, Solana CLI for Solana, etc.). The `plamen setup` command can install these for you.

See [docs/setup.md](docs/setup.md) for the full prerequisite list and installation guide.

---

## Documentation

| Topic | Link |
|-------|------|
| Full setup guide | [docs/setup.md](docs/setup.md) |
| Platform dependencies | [docs/dependencies.md](docs/dependencies.md) |
| Audit mode comparison | [docs/audit-modes.md](docs/audit-modes.md) |
| Pipeline architecture | [docs/architecture.md](docs/architecture.md) |
| MCP servers & API keys | [docs/mcp-servers.md](docs/mcp-servers.md) |
| Usage & CLI options | [docs/usage.md](docs/usage.md) |
| Skills, rules & internals | [docs/internals.md](docs/internals.md) |
| Repository structure | [docs/repository-structure.md](docs/repository-structure.md) |
| Automated setup (Claude) | [SETUP.md](SETUP.md) |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Skills are the most impactful contribution — teach methodology (how to look), not patterns (what to find).

## License

[MIT](LICENSE)

## Acknowledgments

- [Trail of Bits](https://github.com/trailofbits) — Slither MCP server
- [Farofino](https://github.com/italoag/farofino-mcp) — Aderyn integration
- [SunWeb3Sec](https://github.com/SunWeb3Sec/DeFiHackLabs) — DeFiHackLabs exploit corpus
- [Solodit](https://solodit.xyz) — Audit finding database
- [Anthropic](https://anthropic.com) — Claude Code runtime
