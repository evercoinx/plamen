# Phase 1 Week 1 — Progress Log

> **Session date**: 2026-04-10
> **Branch**: `l1-experimental` on `PlamenTSV/plamen-l1-experimental`
> **Status**: Primitives + skills scaffolded. User-machine steps pending.

This document captures what was built in the current session vs what's pending user machine action (toolchain install, repo cloning, actual benchmark runs). It maps to Week 1 of the design v0.3 milestone plan (`docs/l1-mode/design.md` Section 10).

## Completed in-session (no user action required)

### Depth agent roles
- [x] `agents/depth-consensus-invariant.md` — new depth agent for consensus invariant analysis, non-determinism sweeps, Byzantine-scenario reasoning, cross-client differential. Matches existing Plamen depth-*.md format with YAML frontmatter.
- [x] `agents/depth-network-surface.md` — new depth agent for p2p / RPC / mempool attack surface deep analysis, pre-auth panic sweeps, asymmetric cost analysis, eclipse-vector check.

### SCIP reader primitive shim
- [x] `plamen_l1/__init__.py` — package init.
- [x] `plamen_l1/scip_reader.py` — ~200 LOC Python module implementing `ScipReader` class with `find_definition`, `find_references`, `list_symbols_in_file`, `workspace_symbol`, `stats` query methods. Reads the SCIP protobuf format produced by `scip-go` and `rust-analyzer scip`. Cross-platform (Windows/macOS/Linux), no WSL2 required. CLI entry point for shell sanity checks. **Prerequisite**: user must run `protoc` once to generate `plamen_l1/scip_pb2.py` from the sourcegraph/scip protobuf schema (instructions in the module docstring).

### Skill index registration
- [x] `rules/skill-index.md` — added "L1 Skills" section registering all 15 L1 skills with trigger patterns and consuming depth agents. Documents the new depth agent roles, Phase 4c removal, new Phase 5 evidence tags, and Phase 0.5 Bake phase. Mirrors the existing EVM/Solana/Aptos/Sui format.

### L1 prompt subtree (foundational files)
- [x] `prompts/l1/phase1-recon-prompt.md` — threat-model-first recon with 3-agent split (L1-1 threat model + fork ancestry, L1-2 subsystem map + attack surface, L1-3 bake validation + opengrep sweep). Includes Phase 0.5 Bake prerequisite documentation. ~320 lines.
- [x] `prompts/l1/phase5-verification-prompt.md` — L1-specific verification protocol with new evidence tags (`[DIFF-PASS]`, `[CONFORMANCE-PASS]`, `[NON-DET-PASS]`, `[FUZZ-PASS]`, `[LSP-TRACE]`) and hypothesis-type routing. Defines `INFEASIBLE` as a new verdict for findings with no mechanical verification path.

### Opengrep rule pack starter
- [x] `agents/skills/injectable/l1/_opengrep-rules/go-integer-underflow-p2p.yaml` — Geth CVE-2024-32972 pattern (`count - 1` underflow in p2p handler).
- [x] `agents/skills/injectable/l1/_opengrep-rules/go-panic-in-endblocker.yaml` — Cosmos ASA-2025-003 / ISA-2025-002 class (panic / unchecked div / unchecked index in BeginBlock/EndBlock).
- [x] `agents/skills/injectable/l1/_opengrep-rules/rust-unwrap-in-preauth.yaml` — NEAR Ping of Death pattern (`.unwrap()` / `.expect()` / `panic!()` in handshake / verify / signature paths).
- [x] `agents/skills/injectable/l1/_opengrep-rules/README.md` — rule pack overview, expansion plan for Week 3 (15-25 rules target).

### Benchmark corpus documentation
- [x] `benchmarks/l1/README.md` — 5 Phase 1 targets with clone/checkout/build/SCIP-index commands, expected skill catches, build feasibility notes, ground-truth template structure, Phase 2 stretch list, Week 6 validation run protocol.

## Pending user-machine action (Phase 1 Week 1 Days 1-5 cannot run autonomously)

The remaining Week 1 work requires actual toolchain installation and repo cloning on the user's dev machine. Per design.md Section 13, these run **natively on the host OS** (Windows, macOS, or Linux — no WSL2).

### Day 1 — Toolchain install

User must install, cross-platform:

```bash
# Go toolchain
# Windows: download from https://go.dev/dl/
# macOS: brew install go
# Linux: apt/pacman/etc

# Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
rustup component add rust-src
# Install rust-analyzer
# Windows: download from https://github.com/rust-lang/rust-analyzer/releases
# macOS: brew install rust-analyzer
# Linux: package manager or rustup

# SCIP indexers
go install github.com/sourcegraph/scip-go/cmd/scip-go@latest

# ast-grep
cargo install ast-grep --locked
# or: brew install ast-grep

# Opengrep
# Download from https://github.com/opengrep/opengrep/releases
# Or: pip install opengrep (when available)

# Python deps
pip install "protobuf>=5.0"

# (Optional) CodeQL CLI for public-OSS targets only
# https://github.com/github/codeql-cli-binaries/releases
```

### Day 1 — Generate SCIP protobuf bindings

User runs once:

```bash
cd ~/.plamen
curl -L -o scip.proto https://raw.githubusercontent.com/sourcegraph/scip/main/scip.proto
protoc --python_out=plamen_l1/ scip.proto
# Produces plamen_l1/scip_pb2.py
```

