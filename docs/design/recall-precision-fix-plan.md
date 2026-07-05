# Recall/Precision Fix Plan — post-v2.2.2 diagnosis

> Four root causes, each verified in code + scratchpad data from the DODO(EVM)+Spectra(Soroban) `--fresh` reruns. Ordered by leverage. All fixes are surgical, additive, and anti-overfit (HOW not WHAT).

---

## Root cause 1 — Attribution is unmeasurable (BLOCKS every other verdict)

**Verified**: `promote_axis_findings_to_inventory` (enumeration_gate.py) stamps
`**Source IDs**: AXIS-101 (multi-axis coverage meta-pass)` — a real tag, BUT:
- (a) it is `AXIS-101`, not a clean greppable class token like `AXISGAP`; and
- (b) when `sc_semantic_dedup` (4e) merges an AXIS/CI finding INTO a normal
  twin, the survivor keeps the NORMAL source and the "also independently found
  by M2/M1" fact is **dropped**. So a GT bug M2 found looks 100% normal-sourced.

This is why two independent attribution passes read "0 M1/M2 recalls" — the
instrument is broken, not the mechanism.

### Fix 1 (enumeration_gate.py + plamen_mechanical dedup merge)
1. In `promote_axis_findings_to_inventory` / `promote_invariant_*`: add a clean,
   machine-greppable class token to the Source IDs line, e.g.
   `**Source IDs**: AXISGAP:AXIS-101 (...)` and `**Source IDs**: INVARIANT:CI-3 (...)`.
   Keep the human parenthetical.
2. In the semantic-dedup MERGE path: when the absorbed finding carries an
   `AXISGAP`/`INVARIANT` source and the survivor does not, **append the absorbed
   token to the survivor's Source IDs** (provenance-preserving merge), e.g.
   survivor becomes `Source IDs: TF-4, AXISGAP:AXIS-102 (co-found)`. Never drop a
   generator's provenance on merge — mirrors the existing recall-safe "carry
   real content" rule.
3. Add a driver-inline attribution ledger `mechanism_attribution.md`: for each
   report finding, list every Source token that contributed (normal + AXISGAP +
   INVARIANT). This makes "did M2/M1 co-find this GT item" a mechanical grep, not
   an LLM guess.

**Test**: fixture inventory with an AXIS finding + a normal twin on the same
location → dedup merges → assert the survivor's Source IDs contains BOTH the
normal token and `AXISGAP:`. Assert `mechanism_attribution.md` lists both.

**Anti-overfit**: pure provenance bookkeeping; no protocol content.

---

## Root cause 2 — M1 is structurally starved (dormant by design)

