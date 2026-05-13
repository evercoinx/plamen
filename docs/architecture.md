# Architecture

## Pipeline Overview

```
                          +-----------------------------------+
                          |  ORCHESTRATOR (CLAUDE.md/AGENTS.md)|
                          |  Detects language, reads phase     |
                          |  prompts, spawns agents,           |
                          |  enforces gates                    |
                          +----------------+------------------+
                                           |
          +----------------------------+---+----------------------------+
          v                            v                                v
   +--------------+          +--------------+          +--------------+
   |  Phase 1     |          |  Phase 2     |          |  Phase 3     |
   |  RECON       |--------> |  INSTANTIATE |--------> |  BREADTH     |
   |  (4 agents)  |          |  (orchestr.) |          |  (5-9 agents)|
   +--------------+          +--------------+          +------+-------+
                                                              |
          +---------------------------------------------------+
          v
   +--------------+     +--------------+     +-----------------+
   |  Phase 3b    |     |  Phase 3c    |     |  Phase 4a       |
   |  RE-SCAN     |---> |  PER-CONTRACT|---> |  INVENTORY      |
   |  (sonnet,    |     |  (sonnet,    |     |  + Side Effect  |
   |   2 iters)   |     |   1/cluster) |     |  Trace Audit    |
   +--------------+     +--------------+     +------+----------+
                                                    |
          +-----------------------------------------+
          v
   +--------------+     +-----------------+     +--------------+
   |  Phase 4a.5  |     |  Phase 4b       |     |  Phase 4c    |
   |  SEMANTIC    |---> |  DEPTH LOOP     |---> |  CHAIN       |
   |  INVARIANTS  |     |  (8+ agents x   |     |  ANALYSIS    |
   |  (sonnet)    |     |   1-3 iters)    |     |  + Enablers  |
   +--------------+     |  + Niche agents |     +------+-------+
                        |  + Inv. Fuzz    |            |
                        |  + Medusa Fuzz  |            |
                        |  + Design Stress|            |
                        +-----------------+            |
                                                       |
          +--------------------------------------------+
          v
   +--------------+     +--------------+     +------------------+
   |  Phase 5     |     |  Phase 5.1   |     |  Phase 6         |
   |  VERIFY      |---> |  SKEPTIC-    |---> |  REPORT          |
   |  (N verifier |     |  JUDGE       |     |  Index -> 3 Tier |
   |   agents)    |     |  (Thorough)  |     |  Writers ->      |
   +--------------+     +--------------+     |  Assembler       |
                                              +------------------+
                                                      |
                                                      v
                                               AUDIT_REPORT.md
```

The workflow is fully autonomous -- provide a smart contract project and optionally documentation. The orchestrator detects the language, loads the appropriate branch, and handles everything from pattern detection to PoC verification to final report assembly.

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

### Phase 3: Parallel Breadth Analysis (5-9 agents)

All agents spawned in a single message. Each runs a targeted sweep per vulnerability class across its scope, producing findings with precondition/postcondition analysis.

### Phase 3b/3c: Re-Scan + Per-Contract (Thorough only)

- **Re-scan**: 2-3 sonnet agents re-analyze with an exclusion list of known findings. Counters LLM attention saturation.
- **Per-contract**: 1 agent per contract/cluster at maximum depth. Zero distraction from other contracts.

### Phase 4a: Inventory + Side Effect Trace

Consolidates all findings, promotes static analysis results, performs side effect trace audit on external token interactions.

### Phase 4a.5: Semantic Invariant Pre-Computation

Sonnet agent enumerates write sites, defines semantic invariants, detects mirror variables, flags conditional writes and accumulation exposures. Pass 2 (Thorough) traces consequences recursively.

### Phase 4b: Adaptive Depth Loop (8+ agents x 1-3 iterations)

**Iteration 1** (always): 4 depth agents + 3 blind spot scanners + validation sweep + niche agents, all in parallel.

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

**Iterations 2-3** (Thorough): Devil's Advocate agents with structural adversarial role, contrastive path summaries, fresh MCP calls.

