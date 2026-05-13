# Updating Plamen

## Quick Update

```bash
cd ~/.plamen && git pull && plamen install
```

`plamen install` is safe to re-run at any time. It will not wipe your RAG database, re-install toolchains, or overwrite your API keys.

If you skip `plamen install` after pulling, `plamen` will warn you on next launch:

```
⚠ Version mismatch: repo is v2.0.0 but ~/.claude/CLAUDE.md has v1.0.6
  Run 'plamen install' to update. Pipeline may behave incorrectly until then.
```

(Codex backend shows the equivalent: `~/.codex/AGENTS.md has v1.0.6`.)

---

## What Updates Automatically (just `git pull`)

These components are symlinked as **directories** — new and modified files are immediately visible:

| Component | Symlink Type | Claude Code Path | Codex Path (`--codex`) |
|-----------|-------------|------|------|
| Skills (standard + injectable + niche) | Directory | `~/.claude/agents/skills/` → `~/.plamen/agents/skills/` | `~/.codex/plamen/agents/skills/` → same |
| Prompts (all 5 language trees) | Directory | `~/.claude/prompts/` → `~/.plamen/prompts/` | `~/.codex/plamen/prompts/` → same |
| MCP server source code | Directory | `~/.claude/custom-mcp/` → `~/.plamen/custom-mcp/` | N/A (Codex uses tool translation, not MCP) |

These are symlinked as **individual files** — existing files update, but no new files were added since v1.0.0:

| Component | Claude Code Path | Codex Path (`--codex`) |
|-----------|------|------|
| Agent definitions | `~/.claude/agents/depth-*.md`, `security-*.md` | `~/.codex/plamen/agents/` (same files) |
| Rule files | `~/.claude/rules/*.md` | `~/.codex/plamen/rules/` (same files) |
| `/plamen` command | `~/.claude/commands/plamen.md` | N/A (Codex uses `AGENTS.md` orchestrator) |
| CLI wrapper | `~/.claude/plamen.py`, `plamen.sh`, `plamen.bat` | Same (shared CLI entry point) |
| VERSION file | `~/.claude/VERSION` | `~/.codex/plamen/VERSION` (same file) |

---

## What Requires `plamen install`

These are **not symlinked** — they are merged/injected at install time:

| Component | Why Not Symlinked | What Install Does |
|-----------|-------------------|-------------------|
| **CLAUDE.md** (Claude Code) | User may have their own content | Strips old `<!-- PLAMEN:START -->...<!-- PLAMEN:END -->` section, re-injects current version. User content outside markers is preserved. |
| **AGENTS.md** (Codex, `--codex`) | User may have their own content | Same marker-based injection as CLAUDE.md. Codex equivalent of the orchestrator config. |
| **settings.json** (Claude Code) | User has their own API keys and permissions | Additive merge: adds new env vars and permissions that don't exist. Never overwrites existing keys. |
| **config.toml** (Codex, `--codex`) | User has their own Codex settings | Full copy from `codex-adapter/config.toml`. Overwrites existing file — back up custom settings before re-install. |
| **mcp.json** (Claude Code only) | User has their own MCP servers and API keys | Additive merge: adds new server entries that don't exist. Fixes wrong-platform paths (e.g., Windows `C:/` on macOS) in existing servers while preserving env vars and API keys. |
| **MCP packages** (Claude Code legacy only) | Only if `~/.claude.json` has bare `npx -y @pkg` without version pins | Installs pinned npm packages locally, updates config to use schema sanitizer. Skipped for fresh installs. |

**CLAUDE.md (or AGENTS.md for Codex) is the critical one.** It contains the orchestrator's rules — agent counts, mode table, critical rules, and phase references. If it is stale, the orchestrator follows old rules while skills and prompts are already updated. This can cause wrong agent counts, skipped mandatory steps, or mismatched phase references.

---

## MCP Package Pinning (Claude Code Only)

npm-based MCP servers (`evm-chain-data`, `foundry-suite`, `tavily-search`, `memory`, `helius`) are pinned to specific versions in `mcp-packages/package.json` to prevent the Anthropic API `oneOf`/`allOf` schema rejection error caused by upstream npm package updates. This applies only to Claude Code -- Codex uses tool translation instead of MCP.

`plamen install` handles this automatically:
1. Runs `npm install` in `mcp-packages/` (installs pinned versions to `node_modules/`)
2. Runs `update_config.py` to update `~/.claude/mcp.json` with pinned paths + schema sanitizer

If you add a new npm MCP server or update a version:
1. Edit `mcp-packages/package.json`
2. Run `cd ~/.plamen/mcp-packages && npm install` (or just `plamen install`)
3. Restart Claude Code

The `schema-sanitizer.js` proxy wraps `evm-chain-data` and `foundry-suite` — it strips `oneOf`/`allOf`/`anyOf` from tool schemas before they reach the API. Safe servers run directly without the proxy.

---

## What Is Never Touched

| Component | Location | Update Method |
|-----------|----------|---------------|
| RAG database | `~/.plamen/custom-mcp/unified-vuln-db/data/` (gitignored) | `plamen rag` (manual, explicit). Re-run after update if new indexers were added (e.g., Immunefi Competitions in v1.1.5). |
| Toolchains (Foundry, Solana CLI, etc.) | System-level installs | `plamen setup` (interactive, checkbox) |
| API keys | In `settings.json`/`mcp.json` (Claude Code) or `config.toml` (Codex) | Manual edit only |
| User's own agents/rules | `~/.claude/` (non-Plamen files) or `~/.codex/` (non-Plamen files) | Never modified by either `plamen install` or `plamen install --codex` |

---

## Codex Backend Updates

If you use the Codex CLI backend (`~/.codex/plamen/`), updating follows the same pattern:

```bash
cd ~/.plamen && git pull && plamen install --codex
```

If you use **both** backends, run both installs:

```bash
cd ~/.plamen && git pull && plamen install && plamen install --codex
```

**Key differences from Claude Code install:**
- `AGENTS.md` (not `CLAUDE.md`) receives the marker-based orchestrator injection
- `config.toml` (not `settings.json`) receives additive model/sandbox merges
- MCP servers are not configured (Codex uses tool translation instead of MCP)
- Symlinked directories (skills, prompts, agent definitions, rules) work identically

---

## Install Is Idempotent

Running `plamen install` (or `plamen install --codex`) multiple times is safe. Here's what each step does on re-install:

| Step | First Install | Re-install | Codex (`--codex`) |
|------|--------------|------------|-------------------|
| Symlinks | Creates new links | Removes old links, recreates (same result) | Same (into `~/.codex/plamen/`) |
| User file backup | Backs up to `.pre-plamen` | Skips if backup already exists | Same |
| settings.json / config.toml | Merges Plamen entries (permissions, env) | Skips entries that already exist | Merges model routing and sandbox entries into `config.toml` |
| mcp.json | Merges Plamen servers | Skips existing servers, but fixes wrong-platform paths and backfills new env vars | N/A (Codex has no MCP) |
| CLAUDE.md / AGENTS.md | Injects between markers | Strips old injection, re-injects current | Same (into `AGENTS.md`) |
| Python deps | Installs packages | `pip` skips already-installed packages | Same |
| Toolchains | Shows missing as checkboxes | Already-installed tools don't appear in list | Same |
| RAG | Shows as optional checkbox | Only offered if empty or user explicitly selects | Same |

---

## Version History

See [CHANGELOG.md](../CHANGELOG.md) for what changed in each version.
