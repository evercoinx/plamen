"""Phase 5b: Mechanical PoC verification — Python-native phase.

The driver invokes this module instead of trusting LLM-reported test outcomes.
For each `verify_*.md` in the scratchpad:

  1. Parse `Test File:` + `Command:` fields (reuses spike_mechanical_poc.py
     parser — already battle-tested by 21 unit tests).
  2. Look up the language's test runner from
     `~/.plamen/rules/language-toolchain-registry.json`.
  3. Resolve the test path under the project root.
  4. Invoke the test runner with a per-test timeout.
  5. Classify outcome: PASS | FAIL | COMPILE_FAIL | TIMEOUT | NO_TEST_MATCH |
     TOOLCHAIN_UNAVAILABLE | BUILD_FAILED | NO_TEST_FILE | EXEC_ERROR.
  6. Append (never overwrite) the mechanical verdict to the verify file:
       - `Mechanical-Verified: YES — Result: PASS` and update Evidence Tag.
       - `Mechanical-Verified: YES — Result: FAIL` (preserve LLM body for
         the Assertion Retry Protocol next pass).
       - `Mechanical-Verified: NO (reason: ...)` for non-execution outcomes.
  7. Emit `mechanical_verify_manifest.md` summarizing all per-finding outcomes.

The phase is opt-in via `MECHANICAL_VERIFY=true` env or
`config["mechanical_verify"]=True`. Default OFF for first ship.
Failure mode is DEGRADED (warning), never HALT — the LLM tag is preserved
when mechanical execution is unavailable.

Cross-ecosystem support:
  - evm     : forge test                          (registry.evm)
  - solana  : cargo test test_{id} (Anchor or native)
  - aptos   : aptos move test --filter test_{id}
  - sui     : sui move test {test_name}
  - soroban : cargo test --features testutils test_{id}
  - l1_go   : go test -run Test_{id} ./...
  - l1_rust : cargo test test_{id}

L1 entries (l1_go, l1_rust) are added to the registry at first load via
_ensure_l1_registry_entries(); the file on disk is the source of truth
for SC ecosystems and L1 is loaded as overlay.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# Per-file and per-phase budgets (overridable via env for ops scenarios).
_DEFAULT_PER_TEST_TIMEOUT_S = int(os.environ.get("PLAMEN_MECH_VERIFY_TIMEOUT", "180"))
# One-time pre-warm compile budget (see _prewarm_build). Raised from 300s: a cold
# `--via-ir` dependency-heavy repo cannot compile in the old budget, so the cache
# never warmed and every PoC TIMEOUTed at [CODE-TRACE]. Default matches recon's build
# ceiling (a measured large-repo cold via-ir build runs >34min, so a 40-min budget
# was too tight for the cold-verify path). On a cache already warmed by recon this
# pre-warm is a ~seconds incremental build; the generous budget only bites when verify
# runs against a cold cache. Ops-overridable via PLAMEN_MECH_BUILD_TIMEOUT.
_DEFAULT_BUILD_TIMEOUT_S = int(os.environ.get("PLAMEN_MECH_BUILD_TIMEOUT", "5400"))
_DEFAULT_PHASE_BUDGET_S = int(os.environ.get("PLAMEN_MECH_VERIFY_BUDGET", "1800"))


@dataclass
class ExecResult:
    """One verify_*.md → test-runner execution record."""
    verify_file: str
    finding_id: str
    language: str
    test_file_resolved: Optional[str] = None
    test_function: Optional[str] = None
    test_command_used: Optional[str] = None
    # PASS | FAIL | COMPILE_FAIL | TIMEOUT | NO_TEST_MATCH |
    # TOOLCHAIN_UNAVAILABLE | BUILD_FAILED | NO_TEST_FILE | EXEC_ERROR | SKIPPED
    status: str = "SKIPPED"
    duration_s: float = 0.0
    stdout_tail: str = ""
    # Derived evidence tag the manifest recommends ([POC-PASS] / [POC-FAIL] /
    # preserve-existing). Driver decides whether to write back.
    recommended_tag: str = ""


# ---------------------------------------------------------------------------
# Registry loading + L1 overlay
# ---------------------------------------------------------------------------


def _registry_path() -> Path:
    """Resolve language-toolchain-registry.json.

    Prefers the canonical install location (PLAMEN_HOME or ~/.plamen/rules/);
    falls back to the copy shipped beside this module in the repo (rules/, one
    level up from scripts/) when the canonical path is absent -- e.g. CI, or the
    driver run directly from a checkout that isn't symlinked into ~/.plamen.
    """
    home = Path(os.environ.get("PLAMEN_HOME", str(Path.home() / ".plamen")))
    canonical = home / "rules" / "language-toolchain-registry.json"
    if canonical.exists():
        return canonical
    repo = Path(__file__).resolve().parent.parent / "rules" / "language-toolchain-registry.json"
    if repo.exists():
        return repo
    return canonical  # let _load_registry handle the missing-file fallback


def _load_registry(custom_path: Optional[Path] = None) -> dict:
    """Load registry JSON. L1 entries are overlay-injected at load time."""
    path = custom_path or _registry_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 2, "languages": {}}
    _ensure_l1_registry_entries(data)
    return data


def _ensure_l1_registry_entries(reg: dict) -> None:
    """Inject l1_go / l1_rust into the registry if not present.

    L1 entries are kept as runtime overlay (not on-disk) for two reasons:
      1. The SC-only registry file is shared across all 5 SC ecosystems and
         doesn't conceptually own L1 client testing.
      2. L1 mode currently hard-codes commands in `prompts/l1/*` — this
         consolidates them under a single dispatch surface without touching
         the L1 prompts.
    """
    langs = reg.setdefault("languages", {})
    if "l1_go" not in langs:
        langs["l1_go"] = {
            "build_command": "go build ./...",
            "test_command": "go test -run {test_function} -v ./...",
            "test_filter_mode": "go_run_regex",
            "evidence_tags": ["POC-PASS", "POC-FAIL", "CODE-TRACE"],
            "fuzz_engines": [],
        }
    if "l1_rust" not in langs:
        langs["l1_rust"] = {
            "build_command": "cargo build --all-targets",
            "test_command": "cargo test {test_function} -- --nocapture",
            "test_filter_mode": "cargo_name_filter",
            "evidence_tags": ["POC-PASS", "POC-FAIL", "CODE-TRACE"],
            "fuzz_engines": [],
        }


def _toolchain_binary_for(language: str) -> str:
    """First command word from the build/test command (used for shutil.which)."""
    table = {
        "evm": "forge",
        "solana": "cargo",
        "aptos": "aptos",
        "sui": "sui",
        "soroban": "cargo",
        "l1_go": "go",
        "l1_rust": "cargo",
    }
    return table.get(language, "")


# ---------------------------------------------------------------------------
# Reuse parser + path resolution from the spike script
# ---------------------------------------------------------------------------


def _spike_module():
    """Lazy-import the spike to reuse parse_verify_file + classify_match."""
    import importlib
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    return importlib.import_module("spike_mechanical_poc")


# ---------------------------------------------------------------------------
# Command-template substitution
# ---------------------------------------------------------------------------


def _inject_cargo_exact(argv: list[str]) -> list[str]:
    """Add libtest `--exact` so a cargo test-name filter matches ONE test.

    Without it `cargo test test_x` is a substring filter that also runs
    `test_x_helper`; a sibling FAIL would mis-attribute to this finding (the
    non-EVM analogue of the EVM VERIF-5 AMBIGUOUS isolation guard).
    `--exact` is a libtest harness flag, so it must follow the `--` separator.
    """
    if "--exact" in argv:
        return argv
    if "--" in argv:
        i = argv.index("--")
        return argv[: i + 1] + ["--exact"] + argv[i + 1:]
    return argv + ["--", "--exact"]


def _format_test_command(template: str, test_function: str,
                        test_file: Optional[str],
                        language: Optional[str] = None) -> list[str]:
    """Render the registry's test_command into an argv list.

    Substitution tokens:
      {ID}            — finding ID (legacy; rarely needed)
      {id}            — same as {ID}, lowercased
      {test_function} — extracted from verify file's `Test File` or `Command`
      {test_name}     — alias for test_function (sui uses {test_name})
      {test_path}     — relative path to the test file under project root

    v2.8.16 Phase 1 (must-fix #1): apply per-ecosystem EXACT-name isolation so
    a `[POC-PASS]` can only be attributed to the finding's own test:
      - cargo (solana/soroban/l1_rust): append libtest `--exact`
      - go (l1_go): anchor the `-run` regex as `^fn$`
      - aptos `--filter` / sui positional have no exact flag (substring; the
        driver-dictated unique function name is the isolation in practice).
    """
    lang = (language or "").lower().strip()
    # l1_go: anchor the -run regex to the exact function name.
    # Guard against a None test_function: str.replace() rejects a None
    # replacement ("replace() argument 2 must be str, not None"), which on a
    # finding with no dictated test name crashed the whole sc_mechanical_verify
    # phase (observed: 1 degraded phase on DFlow). Falling back to "" localizes
    # the failure to that one finding (empty test name -> its PoC simply can't
    # run) instead of degrading the entire phase. All downstream uses of
    # test_function (fn_lower, the test_{id} substitution, cargo --exact) are
    # already None-guarded.
    fn_sub = test_function or ""
    if lang == "l1_go" and test_function and not (
        test_function.startswith("^") and test_function.endswith("$")
    ):
        fn_sub = f"^{test_function}$"
    cmd = template.replace("{test_function}", fn_sub)
    cmd = cmd.replace("{test_name}", fn_sub)
    # Legacy {ID} / {id}: extract suffix after leading 'test_' if present.
    # Use a leading-prefix strip (NOT global replace) so internal 'test_'
    # substrings — e.g. 'test_a_test_b' — are preserved.
    fn_lower = (test_function or "").lower()
    id_suffix = fn_lower[5:] if fn_lower.startswith("test_") else fn_lower
    cmd = cmd.replace("{ID}", id_suffix.upper())
    # Intercept the literal `test_{id}` token with the dictated function name
    # verbatim BEFORE the {id} substitution, so non-`test_`-prefixed names
    # (e.g. aptos 'overflow_check') filter on the real name rather than
    # 'test_overflow_check'. Guarded on a non-empty test_function.
    if test_function:
        cmd = cmd.replace("test_{id}", test_function)
    cmd = cmd.replace("{id}", id_suffix)
    if test_file:
        norm_file = test_file.replace("\\", "/")
        cmd = cmd.replace("{test_path}", norm_file)
        # DAML: `daml test --files {file}` has no per-test name filter
        # (test_filter_mode == "daml_no_filter"). `daml test` runs every
        # in-scope Script(); isolation is file-scoped, so the {file} token is
        # the per-PoC isolated file path. NEVER attempt --match-test/--filter.
        cmd = cmd.replace("{file}", norm_file)
    # Tokenize on whitespace (registry commands don't contain quoted args)
    argv = cmd.split()
    if lang in ("solana", "soroban", "l1_rust") and test_function:
        argv = _inject_cargo_exact(argv)
    return argv


# ---------------------------------------------------------------------------
# Build-root resolution
#
# The audit's `project_root` is the audit *scope* directory — often a
# subdirectory like `omni-chain-contracts/contracts`. But the build manifest
# (foundry.toml / Cargo.toml / Move.toml / go.mod) and the test directory
# (`test/`, `tests/`) live at the *project* root, which is frequently the
# parent. Resolving test files against the scope dir is what produced
# 142/142 NO_TEST_FILE on a prior audit. _find_build_root walks UP from
# project_root to the directory that actually owns the build.
# ---------------------------------------------------------------------------


_BUILD_MANIFESTS: dict[str, tuple[str, ...]] = {
    "evm": ("foundry.toml", "hardhat.config.ts", "hardhat.config.js"),
    "solana": ("Cargo.toml", "Anchor.toml"),
    "soroban": ("Cargo.toml",),
    "aptos": ("Move.toml",),
    "sui": ("Move.toml",),
    "l1_go": ("go.mod",),
    "l1_rust": ("Cargo.toml",),
    "daml": ("daml.yaml", "Daml.toml"),
}


_BUILD_SCAN_SKIP_DIRS = {
    "node_modules", "target", ".git", "out", "cache", "artifacts",
    "dist", "build", ".venv", "venv", "__pycache__", "lib", ".cargo",
}


_RECON_BUILD_ROOT_RE = re.compile(
    r"(?im)^\s*\**\s*Chosen\s+build\s+root\s*\**\s*:\s*`?\s*([^`\n]+?)\s*`?\s*$"
)


def _read_recon_build_root(scratchpad, language: str) -> Optional[Path]:
    """Honor recon's authoritative chosen build root from build_status.md.

    Recon (phase1 TASK 1) resolves the directory that owns the real build
    manifest — frequently a sibling/ancestor of the source-only audit scope
    dir that the heuristic upward-walk + tight neighbourhood scan cannot
    reach. Recon records it in build_status.md as a line:

        **Chosen build root**: `<absolute path>`

    Returns the resolved path ONLY if it exists AND actually owns a manifest
    for `language` (so a stale/wrong recon line degrades safely to the
    heuristic). Returns None when:
      - scratchpad/build_status.md is missing,
      - no `Chosen build root` line is present,
      - the value is the explicit `(none)` token,
      - the path does not exist or owns no matching manifest.
    """
    if scratchpad is None:
        return None
    try:
        status_path = Path(scratchpad) / "build_status.md"
        if not status_path.exists():
            return None
        text = status_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _RECON_BUILD_ROOT_RE.search(text)
    if not m:
        return None
    raw = (m.group(1) or "").strip().strip("`").strip()
    if not raw or raw.lower() in ("(none)", "none"):
        return None
    _lang = (language or "").lower().strip()
    if _lang == "go":
        _lang = "l1_go"
    elif _lang == "rust":
        _lang = "l1_rust"
    manifests = _BUILD_MANIFESTS.get(_lang, ("foundry.toml",))
    try:
        cand = Path(raw).resolve()
    except OSError:
        return None
    if not cand.is_dir():
        return None
    if any((cand / man).exists() for man in manifests):
        return cand
    return None


def _find_build_root(project_root: Path, language: str) -> Path:
    """Resolve the directory that owns the build manifest.

    Order (v2.8.16 Phase 1, must-fix #6):
      1. Walk UP from project_root (project_root + 5 ancestors).
      2. Bounded sibling/descendant scan (≤2 levels under each ancestor),
         short-circuiting on the first manifest match — the audit scope dir is
         frequently a SIBLING of the build root (e.g. an `interfaces/` scope
         beside a `contracts/` Foundry project), which an upward-only walk can
         never reach.
    Falls back to project_root itself if no manifest is found (degradation,
    not failure — the original behavior).
    """
    _lang = (language or "").lower().strip()
    if _lang == "go":
        _lang = "l1_go"
    elif _lang == "rust":
        _lang = "l1_rust"
    manifests = _BUILD_MANIFESTS.get(_lang, ("foundry.toml",))
    root = Path(project_root).resolve()

    def _has_manifest(d: Path) -> bool:
        return any((d / man).exists() for man in manifests)

    # 1. Upward walk (project_root + 5 ancestors).
    cur = root
    for _ in range(6):
        if _has_manifest(cur):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent

    # 2. Conservative neighbourhood scan. A *wrong* build root yields a false
    #    verdict (worse than no root → safe degrade), so the scan is deliberately
    #    tight: only project_root's own subtree (scope ABOVE the build root) and
    #    its IMMEDIATE siblings (scope and build root share one parent). Deeper
    #    ancestor scanning is NOT done here — that is the job of the authoritative
    #    recon-emitted build_root, not an unbounded heuristic that could match an
    #    unrelated project. Short-circuits on the first manifest match.
    def _scan(base: Path, depth: int) -> Optional[Path]:
        try:
            children = [c for c in base.iterdir() if c.is_dir()]
        except OSError:
            return None
        for c in children:
            if c.name in _BUILD_SCAN_SKIP_DIRS or c.name.startswith("."):
                continue
            if _has_manifest(c):
                return c
            if depth > 1:
                found = _scan(c, depth - 1)
                if found is not None:
                    return found
        return None

    # 2a. project_root's own subtree (≤2 levels): scope dir sits above build root.
    found = _scan(root, 2)
    if found is not None:
        return found
    # 2b. immediate siblings only (≤1 level): scope and build root share a parent.
    parent = root.parent
    if parent != root:
        try:
            for sib in parent.iterdir():
                if sib == root or not sib.is_dir():
                    continue
                if sib.name in _BUILD_SCAN_SKIP_DIRS or sib.name.startswith("."):
                    continue
                if _has_manifest(sib):
                    return sib
        except OSError:
            pass

    return root


# ---------------------------------------------------------------------------
# Per-finding test runner
# ---------------------------------------------------------------------------


def _resolve_test_path_for(probe, build_root: Path,
                          project_root: Optional[Path] = None) -> Optional[Path]:
    """Resolve a test file path under the build root, trying multiple anchors.

    Tries the build root first (where `test/` normally lives), then the
    narrower audit scope dir as a fallback.
    """
    if not probe.test_file_resolved:
        return None
    raw = probe.test_file_resolved
    name = Path(raw.replace("\\", "/")).name
    roots = [build_root]
    if project_root is not None and Path(project_root).resolve() != Path(build_root).resolve():
        roots.append(Path(project_root))
    for root in roots:
        for c in (
            root / raw,
            root / name,
            root / "test" / name,
            root / "tests" / name,
            root / "sources" / "tests" / name,
            root / "trident-tests" / name,
        ):
            try:
                if c.exists() and c.is_file():
                    return c
            except OSError:
                continue
    return None


_MATCH_TEST_CMD_RE = re.compile(r"--match-test\s+[\"']?([A-Za-z0-9_]+)")
_MATCH_CONTRACT_CMD_RE = re.compile(r"--match-contract\s+[\"']?([A-Za-z0-9_]+)")


def _evm_forge_filter(probe, rel_path: str) -> list[str]:
    """Pick the narrowest forge filter available.

    Prefer --match-test (a single function), then --match-contract (the test
    contract), then --match-path (the whole file — always works once the file
    is resolved, even when the verify file gave no function/contract name).
    """
    cmd = probe.test_command or ""
    m = _MATCH_TEST_CMD_RE.search(cmd)
    if m:
        return ["--match-test", m.group(1)]
    if getattr(probe, "test_function", None):
        return ["--match-test", probe.test_function]
    m = _MATCH_CONTRACT_CMD_RE.search(cmd)
    if m:
        return ["--match-contract", m.group(1)]
    return ["--match-path", rel_path]


_FOUNDRY_PROFILE_CMD_RE = re.compile(r"FOUNDRY_PROFILE\s*=\s*[\"']?([A-Za-z0-9_]+)")
# `[profile.<name>]` ... `test = "<dir>"` (and `src`/`test_dir` aliases).
_TOML_PROFILE_HDR_RE = re.compile(r"(?m)^\s*\[profile\.([A-Za-z0-9_]+)\]\s*$")
_TOML_TEST_DIR_RE = re.compile(
    r"(?m)^\s*(?:test|test_dir)\s*=\s*[\"']([^\"']+)[\"']")


def _resolve_foundry_profile(probe, build_root, resolved) -> Optional[str]:
    """Recover the FOUNDRY_PROFILE the verifier's PoC actually ran under.

    The verifier records its working command (e.g. `FOUNDRY_PROFILE=poc forge
    test ...`) on the probe; the mechanical re-run reconstructs its own argv and
    used to drop that env var, so forge fell back to `[profile.default]` whose
    `test` dir often does NOT contain the PoCs (custom profiles route tests to a
    non-default dir). That silently turned a passing suite into mass
    NO_TEST_FILE/FAIL, cascading into spurious assertion + INFLATED_PROSE
    demotions.

    Resolution order:
      1. `FOUNDRY_PROFILE=<x>` explicitly recorded in the verify file's Command.
      2. foundry.toml auto-detect: the non-default profile whose `test` dir
         actually contains the resolved test file (works even when the verifier
         never recorded the env var).
    Returns the profile name, or None to run under the default profile."""
    cmd = getattr(probe, "test_command", "") or ""
    m = _FOUNDRY_PROFILE_CMD_RE.search(cmd)
    if m:
        return m.group(1)
    try:
        toml = (Path(build_root) / "foundry.toml").read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        rel = str(Path(resolved).resolve().relative_to(Path(build_root).resolve()))
    except Exception:
        rel = str(resolved)
    rel = rel.replace("\\", "/")
    # Walk each [profile.<name>] block; map name -> its test dir.
    hdrs = list(_TOML_PROFILE_HDR_RE.finditer(toml))
    for i, h in enumerate(hdrs):
        name = h.group(1)
        block = toml[h.end():(hdrs[i + 1].start() if i + 1 < len(hdrs) else len(toml))]
        tm = _TOML_TEST_DIR_RE.search(block)
        if not tm:
            continue
        test_dir = tm.group(1).strip("/").replace("\\", "/")
        if name != "default" and (rel.startswith(test_dir + "/") or rel == test_dir):
            return name
    return None


_CARGO_PKG_RE = re.compile(
    r"(?:^|\s)(?:-p|--package)(?:[=\s]+)([A-Za-z0-9_][A-Za-z0-9_-]*)")
# Generic tokens that are NOT real test-function names — typically mis-extracted
# from a file stem (`test.rs`, `lib.rs`) or a module path. A cargo
# `--exact <token>` on any of these matches ZERO tests → false NO_TEST_MATCH/FAIL.
_CARGO_BOGUS_FILTER = frozenset(
    {"test", "tests", "mod", "lib", "main", "src", "it", "unit", "integration"})


def _resolve_cargo_package(probe) -> Optional[str]:
    """Recover the `-p <package>` the verifier's PoC ran under (mirrors
    `_resolve_foundry_profile` for EVM).

    The registry cargo template carries no package selector, so on a multi-member
    workspace (e.g. `contracts/registry`, `contracts/factory`, …) the mechanical
    re-run executes at the workspace root and cannot resolve a member's test →
    mass NO_TEST_FILE/FAIL, and every `[POC-PASS]` fails to graduate. The
    verifier records its working command (`cargo test -p <pkg> …`); read the
    package back from it. Returns the package name, or None."""
    cmd = getattr(probe, "test_command", "") or ""
    m = _CARGO_PKG_RE.search(cmd)
    return m.group(1) if m else None


def _apply_cargo_workspace_fixups(argv: list, probe) -> list:
    """Repair the two mechanical-cargo mis-reconstructions that made every
    workspace-member Soroban/Rust PoC read as NO_TEST_FILE/FAIL:
      (1) thread the verifier's `-p <package>` back in (registry template omits
          it), so a workspace-member test resolves;
      (2) drop a phantom `<generic> -- --exact` filter when the substituted test
          name is a non-test token (e.g. `test` extracted from `test.rs`) — run
          the package suite as the verifier actually did, instead of
          `--exact test` which matches nothing.
    Never raises; returns argv unchanged on any parse issue."""
    try:
        out = list(argv)
        # (2) The registry template appends `-- --exact <fn>` for isolation, but
        # the extracted filter is a BARE function name while Rust tests are
        # module-nested (`mod xxx_tests { #[test] fn poc_… }`). `--exact
        # <bare_fn>` then matches NOTHING (cargo `--exact` needs the full
        # `mod::fn` path) → NO_TEST_MATCH. Cargo's SUBSTRING match on the unique
        # function name isolates to that one test in practice (`N filtered out`),
        # so drop the `-- --exact` tail. If the filter token is itself a bogus
        # generic (mis-extracted from `test.rs`), also drop it → package suite.
        if "--" in out:
            sep = out.index("--")
            filt_idx = None
            for i in range(sep - 1, 0, -1):
                if out[i].startswith("-"):
                    continue
                if i <= 1:  # position 1 is the `test` subcommand, never a filter
                    break
                filt_idx = i
                break
            bogus = (filt_idx is not None
                     and out[filt_idx].lower() in _CARGO_BOGUS_FILTER)
            del out[sep:]              # drop `-- --exact …` (substring is isolation)
            if bogus and filt_idx is not None:
                del out[filt_idx]      # also drop the bogus filter → package suite
        # (3) reconcile `--features` with the verifier's recorded command: the
        # registry template hardcodes `--features testutils`, but not every
        # workspace member DEFINES that feature (cargo hard-errors "does not
        # contain this feature"). Mirror exactly what the verifier ran.
        rec = getattr(probe, "test_command", "") or ""
        rec_feat = re.search(r"--features(?:[=\s]+)(\S+)", rec)
        if "--features" in out:
            fi = out.index("--features")
            if rec_feat and fi + 1 < len(out):
                out[fi + 1] = rec_feat.group(1)   # use verifier's feature set
            elif fi + 1 < len(out):
                del out[fi:fi + 2]                # verifier used none → strip
            else:
                del out[fi:fi + 1]
        elif rec_feat:
            try:
                ti2 = out.index("test")
                out[ti2 + 1:ti2 + 1] = ["--features", rec_feat.group(1)]
            except ValueError:
                out.extend(["--features", rec_feat.group(1)])
        # (1) inject `-p <pkg>` right after the `test` subcommand if absent.
        pkg = _resolve_cargo_package(probe)
        has_pkg = any(
            t in ("-p", "--package") or t.startswith("--package=") for t in out)
        if pkg and not has_pkg:
            try:
                ti = out.index("test")
                out[ti + 1:ti + 1] = ["-p", pkg]
            except ValueError:
                out.extend(["-p", pkg])
        return out
    except Exception:
        return argv


def _classify_evm_outcome(rc: int, stdout: str, isolated: bool = True) -> str:
    """Classify `forge test` output.

    VERIF-5: when the run was NOT isolated to the finding's own test (i.e. a
    whole-file `--match-path` fallback because the verify file named no test/
    contract), a result containing BOTH `[PASS]` and `[FAIL]` cannot be
    attributed to this finding -- an unrelated test in the same file may have
    failed. Return AMBIGUOUS so the integrity/demotion layer does NOT treat it
    as a real mechanical FAIL (which could wrongly demote a true positive).
    """
    s = stdout
    if "Compiler run failed" in s or re.search(r"^Error \(", s, re.MULTILINE):
        return "COMPILE_FAIL"
    if "No tests match" in s or "no tests to run" in s.lower():
        return "NO_TEST_MATCH"
    if not isolated and "[PASS]" in s and ("[FAIL" in s or re.search(r"Suite result:\s*FAILED", s)):
        return "AMBIGUOUS"
    if rc == 0 and "[PASS]" in s:
        return "PASS"
    if "[FAIL" in s or re.search(r"Suite result:\s*FAILED", s):
        return "FAIL"
    if rc != 0:
        return "FAIL"
    if "[PASS]" in s:
        return "PASS"
    return "FAIL"


def _run_test_for_finding(verify_path: Path, build_root: Path, language: str,
                          registry: dict, per_test_timeout_s: int,
                          project_root: Optional[Path] = None) -> ExecResult:
    """Execute one verify file's PoC and classify the outcome."""
    spike = _spike_module()
    probe = spike.parse_verify_file(verify_path, language=language)
    result = ExecResult(
        verify_file=verify_path.name,
        finding_id=probe.finding_id,
        language=language,
        test_file_resolved=probe.test_file_resolved,
        test_function=probe.test_function,
    )

    # Short-circuit: no test file referenced at all → record + skip.
    # NOTE: a missing test_function is NOT a skip — we run by --match-path.
    if not probe.test_file_resolved:
        result.status = "NO_TEST_FILE"
        return result

    # Toolchain availability
    bin_name = _toolchain_binary_for(language)
    if bin_name and shutil.which(bin_name) is None:
        result.status = "TOOLCHAIN_UNAVAILABLE"
        result.stdout_tail = f"{bin_name} not on PATH"
        return result

    # Resolve path against the build root (and scope dir as fallback)
    resolved = _resolve_test_path_for(probe, build_root, project_root)
    if resolved is None:
        result.status = "NO_TEST_FILE"
        result.stdout_tail = (
            f"referenced {probe.test_file_resolved} but not found under "
            f"{build_root}"
        )
        return result

    lang_cfg = (registry.get("languages") or {}).get(language)
    if not lang_cfg or "test_command" not in lang_cfg:
        result.status = "TOOLCHAIN_UNAVAILABLE"
        result.stdout_tail = f"no test_command in registry for language={language!r}"
        return result

    try:
        rel_path = str(resolved.relative_to(build_root)).replace("\\", "/")
    except ValueError:
        rel_path = str(resolved).replace("\\", "/")

    # EVM: run forge directly from the build root. Filter by --match-test when
    # a function is known, else --match-contract, else --match-path (whole
    # file). cwd MUST be the build root (where foundry.toml lives).
    if language == "evm":
        forge_bin = shutil.which("forge") or "forge"
        _filter = _evm_forge_filter(probe, rel_path)
        cmd = [forge_bin, "test", *_filter, "-vv"]
        # VERIF-5: a --match-path run is NOT isolated to the finding's own test;
        # a FAIL could be an unrelated test in the same file. Track isolation so
        # the classifier can return AMBIGUOUS instead of mis-attributing FAIL.
        _isolated = _filter[:1] == ["--match-test"]
        # RC-harness: forge must run under the SAME FOUNDRY_PROFILE the verifier
        # used, or a custom profile's non-default test dir is invisible and the
        # whole suite reads as NO_TEST_FILE/FAIL. Inherit env + propagate the
        # resolved profile (recorded command, else foundry.toml auto-detect).
        env = os.environ.copy()
        profile = _resolve_foundry_profile(probe, build_root, resolved)
        if profile:
            env["FOUNDRY_PROFILE"] = profile
        elif env.get("FOUNDRY_PROFILE"):
            profile = env["FOUNDRY_PROFILE"]  # already set in the parent env
        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=str(build_root), capture_output=True, text=True,
                timeout=per_test_timeout_s, shell=False, env=env,
            )
            result.duration_s = time.time() - t0
            result.test_command_used = (
                (f"FOUNDRY_PROFILE={profile} " if profile else "") + " ".join(cmd))
            stdout = (proc.stdout or "") + "\n" + (proc.stderr or "")
            result.stdout_tail = stdout[-3000:]
            result.status = _classify_evm_outcome(proc.returncode, stdout, isolated=_isolated)
        except subprocess.TimeoutExpired:
            result.duration_s = float(per_test_timeout_s)
            result.status = "TIMEOUT"
            result.stdout_tail = f"forge test exceeded {per_test_timeout_s}s"
        except Exception as exc:
            result.duration_s = time.time() - t0
            result.status = "EXEC_ERROR"
            result.stdout_tail = f"forge subprocess error: {exc}"
        return result

    # Non-EVM ecosystems — build argv from registry template
    cmd = _format_test_command(
        lang_cfg["test_command"], probe.test_function, rel_path,
        language=language,
    )
    if not cmd:
        result.status = "EXEC_ERROR"
        result.stdout_tail = "empty command after template substitution"
        return result

    # Cargo workspace fixups: thread the verifier's `-p <package>` back in and
    # drop a phantom generic `--exact` filter, so a workspace-member PoC is
    # resolvable (RC-harness, mirrors the EVM FOUNDRY_PROFILE fix).
    if language in ("solana", "soroban", "l1_rust"):
        cmd = _apply_cargo_workspace_fixups(cmd, probe)

    # Resolve binary path (handles Windows .cmd / .exe shims)
    bin_path = shutil.which(cmd[0])
    if bin_path:
        cmd[0] = bin_path

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(build_root),
            capture_output=True,
            text=True,
            timeout=per_test_timeout_s,
            shell=False,
        )
        result.duration_s = time.time() - t0
        result.test_command_used = " ".join(cmd)
        stdout = (proc.stdout or "") + "\n" + (proc.stderr or "")
        result.stdout_tail = stdout[-3000:]
        result.status = _classify_non_evm_outcome(language, proc.returncode, stdout)
    except subprocess.TimeoutExpired:
        result.duration_s = float(per_test_timeout_s)
        result.status = "TIMEOUT"
        result.stdout_tail = f"test execution exceeded {per_test_timeout_s}s"
    except Exception as exc:
        result.duration_s = time.time() - t0
        result.status = "EXEC_ERROR"
        result.stdout_tail = f"subprocess error: {exc}"
    return result


