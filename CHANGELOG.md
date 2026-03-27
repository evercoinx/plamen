# Changelog

All notable changes to Plamen will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.3] - 2026-03-27

### Added
- **EVM Compilation Weight Check (Step 3c)**: Recon TASK 1 now counts `.sol` files and checks `via-ir`/`auto_detect_solc` settings before `forge build`. Heavy projects (>500 files, via-ir + >200 files, or multi-version pragmas) get `threads = 2` in foundry.toml and solc version pinning. Prevents parallel solc instances from exhausting system RAM and crashing Claude Code.
- **Solana Compilation Weight Check (Step 1e)**: Recon TASK 1 now counts `.rs` files and workspace members before `anchor build`/`cargo build-sbf`. Heavy projects (>300 files, >3 workspace members) get `CARGO_BUILD_JOBS=2` prefix. Prevents parallel rustc instances from causing OOM.

### Why
Observed repeated crashes on large projects (e.g., Umia: 5,699 .sol files with `via-ir = true`). Foundry spawns 5-6 solc instances at 4-8GB each, exhausting RAM. Cargo does the same with rustc. Aptos/Sui Move compilers are single-threaded and lightweight — no mitigation needed.

## [1.1.2] - 2026-03-27

### Added
- **Scanner CHECK 5 extension**: Untrusted call target validation — when code decodes an address from calldata and calls interface functions on it, the return values are untrusted unless the address is verified against a registry or factory. Fills a gap between "untrusted parameters in calls to known contracts" (existing) and "calls to untrusted contracts whose return values are trusted" (new). RC-METHOD fix from dHEDGE post-mortem (2 High misses).
- **Niche agent Processing Protocol**: All 8 niche agents now enforce enumerate-first processing — ENUMERATE targets → PROCESS exhaustively → COVERAGE GATE. Based on CheckEval (EMNLP 2025) and Plan-and-Act (ICML 2025) research showing binary per-item decomposition and plan/execute separation improve checklist adherence. ~100 extra tokens per agent, zero additional API calls. Applies to Core and Thorough modes.
- **Niche agent Coverage Assertion**: Pre-return reminder in all 8 niche agents requiring explicit verification that every enumerated item was processed. Based on Lost-in-the-Middle research — repeating key instructions at prompt end provides recency attention boost.
- **Niche Agent Coverage Judge (Thorough only)**: Post-iteration-1 haiku agent that mechanically cross-references niche output files against function_list.md to detect skipped entities. If gaps found, spawns targeted sonnet gap-fillers for missed items only. Added to all 4 language trees (EVM, Solana, Aptos, Sui).

## [1.1.0] - 2026-03-27

### Added
- **EVM CHECK 2g**: Missing native ETH receiver detection — flags payable functions/contracts that lack a `receive()` or `fallback()` function
- **DIMENSIONAL_ANALYSIS injectable skill**: Unit/dimension mismatch analysis for protocols using mixed fixed-point arithmetic (MIXED_DECIMALS flag)
- **Move-Safety Agent architecture (Aptos/Sui)**: New `move-safety-core-directives` skill split from the 4 always-required skills (~950 lines total). Core directives (~130 lines) load into every breadth agent; a dedicated Move-Safety Agent gets full skills. Prevents attention saturation on dense methodology.
- **Phase 5 batched verifier spawning**: When >8 verifiers needed, splits into severity-tier batches (A: Chain+High opus, B/C: Medium sonnet, D: Low+Info single agent). Crash-resume support — skips already-verified hypotheses on restart. Short return messages (~50 tokens/agent) prevent orchestrator context bloat.
- **New niche skills**: `callback-receiver-safety` (EVM callback handler access control, state inflation), `multi-step-operation-safety` (authorization conflicts, on-behalf-of targeting)
- **New injectable skill**: `lending-protocol-security` for lending protocol audits
- **Depth template improvements**: ANCHORING REJECTION LIST (7-row table of insufficient REFUTED/CONTESTED justifications), File Coverage Map task in inventory prompt, MIXED_DECIMALS flag in recon

