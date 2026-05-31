# Phase 6: Report Generation Prompt Templates

> **Usage**: Orchestrator reads this file and spawns the report pipeline: Index → 3 Tier Writers → Assembler.
> Replace placeholders `{SCRATCHPAD}`, `{PROJECT_ROOT}`, `{PASTE_VERDICTS}`, etc. with actual values.

> **Architecture**: Index Agent → 3 Parallel Tier Writers → Assembler Agent
> **Why**: A single report agent gets overwhelmed on 30+ hypotheses, producing catch-all tables and invisible findings. Splitting by severity tier ensures every finding gets a proper write-up.

---

## Step 6a: Index Agent

> **Model**: haiku (mechanical task - fast, cheap)
> **Purpose**: Creates master index mapping internal hypothesis IDs to clean report IDs. Assigns each hypothesis to exactly one tier.

```
Task(subagent_type="general-purpose", model="haiku", prompt="
You are the Report Index Agent. You create the master finding index for the audit report.

## Your Inputs
Read:
- {SCRATCHPAD}/verify_core.md — **OPTIONAL but primary when present**. Both current SC and L1 pipelines normally produce this via the verification aggregate phase. When absent, enumerate `verify_*.md` files directly and derive the per-hypothesis verdicts from them. Do NOT fail the phase on its absence.
- {SCRATCHPAD}/rag_validation.md (historical support / contradiction)
- {SCRATCHPAD}/finding_mapping.md (hypothesis → agent finding mapping)
- {SCRATCHPAD}/contract_inventory.md (component list for report header)
- {SCRATCHPAD}/findings_inventory.md (complete agent finding inventory)
- {SCRATCHPAD}/recon_summary.md (audit themes and risk areas)
- {SCRATCHPAD}/template_recommendations.md (recommended niche/analysis lanes)
- If `verify_core.md` exists and already provides the needed status, do NOT open
  `finding_mapping.md`, raw depth findings, or scanner findings up front.
  Treat those as fallback-only inputs for missing detail, not default reads.
  If `verify_core.md` is absent, read `verify_*.md` files directly
  — each contains its own hypothesis ID and verdict header.
- {SCRATCHPAD}/depth_*_findings.md (raw depth findings and chain summaries)
- {SCRATCHPAD}/blind_spot_*_findings.md or {SCRATCHPAD}/scanner_*_findings.md (scanner findings)
- {SCRATCHPAD}/validation_sweep_findings.md or {SCRATCHPAD}/scanner_validation_findings.md (validation findings)
- {SCRATCHPAD}/dedup_candidate_pairs.md (OPTIONAL — pre-computed same-file finding pairs with high title overlap or shared code identifiers, produced by the depth promotion pipeline. Use these pairs as HINTS for Step 1.5 consolidation — each pair is a candidate for merging, not a mandate.)
- {SCRATCHPAD}/poc_demotions.md (OPTIONAL — mechanically-computed severity caps for findings where PoC execution disproved the claimed harm. If present, apply caps in STEP 1 rule 7.)

Forbidden inputs:
- Do NOT read `{SCRATCHPAD}/report_index.md`, `{SCRATCHPAD}/report_coverage.md`,
  or any `*.attempt*` file when a retry hint is present. Previous report-index
  outputs are known-bad artifacts, not evidence.
- Do NOT read `_prompt_*`, `_stdio_*`, `tool_calls.jsonl`, `_retry_quarantine/`,
  or `_overflow/`.

Verification verdicts from orchestrator:
{PASTE_VERDICTS}

## Your Task

### Severity Authority Contract (READ BEFORE TIERING)

Report indexing is a mapping task, not a new severity-assessment phase. Do NOT
recalibrate severity from your own impact judgment, economic intuition, title
wording, or evidence tag. The final report tier must be mechanically derived
from upstream severity plus explicit, auditable adjustment rules.

For every Master Finding Index row:

1. Read the mapped `verify_<ID>.md` file and the matching
   `verification_queue.md` row.
2. The row's final `Severity` and Report ID prefix (`C-`, `H-`, `M-`, `L-`,
   `I-`) MUST match the upstream verifier/queue severity unless one of the
   canonical adjustments below applies.
3. `[CODE-TRACE]` does NOT imply Low. When `PROVEN_ONLY: false`, a
   Medium/High/Critical `[CODE-TRACE]` finding stays at its upstream severity.
4. If final severity differs from upstream, the `Trust Adj.` column MUST
   contain one canonical reason with the original severity:
   - `TRUSTED-ACTOR(original_sev)`
   - `UNRESOLVED(original_sev)` or `PARTIAL(original_sev)`
   - `SEVERITY_OVERRIDE(original_sev)` only when supplied by the
     driver-only `_severity_override_ledger.json`
   - `POC-FAIL(original_sev)`
   - `PROVEN(original_sev)` only when `PROVEN_ONLY: true`
   - `CHAIN-UPGRADE(original_sev)` / `CHAIN-DOWNGRADE(original_sev)` with the
     chain ID and enabling relation
5. A bare `-` / `—` Trust Adj. is valid only when final severity equals
   upstream severity.
6. If no canonical adjustment applies, restore the upstream severity and
   matching report-ID tier. Do not silently move Medium verified findings into
   Low rows, and do not silently upgrade Informational findings to Low.

### STEP 1: Determine Final Severities

For each hypothesis, apply this priority order:
1. If a verifier returned a verdict → use verifier's final severity
2. If chain analysis upgraded severity → use upgraded severity
3. Otherwise → use the severity from hypotheses.md
4. **Trust tags**: In `findings_inventory.md`, `[ASSUMPTION-DEP: TRUSTED-ACTOR]` mechanically applies -1 tier downgrade (floor: Informational) and `TRUSTED-ACTOR(original_sev)` in Trust Adj.; `[ASSUMPTION-DEP: WITHIN-BOUNDS]` is index context only. Do not override, remove, or selectively skip Inventory tags.
5. **Proven-only mode**: Only when `PROVEN_ONLY: true`, cap findings whose best evidence is `[CODE-TRACE]` only at Low and record `PROVEN(original_sev)`. Count demotions for the report header note. When false, `[CODE-TRACE]` does not affect severity.
6. **UNRESOLVED/PARTIAL**: Treat both tokens from `skeptic_*.md` or `judge_*.md` as unresolved verifier disagreement. Apply -1 tier downgrade (floor: Low), record `UNRESOLVED(original_sev)`, keep the finding in the body, and have the writer tag it `[UNRESOLVED - needs human review]`. Placing it in Excluded Findings is a workflow violation.
7. **Skeptic-judge DOWNGRADE**: If `skeptic_judge_decisions.md` exists, for each row where Decision is `DOWNGRADE`, cap the finding's severity at the Final Severity column value. Record `SKEPTIC-DOWNGRADE(original_sev)` in Trust Adj. Do NOT apply to rows with Decision KEEP, UNRESOLVED, or PARTIAL (those are handled by rule 6). DOWNGRADE is deliberate severity calibration by the skeptic-judge — it takes priority over matrix defaults but yields to PoC mechanical evidence (rule 8).
8. **PoC-fail caps**: If `poc_demotions.md` exists, apply each listed cap, record `POC-FAIL(original_sev)`, and keep the finding in the body. The driver computes this file from `[POC-FAIL]` evidence; the Index Agent must not override or skip entries.
9. **Driver-only severity overrides**: If `_severity_override_ledger.json`
   exists, apply only the listed override rows and record
   `SEVERITY_OVERRIDE(original_sev)` in Trust Adj. Agents must not invent this
   token without the driver-only ledger.

### STEP 1.25: Client-Worthiness Triage (CONSERVATIVE)

This is presentation triage, not a new analysis phase. Assign exactly one:

- `REPORTABLE`: client-facing body section.
- `MERGE_INTO:<report-or-internal-id>`: same root cause/fix, no semantic loss; preserve source ID in Consolidation Map.
- `APPENDIX_ONLY`: true/plausible observation, but not body material.
- `DROP_FALSE_POSITIVE`: verifier refuted the claim or harm.
- `DROP_NON_SECURITY`: no plausible loss, privilege, liveness, accounting, integrity, or observability impact.
- `DROP_DESIGN_CONFIRMATION`: expected behavior or intentionally safe design property.
- `DROP_UNACTIONABLE_SPECULATION`: no reachable path, impacted actor, or actionable fix.
- `UNRESOLVED_EVIDENCE`: contested or insufficient evidence after analysis/verification.

Safety rules:

1. Never silently delete a candidate. Non-`REPORTABLE` candidates MUST appear in
   `report_coverage.md` and either the Consolidation Map or Excluded Findings.
2. Medium+ verified candidates default to `REPORTABLE`. They may be moved out
   of the body only when the verifier/judge evidence explicitly supports
   `DROP_FALSE_POSITIVE`, `DROP_NON_SECURITY`, `MERGE_INTO`, or
   `UNRESOLVED_EVIDENCE` with a reason that names the missing reproducible
   path, trace, proof, support, or sufficient evidence.
3. Low/Informational candidates may be `APPENDIX_ONLY` when true but minor,
   repetitive, non-exploitable, or primarily operational/documentation quality.
4. Design confirmations, positive properties, safe invariants, and
   "works as intended" rows are not report-body findings. Use
   `DROP_DESIGN_CONFIRMATION` unless they are needed as context for a real
   reportable issue.
5. Do not use triage to hide uncertainty. If a candidate has credible loss or
   safety impact and is not refuted, keep it `REPORTABLE` or `APPENDIX_ONLY`
   with a clear reason.
6. Dozens of weak, repetitive, design-confirmation, or non-security body sections are a quality failure. Prefer a smaller client body plus complete traceability.

Use the existing `Excluded Findings` table for `APPENDIX_ONLY` and `DROP_*`
statuses. The Exclusion Reason MUST begin with the exact triage status token
above, followed by a concise evidence-based reason.

### STEP 1.5: Root-Cause Consolidation (MANDATORY)

Before assigning report IDs, consolidate hypotheses that share the same root cause into single report findings. This prevents inflated finding counts from pipeline fragmentation.

**Pre-computed hints**: Both sources are hints only; final semantic decision uses the test below and neither hint mechanically blocks/removes findings.

1. **`[LIKELY-DUP]` tags in `findings_inventory.md`**: same file and >=80% title overlap. Evaluate first; consolidate only when root cause and fix are the same.

2. **`dedup_candidate_pairs.md`**: same-file pairs with >=50% title overlap or shared code identifiers. Evaluate with the consolidation test.

**When in doubt, do NOT merge.** A duplicate finding in the report is a cosmetic issue. A dropped true positive is a missed vulnerability.

**Semantic retention rule**: Preserve established broken invariant, branch precondition, terminal mechanism, verification disposition, and source IDs from bounded inputs (`verify_core.md`, `verify_*.md`, `finding_mapping.md`, `findings_inventory.md`, dedup hints). Retain, do not rediscover: no fresh analysis or bulk raw-artifact reads; read minimal fallback only for missing detail that prevents semantic loss.

**Consolidation test** - merge two hypotheses into ONE report finding if ALL of these are true:
1. **Same fix pattern**: Same type of code change.
2. **Same severity tier**: Both are in the same tier after STEP 1 adjustments
3. **Same vulnerability class**: Same bug pattern.
4. **Describable together**: A reader can understand all affected locations from a single description + location table
5. **No semantic loss**: The merged finding preserves every distinct invariant, branch, terminal mechanism, disposition, and source ID in title/description, locations, Consolidation Reason, or coverage ledger

**Do NOT merge if**:
- Findings are in different severity tiers
- Root causes/fixes differ (for example missing event vs wrong event parameters)
- Merging would exceed 6 locations per finding (split into 2 findings for readability)
- Merging would drop, blur, or overwrite a distinct branch precondition or terminal mechanism. Different branches/mechanisms stay separate unless a shared finding can state both clearly.

**Common consolidation patterns**: missing events, invalid admin setter values, missing staleness checks, retroactive parameter changes, and same-role trust findings when severity/root cause/fix match.

**Output**: For each consolidation, record:
```
CONSOLIDATED: H-{A} + H-{B} [+ H-{C}...] → single finding
  Title: {consolidated title}
  Locations: {list all affected locations}
  Severity: {shared severity}
  Internal refs: {all absorbed hypothesis IDs}
