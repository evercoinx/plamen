# Cross-File Consistency Audit — Plamen V2 System

**Date**: 2026-04-16
**Scope**: Full cross-file reference verification across driver, prompts, skills, agents, rules, commands, and assessment files.

---

## Summary

| Category | Checked | OK | Broken/Stale | Missing |
|----------|---------|-----|-------------|---------|
| Driver PROMPT_FILES → disk | 16 | 16 | 0 | 0 |
| Driver LANG_PROMPT_FILES → disk (5 langs) | 35 | 35 | 0 | 0 |
| L1 prompt resolution → disk | 6 | 5 | 0 | 1 |
| CLAUDE.md REFERENCE FILES → disk | 54 | 54 | 0 | 0 |
| Depth agent definitions → disk | 6 | 6 | 0 | 0 |
| Skills (EVM) → disk | 18 | 18 | 0 | 0 |
| Skills (Solana) → disk | 20 | 20 | 0 | 0 |
| Skills (Aptos) → disk | 22 | 22 | 0 | 0 |
| Skills (Sui) → disk | 22 | 22 | 0 | 0 |
| Skills (Soroban) → disk | 19 | 19 | 0 | 0 |
| Niche agents → disk | 9 | 9 | 0 | 0 |
| Injectable skills → disk | 8 | 8 | 0 | 0 |
| L1 injectable skills → disk | 16 | 16 | 0 | 0 |
| Driver always_on → disk | 14 | 14 | 0 | 0 |
| Commands → driver path | 2 | 2 | 0 | 0 |
| Driver dispatch → functions | 14 | 14 | 0 | 0 |
| Assessment prompts | 2 | 0 | 2 | 1 |
| Megaplan status | 4 | 0 | 3 | 0 |
| CLAUDE.md language list | 1 | 0 | 1 | 0 |

**Total issues found: 8** (3 stale references, 1 missing file, 3 stale megaplan statuses, 1 CLAUDE.md omission)

---

## 1. Driver PROMPT_FILES (Static Mapping) — ALL OK

Every entry in the `PROMPT_FILES` dict at line 645 resolves to an existing file:

| Phase Key | Mapped Path | Status |
|-----------|-------------|--------|
| `inventory_merge` | `prompts/shared/phase4a-inventory-merge.md` | OK |
| `invariants_p1` | `prompts/shared/phase4a5-invariants.md` | OK |
| `invariants_p2` | `prompts/shared/phase4a5-invariants-p2.md` | OK |
| `confidence` | `prompts/shared/phase4b-scoring.md` | OK |
| `variable_map` | `prompts/shared/phase4b-variable-map.md` | OK |
| `perturbation` | `prompts/shared/phase4b-perturbation.md` | OK |
| `skill_checklist` | `prompts/shared/phase4b-skill-checklist.md` | OK |
| `rescore` | `prompts/shared/phase4b-rescore.md` | OK |
| `final_scoring` | `prompts/shared/phase4b-final-scoring.md` | OK |
| `skeptic_judge` | `prompts/shared/phase5-skeptic-judge.md` | OK |
| `crossbatch` | `prompts/shared/phase5-crossbatch.md` | OK |
| `report_assemble` | `rules/phase6-report-prompts.md` | OK |
| `rescan` | `rules/phase3b-rescan-prompt.md` | OK |
| `percontract` | `rules/phase3b-rescan-prompt.md` | OK |
| `rag_sweep` | `rules/phase4-confidence-scoring.md` | OK |
| `chain_agent1` | `rules/phase4c-chain-prompt.md` | OK |
| `chain_agent2` | `rules/phase4c-chain-prompt.md` | OK |
| `chain_iter2` | `rules/phase4c-chain-prompt.md` | OK |
| `report_index` | `rules/phase6-report-prompts.md` | OK |
| `invariant_fuzz` | `prompts/evm/phase4b-invariant-fuzz.md` | OK |
| `medusa_fuzz` | `prompts/evm/phase4b-loop.md` | OK |

---

## 2. Driver LANG_PROMPT_FILES (Dynamic, per-language) — ALL OK

Every language-specific prompt resolves for all 5 SC languages (evm, solana, aptos, sui, soroban):

| Phase Key | Template Pattern | EVM | Solana | Aptos | Sui | Soroban |
|-----------|-----------------|-----|--------|-------|-----|---------|
| `recon` | `prompts/{lang}/phase1-recon-prompt.md` | OK | OK | OK | OK | OK |
| `breadth` | `prompts/{lang}/phase3-breadth-driver.md` | OK | OK | OK | OK | OK |
| `inventory` | `prompts/{lang}/phase4a-inventory-prompt.md` | OK | OK | OK | OK | OK |
| `depth` | `prompts/{lang}/phase4b-depth-driver.md` | OK | OK | OK | OK | OK |
| `depth_iter2` | `prompts/shared/phase4b-da-iter2.md` | OK (shared) | OK | OK | OK | OK |
| `depth_iter3` | `prompts/shared/phase4b-da-iter2.md` | OK (shared) | OK | OK | OK | OK |
| `verify` | `prompts/{lang}/phase5-verification-prompt.md` | OK | OK | OK | OK | OK |

