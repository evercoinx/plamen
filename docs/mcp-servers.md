# MCP Servers

Plamen uses 9 MCP servers configured in `mcp.json` (Claude Code) or `[mcp_servers.*]` TOML sections in `~/.codex/config.toml` (Codex). All keys are optional — the pipeline degrades gracefully.

MCP runs natively under both backends. On Claude Code, `plamen install` registers each server with the `claude` CLI itself (`claude mcp add-json <name> <json> -s user`), persisted to `~/.claude.json` under user scope — this is the only mechanism the `claude` binary actually reads; an `mcpServers` key placed directly in `~/.claude/settings.json` or `~/.claude/mcp.json` is inert and silently ignored. User scope means registration is available regardless of the cwd `plamen`/`claude` is invoked from. On Codex the servers are loaded from `[mcp_servers.*]` blocks in `~/.codex/config.toml`, which `scripts/codex_adapter.py:generate_config_toml` generates from `mcp.json.example` at install time and `plamen install --codex` copies into place. The "tool translation" sometimes referenced elsewhere is prompt-text rewriting in `plamen_driver.py` (paths, `Task()` → `spawn_agent`, bash → PowerShell) — it is NOT an MCP transport shim.

Two Codex-specific caveats:
1. `evm-chain-data` is currently disabled on Codex due to an MCP protocol version mismatch (`scripts/codex_adapter.py:276`).
2. Four Python MCP servers (`slither-analyzer`, `unified-vuln-db`, `farofino`, `solana-fender`) are wrapped through `mcp-packages/schema-sanitizer.js` to strip `oneOf`/`allOf` JSON-schema constructs Codex rejects (`scripts/codex_adapter.py:270-275`).

Tool permissions on Codex cannot be pre-configured: select "Always allow" on the first prompt per MCP server (`plamen.py:_install_codex_adapter`).

## Bundled (custom-mcp/)

| Server | Purpose | Required? |
|--------|---------|-----------|
| **unified-vuln-db** | RAG vulnerability database (Solodit, DeFiHackLabs, Immunefi, Immunefi Competitions) | **Required** |
| **solana-fender** | Solana program static security analysis | Optional (Solana only) |

## Submodules (custom-mcp/)

| Server | Purpose | Required? |
|--------|---------|-----------|
| **[slither-mcp](https://github.com/trailofbits/slither-mcp)** | Slither static analyzer | Optional (EVM, falls back to grep) |
| **[farofino-mcp](https://github.com/italoag/farofino-mcp)** | Aderyn + pattern analysis | Optional (EVM fallback) |

## npm Packages (installed on demand via npx)

| Server | Purpose | API Key? |
|--------|---------|----------|
| **foundry-suite** | Anvil fork testing, Forge scripts, Heimdall bytecode | No |
| **evm-chain-data** | On-chain ABI/state queries via Etherscan | Optional (free) |
| **tavily-search** | Web search for fork ancestry + docs | Optional (free) |
| **helius** | Solana on-chain data | Optional (free) |
| **memory** | Persistent memory across sessions | No |

## API Keys

| Key | Where to Get | Cost | Used For |
|-----|-------------|------|----------|
| Solodit | [solodit.cyfrin.io](https://solodit.cyfrin.io) | Free | RAG indexing (3400+ findings from Solodit alone, 4k+ total across all sources) + live search |
| Etherscan | [etherscan.io/apis](https://etherscan.io/apis) | Free | Contract ABI verification |
| Tavily | [tavily.com](https://tavily.com) | Free tier | Fork ancestry, RAG fallback |
| Helius | [helius.dev](https://helius.dev) | Free tier | Solana on-chain data |
| RPC URL | Alchemy, Infura, or public | Free/Paid | Fork testing |

> **Recommended**: Get the free Solodit API key (4k+ findings with all sources vs ~1.5k without Solodit) and Tavily key (WebSearch fallback when RAG is slow).

## Configuration Example

**Claude Code**: See `mcp.json.example` for the full 9-server configuration. After `plamen install`, each server is registered via `claude mcp add-json -s user` (check with `claude mcp list`). To re-pin an npm-based server or apply an env var change, remove it first: `claude mcp remove <name> -s user`, then re-run `plamen install`.

**Codex**: MCP servers are loaded from `[mcp_servers.*]` TOML blocks in `~/.codex/config.toml`, generated at install time by `scripts/codex_adapter.py` from `mcp.json.example`. After `plamen install --codex` the config is at `~/.codex/config.toml`. Tool permissions are interactive on first use per server. See the two Codex caveats above (disabled `evm-chain-data`, schema-sanitized Python servers).

Key `mcp.json.example` entries (each becomes one `claude mcp add-json <name> '<this object>' -s user` call):

```json
{
  "mcpServers": {
    "unified-vuln-db": {
      "command": "python",
      "args": ["-m", "unified_vuln.server"],
      "cwd": "./custom-mcp/unified-vuln-db"
    },
    "foundry-suite": {
      "command": "npx",
      "args": ["-y", "@pranesh.asp/foundry-mcp-server"],
      "env": { "RPC_URL": "YOUR_RPC_URL" }
    }
  }
}
```

Path notes: `cwd` fields use relative paths resolved from `~/.plamen/`. On Codex, paths inside generated `config.toml` resolve via `~/.codex/plamen/`, which is a symlink to `~/.plamen/` created by `plamen install --codex` — same source tree, different runtime root.

---

**See also**: [getting-started.md](getting-started.md) · [dependencies.md](dependencies.md) · [codex-backend.md](codex-backend.md) · [architecture.md](architecture.md) · [docs index](README.md)
