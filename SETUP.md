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

## Step 2: Set API keys BEFORE installing

The installer builds the RAG vulnerability database during setup. The Solodit source (3400+ audit findings — the largest and most important source) requires an API key. **Set this before running the installer**, otherwise Solodit indexing will be skipped and you'll get a weaker RAG database.

**Add `SOLODIT_API_KEY` to `~/.claude/settings.json`** (the recommended approach — makes the key available to both `plamen rag` and all audit agents):

```json
{
  "env": {
    "MCP_TIMEOUT": "30000",
    "MCP_TOOL_TIMEOUT": "300000",
    "SOLODIT_API_KEY": "your_key_here"
  }
}
```

> **If `~/.claude/settings.json` doesn't exist** (common on a fresh Claude Code install): create it with exactly the content above. The installer in Step 3 will merge Plamen's permissions into this file additively — your API key will not be overwritten.

Get a free key at https://solodit.cyfrin.io.

> **Why settings.json and not `export`?** Claude Code spawns audit agent subprocesses in non-interactive shells that don't source `.bashrc`/`.zshrc`. Only env vars in `settings.json`'s `env` section are reliably available to all subprocesses. A terminal `export` only works for the one `plamen rag` run you do in that terminal — audit agents won't see it.

> Other API keys (Etherscan, Tavily, Helius, RPC URL) are configured AFTER install in `~/.claude/mcp.json`. Only Solodit is needed before install because the RAG indexer runs during setup.

## Step 3: Run the installer

Detect my OS and run the appropriate command:

**Linux / macOS:**
```bash
cd ~/.plamen && python3 plamen.py install
```

**Windows:**
```powershell
cd $HOME\.plamen; python plamen.py install
```

> Python dependencies (`rich`, `InquirerPy`, PyTorch, etc.) are installed automatically on first run. On macOS/Linux use `python3`, on Windows use `python`.

This will:
- Symlink Plamen's agents, rules, prompts, skills, and commands into `~/.claude/`
- Merge permissions and env vars into `settings.json` (additive — won't remove your existing entries)
- Merge MCP server definitions into `mcp.json` (won't overwrite your existing servers)
- Inject Plamen's CLAUDE.md instructions between `<!-- PLAMEN:START -->` / `<!-- PLAMEN:END -->` markers
- Install Python dependencies (~2GB for PyTorch embeddings)
- Build the RAG vulnerability database (using the Solodit key from Step 2)

## Step 4: Configure remaining API keys

Edit `~/.claude/mcp.json`:
- Replace `YOUR_RPC_URL` with an Ethereum RPC URL (Alchemy/Infura free tier, or public: `https://eth.llamarpc.com`)
- Replace `YOUR_ETHERSCAN_API_KEY` with a free key from https://etherscan.io/apis (optional)
- Replace `YOUR_TAVILY_API_KEY` with a free key from https://tavily.com (optional, used as RAG fallback)
- Replace `YOUR_HELIUS_API_KEY` with a free key from https://helius.dev (optional, Solana only)

**Fix the Python command for MCP servers (macOS/Linux only):**

The Python-based MCP servers (unified-vuln-db, solana-fender, farofino) use `"command": "python"` by default. On macOS and most Linux systems the executable is `python3`, not `python`. Check which exists:

```bash
which python || which python3
```

If only `python3` exists, update mcp.json:

```bash
# macOS:
sed -i '' 's/"command": "python"/"command": "python3"/g' ~/.claude/mcp.json
# Linux:
sed -i 's/"command": "python"/"command": "python3"/g' ~/.claude/mcp.json
```

On Windows, keep `"command": "python"` — that is the correct command.

**slither-mcp:** If `slither-mcp` is not found after `pip install slither-analyzer`, replace `"slither-mcp"` in mcp.json with the full path from `which slither-mcp` (macOS/Linux) or `where slither-mcp` (Windows).

> **If you skipped Step 2 or want to rebuild the RAG database after adding the Solodit key:**
> 1. Add `SOLODIT_API_KEY` to `~/.claude/settings.json` → `"env"` section (see Step 2 above)
> 2. Run `plamen rag` — rebuilds all sources from scratch. Safe to re-run as many times as needed.

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

If any step fails, check [docs/dependencies.md](docs/dependencies.md) for platform-specific fixes.

### MCP server fails to start (`spawn python ENOENT` or server shows red in Claude Code)
The Python-based MCP servers use `"command": "python"` in mcp.json. On macOS/Linux, change to `"command": "python3"`:
```bash
sed -i '' 's/"command": "python"/"command": "python3"/g' ~/.claude/mcp.json  # macOS
sed -i 's/"command": "python"/"command": "python3"/g' ~/.claude/mcp.json    # Linux
```
Then restart Claude Code.

### RAG database build failed partway through (network error, timeout, or partial entries)
Run `plamen rag` again — it wipes the existing database and rebuilds from scratch every time. Safe to re-run as many times as needed. Add `SOLODIT_API_KEY` to `~/.claude/settings.json` first if you haven't already.

### Solodit indexing was skipped (no API key during install)
1. Get a free key at https://solodit.cyfrin.io
2. Add it to `~/.claude/settings.json` → `"env"` section: `"SOLODIT_API_KEY": "your_key_here"`
3. Run `plamen rag` to rebuild with the key

### `macOS`: `hnswlib` build fail
Run `xcode-select --install` first, then retry.

### `macOS/Linux`: `externally-managed-environment`
Handled automatically by the installer. If running pip manually, add `--break-system-packages`.

### `Linux`: ChromaDB SQLite version error
`pip install pysqlite3-binary`

### `Python 3.13+`: PyTorch/sentence-transformers compatibility issues
Use Python 3.11 or 3.12. See [docs/dependencies.md](docs/dependencies.md) for venv setup.

### `Windows`: symlink permission error
Enable Developer Mode (Step 0b).

## Done

After setup, I can start an audit by typing `plamen` in my terminal or `/plamen` inside Claude Code.

Available commands (work from any directory after PATH is set):
- `plamen` — interactive wizard
- `plamen setup` — install tools + build RAG
- `plamen rag` — rebuild RAG database only
- `plamen uninstall` — remove Plamen from ~/.claude

> **Important**: Always use `plamen` (not `python3 plamen.py`) after PATH is set up. The `python3 plamen.py` form only works from inside `~/.plamen/`.
