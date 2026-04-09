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

Dormancy: If no watchdog_state.json exists, all subcommands exit 0 immediately.
This means non-audit Claude Code sessions have near-zero overhead.
"""

import json
import os
import sys
import glob
import time


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


def find_state_file(explicit_scratchpad=None):
    """
    Find watchdog_state.json. Search order:
    1. Explicit scratchpad path
    2. PLAMEN_SCRATCHPAD env var
    3. Scan common locations
    """
    candidates = []

    if explicit_scratchpad:
        candidates.append(os.path.join(explicit_scratchpad, STATE_FILENAME))

    env_sp = os.environ.get("PLAMEN_SCRATCHPAD")
    if env_sp:
        candidates.append(os.path.join(env_sp, STATE_FILENAME))

    # 3. Breadcrumb file written by --init
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


def glob_match(scratchpad, pattern):
    """
    Glob match files in scratchpad. Returns list of matching paths.
    Pattern can contain * wildcards.
    """
    full_pattern = os.path.join(scratchpad, pattern).replace("\\", "/")
    return glob.glob(full_pattern)


def evaluate_condition(condition, mode):
    """
    Evaluate a simple condition string like 'MODE == thorough' or 'MODE != light'.
    """
    condition = condition.strip()
    if "!=" in condition:
        parts = condition.split("!=")
        if len(parts) == 2:
            lhs = parts[0].strip()
            rhs = parts[1].strip()
            if lhs == "MODE":
                return mode != rhs
    elif "==" in condition:
        parts = condition.split("==")
        if len(parts) == 2:
            lhs = parts[0].strip()
            rhs = parts[1].strip()
            if lhs == "MODE":
                return mode == rhs
    return False


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
            if in_niche_section and line.startswith("## "):
                break
            if in_niche_section and line.strip().startswith("-"):
                # Extract niche agent name from lines like "- EVENT_COMPLETENESS"
                name = line.strip().lstrip("-").strip().split()[0] if line.strip().lstrip("-").strip() else ""
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
                    name = cells[0].strip()
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
        matches = glob_match(scratchpad, artifact_name)
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
    matches = glob_match(scratchpad, artifact_name)
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

        min_bytes = phase.get("min_file_bytes", 50)

        # Check required artifacts
        all_present = True

        for artifact in phase.get("required_artifacts", []):
            if "*" in artifact:
                # For glob patterns, check min_required_glob_matches or default to 1
                min_matches = 1
                glob_map = phase.get("min_required_glob_matches_map", {})
                if artifact in glob_map:
                    min_matches = glob_map[artifact]
                elif "min_required_glob_matches" in phase:
                    min_matches = phase["min_required_glob_matches"]

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
        scanner_cfg = phase.get("scanner_artifacts")
        if scanner_cfg:
            scanner_min = scanner_cfg.get("min_matches", 1)
            scanner_total = 0
            for pattern in scanner_cfg.get("patterns", []):
                matches = glob_match(scratchpad, pattern)
                scanner_total += len([m for m in matches if get_file_size(m) >= min_bytes])
            if scanner_total < scanner_min:
                return phase_name, phase

        # Check conditional artifacts
        for artifact, condition in phase.get("conditional_artifacts", {}).items():
            if evaluate_condition(condition, mode):
                present, _ = check_artifact_present(scratchpad, artifact, min_bytes)
                if not present:
                    return phase_name, phase

        # Check niche artifacts if this phase defines them
        if phase.get("niche_artifacts_source"):
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
    """
    missing = []
    min_bytes = phase.get("min_file_bytes", 50)

    for artifact in phase.get("required_artifacts", []):
        if "*" in artifact:
            min_matches = 1
            glob_map = phase.get("min_required_glob_matches_map", {})
            if artifact in glob_map:
                min_matches = glob_map[artifact]
            elif "min_required_glob_matches" in phase:
                min_matches = phase["min_required_glob_matches"]

            satisfied, actual = check_glob_min_matches(scratchpad, artifact, min_matches, min_bytes)
            if not satisfied:
                missing.append((artifact, "need {} matches, found {}".format(min_matches, actual)))
        else:
            present, reason = check_artifact_present(scratchpad, artifact, min_bytes)
            if not present:
                missing.append((artifact, reason))

    # Check scanner_artifacts (flexible multi-pattern with min_matches)
    scanner_cfg = phase.get("scanner_artifacts")
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

    for artifact, condition in phase.get("conditional_artifacts", {}).items():
        if evaluate_condition(condition, mode):
            present, reason = check_artifact_present(scratchpad, artifact, min_bytes)
            if not present:
                missing.append((artifact, reason + " (conditional: {})".format(condition)))

    if phase.get("niche_artifacts_source"):
        niche_files = extract_niche_agents(scratchpad)
        for nf in niche_files:
            present, reason = check_artifact_present(scratchpad, nf, min_bytes)
            if not present:
                missing.append((nf, reason + " (niche agent)"))

    return missing


def detect_forward_leak(scratchpad, manifest, mode, current_phase_order):
    """
    Check if the orchestrator has started writing artifacts for a LATER phase
    while the current phase is still incomplete. This indicates step skipping.

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

        min_bytes = phase.get("min_file_bytes", 50)

        for artifact in phase.get("required_artifacts", []):
            if "*" in artifact:
                matches = glob_match(scratchpad, artifact)
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

    if mode not in ("light", "core", "thorough"):
        print("Invalid mode '{}'. Must be light, core, or thorough.".format(mode), file=sys.stderr)
        sys.exit(1)

    state = {
        "version": "1.0.0",
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

    if current_phase_name == "complete":
        # All phases have their artifacts — audit is done.
        # Clean up breadcrumb so the watchdog doesn't interfere with
        # non-audit work in the same project directory.
        try:
            os.remove(ACTIVE_AUDIT_PATH)
        except (IOError, OSError):
            pass
        sys.exit(0)

    # Get missing artifacts for current phase
    missing = get_missing_artifacts(scratchpad, current_phase, mode)
    if not missing:
        # Current phase is actually complete (edge case from conditional checks)
        sys.exit(0)

    # Check for forward leak - orchestrator started a later phase
    leaked_phase, leaked_artifact = detect_forward_leak(
        scratchpad, manifest, mode, current_phase["order"]
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
        print("Usage: phase_gate.py [--stop|--track-write|--init ...]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--stop":
        cmd_stop()
    elif cmd == "--track-write":
        cmd_track_write()
    elif cmd == "--init":
        cmd_init(sys.argv[2:])
    else:
        print("Unknown command: {}".format(cmd), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
