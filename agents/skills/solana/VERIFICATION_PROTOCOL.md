# VERIFICATION_PROTOCOL Skill (Solana)

> **Trigger Pattern**: Always (used by all verifier agents)
> **Inject Into**: security-verifier agents (Phase 5)
> **Purpose**: Prove hypotheses TRUE or FALSE using LiteSVM tests with Rust PoC code.

---

## Evidence Source Tracking (MANDATORY)

> **CRITICAL**: For EVERY piece of evidence used in verification, you MUST tag its source.
> Evidence from mocks or unverified external programs CANNOT support a REFUTED verdict.

### Evidence Source Tags

| Tag | Meaning | Valid for REFUTED? |
|-----|---------|-------------------|
| [PROD-ONCHAIN] | Production Solana account data (via `solana account` or RPC) | YES |
| [PROD-SOURCE] | Verified source from Solana Explorer / anchor-verified | YES |
| [PROD-LITESVM] | Tested on LiteSVM with mainnet account dumps | YES |
| [CODE] | Audited codebase (in-scope source) | YES |
| [MOCK] | Mock/test accounts or programs | **NO** |
| [EXT-UNV] | External, unverified program behavior | **NO** |
| [DOC] | Documentation/spec only | **NO** (needs verification) |

### Evidence Audit Table (REQUIRED in every verification output)

Before ANY verdict, fill this table:

```markdown
### Evidence Audit
| Claim | Evidence Source | Tag | Valid for REFUTED? |
|-------|-----------------|-----|-------------------|
| "CPI returns X" | Mock program | [MOCK] | NO |
| "PDA seeds match" | lib.rs:123 | [CODE] | YES |
| "Account layout is Y" | Solana Explorer verified | [PROD-SOURCE] | YES |
```

### Mock Rejection Rule

**AUTOMATIC OVERRIDE**: If ANY evidence supporting REFUTED has tag [MOCK] or [EXT-UNV]:
- CANNOT return REFUTED
- MUST return CONTESTED
- Triggers production verification

---

## Pre-Verification Understanding

Before writing ANY test code, you MUST answer:

### Question 1: What is the EXACT bug?
```
NOT: "Account validation is missing"
NOT: "State is inconsistent"

YES: "Instruction [X] accepts account [Y] without checking owner/type/seeds,
      allowing an attacker to substitute a crafted account with arbitrary data
      at field [Z] (file:line)"
```

### Question 2: What OBSERVABLE difference proves it?
```
NOT: "Accounts are different"
NOT: "Wrong state"

YES: "Before exploit: user_balance = 1000 tokens
      After exploit: user_balance = 0, attacker_balance = 1000
      Expected: transaction should have reverted with AccountConstraint error"
```

### Question 3: What is the EXACT assertion?
```
NOT: assert!(exploit_worked)

YES: assert_eq!(attacker_token_balance, expected_stolen_amount)
 OR: assert!(result.is_err(), "should have reverted but succeeded")
 OR: assert_ne!(state_before, state_after, "state changed when it should not")
```

**If you cannot answer all three -> ASK FOR CLARIFICATION**

---

## Pre-PoC Feasibility Gates (MANDATORY)

Before writing test code, verify these two gates. If either FAILS, adjust the hypothesis.

### Gate F1: Reachability
Trace a call path from a permissionless entry point to the vulnerable code.

- [ ] Entry point identified (public/external/entry function)
- [ ] Call path traced through intermediary functions
- [ ] All access checks on the path are passable by the attacker profile

If NO entry point reaches the vulnerable code → UNREACHABLE → FALSE_POSITIVE.
If reachable only through a restricted path → document the restriction, adjust likelihood.

### Gate F2: Math Bounds
Substitute real-world value domains into the expression that triggers the bug.

- [ ] Parameter domains identified (token decimals, max supply, TVL range, fee range, time bounds)
- [ ] Expression evaluated at worst-case feasible inputs
- [ ] Result crosses the bug threshold

If the bug requires values outside feasible domains → INFEASIBLE → FALSE_POSITIVE.
If feasible only at extreme but realistic parameters → document the threshold, proceed with adjusted severity.

**Both gates PASS → proceed to PoC. Either gate FAILS → document and stop.**

---

## LiteSVM Test Templates

### Template 1: Basic Exploit (Account State Manipulation)