```

In `Consolidation Reason`, include shared root cause/fix plus preserved invariant, branch, terminal mechanism, disposition, and source IDs. Do not add Master Finding Index columns.

Hypotheses NOT consolidated remain as standalone findings.

### STEP 2: Assign Report IDs

Sort all findings (consolidated and standalone) by severity tier, then by verification status (VERIFIED first), then by impact.

Assign clean sequential IDs:
- Critical: C-01, C-02, ...
- High: H-01, H-02, ...
- Medium: M-01, M-02, ...
- Low: L-01, L-02, ...
- Informational: I-01, I-02, ...

### STEP 3: Create Tier Assignments

Assign each finding to exactly ONE tier writer:
- **Critical+High Tier**: All C-XX and H-XX findings
- **Medium Tier**: All M-XX findings
- **Low+Info Tier**: All L-XX and I-XX findings

### STEP 4: Create Cross-Reference Map

For chain findings that reference component findings, note the cross-references using REPORT IDs only.
Example: If chain hypothesis CH-1 (now C-01) references standalone hypothesis H-5 (now H-03), record: 'C-01 references H-03'

### STEP 5: Verify Completeness (MANDATORY)

Cross-check: For EVERY hypothesis in hypotheses.md AND every standalone finding
([VS-*], [BLIND-*], [SE-*], [EN-*], [SLITHER-*]) in findings_inventory.md:
- Is it assigned a report ID in the Master Finding Index above?
- If NO and NOT marked FALSE_POSITIVE by a verifier → ASSIGN a report ID and tier

**HARD RULE**: The ONLY valid exclusion reasons are the conservative triage
statuses from Step 1.25, an explicit false-positive/refuted verifier verdict,
or an explicit duplicate/merge already listed with the absorbing report ID.
"Not grouped into a hypothesis" is NOT a valid exclusion reason.

### STEP 5.5: Promotion Coverage Audit (MANDATORY)

Before finalizing the index, produce an accounting receipt in `report_coverage.md`.
Keep this bounded: use `verification_queue.md`, `verify_core.md`,
`finding_mapping.md`, and the final Master Finding Index as the coverage
sources. Do NOT bulk-read raw breadth/depth/scanner artifacts in this phase.
Raw promotion checks are enforced mechanically by the Python gate; the indexer
owns the reportable verification-to-report mapping.

Coverage reasoning preserves upstream semantics. Keep invariant, branch, terminal mechanism, disposition, and source IDs visible in `Report ID / Refutation / Reason` when they affect promotion, duplicate, false-positive, or deferred status. `DUPLICATE` reasons must show no distinct branch/mechanism was dropped; otherwise assign a report ID or use the correct non-duplicate status.

For each verification/finding-mapping candidate, assign one of:
- `PROMOTED`: mapped to a report ID
- `MERGED`: absorbed into an existing report ID with no semantic loss
- `APPENDIX_ONLY`: accounted in Excluded Findings but intentionally outside the body
- `DROP_FALSE_POSITIVE`: explicitly refuted by verifier/judge evidence
- `DROP_NON_SECURITY`: no security-relevant impact after verification
- `DROP_DESIGN_CONFIRMATION`: positive/safe/design-confirmation row, not a finding
- `DROP_UNACTIONABLE_SPECULATION`: no reachable path, impacted actor, or actionable fix
- `DUPLICATE`: absorbed into an existing report ID; use only when equivalent to `MERGED`
- `FALSE_POSITIVE`: legacy alias for `DROP_FALSE_POSITIVE`
- `DEFERRED`: hypothesis remains unproven, but must still appear in the ledger

**HARD RULES**:
1. A Medium+ candidate from verification_queue/finding_mapping MUST NOT disappear silently.
2. If a Medium+ candidate is not promoted, the ledger must name the absorbing
   report ID or the exact verifier/judge evidence supporting the non-body
   triage status.
3. If recon recommends a niche lane (for example `EVENT_COMPLETENESS`) and that
   lane did not run in the current mode, record the uncovered recommendation in
   the ledger as `DEFERRED: mode-limited`.
4. If a candidate appears in verification_queue/verify_core but not in
   `finding_mapping.md`, treat that as a promotion failure and either assign it
   a report ID or explicitly mark it duplicate / false positive / deferred with
   reasoning.
5. Do not use `DUPLICATE`, `MERGED`, `APPENDIX_ONLY`, or any `DROP_*` status as
   convenience when a distinct invariant, branch, terminal mechanism,
   disposition, or source ID is not retained by the absorbing report ID and
   ledger reason.

This step exists to catch the exact pipeline failure mode where a verified
candidate is dropped before the final report without making report_index an
unbounded raw-artifact scanner.

### Excluded Findings (for Appendix A)
| Internal ID | Severity | Title | Exclusion Reason (APPENDIX_ONLY / DROP_* / FALSE_POSITIVE / DUPLICATE OF X-NN / MERGE_INTO X-NN only) |

## Output

Write to {SCRATCHPAD}/report_index.md:

```markdown
# Report Index