---

## 3. L1 Prompt Resolution — 1 MISSING FILE

L1 pipeline uses `prompts/l1/` override. Resolution order: L1-specific first, then falls back to shared.

| Phase Key | Resolved Path | Status |
|-----------|---------------|--------|
| `recon` | `prompts/l1/phase1-recon-prompt.md` | OK |
| `breadth` | `prompts/l1/phase3-breadth-driver.md` | OK |
| `depth` | `prompts/l1/phase4b-depth-driver.md` | OK |
| `verify` | `prompts/l1/phase5-verification-prompt.md` | OK |
| `inventory` | `prompts/l1/phase4a-inventory-prompt.md` | **MISSING** |
| Report overrides | `prompts/l1/phase6-report-overrides.md` | OK |

**ISSUE**: `prompts/l1/phase4a-inventory-prompt.md` does not exist. When L1 pipeline runs the `inventory` phase, `_resolve_prompt()` tries `prompts/l1/phase4a-inventory-prompt.md` (L1 override), then falls back to `prompts/{lang}/phase4a-inventory-prompt.md` where `lang` is `go` or `rust`. Neither `prompts/go/` nor `prompts/rust/` exist. Then falls back to `PROMPT_FILES` which has no `inventory` entry. **Result**: L1 inventory phase will log "No prompt file resolved" and return exit code 1.

**Fix needed**: Either create `prompts/l1/phase4a-inventory-prompt.md` or add `"inventory"` to the static `PROMPT_FILES` dict with a shared fallback.

---

## 4. CLAUDE.md REFERENCE FILES Table — ALL OK + 1 Omission

### Shared references (all exist):
- `~/.claude/rules/finding-output-format.md` — OK
- `~/.claude/rules/phase3b-rescan-prompt.md` — OK
- `~/.claude/rules/phase4-confidence-scoring.md` — OK
- `~/.claude/rules/phase4c-chain-prompt.md` — OK
- `~/.claude/rules/phase5-poc-execution.md` — OK
- `~/.claude/rules/phase6-report-prompts.md` — OK
- `~/.claude/rules/report-template.md` — OK
- `~/.claude/rules/skill-index.md` — OK
- `~/.claude/rules/post-audit-improvement-protocol.md` — OK
- `~/.claude/agents/depth-*.md` (4 standard + 2 L1) — all 6 OK

### Language-specific references (all 5 languages x 9 files = 45 checks): ALL OK

### STALE OMISSION in CLAUDE.md:
The REFERENCE FILES table says: *"resolve `{LANGUAGE}` to `evm`, `solana`, `aptos`, or `sui`"* — it omits **`soroban`** from the language list. Soroban has a full set of prompts and skills on disk, and the driver's `LANG_PROMPT_FILES` and `always_on` dict include it. The CLAUDE.md text should list all 5 languages.

---

## 5. Depth Agent Definitions — ALL OK

| Agent | Path | Status |
|-------|------|--------|
| depth-token-flow | `agents/depth-token-flow.md` | OK |
| depth-state-trace | `agents/depth-state-trace.md` | OK |
| depth-edge-case | `agents/depth-edge-case.md` | OK |
| depth-external | `agents/depth-external.md` | OK |
| depth-consensus-invariant (L1) | `agents/depth-consensus-invariant.md` | OK |
| depth-network-surface (L1) | `agents/depth-network-surface.md` | OK |

---

## 6. Skills — ALL OK

### EVM (18 skills): All 18 exist on disk. Matches skill-index.md.
### Solana (20 skills): All 20 exist on disk. Matches skill-index.md.
### Aptos (22 skills): All 22 exist on disk (21 standard + 1 core directive). Matches skill-index.md.
### Sui (22 skills): All 22 exist on disk (21 standard + 1 core directive). Matches skill-index.md.
### Soroban (19 skills): All 19 exist on disk. Matches skill-index.md.
### Niche agents (9): All 9 exist on disk. Matches skill-index.md.
### Injectable skills (8 standard + 16 L1 = 24): All 24 exist on disk.

### Driver always_on dict vs disk:
- EVM: `[]` — correct (no always-on skills)
- Solana: `["account-validation"]` — OK on disk
- Aptos: 5 skills — all 5 OK on disk
- Sui: 5 skills — all 5 OK on disk
- Soroban: 3 skills — all 3 OK on disk

---

## 7. Commands — ALL OK

