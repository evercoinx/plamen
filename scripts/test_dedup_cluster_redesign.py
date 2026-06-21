"""Tests for the dedup REDESIGN: pairwise -> in-context clustering blocks.

Covers DEDUP_REDESIGN_SPEC.md §6 TEST PLAN:

  * block builder (`_compute_dedup_candidate_blocks`): size bounds 4..18,
    multi-signal blocking, > 18 split, singleton exclusion + count, empty case,
    compat-shim pair files.
  * union-find transitive closure (A~B, B~C => {A,B,C}).
  * group-line parser (`_DEDUP_GROUP_LINE_RE` / `_parse_dedup_group_lines`):
    MERGE/KEEP parsing, malformed tolerance (never raises), bracket/case
    tolerance, single-ID rejection.
  * reduce safety: a proposed merge that FAILS the survivor-superset /
    same-severity gate is NOT applied (recall intact); zero-loss coupling
    preserves absorbed content.
  * DEGRADATION: ~80-finding inventory -> deterministic across two runs,
    drops ZERO finding IDs.
  * HALT: ~300-finding inventory -> bounded worker packet (small block file +
    small simulated decision output, block count << n^2); empty + malformed LLM
    output -> reduce/degrade returns passthrough / mechanical merges, NEVER
    raises.

Run: python scripts/test_dedup_cluster_redesign.py
  (or under pytest with $env:PLAMEN_HOME set)
"""
from __future__ import annotations

import os
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from plamen_parsers import (
    _compute_dedup_candidate_blocks,
    _dedup_extract_findings,
    _dedup_block_max,
    _DEDUP_BLOCK_MIN,
    _DEDUP_BLOCK_MAX,
    _DEDUP_AGGREGATE_SOURCE_ID_THRESHOLD,
)
from plamen_mechanical import (
    apply_llm_dedup_decisions,
    _apply_llm_group_decisions,
    _parse_dedup_group_lines,
    _DEDUP_GROUP_LINE_RE,
    _apply_mechanical_dedup_from_pairs,
)


# ─── helpers ──────────────────────────────────────────────────────

def _mktmp() -> str:
    import tempfile
    return tempfile.mkdtemp()


def _block(inv_id, title, loc, sev="High", src=None, extra=""):
    src = src or []
    s = ", ".join(src)
    # Allow bare-int IDs in callers: 7 -> INV-7 (extractor requires INV-/F- ids).
    if isinstance(inv_id, int) or (isinstance(inv_id, str) and inv_id.isdigit()):
        inv_id = f"INV-{inv_id}"
    return (
        f"### Finding [{inv_id}]: {title}\n"
        f"**Severity**: {sev}\n"
        f"**Location**: {loc}\n"
        + (f"**Source IDs**: {s}\n" if src else "")
        + (extra)
    ).rstrip()


def _write_inv(sp: Path, blocks: list[str]) -> None:
    (sp / "findings_inventory.md").write_text(
        "# Findings Inventory\n\n" + "\n\n".join(blocks) + "\n",
        encoding="utf-8",
    )


def _decisions(body: str) -> str:
    return "# Semantic Dedup Decisions\n\n## Decisions\n\n" + body + "\n"


def _block_sizes(blocks_text: str) -> list[int]:
    """Count `| INV-…` rows per `## Block N` section."""
    return [
        len(re.findall(r"(?m)^\|\s*(?:INV|F)-\d+", sec))
        for sec in re.split(r"(?m)^## Block ", blocks_text)[1:]
    ]


def _all_ids(inv_text: str) -> set[str]:
    return {f["id"] for f in _dedup_extract_findings(inv_text)}


# ════════════════════════════════════════════════════════════════════
# UNIT — block builder
# ════════════════════════════════════════════════════════════════════