def _classify_non_evm_outcome(language: str, rc: int, stdout: str) -> str:
    """Decide PASS / FAIL / COMPILE_FAIL / NO_TEST_MATCH for non-EVM runners."""
    s = stdout
    # Cargo (solana, soroban, l1_rust)
    if language in ("solana", "soroban", "l1_rust"):
        # Evaluate REAL signals FIRST. A cargo run prints a per-target
        # `test result:` line for unittests AND doc-tests; the doc-tests line is
        # almost always `running 0 tests` / `ok. 0 passed`. Checking those zero
        # markers first (the prior order) short-circuited a genuine
        # `test result: ok. 72 passed` unittest section to NO_TEST_MATCH — every
        # passing Soroban/Rust suite was silently discarded.
        if re.search(r"error\[E\d+\]|could not compile|error: linking", s):
            return "COMPILE_FAIL"
        if re.search(r"test result:\s*FAILED", s) or re.search(r"[1-9]\d*\s+failed", s):
            return "FAIL"
        if rc == 0 and re.search(r"test result:\s*ok\.\s*[1-9]\d*\s*passed", s):
            return "PASS"
        # No pass and no failure recorded → genuinely zero tests matched.
        if "running 0 tests" in s or re.search(r"test result:\s*ok\.\s*0\s*passed", s):
            return "NO_TEST_MATCH"
        if rc != 0:
            return "FAIL"
        return "NO_TEST_MATCH"
    # Go testing
    if language == "l1_go":
        # Zero-tests-matched must NOT be read as a pass (the `ok\tpkg` summary
        # is printed even when `-run` matched nothing).
        if "no tests to run" in s or "no test files" in s or "matching no tests" in s:
            return "NO_TEST_MATCH"
        if rc == 0 and (re.search(r"^ok\s+", s, re.MULTILINE) or "--- PASS" in s):
            return "PASS"
        if "build failed" in s or "cannot find package" in s or "syntax error" in s:
            return "COMPILE_FAIL"
        if rc != 0:
            return "FAIL"
        return "PASS"
    # Aptos Move
    if language == "aptos":
        if rc == 0 and re.search(r"Result\s*:\s*PASS|Test result:\s*OK", s):
            return "PASS"
        if "ERROR" in s and ("compile" in s.lower() or "type error" in s.lower()):
            return "COMPILE_FAIL"
        if rc != 0:
            return "FAIL"
        # rc==0 but no PASS/OK marker → zero tests matched, not a real pass.
        return "NO_TEST_MATCH"
    # Sui Move
    if language == "sui":
        if rc == 0 and re.search(r"Test result:\s*OK|PASS\s*$", s, re.MULTILINE):
            return "PASS"
        if "error[E" in s or "FAILURE building" in s:
            return "COMPILE_FAIL"
        if rc != 0:
            return "FAIL"
        # rc==0 but no PASS/OK marker → zero tests matched, not a real pass.
        return "NO_TEST_MATCH"
    # DAML (Canton) — `daml test --files <file>` runs every Script() in scope.
    # No per-test name filter (daml_no_filter); isolation is file-scoped.
    if language == "daml":
        sl = s.lower()
        # Compilation problems surface before any test runs.
        if re.search(r"error:|file does not compile|parse error|"
                     r"type checking|scope error|unknown identifier", sl):
            return "COMPILE_FAIL"
        # No Script() in the file → nothing executed, not a pass.
        if "no scripts" in sl or re.search(r"\b0\s+(?:of\s+\d+\s+)?(?:tests?|scripts?)\b", sl):
            return "NO_TEST_MATCH"
        # Runtime PoC failures map to FAIL (the assertion/precondition fired).
        if rc != 0 or re.search(
            r"failed|preconditionfailed|assertion|unhandled exception|"
            r"abort|errors?:\s*[1-9]", sl
        ):
            return "FAIL"
        # rc==0 plus a positive test-summary marker is a genuine pass.
        if re.search(r"test summary|tests?\s+passed|\bok\b|all scripts? ran", sl):
            return "PASS"
        # rc==0 but no positive marker → treat as zero-matched, not a pass.
        return "NO_TEST_MATCH"
    return "EXEC_ERROR"


