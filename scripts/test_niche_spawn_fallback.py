"""Niche-agent spawn fallback: recon Required=YES lanes must spawn even when
instantiate drops them from spawn_manifest.md.

Root cause pinned here: `_required_niche_worker_jobs` reads `spawn_manifest.md`
(PRIMARY). instantiate sometimes writes NO niche rows there even though recon
flagged niche agents `Required=YES` in `template_recommendations.md`. The
deterministic fallback reads recon's authoritative 4-column niche table when the
manifest declared none, spawning ONLY `Required=YES` rows that resolve to a real
`agents/skills/niche/<slug>/SKILL.md`.

Generic shapes only — no protocol / contract / finding names.
"""

import tempfile
from pathlib import Path

import plamen_driver as D


def _scratch(**files) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, content in files.items():
        (d / name.replace("__", ".")).write_text(content, encoding="utf-8")
    return d


# A recon template_recommendations.md with an EMPTY stub `### Niche Agents`
# section first, then the REAL determinations table. DIMENSIONAL_ANALYSIS and
# SEMANTIC_CONSISTENCY_AUDIT are Required=YES and resolve to real SKILL.md;
# EVENT_COMPLETENESS is NO; NONEXISTENT_NICHE_AGENT is YES but does not resolve.
_REC_WITH_YES = (
    "# Template Recommendations\n"
    "\n"
    "### Niche Agents\n"
    "| Skill | Trigger | Required | Rationale |\n"
    "| --- | --- | --- | --- |\n"
    "| _(none extracted)_ | - | - | - |\n"
    "\n"
    "## Injectable Skills\n"
    "| Skill | Type | Inject |\n"
    "| --- | --- | --- |\n"
    "| VAULT_ACCOUNTING | vault | M4 |\n"
    "\n"
    "### Niche Agents\n"
    "| Skill | Trigger | Required | Rationale |\n"
    "| --- | --- | --- | --- |\n"
    "| `DIMENSIONAL_ANALYSIS` | `MIXED_DECIMALS` | **YES** | Strong trigger: mulDiv + mixed scale |\n"
    "| `SEMANTIC_CONSISTENCY_AUDIT` | `HAS_MULTI_CONTRACT` | YES | 2+ contracts share formulas |\n"
    "| `EVENT_COMPLETENESS` | `MISSING_EVENT` | NO | Weak signal, skip |\n"
    "| `NONEXISTENT_NICHE_AGENT` | `SOME_FLAG` | YES | Required but no SKILL.md exists |\n"
)


def test_fallback_fires_only_for_resolving_yes_rows():
    # Empty spawn_manifest (EXISTS, declares no niche rows) => fallback fires.
    d = _scratch(
        spawn_manifest__md="# Spawn Manifest\n",
        template_recommendations__md=_REC_WITH_YES,
    )
    jobs = D._required_niche_worker_jobs(d)
    outs = sorted(j["output"] for j in jobs)
    ids = sorted(j["agent_id"] for j in jobs)

    # YES + resolves to real SKILL.md => spawned.
    assert any("dimensional_analysis" in o for o in outs), outs
    assert any("semantic_consistency_audit" in o for o in outs), outs
    # NO row => NOT spawned.
    assert not any("event_completeness" in o for o in outs), outs
    # YES but unresolvable skill => NOT spawned.
    assert not any("nonexistent" in o for o in outs), outs
    # Stub `_(none extracted)_` row => never a job.
    assert not any("none" in o.lower() for o in outs), outs

    # Exactly the two valid lanes, well-formed job dicts.
    assert len(jobs) == 2, jobs
    assert ids == ["niche-dimensional-analysis", "niche-semantic-consistency-audit"], ids
    for j in jobs:
        assert j["category"] == "niche"
        assert j["output"].startswith("niche_") and j["output"].endswith("_findings.md")
        assert j["role"] and "-" not in j["role"]


def test_manifest_declares_niche_fallback_does_not_fire():
    # spawn_manifest DOES declare a niche job => fallback must NOT fire, so the
    # template_recommendations YES rows are ignored (no double-spawn).
    sm = (
        "# Spawn Manifest\n"
        "### Niche Agents\n"
        "| Niche Agent | Focus | Required | Agent ID | Output |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| EVENT_COMPLETENESS | e | YES | a1 | niche_event_completeness_findings.md |\n"
    )
    d = _scratch(
        spawn_manifest__md=sm,
        template_recommendations__md=_REC_WITH_YES,
    )
    jobs = D._required_niche_worker_jobs(d)
    outs = sorted(j["output"] for j in jobs)
    # Only the manifest-declared lane; fallback lanes absent.
    assert outs == ["niche_event_completeness_findings.md"], outs
    assert not any("dimensional" in o for o in outs), outs


def test_manifest_absent_returns_empty_no_fallback():
    # Historical behavior preserved: manifest ABSENT => [] (resume-before-
    # instantiate safety), fallback does NOT fire even if recon has YES rows.
    d = _scratch(template_recommendations__md=_REC_WITH_YES)
    assert D._required_niche_worker_jobs(d) == []


def test_fallback_dedupes_duplicate_yes_rows():
    # Same skill flagged YES twice (pipeline fragmentation) => single job.
    rec = (
        "### Niche Agents\n"
        "| Skill | Trigger | Required | Rationale |\n"
        "| --- | --- | --- | --- |\n"
        "| `DIMENSIONAL_ANALYSIS` | `MIXED_DECIMALS` | YES | first |\n"
        "| `DIMENSIONAL_ANALYSIS` | `MIXED_DECIMALS` | YES | duplicate row |\n"
    )
    d = _scratch(
        spawn_manifest__md="# Spawn Manifest\n",
        template_recommendations__md=rec,
    )
    jobs = D._required_niche_worker_jobs(d)
    assert len(jobs) == 1, jobs
    assert jobs[0]["agent_id"] == "niche-dimensional-analysis"


def test_fallback_skips_semantic_gap_lane():
    # SEMANTIC_GAP_INVESTIGATOR is spawned by the dedicated `_semantic_gap_required`
    # path; the fallback must never double-spawn it.
    rec = (
        "### Niche Agents\n"
        "| Skill | Trigger | Required | Rationale |\n"
        "| --- | --- | --- | --- |\n"
        "| `SEMANTIC_GAP_INVESTIGATOR` | `sync_gaps >= 1` | YES | gaps flagged |\n"
        "| `DIMENSIONAL_ANALYSIS` | `MIXED_DECIMALS` | YES | strong trigger |\n"
    )
    d = _scratch(
        spawn_manifest__md="# Spawn Manifest\n",
        template_recommendations__md=rec,
    )
    jobs = D._required_niche_worker_jobs(d)
    outs = sorted(j["output"] for j in jobs)
    assert not any("semantic_gap" in o for o in outs), outs
    assert outs == ["niche_dimensional_analysis_findings.md"], outs


def test_fallback_empty_when_only_stub_section():
    # Recon extracted no niche agents (only the placeholder stub) => no jobs.
    rec = (
        "### Niche Agents\n"
        "| Skill | Trigger | Required | Rationale |\n"
        "| --- | --- | --- | --- |\n"
        "| _(none extracted)_ | - | - | - |\n"
    )
    d = _scratch(
        spawn_manifest__md="# Spawn Manifest\n",
        template_recommendations__md=rec,
    )
    assert D._required_niche_worker_jobs(d) == []
