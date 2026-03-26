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
| Claude Code | latest | AI runtime | `npm install -g @anthropic-ai/claude-code` |
| Python | 3.11-3.12 (recommended) | MCP servers, wrapper | [python.org](https://python.org) |
| Node.js | 18+ | npm-based MCP servers | [nodejs.org](https://nodejs.org) |
| Git | any | Submodules, version control | [git-scm.com](https://git-scm.com) |
| Rust | stable | Compiling security tools | [rustup.rs](https://rustup.rs) |

### Windows: Developer Mode (required)

Plamen's installer creates symlinks from `~/.plamen/` into Claude Code's `~/.claude/` directory. On Windows, **file symlinks require Developer Mode** (directory junctions work without it, but file symlinks do not).

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

## MCP Servers & RAG

| Component | Purpose | Install | Required? |
|-----------|---------|---------|-----------|
| unified-vuln-db | RAG vulnerability database | `pip install -r custom-mcp/unified-vuln-db/requirements.txt` | Recommended |
| slither-mcp | Slither static analyzer bridge | `pip install -e custom-mcp/slither-mcp` | EVM only |
| farofino-mcp | Aderyn/Slither fallback | `pip install -r custom-mcp/farofino-mcp/requirements.txt` | EVM only |
| solana-fender | Solana security checks | `pip install -e custom-mcp/solana-fender` | Solana only |
| solodit-scraper | Solodit.xyz data | `pip install -r custom-mcp/solodit-scraper/requirements.txt` | Optional |
| defihacklabs-rag | DeFiHackLabs data | `pip install -r custom-mcp/defihacklabs-rag/requirements.txt` | Optional |

> **Note**: The unified-vuln-db install pulls ~2GB (includes PyTorch for embeddings). First MCP call per session loads ChromaDB and the embedding model (1-5 minutes cold start). Subsequent calls are instant.

### API Keys

| Key | Source | Purpose | Required? |
|-----|--------|---------|-----------|
| `SOLODIT_API_KEY` | [solodit.cyfrin.io](https://solodit.cyfrin.io) | Index 3400+ audit findings for RAG | Recommended (free) |
| `TAVILY_API_KEY` | [tavily.com](https://tavily.com) | Web search fallback for RAG | Optional (free tier) |
| `ETHERSCAN_API_KEY` | [etherscan.io/apis](https://etherscan.io/apis) | Contract source verification | Optional (free) |
| `HELIUS_API_KEY` | [helius.dev](https://helius.dev) | Solana on-chain data | Optional (free tier) |
| RPC URL | Alchemy/Infura/public | Ethereum fork testing | Optional (free tier) |

Set keys in `~/.claude/mcp.json` after copying from `mcp.json.example`. See [MCP Servers](mcp-servers.md) for details.

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

### MCP server timeout on first call
ChromaDB loads the embedding model on first use (1-5 minutes). This is normal. The pipeline handles it with probe-first patterns and WebSearch fallback. The tool timeout is set to 300s in `settings.json` to accommodate this.

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
