# MCP Servers

Plamen uses 9 MCP servers configured in `mcp.json`. All keys are optional -- the pipeline degrades gracefully.

## Bundled (custom-mcp/)

| Server | Purpose | Required? |
|--------|---------|-----------|
| **unified-vuln-db** | RAG vulnerability database (Solodit, DeFiHackLabs, Immunefi) | **Required** |
| **solodit-scraper** | Solodit API scraper with SQLite cache | Required by unified-vuln-db |
| **defihacklabs-rag** | DeFiHackLabs exploit analysis with ChromaDB | Optional |
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
| Solodit | [solodit.cyfrin.io](https://solodit.cyfrin.io) | Free | RAG indexing (3400+ findings) + live search |
| Etherscan | [etherscan.io/apis](https://etherscan.io/apis) | Free | Contract ABI verification |
| Tavily | [tavily.com](https://tavily.com) | Free tier | Fork ancestry, RAG fallback |
| Helius | [helius.dev](https://helius.dev) | Free tier | Solana on-chain data |
| RPC URL | Alchemy, Infura, or public | Free/Paid | Fork testing |

> **Recommended**: Get the free Solodit API key (3400+ findings vs ~700 without) and Tavily key (WebSearch fallback when RAG is slow).

## Configuration Example

See `mcp.json.example` for the full 9-server configuration. Key entries:

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

Path notes: `cwd` fields use relative paths resolved from `~/.claude/`.
