---
description: "Plamen v2 Smart Contract audit wizard. Collects config, launches deterministic driver."
---

# Plamen v2 — Smart Contract Audit Wizard

> This wizard collects audit parameters and launches `plamen_driver.py`.
> The driver handles ALL phase sequencing deterministically — no LLM orchestration.

## Step 1: Banner

Output:

```
██████╗ ██╗      █████╗ ███╗   ███╗███████╗███╗   ██╗
██╔══██╗██║     ██╔══██╗████╗ ████║██╔════╝████╗  ██║
██████╔╝██║     ███████║██╔████╔██║█████╗  ██╔██╗ ██║
██╔═══╝ ██║     ██╔══██║██║╚██╔╝██║██╔══╝  ██║╚██╗██║
██║     ███████╗██║  ██║██║ ╚═╝ ██║███████╗██║ ╚████║
╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═══╝
```

**Web3 Security Auditor** v2.0.0 — Deterministic Driver

Then run the toolchain probe:

```bash
export PATH="$HOME/.foundry/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.avm/bin:$HOME/.cargo/bin:$HOME/.aptoscli/bin:$HOME/.local/bin:$HOME/go/bin:$PATH" && \
echo "Toolchain:" && \
echo -n "  Required: " && \
(command -v claude >/dev/null 2>&1 && echo -n "claude " || echo -n "MISSING:claude ") && \
(command -v python >/dev/null 2>&1 && echo -n "python " || (command -v python3 >/dev/null 2>&1 && echo -n "python " || echo -n "MISSING:python ")) && \
(command -v git >/dev/null 2>&1 && echo -n "git" || echo -n "MISSING:git") && echo "" && \
echo -n "  EVM:      " && \
(command -v forge >/dev/null 2>&1 && echo -n "forge " || echo -n "-forge ") && \
(command -v slither >/dev/null 2>&1 && echo -n "slither " || echo -n "-slither ") && \
(command -v medusa >/dev/null 2>&1 && echo -n "medusa" || echo -n "-medusa") && echo "" && \
echo -n "  Solana:   " && \
(command -v solana >/dev/null 2>&1 && echo -n "solana " || echo -n "-solana ") && \
(command -v anchor >/dev/null 2>&1 && echo -n "anchor " || echo -n "-anchor ") && \
(command -v trident >/dev/null 2>&1 && echo -n "trident" || echo -n "-trident") && echo "" && \
echo -n "  Move:     " && \
(command -v aptos >/dev/null 2>&1 && echo -n "aptos " || echo -n "-aptos ") && \
(command -v sui >/dev/null 2>&1 && echo -n "sui" || echo -n "-sui") && echo "" && \
echo -n "  Soroban:  " && \
(command -v stellar >/dev/null 2>&1 && echo -n "stellar " || echo -n "-stellar ") && \
(cargo scout-audit --version >/dev/null 2>&1 && echo -n "scout" || echo -n "-scout") && echo ""
```

## Step 1.5: Detect Existing Audit

Before collecting config, check if an existing audit can be resumed. Search for `.scratchpad/config.json` in the current directory (or the path from `$ARGUMENTS` if provided):

```bash
# Check cwd and common patterns
if [ -f ".scratchpad/config.json" ]; then echo "FOUND:.scratchpad/config.json"
elif [ -f "src/.scratchpad/config.json" ]; then echo "FOUND:src/.scratchpad/config.json"
elif [ -f "contracts/.scratchpad/config.json" ]; then echo "FOUND:contracts/.scratchpad/config.json"
else echo "NONE"; fi
```

If a config is found, read the checkpoint file (`_v2_checkpoint.json` in that scratchpad) to determine progress, then offer:

```
AskUserQuestion(questions=[{
  question: "Existing audit detected. What would you like to do?",
  header: "Existing Audit Found",
  options: [
    { label: "Resume", description: "Continue from last checkpoint ({LAST_PHASE} → next)" },
    { label: "Fresh restart", description: "Wipe scratchpad and start over" },
    { label: "New audit", description: "Ignore existing, configure a new target" }
  ]
}])
```

- **Resume**: Skip to Step 3's launch section — use the existing `config.json` path directly. Launch with `run_in_background: true`.
- **Fresh restart**: Launch with `--fresh` flag. Skip to Step 3's launch section.
- **New audit**: Fall through to Step 2 (collect config normally).

