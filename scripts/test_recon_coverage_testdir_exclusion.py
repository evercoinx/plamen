"""Regression: the recon coverage gate must not flag test/fuzz/verification
dirs as 'missed substantial modules'. `.medusa-tests` (and dotted/hyphenated
variants) slipped through the exact-segment skip list, tripping the gate +
retries + a scary HALT panel on every Foundry repo with a Medusa fuzz suite,
even though recon is explicitly told to skip those dirs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plamen_validators as pv  # noqa: E402


# --- helper unit tests -----------------------------------------------------
def test_segment_helper_excludes_test_fuzz_variants():
    f = pv._recon_segment_is_test_fuzz
    for seg in (".medusa-tests", "medusa-tests", "medusa", "fuzz",
                "fuzz-tests", "invariant", "invariant-tests", "test", "tests",
                "echidna", "halmos", "verification", "certora",
                "mocks", "fixtures", "verify_helpers", "coverage"):
        assert f(seg) is True, seg


def test_segment_helper_keeps_real_modules():
    f = pv._recon_segment_is_test_fuzz
    # Single words that merely CONTAIN a marker substring must NOT be excluded.
    for seg in ("latest", "attestation", "contest", "src", "managers",
                "contracts", "core", "protest", "testament"):
        assert f(seg) is False, seg


# --- integration: the actual gate --------------------------------------------
def _mk_module(root: Path, rel_dir: str, n: int):
    d = root / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"C{i}.sol").write_text("contract C {}", encoding="utf-8")


def test_gate_does_not_flag_medusa_tests(tmp_path):
    root = tmp_path
    _mk_module(root, ".medusa-tests", 12)            # 12-file fuzz dir
    _mk_module(root, "contracts/.medusa-tests", 11)  # nested variant
    sp = root / ".scratchpad"
    sp.mkdir()
    (sp / "recon_summary.md").write_text("# recon\nno production modules\n",
                                         encoding="utf-8")
    issues = pv._validate_recon_coverage(sp, str(root), "evm")
    assert not any("medusa" in i.lower() for i in issues), issues


def test_gate_still_flags_real_uncited_module(tmp_path):
    # No-regression: a genuine >=10-file production module that recon never
    # cites must STILL trip the gate (the exclusion is scoped to test dirs).
    root = tmp_path
    _mk_module(root, "src/managers", 12)
    _mk_module(root, ".medusa-tests", 12)
    sp = root / ".scratchpad"
    sp.mkdir()
    (sp / "recon_summary.md").write_text("# recon\nnothing cited\n",
                                         encoding="utf-8")
    issues = pv._validate_recon_coverage(sp, str(root), "evm")
    joined = " ".join(issues).lower()
    assert "managers" in joined, f"real module not flagged: {issues}"
    assert "medusa" not in joined, f"test dir wrongly flagged: {issues}"