## Report Header Info
- Project Name: {from design_context.md}
- Date: {today}
- Contracts: {from contract_inventory.md}
- Build Status: {from build_status.md}

## Summary Counts
| Severity | Count |
|----------|-------|
| Critical | {N} |
| High | {N} |
| Medium | {N} |
| Low | {N} |
| Informational | {N} |

## Master Finding Index

| Report ID | Title | Severity | Location | Verification | Trust Adj. | Internal Hypothesis |
|-----------|-------|----------|----------|--------------|-----------|--------------------|
| C-01 | [critical upstream title] | Critical | [location] | VERIFIED | - | <critical-internal-id> |
| H-01 | [high upstream title] | High | [location] | VERIFIED | - | <high-internal-id> |
| M-01 | [trusted-actor-demoted high title] | Medium | [location] | UNVERIFIED | TRUSTED-ACTOR(High) | <trusted-actor-internal-id> |
| ... | ... | ... | ... | ... | ... | ... |

**CRITICAL FORMAT RULES for Master Finding Index:**
0. **Example rows use placeholders only.** Do not copy their report tier, report ID, or internal ID shape for real findings.
1. **Internal Hypothesis column MUST be LAST** — downstream validation extracts the last matching ID per row.
2. **Do NOT include an Agent Sources column.** Agent source traceability belongs in `report_coverage.md`, not in the client-facing report.
3. **No parenthetical constituent IDs** — write `CH-3` or `H-2+H-13`, NOT `CH-3 (H-2, H-13)`. Parenthetical constituent IDs in the same cell cause duplicate binding failures.
4. **Consolidated rows may use `+`-joined IDs** — write `H-2+H-13` in Internal Hypothesis and list the same IDs in the Consolidation Map. Use `CH-3` only when a chain has its own verifier file; otherwise use joined constituents so writers can read every evidence file.
5. **Section headings must be EXACT** — use `## Master Finding Index` and `## Excluded Findings`; suffixes create duplicate parsing.

