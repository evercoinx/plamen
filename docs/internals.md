# Internals

## Skill System

Skills are methodology files loaded into agents at instantiation time. Three tiers:

### Standard Skills (per-language)

Always-available, triggered by pattern flags from recon. Examples: `ORACLE_ANALYSIS`, `SEMI_TRUSTED_ROLES`, `TOKEN_FLOW_TRACING`, `FLASH_LOAN_INTERACTION`.

| Language | Skills |
|----------|--------|
| EVM | 18 |
| Solana | 20 |
| Aptos | 22 (21 + core directives) |
| Sui | 22 (21 + core directives) |

### Injectable Skills (protocol-type-specific)

Loaded only when recon classifies the protocol as a matching type. Appended to existing agents (7 total):

| Skill | Trigger |
|-------|---------|
| VAULT_ACCOUNTING | `vault` protocol type |
| ACCOUNT_ABSTRACTION_SECURITY | ERC-4337, EntryPoint, UserOperation |
| NFT_PROTOCOL_SECURITY | ERC721/1155 with marketplace/staking/collateral |
| GOVERNANCE_ATTACK_VECTORS | Governor, Timelock, voting, proposal |
| OUTCOME_DETERMINISM | Finite-pool selection with depletion fallback |
| LENDING_PROTOCOL_SECURITY | liquidate/borrow/repay/collateral/LTV/healthFactor |
| DEX_INTEGRATION_SECURITY | swap/addLiquidity/removeLiquidity (non-DEX protocols) |

### Niche Agents (flag-triggered standalone)

Spawn as independent agents (1 depth budget slot each, 8 total):

| Agent | Trigger |
|-------|---------|
| EVENT_COMPLETENESS | `MISSING_EVENT` flag |
| SEMANTIC_GAP_INVESTIGATOR | Semantic invariant flags |
| SPEC_COMPLIANCE_AUDIT | `HAS_DOCS` flag |
| SIGNATURE_VERIFICATION_AUDIT | `HAS_SIGNATURES` flag |
| SEMANTIC_CONSISTENCY_AUDIT | `HAS_MULTI_CONTRACT` flag |
| MULTI_STEP_OPERATION_SAFETY | `MULTI_STEP_OPS` flag (approve/delegate + on-behalf-of) |
| CALLBACK_RECEIVER_SAFETY | `OUTCOME_CALLBACK` flag (EVM only) |
| DIMENSIONAL_ANALYSIS | `MIXED_DECIMALS` flag (EVM only) |

---

## Security Rules (R1-R16)

| Rule | Name | Summary |
|------|------|---------|
| R1 | External Return Types | Verify all external call return values |
| R2 | Keeper/Admin Griefability | Check both directions of privileged action abuse |
| R3 | Transfer Side Effects | Document token type and side effects |
| R4 | Adversarial Assumption | CONTESTED/unknown -> assume adversarial |
| R5 | Combinatorial Impact | N-entity systems need combinatorial analysis |
| R6 | Bidirectional Role | Semi-trusted roles analyzed in both directions |
| R7 | Donation-based DoS | Check thresholds vulnerable to donations |
| R8 | Cached Parameters | Multi-step ops with stale external state |
| R9 | Stranded Assets | Check recovery paths for locked funds |
| R10 | Worst-State Severity | Use worst realistic state, not current snapshot |
| R11 | Unsolicited Token Transfer | Trace impact of uninitiated transfers |
| R12 | Exhaustive Enabler Enum | 5 actor categories per dangerous state |
| R13 | Anti-Normalization | "By design" is not a valid severity dismissal |
| R14 | Cross-Variable Invariant | Aggregate variables, constraint coherence |
| R15 | Flash Loan Precondition | Flash-loan-accessible state manipulation |
| R16 | Oracle Integrity | Staleness, decimals, zero, failure modes |

---

## Severity Matrix

Impact x Likelihood:

| | **High Likelihood** | **Medium Likelihood** | **Low Likelihood** |
|---|---|---|---|
| **High Impact** (direct fund loss) | **Critical** | **High** | **Medium** |
| **Medium Impact** (conditional fund loss) | **High** | **Medium** | **Medium** |
| **Low Impact** (non-fund) | **Medium** | **Low** | **Low** |
| **Info** (quality, style) | **Informational** | **Informational** | **Informational** |

Downgrade modifiers: on-chain-only exploit (-1), view-function-only (cap Medium), fully-trusted actor (-1, floor Info).

---

## Evidence Tags

| Tag | Weight | Meaning |
|-----|--------|---------|
| `[PROD-ONCHAIN]` | 1.0 | Verified against production on-chain state |
| `[PROD-SOURCE]` | 0.9 | Verified against production source code |
| `[PROD-FORK]` | 0.9 | Verified on Anvil fork |
| `[MEDUSA-PASS]` | 1.0 | Medusa fuzzer found counterexample |
| `[POC-PASS]` | 1.0 | PoC compiled, executed, assertions passed |
| `[POC-FAIL]` | -- | PoC executed but assertions failed |
| `[CODE]` | 0.8 | Code-level evidence with specific locations |
| `[CODE-TRACE]` | 0.6 | Manual trace, no execution (caps at CONTESTED) |
| `[DOC]` | 0.4 | Documentation-based evidence |
| `[MOCK]` | 0.2 | Mock-based (not production-representative) |