def _recommended_tag(status: str) -> str:
    return {
        "PASS": "[POC-PASS]",
        "FAIL": "[POC-FAIL]",
        "COMPILE_FAIL": "[CODE-TRACE]",  # broken LLM test, not a defense
        "TIMEOUT": "[CODE-TRACE]",
        "NO_TEST_MATCH": "[CODE-TRACE]",
        "NO_TEST_FILE": "[CODE-TRACE]",
        "TOOLCHAIN_UNAVAILABLE": "",  # preserve existing tag
        "BUILD_FAILED": "",            # preserve existing tag
        "EXEC_ERROR": "",              # preserve existing tag
        "SKIPPED": "",
    }.get(status, "")


# ---------------------------------------------------------------------------
# Verify-file annotation (append-only)
# ---------------------------------------------------------------------------


_EVIDENCE_TAG_LINE_RE = re.compile(
    r"^(\s*\**Evidence\s+Tag\**\s*:.*)$",
    re.MULTILINE | re.IGNORECASE,
)
_PREFERRED_TAG_LINE_RE = re.compile(
    r"^(\s*\**Preferred\s+Tag\**\s*:.*)$",
    re.MULTILINE | re.IGNORECASE,
)
_MECHANICAL_LINE_RE = re.compile(
    r"^\s*\**Mechanical-Verified\**\s*:.*$",
    re.MULTILINE | re.IGNORECASE,
)


