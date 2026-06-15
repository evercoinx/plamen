# Glossary

Quick reference for the Plamen-specific terms that show up in the README,
slash commands, and orchestrator rules. Read once; everything else is
explained inline where it's used.

## Pipeline structure

- **Pipeline** — the full audit. Two flavors: `sc` (smart contract) and `l1`
  (node-client infrastructure). Picked at wizard step 0.
- **Phase** — one stage of the pipeline. SC has 39, L1 has more. Examples:
  `recon`, `breadth`, `inventory`, `depth_iter1`, `verify_critical`,
  `report_assemble`. Sequence is hard-coded in
  [`docs/architecture.md`](architecture.md).
- **V1 / V2** — V1 was the legacy single-conversation LLM orchestrator. V2 is
  the current pipeline: a Python driver (`scripts/plamen_driver.py`) that runs
  each phase. For the three parallel discovery phases (breadth, depth, rescan)
  V2 spawns one Claude PTY worker per output artifact and trusts only disk
  markers (`PLAMEN_STATUS: COMPLETE`) for completion. Other phases run as a
  single phase LLM with disk-gated artifacts. V2 is resumable on crash.
- **Execution shape** — every phase runs in one of three shapes: **LLM phase
  session** (single `claude -p` / `codex exec` subprocess), **Python
  mechanical** (no LLM), or **Direct PTY worker pool** (driver supervises one
  Claude PTY per worker artifact). See `docs/pipeline-phases-presentation.md`
  for the per-phase mapping.
- **Worker pool** — the parallel execution shape used for breadth, depth, and
  rescan. The Python driver schedules N concurrent Claude PTY workers via a
  `ThreadPoolExecutor`, one per `analysis_*.md` / `depth_*_findings.md`
  artifact, retries only missing or `IN_PROGRESS` rows, and treats Claude's
  "done" text as advisory.
- **PLAMEN_STATUS marker** — HTML comment markers written into worker output
  files (e.g. `<!-- PLAMEN_STATUS: IN_PROGRESS -->`, then `... COMPLETE -->`).
  The driver's disk gate uses these to distinguish complete artifacts from
  crash-safety reservation headers. Full marker envelope is documented in
  `docs/architecture.md`.
- **Disk gate** — completion check that reads `PLAMEN_STATUS` markers from
  disk, not LLM "DONE" prose. Source of truth for worker-pool phases.
- **Compaction (informational)** — Claude auto-compacts a long session
  mid-turn. For worker phases, the driver emits a single heartbeat line and
  continues — disk markers still decide completion. Not a warning, not a
  failure.

## Agent vocabulary

- **Breadth agent** — surveys the whole codebase quickly, flags candidate
  issues. Multiple run in parallel, each covering a subset.
- **Depth agent** — verifies a single candidate by tracing code paths.
  Types: `depth-token-flow`, `depth-state-trace`, `depth-edge-case`,
  `depth-external`, plus `depth-consensus-invariant` and
  `depth-network-surface` for L1.
- **Scanner** — focused single-purpose static check (e.g. `scanner-A`,
  `scanner-B`, `scanner-C` for blind-spots).
- **Niche agent** — flag-triggered specialist (e.g.
  `callback-receiver-safety`, `signature-verification-audit`). Loads only
  when its trigger pattern is detected.
- **Skill** — reusable methodology shipped as a markdown file
  (`SKILL.md`) under `agents/skills/`. Injected into an agent prompt
  when the relevant flag fires. Three tiers exist: standard (per-language),
  injectable, and niche — see [internals.md](internals.md).
- **Injectable skill** — a protocol-type-specific skill that is **appended to
  an existing agent's prompt** (it does not spawn a new agent) when recon
  classifies the protocol as a matching type (e.g. `VAULT_ACCOUNTING` for
  vaults, `LENDING_PROTOCOL_SECURITY` for lending). Increases the depth of an
  existing agent rather than adding a budget slot. Contrast with niche agents,
  which are standalone. Full list in [internals.md](internals.md).
