# Validation Research: 4 Proposed Optimizations

Research date: 2026-04-21. Default posture: REJECT on uncertainty.

---

## A: Prompt cache prefix restructuring

**Verdict**: ACCEPT WITH CAVEAT

**Evidence**:
- Anthropic's prompt-caching docs prescribe exactly this structure: *"Place cached content at the prompt's beginning for best performance... Place the breakpoint on the last block that stays identical across requests. For a prompt with a static prefix and a varying suffix, that is the end of the prefix, not the varying block."*
- Liu et al. 2023 "Lost in the Middle" (arXiv:2307.03172) finds a U-shaped attention curve: "performance is often highest when relevant information occurs at the beginning or end... and significantly degrades... in the middle." End-of-prompt is an attention peak. The 30%+ accuracy drop is for middle positioning, not end.
- Multiple guides (OpenAI, DigitalOcean, Anthropic) confirm: *"Prompt caching reduces redundant prefill computation without changing model quality."* KV cache reuse is mathematically identical to recomputation — no output-distribution change.

**Quality mechanism**: Cache hits are a prefill optimization; they do not alter sampling. The real question is positional bias, and Lost-in-the-Middle confirms end-of-prompt sits on an attention peak. Placing per-agent scope in the final 20% is structurally correct for BOTH caching and attention.

**Caveat**:
1. Do NOT place scope in the literal last tokens if an assistant-priming suffix follows — scope should be the last *instruction* content.
2. Keep per-agent scope ≥200 tokens so it registers as a distinct attention region.
3. Add an `## AGENT SCOPE` heading as a salient anchor.
4. A/B on 10 audits: recall on matched findings must not regress >2.

---

## B: Haiku 4.5 for instantiate + inventory

**Verdict**: ACCEPT for instantiate. REJECT for inventory (keep Sonnet).

**Evidence**:
- Haiku 4.5 scores 31 on Artificial Analysis Intelligence Index (above the 22 median for non-reasoning tier), 73.3% on SWE-bench Verified, and "matches Sonnet 4 on coding, computer use, and agent tasks." Rated "Excellent" for structured data extraction/formatting.
- HOWEVER, Haiku compaction/summarization failure modes are documented. Claude Code's memory architecture uses Haiku to *"identify overlapping entries to merge, outdated entries to drop, contradictions to resolve"* — a design that explicitly discards entities. For inventory, this is the wrong default: requirement is preservation, not lossy dedup. GitHub issues #23751, #7530, #8839 report Haiku-class compaction failures even at low context utilization.
- Inventory task profile: 8-20 files, ~100KB, requires perfect finding ID preservation, root-cause dedup WITHOUT loss, cross-file reference tracking. Dropped findings = silent recall loss (chain analysis reads inventory; missing findings become unrecoverable).
- Instantiate task profile: 5KB input, deterministic arithmetic, structured short output. Within Haiku's proven range; no preservation risk.

**Quality mechanism**: Instantiate is a template + arithmetic problem. Inventory is a lossless-merge problem where Haiku's summarization training actively works against you — optimized to compress, not preserve verbatim.

**Caveat**: On instantiate, orchestrator asserts `spawn_manifest.md contains {N} agents matching template_recommendations.md Required list`. Fall back to Sonnet on mismatch. Do not migrate inventory.

---

## C: Batch API for report phase

**Verdict**: ACCEPT

**Evidence**:
- Anthropic batch docs + launch announcement confirm: **same models, identical output quality** — only latency (24h SLA, most <1h) and price (50% off) differ.
- Prompt caching IS compatible with batch mode on a best-effort basis. Anthropic explicitly recommends: *"gather requests with a shared prefix, send one request first with a 1-hour cache block, then submit the rest."* Batch + cache stacks to ~95% discount.
- No documented reports of batch outputs diverging from real-time. No distilled model variant — same weights, same sampling, async scheduling only.
- Report phase matches the batch use case: no downstream deps during generation, latency-tolerant (final artifact), bulk structure (index + 3 tier writers + assembler are independent).

**Quality mechanism**: Mechanical equivalence. Async scheduling, not a different model.

**Caveat**: Use 1-hour cache duration (not default 5-min) since batch completion times exceed 5-min TTL. Prime one request to warm cache, then submit rest.

---

## D: SCIP-sliced depth context

**Verdict**: ACCEPT WITH CAVEAT