## Tier Assignments

### Critical+High Tier (for Opus writer)
[List of report IDs with their internal hypothesis refs and verification file paths]

### Medium Tier (for Sonnet writer)
[List of report IDs with their internal hypothesis refs]

### Low+Info Tier (for Sonnet writer)
[List of report IDs with their internal hypothesis refs]

## Consolidation Map
| Report ID | Consolidated From | Consolidation Reason |
|-----------|------------------|---------------------|
| L-03 | H-39, H-40, H-55 | Same fix pattern: add zero-value validation to admin setters |
| L-08 | H-70, H-71 | Same fix pattern: add event emission to admin state changes |

## Cross-Reference Map
| Report ID | References | Context |
|-----------|-----------|---------|
| C-01 | H-03, M-05 | Chain: C-01 combines the bugs described in H-03 and M-05 |

## Excluded Findings (for Appendix A)
| Internal ID | Severity | Title | Exclusion Reason (FALSE_POSITIVE or DUPLICATE OF X-NN only) |
```

Also write to `{SCRATCHPAD}/report_coverage.md`:

```markdown
# Report Coverage Audit

## Raw Candidate Ledger
| Source File | Candidate ID / Label | Severity Signal | Status | Report ID / Refutation / Reason |
|-------------|----------------------|-----------------|--------|---------------------------------|

