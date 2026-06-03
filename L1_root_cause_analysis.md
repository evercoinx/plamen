# L1 Audit Pipeline — Root Cause Analysis

**Investigation date:** 2026-06-03
**Branch:** `experimental/pty-supervision`
**Subjects:** two completed L1 Thorough Irys runs (Claude PTY backend, 58 findings, NOT degraded; Codex backend, 11 findings, DEGRADED on `report_assemble`)
**Hypothesis under test:** *"We cannot get L1 to work properly; before PTY it was perfect."*
**Mode:** READ-ONLY forensic. No pipeline source was modified.

---

## 1. Verdict on "before PTY it was perfect"

**Nostalgia, with one true splinter.** The claim is *not* supported by the git archaeology or the two scratchpads. The PTY/marker supervision regime introduced **exactly one** genuine L1 regression — and it is already patched. Everything else the user experiences as "L1 doesn't work" is either (a) pre-existing mechanical/prompt bugs that predate the PTY commit (8c90ae9, 2026-05-26) by ~2 weeks and would fire identically on the old phase-coordinator design, or (b) intrinsic single-pass behavior of the Codex backend, which structurally *never enters the PTY worker pool* and so cannot be a victim of PTY supervision.

The one true splinter: `_classify_artifact_row` was introduced *by* the PTY commit (`git log -S "_BREADTH_STATUS_IN_PROGRESS"` returns only 8c90ae9) and encoded "unmarked artifact on a fresh audit ⇒ a worker is still mid-write ⇒ IN_PROGRESS." That is a correct Claude-PTY assumption applied unconditionally to all backends. Codex writes one returned subprocess per phase and never writes the `<!-- PLAMEN_STATUS: COMPLETE -->` marker, so its *final, substantive* depth artifacts were misread as IN_PROGRESS → manifest-exact-incomplete → false-fail. This hit L1 **Thorough** specifically (the manifest-exact-with-marker gate; Light/Core dodged it via a count-based gate). It is fixed in commit 82664ff (backend-aware `_read_cli_backend_from_config`), and the fix is recall-safe (missing/stub/explicit-IN_PROGRESS still block both backends).

Counter-evidence that the regime *helps* recall, not hurts it: on the Claude run, the marker/structural gate caught a genuinely empty `depth_consensus_invariant_findings.md` and a `TODO:`-poisoned `depth_network_surface_findings.md` on attempt 1, then re-spawned *only those 2 rows* (not the whole phase), producing 23.5KB/3-finding and 29KB/4-finding real artifacts that propagated downstream. The pre-PTY compacting coordinator had no per-artifact disk gate and would have silently accepted the empty file (the worker even self-reported `DONE ... complete` while the file was empty). So for the Claude path the PTY regime is a recall improvement.

The user's pain is real but mostly **misattributed**: the Codex DEGRADE (H-01/INV-004 UNRESOLVED-untagged), the wasted attempt-1 retries, the 58→~28 duplication, and the all-zero Components-Audited table are all non-PTY defects. PTY did not cause them and predates two of them.

---

## 2. Confirmed-issue buckets

### 2a. PTY regressions (attribution-confirmed only)

| ID | Issue | Status | Recall-safe |
|----|-------|--------|-------------|
| A-1 / E-1 | Marker assumption (`unmarked-on-fresh-audit ⇒ IN_PROGRESS`) false-failed Codex L1 Thorough depth. Introduced by 8c90ae9, applied unconditionally to all backends. | **FIXED (82664ff)** — backend-aware classification | yes |

Note: A-2 / E-1 are filed on Axis A/E but their *conclusion* refutes the user hypothesis (the Claude PTY path is sound). The attribution verdict on A-2 set `attribution_holds=false` for the blame-PTY claim; the only finding whose PTY attribution actually *holds* is A-1/E-1. **B3 was REFUTED** by the attribution pass (corrected to PREEXISTING) — see 2c.

So the pty_regressions bucket has **exactly one member, already patched**.

### 2b. Inherent backend behavior (not a code bug)

| ID | Issue | Why intrinsic |
|----|-------|---------------|
| A-6 / B2 / E-4 | Codex single-subprocess under-covers / under-cites on attempt 1 (recon path-citation, breadth no-op, PoC ledger), recovers on the retry-once policy. Codex bypasses the worker pool (`backend=='codex'` → `_translate_prompt_for_codex` at driver:9396, before the `is_claude_pty` branches), so it self-orchestrates fan-out and frequently "plans then exits." | Property of codex `exec` single-turn execution; the literal *opposite* of a PTY regression. |
| B2 | Codex pays a full verify-shard re-run for PoC churn; the one-shot targeted repair only matches the Claude-direction failure (`Attempted:YES`-without-TestFile), not the Codex-direction (`Attempted:NO`-without-blocker). | Repair-eligibility asymmetry, not a correctness bug. |
| C-3 / E-7 | Cross-backend coverage divergence: Claude (PTY per-layer pool) goes deep/networking; Codex (3 self-chosen coarse buckets) goes broad/shallow. | Model + decomposition divergence. Compounded by config (see A-3). |

