# Plan: V2 Pipeline Robustness + L1 Recall Uplift (Irys postmortem follow-through)

## Context

The pre-fixes Plamen V2 L1 Thorough audit of the Irys Rust node client achieved ~6.8% strict recall / ~17.6% any-match recall against the human reference (74 findings). Root-cause distribution was ~57% RC-METHOD, ~20% RC-DEPTH, ~13% RC-SCOPE, ~8% Issue #5 pipeline, ~2–5% RC-AGENT. Subsequent driver work (empty-verify short-circuit, watchdog scoping, 429/529 envelope parse, cost ledger, manifest-aware breadth quorum, critical halt/resume) hardened operational robustness but left the actual recall drivers — methodology gaps and log-only SCIP enforcement — untouched.

The user wants two guarantees from V2 that V1 never delivered:
1. **Completeness** — no silent phase skipping, no silent agent skipping, no silent early exit.
2. **Coverage** — every class of finding the human auditors saw on Irys either has a methodology that would surface it, a depth directive that forces reasoning through it, or an explicit out-of-scope acknowledgement.

This plan aggregates every postmortem-derived work item, groups them by surface (driver / prompt-gate / methodology / scope), and awaits user decisions on priority and approach before any implementation.

---

## Scope

### A. Driver completeness (V2 robustness — prevent V1's silent-skip failure modes)
- A1. Verify gate AND-semantics regression — `gate_passes` at `plamen_driver.py:756–790` requires ALL listed patterns to match; current verify phase lists both `verify_F_*.md` and `verify_F-*.md`, so emitting either shape alone fails the gate. Fix via single regex-ish pattern `verify_F[_-]*.md` OR an `any_of` list wrapper.
- A2. SCIP pre-baked read enforcement — `commands/plamen-l1.md:428–431` writes `[GATE FAIL]` entries to `violations.md` but the driver does not hard-reject. Irys had 4 depth iter-1 agents skip SCIP and proceed. Options: (a) driver post-phase scan of `violations.md` for `GATE FAIL` → force depth retry; (b) LLM-side hard block; (c) promote to §WRITE-THEN-VERIFY class rule.
- A3. Depth quorum parity — `L1_PHASES` depth phase uses fixed `min_artifacts_count=3`. No equivalent to `parse_breadth_manifest_count`. If depth manifest declares N agents and only 3 write, gate false-passes.
- A4. NEVER-CUT agent enforcement — CLAUDE.md rule 13a lists 7 must-spawn agents for Thorough. Driver has no check. Options: (a) driver reads `spawn_manifest.md` + depth output files, asserts each NEVER-CUT agent has an artifact; (b) LLM self-asserts via a new checkpoint file; (c) keep as LLM-only rule.
- A5. Depth loop early-exit guard — Irys `violations.md` line 12 shows iter-3 skipped via "exit criterion 4 (0 UNCERTAIN Medium+)". This is a legit exit, but there is no driver-side assertion that the reason is in the allowed set. Consider: structured exit-criterion field in depth output + driver validates.
- A6. Report partial-success recovery — no tier-writer retry logic in driver. A truncated tier file currently passes `min_artifact_bytes`. Options: (a) per-tier `report_{critical,high,medium,low,info}.md` gate + stitch on success; (b) accept stub; (c) post-report quality-gate phase.
- A7. Post-phase rate-limit handling — confirmed 429/529 envelope parsing at `plamen_driver.py:447`. Open question: is exponential backoff + resume durable across multi-hour waits (5-min cache TTL penalty)?

### B. V1 failure-mode retirement (what V2 must guarantee)
- B1. Phase skipping under context pressure — addressed by V2's one-phase-per-subprocess architecture. Needs an explicit test: run a Thorough audit and assert every phase in `L1_PHASES` has a completion marker.
- B2. Agent skipping (depth agent merged into one, scanner C dropped, niche agents skipped) — NOT addressed. Depends on A4.
- B3. Early report before verification — partially addressed by phase order + PHASE 5 COMPLETION ASSERTION. No driver-side check that `skeptic_findings.md` exists before `report` runs.
- B4. Mid-audit compaction — V2 subprocess boundaries eliminate this within a phase. Cross-phase context is in `pipeline_checkpoint.md`. Need smoke test.

