# Audit Modes

## Comparison

| Dimension | Light | Core | Thorough |
|-----------|-------|------|----------|
| **Target plan** | **Pro** | Max | Max |
| Orchestrator model | Session model (Sonnet default) | Opus | Opus |
| Agent models | All Sonnet/Haiku | Opus + Sonnet | Opus + Sonnet |
| Recon | 2 sonnet (no RAG, no fork) | 4 agents (RAG fire-and-forget) | 4 agents (full RAG) |
| Breadth | 3-4 sonnet | 5-9 opus | 5-9 opus |
| Re-scan (3b/3c) | Skip | Skip | Full (sonnet, 2 iters + per-contract) |
| Depth loop | 4 merged sonnet, iter 1 | 8+ agents, iter 1 | Iter 1-3 (Devil's Advocate) |
| Niche agents | Skip | Flag-triggered (up to 8) | Flag-triggered (up to 8) |
| Semantic invariants | Skip | Pass 1 only | Pass 1 + Pass 2 (recursive trace) |
| Confidence scoring | None (verdicts only) | 2-axis (Evidence + Quality) | 4-axis (Evidence, Consensus, Quality, RAG) |
| RAG Sweep | Skip | 1 sonnet | 1 sonnet |
| Invariant / Medusa fuzz | Skip | Skip | Yes (EVM, zero budget cost) |
| Design stress testing | Skip | Skip | 1 reserved slot, UNCONDITIONAL |
| Chain analysis | 1 sonnet (merged) | 2 agents | 2 agents + iteration 2 |
| Verification (PoC) | Chains + ALL Medium+ (sonnet) | Chains + ALL Medium+ | ALL severities (with fuzz) |
| Skeptic-Judge | Skip | Skip | HIGH/CRIT |
| Cross-batch consistency | Skip | 1 sonnet | 1 sonnet |
| Report | 2 agents (sonnet + haiku) | 5 agents (opus + sonnet + haiku) | 5 agents |
| Agent count | **~18-22** | **~30-50** | **~40-100** |

> The "agent" counts above are role counts. Agents in the **breadth**, **rescan**, and **depth** rows now execute as a driver-owned PTY worker pool (one Claude PTY per agent artifact) rather than as `Task()` subagents under a Claude coordinator — see [pipeline-phases-presentation.md](pipeline-phases-presentation.md) for execution-shape details.

## When to Use Each

- **Light**: Pro plan, codebases under 3000 lines, quick first pass. Reports all severities but skips semantic invariants, fuzzing, and design stress testing.
- **Core**: Standard audit. Reports all severities, PoC-verifies Medium+, flag-triggered niche agents. Best balance of coverage and cost.
- **Thorough**: Maximum coverage. Iterative depth with Devil's Advocate, fuzz campaigns (invariant + Medusa), design stress testing, skeptic-judge for HIGH/CRIT, 4-axis confidence scoring. Use for high-value or pre-deployment audits.

## Proven-Only Mode

Available in all modes via `--proven-only`. Caps findings with only `[CODE-TRACE]` evidence (no executed PoC or fuzzer counterexample) at Low severity. Useful for benchmark comparisons where only mechanically proven findings should drive severity.

---

## L1 Mode

L1 infrastructure audits use the same Light/Core/Thorough tiers with these differences:

| Dimension | L1 Difference |
|-----------|---------------|
| Languages | Go, Rust (instead of Solidity, Move, etc.) |
| Depth agents | + depth-consensus-invariant, depth-network-surface; no depth-token-flow |
| Phase 0.5 | Bake phase: scip-go / rust-analyzer SCIP batch indexing |
| Phase 4c | Removed (no chain analysis for L1 point vulnerabilities) |
| Severity matrix | L1-specific, aligned with Immunefi v2.3 |
| Skills | 22+ L1 injectable skills (consensus, p2p, mempool, RPC, validator, etc.) |
| Evidence tags | + [DIFF-PASS], [CONFORMANCE-PASS], [NON-DET-PASS], [FUZZ-PASS], [LSP-TRACE] |

```bash
plamen l1 core /path/to/node-client    # terminal wrapper (both backends)
```

Inside Claude Code, use `/plamen-l1-wizard` for interactive L1 audit configuration.

See [l1-mode/design.md](l1-mode/design.md) for the full L1 architecture.

---

## Codex Backend

All audit modes work with both Claude Code and OpenAI Codex CLI backends. The V2 driver (`plamen_driver.py`) auto-detects the active backend via `plamen_home()` and handles tool translation and path rewriting (`~/.claude/` vs `~/.codex/plamen/`). Install the Codex backend with `plamen install --codex`. See the main [README](../README.md) for full Codex setup.
