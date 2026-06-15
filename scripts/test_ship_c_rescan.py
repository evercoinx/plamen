"""Ship C — rescan manifest hyphen/dot filename fix (SW04-4).

The Ship 8.17 exact gate parsed declared output filenames with `[A-Za-z0-9_]+`,
silently dropping hyphenated/dotted per-contract names
(analysis_percontract_core-vault.md, analysis_percontract_v1.2.md). When ALL
declared names were non-matching the gate fell back to the permissive glob,
defeating the exact gate. Ship C accepts `-`/`.` so declared names are honored.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_driver as D  # noqa: E402
from plamen_validators import (  # noqa: E402
    gate_passes, _parse_rescan_manifest_files, _rescan_manifest_exact_missing,
)

RESCAN = next(p for p in D.SC_PHASES if p.name == "rescan")
SUB = "z" * 300


def _manifest(sp: Path, files: list[str]):
    body = "# Rescan Manifest\n" + "".join(f"- {f}\n" for f in files)
    (sp / "rescan_manifest.md").write_text(body, encoding="utf-8")


def test_parser_accepts_hyphen_and_dot():
    text = ("- analysis_rescan_1.md\n"
            "- analysis_percontract_core-vault.md\n"
            "- analysis_percontract_v1.2.md\n")
    files = _parse_rescan_manifest_files(text)
    assert "analysis_percontract_core-vault.md" in files
    assert "analysis_percontract_v1.2.md" in files
    assert "analysis_rescan_1.md" in files


def test_hyphen_dot_declared_file_is_gated_not_dropped(tmp_path):
    """A declared hyphen/dot file that is MISSING must fail the exact gate
    (pre-Ship-C it was silently dropped, and an all-non-matching manifest fell
    back to the permissive glob)."""
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    _manifest(sp, ["analysis_rescan_1.md", "analysis_percontract_core-vault.md"])
    (sp / "analysis_rescan_1.md").write_text(SUB, encoding="utf-8")
    # core-vault NOT written
    missing = _rescan_manifest_exact_missing(sp, RESCAN)
    assert missing == ["analysis_percontract_core-vault.md"]
    passed, miss = gate_passes(sp, str(tmp_path), RESCAN)
    assert passed is False
    assert "analysis_percontract_core-vault.md" in miss


def test_hyphen_dot_all_present_passes(tmp_path):
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    files = ["analysis_rescan_1.md", "analysis_percontract_core-vault.md",
             "analysis_percontract_v1.2.md"]
    _manifest(sp, files)
    for f in files:
        (sp / f).write_text(SUB, encoding="utf-8")
    passed, miss = gate_passes(sp, str(tmp_path), RESCAN)
    assert passed is True, miss


def test_all_hyphen_names_no_longer_silently_fall_back_to_glob(tmp_path):
    """Pre-Ship-C: a manifest declaring ONLY hyphenated names parsed to zero
    -> None -> glob fallback (1 unrelated file passed). Now the declared names
    are parsed, so a partial set fails."""
    sp = tmp_path / ".scratchpad"; sp.mkdir()
    _manifest(sp, ["analysis_percontract_core-vault.md",
                   "analysis_percontract_token-flow.md"])
    # only ONE declared file present + an unrelated glob-matching file
    (sp / "analysis_percontract_core-vault.md").write_text(SUB, encoding="utf-8")
    (sp / "analysis_rescan_9.md").write_text(SUB, encoding="utf-8")  # glob bait
    assert _rescan_manifest_exact_missing(sp, RESCAN) == [
        "analysis_percontract_token-flow.md"
    ]
    passed, _ = gate_passes(sp, str(tmp_path), RESCAN)
    assert passed is False  # exact gate, not fooled by the glob bait