### C. Coverage uplift — methodology (RC-METHOD, ~57% of misses)
Seven new L1 skills identified in postmortem:
- C1. `peer_scoring_correctness` — scoring drift, selective peer banning, scoring-reset races.
- C2. `gossip_cache_invariance` — duplicate-message cache invariants, cache eviction races, equivocation through cache.
- C3. `consensus_tx_identity_invariants` — tx ID collisions, nonce/replay-protection asymmetry between consensus layer vs execution layer.
- C4. Broader merkle-proof trigger — current `light-client-proof-verification` trigger is too narrow; should match any in-tree merkle root/path verification, not just IBC/ICS23.
- C5. Header-field coverage matrix — always-on depth directive listing every header field and asking "is this field validated against every adversarial value (zero, MAX, mismatched against parent, etc.)?"
- C6. Boundary-coverage checklist — always-on depth directive: for every numeric state field, enumerate 0, 1, MAX, type-boundary, empty-container with concrete substitution.
- C7. C/CUDA language lane — Irys has a C/CUDA mining validator that was silently out-of-scope; Rust-only routing missed it.

### D. Coverage uplift — depth directives (RC-DEPTH, ~20% of misses)
- D1. Same as C5/C6 if implemented as always-on depth directives.
- D2. Devil's Advocate iter-2 strengthening — Irys had 0 iter-2 UNCERTAIN Medium+, which auto-exited. Need a minimum-exploration assertion.

### E. Coverage uplift — scope (RC-SCOPE, ~13% of misses)
- E1. Language-lane routing — recon detects C/CUDA files → emits `LANGUAGE_LANES=rust,c,cuda` → driver spawns additional depth-edge-case/depth-external runs scoped to each non-primary language.
- E2. Silent file exclusions — recon should surface files NOT in any agent's scope as a `scope_leftover.md` artifact.

### F. Post-audit hygiene
- F1. The Irys postmortem was done in-session. RC-AGENT Exclusion Test (Step 2.5 of post-audit-improvement-protocol.md) was NOT formally applied per miss. Any methodology changes (C/D) above should pass the exclusion test per item before implementation.
- F2. MEMORY.md entry for v1.2 after this round of changes, per protocol.

---

## Open questions (to ask the user)

Asked in AskUserQuestion groups below.

---

## Decisions (locked via AskUserQuestion rounds)

### Round 1 (priority + first-4 mechanisms)
- **D1 — Priority ordering**: **Parallel track.** A1-A6 driver bug fixes run alongside the 3-4 highest-impact L1 skills. C/CUDA lane (C7/E1) and exotic skills deferred to a later plan.
- **D2 — Verify gate (A1)**: Add `any_of: list[list[str]]` field to the Phase dataclass. Each inner list is an OR-group; all outer groups must match (AND across groups, OR within group). Verify phase becomes `any_of=[["verify_F_*.md", "verify_F-*.md"]]` and removes the current conflicting `expected_artifacts` entries. `gate_passes` adds OR-group evaluation alongside the existing AND-evaluation over `expected_artifacts`.
- **D3 — SCIP gate (A2)**: **Both LLM halt and driver verify.** Rewrite `plamen-l1.md:428-431` so the agent writes a `[HALT]` sentinel line to its output file (plus existing `[GATE FAIL]` to violations.md) when pre-baked reads < 2. Driver adds a post-depth-phase scan that (a) rejects any depth output containing `[HALT]` and (b) greps violations.md for `[GATE FAIL].*pre-baked reads`. Either signal triggers a single retry with a prompt addendum listing the exact pre-baked artifact paths. Second failure degrades the phase to failed status and continues.
- **D4 — NEVER-CUT (A4)**: **Driver manifest + LLM self-assert, both enforced.** (a) LLM writes `never_cut_checkpoint.md` at end of each depth iter-1 listing every NEVER-CUT agent category with `SPAWNED` / `SKIPPED: {reason}`. (b) Driver maintains a hardcoded L1 NEVER-CUT list (4 depth + 3 scanners + required niche + Validation Sweep + DST + Confidence scoring + RAG Sweep, scoped to L1 applicability) and asserts each has an output artifact. Either check failing triggers a retry; second failure halts the pipeline with the violation surfaced to the user.

