# Plamen V2 — Methodology Review, Opus 4.7 Compliance, and Optimization Plan

> **Status**: Strategic planning document. No code changes mandated by this file
> until the user greenlights a specific tier.
> **Date**: 2026-04-21
> **Scope**: Methodology critique, Opus 4.7 compliance audit, rate-limit + cost
> optimization plan.
> **Prerequisite for implementation**: V2 must execute end-to-end on a real
> SC Thorough and a real L1 Core run before restructuring methodology.

---

## Part 1 — Methodology Review

### Overall grade: B+

**Thesis**: Plamen's methodology is above commercial web3 audit tools and close
to state-of-the-art LLM agent systems in structure, but it **over-constrains
Opus with procedural rules** and **under-invests in unconstrained exploration**.
The pipeline shape is sound; the content has drift and bloat. If V2 execution
ships reliably, the next frontier is not coverage — it is **trimming methodology
so Opus can actually think**.

### Landscape positioning

| Tier | Examples | Plamen relative |
|---|---|---|
| Deterministic scanners | Slither, Aderyn, Mythril | Plamen integrates via MCP; superior on semantic bugs |
| Commercial LLM-assisted | CertiK AI, Zircuit Guardian, Jackal | Plamen more rigorous, open methodology |
| Academic frameworks | LLMxCPG, IRIS, DeepInspect | Peer in architecture (RAG + sliced context); Plamen broader |
| Human audit firms | Trail of Bits, Spearbit, OpenZeppelin | Plamen targets 30-70% recall vs ~100% human; not replacing |
| General LLM agent systems | AutoGen, MetaGPT, LangGraph | More domain-specialized; less autonomous |

### Strengths (preserve)

1. **Phase 4c Chain Analysis** — postcondition→precondition matching catches
   compound DeFi exploits most tools miss. No other public web3 auditing system
   formalizes this.
2. **4-axis Confidence scoring** — forces structured epistemic honesty and
   blocks the common LLM "confidence: high" failure without evidence.
3. **Verification protocol's "mechanical proof required" rule** —
   `[POC-PASS]` as the only CONFIRMED basis aligns with auditing reality.
4. **Skill modular system** (~72+ skills with conditional triggers) — good
   LLM-alignment, rarely seen in web3 tools.
5. **RAG with multi-source corpora** (Solodit + DeFiHackLabs + Immunefi +
   Solana-Fender) — peer with IRIS-class systems.
6. **Devil's Advocate depth iter 2+** with analysis-path-summary contrastive
   conditioning — research-backed, correctly implemented.
7. **Post-audit-improvement protocol with RC-AGENT Exclusion Test** — prevents
   methodology drift from LLM reasoning errors. Unique.

### Weaknesses (address)

1. **Rules R4-R16 are performance theater for Opus.** Mandatory per-finding
   annotation `[R4:✓, R5:✓, R6:✗(no role), ...]` turns Opus into a checklist
   processor. The rules themselves are good; the MANDATORY application syntax
   fights Opus's judgment.
2. **Evidence tag proliferation is cognitive debt.** 21+ tags across
   depth/verification. Collapse to 3 tiers: HARD_PROOF (executed mechanical),
   SOFT_PROOF (traced), NEGATIVE.
3. **Scoring formula weights (0.25/0.25/0.3/0.2) are unvalidated.** No
   empirical tuning visible. Either validate on a benchmark or simplify to
   "Opus self-scores 0-1 with rationale; Python floors based on evidence tag."
4. **Phase 3b/3c attention-saturation protocol is a V1 relic.** V2's
   fresh-per-phase context eliminates the saturation problem that 3b/3c were
   designed to work around. In Thorough mode, rescan + per-contract adds 4-8
   sonnet agents to solve a problem V2's architecture no longer has.
5. **No "free exploration" phase.** Every phase prescribes what to look for.
   Nowhere does the pipeline say: "Opus, read this code and tell me what
   worries you, no template." Opus's unprompted pattern-recognition is
   structurally excluded.
6. **Skeptic-Judge only applies to HIGH/CRIT in Thorough.** Arbitrary gate.
   Medium findings with `[CODE-TRACE]`-only evidence benefit more from
   adversarial challenge than HIGH findings with `[POC-PASS]`. Gate should be
   evidence-quality, not severity.
