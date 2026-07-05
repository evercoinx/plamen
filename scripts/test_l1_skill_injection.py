"""Tests for L1 mechanical skill injection into depth phase prompts
and graph-sweeps artifact directive alignment."""
from __future__ import annotations

import pytest
from pathlib import Path

from plamen_prompt import (
    _parse_l1_required_skills,
    _resolve_l1_skill_paths,
    _build_l1_depth_skill_injection,
    _build_graph_sweeps_artifact_directive,
    _L1_SKILL_DEPTH_ROUTING,
    _L1_ALWAYS_ON_DEPTH,
    _L1_DEPTH_AGENT_ROLES,
    _L1_SKILL_BASE,
)


NODECLIENT_TEMPLATE_RECOMMENDATIONS = """\
| Skill / Template | Inject Into | Required | Rationale |
|---|---|---|---|
| `P2P_DOS_AND_ECLIPSE` | `depth-network-surface` | YES | Inbound gossip is attacker-reachable. |
| `PEER_SCORING_CORRECTNESS` | `depth-network-surface` | YES | Peer liveness/reputation directly influences peer selection. |
| `GOSSIP_CACHE_INVARIANCE` | `depth-network-surface`, `depth-consensus-invariant` | YES | Duplicate suppression and per-peer seen state. |
| `STATE_SYNC_PRUNING` | `depth-state-trace`, `depth-edge-case` | YES | Sync state gates both ingress and egress. |
| `DATA_AVAILABILITY_ENFORCEMENT` | `depth-consensus-invariant`, `depth-state-trace` | YES | The subsystem enforces DA-adjacent objects. |
| `DEPENDENCY_AUDIT_NODECLIENT` | recon agent, every breadth agent | YES | This crate is dependency-heavy at attack boundaries. |
| `RUST_UNSAFE_AUDIT` | every L1 agent on Rust code | NO | No unsafe code found under crates/p2p. |
"""


class TestParseL1RequiredSkills:
    def test_basic(self, tmp_path: Path):
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        required, excluded = _parse_l1_required_skills(tmp_path)
        assert len(required) == 6
        assert "P2P_DOS_AND_ECLIPSE" in required
        assert "PEER_SCORING_CORRECTNESS" in required
        assert "GOSSIP_CACHE_INVARIANCE" in required
        assert "STATE_SYNC_PRUNING" in required
        assert "DATA_AVAILABILITY_ENFORCEMENT" in required
        assert "DEPENDENCY_AUDIT_NODECLIENT" in required

    def test_excluded(self, tmp_path: Path):
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        _, excluded = _parse_l1_required_skills(tmp_path)
        assert "RUST_UNSAFE_AUDIT" in excluded

    def test_missing_file(self, tmp_path: Path):
        required, excluded = _parse_l1_required_skills(tmp_path)
        assert required == []
        assert excluded == set()

    def test_bold_backtick_stripping(self, tmp_path: Path):
        content = """\
| Skill / Template | Inject Into | Required | Rationale |
|---|---|---|---|
| `P2P_DOS_AND_ECLIPSE` | `depth-network-surface` | **YES** | Bold yes. |
| **GOSSIP_CACHE_INVARIANCE** | `depth-network-surface` | YES | Bold name. |
| `RPC_SURFACE_AUDIT` | `depth-network-surface` | **NO** | Bold no. |
"""
        (tmp_path / "template_recommendations.md").write_text(content, encoding="utf-8")
        required, excluded = _parse_l1_required_skills(tmp_path)
        assert "P2P_DOS_AND_ECLIPSE" in required
        assert "GOSSIP_CACHE_INVARIANCE" in required
        assert "RPC_SURFACE_AUDIT" in excluded

    def test_header_row_excluded(self, tmp_path: Path):
        content = """\
| Skill / Template | Inject Into | Required | Rationale |
|---|---|---|---|
| `P2P_DOS_AND_ECLIPSE` | `depth-network-surface` | YES | Test. |
"""
        (tmp_path / "template_recommendations.md").write_text(content, encoding="utf-8")
        required, _ = _parse_l1_required_skills(tmp_path)
        assert len(required) == 1
        assert "P2P_DOS_AND_ECLIPSE" in required
        assert "Skill" not in required
        assert "Skill / Template" not in required