## Uncovered Mode-Limited Recommendations
| Source | Recommended Lane | Why Not Run | Risk of Miss |
|--------|------------------|-------------|--------------|

## Promotion Failures Repaired
| Raw Source | Candidate | Action Taken |
|------------|----------|--------------|
```

### Terminal Stop Rule

After writing both `report_index.md` and `report_coverage.md`, stop immediately.
Do not reopen either output to polish it. Do not run another self-audit loop.
The Python driver owns post-write validation and will retry with a precise hint
if anything is still wrong.

Forbidden outputs in this phase:
- Do NOT write `report_critical_high.md` or `report_critical_high_*.md`.
- Do NOT write `report_medium.md` or `report_medium_*.md`.
- Do NOT write `report_low_info.md` or `report_low_info_*.md`.
- Do NOT write `AUDIT_REPORT.md`.

If you notice any of those files already exist, ignore them. They belong to
later subprocesses and are not part of your task.

Return: 'DONE: {N_total} findings indexed ({N_consolidated} consolidated from {N_original} hypotheses) - {C} Critical, {H} High, {M} Medium, {L} Low, {I} Info'
")
```

---

### Step 6a.1: Completeness Verification (Orchestrator Inline)

After Index Agent returns, orchestrator performs:
1. Count hypothesis IDs in `{SCRATCHPAD}/hypotheses.md` (grep for `| H-`)
2. Count report IDs in `{SCRATCHPAD}/report_index.md` Master Finding Index
3. Count excluded IDs in `{SCRATCHPAD}/report_index.md` Excluded Findings
4. Count consolidated IDs (hypotheses absorbed into another report finding via STEP 1.5)
5. **ASSERT**: `hypothesis_count == report_ids + excluded_count + consolidated_absorbed_count`
6. If **MISMATCH**:
   - Diff ID sets to find missing hypotheses
   - Log: `"INDEX COMPLETENESS FAILURE: {missing_list}"`
   - Re-spawn Index Agent with: `"Assign report IDs to these missing hypotheses: {list}"`
   - Re-run this verification after re-spawn

