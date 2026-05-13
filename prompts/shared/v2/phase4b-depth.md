# Phase 4b: Adaptive Depth Loop

> **Loaded by**: The V2 driver's Phase 4b subprocess (depth analysis loop).
> **Reference**: `~/.claude/rules/phase4-confidence-scoring.md` for scoring model,
> anti-dilution rules, and convergence criteria.
> **Purpose**: Self-contained methodology for the adaptive depth loop including
> iteration 1-3, agent roles, scoring integration, convergence criteria, blind
> spot scanners, validation sweep, niche agents, DST, perturbation, and skill
> execution checklist.

---

## Phase Boundary

This prompt owns only Phase 4b depth work. It must not execute Phase 4b.5
RAG, chain analysis, verification queue construction, per-finding
verification, skeptic/crossbatch work, or report work.

Depth may write only depth-owned artifacts: depth/scanner/niche findings,
initial confidence scoring, adaptive loop logs, design stress, perturbation,
skill execution gaps, invariant fuzz results, Medusa fuzz findings, and the
depth lifecycle markers. If a later phase needs extra context, record the need
inside a depth-owned output and stop; the driver starts the later phase with a
fresh subprocess.

## Light Mode Override

When `MODE == light`, skip the standard 8-agent spawn. Instead spawn 4 merged sonnet agents, but still write the canonical output files that the driver gates:
- (a) combined token-flow + state-trace writes `depth_token_flow_findings.md` and `depth_state_trace_findings.md`
- (b) combined edge-case + external writes `depth_edge_case_findings.md` and `depth_external_findings.md`
- (c) combined scanner A+B+C writes `blind_spot_a_findings.md`, `blind_spot_b_findings.md`, and `blind_spot_c_findings.md`
- (d) validation sweep writes `validation_sweep_findings.md` or `scanner_validation_findings.md`

Skip niche agents, skip confidence scoring, skip iterations 2-3. After iteration 1 completes, write the required depth outputs and stop. Chain analysis runs in a separate phase.

---

## Iteration 1 (Core/Thorough): Full Agent Spawn

Spawn ALL standard agents + niche agents in a SINGLE message as parallel Task calls:

### Standard Depth Agents (4)

| Agent | Role | Output File | Domain |
|-------|------|-------------|--------|
| depth-token-flow | Token/value flow tracing | `depth_token_flow_findings.md` | Balance changes, transfer paths, fee calculations, share conversions |
| depth-state-trace | State transition correctness | `depth_state_trace_findings.md` | State variable updates, conditional writes, storage layout, role transitions |
| depth-edge-case | Boundary conditions | `depth_edge_case_findings.md` | Zero/max values, first/last user, overflow, rounding, empty state |
| depth-external | External interactions | `depth_external_findings.md` | Oracle calls, external contracts, cross-chain, reentrancy, callback handling |

Each depth agent reads:
- `{SCRATCHPAD}/findings_inventory.md` (breadth findings to investigate)
- `{SCRATCHPAD}/semantic_invariants.md` (if exists; else `state_variables.md`)
- `{SCRATCHPAD}/design_context.md` (protocol context)
- `{SCRATCHPAD}/spec_expectations.md` (if present; tests/mocks/harnesses as
  expectation evidence only, not as reportable production targets)
- Source files relevant to their domain

### Standard Depth Agent Semantic Proof Block (COPY INTO EACH OF THE 4 PROMPTS)

When spawning `depth-token-flow`, `depth-state-trace`, `depth-edge-case`, and
`depth-external`, paste this block verbatim into each agent prompt. Record these
checks inside the assigned depth output file only; do not create new artifacts.

