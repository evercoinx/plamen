"""Pin locally-installed MCP npm packages via `claude mcp add-json -s user`.

Replaces npx commands with direct node invocations pointing at the local
node_modules. Wraps servers that need schema sanitization. Preserves each
server's existing env vars.

Cross-platform: works on Windows, macOS, and Linux.
Target: Claude Code's user-scope MCP registry, via the `claude` CLI —
NOT ~/.claude/settings.json, whose `mcpServers` key the `claude` binary
does not read.
"""

import json
import os
import shutil
import subprocess
import sys

MCP_DIR = os.path.dirname(os.path.abspath(__file__))
NM = os.path.join(MCP_DIR, "node_modules")
SANITIZER = os.path.join(MCP_DIR, "schema-sanitizer.js")

# Map of server name -> (npm package path relative to node_modules, needs_sanitizer)
NPM_SERVERS = {
    "evm-chain-data": (os.path.join("@mcpdotdirect", "evm-mcp-server", "build", "index.js"), True),
    "foundry-suite": (os.path.join("@pranesh.asp", "foundry-mcp-server", "dist", "index.js"), True),
    "tavily-search": (os.path.join("tavily-mcp", "build", "index.js"), False),
    "memory": (os.path.join("@modelcontextprotocol", "server-memory", "dist", "index.js"), False),
    "helius": (os.path.join("@mcp-dockmaster", "mcp-server-helius", "build", "index.js"), False),
}


def _find_node():
    """Find the node binary path."""
    import shutil
    node = shutil.which("node")
    if node:
        return node
    # Windows fallback
    candidate = os.path.join("C:\\Program Files", "nodejs", "node.exe")
    if os.path.isfile(candidate):
        return candidate
    return "node"


def _build_args(entry_js, needs_sanitizer, node_bin):
    """Build the args list for a server entry. Cross-platform."""
    if needs_sanitizer:
        return [SANITIZER, entry_js]
    else:
        return [entry_js]


def _existing_env(name):
    """Read a currently-registered server's env vars from ~/.claude.json.

    `claude mcp get` has no machine-readable output (no --json flag) — reading
    the user-scope entry directly from ~/.claude.json is the only way to
    recover env vars before a remove+re-add. This is a read, not a write;
    all writes still go through `claude mcp add-json`.
    """
    path = os.path.join(os.path.expanduser("~"), ".claude.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("mcpServers", {}).get(name, {}).get("env", {})


def main():
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print("ERROR: claude CLI not found on PATH.", file=sys.stderr)
        sys.exit(1)

    node_bin = _find_node()
    updated, failed = [], []

    for name, (rel_path, needs_sanitizer) in NPM_SERVERS.items():
        entry_js = os.path.join(NM, rel_path)
        if not os.path.isfile(entry_js):
            print(f"  SKIP {name}: {entry_js} not found")
            continue

        existing_env = _existing_env(name)

        new_entry = {
            "command": node_bin,
            "args": _build_args(entry_js, needs_sanitizer, node_bin),
        }
        if existing_env:
            new_entry["env"] = existing_env

        # Re-registering requires removing any prior registration first —
        # `claude mcp add-json` errors if the name already exists.
        subprocess.run([claude_bin, "mcp", "remove", name, "-s", "user"],
                        capture_output=True, text=True, timeout=15)
        r = subprocess.run(
            [claude_bin, "mcp", "add-json", name, json.dumps(new_entry), "-s", "user"],
            capture_output=True, text=True, timeout=15,
        )
        tag = "+ SANITIZER" if needs_sanitizer else "direct"
        if r.returncode == 0:
            updated.append(f"  {name}: pinned ({tag})")
        else:
            failed.append(f"  {name}: {r.stderr.strip()[:200]}")

    print(f"DONE: {len(updated)} npm-based MCP servers pinned (user scope):")
    for line in updated:
        print(line)
    if failed:
        print(f"\nFAILED: {len(failed)}")
        for line in failed:
            print(line)
    print()
    print("Unchanged: Python-based servers (unified-vuln-db, slither-analyzer, etc.)")


if __name__ == "__main__":
    main()
