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
| Cross-batch consistency | Skip | 1 haiku | 1 haiku |
| Report | 2 agents (sonnet + haiku) | 5 agents (opus + sonnet + haiku) | 5 agents |
| Agent count | **~18-22** | **~30-50** | **~40-100** |

## When to Use Each

- **Light**: Pro plan, codebases under 3000 lines, quick first pass. Reports all severities but skips semantic invariants, fuzzing, and design stress testing.
- **Core**: Standard audit. Reports all severities, PoC-verifies Medium+, flag-triggered niche agents. Best balance of coverage and cost.
- **Thorough**: Maximum coverage. Iterative depth with Devil's Advocate, fuzz campaigns (invariant + Medusa), design stress testing, skeptic-judge for HIGH/CRIT, 4-axis confidence scoring. Use for high-value or pre-deployment audits.

## Proven-Only Mode

Available in all modes via `--proven-only`. Caps findings with only `[CODE-TRACE]` evidence (no executed PoC or fuzzer counterexample) at Low severity. Useful for benchmark comparisons where only mechanically proven findings should drive severity.
