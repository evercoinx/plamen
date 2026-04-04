---
name: "zero-state-return"
description: "Trigger Always inject into Arithmetic agent (extends existing ZERO_STATE_ECONOMICS) - Purpose Check protocol return-to-zero state, not just initial zero state"
---

# ZERO_STATE_RETURN Skill (Soroban)

> **Trigger**: Always inject into Arithmetic agent (extends existing ZERO_STATE_ECONOMICS)
> **Purpose**: Check protocol return-to-zero state, not just initial zero state
> **Finding prefix**: `[ZS-N]`
> **Rules referenced**: R5, R10, R13

## Overview

ZERO_STATE_ECONOMICS checks initial zero state. This skill EXTENDS it to cover:
- Protocol returning to zero after normal operations
- Residual assets when LP/share supply returns to zero
- Re-entry vulnerabilities after full exit
- Soroban-specific: i128 division edge cases at zero, empty Instance/Persistent storage states

**Soroban arithmetic context**: All balances are `i128`. Division truncates toward zero. `i128::MIN / -1` overflows (only case of signed overflow). When `total_shares = 0` and `total_value > 0`, the exchange rate formula `total_value / total_shares` panics (division by zero) unless guarded.

---

## 1. Return-to-Zero Scenarios

After normal operations, can the protocol return to:

| State | Trigger | Check |
|-------|---------|-------|
| `total_shares == 0` (i128) | All users burned shares / withdrew | Does this recreate first-depositor conditions? |
| `total_deposited == 0` | All funds withdrawn | Are there residual rewards or time-decay state in Persistent storage? |
| Empty Instance storage keys | All operational state cleared | Can protocol still function on next call? |
| Zero liquidity | All LP positions closed | What happens to accumulated fees / ratio snapshots stored in Persistent entries? |

---

## 2. Residual Asset Check

When LP/share supply returns to zero, check for:

### 2a. Accrued Rewards / Time-Decay State

- Do rewards or time-decay state (locked profit, vesting, streaming amounts) persist in Instance or Persistent storage when `total_shares = 0`?
- If YES → inflates exchange rate for next depositor
- Example: Protocol accrues 100 XLM in rewards tracked in Instance storage; last user exits (`total_shares = 0`); next deposit of 1 stroop receives claim to 100 XLM worth of value

### 2b. Unclaimed Fees / Ratio Snapshot State

- Are there fee accumulators (performance fees, management fees) in Instance storage that persist?
- Do ratio snapshots (high water marks, benchmark prices) reset when all shares are burned?
- Can first new depositor capture accumulated fees stored in Instance storage?

### 2c. Token Balance Dust

- Can dust (small `i128` token amounts) remain in the contract's token balance (`token_client.balance(contract_address)`) after all shares are burned?
- Does dust affect exchange rate calculations when `total_shares = 0`?
- Example: `total_shares = 0`, but `token_client.balance(contract_address) = 1` (1 stroop dust) — rate formula divides by zero unless guarded; if guarded with `unwrap_or`, returns 0 or 1:1, but next depositor's share count is computed against a non-empty vault
- **Soroban note**: Unlike Solana token accounts, there is no separate "token account" to close. Token balance at the contract address is a balance in the SEP-41 token's own Persistent storage for the contract's address entry.

### 2d. Pending Operations

- Are there pending withdrawal receipts, claim tickets, or in-flight positions stored in Persistent storage that persist when `total_shares = 0`?
- What happens to allocations or strategy calls when LP supply hits zero?
- Do sub-contract or cross-contract positions retain allocated funds after all shares are burned?

---

## 3. Re-Entry Vulnerability Analysis

Does re-entering zero state recreate first-depositor attack conditions?

| Scenario | Initial State | Return-to-Zero State | Same Vulnerability? |
|----------|---------------|---------------------|---------------------|
| First depositor attack | total_shares=0, total_value=0 | total_shares=0, total_value=X (residual in Instance storage) | **WORSE** if residual > 0 |
| Exchange rate manipulation | No shares exist | No shares, but token balance > 0 | YES + amplified |
| Donation attack | Clean state | Dirty state (residual, unsolicited transfer dust) | YES + pre-seeded |

**Key question**: Does first-depositor protection (minimum deposit requirement, burned shares) apply only on initial `initialize()` invocation, or also on return-to-zero re-deposits?

**Soroban-specific**: If first-depositor protection is implemented as `if total_shares == 0 { require(amount >= MIN_FIRST_DEPOSIT) }`, does this check also fire when the protocol returns to zero after normal operations? Is `MIN_FIRST_DEPOSIT` stored in Instance storage (accessible) or hardcoded?

---

## 4. Protocol Reset Functions

Check for admin functions that can force zero state:

- `emergency_withdraw()` — does it clear ALL tracked state (total_deposited, time-decay accumulators, ratio snapshots) in Instance storage?
- `close_vault()` — what Persistent/Instance storage keys persist after calling this?
- `migrate()` — does the old contract retain residual token balances at its address?
- `force_deallocate()` — can it create accounting mismatch between Instance state and actual token balance?

For each: what state persists after the "reset"?

**Soroban-specific**: Instance storage is shared for all keys; there is no single "close all" operation. Verify each `DataKey` variant is explicitly cleared (set to 0 / removed) during reset, not just the main accounting keys.

