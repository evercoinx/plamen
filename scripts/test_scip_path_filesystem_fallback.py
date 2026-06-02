"""Tests for the SCIP path-resolution gate filesystem-existence fallback.

Background: `_validate_cited_paths_in_verify()` flags verify_*.md findings
whose **Location** cited path does not resolve against the SCIP-indexed file
set (`scratchpad/scip/repo_map.md`). SCIP coverage (e.g. rust-analyzer scip)
is frequently incomplete and skips whole crates, so a cited path absent from
the SCIP map is NOT proof of hallucination. The gate now falls back to a
filesystem-existence check against the project/repo root: a path that exists
on disk is RESOLVED even if SCIP never indexed it. Only paths absent from
BOTH the SCIP map AND the filesystem stay flagged.

These tests reproduce the confirmed Irys L1 false-rejection (verify_INV-007/
012/016 citing crates/p2p/src/{gossip_data_handler,gossip_service,cache}.rs,
present on disk but absent from the partial SCIP map) and assert hallucination
detection is preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plamen_validators as V  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────

def _mkscratch(tmp_path: Path) -> Path:
    s = tmp_path / ".scratchpad"
    s.mkdir()
    return s


def _write_repo_map(scratchpad: Path, rel_paths: list[str]) -> None:
    """Write a minimal SCIP repo_map.md with one H2 header per indexed file."""
    scip = scratchpad / "scip"
    scip.mkdir(exist_ok=True)
    lines = ["# SCIP Repo Map", ""]
    for p in rel_paths:
        lines.append(f"## {p}")
        lines.append("")
    (scip / "repo_map.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_verify(scratchpad: Path, name: str, cited_path: str) -> None:
    """Write a minimal verify_*.md citing a Location path."""
    text = (
        f"# Verify {name}\n\n"
        f"**Verdict**: CONFIRMED\n"
        f"**Severity**: High\n"
        f"**Location**: `{cited_path}`\n\n"
        f"Some analysis here.\n"
    )
    (scratchpad / f"verify_{name}.md").write_text(text, encoding="utf-8")


def _touch_repo_file(project_root: Path, rel_path: str) -> None:
    """Create a real source file on disk under the project root."""
    fp = project_root / rel_path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("// source\n", encoding="utf-8")


# ── (1) cited path NOT in SCIP map but EXISTS on disk -> RESOLVED ─────────

def test_path_absent_from_scip_but_exists_on_disk_resolves(tmp_path):
    scratchpad = _mkscratch(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    # SCIP indexed something unrelated (partial coverage — did NOT index p2p).
    _write_repo_map(scratchpad, ["crates/storage/src/lib.rs"])

    # The cited file exists on disk but was not indexed by SCIP.
    rel = "crates/p2p/src/gossip_service.rs"
    _touch_repo_file(project_root, rel)
    _write_verify(scratchpad, "INV-012", rel)

    issues = V._validate_cited_paths_in_verify(scratchpad, str(project_root))

    assert issues == [], f"expected RESOLVED via filesystem, got: {issues}"
    # Gate must NOT persist a path_unresolved.md for a resolved path.
    assert not (scratchpad / "path_unresolved.md").exists()


def test_irys_three_files_present_on_disk_all_resolve(tmp_path):
    """The exact confirmed Irys regression: 3 verify shards previously
    degraded because their cited p2p files were absent from the partial
    SCIP map. All three exist on disk -> path sub-check now passes."""
    scratchpad = _mkscratch(tmp_path)
    project_root = tmp_path / "irys"
    project_root.mkdir()

    _write_repo_map(scratchpad, ["crates/storage/src/lib.rs"])  # no p2p

    citations = {
        "INV-007": "crates/p2p/src/gossip_data_handler.rs",
        "INV-012": "crates/p2p/src/gossip_service.rs",
        "INV-016": "crates/p2p/src/cache.rs",
    }
    for name, rel in citations.items():
        _touch_repo_file(project_root, rel)
        _write_verify(scratchpad, name, rel)

    issues = V._validate_cited_paths_in_verify(scratchpad, str(project_root))

    assert issues == [], f"expected all 3 resolved on disk, got: {issues}"
    assert not (scratchpad / "path_unresolved.md").exists()


# ── (2) cited path absent from BOTH SCIP map AND disk -> STILL flagged ────

def test_path_absent_from_scip_and_disk_stays_unresolved(tmp_path):
    scratchpad = _mkscratch(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    _write_repo_map(scratchpad, ["crates/storage/src/lib.rs"])

    # Genuine hallucination: not in SCIP, not on disk.
    rel = "crates/ghost/src/does_not_exist.rs"
    _write_verify(scratchpad, "HALL-1", rel)

    issues = V._validate_cited_paths_in_verify(scratchpad, str(project_root))

    assert issues, "genuine hallucination must still be flagged"
    assert "path unresolved" in issues[0]
    assert (scratchpad / "path_unresolved.md").exists()
    pu = (scratchpad / "path_unresolved.md").read_text(encoding="utf-8")
    assert rel in pu


def test_hallucination_detection_preserved_when_no_project_root(tmp_path):
    """Without project_root the gate behaves as before: SCIP-only check.
    A path absent from SCIP is flagged (no filesystem fallback available)."""
    scratchpad = _mkscratch(tmp_path)

    _write_repo_map(scratchpad, ["crates/storage/src/lib.rs"])
    rel = "crates/p2p/src/gossip_service.rs"
    _write_verify(scratchpad, "INV-012", rel)

    # No project_root passed -> no filesystem fallback -> flagged.
    issues = V._validate_cited_paths_in_verify(scratchpad)

    assert issues, "without project_root, SCIP-only behavior is preserved"
    assert "path unresolved" in issues[0]


# ── (3) cited path present in SCIP map -> resolved as before (unchanged) ──

def test_path_in_scip_map_resolves_unchanged(tmp_path):
    scratchpad = _mkscratch(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    rel = "crates/p2p/src/gossip_service.rs"
    _write_repo_map(scratchpad, [rel])  # SCIP indexed it
    # Note: file NOT on disk — proves SCIP path alone resolves.
    _write_verify(scratchpad, "INV-012", rel)

    issues = V._validate_cited_paths_in_verify(scratchpad, str(project_root))

    assert issues == [], f"SCIP-indexed path must resolve, got: {issues}"


def test_basename_match_still_resolves(tmp_path):
    """Lenient basename fallback (b) remains intact."""
    scratchpad = _mkscratch(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    _write_repo_map(scratchpad, ["crates/p2p/src/gossip_service.rs"])
    # Cited under a different dir but same basename -> basename match.
    _write_verify(scratchpad, "INV-012", "other/path/gossip_service.rs")

    issues = V._validate_cited_paths_in_verify(scratchpad, str(project_root))

    assert issues == [], f"basename match must resolve, got: {issues}"


# ── (4) relative-path resolution against project root variants ───────────

def test_resolves_against_project_root_parent(tmp_path):
    """If the SCIP index/build root is project_root's parent (monorepo
    layout where cited paths are relative to the workspace root), the
    fallback still resolves by trying the parent root."""
    scratchpad = _mkscratch(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project_root = workspace / "subproject"
    project_root.mkdir()

    _write_repo_map(scratchpad, ["crates/storage/src/lib.rs"])

    # File lives under the workspace (parent of project_root).
    rel = "crates/p2p/src/cache.rs"
    _touch_repo_file(workspace, rel)
    _write_verify(scratchpad, "INV-016", rel)

    issues = V._validate_cited_paths_in_verify(scratchpad, str(project_root))

    assert issues == [], f"expected resolution against parent root, got: {issues}"


def test_leading_slash_cited_path_normalized(tmp_path):
    """A cited path with a leading slash is normalized before disk check."""
    scratchpad = _mkscratch(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    _write_repo_map(scratchpad, ["crates/storage/src/lib.rs"])
    rel = "crates/p2p/src/gossip_data_handler.rs"
    _touch_repo_file(project_root, rel)
    # Verifier cites with a leading slash (repo-relative, slash-prefixed).
    # path_with_line_re only matches paths starting with [a-zA-Z0-9_./-],
    # and a leading "/" is in that class, so this exercises lstrip("/").
    _write_verify(scratchpad, "INV-007", "/" + rel)

    issues = V._validate_cited_paths_in_verify(scratchpad, str(project_root))

    assert issues == [], f"leading-slash path should resolve on disk, got: {issues}"


# ── (5) mixed shard: existing files resolve, hallucination still flagged ──

def test_mixed_shard_only_hallucination_flagged(tmp_path):
    scratchpad = _mkscratch(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()

    _write_repo_map(scratchpad, ["crates/storage/src/lib.rs"])

    # Two real-on-disk (SCIP-missed) + one genuine hallucination.
    real1 = "crates/p2p/src/gossip_service.rs"
    real2 = "crates/p2p/src/cache.rs"
    hall = "crates/ghost/src/phantom.rs"
    _touch_repo_file(project_root, real1)
    _touch_repo_file(project_root, real2)
    _write_verify(scratchpad, "INV-012", real1)
    _write_verify(scratchpad, "INV-016", real2)
    _write_verify(scratchpad, "HALL-9", hall)

    issues = V._validate_cited_paths_in_verify(scratchpad, str(project_root))

    assert issues, "the genuine hallucination must still be flagged"
    pu = (scratchpad / "path_unresolved.md").read_text(encoding="utf-8")
    assert hall in pu
    assert real1 not in pu
    assert real2 not in pu
