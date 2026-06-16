"""Regression: DAML `DML-` internal finding IDs must be (a) recognized as
internal by the unified ID regex (so completeness/coverage accounting sees
them) and (b) stripped from the client-facing report body (so a `[DML-*]`
internal ID never leaks into the delivered AUDIT_REPORT.md). Guards the gap the
wiring agent flagged: the inverse of the report-strip collision check.
"""
import re

import plamen_parsers as P

DML_IDS = [
    "DML-AUTH-1", "DML-ASM-2", "DML-CID-3", "DML-SK-4", "DML-BI-5",
    "DML-PR-6", "DML-IF-7", "DML-AM-8", "DML-CHS-9", "DML-CK-10",
    "DML-CC-11", "DML-PD-12", "DML-LK-13", "DML-EI-14",
]


def test_all_dml_ids_recognized_as_internal():
    rx = re.compile(r"\b(" + P._ID_ALL_INTERNAL + r")\b")
    for fid in DML_IDS:
        m = rx.search(f"finding {fid} here")
        assert m and m.group(1) == fid, f"{fid} not fully recognized: {m and m.group(1)}"


def test_dml_cc_not_partially_matched_as_bare_cc():
    # DML-CC-11 must match wholesale (DAML alt first), NOT the bare CC-11 substring.
    rx = re.compile(r"\b(" + P._ID_ALL_INTERNAL + r")\b")
    m = rx.search("see DML-CC-11 in scope")
    assert m and m.group(1) == "DML-CC-11", m and m.group(1)


def test_dml_ids_stripped_from_client_body():
    body = "The issue (DML-AUTH-1, DML-CC-11) lets an attacker move the asset."
    cleaned = P._CLIENT_BODY_INTERNAL_ID_RE.sub("upstream finding", body)
    for fid in ("DML-AUTH-1", "DML-CC-11"):
        assert fid not in cleaned, f"{fid} leaked into client body: {cleaned!r}"


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
