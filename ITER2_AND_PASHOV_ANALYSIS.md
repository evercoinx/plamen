# Iter-2 Root Cause + Pashov/skills Decision Memo

Date: 2026-06-04
Author: Plamen synthesizer
Scope: (A) why the mandated Devil's-Advocate iteration-2 did not fire in Claude PTY depth; (B) reconciliation of two independent analyses of `github.com/pashov/skills` and what, if anything, to adopt.

---

## Part A — Iter-2 DA failure

### A.1 Confirmed root cause

The mandated DA iteration-2 silently no-ops because of an **inventory-ID-mismatch in the iter-2 gate**, confirmed by direct code reading.

- The DA job is only created if `_depth_da_job_if_required` (`plamen_driver.py:8647`) returns a job, which it does only when `_validate_confidence_iter2_mandatory` returns issues (`8651`). The DA batch is spawned at `8699-8714`.
- `_validate_confidence_iter2_mandatory` (`plamen_validators.py:4177`) computes `uncertain_ids` from `confidence_scores.md` (`4185-4189`), then for **each uncertain id, searches for that exact id string inside `findings_inventory.md`** (`4202-4204`: `m = re.search(re.escape(fid), inv_text)`), and only then reads a `Severity:` field in the 500-char window after the match (`4207-4212`).
- If the inventory does not contain the same finding IDs that `confidence_scores.md` uses (the Track-1 "inventory lacked agent IDs at finalize time"), every `re.search` misses, `medium_plus_uncertain` stays `0`, and the function returns `[]` at `4214-4215`. The gate reports "all clear," so `_depth_da_job_if_required` returns `[]` and **no DA worker is ever spawned.** Iteration-2 is mandated by orchestrator-rules §12 but is gated behind a check that can only fail-open.

This is a fail-**open** validator: an ID-join failure is indistinguishable from "no uncertain Medium+ findings," and the code treats both as "skip iter-2."

### A.2 Codex-vs-PTY asymmetry

The asymmetry is real and structural. On Codex, the depth phase runs as a single `codex exec`, and the iteration-2 Devil's-Advocate logic executes **inside the phase prompt** (the LLM follows phase4-confidence-scoring AD-1/AD-2 in-context). On the Claude backend, depth runs as a **PTY worker pool** — one bounded single-role worker per artifact, explicitly forbidden from spawning `Task()` subagents — so the worker pool *cannot* run a cross-finding adaptive loop itself. The driver must inject DA iter-2 as an extra worker batch, which is exactly what `_depth_da_job_if_required` + `_run_depth_worker_batch` are for. That driver-side path is the one with the brittle ID-join. So Codex gets iter-2 "for free" in-prompt; PTY depends on a gate that silently fails.

### A.3 Regression verdict

**Yes — this is a regression introduced by the PTY worker-pool refactor.** Before the worker-pool split, the depth phase ran as a phase-LLM that performed the adaptive loop in-context (the Codex shape). The worker-pool refactor moved iter-2 from in-prompt execution to a driver-orchestrated batch gated on `_validate_confidence_iter2_mandatory`, and that gate's ID-join against the inventory is the new failure surface. The Codex path, which kept the in-prompt shape, is unaffected — which is the tell that this is refactor-induced, not a pre-existing methodology bug.

### A.4 Minimal recall-safe fix

**Locus:** `plamen_validators.py:_validate_confidence_iter2_mandatory` (lines ~4201-4215), with the call site at `plamen_driver.py:8647-8666` unchanged.

**Mechanism:** Stop joining on `findings_inventory.md` by ID. Severity for the iter-2 decision should be derived from a source that shares the `confidence_scores.md` id namespace:

1. **Primary:** parse severity directly out of `confidence_scores.md` if present (the scoring agent already emits per-finding severity alongside the composite; the confidence-scoring file is the same namespace as `uncertain_ids`, so no join is needed).
2. **Fallback (Track-1 recommendation):** if the score file has no severity column, resolve severity from the depth/source finding files (`depth_*_findings.md`) by the same id, not from the inventory — the depth outputs use the agent-finding ID namespace that confidence scores are computed from.
3. **Recall-safe failure mode:** if severity is genuinely unresolvable for an uncertain id, **treat it as Medium+ (fire iter-2), not as "skip."** The current code fails open (skips); a recall-starved pipeline must fail **closed** (run the extra DA pass). Spawning one DA worker on an ambiguous case costs one worker; skipping it loses the entire iter-2 recall lift the user paid Thorough for.

