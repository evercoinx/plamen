# Automated Setup — Paste This Into Claude Code

> **For users who prefer Claude Code to handle the entire installation.**
> Copy everything below the line into a Claude Code session and it will set up Plamen for you.

---

Please set up Plamen (Web3 Security Auditor) on my machine. Follow these steps exactly:

## Step 1: Clone the repository

```bash
git clone https://github.com/PlamenTSV/plamen.git ~/.claude
cd ~/.claude
git submodule update --init --recursive
```

If `~/.claude` already exists, back it up first:
```bash
mv ~/.claude ~/.claude.backup
```

## Step 2: Install Python dependencies

```bash
pip install -r ~/.claude/requirements.txt
pip install -r ~/.claude/custom-mcp/unified-vuln-db/requirements.txt
pip install -r ~/.claude/custom-mcp/solodit-scraper/requirements.txt
pip install -r ~/.claude/custom-mcp/defihacklabs-rag/requirements.txt
pip install -e ~/.claude/custom-mcp/solana-fender
pip install -r ~/.claude/custom-mcp/farofino-mcp/requirements.txt
pip install -e ~/.claude/custom-mcp/slither-mcp  # EVM only — skip if not auditing Solidity
```

## Step 3: Configure MCP servers and API keys

Copy the example configs:
```bash
cp ~/.claude/mcp.json.example ~/.claude/mcp.json
cp ~/.claude/settings.json.example ~/.claude/settings.json
```

Then edit `~/.claude/mcp.json`:
- Replace `YOUR_SOLODIT_API_KEY` with a free key from https://solodit.cyfrin.io (**recommended** — needed to index 3400+ findings)
- Replace `YOUR_RPC_URL` with an Ethereum RPC URL (Alchemy/Infura free tier, or public: `https://eth.llamarpc.com`)
- Replace `YOUR_ETHERSCAN_API_KEY` with a free key from https://etherscan.io/apis (optional)
- Replace `YOUR_TAVILY_API_KEY` with a free key from https://tavily.com (optional, used as RAG fallback)
- Replace `YOUR_HELIUS_API_KEY` with a free key from https://helius.dev (optional, Solana only)
- Update the `command` paths for Python and slither-mcp to match my system

For the Python command path, run `which python` (Unix) or `where python` (Windows) and use that path.

## Step 4: Build the RAG vulnerability database

Set the Solodit API key first (needed for the largest data source):
```bash
export SOLODIT_API_KEY=your_key_here
```

Then build:
```bash
cd ~/.claude/custom-mcp/unified-vuln-db
python -m unified_vuln.indexer index -s solodit --max-pages 10
python -m unified_vuln.indexer index -s defihacklabs
python -m unified_vuln.indexer index -s immunefi
python -m unified_vuln.indexer stats
```

Without `SOLODIT_API_KEY`, only DeFiHackLabs + Immunefi are indexed (~700 entries vs ~4000).

## Step 5: Verify installation

Run the terminal wrapper to check everything:
```bash
python ~/.claude/plamen.py setup
```

This shows the toolchain status box. If any optional tools are missing (Foundry, Solana CLI, etc.), the Setup menu can install them automatically.

## Step 6: Add to PATH (optional)

So I can just type `plamen` from any directory:

**Unix/macOS** — add to shell profile:
```bash
echo 'export PATH="$HOME/.claude:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

**Windows** — run in PowerShell:
```powershell
[System.Environment]::SetEnvironmentVariable("Path", "$env:USERPROFILE\.claude;" + [System.Environment]::GetEnvironmentVariable("Path", "User"), "User")
```

## Done

After setup, I can start an audit by typing `plamen` in my terminal or `/plamen` inside Claude Code.