> **This is a mechanical check - the orchestrator does it inline, no new agent needed.**

Then verify `{SCRATCHPAD}/report_coverage.md` exists and has at least one ledger row
for every depth/scanner artifact present in the scratchpad.

---

## Step 6b: Tier Writers (3 Parallel Agents)

> **Spawn ALL THREE in a single message as parallel Task calls.**
> Each tier writer receives ONLY its assigned findings from report_index.md.

### Tier Writer Common Rules (referenced by all 3 writers)

All tier writers MUST follow these rules:
1. **NO internal IDs** (hypothesis IDs, chain IDs, agent IDs) anywhere in output
2. **Every finding gets its own ### section** - no tables, no groups, no summaries, no catch-all dumps
3. **Write as if the reader has never seen the audit pipeline** - no references to breadth agents, chain analysis, etc.
4. **Cross-references use report IDs only** - include finding title in parentheses for context: `see H-03 (example title)`
5. **Trust context**: For `TRUSTED-ACTOR`, include after Severity: "Severity adjusted from {original} - attack requires {actor} to violate stated trust assumption: {assumption}." For `WITHIN-BOUNDS`, note bounds context in Description without changing severity.
6. **Missing verify_*.md**: Never stub. Write the full body from hypothesis/inventory data, mark header `[VERIFICATION NOT EXECUTED]`, include the Phase 5 no-PoC sentence, and populate Severity, Location, Description, Impact, Recommendation.
7. **Minimum length**: Every `### [X-NN]` body must be at least 400 characters or the quality gate retries the writer.
8. **Chunk scoping**: If the driver prefix lists "Findings assigned to THIS chunk", write only those IDs in order.
9. **Minimal-input override**: If the driver prefix supplies a minimal read set, obey it; avoid broad `hypotheses.md`, `chain_hypotheses.md`, `synthesis_full.md`, or unrelated `verify_*.md` reads unless assigned detail is missing.
10. **Constituent root-cause preservation**: For each finding, check `finding_mapping.md` to identify all source findings grouped into that hypothesis. For each source finding, read its Root Cause line in `findings_inventory.md`. If any source finding describes a distinct root cause angle not already in your Description — a different function, a different missing write, a different mechanism — add it to the Description (e.g., "Additionally, `distributeTitanXForBurning()` does not update `totalTitanXDistributed` on deposit, meaning…"). A report finding that only describes the hypothesis's dominant framing while dropping an absorbed constituent's distinct root cause is a recall gap in the delivered report.

### Critical+High Tier Writer

> **Model**: opus (highest quality for most important findings)

```
Task(subagent_type="general-purpose", model="opus", prompt="
You are the Critical+High Findings Writer. You write the Critical and High severity sections of the audit report.

**FIRST ACTION**: Use the Write tool to create `{SCRATCHPAD}/report_critical_high.md` with a one-line header `# Critical and High Findings`. This reserves your write budget so the file exists on disk even if your composition is interrupted. You will overwrite it with the full tier content at the end.

## Your Inputs
Read:
- {SCRATCHPAD}/report_index.md (your tier assignments under 'Critical+High Tier')
- assigned `verify_*.md` files only (verification results with PoC details)
- {SCRATCHPAD}/findings_inventory.md (fallback detail and source citations)
- {SCRATCHPAD}/finding_mapping.md (hypothesis → source finding mapping — for rule 10 constituent angle check)
- Optional fallback only if an assigned verify file is missing: {SCRATCHPAD}/hypotheses.md, {SCRATCHPAD}/chain_hypotheses.md
- ~/.claude/rules/report-template.md (finding format and rules)

## Your Task

For EACH finding assigned to your tier in report_index.md:

1. Write a full finding section using the EXACT format from report-template.md
2. Use the report ID from report_index.md (C-01, H-01, etc.) - NEVER use internal pipeline IDs
3. Include code snippets from the actual source files
4. For chain findings: describe the COMPLETE attack sequence in the Description - the reader should understand the full attack without reading other findings
5. For verified findings: include PoC results from verify_*.md
6. Cross-reference other findings using ONLY report IDs from report_index.md

