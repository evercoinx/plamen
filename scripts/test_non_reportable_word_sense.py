"""Recall-safety regression: _non_reportable_marker must demote real dispositions
(duplicate of / merged into / refuted) but NOT fire on the adjective word-sense of
'duplicate'/'merged' (e.g. 'merged-pool', 'duplicate entry'), which would falsely
demote a real finding. Caught during review of the regex-fragility remediation:
the negation guard handled 'NOT a duplicate' but the bare-substring marker still
fired on 'merged-pool'. Tightened to disposition-sense; firing LESS is recall-safe.
"""
from __future__ import annotations

import importlib
import os
import sys


def _p():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    return importlib.import_module("plamen_parsers")


# Real dispositions -> MUST be demoted (non-reportable=True)
_DEMOTE = [
    "**Verdict**: Duplicate of M-03, same root cause.",
    "**Verdict**: duplicates H-05.",
    "**Verdict**: Merged into H-05 (same fix).",
    "**Verdict**: merged with H-02.",
    "**Verdict**: This is a duplicate.",
    "**Verdict**: marked as duplicate.",
    "**Verdict**: Duplicate",
    "**Verdict**: Merged",
    "**Verdict**: REFUTED.",
    "**Verdict**: false positive.",
    "**Verdict**: absorbed into H-9.",
]

# Word-sense / negation -> MUST stay reportable (non-reportable=False) — recall-safe
_KEEP = [
    "**Description**: Unlike the merged-pool case, this affects a separate path.",
    "**Description**: the merged pool reserves are tracked separately.",
    "**Description**: a duplicate entry in the array is silently accepted.",
    "**Description**: duplicate keys are rejected by the mapping.",
    "**Description**: This is NOT a duplicate; it is a distinct bug.",
    "**Description**: these reserves are not merged with the main pool.",
]


def test_real_dispositions_are_demoted():
    P = _p()
    for txt in _DEMOTE:
        assert P._non_reportable_marker(txt) is True, txt


def test_word_sense_and_negation_stay_reportable():
    P = _p()
    for txt in _KEEP:
        assert P._non_reportable_marker(txt) is False, txt


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
