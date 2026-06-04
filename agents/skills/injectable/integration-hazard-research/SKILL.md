---
name: "integration-hazard-research"
description: "Protocol Type Trigger NAMED_EXTERNAL_PROTOCOL (detected when recon finds import/interface for an identifiable external protocol — not standard libraries). Researches known integration hazards of the target protocol."
---

# Injectable Skill: Integration Hazard Research

> **Protocol Type Trigger**: `NAMED_EXTERNAL_PROTOCOL` — detected when recon identifies imports or interface calls to a named external protocol (Uniswap, Aave, Balancer, Compound, Curve, Chainlink, Lido, MakerDAO, CCA, etc.) that is NOT a standard library (OpenZeppelin, solmate, solady) and NOT the protocol under audit itself.
> **Inject Into**: depth-external agent
> **Language**: All chains (EVM primary; Solana/Aptos/Sui when integrating with named on-chain protocols)
> **Finding prefix**: `[IHR-N]`
> **Added in**: v1.1.5

## Orchestrator Decomposition Guide

This skill adds a **research phase** (Section 0) before depth-external's existing code analysis. All sections map to depth-external's domain. The orchestrator includes this skill in the depth-external agent's prompt when `NAMED_EXTERNAL_PROTOCOL` is flagged.

When decomposing into investigation questions:
- Section 0 (research): produces a hazard catalog that informs all of depth-external's existing Sections 1-4
- Section 1 (third-party race): extends depth-external Section 1 (side effects)
- Section 2 (state TOCTOU): extends depth-external Section 4 (governance/parameter change)

## When This Skill Activates

Recon Agent 3 detects named external protocol imports during TASK 6 pattern scanning. Indicators:
- Named protocol interfaces: `IUniswapV2Router`, `IUniswapV3Pool`, `IBalancerVault`, `IPool`, `IAToken`, `ICToken`, `ILendingPool`, `ICurvePool`, `IChainlinkAggregator`, `IStETH`, `IContinuousClearingAuction`
- Named protocol library imports: `@uniswap/`, `@aave/`, `@balancer-labs/`, `@chainlink/`, `@openzeppelin/` (only when calling protocol-specific functions, not generic utilities)
- Solana: CPI targets to known program IDs (Jupiter, Marinade, Raydium, Orca, Drift)
- Sui: external package calls to known protocols (Cetus, DeepBook, Suilend, NAVI)
- Aptos: external module calls to known protocols (Thala, Echelon, Liquidswap, PancakeSwap)
- Soroban: cross-contract calls to known protocols (SoroSwap, Blend Protocol, Phoenix DEX, Aqua Network, OrbitCDP)

The flag records which protocols were detected: `NAMED_EXTERNAL_PROTOCOL: [Uniswap V3, Chainlink]`

---

## Processing Protocol (MANDATORY)

For each section below, execute in order:
1. **ENUMERATE targets**: List every entity the section applies to (external protocols, hazard catalog entries, race condition candidates, TOCTOU pairs) as a numbered list before analysis begins.
2. **PROCESS exhaustively**: Analyze each numbered entity. Mark each "DONE" or "N/A (reason)" before moving to the next.
3. **COVERAGE GATE**: Count enumerated vs processed. If any entity lacks a marker, process it before proceeding to the next section.

## 0. Target Protocol Hazard Research

For EACH named external protocol detected by recon:

### 0a. Solodit Search (two queries per protocol, parallel)

```
// Query 1: Known integration issues
search_solodit_live(
  keywords="{protocol_name} integration vulnerability",
  impact=["HIGH", "CRITICAL"],
  quality_score=3,
  sort_by="Quality",
  max_results=10
)
// Query 2: Known footguns and edge cases
search_solodit_live(
  keywords="{protocol_name} caller assumption edge case",
  impact=["HIGH", "MEDIUM"],
  sort_by="Rarity",
  max_results=10
)
```

### 0b. Tavily Search

```
tavily_search(query="{protocol_name} smart contract integration pitfalls known issues audit 2024 2025 2026")
```

### 0c. Compile Hazard Catalog

| Target Protocol | Known Integration Hazard | Severity | Root Cause | Source | Applicable to This Integration? |
|----------------|------------------------|----------|------------|--------|-------------------------------|
| {protocol} | {hazard title} | {sev} | {brief root cause} | {Solodit ID / URL} | YES / NO / CHECK |

**Applicability criteria** (same as FORK_ANCESTRY):
- YES: The audited code calls the function or reads the state involved in this hazard
- NO: The audited code does not interact with the affected function/state (document why)
- CHECK: Cannot determine without deeper analysis — flag for depth trace

### 0c-bis. Asset-Form Delivery Check (MANDATORY for every bridge/gateway/router callback + outbound deposit/withdraw)

