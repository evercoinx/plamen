#!/usr/bin/env python3
"""
Plamen Command Guard — blocks dangerous shell commands.

Mirrors the deny list from Claude Code's settings.json permissions.
Used as a PreToolUse hook in both Claude Code and Codex to prevent
destructive operations during autonomous audit runs.

Exit 0 + no output = allow
Exit 0 + {"decision": "block", "reason": "..."} = deny
"""

import json
import sys

# Commands that should NEVER run during an audit.
# Patterns are prefix-matched against the shell command string.
DENY_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf .",
    "sudo ",
    "git push --force",
    "git push -f ",
    "git reset --hard",
    "git clean -fd",
    "format ",
    "diskpart",
    "shutdown",
    "taskkill /f /im",
    "del /s /q c:",
    "rmdir /s /q",
    "mkfs.",
    "dd if=",
]

# Pipe-to-shell patterns (curl/wget piped to bash/sh)
PIPE_PATTERNS = [
    "| bash",
    "| sh",
    "| /bin/bash",
    "| /bin/sh",
]


def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, IOError):
        sys.exit(0)  # Can't parse — allow (fail-open)

    # Extract the command being run
    # Claude Code: tool_input.command for Bash tool
    # Codex: may vary — try multiple paths
    command = ""
    tool_input = data.get("tool_input", {})
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "")
    if not command:
        command = data.get("command", "")
    if not command:
        command = data.get("input", "")

    if not command:
        sys.exit(0)  # No command found — allow

    cmd_lower = command.lower().strip()

    # Check deny patterns
    for pattern in DENY_PATTERNS:
        if pattern.lower() in cmd_lower:
            print(json.dumps({
                "decision": "block",
                "reason": "[Plamen Guard] Blocked dangerous command: '{}'\nPattern matched: '{}'".format(
                    command[:100], pattern
                )
            }))
            sys.exit(0)

    # Check pipe-to-shell patterns
    for pattern in PIPE_PATTERNS:
        if pattern.lower() in cmd_lower:
            print(json.dumps({
                "decision": "block",
                "reason": "[Plamen Guard] Blocked pipe-to-shell: '{}'\nPattern matched: '{}'".format(
                    command[:100], pattern
                )
            }))
            sys.exit(0)

    # Allow
    sys.exit(0)


if __name__ == "__main__":
    main()