```rust
use litesvm::LiteSVM;
use solana_sdk::{
    instruction::{AccountMeta, Instruction},
    pubkey::Pubkey,
    signature::{Keypair, Signer},
    system_instruction,
    transaction::Transaction,
};

/// BUG: {2-sentence description}
/// EXPECTED: {what should happen}
/// ACTUAL: {what does happen}
#[test]
fn test_exploit_basic() {
    // === SETUP ===
    let mut svm = LiteSVM::new();
    let program_id = Pubkey::new_unique(); // or load deployed program

    // Deploy the program under test
    let program_bytes = std::fs::read("target/deploy/program_name.so")
        .expect("Program binary not found");
    svm.add_program(program_id, &program_bytes);

    // Create actors
    let attacker = Keypair::new();
    let victim = Keypair::new();
    let authority = Keypair::new();
    svm.airdrop(&attacker.pubkey(), 10_000_000_000).unwrap(); // 10 SOL
    svm.airdrop(&victim.pubkey(), 10_000_000_000).unwrap();
    svm.airdrop(&authority.pubkey(), 10_000_000_000).unwrap();

    // Initialize protocol state (config, vaults, token mints, etc.)
    // ... setup instructions ...

    // === RECORD BEFORE STATE ===
    let state_before = svm.get_account(&state_account_pubkey);
    println!("=== BEFORE ===");
    // Deserialize and log relevant fields

    // === EXECUTE EXPLOIT ===
    println!("=== EXPLOIT ACTION ===");
    let exploit_ix = Instruction::new_with_borsh(
        program_id,
        &exploit_instruction_data,
        vec![
            AccountMeta::new(attacker.pubkey(), true),  // signer
            AccountMeta::new(target_account, false),     // writable
            // ... other accounts
        ],
    );
    let tx = Transaction::new_signed_with_payer(
        &[exploit_ix],
        Some(&attacker.pubkey()),
        &[&attacker],
        svm.latest_blockhash(),
    );
    let result = svm.send_transaction(tx);

    // === RECORD AFTER STATE ===
    println!("=== AFTER ===");
    let state_after = svm.get_account(&state_account_pubkey);
    // Deserialize and log relevant fields

    // === PROVE BUG ===
    println!("=== VERIFICATION ===");
    assert!(result.is_ok(), "Exploit transaction should succeed");
    // THE ASSERTION THAT PROVES THE BUG
    // Design this so it PASSES when the bug EXISTS
}
```

### Template 2: Account Substitution Attack

Tests whether an instruction properly validates account ownership, type, or PDA derivation.

```rust
#[test]
fn test_account_substitution() {
    let mut svm = LiteSVM::new();
    let program_id = Pubkey::new_unique();
    svm.add_program(program_id, &program_bytes);

    let attacker = Keypair::new();
    svm.airdrop(&attacker.pubkey(), 10_000_000_000).unwrap();

    // === CREATE LEGITIMATE ACCOUNT ===
    // ... initialize a real protocol account ...

    // === CREATE FAKE ACCOUNT WITH CRAFTED DATA ===
    // Craft account data that mimics the expected type but with malicious values
    let fake_account_keypair = Keypair::new();
    let mut fake_data = vec![0u8; expected_account_size];

    // Write the 8-byte Anchor discriminator (if Anchor program)
    // let discriminator = hash("account:AccountTypeName");
    // fake_data[..8].copy_from_slice(&discriminator[..8]);

    // Write crafted fields at known offsets
    // e.g., set balance field to max value
    // fake_data[offset..offset+8].copy_from_slice(&u64::MAX.to_le_bytes());

    // Allocate the fake account with the WRONG owner (attacker's program or system)
    // If the instruction does not check owner, this will be accepted
    let create_fake_ix = system_instruction::create_account(
        &attacker.pubkey(),
        &fake_account_keypair.pubkey(),
        svm.minimum_balance_for_rent_exemption(fake_data.len()),
        fake_data.len() as u64,
        &program_id, // or &wrong_program_id to test owner check
    );

    // Set the fake account data
    // svm.set_account(fake_account_keypair.pubkey(), fake_account);

    // === ATTEMPT SUBSTITUTION ===
    let exploit_ix = Instruction::new_with_borsh(
        program_id,
        &instruction_data,
        vec![
            AccountMeta::new(attacker.pubkey(), true),
            // Pass fake account where legitimate account is expected
            AccountMeta::new(fake_account_keypair.pubkey(), false),
            // ... other accounts
        ],
    );
    let tx = Transaction::new_signed_with_payer(
        &[exploit_ix],
        Some(&attacker.pubkey()),
        &[&attacker, &fake_account_keypair],
        svm.latest_blockhash(),
    );
    let result = svm.send_transaction(tx);

    // === VERIFY ===
    // If the bug EXISTS: transaction succeeds with fake account (no validation)
    // If the bug is FIXED: transaction fails with constraint violation
    assert!(
        result.is_ok(),
        "Account substitution should succeed if validation is missing"
    );
    // Check that attacker gained something from the substitution
}
```