If no config found, fall through to Step 2.

## Step 2: Collect Configuration

Ask these questions using AskUserQuestion. Parse `$ARGUMENTS` first for shortcuts (`light`, `core`, `thorough`, `compare`, path arguments).

**Q1: Mode**

```
AskUserQuestion(questions=[{
  question: "Which audit mode?",
  header: "Mode",
  multiSelect: false,
  options: [
    { label: "Light (Pro plan)", description: "~18-22 Sonnet agents. Fast, fits Pro rate limits." },
    { label: "Core (Recommended)", description: "~30-50 agents (Max plan). Standard audit depth." },
    { label: "Thorough", description: "~50-70 agents (Max plan). Iterative depth, fuzz, skeptic-judge." },
    { label: "Compare", description: "Diff a past report against ground truth." }
  ]
}])
```

If "Compare" is selected, inform the user that compare mode is not yet ported to v2 and they should use `/plamen compare` with the v1 pipeline. Stop here.

**Q2: Target project**

```
AskUserQuestion(questions=[{
  question: "Is this the project to audit?",
  header: "Target",
  options: [
    { label: "Yes, use current directory", description: "{cwd}" },
    { label: "No, let me specify", description: "I'll provide a path" }
  ]
}])
```

If "No", ask for the path.

**Q3: Documentation**

```
AskUserQuestion(questions=[{
  question: "Do you have project docs with trust roles or actor permissions?",
  header: "Docs",
  options: [
    { label: "No docs", description: "Trust roles inferred from code" },
    { label: "Yes, local files", description: "Whitepaper/spec with trust info" },
    { label: "Yes, a URL", description: "Link to docs" }
  ]
}])
```

If local files or URL, ask for the path/URL.

**Q4: Scope**

```
AskUserQuestion(questions=[{
  question: "Limit the audit scope?",
  header: "Scope",
  options: [
    { label: "Full project", description: "Audit everything" },
    { label: "Scope file", description: "I have a scope.txt" },
    { label: "Scope notes", description: "I'll describe focus areas" }
  ]
}])
```

If scope file or notes, collect them.

**Q5: Proven-only mode**

```
AskUserQuestion(questions=[{
  question: "Enable proven-only mode? (unproven findings capped at Low)",
  header: "Proven-Only",
  options: [
    { label: "No (default)", description: "Standard severity rules" },
    { label: "Yes", description: "Require executed PoC for Medium+ severity" }
  ]
}])
```

## Step 3: Write config.json and launch

After all questions are answered, write the config to the project's scratchpad:

```python
Before writing config, detect the language. Decide PRIMARILY from the dominant
production source-file EXTENSION found recursively under PROJECT_PATH (count
files; the most common recognized suffix wins). Walk UP to the nearest
`Cargo.toml` / `Move.toml` (the manifest is often an ancestor of a scope-dir
PROJECT_PATH such as `.../<crate>/src/`) and use the manifest grep ONLY to
disambiguate solana-vs-soroban and aptos-vs-sui — never as the primary signal.
Use explicit if/elif blocks (no chained `&&`/`||` one-liner — its precedence is
wrong). Do NOT silently default to `evm`; if no recognized source files are
found, emit `indeterminate` and let the driver's startup language-consistency
gate surface it.

```bash
cd "{PROJECT_PATH}"

# 1. Count dominant source extension recursively (production dirs only).
SOL=$(find . -name "*.sol" -not -path "*/node_modules/*" -not -path "*/lib/*" 2>/dev/null | wc -l)
RS=$(find . -name "*.rs" -not -path "*/target/*" 2>/dev/null | wc -l)
MOVE=$(find . -name "*.move" -not -path "*/build/*" 2>/dev/null | wc -l)

# 2. Locate the nearest manifest by walking UP from PROJECT_PATH.
find_up() {
  local name="$1" dir
  dir="$(pwd)"
  while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if [ -f "$dir/$name" ]; then echo "$dir/$name"; return 0; fi
    dir="$(dirname "$dir")"
  done
  return 1
}
CARGO=$(find_up Cargo.toml)
MOVETOML=$(find_up Move.toml)

# 3. Decide by dominant extension; manifest disambiguates only within a family.
if [ "$SOL" -gt 0 ] && [ "$SOL" -ge "$RS" ] && [ "$SOL" -ge "$MOVE" ]; then
  echo "evm"