**Verified**: CI emission is gated on **NO-GAP dispositions** in the skeptic
prompt (phase4b6 §"Committed-Invariant Emission" L194: "Whenever you record
NO-GAP for a value-bearing instance…"). But the SAME prompt says (L190) "When in
doubt between NO-GAP and ADD, choose ADD" — so a skeptic doing its job emits
FEW NO-GAPs → FEW CI blocks. DODO: 4 CI blocks total, 2 promoted, 1 refuted.
Meanwhile the biggest reservoir of "concluded-safe" verdicts — the **depth
phase** — only *optionally* emits CI (phase4b-depth hook is "optional").

So M1 fires on the rarest disposition and skips the richest one. It never got a
fair test.

### Fix 2 (prompt hooks — phase4b-depth.md + phase5-verification)
1. **Move M1's primary emission to the depth + verify phases**, where "this path
   is safe / this defense holds / REFUTED" verdicts are abundant. Make the CI
   block emission **mandatory (not optional)** on any depth/verify verdict that
   REFUTES or CLEARS a value-bearing path — that is exactly the Code-Augur
   "commit the invariant behind every safe judgment" locus.
2. Keep the skeptic NO-GAP hook as a secondary emitter (unchanged).
3. Widen the deriver `compute_invariant_assertion_candidates` to also scan
   `depth_*_findings.md` and `verify_*.md` for CI blocks (currently skeptic-only).

**Test**: fixture depth output with a REFUTED value-bearing finding + no CI block
→ new soft validator warns; with a CI block → deriver emits an INVARIANT
candidate. Assert depth/verify CI blocks are scanned.

**Anti-overfit**: the 6 invariant shapes stay generic; only the emission *trigger*
moves to where safe-conclusions actually live.

---

## Root cause 3 — M2 is EVM-favored via depth-tag starvation

**Verified (measured)**: M2's axis-EXAMINED detection reads ONLY the closed
depth-evidence tag vocabulary. Tag density: DODO 104 tags (T34/B44/V26) vs
Spectra 52 (T19/B13/V20). Result: Spectra 92% of cells flagged GAP (vs EVM 67%),
but most are **false gaps** (axis WAS examined, just not tagged in the recognized
form) → worker burns budget re-examining covered ground → 41% confirm-rate vs
EVM's 73%.

Two contributing factors: (a) Soroban depth templates emit fewer/differently-
phrased depth-evidence tags; (b) the Rust value-effect regex (`+=`, `.push(`)
over-matches non-value code, diluting the Soroban hot-40.

### Fix 3a (Soroban depth-tag emission — prompts/soroban/phase4b-depth-templates.md)
Bring Soroban depth-evidence tag emission to EVM parity: require the depth agent
to stamp `[TRACE:…→outcome]`, `[BOUNDARY:X=0/MAX]`, `[VARIATION:…]` on Soroban
findings the same way the EVM template does. This is an UPSTREAM fix that feeds
M2 (and confidence scoring, and chain) — not an M2-specific patch. Recall-safe:
more tagging never removes a finding.

### Fix 3b (M2 EXAMINED-detection robustness — enumeration_gate.py)
Add a **secondary EXAMINED signal** beyond the closed tags: if a finding block
resolves to function `f` AND its Description/Impact concretely addresses axis A's
concern (mechanical substring cues already defined: `_MH_LIVENESS`,
`_STALENESS_CUE`, BALANCE/ACCESS postcondition), count the axis EXAMINED even
without a bracketed tag. Keeps "ambiguous ⇒ GAP" as the floor but stops
false-GAP inflation when the depth agent described the axis in prose without the
exact tag. This directly closes the DODO/Spectra asymmetry.

### Fix 3c (Rust value-effect regex — enumeration_gate.py `_LANG["rust"]`)
Tighten the Rust `effect` pattern: drop bare `+=`/`.push(` (noise), keep
token-movement (`transfer|transfer_from|mint|burn|token::transfer|TokenClient`).
Reduces hot-set dilution on Soroban. Generic Rust, no protocol names.

**Test**: fixture with a prose-only liveness finding (no `[TRACE:→revert]` tag) →
Fix 3b marks liveness EXAMINED (not GAP). Fixture Rust file with `+=` but no
token move → not hot under tightened regex.

**Anti-overfit**: axes/cues/regex all generic; this fixes an ecosystem-maturity
gap, not a protocol.

---

## Root cause 4 — Precision: pairwise dedup leaves N co-referent survivors (fragmentation)

**Verified**: The H-05≡H-09 duplicate is NOT a dedup miss at the pair level —
dedup correctly merged INV-015→016, INV-017→018, INV-019→020 (three pairs of the
"public withdraw" bug). But those **three survivors (INV-016/018/020) are all the
same bug** and were never merged with each other → the report shows H-05, H-09,
and a third as separate findings. Semantic dedup is **pairwise/local**; it does
not cluster N co-referent findings. This is the ~2.5–3:1 fragmentation source
(56 C/H/M for ~15–20 real bugs on DODO).

### Fix 4 (semantic dedup — plamen_mechanical / phase4e)
Add a **transitive-closure clustering** step after pairwise dedup: build an
undirected graph over surviving findings where an edge = (same file+function AND
same root-cause/fix-pattern), then collapse each connected component to one
finding (highest severity, union of locations). This turns 3 "public withdraw"
survivors into one finding with a 3-location table — exactly the report-template
consolidation contract. Recall-safe: merges only same-root-cause+fix findings
(the existing consolidation test), never cross-tier or cross-mechanism.

Also feed the report-index STEP-1.5 consolidation the cluster map so the writer
emits ONE finding with a location table, per report-template.md
Root-Cause-Consolidation rule.

**Test**: fixture with 3 findings same file+function+fix-pattern → cluster
collapses to 1 with 3-location table; different fix-pattern → stays separate;
different tier → stays separate (no severity-blur).

**Anti-overfit**: clustering key is structural (file+function+fix-pattern); no
protocol content.

---

## Sequencing + validation

1. **Fix 1 first** (attribution) — it is the prerequisite: without it, no run can
   measure whether 2/3 worked.
2. Then Fix 2 (M1 emission), Fix 3 (M2 Soroban parity), Fix 4 (dedup clustering).
3. Full suite green after each.
4. **Re-score the EXISTING DODO+Spectra reports** with the new
   `mechanism_attribution.md` — this alone tells us M2/M1's TRUE contribution on
   data we already have (no rerun needed for attribution).
5. Then ONE clean A/B rerun per ecosystem (M1/M2 phase ON vs OFF, same tree) —
   now *measurable* — as the decisive validation. Go/no-go: M2/M1 co-find ≥1 GT
   item NO normal source found, AND fragmentation ratio drops, AND precision
   ≤3pp.

## Anti-overfit self-audit
Every fix is provenance bookkeeping (1), an emission-trigger move (2), an
ecosystem-parity/robustness change (3), or structural clustering (4). None
encodes a protocol, token, function name, or stored finding. Clears Part-0.
