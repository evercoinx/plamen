---
name: "verification-protocol"
description: "Trigger Pattern Always (used by all verifier agents) - Inject Into security-verifier agents (Phase 5)"
---

# VERIFICATION_PROTOCOL Skill (Soroban)

> **Trigger Pattern**: Always (used by all verifier agents)
> **Inject Into**: security-verifier agents (Phase 5)
> **Purpose**: Prove hypotheses TRUE or FALSE using `cargo test` with `soroban-sdk` testutils and the `Env` harness.

---

## Evidence Source Tracking (MANDATORY)

> **CRITICAL**: For EVERY piece of evidence used in verification, you MUST tag its source.
> Evidence from mocks or unverified external contracts CANNOT support a REFUTED verdict.

### Evidence Source Tags

| Tag | Meaning | Valid for REFUTED? |
|-----|---------|-------------------|
| `[PROD-ONCHAIN]` | Production Stellar account / contract data (via Horizon RPC or `stellar contract read`) | YES |
| `[PROD-SOURCE]` | Verified source from Stellar Explorer / published audit | YES |
| `[PROD-FORK]` | Tested with forked production state via `stellar-quickstart` or RPC state dump | YES |
| `[CODE]` | Audited codebase (in-scope source) | YES |
| `[MOCK]` | Mock contracts or test-only state created in `testutils` | **NO** |
| `[EXT-UNV]` | External contract behavior inferred but not verified against production | **NO** |
| `[DOC]` | Documentation/spec only | **NO** (needs verification) |

### Evidence Audit Table (REQUIRED in every verification output)

Before ANY verdict, fill this table:

```markdown
### Evidence Audit
| Claim | Evidence Source | Tag | Valid for REFUTED? |
|-------|-----------------|-----|-------------------|
| "External contract returns X" | Test mock | [MOCK] | NO |
| "Storage key Y holds value Z" | src/lib.rs:123 | [CODE] | YES |
| "Contract balance is N" | Stellar Explorer verified | [PROD-SOURCE] | YES |
```

### Mock Rejection Rule

**AUTOMATIC OVERRIDE**: If ANY evidence supporting REFUTED has tag `[MOCK]` or `[EXT-UNV]`:
- CANNOT return REFUTED
- MUST return CONTESTED
- Triggers production verification request

---

## Pre-Verification Understanding

Before writing ANY test code, you MUST answer:

### Question 1: What is the EXACT bug?
```
NOT: "Authorization is missing"
NOT: "State is inconsistent"

YES: "Function [withdraw] does not call env.require_auth(&user) before
      modifying user balances, allowing any caller to withdraw funds from
      any user's balance (src/lib.rs:line N)"
```

### Question 2: What OBSERVABLE difference proves it?
```
NOT: "Balances are different"
NOT: "Wrong state"

YES: "Before exploit: user_balance = 1000 stroops
      After exploit: user_balance = 0, attacker_balance = 1000
      Expected: transaction should have panicked with 'auth required' error"
```

### Question 3: What is the EXACT assertion?
```
NOT: assert!(exploit_worked)

YES: assert_eq!(client.balance(&attacker), initial_user_balance)
 OR: let result = std::panic::catch_unwind(|| client.withdraw(&attacker, &user, &amount));
     assert!(result.is_err(), "should have panicked but succeeded")
 OR: assert_ne!(state_before, state_after, "state changed when it should not")
```

**If you cannot answer all three -> ASK FOR CLARIFICATION**

---

## Pre-PoC Feasibility Gates (MANDATORY)

Before writing test code, verify these two gates. If either FAILS, adjust the hypothesis.

### Gate F1: Reachability
Trace a call path from a permissionless entry point to the vulnerable code.

- [ ] Entry point identified (public `#[contractimpl]` function)
- [ ] Call path traced through internal helpers
- [ ] All `require_auth` checks on the path are passable by the attacker profile (attacker can authorize their own address but not another address without their cooperation)

If NO public entry point reaches the vulnerable code -> UNREACHABLE -> FALSE_POSITIVE.
If reachable only through an admin-gated path -> document the restriction, adjust likelihood.

### Gate F2: Math Bounds
Substitute real-world value domains into the expression that triggers the bug.

