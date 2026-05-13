# V2 Optimization Megaplan — Draft

> **Status**: Draft, awaiting dHEDGE rerun assessment results before finalizing
> **Scope**: Three optimization tracks applied to the existing pipeline. No new modes.
> **Goal**: Reduce wall time by ~40%, reduce token consumption by ~25-30%, improve chain coverage from 3.6% to 80%+, and align with Opus 4.7 behavioral changes.

---

## Track 1: SC Prebake — Slither Graph → Flat Files → Agents

### Problem
~55 agents each read raw .sol files and build their own mental model of the codebase from scratch. ~30-40% of per-agent tokens are spent on structural understanding (tracing imports, call chains, inheritance) that is identical across agents. The new Opus 4.7 tokenizer makes this 1.0-1.35x worse.

### Solution
Run Slither ONCE before agents start (like L1's SCIP prebake). Produce 6 markdown flat files. Every agent reads these as structural orientation before reading source code.

### Implementation

**Phase registry** — two new entries between `network_resolve` and `recon`:
```python
("sc_bake",    "all", "sc", "run_sc_bake",    "evm_only"),
("sc_prebake", "all", "sc", "run_sc_prebake",  "slither_available"),
```

**`run_sc_bake`** (pure Python, no LLM):
1. Run `forge build` (required for Slither)
2. Initialize Slither via Python API (import from existing `slither_mcp` package)
3. Extract `ProjectFacts` + state variable write map via `Function.state_variables_written`
4. Cache to `.scratchpad/slither/project_facts.json` + `state_writes.json`
5. Write `.scratchpad/slither/primitive_status.md` with `SLITHER_AVAILABLE: true/false`
6. Non-fatal on failure — agents degrade to source-only analysis

**`run_sc_prebake`** (pure Python, no LLM):
Read cached analysis, produce 6 flat files in `.scratchpad/slither/`:

| File | Contents | Line Cap | Used By |
|------|----------|----------|---------|
| `call_graph.md` | Per-function caller/callee tables, external calls marked | 3000 | All agents |
| `state_write_map.md` | Per-function: variables written + modifiers + visibility | 2000 | depth-token-flow, depth-state-trace |
| `function_summary.md` | One-line per function: visibility, modifiers, callee count, line range | 2000 | All agents |
| `inheritance_tree.md` | Inheritance chain + dependency graph per contract | 1000 | depth-external, breadth |
| `access_control_map.md` | Modifier → function reverse index | 1000 | Scanners, depth-state-trace |
| `detector_findings.md` | High/Medium Slither detector hits only (leads, not findings) | 1000 | depth-edge-case |

Each file includes a limitations header: "This graph is structural, not semantic. It may miss: delegatecalls, proxy patterns, assembly-level calls. Always verify against source code."

**Gate artifacts:**
```python
"sc_bake":    [("slither/primitive_status.md", 1, 10)],
"sc_prebake": [("slither/call_graph.md", 1, 50), ("slither/function_summary.md", 1, 50)],
```

**Condition check:**
```python
elif condition == "slither_available":
    return (scratchpad / "slither" / "project_facts.json").exists()
```

### Agent Prompt Integration

**Breadth driver** — new Step 0 before Step 1:
```markdown
## Step 0: Read SC Prebake Artifacts (if available)

If `{SCRATCHPAD}/slither/primitive_status.md` contains `SLITHER_PREBAKE_COMPLETE: true`,
instruct every breadth agent to include this at the TOP of their analysis:

### Structural Orientation (from Slither prebake)
Read these files FIRST to understand structure before reading source code:
1. `{SCRATCHPAD}/slither/function_summary.md` — one-line overview of every function
2. `{SCRATCHPAD}/slither/call_graph.md` — who calls whom (your navigation map)
3. `{SCRATCHPAD}/slither/inheritance_tree.md` — inheritance and dependencies
4. `{SCRATCHPAD}/slither/access_control_map.md` — which modifiers guard which functions

**USAGE RULES**:
- These files are your MAP — source code is your GROUND TRUTH.
- Use the call graph to identify which functions to read, then READ THE ACTUAL SOURCE.
- Do NOT cite graph files as evidence for findings. Evidence must come from source code.
- The graph may miss dynamic calls (interfaces, delegatecall, etc.).
```

**Depth driver** — per-role file assignments:
| Agent Role | Slither Files to Read |
|------------|----------------------|
| depth-token-flow | call_graph.md, state_write_map.md, function_summary.md |
| depth-state-trace | state_write_map.md, access_control_map.md, function_summary.md |
| depth-edge-case | function_summary.md, detector_findings.md, call_graph.md |
| depth-external | call_graph.md (external calls column), inheritance_tree.md |
| Scanner A-C | function_summary.md, access_control_map.md |

### Non-EVM Coverage

| Chain | Tool | Action |
|-------|------|--------|
| EVM | Slither (Python API) | Full 6-file prebake |
| Solana/Soroban | rust-analyzer SCIP | Reuse L1 bake/prebake (add `rust_bake`/`rust_prebake` phases with `"sc"` pipeline filter) |
| Aptos/Sui | None viable | Skip — skills ARE the graph for Move |

### Error Handling
- `forge build` fails → `SLITHER_AVAILABLE: false`, prebake skipped, zero degradation
- Slither analysis fails → same fallback
- Individual generator crashes → stub file with error, other files generate normally
- Agents detect `SLITHER_PREBAKE_COMPLETE: false` → source-only analysis (existing behavior)

### Estimated Impact
- ~25-30% reduction in per-agent token consumption (structural understanding pre-computed)
- ~30 seconds added for Slither analysis (vs hours of redundant agent reasoning saved)
- Zero risk — purely additive, graceful fallback

---

## Track 2: Mechanical Chain Pre-Filter

### Problem
Chain analysis explored 3.6% of finding pairs (67/1891) because the LLM evaluated pairs one-by-one and ran out of budget. O(N²) pairs with N=60 findings is too many for LLM reasoning.

### Solution
A pure Python phase that computes which finding pairs share state variables. Pre-filters ~1891 pairs down to ~40-100 mechanically viable candidates. Chain agents evaluate only the candidates.

### Implementation

**Phase registry** — one new entry between `final_scoring` and `chain_agent1`:
```python
("chain_prefilter", "all", "sc", "run_chain_prefilter", None),
```

**`run_chain_prefilter`** (pure Python, zero LLM cost):

**Stage 1 — Variable-level pairing (STATE type):**
- Parse `variable_finding_map.md`: for each variable, extract findings that WRITE it and findings that READ it
- Cross-product writers × readers = candidate STATE pairs
- Augment: keyword-grep finding descriptions against `state_variables.md` variable names (catches implicit references the haiku mapper missed)

**Stage 2 — Type-level pairing (ACCESS/TIMING/EXTERNAL/BALANCE):**
- Parse Chain Summary tables from `findings_inventory.md`
- Match postcondition types to precondition types
- Less precise than variable-level but eliminates all disjoint-type pairs

**Stage 3 — Priority sort:**
1. Cross-class pairs with max severity >= Medium — highest
2. Same-variable STATE pairs — high
3. Same-type non-STATE pairs — medium
4. No-overlap pairs → explicitly EXCLUDED

**Output:** `.scratchpad/chain_candidate_pairs.md`
```markdown
# Chain Candidate Pairs (Pre-filtered)
Total findings: 60 | Total possible: 1891 | Candidates: 83 | Excluded: 1808 | Reduction: 95.6%

## STATE Pairs (variable-level match)
| # | Finding A (Writer) | Finding B (Reader) | Shared Variable(s) | Priority |
...

## TYPE Pairs (precondition-type match)
| # | Finding A (Postcondition) | Finding B (Precondition) | Matched Type | Priority |
...
```

**Gate artifact:**
```python
"chain_prefilter": [("chain_candidate_pairs.md", 1, 50)],
```

### Chain Prompt Integration

Modify Agent 2 section in `phase4c-chain-prompt.md`:
```markdown
### Step 2.0: Load Pre-Filtered Pairs

Read {SCRATCHPAD}/chain_candidate_pairs.md. If present:
- Evaluate ONLY the pairs listed in STATE Pairs and TYPE Pairs tables
- For each pair: verify the mechanical match is semantically valid
- Create CHAIN HYPOTHESIS for valid matches
- All unlisted pairs are EXCLUDED (no shared state) — mark as 
  "EXCLUDED: no shared state" in composition_coverage.md
- Do NOT spend time evaluating excluded pairs

If chain_candidate_pairs.md is MISSING, fall back to the original algorithm.
```

Update `_has_unexplored_pairs()` in driver: EXCLUDED pairs should NOT trigger `chain_iter2`.

### Edge Cases
- Finding postcondition has no named variable ("creates timing window") → caught by Stage 2 type matching
- `variable_finding_map.md` missing (Core mode) → fall back to keyword-grep + type-level only
- Zero candidate pairs → write empty table, note for Agent 2 to spot-check 5-10 high-severity pairs
- Same prompt file serves chain_agent1/agent2/iter2 → modification scoped to Agent 2 section only

### Estimated Impact
- Coverage: 3.6% → 80-100% of mechanically viable pairs
- Chain agent token consumption: reduced (evaluating 80 pairs vs attempting 1891)
- Zero risk — if pre-filter file is missing, existing behavior is unchanged

---

## Track 3: Opus 4.7 Behavioral Alignment

### Problem
Opus 4.7 has five behavioral changes that directly impact Plamen:
1. **1.0-1.35x more tokens** per input (new tokenizer)
2. **Fewer tool calls** by default (reasons more, acts less)
3. **Fewer subagents spawned** by default (more judicious delegation)
4. **More literal instruction following** (won't infer/generalize beyond what's asked)
5. **Response length scales with perceived complexity** (no longer defaults to verbose)

### Solution
Targeted prompt changes across all driver prompts + future PhaseConfig additions when Claude Code supports effort and task budgets.

### Immediate Changes (Prompt-Level)

#### A. Explicit subagent spawning mandates

**Breadth driver** (all languages) — add after "Spawn ALL breadth agents":
```
Opus 4.7 MANDATE: You MUST spawn exactly {N} agents listed above. Do not 
consolidate, skip, or handle analysis yourself. Your role is orchestration — 
spawn agents and verify artifacts. If you believe fewer agents would suffice, 
spawn them all anyway. Missing output files cause gate failures and pipeline waste.
```

**Depth driver** (all languages) — add after "Spawn ALL iteration 1 agents":
```
Opus 4.7 MANDATE: Spawn ALL agents listed in the roster above in a SINGLE 
message. Do not reason about whether each agent is needed — spawn them all. 
Every output file is required by downstream phases. Spawn subagents simultaneously 
when fanning across independent analysis domains.
```

#### B. Mandatory evidence tags

**Depth templates** (all languages) — change from suggestive to mandatory:
```
MANDATORY: Every finding MUST include at least one Depth Evidence tag:
[BOUNDARY:X=val], [VARIATION:param A->B], or [TRACE:path->outcome].
A finding without any evidence tag is INCOMPLETE and will be flagged for 
re-analysis in iteration 2. Do not submit findings without tags.
```

#### C. Effort-appropriate thinking guidance

**Opus driver sessions** (breadth, depth, verify) — add to prompt:
```
Think carefully and step-by-step before responding. This security analysis 
is harder than it looks — subtle bugs hide in edge cases and cross-function 
interactions. Do not rush to conclusions.
```

**Haiku mechanical sessions** (scoring, crossbatch, report_index) — add to prompt:
```
Prioritize responding quickly rather than thinking deeply. This is a mechanical 
task — apply the formula/template directly without extensive reasoning.
```

#### D. Explicit tool use encouragement

**All agent prompts** — add before Output Requirements:
```
You SHOULD use tools aggressively: Read source files, Grep for patterns, 
Glob for file discovery. Do not reason about code you haven't read. 
Every claim about code behavior must be backed by a tool call that read 
the relevant source lines.
```

### Future Changes (When Claude Code Supports Them)

#### E. Task budgets per phase

Add to PhaseConfig when API is available via `claude -p`:
```python
PHASE_CONFIGS = {
    # Mechanical phases — small budgets, finish fast
    "inventory_merge":   PhaseConfig("haiku",  10, task_budget=30000),
    "confidence":        PhaseConfig("haiku",  10, task_budget=25000),
    "report_index":      PhaseConfig("haiku",  15, task_budget=40000),
    
    # Analysis phases — large budgets, self-regulate
    "recon":             PhaseConfig("sonnet", 30, task_budget=80000),
    "breadth":           PhaseConfig("opus",   40, task_budget=150000),
    "depth":             PhaseConfig("opus",   60, task_budget=200000),
    "verify":            PhaseConfig("opus",   40, task_budget=120000),
    
    # Chain phases — medium budgets (pre-filtered pairs)
    "chain_agent1":      PhaseConfig("sonnet", 25, task_budget=60000),
    "chain_agent2":      PhaseConfig("sonnet", 25, task_budget=60000),
    
    # Report phases — moderate budgets
    "report_assemble":   PhaseConfig("sonnet", 15, task_budget=50000),
}
```

This replaces timeout-based waste (1800s ceiling) with model-driven self-regulation. The model sees a running countdown and finishes gracefully instead of idling.

#### F. Effort levels per phase

Add to PhaseConfig when API is available:
```python
PHASE_CONFIGS = {
    # Mechanical: low effort
    "inventory_merge":   PhaseConfig("haiku",  10, effort="low"),
    "confidence":        PhaseConfig("haiku",  10, effort="low"),
    "crossbatch":        PhaseConfig("haiku",  10, effort="low"),
    
    # Analysis: xhigh effort (Anthropic recommended for agentic coding)
    "breadth":           PhaseConfig("opus",   40, effort="xhigh"),
    "depth":             PhaseConfig("opus",   60, effort="xhigh"),
    "verify":            PhaseConfig("opus",   40, effort="xhigh"),
    
    # Navigation/grouping: high effort
    "recon":             PhaseConfig("sonnet", 30, effort="high"),
    "chain_agent1":      PhaseConfig("sonnet", 25, effort="high"),
    "chain_agent2":      PhaseConfig("sonnet", 25, effort="high"),
    
    # Report: medium effort
    "report_index":      PhaseConfig("haiku",  15, effort="medium"),
    "report_assemble":   PhaseConfig("sonnet", 15, effort="medium"),
}
```

---

## Implementation Sequence

### Phase 1: Opus 4.7 Prompt Alignment (immediate, low effort)

| Step | What | Files | Risk |
|------|------|-------|------|
| 1a | Subagent spawn mandates | All breadth/depth drivers (10 files) | Zero |
| 1b | Mandatory evidence tags | All depth templates (5 files) | Zero |
| 1c | Thinking guidance per phase | All driver prompts | Zero |
| 1d | Tool use encouragement | All agent prompt templates | Zero |

**Estimated effort**: 1-2 hours. Apply text edits to prompt files.
**When**: Before the next audit.

### Phase 2: Chain Pre-Filter (small, zero LLM cost)

| Step | What | Files | Risk |
|------|------|-------|------|
| 2a | `run_chain_prefilter()` function | plamen_driver.py | Low |
| 2b | Phase registry + gate + dispatch | plamen_driver.py | Low |
| 2c | Agent 2 prompt modification | phase4c-chain-prompt.md | Low |
| 2d | `_has_unexplored_pairs()` update | plamen_driver.py | Low |
| 2e | Assessment prompt update | v2-dhedge-rerun-assessment.md | Zero |

**Estimated effort**: 3-4 hours. Pure Python parsing + prompt edit.
**When**: After dHEDGE assessment confirms chain coverage is still low.

### Phase 3: SC Prebake (medium, requires Slither API integration)

| Step | What | Files | Risk |
|------|------|-------|------|
| 3a | `run_sc_bake()` — Slither compilation + analysis | plamen_driver.py | Medium (import path) |
| 3b | `run_sc_prebake()` — 6 markdown generators | plamen_driver.py | Low |
| 3c | Phase registry + gates + conditions | plamen_driver.py | Low |
| 3d | Breadth driver Step 0 (all languages) | 5 breadth driver files | Low |
| 3e | Depth driver Slither directive (all languages) | 5 depth driver files | Low |
| 3f | Test on known Foundry project | N/A | Low |
| 3g | Solana/Soroban SCIP reuse (optional) | plamen_driver.py | Low |

**Estimated effort**: 1-2 days. Slither API integration + 6 generators + prompt edits.
**When**: After Phase 2 is validated.

### Phase 4: Task Budgets + Effort Levels (future, blocked on Claude Code)

| Step | What | Blocked By |
|------|------|-----------|
| 4a | Add `effort` field to PhaseConfig | Claude Code CLI `--effort` flag |
| 4b | Add `task_budget` field to PhaseConfig | Claude Code CLI `--task-budget` flag |
| 4c | Per-phase effort/budget tuning | Both above |

**When**: When Anthropic adds these to `claude -p` CLI.

---

## Expected Cumulative Impact

| Metric | Baseline (dHEDGE v1) | After Phase 1 | After Phase 2 | After Phase 3 | After Phase 4 |
|--------|----------------------|---------------|---------------|---------------|---------------|
| Wall time | ~6h | ~5.5h (less retry waste from clearer prompts) | ~5h (chain agents faster) | ~3.5-4h (agents start smarter) | ~3h (self-regulating budgets) |
| Token consumption | 100% | ~95% (fewer wasted retries) | ~90% (chain pre-filtered) | ~65-70% (prebaked graph) | ~55-60% (effort+budget tuning) |
| Chain coverage | 3.6% | 3.6% | 80-100% of viable pairs | 80-100% | 80-100% |
| Depth evidence tags | 0 | >0 (mandatory enforcement) | Same | Same | Same |
| Subagent count accuracy | Variable (4.7 may under-spawn) | Fixed (explicit mandates) | Same | Same | Same |
| Timeout waste | ~53 min confirmed | ~30 min (clearer prompts) | ~25 min | ~20 min | ~0 (task budgets replace timeouts) |

---

## Dependencies and Blockers

| Dependency | Blocks | Status |
|------------|--------|--------|
| dHEDGE rerun assessment results | Finalizing this plan | In progress |
| Slither MCP package importable from driver | Phase 3a | Untested (import path) |
| Claude Code `--effort` CLI flag | Phase 4a | Not available |
| Claude Code `--task-budget` CLI flag | Phase 4b | Not available (API-only beta) |
| `forge build` working on target project | Phase 3a | Required (Slither needs compilation) |

---

## Research References

- [Heimdallr](https://arxiv.org/html/2601.17833) — Slither dependency graph for contextual profiling, F1=0.62
- [iAudit](https://arxiv.org/abs/2403.16073) — Mixed results from call graph info; over-reliance risk
- [LLMSmartSec](https://ieeexplore.ieee.org/document/10664261/) — Annotated CFG fed to 3 specialized agents
- [Grego.ai](https://grego.ai/) — AST → call graph → dataflow → LLM reasoning, 4 min for 48K LOC
- [Opus 4.7 Best Practices](https://claude.com/blog/best-practices-for-using-claude-opus-4-7-with-claude-code) — xhigh effort, fewer subagents, literal instruction following
- [Opus 4.7 What's New](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7) — Tokenizer 1.0-1.35x, adaptive thinking, task budgets
- [Effort Levels](https://platform.claude.com/docs/en/build-with-claude/effort) — xhigh recommended for coding/agentic
- [Task Budgets](https://platform.claude.com/docs/en/build-with-claude/task-budgets) — Advisory token ceiling, API-only beta
