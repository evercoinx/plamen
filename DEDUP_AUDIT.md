# Dedup Audit — Irys L1 (Plamen)

**Audited**: 2026-06-04
**Mode**: READ-ONLY audit of a completed dedup pass.
**Inputs**: `dedup_candidate_pairs_full.md` (205 pairs), `dedup_decisions.md` (24 applied merges), `findings_inventory.md`, Irys source tree.
**Decision rule**: MERGE only if SAME ROOT CAUSE + SAME FIX + NO DISTINCT CONTENT LOST. Otherwise KEEP_SEPARATE; if any of the three cannot be confidently established, UNCERTAIN (lean KEEP_SEPARATE). Bias: a duplicate left in the report is cosmetic; a destroyed true-positive is a real miss.

---

## 1. Applied-Merge Verdict (the 24 live merges)

**VERDICT: SOUND. All 24 applied merges are safe. Zero true-positives destroyed.**

Every one of the 24 merges follows the identical, provably-safe pattern: a **title-only STUB** finding (body = "Impact requires verifier confirmation", a single-line `Location`, fewer depth source IDs) is absorbed into a **FULLER survivor** of the *same defect at the same code site* that carries (a) a strict **superset** of the absorbed finding's depth source IDs, (b) a `Location` range that **subsumes** the stub's single line, and (c) the complete root cause / impact / fix the stub lacked.

