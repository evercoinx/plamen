# Phase 3: Parallel Breadth Analysis

> **Loaded by**: The V2 driver's Phase 3 subprocess (breadth analysis).
> **Purpose**: Parallel spawn rule, post-spawn verification, overreach handling,
> output file conventions, and agent closeout. Self-contained methodology for the
> breadth analysis phase.

---

## Spawn Rule

Spawn breadth agents in bounded parallel batches.

- If 1-6 breadth agents are missing, spawn them in a SINGLE message as parallel Task calls.
- If 7+ breadth agents are missing, spawn a batch of at most 6 agents, wait for that batch, close completed agents, then spawn the next batch.
- On retry, count only missing or stub breadth outputs from `spawn_manifest.md`; do not include already substantial outputs in the batch size.
- Each agent operates on its own scope independently and writes to its exact
  `Expected Output` from `spawn_manifest.md`.

---

## Post-Spawn Verification

Completion is manifest-exact, not batch-exact. A returned batch does not mean
the phase is complete.

Before exit, run this loop:

1. Parse `spawn_manifest.md` and build `EXPECTED_OUTPUTS`:
   - Include only rows that represent spawned breadth agents.
   - Do not include skill, injectable, template, methodology, checklist,
     binding, `merged into ...`, `covered by ...`, or `no separate agent`
     rows. Those rows modify an agent prompt; they do not own standalone
     `analysis_*.md` files.
   - Use the explicit output filename if the manifest names one.
   - Otherwise derive `{SCRATCHPAD}/analysis_<focus_area>.md`.
2. Build `COMPLETE_OUTPUTS` from expected files that exist and are >=200 bytes.
3. Build `OPEN_OUTPUTS` from expected files that are missing or <200 bytes.
4. If `OPEN_OUTPUTS` is non-empty:
   - Spawn agents for only the first `OPEN_OUTPUTS` batch.
   - Every Task prompt MUST include: focus area, expected output filename,
     and `FIRST ACTION: write a one-line header to {SCRATCHPAD}/{expected_output}`.
   - Do not identify outputs by numeric agent id; `analysis_1.md` is invalid
     when the manifest expects `analysis_core_state.md`.
   - Use at most 6 parallel Task calls per batch.
   - Wait for that batch and close completed agents.
   - Return to step 2.
5. Exit only when `OPEN_OUTPUTS` is empty.

Do not stop because the current batch returned. Do not proceed with 7/12,
9/12, or any partial manifest completion. If any required file is missing,
re-spawn that exact agent before returning from Phase 3.

Update `spawn_manifest.md` with completion status for each agent only after
the corresponding output file exists and is >=200 bytes.

---

## Output File Conventions

Each breadth agent writes to a single file:
```
{SCRATCHPAD}/analysis_{focus_area}.md
```

Where `{focus_area}` is the lowercase, hyphenated or underscored version of the agent's focus area (e.g., `analysis_core_state.md`, `analysis_access_control.md`, `analysis_oracle.md`).
The manifest `Expected Output` column is authoritative. If it names a file,
the agent must write that exact filename.

Finding IDs use a per-agent prefix:
- Core state agent: `[CS-1]`, `[CS-2]`, ...
- Access control agent: `[AC-1]`, `[AC-2]`, ...
- Token flow agent: `[TF-1]`, `[TF-2]`, ...
- External dependency: `[EX-1]`, `[EX-2]`, ...
- (Other prefixes assigned per focus area)

---

## Overreach Handling

If any breadth agent wrote later-phase files (inventory/depth/chain/verify/report), treat those files as invalid overreach for sequencing purposes. Record the violation in `{SCRATCHPAD}/violations.md`, close the offending agent, and continue the pipeline from inventory using only valid `analysis_*` outputs.

Specifically, breadth agents write exactly one manifest-derived
`analysis_<focus_area>.md` file. No other artifact family is a breadth output.
`analysis_rescan_*.md` and `analysis_percontract_*.md` are owned by the later
`rescan` phase, not first-pass breadth. Do not create, update, register, or
mention them as completion artifacts in `spawn_manifest.md`.

---

## Agent Closeout

- Do NOT read analysis files after agents return — the inventory agent reads them in Phase 4a.
- Close completed breadth agents before inventory begins. Do not carry finished breadth workers into subsequent phases.

---

## Context Budget Protection

The orchestrator does NOT read agent output files. Agent outputs stay on disk and are consumed by downstream phases (inventory agent, depth agents, chain agents). This protects the orchestrator's context from saturation.

Per the WRITE-THEN-VERIFY protocol, each agent:
1. Writes output directly to `{SCRATCHPAD}/{expected_filename}` using the Write tool
2. Returns ONLY a one-line summary: `"DONE: {N} findings written to {filename}"`

The orchestrator verifies file existence and size (>=200 bytes) mechanically after each return.

---

## Mode-Specific Agent Counts

| Mode | Agent Count | Model |
|------|-------------|-------|
| Light | 3-4 | sonnet |
| Core | 5-9 | opus |
| Thorough | 5-9 | opus |

Light mode caps at 3-4 sonnet agents. Core/Thorough use 5-9 opus agents based on complexity determination from Phase 2 Step 2a.

---

## Scope Containment Directive

Every breadth agent prompt MUST end with:

```
SCOPE: Write ONLY to your assigned output file. Do NOT read or write other agents' output files. Do NOT proceed to subsequent pipeline phases (re-scan, per-contract, inventory, semantic invariants, depth, RAG, chain analysis, verification, report). Return your findings and stop.
```

This prevents agents from attempting to run the entire pipeline solo.

For the breadth orchestrator itself: always reuse existing substantial
manifest-derived breadth outputs and spawn only the missing or stub rows from
`spawn_manifest.md`. Re-run the completion loop until all expected outputs are
substantial.
