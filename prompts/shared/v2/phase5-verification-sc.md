# Phase 5: SC Verification - Isolated Contracts

> **Purpose**: Shared source file for SC verification phases. The V2 prompt
> builder selects exactly one contract below for each phase:
> `sc_verify_queue`, one `sc_verify_*` shard, or `sc_verify_aggregate`.
>
> This file intentionally contains no child-agent spawn template.

---

## SC Verify Queue Contract

Build the verification queue only.

### Inputs

- `{SCRATCHPAD}/findings_inventory.md`
- `{SCRATCHPAD}/chain_hypotheses.md` if present
- `{SCRATCHPAD}/hypotheses.md` if present

### Method

1. Read only the inputs above and route findings that require Phase 5
   verification into `{SCRATCHPAD}/verification_queue.md`.
2. Preserve finding IDs, severity, location, primary artifact, and concise
   verification objective for each queued row.
3. Do not verify findings in this phase.
4. Do not spawn verifier shards, skeptic-judge agents, cross-batch agents, or
   report agents.

### Output

Write ONLY `{SCRATCHPAD}/verification_queue.md`.

SCOPE: Write ONLY to your assigned output file. Do NOT read or write other
agents' output files. Do NOT proceed to subsequent pipeline phases. Return
your findings and stop.

---

## SC Verify Shard Contract

Verify only the rows assigned to this shard by the driver's
`VERIFY COST OVERRIDE (SC SHARD)` block.

### Mandatory Reads

For each assigned row, read only:

- the row in this shard's manifest
- `~/.claude/rules/phase5-poc-execution.md`
- the exact source file(s) at the row's `Location`
- the one `Primary Artifact` named in the queue row

Do NOT open legacy language-specific Phase 5 prompt files from inside a shard.
Those legacy files contain skeptic, cross-batch, aggregate, and report
instructions. This standalone shard contract plus `phase5-poc-execution.md` is
the complete verifier methodology for V2.

### Method

1. Do not spawn subagents. The current verifier shard processes assigned rows
   directly.
2. Verify only finding IDs present in the shard manifest named by the Python
   override.
3. Process rows sequentially. Write each row's verifier file before moving to
   the next row.
   On resume/retry, if the row's exact `verify_<ID>.md` file already exists
   and contains `Severity:`, `Evidence Tag:`, and `Verdict:`, count that row as
   complete and skip to the next row. Do not rewrite completed verifier files
   just because the previous shard run was interrupted.
4. Prove or refute the claimed harm using the language-specific methodology
   and PoC execution protocol.
5. Do not bulk-read unrelated `verify_*.md`, unrelated depth artifacts, or the
   whole scratchpad.

### PoC Testability Triage (Mandatory)

Before accepting `[CODE-TRACE]`, classify the row from the shard manifest:

- `PoC Class = unit` or `property`: attempt an executable PoC/test first.
  In a Foundry/Hardhat/cargo/Move/Soroban project, "no test written" is not a
  valid result for a testable row. Write the smallest harm-assertion test that
  targets the queued Location and run it.
- `PoC Class = structural` or `integration`: executable proof is still
  preferred, but `[CODE-TRACE]` is acceptable only when you document why no
  meaningful local harness exists.
- Critical/High rows with `unit` or `property` class MUST NOT be marked
  `CONFIRMED` on `[CODE-TRACE]` alone. If execution cannot be completed, mark
  `CONTESTED` and document the blocker.

Every verifier file MUST include this ledger:

```markdown
### PoC Attempt
- PoC Required: YES/NO
- PoC Class: <unit|property|integration|structural>
- Attempted: YES/NO
- PoC Not Attempted Because: <NO_BUILD_ENVIRONMENT|EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS|DEPLOYMENT_ONLY_REQUIRES_LIVE_EXTERNAL|PURE_SPEC_OR_DOCS_ONLY|STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION|N/A>
- Test File: <path or N/A>
- Command: <command or N/A>

### Execution Result
- Compiled: YES/NO (attempts: N)
- Result: PASS / FAIL / REVERT / NOT_EXECUTED
- Output: <relevant output or compiler/runtime blocker>
```

For `unit` and `property` rows, `Compiled: N/A`, `Result: N/A`, and "no test
written" are invalid unless `PoC Not Attempted Because` names a real
environmental blocker. Do not use `STRUCTURAL_NO_EXECUTABLE_HARM_ASSERTION`
for a `unit` or `property` queue row; that means the queue classification must
be challenged, not silently bypassed.

### Output

For each assigned row, write ONLY that row's verifier artifact:
`{SCRATCHPAD}/verify_<ID>.md`.

Every verifier file MUST include:

- `Severity:`
- `Evidence Tag:` with one of `[POC-PASS]`, `[POC-FAIL]`, `[CODE-TRACE]`,
  `[MEDUSA-PASS]`
- `Verdict:`

Return one compact line per row:

`<ID>: <VERDICT> | <EVIDENCE_TAG> | <1-sentence justification>`

SCOPE: Write ONLY the `verify_<ID>.md` files for IDs assigned in this shard's
manifest. Do NOT read or write other verifier shards' files. Do NOT write
skeptic, cross-batch, aggregate, report, or later-phase artifacts. Return your
findings and stop.

---

## SC Verify Aggregate Contract

Aggregate existing verifier outputs only.

### Inputs

- `{SCRATCHPAD}/verification_queue.md`
- `{SCRATCHPAD}/verify_*.md`

### Method

1. Read existing per-finding verifier files and the verification queue.
2. Confirm each required queued ID has a corresponding verifier file or record
   it as missing in the aggregate.
3. Summarize verdicts, evidence tags, and any malformed verifier records.
4. Do not open source files unless a verifier file is malformed and blocks
   aggregation.
5. Do not spawn new per-finding verifiers.

### Output

Write ONLY `{SCRATCHPAD}/verify_core.md`.

SCOPE: Write ONLY `{SCRATCHPAD}/verify_core.md`. You MAY read upstream
`verify_*.md` files and `verification_queue.md` to aggregate them, but you MUST
NOT modify those inputs or create new per-finding verifier files. Do NOT proceed
to skeptic, cross-batch, report, or any later phase. Return and stop.
