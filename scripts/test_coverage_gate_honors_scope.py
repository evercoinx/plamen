"""Tests: SC subsystem-coverage gate must honor the wizard scope file.

A properly-scoped audit must never be failed for not covering out-of-scope
vendored files (e.g. uniswap math libraries). The hard SC coverage gate
`_validate_sc_subsystem_coverage` (and its soft sibling
`_compute_subsystem_coverage_gap` via `_compute_scip_coverage_sets`) now thread
a `scope_file` param and filter the REQUIRED-coverage universe to in-scope
files, mirroring `_validate_recon_coverage`.

Fixture note: the gate only flags a *bucket* (module key) when it contains
>= min_bucket_files (default 4) files and none are cited. `_sc_contract_module_key`
buckets `utils/uniswap/libraries/*.sol` under `utils/uniswap`, so we use 4
vendored files there to make a substantial uncited vendored bucket (otherwise
the gate would never fire on it at all). The in-scope `contracts` files are
cited so their bucket is covered.
"""

import tempfile
from pathlib import Path

import plamen_validators as V


def _mk_scratch(repo_map: str, *finding_files: tuple[str, str]) -> Path:
    """Build a tmp scratchpad with a SCIP repo_map.md and finding artifacts.

    `_collect_scip_indexed_paths` reads `scip/repo_map.md` `## <path>` headers.
    `_collect_cited_paths` harvests `path/file.ext` tokens from finding globs
    (e.g. `analysis_*.md`).
    """
    sp = Path(tempfile.mkdtemp(prefix="plamen_scope_gate_"))
    scip = sp / "scip"
    scip.mkdir()
    (scip / "repo_map.md").write_text(repo_map, encoding="utf-8")
    for name, body in finding_files:
        (sp / name).write_text(body, encoding="utf-8")
    return sp


# Indexed: 2 in-scope contracts (cited) + 4 vendored uniswap libs (uncited).
# The vendored bucket `utils/uniswap` reaches min_bucket_files=4 → substantial.
_REPO_MAP = (
    "## contracts/Vault.sol\n"
    "## contracts/Pool.sol\n"
    "## utils/uniswap/libraries/FullMath.sol\n"
    "## utils/uniswap/libraries/BitMath.sol\n"
    "## utils/uniswap/libraries/TickMath.sol\n"
    "## utils/uniswap/libraries/SqrtPriceMath.sol\n"
)
# Cite only the two in-scope contracts.
_CITED_ONLY_INSCOPE = (
    "analysis_1.md",
    "Finding at contracts/Vault.sol:L10 and contracts/Pool.sol:L20.\n",
)


def test_no_scope_flags_vendored():
    """Reproduces today's failure: with NO scope file the gate flags the
    out-of-scope vendored uniswap libraries as an uncovered substantial bucket."""
    sp = _mk_scratch(_REPO_MAP, _CITED_ONLY_INSCOPE)
    issues = V._validate_sc_subsystem_coverage(sp, "thorough")
    assert issues, "expected gate to fire when vendored bucket is uncited and unscoped"
    blob = " ".join(issues)
    assert "FullMath.sol" in blob and "BitMath.sol" in blob, (
        f"expected vendored libs to be flagged; got {issues!r}"
    )


def test_scope_excludes_vendored():
    """A scope.txt listing only the in-scope contracts must suppress the
    out-of-scope vendored libs entirely -> gate returns []."""
    sp = _mk_scratch(_REPO_MAP, _CITED_ONLY_INSCOPE)
    scope = sp / "scope.txt"
    scope.write_text(
        "contracts/Vault.sol\ncontracts/Pool.sol\n", encoding="utf-8"
    )
    issues = V._validate_sc_subsystem_coverage(
        sp, "thorough", scope_file=str(scope)
    )
    assert issues == [], (
        f"out-of-scope vendored libs must not be required; got {issues!r}"
    )


def test_scope_still_flags_inscope_miss():
    """Scope does NOT suppress a real in-scope coverage gap. An in-scope
    `contracts/router` bucket of 4 uncited files is still flagged, while the
    out-of-scope vendored bucket is suppressed."""
    repo_map = (
        "## contracts/core/Vault.sol\n"
        "## contracts/core/Pool.sol\n"
        "## contracts/router/Router.sol\n"
        "## contracts/router/RouterB.sol\n"
        "## contracts/router/RouterC.sol\n"
        "## contracts/router/RouterD.sol\n"
        "## utils/uniswap/libraries/FullMath.sol\n"
        "## utils/uniswap/libraries/BitMath.sol\n"
        "## utils/uniswap/libraries/TickMath.sol\n"
        "## utils/uniswap/libraries/SqrtPriceMath.sol\n"
    )
    sp = _mk_scratch(
        repo_map,
        (
            "analysis_1.md",
            "Finding at contracts/core/Vault.sol:L10 and "
            "contracts/core/Pool.sol:L20.\n",
        ),
    )
    scope = sp / "scope.txt"
    scope.write_text(
        "contracts/core/Vault.sol\n"
        "contracts/core/Pool.sol\n"
        "contracts/router/Router.sol\n"
        "contracts/router/RouterB.sol\n"
        "contracts/router/RouterC.sol\n"
        "contracts/router/RouterD.sol\n",
        encoding="utf-8",
    )
    issues = V._validate_sc_subsystem_coverage(
        sp, "thorough", scope_file=str(scope)
    )
    blob = " ".join(issues)
    assert issues, f"expected in-scope router bucket to be flagged; got {issues!r}"
    assert "Router.sol" in blob, (
        f"in-scope uncited router bucket must still be flagged; got {issues!r}"
    )
    assert "uniswap" not in blob.lower(), (
        f"out-of-scope vendored bucket must remain suppressed; got {issues!r}"
    )


def test_empty_scope_unchanged():
    """scope_file='' or None is permissive -> identical to the no-scope case."""
    sp_empty = _mk_scratch(_REPO_MAP, _CITED_ONLY_INSCOPE)
    issues_empty = V._validate_sc_subsystem_coverage(
        sp_empty, "thorough", scope_file=""
    )
    sp_none = _mk_scratch(_REPO_MAP, _CITED_ONLY_INSCOPE)
    issues_none = V._validate_sc_subsystem_coverage(
        sp_none, "thorough", scope_file=None
    )
    for issues in (issues_empty, issues_none):
        assert issues, f"empty/None scope must behave like no-scope; got {issues!r}"
        blob = " ".join(issues)
        assert "FullMath.sol" in blob and "BitMath.sol" in blob, (
            f"empty/None scope must still flag vendored libs; got {issues!r}"
        )