### Fixed
- **`nice -n 10` on Unix**: Indexer processes now run at reduced CPU priority on macOS/Linux — keeps machine responsive during RAG build (~10-20% throughput cost on idle machine; none on loaded machine)
- **Adaptive RAG timeouts**: Fanless Macs (MacBook Air) get extended timeouts (1800s Solodit, 900s embedding) and reduced Solodit page count (5 vs 10) to prevent thermal-throttle timeouts
- **Resource warning banner**: `plamen rag` now warns before indexing: "RAG indexing is CPU and RAM intensive. Your machine may feel sluggish — do not close this terminal."
- **Status box RAG hint**: "not built" now shows "run 'plamen rag' (~10 min, CPU intensive)" hint
- **`sys.executable` MCP injection**: `_merge_mcp_json` replaces `"python"`/`"python3"` with `sys.executable` at install time — eliminates "spawn python ENOENT" on macOS/Linux without manual sed
- **Malformed JSON handling**: `_merge_settings_json` and `_merge_mcp_json` now show friendly errors (not raw tracebacks) when existing config files have trailing commas or syntax errors
- **Removed dead package installs**: `solodit-scraper` and `defihacklabs-rag` removed from `_setup_python_deps` — `unified-vuln-db` handles all RAG indexing internally; `defihacklabs-rag` had `openai>=1.0.0` as unnecessary hard dep
- **`plamen rag` dep-guard**: `_build_rag_db` auto-installs missing RAG deps before indexing — `plamen rag` is now self-healing after a fresh clone or partial install
- **Sentence-transformers quick-check**: `_setup_python_deps` quick-check now uses `import sentence_transformers, chromadb` instead of `import torch` — avoids 2-3s torch cold-start on every `plamen setup`
- **Pip `--user` args fix**: Corrected `[3:]` → `[4:]` slice bug that produced `--user --user` args
- **Always MiniLM embeddings**: Removed Nomic/Voyage model selection — always uses `all-MiniLM-L6-v2` (384-dim, ~90MB). Eliminates RAM crashes on 16GB M1 Macs.
- **`_python_bin()` space-quoting**: Uses `sys.executable` with double-quote wrapping for paths containing spaces

## [1.0.13] - 2026-03-26

### Changed
- **RAG separated from `plamen setup`**: The `setup` command no longer installs PyTorch (~2GB), chromadb, sentence-transformers, or builds the RAG database. These are now installed and built exclusively via `plamen rag`. This prevents 1+ hour install times and crashes on memory-constrained machines (M1 Macs with 16GB RAM, fanless MacBook Airs). Setup now completes in ~30 seconds.
- **New `_install_rag_deps()` function**: `plamen rag` auto-installs RAG Python dependencies before building the index. Users no longer need to manually pip install anything — just run `plamen rag` when ready.
- **Fixed `_RAG_MIN_ENTRIES` undefined**: Added missing constant (500) that would crash `check_dependencies()` at runtime.

## [1.0.12] - 2026-03-25

### Added
- **RAG indexing resource warning**: `_build_rag_db()` now prints a caution banner before indexing starts — warns that the process is CPU/RAM intensive, the machine may feel sluggish, and the terminal should not be closed.
- **`nice -n 10` on Unix indexer commands**: On macOS/Linux, all indexer subprocesses run at reduced CPU priority (`nice -n 10`), yielding CPU to other applications. No effect on indexing quality, ~10-20% slower on idle machines. Silently skipped on Windows.
- **First-time RAG hint in status box**: When RAG DB is not yet built (`-1`), the status box now shows `run 'plamen rag' (5-20 min, CPU intensive)` instead of bare `not built`, guiding new users.

## [1.0.11] - 2026-03-25

