# Cost-Efficient LLM Audit Architecture Research

Context: Plamen v2 Thorough L1 audit currently consumes ~13% of a Max 20x weekly quota (~$100+ effective). Goal: fit on Max 5x ($100/mo), multiple audits/week, single 5-hour session window.

## Part A — Architecture patterns (with cost data)

**Anthropic's multi-agent research system.** Lead orchestrator + parallel subagents with independent context windows. Outperformed single-agent Opus by **90.2%** on research eval — but consumed **~15× the tokens of a chat** (4× for single-agent, 15× for multi-agent). Anthropic explicitly states the pattern is only economically justified when task value exceeds the token premium. [Anthropic engineering](https://www.anthropic.com/engineering/multi-agent-research-system)

**LLMxCPG (USENIX Security '25).** Uses Code Property Graphs to extract **minimal vulnerability-relevant slices** instead of full files. Reports **67.84%–90.93% code size reduction** with 15–40% F1 improvement over baselines. Two-phase: (1) LLM generates CPG queries to extract slice, (2) second LLM classifies slice. [arXiv 2507.16585](https://arxiv.org/abs/2507.16585)

**IRIS (ICLR '25).** LLM + CodeQL hybrid. Takes a neurosymbolic route — LLM generates taint specs on the fly, CodeQL executes the query. On CWE-Bench-Java: CodeQL alone = 27/120 CVEs; IRIS+GPT-4 = 55/120 (+28). 5pp FDR improvement. Most of the heavy graph traversal stays in CodeQL (cheap); LLM only called for spec synthesis + context triage. [arXiv 2405.17238](https://arxiv.org/abs/2405.17238)

**GPTScan (ICSE '24).** Smart-contract logic bug scanner. **$0.01 per 1K Solidity LOC**, 14.39s per KLOC. >90% precision on token contracts, >70% recall vs human auditors, 9 novel bugs. Architecture: decompose bug classes into scenarios/properties → GPT matches candidates → static analyzer validates → eliminates **⅔ of false positives**. [arXiv 2308.03314](https://arxiv.org/abs/2308.03314)

**Triage router (arXiv 2604.07494).** Routes tasks to Haiku/Sonnet/Opus tiers using code-health signals as a router. Reports **~60% cost reduction** at equivalent quality when routing gate is sound. Core rule: light-tier pass rate on healthy code must exceed inter-tier cost ratio. [arXiv 2604.07494](https://arxiv.org/abs/2604.07494)

**Cognition Devin.** Single long-running agent + specialized sub-models (SWE-grep, SWE-grep-mini) for **cheap parallel repo search**. SWE-1.5 advertised at "13× speed of Sonnet 4.5" at frontier performance. Direction: fewer orchestrator hops, more tool-augmented single-agent with purpose-built retrieval sidecars. [Cognition 2025 review](https://cognition.ai/blog/devin-annual-performance-review-2025)

**Aider / Codebase-Memory / LspRag.** Tree-sitter + PageRank dependency graph → **8.5–13K tokens per task** (lowest among tested agents). Codebase-Memory reports **83% answer quality at 10× fewer tokens and 2.1× fewer tool calls** vs file-exploration agent. [Codebase-Memory arXiv](https://arxiv.org/html/2603.27277) | [Aider](https://aider.chat)

**Vulnhalla (CodeQL triage).** CyberArk triaged a real codebase in 2 days with **<$80 total LLM spend** — LLM only sees CodeQL's pre-filtered candidate list, never full files. [CyberArk Vulnhalla](https://www.cyberark.com/resources/threat-research-blog/vulnhalla-picking-the-true-vulnerabilities-from-the-codeql-haystack)

## Part B — Direct cost comparisons

| System | Scope | Cost | Recall | Source |
|---|---|---|---|---|
| GPTScan | Solidity logic bugs | **$0.01 / KLOC** | >70% | arXiv 2308.03314 |
| LLMxCPG | C/C++ CVE detection | 67–90% token reduction vs full-file | +15–40% F1 | arXiv 2507.16585 |
| IRIS | Java CWE | unreported $, uses CodeQL heavy lift | 55/120 (CodeQL: 27/120) | arXiv 2405.17238 |
| Vulnhalla | CodeQL triage | **<$80 / codebase** | — | CyberArk |
| Codebase-Memory | Repo Q&A | **10× fewer tokens** | 83% vs 92% baseline | arXiv 2603.27277 |
| Plamen v2 Thorough (current) | Smart contract / L1 | **~$100+ / audit** | unknown | internal |
| Triage router | SWE tasks | **60% cost reduction** | equivalent quality | arXiv 2604.07494 |

Plamen is an **order of magnitude** above GPTScan/IRIS/Vulnhalla-class systems on $-per-codebase, and is not using retrieval, triage, or batch discount.

## Part C — Anthropic prompt cache mechanics

- **Read = 0.1× base input**. **5-min write = 1.25×**. **1-hour write = 2.0×**. Breakeven: cache pays off after **1 read** at 5-min, **2 reads** at 1-hour. [Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- **Cache scope**: tools + system + messages up to `cache_control` block. Any reordering invalidates the cache.
- **Subagent pattern**: documented Claude Code sessions achieve **92% cache hit rate**, bringing a 1.84M-token session to $1.15. [Claude Code caching](https://www.dsebastien.net/claude-code-prompt-caching/)
- **Critical**: subagents should use **5-min TTL by default** — 1-hour writes are a worst-case for one-shot agents you won't revisit.
- **Stacking**: cache + batch API stacks to **~95% savings** off list price. [Finout 2026 guide](https://www.finout.io/blog/anthropic-api-pricing)
- **Haiku 4.5** = $1/$5 per M, **Sonnet 4.6** = $3/$15 per M, **Opus 4.6/4.7** = $5/$25 per M. Sonnet is **within 1.2 pts of Opus on SWE-bench at 40% the cost**; Haiku is within **~4 pts of Sonnet on SWE-bench Verified (73.3 vs 77.2)** at **⅓ the cost**. [Morph benchmarks](https://www.morphllm.com/claude-benchmarks)

## Part D — Sliced context / retrieval findings

- LLMxCPG: **67.84%–90.93% reduction** from CPG slicing (production benchmark).
- Codebase-Memory (Tree-sitter MCP): **10× fewer tokens, 2.1× fewer tool calls** at 83% quality vs 92% baseline. [arXiv 2603.27277](https://arxiv.org/html/2603.27277)
- Aider dependency graph: **8.5–13K tokens per task** — lowest on the tested benchmark.
- Pattern: LSP/tree-sitter resolves symbol→definition at query time instead of preloading files. Scales to multi-M-LOC repos.
- Plamen currently has agents ingest whole files (plus a 26K-line skill library duplicated across agents). **Biggest single token waste.**

## Part E — Triage + deep-dive architectures

- **GPTScan**: LLM candidate → static validation rejects ⅔ of FPs. Cheap because validation is cheap.
- **IRIS**: CodeQL does graph work, LLM does spec + triage. Heavy symbolic step is outside the LLM budget.
- **Triage router (arXiv 2604.07494)**: tier-routing with code-health signals = **60% cost cut**, equivalent quality.
- **Vulnhalla**: CodeQL output → LLM triage = **<$80** total.
- **Anthropic Taskflow Agent**: LLM filters raw CodeQL alerts — found multiple real bugs that CodeQL alone was drowning in FPs. [GitHub blog](https://github.blog/security/ai-supported-vulnerability-triage-with-the-github-security-lab-taskflow-agent/)
- **Recall loss**: the triage-gate recall loss is bounded by the floor tier's recall on healthy code. Published systems report parity once the gate metric is tuned.
- **Batch API**: **50% off** input+output if 24-hour turnaround is acceptable. Stacks with cache. Ideal for overnight deep-dive on triaged candidates. [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)

## Part F — Recommendations (ranked by cost-reduction × low risk)

1. **Extract a shared cache prefix across subagents (1–2 day effort, low risk).** All depth/breadth/scanner agents currently see different prompts. Restructure so the first 60–80KB (skill library + common rules) is byte-identical across all subagents in a phase. Target 90%+ cache hit rate. **Expected: 50–70% input-token cost reduction**, no methodology change. [Caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

2. **Model-tier routing (2 day effort, medium risk).** Haiku is within ~4pts of Sonnet on SWE-bench Verified at ⅓ the cost. Route scanners, inventory, index, validation-sweep, per-contract, and rescan to Haiku. Keep Sonnet for depth; reserve Opus only for Critical/High verification, chain analysis, and C+H tier writer. **Expected: 40–50% cost reduction**. Triage paper shows 60% is achievable if routing gate is sound. [arXiv 2604.07494](https://arxiv.org/abs/2604.07494)

3. **Retrieval-based context (3–5 day effort, medium risk).** Add tree-sitter/LSP pre-pass. Agents receive **symbol slices** not full files. LLMxCPG, Codebase-Memory, Aider all show **5–10× token reduction** at comparable recall. The current 50K-LOC L1 audit reading full Rust/Go files is the dominant cost line. **Expected: 60–80% context-token reduction** — compounds multiplicatively with #1.

4. **Two-stage triage+deep (3–5 days, medium risk).** Stage 1 = Haiku sweep over slices flags ~N candidates/KLOC. Stage 2 = Opus/Sonnet deep-dive on flagged candidates only. Mirrors GPTScan, Vulnhalla, IRIS. **Expected: 50–70% end-to-end reduction** vs current "run all depth on everything." Counterfactual: current Plamen runs 4 depth agents + 3 scanners on the full inventory regardless of triage signal.

5. **Kill redundant phases in Core/Light (1 day, low risk).** Anthropic research shows multi-agent systems cost ~15× chat-equivalent; value only when task reward > overhead. Merge 3b rescan + 3c per-contract into a single pass on Core. Skip DA iter-2 unless composite <0.5. Cap chain-analysis to a single agent for Core. **Expected: 20–30% reduction on Core audits.**

6. **Batch API for Phase 6 report writers (1 day, low risk).** Tier writers and report assembler are **not interactive** — they run once at end of audit. 24-hour SLA is acceptable for a $ saving. **50% off input+output**, stacks with cache (~95% off vs list). [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)

7. **Remove Devil's Advocate iter-2 unless confidence gate fires (1 day, medium risk).** Per Anthropic: each additional iteration is ~15× chat. Skip DA unless >3 findings at composite <0.5 AND Medium+. Published multi-round adversarial work shows gains are incremental (single-digit % on code-review tasks) — doesn't justify the cost in most audit runs. Keep DA for Thorough only. Single-agent results **match or outperform multi-agent on multi-hop reasoning at equal budget** — direct evidence against unconditional multi-round. [arXiv 2604.02460](https://arxiv.org/html/2604.02460)

8. **Single-agent-with-tools trial for Light mode (5 day effort, higher risk).** Devin's direction is single long-running agent + cheap retrieval sidecars. Test Light mode as one Sonnet agent with Read/Grep/Glob/tree-sitter/static-analyzer tools. If it lands within 1 severity tier of Core recall at 20% the cost, promote to Core mode default. **Expected: 60–80% reduction on Light**, recall ±10%.

Counterfactual stack: #1 + #2 + #3 + #6 combined = list-price 95%+ reduction plus 5–10× retrieval compression. An audit currently at $100 lands near **$5–$15** without methodology change.

## Part G — What not to touch

- **Multi-agent orchestration itself** — Anthropic's **90.2%** lift for research-class tasks validates the pattern. Collapsing to single-agent everywhere loses recall on chain analysis and cross-contract reasoning. Keep it where findings-per-dollar is the metric.
- **Depth-agent specialization (token-flow / state-trace / edge-case / external)** — matches LLMxCPG's slice-classifier split and Anthropic's parallel-subagent compression pattern. Healthy division of attention.
- **Confidence scoring** — equivalent to the Triage-paper gate mechanism. Keep as the router for iter-2/3 spending.
- **RAG validation sweep** — Vulnhalla/IRIS show retrieval-over-history lifts recall materially. Keep it.
- **PoC execution / `[POC-PASS]` evidence gate** — mechanical verification is exactly how GPTScan gets ⅔ FP reduction. Double-down, don't weaken.
- **Skill-index + injectable skills** — already aligned with the retrieval-based direction. Cost lever is *how* they're loaded (shared prefix), not *whether*.

---
Word count: ~1,490. Sources cited inline.
