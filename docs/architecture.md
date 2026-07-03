# Architecture

> Plamen v2.2.0. The default analysis model is Opus 4.8 (`claude-opus-4-8`),
> resolved via `PLAMEN_OPUS_MODEL` / `PLAMEN_THOROUGH_OPUS_MODEL` in
> `scripts/plamen_types.py`. Runs on Windows, macOS, and Linux.

## Pipeline Overview

> Python driver → worker pool → one Claude PTY session per artifact → disk artifact gate → retry only missing/bad rows. Claude saying "done" is not trusted; disk markers are.

```
   +-----------------------------------------------------------+
   |  plamen_driver.py  (outer phase loop, resumable)          |
   |  - reads config.json, walks Phase[] in order              |
   |  - per-phase: build prompt -> spawn -> gate -> checkpoint |
   |  - on failure: retry only the bad rows -> degrade -> halt |
   +-----------------------------------------------------------+
           |                       |                        |
           v                       v                        v
   +-----------------+   +-------------------+   +-------------------+
   |  Worker-pool    |   |  LLM phase        |   |  Python           |
   |  phase          |   |  session          |   |  mechanical       |
   |  (breadth,      |   |  (recon, instan-  |   |  (inventory_      |
   |   depth, rescan)|   |   tiate, inventory|   |   prepare,        |
   |                 |   |   chunks, invari- |   |   report_assemble)|
   |  Driver spawns  |   |   ants, dedup,    |   |                   |
   |  N PTY workers  |   |   chain, verify,  |   |  Driver runs      |
   |  via Thread-    |   |   skeptic, report)|   |  Python directly. |
   |  PoolExecutor.  |   |                   |   |  No LLM call. No  |
   |  One Claude PTY |   |  ONE `claude -p`  |   |  PTY. Result is   |
   |  per artifact.  |   |  (or `codex exec`)|   |  written to disk  |
   |                 |   |  subprocess for   |   |  and gated like   |
   |  Each worker is |   |  the entire phase.|   |  any LLM phase.   |
   |  bound to one   |   |  Prompt includes  |   |                   |
   |  manifest row   |   |  manifest + ID    |   +-------------------+
   |  and one output |   |  validation rules.|             |
   |  file.          |   |                   |             |
   +--------+--------+   +---------+---------+             |
            |                      |                       |
            v                      v                       v
   +-----------------------------------------------------------+
   |  Disk artifact gate (gate_passes)                         |
   |  - file exists                                            |
   |  - last PLAMEN_STATUS marker is COMPLETE                  |
   |  - no shard-marker leakage / wrong-phase mismatch         |
   |  - role-specific content validators pass                  |
   +-----------------------------------------------------------+
                           |
              PASS         |        FAIL
           +---------------+--------------+
           v                              v
   pipeline_checkpoint.md          repair scope built from disk
   advances to next phase          driver retries ONLY missing
                                   /bad rows (not the whole phase)
```

The workflow is fully autonomous -- provide a project tree and optional documentation. The V2 deterministic driver (`scripts/plamen_driver.py`) executes each phase in one of the three shapes above, detects the language, loads the appropriate prompt branch, and handles everything from pattern detection to PoC verification to report assembly.

Pipeline phases (semantic order, regardless of execution shape):

```
Phase 1 RECON -> Phase 2 INSTANTIATE -> Phase 3 BREADTH -> Phase 3b RESCAN
  -> Phase 3c PER-CONTRACT -> Phase 4a INVENTORY -> Phase 4a.5 SEMANTIC
  -> Phase 4b DEPTH LOOP -> Phase 4b.6 EXPLORATION SKEPTIC (Thorough)
  -> Phase 4c CHAIN -> Phase 5 VERIFY -> Phase 5.1 SKEPTIC-JUDGE
  -> Phase 6 REPORT -> Phase 6b REPORT_DEDUP (Python) -> AUDIT_REPORT.md
```

---

## Canonical Layout