def _annotate_verify_file(verify_path: Path, result: ExecResult) -> bool:
    """Append a Mechanical-Verified line and (when PASS/FAIL) update the tag.

    Append-only semantics: previous Evidence Tag line is preserved as a comment
    so the LLM's original claim is auditable. Returns True if file was modified.
    """
    try:
        text = verify_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False

    # Idempotency: if a prior Mechanical-Verified line exists for the same
    # status, leave the file alone. (Rerunning the phase shouldn't grow it.)
    # Substring match is bold-marker agnostic (the line may carry `**` or not).
    existing = _MECHANICAL_LINE_RE.search(text)
    if existing:
        line = existing.group(0)
        if result.status in ("PASS", "FAIL"):
            same_status = f"Status: {result.status}" in line
        else:
            same_status = f"({result.status})" in line
        if same_status:
            return False

    rec_tag = _recommended_tag(result.status)
    mod_lines: list[str] = []

    # Strip any prior Mechanical-Verified line so we don't accumulate.
    text = _MECHANICAL_LINE_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    new_lines: list[str] = [
        "",
        "<!-- mechanical-verify v1 — driver-stamped, do not hand-edit below -->",
    ]
    if result.status in ("PASS", "FAIL"):
        new_lines.append(
            f"**Mechanical-Verified**: YES — Status: {result.status} "
            f"(duration: {result.duration_s:.1f}s)"
        )
    else:
        new_lines.append(
            f"**Mechanical-Verified**: NO ({result.status}) — "
            f"{(result.stdout_tail or '')[:200]}"
        )
    if result.test_command_used:
        new_lines.append(f"**Mechanical-Command**: `{result.test_command_used}`")
    if rec_tag:
        new_lines.append(f"**Mechanical-Tag**: {rec_tag}")
    new_lines.append("")

    text = text.rstrip() + "\n" + "\n".join(new_lines)

    # Only PASS/FAIL update the canonical Evidence Tag. Anything else
    # preserves the LLM's prior tag (the driver-stamped Mechanical-Tag line
    # above carries the override semantics for the report-writer to read).
    if result.status == "PASS":
        # If a downgrade comment is in the existing tag (e.g. "[CODE-TRACE]
        # (was [POC-PASS], integrity downgrade: ...)"), the regex preserves
        # the line. We don't aggressively rewrite — Mechanical-Tag below
        # is the authoritative override that downstream phases read.
        pass

    try:
        verify_path.write_text(text, encoding="utf-8")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Manifest writer