- [ ] Parameter domains identified (token precision (7 decimals for XLM, varies for other SAC tokens), max supply, TVL range, fee range, ledger sequence bounds)
- [ ] Expression evaluated at worst-case feasible inputs
- [ ] Result crosses the bug threshold

If the bug requires values outside feasible domains -> INFEASIBLE -> FALSE_POSITIVE.
If feasible only at extreme but realistic parameters -> document the threshold, proceed with adjusted severity.

**Both gates PASS -> proceed to PoC. Either gate FAILS -> document and stop.**

---

## Soroban Test Environment Setup

### Anti-Hallucination Rules for Soroban API (MANDATORY)

Before writing test code, verify EVERY API call you intend to use against these known-correct patterns. Do NOT assume Solana/EVM API shapes apply.

**Correct Soroban testutils patterns**:

```rust
// Environment creation — CORRECT
let env = Env::default();           // Standard test env
// NOT: Env::new(), Environment::new(), TestEnv::default()

// Auth mocking — choose the right one:
env.mock_all_auths();               // Disables ALL auth checks — use for testing logic, NOT for testing auth
env.mock_auths(&[MockAuth { ... }]);// Simulates specific auth approvals — use for testing auth flows

// Contract registration — CORRECT
let contract_id = env.register(ContractType, ());          // With no constructor args
let contract_id = env.register(ContractType, (arg1, arg2,));// With constructor args
// NOT: env.register_contract(), env.deploy_contract(), env.new_contract()

// Creating test addresses — CORRECT
let user = Address::generate(&env);
// NOT: Address::random(), Address::from_str(), Pubkey::new_unique()

// Token client (SAC-compatible) — CORRECT
let token_admin = Address::generate(&env);
let (token_address, token_admin_client) = create_token_contract(&env, &token_admin);
let token_client = token::Client::new(&env, &token_address);
// NOT: TokenClient::new(), StellarAssetClient::new()

// Ledger manipulation — CORRECT
env.ledger().with_mutation(|l| {
    l.sequence_number = 100;
    l.timestamp = 1_000_000;
});
// NOT: env.set_ledger_sequence(), env.advance_ledger()

// Reading contract storage (for assertions) — CORRECT
let val: ExpectedType = env.as_contract(&contract_id, || {
    env.storage().persistent().get(&DataKey::MyKey).unwrap()
});
// NOT: contract.get_storage(), env.get_storage_entry()

// Invoking contract function — CORRECT (via generated client)
let client = ContractClient::new(&env, &contract_id);
client.my_function(&arg1, &arg2);
// Expecting a panic (auth failure, assertion, etc.):
let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
    client.my_function(&arg1, &arg2)
}));
assert!(result.is_err());
```

**Correct storage access patterns**:
```rust
// Persistent storage (survives ledger TTL extension, used for user data)
env.storage().persistent().set(&key, &value);
env.storage().persistent().get::<KeyType, ValueType>(&key);
env.storage().persistent().has(&key);

// Instance storage (survives as long as contract instance survives)
env.storage().instance().set(&key, &value);
env.storage().instance().get::<KeyType, ValueType>(&key);

// Temporary storage (expires on TTL, no guarantee of persistence)
env.storage().temporary().set(&key, &value);
```

**Correct auth patterns in production code** (for understanding what to test):
```rust
// Require that `address` authorized this call
env.require_auth(&address);

// Require that `address` authorized this call WITH SPECIFIC ARGS
env.require_auth_for_args(&address, args!(arg1, arg2));

// In tests: check what auths were recorded
let auths = env.auths();  // Returns Vec<(Address, AuthorizedInvocation)>
```

---

## Soroban PoC Test Templates