```
## MANDATORY SEMANTIC-PROOF CHECKS

For every investigated candidate touching value movement, accounting,
authorization or lifecycle state, shares or claims, fees, limits, or guarded
arithmetic / branch-dependent updates:

1. Semantic invariant challenge: state the intended invariant in code-level
   terms, then try to falsify it across reachable write paths, not only the
   originally reported path.
2. Read-site expectation check: identify where the written or derived value is
   later read, trusted, or consumed, and compare writer semantics against reader
   expectations. If no read site is found, record that as an unresolved evidence
   gap, not proof of safety.
3. Branch-formula matrix for guarded arithmetic: for each guard or conditional
   that changes arithmetic, conversions, caps, rounding, or accumulator updates,
   record condition -> formula/effect -> boundary inputs -> expected
   postcondition. Check branch inversion, skipped branch, equality boundary,
   zero/empty-state, and max/cap cases when applicable.
4. Intent-proof before no-finding: do not mark SAFE, REFUTED, by-design,
   expected behavior, or no-finding for the candidate unless you cite concrete
   intent evidence from specs, docs, tests, comments, interfaces, or consistent
   call/read-site behavior. Absence of a failing trace, lack of exploitability
   work, or "guard exists" is not intent proof.

If intent proof is missing, classify the candidate as unresolved/non-reportable
only with the missing proof and remaining risk recorded; do not treat it as
SAFE/REFUTED.

In your output file, include a section named exactly:

## Semantic Proof Checks

Summarize the challenged invariant(s), read-site expectation(s), branch matrix
entries, and intent evidence or evidence gap for each relevant candidate.
```

### Finding Severity / Disposition Contract (MANDATORY)

For every `### Finding [...]` block, the `**Severity**:` field MUST contain
exactly one canonical severity:

`Critical`, `High`, `Medium`, `Low`, or `Informational`

Never write disposition text in the severity field. Invalid examples:

- `N/A`
- `N/A (absorbed into DE-2)`
- `REFINED`
- `absorbed into ...`
- `duplicate`
- `refuted`

If a candidate is absorbed/refined/duplicate/not independently reportable, do
not emit it as a live finding block. Put that decision in a separate
`## Non-Reportable / Absorbed Candidates` section or the Chain Summary table,
with the canonical severity cell set to `Informational` if a severity cell is
required. The Python verifier treats disposition-as-severity as contract drift.

### Language-Specific Depth Template Binding (MANDATORY)

Before spawning any of the 4 standard depth agents, read:

`~/.claude/prompts/{LANGUAGE}/phase4b-depth-templates.md`

Use the matching role template from that file as the base prompt for each standard
depth agent. Do not summarize, weaken, or omit template requirements. The shared
V2 prompt defines phase orchestration; the language-specific depth template
defines the actual per-role analysis contract.

Every standard depth-agent prompt MUST include the language template's
graph-artifact section verbatim. If any of these graph artifacts exist in
`{SCRATCHPAD}`, every standard depth output MUST reference every produced graph
artifact by filename, or emit an explicit unavailable tag:

- `{SCRATCHPAD}/caller_map.md`
- `{SCRATCHPAD}/callee_map.md`
- `{SCRATCHPAD}/state_write_map.md`
- `{SCRATCHPAD}/function_summary.md`
- `[GRAPH-ARTIFACT: UNAVAILABLE:<file>]`

The Python gate enforces this contract after the phase. Treat missing graph
references as a first-attempt failure, not as something to leave for retry.

### Standard Depth Agent Graph Block (COPY INTO EACH OF THE 4 PROMPTS)

When spawning `depth-token-flow`, `depth-state-trace`, `depth-edge-case`, and
`depth-external`, paste this block verbatim into each agent prompt. Do not
replace it with a summary.