### Template 3: CPI Attack (Malicious Program Substitution)

Tests whether the protocol validates CPI target program IDs.

```rust
#[test]
fn test_cpi_attack() {
    let mut svm = LiteSVM::new();

    // Deploy the LEGITIMATE target program
    let target_program_id = Pubkey::new_unique();
    svm.add_program(target_program_id, &target_program_bytes);

    // Deploy the protocol under test
    let protocol_program_id = Pubkey::new_unique();
    svm.add_program(protocol_program_id, &protocol_program_bytes);

    // Deploy MALICIOUS program that mimics the target's interface
    // but returns attacker-favorable results
    let malicious_program_id = Pubkey::new_unique();
    svm.add_program(malicious_program_id, &malicious_program_bytes);

    let attacker = Keypair::new();
    svm.airdrop(&attacker.pubkey(), 10_000_000_000).unwrap();

    // === SETUP LEGITIMATE STATE ===
    // ... initialize protocol with legitimate target program ...

    // === ATTEMPT CPI WITH MALICIOUS PROGRAM ===
    // Pass malicious_program_id where target_program_id is expected
    let exploit_ix = Instruction::new_with_borsh(
        protocol_program_id,
        &instruction_data,
        vec![
            AccountMeta::new(attacker.pubkey(), true),
            // Account that should be the target program but is malicious
            AccountMeta::new_readonly(malicious_program_id, false),
            // ... other accounts the CPI expects
        ],
    );
    let tx = Transaction::new_signed_with_payer(
        &[exploit_ix],
        Some(&attacker.pubkey()),
        &[&attacker],
        svm.latest_blockhash(),
    );
    let result = svm.send_transaction(tx);

    // === VERIFY ===
    // If bug EXISTS: CPI to malicious program succeeds
    // If bug FIXED: transaction fails with "incorrect program id" error
    assert!(
        result.is_ok(),
        "CPI to malicious program should succeed if program ID not validated"
    );
}
```

### Template 4: Multi-Instruction Composition (Flash-Loan-Like Pattern)

Tests atomic borrow+use+repay patterns within a single transaction.

```rust
#[test]
fn test_multi_instruction_exploit() {
    let mut svm = LiteSVM::new();
    let program_id = Pubkey::new_unique();
    svm.add_program(program_id, &program_bytes);

    let attacker = Keypair::new();
    svm.airdrop(&attacker.pubkey(), 10_000_000_000).unwrap();

    // === SETUP ===
    // ... initialize protocol, fund pools, create accounts ...

    // === RECORD BEFORE STATE ===
    let attacker_balance_before = /* read token balance */;
    let pool_balance_before = /* read pool balance */;

    // === BUILD MULTI-INSTRUCTION TRANSACTION ===
    // All instructions execute atomically in a single transaction
    // Same Clock values, same slot, all-or-nothing

    let ix_1_borrow = Instruction::new_with_borsh(
        program_id,
        &borrow_instruction_data, // Borrow large amount
        borrow_accounts.clone(),
    );

    let ix_2_exploit = Instruction::new_with_borsh(
        program_id,
        &exploit_instruction_data, // Use borrowed funds to manipulate state
        exploit_accounts.clone(),
    );

    let ix_3_extract = Instruction::new_with_borsh(
        program_id,
        &extract_instruction_data, // Extract value from manipulated state
        extract_accounts.clone(),
    );

    let ix_4_repay = Instruction::new_with_borsh(
        program_id,
        &repay_instruction_data, // Repay borrowed amount
        repay_accounts.clone(),
    );

    // All 4 instructions in ONE transaction (atomic)
    let tx = Transaction::new_signed_with_payer(
        &[ix_1_borrow, ix_2_exploit, ix_3_extract, ix_4_repay],
        Some(&attacker.pubkey()),
        &[&attacker],
        svm.latest_blockhash(),
    );
    let result = svm.send_transaction(tx);

    // === VERIFY ===
    assert!(result.is_ok(), "Multi-instruction exploit should succeed");

    let attacker_balance_after = /* read token balance */;
    let profit = attacker_balance_after - attacker_balance_before;
    assert!(
        profit > 0,
        "Attacker should profit from atomic multi-instruction exploit"
    );

    println!("Profit: {} tokens", profit);
    println!("Pool balance change: {}", pool_balance_before - /* pool after */);
}
```