# ---------------------------------------------------------------------------


def _write_manifest(results: list[ExecResult], scratchpad: Path) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    lines = [
        "# Mechanical Verify Manifest",
        "",
        f"**Generated**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Total verify files**: {len(results)}",
        "",
        "## Status Counts",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status in (
        "PASS", "FAIL", "COMPILE_FAIL", "TIMEOUT",
        "NO_TEST_MATCH", "NO_TEST_FILE",
        "TOOLCHAIN_UNAVAILABLE", "BUILD_FAILED", "EXEC_ERROR", "SKIPPED",
    ):
        lines.append(f"| {status} | {counts.get(status, 0)} |")
    lines.append("")
    lines.append("## Per-Finding Results")
    lines.append("")
    lines.append("| Finding | Status | Duration | Test File | Function | Tag |")
    lines.append("|---------|--------|---------:|-----------|----------|-----|")
    for r in sorted(results, key=lambda x: x.finding_id or x.verify_file):
        tf = r.test_file_resolved or "—"
        if len(tf) > 40:
            tf = "…" + tf[-37:]
        lines.append(
            f"| {r.finding_id or '?'} | {r.status} | {r.duration_s:.1f}s "
            f"| {tf} | {r.test_function or '—'} | {_recommended_tag(r.status) or '—'} |"
        )
    (scratchpad / "mechanical_verify_manifest.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )

    # JSON sidecar for downstream programmatic consumption
    (scratchpad / "mechanical_verify_manifest.json").write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "counts": counts,
                "results": [asdict(r) for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # v2.0.8 (P3.1): write verdict_manifest.json — the canonical
    # machine-readable evidence-truth record that cross-references the
    # verifier's prose Evidence Tag claim against this mechanical execution.
    _write_verdict_manifest(results, scratchpad)


# ---------------------------------------------------------------------------
# v2.0.8 (P3.1): verdict manifest — evidence-chain truth layer
# ---------------------------------------------------------------------------

_PROOF_EVIDENCE_TAGS = (
    "[POC-PASS]", "[MEDUSA-PASS]", "[FUZZ-PASS]",
    "[NON-DET-PASS]", "[DIFF-PASS]", "[CONFORMANCE-PASS]",
)

_PROSE_TAG_RE = re.compile(
    r"\[(?:POC-PASS|POC-FAIL|CODE-TRACE|MEDUSA-PASS|"
    r"FUZZ-PASS|NON-DET-PASS|DIFF-PASS|CONFORMANCE-PASS|LSP-TRACE)\]",
    re.IGNORECASE,
)


# v2.8.16 Phase 1: the leading-marker class MUST include `-` and `\t`. Real
# verifier files write the canonical field as a Markdown bullet
# (`- **Evidence Tag**: [POC-PASS]`); the old `[*_`> ]*` prefix did not match a
# `-`, so the verifier's actual claim line was silently skipped. After the
# mechanical phase appends a NON-bullet `**Mechanical-Tag**: [CODE-TRACE]` line,
# that line WAS matched instead — so a fabricated bullet-form `[POC-PASS]` +
# NO_TEST_FILE was misclassified CONSISTENT rather than INFLATED_PROSE, defeating
# the integrity downgrade (and the #3a verdict flip that keys on it). The
# verifier's own claim (Evidence/Preferred Tag) is now read with PRIORITY over
# the driver-stamped Mechanical-Tag, so re-runs cannot shadow the claim either.
_CLAIM_FIELD_RE = re.compile(
    r"(?im)^[-*_`> \t]*(?:Evidence\s+Tags?|Preferred\s+Tag)"
    r"[*_`> \t]*\s*:\s*(.+)$"
)
_MECH_TAG_FIELD_RE = re.compile(
    r"(?im)^[-*_`> \t]*Mechanical-?Tag[*_`> \t]*\s*:\s*(.+)$"
)
_FENCED_CODE_RE = re.compile(r"(?s)```.*?```")


def _extract_verifier_prose_tag(verify_path: Path) -> str:
    """Read the verifier's prose Evidence Tag from a verify_<ID>.md file.

    VERIF-2: anchor to the canonical `Evidence Tag:` / `Preferred Tag:` FIELD
    value (the contract every verifier file must carry), NOT a whole-file
    first-match -- a pasted reference table or an example tag in a fenced code
    block could otherwise poison the result. The verifier's OWN claim is read
    with priority; the driver-stamped `Mechanical-Tag:` line is only a fallback
    so a prior annotation cannot shadow the claim being integrity-checked.
    Falls back to a whole-file search (with fenced code stripped) only when no
    field is present. Returns the evidence-tag token (e.g. '[POC-PASS]') or "".
    """
    if not verify_path.exists():
        return ""
    try:
        text = verify_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # 1. Verifier's own claim (Evidence/Preferred Tag) — highest priority.
    for fm in _CLAIM_FIELD_RE.finditer(text):
        tm = _PROSE_TAG_RE.search(fm.group(1))
        if tm:
            return tm.group(0).upper()
    # 2. Driver-stamped Mechanical-Tag field (only if no verifier claim found).
    for fm in _MECH_TAG_FIELD_RE.finditer(text):
        tm = _PROSE_TAG_RE.search(fm.group(1))
        if tm:
            return tm.group(0).upper()
    # 3. Fallback: whole-file, but ignore tags inside fenced code blocks.
    stripped = _FENCED_CODE_RE.sub(" ", text)
    m = _PROSE_TAG_RE.search(stripped)
    return m.group(0).upper() if m else ""


# ---------------------------------------------------------------------------
# Harm-assertion detection (v2.8.17)
#
# A NO_TEST_FILE status means the mechanical layer could not re-locate / re-run
# the test file — a harness/file-location failure, NOT proof the exploit is
# false. When the verifier actually wrote a real asserting PoC, that finding
# must not be severity-capped to [CODE-TRACE] on a tooling gap. This detector
# recognizes whether the verify prose contains an explicit harm assertion,
# INCLUDING revert / error-expectation forms that narrow positive-assertion
# vocabularies miss (e.g. `try_foo(..).is_err()`). It is strictly ADDITIVE:
# it can only REDUCE false "no assertion" downgrades, never introduce one.
# ---------------------------------------------------------------------------

_HARM_ASSERTION_RE = re.compile(
    "|".join((
        # Revert / error-expectation forms (Rust / Soroban + generic)
        r"\.is_err\s*\(\s*\)",            # x.is_err() / try_foo(..).is_err()
        r"\.is_ok\s*\(\s*\)",             # x.is_ok() on a call result
        r"\.expect_err\s*\(",             # x.expect_err("...")
        r"\.unwrap_err\s*\(\s*\)",        # x.unwrap_err()
        r"#\[\s*should_panic",            # #[should_panic] / #[should_panic(expected=..)]
        r"\bshould_panic\s*\(",           # should_panic(expected = ...)
        r"\bmatches!\s*\([^)]*\bErr\b",   # matches!(x, Err(..))
        r"==\s*Err\s*\(",                 # x == Err(..)
        # Generic positive assertions (existing recognized forms — kept)
        r"\bassert!\s*\(",                # assert!(..) incl. assert!(x.is_err())
        r"\bassert_eq!\s*\(",             # assert_eq!(x, Err(..))
        r"\bassert_ne!\s*\(",
        r"\bassertEq\b", r"\bassertTrue\b", r"\bassertFalse\b",
        r"\bassertGt\b", r"\bassertLt\b",
        r"\bexpectRevert\b",              # Foundry vm.expectRevert
        r"\bassert\.[A-Za-z]",            # Go testify assert.X
        r"\brequire\.[A-Za-z]",           # Go testify require.X
    ))
)


def _contains_harm_assertion(text: str) -> bool:
    """True if `text` contains an explicit harm/error-expectation assertion.

    Recognizes revert/error-expectation assertions (`.is_err()`,
    `#[should_panic]`, `.expect_err(..)`, `.unwrap_err()`, `matches!(.., Err(..))`,
    `assert_eq!(.., Err(..))`) in addition to positive assertion forms. Scans the
    whole verify prose (fenced code blocks included) — a real PoC snippet may
    live either inside or outside a code fence in a verify_<ID>.md file.
    """
    if not text:
        return False
    return bool(_HARM_ASSERTION_RE.search(text))


def _read_verify_text(verify_path: Path) -> str:
    """Best-effort read of a verify_<ID>.md file; '' on any error/absence."""
    try:
        return verify_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _classify_integrity(prose_tag: str, mechanical_status: str,
                        verify_text: str = "") -> tuple[str, str]:
    """v2.0.8 (P3.1): given the verifier prose tag and the mechanical
    execution status, return (integrity_state, effective_tag).

    States:
      - CONSISTENT: prose tag matches mechanical reality.
      - INFLATED_PROSE: prose claims proof-grade evidence
        ([POC-PASS]/[MEDUSA-PASS]/etc.) but mechanical did NOT confirm
        (NO_TEST_FILE / FAIL / COMPILE_FAIL / TIMEOUT) AND the verify prose
        carries no explicit harm assertion. Effective tag forced to
        [CODE-TRACE] with [INTEGRITY-DOWNGRADE] flag.
      - POC_UNVERIFIED_HARNESS (v2.8.17): prose claims proof-grade evidence,
        the verify prose DOES contain an explicit harm assertion, but the
        mechanical layer hit a NO_TEST_FILE harness/file-location failure.
        This is a tooling gap, not disproof — the effective tag PRESERVES the
        upstream severity (keeps the prose tag) and adds
        `[POC-UNVERIFIED-HARNESS] [NEEDS-BUILD]`. It carries NO
        [INTEGRITY-DOWNGRADE], so the driver's verdict-flip (gated on
        INFLATED_PROSE) leaves CONFIRMED intact.
      - MECHANICAL_UNAVAILABLE: no mechanical record (finding not in
        manifest, or toolchain unavailable). Effective tag = prose tag
        with [MECHANICAL-UNAVAILABLE] flag.

    `verify_text` is the verify_<ID>.md prose (optional for back-compat; when
    empty the harness carve-out cannot fire and behavior is unchanged).
    """
    prose_upper = (prose_tag or "").upper()
    status = (mechanical_status or "").upper()
    prose_is_proof = prose_upper in {t.upper() for t in _PROOF_EVIDENCE_TAGS}

    if status in ("TOOLCHAIN_UNAVAILABLE", "SKIPPED", "AMBIGUOUS"):
        # Mechanical layer was unavailable, or (VERIF-5) AMBIGUOUS = a whole-file
        # run with mixed pass/fail that cannot be attributed to THIS finding.
        # Preserve prose with a flag -- do NOT treat as INFLATED_PROSE, which
        # would wrongly demote a true positive on an unrelated test's failure.
        effective = prose_tag or "[CODE-TRACE]"
        return ("MECHANICAL_UNAVAILABLE",
                f"{effective} [MECHANICAL-UNAVAILABLE]")
    if status == "PASS":
        # Mechanical confirmed PASS. If prose also claimed proof → CONSISTENT.
        if prose_is_proof:
            return ("CONSISTENT", prose_tag)
        # Prose was conservative (e.g., [CODE-TRACE]) but mechanical
        # actually passed. Upgrade effective_tag to [POC-PASS] —
        # mechanical truth wins.
        return ("CONSISTENT", "[POC-PASS]")
    if status in ("FAIL",):
        # Mechanical FAILED; verifier shouldn't have claimed proof-grade.
        if prose_is_proof:
            return ("INFLATED_PROSE",
                    "[CODE-TRACE] [INTEGRITY-DOWNGRADE]")
        return ("CONSISTENT", "[POC-FAIL]")
    # NO_TEST_FILE / NO_TEST_MATCH / COMPILE_FAIL / TIMEOUT / BUILD_FAILED /
    # EXEC_ERROR — mechanical did NOT confirm proof.
    if prose_is_proof:
        # v2.8.17 harness-failure carve-out: NO_TEST_FILE is a file-location /
        # harness failure, NOT evidence the exploit is false. When the verifier
        # DID write a real asserting PoC (a recognized harm assertion — incl.
        # revert/error-expectation forms — is present in the verify prose) and
        # claimed proof-grade evidence, demoting to [CODE-TRACE] wrongly caps
        # severity on a tooling gap. Emit a DISTINCT, non-severity-capping
        # disposition that PRESERVES the upstream severity and routes to a build
        # re-run. It carries no [INTEGRITY-DOWNGRADE], so the driver's verdict
        # flip (gated on INFLATED_PROSE) leaves CONFIRMED intact.
        if status == "NO_TEST_FILE" and _contains_harm_assertion(verify_text):
            return ("POC_UNVERIFIED_HARNESS",
                    f"{prose_tag} [POC-UNVERIFIED-HARNESS] [NEEDS-BUILD]")
        # Codex Point 5: the canonical phantom-[POC-PASS] downgrade case
        # (assertion-less prose, or a genuine mechanical FAIL/COMPILE/TIMEOUT).
        return ("INFLATED_PROSE",
                "[CODE-TRACE] [INTEGRITY-DOWNGRADE]")
    # Prose was honest about not having proof; preserve it.
    return ("CONSISTENT", prose_tag or "[CODE-TRACE]")


_VERDICT_CONFIRMED_FIELD_RE = re.compile(
    r"(?im)^([-*>\s`_]*Verdict[*>\s`_]*:\s*)CONFIRMED\b"
    r"(?!\s*\[INTEGRITY-DOWNGRADE\])"
)


def flip_verdict_on_integrity_downgrade(text: str) -> tuple[str, bool]:
    """v2.8.16 Phase 1 (#3a): flip a verifier's `**Verdict**: CONFIRMED` line to
    `CONTESTED [INTEGRITY-DOWNGRADE]`.

    Demoting the Evidence Tag alone does not reach the report — the report Index
    Agent sets the VERIFIED column from the verifier's `Verdict:` line. When the
    mechanical layer classifies a finding INFLATED_PROSE (prose claimed
    proof-grade evidence the run did not confirm), the driver calls this so a
    mechanically-disproven exploit can never ship as a verified-Critical.

    Only the Verdict FIELD line is rewritten (anchored, multiline) — prose
    mentions of the word "CONFIRMED" elsewhere are left untouched. Idempotent:
    an already-downgraded line is not matched again. Returns (new_text, changed).
    """
    new_text, n = _VERDICT_CONFIRMED_FIELD_RE.subn(
        r"\1CONTESTED [INTEGRITY-DOWNGRADE]", text
    )
    return new_text, (n > 0)


def _write_verdict_manifest(results: list, scratchpad: Path) -> None:
    """v2.0.8 (P3.1): write `verdict_manifest.json` from the mechanical
    verify results + each verify_<ID>.md prose Evidence Tag.

    Schema: `plamen.verdict_manifest.v1`. Downstream consumers (skeptic-
    judge, report_index) MUST read `effective_tag` from this manifest
    rather than the verifier's prose claim, which can be inflated.
    """
    verdicts = []
    for r in results:
        verify_path = scratchpad / r.verify_file
        prose_tag = _extract_verifier_prose_tag(verify_path)
        verify_text = _read_verify_text(verify_path)
        integrity_state, effective_tag = _classify_integrity(
            prose_tag, r.status, verify_text
        )
        verdicts.append({
            "finding_id": r.finding_id or "",
            "verify_file": r.verify_file,
            "mechanical_status": r.status,
            "verifier_prose_tag": prose_tag,
            "integrity_state": integrity_state,
            "effective_tag": effective_tag,
        })
    payload = {
        "schema_version": "plamen.verdict_manifest.v1",
        "mechanical_source": "mechanical_verify_manifest.md",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "row_count": len(verdicts),
        "verdicts": verdicts,
    }
    out = scratchpad / "verdict_manifest.json"
    try:
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(out)
    except OSError:
        pass


def read_verdict_manifest(scratchpad: Path) -> list[dict]:
    """v2.0.8 (P3.1): read `verdict_manifest.json` if present and valid.

    Returns the `verdicts` list (or [] on absent / malformed file).
    Skeptic-judge and report_index consume this in preference to the
    verifier's prose Evidence Tag.
    """
    path = scratchpad / "verdict_manifest.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("schema_version") != "plamen.verdict_manifest.v1":
        return []
    verdicts = payload.get("verdicts")
    if not isinstance(verdicts, list):
        return []
    return verdicts


# ---------------------------------------------------------------------------
# Driver entry point
# ---------------------------------------------------------------------------


def _prewarm_build(build_root: Path, language: str, registry: dict,
                   timeout_s: int) -> tuple[bool, str]:
    """One-time best-effort compile from the build root to WARM the build cache
    before the per-finding test loop.

    Without this, the first `forge test` / `cargo test` on a COLD cache must do
    the whole-project (often `--via-ir`) compile inside its own per-test budget
    and TIMEOUTs on a dependency-heavy repo — capping every finding at
    [CODE-TRACE] instead of [POC-PASS]. A warm cache makes each subsequent test
    an incremental (seconds) build.

    Best-effort and NON-FATAL: any failure/timeout just leaves the cache as-is
    and the loop proceeds exactly as before (the per-test build then behaves as
    it did pre-fix). Never raises."""
    try:
        env = os.environ.copy()
        if language == "evm":
            cmd = [shutil.which("forge") or "forge", "build"]
        else:
            lang_cfg = registry.get(language) or {}
            build_cmd = str(lang_cfg.get("build_command") or "").strip()
            if not build_cmd:
                return (False, f"no build_command for '{language}' — pre-warm skipped")
            cmd = build_cmd.split()
            resolved = shutil.which(cmd[0])
            if resolved:
                cmd[0] = resolved
        t0 = time.time()
        proc = subprocess.run(
            cmd, cwd=str(build_root), capture_output=True, text=True,
            timeout=max(1, int(timeout_s)), shell=False, env=env,
        )
        dt = time.time() - t0
        if proc.returncode == 0:
            return (True, f"warm ok (rc=0) in {dt:.0f}s")
        # A non-zero build here is informative but not fatal — the per-test run
        # still tries (a scoped compile can succeed where a whole-project one
        # fails), and the classifier handles COMPILE_FAIL.
        return (False, f"build rc={proc.returncode} in {dt:.0f}s (cache left as-is)")
    except subprocess.TimeoutExpired:
        return (False, f"pre-warm build exceeded {timeout_s}s (cache left as-is)")
    except Exception as exc:  # never let cache-warming break verification
        return (False, f"pre-warm build error: {exc}")


def run_phase5b_mechanical_verify(scratchpad: Path, project_root: Path,
                                  language: str, *,
                                  per_test_timeout_s: Optional[int] = None,
                                  phase_budget_s: Optional[int] = None,
                                  registry: Optional[dict] = None) -> dict:
    """Execute mechanical PoC verification for every verify_*.md in scratchpad.

    Returns a summary dict (also written to mechanical_verify_manifest.json):

      {
        "status": "ok" | "no_verify_files" | "toolchain_unavailable",
        "counts": {PASS: N, FAIL: N, ...},
        "files_annotated": N,
        "elapsed_s": float,
      }

    Never raises. Phase failure is captured in the returned status; the driver
    chooses to mark the phase DEGRADED (warning) rather than HALT.
    """
    per_test_timeout_s = per_test_timeout_s or _DEFAULT_PER_TEST_TIMEOUT_S
    phase_budget_s = phase_budget_s or _DEFAULT_PHASE_BUDGET_S
    registry = registry or _load_registry()

    # Resolve actual language (caller may pass empty string when config absent)
    lang = (language or "").lower().strip()
    if not lang:
        lang = "evm"  # back-compat default
    elif lang in ("go", "rust"):
        # v2.8.16 Phase 1 (#0a): L1 config stores the raw language `go`/`rust`,
        # but the toolchain registry + manifest tables key on `l1_go`/`l1_rust`.
        # Without this remap _toolchain_binary_for("rust")="" and the registry
        # lookup misses → every L1 finding returns TOOLCHAIN_UNAVAILABLE and
        # L1 mechanical verify is silently dead. Normalize at the single
        # dispatch surface so every caller benefits.
        lang = "l1_go" if lang == "go" else "l1_rust"

    skip_names = {
        "verify_core.md", "verify_core_full.md", "verify_aggregate.md",
    }
    verify_files = sorted(
        f for f in scratchpad.glob("verify_*.md")
        if f.name not in skip_names
    )
    if not verify_files:
        _write_manifest([], scratchpad)
        return {"status": "no_verify_files", "counts": {}, "files_annotated": 0,
                "elapsed_s": 0.0}

    # Toolchain pre-check — if the binary is absent, short-circuit gracefully.
    bin_name = _toolchain_binary_for(lang)
    if bin_name and shutil.which(bin_name) is None:
        results = [
            ExecResult(verify_file=f.name, finding_id=f.stem.replace("verify_", ""),
                       language=lang, status="TOOLCHAIN_UNAVAILABLE",
                       stdout_tail=f"{bin_name} not on PATH")
            for f in verify_files
        ]
        _write_manifest(results, scratchpad)
        return {"status": "toolchain_unavailable", "counts": {"TOOLCHAIN_UNAVAILABLE": len(results)},
                "files_annotated": 0, "elapsed_s": 0.0}

    # Resolve the build root once — the directory that owns the build
    # manifest (foundry.toml etc.), which is often a PARENT of the audit
    # scope dir. Test files and `test/` live here, not under project_root.
    # Recon's authoritative chosen build root (from build_status.md) wins when
    # present — the heuristic is the fallback for runs where recon emitted no
    # (or a stale) choice.
    build_root = _read_recon_build_root(scratchpad, lang) or _find_build_root(
        Path(project_root), lang
    )

    # Warm the build cache ONCE before the per-finding loop. A cold, dependency-
    # heavy (`--via-ir`) repo cannot compile inside a single per-test budget, so
    # without this every finding TIMEOUTs and caps at [CODE-TRACE]; a warm cache
    # makes each test an incremental build. Best-effort / non-fatal.
    prewarm_ok, prewarm_note = _prewarm_build(
        build_root, lang, registry, _DEFAULT_BUILD_TIMEOUT_S)

    results: list[ExecResult] = []
    annotated = 0
    t_start = time.time()
    for vf in verify_files:
        if time.time() - t_start > phase_budget_s:
            results.append(ExecResult(
                verify_file=vf.name,
                finding_id=vf.stem.replace("verify_", ""),
                language=lang,
                status="SKIPPED",
                stdout_tail="phase budget exhausted",
            ))
            continue
        r = _run_test_for_finding(
            vf, build_root, lang, registry, per_test_timeout_s,
            project_root=Path(project_root),
        )
        r.recommended_tag = _recommended_tag(r.status)
        if _annotate_verify_file(vf, r):
            annotated += 1
        results.append(r)

    _write_manifest(results, scratchpad)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {
        "status": "ok",
        "counts": counts,
        "files_annotated": annotated,
        "build_root": str(build_root),
        "prewarm_ok": prewarm_ok,
        "prewarm_note": prewarm_note,
        "elapsed_s": time.time() - t_start,
    }


__all__ = [
    "ExecResult",
    "run_phase5b_mechanical_verify",
    "_load_registry",
    "_ensure_l1_registry_entries",
    "_find_build_root",
    "_read_recon_build_root",
    "_format_test_command",
    "_classify_non_evm_outcome",
    "_classify_evm_outcome",
    "_recommended_tag",
    "flip_verdict_on_integrity_downgrade",
    "read_verdict_manifest",
]