def test_block_builder_size_bounds_split_large_cluster(tmp_path=None):
    """A 50-member single-signal cluster splits into blocks each 4..18."""
    sp = Path(tmp_path or _mktmp())
    blocks = [
        _block(i, "reentrancy in withdraw", "Vault.sol:withdraw:L40-L55")
        for i in range(1, 51)
    ]
    _write_inv(sp, blocks)
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 50, f"all 50 placed, got {placed}"
    txt = (sp / "dedup_blocks.md").read_text(encoding="utf-8")
    sizes = _block_sizes(txt)
    assert sizes, "expected at least one block"
    for s in sizes:
        assert s <= _dedup_block_max(), f"block {s} exceeds max {_dedup_block_max()}"
    # ceil(50/18) = 3 sub-blocks, near-equal, none empty.
    assert len(sizes) == 3, f"50/18 -> 3 sub-blocks, got {len(sizes)} ({sizes})"
    assert sum(sizes) == 50
    # block-count marker written
    assert (sp / "dedup_block_count.txt").read_text().strip() == str(len(sizes))


def test_block_builder_small_cluster_kept(tmp_path=None):
    """A 2-member cluster stays as ONE block (small blocks not padded)."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "overflow in mint", "Token.sol:mint:L10-L12"),
        _block(2, "mint overflow on cast", "Token.sol:mint:L10-L14"),
    ])
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 2
    txt = (sp / "dedup_blocks.md").read_text(encoding="utf-8")
    sizes = _block_sizes(txt)
    assert sizes == [2], f"one 2-member block expected, got {sizes}"
    # 2 < _DEDUP_BLOCK_MIN(4) but NOT padded with unrelated findings.
    assert 2 < _DEDUP_BLOCK_MIN


def test_block_builder_singleton_excluded_and_counted(tmp_path=None):
    """A finding sharing no signal is excluded from blocks but counted."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "reentrancy a", "Vault.sol:foo:L1-L5"),
        _block(2, "reentrancy a", "Vault.sol:foo:L1-L5"),
        # INV-3 shares no file / title / func / source-ID with anyone.
        _block(3, "lonely access control bug", "Other.sol:admin:L99-L120"),
    ])
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 2, f"only the 2 duplicates placed, got {placed}"
    txt = (sp / "dedup_blocks.md").read_text(encoding="utf-8")
    assert "INV-3" not in txt, "singleton must not appear in any block"
    m = re.search(r"Singletons:\s*(\d+)", txt)
    assert m and int(m.group(1)) == 1, f"expected Singletons: 1, got {txt!r}"


def test_block_builder_multi_signal_cross_file_subset(tmp_path=None):
    """Cross-file source-ID-subset pair lands in the SAME block."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "alpha", "FileA.sol:foo:L1-L5", src=["D-1"]),
        _block(2, "beta", "FileB.sol:bar:L1-L5", src=["D-1", "D-2"]),
    ])
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 2, "subset edge must group the cross-file pair"
    txt = (sp / "dedup_blocks.md").read_text(encoding="utf-8")
    assert _block_sizes(txt) == [2]


def test_block_builder_same_func_different_line(tmp_path=None):
    """Same func / different line pair lands in the same block."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "entry check", "Vault.sol:withdraw:L40-L42"),
        _block(2, "exit path bug", "Vault.sol:withdraw:L80-L85"),
    ])
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 2, "same-func edge must group the pair"
    assert _block_sizes((sp / "dedup_blocks.md").read_text(encoding="utf-8")) == [2]


