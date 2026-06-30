"""FIX 1: backend-agnostic last-resort recon pre-pass marker degrade.

Root cause: the recon content gate (`_validate_recon_content_structure`)
HARD-fails while a line-1 `_PREPASS_MARKER` survives on a canonical recon
artifact. On the Claude backend, a recon worker that never reaches
`PLAMEN_STATUS: COMPLETE` leaves the mechanical pre-pass stub (marker intact)
in place, so the gate fails forever and the phase retries forever — there was
no live-path degrade and the resume-path strip was guarded to Codex only.

Fix: `_try_recon_prepass_marker_degrade` (driver) runs as a LAST RESORT after
the worker pool + direct fallback + retries have all exhausted. When the ONLY
recon hard failures are survived pre-pass markers (and the bodies are
regex-complete pre-pass content, not placeholders), it strips the marker,
promoting that content to canonical so the gate passes and the pipeline
continues with degraded-but-usable recon. It declines (no-op) when ANY
non-marker recon gap exists.
"""
from __future__ import annotations

from pathlib import Path

import plamen_driver as D
import plamen_mechanical as M
from plamen_mechanical import _PREPASS_MARKER
from plamen_validators import _validate_recon_content_structure


# A regex-complete pre-pass table body (what the mechanical pre-pass writes).
_REAL_FUNCS = (
    "# Function List\n\n"
    "Pre-pass: 2 function(s) identified via regex scan.\n\n"
    "| File | Function | Visibility | Line |\n"
    "|------|----------|------------|------|\n"
    "| Vault.sol | deposit | external | 20 |\n"
    "| Vault.sol | withdraw | external | 40 |\n"
)
_REAL_STATEVARS = (
    "# State Variables\n\n"
    "Pre-pass: 2 state(s) identified via regex scan.\n\n"
    "| File | Variable | Type | Line |\n"
    "|------|----------|------|------|\n"
    "| Vault.sol | totalAssets | uint256 | 12 |\n"
    "| Vault.sol | owner | address | 14 |\n"
)


def _seed_clean_recon_context(scratch: Path) -> None:
    """Write the OTHER recon-content artifacts cleanly so the only hard
    failures come from the marker on function_list / state_variables."""
    # design_context must carry Operational Implications + Key Invariants and
    # NO marker (Claude Write replaces line 1 for the LLM-authored design doc).
    (scratch / "design_context.md").write_text(
        "# Design Context\n\n"
        "## Operational Implications\n"
        "The vault tracks depositor assets via totalAssets; every deposit and "
        "withdraw must keep share accounting consistent.\n\n"
        "## Key Invariants\n"
        "totalAssets == sum(user balances). Shares are minted pro-rata.\n",
        encoding="utf-8",
    )
    # recon_summary must clear the 512-byte (claude) minimum.
    (scratch / "recon_summary.md").write_text(
        "# Recon Summary\n\n"
        "## Protocol overview\n"
        + ("This ERC4626-style vault holds depositor assets and mints shares. "
           "The attack surface centers on deposit/withdraw share accounting and "
           "the owner role. ") * 8
        + "\n",
        encoding="utf-8",
    )


def test_degrade_strips_marker_and_gate_passes(tmp_path):
    """Sole hard failures are survived markers on regex-complete pre-pass
    artifacts -> degrade strips the marker and the content gate then passes."""
    _seed_clean_recon_context(tmp_path)
    (tmp_path / "function_list.md").write_text(
        _PREPASS_MARKER + "\n" + _REAL_FUNCS, encoding="utf-8"
    )
    (tmp_path / "state_variables.md").write_text(
        _PREPASS_MARKER + "\n" + _REAL_STATEVARS, encoding="utf-8"
    )

    # Pre-condition: gate fails ONLY on the markers.
    hard_before, _ = _validate_recon_content_structure(tmp_path)
    assert hard_before
    assert all("pre-pass overwrite marker" in h for h in hard_before), hard_before

    config = {"cli_backend": "claude"}
    missing = ["recon content: " + "; ".join(hard_before)]

    degraded, new_missing = D._try_recon_prepass_marker_degrade(
        tmp_path, config, missing
    )

    assert degraded is True
    assert new_missing == []
    # Markers are gone on disk.
    for name in ("function_list.md", "state_variables.md"):
        first = (tmp_path / name).read_text(encoding="utf-8").splitlines()[:1]
        assert first != [_PREPASS_MARKER], name
        # content preserved
        body = (tmp_path / name).read_text(encoding="utf-8")
        assert "Vault.sol" in body
    # Gate now passes (no hard failures).
    hard_after, _ = _validate_recon_content_structure(tmp_path)
    assert hard_after == []


def test_degrade_declines_when_non_marker_hard_failure_present(tmp_path):
    """A genuine recon gap (missing Key Invariants) alongside the marker must
    BLOCK the degrade — the marker strip cannot fix a real content gap."""
    # design_context with Operational Implications but NO Key Invariants ->
    # a real (non-marker) hard failure.
    (tmp_path / "design_context.md").write_text(
        "# Design Context\n\n## Operational Implications\nsome implications\n",
        encoding="utf-8",
    )
    (tmp_path / "recon_summary.md").write_text(
        "# Recon Summary\n\n## overview\n" + ("filler protocol contract risk " * 20) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "function_list.md").write_text(
        _PREPASS_MARKER + "\n" + _REAL_FUNCS, encoding="utf-8"
    )

    hard_before, _ = _validate_recon_content_structure(tmp_path)
    # Both a marker failure AND a missing-Key-Invariants failure present.
    assert any("pre-pass overwrite marker" in h for h in hard_before)
    assert any("Key Invariants" in h for h in hard_before)

    config = {"cli_backend": "claude"}
    missing = ["recon content: " + "; ".join(hard_before)]

    degraded, new_missing = D._try_recon_prepass_marker_degrade(
        tmp_path, config, missing
    )

    assert degraded is False
    assert new_missing == missing
    # Marker must NOT have been stripped (real gap blocks the degrade).
    first = (tmp_path / "function_list.md").read_text(
        encoding="utf-8"
    ).splitlines()[:1]
    assert first == [_PREPASS_MARKER]


