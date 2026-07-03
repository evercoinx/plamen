"""Ship 8.14 -- Prompt source selection: prefer V2 recon prompt, prove the
RENDERED prompt is clean.

Root cause (prior post-mortem): the recon prompt resolver preferred the LEGACY
multi-agent orchestrator prompt over the V2 single-agent direct-execution
prompt. Under the V2 foreground-Task contract the legacy "spawn Agent 1A/1B/2"
directive ran SERIALLY (1A 4min -> 1B 6min -> Slither 32min) and blew the 3000s
timeout, leaving a 443-byte recon_summary that failed the gate.

These tests assert (a) the resolver prefers v2 with legacy fallback, and
(b) codex adjustment 3: the RENDERED recon prompt uses the V2 body, carries no
legacy spawn directive, no stale `claude -p` framing, and no unresolved
{network_if_provided}/{scope_notes_if_provided} placeholders.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from plamen_types import plamen_home  # noqa: E402
import plamen_driver as D  # noqa: E402
from plamen_prompt import (  # noqa: E402
    _resolve_recon_prompt,
    _render_runtime_placeholders,
    build_phase_prompt,
)

LANGS = ("evm", "solana", "aptos", "sui", "soroban")


# --------------------------------------------------------------------------
# Resolver-level
# --------------------------------------------------------------------------

def test_resolver_prefers_v2_recon_prompt_evm():
    p = _resolve_recon_prompt({"pipeline": "sc", "language": "evm"})
    assert p is not None
    assert p.parent.name == "v2", f"expected v2 dir, got {p}"
    assert p.name == "phase1-recon-prompt.md"


def test_resolver_prefers_v2_across_all_sc_languages():
    for lang in LANGS:
        p = _resolve_recon_prompt({"pipeline": "sc", "language": lang})
        assert p is not None, f"{lang}: no recon prompt resolved"
        assert p.parent.name == "v2", f"{lang}: expected v2, got {p}"


def test_resolver_prefers_v2_for_l1():
    p = _resolve_recon_prompt({"pipeline": "l1", "language": "go"})
    assert p is not None
    assert p.parent.name == "v2", f"expected l1/v2, got {p}"


def test_resolver_falls_back_to_legacy_when_v2_absent(tmp_path, monkeypatch):
    """If a language ships ONLY a legacy recon prompt, the resolver must still
    return it (no phase loses its prompt)."""
    fake_home = tmp_path
    legacy_dir = fake_home / "prompts" / "evm"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "phase1-recon-prompt.md").write_text("legacy", encoding="utf-8")
    # No v2/ subdir.
    import plamen_prompt as PP
    monkeypatch.setattr(PP, "plamen_home", lambda: fake_home)
    p = _resolve_recon_prompt({"pipeline": "sc", "language": "evm"})
    assert p is not None
    assert p.name == "phase1-recon-prompt.md"
    assert p.parent.name == "evm"  # legacy, not v2


# --------------------------------------------------------------------------
# Placeholder rendering
# --------------------------------------------------------------------------

def test_network_and_scope_notes_placeholders_resolved():
    txt = "NET: {network_if_provided} NOTES: {scope_notes_if_provided}"
    out = _render_runtime_placeholders(txt, {})
    assert "{network_if_provided}" not in out
    assert "{scope_notes_if_provided}" not in out
    assert "(none)" in out  # both default to (none) when not in config


def test_scope_notes_placeholder_uses_config_value():
    out = _render_runtime_placeholders(
        "NOTES: {scope_notes_if_provided}",
        {"scope_notes": "only the vault"},
    )
    assert "only the vault" in out
    assert "{scope_notes_if_provided}" not in out


# --------------------------------------------------------------------------
# Rendered-prompt acceptance (codex adjustment 3)
# --------------------------------------------------------------------------

def _render_recon(tmp_path, *, backend="claude", exec_mode="pty"):
    sp = tmp_path / ".scratchpad"
    sp.mkdir(exist_ok=True)
    phase = next(p for p in D.SC_PHASES if p.name == "recon")
    return build_phase_prompt(
        plamen_home() / "commands" / "plamen.md",
        phase,
        {
            "scratchpad": str(sp),
            "project_root": str(tmp_path),
            "language": "evm",
            "mode": "thorough",
            "pipeline": "sc",
            "proven_only": False,
            "cli_backend": backend,
            "claude_exec_mode": exec_mode,
        },
    )


def test_rendered_recon_uses_v2_body(tmp_path):
    prompt = _render_recon(tmp_path)
    # V2 body markers (single-agent direct-execution structure).
    assert "TURN BUDGET POLICY" in prompt
    assert ("prompts/evm/v2/phase1-recon-prompt.md" in prompt
            or "prompts\\evm\\v2\\phase1-recon-prompt.md" in prompt)


def test_rendered_recon_has_no_legacy_spawn_directive(tmp_path):
    """The legacy multi-agent directive that caused the serial fan-out must be
    gone. NOTE: we do NOT ban the literal 'run_in_background' substring -- the
    PTY execution contract legitimately PROHIBITS it ('Do NOT pass
    run_in_background: true'). We ban the affirmative SPAWN directive."""
    prompt = _render_recon(tmp_path)
    assert "Spawn Agent 1A" not in prompt
    assert "Spawn Agent 1A with `run_in_background: true`" not in prompt
    # Legacy MCP-batching directive that only the orchestrator body carried.
    assert "PARALLELIZATION DIRECTIVE" not in prompt


def test_rendered_recon_has_no_stale_claude_p_framing(tmp_path):
    prompt = _render_recon(tmp_path)
    # The v2 recon body's turn-budget guidance no longer names `claude -p`
    # (false under PTY). The DRAFT-FIRST guidance is preserved.
    assert "`claude -p`" not in prompt
    assert "DRAFT-FIRST" in prompt or "SUBSTANTIVE DRAFTS" in prompt


def test_rendered_recon_has_no_unresolved_placeholders(tmp_path):
    prompt = _render_recon(tmp_path)
    assert "{network_if_provided}" not in prompt
    assert "{scope_notes_if_provided}" not in prompt
    assert "{docs_path_or_url_if_provided}" not in prompt
    assert "{scope_file_if_provided}" not in prompt


def test_codex_recon_render_has_no_claude_specific_runtime_language(tmp_path):
    """Codex backend must not receive `claude -p` body framing."""
    prompt = _render_recon(tmp_path, backend="codex")
    assert "`claude -p`" not in prompt