### Template 5: PDA Collision/Seed Manipulation

Tests whether PDA derivation is unique and cannot collide across different logical entities.

```rust
#[test]
fn test_pda_collision() {
    let mut svm = LiteSVM::new();
    let program_id = Pubkey::new_unique();
    svm.add_program(program_id, &program_bytes);

    let attacker = Keypair::new();
    svm.airdrop(&attacker.pubkey(), 10_000_000_000).unwrap();

    // === DERIVE TWO PDAs THAT SHOULD BE DIFFERENT ===
    // If seed schema is weak, different logical entities may share a PDA

    let (pda_entity_a, bump_a) = Pubkey::find_program_address(
        &[b"account", entity_a_key.as_ref()],
        &program_id,
    );

    let (pda_entity_b, bump_b) = Pubkey::find_program_address(
        &[b"account", entity_b_key.as_ref()], // Different entity, same seed prefix
        &program_id,
    );

    // === CHECK FOR COLLISION ===
    // These SHOULD be different addresses for different entities
    assert_ne!(
        pda_entity_a, pda_entity_b,
        "PDAs for different entities should not collide"
    );

    // === TEST CROSS-ENTITY ACCESS ===
    // Try to use entity_a's PDA in an instruction meant for entity_b
    let cross_access_ix = Instruction::new_with_borsh(
        program_id,
        &instruction_data_for_entity_b,
        vec![
            AccountMeta::new(attacker.pubkey(), true),
            AccountMeta::new(pda_entity_a, false), // Wrong PDA!
            // ... other accounts
        ],
    );
    let tx = Transaction::new_signed_with_payer(
        &[cross_access_ix],
        Some(&attacker.pubkey()),
        &[&attacker],
        svm.latest_blockhash(),
    );
    let result = svm.send_transaction(tx);

    // If bug EXISTS: cross-entity access succeeds (seeds not checked)
    // If bug FIXED: transaction fails with seeds constraint error
    assert!(
        result.is_err(),
        "Cross-entity PDA access should be rejected"
    );
}
```

---

## Template 6: Trident Fuzz Test (Anchor Programs Only)

> **Prerequisite**: `trident_available: true` in `build_status.md`. If false, skip to proptest or boundary-value tests.

Trident generates scaffolding from the program IDL. The verifier customizes the generated handlers to target the finding's instruction sequence and adds invariant assertions.

### Step 1: Initialize (if trident-tests/ does not exist)
```bash
trident init
```
This creates `trident-tests/fuzz_tests/` with handler templates derived from the program's IDL.

### Step 2: Customize Fuzz Instructions
Edit `trident-tests/fuzz_tests/fuzz_0/fuzz_instructions.rs`:
```rust
use trident_fuzz::*;

// Trident auto-generates FuzzInstruction enum from IDL.
// Customize: add invariant checks, bound parameters, add pre/post hooks.

impl FuzzInstruction {
    // Add custom invariant check after each instruction
    fn check_invariant(&self, accounts: &AccountsStorage) {
        // e.g., verify total_supply == sum(balances)
        // e.g., verify vault.amount >= total_shares
    }
}
```