def test_block_builder_aggregate_suppression(tmp_path=None):
    """A >threshold source-ID subset pair does NOT create a subset edge."""
    sp = Path(tmp_path or _mktmp())
    big = [f"D-{k}" for k in range(1, _DEDUP_AGGREGATE_SOURCE_ID_THRESHOLD + 3)]
    bigger = big + ["D-99"]
    # Cross-file (no file/func/title overlap); ONLY signal would be subset, but
    # both sides exceed the aggregate threshold -> suppressed -> no edge.
    _write_inv(sp, [
        _block(1, "aggregate one", "FileA.sol:fa:L1-L5", src=big),
        _block(2, "aggregate two", "FileB.sol:fb:L1-L5", src=bigger),
    ])
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 0, "aggregate-suppressed subset must not form a block"
    txt = (sp / "dedup_blocks.md").read_text(encoding="utf-8")
    assert "No candidate duplicate blocks found." in txt
    # Both counted as singletons.
    m = re.search(r"Singletons:\s*(\d+)", txt)
    assert m and int(m.group(1)) == 2


def test_block_builder_empty_no_shared_signal(tmp_path=None):
    """Zero shared signals -> empty file, return 0."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "alpha distinct one", "A.sol:fa:L1-L5", src=["X-1"]),
        _block(2, "beta distinct two", "B.sol:fb:L40-L60", src=["Y-9"]),
    ])
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 0
    txt = (sp / "dedup_blocks.md").read_text(encoding="utf-8")
    assert "No candidate duplicate blocks found." in txt


def test_block_builder_compat_shim_pairs_parse(tmp_path=None):
    """The compat-shim pair files are still written and still dedup.

    Uses a cross-file source-ID-subset pair so the non-supplemental mechanical
    fallback's eligibility filter (`source-id subset` / `pert lineage` signal)
    fires — this is the EXISTING fallback contract, unchanged by the redesign.
    """
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "reent a", "FileA.sol:foo:L1-L5", "Low", ["A-1"]),
        _block(2, "reent a dup", "FileB.sol:bar:L1-L5", "Low", ["A-1", "A-2"]),
    ])
    _compute_dedup_candidate_blocks(sp)
    # The live compat-shim pair file is always written. The `_full.md` deferred
    # file is conditional (only when pairs exceed the live cap), so only assert
    # the live file here.
    assert (sp / "dedup_candidate_pairs.md").exists()
    # The mechanical fallback consumes the shim pair files and dedups.
    n = _apply_mechanical_dedup_from_pairs(sp, "sc_semantic_dedup")
    assert n >= 1, f"compat-shim mechanical dedup should apply >=1, got {n}"
    out = (sp / "findings_inventory_deduped.md").read_text(encoding="utf-8")
    # Zero-loss: survivor INV-2 (source-ID superset) kept, INV-1 coupled into it
    # (its distinct content carried via the `absorbed from INV-1` marker).
    survivors = set(re.findall(r"### Finding \[((?:INV|F)-\d+)\]", out))
    coupled = set(re.findall(r"absorbed from ((?:INV|F)-\d+)", out))
    assert (survivors | coupled) >= {"INV-1", "INV-2"}, (out)


def test_block_builder_full_shim_written_when_over_cap(tmp_path=None):
    """When pairs exceed the live cap, the `_full.md` deferred shim is written."""
    sp = Path(tmp_path or _mktmp())
    os.environ["PLAMEN_DEDUP_LIVE_PAIR_CAP"] = "3"
    try:
        # 10 same-file findings all in one overlapping line range -> C(10,2)=45
        # pairs, far over the cap of 3 -> deferred pairs -> `_full.md` written.
        blocks = [
            _block(i, "shared defect", "Big.sol:hot:L10-L40")
            for i in range(1, 11)
        ]
        _write_inv(sp, blocks)
        _compute_dedup_candidate_blocks(sp)
        assert (sp / "dedup_candidate_pairs.md").exists()
        assert (sp / "dedup_candidate_pairs_full.md").exists(), (
            "deferred pairs over the cap must write the `_full.md` shim"
        )
    finally:
        os.environ.pop("PLAMEN_DEDUP_LIVE_PAIR_CAP", None)


# ════════════════════════════════════════════════════════════════════
# UNIT — group-line parser + union-find
# ════════════════════════════════════════════════════════════════════

def test_group_line_parser_basic(tmp_path=None):
    txt = "MERGE: INV-3, INV-7, INV-12\tsame-root-cause reentrancy\nKEEP: INV-9\n"
    clusters = _parse_dedup_group_lines(txt)
    assert clusters == [["INV-3", "INV-7", "INV-12"]], clusters


def test_group_line_parser_rejects_single_id(tmp_path=None):
    assert _parse_dedup_group_lines("MERGE: INV-3\n") == []
    assert _parse_dedup_group_lines("MERGE: INV-3\treason\n") == []


def test_group_line_parser_ignores_keep(tmp_path=None):
    assert _parse_dedup_group_lines("KEEP: INV-3\nKEEP: INV-4\n") == []


def test_group_line_parser_brackets_and_case(tmp_path=None):
    txt = "merge: [inv-3], [INV-7] , f-12\n"
    clusters = _parse_dedup_group_lines(txt)
    assert clusters == [["INV-3", "INV-7", "F-12"]], clusters


def test_group_line_parser_malformed_never_raises(tmp_path=None):
    junk = (
        "MERGE: garbage\n"
        "MERGE:\n"
        "this is prose, not a decision\n"
        "| INV-1 | foo |\n"
        "MERGE: INV-1\n"          # single id -> skipped
        "\x00\x01 binary junk \xff\n"
        "MERGE: INV-2, INV-5\n"   # the one valid line
    )
    clusters = _parse_dedup_group_lines(junk)
    assert clusters == [["INV-2", "INV-5"]], clusters


def test_union_find_transitivity(tmp_path=None):
    """Blocks emit MERGE A,B and MERGE B,C -> one component {A,B,C}."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "a", "x.sol:foo:L1-L5", "Low", ["S-1"]),
        _block(2, "b", "x.sol:foo:L1-L5", "Low", ["S-1", "S-2"]),
        _block(3, "c", "x.sol:foo:L1-L5", "Low", ["S-1", "S-2", "S-3"]),
    ])
    # Two separate MERGE group-lines forming a transitive chain.
    (sp / "dedup_decisions.md").write_text(
        _decisions("MERGE: INV-1, INV-2\nMERGE: INV-2, INV-3\n"),
        encoding="utf-8",
    )
    n = apply_llm_dedup_decisions(sp, "sc_semantic_dedup")
    # {INV-1,INV-2,INV-3} collapse: survivor = source-ID superset INV-3,
    # 2 absorbeds applied.
    assert n == 2, f"transitive component should apply 2 merges, got {n}"
    out = (sp / "findings_inventory_deduped.md").read_text(encoding="utf-8")
    assert "### Finding [INV-3]" in out
    assert "### Finding [INV-1]" not in out
    assert "### Finding [INV-2]" not in out
    assert "Coupled from INV-1" in out and "Coupled from INV-2" in out


