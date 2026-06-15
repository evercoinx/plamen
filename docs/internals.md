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
| Soroban | 19 (13 cross-language + 6 Soroban-specific) |

### Injectable Skills (protocol-type-specific)

Loaded only when recon classifies the protocol as a matching type. Appended to existing agents (8 total):

| Skill | Trigger |
|-------|---------|
| VAULT_ACCOUNTING | `vault` protocol type |
| ACCOUNT_ABSTRACTION_SECURITY | ERC-4337, EntryPoint, UserOperation |
| NFT_PROTOCOL_SECURITY | ERC721/1155 with marketplace/staking/collateral |
| GOVERNANCE_ATTACK_VECTORS | Governor, Timelock, voting, proposal |
| OUTCOME_DETERMINISM | Finite-pool selection with depletion fallback |
| LENDING_PROTOCOL_SECURITY | liquidate/borrow/repay/collateral/LTV/healthFactor |
| DEX_INTEGRATION_SECURITY | swap/addLiquidity/removeLiquidity (non-DEX protocols) |
| INTEGRATION_HAZARD_RESEARCH | NAMED_EXTERNAL_PROTOCOL flag (named external protocol imports) |

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
| STABLESWAP_COMPLIANCE | `STABLESWAP_FORK` flag (Curve/StableSwap fork) |

### L1 Skills (infrastructure audits)

Loaded only in L1 mode (`/plamen-l1-wizard` in Claude Code, or `plamen l1` from terminal). Injected into `depth-consensus-invariant` or `depth-network-surface`:

| Skill | Trigger |
|-------|---------|
| CONSENSUS_SAFETY_INVARIANTS | `CONSENSUS` flag |
| CONSENSUS_MATH_CORRECTNESS | `CONSENSUS` + difficulty/EMA/reward patterns |
| FORK_CHOICE_AUDIT | `CONSENSUS` + fork_choice/ghost patterns |
| P2P_DOS_AND_ECLIPSE | `P2P` flag |
| MEMPOOL_ASYMMETRIC_DOS | `MEMPOOL` flag |
| LIGHT_CLIENT_PROOF_VERIFICATION | `LIGHT_CLIENT` flag |
| RPC_SURFACE_AUDIT | `RPC` flag |
| BLS_AGGREGATION_AUDIT | `BLS` flag |
| STATE_SYNC_PRUNING | `STATE_SYNC` flag |
| EXECUTION_CLIENT_HARDENING | `EXECUTION` flag |
| CROSS_ENVIRONMENT_SEMANTIC_DRIFT | `XENV` flag |
| VALIDATOR_LIFECYCLE_AND_SLASHING | `VALIDATOR_LIFECYCLE` flag |
| HARDFORK_ACTIVATION_AND_PROTOCOL_UPGRADE | `HARDFORK` flag |
| GO_CONCURRENCY_SAFETY | Always (Go code) |
| RUST_UNSAFE_AUDIT | Always (Rust code) |
| DEPENDENCY_AUDIT_NODECLIENT | Always (L1) |
| DATA_AVAILABILITY_ENFORCEMENT | `data_availability` flag |
| PEER_SCORING_CORRECTNESS | `P2P` + scoring patterns |
| GOSSIP_CACHE_INVARIANCE | `P2P` + cache patterns |
| CONSENSUS_TX_IDENTITY_INVARIANTS | `CONSENSUS` + txid/nonce patterns |
| CONFIG_CORRECTNESS | `L1_PATTERN` + config patterns |
| WRITE_ERROR_DIVERGENCE | `STORAGE`/`DATABASE_TX` flag |

Plus 2 new depth agents for L1 mode: **depth-consensus-invariant** and **depth-network-surface**.

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

### L1 Evidence Tags

| Tag | Meaning |
|-----|---------|
| `[DIFF-PASS]` | Cross-client differential test passed |
| `[CONFORMANCE-PASS]` | Spec conformance test passed |
| `[NON-DET-PASS]` | Non-determinism detection test passed |
| `[FUZZ-PASS]` | Fuzzer found counterexample |
| `[LSP-TRACE]` | LSP-assisted code trace |

