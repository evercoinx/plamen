import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plamen_types import plamen_home  # noqa: E402

ROOT = plamen_home()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_language_toolchain_registry_exists_and_paths_are_valid():
    registry = json.loads(_read(ROOT / "rules" / "language-toolchain-registry.json"))
    assert registry["languages"]["sui"]["test_filter_mode"] == "positional_filter"
    assert registry["languages"]["sui"]["fuzz_engines"][0]["template"] == "#[random_test]"
    soroban_template = registry["languages"]["soroban"]["fuzz_engines"][0]["template_path"]
    assert (ROOT / soroban_template).exists()


def test_skill_registry_resolves_aliases_and_paths():
    registry = json.loads(_read(ROOT / "rules" / "skill-registry.json"))
    storage = registry["standard_skills"]["STORAGE_LIFECYCLE"]
    assert "STORAGE_TTL_SAFETY" in storage["aliases"]
    external = registry["standard_skills"]["EXTERNAL_PRECONDITION_AUDIT"]
    assert "ORACLE_ANALYSIS" in external["aliases"]
    for section in ("standard_skills", "injectables", "niche_agents"):
        for item in registry[section].values():
            assert (ROOT / item["path"]).exists(), item["path"]


def test_sui_prompts_do_not_use_stale_filter_or_fuzzer_claim():
    files = [
        ROOT / "prompts" / "sui" / "phase5-verification-prompt.md",
        ROOT / "prompts" / "sui" / "phase1-recon-prompt.md",
        ROOT / "prompts" / "sui" / "v2" / "phase1-recon-prompt.md",
        ROOT / "prompts" / "sui" / "phase4b-depth-driver.md",
        ROOT / "prompts" / "sui" / "phase4b-loop.md",
    ]
    text = "\n".join(_read(path) for path in files)
    poc = _read(ROOT / "rules" / "phase5-poc-execution.md")
    sui_row = next(line for line in poc.splitlines() if line.startswith("| **Sui**"))
    sui_guidance = poc[poc.find("### Sui -"):]
    assert "sui move test --filter" not in text
    assert "No built-in fuzzer" not in text
    assert "Move lacks a fuzzer" not in sui_guidance
    assert "SKIPPED (Sui)" not in text
    assert "sui move test --filter" not in sui_row
    assert "No built-in fuzzer" not in sui_row
    assert "#[random_test]" in text + sui_row + sui_guidance


def test_injectable_prompts_are_append_only_not_spawned():
    for lang in ("evm", "solana", "aptos", "sui", "soroban"):
        text = _read(ROOT / "prompts" / lang / "phase4b-depth-driver.md")
        loop = _read(ROOT / "prompts" / lang / "phase4b-loop.md")
        combined = text + "\n" + loop
        for stale in (
            "injectable_{domain}_findings.md",
            "spawn dedicated sonnet agents",
            "injectable_investigation_agent",
            "depth_{d}_injectable_findings.md",
            "injectable_outputs",
            "len(injectable_agents)",
            "N injectable investigation agents",
        ):
            assert stale not in combined
        assert "append-only methodology" in text


def test_semantic_gap_trigger_includes_cluster_gaps_everywhere():
    for lang in ("evm", "solana", "aptos", "sui", "soroban"):
        text = _read(ROOT / "prompts" / lang / "phase4b-loop.md")
        trigger_line = next(
            line for line in text.splitlines()
            if "SEMANTIC_GAP_INVESTIGATOR MUST be in niche_agents" in line
        )
        assert "cluster_gaps >= 1" in trigger_line


def test_soroban_recon_uses_registered_skill_keys():
    registry = json.loads(_read(ROOT / "rules" / "skill-registry.json"))
    known = set(registry["standard_skills"])
    for item in registry["standard_skills"].values():
        known.update(item.get("aliases") or [])
    for rel in (
        "prompts/soroban/phase1-recon-prompt.md",
        "prompts/soroban/v2/phase1-recon-prompt.md",
    ):
        text = _read(ROOT / rel)
        assert "STORAGE_LIFECYCLE" in text
        assert "EXTERNAL_PRECONDITION_AUDIT" in text
        assert "ORACLE_ANALYSIS | ORACLE flag" not in text
        assert "STORAGE_TTL_SAFETY | TEMPORAL" not in text
        for key in ("AUTH_ANALYSIS", "UPGRADE_SAFETY", "STORAGE_LIFECYCLE", "EXTERNAL_PRECONDITION_AUDIT"):
            assert key in known