`~/.plamen/` is the canonical Git checkout. `~/.claude/` (Claude Code) and `~/.codex/plamen/` (Codex) are install-created runtime integration symlinks pointing at it. The driver resolves the right one at runtime via `plamen_home()` in `scripts/plamen_types.py:87`, so prompts can use `~/.claude/...` paths even when running under Codex.

---

## Phase Details

### Phase 1: Reconnaissance (4 parallel agents)

Split into 4 agents to prevent timeout:
- **Agent 1A (sonnet)**: RAG queries -- unified-vuln-db, Solodit live search
- **Agent 1B (opus)**: Documentation parsing, fork ancestry research, trust model extraction
- **Agent 2 (sonnet)**: Build environment, static analysis (Slither -> Farofino/Aderyn -> grep fallback), test suite
- **Agent 3 (opus)**: Pattern detection, attack surface mapping, template recommendations with BINDING MANIFEST

Produces 17+ scratchpad artifacts consumed by all downstream phases.

### Phase 2: Instantiation (orchestrator)

Reads the BINDING MANIFEST, resolves skill templates, applies merge hierarchy (max 3 skills/agent), and composes agent prompts with instantiated parameters.

### Phase 3: Parallel Breadth Analysis (5-9 workers)

Worker-pool phase. The driver builds a manifest row per agent, then `_run_breadth_worker_pool_pty` (`scripts/plamen_driver.py:4636`) spawns one Claude PTY worker per open row through a `ThreadPoolExecutor`. Each worker runs a targeted sweep over its scope and writes one findings file. Retries only target rows that fail the disk gate.

### Phase 3b/3c: Re-Scan + Per-Contract (Thorough only)

- **Re-scan** (worker pool, `scripts/plamen_driver.py:5155`): 2-3 sonnet workers re-analyze with an exclusion list of known findings. Counters LLM attention saturation.
- **Per-contract**: 1 worker per contract/cluster at maximum depth. Zero distraction from other contracts.

### Phase 4a: Inventory + Side Effect Trace

Consolidates all findings, promotes static analysis results, performs side effect trace audit on external token interactions.

### Phase 4a.5: Semantic Invariant Pre-Computation

Sonnet agent enumerates write sites, defines semantic invariants, detects mirror variables, flags conditional writes and accumulation exposures. Pass 2 (Thorough) traces consequences recursively.

### Phase 4b: Adaptive Depth Loop (8+ workers x 1-3 iterations)

Worker-pool phase (`_run_depth_worker_pool_pty`, `scripts/plamen_driver.py:6183`). Iteration 1 (always): 4 depth workers + 3 blind spot scanners + validation sweep + niche workers, all PTY-supervised in parallel.

| Depth Agent | Model | Focus |
|-------------|-------|-------|
| depth-token-flow | opus | Balance invariants, mint/burn, transfer side effects |
| depth-state-trace | opus | Cross-function state mutation, constraint enforcement |
| depth-edge-case | sonnet | Boundary values, zero state, overflow, first-user |
| depth-external | sonnet | External call effects, oracle integrity, cross-chain timing |

| Scanner | Focus |
|---------|-------|
| Blind Spot A | External token coverage, parameter governance, msg.value loops, returnbomb |
| Blind Spot B | Guards, visibility, inheritance, override safety |
| Blind Spot C | Role lifecycle, capability exposure, reachability |
| Validation Sweep | Write completeness, struct validation, sibling propagation |

**Niche agents** (flag-triggered, 1 budget slot each):
- EVENT_COMPLETENESS, SEMANTIC_GAP_INVESTIGATOR, SPEC_COMPLIANCE_AUDIT, SIGNATURE_VERIFICATION_AUDIT, SEMANTIC_CONSISTENCY_AUDIT, MULTI_STEP_OPERATION_SAFETY, CALLBACK_RECEIVER_SAFETY (EVM), DIMENSIONAL_ANALYSIS (EVM)

**Invariant fuzzing** (EVM Thorough only):
- Foundry invariant fuzz campaign (protocol-derived invariants, 256 runs x depth 25)
- Medusa stateful fuzz campaign (parallel, standalone harness, 15-min timeout)

**Iterations 2-3** (Thorough): Devil's Advocate workers with structural adversarial role, contrastive path summaries, fresh MCP calls.

