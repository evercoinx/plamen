---
name: "semi-trusted-roles"
description: "Trigger Pattern operator/keeper/crank require_auth checks, authority-gated functions - Inject Into Breadth agents, depth-state-trace"
---

# SEMI_TRUSTED_ROLES Skill (Soroban)

> **Trigger Pattern**: operator/keeper/crank `require_auth` checks, authority-gated functions, admin actions beyond pure parameter-setting
> **Inject Into**: Breadth agents, depth-state-trace
> **Finding prefix**: `[STR-N]`
> **Rules referenced**: R2, R6, R10, R13

```
operator|keeper|crank|authority|admin|require_auth|guardian|relayer|updater|manager
```

**Soroban role context**: Soroban has no role-based modifiers (no EVM `onlyOwner`). Access control is entirely custom: a stored `Address` value is retrieved from Instance/Persistent storage, then `.require_auth()` is called on it. The `Address` type is opaque — it may resolve to a user keypair, a Stellar multisig account, or another Soroban contract. This ambiguity means the "role" may itself have complex authority semantics.

---

## Step 1: Inventory Role Permissions

In `{CONTRACTS}`, find all functions callable by `{ROLE_NAME}`.

For each function at `{ROLE_FUNCTIONS}`:
- What storage keys does it read or write (Instance/Persistent/Temporary)?
- What cross-contract calls (`invoke_contract` / `TokenClient`) does it make?
- What function parameters does it accept from the caller?
- How is the signer validated? (stored Address + `require_auth()` vs inline parameter vs direct signer check)

| Function | Auth Check | Storage Mutations | Cross-Contract Calls | Parameters |
|----------|-----------|-------------------|---------------------|------------|

**Soroban auth patterns to scan for**:
- `let admin: Address = e.storage().instance().get(&DataKey::Admin).unwrap(); admin.require_auth();`
- `let operator: Address = e.storage().persistent().get(&DataKey::Operator).unwrap(); operator.require_auth();`
- `e.current_contract_address().require_auth()` — contract authorizing itself (sub-invocation pattern)
- Functions with no auth check at all that modify privileged state (missing auth bug)

---

## Step 2: Analyze Within-Scope Abuse

For each permitted action, ask:

**Timing Abuse** (~5-second ledger close, no mempool):
- Can `{ROLE_NAME}` execute at harmful times? (Soroban has no public mempool, so front-running is limited to cases where the operator controls transaction submission order, e.g., as a validator or transaction fee bump)
- Can `{ROLE_NAME}` delay execution to harm users? (skip oracle updates, withhold distribution cranks)
- What is the maximum harm window from delayed execution?

**Parameter Abuse**:
- Can `{ROLE_NAME}` pass harmful values as function arguments? (inflated amounts, wrong recipient address, max slippage)
- Are function parameters validated on-chain, or accepted from the caller implicitly?
- Can `{ROLE_NAME}` supply an attacker-controlled contract address in a `cross_contract_address` parameter?

**Sequence Abuse**:
- Can `{ROLE_NAME}` execute functions out of intended order? (claim before distribute, settle before finalize)
- Can `{ROLE_NAME}` skip required functions? (skip epoch advancement, skip price update)

**Omission Abuse**:
- Can `{ROLE_NAME}` harm users by NOT executing? (skip reward distribution, delay settlement indefinitely)
- What is the protocol degradation timeline if the role stops acting?

---

## Step 3: Model Attack Scenarios

```
Scenario A: Timing Attack (Transaction Ordering)
1. {ROLE_NAME} controls or observes pending user transaction
2. {ROLE_NAME} submits role_function() in same or prior ledger
3. Role function executes, changing state
4. User transaction executes with worse conditions
5. Impact: {TIMING_IMPACT}

Note: Soroban has no public mempool. Timing attacks require role to be a validator,
fee-bumper, or to front-run in separate ledgers. Assess likelihood accordingly.

Scenario B: Parameter Attack
1. {ROLE_NAME} calls {ROLE_FUNCTION} with {MALICIOUS_PARAMS}
2. Parameters not validated against {EXPECTED_CONSTRAINTS}
3. Impact: {PARAM_IMPACT}

Scenario C: Key Compromise
1. {ROLE_NAME} Address keypair is compromised
   (or: {ROLE_NAME} Address is a contract that itself gets compromised)
2. Attacker can call: {ROLE_FUNCTIONS}
3. Maximum extractable value: {MAX_DAMAGE}
4. Recovery: {RECOVERY_PATH} — is there an admin function to rotate the role?
   If Address is a contract: is the contract upgradeable? Who holds its upgrade key?
```

