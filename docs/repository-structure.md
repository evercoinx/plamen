# Repository Structure

```
~/.plamen/
├── CLAUDE.md                          # Orchestrator config — mode table, rules, file refs
├── plamen.py                          # Terminal wrapper (Rich + InquirerPy)
├── plamen.sh / plamen.bat             # Launcher scripts
├── VERSION                            # Semantic version (2.0.1)
│
├── commands/
│   ├── plamen.md                      # /plamen slash command — wizard + full SC workflow
│   └── plamen-l1.md                   # /plamen l1 slash command — L1 infrastructure workflow
│
├── rules/                             # Shared rules (all languages)
│   ├── finding-output-format.md       # Finding template, Rules Applied, Depth Evidence Tags
│   ├── orchestrator-rules.md          # Orchestration modes, critical rules
│   ├── phase3b-rescan-prompt.md       # Breadth re-scan (Thorough)
│   ├── phase4-confidence-scoring.md   # 4-axis scoring, anti-dilution, convergence
│   ├── phase4c-chain-prompt.md        # Chain analysis — enabler enum + chain matching
│   ├── phase5-poc-execution.md        # Mandatory PoC execution protocol
│   ├── phase6-report-prompts.md       # Report pipeline — Index → Writers → Assembler
│   ├── report-template.md             # Report format, severity matrix, consolidation
│   ├── skill-index.md                 # Master skill registry (all trees)
│   └── post-audit-improvement-protocol.md
│
├── agents/                            # Agent definitions (language-agnostic)
│   ├── depth-token-flow.md
│   ├── depth-state-trace.md
│   ├── depth-edge-case.md
│   ├── depth-external.md
│   ├── depth-consensus-invariant.md   # L1 mode: consensus safety/liveness
│   ├── depth-network-surface.md       # L1 mode: p2p/RPC/mempool attack surface
│   ├── security-analyzer.md
│   └── security-verifier.md
│
├── prompts/                           # Language-specific prompts
│   ├── evm/                           # 10 files (includes invariant-fuzz)
│   ├── solana/                        # 10 files (includes invariant-fuzz)
│   ├── aptos/                         # 9 files
│   ├── sui/                           # 9 files
│   ├── soroban/                       # 9 files (Soroban/Stellar)
│   ├── l1/                            # L1 infrastructure prompts
│   └── shared/                        # Shared prompt components
│       └── v2/                        # V2-specific shared prompts
│
├── agents/skills/
│   ├── evm/                           # 18 EVM skill templates
│   ├── solana/                        # 20 Solana skill templates
│   ├── aptos/                         # 22 Aptos skill templates (21 + core directives)
│   ├── sui/                           # 22 Sui skill templates (21 + core directives)
│   ├── soroban/                       # 19 Soroban skill templates
│   ├── injectable/                    # 8 protocol-type-specific skills
│   │   └── l1/                        # 22+ L1 infrastructure skills
│   └── niche/                         # 9 flag-triggered niche agents
│
├── scripts/                           # V2 driver and utilities
│   ├── plamen_driver.py               # Phase scheduling, checkpointing, retry
│   ├── plamen_types.py                # Canonical definitions (evidence tags, severities)
│   ├── plamen_parsers.py              # LLM output parsing
│   ├── plamen_validators.py           # Artifact quality gates
│   ├── plamen_prompt.py               # Phase prompt building
│   ├── plamen_mechanical.py           # Deterministic report assembly
│   ├── plamen_display.py              # Rich terminal UI for driver
│   ├── codex_adapter.py               # Codex CLI backend adapter
│   └── recon_prepass.py               # Pre-recon static analysis
│
├── codex-adapter/                     # Codex CLI backend config source
│   ├── AGENTS.md                      # Codex orchestrator config
│   ├── config.toml                    # Codex model/MCP settings (generated)
│   ├── commands/                      # Codex slash commands
│   └── skills/                        # Codex skill overrides
│
├── custom-mcp/                        # MCP servers
│   ├── unified-vuln-db/               # RAG database (code only, data/ gitignored)
│   ├── solana-fender/                 # Solana static analysis
│   ├── farofino-mcp/                  # [submodule] Aderyn integration
│   └── slither-mcp/                   # [submodule] Trail of Bits Slither
│
├── docs/                              # Documentation
│   └── l1-mode/                       # L1 mode design docs and severity matrix
├── mcp-packages/                      # Pinned npm MCP server packages
├── mcp.json.example                   # MCP server config template
├── settings.json.example              # Permissions config template
├── requirements.txt                   # Python deps (Rich, InquirerPy)
├── .gitmodules                        # Submodule refs
└── .gitignore
```
