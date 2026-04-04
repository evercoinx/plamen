---
name: "economic-design-audit"
description: "Trigger Pattern MONETARY_PARAMETER flag (fee, rate, emission, cap, bps values) - Inject Into Breadth agents (merged via M4 hierarchy)"
---

# ECONOMIC_DESIGN_AUDIT Skill (Soroban)

> **Trigger Pattern**: MONETARY_PARAMETER flag (required)
> **Inject Into**: Breadth agents (merged via M4 hierarchy)
> **Finding prefix**: `[EDA-N]`
> **Rules referenced**: R2, R10, R13, R14

```
rate|rebase|supply|burn|emission|inflation|peg|price_cap|price_floor|
fee|reward_rate|basis_points|bps|fee_bps|spread
```

**Soroban arithmetic context**: All numeric values in Soroban use `i128` (signed 128-bit integer). There is NO floating-point arithmetic. All fee and rate formulas must use integer-only math (multiply-then-divide patterns). Overflow panics unless `checked_*` arithmetic is used; unchecked overflow in release mode wraps silently on Rust's `i128`. Division truncates toward zero (floor for positive operands).

---

## 1. Parameter Boundary Analysis

For every monetary parameter setter (rate, fee, reward rate, emission, cap, floor, BPS values):

| Parameter | Setter Function | Min Value | Max Value | Enforced? | Impact at Min | Impact at Max |
|-----------|----------------|-----------|-----------|-----------|---------------|---------------|

For each parameter: substitute min and max into ALL consuming functions.
Tag: `[BOUNDARY:param=val -> outcome]`

**Soroban-specific boundary checks**:
- Does a parameter at MAX cause `i128` overflow in multiply-then-divide operations?
  - `i128::MAX = 170_141_183_460_469_231_731_687_303_715_884_105_727`
  - `amount * rate_bps / 10_000`: if `amount = i128::MAX` and `rate_bps = 10_000`, intermediate `amount * rate_bps` overflows before division
  - Check whether code uses `checked_mul`, `saturating_mul`, or unchecked `*`
- Does a parameter at 0 cause division-by-zero? In Soroban, a panic aborts the entire transaction (host function trap). Verify `.checked_div()` or explicit `if denominator == 0` guards.
- Does a fee at 10_000 BPS (100%) cause the user to receive 0 tokens, or go negative with fees-on-fees patterns?
- Does a negative `i128` parameter value cause unexpected behavior? (signed type allows negative rates/fees if not bounds-checked)

---

## 2. Economic Invariant Identification

List all economic invariants the protocol must maintain:

| Invariant | Parameters Involved | Can Admin Break It? | Functions That Assume It |
|-----------|--------------------|-----------------------|--------------------------|

For each setter: can changing this parameter break an invariant that user-facing functions depend on? If yes → finding.

**Soroban-specific invariants**:
- **i128 conservation**: Token balances are `i128`; total tracked must equal sum of individual claims. Verify no rounding loss accumulates across many users.
- **No negative balances**: `i128` allows negative values. If a subtraction underflows (without checked math), a user balance can go negative without panicking — check all balance-decreasing operations.
- **TTL-gated invariants**: Some invariants are maintained only while Instance/Persistent storage entries are alive. If a key expires and returns `None`, `unwrap()` panics. If `unwrap_or(0)` is used instead, a zero default may silently break the invariant.

---

## 3. Rate/Supply Interaction Matrix

For protocols with multiple monetary parameters that interact:

| Parameter A | Parameter B | Interaction | Can A × B Produce Extreme Output? |
|-------------|-----------|-------------|----------------------------------|

Check: can two independently-valid parameter settings combine to create an extreme or invalid economic state? (Rule 14 constraint coherence)

**Example**: `reward_rate_bps = 5_000` (valid alone: 50%) and `boost_multiplier = 20` (valid alone) combine to `effective_rate = 100_000 BPS = 1_000%`, draining the reward vault within one epoch cycle.

---

## 4. Fee Formula Verification at Normal Values

For every fee-related computation:

### 4a. Concrete Example Computation

Pick 3 representative fee rates and trace through the actual code formula:

| Fee Param | Value | Formula | Input Amount (i128) | Expected Output | Actual Output | Match? |
|-----------|-------|---------|---------------------|----------------|---------------|--------|
| {fee_bps} | 100 | {code formula} | 10_000_0000000 | {expected} | {computed} | YES/NO |
| {fee_bps} | 500 | {code formula} | 10_000_0000000 | {expected} | {computed} | YES/NO |
| {fee_bps} | 1000 | {code formula} | 10_000_0000000 | {expected} | {computed} | YES/NO |

Tag: `[BOUNDARY:fee_bps={val} → effective_rate={computed_rate}]`

**Note on decimal units**: XLM uses 7 decimal places (1 XLM = 10_000_000 stroops). Many SEP-41 tokens also use 7 decimals. Fee calculations on raw `i128` values work in the token's smallest unit — verify formulas account for the correct denomination.

**Red flags**:
- Gross-up formulas: `amount * MAX / (MAX - fee)` charges a higher effective rate than `fee/MAX`. At 5% this is 5.26%. Document whether intentional.
- Fee-on-fee: Does fee A's output feed into fee B's input? Combined effective rate is not simply A + B.
- Rounding direction: `i128` division truncates toward zero. For fee deductions, truncation favors the user; for fee collection, truncation favors the user too (protocol collects less). Verify intended rounding direction and document.
- Integer division order: `amount * rate / MAX` vs `amount / MAX * rate` — the second loses precision catastrophically for small amounts.