# ════════════════════════════════════════════════════════════════════
# UNIT — gate still blocks a bad merge
# ════════════════════════════════════════════════════════════════════

def test_reduce_gate_rejects_non_superset(tmp_path=None):
    """A pair where neither side subsumes -> KEPT SEPARATE (no merge)."""
    sp = Path(tmp_path or _mktmp())
    # Different files, DISJOINT source IDs, different funcs: the survivor gate
    # (_resolve_dedup_survivor) has no superset direction -> reject.
    _write_inv(sp, [
        _block(1, "alpha", "A.sol:fa:L1-L5", "Low", ["A-1"]),
        _block(2, "beta", "B.sol:fb:L40-L60", "Low", ["B-9"]),
    ])
    (sp / "dedup_decisions.md").write_text(
        _decisions("MERGE: INV-1, INV-2\n"),
        encoding="utf-8",
    )
    n = _apply_llm_group_decisions(sp, "sc_semantic_dedup")
    assert n == 0, f"gate must reject non-superset merge, got {n}"
    # No deduped artifact with a removed block (passthrough preserved upstream).
    dd = sp / "findings_inventory_deduped.md"
    if dd.exists():
        out = dd.read_text(encoding="utf-8")
        assert "[INV-1]" in out and "[INV-2]" in out