7. **L1 mode unvalidated.** `docs/l1-mode/design.md` requires recall ≥ 30% on
   a 5-target corpus. Never run. L1 remains speculative until this validates.
8. **Methodology bloat despite anti-bloat protocol.** 78 skill files, 21
   evidence tags, 16 rules, 12 injectables, 8+ niche agents. The trend is
   still additive. Needs a consolidation sweep (Part 7 of improvement
   protocol, never run).
9. **Chain analysis is mechanical, not causal.** It matches postcondition
   text to precondition text. Doesn't build an assumption graph. A finding
   "assumes oracle price fresh within 1 hour" should link to every finding
   that consumes oracle data.
10. **Report phase is LLM-heavy by default.** V2 archived Python-deterministic
    report assembly in the drift cleanup. Report generation is the ONE place
    determinism is strictly better — content is fixed after Phase 5, assembly
    is mechanical. Revive the concept.

### Comparison to non-web3 state of the art

| Capability | Best elsewhere | Plamen | Gap |
|---|---|---|---|
| Multi-agent orchestration | AutoGen (conversational) | Task-parallel | Plamen simpler, better for audits |
| Adversarial reasoning | Debate frameworks (Irving et al.) | Devil's Advocate | Plamen narrow — could apply broader |
| Retrieval-augmented reasoning | LangChain + vector DB | RAG sweep | Comparable |
| Self-consistency | Multi-sample + vote | Not used | Missing — cheap win |
| Chain-of-thought prompting | Standard | Implicit | Not mandated; could be |
| Planning + decomposition | AutoGPT, Devin | Hardcoded phase list | Less autonomous; intentional |
| Meta-reasoning / reflection | Reflexion, Self-Refine | Perturbation + Skeptic | Partial; could extend |
| Tool use / grounding | ReAct + MCP | Task + MCP | Comparable |

**Notable absences in Plamen:**
- No **self-consistency** (multi-sample + vote) on HIGH/CRIT hypotheses. Cheap
  to add via Haiku voting on Opus samples.
- No **plan-then-execute** meta-phase where Opus proposes its own investigation
  plan.
- No **reflection-on-false-positives** feedback loop during the run (only
  post-audit).

---

## Part 2 — Opus 4.7 Compliance Audit

### Current compliance grade: C+/B-

Plamen drives Opus like Sonnet-with-a-longer-context. It gets structured
reasoning but does not exploit 4.7's improved unconstrained judgment. The
rules, tags, and procedures that made Sonnet reliable in V1 are now damping
Opus's ceiling.

### What Opus does well here (preserve)

- Parallel Task spawning exploits Opus's multi-step reasoning strength.
- Phase-scoped fresh contexts (V2) respect Opus's context-sensitivity.
- Depth iteration 2+ Devil's Advocate fits Opus's adversarial-mode capability.
- Extended 1M context (Opus 4.7 specific) — V2 doesn't need it as much, but it
  gives headroom for complex phases.

### What fights Opus (remove or soften)

1. **Rigid finding output format with step-execution annotations**
   (`✓1,2,3,5 | ✗4(N/A) | ?6,7`) turns reasoning into bookkeeping.
2. **Mandatory R4-R16 checks per finding** — Opus context-switches from
   "analyze this bug" to "did I annotate R8?" every finding.
3. **Severity modifier stacking** (base matrix × on-chain × view-function ×
   trusted-actor × dead-code) requires mental multiplication Opus can do but
   should not have to.
4. **Evidence tag requirement on every finding** forces classification work
   that sometimes obscures Opus's natural description.
5. **Skills auto-loaded into every relevant agent** can balloon to 800+ lines
   per depth agent (depth-consensus-invariant load). Attention saturation
   inside a single agent context still applies.

### What would unleash Opus more

- **Prose rules over structured rules.** "Consider whether stored parameters
  drift from external sources" beats "R8: Cached Parameters / Stored External
  State: ✓ or ✗(no stored external state)".
- **Opus self-critique phase.** After Phase 4b, give Opus the full findings
  set + attack_surface, ask "what am I missing?" with no template.
- **Remove evidence tag mandate from DRAFT findings.** Tags belong in the
  verified report, not exploratory output. Let depth agents write "I found X
  at file:line" without forcing a tag commitment prematurely.
