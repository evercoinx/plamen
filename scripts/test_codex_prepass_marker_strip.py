"""2026-06-02: Codex recon pre-pass marker strip.

Root cause: the recon content gate (`_validate_recon_content_structure`) treats
a surviving line-1 `_PREPASS_MARKER` as proof that recon produced no durable
canonical handoff. That proxy holds under Claude's whole-file `Write` (line 1
always replaced) but NOT under Codex's `apply_patch`, which makes targeted body
edits and leaves line 1 — so a legitimately-enriched file keeps the marker and
false-fails the gate, burning a full recon retry every Codex run.

Fix: `strip_codex_prepass_markers` drops the line-1 marker on Codex when the
body holds real content, while keeping it on pure `[LLM TO ENRICH]` placeholders
so a genuinely-empty recon still fails the gate.
"""
from __future__ import annotations

from pathlib import Path

import plamen_mechanical as M
from plamen_mechanical import _PREPASS_MARKER, strip_codex_prepass_markers
from plamen_validators import _validate_recon_content_structure


def _write(scratch: Path, name: str, body: str, *, marker: bool = True) -> Path:
    p = scratch / name
    content = (_PREPASS_MARKER + "\n" + body) if marker else body
    p.write_text(content, encoding="utf-8")
    return p


_REAL_STATEVARS = (
    "# State Variables\n\n"
    "Pre-pass: 3 state(s) identified via regex scan.\n"
    "Regex-based heuristic — LLM recon may add/correct entries.\n\n"
    "| File | Variable | Type | Line |\n"
    "|------|----------|------|------|\n"
    "| Vault.sol | totalAssets | uint256 | 12 |\n"
    "| Vault.sol | owner | address | 14 |\n"
)


# ---------- mechanically-populated files: marker stripped, content kept ----------

def test_real_table_marker_stripped(tmp_path):
    _write(tmp_path, "state_variables.md", _REAL_STATEVARS)
    stripped = strip_codex_prepass_markers(tmp_path)
    assert "state_variables.md" in stripped
    text = (tmp_path / "state_variables.md").read_text(encoding="utf-8")
    assert text.splitlines()[:1] != [_PREPASS_MARKER]
    # content survives
    assert "totalAssets" in text and "| File | Variable | Type | Line |" in text


def test_gate_passes_after_strip(tmp_path):
    # Before strip: gate flags the marker. After strip: that flag is gone.
    _write(tmp_path, "function_list.md", _REAL_STATEVARS)
    hard_before, _ = _validate_recon_content_structure(tmp_path, backend="codex")
    assert any("function_list.md" in h and "pre-pass overwrite marker" in h
               for h in hard_before)
    strip_codex_prepass_markers(tmp_path)
    hard_after, _ = _validate_recon_content_structure(tmp_path, backend="codex")
    assert not any("function_list.md" in h and "pre-pass overwrite marker" in h
                   for h in hard_after)


def test_llm_enriched_body_stripped(tmp_path):
    body = "# Contract Inventory\n\nThe vault holds depositor assets and ...\n\n| A | B |\n|---|---|\n| x | y |\n"
    _write(tmp_path, "contract_inventory.md", body)
    stripped = strip_codex_prepass_markers(tmp_path)
    assert "contract_inventory.md" in stripped


# ---------- placeholders / empty: marker KEPT so gate still fails ----------

def test_placeholder_marker_kept(tmp_path):
    _write(tmp_path, "state_variables.md",
           "# State Variables\n\n[LLM TO ENRICH] pre-pass failed: boom\n")
    stripped = strip_codex_prepass_markers(tmp_path)
    assert "state_variables.md" not in stripped
    assert (tmp_path / "state_variables.md").read_text(
        encoding="utf-8").splitlines()[:1] == [_PREPASS_MARKER]


def test_placeholder_still_fails_gate(tmp_path):
    _write(tmp_path, "function_list.md",
           "# Function List\n\n[LLM TO ENRICH] Unknown language: xyz\n")
    strip_codex_prepass_markers(tmp_path)
    hard, _ = _validate_recon_content_structure(tmp_path, backend="codex")
    assert any("function_list.md" in h and "pre-pass overwrite marker" in h
               for h in hard)


def test_empty_body_marker_kept(tmp_path):
    p = tmp_path / "detected_patterns.md"
    p.write_text(_PREPASS_MARKER + "\n", encoding="utf-8")
    stripped = strip_codex_prepass_markers(tmp_path)
    assert "detected_patterns.md" not in stripped
    assert p.read_text(encoding="utf-8").splitlines()[:1] == [_PREPASS_MARKER]


# ---------- no-marker (Claude Write path): untouched ----------

def test_no_marker_untouched(tmp_path):
    body = _REAL_STATEVARS
    _write(tmp_path, "attack_surface.md", body, marker=False)
    before = (tmp_path / "attack_surface.md").read_text(encoding="utf-8")
    stripped = strip_codex_prepass_markers(tmp_path)
    assert "attack_surface.md" not in stripped
    assert (tmp_path / "attack_surface.md").read_text(encoding="utf-8") == before


def test_missing_files_no_crash(tmp_path):
    # empty scratchpad -> empty result, no exception
    assert strip_codex_prepass_markers(tmp_path) == []


# ---------- multi-file: mixed real + placeholder ----------

def test_mixed_set(tmp_path):
    _write(tmp_path, "state_variables.md", _REAL_STATEVARS)              # real -> strip
    _write(tmp_path, "function_list.md", _REAL_STATEVARS)               # real -> strip
    _write(tmp_path, "design_context.md",
           "# Design\n\n[LLM TO ENRICH] Pre-pass stub.\n")              # placeholder -> keep
    _write(tmp_path, "build_status.md",
           "# Build\n\nForge build OK\nslither: 0 issues\n")            # real -> strip
    stripped = set(strip_codex_prepass_markers(tmp_path))
    assert {"state_variables.md", "function_list.md", "build_status.md"} <= stripped
    assert "design_context.md" not in stripped


# ---------- export surface ----------

def test_helper_is_exported(tmp_path):
    assert "strip_codex_prepass_markers" in M.__all__