### Fixed
- **RAG build wipes wrong ChromaDB path**: `_build_rag_db()` wiped `custom-mcp/unified-vuln-db/data/chroma_db` instead of the actual database location `unified-vuln-db/data/chroma_db` (per `database.py` `parents[3]` resolution). The nuke was a silent no-op, leaving stale DBs from crashed builds untouched and causing rebuilds to fail with partial data.
- **Per-source RAG timeouts**: Replace flat 600s timeout with per-source limits — Solodit 1200s (20 min) / 1800s on fanless Macs (30 min), indexing 600s / 900s. Solodit retries removed (hanging API call doesn't improve on retry). Immunefi retry uses `--skip-fetch` to reuse cached HTTP responses instead of re-fetching 139 URLs.
- **`--skip-fetch` CLI flag**: Expose existing `skip_fetch` parameter in `index_immunefi()` as a `--skip-fetch` CLI flag in the indexer, enabling cache-only retry after a timeout without re-fetching all Immunefi URLs.
- **Solodit page count on constrained machines**: Reduce `--max-pages` from 10 to 5 on fanless Macs / low-RAM machines (29 tags × 10 pages × 3.5s delay exceeds timeout on slow networks).

## [1.0.10] - 2026-03-24

### Fixed
- **RAG build hang on fanless Macs**: Stale ChromaDB with Nomic 768-dim HNSW index caused `get_or_create_collection()` to hang indefinitely when MiniLM 384-dim embeddings were used. Added `_wipe_if_dimension_mismatch()` to detect and clear dimension-mismatched databases before opening.

## [1.0.9] - 2026-03-23

### Added
- **Thermal constraint auto-detection**: `_is_fanless_mac()` detects MacBook Air and other fanless Macs via IORegistry. `_should_use_fast_rag()` switches to MiniLM (`all-MiniLM-L6-v2`, 384-dim, ~90MB) instead of Nomic Embed v1.5 (768-dim, ~500MB) on fanless Macs or machines with <16GB RAM, preventing thermal throttling during RAG indexing. Override with `VULN_DB_FAST_MODE=0/1`.

## [1.0.8] - 2026-03-22

### Added
- **Cross-batch verification consistency check (Phase 5.2)**: Haiku agent checks for contradictions between verification batches before final report assembly.

### Fixed
- **Slither/Hardhat dependency failure**: Resolved installation conflict between slither-analyzer and hardhat dev dependencies.

## [1.0.7] - 2026-03-21

### Fixed
- **Invariant generation bypass**: Agents could shortcut Phase 4b-invariant-fuzz template by summarizing properties inline rather than reading the full methodology file. Enforced agent-read requirement for fuzz templates (Rule 3 hardening).

## [1.0.6] - 2026-03-19

### Changed
- **Non-destructive install**: Plamen now clones to `~/.plamen` instead of `~/.claude`, preserving existing Claude Code configuration. The installer creates symlinks into `~/.claude/` and merges configs additively (settings.json, mcp.json, CLAUDE.md with markers). Closes #3.
- **macOS/Linux support**: All commands use `python3` (not `python`). PATH setup targets `~/.zshrc` on macOS and `~/.bashrc` on Linux.
- **Windows support**: Clone to `$HOME\.plamen` (PowerShell). Directory junctions (no admin needed) for dirs, Developer Mode required for file symlinks. Documented in all setup guides.

### Added
- **Bootstrap auto-install**: `plamen.py` detects missing `rich`/`InquirerPy` on first run and installs them automatically before importing. No more `ModuleNotFoundError` on fresh installs.
- **`plamen rag` command**: Rebuild the RAG database without running full setup. Setup wizard now always shows RAG rebuild option even when database has entries.
- **`plamen help` / `plamen --help`**: Shows all available commands and options.
- **`plamen uninstall` confirmation**: Interactive prompt before removing symlinks and config entries.
- **`plamen` extensionless launcher**: Unix shells find `plamen` on PATH (previously only `plamen.sh` existed, which required typing the extension).
- **Install manifest**: `.plamen-manifest.json` tracks all installed symlinks for clean uninstall with `.pre-plamen` backup restoration.

### Fixed
- **einops missing from requirements**: `nomic-embed-text-v1.5` silently fell back to `all-MiniLM-L6-v2` (384 dims vs 768). Added `einops>=0.7.0` to unified-vuln-db requirements.
- **unified-vuln-db not globally importable**: `pip install -e` was missing, so `python3 -m unified_vuln.indexer` only worked from inside the package directory. Now installed as editable package during setup.
- **Solodit API key ordering**: Setup docs now set `SOLODIT_API_KEY` before running the installer, preventing silent Solodit indexing failure on first install.
- **3x `os.path.abspath` → `PLAMEN_HOME`**: Setup helper scripts (_solana_installer.py, _avm_installer.py, _sui_installer.py) failed when run through symlinks.
- **Solana skill count**: skill-index.md said 19, actual count is 20 (stale from v1.0.3 Trident addition).
- **"Info" vs "Informational"**: finding-output-format.md now matches report-template.md label.
- **CLAUDE.md marker guard**: Missing `<!-- PLAMEN:END -->` no longer crashes install/uninstall.

## [1.0.5] - 2026-03-19

### Changed
- **Skill file architecture**: All 92 skill files restructured from `SKILL_NAME.md` to `skill-name/SKILL.md` named-folder format with YAML frontmatter (`name`, `description`). Enables Claude Code skill registry compliance and reference file splitting for large skills.
- **Verification protocol split**: 4 large verification-protocol files (700-1097 lines) split into `SKILL.md` + `references/` subdirectory (advanced.md, templates.md) for better context management.
- **Orchestrator path resolution**: `commands/plamen.md` updated to construct `skill-name/SKILL.md` paths for standard skills, injectable skills, and niche agents (lines 467, 474, 724).
- **Em-dash normalization**: All em dashes (--) replaced with regular dashes (-) across modified files for consistent formatting.

### Fixed
- **Blocker from PR #1**: `commands/plamen.md` skill path references were not updated in the original PR -- would have caused silent skill loading failures. Fixed before merge.

## [1.0.4] - 2026-03-19

### Fixed
- **Scope file estimation**: Parser now handles markdown tables (`| File.sol | 300 |`), bullet lists (`- contracts/File.sol`), and bare paths (`File.sol`) — previously only bare paths worked, causing "~0 lines, 0 files" for markdown-formatted scope files
- **Cost estimate consistency**: `/plamen` command now calls `plamen.py --estimate` instead of calculating inline — single source of truth, no more divergent numbers between wrapper and command
- **Double confirmation prompt**: Wrapper now passes `wrapper-launch` flag; `/plamen` skips Step 0d (cost estimate + confirmation) when launched from the wrapper since the user already confirmed

### Added
- `plamen.py --estimate` CLI flag: outputs JSON cost estimate for use by `/plamen` command

## [1.0.3] - 2026-03-19

### Added
- **Solana invariant fuzz campaign**: New `phase4b-invariant-fuzz.md` for Solana/Anchor — mirrors EVM v1.1.0 structure with protocol-derived invariants, finding-derived fuzz targets, lifecycle handlers, and 5 mandatory categories. Fills the EVM/Solana parity gap (was explicitly skipped in `phase4b-loop.md`)
- **Trident API reference**: New `TRIDENT_API_REFERENCE.md` (v0.12.0) — prevents method signature hallucination with correct CLI commands, types, and patterns
- **Lending/Liquidation injectable skill**: 247-line methodology covering health factor boundaries, interest accrual, liquidation mechanism safety, DoS vectors, bad debt socialization, collateral factor manipulation, asymmetric pause analysis
- **DEX/Slippage injectable skill**: 134-line methodology covering slippage parameters, deadline enforcement, return value handling, fee tier assumptions, router approval safety
- **Self-transfer accounting check**: Added to TOKEN_FLOW_TRACING in all 4 language trees — detects `sender == recipient` manipulating fees/rewards/snapshots
- **Timestamp unit confusion check**: Added to TEMPORAL_PARAMETER_STALENESS for Sui (`clock::timestamp_ms` vs seconds) and Aptos (`now_seconds` vs `now_microseconds`)
- **Denylist enforcement lag check**: Added to CROSS_CHAIN_TIMING for Sui and Aptos
- **Invariant quality self-check**: Tautological/sensitivity/testability filter before generating fuzz code
- **Scope selector**: Foundation/Integration/Temporal campaign scope based on protocol characteristics
- **Non-triviality guards**: Prevents false confidence from broken fuzz setups (0% success rate detection)
- **Platform dependencies guide**: New `docs/dependencies.md` with per-platform installation, troubleshooting, and Trident version compatibility matrix
- **Windows Developer Mode check**: `plamen.py` auto-detects and warns if Developer Mode is OFF (required for Solana symlinks)
- **OpenSSL auto-detection**: Fuzz templates inline-detect OpenSSL on Windows for Trident compilation
- **Cost estimation in `/plamen`**: Launch confirmation with codebase size, agent count, token estimate, API cost, and plan usage % with color-coded warnings

### Fixed
- **Trident v0.12 commands**: Replaced all `run-hfuzz`/`debug-hfuzz`/`HFUZZ_RUN_ARGS` references with v0.11+ commands (`trident fuzz run fuzz_0`). Trident v0.11+ uses TridentSVM — no honggfuzz/AFL required
- **Cross-platform Trident**: Documented and verified working on Windows (with Developer Mode + OpenSSL), macOS, and Linux
- **Recon probe**: No longer checks for `honggfuzz --version` — checks `trident --version` only

### Changed
- Solana skills: 19 → 20 (added TRIDENT_API_REFERENCE)
- Injectable skills: 5 → 7 (added LENDING_PROTOCOL_SECURITY, DEX_INTEGRATION_SECURITY)

## [1.0.2] - 2026-03-19

### Improved
- **EVM fuzzing**: Invariant fuzz and Medusa campaigns now derive invariants from `design_context.md` (protocol economics) and `findings_inventory.md` (bug targets), not just structural write-site analysis
- **No artificial caps**: Removed max 8/5 invariant limits and max 15 handler limit -- fuzz execution is zero token cost regardless of count
- **Lifecycle sequence handlers**: Mandatory multi-step handlers (create->repay->close) that construct realistic state random individual calls cannot reach
- **Realistic value bounds**: Handlers use protocol-actual decimals and parameter ranges from `constraint_variables.md`
- **Campaign config**: 256 runs x depth 25 (was 64x15), 5 mandatory invariant categories with coverage table in output
- **README restructured**: 865 lines -> 134 lines. Follows Ruff/Foundry landing page pattern
- **Documentation**: New `docs/` directory with 7 focused guides (setup, architecture, audit modes, MCP servers, usage, internals, repository structure)

## [1.0.1] - 2026-03-19

### Added
- **Rule 12**: THOROUGH MODE COMPLETENESS -- mandatory checklist of 13 non-negotiable Thorough steps with violation logging
- **Rule 13**: NO SPEED OPTIMIZATION IN THOROUGH MODE -- blocks weasel phrases that skip steps
- **Pre-Depth checkpoint**: Assertions for invariant fuzz and Medusa campaign completion
- **Post-Depth checkpoint**: Assertions for confidence scores, adaptive loop log, manifest, iteration 2 enforcement
- **Phase 4b.5 inline**: RAG Validation Sweep explicitly marked MANDATORY for Core/Thorough
- **Skeptic-Judge enforcement**: Positive statement that Thorough HIGH/CRIT must run skeptic

### Fixed
- Design Stress Testing now unconditional (1 reserved slot, not budget-conditional)
- AUDIT MODES table updated to match Rule 12 (DST: "1 reserved slot, UNCONDITIONAL")
- `violations.md` and `checkpoint_postdepth.md` registered as scratchpad artifacts
- Removed internal planning document (`RAG_OVERHAUL_STATUS.md`) from public repo

### Changed
- GitHub repo topics added: web3-security, smart-contract-audit, claude-code, solidity, solana, aptos, sui, ai-agent, security-audit, ethereum

## [1.0.0] - 2026-03-14

### Initial public release

Plamen is an autonomous Web3 security auditing agent for Claude Code. This is the first open-source release.

### Core Pipeline
- 8-phase audit pipeline: Recon → Instantiation → Breadth Analysis → Re-Scan → Inventory → Depth Loop → Chain Analysis → Verification → Report
- Two audit modes: **Core** (22-40 agents, HIGH/CRIT focus) and **Thorough** (32-90 agents, all severities)
- **Compare** mode for post-audit improvement against ground truth reports
- Adaptive depth loop with 4-axis confidence scoring and Devil's Advocate iteration
- Iterative chain analysis with enabler enumeration and postcondition-precondition matching
- Mandatory PoC execution with fuzz variants for Medium+ findings
- Tiered report generation (Opus for Critical+High, Sonnet for Medium, Sonnet for Low+Info)

### Language Support
- **EVM/Solidity** — 18 skills, Foundry/Hardhat build, Slither integration, fork testing
- **Solana/Anchor** — 19 skills, LiteSVM tests, Trident fuzzing, Helius on-chain data
- **Aptos Move** — 21 skills, Move test framework, resource/capability analysis
- **Sui Move** — 21 skills, test_scenario framework, object ownership analysis

### Skills System
- 79 language-specific skills across 4 trees
- 5 injectable skills (Vault Accounting, Account Abstraction, NFT Protocol, Governance, Outcome Determinism)
- 5 niche agents (Event Completeness, Semantic Gap Investigator, Spec Compliance, Signature Verification, Semantic Consistency)
- Flag-triggered loading to prevent context dilution

### Scanner Templates
- Blind Spot Scanner A: Tokens & Parameters (+ msg.value loops, returnbomb, gas griefing)
- Blind Spot Scanner B: Guards, Visibility & Inheritance + Override Safety
- Blind Spot Scanner C: Role Lifecycle, Capability Exposure & Reachability
- Validation Sweep Agent with write-completeness checks
- Design Stress Testing Agent (Thorough mode, budget redirect)

### Verification Protocol
- Pre-PoC feasibility gates (Reachability + Math Bounds)
- Evidence source tracking with mandatory audit tables
- Mock rejection rule (CONTESTED, not REFUTED, on mock evidence)
- RAG confidence override (historical precedent protection)
- Chain hypothesis protection with full-sequence PoC requirements
- Bidirectional role analysis for semi-trusted actor findings

### MCP Server Integration
- unified-vuln-db: RAG vulnerability database with Solodit API, DeFiHackLabs, Immunefi
- slither-mcp: Slither static analyzer (Trail of Bits)
- farofino-mcp: Solidity analysis fallback
- foundry-suite: Anvil fork testing, Forge scripts, Heimdall bytecode analysis
- evm-chain-data: On-chain contract ABI/state queries
- helius: Solana on-chain data
- tavily-search: Web search for fork ancestry and documentation

### Python Wrapper (plamen.py)
- Terminal UI with Rich + InquirerPy
- Mode selection, target detection, docs/scope/network configuration
- Auto-detection of project type (Foundry, Hardhat, Anchor, Move)
- Dependency checking, Ctrl+C handling, terminal width adaptation
- CLI fast path for scripted usage

### Security Rules
- 16 rules (R1-R16) covering adversarial assumptions, combinatorial impact, bidirectional roles, cached parameters, worst-state severity, unsolicited tokens, exhaustive enablers, anti-normalization, cross-variable invariants, flash loan preconditions, oracle integrity
- Finding output format with step execution tracking and depth evidence tags
- Severity matrix (Impact x Likelihood) with downgrade modifiers