- **Let Opus pick its own investigation depth per finding.** The fixed
  "iteration 1/2/3" cap mistakes process uniformity for quality control.

---

## Part 3 — Opus 4.7 Usage Optimization

### The real problem

Opus pays per token AND per interrupt. Every mid-phase rate-limit both wastes
the tokens already spent AND destroys the partial work.

Today's V2 phase = one atomic claude -p subprocess with an Opus orchestrator
managing N Task subagents. The failure mode:

1. Breadth spawns 8 Sonnet subagents. 5 complete and write `analysis_1-5.md`.
2. Orchestrator (Opus) approaches turn/token limit while waiting on 3 more.
3. Rate limit hits. Subprocess dies.
4. V2 driver pauses pipeline.
5. On resume, entire breadth phase restarts. The 5 completed files exist on
   disk but the restart orchestrator doesn't know and re-spawns all 8 agents.

**Tokens lost**: orchestrator Opus cycle + 5 completed subagents' work.
**Tokens burned again**: 8 subagents + orchestrator.

### Fix 1 — Resume-within-phase protocol

Add to every phase prompt in `build_phase_prompt` (driver):

```markdown
## RESUMPTION PROTOCOL (MANDATORY FIRST ACTION)

Before spawning ANY Task subagents, check the scratchpad for existing work:

1. List the expected outputs for this phase: {expected_artifacts_list}
2. For each pattern, `ls {scratchpad}/{pattern}` and check file sizes.
3. Any file >= 500 bytes is presumed COMPLETE work from a prior attempt.
   Do NOT re-spawn the subagent that would have produced it.
4. Only spawn subagents for MISSING outputs (or stubs < 200 bytes).
5. If all expected outputs already exist: skip to this phase's merge/analysis
   step. Your job is coordinating what's already there.
```

**Impact**: ~60-80% work preservation on interrupted phases. A rate-limited
breadth loses only the in-flight Task agents (<=3 of 8) and orchestrator
overhead. Previously it lost all 8.

**Cost**: 5 lines of prompt. Zero code change needed.

### Fix 2 — Progressive Opus (Sonnet discovery, Opus decision)

Today's depth phase = 4-8 Opus agents running full methodology. Single biggest
Opus spend.

**Target pattern**:

```
Phase 4b (depth):
  Iter 1: ALL agents = Sonnet. First-pass findings.
  Phase 4b.3 scoring: computes confidence per finding.
  Iter 2 (Devil's Advocate): ONLY runs on findings with
    confidence < 0.7 AND severity >= Medium. Model = Opus.
```

Today's iter 2 in Thorough already routes to Opus for uncertain findings — but
iter 1 is already Opus, so we pay twice. Switch iter 1 to Sonnet.

**Expected savings**: ~70% of depth Opus cost. For a typical Thorough audit
with 30 findings where ~10 are uncertain Medium+, that's ~2/3 of depth budget
saved. **~$15-25 per run**.

**Quality trade-off**: Sonnet misses subtler reasoning in iter 1. Iter 2's
Opus Devil's Advocate explicitly retargets uncertain findings — that's where
Opus adds the most value. Structurally same quality, better cost allocation.

### Fix 3 — Model tier audit

| Phase | Today | Should be | Why |
|---|---|---|---|
| breadth (Core/Thorough) | Opus | **Sonnet** | Discovery task, pattern matching |
| depth iter 1 | Opus | **Sonnet** | See Fix 2 |
| depth iter 2+ (DA) | Opus | Opus | Correct — reasoning-hard |
| rescan iter 1 | Sonnet | Sonnet | OK |
| rescan iter 2 | Sonnet | **Haiku** | Contrastive masking, mechanical |
| per-contract agents | Sonnet | **Haiku or Sonnet** | Narrow-scope, Haiku often sufficient |
| skeptic (Thorough) | Sonnet | Sonnet | OK |
| judge (disagree) | Haiku | Haiku | OK |
| cross-batch consistency | Haiku | Haiku | OK |
| RAG sweep | Sonnet | **Haiku** | Mechanical tool-call loop |
| tier writers | Sonnet | Sonnet C/H, **Haiku L/I** | Writing complexity varies |
| assembler | Sonnet | Sonnet | Correct |
| verify | Sonnet | Sonnet | OK |

