"""Regression test for the RC-harness FOUNDRY_PROFILE propagation bug.

The mechanical verify EVM re-run used to reconstruct `forge test ...` and run it
with the driver's inherited env, dropping the `FOUNDRY_PROFILE=poc` the verifier
used. forge fell back to `[profile.default]`, whose test dir does not contain the
PoCs (custom profiles route tests to a non-default dir), so a passing suite read
as mass NO_TEST_FILE/FAIL and cascaded into spurious assertion + INFLATED_PROSE
demotions. The fix recovers the profile from the recorded command, else from
foundry.toml auto-detect.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mechanical_verify as mv  # noqa: E402

_FOUNDRY_TOML = """\
[profile.default]
test = ".medusa-tests"
out = "out"

[profile.poc]
test = "test/verify"
out = "out_poc"
"""


def _probe(cmd=""):
    return types.SimpleNamespace(test_command=cmd, test_function=None)


def test_profile_from_recorded_command():
    p = _probe("FOUNDRY_PROFILE=poc forge test --match-test test_H79_x -vv")
    assert mv._resolve_foundry_profile(p, "/build", "/build/test/verify/PoC_H79.t.sol") == "poc"


def test_profile_from_recorded_command_quoted():
    p = _probe('FOUNDRY_PROFILE="poc" forge test --match-test t')
    assert mv._resolve_foundry_profile(p, "/x", "/x/whatever.t.sol") == "poc"


def test_profile_autodetect_from_foundry_toml(tmp_path):
    (tmp_path / "foundry.toml").write_text(_FOUNDRY_TOML, encoding="utf-8")
    poc = tmp_path / "test" / "verify" / "PoC_H79.t.sol"
    poc.parent.mkdir(parents=True)
    poc.write_text("contract PoC {}", encoding="utf-8")
    # No env var in the command -> must auto-detect the 'poc' profile by test dir.
    assert mv._resolve_foundry_profile(_probe(""), tmp_path, poc) == "poc"


def test_default_dir_file_needs_no_profile(tmp_path):
    (tmp_path / "foundry.toml").write_text(_FOUNDRY_TOML, encoding="utf-8")
    dflt = tmp_path / ".medusa-tests" / "Foo.t.sol"
    dflt.parent.mkdir(parents=True)
    dflt.write_text("x", encoding="utf-8")
    # File lives in the DEFAULT profile's dir -> no override needed.
    assert mv._resolve_foundry_profile(_probe(""), tmp_path, dflt) is None


def test_recorded_command_wins_over_toml(tmp_path):
    (tmp_path / "foundry.toml").write_text(_FOUNDRY_TOML, encoding="utf-8")
    p = _probe("FOUNDRY_PROFILE=poc forge test")
    # Even a default-dir file is run under the explicitly-recorded profile.
    assert mv._resolve_foundry_profile(p, tmp_path, tmp_path / ".medusa-tests/F.t.sol") == "poc"


def test_missing_toml_is_safe(tmp_path):
    assert mv._resolve_foundry_profile(_probe(""), tmp_path, tmp_path / "x.t.sol") is None


def test_standard_project_test_dir_unaffected(tmp_path):
    # The common case: tests in the default `test/` dir, no custom profile.
    (tmp_path / "foundry.toml").write_text(
        '[profile.default]\nsrc = "src"\ntest = "test"\n', encoding="utf-8")
    f = tmp_path / "test" / "Foo.t.sol"
    f.parent.mkdir(parents=True)
    f.write_text("x", encoding="utf-8")
    assert mv._resolve_foundry_profile(_probe(""), tmp_path, f) is None