### Round 2 (skill selection + remaining gates)
- **D5 — Skill selection**: User direction: *"All of them if possible, if not only the ones causing C/H misses. Do not overfit to Irys, research if they would be common."* Applying the generalizability test to each candidate:
  - **C1 peer_scoring_correctness**: universal on L1s (reth/prysm/lighthouse/geth/cometbft all implement peer scoring; multiple historical CVEs in devp2p, prysm, cometbft). **INCLUDE**.
  - **C2 gossip_cache_invariance**: universal (libp2p gossipsub, Ethereum attestation cache, tx cache in every execution client; multiple historical gossipsub CVEs). **INCLUDE**.
  - **C3 consensus_tx_identity_invariants**: common on any chain with separated consensus/execution layers (ETH 2.0, Cosmos, Polkadot). Narrower than C1/C2 but still broadly applicable. **INCLUDE**.
  - **C4 broader merkle-proof trigger**: trigger-fix (~2 lines). **AUTO-INCLUDE** (no methodology gate needed).
  - **C5 header-field coverage matrix**: universal — every L1 has block headers with fields validated in isolation. **INCLUDE** as always-on depth directive in `depth-consensus-invariant`.
  - **C6 boundary-coverage checklist**: universal — applies to all L1 code with numeric state. **INCLUDE** as always-on depth directive appended to all L1 depth agents (consensus-invariant, network-surface, state-trace, external, edge-case).
  - **C7 C/CUDA language lane**: Irys-specific per Round-1 priority decision. **DEFER** to a later plan.
  - Decision: include **C1, C2, C3, C5, C6** as skills, **C4** as trigger-fix auto-include.
- **D6 — Depth quorum (A3)**: **Manifest-aware + hardcoded NEVER-CUT floor.** Add `parse_depth_manifest_count` mirroring `parse_breadth_manifest_count`. Depth phase gate requires ALL manifest-declared agents to produce artifacts AND the NEVER-CUT floor from D4 to be satisfied. Overlap is intentional: manifest catches declared-but-unspawned, NEVER-CUT catches never-declared.
- **D7 — Report recovery (A6)**: **Per-tier gate + stitch on success.** Split the report phase into `report_critical_high`, `report_medium`, `report_low_info` sub-phases, each with its own gate (per-finding section ≥400 chars per phase6-report-prompts.md rule 7) + retry. `report_assemble` runs after all 3 pass. Existing `report_index` phase remains upstream of the tier phases.
- **D8 — Early-exit guard (A5)**: **Both structured exit field AND DA minimum-exploration.** (a) Depth phase writes `depth_exit.md` with `criterion: {1|2|3|4}` + `rationale: …` + `explored_paths: [list]`. Driver validates criterion is in allowed set, rationale is non-empty, and explored_paths has ≥N entries for iter-2 exits. (b) DA iter-2 prompt addendum requires the agent to enumerate explored paths (per AD-2 point 4) before being allowed to return a "no new findings" result. Invalid exit file = retry; second invalid = halt.

