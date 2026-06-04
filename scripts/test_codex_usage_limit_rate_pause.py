"""Codex usage-cap errors are NATURAL LANGUAGE, not structured tokens, and MUST
be classified as a rate-limit (auto-wait + preserve state via
checkpoint.rate_limited_at), NOT a generic phase failure that burns the retry
budget and HALTS.

Fixture = the verbatim message from a live dHEDGE SC Thorough Codex halt
(account out of credits, reset 5:46 PM). Before the fix the regex looked only
for structured tokens (usage_limit_reached / 429 / "type":"usage_limit") and
missed this, so the run halted instead of auto-waiting.
"""
from pathlib import Path

import plamen_driver as d

_REAL_USAGE_LIMIT = (
    '{"type":"thread.started","thread_id":"019e92ed-5538-74f3-80ee-5a79649d3c7a"}\n'
    '{"type":"turn.started"}\n'
    '{"type":"error","message":"You\'ve hit your usage limit. Visit '
    'https://chatgpt.com/codex/settings/usage to purchase more credits or try '
    'again at 5:46 PM."}\n'
    '{"type":"turn.failed","error":{"message":"You\'ve hit your usage limit. '
    'Visit https://chatgpt.com/codex/settings/usage to purchase more credits or '
    'try again at 5:46 PM."}}\n'
)


def test_real_codex_usage_limit_is_rate_limited(tmp_path: Path):
    log = tmp_path / "_stdio_recon.attempt2.log"
    log.write_text(_REAL_USAGE_LIMIT, encoding="utf-8")
    assert d._CODEX_RATE_LIMIT_RE.search(_REAL_USAGE_LIMIT), (
        "regex must match the verbatim Codex usage-cap message"
    )
    # rc=1 (turn.failed) AND rc=0 (Codex can graceful-stop with the error
    # in-stream) both classify as rate-limited -> auto-wait, never a failure.
    assert d._detect_codex_rate_limit(log, returncode=1) is True
    assert d._detect_codex_rate_limit(log, returncode=0) is True


def test_codex_credit_phrase_variants_match():
    for msg in (
        "You've hit your usage limit.",
        "You have reached your rate limit, try again later.",
        "Please purchase more credits to continue.",
        "see https://chatgpt.com/codex/settings/usage",
    ):
        assert d._CODEX_RATE_LIMIT_RE.search(msg), f"should match: {msg!r}"


def test_codex_normal_output_not_rate_limited(tmp_path: Path):
    log = tmp_path / "_stdio_recon.attempt1.log"
    log.write_text(
        '{"type":"item.completed","item":{"type":"agent_message",'
        '"text":"Using the plamen skill; writing recon artifacts."}}\n',
        encoding="utf-8",
    )
    assert d._detect_codex_rate_limit(log, returncode=0) is False


def test_codex_auth_error_not_misclassified_as_rate_limit(tmp_path: Path):
    # Auth errors need re-auth, not backoff — the new usage-cap patterns must
    # NOT swallow a 401 into the rate-limit path.
    log = tmp_path / "_stdio_recon.attempt1.log"
    log.write_text(
        '{"type":"error","message":"401 unauthorized: invalid_api_key"}\n',
        encoding="utf-8",
    )
    assert d._detect_codex_rate_limit(log, returncode=1) is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
