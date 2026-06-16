"""Regression: 5xx API server errors (500/502/503/504) must be treated as the
same transient RETRY class as 529 overloaded, so a worker that hits an API 500
gets backoff-retry (and ultimately halt/resume) instead of stalling forever
waiting for a completion marker that never arrives. (Mac recon-worker bug.)"""
import pty_exec as P


def test_text_5xx_with_context_detected():
    assert P.text_shows_overloaded('apiErrorStatus 500 internal server error')
    assert P.text_shows_overloaded('status_code: 503')
    assert P.text_shows_overloaded('500 Internal Server Error')
    assert P.text_shows_overloaded('502 Bad Gateway')
    assert P.text_shows_overloaded('"type":"api_error"')


def test_bare_500_in_audit_prose_not_detected():
    # audit prose mentioning 500 tokens / a $500 loss must NOT trip it
    assert not P.text_shows_overloaded('the attacker drains 500 tokens from the pool')
    assert not P.text_shows_overloaded('loss capped at 500 USDC')


def test_event_5xx_status_detected():
    assert P.event_is_overloaded({'api_error_status': 500})
    assert P.event_is_overloaded({'apiErrorStatus': 503})
    assert P.event_is_overloaded({'error': {'type': 'api_error'}, 'type': 'assistant'})
    assert P.event_is_overloaded({'api_error_status': 529})  # 529 still works


def test_event_user_role_prose_guarded():
    # a 'user' event (tool-result/prose echo) must never trigger
    assert not P.event_is_overloaded({'type': 'user', 'message': '500 internal server error'})


def test_rate_limit_429_unaffected():
    assert P.text_shows_rate_limit('429 too many requests')
    assert not P.text_shows_overloaded('429 too many requests')  # 429 != server-error class


def test_inspect_transcript_529_sets_overloaded_and_rate_limited(tmp_path):
    """A 529 in a worker transcript sets BOTH flags (overloaded is a subset of
    the broad rate-limit set), so the worker-pool runner routes it to the
    retriable 'overloaded' status instead of the phase-level usage-cap pause."""
    t = tmp_path / "transcript.jsonl"
    t.write_text(
        '{"type":"assistant","subtype":"api_error","status":529,'
        '"error":{"type":"overloaded_error","message":"Overloaded"}}\n',
        encoding="utf-8",
    )
    state = P.inspect_transcript(t)
    assert state.overloaded is True
    assert state.rate_limited is True


def test_inspect_transcript_429_is_rate_limit_not_overloaded(tmp_path):
    """A real 429 account/usage cap sets rate_limited but NOT overloaded, so it
    still routes to the usage-cap pause (behavior unchanged)."""
    t = tmp_path / "transcript.jsonl"
    t.write_text(
        '{"type":"assistant","subtype":"api_error","status":429,'
        '"error":{"type":"rate_limit_error","message":"Too Many Requests"}}\n',
        encoding="utf-8",
    )
    state = P.inspect_transcript(t)
    assert state.rate_limited is True
    assert state.overloaded is False


if __name__ == '__main__':
    import pytest, sys
    sys.exit(pytest.main([__file__, '-q']))