def test_reduce_same_severity_guard(tmp_path=None):
    """A MERGE whose absorbed tier is strictly HIGHER than survivor is skipped.

    The fold picks the source-ID superset as survivor; if that survivor is a
    LOWER severity than the absorbed, removing the absorbed would drop a
    higher-severity finding -> same-severity guard keeps it separate.
    """
    sp = Path(tmp_path or _mktmp())
    # INV-2 is the source-ID superset (would be chosen survivor) but is LOW,
    # while INV-1 is HIGH. Merging INV-1 into INV-2 would drop the High -> skip.
    _write_inv(sp, [
        _block(1, "high one", "x.sol:foo:L1-L5", "High", ["S-1"]),
        _block(2, "low superset", "x.sol:foo:L1-L5", "Low", ["S-1", "S-2"]),
    ])
    (sp / "dedup_decisions.md").write_text(
        _decisions("MERGE: INV-1, INV-2\n"),
        encoding="utf-8",
    )
    n = _apply_llm_group_decisions(sp, "sc_semantic_dedup")
    assert n == 0, f"same-severity guard must skip cross-tier drop, got {n}"


def test_reduce_zero_loss_coupling(tmp_path=None):
    """After apply, survivor carries absorbed Location + union Source IDs."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "a", "x.sol:foo:L1-L5", "Medium", ["A-1"]),
        _block(2, "b", "x.sol:foo:L1-L5", "Medium", ["A-1", "A-2"]),
    ])
    (sp / "dedup_decisions.md").write_text(
        _decisions("MERGE: INV-1, INV-2\n"),
        encoding="utf-8",
    )
    n = apply_llm_dedup_decisions(sp, "sc_semantic_dedup")
    assert n == 1, f"expected 1 merge, got {n}"
    out = (sp / "findings_inventory_deduped.md").read_text(encoding="utf-8")
    # survivor = source-ID superset INV-2; INV-1 coupled in losslessly.
    assert "### Finding [INV-2]" in out
    assert "### Finding [INV-1]" not in out
    assert "Coupled from INV-1" in out
    # union source IDs preserved on the survivor.
    assert "A-1" in out and "A-2" in out


# ════════════════════════════════════════════════════════════════════
# DEGRADATION — ~80-finding inventory: deterministic, zero drops
# ════════════════════════════════════════════════════════════════════

def _synthetic_inventory(n_pairs: int, n_singletons: int) -> list[str]:
    """Build planted-duplicate inventory: n_pairs duplicate pairs +
    n_singletons distinct singletons. Returns a list of finding blocks.

    Each planted pair is CROSS-FILE with a source-ID SUBSET relationship
    (``{Pp-1}`` ⊂ ``{Pp-1, Pp-2}``) so it fires the `source-id subset` signal —
    the one the non-supplemental mechanical fallback acts on. This exercises the
    degrade-without-LLM floor deterministically.
    """
    blocks = []
    idx = 1
    for p in range(n_pairs):
        blocks.append(
            _block(idx, f"dup defect {p}", f"ModA{p}.sol:f{p}:L{10 + p}-L{15 + p}",
                   "Medium", [f"P{p}-1"])
        )
        idx += 1
        blocks.append(
            _block(idx, f"dup defect {p} alt", f"ModB{p}.sol:g{p}:L{20 + p}-L{25 + p}",
                   "Medium", [f"P{p}-1", f"P{p}-2"])
        )
        idx += 1
    for s in range(n_singletons):
        blocks.append(
            _block(idx, f"unique defect {s}", f"Solo{s}.sol:g{s}:L{500 + s}",
                   "Low", [f"U{s}"])
        )
        idx += 1
    return blocks


def test_degradation_80_findings_zero_drop_deterministic(tmp_path=None):
    """~80-finding inventory: block + mechanical path dedups deterministically
    and DROPS ZERO finding IDs."""
    sp1 = Path(tmp_path or _mktmp())
    sp2 = Path(_mktmp())
    blocks = _synthetic_inventory(n_pairs=30, n_singletons=20)  # 80 findings
    inv_text = "# Findings Inventory\n\n" + "\n\n".join(blocks) + "\n"
    original_ids = _all_ids(inv_text)
    assert len(original_ids) == 80, f"setup: expected 80, got {len(original_ids)}"

    def _run(sp: Path) -> str:
        _write_inv(sp, blocks)
        _compute_dedup_candidate_blocks(sp)
        # Mechanical (no LLM) path on the compat shim — the degrade floor.
        _apply_mechanical_dedup_from_pairs(sp, "sc_semantic_dedup")
        return (sp / "findings_inventory_deduped.md").read_text(encoding="utf-8")

    out1 = _run(sp1)
    out2 = _run(sp2)

    # Determinism: identical deduped output across two independent runs.
    assert out1 == out2, "dedup output must be deterministic across runs"

    # ZERO DROP: every original ID is present either as a surviving block
    # heading OR coupled into a survivor.
    survivors = set(re.findall(r"### Finding \[((?:INV|F)-\d+)\]", out1))
    coupled = set(re.findall(r"Coupled from ((?:INV|F)-\d+)", out1))
    accounted = survivors | coupled
    missing = original_ids - accounted
    assert not missing, f"findings dropped: {sorted(missing)}"
    # The 30 planted pairs collapse: survivors == 80 - 30 absorbed == 50.
    assert len(survivors) == 50, f"expected 50 survivors, got {len(survivors)}"
    assert len(coupled) == 30, f"expected 30 coupled-in, got {len(coupled)}"


# ════════════════════════════════════════════════════════════════════
# HALT — bounded packet, no O(n^2), degrade-not-raise
# ════════════════════════════════════════════════════════════════════

def test_halt_300_findings_bounded_packet(tmp_path=None):
    """~300-finding inventory -> dedup_blocks.md is small (no O(n^2)) and the
    block count is bounded (<< 300^2). The simulated single-turn decision
    output (all groups) is tiny."""
    sp = Path(tmp_path or _mktmp())
    # 150 planted duplicate pairs == 300 findings, all duplicates (worst case
    # for the OLD pair builder: ~ C(300,2) pairs; the block builder bounds it).
    blocks = _synthetic_inventory(n_pairs=150, n_singletons=0)
    _write_inv(sp, blocks)
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 300, f"all 300 placed, got {placed}"

    blocks_text = (sp / "dedup_blocks.md").read_text(encoding="utf-8")
    # Bounded by construction: the live LLM input is small. Each of 150 pairs is
    # its own 2-member block; the file is a few tens of KB, NOT megabytes. It
    # MUST stay under the driver's 200KB budget-guard sentinel (plamen_driver.py
    # _DEDUP_BLOCKS_SIZE_SENTINEL) so a legitimate 300-finding inventory takes
    # the LIVE path rather than tripping the defense-in-depth skip.
    assert len(blocks_text) < 200 * 1024, (
        f"dedup_blocks.md too large (would trip driver sentinel): "
        f"{len(blocks_text)} bytes"
    )
    n_blocks = int((sp / "dedup_block_count.txt").read_text().strip())
    # block count must be far below the O(n^2) pair count (300^2 = 90000).
    assert n_blocks < 300 * 300, f"block count not bounded: {n_blocks}"
    assert n_blocks <= 300, f"block count should be O(n), got {n_blocks}"

    # Simulated single-turn decision output: one MERGE line per pair.
    sim_decisions = "\n".join(
        f"MERGE: INV-{2*p+1}, INV-{2*p+2}" for p in range(150)
    )
    assert len(sim_decisions) < 10 * 1024, (
        f"simulated decision output too large: {len(sim_decisions)} bytes"
    )


def test_halt_empty_llm_output_passthrough_no_raise(tmp_path=None):
    """Empty / PASSTHROUGH decisions -> apply returns 0, no raise."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "a", "x.sol:foo:L1-L5", "Low", ["A-1"]),
        _block(2, "b", "x.sol:foo:L1-L5", "Low", ["A-1", "A-2"]),
    ])
    (sp / "dedup_decisions.md").write_text("PASSTHROUGH\n", encoding="utf-8")
    n = apply_llm_dedup_decisions(sp, "sc_semantic_dedup")
    assert n == 0, f"passthrough must apply 0 merges, got {n}"