### 2c. Pre-existing gaps (predate PTY / unrelated to it)

| ID | Issue | Evidence it predates PTY |
|----|-------|--------------------------|
| C-1 / A-4 / E-3 | Mechanical `report_index` builder derives UNRESOLVED **only** from verifier text + judge **DOWNGRADE** map; never calls `_collect_judge_unresolved_ids`. Judge UNRESOLVED over a CONFIRMED verifier (INV-004) → no demote, no Trust-Adj, no body flag → `report_assemble` DEGRADE. | Builder loop is commit f42a744 (v2.0.0, 2026-05-13), 13 days before PTY. Confirmed at `plamen_mechanical.py:4513` (downgrades only), `:4526` (`_verifier_status_from_text`), `:4530`. |
| C-2 | Builder appends **bare** `"UNRESOLVED"` (`:4534`) but body-tagger regex requires the **paren** form `UNRESOLVED\s*\(` (`:2375`). Even a verifier-origin UNRESOLVED would set demote but fail to tag the body. Latent; compounds C-1. | Same f42a744 lineage. |
| C-3 / D-1 | L1 **always** uses `_write_mechanical_report_index` (`driver:13346`, unconditional for `pipeline=='l1' and phase.name=='report_index'`), so the LLM Index Agent STEP 1.5 root-cause consolidation never runs and all `[LIKELY-DUP]` tags are ignored. SC is shielded by `_repair_sc_report_index_from_prior` + LLM index. | L1-mechanical-index path introduced f42a744 (pre-PTY). Claude scratchpad has no `_prompt_report_index`/`_stdio_report_index` and no `## Consolidation Map`. |
| A-3 / D-5 | The two runs are not an A/B of backends: Claude `subsystem_scope = ...\crates\p2p`; Codex `subsystem_scope = ""` (whole repo). This *fully* explains symptom #5 (non-overlap). | Operator config, not code. |
| B1 | Pre-fix L1 verify prompt taught neither ledger schema; gate accepts two bimodal shapes (`YES`+TestFile/Command OR `NO`+blocker-code). Both backends guessed wrong on attempt 1 in *opposite directions* — positive proof the cause is prompt ambiguity, not backend. | `git show 1616fca^:prompts/shared/v2/phase5-verification-l1.md` has no `### PoC Attempt`/`### Execution Result` schema; 1616fca (dated after both runs) added it. |
| B3 | Codex breadth attempt-1 refused: shared `phase3-breadth.md` mandates reading `spawn_manifest.md` and forbids inventing filenames, but the L1 phase graph has **no `instantiate` phase** to produce that manifest (pre-PTY baseline 62b6207 L1_PHASES has no instantiate; identical today). The "invent analysis_NN.md" block is the *universal* driver-injected EXPECTED-OUTPUT contract, not codex-specific, and existed pre-PTY. **Attribution corrected REGRESSION_PTY → PREEXISTING.** | 62b6207 vs current L1_PHASES identical; phase3-breadth.md spawn_manifest text byte-identical pre/post PTY. |
| E-8 | L1 chain-analysis removal and L1 evidence tags (`[NON-DET-PASS]`, `[DIFF-PASS]`, `[CONFORMANCE-PASS]`, `[LSP-TRACE]`) are cleanly engineered — no orphan consumer, no parser gap. Rules these out as failure surfaces. | Clean by construction. |

### 2d. Quality gaps (recall/precision/report-quality)

| ID | Issue |
|----|-------|
| A-5 / D-2 / D-3 / E-2 | 58 findings ≈ 28 distinct root causes; same-class clusters not merged. Three compounding causes: (D-3) candidate-pair generator sorts source-ID/CC-tag co-occurrence (noisiest signal) above location-overlap, starving the 24-pair LLM budget — all 24 live pairs were CC-shared noise, 0 merged, 37/61 deferred unseen; (D-2) mechanical consolidator clusters by fuzzy keyword signature with a rigid `>=3` threshold that structurally cannot merge any 2-member cluster; (E-2) supplemental mechanical dedup requires `location overlap AND title_score >= 1.0`, rejecting paraphrased-title duplicates at the same `file:line`. |
| D-4 / E-5 | Components-Audited table all-zero: `_synthesize_components_audited` (`plamen_mechanical.py:481-504`) substring-matches subsystem_map **prose headings** ("Rate Limiting") against **file paths** ("crates/p2p/src/rate_limiting.rs") — semantically impossible. The primary path (`file_coverage_ledger.md`) was never produced for the L1 run, exposing the broken fallback. Presentation-only. |
| C-4 | Blast-radius scope: no other degraded/stubbed/parity-recovery artifacts in either scratchpad. The `[CODE-TRACE]` tags are legitimate (no local rust/go build env), not supervision-failure stubs. The only failure event is the single C-1 DEGRADE. |
| B6 | The ~4h inventory_chunk_b gap on Claude is a rate-limit pause/resume, **not** a gate-driven retry. Excluded from the wasted-retry tally to keep it honest. |

