# Audit Modes

## Comparison

| Dimension | Light | Core | Thorough |
|-----------|-------|------|----------|
| **Target plan** | **Pro** | Max | Max |
| Agent models | All Sonnet/Haiku | Opus + Sonnet | Opus + Sonnet |
| Recon | 2 sonnet (no RAG) | 4 agents | 4 agents (full RAG) |
| Breadth | 2-3 sonnet | 2-7 opus | 2-7 opus |
| Re-scan (3b/3c) | Skip | Skip | Full (2 iter + per-contract) |
| Depth loop | 4 merged sonnet, iter 1 | 8+ agents, iter 1 | Iter 1-3 (Devil's Advocate) |
| Niche agents | Skip | Flag-triggered | Flag-triggered |
| Semantic invariants | Skip | Pass 1 | Pass 1 + Pass 2 |
| Confidence scoring | None (verdicts only) | 2-axis | 4-axis |
| RAG Sweep | Skip | 1 agent | 1 agent |
| Invariant / Medusa fuzz | Skip | Skip | Yes (EVM) |
| Chain analysis | 1 sonnet (merged) | 2 agents | 2 agents + iteration 2 |
| Verification (PoC) | Medium+ (sonnet) | Medium+ | ALL severities + fuzz |
| Skeptic-Judge | Skip | Skip | HIGH/CRIT |
| Report | 2 agents | 5 agents | 5 agents |
| Agent count | **~15-18** | ~25-45 | ~35-95 |

## When to Use Each

- **Light**: Pro plan, codebases under 3000 lines, quick first pass. Reports all severities but skips semantic invariants and fuzzing.
- **Core**: Standard audit. Reports all severities, PoC-verifies Medium+. Best balance of coverage and cost.
- **Thorough**: Maximum coverage. Iterative depth with Devil's Advocate, fuzz campaigns, skeptic-judge for HIGH/CRIT. Use for high-value or pre-deployment audits.

## Proven-Only Mode

Available in all modes via `--proven-only`. Caps findings with only `[CODE-TRACE]` evidence (no executed PoC or fuzzer counterexample) at Low severity. Useful for benchmark comparisons where only mechanically proven findings should drive severity.
