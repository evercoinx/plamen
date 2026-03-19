# Automated Setup — Paste This Into Claude Code

> **For users who prefer Claude Code to handle the entire installation.**
> Copy everything below the line into a Claude Code session and it will set up Plamen for you.

---

Please set up Plamen (Web3 Security Auditor) on my machine. Follow these steps exactly:

## Step 0: Check prerequisites

Check if the required tools are installed. Run these checks and report what's missing:

```bash
python3 --version || python --version    # need 3.11+
node --version                           # need 18+
npx --version
git --version
```

If any are missing:
- **Python**: Download from https://python.org (3.11 or later). On macOS: `brew install python@3.12`. On Ubuntu: `sudo apt install python3.12 python3.12-venv python3-pip`.
- **Node.js**: Download from https://nodejs.org (LTS). On macOS: `brew install node`. On Ubuntu: `curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt install -y nodejs`.
- **Git**: Download from https://git-scm.com. On macOS: `brew install git`. On Ubuntu: `sudo apt install git`.

Do NOT proceed to Step 1 until all four tools are available.

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

The installer builds the RAG vulnerability database during setup. The Solodit source (3400+ audit findings — the largest and most important source) requires an API key. **Set this before running the installer**, otherwise Solodit indexing will fail silently and you'll get a weaker RAG database.

Set the Solodit API key as an environment variable (free key from https://solodit.cyfrin.io):

**Linux / macOS:**
```bash
export SOLODIT_API_KEY=your_key_here
```

**Windows (PowerShell):**
```powershell
$env:SOLODIT_API_KEY = "your_key_here"
```

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
- Update the `command` paths for Python and slither-mcp to match my system

For the Python command path, run `which python3` (macOS/Linux) or `where python` (Windows) and use that path.

> If you skipped Step 2 or want to rebuild the RAG database after adding the Solodit key, run:
> `export SOLODIT_API_KEY=your_key_here && python3 -m unified_vuln.indexer index -s solodit --max-pages 10` (use `python` on Windows). This works from any directory after install.

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

## Done

After setup, I can start an audit by typing `plamen` in my terminal or `/plamen` inside Claude Code.

Available commands (work from any directory after PATH is set):
- `plamen` — interactive wizard
- `plamen setup` — install tools + build RAG
- `plamen rag` — rebuild RAG database only
- `plamen uninstall` — remove Plamen from ~/.claude

> **Important**: Always use `plamen` (not `python3 plamen.py`) after PATH is set up. The `python3 plamen.py` form only works from inside `~/.plamen/`.