---

## Step 4: Assess Mitigations

- Is there a timelock on `{ROLE_NAME}` actions? (multi-ledger proposal+execute pattern)
- Is `{ROLE_NAME}` a Stellar multisig account (M-of-N threshold)?
- Is `{ROLE_NAME}` a Soroban governance contract with timelock?
- **Does a rotation/removal function for `{ROLE_NAME}` EXIST?** If NO → FINDING: authority is irrevocable without contract upgrade. Severity: minimum Medium if role can modify user-facing state.
- Can admin rotate `{ROLE_NAME}` authority quickly enough to respond to a compromise?
- Are there rate limits encoded in the contract (per-ledger caps, cooldown periods in ledger numbers)?
- If the contract is immutable (no `update_current_contract_wasm`): can a compromised role be replaced at all?

**Soroban-specific mitigation patterns**:
- `Address` pointing to a Stellar multisig (M-of-N signers, timelock): check threshold
- `Address` pointing to a governance contract: check governance contract's own security
- Two-step role transfer: `propose_new_admin(new_addr)` + `accept_admin()` called by `new_addr` — prevents accidental transfer to wrong address
- Role stored in Instance storage: changes take effect immediately on next call (no delay)
- Role stored in Persistent storage: same immediate effect — no built-in delay

---

## Step 5: Model User-Side Exploitation (Direction 2 — MANDATORY)

**Predictability Analysis**:
- Is the role's behavior predictable? (executes on schedule, on price triggers, on queue length)
- Can users observe when the role will act? (on-chain state reveals when conditions are met)
- Can users front-run or back-run the role's action via higher-fee transactions?

**Scenario D: User Exploits Role Timing**
```
1. User observes that {ROLE_NAME} executes {ROLE_FUNCTION} when {CONDITION} is met
2. User submits transaction with higher fee to land in same or prior ledger
3. {ROLE_FUNCTION} executes, changing state
4. User benefits from known state change
5. Impact: {USER_EXPLOIT_IMPACT}

Note: Soroban transaction ordering within a ledger is determined by fee priority.
Users can bid higher fees to order before the role's transaction.
```

**Scenario E: User Griefs Role Preconditions**
```
1. {ROLE_FUNCTION} requires state: {PRECONDITION}
2. User manipulates state to violate {PRECONDITION}
3. {ROLE_NAME} submits transaction; function panics or reverts
4. Protocol enters degraded state (role functions blocked)
5. Impact: {GRIEF_IMPACT}
```

**Scenario F: User Forces Suboptimal Role Action**
```
1. {ROLE_NAME} must choose between options based on on-chain state
2. User manipulates state (deposits/withdrawals) to make worst option appear best
3. {ROLE_NAME} (following honest policy) chooses suboptimal path
4. User profits from forced suboptimal execution
5. Impact: {SUBOPTIMAL_IMPACT}
```

**Scenario G: Stale Rate via Discrete Updates**
```
1. Protocol's exchange rate only updates when {ROLE_NAME} calls {UPDATE_FUNCTION}
2. Between calls, rate is stale (does not reflect accumulated value)
3. User detects stale rate via on-chain state observation
4. User enters at stale rate (favorable), role updates, user exits at updated rate
5. Impact: {RATE_ARBIT_IMPACT}
```

---

## Step 6: Precondition Griefability Check

For each function callable by `{ROLE_NAME}`:

