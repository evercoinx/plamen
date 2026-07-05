# Plamen Codex Adapter

This directory contains Codex-compatible configuration files generated from the
Plamen audit pipeline's Claude-side manifests. These files allow Plamen to run
inside the [Codex CLI](https://github.com/openai/codex) in addition to Claude Code.

## Installation

```bash
# From the Plamen repo directory:
plamen install --codex

# Or manually:
python scripts/codex_adapter.py
```

The installer:
1. Generates Codex config files into this `codex-adapter/` directory
2. Creates `~/.codex/` if it does not exist
3. Symlinks `~/.codex/plamen/` to the Plamen repo (shared methodology files)
4. Copies Codex-specific files (`config.toml`, agent TOMLs, `AGENTS.md`) into `~/.codex/`

## Usage

After installation, open the Codex CLI and use the Plamen skill:

```bash
codex
# Then inside Codex:
/plamen core /path/to/project
/plamen thorough /path/to/project --docs /path/to/whitepaper.pdf
```

## Architecture

### What is shared (via symlink)

The Plamen methodology files are shared between Claude Code and Codex via a
symlink at `~/.codex/plamen/` pointing to the Plamen repo. This includes:

- `prompts/` -- language-specific phase prompts (recon, inventory, depth, verification)
- `agents/` -- depth agent definitions and skill files
- `rules/` -- finding format, confidence scoring, chain analysis, report templates
- `custom-mcp/` -- MCP server source code

### What is Codex-specific (in this directory)

- `AGENTS.md` -- Condensed orchestrator rules (under 32KB for Codex context)
- `config.toml` -- Codex main config with model, MCP server mappings
- `agents/*.toml` -- Role TOML files for each agent type
- `skills/plamen/SKILL.md` -- The `/plamen` orchestrator skill for Codex

### Regenerating

If you update Claude-side files (CLAUDE.md, mcp.json.example,
agent definitions), regenerate the Codex files:

```bash
python scripts/codex_adapter.py
```

## Current Limitations

- **Phase 1 generator**: Most adapter output content is templated, not fully
  derived from Claude-side manifests. MCP servers (from mcp.json.example)
  and agent role file lists (from agents/depth-*.md) are manifest-driven.
  AGENTS.md orchestrator rules, SKILL.md phase sequence, and agent
  developer_instructions are templated and must be updated manually when
  Claude-side files change. Phase 2 goal is to derive more content from
  CLAUDE.md and commands/plamen.md parsing.
- **Model**: On ChatGPT-authenticated Codex, use an entitled base model
  (`gpt-5.5` for the opus tier, `gpt-5.4` for sonnet, `gpt-5.4-mini` for haiku;
  see `_CODEX_MODEL_MAP` in `scripts/plamen_types.py`). `-codex`/preview
  suffixes such as `gpt-5.3-codex` are rejected on ChatGPT accounts. Codex
  context is smaller than Claude Code's Opus (1M context), so Thorough mode may
  require more careful context management.
- **Thorough mode parity**: Several Thorough-only features are experimental or
  not yet implemented on Codex. See the Mode Support Status table in
  `skills/plamen/SKILL.md` for details. Skeptic-Judge, invariant fuzz,
  Medusa fuzz, and finding perturbation are not yet available.
- **MCP servers**: All servers are mapped but may need manual API key configuration
  in `config.toml` (replace `YOUR_*_API_KEY` placeholders).
- **Platform**: Generated configs assume macOS/Linux (`python3`, forward slashes).
  Windows users should use WSL or adjust paths manually.
