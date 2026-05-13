#!/usr/bin/env python3
"""
Plamen L1 Primitive Telemetry Hook - primitive_telemetry.py

PreToolUse hook (matcher="*") that logs every tool invocation to a JSONL file
inside the active audit scratchpad. Used by benchmarks/l1/telemetry_check.py to
audit whether depth agents actually called SCIP reader / ast-grep / opengrep
primitives, rather than trusting the self-reported YAML header (Layer 1) which
is lossy and can be omitted entirely.

Two-layer telemetry design:
  Layer 1 (self-reported YAML header in depth_*_findings.md) - advisory
  Layer 2 (this JSONL log written from the PreToolUse hook) - authoritative

Dormancy:
  If no active audit is detected (no PLAMEN_SCRATCHPAD env var, no .active_audit
  breadcrumb, no nearby .scratchpad dir), the hook exits 0 silently with zero
  overhead. Non-audit Claude Code sessions are unaffected.

Input (stdin, PreToolUse JSON):
  {
    "tool_name": "Bash" | "Read" | "Write" | "mcp__scip-reader__find_definition" | ...,
    "tool_input": { ... },
    "session_id": "...",
    "transcript_path": "...",
    "cwd": "..."
  }

Output:
  Appends one JSON line to {scratchpad}/tool_calls.jsonl
  Always exits 0 (never blocks the tool call — this hook is observation-only).
"""

import json
import os
import sys
import time


HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIVE_AUDIT_PATH = os.path.join(HOOKS_DIR, ".active_audit")
JSONL_FILENAME = "tool_calls.jsonl"

# Tool name prefixes we care about for primitive-call auditing.
PRIMITIVE_PREFIXES = (
    "mcp__scip-reader__",
    "mcp__ast-grep-mcp__",
    "mcp__opengrep-mcp__",
    "mcp__unified-vuln-db__",
)


def read_stdin_json():
    try:
        data = sys.stdin.read()
        if not data or not data.strip():
            return {}
        return json.loads(data)
    except (json.JSONDecodeError, IOError, OSError):
        return {}


def find_active_scratchpad(cwd):
    """
    Resolve the active audit scratchpad, in order of precedence:
      1. PLAMEN_SCRATCHPAD env var
      2. .active_audit breadcrumb file (written by phase_gate.py --init)
      3. {cwd}/.scratchpad (smart-contract + L1 mode convention)

    Returns None if no active scratchpad found. No /tmp fallback - scratchpads
    MUST live in the project root so the user can gitignore them and they get
    cleaned up with the project, not scattered across system temp.
    """
    env_sp = os.environ.get("PLAMEN_SCRATCHPAD")
    if env_sp and os.path.isdir(env_sp):
        return env_sp

    if os.path.isfile(ACTIVE_AUDIT_PATH):
        try:
            with open(ACTIVE_AUDIT_PATH, "r") as f:
                breadcrumb = f.read().strip()
            if breadcrumb and os.path.isdir(breadcrumb):
                return breadcrumb
        except (IOError, OSError):
            pass

    if cwd:
        candidate = os.path.join(cwd, ".scratchpad")
        if os.path.isdir(candidate):
            return candidate

    return None


def extract_summary(tool_name, tool_input):
    """
    Extract a short, JSONL-friendly summary of the tool call. We do NOT persist
    full file contents (Read tool) or full Bash command strings verbatim - just
    enough to audit primitive usage.
    """
    summary = {"tool": tool_name}
    if not isinstance(tool_input, dict):
        return summary

    # Bash: store the first 200 chars of the command for primitive detection
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if isinstance(cmd, str):
            summary["command_prefix"] = cmd[:200]
            # Flag bash-based primitive invocations
            lc = cmd.lower()
            if "plamen_l1.scip_reader" in lc or "scip_reader" in lc:
                summary["primitive"] = "scip_reader_bash"
            elif "ast-grep" in lc or "sg " in lc[:4]:
                summary["primitive"] = "ast_grep_bash"
            elif "opengrep" in lc:
                summary["primitive"] = "opengrep_bash"
            elif lc.startswith("grep ") or " grep " in lc[:10]:
                summary["primitive"] = "grep_fallback"
        return summary

    # MCP primitives: record tool name + key input fields
    if tool_name.startswith(PRIMITIVE_PREFIXES):
        summary["primitive"] = tool_name.split("__")[1] if "__" in tool_name else tool_name
        # Keep a tiny subset of input keys for auditing
        for key in ("query", "symbol", "pattern", "lang", "rule_id", "hypothesis"):
            if key in tool_input:
                val = tool_input[key]
                if isinstance(val, str):
                    summary[key] = val[:120]
                else:
                    summary[key] = val
        return summary

    # Read/Write/Edit: record just the target path
    for key in ("file_path", "path"):
        if key in tool_input:
            summary["path"] = tool_input[key]
            break

    return summary


def main():
    payload = read_stdin_json()
    if not payload:
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    cwd = payload.get("cwd", "")
    session_id = payload.get("session_id", "")

    scratchpad = find_active_scratchpad(cwd)
    if not scratchpad:
        # Dormant - no active audit, nothing to log
        sys.exit(0)

    run_id = ""
    state_path = os.path.join(scratchpad, "watchdog_state.json")
    if os.path.isfile(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            run_id = state.get("run_id", "")
        except (IOError, OSError, json.JSONDecodeError):
            run_id = ""

    jsonl_path = os.path.join(scratchpad, JSONL_FILENAME)
    record = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": run_id,
        "session": session_id[:16] if session_id else "",
        "summary": extract_summary(tool_name, tool_input),
    }

    try:
        os.makedirs(scratchpad, exist_ok=True)
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except (IOError, OSError):
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
