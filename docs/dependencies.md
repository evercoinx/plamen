# Platform Dependencies

> Complete dependency guide for all platforms. **Not sure what you need?** See [getting-started.md](getting-started.md) — most users only need tools for their target chain.
>
> `plamen setup` auto-installs chain toolchains, and `plamen rag` builds/rebuilds the RAG vulnerability database separately. This page documents manual installation and troubleshooting.

## Quick Start

```bash
# Auto-install everything (interactive)
plamen setup                                    # if PATH is set
cd ~/.plamen && python3 plamen.py setup         # macOS/Linux (before PATH)
cd $HOME\.plamen; python plamen.py setup        # Windows PowerShell (before PATH)
```

The setup wizard detects your OS and installed tools, then offers to install missing ones. For manual installation or troubleshooting, see below.

---

## Required (All Platforms)

| Tool | Version | Purpose | Install |
|------|---------|---------|---------|
| Claude Code and/or Codex CLI | latest | AI runtime (one or both) | `npm install -g @anthropic-ai/claude-code` and/or [github.com/openai/codex](https://github.com/openai/codex) |
| Python | 3.11-3.12 (recommended) | MCP servers, wrapper | [python.org](https://python.org) |
| Node.js | 18+ | npm-based MCP servers | [nodejs.org](https://nodejs.org) |
| Git | any | Submodules, version control | [git-scm.com](https://git-scm.com) |
| Rust | stable | Solana (Trident fuzzer), Soroban contracts, L1 Rust clients | [rustup.rs](https://rustup.rs) — Solana, Soroban, and L1 Rust |
| `pywinpty>=2.0.14` (Windows only) | latest | PTY supervision transport for Claude workers | auto-installed by `plamen install` (gated `platform_system=="Windows"` in `requirements.txt`); macOS/Linux use stdlib `pty.openpty()` with `Popen` ownership + SIGCHLD reset |

> **PTY-supervised execution (v2.1.0)**: the driver now drives each Claude/Codex
> worker through a pseudo-terminal and infers turn completion from artifacts
> written to disk (the `<!-- PLAMEN_STATUS: COMPLETE -->` marker), not from a
> stdout/JSON envelope. This removes the 0-byte-stdio ambiguity and silent-hang
> class from v2.0.x. A one-time PTY transport preflight
> (`scripts/preflight_pty_transports.py`) probes which continuation mechanisms
> the installed Claude Code binary supports; results are cached per
> `claude --version` and the driver always falls back to a slower respawn path
> if a probe is inconclusive — no extra setup is required.

### Windows: Developer Mode (required)

Plamen's installer creates symlinks from `~/.plamen/` into `~/.claude/` (and `~/.codex/plamen/` with `--codex`). On Windows, **file symlinks require Developer Mode** (directory junctions work without it, but file symlinks do not).

**Enable Developer Mode** (one-time):
- **Settings UI**: Settings > System > For Developers > toggle ON
- **Admin PowerShell**: `reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f`
- **Admin CMD**: `reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f`

This is also required later for Solana builds (`cargo-build-sbf` creates symlinks internally).

> **macOS / Linux**: No extra setup needed. Symlinks work without elevated privileges.

---

## EVM/Solidity

| Tool | Purpose | Install | Required? |
|------|---------|---------|-----------|
| Foundry (forge, cast, anvil) | Build, test, invariant fuzz, fork testing | `curl -L https://foundry.paradigm.xyz \| bash && foundryup` | Yes |
| Slither | Static analysis (MCP) | `pip install slither-analyzer` | Recommended |
| Medusa | Stateful fuzzing (Thorough mode) | [github.com/crytic/medusa/releases](https://github.com/crytic/medusa/releases) | Optional |

### EVM Platform Notes

**Windows**: Foundry works natively. No special setup needed.
**macOS (Apple Silicon)**: Foundry works natively via Rosetta or arm64.
**Linux**: Foundry works natively.

Medusa requires Go. The setup wizard installs Go automatically if missing (`go install github.com/crytic/medusa@latest`).

---

## Solana

| Tool | Purpose | Install | Required? |
|------|---------|---------|-----------|
| Solana CLI | Toolchain, account data | [docs.anza.xyz](https://docs.anza.xyz/cli/install) | Yes |
| Anchor (via AVM) | Build Anchor programs | `avm install latest && avm use latest` | Yes (for Anchor projects) |
| Trident | Stateful fuzzing (v0.11+) | `cargo install trident-cli` | Recommended |

### Solana Platform Notes

<details>
<summary><strong>Windows -- Required Setup</strong></summary>

**1. Enable Developer Mode** (one-time, required for `cargo-build-sbf`):

Solana's build tools create symlinks internally. Without Developer Mode, builds fail with:
```
error 1314: A required privilege is not held by the client
```

Fix (choose one):
- **Settings UI**: Settings > System > For Developers > toggle Developer Mode ON
- **Admin PowerShell**: `reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f`
- **Per-session**: Run your terminal as Administrator (right-click > Run as administrator)

**2. Install OpenSSL** (required for Trident fuzz compilation):

```
winget install ShiningLight.OpenSSL.Dev
```

The `plamen.py` wrapper auto-detects OpenSSL in standard locations and sets environment variables. It checks (in order):
1. Existing `OPENSSL_LIB_DIR` / `OPENSSL_INCLUDE_DIR` env vars
2. vcpkg installation (`$VCPKG_ROOT/installed/x64-windows/`)
3. ShiningLight installer paths (`C:\Program Files\OpenSSL-Win64`, `C:\Program Files\OpenSSL`, `C:\OpenSSL-Win64`)

If auto-detection fails, set manually in PowerShell:
```powershell
$env:OPENSSL_DIR = "C:\Program Files\OpenSSL-Win64"
$env:OPENSSL_LIB_DIR = "C:\Program Files\OpenSSL-Win64\lib\VC\x64\MD"
$env:OPENSSL_INCLUDE_DIR = "C:\Program Files\OpenSSL-Win64\include"
```

**3. Anchor workspace glob issue** (Anchor CLI < 0.32):

If `anchor build` fails with `error: failed to load manifest for workspace member programs/*`, the Anchor CLI's `\\?\` long path prefix breaks glob expansion. Workaround: temporarily replace `"programs/*"` in `Cargo.toml` with explicit member paths, or use `cargo build-sbf` directly.

</details>

<details>
<summary><strong>macOS -- Notes</strong></summary>

- Solana CLI installs natively on both Intel and Apple Silicon
- Trident v0.11+ works on Apple Silicon (no honggfuzz dependency)
- OpenSSL is available via Homebrew: `brew install openssl` (usually pre-installed via Xcode)

</details>

<details>
<summary><strong>Linux -- Notes</strong></summary>

- All tools install natively
- System OpenSSL dev packages may be needed: `sudo apt install libssl-dev pkg-config` (Ubuntu/Debian) or `sudo dnf install openssl-devel` (Fedora)
- Trident v0.11+ works without honggfuzz

</details>

### Trident Version Compatibility

| Trident | Honggfuzz Required? | Platforms | Solana SDK |
|---------|---------------------|-----------|------------|
| **v0.12.x (current)** | No (TridentSVM) | Linux, macOS, Windows | 2.3 |
| **v0.11.x** | No (TridentSVM) | Linux, macOS, Windows | >=1.17.3 |
| v0.10.x and below | Yes (Linux only) | Linux only | >=1.17.3 |

> **Important**: Trident v0.11+ completely replaced honggfuzz with its own TridentSVM engine. There is NO need to install honggfuzz or AFL.

---

## Aptos Move

| Tool | Purpose | Install | Required? |
|------|---------|---------|-----------|
| Aptos CLI | Build, test, prove | [aptos.dev/build/cli](https://aptos.dev/build/cli) |  Yes |

Works on all platforms. On macOS with Homebrew: `brew install aptos`. Otherwise the setup wizard uses the official Python installer script.

---

## Sui Move

| Tool | Purpose | Install | Required? |
|------|---------|---------|-----------|
| Sui CLI (via suiup) | Build, test | [docs.sui.io](https://docs.sui.io/guides/developer/getting-started/sui-install) | Yes |

Works on all platforms. The setup wizard installs via `suiup` (the official Sui version manager). On Windows, a bundled Python installer script handles the download since bash is not always available.

---

## Soroban/Stellar

| Tool | Purpose | Install | Required? |
|------|---------|---------|-----------|
| Stellar CLI | Build, deploy, test Soroban contracts | [stellar.org/docs](https://stellar.org/docs/build/smart-contracts/getting-started) | Yes |
| Rust (stable) | Soroban contract compilation | [rustup.rs](https://rustup.rs) | Yes |

Soroban contracts are Rust-based. The Stellar CLI (`stellar`) handles contract building and testing. Install Rust stable toolchain first, then install the Stellar CLI.

### Soroban Platform Notes

Works on all platforms. No special setup needed beyond Rust and the Stellar CLI.

---

## L1 Infrastructure (Go/Rust Node Clients)

> These tools are needed only for L1 mode (`plamen l1`). Skip if you only audit smart contracts.

| Tool | Purpose | Install | Required? |
|------|---------|---------|-----------|
| Go | 1.22+ | Build Go-based node clients | [go.dev/dl](https://go.dev/dl/) | Yes (Go clients) |
| Rust | stable | Build Rust-based node clients | [rustup.rs](https://rustup.rs) (preferred) | Yes (Rust clients) |
| scip-go | SCIP indexer for Go | `go install github.com/sourcegraph/scip-go/cmd/scip-go@latest` | Recommended |
| rust-analyzer | SCIP indexer for Rust | `rustup component add rust-analyzer` (or `brew install rust-analyzer` on Homebrew Rust) | Recommended |
| Opengrep | Cross-ecosystem static analysis | [github.com/opengrep/opengrep](https://github.com/opengrep/opengrep) | Recommended |
| ast-grep | Structural code search | `cargo install ast-grep --locked` (or `brew install ast-grep` on macOS); auto-installed by `plamen setup` | Recommended |
| CodeQL CLI | Advanced static analysis | [github.com/github/codeql-cli-binaries](https://github.com/github/codeql-cli-binaries) | Optional |

> **macOS — `brew install rust` vs `rustup`**: Plamen's auto-install of
> `rust-analyzer` uses `rustup component add rust-analyzer`, which only works
> when Rust came from [rustup.rs](https://rustup.rs). Homebrew's `rust` formula
> ships the compiler but **not** the rustup multiplexer, so the
> `component add` command fails. If you installed Rust via Homebrew, either
> run `brew install rust-analyzer` separately or switch to rustup
> (`brew uninstall rust && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`).
> `plamen setup` detects Homebrew Rust and routes to `brew install` for
> `rust-analyzer` and `ast-grep` automatically. Same applies to `ast-grep`
> when installed via `cargo install` (needs rustup-managed cargo) vs
> `brew install ast-grep` (standalone).

These tools power the Phase 0.5 "Bake" step that batch-indexes repositories before depth analysis. The pipeline works without them (falls back to grep-based analysis), but SCIP indexing significantly improves cross-reference accuracy.

---

## MCP Servers & RAG

MCP servers are shared between backends. Claude Code loads them from `mcp.json`; Codex loads them from `[mcp_servers.*]` TOML blocks in `~/.codex/config.toml` that `scripts/codex_adapter.py:generate_config_toml` generates from `mcp.json.example` at install time. The RAG database, Python packages, and Node MCP package versions in `mcp-packages/package.json` apply to both backends. A small subset is disabled or wrapped on Codex (`evm-chain-data` disabled due to MCP protocol version mismatch; four Python servers — `slither-analyzer`, `unified-vuln-db`, `farofino`, `solana-fender` — launched through `mcp-packages/schema-sanitizer.js`). See [mcp-servers.md](mcp-servers.md) for specifics.

| Component | Purpose | Install | Required? |
|-----------|---------|---------|-----------|
| unified-vuln-db | RAG vulnerability database | `pip install -r custom-mcp/unified-vuln-db/requirements.txt` | Recommended |
| slither-mcp | Slither static analyzer bridge | `pip install -e custom-mcp/slither-mcp` | EVM only |
| farofino-mcp | Aderyn/Slither fallback | `pip install -r custom-mcp/farofino-mcp/requirements.txt` | EVM only |
| solana-fender | Solana security checks | `pip install -e custom-mcp/solana-fender` | Solana only |

> **Note**: The unified-vuln-db install pulls ~2GB (includes PyTorch for sentence-transformers). First MCP call per session loads ChromaDB and the all-MiniLM-L6-v2 model (~5s cold start). Subsequent calls are instant.

### API Keys

| Key | Source | Purpose | Required? |
|-----|--------|---------|-----------|
| `SOLODIT_API_KEY` | [solodit.cyfrin.io](https://solodit.cyfrin.io) | Index 3400+ Solodit audit findings for RAG (4k+ total across all sources) | Recommended (free) |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com) | Web search fallback for RAG | Optional (free tier) |
| `ETHERSCAN_API_KEY` | [etherscan.io/apis](https://etherscan.io/apis) | Contract source verification | Optional (free) |
| `HELIUS_API_KEY` | [helius.dev](https://helius.dev) | Solana on-chain data | Optional (free tier) |
| RPC URL | Alchemy/Infura/public | Ethereum fork testing | Optional (free tier) |

Set keys in `~/.claude/mcp.json` (Claude Code) after copying from `mcp.json.example`. On Codex set the same keys in `~/.codex/config.toml` under each `[mcp_servers.<name>.env]` block (generated by the adapter from the same `mcp.json.example`). See [MCP Servers](mcp-servers.md) for details.

---

## Troubleshooting

### Windows: `error 1314: A required privilege is not held by the client`
Enable Developer Mode. See [Solana > Windows](#solana-platform-notes) above.

### Windows: `Could not find directory of OpenSSL installation`
Install OpenSSL: `winget install ShiningLight.OpenSSL.Dev`. See [Solana > Windows](#solana-platform-notes) above.

### macOS: `Unsupported MAC OS X version` when installing honggfuzz
You don't need honggfuzz. Trident v0.11+ uses TridentSVM. Just `cargo install trident-cli`.

### `Failed to list installed solana versions`
This occurs when Anchor CLI encounters Agave v3 (Solana CLI 3.x). Use Solana CLI 2.x for Anchor projects that specify `solana_version = "2.x"` in Anchor.toml.

### MCP server won't start (`spawn python ENOENT` or server shows as failed)
Claude Code only (Codex does not use MCP servers). The Python-based MCP servers use `"command": "python"` in mcp.json. On macOS/Linux, change to `"command": "python3"`:
```bash
sed -i '' 's/"command": "python"/"command": "python3"/g' ~/.claude/mcp.json  # macOS
sed -i 's/"command": "python"/"command": "python3"/g' ~/.claude/mcp.json    # Linux
```
Restart Claude Code after editing. On Windows, keep `"command": "python"`.

### MCP server timeout on first call
Claude Code only. ChromaDB and all-MiniLM-L6-v2 load on first use (~5s cold start). This is normal. The pipeline handles it with probe-first patterns and WebSearch fallback. The tool timeout is set to 300s in `settings.json`.

### RAG database build failed or entries count is too low
Run `plamen rag` again — it wipes the existing database and rebuilds from scratch. Ensure `SOLODIT_API_KEY` is set in `~/.claude/settings.json` → `"env"` section (Claude Code) or `~/.codex/config.toml` → `[env]` section (Codex). Safe to re-run as many times as needed.

### `No IDL files found`
Run `anchor build` or `cargo build-sbf` first to generate IDL files before `trident init`.

### Python 3.13+ compatibility issues
PyTorch, sentence-transformers, and Slither may not fully support Python 3.13+. If you encounter import errors or segfaults during RAG indexing, use Python 3.11 or 3.12:
```bash
# macOS (Homebrew)
brew install python@3.12
python3.12 -m venv ~/.plamen-venv && source ~/.plamen-venv/bin/activate
cd ~/.plamen && python plamen.py install

# Ubuntu/Debian
sudo apt install python3.12 python3.12-venv
python3.12 -m venv ~/.plamen-venv && source ~/.plamen-venv/bin/activate
cd ~/.plamen && python plamen.py install
```

### Slither install fails on Python 3.13+
Slither requires Python 3.11 or 3.12. If your default Python is 3.13+, use a virtualenv with 3.12 (see above).

### ChromaDB: `Your system has an unsupported version of sqlite3`
ChromaDB requires SQLite >= 3.35. Older Python versions or OS builds may bundle an older SQLite. Fixes:
- **Easiest**: Use Python 3.11+ from [python.org](https://python.org) (bundles recent SQLite)
- **Linux**: `pip install pysqlite3-binary` then add to your script before importing chromadb:
  ```python
  __import__('pysqlite3')
  import sys
  sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
  ```
- **Windows**: Download latest `sqlite3.dll` from [sqlite.org](https://www.sqlite.org/download.html) and replace the one in your Python `DLLs/` folder

### macOS: `Failed to build hnswlib` during pip install
ChromaDB depends on `hnswlib` which needs a C++ compiler. Install Xcode Command Line Tools first:
```bash
xcode-select --install
```
If you get `clang: error: the clang compiler does not support '-march=native'`, set:
```bash
export HNSWLIB_NO_NATIVE=1
pip3 install chromadb
```

### `externally-managed-environment` error on pip install
macOS (Homebrew Python) and Ubuntu 23.04+ block bare `pip install`. Plamen handles this automatically by detecting the `EXTERNALLY-MANAGED` marker and adding `--break-system-packages`. If you still hit this error running manual pip commands, add `--break-system-packages` or use a virtualenv.

### `error: failed to load manifest for workspace member programs/*`
Anchor CLI < 0.32 glob issue on Windows. See [Solana > Windows](#solana-platform-notes) above.

### Worker appears to "hang" with no output (PTY supervision)
As of v2.1.0 the driver supervises each worker over a pseudo-terminal and treats
the on-disk `<!-- PLAMEN_STATUS: COMPLETE -->` marker as the only completion
signal. A worker that is doing slow-but-real work no longer trips the old
context-thrash fast-fail; quiet stdio is expected. The driver detects completion
from disk, repairs-then-degrades rather than halting at the finish line, and
surfaces any unfinished obligations as flagged Appendix-B items in
`AUDIT_REPORT.md`. On Windows, ensure `pywinpty>=2.0.14` is installed (see
[Required](#required-all-platforms)); macOS/Linux use the stdlib `pty` module.

### Wrong toolchain selected / ecosystem mismatch
v2.1.0 auto-detects the target ecosystem (EVM, Solana, Aptos, Sui, Soroban, or
L1) at startup, auto-corrects it without a halt-to-rerun, and shows the resolved
ecosystem on the startup banner. Detection uses manifest-priority rules
(file-suffix-only signals never clobber an explicit config; Pinocchio/native-SDK
Solana is detected at high confidence). If the banner shows the wrong ecosystem,
set it explicitly in your project config and re-run — the explicit value wins.