```
## MANDATORY GRAPH-ARTIFACT CONSUMPTION

Before investigating findings, read or check these four graph artifacts:

1. `{SCRATCHPAD}/caller_map.md`
2. `{SCRATCHPAD}/callee_map.md`
3. `{SCRATCHPAD}/state_write_map.md`
4. `{SCRATCHPAD}/function_summary.md`

In your output file, include a section named exactly:

## Graph Artifact Consumption

That section MUST contain one bullet for each artifact basename. Use the
consumed form when the artifact exists and was read; use the unavailable form
only when the artifact is absent or unreadable:

- `[GRAPH-ARTIFACT: caller_map.md]` - consumed; relevant calls/callers used:
  <brief note>
- `[GRAPH-ARTIFACT: UNAVAILABLE:caller_map.md]` - <reason>
- `[GRAPH-ARTIFACT: callee_map.md]` - consumed; relevant callees used:
  <brief note>
- `[GRAPH-ARTIFACT: UNAVAILABLE:callee_map.md]` - <reason>
- `[GRAPH-ARTIFACT: state_write_map.md]` - consumed; relevant state writes used:
  <brief note>
- `[GRAPH-ARTIFACT: UNAVAILABLE:state_write_map.md]` - <reason>
- `[GRAPH-ARTIFACT: function_summary.md]` - consumed; relevant functions used:
  <brief note>
- `[GRAPH-ARTIFACT: UNAVAILABLE:function_summary.md]` - <reason>

Mentioning only the absolute path is not enough; the tag must contain the
basename exactly. Missing this section or any of the four artifact basenames
causes the Python gate to fail the whole depth phase.
```

Before returning from the depth phase, re-open the four standard depth output files and verify each contains `## Graph Artifact Consumption` plus all four graph-artifact basenames. If any output is missing the section, repair that output before returning; do not rely on the driver retry.

### Blind Spot Scanners (3)

| Scanner | Focus | Output File |
|---------|-------|-------------|
| Scanner A | Tokens and Parameters | `blind_spot_a_findings.md` |
| Scanner B | Guards, Visibility, Inheritance + Override Safety | `blind_spot_b_findings.md` |
| Scanner C | Role Lifecycle, Capability Exposure + Reachability | `blind_spot_c_findings.md` |

Scanner C CHECK 5 contains the untrusted-call-target check that catches guard parameter injection. ALL 3 scanners are NEVER-CUT in Thorough mode.

### Validation Sweep Agent (1)

Output: `validation_sweep_findings.md` or `scanner_validation_findings.md`

Performs cross-cutting validation across all contracts: write-site completeness, accumulator co-dependencies, loop iteration bounds, event emission coverage.

### Niche Agents (flag-triggered)

For each REQUIRED niche agent in `template_recommendations.md` -> `Niche Agents` section:
- Read definition from `~/.claude/agents/skills/niche/{name}/SKILL.md`
- Spawn alongside depth agents
- Each niche agent = 1 budget slot
- Output: `{SCRATCHPAD}/niche_{name}_findings.md`

Late trigger: after reading `semantic_invariants.md`, if it reports
`sync_gaps >= 1`, `accumulation_exposures >= 1`, `conditional_writes >= 1`,
or `cluster_gaps >= 1`, spawn `SEMANTIC_GAP_INVESTIGATOR` even if it is not
listed in `template_recommendations.md`. It writes
`{SCRATCHPAD}/niche_semantic_gap_findings.md` and must preserve every
missing-write / lifecycle-gap mechanism as a standalone finding or explicit
SAFE conclusion.

Every Phase 4b subagent prompt MUST end with this containment line:

`SCOPE: Write ONLY to your assigned output file. Do NOT read or write other agents' output files. Do NOT proceed to subsequent pipeline phases. Return your findings and stop.`

### Timeout Split-and-Retry

If any spawned agent stalls or times out, do not wait until the whole phase
expires. Close or abandon only that stalled agent, then split its assigned scope
into 2 "lite" agents (max 3 findings each, no static analyzer, max 5 files).
2 lite agents = 1 budget unit.

The phase may return only after every required output file below exists and is
substantial, or after a replacement lite agent has produced the missing output:

- `depth_token_flow_findings.md`
- `depth_state_trace_findings.md`
- `depth_edge_case_findings.md`
- `depth_external_findings.md`
- `blind_spot_a_findings.md`
- `blind_spot_b_findings.md`
- `blind_spot_c_findings.md`
- `validation_sweep_findings.md` or `scanner_validation_findings.md`
- `confidence_scores.md` (Core/Thorough)
- `adaptive_loop_log.md`
- `design_stress_findings.md` or `depth_design_stress_findings.md` (Thorough)
- `perturbation_findings.md` or `depth_perturbation_findings.md` (Thorough)
- `skill_execution_gaps.md` or `skill_execution_checklist.md` (Thorough)

This is a first-attempt completion requirement. Do not rely on the driver retry
path to repair missing standard depth files, never-cut artifacts, or graph
artifact references.

---

## STEP-TRACE Injection (Thorough Only — MANDATORY)

Each of the 4 depth-agent prompts (token-flow, state-trace, edge-case, external) MUST include the STEP-TRACE directive verbatim. Without it, the agent will not emit `step_execution_trace_{role}.md` and the driver's `_check_step_execution_traces` gate will hard-fail the depth phase.

- Light/Core mode depth spawns SKIP STEP-TRACE — the gate is mode-gated to Thorough only.
- Scanners and niche agents do NOT need STEP-TRACE (different agent class; gate operates on `depth_*_findings.md` only).
- v2.3.3 status: Agent-emit is now ADVISORY. The Python driver auto-synthesizes `step_execution_trace_{role}.md` from findings evidence-tag density if the agent does not emit a richer one.

---

## Scoring (MANDATORY for Core/Thorough)

After iteration 1 agents return, the orchestrator MUST spawn the scoring agent and await `confidence_scores.md` before deciding whether to proceed to iteration 2. Skipping scoring to "go straight to chain analysis" is a VIOLATION.

This is **initial Phase 4b confidence scoring** and is in scope for the depth
phase. It is distinct from the later `final_scoring` phase. Do not run RAG or
`final_scoring` here, but do produce `confidence_scores.md` before returning
in Core/Thorough mode.

Use the standalone scoring prompt:

`~/.claude/prompts/shared/v2/phase4b-scoring.md`

When writing `confidence_scores.md`, preserve original depth/scanner/niche
finding IDs such as `DCI-3`, `DST-4`, `DX-2`, `DN-1`, `PERT-1`, `SLITHER-1`,
`VS-1`, and `BLIND-1`. Do NOT collapse those rows into only mapped `INV-*`
inventory IDs; downstream promotion and retry gates parse the original feeder
IDs and the `Composite` column from this file.

If no scoreable findings are present, still write `confidence_scores.md` with
the header row and a short note: `No scoreable findings found after depth
iteration 1.` An empty or missing scoring file fails the depth gate.

### Core Mode: 2-Axis Scoring
```
composite = Evidence x 0.5 + Analysis_Quality x 0.5
```

### Thorough Mode: 4-Axis Scoring
```
composite = Evidence x 0.25 + Consensus x 0.25 + Analysis_Quality x 0.3 + RAG_Match x 0.2
```

### Classification Thresholds

| Composite Score | Classification | Action |
|----------------|---------------|--------|
| >= 0.7 | **CONFIDENT** | No more depth needed for this finding |
| 0.4-0.7 | **UNCERTAIN** | Spawn targeted depth agent for this finding's domain during the depth loop |
| < 0.4 | **LOW CONFIDENCE** | Spawn a depth agent for more code evidence during the depth loop; record `RAG_NEEDED` and `VERIFICATION_NEEDED` notes inside depth-owned outputs only |

---

## Iteration 2 (Thorough Only)

**Core mode**: Skip iteration 2 entirely. Record uncertain findings in depth outputs, then stop. Later phases consume those outputs separately.

**Thorough mode**: Spawn targeted Devil's Advocate depth agents per domain for ALL uncertain findings.

### Hard DA Role (Anti-Dilution Rule AD-2)

Iteration 2+ agents receive structural adversarial framing:

*"You are the Devil's Advocate Depth Agent. Your PRIMARY job is to find what the previous analysis MISSED - not to re-confirm what it found."*