**Confidence scoring** (sonnet, batched): 4-axis model (Evidence x 0.25 + Consensus x 0.25 + Analysis Quality x 0.3 + RAG Match x 0.2).

### Phase 4b.6: Exploration-Completeness Skeptic (Thorough only)

Recall-positive additive soft phase that audits whether the depth loop left
unexplored paths. Validator-soft: it never halts (writes a sentinel + warning
on missing output) and only adds coverage, never removes findings. Wired at
`scripts/plamen_driver.py:13178` (`_validate_exploration_skeptic`).

### Phase 4c: Chain Analysis (2 sequential agents)

- **Agent 1**: Exhaustive enabler enumeration (5 actor categories per dangerous state), finding grouping
- **Agent 2**: Postcondition-to-precondition chain matching, composition coverage map, RAG validation

### Phase 5: Verification (parallel verifiers)

Mandatory PoC execution with evidence tags: `[POC-PASS]`, `[POC-FAIL]`, `[CODE-TRACE]`, `[MEDUSA-PASS]`.

### Phase 5.1: Skeptic-Judge (Thorough, HIGH/CRIT only)

Skeptic (sonnet) with INVERSION MANDATE, then Judge (haiku) resolves disagreements.

### Phase 6: Report Generation

Index Agent (haiku, LLM phase) -> 3 Tier Writers (opus/sonnet, LLM phase) -> Assembler (Python mechanical, no LLM) -> `AUDIT_REPORT.md`.

The Index Agent's report_index is built deterministically where it used to rely on prose parsing — mechanical SC report_index recovery repairs missing/malformed rows in Python rather than re-running the LLM or halting (see *Haltless Resilience*).

### Phase 6b: report_dedup (Python mechanical)

A durable post-report deduplication phase (`report_dedup`, dispatched at
`scripts/plamen_driver.py:16961`, `critical=False`/non-fatal). It runs a
data-loss-free dedup over the full candidate set with a precision-guarded
cross-tier same-fix catch — recall-positive, and a non-fatal failure never
blocks delivery of `AUDIT_REPORT.md`.

---

## Driver Architecture

`scripts/plamen_driver.py` is a Python outer loop. Invoked via `/plamen-wizard` (Claude Code), the `plamen` terminal wrapper (both backends), or directly:

```
plamen_driver.py
  outer phase loop
    for phase in Phase[]:
      1. build phase-specific prompt (strip forward refs, sanitize foreign sections)
      2. dispatch by execution shape:
         - worker-pool phase  -> spawn N PTY workers, one per manifest row
         - LLM phase session  -> spawn one `claude -p` / `codex exec` subprocess
         - Python mechanical  -> run driver code directly
      3. gate_passes(scratchpad, project_root, phase)  // disk-only check
      4. if PASS: write checkpoint, advance
         if FAIL: build repair scope from disk, retry ONLY bad rows
                  -> if still failing: degrade -> halt
  AUDIT_REPORT.md  // assembled Python-native (see Phase 6)
```

Worker-pool phases run an additional inner loop per `pty_continuation_budget` attempt (`scripts/plamen_driver.py:4677`):

```
for pool_attempt in range(1, budget + 2):
    open_jobs = jobs whose output file does not yet end with PLAMEN_STATUS: COMPLETE
    if no open_jobs and gate_passes: return 0
    ThreadPoolExecutor(max_workers=concurrency) over open_jobs
      -> _run_single_breadth_worker_pty (one Claude PTY per row)
```

Disk-gate completion contract. The driver only counts a worker complete when:
1. The expected output file exists.
2. The LAST `PLAMEN_STATUS:` marker line in the file is `COMPLETE`.
3. Shard-internal markers (e.g. `OPENGREP_SHARD_*`) have NOT leaked into the artifact.
4. The role-specific content validator passes (e.g. for breadth: findings or an explicit "no findings" rationale; for verify: an evidence tag).

Claude saying "done" in the PTY transcript is not trusted for worker phases. The reader thread captures the `DONE:` line for observability but the only completion authority is the disk gate. This closes the premature-DONE failure class observed when Claude Code auto-compacts a worker turn (see *Compaction-as-informational* below).