**If all applied**: typical Thorough drops from ~$45-50 Opus to ~$12-15 Opus.
**~70% reduction** in premium-model spend.

### Fix 4 — Context minimization in build_phase_prompt

V2 driver sends the FULL V1 orchestrator prompt (~65KB) to every phase. That
is ~15K input tokens × 13 phases = ~195K tokens on the wrapper alone.

**Optimization**: extract ONLY the assigned phase's section (`Phase 3` or
`Step 4b`) + 2-page pipeline-overview table-of-contents + referenced rules.
Drop 40-50KB from the per-phase prompt.

**Expected savings**: ~$3-5 per run. Small but compounds across many audits.

**Cost**: ~30 lines of Python (section-extraction logic). Risk: LLM needs
some cross-reference context ("Phase 4a produces findings_inventory.md which
Phase 4b consumes"). Compromise with the pipeline TOC mitigates.

### Fix 5 — Prompt cache exploitation

Anthropic's prompt cache: identical prompt prefixes get ~90% discount on cache
hits within a ~5 min window.

**Plamen currently doesn't exploit this.** Inside a phase (breadth spawns 8
subagents), each subagent prompt is built independently. If the first 80% is
identical (skill templates + rules + config) and only the last 20% varies
(agent-specific scope), we get cache hits on 7 of 8 agents.

**How**: restructure V1 plamen.md Phase 2/3 subagent-prompt instantiation so
the common prefix is concatenated first, agent-specific content appended last.

**Expected savings**: ~40-50% of per-subagent input token cost on phases that
spawn many similar agents (breadth, depth, verify).

**Cost**: Prompt engineering in V1 plamen.md Phase 2/3. No Python change.

### Fix 6 — Explicit budget dimension

Add `budget: minimal|normal|max` orthogonal to Light/Core/Thorough (which
control COVERAGE).

| Mode | Default budget |
|---|---|
| Light | minimal |
| Core | normal |
| Thorough | normal |

**`budget: minimal`**: No Opus anywhere. Haiku for scoring/merge/validation.
Sonnet for reasoning. **~$5-10 per audit** on a typical 20k LOC project.
Quality loss: ~10-20% finding recall.

**`budget: max`**: Opus for breadth + depth + tier writers. Current behavior.

Lets a user run `/plamen-wizard thorough budget:minimal` — full Thorough
coverage on a Sonnet-only budget. Cost ~$10 vs $60.

---

## Part 4 — Combined Implementation Plan

### Priority tiers

**Tier 1 — Immediate, cheap, high-impact** (~45 min total work):

- [ ] T1.1: Add resumption protocol to `build_phase_prompt` in
      `scripts/plamen_driver.py`. Every claude -p subprocess learns to
      preserve prior-attempt work. **Biggest single user-experience win.**
- [ ] T1.2: Model tier audit — update phase model assignments in
      `scripts/plamen_driver.py` SC_PHASES / L1_PHASES:
  - breadth: `"opus"` → `"sonnet"` (both lists)
  - depth iter 1: `"opus"` → `"sonnet"` (via phase_model override)
  - rescan iter 2: `"sonnet"` → `"haiku"` (internal to phase)
  - RAG sweep: `"sonnet"` → `"haiku"`
  - tier writers for L/I: `"sonnet"` → `"haiku"` (internal to phase)
- [ ] T1.3: Document Tier 1 changes in CLAUDE.md's AUDIT MODES table.

**Tier 2 — This week** (~3-5 hours):

- [ ] T2.1: Context minimization in `build_phase_prompt`. Add
      section-extraction. Pass only the assigned phase's V1 prompt section
      plus a pipeline TOC.
- [ ] T2.2: Prompt cache exploitation in V1 `commands/plamen.md` Phase 2/3.
      Restructure subagent-prompt instantiation so common prefix is cacheable.
- [ ] T2.3: Progressive Opus wiring — depth iter 1 always Sonnet; iter 2
      routes to Opus only for findings with confidence < 0.7 AND severity
      >= Medium. Requires phase 4b scoring gate to emit a routing decision.

**Tier 3 — Validation required** (~5-10 hours):

- [ ] T3.1: Run the L1 5-target benchmark specified in
      `docs/l1-mode/design.md` Section 11. Exit criterion: recall >= 30% on
      the 5 validation targets. Blocks L1 mode's production greenlight.