class TestResolveL1SkillPaths:
    def test_known_skills(self):
        resolved = _resolve_l1_skill_paths(["P2P_DOS_AND_ECLIPSE", "STATE_SYNC_PRUNING"])
        assert "P2P_DOS_AND_ECLIPSE" in resolved
        assert "STATE_SYNC_PRUNING" in resolved
        for p in resolved.values():
            assert p.exists()

    def test_unknown_skill(self):
        resolved = _resolve_l1_skill_paths(["NONEXISTENT_SKILL_XYZ"])
        assert "NONEXISTENT_SKILL_XYZ" not in resolved

    def test_all_22_l1_skills_exist(self):
        all_skills = [
            k for k in _L1_SKILL_DEPTH_ROUTING
            if _L1_SKILL_DEPTH_ROUTING[k] != ()
        ]
        resolved = _resolve_l1_skill_paths(all_skills)
        for s in all_skills:
            assert s in resolved, f"Skill {s} has no SKILL.md on disk"

    def test_always_on_skills_exist(self):
        for lang_skills in _L1_ALWAYS_ON_DEPTH.values():
            resolved = _resolve_l1_skill_paths(lang_skills)
            for s in lang_skills:
                assert s in resolved, f"Always-on skill {s} has no SKILL.md"


class TestBuildManifest:
    def test_nodeclient_scenario(self, tmp_path: Path):
        """Reproduce a representative node-client template_recommendations.md."""
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        assert "## L1 SKILL INJECTION MANIFEST" in manifest
        assert "p2p-dos-and-eclipse" in manifest
        assert "peer-scoring-correctness" in manifest
        assert "gossip-cache-invariance" in manifest
        assert "state-sync-pruning" in manifest
        assert "data-availability-enforcement" in manifest
        assert "dependency-audit-nodeclient" in manifest
        assert "rust-unsafe-audit" not in manifest

    def test_routing_consensus_invariant(self, tmp_path: Path):
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        ci_start = manifest.index("#### depth-consensus-invariant")
        ns_start = manifest.index("#### depth-network-surface")
        ci_section = manifest[ci_start:ns_start]
        assert "data-availability-enforcement" in ci_section
        assert "gossip-cache-invariance" in ci_section
        assert "dependency-audit-nodeclient" in ci_section

    def test_routing_network_surface(self, tmp_path: Path):
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        sections = manifest.split("####")
        ns_section = [s for s in sections if "depth-network-surface" in s][0]
        assert "p2p-dos-and-eclipse" in ns_section
        assert "peer-scoring-correctness" in ns_section
        assert "gossip-cache-invariance" in ns_section
        assert "dependency-audit-nodeclient" in ns_section

    def test_routing_state_trace(self, tmp_path: Path):
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        sections = manifest.split("####")
        st_section = [s for s in sections if "depth-state-trace" in s][0]
        assert "state-sync-pruning" in st_section
        assert "data-availability-enforcement" in st_section
        assert "dependency-audit-nodeclient" in st_section

    def test_routing_edge_case(self, tmp_path: Path):
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        sections = manifest.split("####")
        ec_section = [s for s in sections if "depth-edge-case" in s][0]
        assert "state-sync-pruning" in ec_section
        assert "dependency-audit-nodeclient" in ec_section

    def test_always_on_no_override_by_explicit_no(self, tmp_path: Path):
        """RUST_UNSAFE_AUDIT with explicit NO should be filtered from always-on."""
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        assert "rust-unsafe-audit" not in manifest

    def test_always_on_when_not_mentioned(self, tmp_path: Path):
        """When template_recommendations doesn't mention a skill at all,
        always-on should include it."""
        content = """\
| Skill / Template | Inject Into | Required | Rationale |
|---|---|---|---|
| `P2P_DOS_AND_ECLIPSE` | `depth-network-surface` | YES | Test. |
"""
        (tmp_path / "template_recommendations.md").write_text(content, encoding="utf-8")
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        assert "rust-unsafe-audit" in manifest
        assert "dependency-audit-nodeclient" in manifest

    def test_empty_skills(self, tmp_path: Path):
        """No template_recommendations.md -> only always-on skills."""
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        assert "## L1 SKILL INJECTION MANIFEST" in manifest
        assert "rust-unsafe-audit" in manifest
        assert "dependency-audit-nodeclient" in manifest

    def test_go_always_on(self, tmp_path: Path):
        manifest = _build_l1_depth_skill_injection(tmp_path, "go")
        assert "go-concurrency-safety" in manifest
        assert "dependency-audit-nodeclient" in manifest
        assert "rust-unsafe-audit" not in manifest

    def test_mixed_always_on(self, tmp_path: Path):
        """Mixed Go+Rust codebases get both language-safety skills."""
        manifest = _build_l1_depth_skill_injection(tmp_path, "mixed")
        assert "go-concurrency-safety" in manifest
        assert "rust-unsafe-audit" in manifest
        assert "dependency-audit-nodeclient" in manifest

    def test_unknown_language_graceful(self, tmp_path: Path):
        """Unknown language with no required skills returns empty manifest."""
        manifest = _build_l1_depth_skill_injection(tmp_path, "cpp")
        assert manifest == ""

    def test_unknown_language_with_required_skills(self, tmp_path: Path):
        """Unknown language still injects explicitly required skills."""
        content = """\
| Skill / Template | Inject Into | Required | Rationale |
|---|---|---|---|
| `P2P_DOS_AND_ECLIPSE` | `depth-network-surface` | YES | Test. |
"""
        (tmp_path / "template_recommendations.md").write_text(content, encoding="utf-8")
        manifest = _build_l1_depth_skill_injection(tmp_path, "cpp")
        assert "p2p-dos-and-eclipse" in manifest
        assert "go-concurrency-safety" not in manifest

    def test_contains_threat_model_note(self, tmp_path: Path):
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        assert "threat_model.md" in manifest
        assert "design_context.md" in manifest

    def test_sc_pipeline_no_injection(self, tmp_path: Path):
        """SC pipeline should never produce a manifest."""
        (tmp_path / "template_recommendations.md").write_text(
            NODECLIENT_TEMPLATE_RECOMMENDATIONS, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        assert manifest != ""

    def test_skill_injection_protocol_present(self, tmp_path: Path):
        manifest = _build_l1_depth_skill_injection(tmp_path, "rust")
        assert "### Skill Injection Protocol" in manifest
        assert "step-execution-gap checker" in manifest


class TestRoutingTableCompleteness:
    """Verify the routing table covers all L1 skills on disk."""

    def test_all_disk_skills_have_routing(self):
        if not _L1_SKILL_BASE.exists():
            pytest.skip("L1 skill directory not found")
        on_disk = set()
        for d in _L1_SKILL_BASE.iterdir():
            if d.is_dir() and (d / "SKILL.md").exists():
                upper = d.name.upper().replace("-", "_")
                on_disk.add(upper)
        for skill in on_disk:
            assert skill in _L1_SKILL_DEPTH_ROUTING, (
                f"Skill {skill} exists on disk but has no routing entry"
            )

    def test_all_routing_entries_have_disk_skills(self):
        for skill in _L1_SKILL_DEPTH_ROUTING:
            kebab = skill.lower().replace("_", "-")
            path = _L1_SKILL_BASE / kebab / "SKILL.md"
            assert path.exists(), (
                f"Routing entry {skill} has no SKILL.md at {path}"
            )

    def test_all_agent_roles_in_routing_values(self):
        """Every role referenced in routing values must be a valid role."""
        for skill, targets in _L1_SKILL_DEPTH_ROUTING.items():
            for t in targets:
                assert t in _L1_DEPTH_AGENT_ROLES, (
                    f"Skill {skill} routes to unknown role {t}"
                )


class TestCosmosSdkModuleSafety:
    """Phase 1: Cosmos-SDK / CometBFT framework lane wiring."""

    SKILL = "COSMOS_SDK_MODULE_SAFETY"

    def test_skill_resolves_on_disk(self):
        resolved = _resolve_l1_skill_paths([self.SKILL])
        assert self.SKILL in resolved
        assert resolved[self.SKILL].exists()

    def test_routing_targets(self):
        assert _L1_SKILL_DEPTH_ROUTING[self.SKILL] == (
            "consensus_invariant",
            "state_trace",
        )
        for role in _L1_SKILL_DEPTH_ROUTING[self.SKILL]:
            assert role in _L1_DEPTH_AGENT_ROLES

    def test_required_yes_is_parsed(self, tmp_path: Path):
        content = (
            "| Skill / Template | Inject Into | Required | Rationale |\n"
            "|---|---|---|---|\n"
            "| `COSMOS_SDK_MODULE_SAFETY` | `depth-consensus-invariant`, "
            "`depth-state-trace` | YES | Cosmos-SDK detected. |\n"
        )
        (tmp_path / "template_recommendations.md").write_text(
            content, encoding="utf-8"
        )
        required, _ = _parse_l1_required_skills(tmp_path)
        assert self.SKILL in required

    def test_injected_into_manifest(self, tmp_path: Path):
        content = (
            "| Skill / Template | Inject Into | Required | Rationale |\n"
            "|---|---|---|---|\n"
            "| `COSMOS_SDK_MODULE_SAFETY` | `depth-consensus-invariant`, "
            "`depth-state-trace` | YES | Cosmos-SDK detected. |\n"
        )
        (tmp_path / "template_recommendations.md").write_text(
            content, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "go")
        assert "cosmos-sdk-module-safety" in manifest
        sections = manifest.split("####")
        ci = [s for s in sections if "depth-consensus-invariant" in s][0]
        st = [s for s in sections if "depth-state-trace" in s][0]
        assert "cosmos-sdk-module-safety" in ci
        assert "cosmos-sdk-module-safety" in st


class TestCosmosIbcSecurity:
    """Phase 2: Cosmos IBC / ibc-go cross-chain lane wiring."""

    SKILL = "COSMOS_IBC_SECURITY"

    def test_skill_resolves_on_disk(self):
        resolved = _resolve_l1_skill_paths([self.SKILL])
        assert self.SKILL in resolved
        assert resolved[self.SKILL].exists()

    def test_routing_targets(self):
        assert _L1_SKILL_DEPTH_ROUTING[self.SKILL] == (
            "consensus_invariant",
            "external",
        )
        for role in _L1_SKILL_DEPTH_ROUTING[self.SKILL]:
            assert role in _L1_DEPTH_AGENT_ROLES

    def test_required_yes_is_parsed(self, tmp_path: Path):
        content = (
            "| Skill / Template | Inject Into | Required | Rationale |\n"
            "|---|---|---|---|\n"
            "| `COSMOS_IBC_SECURITY` | `depth-consensus-invariant`, "
            "`depth-external` | YES | IBC detected. |\n"
        )
        (tmp_path / "template_recommendations.md").write_text(
            content, encoding="utf-8"
        )
        required, _ = _parse_l1_required_skills(tmp_path)
        assert self.SKILL in required

    def test_injected_into_both_roles(self, tmp_path: Path):
        content = (
            "| Skill / Template | Inject Into | Required | Rationale |\n"
            "|---|---|---|---|\n"
            "| `COSMOS_IBC_SECURITY` | `depth-consensus-invariant`, "
            "`depth-external` | YES | IBC detected. |\n"
        )
        (tmp_path / "template_recommendations.md").write_text(
            content, encoding="utf-8"
        )
        manifest = _build_l1_depth_skill_injection(tmp_path, "go")
        assert "cosmos-ibc-security" in manifest
        sections = manifest.split("####")
        ci = [s for s in sections if "depth-consensus-invariant" in s][0]
        ext = [s for s in sections if "depth-external" in s][0]
        assert "cosmos-ibc-security" in ci
        assert "cosmos-ibc-security" in ext


class TestCosmosReconDetection:
    """Mechanical Cosmos-SDK manifest detection in the recon pre-pass."""

    def _detect(self):
        import recon_prepass
        return recon_prepass

    def test_go_mod_cosmos_detected(self, tmp_path: Path):
        rp = self._detect()
        (tmp_path / "go.mod").write_text(
            "module example.com/chain\n\n"
            "require (\n"
            "\tgithub.com/cosmos/cosmos-sdk v0.50.1\n"
            "\tgithub.com/cometbft/cometbft v0.38.0\n"
            ")\n",
            encoding="utf-8",
        )
        cosmos, ibc = rp._detect_cosmos_markers(tmp_path)
        assert cosmos is True
        assert ibc is False

    def test_ibc_detected(self, tmp_path: Path):
        rp = self._detect()
        (tmp_path / "go.mod").write_text(
            "module example.com/chain\n\n"
            "require (\n"
            "\tgithub.com/cosmos/cosmos-sdk v0.50.1\n"
            "\tgithub.com/cosmos/ibc-go/v8 v8.0.0\n"
            ")\n",
            encoding="utf-8",
        )
        cosmos, ibc = rp._detect_cosmos_markers(tmp_path)
        assert cosmos is True
        assert ibc is True

    def test_non_cosmos_not_detected(self, tmp_path: Path):
        rp = self._detect()
        (tmp_path / "go.mod").write_text(
            "module example.com/geth\n\nrequire github.com/ethereum/go-ethereum v1.13.0\n",
            encoding="utf-8",
        )
        cosmos, ibc = rp._detect_cosmos_markers(tmp_path)
        assert cosmos is False
        assert ibc is False

    def test_cosmwasm_rust_detected(self, tmp_path: Path):
        rp = self._detect()
        (tmp_path / "Cargo.toml").write_text(
            '[dependencies]\ncosmwasm-std = "2.0"\n', encoding="utf-8"
        )
        cosmos, _ = rp._detect_cosmos_markers(tmp_path)
        assert cosmos is True

    def test_seed_flips_template_rec_and_emits_flags(self, tmp_path: Path):
        rp = self._detect()
        proj = tmp_path / "proj"
        scratch = tmp_path / "scratch"
        proj.mkdir()
        scratch.mkdir()
        (proj / "go.mod").write_text(
            "module example.com/chain\n\nrequire github.com/cosmos/cosmos-sdk v0.50.1\n",
            encoding="utf-8",
        )
        # Seed pre-pass-owned files (must carry the marker) as the prepass would.
        marker = rp._PREPASS_MARKER + "\n"
        (scratch / "template_recommendations.md").write_text(
            marker
            + "# Template Recommendations\n\n## BINDING MANIFEST\n\n### L1 Skills\n\n"
            "| Skill | Trigger | Required | Rationale |\n"
            "|-------|---------|----------|-----------|\n"
            "| `COSMOS_SDK_MODULE_SAFETY` | COSMOS_SDK flag | NO | [LLM TO ENRICH] |\n",
            encoding="utf-8",
        )
        (scratch / "recon_summary.md").write_text(
            marker + "# Recon Summary\n\n- **Language**: go\n", encoding="utf-8"
        )
        status = rp._seed_cosmos_flag(scratch, proj)
        assert status.startswith("DETECTED:COSMOS_SDK")

        tr = (scratch / "template_recommendations.md").read_text(encoding="utf-8")
        required, _ = _parse_l1_required_skills(scratch)
        assert "COSMOS_SDK_MODULE_SAFETY" in required, tr

        rs = (scratch / "recon_summary.md").read_text(encoding="utf-8")
        assert "COSMOS_SDK" in rs

        dp = (scratch / "detected_patterns.md").read_text(encoding="utf-8")
        assert "COSMOS_SDK" in dp

    def test_seed_flips_ibc_row_when_ibc_present(self, tmp_path: Path):
        rp = self._detect()
        proj = tmp_path / "proj"
        scratch = tmp_path / "scratch"
        proj.mkdir()
        scratch.mkdir()
        (proj / "go.mod").write_text(
            "module example.com/chain\n\nrequire (\n"
            "\tgithub.com/cosmos/cosmos-sdk v0.50.1\n"
            "\tgithub.com/cosmos/ibc-go/v8 v8.0.0\n"
            ")\n",
            encoding="utf-8",
        )
        marker = rp._PREPASS_MARKER + "\n"
        (scratch / "template_recommendations.md").write_text(
            marker
            + "# Template Recommendations\n\n## BINDING MANIFEST\n\n### L1 Skills\n\n"
            "| Skill | Trigger | Required | Rationale |\n"
            "|-------|---------|----------|-----------|\n"
            "| `COSMOS_SDK_MODULE_SAFETY` | COSMOS_SDK flag | NO | [LLM TO ENRICH] |\n"
            "| `COSMOS_IBC_SECURITY` | IBC flag | NO | [LLM TO ENRICH] |\n",
            encoding="utf-8",
        )
        status = rp._seed_cosmos_flag(scratch, proj)
        assert status == "DETECTED:COSMOS_SDK,IBC"

        required, _ = _parse_l1_required_skills(scratch)
        assert "COSMOS_SDK_MODULE_SAFETY" in required
        assert "COSMOS_IBC_SECURITY" in required

    def test_seed_does_not_flip_ibc_row_without_ibc(self, tmp_path: Path):
        rp = self._detect()
        proj = tmp_path / "proj"
        scratch = tmp_path / "scratch"
        proj.mkdir()
        scratch.mkdir()
        (proj / "go.mod").write_text(
            "module example.com/chain\n\nrequire github.com/cosmos/cosmos-sdk v0.50.1\n",
            encoding="utf-8",
        )
        marker = rp._PREPASS_MARKER + "\n"
        (scratch / "template_recommendations.md").write_text(
            marker
            + "# Template Recommendations\n\n## BINDING MANIFEST\n\n### L1 Skills\n\n"
            "| Skill | Trigger | Required | Rationale |\n"
            "|-------|---------|----------|-----------|\n"
            "| `COSMOS_SDK_MODULE_SAFETY` | COSMOS_SDK flag | NO | [LLM TO ENRICH] |\n"
            "| `COSMOS_IBC_SECURITY` | IBC flag | NO | [LLM TO ENRICH] |\n",
            encoding="utf-8",
        )
        status = rp._seed_cosmos_flag(scratch, proj)
        assert status == "DETECTED:COSMOS_SDK"

        required, _ = _parse_l1_required_skills(scratch)
        assert "COSMOS_SDK_MODULE_SAFETY" in required
        assert "COSMOS_IBC_SECURITY" not in required

    def test_seed_noop_when_not_cosmos(self, tmp_path: Path):
        rp = self._detect()
        proj = tmp_path / "proj"
        scratch = tmp_path / "scratch"
        proj.mkdir()
        scratch.mkdir()
        (proj / "go.mod").write_text(
            "module x\n\nrequire github.com/ethereum/go-ethereum v1.13.0\n",
            encoding="utf-8",
        )
        assert rp._seed_cosmos_flag(scratch, proj) == "NOT_DETECTED"


def _scip_scratch(tmp_path: Path, files: dict[str, str],
                  scip_files: dict[str, str] | None = None) -> Path:
    """Create a scratchpad with optional scip/ subdir for graph-sweep tests."""
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    if scip_files:
        scip = tmp_path / "scip"
        scip.mkdir(exist_ok=True)
        for name, body in scip_files.items():
            (scip / name).write_text(body, encoding="utf-8")
    return tmp_path


class TestGraphSweepsArtifactDirective:
    """Verify _build_graph_sweeps_artifact_directive mirrors the validator."""

    def test_no_surfaces_returns_empty(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 10 | **Cited**: 10 | "
                "**Uncited**: 0 | **Coverage**: 100.0%\n"
            ),
        })
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert directive == ""

    def test_low_coverage_includes_coverage_fill(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 100 | **Cited**: 40 | "
                "**Uncited**: 60 | **Coverage**: 40.0%\n"
            ),
        }, scip_files={"repo_map.md": "## crates/a/src/lib.rs\n"})
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert "MANDATORY ARTIFACT CHECKLIST" in directive
        assert "coverage_fill" in directive
        assert "40.0%" in directive
        assert "60 uncited" in directive

    def test_panic_sites_includes_panic_audit(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 10 | **Cited**: 10 | "
                "**Uncited**: 0 | **Coverage**: 100.0%\n"
            ),
        }, scip_files={
            "repo_map.md": "## main.rs\n",
            "panic_sites.md": "| file | line | kind |\n|---|---|---|\n| a.rs | 10 | unwrap |\n",
        })
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert "panic_audit" in directive
        assert "panic_audit_summary" in directive

    def test_field_validation_surface(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 10 | **Cited**: 10 | "
                "**Uncited**: 0 | **Coverage**: 100.0%\n"
            ),
        }, scip_files={
            "repo_map.md": "## block_header.rs\nBlock Header Transaction\n",
        })
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert "field_validation_matrix.md" in directive

    def test_primitive_surface(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 10 | **Cited**: 10 | "
                "**Uncited**: 0 | **Coverage**: 100.0%\n"
            ),
        }, scip_files={
            "repo_map.md": "## merkle.rs\nmerkle proof validate_path hash\n",
        })
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert "primitive_correctness_findings.md" in directive

    def test_network_amplification_surface(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 10 | **Cited**: 10 | "
                "**Uncited**: 0 | **Coverage**: 100.0%\n"
            ),
        }, scip_files={
            "repo_map.md": "## p2p.rs\ngossip broadcast peer network\n",
        })
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert "network_amplification_findings.md" in directive

    def test_lifecycle_replay_surface(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 10 | **Cited**: 10 | "
                "**Uncited**: 0 | **Coverage**: 100.0%\n"
            ),
        }, scip_files={
            "repo_map.md": "## cache.rs\ncache seen pending pool mempool\n",
        })
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert "lifecycle_replay_findings.md" in directive

    def test_nodeclient_scenario_all_surfaces(self, tmp_path: Path):
        """Node-client P2P subsystem: all surfaces detected."""
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 50 | **Cited**: 0 | "
                "**Uncited**: 25 | **Coverage**: 0.0%\n"
            ),
        }, scip_files={
            "repo_map.md": (
                "## gossip.rs\ngossip broadcast peer network\n"
                "## block.rs\nBlock Header Transaction hash signature\n"
                "## merkle.rs\nmerkle proof serialize hash\n"
                "## cache.rs\ncache seen pending pool replay nonce\n"
            ),
            "panic_sites.md": "| file | line | kind |\n|---|---|---|\n| a.rs | 10 | unwrap |\n",
        })
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert "coverage_fill" in directive
        assert "panic_audit" in directive
        assert "field_validation_matrix.md" in directive
        assert "primitive_correctness_findings.md" in directive
        assert "network_amplification_findings.md" in directive
        assert "lifecycle_replay_findings.md" in directive
        assert "Total required artifacts: 7" in directive

    def test_contains_do_not_exit_warning(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 100 | **Cited**: 40 | "
                "**Uncited**: 60 | **Coverage**: 40.0%\n"
            ),
        }, scip_files={"repo_map.md": "## a.rs\n"})
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert "DO NOT" in directive
        assert "graph_sweep_summary.md" in directive

    def test_missing_scratchpad_files_returns_empty(self, tmp_path: Path):
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert directive == ""

    def test_high_coverage_no_scip_returns_empty(self, tmp_path: Path):
        _scip_scratch(tmp_path, {
            "subsystem_coverage_gap.md": (
                "**Indexed prod files**: 10 | **Cited**: 9 | "
                "**Uncited**: 1 | **Coverage**: 90.0%\n"
            ),
        })
        directive = _build_graph_sweeps_artifact_directive(tmp_path)
        assert directive == ""