Key properties:
- **Resumable**: re-run `python3 ~/.claude/scripts/plamen_driver.py {project}/.scratchpad/config.json` to resume from the last checkpoint. Stale or corrupt checkpoints recover rather than stranding the run.
- **Phase-isolated**: each subprocess sees only its own prompt section; forward refs and foreign subsections are stripped before dispatch.
- **Backend-agnostic**: Claude Code (`claude -p`) and Codex CLI (`codex exec`) share the same phase contracts.
- **Deterministic gating**: artifact existence and marker hygiene are checked mechanically. The LLM never self-reports completion to the driver's state machine.
- **Ecosystem auto-detect**: the language/ecosystem is detected and auto-corrected at startup (no halt-to-rerun), shown on the startup banner, and resolved via manifest-priority rules (`config.language` reconciliation around `scripts/plamen_driver.py:14838`). A suffix-only signal never clobbers an explicit config; high-confidence cases such as Pinocchio and native-SDK Solana are corrected automatically, while ambiguous cases stay conservative (recall-safe — a wrong auto-correct is worse than the status quo).
- **Haltless at the finish line**: late-stage gates degrade-with-flag instead of halting (see *Haltless Resilience* below).

---

## Haltless Resilience

A finished audit is never thrown away at the finish line. Late-stage phases
(report_index, verify, inventory, and the resume paths) **repair-then-degrade**
rather than halting: where v2.0.x would stop the run, v2.1.0 completes the run
and surfaces any unfinished obligations as flagged items so a human can triage
them.

- **Degrade-with-flag → delivered Appendix B.** Gates that previously wrote
  "for human review" notes into intermediate `report_semantic_*.md` files (where
  the flag never actually reached the reader) now fold those items into a
  delivered Appendix B of `AUDIT_REPORT.md` via
  `_build_human_review_appendix` (`scripts/plamen_mechanical.py:1970`). The
  human-review flag reaches the human.
- **Mechanical report_index / verify recovery.** Missing or malformed
  report_index rows and verify files are repaired deterministically in Python
  (mechanical SC report_index recovery, verify backfill, and queue-completeness
  manifests) instead of triggering an LLM re-run or a halt. The verify
  queue-completeness backfill also stops the resume-rewind loop.
- **Unified retry/recovery.** Retry and recovery are consistent across
  backend × mode × pipeline: a hinted 3rd retry, rescan added to the set of
  recovering phases, and stale/corrupt-checkpoint recovery.

---

## PTY Transport

Worker-pool phases (and LLM phase sessions that benefit from interactive supervision) run each `claude` invocation through a controlling PTY rather than `subprocess.PIPE`. The transport split lives in `scripts/pty_exec.py:548-720`.

### POSIX (Linux, macOS)

`pty.openpty()` + `subprocess.Popen(stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, preexec_fn=_child_setup)`. The child setup:
- Calls `os.setsid()` to detach from the parent's session.
- Calls `fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)` so the slave becomes the controlling terminal.
- Resets `SIGCHLD` to `SIG_DFL`.

Reason: launching `/plamen` from inside Claude Code on macOS/Linux meant the Python driver inherited the parent session's SIGCHLD disposition, and a raw POSIX fork + manual child wait was fragile -- children could appear reaped immediately. The driver also resets SIGCHLD in the parent before spawn (`scripts/pty_exec.py:569`) to belt-and-suspenders the fix.

### Windows

`winpty.PtyProcess.spawn(..., dimensions=(40, 120))` via `pywinpty>=2.0.14`, gated by `platform_system=="Windows"` in `requirements.txt`. Unchanged across the v2.x ship line.

### Parent-Claude env stripping

`_PARENT_CLAUDE_IDENTITY_ENV_KEYS` (`scripts/plamen_driver.py:95`) removes `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_EXECPATH`, and `AI_AGENT` from any subprocess environment before spawn. Without this, a `/plamen` invocation from inside a Claude Code session would poison the child `claude -p`: Claude detects the nested active session ID and exits `rc=0` without doing any phase work. The same key set is mirrored verbatim in `scripts/preflight_pty_transports.py:77` for the probe path. Helper applied at 4 production spawn sites (2 in `plamen_driver.py`, 2 in `preflight_pty_transports.py`) plus regression tests.