This is recall-safe: worst case it runs DA iter-2 when it was not strictly required (small cost), and it can never again silently skip a mandated iteration. Add a fixture test with mismatched id namespaces between `confidence_scores.md` and `findings_inventory.md` (the exact production condition) — the existing tests in `test_v2_6_2_confidence_quality.py` all use matched IDs and therefore never exercise the join-miss path.

---

## Part B — pashov/skills

### B.1 What it is (both analysts agree, high confidence)

`pashov/skills` is a small public MIT repo of **Claude Code "skills"** (markdown SKILL.md + references + a couple of helper scripts) — NOT a pipeline, service, or daemon. Two skills: `solidity-auditor` (12-agent single-wave Solidity reviewer, "<5 min") and `x-ray` (pre-audit recon/readiness + a ~1300-line `analyze_git_security.py`). Solidity-only. No PoC execution, no adaptive loop, no RAG, no verifier agents, no skeptic/crossbatch. Validation is a single textual 4-gate pass (`judging.md`) run in the same turn as dedup, with "UNCERTAIN = ALLOWS" and an explicit "skip re-verification." Adversarialism is **persona-level**, not separate-context passes.

The two analysts are in near-total agreement on architecture, cost mechanism, and absence of evidence. They differ only in tone and minor line counts (Analyst #1: ~1000 lines of auditor prose; Analyst #2: ~2,830 md + ~2,400 py total). Neither difference is material.

### B.2 The "same recall at ~5x cheaper / much faster" claim

**Verdict: PARTIALLY real on cost/speed, INFLATED/UNSUBSTANTIATED on recall parity.**

- **Cost/speed: REAL and well-explained.** Both analysts independently and concretely attribute the cheapness to *deletions*, not to a novel efficiency trick: 12 fixed agents in one wave (no recon/inventory/rescan/invariant/dedup-phase/verify-shards/skeptic/crossbatch as separate invocations); **no PoC authoring/compile/execute/retry** (the single biggest sink vs our Phase 5); no iter-2/3 adaptive loop; bundle-once context (no exploratory Grep/Read churn); no MCP/RAG round-trips. Directionally a ~5x reduction is plausible **against an unusually heavy baseline (ours).** Both correctly note: most of the saving *is the verification and iteration our pipeline deliberately buys.*

- **Recall parity: UNSUBSTANTIATED.** Both analysts independently grepped the repo for `benchmark|recall|precision|ground-truth|contest|F1|eval|sherlock|code4rena|cantina` and found **zero** measured numbers, no eval harness, no GT comparison, no CI eval workflow — only illustrative GIFs and humble marketing ("findings in minutes, not weeks"; "Not a substitute for a formal audit"; "AI analysis can never verify the complete absence of vulnerabilities"). The "same recall" framing is **external (task brief / word-of-mouth), not made by the repo.** The repo's own design (single-pass, no-PoC, "run 2-3 times" handed to the human, "target 2-5 hot contracts") predicts a recall ceiling on hard multi-step / composition / large-protocol bugs.

**Do the analysts disagree?** No material disagreement. Both reach UNSUBSTANTIATED on recall and PARTIALLY-real on cost. Analyst #2 is slightly better-supported because it explicitly frames the cost saving as "compared against an unusually heavy baseline (ours) — most of the saving is 'don't run PoCs and don't iterate,'" which is the load-bearing caveat for *our* decision. Both note the non-determinism concession ("run 2-3 times") that makes single-run recall variable — meaning even an anecdotal recall figure likely assumes multiple runs and/or a generous bug-class subset.

### B.3 Honest framing for our pipeline

Their cheapness comes **largely from doing less of what we deliberately buy**: mechanical ground truth (executed harm-asserting PoCs), automated adaptive iteration, RAG precedent, and structural (separate-context) adversarial passes. Our own AD-2 research note states same-context "be adversarial" yields <50% divergence vs >99% for hard role separation — their persona-only adversarialism is exactly the weaker form. So we should **adopt their cheap *discovery-side* techniques, and adopt nothing that would trade away our verification/iteration layer.** The recall-per-dollar wins are in *prompt engineering and context discipline*, not in their pipeline shape.

### B.4 Adoptable techniques, ranked by recall-per-dollar lift for OUR pipeline

1. **Seam / gap-hunter agents** — `references/hacking-agents/{numerical-gap,trust-gap,flow-gap}-agent.md`. Dedicated agents that ONLY hunt bugs requiring 2-3 lenses simultaneously and are forbidden from re-reporting single-lens bugs. Directly targets the compound/composition bugs our single-domain depth agents miss between domains; cheaper and earlier than our chain-analysis phase. Highest lift because it attacks our known weak spot (cross-domain seams) at discovery time.
2. **Attacker-only discovery framing + anti-self-refutation** — `references/senior-auditor-sop.md` ("deepen the attack; never argue yourself out of one"; escalate to worst variant). Separate discovery (amplify) from validation (gate). Counters the LLM tendency to talk itself out of real findings during breadth/depth — a pure recall win at zero added cost.
3. **Cross-contract weaponization rule** — `references/hacking-agents/shared-rules.md` ("when you find a bug, weaponize the pattern across EVERY other contract; missing a repeat instance is an audit failure"). Tighter than our existing sibling-propagation directive; cheap recall multiplier.
4. **Mental-tool trigger markers** (`[Feynman:]`/`[Socratic:]`/`[Inversion:]`) — `senior-auditor-sop.md` + `shared-rules.md`. Forced, grep-able reasoning-depth markers; the "explain in plain English; where you reach for jargon, a bug hides" heuristic is a concrete depth-forcer. Mechanically auditable proof-of-work analogous to our STEP-TRACE gate but at reasoning granularity — possibly cheaper than our skill-execution-checklist agent.
5. **Bundle-once context discipline** — `solidity-auditor/SKILL.md` Turn 2 + `shared-rules.md` ("Do NOT re-read in-scope files for the initial scan"). Pre-concatenate source+SOP+specialty+rules per agent; kills exploratory tool-call churn. A direct token/latency saving for small-scope breadth/depth targets (cost lever, modest recall effect).
6. **Git-history security analysis** — `x-ray/scripts/analyze_git_security.py`. Fix-candidate commits, late changes, forked deps, single-dev dominance, test co-change rate → hotspot ranking BEFORE reading code. We have no git-temporal risk signal in recon; zero-LLM-cost, adoptable as a recon prepass.
7. **Dedup fix-preservation + function-isolation HARD GATE** — `solidity-auditor/SKILL.md` Turn 4. Never merge across different functions; when ≥2 distinct fixes exist, emit Option A/B verbatim; print a completeness receipt. Anti-silent-drop / anti-over-merge — exactly the failure class our consolidation hit in v2.4.2 / v2.7.0.
8. **Admin-finding amplifier requirement + explicit DO-NOT-REPORT lists** — `references/judging.md` + `shared-rules.md` (admin-can-rug-without-mechanism, MEV, rounding dust, MINIMUM_LIQUIDITY, SafeERC20, nonReentrant). Precision controls (FP suppression), not recall — adopt cautiously; their admin-rejection is aggressive enough to risk dropping legitimate centralization findings we currently keep.
9. **Dense pattern catalogs** — every `hacking-agent` file (e.g. `math-precision-agent.md` ~20 named exploits in 48 lines; `asymmetry-agent.md` Beefy/DAI-permit exemplars). High signal-per-token; worth distilling our longer skill files toward this density. Quality/efficiency, indirect recall.

### B.5 Risks if we naively copy their *shape* (do not)

No executed PoCs (no mechanical harm verification; their "UNCERTAIN=ALLOWS" + heuristic confidence ships plausible-reading FPs our `[POC-PASS]` gate catches); single-pass (no iter-2/3, recall variance outsourced to human re-runs); context-scaling limit (all-source-into-every-agent blows up on large multi-contract protocols — their honest mitigation is to shrink scope); no RAG/historical grounding; persona-only (anchored) adversarialism. These are precisely the layers our pipeline exists to provide.

---

## Recommendations (prioritized)

1. **Fix the iter-2 gate (P0).** Re-derive severity from `confidence_scores.md` namespace (fallback: depth findings), fail-CLOSED on unresolved severity, add a mismatched-namespace fixture test. Recall-safe, ~30 LOC + test.
2. **Pilot seam/gap-hunter as a depth-agent variant.** Highest recall-per-dollar adoptable; targets our cross-domain blind spot at discovery time.
3. **Add attacker-only / anti-self-refutation discovery framing + cross-contract weaponization directive** to breadth/depth prompts. Near-zero cost, pure recall.
4. **Add git-history hotspot prepass to recon** (port `analyze_git_security.py` concept). Zero LLM cost.
5. **Adopt fix-preservation + function-isolation HARD GATE** into our consolidation/dedup step. Anti-silent-drop hardening.
6. **Consider mental-tool markers + bundle-once** for small-scope modes; measure before generalizing.
7. **Adopt nothing from their pipeline *shape*** — no dropping of PoC execution, adaptive iteration, RAG, or structural adversarial passes. Their cheapness is mostly the absence of the recall/verification we deliberately buy; copying the shape would be a recall regression dressed as a cost win.