---

## Driver

One-liner: **Python driver → worker pool → one backend PTY session (Claude Code or Codex) per artifact → disk artifact gate → retry only missing/bad rows. The worker saying "done" is not trusted; disk markers are.**

The pipeline driver (`plamen_driver.py`) executes phases as isolated subprocesses. This is the only execution model — all invocations (`/plamen-wizard`, `plamen` terminal, `plamen core`, etc.) launch this driver. It runs on Windows, macOS, and Linux against either the Claude Code or Codex (BETA) backend.

### Driver layout

| Component | Purpose |
|-----------|---------|
| `plamen_driver.py` | Phase scheduling, checkpointing, retry, gate checking, worker-pool orchestration |
| `plamen_types.py` | Canonical definitions (evidence tags, severities, finding ID regex); `plamen_home()` resolves `~/.plamen/` (canonical install root) — `~/.claude/` and `~/.codex/plamen/` are install-created symlinks pointing at it, see `glossary.md` / `repository-structure.md` |
| `plamen_parsers.py` | LLM output parsing (report index, verification results) |
| `plamen_validators.py` | Artifact quality gates (mechanical, not LLM-dependent); per-row marker statuses (`_BREADTH_STATUS_*`, `scripts/plamen_validators.py:883-888`) |
| `plamen_prompt.py` | Phase prompt building with forward-ref sanitization |
| `plamen_mechanical.py` | Deterministic report assembly, dedup, tier dispatch |
| `plamen_display.py` | Rich terminal UI for driver progress |
| `plamen_markdown.py` | Markdown AST helpers shared between parsers and validators |
| `plamen_contracts.py` | Worker artifact / marker envelope contracts (manifest schema, expected-output shape) |
| `mechanical_verify.py` | Phase 5 mechanical verification helpers (severity caps, PoC demotions, integrity gates) |
| `chain_prep.py` | Chain-analysis pre-pass: extracts candidate finding pairs with shared state / type before the chain LLM phase |
| `report_index_machinery.py` | Report-index ID assignment and `report_coverage.md` ledger reconciliation |
| `pty_exec.py` | Claude PTY session: POSIX `pty.openpty()` + `Popen` with `preexec_fn` setup, Windows `winpty.PtyProcess.spawn`; `ClaudePtySession`, transcript polling, compaction detection (`scripts/pty_exec.py:548-720`) |
| `preflight_pty_transports.py` | Per-host PTY transport probe; cache schema v3 (`scripts/preflight_pty_transports.py:62`) |
| `codex_adapter.py` | Codex CLI backend: tool translation, path rewriting (`~/.claude/` ↔ `~/.codex/plamen/`) |
| `recon_prepass.py` | Pre-recon static analysis (Slither, Opengrep, SCIP) |

The driver auto-detects the active backend via `plamen_home()` (`scripts/plamen_types.py:87-101`). Resolution order: `PLAMEN_HOME` env → script-relative install root → `~/.claude/` fallback. The canonical install lives under `~/.plamen/`; the backend-named directories (`~/.claude/`, `~/.codex/plamen/`) are symlinks created at install time so each CLI can find the same prompts/rules/skills tree. Config files differ per backend: `CLAUDE.md` + `settings.json` + `mcp.json` for Claude Code; `AGENTS.md` + `config.toml` for Codex.

### Execution model

Three execution shapes coexist behind the same `plamen_home()` and disk-gate primitives:

