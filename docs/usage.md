# Usage

> **Just installed?** See [getting-started.md](getting-started.md) first — what's required, what's optional, and how to run your first audit.

## Three Ways to Run

### Option A: Terminal Wrapper (recommended)

```bash
plamen
```

Interactive UI with dependency checking, tool installation, cost estimation, and backend launch (Claude Code or Codex CLI).

**CLI fast path** (skip the wizard):

```bash
plamen core /path/to/project --docs whitepaper.pdf
plamen thorough /path/to/project --scope scope.txt --network ethereum --proven-only
plamen l1 core /path/to/node-client     # L1 infrastructure audit
plamen l1 thorough /path/to/geth         # L1 thorough mode
plamen setup                        # install chain toolchains
plamen rag                          # build/rebuild RAG database
plamen uninstall                    # remove Plamen from ~/.claude (and ~/.codex/plamen/ if installed)
```

> **Important**: Always use `plamen` (not `python3 plamen.py`) after PATH is set. The `python3 plamen.py` form only works from inside `~/.plamen/`.

**PATH setup** (to use `plamen` as a command):

```bash
# Linux (bash)
echo 'export PATH="$HOME/.plamen:$PATH"' >> ~/.bashrc && source ~/.bashrc

# macOS (zsh — default shell on macOS)
echo 'export PATH="$HOME/.plamen:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

```powershell
# Windows (PowerShell, one-time)
[System.Environment]::SetEnvironmentVariable("Path", "$env:USERPROFILE\.plamen;" + $env:Path, "User")
```

Or run directly: `python3 ~/.plamen/plamen.py` (macOS/Linux) or `python ~/.plamen/plamen.py` (Windows)

### Option B: Inside Claude Code

```
> /plamen
> /plamen core /path/to/project docs: /path/to/docs
> /plamen thorough /path/to/project scope: scope.txt proven-only: true
> /plamen compare report: audit.md ground_truth: reference.md
```

### Option C: Inside Codex CLI

```
> $plamen core /path/to/project
> $plamen l1 thorough /path/to/node-client
```

Codex backend requires prior setup: `plamen install --codex`. The V2 driver rewrites paths and translates tool calls automatically.

### Option D: V2 Resumable Pipeline

```bash
# Interactive wizard (Claude Code)
/plamen-wizard

# Direct driver launch
python ~/.plamen/scripts/plamen_driver.py /path/to/project/.scratchpad/config.json

# Fresh restart (discard previous progress)
python ~/.plamen/scripts/plamen_driver.py --fresh /path/to/project/.scratchpad/config.json
```

The V2 driver runs each phase as a separate subprocess (`claude -p` on Claude Code, `codex exec` on Codex) with automatic checkpointing. The backend is auto-detected via `plamen_home()`. If the process crashes or hits rate limits, re-run the same command to resume from the last successful phase.

### When to Use Which

| | Terminal Wrapper | Claude Code | Codex CLI | V2 Driver |
|---|---|---|---|---|
| **First time** | Use this | Need tools installed | Need Codex + tools | Need tools installed |
| **Cost estimate** | Shows estimate | No estimate | No estimate | No estimate |
| **Resume on crash** | No | No | No | **Yes** |
| **Daily use** | `plamen core .` | `/plamen core .` | `$plamen core .` | Auto via wizard |

## Cost Estimation

The wrapper estimates token usage before launch:
- Input/Output tokens (millions)
- API cost (USD)
- Weekly plan usage (% of Pro, Max x5, Max x20)

Estimates are rough -- actual usage varies with protocol complexity. Run `/cost` after an audit for actuals.
