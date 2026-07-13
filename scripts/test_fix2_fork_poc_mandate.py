"""Fixtures for Fix 2 (severity-aware fork-PoC mandate — INERT-SAFE, no RPC).

The mandate targets a Medium+ external-integration fund-drain/misrouting finding
whose harm rides on an untrusted external contract at a KNOWN deployed address:

  * `_effective_poc_class` floors such a finding to `integration` so a verifier
    cannot zero the PoC requirement by self-declaring `structural`.
  * `_valid_poc_skip` rejects an EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS skip for
    a Medium+ finding ONLY when the egress-reachability probe SUCCEEDS *and* the
    verifier prose names a concrete address.
  * The whole mandate is GATED on `_egress_rpc_reachable`. With NO reachable RPC
    (this environment) the probe returns False, the skip STAYS valid, and the
    finding is stamped `[UNPROVEN-EXTERNAL]` — a STRICT no-op vs pre-Fix-2.
  * report_index R10 holds a `[UNPROVEN-EXTERNAL]` finding at its proven-
    mechanism floor (Medium), never above, never below.

Per the feedback_id_regex_catalog rule these fixtures exercise the NEW
`[UNPROVEN-EXTERNAL]` token across the parser + severity + body surfaces so no
stale reader mis-reads it.

All fixtures are synthetic/generic (no protocol/token/contract/function names).

Run: pytest scripts/test_fix2_fork_poc_mandate.py -v
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def _v():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    if "plamen_validators" in sys.modules:
        del sys.modules["plamen_validators"]
    return importlib.import_module("plamen_validators")


def _scratch(tmp_path: Path, *, extra_cfg: dict | None = None) -> Path:
    sp = tmp_path / ".scratchpad"
    sp.mkdir()
    cfg = {"proven_only": False}
    if extra_cfg:
        cfg.update(extra_cfg)
    (sp / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return sp


def _queue(sp: Path, rows: list[tuple[str, str, str]]) -> None:
    out = [
        "| Queue # | Finding ID | Severity | Title | PoC Class |",
        "|---------|------------|----------|-------|-----------|",
    ]
    for i, (fid, sev, pc) in enumerate(rows, start=1):
        out.append(f"| {i} | {fid} | {sev} | example finding | {pc} |")
    (sp / "verification_queue.md").write_text("\n".join(out) + "\n", encoding="utf-8")


# External-integration fund-drain harm at a concrete deployed address — the exact
# class the fork-PoC mandate targets (GENERIC prose, no protocol names).
_EXT_DRAIN_MECH = (
    "The external router's reported return value is consumed verbatim and funds "
    "are paid out from the contract's own balance, so a misbehaving integration "
    "drains funds to the wrong recipient. The deployed contract address is "
    "0x1234567890abcdef1234567890abcdef12345678 and is live on mainnet."
)


def _verify(sp: Path, fid: str, *, severity: str, verdict: str, tag: str,
            attempted: str, skip_reason: str = "N/A",
            result: str = "NOT_EXECUTED", poc_class: str = "",
            body: str = "") -> None:
    poc_class_line = f"- PoC Class: {poc_class}\n" if poc_class else ""
    (sp / f"verify_{fid}.md").write_text(
        f"**Severity**: {severity}\n\n"
        f"**Verdict**: {verdict}\n\n"
        f"**Evidence Tag**: {tag}\n\n"
        f"{body}\n\n"
        "### PoC Attempt\n"
        "- PoC Required: YES\n"
        f"{poc_class_line}"
        f"- Attempted: {attempted}\n"
        f"- PoC Not Attempted Because: {skip_reason}\n\n"
        "### Execution Result\n"
        f"- Result: {result}\n",
        encoding="utf-8",
    )


# ===========================================================================
# Helper predicates — mechanism / address detection (generic, negation-aware)
# ===========================================================================

def test_external_integration_harm_matcher():
    V = _v()
    assert V._matches_external_integration_harm(
        "a misbehaving integration drains funds to the wrong recipient"
    ) is True
    assert V._matches_external_integration_harm(
        "output is misrouted to the wrong chain"
    ) is True
    # negation-aware: a negated mention does NOT match
    assert V._matches_external_integration_harm(
        "there is no fund drain here; balances are conserved"
    ) is False
    # unrelated internal bug does not match
    assert V._matches_external_integration_harm(
        "an off-by-one in an internal accumulator understates the total"
    ) is False


def test_named_deployed_address_detection():
    V = _v()
    assert V._names_resolvable_deployed_address(
        "the deployed contract address is 0x1234567890abcdef1234567890abcdef12345678"
    ) is True
    # base58 program-id co-located with a cue resolves too
    assert V._names_resolvable_deployed_address(
        "forked against the deployed program TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    ) is True
    # A bare phrase with NO actual address token is NOT resolvable — it names the
    # ABSENCE of a fork target, not a resolvable one.
    assert V._names_resolvable_deployed_address(
        "forked against the mainnet address for the router"
    ) is False
    assert V._names_resolvable_deployed_address(
        "no concrete address is known for this external dependency"
    ) is False


# ===========================================================================
# Egress-reachability probe — INERT without RPC
# ===========================================================================

def test_egress_probe_inert_without_config(tmp_path):
    """No RPC URL configured (and none in env) → probe returns False."""
    V = _v()
    sp = _scratch(tmp_path)
    # Make sure no env override leaks a URL into the probe.
    for k in ("PLAMEN_FORK_RPC_URL", "FORK_RPC_URL", "ETH_RPC_URL"):
        os.environ.pop(k, None)
    assert V._fork_rpc_url(sp) == ""
    assert V._egress_rpc_reachable(sp) is False


def test_egress_probe_unreachable_url_is_false(tmp_path):
    """A configured but unroutable URL → probe returns False (no exception)."""
    V = _v()
    # 203.0.113.0/24 is TEST-NET-3 (RFC 5737) — guaranteed non-routable.
    sp = _scratch(tmp_path, extra_cfg={"fork_rpc_url": "http://203.0.113.1:8545"})
    for k in ("PLAMEN_FORK_RPC_URL", "FORK_RPC_URL", "ETH_RPC_URL"):
        os.environ.pop(k, None)
    assert V._fork_rpc_url(sp) == "http://203.0.113.1:8545"
    # Bounded connect times out / refuses → False (may take up to ~2s).
    assert V._egress_rpc_reachable(sp) is False


# ===========================================================================
# (a) NO-RPC — mandate inert / skip valid / [UNPROVEN-EXTERNAL] stamped
# ===========================================================================

def test_no_rpc_effective_class_floors_to_integration(tmp_path):
    """The sticky floor is class-only (no RPC needed): a fund-drain finding at a
    named address is floored to `integration` even when the verifier declared
    `structural`."""
    V = _v()
    content = (
        "**Evidence Tag**: [CODE-TRACE]\n"
        f"{_EXT_DRAIN_MECH}\n"
        "- PoC Class: structural (queue said integration)\n"
    )
    assert V._effective_poc_class("structural", content) == "integration"


def test_no_rpc_valid_poc_skip_stays_valid(tmp_path):
    """With no reachable RPC, an EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS skip for a
    Medium+ integration finding STAYS valid (mandate inert)."""
    V = _v()
    sp = _scratch(tmp_path)
    for k in ("PLAMEN_FORK_RPC_URL", "FORK_RPC_URL", "ETH_RPC_URL"):
        os.environ.pop(k, None)
    content = (
        "**Evidence Tag**: [CODE-TRACE]\n"
        f"{_EXT_DRAIN_MECH}\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS\n"
    )
    # No scratchpad/severity: legacy call form is unchanged (skip valid).
    assert V._valid_poc_skip(content, "integration") is True
    # With scratchpad+High severity but NO reachable RPC: still valid.
    assert V._valid_poc_skip(content, "integration", sp, "High") is True


def test_no_rpc_soft_gate_stamps_unproven_external(tmp_path):
    """The soft coverage gate records POC_UNPROVEN_EXTERNAL (the [UNPROVEN-EXTERNAL]
    stamp) — NOT POC_ATTEMPT_SKIPPED — when the mandate is inert."""
    V = _v()
    sp = _scratch(tmp_path)
    for k in ("PLAMEN_FORK_RPC_URL", "FORK_RPC_URL", "ETH_RPC_URL"):
        os.environ.pop(k, None)
    _queue(sp, [("INV-F1", "High", "structural")])
    _verify(sp, "INV-F1", severity="High", verdict="CONFIRMED",
            tag="[CODE-TRACE]", attempted="NO",
            skip_reason="EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS",
            poc_class="integration", body=_EXT_DRAIN_MECH)
    warns = V._validate_poc_attempt_coverage(sp, "thorough")
    joined = "\n".join(warns)
    assert "POC_UNPROVEN_EXTERNAL: INV-F1" in joined
    assert "[UNPROVEN-EXTERNAL]" in joined
    assert "POC_ATTEMPT_SKIPPED: INV-F1" not in joined


def test_unproven_external_r10_holds_at_medium_floor(tmp_path):
    """A verify file carrying the [UNPROVEN-EXTERNAL] stamp with Attempted:NO +
    CODE-TRACE is capped at Medium (never above proven-mechanism, never below)."""
    V = _v()
    sp = _scratch(tmp_path)
    _queue(sp, [("INV-F2", "High", "integration")])
    _verify(sp, "INV-F2", severity="High", verdict="CONFIRMED",
            tag="[CODE-TRACE]", attempted="NO",
            skip_reason="EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS",
            poc_class="integration",
            body=_EXT_DRAIN_MECH + "\n[UNPROVEN-EXTERNAL] external leg unverified.")
    assert V._expected_report_index_severities(sp).get("INV-F2") == "Medium"


def test_unproven_external_r10_never_below_medium(tmp_path):
    """The R10 cap is a FLOOR of Medium: a Low finding stamped [UNPROVEN-EXTERNAL]
    is NOT lifted, and a High is capped DOWN to Medium — never below."""
    V = _v()
    sp = _scratch(tmp_path)
    _queue(sp, [("INV-F3", "Low", "integration")])
    _verify(sp, "INV-F3", severity="Low", verdict="CONFIRMED",
            tag="[CODE-TRACE]", attempted="NO",
            skip_reason="EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS",
            poc_class="integration",
            body=_EXT_DRAIN_MECH + "\n[UNPROVEN-EXTERNAL] external leg unverified.")
    # A Low stays Low (cap is a Medium FLOOR — it never lowers below current sev).
    assert V._expected_report_index_severities(sp).get("INV-F3") == "Low"


# ===========================================================================
# (b) SIMULATED-REACHABLE-RPC + named address — skip INVALID + integration floor
# ===========================================================================

def test_reachable_rpc_valid_poc_skip_invalid(tmp_path, monkeypatch):
    """When the egress probe is SIMULATED reachable AND the prose names a concrete
    address, an EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS skip becomes INVALID for a
    Medium+ finding (the fork PoC is runnable)."""
    V = _v()
    sp = _scratch(tmp_path)
    monkeypatch.setattr(V, "_egress_rpc_reachable", lambda _sp: True)
    content = (
        "**Evidence Tag**: [CODE-TRACE]\n"
        f"{_EXT_DRAIN_MECH}\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS\n"
    )
    # Medium+ + named address + reachable RPC → skip INVALID.
    assert V._valid_poc_skip(content, "integration", sp, "High") is False
    # Low severity is NOT swept up (mandate is Medium+).
    assert V._valid_poc_skip(content, "integration", sp, "Low") is True


def test_reachable_rpc_soft_gate_demands_poc(tmp_path, monkeypatch):
    """With a SIMULATED reachable RPC the soft gate emits POC_ATTEMPT_SKIPPED
    (demand a fork PoC) instead of the inert [UNPROVEN-EXTERNAL] note."""
    V = _v()
    sp = _scratch(tmp_path)
    monkeypatch.setattr(V, "_egress_rpc_reachable", lambda _sp: True)
    _queue(sp, [("INV-F4", "High", "structural")])
    _verify(sp, "INV-F4", severity="High", verdict="CONFIRMED",
            tag="[CODE-TRACE]", attempted="NO",
            skip_reason="EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS",
            poc_class="integration", body=_EXT_DRAIN_MECH)
    warns = V._validate_poc_attempt_coverage(sp, "thorough")
    joined = "\n".join(warns)
    assert "POC_ATTEMPT_SKIPPED: INV-F4" in joined
    assert "fork-url" in joined.lower()
    assert "POC_UNPROVEN_EXTERNAL: INV-F4" not in joined


def test_reachable_rpc_named_address_required(tmp_path, monkeypatch):
    """Even with a reachable RPC, a skip is NOT invalidated when NO concrete
    address is named (a fork has no pinned target)."""
    V = _v()
    sp = _scratch(tmp_path)
    monkeypatch.setattr(V, "_egress_rpc_reachable", lambda _sp: True)
    content = (
        "**Evidence Tag**: [CODE-TRACE]\n"
        "A misbehaving integration drains funds, but no concrete deployed "
        "address is known for the external dependency.\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS\n"
    )
    assert V._valid_poc_skip(content, "integration", sp, "High") is True


# ===========================================================================
# STRICT NO-OP assertion vs pre-Fix-2 severities (this env: no RPC)
# ===========================================================================

def test_strict_no_op_without_stamp_or_rpc(tmp_path):
    """A pre-Fix-2 verify file (external-drain harm, named address, Attempted:NO,
    NO [UNPROVEN-EXTERNAL] stamp) is UNCHANGED by Fix 2 in this env: the R10 cap
    fires ONLY on the literal stamp, so legacy findings keep their severity."""
    V = _v()
    sp = _scratch(tmp_path)
    for k in ("PLAMEN_FORK_RPC_URL", "FORK_RPC_URL", "ETH_RPC_URL"):
        os.environ.pop(k, None)
    _queue(sp, [("INV-F5", "High", "integration")])
    # No stamp in body — exactly a pre-Fix-2 verify file for this finding class.
    _verify(sp, "INV-F5", severity="High", verdict="CONFIRMED",
            tag="[CODE-TRACE]", attempted="NO",
            skip_reason="EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS",
            poc_class="integration", body=_EXT_DRAIN_MECH)
    # R10 does NOT cap (no stamp) → severity stays High, identical to pre-Fix-2.
    assert V._expected_report_index_severities(sp).get("INV-F5") == "High"


def test_unproven_external_stamp_not_internal_id_leak():
    """feedback_id_regex_catalog: the NEW [UNPROVEN-EXTERNAL] client stamp must
    NOT be mis-read as an internal finding-ID by the report-sanitization regex
    (else it would be stripped from the client body as an internal-ID leak)."""
    from plamen_parsers import _INTERNAL_FINDING_ID_RE
    section = "### [H-01] Example [UNPROVEN-EXTERNAL] [CODE-TRACE]\n\nbody see EN-1\n"
    hits = "".join(str(h) for h in _INTERNAL_FINDING_ID_RE.findall(section))
    assert "UNPROVEN" not in hits
    assert "EXTERNAL" not in hits
    # sanity: real internal IDs in the same string ARE still matched
    assert "H-01" in hits and "EN-1" in hits


def test_unproven_external_body_poc_exemption(tmp_path):
    """feedback_id_regex_catalog: a body section tagged [UNPROVEN-EXTERNAL] is
    exempt from the substantive-PoC-Result requirement (it has no executed PoC,
    same basis as [UNVERIFIED]/[CONFIRMED])."""
    V = _v()
    manifest = {
        "findings": [
            {"report_id": "H-01", "severity": "High", "location": "file.sol:L10"}
        ]
    }
    body = (
        "### [H-01] External-integration drain [UNPROVEN-EXTERNAL]\n\n"
        "**Severity**: High\n"
        "**Location**: file.sol:L10\n\n"
        "**Impact**: A misbehaving external integration drains user funds to the "
        "wrong recipient; depositors lose their pro-rata share of the pool.\n\n"
        "**Description**: The external router return value is trusted verbatim.\n"
    )
    res = V._validate_report_body(body, manifest)
    # No "missing substantive PoC Result" content error for the stamped section.
    assert not any(
        "missing substantive PoC Result" in e for e in res.get("content", [])
    )


def test_legacy_valid_poc_skip_signature_unchanged(tmp_path):
    """The 2-arg `_valid_poc_skip(content, poc_class)` call form (used by
    `_structurally_untestable` and other legacy callers) is byte-for-byte
    unchanged: no scratchpad ⇒ mandate branch never fires."""
    V = _v()
    content = (
        "**Evidence Tag**: [CODE-TRACE]\n"
        f"{_EXT_DRAIN_MECH}\n"
        "- Attempted: NO\n"
        "- PoC Not Attempted Because: EXTERNAL_DEPENDENCY_NO_FORK_OR_ADDRESS\n"
    )
    assert V._valid_poc_skip(content, "integration") is True
    assert V._valid_poc_skip(content, "structural") is True