1. **Direct PTY worker pool** — used for `breadth`, `rescan`, `per_contract`, and `depth`. The driver builds a manifest of expected output artifacts (one per spawned worker), launches a bounded `ThreadPoolExecutor` (`_run_breadth_worker_pool_pty`, `scripts/plamen_driver.py:4636-4760`), and spawns one Claude PTY session per row via `ClaudePtySession`. Each worker's success is a disk artifact passing the row gate — the worker's prose `DONE` is **advisory only**.
2. **LLM phase session** — used for sequential analytical phases (recon, inventory, chain, report_index, skeptic, judge, crossbatch, tier writers, etc.). The driver spawns one Claude PTY session per phase, supervises it with `wait_for_turn_complete`, and runs `gate_passes` against the scratchpad after the turn ends.
3. **Python mechanical** — used for `inventory_prepare`, `report_assemble`, `chain_prep`, `verify_aggregate`, semantic-dedup fallback, severity binding, and similar plumbing phases. No LLM is spawned; deterministic Python in `plamen_mechanical.py` / `mechanical_verify.py` / `chain_prep.py` / `report_index_machinery.py` reads and writes scratchpad artifacts in-process.

All three shapes share the same checkpoint (`_v2_checkpoint.json`), the same `gate_passes` validator, the same `plamen_home()`-derived paths, and the same artifact ownership rules. Phase ordering and retry policy live in `plamen_driver.py`; shape-specific behavior is dispatched by phase name.

### Model routing

Opus phases run on **Opus 4.8** (`claude-opus-4-8`) by default across all modes — its stronger multi-step instruction-following reduces attempt-1 misses on recon coverage, breadth/rescan fan-out, and verification rigor (`PLAMEN_OPUS_MODEL`, `scripts/plamen_types.py:105-125`). **Core** (like all modes) defaults its opus tier to Opus 4.8; the **Thorough** promotion only additionally raises to Opus 4.8 the reasoning-critical roles (discovery = breadth + depth, verification shards, skeptic-judge) that would otherwise run on Sonnet, while **Light** stays on Sonnet to bound plan usage (`PLAMEN_THOROUGH_OPUS_MODEL`, `scripts/plamen_types.py:111-118`). Both defaults are env-overridable for benchmarking or cost-capping.

### Backends (Claude Code + Codex BETA)

The driver runs against two interchangeable CLI backends behind the same `plamen_home()` and disk-gate primitives:

- **Claude Code** (default) — config files `CLAUDE.md` + `settings.json` + `mcp.json`.
- **Codex CLI** (`codex exec`, **BETA / cost-saving**) — OpenAI's CLI as an alternative worker backend, with research-backed model/tier/compact configuration and per-job depth fan-out (one `codex exec` per depth job, which fixes the never-cut-stub halt). Codex model aliases map through `_CODEX_MODEL_MAP` (`scripts/plamen_types.py:128`), config files are `AGENTS.md` + `config.toml`, and `codex_adapter.py` regenerates them from the Claude-side manifests to prevent drift. Codex usage-cap messages are detected in natural language so the driver auto-waits instead of halting, Codex depth runs real Devil's-Advocate iter-2, and `context-exceeded` no longer perma-fails. The active backend is detected at startup (`backend == "codex"`, `scripts/plamen_driver.py:205`) and selected paths/tools are translated only when Codex is active.

### Cross-platform (Windows + macOS + Linux)

The PTY worker-pool model runs on all three platforms. POSIX hosts (macOS, Linux) use `pty.openpty()` + `Popen` ownership with a `SIGCHLD` reset on spawn; Windows uses `winpty` (see the PTY transport section below). Nested-session env isolation strips `CLAUDE_CODE_*` markers from child workers on every platform, and PATH is persisted into the child environment so backend binaries resolve.

### Ecosystem auto-detection

The configured language/ecosystem is mechanically auto-detected and auto-corrected at startup with no halt-to-rerun (`_detect_ecosystem`, `scripts/plamen_driver.py:14879`), shown on the startup banner, and resolved via manifest-priority rules (a suffix-only signal never clobbers an explicit config; native-SDK / Pinocchio Solana is detected at high confidence). The auto-corrector is recall-safe by design — a wrong auto-correct is treated as worse than the status quo (`_language_correction`, `scripts/plamen_driver.py:14837`), and L1 pipelines always keep their configured Rust/Go language.

### Haltless resilience

