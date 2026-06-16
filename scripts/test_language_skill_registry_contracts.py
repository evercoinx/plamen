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


def test_language_toolchain_registry_covers_all_supported_chains():
    """Every supported chain must have build+test commands and evidence tags
    registered. Pre-v2.0.1 only sui+soroban were populated, so phase5 verifiers
    on EVM/Solana/Aptos had to hardcode commands from phase5-poc-execution.md.
    Now the registry is the source of truth.
    """
    registry = json.loads(_read(ROOT / "rules" / "language-toolchain-registry.json"))
    expected = {"evm", "solana", "aptos", "sui", "soroban", "daml"}
    assert set(registry["languages"]) == expected, (
        f"registry coverage drift: {set(registry['languages']) ^ expected}"
    )
    for lang, body in registry["languages"].items():
        assert body.get("build_command"), f"{lang}: missing build_command"
        assert body.get("test_command"), f"{lang}: missing test_command"
        tags = body.get("evidence_tags") or []
        assert "POC-PASS" in tags and "CODE-TRACE" in tags, (
            f"{lang}: evidence_tags must include POC-PASS and CODE-TRACE — got {tags}"
        )
        # fuzz_engines list may be empty (Aptos has no native fuzzer), but
        # the field must exist so the schema is uniform across languages.
        assert "fuzz_engines" in body, f"{lang}: fuzz_engines key missing"


def test_language_toolchain_commands_match_phase5_authoritative_table():
    """The phase5-poc-execution.md command table is documented authority for
    verifiers. The registry must agree on the primary build+test commands —
    otherwise verifiers and the registry will drift.
    """
    registry = json.loads(_read(ROOT / "rules" / "language-toolchain-registry.json"))
    poc = _read(ROOT / "rules" / "phase5-poc-execution.md")
    # Spot-check each language's primary build command.
    expectations = {
        "evm": ("forge build", "forge test --match-test"),
        "solana": ("cargo build-sbf", "cargo test"),
        "aptos": ("aptos move compile", "aptos move test"),
        "sui": ("sui move build", "sui move test"),
        "soroban": ("stellar contract build", "cargo test"),
    }
    for lang, (build, test_prefix) in expectations.items():
        body = registry["languages"][lang]
        assert build in body["build_command"], (
            f"{lang}: registry build_command {body['build_command']!r} "
            f"does not contain {build!r}"
        )
        assert test_prefix in body["test_command"], (
            f"{lang}: registry test_command {body['test_command']!r} "
            f"does not start with {test_prefix!r}"
        )
        # The phase5 table must mention this build command somewhere — a
        # cheap drift detector.
        assert build in poc, (
            f"{lang}: build_command {build!r} not found in "
            "phase5-poc-execution.md (drift between registry and prompt)"
        )


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