### Round 3 (rate-limit durability, scope, retroactive gate, verification)
- **D9 — Rate-limit durability (A7)**: **Hibernation checkpoint.** If backoff wait would exceed 5 minutes (prompt-cache TTL), the driver writes `{SCRATCHPAD}/.hibernating` with wake-at timestamp + last-successful-phase, then exits cleanly with a non-zero code indicating hibernation. On next `plamen_driver.py` invocation, the driver detects the marker, validates the last-successful-phase against `_v2_checkpoint.json`, and resumes. Under 5min → keep current in-process backoff. Rationale: long waits burn cache and risk subprocess death; exiting cleanly hands control back to the user/cron and avoids paying for stale-cache retries.
- **D10 — scope_leftover (E2)**: **Recon emits `scope_leftover.md`.** After Recon Agent 3 (manifest composer) finalizes agent scopes, it enumerates every in-scope file (from `contract_inventory.md`) that is NOT referenced by any declared agent scope and writes them to `scope_leftover.md` with a reason tag (language-mismatch / path-excluded / no-agent-covers). Driver post-recon gate: if any file with Medium+ LOC (>200) appears in leftover without an explicit `ACKNOWLEDGED: <reason>` line, fail recon and retry with an addendum listing the uncovered files. Second failure halts with user message.
- **D11 — RC-AGENT retroactive test (F1)**: **Require Step 2.5 exclusion test per new skill.** Before each of C1/C2/C3/C5/C6 is written, the implementer runs the 3-question RC-AGENT Exclusion Test (methodology search / reasoning trace / gap proof) against the specific Irys misses the skill claims to cover. Results recorded ephemerally in the implementation session, not stored. If the test fails (any answer NO → RC-AGENT), the skill is dropped, not written. This respects the anti-bloat memory and guards against overfitting to agent reasoning errors.
- **D12 — Verification (new cost axis added)**: **End-to-end Irys re-run + cost-reduction verification.** User explicitly flagged: Opus 4.7 audit consumed 205%+ weekly + 3+ hourly sessions on Max×20 plan vs Opus 4.6 at 6-8% weekly + ~50% hourly session — a ~25-30× cost regression that is unacceptable. Cost reduction is therefore a FIRST-CLASS success criterion alongside recall. Two axes:
  1. **Recall delta**: re-run Irys Thorough at the same commit (`f0038bb8`); target > 20% strict recall (up from 6.8%), > 35% any-match recall (up from 17.6%).
  2. **Cost delta**: weekly-plan % and per-hourly-session % on Max×20 must drop to < 20% weekly and < 100% of one hourly session. Driver's cost ledger (already in place per summary) provides the measurement.
  No synthetic/smoke tests required up-front — the Irys re-run is the single integration test. If cost target is missed, plan a follow-up to audit the driver's subprocess-spawn patterns.

---

## Implementation plan

**Cost discipline overlay.** Every change below is gated on a cost-impact question: "does this add subprocess spawns, retries, or token consumption?" Where a retry budget is specified, it is bounded (single retry, then degrade) to prevent the 25-30× regression observed in the Irys run.

### Phase 1 — Driver changes (`~/.claude/scripts\plamen_driver.py`)

All changes are additive to the existing ~1416-line driver. Line anchors are approximate and will need to be re-verified at implementation time (memory is 11 days old).

1. **A1 / D2 — `any_of` verify gate**
   - Phase dataclass: add `any_of: list[list[str]] = field(default_factory=list)` alongside existing `expected_artifacts`.
   - `gate_passes()` (~line 756): after the existing AND-evaluation over `expected_artifacts`, iterate `any_of` outer groups and require at least one glob match within each inner list. All outer groups must satisfy (AND); within an outer group, matching is OR.
   - `L1_PHASES` verify phase: replace conflicting `verify_F_*.md`/`verify_F-*.md` entries with `any_of=[["verify_F_*.md", "verify_F-*.md"]]`. Keep existing `verify_NONE.md` empty-verify sentinel logic unchanged.

2. **A2 / D3 — SCIP pre-baked read enforcement**
   - Add `_scan_for_halt_and_gatefail(scratchpad: Path) -> list[str]`: greps `depth_*.md` for `^\[HALT\]` and `violations.md` for `\[GATE FAIL\].*pre-baked reads`. Returns list of offending agent names.
   - Depth-phase post-run hook: call scanner; if non-empty on iter 1, re-spawn only the offending depth agents with a prompt addendum listing the exact pre-baked artifact paths (`contract_inventory.md`, `subsystem_map.md`, `state_variables.md`, `function_list.md`, `build_status.md`). Second failure → log to `violations.md` and degrade the phase (do NOT halt — partial depth data is still useful for inventory + report).