### Subprocess isolation overlay

```
SUBPROCESS_ISOLATION_PAYLOAD = '{"enabledPlugins":{},"hooks":{},"mcpServers":{}}'
```

Defined in `scripts/pty_exec.py:27` and passed to every spawned `claude` via `--settings` + `--strict-mcp-config`. Empty `enabledPlugins`/`hooks`/`mcpServers` disables plugin and hook cold-start work for the child subprocess (e.g. `rust-analyzer-lsp` plugin sync, hook-driven network calls) without modifying the user's real `settings.json` -- so OAuth keychain auth keeps working. Load-bearing for users whose host Claude Code has heavy plugin or MCP integrations.

### Preflight cache schema v3

`scripts/preflight_pty_transports.py:62` sets `_SCHEMA_VERSION = 3`. v3 invalidates every preflight cache written before the env-stripping fix (any false-negative probe taken from inside `/plamen` against an unstripped child). A cache file written under an older schema is ignored and the probe re-runs.

### Worker artifact contract

Every worker output file begins with a canonical marker envelope (this is the master copy -- other docs may reference but should not redefine it):

```
<!-- PLAMEN_ARTIFACT: <expected_file>.md -->
<!-- PLAMEN_OWNER: <agent_id> -->
<!-- PLAMEN_STATUS: IN_PROGRESS|COMPLETE -->
<!-- PLAMEN_PHASE: breadth|depth|rescan -->
<!-- PLAMEN_VERSION: 1 -->
<!-- AGENT_ROW: <agent_id> -->
<!-- EXPECTED_OUTPUT: <expected_file>.md -->
```

The contract is set up in `prompts/shared/v2/phase3-breadth.md:119-129` (breadth) with parallel definitions for depth and rescan. `AGENT_ROW` and `EXPECTED_OUTPUT` exist so the driver's continuation loop can map a subagent handle back to its manifest row on retry.

The disk gate yields one of 4 verdicts per output file:
1. **complete** -- file exists and its LAST `PLAMEN_STATUS:` marker is `COMPLETE` and content validates.
2. **in-progress** -- file exists but the last marker is `IN_PROGRESS`; worker did not finish. The driver retries this row.
3. **wrong-phase / wrong-filename** -- marker mismatch (e.g. `PLAMEN_PHASE: breadth` in a depth file, or `PLAMEN_ARTIFACT` value differs from the file path). Treated as bad and retried with a delta hint.
4. **stale-legacy** -- file present but contains no `PLAMEN_*` markers (pre-PTY-supervision artifact). Handled by a separate legacy-acceptance path so we do not force-retry an entire scratchpad from an older driver version.

### Compaction-as-informational

Claude context compaction during a worker turn is informational, not a failure -- the driver continues under disk-gate validation. If the artifact passes the disk gate, the worker is complete regardless of compaction notice. When `state.complete` is reported and `transcript_shows_compaction(...)` returns True for the captured transcript (`scripts/pty_exec.py:_COMPACTION_MARKERS`, `scripts/plamen_driver.py:3869-3880`), the driver emits a single heartbeat line ("coordinator emitted DONE after Claude context compaction, but disk gate rejected completion; continuing missing rows. This is not a phase failure.") rather than a warning, and proceeds to repair-scope continuation. This replaces the prior class of false-halt where a compacted-DONE was mistaken for premature completion.

### Discovery aids consumed by depth workers

Two driver-generated artifacts feed each depth worker beyond the standard inventory/findings inputs (`prompts/shared/v2/phase4b-depth.md:21,165,277`):

- `security_obligations.md` -- feature-derived obligation ledger. Workers consult it and emit `[OBLIG:security_obligations.md:<SO-ID>] STATUS:R|D|C` lines so the driver can mechanically reconcile coverage.
- `asset_binding_matrix.md` -- compact value-flow binding checklist (asset / amount / recipient / provenance tuples). Not an expected-finding list: for each row in scope, the worker either produces a finding with file:line evidence for an unbound pair, or records why the pair is bound, unreachable, or irrelevant.