Key rules:
- Hard Devil's Advocate role: agents are structurally adversarial
- Severity-weighted budget: `spawn_priority = (1 - confidence) * severity_weight`
- Anti-dilution: evidence-only finding cards (AD-1), max 5 per agent (AD-3)
- Fresh tool calls mandatory (AD-4)
- New-evidence-only re-scoring (AD-5)
- Contrastive conditioning via analysis path summaries

### Loop Dynamics Detection

Classify score changes as:
- **CONTRACTIVE**: Scores converging (improving) -> continue
- **OSCILLATORY**: >50% of changes are reversals -> force CONTESTED, exit
- **EXPLORATORY**: New findings discovered -> continue

---

## Iteration 3 (Thorough Only, Conditional)

Only if still uncertain findings exist AND progress was made in iteration 2.

After iteration 3:
- Force remaining < 0.4 to CONTESTED verdict
- Write `adaptive_loop_log.md` with iteration count, spawns used, exit condition triggered

---

## Convergence Criteria

| Criterion | Value |
|-----------|-------|
| Hard iteration cap | 3 (Core: 1, Light: 1 with no scoring) |
| Dynamic spawn cap | `min(max(depth_floor, ceil(findings/5)+7), hard_cap)` |
| Progress check | If NO finding's confidence improved in an iteration -> exit early |
| Zero uncertain | If 0 findings score < 0.7 after any iteration -> exit loop |
| Forced CONTESTED | After all iterations, any finding still < 0.4 -> forced CONTESTED |
| Oscillation detection | >50% reversals -> force CONTESTED, exit |
| Iteration 2 skip policy | May ONLY skip if all UNCERTAIN findings are Low/Info. Medium+ UNCERTAIN = MANDATORY iter 2. |

---

## Post-Iteration Mandatory Steps (Thorough Only)

Steps 6-8 run IN PARALLEL. Spawn all 3 agents in a single message with 3 Task calls.

### Step 6: Design Stress Testing (UNCONDITIONAL)

ALWAYS spawn Design Stress Testing Agent. 1 slot is pre-reserved and UNCONDITIONAL — not a "budget redirect." This agent runs regardless of remaining budget.

Output: `{SCRATCHPAD}/design_stress_findings.md`

### Step 7: Finding Perturbation Agent (MANDATORY)

After depth iteration completes, spawn the Finding Perturbation Agent (sonnet, 1 pre-reserved budget slot).

- Applies 5 structured mutation operators (DIRECTION_FLIP, BOUNDARY_SHIFT, ROLE_SWAP, TIMING_INVERT, PARAMETER_SWAP) to each CONFIRMED depth finding
- Tests for adjacent vulnerabilities
- Catches the "single-hit satisfaction" class
- Output: `{SCRATCHPAD}/perturbation_findings.md` with `[PERT-N]` IDs

Use the standalone prompt `~/.claude/prompts/shared/v2/phase4b-perturbation.md`.

### Step 8: Skill Execution Checklist (MANDATORY)

After depth iteration completes, spawn the Depth Skill Execution Checklist Agent (haiku, negligible cost).

- Verifies each depth agent executed each step of its assigned skill
- Produces coverage table: `| Agent | Skill Step | Evidence in Output? | Gap? |`
- Gaps become investigation questions for DA iteration 2 per AD-6
- Output: `{SCRATCHPAD}/skill_execution_gaps.md` (also satisfies `skill_execution_checklist.md` via any_of group)

Use the standalone prompt `~/.claude/prompts/shared/v2/phase4b-skill-checklist.md`.

---

## THOROUGH CHECKPOINT: Post-Depth (Static Manifest Check)

**Do NOT write checkpoint assertions from memory.** Read the static manifest and verify against it:

