#!/usr/bin/env python3
"""
Plamen Pipeline Watchdog - phase_gate.py

Claude Code hook script that enforces artifact existence between pipeline phases.
Prevents the orchestrator from skipping mandatory steps by blocking phase transitions
when required artifacts are missing.

Subcommands:
  --stop          Stop hook: detect phase, check artifacts, block if skipping
  --track-write   PostToolUse hook: track scratchpad writes, reset stall counter
  --init          Initialize watchdog state for a new audit run
  --finalize      Mark a completed audit as finished and clear stale stall state
  --validate      Run end-of-audit validation and write audit_validation.md

Dormancy: If no watchdog_state.json exists, all subcommands exit 0 immediately.
This means non-audit Claude Code sessions have near-zero overhead.
"""

import json
import os
import sys
import glob
import time
import uuid


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(HOOKS_DIR, "phase_manifest.json")
ACTIVE_AUDIT_PATH = os.path.join(HOOKS_DIR, ".active_audit")
STATE_FILENAME = "watchdog_state.json"
# Mapping from template_recommendations.md flag/agent names to actual niche output filenames.
# The SKILL.md files use shorter names than the raw flag names.
NICHE_NAME_MAP = {
    "MISSING_EVENT": "niche_event_findings.md",
    "EVENT_COMPLETENESS": "niche_event_findings.md",
    "sync_gaps": "niche_semantic_gap_findings.md",
    "SEMANTIC_GAP_INVESTIGATOR": "niche_semantic_gap_findings.md",
    "accumulation_exposures": "niche_semantic_gap_findings.md",
    "conditional_writes": "niche_semantic_gap_findings.md",
    "cluster_gaps": "niche_semantic_gap_findings.md",
    "HAS_MULTI_CONTRACT": "niche_semantic_consistency_findings.md",
    "SEMANTIC_CONSISTENCY_AUDIT": "niche_semantic_consistency_findings.md",
    "HAS_SIGNATURES": "niche_signature_findings.md",
    "SIGNATURE_VERIFICATION_AUDIT": "niche_signature_findings.md",
    "HAS_DOCS": "niche_spec_compliance_findings.md",
    "SPEC_COMPLIANCE_AUDIT": "niche_spec_compliance_findings.md",
    "MULTI_STEP_OPS": "niche_multi_step_safety_findings.md",
    "MULTI_STEP_OPERATION_SAFETY": "niche_multi_step_safety_findings.md",
    "OUTCOME_CALLBACK": "niche_callback_safety_findings.md",
    "CALLBACK_RECEIVER_SAFETY": "niche_callback_safety_findings.md",
    "MIXED_DECIMALS": "niche_dimensional_analysis_findings.md",
    "DIMENSIONAL_ANALYSIS": "niche_dimensional_analysis_findings.md",
    "STABLESWAP_FORK": "niche_stableswap_compliance_findings.md",
    "STABLESWAP_COMPLIANCE": "niche_stableswap_compliance_findings.md",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_stdin_json():
    """Read JSON from stdin. Return empty dict on failure."""
    try:
        data = sys.stdin.read()
        if not data or not data.strip():
            return {}
        return json.loads(data)
    except (json.JSONDecodeError, IOError, OSError):
        return {}


def load_json_file(path):
    """Load a JSON file. Return None on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, json.JSONDecodeError):
        return None


def save_json_file(path, data):
    """Save data as JSON to a file."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except (IOError, OSError):
        return False


def output_json(data):
    """Print JSON to stdout for Claude Code to consume."""
    print(json.dumps(data))


def find_scratchpad_from_path(file_path):
    """
    Attempt to infer scratchpad directory from a file path.
    Looks for a directory component containing '.scratchpad' or 'scratchpad'.
    """
    normalized = file_path.replace("\\", "/")
    parts = normalized.split("/")
    for i, part in enumerate(parts):
        if "scratchpad" in part.lower():
            candidate = "/".join(parts[:i + 1])
            if os.path.isdir(candidate):
                return candidate
    return None


V2_MARKER_FILENAME = "_v2_checkpoint.json"


def _is_v2_scratchpad(scratchpad_path):
    """True if `scratchpad_path` contains the V2 driver's checkpoint file.

    V2 manages phase gates externally via `plamen_driver.py`. When V2 is
    active, this watchdog MUST stay dormant — its BLOCK semantics fight
    the driver's phase-scoped subprocess model (see Irys L1 postmortem
    anomalies A1/A7/A8).
    """
    if not scratchpad_path:
        return False
    try:
        marker = os.path.join(scratchpad_path, V2_MARKER_FILENAME)
        return os.path.isfile(marker)
    except (IOError, OSError, TypeError):
        return False


def _v2_active_anywhere_nearby():
    """Detect V2 via any of the scratchpad discovery routes used by find_state_file.

    Returns True if ANY candidate scratchpad has a `_v2_checkpoint.json`.
    Used as an early-exit for every hook command so V1 and V2 coexist.
    """
    env_sp = os.environ.get("PLAMEN_SCRATCHPAD")
    if env_sp and _is_v2_scratchpad(env_sp):
        return True

    cwd = os.getcwd().replace("\\", "/")
    for sp in (os.path.join(cwd, ".scratchpad"), cwd):
        if _is_v2_scratchpad(sp):
            return True

    if os.path.isfile(ACTIVE_AUDIT_PATH):
        try:
            with open(ACTIVE_AUDIT_PATH, "r") as f:
                breadcrumb_sp = f.read().strip()
            if breadcrumb_sp and _is_v2_scratchpad(breadcrumb_sp):
                return True
        except (IOError, OSError):
            pass

    return False


def find_state_file(explicit_scratchpad=None):
    """
    Find watchdog_state.json. Search order:
    1. Explicit scratchpad path
    2. PLAMEN_SCRATCHPAD env var
    3. Current working directory / .scratchpad
    4. Scan common locations

    Returns None if a V2 `_v2_checkpoint.json` is discovered in any of
    the same locations — V2 manages gates externally and this watchdog
    must be dormant for the run.
    """
    # V2 bypass: if the driver is active, the watchdog is redundant and
    # will fight the phase-scoped subprocess model. Exit as dormant.
    if _v2_active_anywhere_nearby():
        return None

    candidates = []

    if explicit_scratchpad:
        candidates.append(os.path.join(explicit_scratchpad, STATE_FILENAME))

    env_sp = os.environ.get("PLAMEN_SCRATCHPAD")
    if env_sp:
        candidates.append(os.path.join(env_sp, STATE_FILENAME))

    cwd = os.getcwd().replace("\\", "/")
    candidates.append(os.path.join(cwd, ".scratchpad", STATE_FILENAME))
    candidates.append(os.path.join(cwd, STATE_FILENAME))

    # 4. Breadcrumb file written by --init
    if os.path.isfile(ACTIVE_AUDIT_PATH):
        try:
            with open(ACTIVE_AUDIT_PATH, "r") as f:
                breadcrumb_sp = f.read().strip()
            if breadcrumb_sp:
                candidates.append(os.path.join(breadcrumb_sp, STATE_FILENAME))
        except (IOError, OSError):
            pass

    for candidate in candidates:
        normalized = candidate.replace("\\", "/")
        if os.path.isfile(normalized):
            return normalized

    return None


def format_missing_with_hints(missing, phase):
    """Format missing artifacts list with recovery hints from the manifest."""
    hints = phase.get("recovery_hints", {})
    lines = []
    for name, reason in missing:
        line = "  - {} ({})".format(name, reason)
        # Check for exact match or glob match in recovery hints
        hint = hints.get(name, "")
        if not hint:
            for pattern, h in hints.items():
                if "*" in pattern and name.startswith(pattern.split("*")[0]):
                    hint = h
                    break
        if hint:
            line += "\n    RECOVERY: " + hint
        lines.append(line)
    return "\n".join(lines)


def get_file_size(path):
    """Get file size in bytes. Return 0 if file doesn't exist."""
    try:
        return os.path.getsize(path)
    except (IOError, OSError):
        return 0


def get_latest_artifact_mtime(scratchpad):
    """
    Return the latest mtime among markdown artifacts in the scratchpad, or 0.
    This is used as a fallback progress detector when the PostToolUse write hook
    misses sub-agent writes.
    """
    latest = 0
    try:
        for name in os.listdir(scratchpad):
            if not name.endswith(".md"):
                continue
            full = os.path.join(scratchpad, name)
            try:
                latest = max(latest, os.path.getmtime(full))
            except (IOError, OSError):
                continue
    except (IOError, OSError):
        return 0
    return latest


def glob_match(scratchpad, pattern):
    """
    Glob match files in scratchpad. Returns list of matching paths.
    Pattern can contain * wildcards.
    """
    full_pattern = os.path.join(scratchpad, pattern).replace("\\", "/")
    return glob.glob(full_pattern)


def is_reserved_first_pass_analysis(path):
    """Return True for analysis files owned by later discovery subphases."""
    name = os.path.basename(path)
    return (
        name.startswith("analysis_rescan_")
        or name.startswith("analysis_percontract_")
        or name.startswith("analysis_merged_into_")
        or name.startswith("analysis_report_")
    )


def artifact_matches(scratchpad, pattern):
    """Return artifact matches with phase-family guards applied."""
    if "*" in pattern:
        matches = glob_match(scratchpad, pattern)
    else:
        matches = [os.path.join(scratchpad, pattern).replace("\\", "/")]
    if pattern == "analysis_*.md":
        matches = [m for m in matches if not is_reserved_first_pass_analysis(m)]
    return matches


def _normalize_mode(mode):
    """
    Map L1-specific mode names to their base smart-contract equivalents for
    condition evaluation. `l1-thorough` is treated as `thorough`, `l1-core` as
    `core`, etc. This lets phase_manifest.json use `MODE == thorough` once and
    have it cover both smart-contract and L1 Thorough runs.
    """
    if isinstance(mode, str) and mode.startswith("l1-"):
        return mode[3:]  # strip "l1-" prefix → "thorough" / "core" / etc.
    if mode == "l1":
        return "core"  # bare "l1" defaults to Core depth per plamen-l1.md Step 0
    return mode


def evaluate_condition(condition, mode):
    """
    Evaluate a simple condition string like 'MODE == thorough' or 'MODE != light'.
    L1 modes are normalized to their smart-contract equivalents before comparison.
    """
    normalized = _normalize_mode(mode)
    condition = condition.strip()
    if "!=" in condition:
        parts = condition.split("!=")
        if len(parts) == 2:
            lhs = parts[0].strip()
            rhs = parts[1].strip()
            if lhs == "MODE":
                return normalized != rhs
    elif "==" in condition:
        parts = condition.split("==")
        if len(parts) == 2:
            lhs = parts[0].strip()
            rhs = parts[1].strip()
            if lhs == "MODE":
                return normalized == rhs
    return False


# ---------------------------------------------------------------------------
# Parallel phase groups
# ---------------------------------------------------------------------------
# Phases that legitimately run concurrently. When the watchdog detects a "leak"
# (artifacts for a later phase appearing while the current phase is incomplete),
# it suppresses the alarm if BOTH the current phase AND the leaked phase belong
# to the same group here.
#
# RULES for adding a phase to a group:
#   1. Neither phase reads the other's required artifacts
#   2. Both phases share the same prerequisite (e.g., all consume inventory)
#   3. Neither phase is a downstream prerequisite for the other
#
# Adding a phase that violates these rules can SUPPRESS REAL phase-skip bugs.
# Be conservative — only add phases with proven independent inputs.

PARALLEL_GROUPS = [
    # Phases 4-6 (rescan + per_contract + semantic_invariants) all consume the
    # findings_inventory (rescan, per_contract) or state_variables.md
    # (semantic_invariants), produce independent outputs, and all feed into
    # depth (phase 7). They are safe to run concurrently.
    {"rescan", "per_contract", "semantic_invariants"},
]


def extract_niche_agents(scratchpad):
    """
    Read template_recommendations.md to find which niche agents were recommended.
    Returns list of expected niche output filenames (deduplicated).
    Uses NICHE_NAME_MAP to resolve flag/agent names to actual SKILL.md output filenames.
    """
    rec_path = os.path.join(scratchpad, "template_recommendations.md").replace("\\", "/")
    if not os.path.isfile(rec_path):
        return []

    niche_files = []
    seen = set()
    try:
        with open(rec_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Look for niche agent names in the Niche Agents section
        in_niche_section = False
        for line in content.split("\n"):
            if "## Niche Agents" in line or "## Niche agents" in line:
                in_niche_section = True
                continue
            if in_niche_section and line.lstrip().startswith("#"):
                break
            if in_niche_section and line.strip().startswith("-"):
                # Extract niche agent name from lines like "- EVENT_COMPLETENESS"
                raw = line.strip().lstrip("-").strip()
                name = raw.split()[0] if raw else ""
                name = name.strip("`*_()[]{}:,.")
                if name:
                    # Use the mapping if available, fall back to generated name
                    filename = NICHE_NAME_MAP.get(name, "niche_{}_findings.md".format(name.lower()))
                    if filename not in seen:
                        seen.add(filename)
                        niche_files.append(filename)
            # Also parse table format: | AGENT_NAME | FLAG | YES | YES | ... |
            if in_niche_section and "|" in line:
                stripped = line.strip()
                # Skip header separator rows (e.g., |---|---|---|)
                if stripped.replace("|", "").replace("-", "").replace(" ", "") == "":
                    continue
                cells = [c.strip() for c in stripped.split("|") if c.strip()]
                if len(cells) >= 4:
                    name = cells[0].strip().strip("`*_()[]{}:,.")
                    # Skip table header rows (contain "Niche Agent", "Trigger", etc.)
                    if name.lower() in ("niche agent", "niche agents", "name", "agent", "skill"):
                        continue
                    spawn = cells[3].strip().upper() if len(cells) > 3 else ""
                    if name and not name.startswith("-") and spawn == "YES":
                        filename = NICHE_NAME_MAP.get(name, "niche_{}_findings.md".format(name.lower()))
                        if filename not in seen:
                            seen.add(filename)
                            niche_files.append(filename)
    except (IOError, OSError):
        pass

    return niche_files


def should_enforce_niche_artifacts(mode):
    """
    Light mode skips niche agents entirely, so their outputs should not be part
    of depth-phase gate enforcement.
    """
    return mode in ("core", "thorough")


def phase_field(phase, field_name, mode, default=None):
    """
    Mode-aware field lookup for a phase dict.

    When mode starts with 'l1' and the phase has 'l1_{field_name}', use that
    override. This lets phase_manifest.json declare L1-specific artifact lists
    (l1_required_artifacts, l1_min_required_glob_matches, l1_scanner_artifacts,
    l1_conditional_artifacts, l1_niche_artifacts_source, l1_optional_artifacts,
    l1_gate_message) alongside the smart-contract-mode defaults, without
    forcing a parallel phase set or post-hoc stub-file workarounds.

    Example: phase['required_artifacts'] = ['design_context.md', ...]
             phase['l1_required_artifacts'] = ['threat_model.md', ...]
             phase_field(phase, 'required_artifacts', 'l1-core') -> ['threat_model.md', ...]
             phase_field(phase, 'required_artifacts', 'core')    -> ['design_context.md', ...]
    """
    if isinstance(mode, str) and mode.startswith("l1"):
        l1_key = "l1_" + field_name
        if l1_key in phase:
            return phase[l1_key]  # may be None to explicitly suppress for L1
    return phase.get(field_name, default)


def get_required_phases(manifest, mode):
    """Return phase tuples required for the given mode, in order."""
    phases_sorted = sorted(
        manifest["phases"].items(),
        key=lambda x: x[1]["order"]
    )
    required = []
    for phase_name, phase in phases_sorted:
        if "mode_required" in phase and mode not in phase["mode_required"]:
            continue
        if "mode_excluded" in phase and mode in phase["mode_excluded"]:
            continue
        required.append((phase_name, phase))
    return required


def get_mode_forbidden_patterns(mode):
    """
    Return artifact patterns that should not appear for this mode.
    These are mode-discipline checks for end-of-run validation.
    """
    if mode == "light":
        return [
            "semantic_invariants.md",
            "confidence_scores.md",
            "rag_validation.md",
            "niche_*_findings.md",
            "analysis_rescan_*.md",
            "analysis_percontract_*.md",
            "design_stress_findings.md",
            "perturbation_findings.md",
            "skill_execution_gaps.md",
        ]
    if mode == "core":
        return [
            "analysis_rescan_*.md",
            "analysis_percontract_*.md",
            "design_stress_findings.md",
            "perturbation_findings.md",
            "skill_execution_gaps.md",
        ]
    return []


def find_present_artifacts(scratchpad, pattern, min_bytes=1):
    """Return matching artifact paths for a pattern."""
    if "*" in pattern:
        return [m for m in artifact_matches(scratchpad, pattern) if get_file_size(m) >= min_bytes]
    path = os.path.join(scratchpad, pattern).replace("\\", "/")
    if os.path.isfile(path) and get_file_size(path) >= min_bytes:
        return [path]
    return []


def collect_validation_issues(state, manifest):
    """
    Collect end-of-audit validation errors and warnings.
    Returns (errors, warnings).
    """
    errors = []
    warnings = []
    scratchpad = state.get("scratchpad", "").replace("\\", "/")
    project_root = state.get("project_root", "").replace("\\", "/")
    mode = state.get("mode", "core")

    required_phases = get_required_phases(manifest, mode)
    for phase_name, phase in required_phases:
        missing = get_missing_artifacts(scratchpad, phase, mode)
        if missing:
            missing_names = ", ".join(name for name, _ in missing)
            errors.append(
                "Missing required {} artifacts: {}".format(
                    phase.get("display_name", phase_name), missing_names
                )
            )

    current_phase_name, _ = detect_current_phase(scratchpad, manifest, mode)
    if current_phase_name != "complete":
        errors.append("Run is not complete according to phase manifest; current phase is '{}'.".format(current_phase_name))

    final_report = os.path.join(project_root, "AUDIT_REPORT.md").replace("\\", "/")
    if not os.path.isfile(final_report) or get_file_size(final_report) < 200:
        errors.append("Missing or undersized final report: AUDIT_REPORT.md")

    quality_report = os.path.join(scratchpad, "report_quality.md").replace("\\", "/")
    if not os.path.isfile(quality_report) or get_file_size(quality_report) < 50:
        errors.append("Missing or undersized report_quality.md")

    coverage_report = os.path.join(scratchpad, "report_coverage.md").replace("\\", "/")
    if mode in ("core", "thorough") and (not os.path.isfile(coverage_report) or get_file_size(coverage_report) < 50):
        errors.append("Missing or undersized report_coverage.md")

    recon_summary = os.path.join(scratchpad, "recon_summary.md").replace("\\", "/")
    if os.path.isfile(recon_summary):
        try:
            with open(recon_summary, "r", encoding="utf-8") as f:
                recon_text = f.read().lower()
            rescue_markers = (
                "orchestrator materialized the missing recon artifacts directly",
                "recon agent 3 workers both stalled",
                "recon rescue",
            )
            if any(marker in recon_text for marker in rescue_markers):
                errors.append("Recon phase required orchestrator rescue/materialization; parity run is not clean.")
        except (IOError, OSError):
            warnings.append("Could not inspect recon_summary.md for rescue markers.")

    if state.get("stall_phase") or state.get("stall_missing"):
        warnings.append(
            "Watchdog still records stale stall state: phase={}, missing={}".format(
                state.get("stall_phase"), state.get("stall_missing", [])
            )
        )

    for pattern in get_mode_forbidden_patterns(mode):
        present = find_present_artifacts(scratchpad, pattern)
        if present:
            warnings.append("Forbidden-by-mode artifacts present for {}: {}".format(mode, pattern))

    return errors, warnings


def write_validation_report(scratchpad, mode, errors, warnings):
    """Write audit_validation.md in the scratchpad."""
    lines = [
        "# Audit Validation",
        "",
        "- Mode: `{}`".format(mode),
        "- Status: `{}`".format("PASS" if not errors else "FAIL"),
        "- Errors: {}".format(len(errors)),
        "- Warnings: {}".format(len(warnings)),
        "",
    ]
    if errors:
        lines.append("## Errors")
        for item in errors:
            lines.append("- {}".format(item))
        lines.append("")
    if warnings:
        lines.append("## Warnings")
        for item in warnings:
            lines.append("- {}".format(item))
        lines.append("")
    if not errors and not warnings:
        lines.append("No validation issues detected.")
        lines.append("")

    report_path = os.path.join(scratchpad, "audit_validation.md").replace("\\", "/")
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except (IOError, OSError):
        return None
    return report_path


def mark_state_complete(state_path, state, latest_artifact_mtime=0, validation_status=None):
    """Clear stale state and mark the run complete."""
    state["stop_hook_active"] = False
    state["stall_counter"] = 0
    state["stall_phase"] = None
    state["stall_missing"] = []
    if latest_artifact_mtime:
        state["last_write_time"] = latest_artifact_mtime
    state["status"] = "complete"
    state["completed_at"] = time.time()
    if validation_status:
        state["validation_status"] = validation_status
    save_json_file(state_path, state)
    try:
        os.remove(ACTIVE_AUDIT_PATH)
    except (IOError, OSError):
        pass


# ---------------------------------------------------------------------------
# Phase Detection
# ---------------------------------------------------------------------------

def check_artifact_present(scratchpad, artifact_name, min_bytes):
    """
    Check if an artifact is present and meets minimum size.
    Handles glob patterns (containing *).
    Returns (present: bool, details: str).
    """
    if "*" in artifact_name:
        matches = artifact_matches(scratchpad, artifact_name)
        valid_matches = [m for m in matches if get_file_size(m) >= min_bytes]
        if valid_matches:
            return True, "{} matches".format(len(valid_matches))
        return False, "no matches for {}".format(artifact_name)
    else:
        path = os.path.join(scratchpad, artifact_name).replace("\\", "/")
        if os.path.isfile(path) and get_file_size(path) >= min_bytes:
            return True, "present"
        elif os.path.isfile(path):
            return False, "exists but too small ({} bytes < {})".format(get_file_size(path), min_bytes)
        return False, "missing"


def check_glob_min_matches(scratchpad, artifact_name, min_matches, min_bytes):
    """
    Check that a glob pattern has at least min_matches valid files.
    Returns (satisfied: bool, actual_count: int).
    """
    matches = artifact_matches(scratchpad, artifact_name)
    valid = [m for m in matches if get_file_size(m) >= min_bytes]
    return len(valid) >= min_matches, len(valid)


def detect_current_phase(scratchpad, manifest, mode):
    """
    Walk phases in order. Current phase = first phase whose required artifacts
    are NOT all present. If all complete, return ('complete', None).

    Returns (phase_name, phase_config) or ('complete', None).
    """
    phases_sorted = sorted(
        manifest["phases"].items(),
        key=lambda x: x[1]["order"]
    )

    for phase_name, phase in phases_sorted:
        # Skip phases not required for this mode
        if "mode_required" in phase and mode not in phase["mode_required"]:
            continue
        # Skip phases explicitly excluded for this mode (e.g. Phase 4c chain analysis for L1)
        if "mode_excluded" in phase and mode in phase["mode_excluded"]:
            continue

        min_bytes = phase.get("min_file_bytes", 50)

        # Check required artifacts (L1-aware via phase_field)
        all_present = True

        for artifact in (phase_field(phase, "required_artifacts", mode, default=[]) or []):
            if "*" in artifact:
                # For glob patterns, check min_required_glob_matches or default to 1
                min_matches = 1
                glob_map = phase_field(phase, "min_required_glob_matches_map", mode, default={}) or {}
                if artifact in glob_map:
                    min_matches = glob_map[artifact]
                else:
                    override_min = phase_field(phase, "min_required_glob_matches", mode)
                    if override_min is not None:
                        min_matches = override_min

                satisfied, _ = check_glob_min_matches(scratchpad, artifact, min_matches, min_bytes)
                if not satisfied:
                    all_present = False
                    break
            else:
                present, _ = check_artifact_present(scratchpad, artifact, min_bytes)
                if not present:
                    all_present = False
                    break

        if not all_present:
            return phase_name, phase

        # Check scanner_artifacts (flexible multi-pattern with min_matches)
        # L1 mode sets l1_scanner_artifacts: null to explicitly suppress this check
        scanner_cfg = phase_field(phase, "scanner_artifacts", mode)
        if scanner_cfg:
            scanner_min = scanner_cfg.get("min_matches", 1)
            scanner_total = 0
            for pattern in scanner_cfg.get("patterns", []):
                matches = glob_match(scratchpad, pattern)
                scanner_total += len([m for m in matches if get_file_size(m) >= min_bytes])
            if scanner_total < scanner_min:
                return phase_name, phase

        # Check conditional artifacts (L1-aware)
        conditional = phase_field(phase, "conditional_artifacts", mode, default={}) or {}
        for artifact, condition in conditional.items():
            if evaluate_condition(condition, mode):
                present, _ = check_artifact_present(scratchpad, artifact, min_bytes)
                if not present:
                    return phase_name, phase

        # Check niche artifacts if this phase defines them
        # L1 mode sets l1_niche_artifacts_source: null to explicitly suppress
        if phase_field(phase, "niche_artifacts_source", mode) and should_enforce_niche_artifacts(mode):
            niche_files = extract_niche_agents(scratchpad)
            for nf in niche_files:
                present, _ = check_artifact_present(scratchpad, nf, min_bytes)
                if not present:
                    # Niche artifacts missing - phase not complete
                    return phase_name, phase

    return "complete", None


def get_missing_artifacts(scratchpad, phase, mode):
    """
    Get a detailed list of missing artifacts for a phase.
    Returns list of (artifact_name, reason) tuples.

    L1 mode-aware: uses phase_field() to resolve l1_* overrides when mode
    starts with 'l1'. This is how we let phase_manifest.json declare
    l1_required_artifacts / l1_scanner_artifacts / l1_conditional_artifacts
    alongside the smart-contract defaults without forcing stub redirector
    files in L1 runs.
    """
    missing = []
    min_bytes = phase.get("min_file_bytes", 50)

    for artifact in (phase_field(phase, "required_artifacts", mode, default=[]) or []):
        if "*" in artifact:
            min_matches = 1
            glob_map = phase_field(phase, "min_required_glob_matches_map", mode, default={}) or {}
            if artifact in glob_map:
                min_matches = glob_map[artifact]
            else:
                override_min = phase_field(phase, "min_required_glob_matches", mode)
                if override_min is not None:
                    min_matches = override_min

            satisfied, actual = check_glob_min_matches(scratchpad, artifact, min_matches, min_bytes)
            if not satisfied:
                missing.append((artifact, "need {} matches, found {}".format(min_matches, actual)))
        else:
            present, reason = check_artifact_present(scratchpad, artifact, min_bytes)
            if not present:
                missing.append((artifact, reason))

    # Check scanner_artifacts (L1 mode sets l1_scanner_artifacts: null to suppress)
    scanner_cfg = phase_field(phase, "scanner_artifacts", mode)
    if scanner_cfg:
        scanner_min = scanner_cfg.get("min_matches", 1)
        scanner_total = 0
        for pattern in scanner_cfg.get("patterns", []):
            matches = glob_match(scratchpad, pattern)
            scanner_total += len([m for m in matches if get_file_size(m) >= min_bytes])
        if scanner_total < scanner_min:
            patterns_str = ", ".join(scanner_cfg.get("patterns", []))
            missing.append(
                ("scanner/validation artifacts",
                 "need {} matches across [{}], found {}".format(scanner_min, patterns_str, scanner_total))
            )

    conditional = phase_field(phase, "conditional_artifacts", mode, default={}) or {}
    for artifact, condition in conditional.items():
        if evaluate_condition(condition, mode):
            present, reason = check_artifact_present(scratchpad, artifact, min_bytes)
            if not present:
                missing.append((artifact, reason + " (conditional: {})".format(condition)))

    if phase_field(phase, "niche_artifacts_source", mode) and should_enforce_niche_artifacts(mode):
        niche_files = extract_niche_agents(scratchpad)
        for nf in niche_files:
            present, reason = check_artifact_present(scratchpad, nf, min_bytes)
            if not present:
                missing.append((nf, reason + " (niche agent)"))

    return missing


def _phases_in_same_parallel_group(phase_a, phase_b):
    """
    Return True if phase_a and phase_b belong to the same PARALLEL_GROUPS entry,
    meaning a "forward leak" between them is a false alarm (legitimate parallel
    execution, not a phase skip).
    """
    if not phase_a or not phase_b or phase_a == phase_b:
        return False
    for group in PARALLEL_GROUPS:
        if phase_a in group and phase_b in group:
            return True
    return False


def detect_forward_leak(scratchpad, manifest, mode, current_phase_order, current_phase_name=None):
    """
    Check if the orchestrator has started writing artifacts for a LATER phase
    while the current phase is still incomplete. This indicates step skipping.

    Phases listed in the same PARALLEL_GROUPS entry as current_phase_name are
    exempt from leak detection (they legitimately run concurrently). When
    current_phase_name is None, behavior is identical to the legacy version
    (no parallel-group suppression).

    Returns (leaked_phase_name, leaked_artifact) or (None, None).
    """
    phases_sorted = sorted(
        manifest["phases"].items(),
        key=lambda x: x[1]["order"]
    )

    for phase_name, phase in phases_sorted:
        if phase["order"] <= current_phase_order:
            continue

        if "mode_required" in phase and mode not in phase["mode_required"]:
            continue
        if "mode_excluded" in phase and mode in phase["mode_excluded"]:
            continue

        # Suppress alarms for phases that legitimately run in parallel with
        # the current phase (see PARALLEL_GROUPS at top of file).
        if _phases_in_same_parallel_group(current_phase_name, phase_name):
            continue

        min_bytes = phase.get("min_file_bytes", 50)

        for artifact in (phase_field(phase, "required_artifacts", mode, default=[]) or []):
            if "*" in artifact:
                matches = artifact_matches(scratchpad, artifact)
                valid = [m for m in matches if get_file_size(m) >= min_bytes]
                if valid:
                    return phase_name, artifact
            else:
                present, _ = check_artifact_present(scratchpad, artifact, min_bytes)
                if present:
                    return phase_name, artifact

    return None, None


# ---------------------------------------------------------------------------
# Subcommand: --init
# ---------------------------------------------------------------------------

def cmd_init(args):
    """
    Initialize watchdog state for a new audit run.
    Usage: phase_gate.py --init <scratchpad_path> <mode> [project_root]
    """
    if len(args) < 2:
        print("Usage: phase_gate.py --init <scratchpad_path> <mode> [project_root]", file=sys.stderr)
        sys.exit(1)

    scratchpad = args[0].replace("\\", "/")
    mode = args[1].lower()
    project_root = args[2].replace("\\", "/") if len(args) > 2 else os.getcwd().replace("\\", "/")

    # V2 bypass: if the V2 driver owns this scratchpad, do NOT initialize the
    # V1 watchdog — the driver's Python gate replaces it. An --init call here
    # is almost always a V1 prompt's Step 1.5 running under V2, which would
    # install a breadcrumb that blocks subsequent phases.
    if _is_v2_scratchpad(scratchpad):
        print("[phase_gate] V2 checkpoint detected at {} -- watchdog init skipped".format(scratchpad), file=sys.stderr)
        sys.exit(0)

    if mode not in ("light", "core", "thorough", "l1", "l1-core", "l1-thorough"):
        print("Invalid mode '{}'. Must be light, core, thorough, l1, l1-core, or l1-thorough.".format(mode), file=sys.stderr)
        sys.exit(1)

    state = {
        "version": "1.0.0",
        "run_id": str(uuid.uuid4()),
        "mode": mode,
        "scratchpad": scratchpad,
        "project_root": project_root,
        "initialized_at": time.time(),
        "stop_hook_active": False,
        "stall_counter": 0,
        "stall_phase": None,
        "stall_missing": [],
        "last_write_time": 0,
        "write_count": 0,
        "blocks_issued": 0,
        "warnings_issued": 0
    }

    state_path = os.path.join(scratchpad, STATE_FILENAME).replace("\\", "/")
    for generated_name in ("tool_calls.jsonl", "audit_validation.md"):
        generated_path = os.path.join(scratchpad, generated_name).replace("\\", "/")
        try:
            if os.path.isfile(generated_path):
                os.remove(generated_path)
        except (IOError, OSError):
            pass
    if save_json_file(state_path, state):
        # Write breadcrumb so Stop hook can find the state without env vars
        try:
            with open(ACTIVE_AUDIT_PATH, "w") as f:
                f.write(scratchpad)
        except (IOError, OSError):
            pass  # Non-fatal: env var fallback still works
        output_json({
            "systemMessage": "[Watchdog] Initialized for {} mode audit. Scratchpad: {}".format(mode, scratchpad)
        })
    else:
        print("Failed to write state file to {}".format(state_path), file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: --track-write
# ---------------------------------------------------------------------------

def cmd_track_write():
    """
    PostToolUse hook for Write/Edit. Lightweight tracker.
    - If written file is in scratchpad, reset stall counter
    - If first scratchpad write, check for state initialization

    Supports multiple payload formats:
    - Claude Code: { "tool_input": { "file_path": "..." } }
    - Codex flat:  { "file_path": "..." }  or  { "path": "..." }
    - Codex tool_response: { "tool_response": { "filePath": "..." } }
    """
    stdin_data = read_stdin_json()

    # Try multiple payload field paths to find the written file path.
    # Claude Code format: tool_input.file_path
    file_path = ""
    tool_input = stdin_data.get("tool_input", {})
    if isinstance(tool_input, dict):
        file_path = tool_input.get("file_path", "")

    # Codex may emit flat fields at the top level
    if not file_path:
        file_path = stdin_data.get("file_path", "")

    if not file_path:
        file_path = stdin_data.get("path", "")

    # Claude Code Edit tool puts path in tool_response.filePath
    if not file_path:
        tool_response = stdin_data.get("tool_response", {})
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath", "")
            if not file_path:
                file_path = tool_response.get("file_path", "")

    # Codex may nest under "output" or "result"
    if not file_path:
        for key in ("output", "result"):
            nested = stdin_data.get(key, {})
            if isinstance(nested, dict):
                file_path = nested.get("file_path", "") or nested.get("filePath", "") or nested.get("path", "")
                if file_path:
                    break

    if not file_path:
        print(
            "[Watchdog] Unknown PostToolUse payload format -- stall tracking "
            "disabled for this event. Keys received: {}".format(
                list(stdin_data.keys()) if stdin_data else "empty"
            ),
            file=sys.stderr,
        )
        sys.exit(0)

    file_path = file_path.replace("\\", "/")

    # Try to find scratchpad from env or by inferring from path
    scratchpad = os.environ.get("PLAMEN_SCRATCHPAD", "")
    if not scratchpad:
        scratchpad = find_scratchpad_from_path(file_path) or ""

    if not scratchpad:
        sys.exit(0)

    scratchpad = scratchpad.replace("\\", "/")
    state_path = os.path.join(scratchpad, STATE_FILENAME).replace("\\", "/")

    # Check if this write is in the scratchpad
    normalized_file = os.path.normpath(file_path).replace("\\", "/").lower()
    normalized_sp = os.path.normpath(scratchpad).replace("\\", "/").lower()

    if not normalized_file.startswith(normalized_sp):
        sys.exit(0)

    # Load state
    state = load_json_file(state_path)
    if not state:
        sys.exit(0)

    # Reset stall counter - progress is being made
    state["stall_counter"] = 0
    state["stall_phase"] = None
    state["stall_missing"] = []
    state["last_write_time"] = time.time()
    state["write_count"] = state.get("write_count", 0) + 1

    save_json_file(state_path, state)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Subcommand: --pretool-check
# ---------------------------------------------------------------------------
#
# PreToolUse hook for the Task tool. Blocks Phase 6 (Report) agent spawns
# when Phase 5 verification artifacts are missing. Read-only on state file.
# Honors dormancy, grace period, anti-loop free pass, and Light mode.
# Fails open on any error to never break the pipeline.

# Phase 6 detection markers — output filenames that only Phase 6 agents write.
# Stable across orchestrator versions because they are load-bearing for
# downstream report assembly.
PHASE_6_OUTPUT_MARKERS = (
    "report_index.md",
    "report_critical_high.md",
    "report_medium.md",
    "report_low_info.md",
    "report_quality.md",
    "report_coverage.md",
    "AUDIT_REPORT.md",
)


def cmd_pretool_check():
    """
    PreToolUse hook for the Task tool. Block Phase 6 (Report) agent spawns
    when required Phase 5 verification artifacts are missing.

    Read-only on watchdog_state.json. Never writes state — avoids race
    conditions with the Stop hook (--stop) and PostToolUse hook
    (--track-write).

    Fail-open: any error path exits 0 (allow) to ensure the hook never
    breaks the pipeline by mistake.

    Exit codes:
        0 + no output     → allow Task call
        0 + JSON decision → block Task call with reason
    """
    # 1. Read stdin payload from Claude Code harness.
    stdin_data = read_stdin_json()
    if not stdin_data:
        sys.exit(0)  # Fail-open: no input → allow

    # 2. Defensive tool name check (matcher in settings.json should already
    #    filter to "Task", but double-check in case the matcher changes).
    if stdin_data.get("tool_name") != "Task":
        sys.exit(0)

    # 3. Extract the spawn prompt. Different orchestrator versions store it
    #    under different keys; check all the common ones.
    tool_input = stdin_data.get("tool_input", {}) or {}
    prompt_text = (
        tool_input.get("prompt", "")
        or tool_input.get("description", "")
        or tool_input.get("subject", "")
    )
    if not isinstance(prompt_text, str) or not prompt_text:
        sys.exit(0)  # Fail-open: no prompt → allow

    # 4. Detect Phase 6 spawn by output filename match. This is the most
    #    stable detector because output filenames are load-bearing.
    is_phase_6 = any(marker in prompt_text for marker in PHASE_6_OUTPUT_MARKERS)
    if not is_phase_6:
        sys.exit(0)  # Not Phase 6 → allow

    # 5. Find the watchdog state file. If none, the audit is dormant or
    #    this is a non-audit session — allow the call.
    state_path = find_state_file()
    if not state_path:
        sys.exit(0)

    state = load_json_file(state_path)
    if not state:
        sys.exit(0)  # Fail-open: corrupt state → allow

    # 6. Anti-loop free pass: if the Stop hook just blocked, the next
    #    cycle gets a free pass. Honor that here too — do not double-block.
    if state.get("stop_hook_active"):
        sys.exit(0)

    scratchpad = state.get("scratchpad", "").replace("\\", "/")
    if not scratchpad or not os.path.isdir(scratchpad):
        sys.exit(0)  # Fail-open: no scratchpad → allow

    mode = state.get("mode", "core")

    # 7. Grace period: if the scratchpad has no .md artifacts yet, the
    #    audit is in early bootstrap (recon planning). Phase 6 keywords
    #    might appear coincidentally; do not block in grace period.
    try:
        md_count = sum(
            1 for entry in os.listdir(scratchpad)
            if entry.endswith(".md") and entry != STATE_FILENAME
        )
    except (IOError, OSError):
        md_count = 0
    if md_count == 0:
        sys.exit(0)

    # 8. Check Phase 5 artifacts.
    missing = []

    # 8a. Standard verification artifacts (verify_*.md). Required in all modes.
    if not find_present_artifacts(scratchpad, "verify_*.md", min_bytes=100):
        missing.append("verify_*.md (Phase 5: Verification — at least one required)")

    # 8b. Cross-batch consistency. Required in Core/Thorough only.
    if mode != "light":
        if not find_present_artifacts(scratchpad, "cross_batch_consistency.md", min_bytes=100):
            missing.append("cross_batch_consistency.md (Phase 5.2: Cross-Batch Consistency)")

    # 8c. Per-finding Skeptic-Judge (Thorough only). If hypotheses.md contains
    # any HIGH or CRITICAL findings, at least one skeptic_*.md must exist.
    # We use the existence-of-any check (not strict count) for robustness:
    # the failure mode we're catching is "zero skeptics ran at all", and
    # strict per-finding matching is fragile to format variations.
    if mode == "thorough":
        hypotheses_path = os.path.join(scratchpad, "hypotheses.md").replace("\\", "/")
        try:
            with open(hypotheses_path, "r", encoding="utf-8", errors="ignore") as f:
                hypotheses_text = f.read()
        except (IOError, OSError):
            hypotheses_text = ""

        # Look for HIGH/CRITICAL severity markers. Use both heading-style
        # and severity-line markers for robustness against format drift.
        # `### H-\d` matches "### H-01:", does NOT match "### CH-1:" because
        # the C is followed by H, not by digits.
        import re
        has_high_or_crit = bool(
            re.search(r"^###\s+[HC]-\d+:", hypotheses_text, re.MULTILINE)
            or re.search(r"\*\*Severity\*\*:\s*(High|Critical)", hypotheses_text)
        )

        if has_high_or_crit:
            skeptic_files = find_present_artifacts(scratchpad, "skeptic_*.md", min_bytes=50)
            if not skeptic_files:
                missing.append(
                    "skeptic_*.md (Phase 5.1: Skeptic-Judge — Thorough mode requires "
                    "adversarial re-verification for every HIGH/CRIT finding)"
                )

    # 9. If anything is missing, block.
    if missing:
        block_reason = (
            "[Watchdog PreToolUse BLOCK] Phase 6 (Report) agent spawn detected "
            "before Phase 5 verification is complete.\n\n"
            "Missing Phase 5 artifacts:\n"
            + "\n".join("  - " + m for m in missing)
            + "\n\n"
            "ACTION REQUIRED: Spawn the missing verification agents BEFORE "
            "spawning report agents:\n"
            "  - Phase 5 verification: read prompts/{LANGUAGE}/phase5-verification-prompt.md\n"
            "  - Phase 5.2 Cross-Batch: see CLAUDE.md Phase 5.2 section\n\n"
            "Blocked spawn (truncated): " + prompt_text[:200].replace("\n", " ")
        )
        output_json({"decision": "block", "reason": block_reason})
        sys.exit(0)

    # 10. All checks passed — allow the Task spawn.
    sys.exit(0)


# ---------------------------------------------------------------------------
# Subcommand: --finalize
# ---------------------------------------------------------------------------

def cmd_finalize():
    """
    Explicitly finalize a completed audit run.
    This is used at the end of the pipeline so stale stall metadata does not
    survive simply because no later --stop hook fired after report assembly.
    """
    state_path = find_state_file()
    if not state_path:
        sys.exit(0)

    state = load_json_file(state_path)
    if not state:
        sys.exit(0)

    scratchpad = state.get("scratchpad", os.path.dirname(state_path)).replace("\\", "/")
    manifest = load_json_file(MANIFEST_PATH)
    if not manifest:
        output_json({"decision": "error", "reason": "Manifest unavailable; cannot finalize audit state."})
        sys.exit(0)

    mode = state.get("mode", "core")
    current_phase_name, _ = detect_current_phase(scratchpad, manifest, mode)
    if current_phase_name != "complete":
        output_json({
            "decision": "error",
            "reason": "Cannot finalize: run is incomplete and currently blocked on phase '{}'.".format(current_phase_name)
        })
        sys.exit(0)

    latest_artifact_mtime = get_latest_artifact_mtime(scratchpad)
    mark_state_complete(state_path, state, latest_artifact_mtime)
    output_json({
        "systemMessage": "[Watchdog] Audit finalized. Stale stall state cleared."
    })
    sys.exit(0)


# ---------------------------------------------------------------------------
# Subcommand: --validate
# ---------------------------------------------------------------------------

def cmd_validate():
    """
    Run an explicit end-of-audit validation pass and write audit_validation.md.
    Does not block; it reports pass/fail for the completed run.
    """
    state_path = find_state_file()
    if not state_path:
        sys.exit(0)

    state = load_json_file(state_path)
    if not state:
        sys.exit(0)

    manifest = load_json_file(MANIFEST_PATH)
    if not manifest:
        output_json({"decision": "error", "reason": "Manifest unavailable; cannot validate audit run."})
        sys.exit(0)

    scratchpad = state.get("scratchpad", os.path.dirname(state_path)).replace("\\", "/")
    mode = state.get("mode", "core")
    errors, warnings = collect_validation_issues(state, manifest)
    report_path = write_validation_report(scratchpad, mode, errors, warnings)
    validation_status = "pass" if not errors else "fail"

    if not errors:
        latest_artifact_mtime = max(get_latest_artifact_mtime(scratchpad), time.time())
        mark_state_complete(state_path, state, latest_artifact_mtime, validation_status=validation_status)
    else:
        state["validation_status"] = validation_status
        save_json_file(state_path, state)

    message = "[Watchdog] Validation {}. {} errors, {} warnings.".format(
        validation_status.upper(), len(errors), len(warnings)
    )
    if report_path:
        message += " Report: {}".format(report_path)

    output_json({
        "systemMessage": message
    })
    sys.exit(0)


# ---------------------------------------------------------------------------
# Subcommand: --stop
# ---------------------------------------------------------------------------

def cmd_stop():
    """
    Stop hook. Fires every time Claude stops responding.
    1. Find watchdog state (dormant if none)
    2. Anti-loop check
    3. Detect current phase
    4. Check for forward leak (skipping)
    5. Two-strike stall model
    """
    stdin_data = read_stdin_json()

    # Try to find state file via all discovery methods
    state_path = find_state_file()
    if not state_path:
        # Dormant - no active audit, exit silently
        sys.exit(0)

    state = load_json_file(state_path)
    if not state:
        sys.exit(0)

    scratchpad = state.get("scratchpad", os.path.dirname(state_path)).replace("\\", "/")
    state_path = os.path.join(scratchpad, STATE_FILENAME).replace("\\", "/")
    mode = state.get("mode", "core")

    # Project mismatch guard: if the current working directory is NOT under
    # the project that initialized the watchdog, go dormant. This prevents
    # a stale .active_audit breadcrumb from one project blocking work in
    # a completely different project/session.
    project_root = state.get("project_root", "").replace("\\", "/").rstrip("/").lower()
    cwd = os.getcwd().replace("\\", "/").rstrip("/").lower()
    if project_root and cwd and not cwd.startswith(project_root):
        # Different project — clean up stale breadcrumb and go dormant
        try:
            os.remove(ACTIVE_AUDIT_PATH)
        except (IOError, OSError):
            pass
        sys.exit(0)

    # Anti-loop protection: if stop_hook_active flag is set, this is the
    # response AFTER a block. Give the orchestrator a free pass.
    if state.get("stop_hook_active", False):
        state["stop_hook_active"] = False
        state["stall_phase"] = None
        state["stall_missing"] = []
        save_json_file(state_path, state)
        sys.exit(0)

    # Startup grace: never block until at least 1 audit artifact exists in scratchpad.
    # Time-based grace (120s) was too fragile — Codex planning can take longer.
    # Artifact-based grace is robust: once the first .md file appears (from any
    # recon agent), enforcement activates. Until then, only warn.
    try:
        scratchpad_files = [f for f in os.listdir(scratchpad)
                           if f.endswith(".md") and f != STATE_FILENAME]
    except (IOError, OSError):
        scratchpad_files = []
    in_grace_period = len(scratchpad_files) == 0

    # Load manifest
    manifest = load_json_file(MANIFEST_PATH)
    if not manifest:
        # Can't enforce without manifest - pass through
        sys.exit(0)

    # Detect current phase
    current_phase_name, current_phase = detect_current_phase(scratchpad, manifest, mode)

    # Fallback progress inference: some Codex sub-agent writes do not reliably
    # trigger the write hook, so infer progress from actual artifact mtimes.
    latest_artifact_mtime = get_latest_artifact_mtime(scratchpad)
    if latest_artifact_mtime > state.get("last_write_time", 0):
        state["stall_counter"] = 0
        state["stall_phase"] = None
        state["stall_missing"] = []
        state["last_write_time"] = latest_artifact_mtime
        save_json_file(state_path, state)

    if current_phase_name == "complete":
        # All phases have their artifacts — audit is done.
        # Clean up breadcrumb so the watchdog doesn't interfere with
        # non-audit work in the same project directory.
        mark_state_complete(state_path, state, latest_artifact_mtime)
        sys.exit(0)

    # Get missing artifacts for current phase
    missing = get_missing_artifacts(scratchpad, current_phase, mode)
    if not missing:
        # Current phase is actually complete (edge case from conditional checks)
        state["stall_counter"] = 0
        state["stall_phase"] = None
        state["stall_missing"] = []
        state["last_write_time"] = latest_artifact_mtime
        save_json_file(state_path, state)
        sys.exit(0)

    # Check for forward leak - orchestrator started a later phase.
    # Pass current_phase_name to enable PARALLEL_GROUPS suppression for phases
    # that legitimately run concurrently (e.g., rescan + per_contract +
    # semantic_invariants all run in parallel after inventory).
    leaked_phase, leaked_artifact = detect_forward_leak(
        scratchpad, manifest, mode, current_phase["order"], current_phase_name
    )

    if leaked_phase:
        # BLOCK: orchestrator is skipping ahead
        missing_str = format_missing_with_hints(missing, current_phase)
        block_msg = (
            "[Watchdog BLOCK] Phase skip detected!\n"
            "Current phase: {} (order {})\n"
            "But found artifacts for: {} (order {})\n"
            "Missing artifacts in current phase:\n{}\n\n"
            "{}\n\n"
            "ACTION REQUIRED: Complete the current phase before proceeding. "
            "Spawn the agents listed in RECOVERY hints above."
        ).format(
            current_phase.get("display_name", current_phase_name),
            current_phase["order"],
            manifest["phases"][leaked_phase].get("display_name", leaked_phase),
            manifest["phases"][leaked_phase]["order"],
            missing_str,
            current_phase.get("gate_message", "")
        )

        state["stop_hook_active"] = True
        state["blocks_issued"] = state.get("blocks_issued", 0) + 1
        save_json_file(state_path, state)

        output_json({
            "decision": "block",
            "reason": block_msg
        })
        sys.exit(0)

    # Two-strike stall model
    missing_names = sorted([name for name, _ in missing])
    prev_stall_phase = state.get("stall_phase")
    prev_stall_missing = sorted(state.get("stall_missing", []))

    if prev_stall_phase == current_phase_name and prev_stall_missing == missing_names:
        # Second consecutive stop with same missing artifacts

        if in_grace_period:
            # During startup grace period: warn only, never block.
            # The orchestrator is still planning (reading prompts, detecting
            # language, composing agent prompts) before spawning recon agents.
            warn_msg = (
                "[Watchdog] No artifacts in scratchpad yet — grace period active. "
                "Phase {} has {} missing artifacts. "
                "Enforcement activates after first artifact is written."
            ).format(
                     current_phase.get("display_name", current_phase_name),
                     len(missing))
            state["warnings_issued"] = state.get("warnings_issued", 0) + 1
            save_json_file(state_path, state)
            print(warn_msg, file=sys.stderr)
            output_json({"systemMessage": warn_msg})
            sys.exit(0)

        # Past grace period -> BLOCK
        missing_str = format_missing_with_hints(missing, current_phase)
        block_msg = (
            "[Watchdog BLOCK] Stalled on phase: {}\n"
            "Two consecutive stops with no progress on missing artifacts:\n{}\n\n"
            "{}\n\n"
            "ACTION REQUIRED: You must produce these artifacts before doing anything else. "
            "Follow the RECOVERY instructions above for each missing artifact."
        ).format(
            current_phase.get("display_name", current_phase_name),
            missing_str,
            current_phase.get("gate_message", "")
        )

        state["stop_hook_active"] = True
        state["stall_counter"] = state.get("stall_counter", 0) + 1
        state["blocks_issued"] = state.get("blocks_issued", 0) + 1
        save_json_file(state_path, state)

        output_json({
            "decision": "block",
            "reason": block_msg
        })
        sys.exit(0)

    else:
        # First strike - warn, don't block
        missing_str = format_missing_with_hints(missing, current_phase)
        warn_msg = (
            "[Watchdog] Phase {} has missing artifacts:\n{}\n"
            "Next stop without progress will BLOCK. "
            "Follow RECOVERY instructions for each missing artifact."
        ).format(
            current_phase.get("display_name", current_phase_name),
            missing_str
        )

        state["stall_counter"] = state.get("stall_counter", 0) + 1
        state["stall_phase"] = current_phase_name
        state["stall_missing"] = missing_names
        state["warnings_issued"] = state.get("warnings_issued", 0) + 1
        save_json_file(state_path, state)

        # systemMessage may not be processed for Stop hooks in all Claude Code
        # versions; also print to stderr as fallback so warnings are visible.
        print(warn_msg, file=sys.stderr)
        output_json({
            "systemMessage": warn_msg
        })
        sys.exit(0)


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: phase_gate.py [--stop|--track-write|--init ...|--finalize|--validate|--pretool-check]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--stop":
        cmd_stop()
    elif cmd == "--track-write":
        cmd_track_write()
    elif cmd == "--init":
        cmd_init(sys.argv[2:])
    elif cmd == "--finalize":
        cmd_finalize()
    elif cmd == "--validate":
        cmd_validate()
    elif cmd == "--pretool-check":
        cmd_pretool_check()
    else:
        print("Unknown command: {}".format(cmd), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