| Function | Preconditions | User Can Manipulate? | Grief Impact |
|----------|--------------|---------------------|--------------|
| {fn} | balance > 0 | YES — drain balance | Role function panics |
| {fn} | ledger_timestamp > last_update + interval | NO — time-based | N/A |
| {fn} | threshold met | YES — partial withdraw | Delayed execution |

**Resource metering griefing**: Can a user create enough state entries (Persistent storage records) that iterating over them during a role function exceeds the transaction resource budget? This prices out or DoSes the role function.

**TTL griefing**: Can a user let their Persistent storage entry expire, causing `unwrap()` to panic in a role function that iterates over all users?

---

## Step 6b: Admin/Privileged Function Griefability (EXHAUSTIVE)

Enumerate ALL authority-gated functions across the contract:

| Function | Authority Type | Preconditions | User Can Manipulate? | Grief Impact |
|----------|---------------|--------------|---------------------|--------------|
| {admin_fn} | {admin/operator/keeper} | {preconditions} | YES/NO | {impact} |

**Enumeration completeness check**:
- [ ] Total authority-gated functions in contract: {N}
- [ ] Functions analyzed in this table: {M}
- [ ] If M < N → INCOMPLETE — analyze missing functions before proceeding

**Soroban-specific checks**:
- Can users create Persistent storage entries (e.g., per-user positions) that block admin cleanup/migration functions? (entries must be read/written to be closed; N entries = N resource units consumed)
- Can users initiate multi-ledger operations (pending withdrawal, partial unstake) whose in-flight state blocks admin actions?
- Can a user set an allowance to a contract-controlled address and then revoke it just before the contract attempts `transfer_from`, causing the admin crank to fail?
- Can users donate tokens to the contract (unsolicited `transfer`) that bloat the tracked balance and exceed an admin's expected operating range?

---

## Common False Positives

- **Read-only functions**: If role only reads state, no abuse vector
- **Idempotent functions**: If calling twice has same effect as once, timing abuse is limited
- **User-initiated dependency**: If role action requires user to initiate first (two-phase), front-running may not apply
- **Economic alignment**: If role is economically aligned (staked collateral, fee-funded), malicious action has cost
- **Immutable address**: If the role `Address` is stored as a constant in contract code (not settable), key-rotation finding does not apply — but compromise risk is permanent

---

## Finding Template

```markdown
**ID**: [STR-N]
**Severity**: Critical/High/Medium/Low/Info
**Step Execution**: (see below)
**Rules Applied**: [R2:___, R6:___, R10:___, R13:___]
**Location**: src/{file}.rs:LineN
**Title**: {what role can do / what user can exploit}
**Description**: {specific abuse vector with code reference}
**Impact**: {quantified damage at worst-state parameters}
```

---

## Step Execution Checklist (MANDATORY)

| Step | Required | Completed? | Notes |
|------|----------|------------|-------|
| 1. Inventory Role Permissions | YES | | |
| 2. Analyze Within-Scope Abuse | YES | | |
| 3. Model Attack Scenarios (A, B, C) | YES | | |
| 4. Assess Mitigations | YES | | |
| 5. Model User-Side Exploitation (D, E, F, G) | **YES** | | **MANDATORY** — never skip |
| 6. Precondition Griefability Check | **YES** | | **MANDATORY** — never skip |
| 6b. Admin Instruction Griefability | **YES** | | **MANDATORY** — never skip |

### Cross-Reference Markers

**After Step 4**: DO NOT STOP HERE — Steps 5-6 analyze the reverse direction.

**After Step 5**: Cross-reference with TOKEN_FLOW_TRACING for token-related griefing vectors. IF role actions are time-predictable → document ledger-ordering (fee-bump) vectors.

**After Step 6**: IF any precondition is user-griefable → severity >= MEDIUM. Document protocol degradation timeline if role is blocked indefinitely.

**After Step 6b**: IF admin iterates over user-created Persistent entries → check for unbounded iteration / resource exhaustion.

### Output Format for Step Execution
```markdown
**Step Execution**: check1,2,3,4,5,6,6b | (no skips for this skill)
```