### Template 1: Missing Authorization Check

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use soroban_sdk::{testutils::{Address as _, MockAuth, MockAuthInvoke}, Env, Address};

    #[test]
    fn test_missing_auth_attacker_can_drain() {
        let env = Env::default();
        // NOTE: Do NOT call env.mock_all_auths() — we are testing that auth IS required

        let admin = Address::generate(&env);
        let user = Address::generate(&env);
        let attacker = Address::generate(&env);

        let contract_id = env.register(MyContract, (&admin,));
        let client = MyContractClient::new(&env, &contract_id);

        // Setup: give user a balance
        env.mock_auths(&[MockAuth {
            address: &admin,
            invoke: &MockAuthInvoke {
                contract: &contract_id,
                fn_name: "fund_user",
                args: (&user, &1000_i128).into_val(&env),
                sub_invokes: &[],
            },
        }]);
        client.fund_user(&user, &1000_i128);

        // Verify initial state
        assert_eq!(client.balance(&user), 1000_i128);
        assert_eq!(client.balance(&attacker), 0_i128);

        // EXPLOIT: attacker calls withdraw on behalf of user WITHOUT user's auth
        // If auth is required, this should panic. If not required, attacker drains user.
        // We expect this to SUCCEED (demonstrating the bug) — if it panics, the bug is NOT present
        client.withdraw(&attacker, &user, &1000_i128);  // attacker draining user's balance

        // If we reach here, the bug IS present
        assert_eq!(client.balance(&user), 0_i128, "user balance should be drained");
        assert_eq!(client.balance(&attacker), 1000_i128, "attacker should have stolen funds");
    }
}
```

### Template 2: Storage Key Collision / Schema Mismatch

```rust
#[test]
fn test_storage_key_collision_wrong_deserialization() {
    let env = Env::default();
    env.mock_all_auths();

    let contract_id = env.register(MyContractV2, ());
    let client = MyContractV2Client::new(&env, &contract_id);

    // Simulate V1 storage state written with old schema
    // (In a real migration scenario, this data was written by V1 WASM)
    env.as_contract(&contract_id, || {
        // Write V1 format data manually
        let v1_data = OldDataStruct { field_a: 100_i128, field_b: 50_u64 };
        env.storage().persistent().set(&DataKey::UserBalance, &v1_data);
    });

    // V2 attempts to read V1 data as V2 format — should either trap or return wrong value
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        client.get_user_balance()  // V2 reads DataKey::UserBalance as NewDataStruct
    }));

    // If result is Ok: check for silent data corruption
    if let Ok(balance) = result {
        // V2 may have silently misinterpreted V1 bytes
        assert_ne!(balance, 100_i128, "Balance should not silently return correct value from wrong type");
    }
    // If result is Err: trap confirmed — deserialization failed, contract is bricked for V1 data
}
```

### Template 3: Reentrancy / State Inconsistency

```rust
#[test]
fn test_reentrancy_state_inconsistency() {
    let env = Env::default();
    env.mock_all_auths();

    let admin = Address::generate(&env);
    let attacker_contract_id = env.register(AttackerContract, ());
    let victim_contract_id = env.register(VictimContract, (&admin,));

    let victim_client = VictimContractClient::new(&env, &victim_contract_id);
    let attacker_client = AttackerContractClient::new(&env, &attacker_contract_id);

    // Fund victim contract
    // ... (setup token balances)

    let balance_before = victim_client.total_deposits();

    // Attacker triggers reentrant call via callback mechanism
    attacker_client.execute_reentrancy_attack(&victim_contract_id);

    let balance_after = victim_client.total_deposits();

    // Verify: attacker extracted more than they deposited
    let attacker_profit = attacker_client.profit();
    assert!(attacker_profit > 0, "Reentrancy attack should be profitable");
    assert!(
        balance_after < balance_before,
        "Victim contract balance should decrease: before={}, after={}",
        balance_before, balance_after
    );
}
```

### Template 4: Arithmetic Overflow / Precision Loss

```rust
#[test]
fn test_arithmetic_precision_loss_at_boundary() {
    let env = Env::default();
    env.mock_all_auths();

    let contract_id = env.register(VaultContract, ());
    let client = VaultContractClient::new(&env, &contract_id);

    // XLM has 7 decimal places (1 XLM = 10_000_000 stroops)
    // Test at boundary values
    let user_a = Address::generate(&env);
    let user_b = Address::generate(&env);

    // Deposit small amounts that trigger rounding
    client.deposit(&user_a, &1_i128);   // 1 stroop deposit
    client.deposit(&user_b, &10_000_000_000_000_i128);  // 1,000,000 XLM deposit

    // User A withdraws — rounding may give them 0 or more than deposited
    let user_a_shares = client.shares(&user_a);
    let user_a_withdraw = client.withdraw(&user_a, &user_a_shares);

    // FINDING: User A receives 0 (rounded down) despite depositing 1 stroop
    assert_eq!(user_a_withdraw, 1_i128,
        "User A should receive their deposit back but received {}", user_a_withdraw);
}
```

### Template 5: Ledger Sequence / Timestamp Manipulation

```rust
#[test]
fn test_cooldown_bypass_same_ledger_sequence() {
    let env = Env::default();
    env.mock_all_auths();

    let contract_id = env.register(CooldownContract, ());
    let client = CooldownContractClient::new(&env, &contract_id);
    let user = Address::generate(&env);

    // Set initial ledger state
    env.ledger().with_mutation(|l| {
        l.sequence_number = 100;
        l.timestamp = 1_000_000;
    });

    // First action — triggers cooldown
    client.action(&user);

    // Attempt second action in same ledger (sequence unchanged)
    // Soroban: consecutive ledgers CAN have same timestamp if closed quickly
    env.ledger().with_mutation(|l| {
        l.sequence_number = 100;  // Same sequence
        l.timestamp = 1_000_000;  // Same timestamp — consecutive same-timestamp ledger
    });

    // If cooldown is based on timestamp delta and timestamps can repeat,
    // this second call should be blocked but may not be
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        client.action(&user)
    }));

    // If Ok: cooldown was bypassed
    assert!(result.is_err(),
        "Second call in same ledger should be blocked by cooldown");
}
```

### Template 6: try_invoke_contract Partial State (Error Handling)

```rust
#[test]
fn test_try_invoke_partial_state_on_external_error() {
    let env = Env::default();
    env.mock_all_auths();

    // Deploy a mock external contract that always fails
    let failing_external = env.register(FailingExternalContract, ());
    let contract_id = env.register(ProtocolContract, (&failing_external,));
    let client = ProtocolContractClient::new(&env, &contract_id);

    let user = Address::generate(&env);
    let initial_recorded_deposit = client.recorded_deposit(&user);  // 0

    // Protocol calls deposit() which internally calls try_invoke_contract to external
    // External fails, but protocol may have already updated its own state before the call
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        client.deposit(&user, &1000_i128)
    }));

    let final_recorded_deposit = client.recorded_deposit(&user);

    if result.is_ok() {
        // Deposit function returned Ok — check for partial state
        assert_eq!(final_recorded_deposit, initial_recorded_deposit,
            "Recorded deposit should NOT have changed when external call failed: recorded={}",
            final_recorded_deposit);
    }
    // If result.is_err(): function correctly panicked on external failure — no partial state issue
}
```

---

## Fuzz Variant (Medium+ findings, cargo +nightly)

After the specific PoC passes, write a fuzz variant for Medium+ severity findings:

```rust
// In fuzz/fuzz_targets/fuzz_target_1.rs
#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if data.len() < 16 { return; }
    let env = Env::default();
    env.mock_all_auths();

    // Derive fuzz inputs from data bytes
    let amount = i128::from_le_bytes(data[0..16].try_into().unwrap()).abs() % 1_000_000_000_000_i128;
    if amount == 0 { return; }

    let contract_id = env.register(MyContract, ());
    let client = MyContractClient::new(&env, &contract_id);
    let user = Address::generate(&env);

    // Exercise the vulnerable path with fuzz input
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        client.target_function(&user, &amount);
        // INVARIANT: assert what should always hold
        let balance = client.balance(&user);
        assert!(balance >= 0, "Balance must never be negative: {}", balance);
    }));
});
```

Run with: `cargo +nightly fuzz run fuzz_target_1 -- -max_total_time=300`

**proptest fallback** (if cargo-fuzz not available):

```rust
use proptest::prelude::*;

