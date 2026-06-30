"""Regression tests for scripts/codex_adapter.py generator output.

Guards two bugs found after an upstream merge:
  1. SKILL.md driver-launch lines used a hardcoded backslash separator
     (`scripts\\plamen_driver.py`), breaking the path on Linux/macOS.
  2. The AGENTS.md generator template drifted behind the committed
     codex-adapter/AGENTS.md (missing the "Hard Rule" block + the
     `"cli_backend": "codex"` pin), so `plamen install --codex` silently
     reverted the source files it regenerates.
"""

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
GENERATOR = SCRIPTS_DIR / "codex_adapter.py"


def _generate(out_dir: Path) -> None:
    res = subprocess.run(
        [sys.executable, str(GENERATOR), "--output-dir", str(out_dir)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, f"generator failed: {res.stderr}"


def test_generator_runs_with_custom_output_dir(tmp_path):
    # _rel() fallback: a custom out-dir outside PLAMEN_HOME must not crash.
    _generate(tmp_path)
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "skills" / "plamen" / "SKILL.md").exists()


def test_skill_driver_path_uses_forward_slash(tmp_path):
    _generate(tmp_path)
    skill = (tmp_path / "skills" / "plamen" / "SKILL.md").read_text(encoding="utf-8")
    assert "plamen_driver.py" in skill
    assert "scripts\\plamen_driver.py" not in skill, "Windows separator leaked into SKILL.md"
    assert "scripts/plamen_driver.py" in skill


def test_agents_md_has_hard_rule_and_codex_pin(tmp_path):
    _generate(tmp_path)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Do not manually orchestrate" in agents
    assert '"cli_backend": "codex"' in agents


def test_generator_output_matches_committed_source(tmp_path):
    # The generator is the source of truth: regenerating must reproduce the
    # committed codex-adapter/ files byte-for-byte (no manual-edit drift).
    _generate(tmp_path)
    for rel in ("AGENTS.md", "skills/plamen/SKILL.md"):
        generated = (tmp_path / rel).read_text(encoding="utf-8")
        committed = (ROOT / "codex-adapter" / rel).read_text(encoding="utf-8")
        assert generated == committed, f"{rel} drifted from generator output"
