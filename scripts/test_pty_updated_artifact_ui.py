from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import plamen_display as display  # noqa: E402


def test_phase_heartbeat_can_show_updated_artifacts(monkeypatch, capsys):
    monkeypatch.setattr(display, "RICH_AVAILABLE", False)

    display.print_phase_heartbeat(
        "recon", 65, updated_artifacts=["recon_summary.md", "design_context.md"]
    )

    err = capsys.readouterr().err
    assert "1:05" in err
    assert "~recon_summary.md, design_context.md" in err


def test_phase_heartbeat_can_show_status(monkeypatch, capsys):
    monkeypatch.setattr(display, "RICH_AVAILABLE", False)

    display.print_phase_heartbeat("breadth", 12, status="preflight probe")

    err = capsys.readouterr().err
    assert "0:12" in err
    assert "preflight probe" in err


def test_spinner_redraw_interval_is_responsive():
    assert display._SPINNER_REDRAW_INTERVAL_S <= 0.20