A finished audit is never discarded at the finish line. The `report_index`, `verify`, `inventory`, and resume paths **repair-then-degrade** instead of halting: unfinished obligations are surfaced as flagged Appendix-B items in `AUDIT_REPORT.md` rather than blocking the run. Retry/recovery is unified across backend × mode × pipeline (hinted 3rd retry for under-covering phases, rescan added to recovering phases, verify queue-completeness backfill that stops the resume-rewind loop), and stale/corrupt checkpoints recover rather than stranding the run. Degraded phases carry a sentinel that is cleared on a genuine resume (`checkpoint.degraded` / `clear_degraded_sentinel`, `scripts/plamen_driver.py:2187-2191`).

### Compaction as informational

When a Claude PTY session's transcript shows an auto-compaction event, the driver emits a one-shot `INFO` log line for the affected phase (`compaction_warned` guard, `scripts/plamen_driver.py:3834-3880`). Compaction never gates a phase and never alters control flow — the disk-gate verdict is the only authority. A coordinator that emits `DONE` after compaction but leaves the disk gate red is treated identically to any other premature-DONE: the missing-only / live / resume continuation handles recovery.

---

## Worker contract

Worker artifacts (breadth, depth, rescan, per-contract) carry an HTML-comment envelope of `PLAMEN_*` markers. The canonical 7-line `PLAMEN_STATUS` marker block is defined in `docs/architecture.md`; internals.md summarizes how the driver consumes it.

### Marker-driven row verdicts

For each manifest row, `compute_phase_row_statuses` returns one of four verdicts (`scripts/plamen_validators.py:883-1153`):

| Verdict | Meaning | Driver action |
|---------|---------|---------------|
| **complete** | File exists, `PLAMEN_STATUS: COMPLETE` is present, structural completeness passes | Row is locked in; not retried |
| **in_progress** | File has `PLAMEN_*` markers but `STATUS != COMPLETE`, or unmarked on a fresh-audit scratchpad | Re-queued on the next worker-pool attempt |
| **wrong-phase** (`structural_fail`) | File is `COMPLETE` but fails a required-heading / placeholder / receipts check | Re-queued with structural-failure reasons surfaced to the next worker |
| **stale-legacy** (`legacy_unmarked`) | Substantive content but no `PLAMEN_ARTIFACT` marker on a legacy/resumed scratchpad | Passes with warning; the supervision loop ignores it (treated as pre-existing) |

**`DONE` is advisory for worker phases.** The driver never trusts Claude's natural-language completion claim for breadth/depth/rescan/per-contract; only the marker envelope and the structural gate decide whether a row is locked in. This is the load-bearing rule that the entire artifact-complete PTY supervision design is built around.

### Agent-row routing markers

To reconcile the manifest with returned `agentId:` handles, every Task/Agent dispatch prompt the orchestrator builds embeds two routing markers verbatim:

```
AGENT_ROW: B3
EXPECTED_OUTPUT: analysis_access_control.md
```

`pty_exec.parse_transcript_agentids` (`scripts/pty_exec.py:384-460`) scans the session transcript and produces `{agent_row: {agent_id, expected_output, handle, description}}`, so the supervision loop can build a continuation message that names paused subagents by their manifest row (e.g. "B3 / analysis_access_control.md") instead of the opaque handle. Dispatches missing the `AGENT_ROW` marker are skipped (the parser never fabricates a row name from a handle).

---

## PTY transport

The PTY transport is what makes the worker-pool model viable: each worker is its own Claude session with its own bidirectional terminal, supervised by the driver. The architecture-level walkthrough lives in `docs/architecture.md`; the implementation contract is summarized here.

### POSIX

- Master/slave pair via `pty.openpty()`, child launched with `subprocess.Popen(..., stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, preexec_fn=_child_setup)` (`scripts/pty_exec.py:574-606`).
- `_child_setup` resets `SIGCHLD` to `SIG_DFL`, calls `os.setsid()`, and assigns the controlling terminal via `fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)`. Each child becomes its own process group so `os.killpg` can clean up.
- Driver resets `SIGCHLD` before spawn (the parent may have inherited a non-default disposition from Claude Code, which otherwise causes children to appear reaped immediately).