### Step 3: Run Campaign (v0.11+ — no honggfuzz needed)
```bash
# Windows: set OpenSSL env vars if needed (auto-detect from common install paths)
# On Linux/macOS this is not needed (system OpenSSL is used)
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]] && [ -z "$OPENSSL_DIR" ]; then
  for base in "/c/Program Files/OpenSSL-Win64" "/c/Program Files/OpenSSL"; do
    if [ -d "$base/include/openssl" ]; then
      export OPENSSL_DIR="$base" OPENSSL_LIB_DIR="$base/lib/VC/x64/MD" OPENSSL_INCLUDE_DIR="$base/include"
      break
    fi
  done
fi
# Run from trident-tests/ directory
cd trident-tests && trident fuzz run fuzz_0
# With detailed logging:
TRIDENT_LOG=1 trident fuzz run fuzz_0
```

### Step 4: Check for Violations
```bash
# Trident v0.11+ writes violations to .fuzz-artifacts/
ls -la trident-tests/.fuzz-artifacts/ 2>/dev/null
# Re-run with specific seed for reproduction:
trident fuzz run fuzz_0 <SEED>
```

### Evidence Tagging
- Trident violation with reproducible seed -> `[POC-PASS]` (mechanical proof)
- Trident campaign completes with no violations -> supports `[POC-FAIL]` for the fuzz variant
- Evidence tag: `[TRIDENT-FUZZ]` (subtype of `[CODE]`) — valid for both CONFIRMED and REFUTED

---

## Fork Testing Equivalent (Production Account Loading)

Solana does not have a direct Anvil-fork equivalent. Use this approach:

### Step 1: Dump Production Accounts
```bash
# Dump all accounts the protocol uses
solana account <config_account> --output json > config_dump.json
solana account <vault_account> --output json > vault_dump.json
solana account <user_account> --output json > user_dump.json
# Repeat for all relevant accounts
```

### Step 2: Load into LiteSVM
```rust
use solana_sdk::account::Account;

#[test]
fn test_with_production_state() {
    let mut svm = LiteSVM::new();

    // Load production account dumps
    let config_data: Account = serde_json::from_str(
        &std::fs::read_to_string("config_dump.json").unwrap()
    ).unwrap();
    svm.set_account(config_pubkey, config_data);

    let vault_data: Account = serde_json::from_str(
        &std::fs::read_to_string("vault_dump.json").unwrap()
    ).unwrap();
    svm.set_account(vault_pubkey, vault_data);

    // Deploy the program (use production program binary if available)
    svm.add_program(program_id, &program_bytes);

    // Now test against REAL production state
    // Evidence tag: [PROD-LITESVM]
}
```

### Step 3: Evidence Tagging
- Account dumps from mainnet RPC -> data tagged [PROD-ONCHAIN]
- LiteSVM tests with production data -> tagged [PROD-LITESVM]
- [PROD-LITESVM] is valid for REFUTED verdicts (equivalent to [PROD-FORK] on EVM)

---

## Dual-Perspective Verification (MANDATORY)

### Phase 1 - ATTACKER: Assume you ARE the attacker.
- What is the complete attack instruction sequence?
- What accounts do you need to create/craft?
- What is the profit/damage with real token amounts?
- Can you compose multiple instructions in one transaction for atomicity?
- Why would this succeed? (Which validation is missing/wrong?)

### Phase 2 - DEFENDER: Assume you are the protocol team.
- What account constraint prevents this?
- What PDA seeds ensure correct derivation?
- What CPI program ID check blocks substitution?
- Why is this safe by design?

### Phase 3 - VERDICT: Which argument won?

---

## Realistic Parameter Validation

Substitute ACTUAL program constants (basis points, fee rates, thresholds, account sizes).
Apply Rule 10: Use worst realistic operational state, not current snapshot.

```
State: 'With real constants [fee_bps=X, max_leverage=Y, tvl=Z] at worst-state
[max_users, max_positions], bug triggers when [condition]'
OR: 'With real constants, bug does NOT trigger because [reason]'
```

---

## Anti-Downgrade Guard (MANDATORY for VS/BLIND findings)

When verifying a finding from Validation Sweep ([VS-*]) or Blind Spot Scanner ([BLIND-*]), you MUST apply Rule 13's 5-question test BEFORE downgrading severity or marking FALSE_POSITIVE:

1. **Who is harmed** by this design gap?
2. **Can affected users avoid** the harm?
3. **Is the gap documented** in protocol docs?
4. **Could the protocol achieve the same goal** without this gap?
5. **Does the instruction fulfill its stated purpose completely?**

