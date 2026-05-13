# Phase 3b: Additional Breadth Pass (Thorough Mode Only)

> **Loaded by**: The V2 driver's `rescan` subprocess.
> **Mode gate**: Thorough mode ONLY. Skip in Light and Core mode.
> **Purpose**: Counter attention saturation after first-pass breadth by running
> a smaller, independent breadth pass over under-explored surfaces.

---

## V2 Phase Position

This phase runs after first-pass breadth and before inventory. Inventory has
not run yet.

The later inventory phase consumes:
- first-pass breadth outputs, and
- this phase's additional analysis outputs.

This subprocess does not merge findings or produce inventory artifacts.

---

## Inputs

Read first-pass breadth outputs matching `{SCRATCHPAD}/analysis_*.md` and
the recon artifacts needed to assign under-explored surfaces. Treat existing
first-pass findings as an exclusion set:

- Do not report the same root cause.
- Do not report the same location with only a renamed title.
- Prefer vulnerability classes and files that are weakly represented in the
  first-pass breadth outputs.

On retry, ignore this phase's own prior outputs when building the first-pass
exclusion set; use them only to determine which additional outputs are already
substantial and can be skipped.

---

## Work Plan

Spawn 2-3 additional breadth agents in bounded parallel calls. Each agent gets
one broad non-overlapping or intentionally cross-checking scope, depending on
the gaps visible from first-pass breadth and recon artifacts.

Focus areas that usually reveal attention-saturation misses:

1. Cross-function state inconsistencies.
2. Asymmetric operations.
3. Parameter encoding mismatches between paired functions.
4. Economic assumptions violated under edge conditions.
5. Time-dependent state that goes stale under specific operation sequences.

In Thorough mode, a focused per-contract/cluster pass is mandatory. Keep it in
this same subprocess unless the phase graph launches a separate per-contract
producer. Write it under this phase's owned additional-analysis output family.

---

## Output Contract

Write only this phase's additional analysis files:

- `analysis_rescan_*.md`
- at least one `analysis_percontract_*.md`

Each additional agent writes exactly one output file. If no meaningful
per-contract cluster exists, still write
`analysis_percontract_scope_review.md` with a substantive explanation of why
clustered per-contract analysis is not applicable and what files were checked.
When the files are written, return and stop. The Python phase graph routes all
later phases.

Do not create any artifact outside this output contract.