- **Skeptic-Judge** (a.k.a. **Skeptic-judge**) — Thorough-mode quality gate and
  built-in false-positive filter. For each HIGH/CRITICAL verified finding a
  Skeptic agent independently argues the OPPOSITE case (without seeing the
  verifier's analysis); if it disagrees, a Judge agent reads both sides and
  decides. Unresolved disagreements are tagged `UNRESOLVED` — demoted one tier
  but kept in the report body for human review, not dropped.

## Evidence

- **PoC** — proof of concept. Executable test that demonstrates the bug.
  Evidence tag `[POC-PASS]` means the test ran and the assertion held.
- **CODE-TRACE** — manual trace through code, no executable test. Lower
  confidence than POC-PASS.
- **CONTESTED** — verdict where verifier and skeptic disagree; held back
  from final report or human-review-only.
- **Provisional analysis ID** — finding IDs assigned by breadth/depth/chain
  agents (e.g. `[CS-1]`, `[TF-3]`, `CH-2`). Internal pipeline IDs only — the
  `report_index` phase later assigns the final client-facing IDs.
- **Report ID** — final client-facing finding ID assigned by `report_index`:
  `C-01` (Critical), `H-01` (High), `M-01`, `L-01`, `I-01`. These are the only
  IDs that appear in `AUDIT_REPORT.md`.
- **Canonical finding identity map** — refreshed after major discovery phases.
  Detects re-minted / collision IDs across phases. Owned by the driver
  (`_write_canonical_finding_identity_map`).
- **`.scratchpad/`** — per-audit workspace inside the target project. Holds
  all intermediate artifacts (findings, traces, manifests). Created by
  recon, deleted on `--fresh` restart, otherwise preserved for resume.
  Contains a per-scratchpad `.plamen_run.lock` that prevents concurrent
  driver invocations against the same audit.

## Models & accounts

- **MCP** — Model Context Protocol. Anthropic's protocol for plugging tools
  (Slither, Solodit, ChromaDB, etc.) into Claude Code. Codex CLI supports a
  subset; see [`docs/mcp-servers.md`](mcp-servers.md).
- **RAG** — retrieval-augmented generation. Plamen's vulnerability
  knowledge base built from Solodit + DefiHackLabs + Immunefi writeups.
  Built via `plamen rag` (~6GB RAM, 3–5 min). Optional but improves recall.
- **Pro / Max** — Anthropic Claude subscription tiers in the audit-mode
  table. Pro = ~5x weekly cap; Max = ~20x. Light mode is Pro-friendly;
  Core/Thorough generally need Max.
- **Sonnet / Opus / Haiku** — Anthropic Claude model tiers. Cheaper /
  faster / less capable in that order. Plamen picks per agent role per
  audit mode automatically.

## Operations

- **Bake (Phase 0.5)** — L1-only. Runs `scip-go` / `rust-analyzer scip` /
  Opengrep once before recon to build a code-index baseline. SC mode does
  not have this phase.
- **Recon** — first phase of every audit. Builds the design context,
  attack surface, semantic invariants. Output drives every later phase.
- **Inventory** — phase that lists every entry point, state variable, and
  external interaction for downstream agents to consume.
- **Validation Sweep** — one of the depth iteration-1 workers. Produces
  `scanner_validation_findings.md` / `validation_sweep_findings.md`. Not a
  separate late-pipeline phase.
- **`plamen_home()`** — Python abstraction in `scripts/plamen_types.py`. At
  runtime it resolves to the active backend's integration root (`~/.claude/`
  for Claude Code, `~/.codex/plamen/` for Codex). The canonical repository is
  always `~/.plamen` — the backend roots are install-created symlinks.
- **PTY transport** — how the driver runs Claude PTY workers. On POSIX:
  `pty.openpty()` + `subprocess.Popen` with a `preexec_fn` that calls
  `os.setsid()`, claims the controlling TTY via `TIOCSCTTY`, and resets
  inherited SIGCHLD. On Windows: `winpty.PtyProcess.spawn` via `pywinpty`.
  Lets `/plamen` launch from inside a Claude Code session without parent
  process state poisoning the children.
- **Discovery aids** — feature-derived analysis prompts consumed by depth
  workers: `security_obligations.md` (obligation ledger from recon) and
  `asset_binding_matrix.md` (value-flow binding checklist). Protocol-agnostic
  — generalize across DEX, vault, lending, bridge, L1 client, etc.

## Resilience & recovery

- **PTY-supervised execution** — the v2.1.0 way the driver runs workers: each
  worker (Claude, or one `codex exec` per depth job) is driven through a
  **pseudo-terminal (PTY)** and its turn completion is inferred from artifacts
  written to disk (the `PLAMEN_STATUS: COMPLETE` marker), not from a
  stdout/JSON envelope. This eliminates the 0-byte-stdio "silent hang"
  ambiguity from earlier versions. POSIX uses `pty.openpty()` + `Popen`;
  Windows uses `winpty` via `pywinpty`. See the **PTY transport** entry above
  and [architecture.md](architecture.md) for the implementation contract.
- **Haltless** — a design property of the v2.1.0 driver: a finished audit is
  never thrown away at the finish line. Late-stage phases (`report_index`,
  verify, inventory, resume) **repair-then-degrade** rather than stopping the
  run, and stale/corrupt checkpoints recover instead of stranding the audit.
- **Repair-then-degrade** — the haltless recovery policy: when a late phase
  cannot fully complete, the driver first **repairs** what it can
  deterministically (mechanical report-index recovery, verify backfill, queue
  manifests), and if work still remains it **degrades** — finishing the run and
  surfacing the unfinished obligation as a flagged item rather than halting.
- **Appendix-B flagged items** — the "human-review" obligations that
  repair-then-degrade could not fully resolve. They are folded into a delivered
  **Appendix B** of `AUDIT_REPORT.md` (`_build_human_review_appendix` in
  `scripts/plamen_mechanical.py`) so the flag actually reaches the reader,
  instead of being buried in an intermediate file the client never sees.
- **Degraded phase** — a phase that failed (or was skipped) and was marked
  `degraded` in the checkpoint so the pipeline could continue; downstream
  phases handle its missing optional artifacts gracefully. A degraded sentinel
  is cleared on a genuine resume.