---

## 5. Zero-State Return Checklist

```markdown
## Zero-State Return Analysis for [Contract / Vault]

### Can protocol return to zero state?
- [ ] All users can withdraw / burn shares (no locked funds)
- [ ] All share tokens can be burned
- [ ] total_shares (i128) can reach exactly 0

### What persists when total_shares = 0?
- [ ] Accrued rewards / time-decay state: [amount / none]
- [ ] Protocol fees / ratio snapshots: [amount / none / resets]
- [ ] Token balance dust: [yes / no / amount]
- [ ] Pending operations / withdrawal receipts in Persistent storage: [list / none]
- [ ] Sub-contract or cross-contract allocations: [zeroed / residual]

### Re-entry vulnerability?
- [ ] Initial zero state protected: [yes / no / how]
- [ ] Return-to-zero state protected: [yes / no / how]
- [ ] Same protection mechanism: [yes / no]

### Exchange rate at return-to-zero:
- [ ] Formula: [show calculation using i128 values]
- [ ] With residual X: [show calculation — does it panic? return wrong value?]
- [ ] Can attacker inflate rate before re-entry: [yes / no]
```

---

## 5b. Default / Uninitialized State Values

For each state variable used in arithmetic or control flow, check its **initial value** before any user interaction:

- **Missing key returns None**: `e.storage().instance().get::<_, i128>(&DataKey::TotalShares)` returns `None` if never written. Code using `unwrap()` panics; code using `unwrap_or(0)` silently treats uninitialized state as 0.
- **First-call path**: Trace the FIRST invocation of each state-modifying function. Does it assume a prior `initialize()` already set dependent keys?
- **Check**: For each key read in a function, is there a code path where that key still holds no value (never written)? If YES, does the function behave correctly with `None` / default?
- **Signed zero vs missing**: `i128` defaults to 0 in Rust uninitialized memory, but Soroban Persistent/Instance storage does NOT default to 0 — absent keys return `None`. Confusion between "key present with value 0" and "key absent" can cause subtle bugs.

---

## 6. Code Patterns to Check

```rust
// Pattern 1: Guard covers initial zero only — what about return-to-zero?
if vault.total_shares == 0i128 {
    return Ok(1_000_000i128); // 1:1 rate (assuming 7 decimals)
}
// QUESTION: What if total_shares returns to 0 but token_client.balance(addr) > 0?

// Pattern 2: i128 exchange rate with possible division by zero
let rate = vault.total_value
    .checked_div(vault.total_shares)
    .ok_or(Error::DivisionByZero)?;
// QUESTION: What if total_value > 0 and total_shares = 0? (panics correctly)
// QUESTION: What if both return to 0 but tracked state diverges from token balance?

// Pattern 3: First deposit protection
if vault.total_shares == 0i128 {
    require!(deposit_amount >= MIN_FIRST_DEPOSIT, Error::DepositTooSmall);
}
// QUESTION: Does this check fire for RE-deposits after full exit?
// QUESTION: Is MIN_FIRST_DEPOSIT in Instance storage (could be 0 if not initialized)?

// Pattern 4: Time-decay state
let unlocked = vault.decay_amount * elapsed_ledgers / DECAY_DURATION_LEDGERS;
// QUESTION: Does decay_amount persist in Instance storage when total_shares = 0?
// QUESTION: Does next depositor inherit unlocked value (ratio inflated)?

// Pattern 5: Missing key on return-to-zero
let total: i128 = e.storage().instance()
    .get(&DataKey::Total)
    .unwrap_or(0i128); // Treats absent key as 0 — but 0 may not be safe default
// QUESTION: Is 0 a safe default for this key, or could it cause division by zero downstream?
```

---

## 7. Finding Template

```markdown
**ID**: [ZS-N]
**Severity**: [typically HIGH if funds extractable]
**Location**: src/{file}.rs:LineN
**Title**: Return-to-zero state allows [attack] due to [residual state in storage]
**Description**:
- Protocol can return to total_shares=0 via [mechanism]
- When this happens, [storage key] retains value of [amount] (i128)
- A new depositor can [exploit path]
**Impact**: [Fund extraction / exchange rate manipulation / unfair distribution]
**PoC Scenario**:
1. Users deposit and earn rewards
2. All users withdraw, total_shares = 0i128
3. Residual state remains: {DataKey::DecayAmount} = X in Instance storage
4. Attacker deposits minimum amount
5. Attacker claims X rewards via inflated share-to-value ratio
```

---

## 8. Integration with ZERO_STATE_ECONOMICS

This skill does NOT replace ZERO_STATE_ECONOMICS. It EXTENDS it:

| Check | ZERO_STATE_ECONOMICS | ZERO_STATE_RETURN |
|-------|---------------------|-------------------|
| Initial zero state | YES | — |
| First depositor attack | YES | — |
| Return to zero | — | YES |
| Residual assets (time-decay, fees, dust) in storage | — | YES |
| Re-entry vulnerability | — | YES |
| i128 division-by-zero guards | YES (partial) | YES (full — both initial and return paths) |
| Missing storage key defaults | — | YES |

When applying ZERO_STATE_ECONOMICS, ALSO apply ZERO_STATE_RETURN.