proptest! {
    #[test]
    fn test_fuzz_invariant(
        amount in 1_i128..1_000_000_000_000_i128,
        sequence_number in 1_u32..100_000_u32
    ) {
        let env = Env::default();
        env.mock_all_auths();
        env.ledger().with_mutation(|l| { l.sequence_number = sequence_number; });

        let contract_id = env.register(MyContract, ());
        let client = MyContractClient::new(&env, &contract_id);
        let user = Address::generate(&env);

        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            client.target_function(&user, &amount);
            let balance = client.balance(&user);
            prop_assert!(balance >= 0, "Balance must not be negative: {}", balance);
        }));
        // Ignore panics from auth errors or arithmetic — we care about invariant violations
    }
}
```

---

## Dual-Perspective Verification (MANDATORY)

### Phase 1 — ATTACKER: Assume you ARE the attacker.
- What is the complete attack function call sequence?
- What contracts do you need to deploy (attacker contracts)?
- What is the profit/damage with real token amounts (in stroops)?
- Can you compose multiple `invoke_contract` calls for atomicity?
- Why would this succeed? (Which `require_auth` call is missing or bypassable?)

### Phase 2 — DEFENDER: Assume you are the protocol team.
- What `require_auth` check prevents this?
- What storage key uniqueness ensures correct account targeting?
- What external contract validation blocks substitution?
- Why is this safe by design?

### Phase 3 — VERDICT: Which argument won?

---

## Realistic Parameter Validation

Substitute ACTUAL contract constants (basis points, fee rates, thresholds, TTL values, token decimal places).
Apply Rule 10: Use worst realistic operational state, not current snapshot.

```
State: 'With real constants [fee_bps=X, precision=7_decimals, tvl=Z] at worst-state
[max_users, min_balance], bug triggers when [condition]'
OR: 'With real constants, bug does NOT trigger because [reason]'
```

**Stellar-specific constants**:
- XLM stroops: 1 XLM = 10,000,000 stroops (7 decimal places)
- Stellar base reserve: 0.5 XLM per ledger entry (5,000,000 stroops)
- Max ledger entry TTL: 3,110,400 ledgers (~1 year at 5s/ledger)
- Default min TTL for persistent: 120 ledgers (~10 minutes)

---

## Anti-Downgrade Guard (MANDATORY for Validation Sweep findings)

When verifying a finding from the Validation Sweep or Blind Spot Scanner, apply Rule 13's 5-question test BEFORE downgrading severity or marking FALSE_POSITIVE:

1. **Who is harmed** by this design gap?
2. **Can affected users avoid** the harm?
3. **Is the gap documented** in protocol docs?
4. **Could the protocol achieve the same goal** without this gap?
5. **Does the function fulfill its stated purpose completely?**

**HARD RULE**: If the finding shows Contract A has protection X but Contract B lacks it for the same user action -> defense parity gap, NOT "by design". Minimum severity: Medium.

---

## New Observations (MANDATORY)

If during verification you discover a NEW bug, auth gap, or edge case NOT covered by any existing hypothesis, document it under:

### New Observations
- [VER-NEW-1]: {title} -- {contract:function} -- {brief description}

---

## Error Trace Output (MANDATORY for CONTESTED/FALSE_POSITIVE)

### Error Trace
- **Failure Type**: AUTH_REQUIRED / CONTRACT_TRAP / ARITHMETIC_OVERFLOW / STORAGE_MISSING / UNEXPECTED_STATE
- **Location**: {contract}:{function}:{approximate line}
- **Error Code**: {Soroban host error code, if any — e.g., `WasmVm(InvalidAction)`, `Auth(NotAuthorized)`}
- **State at Failure**: {key storage values and their contents when test failed}
- **Investigation Question**: {What would need to be answered to resolve this}

---

## RAG Queries Before PoC (MANDATORY for HIGH/CRITICAL)

### Step 1: Get Attack Vectors
```
mcp__unified-vuln-db__get_attack_vectors(bug_class="{category}")
```

### Step 2: Get Similar Findings
```
mcp__unified-vuln-db__get_similar_findings(pattern="{vulnerability description}")
```

### Step 3: Validate Hypothesis
```
mcp__unified-vuln-db__validate_hypothesis(hypothesis="{finding summary}")
```

### Step 4: Live Search for Soroban-Specific Precedents
```
mcp__unified-vuln-db__search_solodit_live(
  keywords="{soroban stellar vulnerability pattern}",
  impact=["HIGH", "CRITICAL"],
  tags=["Access Control", "Logic Error"],
  language="Rust",
  quality_score=3,
  max_results=15
)
```

Document RAG evidence in output:
```markdown
### RAG Evidence
- **Attack Vectors Consulted**: [list bug classes queried]
- **Similar Exploits Found**: [count and brief descriptions]
- **Historical Precedent**: [matching Soroban/Stellar-specific vulnerabilities]
```

---

## RAG Confidence Override

| RAG Confidence | Local Verdict | Final Verdict | Action |
|----------------|---------------|---------------|--------|
| >= 7/8 matches | FALSE_POSITIVE | **CONTESTED** (override) | Cannot dismiss — strong precedent |
| >= 6/8 matches | FALSE_POSITIVE | **CONTESTED** (override) | Cannot dismiss — significant precedent |
| < 6/8 matches | FALSE_POSITIVE | FALSE_POSITIVE | Allowed — limited precedent |

---

## Chain Hypothesis PoC Requirements

Chain hypotheses receive PRIORITY verification. Multi-step exploits must test the COMPLETE sequence:

```rust
#[test]
fn test_chain_hypothesis_full() {
    let env = Env::default();
    env.mock_all_auths();

    // Setup contracts and initial state
    let admin = Address::generate(&env);
    let attacker = Address::generate(&env);
    let victim = Address::generate(&env);

    let contract_id = env.register(ProtocolContract, (&admin,));
    let client = ProtocolContractClient::new(&env, &contract_id);

    // ... fund and initialize state ...

    // ========================================
    // STEP 1: ENABLER (Finding B)
    // Execute action that creates the postcondition
    // ========================================
    client.enabler_action(&attacker);

    // ========================================
    // VERIFY POSTCONDITION CREATED
    // Assert the precondition for Finding A is now met
    // ========================================
    let postcondition_state = env.as_contract(&contract_id, || {
        env.storage().persistent().get::<DataKey, StateType>(&DataKey::SomeKey).unwrap()
    });
    // assert postcondition state is as expected

    // ========================================
    // STEP 2: BLOCKED FINDING (Finding A)
    // Execute previously-blocked attack using postcondition
    // ========================================
    client.exploit_action(&attacker, &victim);

    // ========================================
    // VERIFY CHAIN IMPACT
    // Combined impact should exceed either finding alone
    // ========================================
    let attacker_gain = client.balance(&attacker);
    let victim_loss = /* initial_victim_balance */ - client.balance(&victim);
    assert!(attacker_gain > 0, "Chain attack should transfer value to attacker");
    assert_eq!(attacker_gain, victim_loss, "Attacker gain equals victim loss");
}
```

---

## Interpreting Results

### Test PASSES -> Bug CONFIRMED

### Test FAILS -> Check Why

| Failure | Meaning | Action |
|---------|---------|--------|
| Auth error (`NotAuthorized`) | `require_auth` IS present | Re-examine hypothesis — auth is not missing |
| Contract trap / WasmVm error | Logic assertion failed | Check if assertion IS the bug or the fix |
| Arithmetic panic | Overflow/underflow protection present | Check if protection is complete or partial |
| Storage `unwrap()` panic | Storage entry does not exist | Fix test setup — create prerequisite state |
| Wrong balance | Setup amounts incorrect | Verify token decimals (7 for XLM) and stroop math |

---

## Iteration Protocol

**Attempt 1**: Direct implementation of test strategy from hypothesis.
**Attempt 2**: Adjust parameters (different amounts, different ledger state, different call ordering).
**Attempt 3**: Re-examine assumptions (is auth correctly mocked? Are storage keys correct? Are token amounts in stroops not XLM?).
**Attempt 4**: Verify function signatures directly in source — do NOT assume Solana/EVM API shapes apply.
**Attempt 5**: Re-read the anti-hallucination rules above — confirm you are using correct Soroban testutils API.
**After 5 attempts**: FALSE_POSITIVE with documented reasoning.

---

## Severity Determination

### CRITICAL
- Direct fund theft (token drain without authorization)
- Contract upgrade to malicious WASM (if upgrade gating is missing)
- Arbitrary `invoke_contract` with attacker-controlled address
- No prerequisites needed beyond knowing contract address

### HIGH
- Fund loss with specific setup (specific ledger state, timing)
- Broken core function (deposits, withdrawals, swaps)
- Authorization bypass requiring specific role setup
- Significant TVL at risk

### MEDIUM
- Limited fund loss under specific conditions
- State corruption (non-fund data) with meaningful downstream impact
- Edge cases with real impact at design limits
- XLM reserve draining (economic grief)

### LOW
- Negligible direct impact
- Extreme edge cases only
- Admin-controlled risk with multisig + timelock
- View function / off-chain data issues

---

## Insufficient Evidence (HALT CONDITIONS)

Before marking REFUTED, check ALL boxes:
- [ ] External contract behavior verified against PRODUCTION (not mock)
- [ ] Attack path checked on ALL public contract functions that access the same storage
- [ ] Profit calculated with attacker HOLDING tokens (not just calling the function)
- [ ] Missing precondition documented (type: STATE / ACCESS / TIMING / EXTERNAL / BALANCE)
- [ ] Searched other findings for matching postconditions (chain analysis integration)
- [ ] Storage key derivation verified against actual DataKey enum definitions
- [ ] `require_auth` calls verified by reading source (not assumed from function visibility)
- [ ] TTL of relevant storage entries verified (Temporary may have expired)

### Evidence That Does NOT Count
- "Mock external contract shows X" — mocks are not production
- "Function is called with `invoke_contract`" — this does not bypass `require_auth`
- "Attacker loses by sending tokens" — may profit via position held elsewhere
- "Function is `pub(crate)`" — may be reachable via cross-contract `invoke_contract` if WASM exports it
- "Requires admin" — admin may be a compromised EOA or a malicious upgrade
- "Storage key cannot collide" — verify the actual `DataKey` enum, do not assume

---

## Output Format

### CONFIRMED
```markdown
## Verdict: CONFIRMED

