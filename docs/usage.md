# Usage

## Two Ways to Run

### Option A: Terminal Wrapper (recommended)

```bash
plamen
```

Interactive UI with dependency checking, tool installation, cost estimation, and Claude Code launch.

**CLI fast path** (skip the wizard):

```bash
plamen core /path/to/project --docs whitepaper.pdf
plamen thorough /path/to/project --scope scope.txt --network ethereum --proven-only
plamen setup                        # just install tools + build RAG
```

**PATH setup** (to use `plamen` as a command):

```bash
# Unix/macOS -- add to ~/.bashrc or ~/.zshrc
export PATH="$HOME/.claude:$PATH"

# Windows -- run once in PowerShell
[System.Environment]::SetEnvironmentVariable("Path", "$env:USERPROFILE\.claude;" + $env:Path, "User")
```

Or run directly: `python ~/.claude/plamen.py`

### Option B: Inside Claude Code

```
> /plamen
> /plamen core /path/to/project docs: /path/to/docs
> /plamen thorough /path/to/project scope: scope.txt proven-only: true
> /plamen compare report: audit.md ground_truth: reference.md
```

### When to Use Which

| | Terminal Wrapper | Claude Code |
|---|---|---|
| **First time** | Use this -- Setup installs tools + builds RAG | Need tools already installed |
| **Cost estimate** | Shows token/cost estimate before launch | No estimate |
| **Dependency check** | Full toolchain probe with install option | Basic probe |
| **Daily use** | `plamen core .` | `/plamen core .` |
| **Already in Claude** | Opens new session | Uses current session |

## Cost Estimation

The wrapper estimates token usage before launch:
- Input/Output tokens (millions)
- API cost (USD)
- Weekly plan usage (% of Pro, Max x5, Max x20)

Estimates are rough -- actual usage varies with protocol complexity. Run `/cost` after an audit for actuals.
