# Plamen

Autonomous smart contract security auditor for [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

Orchestrates 15-95 AI agents across 8 phases to produce audit reports with verified PoC exploits. Supports **EVM/Solidity**, **Solana/Anchor**, **Aptos Move**, and **Sui Move**.

---

## Prerequisites

[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code), [Python 3.11-3.12](https://python.org) + pip, [Node.js 18+](https://nodejs.org), [Git](https://git-scm.com)

> **macOS**: Also run `xcode-select --install` (needed for C++ dependency compilation).
>
> **Windows**: Enable Developer Mode before installing (required for symlinks). Settings > System > For Developers > toggle ON. Or in admin PowerShell: `reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f`
>
> Per-language tools (Foundry, Solana CLI, etc.) are installed automatically via `plamen setup`.

---

## Install

### Option A: Let Claude set it up (recommended)

Open Claude Code and paste the contents of [`SETUP.md`](SETUP.md). Claude handles cloning, symlink installation, and dependency setup automatically. RAG database building is a separate step (`plamen rag`) — see below.

### Option B: Terminal

**Linux / macOS:**
```bash
git clone https://github.com/PlamenTSV/plamen.git ~/.plamen
export SOLODIT_API_KEY=your_key_here    # free at solodit.cyfrin.io (recommended for RAG quality)
cd ~/.plamen && python3 plamen.py install
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/PlamenTSV/plamen.git $HOME\.plamen
$env:SOLODIT_API_KEY = "your_key_here"  # free at solodit.cyfrin.io (recommended for RAG quality)
cd $HOME\.plamen; python plamen.py install
```

> Python dependencies are installed automatically on first run. On macOS/Linux use `python3`, on Windows use `python`. The RAG database is **not** built during install — run `plamen rag` separately after install (see below).

After install, add to PATH so you can run `plamen` from anywhere:

**Linux (bash):**
```bash
echo 'export PATH="$HOME/.plamen:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

**macOS (zsh):**
```zsh
echo 'export PATH="$HOME/.plamen:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

**Windows (PowerShell, one-time):**
```powershell
[System.Environment]::SetEnvironmentVariable("Path", "$env:USERPROFILE\.plamen;" + [System.Environment]::GetEnvironmentVariable("Path", "User"), "User")
```

Then use `plamen` from anywhere:
```bash
plamen                              # interactive wizard
plamen setup                        # install chain toolchains
plamen rag                          # build/rebuild RAG database (~10-20 min)
plamen uninstall                    # remove Plamen from ~/.claude
```

> **Important**: Always use `plamen` (not `python3 plamen.py`) after PATH is set. The `python3 plamen.py` form only works from inside `~/.plamen/`.

The installer:
- Creates symlinks from `~/.plamen` into `~/.claude/` so Claude Code discovers Plamen's agents, rules, prompts, and commands
- Merges Plamen's permissions into your existing `settings.json` (additive only — won't remove your entries)
- Merges MCP server definitions into `mcp.json` (won't overwrite your existing servers)
- Injects Plamen instructions into `CLAUDE.md` between `<!-- PLAMEN:START/END -->` markers (preserves your content)
- Installs core Python dependencies (RAG deps installed separately via `plamen rag`)

Your existing Claude Code configuration is preserved.

<details>
<summary>How symlinks work</summary>

The Plamen repo stays at `~/.plamen`. The installer creates symlinks (shortcuts) in `~/.claude/` that point back to `~/.plamen/`. When Claude Code reads `~/.claude/agents/depth-edge-case.md`, the OS transparently reads `~/.plamen/agents/depth-edge-case.md`. This means:
- `git pull` in `~/.plamen` updates everything automatically — no re-install needed
- Your own Claude Code files in `~/.claude/` (custom agents, commands, hooks) are untouched
- Deleting `~/.plamen` would break the symlinks — don't delete it while Plamen is installed

| Platform | How links are created | Requirements |
|----------|----------------------|-------------|
| **Linux / macOS** | Standard symlinks (`os.symlink`) | None |
| **Windows (directories)** | Junctions (`mklink /J`) | None |
| **Windows (files)** | Symlinks (`os.symlink`) | Developer Mode enabled |

</details>

> **Migrating from v1.0.x** (installed directly in `~/.claude`): Close Claude Code first, then run both commands together:
>
> Linux/macOS: `mv ~/.claude ~/.plamen && cd ~/.plamen && python3 plamen.py install`
>
> Windows (PowerShell): `Rename-Item $HOME\.claude $HOME\.plamen; cd $HOME\.plamen; python plamen.py install`
>
> This moves the repo to `~/.plamen` and immediately recreates `~/.claude` with symlinks + merged config. Claude Code will not work between the move and install — run them together.

### Option C: Manual dependency install

<details>
<summary>Click to expand (~5-10 min)</summary>

> Option B handles this automatically. These commands are for reference only.

```bash
cd ~/.plamen

# 1. Core Python deps (lightweight — ~30s)
pip install -r requirements.txt
pip install -r custom-mcp/farofino-mcp/requirements.txt
pip install -e custom-mcp/solana-fender
pip install -e custom-mcp/slither-mcp              # EVM only (needs Python 3.11+)

# 2. Chain tools (install what you need)
curl -L https://foundry.paradigm.xyz | bash && foundryup          # EVM
pip install slither-analyzer                                       # EVM static analysis
# See docs/setup.md for Solana, Aptos, Sui, Medusa, Trident
```

> **Windows + Solana**: Enable Developer Mode (Settings > System > For Developers) and install OpenSSL (`winget install ShiningLight.OpenSSL.Dev`) before building. See [docs/dependencies.md](docs/dependencies.md).

See [docs/setup.md](docs/setup.md) for the full guide with all per-language prerequisites.

</details>

### Run your first audit

```bash
plamen                    # terminal wrapper with interactive wizard
```

Or inside Claude Code: `/plamen`

> **New to Plamen?** See [docs/getting-started.md](docs/getting-started.md) — explains what's required vs optional, which chain tools to install, and how to run your first audit.

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
plamen setup                                        # install chain toolchains
plamen rag                                          # build/rebuild RAG database (~10-20 min)
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