---

## L1 Pipeline Differences

When running in L1 mode (`/plamen-l1-wizard` in Claude Code, or `plamen l1` from the terminal), the pipeline adjusts:

| SC Pipeline | L1 Pipeline | Reason |
|-------------|-------------|--------|
| Phase 4c: Chain Analysis | **Removed** | L1 bugs are point vulnerabilities; enabler enumeration doesn't apply |
| depth-token-flow | **Not loaded** | No in-scope DeFi token flow in node clients |
| -- | **Phase 0.5: Bake** | Batch-indexes repo before depth with deterministic SCIP: `_bake_go_scip` (scip-go) for Go node clients, `_bake_rust_scip` (rust-analyzer scip) for Rust. Generates the graph artifacts depth agents expect. |
| -- | **depth-consensus-invariant** | Consensus safety/liveness, non-determinism, Byzantine scenarios |
| -- | **depth-network-surface** | p2p/RPC/mempool attack surfaces, DoS vectors, eclipse checks |
| SC severity matrix | **L1 severity matrix** | Aligned with Immunefi v2.3, stricter for Critical impact |
| Evidence: [POC-PASS] etc. | + [DIFF-PASS], [CONFORMANCE-PASS], [NON-DET-PASS], [FUZZ-PASS], [LSP-TRACE] | L1-specific verification methods |

See [l1-mode/design.md](l1-mode/design.md) for the complete L1 architecture.

---

## Codex Backend (cost-saving, BETA)

The driver supports OpenAI Codex CLI (`codex exec`) as an alternative backend,
added in v2.1.0 as a cost-saving option (still beta):

- Prompts are rewritten by `scripts/codex_adapter.py`: `~/.claude/...` paths become `~/.codex/plamen/...` equivalents, `Task(subagent_type=...)` becomes `spawn_agent(...)`, bash snippets become PowerShell where required. This is prompt-text rewriting, not MCP transport translation.
- MCP is supported natively by Codex via `[mcp_servers.*]` TOML stanzas in `~/.codex/config.toml`, which `codex_adapter.py` generates from the Claude Code MCP config so unified-vuln-db and Solodit RAG tools work in both backends.
- Sandbox constraints are adapted for Codex's execution model.
- `~/.codex/plamen/` is symlinked to `~/.plamen/` (the canonical Git checkout), exactly the same way `~/.claude/` is. The Codex tree carries `AGENTS.md` (orchestrator) and `config.toml` (settings), replacing Claude Code's `CLAUDE.md`, `settings.json`, and `mcp.json`.
- Install: `plamen install --codex`. The driver auto-detects the active backend and resolves the correct symlink via `plamen_home()` (`scripts/plamen_types.py:87`).

v2.1.0 Codex-specific hardening:
- **Per-job depth fan-out**: the depth phase runs one `codex exec` per depth job rather than a single subprocess, fixing the never-cut-stub halt from single-subprocess under-fan-out.
- **Natural-language usage-cap detection**: Codex/ChatGPT usage-cap errors arrive as prose, not structured codes; the driver detects them and auto-waits (preserving state) instead of treating them as a phase failure that retries into a halt (`scripts/plamen_driver.py:494`).
- **Full first-pass artifact seeding**: Codex depth seeds the complete mandatory first-pass artifact set (`_codex_depth_artifact_checklist` / `_codex_widen_depth_output_contract`) so recon/depth stop degrading lossily, and runs a real Devil's-Advocate iteration 2.
- **Context-exceeded no longer perma-fails**: a context-exceeded condition is recoverable rather than fatal.

For the full list of Codex BETA limitations, see [codex-backend.md](codex-backend.md).

---

**See also**: [getting-started.md](getting-started.md) · [pipeline-phases-presentation.md](pipeline-phases-presentation.md) · [internals.md](internals.md) · [repository-structure.md](repository-structure.md) · [usage.md](usage.md) · [docs index](README.md)