---

## 3. Prioritized remediation plan

Ordered P0 → P2. No code was edited. Each item names the exact locus and a recall-safety flag. Any fix that weakens silent-drop / anti-fabrication / recall protection is marked `recall_safe=false` and de-prioritized or rejected.

### P0 — fixes a hard DEGRADE / blocks finished audits from shipping clean

1. **Merge judge UNRESOLVED into the mechanical report_index builder** (C-1 / A-4 / E-3).
   Locus: `plamen_mechanical.py` `_write_mechanical_report_index`, ~lines 4513 / 4530-4535. After computing `unresolved` from verifier status, OR in `fid ∈ _collect_judge_unresolved_ids(scratchpad)` (already imported at line 29) so a judge UNRESOLVED ruling over a CONFIRMED verifier demotes once and sets `unresolved=True`. Mirror in `_repair_sc_report_index_from_prior` for SC parity. **recall_safe=true** (adds a flag/demote-keep-in-body; drops nothing). This is the direct cause of the Codex DEGRADE and will fire on *any* L1 run where the judge rules any finding UNRESOLVED.

2. **Emit the paren-form Trust-Adj stamp** (C-2).
   Locus: `plamen_mechanical.py:4534`. Change `adjustments.append("UNRESOLVED")` → `adjustments.append(f"UNRESOLVED({severity})")` (pre-demote severity), matching the `SKEPTIC-DOWNGRADE({sev})` form at 4539 and the body-tagger regex `UNRESOLVED\s*\(` at validators-side line 2375. Without this, even a fixed C-1 path produces an untagged body and re-degrades. **recall_safe=true.** Must ship together with item 1.

### P1 — recall/precision quality (reduces duplication; one needs a guardrail)

3. **Re-rank dedup candidate pairs: location-overlap and high source-ID-subset above bare source-ID/CC co-occurrence** (D-3 / E-2).
   Locus: `plamen_parsers.py:3822-3828` (sort key `(-has_src, -has_loc, -score)`). Demote bare source-ID (CC-shared breadth provenance) below location-overlap so the 24-pair live budget (`_DEDUP_LIVE_PAIR_LIMIT`) is spent on real same-code pairs instead of noise. **recall_safe=true** (only changes *which pairs the LLM sees*; the LLM still decides merges; raising visibility of real dup pairs cannot drop a TP). Highest-leverage duplication fix.

4. **Relax the supplemental mechanical dedup gate for exact-location, same-severity pairs** (E-2 / D-2).
   Locus: `plamen_mechanical.py:4339` (`location overlap AND title_score >= 1.0`). Accept location-overlap + same-severity-tier at a lower title threshold (e.g. exact `file:line` match + `title_score >= 0.5`). **recall_safe=true ONLY IF** restricted to *exact* location match + same severity tier; broadening beyond that risks merging two distinct bugs at adjacent lines → TP loss. **Guardrail mandatory.**

5. **Mechanical consolidation: cluster on location-overlap / `[LIKELY-DUP]` evidence, threshold ≥2** (D-1 / D-2).
   Locus: `plamen_mechanical.py:4594-4602` (clusters by `_sig_key`, `len(members) >= 3`). Reuse the precomputed per-finding location-overlap / `[LIKELY-DUP]` signal instead of re-deriving a coarse keyword signature; lower threshold to ≥2 for high-confidence location-overlap; make severity/verdict differences non-fatal to grouping. **recall_safe=false** as written — consolidation *collapses report rows*, and lowering the bar can hide a distinct root cause/fix under a merged title. De-prioritize to P1-guarded: ship only with the report-template "no semantic loss" retention rule enforced (preserve every distinct branch/mechanism/fix in the merged Location table + Consolidation Reason). Prefer routing L1 through the LLM Index Agent consolidation pass (next item) over hardening the mechanical merge.

6. **Allow the LLM Index Agent STEP 1.5 consolidation pass for L1** (D-1).
   Locus: `plamen_driver.py:13327-13346` (unconditional mechanical index for L1). Optionally run the LLM index consolidation before the mechanical fallback so `[LIKELY-DUP]` hints + `dedup_candidate_pairs.md` actually drive consolidation, with the mechanical builder as the deterministic backstop. **recall_safe=true** if the mechanical builder remains the fallback (no finding can vanish; the LLM only proposes merges, the backstop guarantees coverage).

