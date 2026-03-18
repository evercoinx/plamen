# Repository Structure

```
~/.claude/
+-- CLAUDE.md                          # Orchestrator config -- mode table, rules, file refs
+-- plamen.py                          # Terminal wrapper (Rich + InquirerPy)
+-- plamen.sh / plamen.bat             # Launcher scripts
+-- VERSION                            # Semantic version
|
+-- commands/
|   +-- plamen.md                      # /plamen slash command -- wizard + full workflow
|
+-- rules/                             # Shared rules (all languages)
|   +-- finding-output-format.md       # Finding template, Rules Applied, Depth Evidence Tags
|   +-- phase3b-rescan-prompt.md       # Breadth re-scan (Thorough)
|   +-- phase4-confidence-scoring.md   # 4-axis scoring, anti-dilution, convergence
|   +-- phase4c-chain-prompt.md        # Chain analysis -- enabler enum + chain matching
|   +-- phase5-poc-execution.md        # Mandatory PoC execution protocol
|   +-- phase6-report-prompts.md       # Report pipeline -- Index -> Writers -> Assembler
|   +-- report-template.md             # Report format, severity matrix, consolidation
|   +-- skill-index.md                 # Master skill registry (all trees)
|   +-- post-audit-improvement-protocol.md
|
+-- agents/                            # Agent definitions (language-agnostic)
|   +-- depth-token-flow.md
|   +-- depth-state-trace.md
|   +-- depth-edge-case.md
|   +-- depth-external.md
|   +-- security-analyzer.md
|   +-- security-verifier.md
|
+-- prompts/                           # Language-specific prompts
|   +-- evm/                           # 10 files (includes invariant-fuzz)
|   +-- solana/                        # 9 files
|   +-- aptos/                         # 9 files
|   +-- sui/                           # 9 files
|
+-- agents/skills/
|   +-- evm/                           # 18 EVM skill templates
|   +-- solana/                        # 20 Solana skill templates
|   +-- aptos/                         # 21 Aptos skill templates
|   +-- sui/                           # 21 Sui skill templates
|   +-- injectable/                    # 5 protocol-type-specific skills
|   +-- niche/                         # 5 flag-triggered niche agents
|
+-- custom-mcp/                        # MCP servers
|   +-- unified-vuln-db/               # RAG database (code only, data/ gitignored)
|   +-- solodit-scraper/               # Solodit API scraper
|   +-- defihacklabs-rag/              # DeFiHackLabs embeddings
|   +-- solana-fender/                 # Solana static analysis
|   +-- farofino-mcp/                  # [submodule] Aderyn integration
|   +-- slither-mcp/                   # [submodule] Trail of Bits Slither
|
+-- docs/                              # Documentation
+-- mcp.json.example                   # MCP server config template
+-- settings.json.example              # Permissions config template
+-- requirements.txt                   # Python deps (Rich, InquirerPy)
+-- .gitmodules                        # Submodule refs
+-- .gitignore
```