**HARD RULE**: If the finding shows Program A has protection X but Program B lacks it for the same user action -> defense parity gap, NOT "by design". Minimum severity: Medium.

---

## New Observations (MANDATORY)

If during verification you discover a NEW bug, account validation gap, or edge case NOT covered by any existing hypothesis, document it under:

### New Observations
- [VER-NEW-1]: {title} -- {program:instruction} -- {brief description}

These will be reviewed by the orchestrator for possible inclusion as new findings.

---

## Error Trace Output (MANDATORY for CONTESTED/FALSE_POSITIVE)

When verdict is CONTESTED or FALSE_POSITIVE, document the failure details:

### Error Trace
- **Failure Type**: ACCOUNT_CONSTRAINT / CPI_ERROR / ARITHMETIC_OVERFLOW / INSUFFICIENT_FUNDS / UNEXPECTED_STATE
- **Location**: {program}:{instruction}:{approximate line in handler}
- **Error Code**: {Anchor error code or custom error, if any}
- **State at Failure**: {key account fields and their values when test failed}
- **Investigation Question**: {What would need to be answered to resolve this}

---

## RAG Queries Before PoC (MANDATORY for HIGH/CRITICAL)

Before writing PoC tests for HIGH/CRITICAL findings:

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

### Step 4: Live Search for Solana-Specific Precedents
```
mcp__unified-vuln-db__search_solodit_live(
  keywords="{solana vulnerability pattern}",
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
- **Historical Precedent**: [matching Solana-specific vulnerabilities]
```

---

## RAG Confidence Override

| RAG Confidence | Local Verdict | Final Verdict | Action |
|----------------|---------------|---------------|--------|
| >= 7/8 matches | FALSE_POSITIVE | **CONTESTED** (override) | Cannot dismiss -- strong precedent |
| >= 6/8 matches | FALSE_POSITIVE | **CONTESTED** (override) | Cannot dismiss -- significant precedent |
| < 6/8 matches | FALSE_POSITIVE | FALSE_POSITIVE | Allowed -- limited precedent |

---

## Chain Hypothesis PoC Requirements

Chain hypotheses receive PRIORITY verification. Multi-step exploits must test the COMPLETE sequence:

```rust
#[test]
fn test_chain_hypothesis_full() {
    let mut svm = LiteSVM::new();
    // ... setup ...

    // ========================================
    // STEP 1: ENABLER (Finding B)
    // Execute action that creates the postcondition
    // ========================================
    let enabler_ix = Instruction::new_with_borsh(/* ... */);
    let tx1 = Transaction::new_signed_with_payer(
        &[enabler_ix],
        Some(&attacker.pubkey()),
        &[&attacker],
        svm.latest_blockhash(),
    );
    svm.send_transaction(tx1).unwrap();

    // ========================================
    // VERIFY POSTCONDITION CREATED
    // Assert precondition for Finding A is now met
    // ========================================
    let postcondition_account = svm.get_account(&postcondition_pubkey);
    // assert postcondition state is as expected

    // ========================================
    // STEP 2: BLOCKED FINDING (Finding A)
    // Execute previously-blocked attack using postcondition
    // ========================================
    let exploit_ix = Instruction::new_with_borsh(/* ... */);
    let tx2 = Transaction::new_signed_with_payer(
        &[exploit_ix],
        Some(&attacker.pubkey()),
        &[&attacker],
        svm.latest_blockhash(),
    );
    let result = svm.send_transaction(tx2);

    // ========================================
    // VERIFY CHAIN IMPACT
    // Combined impact should exceed either finding alone
    // ========================================
    assert!(result.is_ok(), "Chain exploit should succeed");
    let profit = /* calculate attacker profit */;
    assert!(profit > 0, "Chain attack should be profitable");
}
```

### Bidirectional Chain Analysis (Rule 6 Extension)
For chains involving semi-trusted roles (operator/keeper/crank):
Verify BOTH directions: (1) role executes chain to harm users, AND (2) users exploit role timing to trigger chain.
If only one direction analyzed -> verdict CANNOT be FALSE_POSITIVE. Return CONTESTED.

---

## Interpreting Results

### Test PASSES -> Bug CONFIRMED
The assertion that "proves the bug" succeeded.

### Test FAILS -> Check Why