def test_degrade_declines_when_other_recon_gate_failure_present(tmp_path):
    """A non-content recon gate failure (e.g. coverage) must block the
    degrade even if the content failures are marker-only."""
    _seed_clean_recon_context(tmp_path)
    (tmp_path / "function_list.md").write_text(
        _PREPASS_MARKER + "\n" + _REAL_FUNCS, encoding="utf-8"
    )

    config = {"cli_backend": "claude"}
    # missing carries a recon coverage failure in addition to the content one.
    hard_before, _ = _validate_recon_content_structure(tmp_path)
    missing = [
        "recon coverage: recon missed modules: src/Foo.sol",
        "recon content: " + "; ".join(hard_before),
    ]

    degraded, new_missing = D._try_recon_prepass_marker_degrade(
        tmp_path, config, missing
    )

    assert degraded is False
    assert new_missing == missing
    # Marker preserved.
    first = (tmp_path / "function_list.md").read_text(
        encoding="utf-8"
    ).splitlines()[:1]
    assert first == [_PREPASS_MARKER]


def test_degrade_declines_when_body_is_placeholder(tmp_path):
    """Marker survives but body is a pure [LLM TO ENRICH] placeholder ->
    genuine empty recon; strip helper refuses and degrade declines."""
    _seed_clean_recon_context(tmp_path)
    (tmp_path / "function_list.md").write_text(
        _PREPASS_MARKER + "\n# Function List\n\n[LLM TO ENRICH] pre-pass failed\n",
        encoding="utf-8",
    )

    config = {"cli_backend": "claude"}
    hard_before, _ = _validate_recon_content_structure(tmp_path)
    missing = ["recon content: " + "; ".join(hard_before)]

    degraded, new_missing = D._try_recon_prepass_marker_degrade(
        tmp_path, config, missing
    )

    assert degraded is False
    # Marker preserved -> gate still fails on a genuinely empty recon.
    first = (tmp_path / "function_list.md").read_text(
        encoding="utf-8"
    ).splitlines()[:1]
    assert first == [_PREPASS_MARKER]


def test_partial_merge_preserves_mechanical_body_and_appends_addendum(tmp_path):
    """Authority-preservation invariant: when contract_inventory / function_list
    / state_variables already exceed 100 bytes (mechanical full enumeration), a
    (partial) merge keeps that body byte-intact at the top and appends a
    '## Recon Worker Addendum' rather than overwriting it."""
    scratch = tmp_path
    cfg = {
        "project_root": str(tmp_path),
        "scratchpad": str(scratch),
        "language": "evm",
        "mode": "thorough",
        "pipeline": "sc",
    }

    # Marker-free mechanical bodies, each comfortably above 100 bytes.
    bodies = {
        "function_list.md": _REAL_FUNCS,
        "state_variables.md": _REAL_STATEVARS,
        "contract_inventory.md": (
            "# Contract Inventory\n\n"
            "| Contract | File | Lines |\n"
            "|----------|------|-------|\n"
            "| ExampleVault | ExampleVault.sol | 120 |\n"
            "| ExampleToken | ExampleToken.sol | 80 |\n"
        ),
    }
    for name, body in bodies.items():
        assert len(body.encode("utf-8")) >= 100, name
        (scratch / name).write_text(body, encoding="utf-8")

    # Only one (partial) shard present — the inventory_surface narrative shard.
    (scratch / "recon_inventory_surface.md").write_text(
        "<!-- PLAMEN_ARTIFACT: recon_inventory_surface.md -->\n"
        "<!-- PLAMEN_STATUS: COMPLETE -->\n\n"
        "# Recon Worker inventory_surface\n\n"
        "## Attack Surface\n\n"
        "deposit() and withdraw() are the externally reachable entry points.\n\n"
        "## Enumeration Gaps\n\nnone found; checked assembly and delegatecall.\n",
        encoding="utf-8",
    )

    M._merge_recon_worker_shards(scratch, cfg)

    for name, body in bodies.items():
        merged = (scratch / name).read_text(encoding="utf-8")
        # The original mechanical body stays byte-intact at the top.
        assert merged.startswith(body.rstrip()), name
        # The shard is appended as an addendum, not as a replacement.
        assert "## Recon Worker Addendum" in merged, name
        # The mechanical enumeration rows survive verbatim.
        assert "Vault.sol" in merged or "ExampleVault" in merged, name


def test_degrade_noop_when_no_recon_content_failure(tmp_path):
    """No recon content failure in missing -> immediate no-op."""
    config = {"cli_backend": "claude"}
    missing = ["some other phase gate failure"]
    degraded, new_missing = D._try_recon_prepass_marker_degrade(
        tmp_path, config, missing
    )
    assert degraded is False
    assert new_missing == missing