3. **A3 / D6 — Depth quorum parity**
   - Implement `parse_depth_manifest_count(scratchpad: Path) -> int` mirroring `parse_breadth_manifest_count`. Reads `phase4b_manifest.md` (or `spawn_manifest.md`, whichever exists) and counts declared depth agents.
   - Replace the fixed `min_artifacts_count=3` on the depth phase with `min_artifacts_count=parse_depth_manifest_count(scratchpad)`.
   - Combine with the NEVER-CUT assertion (step 4) — both must pass.

4. **A4 / D4 — NEVER-CUT hardcoded floor + LLM self-assert**
   - Add module-level constant `L1_NEVER_CUT = ["depth-consensus-invariant", "depth-network-surface", "depth-state-trace", "depth-external", "depth-edge-case", "scanner-a", "scanner-b", "scanner-c", "validation-sweep", "design-stress", "rag-sweep"]`. (Confidence-scoring is a haiku agent and runs post-depth, out of scope for the depth gate.)
   - New `_assert_never_cut_artifacts(scratchpad)`: for each name in `L1_NEVER_CUT`, glob for a matching output file (e.g., `depth_consensus_invariant_findings.md`). Missing → retry once with addendum; second failure → halt.
   - New `_assert_never_cut_checkpoint(scratchpad)`: read `never_cut_checkpoint.md` (written by LLM per plamen-l1.md change below), fail if any SPAWNED line is missing or any SKIPPED reason is not whitelisted.

5. **A5 / D8 — Early-exit guard**
   - New `_validate_depth_exit(scratchpad, iteration)`: reads `depth_exit.md`, asserts `criterion ∈ {1,2,3,4}`, `rationale` non-empty, and for iter-2 exits `len(explored_paths) >= 3` (echoes AD-2 point 4 minimum-exploration rule). Invalid → retry with addendum; second invalid → halt.

6. **A6 / D7 — Report partial-success recovery**
   - Split the single `report` phase in `L1_PHASES` into 4 sub-phases:
     - `report_index` (unchanged upstream)
     - `report_critical_high` — `expected_artifacts=["report_critical_high.md"]`, `min_artifact_bytes=` computed from `report_index.md` C+H count × 400 chars floor.
     - `report_medium` — `expected_artifacts=["report_medium.md"]`, same per-section floor.
     - `report_low_info` — `expected_artifacts=["report_low_info.md"]`, same.
     - `report_assemble` — `expected_artifacts=["AUDIT_REPORT.md"]`, depends on all three tier files.
   - Each tier sub-phase has a single retry on under-size; second failure writes a `STUB` marker in that tier's file and continues to assemble (partial report > no report).

7. **A7 / D9 — Hibernation checkpoint**
   - Modify rate-limit handling (~line 447): when computed backoff > 300 seconds, write `{SCRATCHPAD}/.hibernating` (JSON: `{"wake_at_utc": "...", "last_phase": "...", "attempt_count": N}`), call `sys.exit(42)` (reserved hibernation code).
   - Add startup hook: if `.hibernating` exists and `wake_at_utc` is past, delete marker and resume from `_v2_checkpoint.json`. If `wake_at_utc` is still in the future, print wait time and exit.
   - Document new exit code 42 in driver docstring.

### Phase 2 — Prompt changes (`~/.claude/commands\plamen-l1.md`)

1. **§SCIP-PREBAKE (~line 167-177 & 428-431)**: Add hard halt instruction: *"If you have performed < 2 pre-baked reads before beginning analysis, write `[HALT] pre-baked reads insufficient` as the first line of your output file, write `[GATE FAIL] pre-baked reads` to `violations.md`, and stop immediately. Do NOT proceed with analysis — the driver will re-spawn you with explicit read instructions."*

2. **Depth iter-1 closing checklist**: add requirement to write `never_cut_checkpoint.md` listing every NEVER-CUT agent category with `SPAWNED: <path>` or `SKIPPED: <whitelisted reason>`. Whitelisted skip reasons: `NO_APPLICABLE_FLAG` (niche only), `LANGUAGE_LANE_NOT_DETECTED` (C7-class, deferred), `EMPTY_SCOPE_AFTER_MANIFEST`.

3. **Depth iter-1 and iter-2 closing checklist**: require `depth_exit.md` with `criterion:`, `rationale:`, `explored_paths:` (YAML list). Iter-2 exits listing "no new findings" must include at least 3 explored-path entries per AD-2 point 4.

