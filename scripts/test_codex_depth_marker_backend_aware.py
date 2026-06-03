"""Codex depth must not be false-failed for lacking the PLAMEN_STATUS marker.

Codex runs each phase as a single `codex exec` subprocess (NOT a PTY worker
pool), so it never writes the `<!-- PLAMEN_STATUS: COMPLETE -->` marker. The
gate's "unmarked on a fresh audit => IN_PROGRESS (a worker is still writing)"
classification is a PTY worker-pool assumption — correct for a live Claude
worker, wrong for a RETURNED Codex subprocess whose files are final. This caused
Codex L1 Thorough to false-fail depth ("manifest-exact incomplete; in_progress:
the 5 depth files").

Fix: _classify_artifact_row reads cli_backend from {scratchpad}/config.json. For
codex, unmarked-but-substantive => tolerated LEGACY_UNMARKED (non-blocking).
Claude keeps strict fresh-audit IN_PROGRESS. Missing / stub / explicit-marked-
IN_PROGRESS still block for BOTH backends (silent-incomplete protection).
"""
import json
from pathlib import Path

import plamen_validators as V


def _cfg(sp: Path, backend: str | None) -> None:
    if backend is None:
        return
    (sp / "config.json").write_text(
        json.dumps({"cli_backend": backend}), encoding="utf-8"
    )


def _substantive(sp: Path, name: str, marker: str = "") -> Path:
    p = sp / name
    body = (
        "# Findings\n\n## [INV-01] concrete depth finding\n\n"
        "Substantive analysis with file:line references, well over the byte "
        "minimum for the gate. " * 4
    )
    if marker:
        body += f"\n\n<!-- PLAMEN_STATUS: {marker} -->\n"
    p.write_text(body, encoding="utf-8")
    return p


def _classify(p: Path, fresh: bool = True):
    return V._classify_artifact_row(
        p, fresh_audit=fresh, min_bytes=100, structural_kwargs={}
    )


def test_codex_fresh_unmarked_is_tolerated_not_in_progress(tmp_path):
    _cfg(tmp_path, "codex")
    p = _substantive(tmp_path, "depth_consensus_invariant_findings.md")
    status, _ = _classify(p, fresh=True)
    assert status == V._BREADTH_STATUS_LEGACY_UNMARKED  # non-blocking
    assert status != V._BREADTH_STATUS_IN_PROGRESS


def test_claude_fresh_unmarked_still_in_progress(tmp_path):
    _cfg(tmp_path, "claude")
    p = _substantive(tmp_path, "depth_consensus_invariant_findings.md")
    status, reasons = _classify(p, fresh=True)
    assert status == V._BREADTH_STATUS_IN_PROGRESS  # Claude unchanged
    assert "legacy-unmarked on fresh audit" in reasons


def test_no_config_defaults_to_strict_claude(tmp_path):
    # absent config.json => default 'claude' => strict (safe default)
    p = _substantive(tmp_path, "depth_consensus_invariant_findings.md")
    status, _ = _classify(p, fresh=True)
    assert status == V._BREADTH_STATUS_IN_PROGRESS


def test_codex_missing_file_still_blocks(tmp_path):
    _cfg(tmp_path, "codex")
    status, _ = _classify(tmp_path / "depth_absent_findings.md", fresh=True)
    assert status == V._BREADTH_STATUS_MISSING


def test_codex_stub_file_still_blocks(tmp_path):
    _cfg(tmp_path, "codex")
    p = tmp_path / "depth_stub_findings.md"
    p.write_text("tiny", encoding="utf-8")  # < 100 bytes
    status, _ = _classify(p, fresh=True)
    assert status == V._BREADTH_STATUS_STUB


def test_codex_explicit_in_progress_marker_still_blocks(tmp_path):
    _cfg(tmp_path, "codex")
    p = _substantive(tmp_path, "depth_marked_findings.md", marker="IN_PROGRESS")
    status, _ = _classify(p, fresh=True)
    # explicit non-COMPLETE marker is NOT "legacy-unmarked" -> still blocks
    assert status == V._BREADTH_STATUS_IN_PROGRESS


def test_codex_marked_file_not_caught_by_legacy_unmarked_branch(tmp_path):
    # A file carrying a PLAMEN marker is NOT "legacy-unmarked", so the codex
    # exemption never applies to it — it flows through the normal completeness
    # path. On a resumed scratchpad a COMPLETE marker yields COMPLETE; the point
    # is the codex branch only ever touches MARKER-ABSENT files.
    _cfg(tmp_path, "codex")
    p = _substantive(tmp_path, "depth_done_findings.md", marker="COMPLETE")
    status, _ = _classify(p, fresh=False)
    assert status == V._BREADTH_STATUS_COMPLETE
    assert status != V._BREADTH_STATUS_LEGACY_UNMARKED