- [ ] T3.2: New `budget` dimension (`minimal|normal|max`). Requires
      wizard update, config key, driver routing. Validate quality loss on 2-3
      test audits against the benchmark before shipping.
- [ ] T3.3: Consolidation sweep per Part 7 of
      `rules/post-audit-improvement-protocol.md`. Target: reduce 78 skills
      by 15-20% through merging semantic overlaps. Target: collapse 21+
      evidence tags to 3 tiers.

**Tier 4 — Larger methodology changes** (require 10+ audit telemetry runs):

- [ ] T4.1: Remove Rules R4-R16 mandatory annotation. Keep as guideline
      prose in orchestrator prompt; drop per-finding tracking.
- [ ] T4.2: Add "free exploration" breadth sub-phase. Opus agent with zero
      skills, reads full codebase (or slice), answers "what's weird?" — feeds
      into inventory.
- [ ] T4.3: Apply Skeptic-Judge by evidence quality, not severity.
      Any finding with `[CODE-TRACE]`-only AND severity >= Medium triggers
      Skeptic, regardless of mode.
- [ ] T4.4: Scoring formula validation. Tune 4-axis weights against
      ground-truth audit corpus. Currently unvalidated numerics.
- [ ] T4.5: Self-consistency for HIGH/CRIT. Sample each HIGH finding 3×
      with temperature variation; Haiku votes on severity.
- [ ] T4.6: Deterministic report assembly (revive the DRIFT-ARCHIVED
      `report_builder.py` CONCEPT, not its implementation). Python pastes
      tier sections; LLM writes tier content.
- [ ] T4.7: Assumption graph (Phase 4b.5 or 4c). Parse findings for
      "assumes X" patterns, link to findings that mutate X. Causal chain
      analysis vs text-match.
- [ ] T4.8: Dedicated economic-reasoning phase. Post-Phase 4b, one agent
      focused on incentive alignment, MEV surfaces, validator cartels,
      LP incentive games.

### Cost-savings summary

Projected Opus spend on a typical SC Thorough audit of 20k LOC:

| Component | Current | After Tier 1 | After Tier 1+2 |
|---|---|---|---|
| Orchestrator wrapper tokens | $3 | $3 | $1 |
| Breadth | $15 | $3 | $3 |
| Depth iter 1 | $25 | $5 | $5 |
| RAG sweep | $0.50 | $0.10 | $0.10 |
| Tier writers (L/I) | $1 | $0.20 | $0.20 |
| Cache misses on repeated subagents | — | — | -30% on all |
| **Total Opus cost** | **~$45-50** | **~$12** | **~$8-10** |

A Max 5x subscription (~$100/week-equivalent) would handle **7-10 audits/week
instead of 2**.

### What not to do (explicit)

- Do NOT re-architect the V2 driver. It works. Every time V2 was
  "improved" it got worse (see `scripts/archive_drift/README.md`).
- Do NOT add more skills until the consolidation sweep runs. Additive changes
  compound — fight that instinct.
- Do NOT port anything from the archived drift scripts without explicit
  justification against the specific problem being solved.
- Do NOT introduce new evidence tags. Collapse first.
- Do NOT tighten methodology rules under pressure from a single missed
  finding. Per `post-audit-improvement-protocol.md` Step 2.5, run the
  RC-AGENT Exclusion Test first.

---

## Appendix A — What makes the Opus 4.7 budget disappear today

Rough per-run cost decomposition on a Thorough SC audit (20k LOC, ~30
findings, 2026-04-20 run data):

| Phase | Model | Token count (approx) | Billed |
|---|---|---|---|
| Orchestrator wrappers (13 × V1 prompt) | varies | 195K input | ~$3 |
| Breadth (5 agents × 20K) | opus | 100K in + 50K out | ~$15 |
| Rescan (4 agents × 15K) | sonnet | 60K in + 30K out | ~$2 |
| Per-contract (3 agents × 10K) | sonnet | 30K in + 15K out | ~$1 |
| Inventory merge | sonnet | 15K | ~$0.50 |
| Invariants | sonnet | 20K | ~$0.70 |
| Depth iter 1 (8 agents × 25K) | opus | 200K in + 80K out | ~$25 |
| RAG sweep | sonnet | 20K | ~$0.50 |
| Chain analysis (2 agents) | sonnet | 40K | ~$1.50 |
| Verify (16 agents × 10K) | sonnet | 160K | ~$5 |
| Skeptic-Judge (Thorough) | sonnet | 40K | ~$1.50 |
| Cross-batch consistency | haiku | 10K | ~$0.10 |
| Report (Index + 3 tiers + Assembler) | sonnet | 100K | ~$3 |
| **Total** | | | **~$60** |