elif [ "$RS" -gt 0 ] && [ "$RS" -ge "$MOVE" ]; then
  if [ -n "$CARGO" ] && grep -rq "anchor" "$CARGO" 2>/dev/null; then
    echo "solana"
  elif [ -n "$CARGO" ] && grep -rq "soroban" "$CARGO" 2>/dev/null; then
    echo "soroban"
  else
    echo "solana"
  fi
elif [ "$MOVE" -gt 0 ]; then
  if [ -n "$MOVETOML" ] && grep -rq "AptosFramework\|aptos" "$MOVETOML" 2>/dev/null; then
    echo "aptos"
  else
    echo "sui"
  fi
else
  echo "indeterminate"
fi
```

Set `LANGUAGE` to the output. If the output is `indeterminate`, ask the user to
confirm the language explicitly before writing config (the driver will halt at
startup on a definite source/language contradiction).

```python
config = {
    "project_root": PROJECT_PATH,
    "scratchpad": f"{PROJECT_PATH}/.scratchpad",
    "mode": MODE,           # "light" | "core" | "thorough"
    "pipeline": "sc",
    "language": LANGUAGE,   # "evm" | "solana" | "soroban" | "aptos" | "sui"
    "cli_backend": "claude",
    "claude_exec_mode": "pty",
    "docs_path": DOCS_PATH or "",
    "scope_file": SCOPE_FILE or "",
    "scope_notes": SCOPE_NOTES or "",
    "proven_only": PROVEN_ONLY or False
}
```
```

Write this JSON to `{PROJECT_PATH}/.scratchpad/config.json` (create .scratchpad/ if needed).

Before launching, print the pre-launch message so the user knows what to expect:

```
Launching V2 deterministic driver in background...

Config: {PROJECT_PATH}/.scratchpad/config.json

Monitor progress:  tail -f "{PROJECT_PATH}/.scratchpad/_plamen.log"
Check checkpoint:  cat "{PROJECT_PATH}/.scratchpad/_v2_checkpoint.json"

If the audit is interrupted (usage cap, crash, Ctrl+C), resume with:
  python3 ~/.claude/scripts/plamen_driver.py "{PROJECT_PATH}/.scratchpad/config.json"

Preview remaining phases:
  python3 ~/.claude/scripts/plamen_driver.py --dry-run "{PROJECT_PATH}/.scratchpad/config.json"

Clean restart:
  python3 ~/.claude/scripts/plamen_driver.py --fresh "{PROJECT_PATH}/.scratchpad/config.json"
```

Then launch the driver **in the background** so the Claude Code session remains interactive. Use a single Bash tool call with `run_in_background: true`:

```bash
python3 ~/.claude/scripts/plamen_driver.py "{PROJECT_PATH}/.scratchpad/config.json"
```

Set `run_in_background: true` on the Bash tool call. Do NOT use `&` or `nohup` — Claude Code's `run_in_background` parameter handles this natively and will notify when the process completes.

**HARD RULE**: After launching the background Bash call, do NOT launch any additional Bash commands, background processes, or agents related to the audit. The driver is the sole owner of the pipeline. Tell the user the audit is running and they can continue using this session.

## Step 4: Handle Driver Completion

The driver runs in the background. When it completes, Claude Code will notify you. At that point, check the exit code:

- **Exit 0**: Pipeline completed. Tell the user: `Report is at {PROJECT_PATH}/AUDIT_REPORT.md`
- **Exit 2 (rate limit / usage exhausted)**: The driver saved a checkpoint. Tell the user:

```
Pipeline paused — rate limit or usage cap reached.

Resume when quota refreshes:
  python3 ~/.claude/scripts/plamen_driver.py "{PROJECT_PATH}/.scratchpad/config.json"

The driver auto-resumes from the last successful phase. No data is lost.
```

- **Exit 1 (error)**: Check `{PROJECT_PATH}/.scratchpad/violations.md` for details. The driver can still be resumed — it will re-attempt failed phases:

```
Pipeline stopped with errors. Check violations:
  cat "{PROJECT_PATH}/.scratchpad/violations.md"

Resume (re-attempts failed phases):
  python3 ~/.claude/scripts/plamen_driver.py "{PROJECT_PATH}/.scratchpad/config.json"
```