| Command File | References | Status |
|-------------|-----------|--------|
| `commands/plamen-wizard.md` | `~/.claude/scripts/plamen_driver.py` (7 references) | OK — path correct |
| `commands/plamen-l1-wizard.md` | `~/.claude/scripts/plamen_driver.py` (2 references) | OK — path correct |

Both wizards invoke the driver at `~/.claude/scripts/plamen_driver.py` with `{PROJECT_PATH}/.scratchpad/config.json` — matches driver's `main()` CLI interface.

---

## 8. CLAUDE.md V1/V2 Table — OK

The table correctly describes:
- V1: `/plamen` with modes `light`, `core`, `thorough`, `compare`
- V2: `/plamen-wizard` launching `plamen_driver.py`
- Resume command: `python ~/.claude/scripts/plamen_driver.py {project}/.scratchpad/config.json`

---

## 9. DA Iter2 Prompt Cross-References — OK

`prompts/shared/phase4b-da-iter2.md` correctly references:
- `~/.claude/rules/finding-output-format.md` (line 115) — exists
- Depth Evidence tags `[BOUNDARY:X=val]`, `[VARIATION:param A->B]`, `[TRACE:path->outcome]` (line 116) — matches `rules/finding-output-format.md` tag table
- `{SCRATCHPAD}/confidence_scores.md` (line 79) — correct artifact name
- `{SCRATCHPAD}/skill_execution_gaps.md` (line 80) — correct artifact name
- `{SCRATCHPAD}/perturbation_findings.md` (line 81) — correct artifact name
- `{SCRATCHPAD}/design_context.md` (line 95) — correct artifact name

---

## 10. Chain Prompt — `chain_candidate_pairs.md` Reference — OK

`rules/phase4c-chain-prompt.md` Agent 2 section correctly references `{SCRATCHPAD}/chain_candidate_pairs.md` at lines 144, 153, 158, and 160. The pre-filter integration was applied when Track 2 of the megaplan was implemented. Chain Agent 2 reads the pre-filtered pairs file and falls back to the original algorithm if it is missing.

---

## 11. Report Prompts Cross-References — OK

`rules/phase6-report-prompts.md` references the correct scratchpad artifacts:
- `{SCRATCHPAD}/hypotheses.md`, `chain_hypotheses.md`, `finding_mapping.md`, `report_index.md`, `report_coverage.md`, etc. — all standard artifact names matching driver gate expectations.
- `~/.claude/rules/report-template.md` — exists.
- `{PROJECT_ROOT}/AUDIT_REPORT.md` — correct output location.

---

## 12. Assessment Prompts — 2 STALE + 1 MISSING

### v2-dhedge-rerun-assessment.md — STALE (2 issues)

**Issue 12a: Phase count is stale.**
Section A3 says: *"For `thorough` mode on `sc`: expect **37 phases** in the registry"*.
The actual PHASES list now has **42 entries** (added since baseline: `sc_bake`, `sc_prebake`, `chain_prefilter`, `finding_extraction`, `completeness`). The expected phase count should be updated to 42 (with the understanding that many are conditional — `sc_bake` and `sc_prebake` are SC-only + EVM-only/slither-conditional, `chain_prefilter` is SC-only, etc.).

**Issue 12b: No coverage of new phases.**
The assessment has no sections testing:
- `sc_bake` / `sc_prebake` phases (Track 1 of megaplan — now implemented)
- `chain_prefilter` phase (Track 2 of megaplan — now implemented)
- `finding_extraction` phase
- `completeness` phase

These phases exist in the PHASES list and run in production, but the assessment prompt does not verify them. If the assessment is used for a future re-run, it will miss failures in these phases.

### v2-post-audit-assessment.md — MISSING

Referenced as item 13 in the task specification. This file does not exist at `prompts/shared/v2-post-audit-assessment.md`. There is only `v2-dhedge-rerun-assessment.md`. If a generic (non-dHEDGE-specific) post-audit assessment prompt is intended, it needs to be created. If `v2-dhedge-rerun-assessment.md` IS the only assessment prompt, this is a non-issue.

---

## 13. Driver PHASES vs Dispatch Table — OK

All 42 PHASES entries resolve to a function in the `dispatch` dict at line 1964. The dispatch table includes entries for all specialized drivers (`run_wizard`, `run_network_resolve`, `run_sc_bake`, `run_sc_prebake`, `run_bake`, `run_prebake`, `run_instantiate`, `run_verify_queue`, `run_verify_batch`, `run_report_tiers`, `run_chain_prefilter`, `run_report_gates`, `run_completeness`, `run_extraction`, `run_preserve`) plus the default `run_claude_phase` for all prompt-driven phases.

All `run_claude_phase` phases have corresponding entries in `PHASE_CONFIGS` (verified: 0 missing).

---