| Failure | Meaning | Action |
|---------|---------|--------|
| Account constraint error | Validation IS present | Re-examine hypothesis |
| Program error / custom error | Instruction logic rejects | Check if rejection is the bug or the fix |
| Insufficient funds | Setup amounts wrong | Fix test setup |
| Transaction too large | Too many accounts/instructions | Split transaction or reduce scope |
| Blockhash expired | LiteSVM state issue | Get fresh blockhash |

---

## Iteration Protocol

**Attempt 1:** Direct implementation of test strategy from hypothesis.
**Attempt 2:** Adjust parameters (different amounts, different account states, different instruction ordering).
**Attempt 3:** Re-examine assumptions (are account constraints correctly modeled? Are PDA seeds correct? Is instruction data serialization correct?).
**After 5 attempts:** If still fails -> FALSE_POSITIVE with documented reasoning.

---

## Severity Determination

### CRITICAL
- Direct fund theft (token drain, SOL extraction)
- Program upgrade to malicious code (if upgrade authority compromised and no timelock)
- Arbitrary CPI execution
- No special prerequisites needed

### HIGH
- Fund loss with specific setup (account pre-creation, timing)
- Broken core instruction (deposits, withdrawals, liquidations)
- Freeze authority abuse
- Significant TVL at risk

### MEDIUM
- Limited fund loss under specific conditions
- Account state corruption (non-fund data)
- Edge cases with real impact at design limits
- Moderate value at risk

### LOW
- Negligible direct impact
- Extreme edge cases only
- Authority-controlled risk (with multisig + timelock)
- View function / off-chain data issues

---

## Output Format

### CONFIRMED
```markdown
## Verdict: CONFIRMED

### Bug Mechanism Verified
{Explain what the LiteSVM test proves in 2-3 sentences}

### Test Code
{Full Rust test function}

### Test Output
{Relevant assertions and logged values}

### Key Evidence
| Metric | Value |
|--------|-------|
| Before | {value} |
| After | {value} |
| Expected | {value} |
| Difference | {calculation} |

### Evidence Audit
| Claim | Evidence Source | Tag | Valid for REFUTED? |
|-------|-----------------|-----|-------------------|

### Severity: {LEVEL}
{Justification in 1-2 sentences}
```

### FALSE_POSITIVE
```markdown
## Verdict: FALSE_POSITIVE

### Attempts Made
**Attempt 1:**
- Approach: {description}
- Result: {what happened -- include error codes}
- Learning: {insight}

**Attempt 2:**
- Approach: {description}
- Result: {what happened}
- Learning: {insight}

**Attempt 3:**
- Approach: {description}
- Result: {what happened}
- Learning: {insight}

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
| External program behavior verified against PRODUCTION | YES/NO | {details} |
| All callers/instruction paths checked | YES/NO | {details} |
| Account validation completeness confirmed | YES/NO | {details} |

### Evidence Audit
| Claim | Evidence Source | Tag | Valid for REFUTED? |

### Why This Cannot Be REFUTED
{Explain what evidence is missing to definitively rule out the bug}

### Escalation Required
- [ ] Fetch production program source for {external dep}
- [ ] Dump production account state for {account}
- [ ] Check additional instruction paths: {list}

### Error Trace
{as above}
```

---

## Insufficient Evidence (HALT CONDITIONS)

Before marking REFUTED, check ALL boxes:
- [ ] External program behavior verified against PRODUCTION (not mock)
- [ ] Attack path checked on ALL instruction handlers that access the same accounts
- [ ] Profit calculated with attacker HOLDING tokens (not just sending)
- [ ] Missing precondition documented (type: STATE / ACCESS / TIMING / EXTERNAL / BALANCE)
- [ ] Searched other findings for matching postconditions (chain analysis integration)
- [ ] PDA derivation verified with correct seeds (not assumed)
- [ ] Account owner checks verified in source (not assumed from Anchor derive)

### Evidence That Does NOT Count
- "Mock program shows X" -- mocks are not production
- "Standard SPL Token" -- may have Token-2022 extensions (transfer hooks, fees)
- "Attacker loses by sending tokens" -- may profit via position held
- "Instruction is internal (pub(crate))" -- may be reachable via CPI
- "Requires authority" -- authority may be compromised or EOA
- "Attacker cannot acquire X" -- another finding may CREATE this condition
