# Updating Plamen

## Quick Update

```bash
cd ~/.plamen && git pull && plamen install
```

`plamen install` is safe to re-run at any time. It will not wipe your RAG database, re-install toolchains, or overwrite your API keys.

If you skip `plamen install` after pulling, `plamen` will warn you on next launch:

```
⚠ Version mismatch: repo is v1.1.7 but ~/.claude/CLAUDE.md has v1.0.6
  Run 'plamen install' to update. Pipeline may behave incorrectly until then.
```

---

## What Updates Automatically (just `git pull`)

These components are symlinked as **directories** — new and modified files are immediately visible:

| Component | Symlink Type | Path |
|-----------|-------------|------|
| Skills (standard + injectable + niche) | Directory | `~/.claude/agents/skills/` → `~/.plamen/agents/skills/` |
| Prompts (all 4 language trees) | Directory | `~/.claude/prompts/` → `~/.plamen/prompts/` |
| MCP server source code | Directory | `~/.claude/custom-mcp/` → `~/.plamen/custom-mcp/` |

These are symlinked as **individual files** — existing files update, but no new files were added since v1.0.0:

| Component | Path |
|-----------|------|
| Agent definitions | `~/.claude/agents/depth-*.md`, `security-*.md` |
| Rule files | `~/.claude/rules/*.md` |
| `/plamen` command | `~/.claude/commands/plamen.md` |
| CLI wrapper | `~/.claude/plamen.py`, `plamen.sh`, `plamen.bat` |
| VERSION file | `~/.claude/VERSION` |

---

## What Requires `plamen install`

These are **not symlinked** — they are merged/injected at install time:

| Component | Why Not Symlinked | What Install Does |
|-----------|-------------------|-------------------|
| **CLAUDE.md** | User may have their own content | Strips old `<!-- PLAMEN:START -->...<!-- PLAMEN:END -->` section, re-injects current version. User content outside markers is preserved. |
| **settings.json** | User has their own API keys and permissions | Additive merge: adds new env vars and permissions that don't exist. Never overwrites existing keys. |
| **mcp.json** | User has their own MCP servers and API keys | Additive merge: adds new server entries that don't exist. Fixes wrong-platform paths (e.g., Windows `C:/` on macOS) in existing servers while preserving env vars and API keys. |
| **MCP packages** (legacy only) | Only if `~/.claude.json` has bare `npx -y @pkg` without version pins | Installs pinned npm packages locally, updates config to use schema sanitizer. Skipped for fresh installs. |

**CLAUDE.md is the critical one.** It contains the orchestrator's rules — agent counts, mode table, critical rules, and phase references. If it is stale, the orchestrator follows old rules while skills and prompts are already updated. This can cause wrong agent counts, skipped mandatory steps, or mismatched phase references.

---

## MCP Package Pinning

npm-based MCP servers (`evm-chain-data`, `foundry-suite`, `tavily-search`, `memory`, `helius`) are pinned to specific versions in `mcp-packages/package.json` to prevent the Anthropic API `oneOf`/`allOf` schema rejection error caused by upstream npm package updates.

`plamen install` handles this automatically:
1. Runs `npm install` in `mcp-packages/` (installs pinned versions to `node_modules/`)
2. Runs `update_config.py` to update `~/.claude.json` with pinned paths + schema sanitizer

If you add a new npm MCP server or update a version:
1. Edit `mcp-packages/package.json`
2. Run `cd ~/.claude/mcp-packages && npm install` (or just `plamen install`)
3. Restart Claude Code

The `schema-sanitizer.js` proxy wraps `evm-chain-data` and `foundry-suite` — it strips `oneOf`/`allOf`/`anyOf` from tool schemas before they reach the API. Safe servers run directly without the proxy.

---

## What Is Never Touched

| Component | Location | Update Method |
|-----------|----------|---------------|
| RAG database | `~/.plamen/custom-mcp/unified-vuln-db/data/` (gitignored) | `plamen rag` (manual, explicit). Re-run after update if new indexers were added (e.g., Immunefi Competitions in v1.1.5). |
| Toolchains (Foundry, Solana CLI, etc.) | System-level installs | `plamen setup` (interactive, checkbox) |
| API keys | In `settings.json` and `mcp.json` | Manual edit only |
| User's own Claude Code agents/rules | `~/.claude/` (non-Plamen files) | Never modified |

---

## Install Is Idempotent

Running `plamen install` multiple times is safe. Here's what each step does on re-install:

| Step | First Install | Re-install |
|------|--------------|------------|
| Symlinks | Creates new links | Removes old links, recreates (same result) |
| User file backup | Backs up to `.pre-plamen` | Skips if backup already exists |
| settings.json | Merges Plamen entries | Skips entries that already exist |
| mcp.json | Merges Plamen servers | Skips existing servers, but fixes wrong-platform paths and backfills new env vars |
| CLAUDE.md | Injects between markers | Strips old injection, re-injects current |
| Python deps | Installs packages | `pip` skips already-installed packages |
| Toolchains | Shows missing as checkboxes | Already-installed tools don't appear in list |
| RAG | Shows as optional checkbox | Only offered if empty or user explicitly selects |

---

## Version History

See [CHANGELOG.md](../CHANGELOG.md) for what changed in each version.