**Evidence**:
- LLMxCPG (arXiv:2507.16585, USENIX Security '25 — the actual paper; 2405.17238 was misquoted) demonstrates **67.84%–90.93% code-size reduction** via CPG-based slicing while **improving F1 by 15%–40%** and accuracy by 9%–27% versus full-file baselines. Thesis: slicing is *quality-positive* when built from dataflow/call-graph traversal — which is what SCIP `find_references` produces.
- HITS (arXiv:2408.11324) and LLM4FPM (arXiv:2411.03079) independently corroborate that slice-guided LLM analysis improves coverage, not degrades it, vs full-file prompts that dilute attention with unrelated code.
- Critical miss-rate data from LLMxCPG: **32% of queries correctly identified the CWE but missed contextual elements needed for complete match.** Accuracy collapses to 0.33 for nesting depth >7 or complex inter-function relationships. CPGs inherently underperform on **race conditions and business logic errors** — two classes that matter heavily for L1 consensus/concurrency bugs.
- The fallback-to-Read design is load-bearing. Without fallback, the 32% contextual-miss rate and nesting collapse disqualify slicing for L1. With Read available and instructed, the slice becomes a first-pass context saver, not a replacement.

**Quality mechanism**: Call-graph slicing reduces tokens AND sharpens attention on dataflow-relevant code (dual benefit per LLMxCPG). But CPGs miss runtime/temporal semantics — a non-trivial fraction of L1-class bugs (consensus invariants spanning many files, slashing races, mempool timing, p2p state machines) live precisely in this gap.

**Caveat**:
1. **Mandate fallback heuristics in the depth-agent prompt**: *"If the slice omits actual callsites, OR the finding class is concurrency/race/consensus/timing, OR cross-file refs exceed 5 hops, perform a follow-up Read on the full file."*
2. **Retain flat-file pre-bakes** (`repo_map.md`, `call_graph_*.md`, `concurrency_inventory.md`, `panic_sites.md`). These provide breadth-scale context a symbol slice cannot reproduce.
3. **Do NOT apply to `depth-consensus-invariant`.** That agent hunts the exact class CPGs are blind to — keep it on full-file + flat pre-bakes.
4. Apply SCIP slicing to `depth-network-surface`, `depth-state-trace`, `depth-external` where call-graph topology matches the bug class.
5. Post-audit metric: if >20% of Medium+ findings required Read fallback, the slicer is underspecified — tune before expanding.

---

## Final recommendation

| Opt | Decision | Savings | Coverage impact | Risk |
|-----|----------|---------|-----------------|------|
| A — Cache prefix restructure | **APPLY** | 25-35% on cacheable agent spawns | Neutral-to-positive (end-of-prompt attention peak) | Low — A/B validate |
| B — Haiku for instantiate | **APPLY** (instantiate) / **HOLD** (inventory) | 2-4% (small but high-call-count) | Neutral; Sonnet protects inventory recall | Low / High if inventory migrated |
| C — Batch API for report | **APPLY** | ~10-15% (50% off on ~6 report calls) | Zero (identical model) | Zero |
| D — SCIP slicing, 3 of 4 L1 depth agents | **APPLY PARTIAL** | 30-50% on applicable agents (L1 only) | Positive IF fallback mandated; skip consensus | Medium — requires prompt updates + monitor |

**Stacked savings**: A + B(instantiate) + C + D(partial) ≈ **35-45% total Thorough reduction**, within the 40-50% target.

**Coverage expectation**: Net neutral-to-positive if caveats are honored. Hold list: inventory→Haiku, SCIP for consensus agent, any scheme moving scope to prompt middle.

---

## Sources

- [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Lost in the Middle (arXiv:2307.03172)](https://arxiv.org/abs/2307.03172)
- [LLMxCPG (arXiv:2507.16585)](https://arxiv.org/abs/2507.16585)
- [Message Batches API announcement](https://www.anthropic.com/news/message-batches-api)
- [Batch processing docs](https://platform.claude.com/docs/en/build-with-claude/batch-processing)
- [Claude Haiku 4.5](https://www.anthropic.com/claude/haiku)
- [Artificial Analysis: Haiku 4.5](https://artificialanalysis.ai/models/claude-4-5-haiku)
- [HITS (arXiv:2408.11324)](https://arxiv.org/html/2408.11324v1)
- [LLM4FPM (arXiv:2411.03079)](https://arxiv.org/html/2411.03079v2)
- [ai.moda: Batches + Caching](https://www.ai.moda/en/blog/anthropics-batches-with-caching)