### Windows

- `winpty.PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(40, 120))` (`scripts/pty_exec.py:549-557`).
- `send_continuation` writes the message, waits 0.75s for the prompt box to settle, then sends a CR via `proc.sendcontrol("m")` with a `\r\n` fallback (`scripts/pty_exec.py:647-655`).

### Parent-Claude env stripping

At every spawn site, the child env is built via `_filtered_child_subprocess_environ`, which strips `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_EXECPATH`, and `AI_AGENT` (`scripts/plamen_driver.py:104-117`, mirrored in `scripts/preflight_pty_transports.py:77-83`). Without this filter, a child `claude` spawned from inside a Claude Code session detects a nested live session and exits `rc=0` without doing any work. The same filter is applied at all five spawn sites in `plamen_driver.py`.

### Preflight cache

`preflight_pty_transports.py` probes whether the host's PTY transport is functional and caches the result per-host. Cache schema version is `_SCHEMA_VERSION = 3` (`scripts/preflight_pty_transports.py:62`); v3 invalidates any pre-env-strip false-negative caches. Cache files written with an older schema are ignored.

---

## Discovery aids

Two driver-generated artifacts steer depth workers toward unbound or under-justified value flows without prescribing findings:

- **`security_obligations.md`** — feature-derived obligation ledger. When present, depth workers read it as input 5 (`prompts/shared/v2/phase4b-depth.md:161-165`) and emit per-obligation receipts in their output (`[OBLIG:security_obligations.md:<SO-ID>] STATUS:R|D|C ...`).
- **`asset_binding_matrix.md`** — value-flow binding checklist. Treated as a compact driver-generated checklist (not an expected-finding list); for each relevant row the depth worker either files a finding with file:line evidence for an unbound asset/amount/recipient/provenance pair or records why the pair is bound, unreachable, or irrelevant (`prompts/shared/v2/phase4b-depth.md:21-25`).

Both are consumed by depth workers; neither is a gate input.

---

## ID identity

Finding IDs flow through three regimes:

1. **Provisional analysis IDs** — assigned by breadth/depth/chain workers in their own output files (e.g. `[CS-1]`, `[TF-3]`, `[BLIND-2]`, `CH-2`). These are not stable and never appear in the client report.
2. **Canonical identity map** — `_write_canonical_finding_identity_map` (`scripts/plamen_driver.py:9634-9658`) refreshes a driver-owned identity sidecar after every major discovery phase (`breadth`, `rescan`, inventory chunks, `depth`, `attention_repair`, `rag_sweep`, semantic dedup variants, chain variants, `post_verify_extract`, `skeptic`, `crossbatch`, `report_index`). Source artifacts are preserved; the map is additive.
3. **Final report IDs** — the `report_index` phase reassigns to clean sequential IDs grouped by severity tier: `C-01 / H-01 / M-01 / L-01 / I-01`. Tier writers and the report assembler consume only these IDs; internal pipeline IDs are explicitly forbidden in the client-facing report (see `rules/report-template.md`).

---

## Concurrency control

A per-scratchpad file lock prevents two driver invocations from racing on the same audit:

- Lock file: `<scratchpad>/.plamen_run.lock` (`_RUN_LOCK_NAME`, `scripts/plamen_driver.py:9730`).
- Payload records PID and acquisition timestamp; held for the lifetime of the driver process.

When the user presses Esc / halts the run, the driver cancels queued workers and terminates in-flight ones with a bounded grace period:

- `_cancel_pending_worker_futures` (`scripts/plamen_driver.py:1501-1513`) cancels each pending `Future` and calls `executor.shutdown(wait=False, cancel_futures=True)` — it does **not** wait for the pool to drain.
- In-flight workers receive `SIGTERM` (POSIX `os.killpg`) or `terminate(force=False)` (Windows winpty), then `SIGKILL` / `kill` after `_HALT_TERMINATE_GRACE_S = 2.0` seconds (`scripts/plamen_driver.py:1498`). The same grace window is used at every PTY-session termination site in the driver.
