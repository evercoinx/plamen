# Automated Setup — Paste This Into Claude Code

> **For users who prefer Claude Code to handle the entire installation.**
> Copy everything below the line into a Claude Code session and it will set up Plamen for you.

---

Please set up Plamen (Web3 Security Auditor) on my machine. Follow these steps exactly:

## Step 0: Check prerequisites

Check if the required tools are installed. Run these checks and report what's missing:

```bash
claude --version                         # Claude Code CLI
python3 --version || python --version    # need 3.11-3.12 (3.13+ has compatibility issues)
pip3 --version || pip --version          # Python package manager
node --version                           # need 18+
npx --version
git --version
```

If any are missing:
- **Claude Code**: `npm install -g @anthropic-ai/claude-code` (see https://docs.anthropic.com/en/docs/claude-code)
- **Python**: Download from https://python.org (3.11 or 3.12 recommended). On macOS: `brew install python@3.12`. On Ubuntu: `sudo apt install python3.12 python3.12-venv python3-pip`.
- **pip**: Usually included with Python. If missing: `python3 -m ensurepip --upgrade` or on Ubuntu: `sudo apt install python3-pip`.
- **Node.js**: Download from https://nodejs.org (LTS). On macOS: `brew install node`. On Ubuntu: `curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt install -y nodejs`.
- **Git**: Download from https://git-scm.com. On macOS: `brew install git`. On Ubuntu: `sudo apt install git`.
- **macOS only**: Xcode Command Line Tools (needed to compile C++ dependencies): `xcode-select --install`

Do NOT proceed to Step 1 until all tools are available.

## Step 0b: Windows — Enable Developer Mode

> **Skip this step on macOS and Linux.**

The Plamen installer creates symlinks to link its files into Claude Code's `~/.claude/` directory. On Windows, file symlinks require Developer Mode.

**Option A — Settings UI** (recommended, one-time):
1. Open **Settings > System > For Developers**
2. Toggle **Developer Mode** ON

**Option B — Admin PowerShell** (alternative):
```powershell
reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f
```

Without Developer Mode, the installer will create directory junctions (which work without privileges) but file symlinks will fail. Developer Mode is also required later for Solana builds (`cargo-build-sbf` creates symlinks internally).

## Step 1: Clone the repository

Detect my OS and run the appropriate commands:

**Linux / macOS:**
```bash
git clone https://github.com/PlamenTSV/plamen.git ~/.plamen
cd ~/.plamen
git submodule update --init --recursive
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/PlamenTSV/plamen.git $HOME\.plamen
cd $HOME\.plamen
git submodule update --init --recursive
```

> This clones into `~/.plamen`, keeping it separate from your Claude Code config at `~/.claude`. The installer creates symlinks — your existing settings, MCP servers, and CLAUDE.md content are preserved.

## Step 2: Run the installer

Detect my OS and run the appropriate command:

**Linux / macOS:**
```bash
cd ~/.plamen && python3 plamen.py install
```

**Windows:**
```powershell
cd $HOME\.plamen; python plamen.py install
```

> Core Python dependencies (`rich`, `InquirerPy`) are installed automatically. On macOS/Linux use `python3`, on Windows use `python`.

This will:
- Symlink Plamen's agents, rules, prompts, skills, and commands into `~/.claude/`
- Merge permissions and env vars into `settings.json` (additive — won't remove your existing entries)
- Merge MCP server definitions into `mcp.json` (won't overwrite your existing servers)
- Inject Plamen's CLAUDE.md instructions between `<!-- PLAMEN:START -->` / `<!-- PLAMEN:END -->` markers
- Install core Python dependencies (lightweight — no PyTorch)

> **RAG database is NOT built during install.** Run `plamen rag` separately after install (Step 3b). This avoids 1+ hour installs and crashes on memory-constrained machines.

## Step 3b: Build the RAG database (separate step)

The RAG vulnerability knowledge base provides historical vulnerability matching during audits. It downloads PyTorch (~2GB), embedding models, and indexes findings from Solodit, DeFiHackLabs, and Immunefi.

**Set the Solodit API key first** (free from https://solodit.cyfrin.io — largest source, 3400+ findings):

**Linux / macOS:**
```bash
export SOLODIT_API_KEY=your_key_here
plamen rag
```

**Windows (PowerShell):**
```powershell
$env:SOLODIT_API_KEY = "your_key_here"
plamen rag
```

This takes ~10-20 minutes and is CPU/RAM intensive. On constrained machines (MacBook Air, <16GB RAM), a lightweight embedding model is auto-selected.

> **RAG is optional.** The pipeline degrades gracefully without it — findings still use code analysis, but lack historical pattern matching. You can always build it later.

## Step 4: Configure remaining API keys

Edit `~/.claude/mcp.json`:
- Replace `YOUR_RPC_URL` with an Ethereum RPC URL (Alchemy/Infura free tier, or public: `https://eth.llamarpc.com`)
- Replace `YOUR_ETHERSCAN_API_KEY` with a free key from https://etherscan.io/apis (optional)
- Replace `YOUR_TAVILY_API_KEY` with a free key from https://tavily.com (optional, used as RAG fallback)
- Replace `YOUR_HELIUS_API_KEY` with a free key from https://helius.dev (optional, Solana only)
- Update the `command` paths for Python and slither-mcp to match my system

For the Python command path, run `which python3` (macOS/Linux) or `where python` (Windows) and use that path.

## Step 5: Verify installation

Run the terminal wrapper to check everything (detect my OS):

**Linux / macOS:**
```bash
cd ~/.plamen && python3 plamen.py setup
```

**Windows:**
```powershell
cd $HOME\.plamen; python plamen.py setup
```

Or if already on PATH: `plamen setup`

This shows the toolchain status box. If any optional tools are missing (Foundry, Solana CLI, etc.), the Setup menu can install them automatically.

## Step 5b: Windows — Solana extras

> Skip if you already enabled Developer Mode in Step 0b. Skip entirely if not auditing Solana.

If Developer Mode is not yet enabled (needed for both Plamen symlinks and Solana builds), see Step 0b above.

**OpenSSL** (Windows only — required to compile Trident fuzz harness):
```
winget install ShiningLight.OpenSSL.Dev
```

Trident v0.11+ does NOT require honggfuzz or AFL — it uses its own TridentSVM engine and works on all platforms.

## Step 6: Add to PATH (optional)

So I can just type `plamen` from any directory:

**Linux (bash):**
```bash
echo 'export PATH="$HOME/.plamen:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

**macOS (zsh):**
```zsh
echo 'export PATH="$HOME/.plamen:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

**Windows** — run in PowerShell:
```powershell
[System.Environment]::SetEnvironmentVariable("Path", "$env:USERPROFILE\.plamen;" + [System.Environment]::GetEnvironmentVariable("Path", "User"), "User")
```

## Troubleshooting

If any step fails, check [docs/dependencies.md](docs/dependencies.md) for platform-specific fixes:
- **macOS**: `hnswlib` build fail → run `xcode-select --install` first
- **macOS/Linux**: `externally-managed-environment` → handled automatically, but see docs if manual pip fails
- **Linux**: ChromaDB SQLite version error → `pip install pysqlite3-binary`
- **Python 3.13+**: PyTorch/sentence-transformers may not work → use Python 3.11 or 3.12
- **Windows**: symlink permission error → enable Developer Mode (Step 0b)

## Done

After setup, I can start an audit by typing `plamen` in my terminal or `/plamen` inside Claude Code.

> **What's next?** See [docs/getting-started.md](docs/getting-started.md) for what's required vs optional, which API keys to set, and how to run your first audit.

Available commands (work from any directory after PATH is set):
- `plamen` — interactive wizard
- `plamen setup` — install chain toolchains
- `plamen rag` — build/rebuild RAG database (~10-20 min)
- `plamen uninstall` — remove Plamen from ~/.claude

> **Important**: Always use `plamen` (not `python3 plamen.py`) after PATH is set up. The `python3 plamen.py` form only works from inside `~/.plamen/`.