Cross-chain gateways frequently deliver the gas token in a DIFFERENT FORM than the handler assumes — native vs wrapped (ETH vs WETH, ZETA vs WZETA, native SOL vs wrapped). This is an asset-FORM mismatch, distinct from the asset-IDENTITY (toToken/targetZRC20) mismatch. For EACH inbound callback (`onCall`/`onReceive`/`onRevert`/router callback) and EACH outbound `deposit`/`withdraw`:
1. From the external protocol's docs/source, determine whether it DELIVERS/EXPECTS the asset in **NATIVE or WRAPPED** form (many gas-token bridges auto-unwrap to native on delivery and auto-wrap on send).
2. Assert the handler's FIRST value-moving op matches that form: a wrapped-ERC20 `approve`/`transferFrom`/`swap` requires the asset to already be wrapped; a `.call{value:}`/native send requires it to be native.
3. **RED FLAG → emit a CHECK candidate** (never a silent `UNVERIFIED`): the callback names a wrapped-token address as the asset param but the handler moves it via ERC20 `approve`/`transfer` with NO `deposit{value:}`/`withdraw()` reconciliation (or vice-versa). The missing inbound native→wrapped (or wrapped→native) conversion makes the swap/transfer revert and traps user funds.
4. If the delivery form cannot be confirmed from docs/source, escalate to a CHECK candidate for depth tracing — do NOT drop it as `UNVERIFIED`.

### 0d. Hardcoded Hazard Floor (Fallback if RAG/Web Search Fails)

If Solodit AND Tavily BOTH fail, check EACH applicable protocol against this minimum catalog:

| Target Protocol | Critical Integration Hazard | Root Cause | Check For |
|----------------|---------------------------|------------|-----------|
| Uniswap V2/V3 | Slippage bypass via `amountOutMin=0` | Caller passes zero slippage tolerance | Zero slippage in any swap call |
| Uniswap V3 | Stale pool observation | `observe()` returns stale TWAP if pool is inactive | TWAP reads without freshness check |
| Uniswap V4 | Hook reentrancy into caller | V4 hooks execute arbitrary code during swap | State changes before/after V4 swap call |
| Aave V2/V3 | Flash loan + oracle manipulation | Flash-borrowed tokens shift oracle price within same tx | Oracle reads after Aave interaction |
| Aave V3 | E-mode collateral factor assumption | E-mode factors change by governance | Hardcoded LTV assumptions |
| Compound V2 | Exchange rate manipulation on empty market | First-depositor rounding exploit on cToken | cToken interaction with low totalSupply |
| Compound V3 (Comet) | Absorb front-running | Liquidation absorb is permissionless and competitive | Liquidation timing assumptions |
| Balancer V2 | Read-only reentrancy via pool balances | View functions read stale state during callback | Balance queries during or after Balancer interaction |
| Curve | Raw ETH reentrancy in remove_liquidity | ETH transfer callback before state update | State reads after Curve ETH pool interaction |
| Chainlink | Stale price from sequencer downtime | L2 sequencer outage returns last known price | Price consumption without sequencer uptime check |
| Chainlink | Deprecated aggregator migration | Price feed address changes after aggregator migration | Hardcoded feed addresses |
| Lido | stETH rebasing balance changes | balanceOf changes between blocks without transfers | stETH balance used as accounting input |
| Lido | wstETH/stETH exchange rate assumption | Rate changes with each rebase | Hardcoded or cached conversion rate |
| MakerDAO | DAI Savings Rate (DSR) flash manipulation | `pot.drip()` is permissionless and changes chi | State reads dependent on DSR rate |
| CCA (Uniswap) | Permissionless `claimTokens()` zeroes `tokensFilled` | Anyone can claim for any bidId | Reading `tokensFilled` after `claimBlock` |
| **Solana** | | | |
| Jupiter | Slippage bypass via `0` slippage on `route()` / `shared_accounts_route()` | Caller passes zero min-out | Zero slippage tolerance in CPI args |
| Jupiter | Stale quote across slots | Quote computed in slot N, executed in slot N+K with changed pool state | Cross-slot quote freshness |
| Marinade | Delayed unstake ticket manipulation | `OrderUnstake` creates tickets claimable after epoch boundary | Ticket state reads after epoch change |
| Marinade | mSOL/SOL exchange rate assumption | Rate changes with each epoch stake reward distribution | Cached or hardcoded mSOL conversion rate |
| Raydium | Concentrated liquidity tick manipulation | Price manipulation within tick range via large swaps | Oracle/price reads from Raydium pool state |
| Orca (Whirlpool) | Position NFT metadata assumption | Whirlpool positions are NFTs; burning/transferring changes liquidity state | Reads of position data without ownership check |
| Pyth | Stale price from publisher downtime | `price.publish_time` can be arbitrarily old | Price consumption without `max_staleness` check |
| Pyth | Confidence interval ignored | Price has wide confidence interval during volatility | Using `price.price` without checking `price.conf` |
| **Sui** | | | |
| Cetus | Concentrated liquidity tick crossing DoS | Extreme tick density causes gas exhaustion on swap | Swap calls into Cetus pools with narrow tick spacing |
| DeepBook | Order cancellation race | Permissionless cancel on expired orders | Reading order state that a third party can cancel |
| Pyth (Sui) | Stale price via `PriceInfoObject` | Price update transaction may not be in same PTB | Price reads without freshness assertion |
| **Aptos** | | | |
| Thala | ThalaSwap stable pool invariant at low liquidity | Amplification factor causes extreme slippage at pool edges | Swap calls into Thala stable pools near depletion |
| Liquidswap | Unchecked curve type | Stable vs uncorrelated curve selection affects pricing | Hardcoded curve type assumption |
| Pyth (Aptos) | Stale price from `pyth::get_price()` | Price staleness not enforced by default | Price reads without `get_price_no_older_than()` |
| **ZetaChain / cross-chain gas-token gateways** | | | |
| ZetaChain Gateway | Gas token delivered/expected in the wrong FORM (native ZETA vs WZETA, native ETH vs WETH) | Gateway auto-(un)wraps the gas token; the handler assumes the other form and skips the conversion | `onCall`/`onRevert`/deposit path: is the inbound native↔wrapped conversion present, and does the first value-moving op's form match the delivered form? (see 0c-bis) |