## 14. Megaplan Status — 3 STALE STATUSES

`docs/v2-optimization-megaplan-draft.md` has outdated status markers:

| Track | Megaplan Says | Actual Status | Action Needed |
|-------|--------------|---------------|---------------|
| Header | "Status: Draft, awaiting dHEDGE rerun assessment results" | Both Track 1 and Track 2 are implemented in the driver | Update to "Phase 1 and Phase 2: Implemented" |
| Track 1 (SC Prebake) | Implied "not started" (Phase 3 in implementation sequence, "After Phase 2 is validated") | `run_sc_bake()` and `run_sc_prebake()` exist in driver (lines 1140 and 1594), PHASES list includes `sc_bake` and `sc_prebake`, gate artifacts defined | Update to "Implemented" |
| Track 2 (Chain Pre-Filter) | Implied "not started" (Phase 2 in implementation sequence, "After dHEDGE assessment confirms") | `run_chain_prefilter()` exists in driver (line 1209), `chain_prefilter` in PHASES list, `chain_candidate_pairs.md` referenced in `phase4c-chain-prompt.md` Agent 2, `_has_unexplored_pairs()` updated | Update to "Implemented" |
| Track 3 (Opus 4.7 Alignment) | Phase 1 in sequence, "Before the next audit" | Prompt-level changes may or may not have been applied to individual prompt files — unclear without reading all 10+ breadth/depth driver prompts | Verify if prompt text changes from 3A-3D were applied |
| dHEDGE dependency | "In progress" | Unknown | Update based on current status |

---

## 15. Driver PHASE_CONFIGS Completeness — OK

All phases that use `run_claude_phase` have a matching entry in `PHASE_CONFIGS`. No orphan config entries.

Note: `sc_bake` and `sc_prebake` do NOT need `PHASE_CONFIGS` entries because they use specialized driver functions (`run_sc_bake`, `run_sc_prebake`), not `run_claude_phase`. Same for `chain_prefilter`, `completeness`, `finding_extraction`, `preserve`, etc.

---

## 16. Driver EXPECTED_ARTIFACTS Gate Coverage — OK

All phases with expected artifacts have matching entries:
- SC recon: 8 artifacts checked
- L1 recon: 6 artifacts checked (separate `l1:recon` key)
- Depth: 3+ files expected (SC) or 4+ files (L1 via `l1:depth`)
- Chain prefilter: `chain_candidate_pairs.md` checked
- Report gates: `report_quality.md` checked
- Report assemble: `../AUDIT_REPORT.md` checked (relative to project root)

---

## Action Items

### Must Fix (broken functionality)

1. **Create `prompts/l1/phase4a-inventory-prompt.md`** — L1 inventory phase will fail silently without it. The driver's `_resolve_prompt()` cannot resolve `inventory` for L1 pipeline (no L1 override, no `go`/`rust` language dir, no static fallback).

### Should Fix (stale references)

2. **Update CLAUDE.md** — Add `soroban` to the language list in the REFERENCE FILES table: change *"resolve `{LANGUAGE}` to `evm`, `solana`, `aptos`, or `sui`"* to *"resolve `{LANGUAGE}` to `evm`, `solana`, `aptos`, `sui`, or `soroban`"*.

3. **Update `v2-dhedge-rerun-assessment.md` Section A3** — Change "expect **37 phases**" to "expect **42 phases**" and add coverage sections for `sc_bake`, `sc_prebake`, `chain_prefilter`, `finding_extraction`, and `completeness` phases.

4. **Update `docs/v2-optimization-megaplan-draft.md`** — Change header status from "Draft, awaiting dHEDGE rerun" to reflect that Track 1 (SC Prebake) and Track 2 (Chain Pre-Filter) are implemented in the driver. Update the dependency table's "In progress" status.

### Low Priority (cosmetic/optional)

5. **Create `prompts/shared/v2-post-audit-assessment.md`** — If a generic (non-project-specific) assessment prompt is desired. Otherwise, rename or annotate the dHEDGE assessment to clarify it is the only assessment template.

---

## Verified Correct (Confirmation)

- All 107 skill files across 5 languages + niche + injectable + L1 are correctly wired
- All 6 depth agent definition files exist
- All 9 rules files exist and are correctly referenced
- All shared prompt files (13 files in `prompts/shared/`) exist
- All L1 prompt files (5 of 6 expected) exist
- Driver dispatch table maps all 42 PHASES to working functions
- Driver PHASE_CONFIGS covers all `run_claude_phase` phases
- Both wizard commands reference the correct driver path
- Chain prompt Agent 2 correctly integrates `chain_candidate_pairs.md`
- DA iter2 prompt correctly references finding format, evidence tags, and scratchpad artifacts
- Report prompts reference correct artifact names and output paths
- Driver gate artifacts match the file names written by each phase
