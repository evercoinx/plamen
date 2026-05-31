# Attention Repair (Thorough Only)

> **Trigger**: Run ONLY when `{SCRATCHPAD}/attention_repair_queue.md` exists.
> **Mode gate**: Thorough mode only.
> **Purpose**: Repair specific queue-driven coverage/attention gaps.
> This is NOT a general analysis pass — only the rows in the queue are in scope.

---

## Inputs

- `{SCRATCHPAD}/attention_repair_queue.md` (the queue — REQUIRED, phase does not run without it)
- The exact source file or artifact named in each queue row
- `function_list.md`, `contract_inventory.md`, `call_graph.md`, and `state_variables.md` ONLY when the queued row needs symbol resolution
- `{SCRATCHPAD}/spec_expectations.md` when present, for context only. It lists
  test/mock/harness files that are specification evidence, not production
  coverage debt.
- `{SCRATCHPAD}/asset_binding_matrix.md` when a queue row has kind
  `asset-binding-gap`.
- `{SCRATCHPAD}/skill_execution_checklist.md` when a queue row has kind
  `skill-execution-gap`.

**Do NOT read** large bulk artifacts (analysis/depth/verify outputs, the full scratchpad). Only read the queue row's named target.

Do NOT re-audit the whole protocol. This phase is scoped to the specific gaps in the queue.

Test, mock, script, fixture, and harness files are not production coverage
obligations. They should not appear as `uncited-security-file` queue rows. If
one appears due to stale queue state, mark it `NO_FINDING`, cite
`spec_expectations.md` or the file path, and do not spend depth-analysis budget
auditing the support file itself. Use support files only to derive expectations
that production code may satisfy or violate.

---

## Output Files

| File | Purpose |
|------|---------|
| `{SCRATCHPAD}/attention_repair_summary.md` | Per-row verdict table (ALWAYS written) |
| `{SCRATCHPAD}/attention_repair_findings.md` | Finding blocks for CONFIRMED issues (written only if findings exist) |

---

## Summary Table Format

For every queue row, write one row to `attention_repair_summary.md`:

| Queue # | Kind | Target | Verdict | Evidence | Notes |
|---------|------|--------|---------|----------|-------|

**Receipt contract**:
- The `Queue #` cell MUST equal the queue row number.
- The `Kind` cell MUST equal the queue row kind.
- The `Target` cell MUST copy the queue row `Target` value exactly, including
  the full relative path. Do not shorten it to a basename or summarize it by
  folder.
- The `Evidence` cell MUST cite the exact target path again with file:line
  evidence when source is available. If the file is unavailable, cite the exact
  target path and explain `NEEDS_HUMAN`.

The validator treats a missing exact target-path receipt as incomplete repair.
Do not return only a prose summary.

---

## Allowed Verdicts

| Verdict | Meaning | Requirements |
|---------|---------|-------------|
| `SAFE` | Reviewed and no issue | Include file:line evidence OR a concrete reason why the row is unreachable |
| `CONFIRMED` | Issue exists | Write a finding block in `attention_repair_findings.md` |
| `NO_FINDING` | Row was stale or already covered | Cite the existing finding/source that already covers this |
| `NEEDS_HUMAN` | Cannot determine mechanically | Only if source is unavailable or semantics depend on deployment data outside the repository |

---

## Finding Format

Confirmed findings MUST use IDs `ATT-1`, `ATT-2`, ... and the following standard fields:

```markdown
### Finding [ATT-1]: title

**Severity**: High/Medium/Low/Informational
**Location**: contracts/path/Contract.sol:L123
**Preferred Tag**: CODE-TRACE
**Evidence Tag**: CODE-TRACE
**Source IDs**: attention_repair_queue.md row N
**Description**: ...
**Impact**: ...
**Evidence**: ...

### Precondition Analysis (if applicable)
**Missing Precondition**: [What blocks this attack]
**Precondition Type**: STATE / ACCESS / TIMING / EXTERNAL / BALANCE
**Why This Blocks**: [Specific reason]

### Postcondition Analysis (if applicable)
**Postconditions Created**: [What conditions this creates]
**Postcondition Types**: [STATE, ACCESS, TIMING, EXTERNAL, BALANCE]
**Who Benefits**: [Who can use these]
```

---

## Repair Priorities by Queue Kind

### NOTREAD or Uncovered Files

Inspect every externally reachable function in the queued file for:
- Access control correctness
- Value flow integrity
- External-call side effects
- Accounting invariants
- Stale reads
- Upgrade/storage layout safety
- Unbounded iteration

### Uncited Security Files

Inspect only the named file and direct callers/callees needed to determine reachability. Do not expand scope beyond what is needed to resolve the queue row.

### Graph/Coverage Rows

Resolve the exact uncertain row and either:
- Confirm it with evidence (file:line + explanation), or
- Mark it SAFE with the missing edge explained

### Asset-Binding Gap Rows

Read only the matching row in `asset_binding_matrix.md`, then inspect the
minimal source path needed to prove whether the field pair is bound before
value moves. If unbound, write a normal `ATT-*` finding. If already covered,
mark `NO_FINDING` and cite the existing finding ID. If the pair is intentionally
irrelevant, mark `SAFE` with the concrete reason.

Asset-binding rows are exact field-pair obligations. Similar topic coverage is
not enough. A valid closure must discuss both queued fields in the same local
claim and state the relationship:

- `CONFIRMED`: both fields exist on a reachable value-moving path and can
  diverge, are not checked against each other, or one can override the other.
- `SAFE`: both fields are explicitly equal/bound, the path is unreachable, or
  the pair is impossible in this protocol shape; cite the source evidence and
  include one enum token:
  `SAFE_REASON:EXPLICIT_EQUALITY`, `SAFE_REASON:EXPLICIT_BINDING_CHECK`,
  `SAFE_REASON:UNREACHABLE_PATH`, or `SAFE_REASON:IMPOSSIBLE_PAIR`.
- `NO_FINDING`: an existing finding already names both queued fields and the
  relationship between them; cite that finding ID.
- `NEEDS_HUMAN`: source or deployment data needed for exact closure is absent.

Do not close a row because a nearby asset, recipient, amount, sender, or branch
issue exists. If the queued pair is `A <-> B`, the row remains open unless your
evidence explains `A` against `B` directly.

For custody/value rows, do not mark SAFE solely because a mismatch would
revert, appears self-punishing, requires a prior balance, or has no obvious
normal accumulation path. Residual custody, failed/refund lifecycle state,
donations, stale approvals, public recovery paths, and later user-controlled
spend paths must be excluded before no-balance reasoning can support SAFE.

### Skill-Execution Gap Rows

Read the matching row in `skill_execution_checklist.md`, then inspect the
specific target files listed in that row. Do not rerun the whole depth phase.
Either produce a narrow `ATT-*` finding for a real missed issue, or mark the row
`NO_FINDING`/`SAFE` with evidence that the missing methodology step is already
covered elsewhere or not applicable.

---

## Scope Containment

SCOPE: Write ONLY to `attention_repair_summary.md` and, if needed, `attention_repair_findings.md`. Do NOT proceed to RAG, chain analysis, verification, or report. Return your findings and stop.

---

## Integration with Pipeline

Attention repair findings (`ATT-*` IDs) are picked up by:
1. The inventory merge step (appended to `findings_inventory.md`)
2. Chain analysis (checked for postcondition/precondition matches)
3. Verification (included in verify queue if Medium+)
4. Report index (assigned report IDs alongside other findings)

The driver handles this integration — the repair agent only needs to write its output files correctly.
