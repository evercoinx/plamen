import pathlib

content = """# Report Dedup Agent Decisions
# PulseChain GameWards
# Generated: 2026-06-22

---

## SECTION 1: MERGE PROPOSALS

Each row proposes merging the Absorbed finding INTO the Survivor. The Survivor's
section in AUDIT_REPORT.md must be expanded to cover the Absorbed finding's
Description/Impact/Location; the Absorbed finding's section is removed.

| Survivor ID | Absorbed ID | Survivor Severity | Absorbed Severity | Same Root Cause | Merge Rationale |
|-------------|-------------|-------------------|-------------------|-----------------|-----------------|
| H-05 | M-05 | High | Medium | YES | Identical wrong-array bounds check: claimCommunityReward validates communityIndex < gameRewards.length but accesses winningCommunity[communityIndex]. Same location (RewardDistribution.sol:216,226,242), same fix (replace gameRewards.length with winningCommunity.length). H-05 [POC-PASS]; M-05 [VERIFIED]. H-05 already covers both PW and CZ branches. Reader understands both from H-05 alone. |
| H-09 | L-04 | High | Low | YES | Both describe declareCommunityWinner accepting _amount==0 which sets hasClaimed/CzClaims permanently for zero payout. H-09 is the chain finding; L-04 is the standalone constituent (internal H-35). Consolidation Map: H-09=CH-01->H-16+H-35 where H-35 maps to L-04. Same location (RewardDistribution.sol:150-159,228,244), same fix (require(_amount > 0)). L-04 adds nothing not already in H-09. |
| H-01 | M-04 | High | Medium | YES | Both describe absence of solvency check in declareWinner/addReward allowing declared obligations to exceed funded ETH pool. H-01 Location already lists MainGameReward.sol:90-106,161-167 and explicitly states same gap exists in MainGameReward. M-04 Location covers MainGameReward.sol:77-81,90-106,132-151. Same root cause and fix (add outstanding-obligations accumulator). H-01 [VERIFIED/POC-PASS]; M-04 includes [MEDUSA-PASS] -- survivor H-01 must absorb that evidence note. |
| H-02 | M-21 | High | Medium | YES | Both describe withdrawAmt in RewardDistribution.sol:259-266 allowing authorized address to exfiltrate reward funds to arbitrary treasury with no solvency floor. H-02 already covers the unconstrained _treasury angle. Same fix (add reserve floor + restrict _treasury to pre-approved address). Both VERIFIED. |
| H-10 | M-18 | High | Medium | YES | Both describe declareWinner accepting duplicate NFT IDs due to missing per-NFT uniqueness check. H-10 is chain finding (duplicate + editWinner redirect -> extra payment); M-18 is standalone duplicate-declaration finding. Internal: H-10=CH-02->H-20+H-7 where H-20 maps to M-18. H-10 Description covers same nftID pushed multiple times at RewardDistribution.sol:107-123. Same fix (add gameNftDeclared dedup mapping). |
| H-08 | M-09 | High | Medium | YES | Both describe MiniGame.plsPrice=0 (uninitialized, no bounds guard) causing div-by-zero panic in DAI->PLS swaps and zero output in PLS->DAI swaps. H-08 states plsPrice defaults to 0 (never initialized in constructor). M-09 four internal hypotheses (H-26+H-27+H-54+H-64) all resolve to same zero/uninitialized plsPrice division hazard. Same fix (require plsPrice>0 in quote; initialize in constructor). H-08 [POC-PASS]; M-09 [VERIFIED]. |
| M-14 | M-15 | Medium | Medium | YES | Both describe strict earned==1 check in claimCommunityReward combined with append-only badge array permanently bricking multi-badge NFT holders. M-14 at RewardDistribution.sol:224,240; M-15 adds PulseWars.sol:197-198,328-333 and RewardDistribution.sol:224-225,240-241. Identical root cause and fix (change earned==1 to earned>=1 with explicit winning-badge scan). Both VERIFIED. Equal severity; M-14 (H-14) is earlier hypothesis; M-15 (H-60) is depth re-discovery. M-15 PulseWars.sol locations must be added to M-14 Location. |
| M-17 | L-16 | Medium | Low | YES | Both describe Citizens.join replay vulnerability: single whitelisted-partner NFT replayed indefinitely mints unlimited Citizen tokens. M-17 = missing partner-NFT uniqueness/replay protection at Citizens.sol:267-290. L-16 = no (partner,ID) replay guard at Citizens.sol:267-290,277-279,285,287-288. Core defect identical: no partnerNftUsed[partner][ID] guard. Same primary fix (add partnerNftUsed mapping). L-16 also contains CEI violation (_safeMint before fee at L285/L287-288); survivor M-17 must absorb that CEI note so it is not lost. L-16 [POC-PASS]; M-17 [VERIFIED]. |
| M-23 | L-23 | Medium | Low | YES | Both describe renounceOwnership() not overridden in Authorizable, allowing single call to permanently brick all onlyOwner functions including unpauseContract(). M-23 at utils/Authorizable.sol:7; L-23 at utils/Authorizable.sol:6 -- same declaration line. Identical root cause and fix (override renounceOwnership() in Authorizable to revert). Both VERIFIED with POC-PASS. Survivor M-23 (Medium > Low). |
| M-13 | L-19 | Medium | Low | YES | Both describe Citizens.addBadge (and PulseWars sibling) callable directly by any authorized address, bypassing per-badge supply cap and per-NFT dedup enforced in Badges.claimBadge. M-13 explicitly covers this bypass. L-19 covers same at Citizens.sol:213-216; Badges.sol:67-98,104-109. Same root cause and fix (restrict Citizens.addBadge to Badges contract only). M-13 [VERIFIED]; L-19 [POC-PASS]. Survivor M-13 (Medium > Low). M-13 must absorb L-19 Badges.sol:67-98,104-109 location reference. |
| M-13 | L-20 | Medium | Low | YES | Both describe direct Citizens.addBadge by authorized address pushing second badge causing earned>=2, permanently reverting claimCommunityReward strict earned==1 check. M-13 covers Citizens.sol addBadge and RewardDistribution.sol:223-224,239-240. L-20 covers Citizens.sol:213-216 and RewardDistribution.sol:234-240. Identical mechanism and harm, same fix. M-13 [VERIFIED]; L-20 [POC-PASS]. Survivor M-13 (Medium > Low). |
| L-14 | I-01 | Low | Informational | YES | Both describe absence of event emissions when authorized mapping is mutated in Authorizable. L-14 Location is utils/Authorizable.sol:20-28 (same as I-01). L-14 Title explicitly lists add/removeAuthorized as covered sites. I-01 = addAuthorized and removeAuthorized emit no events -- identical root cause and fix (add AuthorizedAdded/AuthorizedRemoved events). L-14 [VERIFIED]; I-01 [UNVERIFIED]. Survivor L-14 (Low > Informational). L-14 already lists Authorizable.sol:20-28 in Location; no new location must be added. |

---

## SECTION 2: REVIEWED -- KEPT SEPARATE

Findings examined but NOT merged. Every row carries a specific reason grounded
in Description, Impact, or Recommendation text.

| Finding A | Finding B | Same Root Cause | Reason to Keep Separate |
|-----------|-----------|-----------------|-------------------------|
| H-08 | M-08 | NO | H-08 = zero/uninitialized plsPrice causes div-by-zero panic (fix: require(plsPrice>0) + initialize in constructor). M-08 = instant observable updatePlsPrice enables front-running/swap-sandwich (fix: timelock or commit-reveal on price updates). Different specific defects at same price variable, different fixes. |
| H-03 | M-20 | NO | H-03 = editWinner/editCommunityWinner lack TIME-GATE allowing reassignment after declaration (fix: time-gate + payee binding). M-20 = editWinner can re-point reward to DIFFERENT nftID, invalidating original winner claim (fix: forbid changing nftID, only allow editing amount). Different primary harms, different fixes. H-03 explicitly cross-references M-20 as a distinct finding. |
| H-09 | M-03 | NO | H-09 is the CHAIN finding (M-03 + L-04 together produce worse combined effect: all pending claims zeroed). M-03 is standalone scalar-reprice finding with independent harm path -- even non-zero amounts retroactively reprice all unclaimed winners. Different fixes: M-03 needs per-winner reward storage; L-04/H-09 need require _amount>0. |
| H-10 | H-03 | NO | H-10 chain uses H-03 as constituent (editWinner no-time-gate). H-03 also covers standalone editCommunityWinner path not covered by H-10. Keeping H-03 preserves that distinct angle. Cross-referenced in report. |
| H-11 | H-03 | NO | Same logic as H-10/H-03 row. H-11 chain uses H-03 constituent. H-03 stays independent to cover standalone editCommunityWinner path. |
| M-23 | M-24 | NO | M-23 = renounceOwnership() not disabled (bricks onlyOwner functions). M-24 = circular role dependency: AFTER renounceOwnership, authorized operators become irrevocable while retaining fund/winner control (different postcondition). Different fixes: M-23 = override renounceOwnership; M-24 = additional restrictions on authorized role set. |
| H-01 | M-27 | NO | H-01 = no solvency check in declareWinner (fix: add invariant at declaration time). M-27 = write-only accumulators with no reconciliation across whole lifecycle (fix: accumulator decrements at claim/edit). Different scope and different fixes. Report cross-reference links them. |
| H-02 | M-27 | NO | H-02 = withdrawAmt no reserve floor (active drain path, fix: add solvency floor). M-27 = write-only accumulators make underflow guard meaningless (root cause explanation, fix: accumulator decrements at claim/edit). Related but distinct fixes. |
| M-13 | M-14 | NO | M-13 = authorized caller adds 2nd badge to block community reward claim (access-control gap: direct addBadge bypass). M-14 = strict earned==1 check itself locks multi-badge holders even those who received badges legitimately. Fixing M-13 alone does not fix M-14 (legitimate multi-badge holders through Badges.claimBadge remain locked). Different root causes, different fixes. |
| M-13 | M-15 | NO | M-15 = append-only badge array + strict earned==1 design flaw. Would exist as problem even if M-13 were fixed. Different root causes, different fixes. |
| M-14 | M-16 | NO | M-14 = strict earned==1 check locks multi-badge holders (RewardDistribution.sol logic). M-16 = Badges.claimBadge lifetime per-NFT lock (hasCitizenClaimed) blocks multi-round badge claims (Badges.sol logic). Different mechanisms, different contract layers, different fixes. |
| L-06 | L-07 | NO | L-06 = declareWinner/declareCommunityWinner lack ifNotEnded (post-deadline declarations strand winners, different contracts and mechanism from badge system). L-07 = editBadge <= boundary lets authorized caller halt badge claims. Different contracts, mechanisms, fixes. |
| L-17 | L-18 | NO | L-17 = editCommunityWinner omits badge-existence check (allows badge id 0 to brick community reward slot). L-18 = game-reward path accepts unminted nftID, locking declared funds. Different functions, different checks, different impacts, different fixes. |
| I-02 | I-03 | NO | I-02 = CEI violation in payout functions (latent reentrancy, mitigated by nonReentrant). I-03 = swap output truncates to zero and consumes trade slot (no minimum-output guard). Different functions, different defects, different fixes. |
| I-04 | I-05 | NO | I-04 = Coins/GameLogic/CoinUtils may be unreachable from deployed contract set (deployment configuration gap). I-05 = MiniGame.lastUpdatedTime write-only dead state (no on-chain staleness gate). Unrelated structural observations. |

---

## SECTION 3: QUALITY OBSERVATION RECLASSIFICATIONS

Findings proposed for reclassification from full-section format to Quality
Observations megasection table. Criteria: Low or Informational severity,
unambiguously cosmetic class, NO plausible security impact.

| Finding ID | Severity | Proposed Class | Rationale |
|------------|----------|----------------|-----------|
| L-24 | Low | dead_code | EndTimeUpdated event declared in both MainGameReward.sol:43 and RewardDistribution.sol:55 but never emitted anywhere in the codebase. Finding explicitly states no on-chain value or access-control consequence and structural observability defect with no current on-chain harm. Future-upgrade risk is speculative. No setter was implemented to emit it. Event declaration is dead code. Qualifies as dead_code class under Quality Observations megasection. |

---

## SECTION 4: FINDINGS CONFIRMED NOT RECLASSIFIABLE AS QUALITY OBSERVATIONS

Low and Informational findings reviewed and kept in full-section format because
they have plausible security impact.

| Finding ID | Reason Not Reclassifiable |
|------------|--------------------------|
| L-01 | Trust/access-control -- partner ownerOf can return attacker-controlled address. Plausible security impact (unauthorized minting). |
| L-02 | Fund-routing risk -- zero partner-royalty address causes DoS in _setTokenRoyalty; stale defaultRoyalty causes incorrect payouts. Security impact. |
| L-03 | Compounds PRNG predictability (H-07) -- modulo bias in Fisher-Yates lets minters manipulate coin portfolio selection. Security impact (economic). |
| L-05 | Off-chain data integrity -- wrong loop index in event causes indexers/UIs to display incorrect community winner slot. Observable state corruption off-chain. |
| L-06 | Access control / fund-stranding -- post-deadline declarations lock winners ETH permanently. Security impact (fund lock). |
| L-07 | Access control / DoS -- authorized caller can halt badge claims by clamping maxClaimable. Security impact. |
| L-08 | Access control / DoS -- setRoundStartTime can block all pregame eligibility. Security impact. |
| L-09 | DoS -- off-by-one in getAllBadges reverts view function. Security impact (observability + integration DoS). |
| L-10 | DoS -- OOB panic in paginated getGameRewards reverts callers. Security impact (DoS on data API). |
| L-11 | Economic / liveness -- pause consumes game duration, stranding trading window with no extension. Security impact (game integrity). |
| L-12 | DoS -- infinite-loop or cap overflow in category-supply controls. Security impact (mint brick). |
| L-13 | Accounting gap -- excess msg.value in addReward silently trapped and untracked. Security impact (fund lock). |
| L-14 | Observability / access control -- missing events on withdrawAmt and add/removeAuthorized. Not cosmetic (security monitoring gap). |
| L-15 | PRNG manipulation -- free zero-amount call advances RandomNumber.lastHash state. Security impact (compounds H-07 predictability). |
| L-16 | Absorbed into M-17. |
| L-17 | Access control -- authorized actor can brick community reward slot permanently by writing badge id 0. Security impact (fund lock for entire community cohort). |
| L-18 | Fund lock -- unminted nftID in declareWinner locks declared ETH until operator manually corrects. Security impact. |
| L-19 | Absorbed into M-13. |
| L-20 | Absorbed into M-13. |
| L-21 | Economic -- decimal normalization absent from all quote/swap functions. Systematic 1e12x misvaluation for 6-decimal tokens. Security impact. |
| L-22 | Incident response -- pause does not gate NFT transfers, defeating containment during exploits. Security impact. |
| L-23 | Absorbed into M-23. |
| L-25 | Division-by-zero -- priceInDaiAtStart defaults to 0, causing Panic(0x12) revert on any coin query before recordInitPrice. Security impact (DoS on start-price API). |
| I-01 | Absorbed into L-14. |
| I-02 | Latent reentrancy -- CEI violation in payout functions. Latent exploitable risk if nonReentrant guard is removed in future refactor. Not cosmetic. |
| I-03 | Economic/UX -- zero-output swap consumes trade slot (capped at 20). Security impact (game-slot DoS). |
| I-04 | Deployment configuration gap -- if Coins registry is reachable, coin-poisoning impact is real. Requires manual review. Not cosmetic. |
| I-05 | Staleness enforcement absence -- lastUpdatedTime write-only confirms no on-chain freshness gate for plsPrice. Compounds M-08/H-08. Not cosmetic. |

---

## SECTION 5: SUMMARY

### Proposed Merges (12 merge operations)

| # | Survivor | Absorbed | Net Removal |
|---|----------|----------|-------------|
| 1 | H-05 | M-05 | Remove M-05 section |
| 2 | H-09 | L-04 | Remove L-04 section |
| 3 | H-01 | M-04 | Remove M-04 section; absorb MEDUSA-PASS evidence tag into H-01 |
| 4 | H-02 | M-21 | Remove M-21 section |
| 5 | H-10 | M-18 | Remove M-18 section |
| 6 | H-08 | M-09 | Remove M-09 section |
| 7 | M-14 | M-15 | Remove M-15 section; add PulseWars.sol:197-198,328-333 to M-14 Location |
| 8 | M-17 | L-16 | Remove L-16 section; absorb CEI violation note into M-17 |
| 9 | M-23 | L-23 | Remove L-23 section |
| 10 | M-13 | L-19 | Remove L-19 section; add Badges.sol:67-98,104-109 to M-13 Location |
| 11 | M-13 | L-20 | Remove L-20 section |
| 12 | L-14 | I-01 | Remove I-01 section |

### Proposed Quality Observation Reclassifications (1)

| # | Finding | Action |
|---|---------|--------|
| 1 | L-24 | Move from full finding section to Quality Observations megasection table; class = dead_code |

### Net Severity-Count Impact (if all merges applied)

| Severity | Before | Removed | After |
|----------|--------|---------|-------|
| High | 11 | 0 | 11 |
| Medium | 27 | -5 (M-04, M-05, M-09, M-18, M-21) | 22 |
| Low | 25 | -5 full sections removed (L-04, L-16, L-19, L-20, L-23) + 1 demoted to table row (L-24) | 19 full-section body findings |
| Informational | 5 | -1 (I-01) | 4 |
| Total | 68 | -11 sections removed -1 demoted | 56 body sections |

<!-- PLAMEN_STATUS: COMPLETE -->
"""

import pathlib
p = pathlib.Path(r'D:/Programming/Web3/Private/PulsechainGameWards/contracts/contracts/.scratchpad/report_dedup_agent_decisions.md')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(content, encoding='utf-8')
size = p.stat().st_size
lines = p.read_text(encoding='utf-8').splitlines()
last_line = lines[-1]
print(f'Written: {size} bytes')
print(f'Total lines: {len(lines)}')
print(f'Last line: {last_line}')