```
// STEP 0: Mode gate — this check is Thorough-only
if MODE != THOROUGH:
    // Core/Light: only assert confidence_scores.md + adaptive_loop_log.md exist, then proceed
    ASSERT: confidence_scores.md exists (Core) OR skip scoring (Light)
    ASSERT: adaptive_loop_log.md exists
    LOG to {SCRATCHPAD}/checkpoint_postdepth.md
    return from the depth subprocess

// STEP 1: Read the static manifest (orchestrator MUST NOT modify this file)
manifest = Read("~/.claude/prompts/{LANGUAGE}/phase4b-required-artifacts.md")

// STEP 2: Check EVERY required artifact exists
missing = []
for each file in manifest.required_artifacts_table:
    if not exists({SCRATCHPAD}/{file}):
        missing.append({file, producer})

// STEP 3: Check niche agent artifacts
for each niche agent marked Required: YES in {SCRATCHPAD}/template_recommendations.md:
    if not exists({SCRATCHPAD}/{niche_file}):
        missing.append({niche_file, niche_agent_name})

// STEP 3b: Check late semantic-gap trigger from semantic_invariants.md
if semantic_invariants.md reports sync_gaps/accumulation_exposures/conditional_writes/cluster_gaps > 0:
    if not exists({SCRATCHPAD}/niche_semantic_gap_findings.md):
        missing.append({"niche_semantic_gap_findings.md", "SEMANTIC_GAP_INVESTIGATOR"})

// STEP 4: If missing -> spawn, do NOT proceed
if len(missing) > 0:
    LOG to {SCRATCHPAD}/violations.md: "PHASE 4b INCOMPLETE: {missing}"
    for each missing file:
        spawn the responsible agent (see Producer column in manifest)
    re-run STEP 2 after agents complete
    ASSERT len(missing) == 0 — HARD GATE, cannot mark depth complete

// STEP 5: Standard assertions
ASSERT: confidence_scores.md is non-empty
ASSERT: IF uncertain Medium+ findings exist after iter 1 -> adaptive_loop_log shows iter >= 2

LOG checkpoint result to {SCRATCHPAD}/checkpoint_postdepth.md
```

**WHY STATIC MANIFEST**: The orchestrator previously wrote its own checkpoint — verifying only what it remembered to do, silently skipping what it forgot. The static manifest file is defined outside the orchestrator's generation context. Missing depth-owned artifacts trigger depth-agent spawns, not silent passes.

---

## THOROUGH CHECKPOINT: Pre-Depth (EVM only)

When `MODE == thorough` AND `LANGUAGE == evm`:

**Step A: Invariant Fuzz Campaign** (MANDATORY — zero budget cost)
Read template: `~/.claude/prompts/{LANGUAGE}/phase4b-invariant-fuzz.md`
Spawn agent. Await completion. Write results to `invariant_fuzz_results.md`.
The template has a 5-minute timeout built in. Do NOT skip this to save time.

**Step B: Medusa Campaign** (MANDATORY if MEDUSA_AVAILABLE — zero budget cost)
Read the standalone Medusa prompt:
`~/.claude/prompts/shared/v2/phase4b-medusa.md`.
Spawn agent IN PARALLEL with Step A. Await completion.
Write results to `medusa_fuzz_findings.md`.

**Step C: Assert Completion**
```
ASSERT: invariant_fuzz_results.md exists (or COMPILATION_FAILED logged)
ASSERT: medusa_fuzz_findings.md exists (or MEDUSA_UNAVAILABLE logged)
IF either missing AND no failure logged -> VIOLATION: "Fuzz campaign skipped without failure reason"
```

---

## Depth Exit

When all depth-owned artifacts are present and substantial, write
`depth_exit.md` with the structured format below and stop. Do not continue into
later pipeline phases.

```markdown
- criterion: {1-4}
- rationale: {why this exit criterion was met}
- explored_paths:
- {path 1 explored by depth agents}
- {path 2 explored by depth agents}
- {path 3 explored by depth agents}
```

Exit criteria:
1. All iterations exhausted (hard cap reached)
2. Zero uncertain findings remain (all >= 0.7 confidence)
3. No confidence improvement in latest iteration (stalled)
4. Full convergence (requires >= 3 explored paths documented)