**Note**: This floor catalog covers only the most critical hazards. RAG/web results typically surface 3-5x more per protocol. Treat the floor as minimum coverage, not exhaustive.

### 0e. Record Research Results

Write the hazard catalog to `{SCRATCHPAD}/integration_hazard_catalog.md`. This file is consumed by:
- depth-external (this agent) for Sections 1-4 analysis
- Chain analysis for enabler enumeration (integration hazards may create preconditions for other findings)

---

## 1. Third-Party Race Conditions

For each YES/CHECK hazard in the catalog, ask:

**Permissionless function race**: Does the target protocol expose permissionless functions that modify state the audited code depends on? Can a third party call that function between the audited code's transactions?

| Target Function | Permissionless? | State Modified | Our Code Reads This State? | Race Window |
|----------------|----------------|----------------|---------------------------|-------------|
| {function} | YES/NO | {state var} | {where our code reads it} | {blocks/time} |

For each race with `Permissionless=YES` AND `Our Code Reads=YES`:
- Trace what happens if the third party acts first. Does our code read stale/zeroed state?
- Is the race atomic (same block) or cross-block?
- Can our code detect and recover, or is the damage permanent?

Tag: `[TRACE:third_party_call({target_function}) → our_read({our_function}) → state_is={stale/zeroed/modified} → impact={loss/revert/incorrect_accounting}]`

---

## 2. Integration State TOCTOU

For each external state the audited code reads:

| State Read | Read Location | Used At | Time Between Read and Use | Can State Change In Window? |
|-----------|---------------|---------|---------------------------|---------------------------|
| {value} | {our function:line} | {downstream use:line} | {same tx / cross-tx / cross-block} | YES/NO |

For each `Can Change=YES`:
- What changes it? (governance, permissionless function, oracle update, rebase, fee accrual)
- What is the worst-case delta in realistic conditions?
- Does the audited code have a freshness check or snapshot mechanism?

Tag: `[VARIATION:external_state({value}) fresh_at_read=X → stale_at_use=Y → delta={amount} → impact={consequence}]`

---

## Common False Positives

- **Hazard catalog hit with NO matching code path**: A known hazard exists for Protocol X, but the audited code never calls the affected function or reads the affected state. Do NOT report.
- **Permissionless function race with atomic protection**: If the audited code reads and uses external state within a single transaction AND verifies the result (e.g., slippage check, balance delta), the race window is zero. Do NOT report.
- **State TOCTOU with governance-only trigger**: If external state can only change via governance (timelock, multisig), and the audited code operates on a per-transaction timescale, the TOCTOU risk is informational at most.

**Coverage assertion**: Before returning, verify every entity enumerated under each section has been processed. Report enumerated vs analyzed counts in your return message.

## Step Execution Checklist (MANDATORY)

| Section | Required | Completed? | Notes |
|---------|----------|------------|-------|
| 0a. Solodit search per target protocol | YES | Y/N/? | |
| 0b. Tavily search per target protocol | YES (fallback: 0d floor catalog) | Y/N/? | |
| 0c. Hazard catalog compiled | YES | Y/N/? | |
| 0d. Floor catalog checked (if RAG failed) | IF 0a+0b failed | Y/N/? | |
| 0e. integration_hazard_catalog.md written | YES | Y/N/? | |
| 1. Third-party race conditions | FOR EACH YES/CHECK hazard | Y/N/? | |
| 2. Integration state TOCTOU | FOR EACH external state read | Y/N/? | |