4. **DA iter-2 prompt addendum**: incorporate the minimum-exploration assertion into the agent prompt (in addition to the existing AD-2 directive in `phase4-confidence-scoring.md`). Make it explicit: *"You may not return 'no new findings' unless `explored_paths` contains at least 3 distinct paths not covered by iter-1."*

5. **Recon Agent 3 (manifest composer)**: add `scope_leftover.md` output requirement. After scope assignment, enumerate files from `contract_inventory.md` not covered by any declared scope. Output format:
   ```
   | File | LOC | Reason | Acknowledged |
   |------|-----|--------|--------------|
   ```
   Files with LOC > 200 need `ACKNOWLEDGED: <reason>` or the driver retries.

### Phase 3 — New L1 skill files (under `~/.claude/agents\skills\injectable\l1\`)

For each of C1, C2, C3: implementer FIRST runs the RC-AGENT Exclusion Test (D11) against the Irys misses the skill claims to cover. Only write the file if the test passes. Target size: ~80-120 lines each (per injectable skill size cap of 300).

1. **`peer_scoring_correctness/SKILL.md`** (C1)
   - Trigger: `P2P` flag AND detection of `score_peer`, `peer_score`, `ban_peer`, `reputation`, `misbehavior` patterns.
   - Inject into: `depth-network-surface`.
   - Methodology: scoring drift (reward/penalty asymmetry), selective peer banning (adversary chooses whom to ban), scoring-reset races (peer re-connects and scoring state is wiped), score monotonicity under concurrent updates.

2. **`gossip_cache_invariance/SKILL.md`** (C2)
   - Trigger: `P2P` flag AND detection of `seen_cache`, `message_cache`, `tx_cache`, `gossipsub`, `pubsub` patterns.
   - Inject into: `depth-network-surface`, `depth-consensus-invariant`.
   - Methodology: cache TTL vs message lifetime, eviction races under burst load, equivocation bypass via cache eviction, cache key collisions, memory pressure DoS via forced eviction.

3. **`consensus_tx_identity_invariants/SKILL.md`** (C3)
   - Trigger: detection of separated consensus/execution layers (e.g., consensus-layer tx envelope + execution-layer tx body) OR `tx_hash`, `txid`, `nonce` usage across module boundaries.
   - Inject into: `depth-consensus-invariant`, `depth-state-trace`.
   - Methodology: tx identity (hash/id) determinism across layers, replay protection asymmetry, nonce window drift, tx-pool eviction vs finality mismatch.

4. **C4 — broader merkle-proof trigger (trigger-fix only)**: edit `~/.claude/agents/skills/injectable/l1/light_client_proof_verification/SKILL.md` trigger pattern from the narrow IBC/ICS-23 regex to a broader match covering any `verify_proof`, `merkle_proof`, `verify_root`, `state_root`, `trie_proof`. No RC-AGENT test needed (trigger-fix, not new methodology).

### Phase 4 — Depth template / agent definition updates

1. **C5 header-field coverage matrix** — append to `~/.claude/agents\depth-consensus-invariant.md` as an always-on methodology section. Agent must, for every block-header field in scope, build a table of (field, type-domain, validation-sites, adversarial-values-considered, outcome).

2. **C6 boundary-coverage checklist** — append to all five L1 depth agent definition files (`depth-consensus-invariant.md`, `depth-network-surface.md`, `depth-state-trace.md`, `depth-external.md`, `depth-edge-case.md`) as a shared always-on section. Agent must, for every numeric state field touched, enumerate {0, 1, type-MAX, type-boundary-straddle, empty-container} with concrete substitution and outcome.

### Phase 5 — Registry updates

1. **`~/.claude/rules/skill-index.md`** L1 section: add 3 new rows for C1/C2/C3 with their trigger flags and inject targets.

2. **No CLAUDE.md changes.** Rule 13a already cites NEVER-CUT categories; driver change (A4) mechanically enforces what the rule already names.

### Cost-reduction considerations applied throughout