### 4d. Fee-Base Consistency

For every fee computation, trace the base amount through ALL subsequent code paths:

| Fee Site | Base Amount Variable | Modified After Fee? | Modified How | Fee Recomputed? | Overcharge? |
|----------|---------------------|--------------------:|-------------|-----------------|-------------|

**Methodology**:
- Identify the `i128` variable used as fee base
- Trace FORWARD from the fee computation to end of function
- If variable is reduced (capped, slippage-adjusted, partial fill) AFTER fee was computed → fee was charged on a larger base than actually processed

### 4b. Fee Interaction Matrix

For protocols with multiple fee types:

| Fee A | Fee B | A Output Feeds B Input? | Combined Effective Rate | Independent Rate Sum | Discrepancy? |
|-------|-------|------------------------|------------------------|---------------------|-------------|

### 4c. Fee Impact on Share Price

If the protocol uses share-based accounting:
- After fee deduction: does the share price change?
- Does the fee mechanism create a spread between deposit and immediate withdrawal?
- With `i128` integer math: does rounding in deposit vs withdraw favor one direction consistently?

---

## 5. Emission/Inflation Sustainability

For protocols with emission/inflation/reward distribution mechanics:

- What is the maximum emission rate over 1 ledger / 1 day / 1 year?
- Can emissions exceed the reward vault's token balance?
- Is there a supply cap? Can the admin bypass it?
- What happens when the reward vault is depleted? (panic on `transfer`? zero rewards? proportional reduction?)

| Emission Parameter | Max Rate | Vault Balance Required | Ledgers to Depletion at Max | Cap Exists? |
|-------------------|----------|----------------------|---------------------------|-------------|

---

## 6. Resource Metering and Cost Economics

**Soroban has no compute unit cap like Solana's 1.4M CUs**, but it has resource metering across three axes that determine transaction fees:

| Resource | Description | Limit |
|---------|-------------|-------|
| CPU instructions | VM instruction count | Per-transaction budget (network-configurable) |
| Memory (bytes) | Peak memory usage | Per-transaction budget |
| Ledger I/O | Ledger entries read + written | Per-transaction budget; also affects TTL extension costs |

For batch operations (mass distributions, multi-user reward updates, bulk settlements):

| Operation | Ledger Entries Read | Ledger Entries Written | Memory Growth | Scales With N Users? | DoS via Large N? |
|-----------|--------------------|-----------------------|--------------|----------------------|-----------------|
| {operation} | {count} | {count} | {bytes} | YES/NO | YES/NO |

**Soroban-specific cost analysis**:
- Reading Persistent storage entries costs more fees than Instance storage — avoid iterating Persistent keys in hot paths
- Writing new ledger entries (creating per-user state) increases transaction fees; if the protocol absorbs this cost, check for griefing
- TTL extension fees: extending a Persistent entry's TTL costs `size_bytes * fee_rate * extension_ledgers`. Unbounded TTL extensions can drain a protocol's operational budget.
- **No free loops**: A loop over N entries in a Vec stored in Instance storage consumes proportional CPU instructions and memory. Check for unbounded iteration that could price out legitimate callers.

---

## 7. TTL Cost Economics (Soroban-Specific)

Soroban uses TTL (time-to-live) measured in ledger numbers. Entries that expire are archived; accessing archived entries requires a restoration fee.

| Storage Entry | Storage Type | Current TTL Strategy | Who Pays to Extend | Risk if Expired |
|--------------|-------------|---------------------|-------------------|----------------|
| {key} | Instance / Persistent / Temporary | {manual/auto/never} | {admin/user/protocol} | {panic/zero_default/DoS} |

**Checks**:
- If Instance storage expires, ALL `instance().get()` calls that use `unwrap()` will panic — entire contract becomes unusable.
- If a user's Persistent storage entry expires, their position data is lost (archived). Can it be restored? Who pays the restoration fee?
- If Temporary storage (used for allowances, nonces) expires, can this cause DoS on normal operations?
- Is there an admin function to bulk-extend TTLs? What is its resource cost at maximum user count?
- Can an attacker let their own entries expire to avoid obligations (e.g., debt, locked collateral)?

---

## Finding Template

```markdown
**ID**: [EDA-N]
**Verdict**: CONFIRMED / PARTIAL / REFUTED / CONTESTED
**Step Execution**: (see checklist below)
**Rules Applied**: [R2:___, R10:___, R13:___, R14:___]
**Severity**: Critical/High/Medium/Low/Info
**Location**: src/{file}.rs:LineN
**Title**: {parameter boundary violation / invariant break / economic unsustainability / TTL expiry DoS}
**Description**: {specific issue with code reference and numerical example using i128 values}
**Impact**: {quantified at worst-state operational parameters — Rule 10}
```

---

## Step Execution Checklist (MANDATORY)

| Section | Required | Completed? | Notes |
|---------|----------|------------|-------|
| 1. Parameter Boundary Analysis | YES | | |
| 2. Economic Invariant Identification | YES | | |
| 3. Rate/Supply Interaction Matrix | IF >1 monetary param | | |
| 4. Fee Formula Verification at Normal Values | IF fee parameters detected | | |
| 5. Emission/Inflation Sustainability | IF emission/reward detected | | |
| 6. Resource Metering and Cost Economics | YES | | |
| 7. TTL Cost Economics | YES | | Soroban-specific — never skip |

If any step skipped, document valid reason (N/A, single parameter, no emissions, no TTL-sensitive keys).