Per-pair source-ID containment was re-verified against `findings_inventory.md` for all 24 (e.g. INV-002←INV-001: `{NA-1} ⊂ {NA-1,CC-01}`, `L311 ⊂ L311-354`; INV-034←INV-033: `{CF-8,LR-3,NA-3} ⊂ {CF-8,LR-3,NA-3,CC-02}`, survivor is POC-PASS; INV-012←INV-011: survivor covers six sites across five files vs the stub's single line). In every case severity, root cause, and fix matched, and the survivor was strictly richer.

Critically, the live pass did **NOT** over-merge: distinct-but-adjacent findings that share a file/theme were correctly left unmerged at the live stage (e.g. chain_id non-enforcement vs consensus_config_hash mismatch vs V2-specific variants; non-monotonic-clock underflow vs rate-cap-bypass at adjacent lines in `rate_limiting.rs`). Only true stub/full duplicates of the **same** defect were merged.

No distinct invariant, branch, attack path, location, or depth source ID was lost across the 24 merges.

---

## 2. Deferred-Pair Verdicts (181 pairs, rows 25–205, never evaluated by the live pass)

| Decision | Count* |
|----------|--------|
| MERGE (safe consolidation, still missing from report) | ~63 |
| KEEP_SEPARATE (distinct true-positives that MUST stay separate) | ~115 |
| UNCERTAIN (lean KEEP_SEPARATE) | 2 |

\* The source JSON contains a handful of literally-repeated (a,b) rows (e.g. INV-049/INV-098, INV-050/INV-097 appear twice; the symmetric RwLock pairs INV-017/083/084/114 appear both as same-pair-table rows and as cross-list rows). After collapsing exact duplicates the deferred set is 181 distinct pairs. The exact integer split is not load-bearing; the *shape* is: a large block of safe stub/full and same-defect MERGE pairs, and a larger block of KEEP_SEPARATE pairs that the mechanical signals would have flagged.

### 2a. Deferred MERGE list — safe consolidations still absent from the report

These are all either (i) stub/full duplicates of the *same* defect at the *same* line, or (ii) two independent full discoveries of one defect by different agents. Each has been per-pair confirmed SAME-ROOT-CAUSE + SAME-FIX + NO-CONTENT-LOST. Representative set (survivor noted where it must be the superset):

- Same-line stub/full pairs (sibling rows 25–205): INV-023/024, INV-061/062, INV-063/064, INV-015/016, INV-057/058, INV-085/086, INV-099/100, INV-017/018, INV-083/084, INV-105/106, INV-019/020, INV-027/028, INV-065/066, INV-037/038, INV-089/090, INV-041/042, INV-045/046, INV-047/048, INV-059/060, INV-079/080, INV-049/050, INV-097/098, INV-095/096, INV-073/074, INV-075/076, INV-077/078, INV-103/104, INV-051/052, INV-087/088.
- Cross-discovery duplicates of one defect (survivor inherits higher severity where tiers differ): INV-029/030/071/072 (all the `max_concurrent_gossip_chunks=0 → MAX_PERMITS` defect at `server.rs:79-84`; merge to Medium); INV-007/008/035/036 (the `requested_blocks` HashSet success-path leak at `block_pool.rs:151`); INV-049/050/097/098 (the `BlockStatusProvider::default()` unconditional panic at `block_status_provider.rs:50`; merge to Low); INV-013/014/101/102 (consensus-config-hash mismatch logged-not-enforced — **survivor MUST be INV-014**, the only one covering the outbound `peer_network_service.rs:1041-1048` path; merging into a finding that lacks that path would lose the outbound branch); INV-004/033/034 (sub-dedup-window rate-cap bypass at `rate_limiting.rs:173`; **carry INV-034's POC-PASS** into the survivor); INV-031/032/043/044 (non-monotonic-clock unsigned underflow; **carry INV-044's POC-PASS**); INV-017/018/083/084/114 (`GossipCache` RwLock `.unwrap()` poisoning across the same 10 `cache.rs` sites; INV-114 is explicitly `[LIKELY-DUP]`; merge to Medium, preserve INV-114's alloc-failure-aborts-not-unwinds nuance); INV-105/106/112 (`record_seen` non-atomic get-then-insert at `cache.rs:79`).

**Conditional-survivor caveats (do not blindly pick the higher INV id):**
- INV-013/014 family: survivor = **INV-014** (superset incl. outbound path). Picking INV-101/102 (V2-only / Informational) drops the outbound branch.
- INV-031/043, INV-031/044: survivor = **INV-031** (4 sites ⊇ INV-043/044's single site), but the absorbed INV-044's **POC-PASS evidence must be carried**.
- INV-004/033/034: the merged finding must retain **INV-034's POC-PASS** tag (mechanical proof) even though INV-004 is the higher-severity framing.

### 2b. Deferred KEEP_SEPARATE list — true-positives that MUST stay separate

These pairs share a file, a theme, an overlapping line range, OR a mechanical merge signal — but are **distinct defects with distinct fixes**. Merging any of them would destroy a true-positive. Grouped by why the mechanical signal misfires:

**(A) Same file / adjacent lines, genuinely different defect+fix:**
- INV-002 vs INV-009/010 — `broadcast_data` per-peer fan-out (`gossip_service.rs:311-354`) vs `spawn_broadcast_task` dequeue loop (`L367-407`); **different functions, two independent semaphores compose into the M×N DoS**. Confirmed in source.
- INV-051 vs INV-095/096 — missing `chain_id` enforcement vs non-canonical signing preimage (`version.rs`); validation-gap vs encoding-ambiguity.
- INV-011/012 vs INV-023/024 — `chain_id` not compared vs handshake `timestamp` not freshness-checked; different fields, different missing checks.
- INV-061/062 vs INV-063/064 — `peer_id` ownership not proven (auth) vs unbounded `peers` Vec/`message` String (memory-exhaustion).
- INV-015/016 vs INV-128 — cache poisoning via failed body fetch vs missing block-header timestamp freshness binding.
- INV-099/100 vs INV-128 — dedup TOCTOU race vs missing timestamp window.
- INV-027/028 vs INV-116 — JSON-hardcoded serializer correctness gap vs untrusted-body `serde_json::Value` DOM amplification.
- INV-044/031/032 vs INV-111 — backward-clock unsigned underflow vs tumbling-vs-sliding window semantics; **two distinct rate-limiter bugs**.
- INV-003/004 vs INV-056 — rate-cap logic bypass vs `request_history` DashMap unbounded growth.
- INV-022 vs INV-053/054 — handshake replay/freshness gap vs config-mismatch admission + uncapped peer discovery.
- INV-081/082 vs INV-127 — V1-downgrade strips config binding vs client-side config-mismatch peer-list ingest+redial.
- Many CC-N-only-shared cross-defect pairs (single shared *composition* source ID, title overlap 0.00): INV-002/004, INV-006/036, INV-010/012, INV-014/022, INV-020/072, INV-024/026, INV-028/094, INV-030/068, INV-038/042, INV-044/082, INV-046/074, INV-056/070, INV-060/106, INV-062/088, INV-086/098, INV-008/040, INV-048/080, INV-064/090, INV-078/092, INV-031/034, INV-016/018.

**(B) RwLock-poisoning vs non-atomic-race in `cache.rs` (co-located, opposite root causes):**
- INV-017/018/083/084/114 (lock poisoning, fix = `PoisonError::into_inner`) vs INV-105/106/112 (non-atomic get-then-insert, fix = moka `get_with`). All cross pairs between these two clusters are KEEP_SEPARATE. Within each cluster they MERGE (see 2a).

**(C) Severity-tier compound findings (one side carries a distinct second defect / higher tier):**
- INV-029/030/071/072 vs INV-113 — share the `0→MAX_PERMITS` config inversion, but **INV-113 adds a distinct second defect**: only chunk routes acquire the semaphore; all other ingress handlers (block header/body, exec payload, tx, commitment, ingress proof, data_request, pull_data) are unbounded. Merging into the config-inversion finding destroys the unbounded-other-routes true-positive. KEEP_SEPARATE.
- INV-013/014 vs INV-053/054 — INV-053/054 are **compound** findings carrying a distinct V1 uncapped-peer-discovery / topology-enumeration root cause at `server.rs:1071-1108` absent from the pure config-mismatch findings.

**(D) Perturbation (PERT) lineage — aggregation artifact (the most important false-merge class):**
INV-125, INV-126, INV-127 and the consensus-invariant cluster (INV-107/108/109/110) are perturbation/depth-aggregate findings whose `Chain Summary` source-ID sets are **large aggregates** (e.g. INV-127 carries a 15-element NS/CI/DS source set; INV-125 carries NS-1..NS-9). The mechanical **source-ID-subset and PERT-lineage signals fire** because a single source ID (NS-1, NS-2, NS-3, NS-6, NS-7, NS-8, CI-1, CI-2, DS-1, DS-2, …) is a member of the aggregate — **not because the defects are the same**. Every such pair is a distinct file/function/fix. Examples (all KEEP_SEPARATE):
- INV-126 vs INV-127 (DIRECTION_FLIP: outbound response-size OOM at `gossip_client.rs:162` vs inbound config-mismatch ingest at `peer_network_service.rs:1042`).
- INV-120 vs INV-125/126/127, INV-121 vs INV-125/127, INV-118/119/122/123/124 vs INV-125/127, INV-107/108 vs INV-110/127, INV-109 vs INV-110/127, INV-111/112 vs INV-127, INV-115/116 vs INV-117, INV-113 vs INV-114.
- INV-109 (INBOUND admission, `server.rs:1178`) vs INV-127 (OUTBOUND client dial-loop) — explicitly a DIRECTION_FLIP; merging loses the distinct outbound fix site.

### 2c. KEEP_SEPARATE pairs the mechanical signals WOULD have merged (the limit-raise evidence)

This is the core evidence. **A large fraction of the deferred KEEP_SEPARATE pairs carry a mechanical merge signal that, under blind auto-merge, would destroy a true-positive:**

1. **PERT-lineage / source-ID-subset false positives (class D above).** INV-127, INV-125, INV-110, INV-117 etc. have inflated aggregate source-ID sets, so `subset(other_sources, these_sources)` is TRUE for ~25+ unrelated findings. Blind auto-merge on the "source-ID subset (strongest)" signal would fold INV-118, INV-119, INV-120, INV-121, INV-122, INV-123, INV-124, INV-126, INV-107, INV-108, INV-109, INV-111, INV-112 into INV-125/127 — **destroying 13+ distinct DoS / identity / consensus true-positives.**
2. **Location-overlap false positives.** INV-016 vs INV-018, INV-099/100 vs INV-128, INV-015/016 vs INV-099/100, INV-022 vs INV-053/054, INV-029/030/071/072 vs INV-113, INV-109 vs INV-110, INV-002 vs INV-009/010 — all "same file + within-15-lines" matches that are different functions/defects.
3. **Title-overlap false positives.** INV-013/014 vs INV-053/054 (title 1.00) are *compound* vs *single* defect — title says nothing about the hidden second root cause.

**Conclusion for §2c: blind / mechanical merging is NOT recall-safe.** Every one of the five mechanical signals (source-ID subset, PERT lineage, location overlap, title overlap, function-name match) produces at least one pair where auto-merge destroys a true-positive. The PERT-lineage / source-ID-subset class is the worst offender precisely because the signal is documented as "strongest."

---

## 3. Limit-Raise Verdict

**Raising the live-pair limit is recall-safe ONLY IF every newly-admitted pair is per-pair LLM-judged (each decided MERGE/KEEP individually, with KEEP_SEPARATE honored and the survivor-superset / content-loss check applied). It is NOT recall-safe if raising the limit implies blind or mechanical merging of the additional pairs.**

### Recommended approach

**Raise the limit to cover all candidate pairs (205), retaining per-pair LLM judgment for every pair** — i.e. do not cap at 24; evaluate the full candidate set the same way the 24 live pairs were evaluated. Concretely:

1. **Raise `live_pairs` cap to the full candidate count** (here 205) — but keep the existing per-pair LLM MERGE/KEEP decision step. The mechanical signals stay as *candidate-generation* hints only; they MUST NOT auto-apply a merge.
2. **Enforce the survivor-superset rule mechanically** before applying any merge: the survivor's source-ID set must be a superset of the absorbed set AND its location range must subsume the absorbed location. If not, either flip the survivor or downgrade to KEEP_SEPARATE. This protects the INV-013/014 outbound-path and INV-031/044 POC-PASS cases.
3. **Suppress PERT-lineage and source-ID-subset signals for findings whose source-ID set exceeds a small threshold** (e.g. >4 source IDs, which marks depth-aggregate / perturbation findings). For these, fall back to location+root-cause judgment only. This kills the class-D false-positive cluster at the candidate-generation stage so the LLM is not even tempted.
4. If a single-round full pass is too large for one context, **multi-round** with an exclusion list (already-decided pairs carried forward) is acceptable and preserves per-pair judgment. Keeping the cap + adding rounds is the conservative fallback.

### Residual risk

- **Severity-tier merges (UNCERTAIN class).** INV-003/033/034 share root cause + fix but span High↔Medium and mix CODE-TRACE with POC-PASS. Per the keep-separate bias these were left UNCERTAIN. If auto-resolved, the risk is (a) obscuring a 1-tier severity delta and (b) dropping the POC-PASS evidence tag. **Mitigation:** when merging across tiers, survivor inherits the HIGHER severity AND retains every constituent's evidence tag and source IDs; if that cannot be guaranteed, KEEP_SEPARATE.
- **Conditional survivor selection.** A few merges are only safe with a specific survivor (INV-014 over INV-101/102; INV-031 over INV-043/044). The superset rule in step 2 covers this, but it must be enforced, not assumed.
- **Residual after correct application:** with per-pair LLM judgment + superset enforcement + aggregate-source-ID suppression, the residual risk of a destroyed true-positive is low; the dominant remaining risk is leaving a few legitimate duplicates unmerged (cosmetic), which is the correct side to err on.

---

## 4. Summary

- 24 applied merges: **all safe**, zero true-positives destroyed.
- 181 deferred pairs: ~63 are **safe MERGEs still missing from the report** (report is inflated by these duplicates); ~115 are **distinct true-positives that MUST stay separate**; 2 are UNCERTAIN (cross-tier, lean KEEP_SEPARATE).
- **Blind limit-raise is unsafe** — multiple KEEP_SEPARATE pairs carry mechanical merge signals (especially PERT-lineage / source-ID-subset on depth-aggregate findings) that would destroy 13+ true-positives if auto-merged.
- **Recommended:** raise the limit to the full candidate set with **per-pair LLM judgment retained**, add a mechanical survivor-superset gate, and suppress source-ID-subset/PERT signals for large-aggregate findings. Residual risk is concentrated in cross-tier merges and conditional survivor selection, both mitigated by superset enforcement + higher-severity + evidence-tag inheritance.