def test_halt_garbage_llm_output_no_raise(tmp_path=None):
    """Random / malformed decisions -> 0 merges, no raise."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "a", "x.sol:foo:L1-L5", "Low", ["A-1"]),
        _block(2, "b", "x.sol:foo:L1-L5", "Low", ["A-1", "A-2"]),
    ])
    (sp / "dedup_decisions.md").write_text(
        "lorem ipsum dolor\nMERGE: not-an-id\n### random heading\n"
        "\x00\x07 binary \xfe garbage\nMERGE: INV-1\n",
        encoding="utf-8",
    )
    n = apply_llm_dedup_decisions(sp, "sc_semantic_dedup")
    assert n == 0, f"garbage decisions must apply 0 merges, got {n}"


def test_halt_block_builder_never_raises_on_bad_inventory(tmp_path=None):
    """A malformed inventory degrades to empty blocks + return 0, never raises."""
    sp = Path(tmp_path or _mktmp())
    (sp / "findings_inventory.md").write_text(
        "garbage \x00\x01 not markdown \xff\n### not a finding heading\n",
        encoding="utf-8",
    )
    placed = _compute_dedup_candidate_blocks(sp)  # must not raise
    assert placed == 0
    assert (sp / "dedup_blocks.md").exists()


def test_halt_block_builder_missing_inventory_returns_zero(tmp_path=None):
    """No inventory -> return 0, write empty block file, never raise."""
    sp = Path(tmp_path or _mktmp())
    placed = _compute_dedup_candidate_blocks(sp)
    assert placed == 0
    assert (sp / "dedup_blocks.md").exists()


def test_halt_mechanical_only_path_produces_valid_artifact(tmp_path=None):
    """End-to-end via mechanical path only (no LLM) -> valid deduped artifact."""
    sp = Path(tmp_path or _mktmp())
    _write_inv(sp, [
        _block(1, "reent a", "FileA.sol:foo:L1-L5", "Low", ["A-1"]),
        _block(2, "reent a dup", "FileB.sol:bar:L1-L5", "Low", ["A-1", "A-2"]),
        _block(3, "unrelated", "Other.sol:baz:L99-L120", "Low", ["Z-1"]),
    ])
    _compute_dedup_candidate_blocks(sp)
    n = _apply_mechanical_dedup_from_pairs(sp, "sc_semantic_dedup")
    assert n >= 1
    out = (sp / "findings_inventory_deduped.md").read_text(encoding="utf-8")
    # Valid artifact: header present, survivor + unrelated present, no dropped ID.
    assert out.startswith("# Findings Inventory")
    survivors = set(re.findall(r"### Finding \[((?:INV|F)-\d+)\]", out))
    coupled = set(re.findall(r"Coupled from ((?:INV|F)-\d+)", out))
    assert {"INV-1", "INV-2", "INV-3"} <= (survivors | coupled)


# ─── runner ───────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [f for f in dir() if f.startswith("test_")]
    passed = failed = 0
    for name in sorted(tests):
        print(f"\n--- {name} ---")
        try:
            globals()[name]()
            print("  PASS")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            traceback.print_exc()
            failed += 1
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{'=' * 40}\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