### P2 — presentation / churn reduction (no recall impact)

7. **Fix Components-Audited attribution** (D-4 / E-5).
   Locus: `plamen_mechanical.py:476-513`. Normalize subsystem_map heading → path-segment tokens before matching (strip parentheticals, lowercase, map "Rate Limiting" → `rate_limiting`), or — better — always emit/populate `file_coverage_ledger.md` for L1 so the working path-based primary path is used and the prose-heading fallback never runs. **recall_safe=true** (presentation-only; does not change which findings are reported).

8. **Reduce Codex attempt-1 churn via prompt enforcement** (B1 / B4 / E-6) — largely **already shipped** in 1616fca (verify ledger schema + post-write read-back) and the recon coverage directive. Remaining: extend the recon prompt (codex path) to enumerate every ≥10-file module and cite-or-ACKNOWLEDGE before returning DONE; make `NO_BUILD_ENVIRONMENT` the default blocker for rust/go findings without a fork/harness. **recall_safe=true.** Do **NOT** relax `_validate_poc_contract_for_rows` or the recon-coverage gate — weakening either re-admits ledgerless skips / silent subsystem omission (= recall loss). **Gate-relaxation rejected as recall_safe=false.**

9. **Extend the targeted PoC-contract repair to the Codex-direction failure class** (B2).
   Locus: `plamen_validators.py:12997-13040` (`verify_poc_contract_only_failed_ids` bails unless every sub-issue is the "Attempted:YES-without-TestFile" class). Also match "mandatory {class} PoC not attempted with valid blocker" so codex gets a cheap one-shot repair instead of a full shard re-run. **recall_safe=true** (optimization; the underlying churn is already removed by B1's prompt fix).

10. **Codex breadth/recon fan-out parity** (B3 / E-4 / E-7) — optional enhancement.
    Locus: `prompts/shared/v2/phase3-breadth.md` + `plamen_prompt.py` codex breadth instantiation. When `backend==codex` (no worker pool, no `spawn_manifest.md`), strip/override the spawn_manifest allowlist and make the "produce ≥N `analysis_*.md`" block authoritative, OR have the driver write a minimal `spawn_manifest.md` for the codex path. Alternatively route `CODEX_MULTI_AGENT_PHASES` breadth through per-layer sub-agents mirroring `_l1_breadth_jobs`. **recall_safe=true** (orchestration plumbing; does not affect finding selection). For L1, prefer the Claude PTY backend.

### Rejected / do-not-do

- **Relaxing `_validate_poc_contract_for_rows`, the recon-coverage gate, or the marker/structural depth gate** to "stop the retries." These gates caught genuine incompleteness (empty depth artifact, TODO leftover, 17-file uncited module, ledgerless PoC skip). Weakening them is `recall_safe=false` and re-admits silent drops. The retries *recovered* in every case; the cost is wasted compute, addressed by prompt fixes (item 8), not gate erosion.

---

## 4. Evidence index (verified this session)

| Claim | Verification |
|-------|--------------|
| PTY commit introduced the marker/IN_PROGRESS coupling | `git log -S "_BREADTH_STATUS_IN_PROGRESS" -- scripts/plamen_validators.py` → only `8c90ae9`; `git show 8c90ae9 --stat` confirms "Driver-owned worker pools (replaces compacting phase coordinators), Disk-gated Claude PTY supervision." |
| Codex marker false-fail is fixed and backend-aware | `git show 82664ff` body verbatim; fix reads `cli_backend` from config, codex unmarked → tolerated LEGACY_UNMARKED, claude unchanged, missing/stub/explicit still block. |
| L1 always uses mechanical report_index | `plamen_driver.py:13327` `if config["pipeline"]=="l1" and phase.name=="report_index"` → `:13346 _write_mechanical_report_index`; SC path at `:13295 _repair_sc_report_index_from_prior`. |
| Builder ignores judge UNRESOLVED, only honors DOWNGRADE | `plamen_mechanical.py:4513` `judge_downgrades = _collect_judge_downgrade_map`; `:4526 status = _verifier_status_from_text`; `:4530 unresolved = any(... in status ...)`. No `_collect_judge_unresolved_ids` call in the loop. |
| Builder bare-UNRESOLVED vs paren-form tagger | `plamen_mechanical.py:4534 adjustments.append("UNRESOLVED")` vs `:2375 re.search(r"\b(?:UNRESOLVED\|PARTIAL)\s*\(", trust, ...)`. |
| C-1 lineage predates PTY | builder loop = f42a744 (2026-05-13); PTY = 8c90ae9 (2026-05-26). 13-day gap. |

---

*End of analysis. Deliverable is read-only; no pipeline source modified.*