### Bug Mechanism Verified
{Explain what the cargo test proves in 2-3 sentences}

### Test Code
{Full Rust test function using soroban-sdk testutils}

### Test Output
{Relevant assertions and logged values from `cargo test -- --nocapture`}

### Key Evidence
| Metric | Value |
|--------|-------|
| Before | {value in stroops} |
| After | {value in stroops} |
| Expected | {value in stroops} |
| Difference | {calculation} |

### Evidence Audit
| Claim | Evidence Source | Tag | Valid for REFUTED? |
|-------|-----------------|-----|-------------------|

### Severity: {LEVEL}
{Justification in 1-2 sentences}

### Suggested Fix
```diff
- vulnerable line(s)
+ fixed line(s)
```
**Fix scope**: {1-sentence description}
**Verified**: YES/NO
```

### FALSE_POSITIVE
```markdown
## Verdict: FALSE_POSITIVE

### Attempts Made
**Attempt 1:**
- Approach: {description}
- Result: {what happened — include error type and code}
- Learning: {insight}

**Attempt 2-5:**
[...]

### Evidence Audit
| Claim | Evidence Source | Tag | Valid for REFUTED? |

### Why It Is Not a Bug
{Explain the actual behavior and why hypothesis was wrong in 2-3 sentences}

### Error Trace
- **Failure Type**: {type}
- **Location**: {location}
- **Error Code**: {code}
- **State at Failure**: {state}
- **Investigation Question**: {question}
```

### CONTESTED
```markdown
## Verdict: CONTESTED

### Evidence Status
| Checkpoint | Status | Details |
|------------|--------|---------|
| External contract behavior verified against PRODUCTION | YES/NO | {details} |
| All public function paths checked | YES/NO | {details} |
| Auth flow completeness confirmed | YES/NO | {details} |
| Storage TTL verified | YES/NO | {details} |

### Evidence Audit
| Claim | Evidence Source | Tag | Valid for REFUTED? |

### Why This Cannot Be REFUTED
{Explain what evidence is missing to definitively rule out the bug}

### Escalation Required
- [ ] Fetch production contract WASM and state for {external dep}
- [ ] Verify storage keys match DataKey enum in production deployment
- [ ] Check additional function paths: {list}

### Error Trace
{as above}
```