- Every retry in A2/A4/A5 is bounded to **single retry, then degrade** — no exponential cascades.
- A7 hibernation prevents burning tokens on stale-cache retries across multi-hour waits (the primary mechanism expected to deliver the cost reduction).
- Report tier splits (A6) trade 1 report agent for 4 sub-phases, but each is short and individually gated — net cost is comparable because the single agent currently truncates and retries implicitly.
- NEW skills (C1/C2/C3) add prompt tokens to `depth-network-surface` and `depth-consensus-invariant` only; ~80-120 lines each × 3 skills = ~300 extra lines total per depth subprocess. Acceptable vs the recall uplift.
- C5/C6 always-on depth directives live in agent-definition files which are loaded per-subprocess anyway; marginal cost is ~40-60 lines.

---

## Verification

Single integration test: re-run Irys Thorough at commit `f0038bb8a0230b64f175a7eb40d11ff160cd51d3` and measure two axes.

### Axis 1 — Recall
- **Baseline**: 6.8% strict recall / 17.6% any-match recall against 74 human findings (pre-fixes).
- **Target**: ≥ 20% strict / ≥ 35% any-match.
- **Method**: same alignment matrix approach used in the original postmortem (in-session, ephemeral per post-audit-improvement-protocol). The human reference report is the oracle.

### Axis 2 — Cost (first-class, user-flagged)
- **Baseline (regression we're correcting)**: Opus 4.7 Irys run = 205%+ of weekly Max×20 plan + 3+ hourly sessions per audit.
- **Reference**: Opus 4.6 equivalent audits = 6-8% weekly + ~50% of one hourly session.
- **Target**: < 20% weekly + < 100% of one hourly session (i.e., 10× cost reduction minimum; 25-30× regression at least halfway recovered).
- **Method**: driver cost ledger (already in place) records per-phase subprocess count and token usage. Post-run, sum weekly-plan% and compare to baselines.
- **Failure mode**: if cost target is missed, the subprocess spawn pattern is the likely culprit — open a follow-up plan to audit rate-limit retries, report-tier splits, and NEVER-CUT retries for unnecessary churn.

### Per-gate failure-injection (optional, low-priority)
If the Irys re-run fails to isolate the cause of any regression, injectable tests can be built after the fact:
- Empty depth output → A3/A4 gate fails → retry-once-degrade path.
- `[HALT]` line in `depth_*.md` → A2 scan fires → re-spawn with addendum.
- Truncated tier file → A6 per-tier gate fails → stub marker.
- `wake_at_utc` 10 minutes ahead → A7 hibernation → clean exit code 42.

These are NOT required pre-implementation — the Irys re-run is the acceptance gate.

### Post-verification hygiene (F2)
If recall + cost targets are met, append a one-line MEMORY.md entry per post-audit-improvement-protocol:
*"Pipeline v1.2.0 (YYYY-MM-DD) — Irys re-run recall {X}% strict / {Y}% any-match, cost {Z}% weekly. {N}×RC-METHOD fixes (C1/C2/C3/C5/C6), {M}×RC-AGENT reclassifications, {K}×driver-completeness fixes (A1-A7)."*

### Critical files touched (reference)
- `~/.claude/scripts\plamen_driver.py`
- `~/.claude/commands\plamen-l1.md`
- `~/.claude/agents\depth-consensus-invariant.md`
- `~/.claude/agents\depth-network-surface.md`
- `~/.claude/agents\depth-state-trace.md`
- `~/.claude/agents\depth-external.md`
- `~/.claude/agents\depth-edge-case.md`
- `~/.claude/agents\skills\injectable\l1\peer_scoring_correctness\SKILL.md` (NEW)
- `~/.claude/agents\skills\injectable\l1\gossip_cache_invariance\SKILL.md` (NEW)
- `~/.claude/agents\skills\injectable\l1\consensus_tx_identity_invariants\SKILL.md` (NEW)
- `~/.claude/agents\skills\injectable\l1\light_client_proof_verification\SKILL.md` (trigger edit)
- `~/.claude/rules\skill-index.md`

All file paths will be re-verified against the current filesystem at implementation time (memory is 11 days old per auto-memory staleness reminder).