**Confidence scoring** (haiku, batched): 4-axis model (Evidence x 0.25 + Consensus x 0.25 + Analysis Quality x 0.3 + RAG Match x 0.2).

### Phase 4c: Chain Analysis (2 sequential agents)

- **Agent 1**: Exhaustive enabler enumeration (5 actor categories per dangerous state), finding grouping
- **Agent 2**: Postcondition-to-precondition chain matching, composition coverage map, RAG validation

### Phase 5: Verification (parallel verifiers)

Mandatory PoC execution with evidence tags: `[POC-PASS]`, `[POC-FAIL]`, `[CODE-TRACE]`, `[MEDUSA-PASS]`.

### Phase 5.1: Skeptic-Judge (Thorough, HIGH/CRIT only)

Skeptic (sonnet) with INVERSION MANDATE, then Judge (haiku) resolves disagreements.

### Phase 6: Report Generation (5 agents)

Index Agent (haiku) -> 3 Tier Writers (opus/sonnet) -> Assembler (haiku/sonnet) -> `AUDIT_REPORT.md`.

---

## V2 Driver Architecture

The V2 pipeline (`/plamen-wizard`) wraps the same phase logic in a Python outer loop:

```
plamen_driver.py
  ├── Reads config.json (mode, scope, backend)
  ├── For each phase:
  │     ├── Builds phase-specific prompt (strips forward refs)
  │     ├── Launches `claude -p` (or `codex exec`) subprocess
  │     ├── Waits for completion, checks artifact gates
  │     ├── Writes checkpoint to pipeline_checkpoint.md
  │     └── On failure: retry with hint → degrade → halt
  └── Assembles AUDIT_REPORT.md (Python-native)
```

Key properties:
- **Resumable**: Re-run the driver command to resume from last checkpoint
- **Phase-isolated**: Each subprocess sees only its own prompt section
- **Backend-agnostic**: Supports Claude Code (`claude -p`) and Codex CLI (`codex exec`)
- **Deterministic gating**: Artifact existence checked mechanically, not by LLM

---

## L1 Pipeline Differences

When running in L1 mode (`/plamen l1` in Claude Code, `$plamen l1` in Codex, or `plamen l1` from the terminal), the pipeline adjusts:

| SC Pipeline | L1 Pipeline | Reason |
|-------------|-------------|--------|
| Phase 4c: Chain Analysis | **Removed** | L1 bugs are point vulnerabilities; enabler enumeration doesn't apply |
| depth-token-flow | **Not loaded** | No in-scope DeFi token flow in node clients |
| -- | **Phase 0.5: Bake** | Batch-indexes repo with scip-go / rust-analyzer SCIP before depth |
| -- | **depth-consensus-invariant** | Consensus safety/liveness, non-determinism, Byzantine scenarios |
| -- | **depth-network-surface** | p2p/RPC/mempool attack surfaces, DoS vectors, eclipse checks |
| SC severity matrix | **L1 severity matrix** | Aligned with Immunefi v2.3, stricter for Critical impact |
| Evidence: [POC-PASS] etc. | + [DIFF-PASS], [CONFORMANCE-PASS], [NON-DET-PASS], [FUZZ-PASS], [LSP-TRACE] | L1-specific verification methods |

See [l1-mode/design.md](l1-mode/design.md) for the complete L1 architecture.

---

## Codex Backend

The V2 driver supports OpenAI Codex CLI as an alternative backend:

- Prompts are rewritten: `~/.claude/` paths become `~/.codex/plamen/` equivalents
- Tool calls are translated to Codex equivalents (via `codex_adapter.py`)
- Sandbox constraints are adapted for Codex's execution model
- Codex config lives at `~/.codex/plamen/` (symlinked from `~/.plamen/codex/`): `AGENTS.md` (orchestrator) and `config.toml` (settings), replacing Claude Code's `CLAUDE.md`, `settings.json`, and `mcp.json`
- Install: `plamen install --codex`. The V2 driver auto-detects the active backend via `plamen_home()`