## HARD RULES
Follow ALL Tier Writer Common Rules above (no internal IDs, own ### per finding, reader-naive perspective, report-ID cross-refs with title).

## Output

Write to {SCRATCHPAD}/report_critical_high.md:

```markdown
## Critical Findings

### [C-01] Title [VERIFIED]
[full section]

...

## High Findings

### [H-01] Title [VERIFIED/UNVERIFIED/CONTESTED]
[full section]

...
```

Return: 'DONE: {C} Critical + {H} High findings written'
")
```

### Medium Tier Writer

> **Model**: sonnet (good quality, cost-effective for medium tier)

```
Task(subagent_type="general-purpose", model="sonnet", prompt="
You are the Medium Findings Writer. You write the Medium severity section of the audit report.

**FIRST ACTION**: Use the Write tool to create `{SCRATCHPAD}/report_medium.md` with a one-line header `# Medium Findings`. This reserves your write budget so the file exists on disk even if your composition is interrupted. You will overwrite it with the full tier content at the end.

## Your Inputs
Read:
- {SCRATCHPAD}/report_index.md (your tier assignments under 'Medium Tier')
- assigned `verify_*.md` files only when present
- {SCRATCHPAD}/findings_inventory.md (agent finding details)
- {SCRATCHPAD}/finding_mapping.md (hypothesis → source finding mapping — for rule 10 constituent angle check)
- Optional fallback only if an assigned verify file is missing: {SCRATCHPAD}/hypotheses.md, {SCRATCHPAD}/chain_hypotheses.md
- ~/.claude/rules/report-template.md (finding format and rules)

For each finding, read cited source files directly when you need a code snippet.

## Your Task

For EACH finding assigned to your tier in report_index.md:

1. Write a full finding section using the EXACT format from report-template.md
2. Use the report ID from report_index.md (M-01, M-02, etc.) - NEVER use internal pipeline IDs
3. Include code snippets from the actual source files
4. Include a clear Recommendation with fix guidance

## HARD RULES
Follow ALL Tier Writer Common Rules above. Additionally: do NOT create catch-all sections - every finding is equally important.

## Output

Write to {SCRATCHPAD}/report_medium.md:

```markdown
## Medium Findings

### [M-01] Title [UNVERIFIED]
[full section]

### [M-02] Title [UNVERIFIED]
[full section]

...
```

Return: 'DONE: {M} Medium findings written'
")
```

### Low+Informational Tier Writer

> **Model**: sonnet (good quality, cost-effective for lower tiers)

```
Task(subagent_type="general-purpose", model="sonnet", prompt="
You are the Low+Informational Findings Writer. You write the Low and Informational severity sections of the audit report.

**FIRST ACTION**: Use the Write tool to create `{SCRATCHPAD}/report_low_info.md` with a one-line header `# Low and Informational Findings`. This reserves your write budget so the file exists on disk even if your composition is interrupted. You will overwrite it with the full tier content at the end.

## Your Inputs
Read:
- {SCRATCHPAD}/report_index.md (your tier assignments under 'Low+Info Tier')
- {SCRATCHPAD}/findings_inventory.md (agent finding details)
- {SCRATCHPAD}/finding_mapping.md (hypothesis → source finding mapping — for rule 10 constituent angle check)
- assigned `verify_*.md` files only when an assigned finding explicitly references one
- Optional fallback only if required detail is missing: {SCRATCHPAD}/hypotheses.md
- ~/.claude/rules/report-template.md (finding format and rules)

For each finding, read cited source files directly when you need a code snippet.

## Your Task

For EACH finding assigned to your tier in report_index.md:

1. **Classify**: Does this finding belong in the Quality Observations megasection?
   A finding qualifies ONLY if ALL of these are true:
   - Severity is Low or Informational
   - The title/description matches one of these cosmetic classes: dead code,
     unused imports, unused variables, naming inconsistencies, typos, magic
     numbers, missing documentation, code style, gas optimization, redundant
     code/checks, variable shadowing
   - The finding has NO plausible security impact (missing validation, missing
     events, access control, centralization risk are NOT cosmetic even at Low)

2. **Full-section findings**: Write using the EXACT format from report-template.md.
   Use report IDs (L-01, I-01, etc.). Include code snippets where relevant.
   Low: Recommendation field optional. Informational: PoC Result optional.

3. **Quality Observation findings**: Write as a single row in the megasection table.
   Include: ID, Title, Severity, Location, Class (from list above), 1-sentence Description.

**When in doubt, use full-section format.** The megasection is for unambiguous cosmetic observations only. A finding that could have security implications always gets a full section.

## HARD RULES
Follow ALL Tier Writer Common Rules above. Additionally: even simple Low/Info findings that get full sections deserve 3-5 sentences of Description and a clear Location.

## Output

Write to {SCRATCHPAD}/report_low_info.md:

```markdown
## Low Findings

### [L-01] Title
[full section]

...

## Informational Findings

### [I-01] Title
[full section]

...

## Quality Observations

| ID | Title | Severity | Location | Class | Description |
|----|-------|----------|----------|-------|-------------|
| I-03 | Unused import SafeMath | Info | src/Vault.sol:L5 | Unused imports | SafeMath imported but never used post-0.8 |
| L-04 | Dead code in _legacy() | Low | src/Router.sol:L200 | Dead code | Unreachable after v2 migration |
```

If no findings qualify for Quality Observations, omit the section entirely.

Return: 'DONE: {L} Low + {I} Informational findings written ({Q} as quality observations)'
")
```

---

## Step 6c: Assembler Agent

> **Model**: haiku for ≤25 findings, sonnet for >25 findings (haiku truncated on large reports in prior audits)
> **Purpose**: Merges the three tier sections into the final AUDIT_REPORT.md with header, summary, remediation order, and optional appendix.

```
Task(subagent_type="general-purpose", model="{haiku_or_sonnet}", prompt="
You are the Report Assembler. You merge the tier sections into the final audit report.

## Your Inputs
Read:
- {SCRATCHPAD}/report_index.md (header info, summary counts, cross-reference map, excluded findings)
- {SCRATCHPAD}/report_critical_high.md (Critical + High sections)
- {SCRATCHPAD}/report_medium.md (Medium section)
- {SCRATCHPAD}/report_low_info.md (Low + Informational sections)
- ~/.claude/rules/report-template.md (report structure template)

## Your Task

### STEP 1: Assemble Report

Combine sections in this order:
1. **Report Header** - from report_index.md header info
2. **Executive Summary** - 2-3 paragraphs summarizing the audit (write this yourself based on the findings)
3. **Summary Table** - from report_index.md counts
4. **Components Audited Table** - from report_index.md
5. **Critical Findings** - paste from report_critical_high.md (Critical section)
6. **High Findings** - paste from report_critical_high.md (High section)
7. **Medium Findings** - paste from report_medium.md
8. **Low Findings** - paste from report_low_info.md (Low section)
9. **Informational Findings** - paste from report_low_info.md (Informational section)
10. **Priority Remediation Order** - generate from report_index.md, ordered: Critical → High → Medium
11. **Appendix A: Excluded Findings** - client-facing exclusion summary only; internal traceability remains in report_index.md/report_coverage.md

### STEP 1.5: Output Sanitization

- Sanitize copied tool output before writing the report: strip control
  characters, ANSI escape sequences, form-feed, and other non-printable bytes
  from headers, prose, and code fences unless they are required source code.

### STEP 2: Quality Checks

Before writing, verify:
1. **Finding count matches summary** - count ### sections per severity tier, must equal summary table
2. **No internal IDs anywhere in AUDIT_REPORT.md** - scan for [CS-, [AC-, [TF-, [BLIND-, [EN-, [SE-, [VS-, [DEPTH-, [SLITHER-, [RS-, [PC-, [SP-, [DST-, [DE-, [DX-, [DS-, [DT-, CH-, and bracketed H- followed by numbers. NONE should appear in the delivered report.
2b. **No control-character leakage** - remove form-feed, ANSI escapes, null
bytes, or other non-printable characters copied from shell/tool output.
3. **Cross-references valid** - check the cross-reference map from report_index.md, ensure referenced IDs exist
4. **No duplicate findings** - no two sections describe the same bug
5. **All tier files present** - if any tier file is missing or empty, note it as 'Section pending'

If any quality check fails, fix the issue in the assembled output. Document what was fixed.

### STEP 3: Write Final Report

Write the assembled report to: {PROJECT_ROOT}/AUDIT_REPORT.md

## Output

Write to {PROJECT_ROOT}/AUDIT_REPORT.md

Do NOT write `{SCRATCHPAD}/report_quality.md`. In V2, report quality is a
mechanical Python gate owned by the driver after assembly. The assembler owns
only `{PROJECT_ROOT}/AUDIT_REPORT.md`.

Return: 'DONE: Report assembled - {N} Critical, {N} High, {N} Medium, {N} Low, {N} Info - Quality: {PASS/ISSUES}'
")
```

> **Assembler Model Selection**: If total finding count from report_index.md > 25, use `model="sonnet"` instead of `model="haiku"`. Haiku truncates on large reports (learned from prior audits: 2,669-line report was truncated).