Sanity check: `python -m plamen_l1.scip_reader --help` should print the CLI usage.

### Day 2 — ast-grep smoke test on benchmarks

After cloning the first benchmark target (geth v1.13.14), user runs:

```bash
cd /tmp/go-ethereum-v1.13.14
ast-grep scan --lang go --pattern 'count - 1' eth/protocols/
```

Expected: flags the GetHeadersFrom underflow site. Validates the primitive.

### Day 3 — SCIP reader smoke test on reth

User runs (memory-conservative):

```bash
cd /tmp/reth-66c9403
rust-analyzer scip crates/net/network --exclude-vendored-libraries
cp index.scip ~/.plamen/benchmarks/l1/reth-network.scip

cd ~/.plamen
python -m plamen_l1.scip_reader benchmarks/l1/reth-network.scip stats
python -m plamen_l1.scip_reader benchmarks/l1/reth-network.scip search Handle
```

Expected: `stats` returns non-zero counts; `search` returns handler symbol entries.

### Day 4 — Benchmark corpus fetch + build

Run the commands from `benchmarks/l1/README.md` for each of the 5 Phase 1 targets. Record build time and peak RAM. If reth blows 16 GB, switch to the CometBFT fallback.

### Day 5 — Ground truth writing

For each target, create `benchmarks/l1/<target-name>/ground_truth.md` using the template in `benchmarks/l1/README.md`. Extract vulnerable file/line from the public advisory.

### Day 6 — Skill pack + methodology — already landed

Round 4 exemplar integration + 2 new skills (`validator-lifecycle-and-slashing`, `hardfork-activation-and-protocol-upgrade`) were completed in the previous session commit (`28e217d`). This day's work is already done.

### Day 7 — Smoke test against geth CVE-2024-32972

Final Week 1 milestone: run the L1 Opengrep rule pack against geth v1.13.14 and confirm it flags the underflow:

```bash
opengrep --config agents/skills/injectable/l1/_opengrep-rules/ \
  --json /tmp/go-ethereum-v1.13.14 > /tmp/opengrep_hits.json
jq '.[] | select(.check_id | contains("integer-underflow"))' /tmp/opengrep_hits.json
```

Expected: the rule pack fires on `eth/protocols/eth/handler.go` around the `GetHeadersFrom` call.

## Integration pending (not Week 1, but tracked)

### plamen.py orchestrator
- [ ] Route `/plamen l1 [light|core|thorough]` to the new L1 prompt subtree. The main orchestrator lives at `plamen.py` (139 KB) and will need a new mode-axis branch. **Intentionally deferred** — this is Week 5 per the milestone plan and should not happen until the primitives layer is empirically validated.

### MCP wrapper for scip_reader
- [ ] Wrap `plamen_l1.scip_reader` as an MCP server that exposes the query methods as MCP tools. This makes it callable from within agent prompts the same way slither-analyzer and solana-fender are. **Week 2 deliverable**.

### Opengrep MCP wrapper
- [ ] Thin MCP wrapper over `opengrep --json` so depth agents can invoke it mid-analysis rather than only reading the Phase 0.5 baked output. **Week 2 deliverable**.

### Fork-ancestry for node clients
- [ ] Extend the existing `fork-ancestry` skill (currently smart-contract-focused) to read `go.mod` `replace` and `Cargo.toml` `[patch]` blocks. **Week 2 deliverable**, partially documented in `prompts/l1/phase1-recon-prompt.md` TASK 1.

## Commit history this session

```
l1-experimental (PlamenTSV/plamen-l1-experimental, private)
├── 78fa100  Initial L1 planning (design v0.1 + research round 1)
├── 7952f3f  Integrate validation research (design v0.2 + round 2)
├── 1726b50  Draft 13 L1 skills + severity matrix (v0.1)
├── b0be985  Design v0.3 + severity v0.2 (cross-platform pivot + Opengrep + Immunefi)
├── 28e217d  Skills v0.2: Round 4 exemplars + 2 new skills
└── [pending] Week 1 scaffold: depth agents, SCIP reader, skill-index, L1 prompts, Opengrep rules, benchmarks
```

## Handoff state

Next session can resume from:

1. Read this file first for orientation.
2. Check `docs/l1-mode/design.md` Section 10 for the milestone plan.
3. If user has run Day 1 toolchain install, proceed to Day 2-7 validation.
4. If user has NOT run Day 1, guide them through it using this file's "Pending user-machine action" section.
5. Week 2+ deliverables (MCP wrappers, fork-ancestry extension, opengrep MCP) are tracked above as explicit TODOs.

## What this session achieved in summary

The L1 mode has gone from "design document" to "scaffolded codebase":

- **2 new depth agents** registered and defined
- **SCIP reader primitive shim** implemented and cross-platform
- **15 L1 skills** registered in the skill index with trigger patterns
- **L1 recon + verification prompts** written with Phase 0.5 Bake protocol
- **3 seed Opengrep rules** grounded in real Round 4 exemplars (CVE-2024-32972, Cosmos ASA-2025-003, NEAR Ping of Death)
- **Benchmark corpus** documented with reproduction commands for all 5 Phase 1 targets

None of it is running yet — that's Week 1 Days 1-5 on the user's machine. But every file the orchestrator will need is now in the fork, and the user can proceed independently from the install step without blocking on more design work.