Opus portion: ~$43 (~70% of total).
Sonnet portion: ~$15.
Haiku portion: ~$0.50.

After Tier 1+2: Opus drops to ~$8, Sonnet rises slightly to ~$20, total ~$30.

---

## Appendix B — Known risks of this plan

1. **Sonnet-for-breadth may miss subtle bugs.** Only validated against Opus
   on a small sample. Test on the L1 benchmark before shipping Tier 1.
2. **Progressive Opus (iter 1 Sonnet + iter 2 Opus) may confuse the
   Devil's Advocate role.** DA is designed to challenge a Sonnet-level
   iter-1 agent. Actually: should work BETTER when iter 1 is Sonnet, because
   the DA is adversarially challenging weaker initial reasoning with stronger
   reasoning. Validate on a test run.
3. **Resumption protocol relies on LLM compliance.** Opus might read the
   protocol but re-spawn already-completed subagents anyway. Mitigate with
   a Python-side check in `gate_passes`: log a warning if a phase "completes"
   but wrote fewer subagent outputs than expected (suggests restart wastage).
4. **Context minimization may lose cross-phase context.** If Phase 4b
   orchestrator needs to know what Phase 3 produced, it's in the scratchpad,
   but without the full V1 prompt as framing, Opus might miss the linkage.
   Mitigate with the pipeline TOC.
5. **Budget:minimal may produce unusable reports for complex protocols.**
   Sonnet's ceiling on economic/cross-domain reasoning is lower than Opus's.
   Ship budget:minimal only as opt-in, not default for Core/Thorough.

---

## Appendix C — File-level pointers (where to make changes)

| Change | Target file | Approximate location |
|---|---|---|
| T1.1 Resumption protocol | `scripts/plamen_driver.py` | `build_phase_prompt`, after HARD SCOPE DIRECTIVE |
| T1.2 Model tier audit | `scripts/plamen_driver.py` | `SC_PHASES` and `L1_PHASES` Phase entries |
| T1.3 Documentation | `CLAUDE.md` | AUDIT MODES table |
| T2.1 Context min | `scripts/plamen_driver.py` | `build_phase_prompt`, section extraction |
| T2.2 Cache exploitation | `commands/plamen.md` | Phase 2/3 prompt instantiation |
| T2.3 Progressive Opus | `scripts/plamen_driver.py` + `rules/phase4-confidence-scoring.md` | `phase_model()` + scoring routing |
| T3.1 L1 benchmark | `benchmarks/l1/` (new) | Per `docs/l1-mode/design.md` §11 |
| T3.2 Budget dim | `scripts/plamen_driver.py` + `commands/plamen-wizard.md` + `commands/plamen-l1-wizard.md` | Config key + model resolution |
| T3.3 Consolidation | `agents/skills/` + `rules/finding-output-format.md` | Per Part 7 of improvement protocol |

---

## Appendix D — How to validate

Before declaring any Tier implemented:

1. Run it on a **known-good target** that previously completed. Compare
   output quality.
2. Measure **actual cost** via Anthropic usage dashboard.
3. Compare **finding count + severity distribution** against the baseline.
4. For quality-sensitive changes (Progressive Opus, Sonnet-for-breadth): run
   a side-by-side A/B on one audit. Compare recall against a ground-truth
   report if available.
5. Log the version bump in `MEMORY.md` per `post-audit-improvement-protocol.md`
   format: one line, version + recall % + RC distribution.

---

## Meta-note

This plan assumes V2 execution is reliable first. Do not start Tier 1 work
until:

- At least one SC Thorough run has completed end-to-end with a final
  `AUDIT_REPORT.md` written.
- At least one L1 Core run has completed breadth through report.

Both are currently in progress (2026-04-21). When they complete, the user
should review this plan, greenlight specific tiers, and only then should
implementation begin.
