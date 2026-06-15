"""Regression tests for client-facing report title/body sanitization and the
tier-section finalizer that repairs generic/broken finding headings.

Covers two real defects observed in a finished report:
  1. Finding headings rendered "### [M-05] Verification" (generic) and index
     titles leaked an agent-finding-ID prefix + leading slash, e.g.
     "/ EXT-001: Nominal ERC20 Settlement Without Return/Delta Proof".
  2. Finding bodies leaked internal status prose ("... kept as report-blocked
     because ... not available in the shard inputs.").
"""

from plamen_parsers import _sanitize_client_title, _sanitize_client_body
from plamen_mechanical import _finalize_report_tier_section


def test_title_strips_agent_id_prefix_and_leading_slash():
    assert (
        _sanitize_client_title(
            "/ EXT-001: Nominal ERC20 Settlement Without Return/Delta Proof"
        )
        == "Nominal ERC20 Settlement Without Return/Delta Proof"
    )
    assert (
        _sanitize_client_title(
            "/ STATE-001: DynamicBonds Payout-Token Backing Can Be Drained"
        )
        == "DynamicBonds Payout-Token Backing Can Be Drained"
    )


def test_clean_title_unchanged():
    clean = "Reentrancy in withdraw allows double-spend"
    assert _sanitize_client_title(clean) == clean
    # Modulo internal whitespace collapse.
    assert _sanitize_client_title("Reentrancy   in withdraw") == "Reentrancy in withdraw"


def test_body_strips_report_blocked_sentence():
    body = (
        "The deposit path mints shares on nominal amount. This finding is kept "
        "as report-blocked because the manifest marks the evidence as downgraded "
        "and one consolidated verification file was not available in the shard "
        "inputs. Recommend a balance-delta check."
    )
    out = _sanitize_client_body(body)
    assert "deposit path mints shares" in out
    assert "Recommend a balance-delta check" in out
    assert "report-blocked" not in out
    assert "shard inputs" not in out


def test_normal_body_unchanged():
    body = (
        "The deposit path mints shares on the nominal amount. Recommend a "
        "balance-delta check before crediting the user."
    )
    out = _sanitize_client_body(body)
    assert out == body.strip()


def test_finalize_repairs_generic_heading_and_drops_report_blocked():
    section = (
        "### [REPORT-BLOCKED: insufficient evidence] [M-06] Verification\n\n"
        "Some body about ERC20 settlement.\n"
    )
    out = _finalize_report_tier_section(
        section,
        {"M-06": "Nominal ERC20 Settlement Without Return/Delta Proof"},
    )
    assert (
        "### [M-06] Nominal ERC20 Settlement Without Return/Delta Proof" in out
    )
    assert "REPORT-BLOCKED" not in out
    assert "Verification" not in out
    assert "Some body about ERC20 settlement." in out


def test_finalize_keeps_good_title_and_status_tag():
    section = "### [H-02] Pool accounting bug found here [CONTESTED]\n\nBody.\n"
    out = _finalize_report_tier_section(section, {"H-02": "Index title ignored"})
    assert "### [H-02] Pool accounting bug found here [CONTESTED]" in out


def test_finalize_body_preserves_report_id_replaces_internal_id():
    section = "### [M-01] A real medium finding title\n\nsee M-03 for related; also INV-011\n"
    out = _finalize_report_tier_section(section, {"M-01": "A real medium finding title"})
    assert "M-03" in out
    assert "INV-011" not in out
    assert "upstream finding" in out
