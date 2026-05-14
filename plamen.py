#!/usr/bin/env python3
"""Plamen — Web3 Security Auditor CLI wrapper.

Renders the startup UI in the user's real terminal, collects inputs
via arrow-key selection menus, then hands off to Claude Code.
"""
import sys, os, shutil, glob, subprocess, re, sqlite3

# Windows: enable VT100 + force UTF-8 stdout before anything loads
if sys.platform == "win32":
    import io
    os.system("")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# ── Bootstrap: auto-install core deps on first run ──────────
_BREAK_SYSTEM_PACKAGES_NOTICE_SHOWN = False


def _pip_install_args():
    """Build pip install flags that work on PEP 668 systems (macOS Homebrew, Ubuntu 23.04+).

    On externally-managed Python installs, pip refuses to write into the
    system site-packages without `--break-system-packages`. Plamen adds it
    automatically; set `PIP_BREAK_SYSTEM_PACKAGES=0` to opt out (e.g. if you
    want to confine the install to a venv yourself).
    """
    global _BREAK_SYSTEM_PACKAGES_NOTICE_SHOWN
    args = [sys.executable, "-m", "pip", "install"]
    if sys.platform != "win32":
        args.append("--user")
    # Detect PEP 668 "externally managed" environments
    try:
        import sysconfig
        marker = os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")
        if os.path.isfile(marker):
            # Honor opt-out: PIP_BREAK_SYSTEM_PACKAGES=0 explicitly disables.
            if os.environ.get("PIP_BREAK_SYSTEM_PACKAGES") == "0":
                if not _BREAK_SYSTEM_PACKAGES_NOTICE_SHOWN:
                    sys.stderr.write(
                        "  Plamen: PIP_BREAK_SYSTEM_PACKAGES=0 honored — pip "
                        "may refuse to install into externally-managed Python.\n"
                        "  Activate a virtualenv first or unset the variable.\n"
                    )
                    _BREAK_SYSTEM_PACKAGES_NOTICE_SHOWN = True
            else:
                if not _BREAK_SYSTEM_PACKAGES_NOTICE_SHOWN:
                    sys.stderr.write(
                        "  Plamen: PEP 668 externally-managed Python detected; "
                        "adding `--break-system-packages` to pip install.\n"
                        "  Set PIP_BREAK_SYSTEM_PACKAGES=0 (and re-run from a "
                        "virtualenv) if you'd rather isolate.\n"
                    )
                    _BREAK_SYSTEM_PACKAGES_NOTICE_SHOWN = True
                args.append("--break-system-packages")
    except Exception:
        pass
    return args


def _bootstrap():
    """Install rich + InquirerPy if missing. Returns True if deps are available."""
    try:
        import rich, InquirerPy  # noqa: F401
        return True
    except ImportError:
        req = os.path.join(os.path.dirname(os.path.realpath(__file__)), "requirements.txt")
        if not os.path.isfile(req):
            return False
        print("  Installing Plamen dependencies (first run)...")
        r = subprocess.run(_pip_install_args() + ["-q", "-r", req])
        if r.returncode == 0:
            print("  Done. Restarting...\n")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        return False

if not _bootstrap():
    print("Error: Could not install dependencies. Run manually:")
    print(f"  {sys.executable} -m pip install rich InquirerPy")
    sys.exit(1)

from rich.console import Console
from rich.text import Text
from rich.rule import Rule
from InquirerPy import inquirer
from InquirerPy.separator import Separator
from InquirerPy.utils import InquirerPyStyle

# ── Paths ───────────────────────────────────────────────────
# PLAMEN_HOME: where the Plamen repo actually lives (resolves through symlinks)
# CLAUDE_HOME: where Claude Code reads config from (always ~/.claude)
PLAMEN_HOME = os.path.dirname(os.path.realpath(__file__))
CLAUDE_HOME = os.path.expanduser("~/.claude")

# ── Version ─────────────────────────────────────────────────
def _read_version() -> str:
    vfile = os.path.join(PLAMEN_HOME, "VERSION")
    try:
        with open(vfile) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "dev"

VERSION = _read_version()


def _check_claude_md_version():
    """Warn if ~/.claude/CLAUDE.md has a stale Plamen injection (different version)."""
    claude_md = os.path.join(CLAUDE_HOME, "CLAUDE.md")
    if not os.path.isfile(claude_md):
        return  # not installed yet
    try:
        with open(claude_md, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return
    # Look for version in the injected Plamen section
    marker = "<!-- PLAMEN:START"
    if marker not in content:
        return  # no injection found
    injected = content[content.index(marker):]
    # Extract version from "# Plamen - Security Auditor (vX.Y.Z)"
    import re
    m = re.search(r"Security Auditor \(v([0-9]+\.[0-9]+\.[0-9]+)\)", injected)
    if not m:
        return
    injected_ver = m.group(1)
    if injected_ver != VERSION:
        w = sys.stdout.write
        w(f"\n  \033[33m⚠ Version mismatch: repo is v{VERSION} but "
          f"~/.claude/CLAUDE.md has v{injected_ver}\033[0m\n")
        w(f"  \033[90m  Run 'plamen install' to update. Pipeline may behave "
          f"incorrectly until then.\033[0m\n\n")


# ── Constants ────────────────────────────────────────────────
_BACK = "__back__"
_MAX_LINE = 48  # max visible chars for any prompt line (W - 4)

# ── Back-navigation: clear screen + compact re-render ──────
_breadcrumbs: list[tuple[str, str]] = []

def _crumb_set(crumbs: list[tuple[str, str]]) -> None:
    """Replace breadcrumbs with [(label, value), ...]."""
    _breadcrumbs.clear()
    _breadcrumbs.extend(crumbs)

def _clear_and_rebanner() -> None:
    """Clear terminal + reprint full banner + breadcrumbs of prior answers."""
    os.system("cls" if sys.platform == "win32" else "clear")
    show_banner()
    if _breadcrumbs:
        for label, value in _breadcrumbs:
            sys.stdout.write(f"  {_C_GREEN}✓{_RST} {_DIM}{label}: {value}{_RST}\n")
        sys.stdout.write("\n")
    sys.stdout.flush()

_STYLE = InquirerPyStyle({
    "questionmark": "#7030FF bold",
    "pointer":      "#7030FF bold",
    "highlighted":  "#ffffff bold bg:#101018",
    "selected":     "#22C72E",
    "answer":       "#7030FF bold",
    "question":     "#ffffff",
    "input":        "#ffffff",
})

console = Console(file=sys.stdout, highlight=False, force_terminal=True, legacy_windows=False)

# ── ANSI helpers ─────────────────────────────────────────────
_RST  = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM  = "\x1b[2m"

def _has_truecolor() -> bool:
    """Detect 24-bit color support. macOS Terminal.app only does 256."""
    ct = os.environ.get("COLORTERM", "")
    if ct in ("truecolor", "24bit"):
        return True
    term_prog = os.environ.get("TERM_PROGRAM", "")
    if term_prog in ("iTerm.app", "WezTerm", "Hyper", "vscode"):
        return True
    if sys.platform == "win32":
        return True  # Windows Terminal / ConPTY supports truecolor
    return False

_TRUECOLOR = _has_truecolor()

def _c(r: int, g: int, b: int, fallback_256: int) -> str:
    """Return truecolor or 256-color escape depending on terminal support."""
    if _TRUECOLOR:
        return f"\x1b[38;2;{r};{g};{b}m"
    return f"\x1b[38;5;{fallback_256}m"

# Colors — brand: green #22C72E, purple #7030FF
_C_ACCENT    = _c(112, 48, 255, 99)       # #7030FF purple → 256: 99
_C_ORANGE    = _C_ACCENT                   # alias for 60+ existing call sites
_C_BLUE      = _c(100, 149, 237, 111)      # cornflower → 256: 111
_C_GREEN     = _c(34, 199, 46, 40)         # #22C72E → 256: 40
_C_RED       = _c(200, 60, 60, 160)        # → 256: 160
_C_WHITE     = _c(255, 255, 255, 231)      # → 256: 231
_C_GRAY      = _c(100, 100, 100, 242)      # → 256: 242
_C_DARK_GRAY = _c(60, 60, 60, 239)         # → 256: 239
_C_BOX       = _c(24, 24, 32, 235)         # → 256: 235

# ── Banner gradient: green → purple ──────────────────────────
# 6 rows interpolated from #22C72E to #7030FF
_BANNER_GRAD = [
    _c(34, 199, 46, 40),     # row 0: #22C72E → green
    _c(50, 169, 88, 35),     # row 1: #32A958 → green-teal
    _c(65, 139, 130, 73),    # row 2: #418B82 → teal
    _c(81, 108, 171, 104),   # row 3: #516CAB → blue
    _c(96, 78, 213, 98),     # row 4: #604ED5 → indigo
    _c(112, 48, 255, 99),    # row 5: #7030FF → purple
]
_ART_FULL = [
    " ██████╗ ██╗      █████╗ ███╗   ███╗███████╗███╗   ██╗",
    " ██╔══██╗██║     ██╔══██╗████╗ ████║██╔════╝████╗  ██║",
    " ██████╔╝██║     ███████║██╔████╔██║█████╗  ██╔██╗ ██║",
    " ██╔═══╝ ██║     ██╔══██║██║╚██╔╝██║██╔══╝  ██║╚██╗██║",
    " ██║     ███████╗██║  ██║██║ ╚═╝ ██║███████╗██║ ╚████║",
    " ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚═╝  ╚═══╝",
]
_ART_COMPACT = [
    " PLAMEN",
]


def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except (ValueError, OSError):
        return 80


NETWORKS = {
    "ethereum":  "Ethereum Mainnet",
    "arbitrum":  "Arbitrum One",
    "optimism":  "Optimism",
    "base":      "Base",
    "polygon":   "Polygon",
    "bsc":       "BNB Smart Chain",
    "avalanche": "Avalanche C-Chain",
    "other":     "Other (specify RPC)",
}

MODES = {
    "light":    {"label": "Light Audit",    "agents": "18-22",    "scope": "ALL severities"},
    "core":     {"label": "Core Audit",     "agents": "30-50",    "scope": "ALL severities"},
    "thorough": {"label": "Thorough Audit", "agents": "40-100",   "scope": "ALL severities"},
    "compare":  {"label": "Compare",        "agents": "variable", "scope": "DELTA report"},
}
L1_MODES = {
    "light":    {"label": "L1 Light",    "agents": "15-20",  "scope": "Quick scan"},
    "core":     {"label": "L1 Core",     "agents": "25-40",  "scope": "Standard L1 depth"},
    "thorough": {"label": "L1 Thorough", "agents": "35-55",  "scope": "Iterative + re-scan"},
}


# ── Dependency check ─────────────────────────────────────────

def _python_bin() -> str:
    """Return the Python interpreter command for use in shell strings.

    Uses sys.executable (the interpreter running plamen.py itself) so subprocess
    commands always use the same venv or system Python that launched plamen.
    Path is quoted if it contains spaces.
    """
    exe = sys.executable
    if " " in exe:
        return f'"{exe}"'
    return exe


def _python_extra_paths() -> list:
    """Discover Python install directories on Windows (any version)."""
    if sys.platform != "win32":
        return []
    base = os.path.expanduser("~/AppData/Local/Programs/Python")
    if not os.path.isdir(base):
        return []
    return [os.path.join(base, d) for d in sorted(os.listdir(base), reverse=True)
            if d.startswith("Python")]


def _find_bin(name: str, extra_paths: list = None) -> str:
    """Find a binary in PATH or common install locations."""
    found = shutil.which(name)
    if found:
        return found
    for p in (extra_paths or []):
        for ext in ("", ".exe", ".cmd"):
            full = os.path.join(os.path.expanduser(p), name + ext)
            if os.path.isfile(full):
                return full
    return ""


def _find_claude_bin() -> str:
    """Find Claude Code CLI, honoring explicit override env first."""
    explicit = os.environ.get("CLAUDE_BIN", "").strip()
    if explicit:
        return explicit
    return _find_bin("claude")


def _find_codex_bin() -> str:
    """Find Codex CLI, honoring explicit override env first."""
    explicit = os.environ.get("CODEX_BIN", "").strip()
    if explicit:
        return explicit
    return _find_bin("codex")


def _detect_cli_backends() -> list[str]:
    """Return installed AI runtimes in stable preference order."""
    backends: list[str] = []
    if _find_claude_bin():
        backends.append("claude")
    if _find_codex_bin():
        backends.append("codex")
    return backends


def _ambient_backend(backends: list[str]) -> str:
    """Pick the backend implied by the current command/model context."""
    forced = os.environ.get("PLAMEN_CLI_BACKEND", "").strip().lower()
    if forced in ("claude", "codex"):
        return forced
    if "codex" in backends and (
        os.environ.get("CODEX_HOME")
        or os.environ.get("CODEX_SANDBOX")
    ):
        return "codex"
    if "claude" in backends:
        return "claude"
    return backends[0] if backends else "claude"


def _skip_backend_prompt() -> bool:
    """True when invoked by a slash-command wizard already inside a model."""
    return os.environ.get("PLAMEN_SKIP_BACKEND_PROMPT", "").strip().lower() in {
        "1", "true", "yes",
    }


def _wizard_model_summary(backend: str, mode: str = "") -> str:
    """Short model line for the launch summary."""
    backend = (backend or "claude").strip().lower()
    mode = (mode or "").strip().lower()
    if backend == "codex":
        sonnet = os.environ.get("PLAMEN_CODEX_SONNET_MODEL", "gpt-5.4").strip()
        if mode == "light":
            return f"Codex CLI / {sonnet}"
        opus = os.environ.get("PLAMEN_CODEX_OPUS_MODEL", "gpt-5.5").strip()
        haiku = os.environ.get("PLAMEN_CODEX_HAIKU_MODEL", "gpt-5.4-nano").strip()
        haiku_label = "nano" if haiku == "gpt-5.4-nano" else haiku
        return f"Codex CLI / {opus}, {sonnet}, {haiku_label}"
    if mode == "light":
        return "Claude Code / sonnet"
    opus = os.environ.get("PLAMEN_OPUS_MODEL", "claude-opus-4-6").strip()
    return f"Claude Code / {opus}, sonnet, haiku"


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _vis_len(s: str) -> int:
    """Visible length of a string after stripping ANSI escape codes."""
    return len(_ANSI_RE.sub('', s))


def _check_tool(name: str, binary: str) -> str:
    """Return a colored status string for one tool."""
    if binary:
        return f"{_C_GREEN}✓{_RST}{_C_GRAY}{name}{_RST}"
    return f"{_C_DARK_GRAY}○{name}{_RST}"


def _box_row(w, bx: str, W: int, content: str, right: str = ""):
    """Write one box row: │ content ... right │ with exact W inner width."""
    c_vis = _vis_len(content)
    r_vis = _vis_len(right)
    gap = max(1, W - c_vis - r_vis)
    w(f"  {bx}│{_RST}{content}{' ' * gap}{right}{bx}│{_RST}\n")


_RAG_MIN_ENTRIES = 500  # Below this, RAG is a partial/crashed build — flag as incomplete


def _probe_rag_db() -> int:
    """Return the number of entries in the RAG vulnerability database, or -1 if not found."""
    db_path = os.path.join(PLAMEN_HOME, "unified-vuln-db", "data", "chroma_db", "chroma.sqlite3")
    if not os.path.isfile(db_path):
        return -1
    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        return count
    except Exception:
        return -1


def _probe_mcp_server(name: str, cmd: str, args: list, cwd: str = None,
                      env: dict = None, timeout: float = 10) -> bool:
    """Start an MCP server, send JSON-RPC initialize, check for a response, then kill it.
    Returns True if the server responds to init within timeout."""
    import json as _json
    full_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.Popen(
            [cmd] + args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=cwd, env=full_env)
        # JSON-RPC initialize request (MCP protocol)
        init_msg = _json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "capabilities": {},
                       "clientInfo": {"name": "plamen-probe", "version": "1"}}
        })
        # Send raw newline-delimited JSON. Both Python MCP SDK servers (Content-Length
        # framed) and Node MCP servers (newline-delimited) accept this format for the
        # health probe. Using Content-Length framing alone fails for tavily/helius.
        proc.stdin.write(init_msg.encode() + b"\n")
        proc.stdin.flush()
        # Wait for any stdout response (just check it writes back something)
        import select
        if sys.platform == "win32":
            # Windows: can't select on pipes, just do a timed read
            import threading
            result = [False]
            def _read():
                data = proc.stdout.read(1)
                if data:
                    result[0] = True
            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(timeout)
        else:
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            result = [len(ready) > 0]
        # Clean up: kill process and reap to avoid resource leaks
        proc.kill()
        proc.wait(timeout=3)
        return result[0]
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
        return False


def _npx_package_cached(pkg: str) -> bool:
    """Check if an npx package is in the local npm cache.
    npx caches packages in <npm_cache>/_npx/<hash>/package.json with a dependencies dict."""
    try:
        npm_bin = shutil.which("npm") or "npm"
        r = subprocess.run([npm_bin, "config", "get", "cache"],
                           capture_output=True, text=True, timeout=5)
        cache_dir = r.stdout.strip()
        if not cache_dir or not os.path.isdir(cache_dir):
            return False
        npx_dir = os.path.join(cache_dir, "_npx")
        if not os.path.isdir(npx_dir):
            return False
        import json as _json
        for entry in os.listdir(npx_dir):
            pj = os.path.join(npx_dir, entry, "package.json")
            if os.path.isfile(pj):
                try:
                    with open(pj) as f:
                        data = _json.load(f)
                    if pkg in data.get("dependencies", {}):
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def _probe_mcp_servers() -> list:
    """Probe configured MCP servers for health. Returns list of (name, ok) tuples.
    npx-based servers are skipped if the package isn't cached (download would exceed timeout)."""
    import json as _json
    mcp_path = os.path.join(CLAUDE_HOME, "mcp.json")
    if not os.path.isfile(mcp_path):
        return []
    try:
        with open(mcp_path) as f:
            mcp = _json.load(f)
    except Exception:
        return []

    results = []
    for name, config in mcp.get("mcpServers", {}).items():
        cmd = config.get("command", "")
        args = config.get("args", [])
        cwd = config.get("cwd")
        env = config.get("env")
        # Only probe if the command binary exists
        if not shutil.which(cmd) and not os.path.isfile(cmd):
            results.append((name, False))
            continue
        # npx-based servers: skip probe if package isn't cached yet
        # (npx would download the package first, easily exceeding the timeout)
        cmd_base = os.path.basename(cmd).lower().replace(".cmd", "").replace(".exe", "")
        if cmd_base == "npx" and len(args) >= 2 and args[0] == "-y":
            # Strip version suffix (@latest, @1.2.3) but preserve scoped package prefix (@org/pkg)
            raw = args[1]
            if raw.startswith("@") and "@" in raw[1:]:
                # Scoped package with version: @org/pkg@latest -> @org/pkg
                pkg = raw[:raw.rindex("@")]
            elif not raw.startswith("@") and "@" in raw:
                # Unscoped package with version: pkg@latest -> pkg
                pkg = raw.rsplit("@", 1)[0]
            else:
                # No version suffix (scoped or unscoped)
                pkg = raw
            if not _npx_package_cached(pkg):
                results.append((name, None))  # None = not cached, skip probe
                continue
        # npx-based servers need longer to boot on Windows (Node.js cold start + package init)
        probe_timeout = 20 if cmd_base == "npx" else 10
        ok = _probe_mcp_server(name, cmd, args, cwd=cwd, env=env, timeout=probe_timeout)
        results.append((name, ok))
    return results


def check_dependencies() -> bool:
    """Verify required and optional tools. Returns True if required deps OK."""
    w = sys.stdout.write
    bx = _C_BOX
    W = 52
    ok = True

    # ── Probe all tools ─────────────────────────────────────
    backends = _detect_cli_backends()
    required = [
        ("claude/codex",  backends[0] if backends else ""),
        ("python",  _find_bin("python", _python_extra_paths()) or _find_bin("python3")),
        ("npx",     _find_bin("npx")),
        ("npm",     _find_bin("npm")),
        ("git",     _find_bin("git")),
    ]
    req_found = sum(1 for _, b in required if b)
    ok = req_found == len(required)

    groups = [
        ("EVM", [
            ("forge",   _find_bin("forge", ["~/.foundry/bin"])),
            ("anvil",   _find_bin("anvil", ["~/.foundry/bin"])),
            ("cast",    _find_bin("cast", ["~/.foundry/bin"])),
            ("slither", _find_bin("slither") or _find_bin("slither-mcp")),
            ("medusa",  _find_bin("medusa", ["~/go/bin"])),
        ]),
        ("Solana", [
            ("solana",  _find_bin("solana", ["~/.local/share/solana/install/active_release/bin"])),
            ("anchor",  _find_bin("anchor", ["~/.avm/bin"])),
            ("cargo",   _find_bin("cargo-build-sbf") or _find_bin("cargo")),
            ("trident", _find_bin("trident")),
            ("scout",   _find_bin("cargo-scout-audit", _CARGO_PATHS)),
        ]),
        ("Move", [
            ("aptos",    _find_bin("aptos", ["~/.aptoscli/bin"])),
            ("sui",      _find_bin("sui", ["~/AppData/Local/bin", "~/.local/bin"])),
            ("ast-grep", _find_bin("ast-grep", _CARGO_PATHS + (
                ["/opt/homebrew/bin", "/usr/local/bin"] if sys.platform == "darwin" else []
            )) or _find_bin("sg", _CARGO_PATHS)),
        ]),
        ("Soroban", [
            ("stellar", _find_bin("stellar", ["~/.cargo/bin",
                                              "C:/Program Files (x86)/Stellar CLI",
                                              "C:/Program Files/Stellar CLI"])),
            ("scout",   _find_bin("cargo-scout-audit", ["~/.cargo/bin"])),
        ]),
        ("L1 (Go)", [
            ("go",       _find_bin("go", _GO_PATHS)),
            ("scip-go",  _find_bin("scip-go", _GO_PATHS)),
            ("opengrep", _find_bin("opengrep") or _find_bin("semgrep")),
        ]),
        ("L1 (Rust)", [
            ("cargo",          _find_bin("cargo", _CARGO_PATHS)),
            ("rust-analyzer",  _find_bin("rust-analyzer", _CARGO_PATHS + (
                ["/opt/homebrew/bin", "/usr/local/bin"] if sys.platform == "darwin" else []
            ))),
            ("ast-grep",       _find_bin("ast-grep", _CARGO_PATHS + (
                ["/opt/homebrew/bin", "/usr/local/bin"] if sys.platform == "darwin" else []
            )) or _find_bin("sg", _CARGO_PATHS)),
        ]),
    ]

    # ── Draw box ────────────────────────────────────────────
    w(f"  {bx}╭{'─' * W}╮{_RST}\n")

    # Header
    _box_row(w, bx, W,
             f"  {_BOLD}{_C_WHITE}Toolchain{_RST}")

    w(f"  {bx}│{_RST}{' ' * W}{bx}│{_RST}\n")

    # Required row
    req_tools = "  " + "  ".join(_check_tool(n, b) for n, b in required)
    if ok:
        tag = f"{_C_GREEN}ok{_RST}"
    else:
        n_miss = len(required) - req_found
        tag = f"{_C_RED}{n_miss} missing{_RST}"
    _box_row(w, bx, W, req_tools, tag)

    # Show missing details
    if not ok:
        for name, binary in required:
            if not binary:
                _box_row(w, bx, W,
                         f"    {_C_RED}✗ {name} not found{_RST}")

    # Alternative backend row
    codex_bin = _find_bin("codex")
    _box_row(w, bx, W,
             f"  {_C_GRAY}Backend{_RST}   {_check_tool('claude', _find_bin('claude'))}  "
             f"{_check_tool('codex', codex_bin)}")

    w(f"  {bx}├{'─' * W}┤{_RST}\n")

    # Optional groups
    for group_name, tools in groups:
        found = sum(1 for _, b in tools if b)
        total = len(tools)
        label = f"  {_C_GRAY}{group_name}{_RST}"
        pad = " " * (9 - len(group_name))
        tool_str = " ".join(_check_tool(n, b) for n, b in tools)
        content = f"{label}{pad}{tool_str}"
        if found == total:
            count = f"{_C_GREEN}{found}/{total}{_RST}"
        elif found == 0:
            count = f"{_C_DARK_GRAY}{found}/{total}{_RST}"
        else:
            count = f"{_C_ORANGE}{found}/{total}{_RST}"
        _box_row(w, bx, W, content, count)

    # RAG database status
    w(f"  {bx}├{'─' * W}┤{_RST}\n")
    rag_count = _probe_rag_db()
    if rag_count >= _RAG_MIN_ENTRIES:
        rag_status = f"{_C_GREEN}{rag_count:,} entries{_RST}  {_C_DARK_GRAY}(cold-start ~5s on first query){_RST}"
    elif rag_count > 0:
        rag_status = f"{_C_ORANGE}{rag_count:,} (incomplete){_RST}"
    elif rag_count == 0:
        rag_status = f"{_C_RED}empty{_RST}"
    else:
        rag_status = (f"{_C_RED}not built{_RST}"
                      f"  {_C_DARK_GRAY}run 'plamen rag' (~10 min, CPU intensive){_RST}")
    _box_row(w, bx, W,
             f"  {_C_GRAY}RAG DB{_RST}   vulnerability knowledge base",
             rag_status)

    # MCP server health probes
    w(f"  {bx}├{'─' * W}┤{_RST}\n")
    mcp_results = _probe_mcp_servers()
    if mcp_results:
        mcp_probed = [(n, s) for n, s in mcp_results if s is not None]
        mcp_ok = sum(1 for _, s in mcp_probed if s)
        mcp_total = len(mcp_results)
        mcp_skipped = sum(1 for _, s in mcp_results if s is None)
        if mcp_ok == len(mcp_probed) and mcp_skipped == 0:
            mcp_tag = f"{_C_GREEN}{mcp_ok}/{mcp_total}{_RST}"
        elif mcp_ok == 0 and mcp_skipped == 0:
            mcp_tag = f"{_C_RED}{mcp_ok}/{mcp_total}{_RST}"
        else:
            label = f"{mcp_ok}/{mcp_total}"
            if mcp_skipped:
                label += f" ({mcp_skipped} skip)"
            mcp_tag = f"{_C_ORANGE}{label}{_RST}"
        # Split into rows of ~4-5 servers to fit box width
        _box_row(w, bx, W, f"  {_BOLD}{_C_WHITE}MCP Servers{_RST}", mcp_tag)
        row_items = []
        row_vis = 2  # leading indent
        for name, status in mcp_results:
            # Use short names: drop common suffixes
            short = name.replace("-analyzer", "").replace("-search", "") \
                        .replace("-suite", "").replace("-chain-data", "")
            if status is None:
                # Not cached / skipped — show with dim marker
                item = f"{_C_DARK_GRAY}~{short}{_RST}"
            else:
                item = _check_tool(short, status)
            item_vis = len(short) + 1  # icon + name
            if row_vis + item_vis + 1 > W - 2 and row_items:
                _box_row(w, bx, W, "  " + " ".join(row_items))
                row_items = []
                row_vis = 2
            row_items.append(item)
            row_vis += item_vis + 1
        if row_items:
            _box_row(w, bx, W, "  " + " ".join(row_items))
        # Show names of failed servers (not skipped ones)
        failed = [n for n, s in mcp_results if s is False]
        if failed:
            for n in failed:
                _box_row(w, bx, W, f"    {_C_RED}✗ {n}: not responding{_RST}")
    else:
        _box_row(w, bx, W,
                 f"  {_C_GRAY}MCP{_RST}      no servers configured",
                 f"{_C_DARK_GRAY}--{_RST}")

    w(f"  {bx}╰{'─' * W}╯{_RST}\n")

    # Summary line
    total_opt = sum(len(t) for _, t in groups)
    total_found = sum(1 for _, tools in groups for _, b in tools if b)
    if total_found == total_opt:
        w(f"  {_C_DARK_GRAY}All {total_opt} optional tools available{_RST}\n")
    else:
        w(f"  {_C_DARK_GRAY}{total_found}/{total_opt} optional — "
          f"install per your target chain{_RST}\n")

    # Windows Developer Mode check (required for Solana symlinks)
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock")
            val, _ = winreg.QueryValueEx(key, "AllowDevelopmentWithoutDevLicense")
            winreg.CloseKey(key)
            if val != 1:
                w(f"\n  {_C_ORANGE}Windows Developer Mode is OFF{_RST}\n")
                w(f"  {_C_GRAY}Solana build tools require symlinks. Enable via:{_RST}\n")
                w(f"  {_C_GRAY}  Settings > System > For Developers > Developer Mode{_RST}\n")
                w(f"  {_C_GRAY}  Or run in admin PowerShell:{_RST}\n")
                w(f"  {_C_GRAY}  reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f{_RST}\n")
        except Exception:
            w(f"\n  {_C_ORANGE}Could not check Windows Developer Mode{_RST}\n")
            w(f"  {_C_GRAY}If Solana builds fail with 'privilege' errors, enable Developer Mode{_RST}\n")

    w("\n")
    sys.stdout.flush()
    return ok


# ── Installer ───────────────────────────────────────────────

# Defined early so _INSTALL_RECIPES below can use them in inline conditionals
# at module-load time (e.g. the macOS+brew test for the rust-analyzer prereq).
def _has_bash() -> bool:
    return bool(shutil.which("bash"))


def _has_brew() -> bool:
    return bool(shutil.which("brew"))


def _has_winget() -> bool:
    return sys.platform == "win32" and bool(shutil.which("winget"))


# ── Prerequisite installers (auto-installed when needed) ────

_FOUNDRY_PATHS = ["~/.foundry/bin"]
_SOLANA_PATHS = ["~/.local/share/solana/install/active_release/bin"]
_AVM_PATHS = ["~/.avm/bin"]
_CARGO_PATHS = ["~/.cargo/bin"]
_GO_PATHS = ["~/go/bin", "/usr/local/go/bin",
             "/c/Program Files/Go/bin", "C:/Program Files/Go/bin"]

def _rust_install_cmds():
    if sys.platform == "win32":
        if _has_winget():
            return ['winget install --id Rustlang.Rustup -e --accept-source-agreements'
                    ' --accept-package-agreements']
        elif _has_bash():
            return ['curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y']
        return []  # manual
    return ['curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y']


def _go_install_cmds():
    if sys.platform == "win32" and _has_winget():
        return ['winget install --id GoLang.Go -e --accept-source-agreements'
                ' --accept-package-agreements']
    if sys.platform == "darwin" and _has_brew():
        return ['brew install go']
    return []  # manual — show link


def _ensure_openssl_env():
    """Check if OpenSSL dev libs are available; sets OPENSSL_* env vars if found."""
    if sys.platform != "win32":
        return True  # usually available via system packages on Unix
    if os.environ.get("OPENSSL_LIB_DIR") and os.environ.get("OPENSSL_INCLUDE_DIR"):
        return True
    # Check vcpkg first (common developer setup)
    vcpkg_root = os.environ.get("VCPKG_ROOT", "")
    if vcpkg_root:
        vcpkg_lib = os.path.join(vcpkg_root, "installed", "x64-windows", "lib")
        vcpkg_inc = os.path.join(vcpkg_root, "installed", "x64-windows", "include")
        if os.path.isfile(os.path.join(vcpkg_lib, "libcrypto.lib")):
            os.environ["OPENSSL_LIB_DIR"] = vcpkg_lib
            os.environ["OPENSSL_INCLUDE_DIR"] = vcpkg_inc
            return True
    # ShiningLight OpenSSL (most common Windows installer)
    for base in [r"C:\Program Files\OpenSSL-Win64",
                 r"C:\Program Files\OpenSSL",
                 r"C:\OpenSSL-Win64"]:
        # ShiningLight puts .lib files in lib/VC/x64/MD/, not lib/
        lib_dir = os.path.join(base, "lib", "VC", "x64", "MD")
        include_dir = os.path.join(base, "include")
        if os.path.isfile(os.path.join(lib_dir, "libcrypto.lib")):
            os.environ["OPENSSL_DIR"] = base
            os.environ["OPENSSL_LIB_DIR"] = lib_dir
            os.environ["OPENSSL_INCLUDE_DIR"] = include_dir
            return True
        # Flat lib/ for other installers (choco, manual)
        flat_lib = os.path.join(base, "lib")
        if os.path.isfile(os.path.join(flat_lib, "libcrypto.lib")):
            os.environ["OPENSSL_DIR"] = base
            return True
    return False


def _openssl_install_cmds():
    if sys.platform == "win32" and _has_winget():
        return ['winget install --id ShiningLight.OpenSSL.Dev -e'
                ' --accept-source-agreements --accept-package-agreements']
    return []


_PREREQ_INSTALLERS = {
    "rust": {
        "check": lambda: bool(_find_bin("cargo", _CARGO_PATHS)),
        "cmds_fn": _rust_install_cmds,
        "paths": _CARGO_PATHS,
        "label": "Rust/Cargo",
        "est": "~30s",
        "url": "https://rustup.rs",
    },
    "go": {
        "check": lambda: bool(_find_bin("go", _GO_PATHS)),
        "cmds_fn": _go_install_cmds,
        "paths": _GO_PATHS,
        "label": "Go",
        "est": "~30s",
        "url": "https://go.dev/doc/install",
    },
    "openssl": {
        "check": _ensure_openssl_env,
        "cmds_fn": _openssl_install_cmds,
        "paths": [],
        "label": "OpenSSL (dev)",
        "est": "~30s",
        "url": "https://slproweb.com/products/Win32OpenSSL.html",
    },
}


def _ensure_prereq(prereq_name: str, w) -> bool:
    """Check and install a prerequisite. Returns True if available (was present or installed)."""
    prereq = _PREREQ_INSTALLERS.get(prereq_name)
    if not prereq:
        return True
    if prereq["check"]():
        # Already installed — but ensure its paths are in subprocess PATH
        _update_path_env(prereq["paths"])
        return True

    # Prerequisite missing — try to install
    label = prereq["label"]
    cmds = prereq["cmds_fn"]()
    if not cmds:
        # No auto-install available for this platform
        w(f"  {_C_ORANGE}  requires {label} — install manually:{_RST}\n")
        w(f"  {_C_DARK_GRAY}  {prereq['url']}{_RST}\n")
        return False

    w(f"  {_C_BLUE}  installing prerequisite: {label}{_RST}"
      f"  {_C_DARK_GRAY}{prereq['est']}{_RST}\n")
    sys.stdout.flush()
    for cmd in cmds:
        if not _run_install_cmd(cmd, retries=1):
            w(f"  {_C_RED}  {label} install failed — install manually: {prereq['url']}{_RST}\n")
            return False
    # Update PATH for the newly installed prerequisite
    _refresh_system_path()  # pick up winget/msi PATH changes
    _update_path_env(prereq["paths"])
    # Re-check after install (with fresh system PATH on Windows)
    _refresh_system_path()
    if prereq["check"]():
        w(f"  {_C_GREEN}  {label} installed{_RST}\n")
        return True
    # For OpenSSL on Windows, re-run the full check which sets env vars correctly
    if prereq_name == "openssl" and sys.platform == "win32":
        if _ensure_openssl_env():
            lib_dir = os.environ.get("OPENSSL_LIB_DIR", "")
            w(f"  {_C_GREEN}  {label} configured (LIB_DIR={lib_dir}){_RST}\n")
            return True
    w(f"  {_C_RED}  {label} not found after install — restart terminal and retry{_RST}\n")
    return False


# ── Install recipes ─────────────────────────────────────────
# Each recipe: (display, check_fn, cmds_fn, provides, time_est, path_adds, requires)
# cmds_fn: callable returning platform-appropriate command list
# requires: prerequisite name from _PREREQ_INSTALLERS (or None)


def _foundry_cmds():
    if _has_bash():
        return ['curl -L https://foundry.paradigm.xyz | bash',
                'export PATH="$HOME/.foundry/bin:$PATH" && foundryup']
    if sys.platform == "win32":
        # PowerShell variant for Windows without bash
        return ['powershell -Command "irm https://foundry.paradigm.xyz | iex"',
                'foundryup']
    return ['curl -L https://foundry.paradigm.xyz | bash',
            'export PATH="$HOME/.foundry/bin:$PATH" && foundryup']


def _solana_cmds():
    if sys.platform == "win32":
        script = os.path.join(PLAMEN_HOME,
                              "_solana_installer.py").replace('\\', '/')
        return [f'python "{script}"']
    return ['sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"']


def _anchor_cmds():
    if sys.platform == "win32":
        # Download prebuilt AVM from GitHub, then use AVM to install Anchor
        # AVM downloads prebuilt anchor binaries since v0.31.0
        script = os.path.join(PLAMEN_HOME,
                              "_avm_installer.py").replace('\\', '/')
        return [f'python "{script}"',
                'avm install latest',
                'avm use latest']
    return ['cargo install --git https://github.com/coral-xyz/anchor avm --force',
            'export PATH="$HOME/.avm/bin:$PATH" && avm install latest && avm use latest']


def _aptos_cmds():
    if sys.platform == "darwin" and _has_brew():
        return ['brew install aptos']
    # Python installer works on all platforms (Windows, Linux, macOS)
    py = _python_bin()
    return [f'{py} -c "'
            'import urllib.request,tempfile,os,subprocess;'
            "p=os.path.join(tempfile.gettempdir(),'install_aptos.py');"
            "urllib.request.urlretrieve('https://aptos.dev/scripts/install_cli.py',p);"
            f"subprocess.run(['{py}',p])"
            '"']


def _sui_cmds():
    # suiup is the official Sui version manager (prebuilt binary, fast)
    if sys.platform == "win32":
        # Pure Python: download zip from GitHub releases, extract, place in ~/.local/bin
        # No bash/sh needed — works in cmd.exe and PowerShell
        script = os.path.join(PLAMEN_HOME,
                              "_sui_installer.py").replace('\\', '/')
        return [f'python "{script}"',
                'echo y | suiup install sui@testnet',
                'suiup default set sui']
    # Unix/macOS: bash script works natively
    return ['curl -fsSL https://raw.githubusercontent.com/MystenLabs/suiup/main/install.sh | sh',
            'echo y | suiup install sui@testnet',
            'suiup default set sui']


def _stellar_cmds():
    if sys.platform == "win32":
        return ['winget install --id Stellar.StellarCLI --accept-source-agreements --accept-package-agreements']
    if sys.platform == "darwin" and _has_brew():
        return ['brew install stellar-cli']
    return ['cargo install --locked stellar-cli']


def _scout_soroban_cmds():
    return ['cargo install cargo-scout-audit']


# Same binary as Soroban's scout (cargo-scout-audit). Listed separately so
# Solana-only installers still get the static analyzer even when they skip
# the Soroban group, and so the post-install report can attribute it to
# the right chain.
def _scout_solana_cmds():
    return ['cargo install cargo-scout-audit']


def _scip_go_cmds():
    # Repo was moved from sourcegraph/scip-go to scip-code/scip-go. The
    # original module path now errors with `module declares its path as: ...`.
    return ['go install github.com/scip-code/scip-go/cmd/scip-go@latest']


def _opengrep_cmds():
    if _has_bash():
        return ['curl -fsSL https://raw.githubusercontent.com/opengrep/opengrep/main/install.sh | bash']
    # fallback: pip-installable semgrep works on all platforms (opengrep-compatible)
    return [' '.join(_pip_install_args()) + ' semgrep']


def _rust_analyzer_cmds():
    """Install rust-analyzer for L1 (Rust) SCIP indexing.

    Two paths:
      * rustup-managed toolchain — `rustup component add rust-analyzer`
      * Homebrew Rust (`brew install rust`) — no rustup multiplexer, so
        the `rustup component add` command fails. Fall back to
        `brew install rust-analyzer`, which ships a standalone binary.
      * Otherwise: try cargo as a generic fallback.
    """
    if shutil.which("rustup"):
        return ['rustup component add rust-analyzer']
    if sys.platform == "darwin" and _has_brew():
        return ['brew install rust-analyzer']
    # No rustup, no brew — cargo install is the last resort (slow but works).
    return ['cargo install rust-analyzer']


def _ast_grep_cmds():
    """Install ast-grep. Prefer cargo (works on all platforms) with brew
    fallback on macOS for a faster binary install."""
    if sys.platform == "darwin" and _has_brew():
        return ['brew install ast-grep']
    if sys.platform != "win32" and _has_bash():
        return ['curl -fsSL https://raw.githubusercontent.com/ast-grep/ast-grep/main/install.sh | bash || cargo install ast-grep --locked']
    return ['cargo install ast-grep --locked']


_INSTALL_RECIPES = {
    "EVM": [
        ("Foundry (forge+anvil+cast)",
         lambda: _find_bin("forge", _FOUNDRY_PATHS),
         _foundry_cmds,
         ["forge", "anvil", "cast"], "~30s",
         ["~/.foundry/bin"], None),

        ("slither",
         lambda: _find_bin("slither") or _find_bin("slither-mcp"),
         lambda: [' '.join(_pip_install_args()) + ' slither-analyzer'],
         ["slither"], "~15s", [], None),

        ("medusa",
         lambda: _find_bin("medusa", ["~/go/bin"]),
         lambda: ['go install github.com/crytic/medusa@latest'],
         ["medusa"], "~60s",
         ["~/go/bin"], "go"),
    ],

    "Solana": [
        ("Solana CLI",
         lambda: _find_bin("solana", _SOLANA_PATHS),
         _solana_cmds,
         ["solana", "cargo-build-sbf"], "~30s",
         ["~/.local/share/solana/install/active_release/bin"], None),

        ("Anchor (via AVM)",
         lambda: _find_bin("anchor", _AVM_PATHS),
         _anchor_cmds,
         ["anchor"], "~3-5 min",
         ["~/.avm/bin"], ["rust", "openssl"] if sys.platform == "win32" else "rust"),

        ("Trident fuzzer",
         lambda: _find_bin("trident"),
         lambda: ['cargo install trident-cli'],
         ["trident"], "~2-3 min",
         [], ["rust", "openssl"] if sys.platform == "win32" else "rust"),

        ("Scout (Anchor + native Solana static analyzer)",
         lambda: _find_bin("cargo-scout-audit", _CARGO_PATHS),
         _scout_solana_cmds,
         ["cargo-scout-audit"], "~2-3 min",
         ["~/.cargo/bin"], "rust"),
    ],

    "Move": [
        ("Aptos CLI",
         lambda: _find_bin("aptos", ["~/.aptoscli/bin"]),
         _aptos_cmds,
         ["aptos"], "~30s", ["~/.aptoscli/bin"], None),

        # Reuses the same binary as `L1 (ast-grep)`. Listed here so the
        # SC-only installer (Move audits without L1) still picks it up for
        # structural pattern matching on .move files.
        ("ast-grep (structural pattern matching for Move)",
         lambda: bool(_find_bin("ast-grep", _CARGO_PATHS)
                      or _find_bin("sg", _CARGO_PATHS)
                      or (sys.platform == "darwin" and _find_bin("ast-grep", ["/opt/homebrew/bin", "/usr/local/bin"]))),
         _ast_grep_cmds,
         ["ast-grep"], "~30s",
         ["~/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin"],
         "rust" if not (sys.platform == "darwin" and _has_brew()) else None),

        ("Sui CLI",
         lambda: _find_bin("sui", ["~/AppData/Local/bin", "~/.local/bin"]),
         _sui_cmds,
         ["sui"], "~1-2 min",
         ["~/AppData/Local/bin", "~/.local/bin"], None),
    ],

    "Soroban": [
        ("Stellar CLI",
         lambda: _find_bin("stellar", _CARGO_PATHS +
                           ["C:/Program Files (x86)/Stellar CLI",
                            "C:/Program Files/Stellar CLI"]),
         _stellar_cmds,
         ["stellar"], "~2-3 min",
         ["~/.cargo/bin", "C:/Program Files/Stellar CLI", "C:/Program Files (x86)/Stellar CLI"], "rust" if sys.platform != "win32" else None),

        ("Scout (Soroban static analyzer)",
         lambda: _find_bin("cargo-scout-audit", _CARGO_PATHS),
         _scout_soroban_cmds,
         ["cargo-scout-audit"], "~2-3 min",
         ["~/.cargo/bin"], "rust"),
    ],

    "L1 (Go)": [
        ("scip-go (SCIP semantic index)",
         lambda: _find_bin("scip-go", _GO_PATHS),
         _scip_go_cmds,
         ["scip-go"], "~30s",
         ["~/go/bin"], "go"),

        ("opengrep / semgrep (static analysis)",
         lambda: _find_bin("opengrep", ["~/.local/bin"]) or _find_bin("semgrep"),
         _opengrep_cmds,
         ["opengrep", "semgrep"], "~30s",
         ["~/.local/bin"], None),
    ],

    "L1 (Rust)": [
        ("rust-analyzer (SCIP semantic index)",
         lambda: bool(_find_bin("rust-analyzer", _CARGO_PATHS)
                      or (sys.platform == "darwin" and _find_bin("rust-analyzer", ["/opt/homebrew/bin", "/usr/local/bin"]))),
         _rust_analyzer_cmds,
         ["rust-analyzer"], "~15s",
         ["~/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin"],
         # Only require the `rust` (rustup) prereq when we'd use rustup.
         # Brew-Rust users get rust-analyzer via `brew install rust-analyzer`.
         "rust" if not (sys.platform == "darwin" and _has_brew()) else None),

        ("opengrep / semgrep (static analysis)",
         lambda: _find_bin("opengrep", ["~/.local/bin"]) or _find_bin("semgrep"),
         _opengrep_cmds,
         ["opengrep", "semgrep"], "~30s",
         ["~/.local/bin"], None),
    ],

    "L1 (ast-grep)": [
        ("ast-grep (structural pattern matching)",
         lambda: bool(_find_bin("ast-grep", _CARGO_PATHS)
                      or _find_bin("sg", _CARGO_PATHS)
                      or (sys.platform == "darwin" and _find_bin("ast-grep", ["/opt/homebrew/bin", "/usr/local/bin"]))),
         _ast_grep_cmds,
         ["ast-grep"], "~30s",
         ["~/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin"],
         # cargo path needs rustup; brew path doesn't.
         "rust" if not (sys.platform == "darwin" and _has_brew()) else None),
    ],
}


def _needs_bash(cmd: str) -> bool:
    """Check if a command requires bash (uses shell features not available in cmd.exe)."""
    bash_indicators = ('curl ', 'sh -c', 'sh "', 'sh /', 'export ',
                       '$(', '| bash', '| sh', '||', '&&', '$HOME', '$PATH')
    return any(ind in cmd for ind in bash_indicators)


def _run_install_cmd(cmd: str, retries: int = 1, timeout: int = None) -> bool:
    """Run a single install command with visible output. Returns True on success.

    Args:
        timeout: Max seconds per attempt. None = no limit. On timeout, the subprocess
                 is killed and the attempt counts as a failure.
    """
    w = sys.stdout.write
    w(f"  {_C_GRAY}$ {cmd}{_RST}\n")
    sys.stdout.flush()

    # Pick shell per command: bash for shell-scripting, native shell for simple commands.
    # On Windows, Git Bash mangles paths with spaces (C:\Program Files → broken),
    # so simple commands like "go install" or "cargo install" use cmd.exe.
    bash = shutil.which("bash")
    if _needs_bash(cmd) and bash:
        run_kwargs = {"shell": True, "executable": bash}
    else:
        run_kwargs = {"shell": True}

    for attempt in range(1 + retries):
        try:
            result = subprocess.run(cmd, timeout=timeout, **run_kwargs)
            if result.returncode == 0:
                return True
            # winget exit codes that mean the tool is already present:
            # 0x8A15002B (-1978335189) = UPDATE_NOT_APPLICABLE (no newer version)
            # 0x8A150061 (-1978335135) = PACKAGE_ALREADY_INSTALLED
            if "winget" in cmd and result.returncode in (-1978335189, -1978335135):
                return True
        except subprocess.TimeoutExpired:
            w(f"  {_C_RED}  timed out after {timeout}s{_RST}\n")
            sys.stdout.flush()
        if attempt < retries:
            w(f"  {_C_ORANGE}  retry {attempt + 1}/{retries}...{_RST}\n")
            sys.stdout.flush()
    return False


# Per-binary version probes. Used after install to confirm the binary not
# only exists on disk but actually runs. The flag is the lightest invocation
# that produces output and exits zero on a healthy install. None means
# "skip the probe" — used for tools that have no version flag, only show
# a TTY-clearing banner, or whose CLI is too slow to reasonably probe.
#
# This is STRICTLY informational. A failed probe never blocks install
# completion, never short-circuits other tools, never raises. The worst
# possible outcome is a yellow "installed but couldn't verify" line in
# the post-install report.
_VERSION_PROBES = {
    "forge":              "--version",
    "cast":               "--version",
    "anvil":              "--version",
    "medusa":             "--version",
    "slither":            "--version",
    "solana":             "--version",
    "anchor":             "--version",
    "cargo-build-sbf":    "--version",
    "trident":            "--version",
    # Cargo plugin: direct binary has no --version, only `help` and the
    # `scout-audit` subcommand. `--help` exits zero and prints the usage
    # block, which is enough to confirm the binary runs.
    "cargo-scout-audit":  "--help",
    "aptos":              "--version",
    "sui":                "--version",
    "ast-grep":           "--version",
    "stellar":            "--version",
    "scip-go":            "--version",
    "rust-analyzer":      "--version",
    "opengrep":           "--version",
    "semgrep":            "--version",
    "go":                 "version",  # subcommand, not flag
    "cargo":              "--version",
}


def _probe_tool_runtime(binary_name: str, search_paths: list = None) -> tuple[bool, str]:
    """Return (ok, message) for a post-install runtime probe.

    ok=True   → binary runs and version flag exits zero (with output).
    ok=False  → binary missing, or runs but fails. Caller decides what to do.

    A NotFound / timeout / OSError is captured and returned as ok=False with
    a short message. This function NEVER raises and NEVER mutates state.
    """
    flag = _VERSION_PROBES.get(binary_name)
    if flag is None:
        return True, "skipped"  # no probe configured == not a failure
    path = _find_bin(binary_name, search_paths or [])
    if not path:
        return False, "not on PATH"
    try:
        # 5s ceiling. cargo-installed binaries cold-start in 200-800ms.
        # Anything past 5s is a hung process and we treat it as broken.
        result = subprocess.run(
            [path, flag],
            timeout=5,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and (result.stdout.strip() or result.stderr.strip()):
            return True, "ok"
        return False, f"exited {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, "hung (>5s)"
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return False, f"could not exec ({exc.__class__.__name__})"


def _report_toolchain_visibility(w):
    """Cross-OS report after install: which chain toolchains are visible.

    Background: `plamen install` is the non-interactive install. It
    does NOT install per-chain toolchains (Foundry, Solana CLI, Anchor,
    Aptos, Sui, etc.) — that's `plamen setup`. But users often:
      - install `plamen` non-interactively (Claude Code Bash, CI, docs)
      - skip `plamen setup` ("I'll do it later")
      - launch an audit
      - watch the fuzz phases report COMPILATION_FAILED because
        forge / cargo / sui isn't on PATH for the audit subprocess

    The fast-fail isn't a bug — `phase4b-required-artifacts.md`
    explicitly accepts COMPILATION_FAILED as a present-artifact value.
    But silently degrading the EVM fuzz campaign on a user's first
    Thorough run is bad UX.

    Fix: surface a one-screen report at install time showing exactly
    which chain pipelines will be fully functional and which will
    degrade. The user sees the truth before they spend $30 on a
    Thorough run that won't fuzz.
    """
    # (label, binary_name, install_cmd_hint, used_by)
    toolchains = [
        ("Foundry (forge/cast/anvil)", "forge", "plamen setup → EVM, or `curl -L https://foundry.paradigm.xyz | bash && foundryup`", "EVM invariant + Medusa fuzz, Slither integration"),
        ("Medusa",                     "medusa", "plamen setup → EVM, or `go install github.com/crytic/medusa@latest`", "EVM Medusa stateful fuzz"),
        ("Slither",                    "slither", "plamen setup → EVM, or `pip install slither-analyzer`", "EVM static analysis"),
        ("Solana CLI",                 "solana", "plamen setup → Solana", "Solana / Anchor audits"),
        ("Anchor",                     "anchor", "plamen setup → Solana → Anchor", "Solana program audits"),
        ("Aptos CLI",                  "aptos",  "plamen setup → Move", "Aptos Move audits"),
        ("Sui CLI",                    "sui",    "plamen setup → Move", "Sui Move audits"),
        ("Stellar CLI",                "stellar","plamen setup → Soroban", "Soroban audits"),
        ("Go (scip-go, medusa)",       "go",     "plamen setup → installs Go, or system package manager", "L1 mode + Medusa"),
        ("Rust (cargo)",               "cargo",  "plamen setup → installs Rust, or `curl https://sh.rustup.rs -sSf | sh`", "L1 mode + Soroban + Solana"),
    ]
    # Use the same search paths as check_dependencies() so we don't get
    # a different answer here than `plamen doctor` would give.
    search_paths = {
        "forge": _FOUNDRY_PATHS,
        "medusa": ["~/go/bin"],
        "solana": _SOLANA_PATHS,
        "anchor": _AVM_PATHS,
        "aptos": ["~/.aptoscli/bin"],
        "sui": ["~/AppData/Local/bin", "~/.local/bin"],
        "stellar": _CARGO_PATHS + ["C:/Program Files/Stellar CLI", "C:/Program Files (x86)/Stellar CLI"],
        "go": _GO_PATHS,
        "cargo": _CARGO_PATHS,
    }
    found, missing = [], []
    for label, bin_name, install_hint, used_by in toolchains:
        paths = search_paths.get(bin_name, [])
        if _find_bin(bin_name, paths) or _find_bin(bin_name + ".exe", paths):
            found.append((label, bin_name))
        else:
            missing.append((label, bin_name, install_hint, used_by))

    console.print(Rule(title="Toolchain Visibility (audit-subprocess view)", style="color(238)"))
    if not missing:
        w(f"  {_C_GREEN}All chain toolchains visible.{_RST} Every audit mode will run end-to-end.\n\n")
        return
    if found:
        w(f"  {_C_GREEN}Detected:{_RST} {', '.join(label for label, _ in found)}\n")
    w(f"  {_C_ORANGE}Not detected ({len(missing)}):{_RST}\n")
    for label, bin_name, install_hint, used_by in missing:
        w(f"    {_C_ORANGE}!{_RST} {_C_WHITE}{label}{_RST}\n")
        w(f"      {_C_GRAY}needed for: {used_by}{_RST}\n")
        w(f"      {_C_GRAY}install:    {install_hint}{_RST}\n")
    w(f"\n")
    w(f"  {_C_GRAY}Audits will run, but phases that depend on missing tools{_RST}\n")
    w(f"  {_C_GRAY}will report `COMPILATION_FAILED` / `<TOOL>_UNAVAILABLE`{_RST}\n")
    w(f"  {_C_GRAY}artifacts (accepted by the gate, but reduced coverage).{_RST}\n")
    w(f"  {_C_GRAY}Run `plamen setup` from a real terminal to install missing{_RST}\n")
    w(f"  {_C_GRAY}toolchains interactively.{_RST}\n")
    if sys.platform != "win32":
        w(f"\n")
        w(f"  {_C_GRAY}macOS/Linux PATH note: if you installed a toolchain manually{_RST}\n")
        w(f"  {_C_GRAY}(e.g. via `foundryup`), its installer may have only written{_RST}\n")
        w(f"  {_C_GRAY}`export PATH=...` to .bashrc / .zshrc. Codex / Claude Code{_RST}\n")
        w(f"  {_C_GRAY}subprocesses launched from a parent shell that didn't source{_RST}\n")
        w(f"  {_C_GRAY}those files will not see the toolchain. Add the export to{_RST}\n")
        w(f"  {_C_GRAY}~/.profile (sourced by login shells) instead, OR start the{_RST}\n")
        w(f"  {_C_GRAY}backend CLI from a terminal that DOES source the rc file.{_RST}\n")
    w(f"\n")


def _update_path_env(new_paths: list, persist: bool = False):
    """Add directories to the current process PATH (for post-install detection and subprocesses).

    If persist=True and on Windows, also adds to the user's persistent PATH via setx
    so future terminal sessions find the tools without manual configuration.

    Persistence is decoupled from the current-PATH check: a directory that's
    already in the running process PATH (e.g. inherited from `.bashrc`) may
    still be MISSING from the Windows User PATH that Codex / Claude Code
    subprocesses inherit at spawn time. We persist to the registry regardless
    of whether the running process already has it. `_persist_path_windows`
    is itself idempotent, so this is safe.
    """
    current = os.environ.get("PATH", "")
    for p in new_paths:
        expanded = os.path.normpath(os.path.expanduser(p))
        if not os.path.isdir(expanded):
            continue
        if expanded not in current:
            os.environ["PATH"] = expanded + os.pathsep + os.environ.get("PATH", "")
            current = os.environ["PATH"]
        # Persist to Windows user PATH so future terminals AND subprocesses
        # spawned by external CLI runtimes (codex exec, claude -p) find the
        # tool. Done unconditionally because the in-process PATH check above
        # cannot detect a gap between the running shell's PATH and the
        # persistent Windows User PATH.
        if persist and sys.platform == "win32":
            _persist_path_windows(expanded)


def _persist_path_windows(directory: str):
    """Add a directory to the Windows user PATH permanently via registry."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment",
                            0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                user_path, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                user_path = ""
            # Check if already there (case-insensitive on Windows)
            entries = [e.strip() for e in user_path.split(os.pathsep) if e.strip()]
            if not any(os.path.normcase(e) == os.path.normcase(directory) for e in entries):
                entries.append(directory)
                new_path = os.pathsep.join(entries)
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
    except Exception:
        pass  # non-critical — user can add manually


def _refresh_system_path():
    """On Windows, reload PATH from the registry so winget installs are visible."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        sys_path = ""
        usr_path = ""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
                sys_path = winreg.QueryValueEx(key, "Path")[0]
        except (FileNotFoundError, OSError):
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                usr_path = winreg.QueryValueEx(key, "Path")[0]
        except (FileNotFoundError, OSError):
            pass
        fresh = sys_path + os.pathsep + usr_path
        for entry in fresh.split(os.pathsep):
            entry = entry.strip()
            if entry and entry not in os.environ.get("PATH", ""):
                os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + entry
    except Exception:
        pass  # non-critical — user can restart terminal


def _rag_needs_build() -> bool:
    """Check if the RAG database needs building or is incomplete from a crashed build."""
    return _probe_rag_db() < _RAG_MIN_ENTRIES


# ── RAG thermal/resource detection ────────────────────────

def _get_total_ram_gb() -> float:
    """Get total physical RAM in GB. Returns 0 on failure."""
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["sysctl", "-n", "hw.memsize"],
                               capture_output=True, text=True, timeout=3)
            return int(r.stdout.strip()) / (1024 ** 3)
        elif sys.platform == "linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
        elif sys.platform == "win32":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
    except Exception:
        pass
    return 0


def _is_fanless_mac() -> bool:
    """Detect fanless Macs (MacBook Air, etc.) that throttle under sustained ML load."""
    if sys.platform != "darwin":
        return False
    # Primary: IORegistry contains the hardware model (e.g. "MacBookAir15,1")
    try:
        r = subprocess.run(
            ["ioreg", "-c", "IOPlatformExpertDevice", "-d", "2"],
            capture_output=True, text=True, timeout=3)
        if "macbookair" in r.stdout.lower():
            return True
    except Exception:
        pass
    # Fallback: default macOS hostnames contain the model name
    try:
        import socket
        hostname = socket.gethostname().lower().replace(" ", "-")
        if "macbook-air" in hostname or "macbookair" in hostname:
            return True
    except Exception:
        pass
    return False


def _build_rag_db(w):
    """Run the RAG indexer pipeline. Returns True on success."""
    vuln_db_dir = os.path.join(PLAMEN_HOME, "custom-mcp", "unified-vuln-db")
    if not os.path.isdir(vuln_db_dir):
        w(f"  {_C_RED}unified-vuln-db not found at {vuln_db_dir}{_RST}\n")
        return False

    # Ensure RAG dependencies are installed before attempting to run the indexer.
    # This makes `plamen rag` self-healing: if deps are missing (e.g. after a fresh clone
    # or a failed previous install) it installs them here before proceeding.
    try:
        import chromadb, sentence_transformers  # noqa: F401
    except ImportError:
        w(f"  {_C_ORANGE}RAG dependencies not installed — installing now...{_RST}\n\n")
        sys.stdout.flush()
        if not _setup_python_deps(w):
            w(f"  {_C_RED}Dependency installation failed. "
              f"Run 'plamen setup' to retry.{_RST}\n")
            return False

    py = _python_bin()

    # Wipe existing ChromaDB — rebuild means fresh start.
    # NOTE: database.py resolves DATA_DIR via Path(__file__).parents[3] / "unified-vuln-db" / "data",
    # which puts chroma_db at PLAMEN_HOME/unified-vuln-db/data/chroma_db — NOT under custom-mcp/.
    chroma_dir = os.path.join(PLAMEN_HOME, "unified-vuln-db", "data", "chroma_db")
    if os.path.isdir(chroma_dir):
        import shutil as _shutil
        _shutil.rmtree(chroma_dir, ignore_errors=True)
        w(f"  {_C_GRAY}Cleared stale RAG database for clean rebuild{_RST}\n")

    # Check for Solodit API key — needed for the largest data source.
    # The key must be available in the environment when this process runs.
    # Recommended: add SOLODIT_API_KEY to ~/.claude/settings.json "env" section
    # so it is always available to plamen and audit agents alike.
    if not os.environ.get("SOLODIT_API_KEY", "").strip():
        w(f"  {_C_ORANGE}Note: SOLODIT_API_KEY not set — Solodit indexing will be skipped{_RST}\n")
        w(f"  {_C_GRAY}Get a free key at https://solodit.cyfrin.io{_RST}\n")
        w(f"  {_C_GRAY}Add to ~/.claude/settings.json → \"env\": {{\"SOLODIT_API_KEY\": \"your_key\"}}{_RST}\n\n")

    # Adaptive timeouts: fanless Macs (MacBook Air) thermal-throttle under sustained ML
    # load, so give them more time and fewer Solodit pages to stay within timeout.
    fanless = _is_fanless_mac()
    solodit_timeout  = 1800 if fanless else 1200  # 30 min / 20 min
    indexing_timeout =  900 if fanless else  600  # 15 min / 10 min
    max_pages        =    5 if fanless else   10  # 29 tags × pages × 3.5s delay

    # On macOS/Linux, run the indexer at reduced CPU priority so it doesn't
    # hog the machine. nice -n 10 yields CPU to other apps with ~10-20% throughput
    # cost on an otherwise idle machine. Skipped on Windows (no nice command).
    nice = "nice -n 10 " if sys.platform != "win32" else ""

    steps = [
        # (label, est, cmd, retry_cmd, timeout)
        # Solodit: no retry — a hanging API call won't improve on retry
        ("Solodit — live API",
         f"~{'20' if fanless else '10'} min",
         f'cd "{vuln_db_dir}" && {nice}{py} -m unified_vuln.indexer index -s solodit --max-pages {max_pages}',
         None,
         solodit_timeout),
        # DeFiHackLabs: local parsing + embedding; retry with same command is safe
        ("DeFiHackLabs — local",
         "~1 min",
         f'cd "{vuln_db_dir}" && {nice}{py} -m unified_vuln.indexer index -s defihacklabs',
         f'cd "{vuln_db_dir}" && {nice}{py} -m unified_vuln.indexer index -s defihacklabs',
         indexing_timeout),
        # Immunefi: first attempt fetches 139 URLs + embeds; retry skips the HTTP fetch
        # phase (uses cached immunefi_fetched.json) and goes straight to embedding
        ("Immunefi — writeups",
         "~2 min",
         f'cd "{vuln_db_dir}" && {nice}{py} -m unified_vuln.indexer index -s immunefi',
         f'cd "{vuln_db_dir}" && {nice}{py} -m unified_vuln.indexer index -s immunefi --skip-fetch',
         indexing_timeout),
        # Immunefi Competitions: 879 findings from 25 audit competitions via GitHub raw URLs.
        # No token needed (~50 API calls for directory listing, content via raw.githubusercontent.com).
        # Retry uses cached markdown files (skip-fetch).
        ("Immunefi — competitions",
         "~3 min",
         f'cd "{vuln_db_dir}" && {nice}{py} -m unified_vuln.indexer index -s immunefi-competitions',
         f'cd "{vuln_db_dir}" && {nice}{py} -m unified_vuln.indexer index -s immunefi-competitions --skip-fetch',
         indexing_timeout),
    ]

    # Warn the user before the heavy CPU/RAM work begins.
    w(f"  {_C_ORANGE}{_BOLD}NOTE:{_RST} {_C_ORANGE}RAG indexing is CPU and RAM intensive.{_RST}\n")
    w(f"  {_C_GRAY}Your machine may feel sluggish for several minutes — this is normal.{_RST}\n")
    w(f"  {_C_GRAY}Do not close this terminal or press Ctrl+C during indexing.{_RST}\n\n")

    for label, est, cmd, retry_cmd, timeout in steps:
        w(f"  {_C_ORANGE}>{_RST} {_C_WHITE}{label}{_RST}"
          f"  {_C_DARK_GRAY}{est}{_RST}\n")
        sys.stdout.flush()
        ok = _run_install_cmd(cmd, retries=0, timeout=timeout)
        if not ok and retry_cmd:
            w(f"  {_C_ORANGE}  retry 1/1 (cached)...{_RST}\n")
            sys.stdout.flush()
            ok = _run_install_cmd(retry_cmd, retries=0, timeout=timeout)
        if ok:
            w(f"  {_C_GREEN}  done{_RST}\n")
        else:
            w(f"  {_C_RED}  failed — continuing with partial data{_RST}\n")
        w("\n")

    count = _probe_rag_db()
    if count > 0:
        w(f"  {_C_GREEN}RAG database: {count:,} entries indexed{_RST}\n\n")
        return True
    return False


def _quick_check_required() -> bool:
    """Silent check for required tools. Returns True if all present."""
    if not _detect_cli_backends():
        return False
    for name in ("python", "npx", "npm", "git"):
        if name == "python":
            if not (_find_bin("python", _python_extra_paths()) or _find_bin("python3")):
                return False
        elif not _find_bin(name):
            return False
    return True


def _setup_python_deps(w):
    """Install all Python dependencies if missing. Returns True if all installed."""
    base = PLAMEN_HOME
    py = _python_bin()
    req_files = [
        ("Plamen wrapper", "requirements.txt"),
        # unified-vuln-db handles all RAG indexing (solodit, defihacklabs, immunefi writeups, immunefi competitions)
        # via its own HTTP fetching code — no separate solodit-scraper or
        # defihacklabs-rag packages needed. Those legacy packages are not MCP servers
        # and not called by the current pipeline.
        ("unified-vuln-db", "custom-mcp/unified-vuln-db/requirements.txt"),
        ("farofino-mcp", "custom-mcp/farofino-mcp/requirements.txt"),
    ]
    # (label, path, critical_for): critical_for is a human-readable note
    # describing what the user loses if this fails. "non-critical" stays
    # silent; anything else is surfaced loud after the install loop.
    editable_pkgs = [
        ("unified-vuln-db", "custom-mcp/unified-vuln-db", "non-critical"),
        ("solana-fender", "custom-mcp/solana-fender", "Solana static analysis (Fender)"),
        ("slither-mcp (EVM)", "custom-mcp/slither-mcp", "EVM static analysis (Slither)"),
    ]

    # Check if core deps already installed
    w(f"  {_C_DARK_GRAY}Checking core packages...{_RST}")
    sys.stdout.flush()
    try:
        import rich, InquirerPy  # noqa: F401
        core_ok = True
    except ImportError:
        core_ok = False
    w(f"\r  {_C_GREEN}✓{_RST} Core packages {'found' if core_ok else 'missing'}            \n")
    sys.stdout.flush()

    if core_ok:
        # Quick-check: try importing the core RAG deps (what the indexer actually needs).
        # Deliberately avoid `import torch` here — torch cold-start takes 2-3s and would
        # make every `plamen setup` feel sluggish even when deps are already installed.
        w(f"  {_C_DARK_GRAY}Checking RAG packages (may take a few seconds)...{_RST}")
        sys.stdout.flush()
        try:
            import sentence_transformers, chromadb  # noqa: F401
            deep_ok = True
        except ImportError:
            deep_ok = False
        w(f"\r  {_C_GREEN}✓{_RST} RAG packages {'found' if deep_ok else 'missing'}                                        \n")
        sys.stdout.flush()
    else:
        deep_ok = False

    if core_ok and deep_ok:
        w(f"  {_C_GREEN}Python dependencies already installed{_RST}\n\n")
        return True

    w(f"  {_C_ORANGE}>{_RST} {_C_WHITE}Installing Python dependencies...{_RST}"
      f"  {_C_DARK_GRAY}~2-5 min (PyTorch is ~2GB){_RST}\n\n")
    sys.stdout.flush()

    # Build pip flags that work on PEP 668 systems (macOS Homebrew, Ubuntu 23.04+).
    # _pip_install_args() = [sys.executable, "-m", "pip", "install", <flags...>]
    # Skip indices 0-3 (the base command) to get only the flags: --user, --break-system-packages
    pip_flags = " ".join(a for a in _pip_install_args()[4:])  # skip "python -m pip install"
    pip_base = f'{py} -m pip install {pip_flags}'.rstrip()

    all_ok = True
    for label, req in req_files:
        path = os.path.join(base, req)
        if not os.path.isfile(path):
            w(f"  {_C_DARK_GRAY}  skipping {label} — {req} not found{_RST}\n")
            continue
        w(f"  {_C_ORANGE}>{_RST} {label}\n")
        sys.stdout.flush()
        if not _run_install_cmd(f'{pip_base} -r "{path}"', retries=1):
            w(f"  {_C_RED}  failed{_RST}\n")
            all_ok = False
        else:
            w(f"  {_C_GREEN}  done{_RST}\n")

    critical_failures = []   # list of (label, reason)
    for label, pkg, critical_for in editable_pkgs:
        path = os.path.join(base, pkg)
        if not os.path.isdir(path):
            w(f"  {_C_DARK_GRAY}  skipping {label} — not found{_RST}\n")
            continue
        # Detect unpopulated git submodule: dir exists but contains neither
        # setup.py nor pyproject.toml. Without this check pip errors with
        # "neither setup.py nor pyproject.toml found" and the install
        # masks the failure as "non-critical". Caller needs to know.
        has_setup = (
            os.path.isfile(os.path.join(path, "setup.py"))
            or os.path.isfile(os.path.join(path, "pyproject.toml"))
        )
        if not has_setup:
            w(f"  {_C_RED}  skipping {label} — empty submodule "
              f"({pkg}/ has no setup.py or pyproject.toml){_RST}\n")
            w(f"    {_C_GRAY}Submodule not initialized. Run inside ~/.plamen:{_RST}\n")
            w(f"    {_C_GRAY}  git submodule update --init --recursive{_RST}\n")
            if critical_for != "non-critical":
                critical_failures.append((label, f"empty submodule — {critical_for} unavailable"))
            continue
        w(f"  {_C_ORANGE}>{_RST} {label}\n")
        sys.stdout.flush()
        if not _run_install_cmd(f'{pip_base} -e "{path}"', retries=1):
            if critical_for == "non-critical":
                w(f"  {_C_DARK_GRAY}  failed (non-critical){_RST}\n")
            else:
                w(f"  {_C_RED}  failed — {critical_for} will be unavailable{_RST}\n")
                critical_failures.append((label, f"pip install failed — {critical_for} unavailable"))
                all_ok = False
        else:
            w(f"  {_C_GREEN}  done{_RST}\n")

    if critical_failures:
        w(f"\n  {_C_RED}{len(critical_failures)} critical Python dep(s) failed to install:{_RST}\n")
        for label, reason in critical_failures:
            w(f"    {_C_RED}• {label}{_RST}  {_C_GRAY}{reason}{_RST}\n")
        w(f"  {_C_GRAY}Resolve and re-run `plamen install` before auditing affected chains.{_RST}\n")

    w("\n")
    return all_ok


def _setup_mcp_packages(w):
    """Install pinned MCP npm packages and update config to use them.

    This only activates when mcp-packages/node_modules already exists (user opted in)
    or when ~/.claude/mcp.json has bare 'npx -y @pkg' without version pins (legacy config).
    New users get correctly pinned npx commands from mcp.json.example and don't need this.

    Target: ~/.claude/mcp.json (the MCP-specific config).
    NEVER writes to ~/.claude.json (global Claude config — not our file to touch).
    """
    mcp_dir = os.path.join(PLAMEN_HOME, "mcp-packages")
    pkg_json = os.path.join(mcp_dir, "package.json")
    update_script = os.path.join(mcp_dir, "update_config.py")
    nm_dir = os.path.join(mcp_dir, "node_modules")

    if not os.path.isfile(pkg_json):
        return  # mcp-packages not part of this install — skip silently

    # Only activate if: (a) node_modules already exists (user previously opted in), or
    # (b) ~/.claude/mcp.json has bare npx -y without version pins (legacy config needing fix)
    has_local_install = os.path.isdir(nm_dir)
    has_legacy_config = False
    mcp_json_path = os.path.join(CLAUDE_HOME, "mcp.json")
    if os.path.isfile(mcp_json_path):
        try:
            with open(mcp_json_path) as f:
                mj = _json.load(f)
            for _name, srv in mj.get("mcpServers", {}).items():
                cmd = str(srv.get("command", ""))
                if "npx" not in cmd:
                    continue
                # Check if any arg is an npm package without @version pin
                for arg in srv.get("args", []):
                    a = str(arg)
                    if a.startswith("-"):
                        continue
                    # Scoped: @scope/name (unpinned) vs @scope/name@ver (pinned)
                    if a.startswith("@") and "/" in a:
                        after_slash = a.split("/", 1)[1]
                        if "@" not in after_slash:
                            has_legacy_config = True
                            break
                    # Unscoped: name (unpinned) vs name@ver (pinned)
                    elif not a.startswith("@") and a != "npx" and "@" not in a:
                        has_legacy_config = True
                        break
                if has_legacy_config:
                    break
        except Exception:
            pass

    if not has_local_install and not has_legacy_config:
        return  # New user with correct config from mcp.json.example — nothing to do

    # Step 1: npm install (only if node_modules missing or package.json newer)
    needs_install = not has_local_install
    if not needs_install:
        try:
            needs_install = os.path.getmtime(pkg_json) > os.path.getmtime(nm_dir)
        except OSError:
            needs_install = True

    if needs_install:
        npm_bin = shutil.which("npm")
        if npm_bin:
            w(f"  {_C_DARK_GRAY}  Installing pinned MCP packages...{_RST}\n")
            sys.stdout.flush()
            r = subprocess.run([npm_bin, "install"], cwd=mcp_dir,
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                w(f"  {_C_GREEN}✓{_RST} MCP packages installed\n")
            else:
                w(f"  {_C_ORANGE}!{_RST} npm install failed: {r.stderr[:200]}\n")
                return
        else:
            w(f"  {_C_ORANGE}!{_RST} npm not found — cannot install MCP packages\n")
            return
    else:
        w(f"  {_C_GREEN}✓{_RST} MCP packages up to date\n")

    # Step 2: Update ~/.claude/mcp.json to use pinned local paths + schema sanitizer
    if os.path.isfile(update_script) and os.path.isdir(nm_dir):
        r = subprocess.run([sys.executable, update_script],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            w(f"  {_C_GREEN}✓{_RST} MCP server config updated (pinned + sanitized)\n")
        else:
            w(f"  {_C_ORANGE}!{_RST} MCP config update failed: {r.stderr[:200]}\n")


def _setup_config_files(w):
    """Merge Plamen's config into Claude Code's ~/.claude/ (additive, non-destructive)."""
    steps = [("settings.json", _merge_settings_json),
             ("mcp.json",      _merge_mcp_json),
             ("CLAUDE.md",     _merge_claude_md),
             ("MCP packages",  _setup_mcp_packages)]
    for i, (label, fn) in enumerate(steps, 1):
        w(f"  {_C_DARK_GRAY}[{i}/{len(steps)}] Merging {label}...{_RST}\n")
        sys.stdout.flush()
        fn(w)
    w("\n")


# ── Symlink install / uninstall ─────────────────────────────

# Files tracked by the installer manifest
_PLAMEN_MANIFEST = ".plamen-manifest.json"
_CLAUDE_MD_START = "<!-- PLAMEN:START — managed by plamen install, do not edit -->"
_CLAUDE_MD_END = "<!-- PLAMEN:END -->"


def _is_junction(path):
    """Check if path is a Windows junction (reparse point). os.path.islink misses these."""
    if sys.platform != "win32" or not os.path.isdir(path):
        return False
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attrs != -1 and bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except Exception:
        return False


def _safe_link(src, dst, w):
    """Create a symlink (or junction on Windows dirs). Back up existing non-link targets."""
    if os.path.islink(dst) or _is_junction(dst):
        # Existing symlink or junction — remove and recreate (idempotent re-install)
        if os.path.isdir(dst) and not os.path.islink(dst):
            os.rmdir(dst)  # junctions are removed with rmdir, not shutil.rmtree
        else:
            os.remove(dst)
    elif os.path.exists(dst):
        backup = dst + ".pre-plamen"
        if not os.path.exists(backup):
            shutil.move(dst, backup)
            w(f"  {_C_GRAY}  backed up {os.path.basename(dst)} → {os.path.basename(backup)}{_RST}\n")
        else:
            w(f"  {_C_ORANGE}  skipped {os.path.basename(dst)} — backup already exists{_RST}\n")
            return False

    try:
        is_dir = os.path.isdir(src)
        if sys.platform == "win32" and is_dir:
            # Junctions don't need admin privileges on Windows.
            # mklink rejects paths with mixed separators (e.g.
            # `C:\Users\plmnt/.claude\agents\skills` from expanduser +
            # os.path.join). Normalise both args so they use the OS
            # native separator before invoking cmd.
            subprocess.run(
                ["cmd", "/c", "mklink", "/J",
                 os.path.normpath(dst), os.path.normpath(src)],
                check=True, capture_output=True,
            )
        else:
            os.symlink(src, dst, target_is_directory=is_dir)
        return True
    except OSError as e:
        w(f"  {_C_RED}  failed to link {os.path.basename(dst)}: {e}{_RST}\n")
        if sys.platform == "win32" and "privilege" in str(e).lower():
            w(f"  {_C_GRAY}  Enable Developer Mode: Settings > System > For Developers{_RST}\n")
        return False


def _clean_dangling_plamen_links(directory, w):
    """Remove symlinks in directory that point into PLAMEN_HOME but whose target no longer exists."""
    if not os.path.isdir(directory):
        return
    cleaned = 0
    for entry in os.listdir(directory):
        path = os.path.join(directory, entry)
        if not os.path.islink(path):
            continue
        target = os.readlink(path)
        if not os.path.isabs(target):
            target = os.path.normpath(os.path.join(directory, target))
        if os.path.normpath(PLAMEN_HOME) in os.path.normpath(target) and not os.path.exists(target):
            os.remove(path)
            cleaned += 1
    if cleaned:
        w(f"  {_C_GRAY}  cleaned {cleaned} dangling symlink(s) in {os.path.basename(directory)}/{_RST}\n")


def _run_symlink_install(w):
    """Create symlinks from Plamen repo into ~/.claude/ for Claude Code discovery."""
    os.makedirs(CLAUDE_HOME, exist_ok=True)
    installed = []

    # 1. Agent definition files (individual — user may have own agents)
    agents_dir = os.path.join(CLAUDE_HOME, "agents")
    os.makedirs(agents_dir, exist_ok=True)
    w(f"  {_C_ORANGE}>{_RST} Linking agent definitions\n")
    for f in sorted(glob.glob(os.path.join(PLAMEN_HOME, "agents", "*.md"))):
        dst = os.path.join(agents_dir, os.path.basename(f))
        if _safe_link(f, dst, w):
            installed.append(dst)
    _clean_dangling_plamen_links(agents_dir, w)

    # 2. Skills directory (Plamen-only)
    skills_src = os.path.join(PLAMEN_HOME, "agents", "skills")
    skills_dst = os.path.join(agents_dir, "skills")
    if os.path.isdir(skills_src):
        w(f"  {_C_ORANGE}>{_RST} Linking skills\n")
        if _safe_link(skills_src, skills_dst, w):
            installed.append(skills_dst)

    # 3. Slash commands (all .md files in commands/)
    commands_dir = os.path.join(CLAUDE_HOME, "commands")
    os.makedirs(commands_dir, exist_ok=True)
    cmd_files = sorted(glob.glob(os.path.join(PLAMEN_HOME, "commands", "*.md")))
    if cmd_files:
        w(f"  {_C_ORANGE}>{_RST} Linking commands ({len(cmd_files)} files)\n")
        for f in cmd_files:
            dst = os.path.join(commands_dir, os.path.basename(f))
            if _safe_link(f, dst, w):
                installed.append(dst)
    _clean_dangling_plamen_links(commands_dir, w)

    # 4. Rule files (individual — user may have own rules)
    rules_dir = os.path.join(CLAUDE_HOME, "rules")
    os.makedirs(rules_dir, exist_ok=True)
    rule_files = sorted(glob.glob(os.path.join(PLAMEN_HOME, "rules", "*.md")) +
                        glob.glob(os.path.join(PLAMEN_HOME, "rules", "*.json")))
    if rule_files:
        w(f"  {_C_ORANGE}>{_RST} Linking rules ({len(rule_files)} files)\n")
        for f in rule_files:
            dst = os.path.join(rules_dir, os.path.basename(f))
            if _safe_link(f, dst, w):
                installed.append(dst)
    _clean_dangling_plamen_links(rules_dir, w)

    # 5. Prompts directory (Plamen-only — per-language prompt trees)
    prompts_src = os.path.join(PLAMEN_HOME, "prompts")
    prompts_dst = os.path.join(CLAUDE_HOME, "prompts")
    if os.path.isdir(prompts_src):
        w(f"  {_C_ORANGE}>{_RST} Linking prompts\n")
        if _safe_link(prompts_src, prompts_dst, w):
            installed.append(prompts_dst)

    # 6. Custom MCP server source (Plamen-only)
    mcp_src = os.path.join(PLAMEN_HOME, "custom-mcp")
    mcp_dst = os.path.join(CLAUDE_HOME, "custom-mcp")
    if os.path.isdir(mcp_src):
        w(f"  {_C_ORANGE}>{_RST} Linking MCP servers\n")
        if _safe_link(mcp_src, mcp_dst, w):
            installed.append(mcp_dst)

    # 7. Scripts directory (driver + modules)
    scripts_src = os.path.join(PLAMEN_HOME, "scripts")
    scripts_dst = os.path.join(CLAUDE_HOME, "scripts")
    if os.path.isdir(scripts_src):
        w(f"  {_C_ORANGE}>{_RST} Linking scripts\n")
        if _safe_link(scripts_src, scripts_dst, w):
            installed.append(scripts_dst)

    # 7c. L1 static analysis modules (SCIP reader, SARIF merge)
    l1_src = os.path.join(PLAMEN_HOME, "plamen_l1")
    l1_dst = os.path.join(CLAUDE_HOME, "plamen_l1")
    if os.path.isdir(l1_src):
        w(f"  {_C_ORANGE}>{_RST} Linking L1 modules\n")
        if _safe_link(l1_src, l1_dst, w):
            installed.append(l1_dst)

    # 8. Utility files
    for fname in ("plamen", "plamen.py", "plamen.sh", "plamen.bat", "VERSION"):
        src = os.path.join(PLAMEN_HOME, fname)
        if os.path.isfile(src):
            dst = os.path.join(CLAUDE_HOME, fname)
            if _safe_link(src, dst, w):
                installed.append(dst)

    # 9. Write install manifest
    import json as _json
    manifest = {
        "plamen_home": PLAMEN_HOME,
        "version": VERSION,
        "installed": installed,
    }
    manifest_path = os.path.join(CLAUDE_HOME, _PLAMEN_MANIFEST)
    with open(manifest_path, "w") as f:
        _json.dump(manifest, f, indent=2)

    w(f"\n  {_C_GREEN}Linked {len(installed)} items into {CLAUDE_HOME}{_RST}\n\n")


def _ensure_python3_shim_windows(w):
    """Windows: make `python3` resolve to the real interpreter.

    The default Python installer creates `python.exe` but not
    `python3.exe` (the latter is a Unix convention). Windows ships a
    Microsoft Store App Execution Alias at
    `%LOCALAPPDATA%\\Microsoft\\WindowsApps\\python3.exe` — a 0-byte
    stub that opens the Store when invoked. Any LLM-spawned shell that
    runs `python3 ...` (recon Bash invocations, the L1 bake script,
    `python3 -m plamen_l1.scip_reader`, etc.) pops the Store mid-audit.

    Plamen's own subprocess.run calls bypass this via `sys.executable`
    in `_python_bin()`. The remaining surface is LLM-typed shell
    commands, which we can't intercept.

    Two-tier fix:
      1. Best path — copy python.exe -> python3.exe in the SAME dir as
         sys.executable. The Python install dir is already on PATH
         (added by the installer) and comes before WindowsApps, so
         `python3` resolves to the real interpreter regardless of which
         shell / how PATH is ordered downstream.
      2. Fallback — if the install dir is read-only (system-wide
         install at `C:\\Program Files\\Python*`), drop a `python3.bat`
         shim in PLAMEN_HOME. This requires PLAMEN_HOME to be on PATH
         before WindowsApps; works for typical user installs that
         followed the README PATH instructions, fails silently if not.

    Idempotent — if python3.exe already exists next to python.exe (some
    newer Python installs include it; users who ran this before),
    no-op. On non-Windows platforms `python3` is real and this
    function is a no-op.
    """
    if sys.platform != "win32":
        return
    py_exe = sys.executable
    if not py_exe or not os.path.isfile(py_exe):
        return
    py_dir = os.path.dirname(py_exe)
    py3_exe = os.path.join(py_dir, "python3.exe")
    if os.path.isfile(py3_exe):
        return  # already present — newer Python or prior plamen install

    # Tier 1: copy python.exe -> python3.exe in the Python install dir.
    # Python's `except ... as e` unbinds the variable after the block, so
    # we capture the exception into a function-scope name to reference it
    # in tier 2's diagnostics. None == tier 1 succeeded.
    tier1_error: Exception | None = None
    try:
        shutil.copy2(py_exe, py3_exe)
        w(f"  {_C_GREEN}>{_RST} Created python3.exe shim at {py3_exe}\n")
        w(f"    {_C_GRAY}prevents `python3` from opening the Microsoft Store{_RST}\n")
        return
    except (OSError, PermissionError) as e:
        tier1_error = e

    # Tier 2: PLAMEN_HOME/.python3.bat as a softer fallback. Works only
    # if PLAMEN_HOME is on PATH ahead of WindowsApps. The README install
    # instructions add PLAMEN_HOME to PATH, so this covers the standard
    # case; users with non-standard PATH ordering see the doctor warning
    # and can disable the App Execution Alias themselves.
    shim_path = os.path.join(PLAMEN_HOME, "python3.bat")
    try:
        with open(shim_path, "w", encoding="ascii", newline="") as f:
            f.write("@echo off\r\n")
            f.write(f'"{py_exe}" %*\r\n')
            f.write("exit /b %ERRORLEVEL%\r\n")
        w(f"  {_C_ORANGE}!{_RST} Couldn't write python3.exe next to {py_exe}\n")
        w(f"    {_C_GRAY}Reason: {tier1_error}{_RST}\n")
        w(f"    {_C_GRAY}Fallback shim: {shim_path}{_RST}\n")
        w(f"    {_C_GRAY}Effective only if ~/.plamen is on PATH before WindowsApps.{_RST}\n")
    except OSError as e_shim:
        w(f"  {_C_RED}!{_RST} Could not create any python3 shim\n")
        w(f"    {_C_GRAY}python.exe dir: {tier1_error}{_RST}\n")
        w(f"    {_C_GRAY}PLAMEN_HOME shim: {e_shim}{_RST}\n")
        w(f"    {_C_GRAY}Workaround: Settings > Apps > Advanced app{_RST}\n")
        w(f"    {_C_GRAY}settings > App execution aliases > turn OFF{_RST}\n")
        w(f"    {_C_GRAY}App Installer python.exe + python3.exe.{_RST}\n")


def _heal_dangling_hooks(w):
    """Strip dangling Plamen-owned hook entries from ~/.claude/settings.json.

    A previous install whose ~/.plamen/ source has been moved or renamed leaves
    every PreToolUse Bash hook pointing into a vanished symlink target. Claude
    Code's hook runner then blocks every Bash invocation — including a retry
    of `plamen install` itself.

    Convention: any hook whose `command` string contains `~/.claude/hooks/` is
    Plamen-owned. We check whether that target resolves. If not, strip the
    entry. The subsequent symlink install re-wires the hooks dir fresh.
    Non-Plamen hooks (anything else in the command field) are preserved.
    """
    import json as _json

    settings_path = os.path.join(CLAUDE_HOME, "settings.json")
    if not os.path.isfile(settings_path):
        return

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except (_json.JSONDecodeError, ValueError, OSError):
        return  # _merge_settings_json will report.

    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not hooks:
        return

    claude_hooks_dir = os.path.normpath(os.path.expanduser(
        os.path.join(CLAUDE_HOME, "hooks")
    ))
    plamen_marker = "~/.claude/hooks/"

    def _entry_is_dangling(entry):
        cmd = entry.get("command", "") if isinstance(entry, dict) else ""
        if plamen_marker not in cmd:
            return False  # not Plamen-owned; preserve.
        for tok in cmd.split():
            if tok.startswith("~/.claude/hooks/") or tok.startswith(claude_hooks_dir):
                resolved = os.path.normpath(os.path.expanduser(tok))
                try:
                    real = os.path.realpath(resolved)
                except OSError:
                    return True
                return not os.path.isfile(real)
        return False

    stripped = 0
    new_hooks = {}
    for event_name, event_groups in hooks.items():
        if not isinstance(event_groups, list):
            new_hooks[event_name] = event_groups
            continue
        kept_groups = []
        for group in event_groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            group_hooks = group.get("hooks", [])
            if not isinstance(group_hooks, list):
                kept_groups.append(group)
                continue
            kept_entries = []
            for entry in group_hooks:
                if _entry_is_dangling(entry):
                    stripped += 1
                else:
                    kept_entries.append(entry)
            if kept_entries:
                new_group = dict(group)
                new_group["hooks"] = kept_entries
                kept_groups.append(new_group)
            # else: whole group was Plamen-owned and dangling — drop it
        if kept_groups:
            new_hooks[event_name] = kept_groups

    if stripped == 0:
        return

    data["hooks"] = new_hooks
    try:
        with open(settings_path, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2)
            f.write("\n")
    except OSError as e:
        w(f"  {_C_ORANGE}!{_RST} Could not rewrite settings.json: {e}\n")
        return

    w(f"  {_C_ORANGE}>{_RST} Healed {stripped} dangling Plamen hook entr"
      f"{'y' if stripped == 1 else 'ies'} in settings.json\n")
    w(f"    {_C_GRAY}(previous install location was moved or removed; "
      f"fresh hooks will be wired below){_RST}\n")


def _merge_settings_json(w):
    """Merge Plamen's permissions and env into ~/.claude/settings.json (additive only)."""
    import json as _json

    example = os.path.join(PLAMEN_HOME, "settings.json.example")
    target = os.path.join(CLAUDE_HOME, "settings.json")

    if not os.path.isfile(example):
        w(f"  {_C_ORANGE}settings.json.example not found — skipping{_RST}\n")
        return

    with open(example) as f:
        plamen = _json.load(f)

    existing = {}
    if os.path.isfile(target):
        try:
            with open(target) as f:
                existing = _json.load(f)
        except (_json.JSONDecodeError, ValueError) as e:
            w(f"  {_C_RED}settings.json is not valid JSON: {e}{_RST}\n")
            w(f"  {_C_GRAY}  Fix the file manually, then re-run install.{_RST}\n")
            w(f"  {_C_GRAY}  Common cause: trailing commas or missing quotes.{_RST}\n")
            return

    # Merge env vars (additive — don't overwrite existing keys)
    plamen_env = plamen.get("env", {})
    existing.setdefault("env", {})
    added_env = []
    for k, v in plamen_env.items():
        if k not in existing["env"]:
            existing["env"][k] = v
            added_env.append(k)

    # Merge permissions (union of allow/deny lists)
    plamen_perms = plamen.get("permissions", {})
    existing.setdefault("permissions", {})
    for key in ("allow", "deny"):
        plamen_list = plamen_perms.get(key, [])
        existing_list = existing["permissions"].get(key, [])
        merged = list(dict.fromkeys(existing_list + plamen_list))
        existing["permissions"][key] = merged

    if "defaultMode" not in existing["permissions"]:
        existing["permissions"]["defaultMode"] = plamen_perms.get("defaultMode", "acceptEdits")

    with open(target, "w") as f:
        _json.dump(existing, f, indent=2)
        f.write("\n")

    w(f"  {_C_GREEN}settings.json: merged permissions + env{_RST}\n")


def _merge_mcp_json(w):
    """Merge Plamen's MCP servers into ~/.claude/mcp.json (additive only)."""
    import json as _json

    example = os.path.join(PLAMEN_HOME, "mcp.json.example")
    target = os.path.join(CLAUDE_HOME, "mcp.json")

    if not os.path.isfile(example):
        w(f"  {_C_ORANGE}mcp.json.example not found — skipping{_RST}\n")
        return

    with open(example) as f:
        plamen = _json.load(f)

    # Resolve all command/cwd paths to platform-correct absolute paths.
    # - python/python3 → sys.executable (correct site-packages + venv)
    # - npx → absolute path via shutil.which (prevents ENOENT on systems
    #   where npx is not in the MCP server's PATH)
    # - slither-mcp → absolute path via _find_bin (checks pip script dirs)
    # - relative cwd (./...) → absolute path under PLAMEN_HOME
    def _resolve_command(cmd: str) -> str:
        """Resolve a generic command name to an absolute platform path."""
        if cmd in ("python", "python3"):
            return sys.executable
        # Build extra search dirs: pip --user scripts + sys.executable's dir.
        # Covers macOS ~/Library/Python/X.Y/bin/, Linux ~/.local/bin/,
        # Windows %APPDATA%/Python/PythonXY/Scripts/, and venv/conda bins.
        extra = [os.path.dirname(sys.executable)]
        try:
            import sysconfig as _sc
            for scheme in ("posix_user", "nt_user"):
                try:
                    d = _sc.get_path("scripts", scheme)
                    if d and os.path.isdir(d):
                        extra.append(d)
                except KeyError:
                    pass
        except Exception:
            pass
        resolved = _find_bin(cmd, extra_paths=extra)
        return resolved if resolved else cmd  # keep original if not found

    for _name, config in plamen.get("mcpServers", {}).items():
        if "cwd" in config and config["cwd"].startswith("./"):
            config["cwd"] = os.path.join(PLAMEN_HOME, config["cwd"][2:])
        config["command"] = _resolve_command(config["command"])

    existing = {"mcpServers": {}}
    if os.path.isfile(target):
        try:
            with open(target) as f:
                existing = _json.load(f)
        except (_json.JSONDecodeError, ValueError) as e:
            w(f"  {_C_RED}mcp.json is not valid JSON: {e}{_RST}\n")
            w(f"  {_C_GRAY}  Fix the file manually, then re-run install.{_RST}\n")
            w(f"  {_C_GRAY}  Common cause: trailing commas or missing quotes.{_RST}\n")
            return
        existing.setdefault("mcpServers", {})

    def _is_wrong_platform_path(path: str) -> bool:
        """Detect paths from a different OS (e.g., Windows paths on macOS/Linux)."""
        if not path:
            return False
        if sys.platform == "win32":
            return path.startswith("/") and not path.startswith("//")
        return bool(re.match(r'^[A-Za-z]:[/\\]', path))

    added, skipped, patched_env, patched_paths = [], [], [], []
    for name, config in plamen.get("mcpServers", {}).items():
        if name in existing["mcpServers"]:
            skipped.append(name)
            ex = existing["mcpServers"][name]

            # Fix stale command/cwd paths from a different platform.
            # Preserves user's env vars and any other customizations —
            # only overwrites command and cwd with the resolved template values.
            if _is_wrong_platform_path(ex.get("command", "")):
                ex["command"] = config["command"]
                patched_paths.append(f"{name}.command")
            if _is_wrong_platform_path(ex.get("cwd", "")):
                ex["cwd"] = config["cwd"]
                patched_paths.append(f"{name}.cwd")

            # Backfill missing env vars into existing servers (e.g., new keys added
            # to mcp.json.example after initial install — propagate to existing config)
            template_env = config.get("env", {})
            if template_env:
                existing_env = ex.setdefault("env", {})
                for k, v in template_env.items():
                    if k not in existing_env and not v.startswith("YOUR_"):
                        existing_env[k] = v
                        patched_env.append(f"{name}.{k}")
        else:
            existing["mcpServers"][name] = config
            added.append(name)

    with open(target, "w") as f:
        _json.dump(existing, f, indent=2)
        f.write("\n")

    if added:
        w(f"  {_C_GREEN}mcp.json: added {', '.join(added)}{_RST}\n")
    if patched_paths:
        w(f"  {_C_GREEN}mcp.json: fixed platform paths: {', '.join(patched_paths)}{_RST}\n")
    if patched_env:
        w(f"  {_C_GREEN}mcp.json: backfilled env vars: {', '.join(patched_env)}{_RST}\n")
    if skipped:
        w(f"  {_C_GRAY}mcp.json: kept existing {', '.join(skipped)}{_RST}\n")
    if not added and not skipped and not patched_env and not patched_paths:
        w(f"  {_C_GREEN}mcp.json: up to date{_RST}\n")

    # Remind about API keys for newly added servers
    needs_keys = [n for n in added if any(
        "YOUR_" in str(v) for v in (plamen.get("mcpServers", {}).get(n, {}).get("env", {})).values()
    )]
    if needs_keys:
        w(f"  {_C_GRAY}  Edit {target} to add API keys for: {', '.join(needs_keys)}{_RST}\n")
        w(f"  {_C_GRAY}  Free keys: solodit.cyfrin.io, etherscan.io/apis, tavily.com{_RST}\n")


def _merge_claude_md(w):
    """Inject Plamen's CLAUDE.md into ~/.claude/CLAUDE.md between markers."""
    plamen_md = os.path.join(PLAMEN_HOME, "CLAUDE.md")
    target = os.path.join(CLAUDE_HOME, "CLAUDE.md")

    if not os.path.isfile(plamen_md):
        w(f"  {_C_ORANGE}CLAUDE.md not found in repo — skipping{_RST}\n")
        return

    with open(plamen_md, "r", encoding="utf-8") as f:
        plamen_content = f.read().strip()

    same_file = os.path.realpath(plamen_md) == os.path.realpath(target)

    # If source file already contains markers (same-dir install, or prior install
    # committed back to repo), extract only the content within markers as the
    # canonical Plamen instructions. This prevents content doubling/tripling
    # when PLAMEN_HOME == CLAUDE_HOME.
    if _CLAUDE_MD_START in plamen_content:
        start_idx = plamen_content.index(_CLAUDE_MD_START) + len(_CLAUDE_MD_START)
        if _CLAUDE_MD_END in plamen_content:
            end_idx = plamen_content.index(_CLAUDE_MD_END)
        else:
            end_idx = len(plamen_content)
        plamen_content = plamen_content[start_idx:end_idx].strip()

    if same_file:
        # Same-dir install: file is both source and target.
        # No separate user content to preserve — just write clean markers.
        with open(target, "w", encoding="utf-8") as f:
            f.write(f"{_CLAUDE_MD_START}\n{plamen_content}\n{_CLAUDE_MD_END}\n")
        w(f"  {_C_GREEN}CLAUDE.md: refreshed Plamen instructions (same-dir){_RST}\n")
        return

    existing = ""
    if os.path.isfile(target):
        with open(target, "r", encoding="utf-8") as f:
            existing = f.read()

    # Remove any prior Plamen section
    if _CLAUDE_MD_START in existing:
        if _CLAUDE_MD_END in existing:
            before = existing[:existing.index(_CLAUDE_MD_START)]
            after_end = existing.index(_CLAUDE_MD_END) + len(_CLAUDE_MD_END)
            after = existing[after_end:]
            existing = before.rstrip("\n") + after.lstrip("\n")
        else:
            # End marker missing (user corrupted file) — strip from start marker to EOF
            existing = existing[:existing.index(_CLAUDE_MD_START)].rstrip("\n")

    # Append Plamen section with markers
    injected = f"\n\n{_CLAUDE_MD_START}\n{plamen_content}\n{_CLAUDE_MD_END}\n"

    with open(target, "w", encoding="utf-8") as f:
        f.write(existing.rstrip("\n") + injected)

    w(f"  {_C_GREEN}CLAUDE.md: injected Plamen instructions (with markers){_RST}\n")


def run_uninstall():
    """Remove Plamen symlinks and injected config from ~/.claude/."""
    import json as _json
    w = sys.stdout.write

    manifest_path = os.path.join(CLAUDE_HOME, _PLAMEN_MANIFEST)
    if not os.path.isfile(manifest_path):
        w(f"  {_C_ORANGE}No install manifest found at {manifest_path}{_RST}\n")
        w(f"  {_C_GRAY}Nothing to uninstall.{_RST}\n")
        return

    with open(manifest_path) as f:
        manifest = _json.load(f)

    n_items = len(manifest.get("installed", []))
    w(f"\n  {_C_WHITE}This will remove {n_items} symlinks from {CLAUDE_HOME}{_RST}\n")
    w(f"  {_C_GRAY}and undo config merges (settings.json, mcp.json, CLAUDE.md).{_RST}\n")
    w(f"  {_C_GRAY}Backups (.pre-plamen) will be restored if they exist.{_RST}\n")
    w(f"  {_C_GRAY}The Plamen repo at {PLAMEN_HOME} will NOT be deleted.{_RST}\n\n")
    sys.stdout.flush()

    # Non-TTY guard: refuse to proceed without an interactive confirm.
    # Destructive ops should never silently auto-proceed; if you need a
    # scripted uninstall, set PLAMEN_UNINSTALL_YES=1 in the environment.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        if os.environ.get("PLAMEN_UNINSTALL_YES") == "1":
            w(f"  {_C_ORANGE}>{_RST} Non-TTY context, PLAMEN_UNINSTALL_YES=1 — proceeding.\n")
        else:
            w(f"  {_C_ORANGE}!{_RST} Non-TTY context (Claude Code Bash / CI / piped stdio).\n")
            w(f"    {_C_GRAY}Uninstall requires explicit confirmation. Either:{_RST}\n")
            w(f"    {_C_GRAY}  - Run `plamen uninstall` from a real terminal, OR{_RST}\n")
            w(f"    {_C_GRAY}  - Set PLAMEN_UNINSTALL_YES=1 to bypass the prompt.{_RST}\n\n")
            return
        # Non-TTY + PLAMEN_UNINSTALL_YES=1 path: skip the inquirer prompt.
        confirm = True
    else:
        confirm = inquirer.select(
            message="Proceed with uninstall?",
            choices=[
                {"name": "Yes, uninstall Plamen", "value": True},
                {"name": "Cancel", "value": False},
            ],
            default=False,
            pointer="  >",
            style=_STYLE,
            qmark=">",
            amark="✓",
        ).execute()

    if not confirm:
        w(f"  {_C_DARK_GRAY}Cancelled.{_RST}\n")
        return

    removed = 0
    restored = 0
    for path in manifest.get("installed", []):
        is_link = os.path.islink(path) or _is_junction(path)
        if is_link:
            if os.path.isdir(path) and not os.path.islink(path):
                os.rmdir(path)  # junction
            else:
                os.remove(path)  # symlink
            removed += 1
            backup = path + ".pre-plamen"
            if os.path.exists(backup):
                shutil.move(backup, path)
                restored += 1

    # Remove CLAUDE.md injection
    claude_md = os.path.join(CLAUDE_HOME, "CLAUDE.md")
    if os.path.isfile(claude_md):
        with open(claude_md, "r", encoding="utf-8") as f:
            content = f.read()
        if _CLAUDE_MD_START in content:
            if _CLAUDE_MD_END in content:
                before = content[:content.index(_CLAUDE_MD_START)]
                after_end = content.index(_CLAUDE_MD_END) + len(_CLAUDE_MD_END)
                after = content[after_end:]
                cleaned = before.rstrip("\n") + after.lstrip("\n")
            else:
                # End marker missing — strip from start marker to EOF
                cleaned = content[:content.index(_CLAUDE_MD_START)].rstrip("\n")
            with open(claude_md, "w", encoding="utf-8") as f:
                f.write(cleaned if cleaned.strip() else "")
            w(f"  {_C_GREEN}CLAUDE.md: removed Plamen section{_RST}\n")

    # Remove Plamen entries from settings.json
    settings_path = os.path.join(CLAUDE_HOME, "settings.json")
    if os.path.isfile(settings_path):
        with open(settings_path) as f:
            settings = _json.load(f)
        example = os.path.join(PLAMEN_HOME, "settings.json.example")
        if os.path.isfile(example):
            with open(example) as f:
                plamen_settings = _json.load(f)
            # Remove Plamen-specific permissions
            for key in ("allow", "deny"):
                plamen_list = plamen_settings.get("permissions", {}).get(key, [])
                current = settings.get("permissions", {}).get(key, [])
                if "permissions" in settings and key in settings["permissions"]:
                    settings["permissions"][key] = [x for x in current if x not in plamen_list]
            # Remove Plamen env vars
            for k in plamen_settings.get("env", {}):
                settings.get("env", {}).pop(k, None)
            # Remove legacy Plamen hooks (V1 watchdog — removed in v2.0.0)
            if "hooks" in settings:
                for event_name in list(settings["hooks"].keys()):
                    settings["hooks"][event_name] = [
                        group for group in settings["hooks"][event_name]
                        if not any("phase_gate.py" in h.get("command", "")
                                   or "command_guard.py" in h.get("command", "")
                                   or "primitive_telemetry.py" in h.get("command", "")
                                   for h in group.get("hooks", []))
                    ]
                    if not settings["hooks"][event_name]:
                        del settings["hooks"][event_name]
                if not settings["hooks"]:
                    del settings["hooks"]
            with open(settings_path, "w") as f:
                _json.dump(settings, f, indent=2)
                f.write("\n")
            w(f"  {_C_GREEN}settings.json: removed Plamen entries{_RST}\n")

    # Remove Plamen MCP servers from mcp.json
    mcp_path = os.path.join(CLAUDE_HOME, "mcp.json")
    if os.path.isfile(mcp_path):
        with open(mcp_path) as f:
            mcp = _json.load(f)
        example = os.path.join(PLAMEN_HOME, "mcp.json.example")
        if os.path.isfile(example):
            with open(example) as f:
                plamen_mcp = _json.load(f)
            for name in plamen_mcp.get("mcpServers", {}):
                mcp.get("mcpServers", {}).pop(name, None)
            with open(mcp_path, "w") as f:
                _json.dump(mcp, f, indent=2)
                f.write("\n")
            w(f"  {_C_GREEN}mcp.json: removed Plamen servers{_RST}\n")

    os.remove(manifest_path)

    w(f"\n  {_C_GREEN}Uninstalled: {removed} links removed, {restored} backups restored{_RST}\n")
    w(f"  {_C_GRAY}Plamen repo at {PLAMEN_HOME} is untouched.{_RST}\n\n")


def _install_codex_adapter(w):
    """Generate Codex config files and install into ~/.codex/.

    1. Runs scripts/codex_adapter.py to (re)generate codex-adapter/ files
    2. Creates ~/.codex/ if it doesn't exist
    3. Symlinks ~/.codex/plamen/ → PLAMEN_HOME (shared methodology)
    4. Copies Codex-specific files (AGENTS.md, config.toml, agents/, skills/, commands/)

    The repo dir is named `codex-adapter/` (not `codex/`) to avoid shadowing the
    Codex CLI binary when ~/.plamen is on PATH.
    """
    codex_home = os.path.normpath(os.path.expanduser("~/.codex"))
    codex_plamen = os.path.normpath(os.path.join(codex_home, "plamen"))
    codex_dir = os.path.normpath(os.path.join(PLAMEN_HOME, "codex-adapter"))
    generator = os.path.normpath(os.path.join(PLAMEN_HOME, "scripts", "codex_adapter.py"))

    # Step 0: Verify codex CLI is on PATH. Without it, the adapter still
    # generates files and the install reports success, but `codex` itself
    # won't run — the user discovers this when they try to start an audit.
    # Warn loud but don't block: a user may be staging configs for a machine
    # where codex will be installed later.
    codex_bin = _find_codex_bin()
    if not codex_bin:
        w(f"  {_C_ORANGE}!{_RST} `codex` not found on PATH. Adapter files will be generated,\n")
        w(f"    {_C_GRAY}but the Codex backend will be unusable until you install the CLI:{_RST}\n")
        w(f"    {_C_GRAY}  mkdir -p ~/.npm-global && npm config set prefix ~/.npm-global{_RST}\n")
        w(f"    {_C_GRAY}  echo 'export PATH=\"$HOME/.npm-global/bin:$PATH\"' >> ~/.zshrc{_RST}\n")
        w(f"    {_C_GRAY}  npm install -g @openai/codex{_RST}\n\n")
    else:
        w(f"  {_C_GREEN}✓{_RST} Codex CLI detected at {codex_bin}\n")

    # Step 1: Run the generator script
    w(f"  {_C_ORANGE}>{_RST} Generating Codex config files...\n")
    sys.stdout.flush()
    py = _python_bin()
    r = subprocess.run([py, generator, "--output-dir", codex_dir],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        w(f"  {_C_RED}Generator failed: {r.stderr[:300]}{_RST}\n")
        return False
    w(f"  {_C_GREEN}✓{_RST} Generated Codex files in codex-adapter/\n")

    # Step 2: Create ~/.codex/
    os.makedirs(codex_home, exist_ok=True)
    w(f"  {_C_GREEN}✓{_RST} ~/.codex/ directory ready\n")

    # Step 3: Symlink ~/.codex/plamen/ → PLAMEN_HOME
    w(f"  {_C_ORANGE}>{_RST} Linking ~/.codex/plamen/ → {PLAMEN_HOME}\n")
    if _safe_link(PLAMEN_HOME, codex_plamen, w):
        w(f"  {_C_GREEN}✓{_RST} Shared methodology linked\n")
    else:
        w(f"  {_C_ORANGE}!{_RST} Could not create symlink — Codex may not find methodology files\n")

    # Step 4: Copy Codex-specific files into ~/.codex/
    items_copied = 0
    for item in ("AGENTS.md", "config.toml"):
        src = os.path.join(codex_dir, item)
        dst = os.path.join(codex_home, item)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            items_copied += 1

    # Copy agents/ directory
    agents_src = os.path.join(codex_dir, "agents")
    agents_dst = os.path.join(codex_home, "agents")
    if os.path.isdir(agents_src):
        os.makedirs(agents_dst, exist_ok=True)
        for f in os.listdir(agents_src):
            src_f = os.path.join(agents_src, f)
            dst_f = os.path.join(agents_dst, f)
            if os.path.isfile(src_f):
                shutil.copy2(src_f, dst_f)
                items_copied += 1

    # Copy skills/ directory tree
    skills_src = os.path.join(codex_dir, "skills")
    skills_dst = os.path.join(codex_home, "skills")
    if os.path.isdir(skills_src):
        for root, dirs, files in os.walk(skills_src):
            rel = os.path.relpath(root, skills_src)
            dst_root = os.path.join(skills_dst, rel)
            os.makedirs(dst_root, exist_ok=True)
            for f in files:
                shutil.copy2(os.path.join(root, f), os.path.join(dst_root, f))
                items_copied += 1

    w(f"  {_C_GREEN}✓{_RST} Copied {items_copied} Codex-specific files into {codex_home}\n")

    commands_src = os.path.join(codex_dir, "commands")
    commands_dst = os.path.join(codex_home, "commands")
    if os.path.isdir(commands_src):
        os.makedirs(commands_dst, exist_ok=True)
        for f in os.listdir(commands_src):
            src_f = os.path.join(commands_src, f)
            dst_f = os.path.join(commands_dst, f)
            if os.path.isfile(src_f):
                shutil.copy2(src_f, dst_f)
                items_copied += 1

    # NOTE: Codex MCP tool permissions cannot be pre-configured via rules files.
    # Users must select "Always allow" on the first MCP tool prompt per server.
    # Codex's rules/default.rules only supports prefix_rule (shell commands).
    w(f"  {_C_ORANGE}!{_RST} MCP tools require one-time approval on first use in Codex.\n")
    w(f"    {_C_GRAY}Select '3. Always allow' when prompted for each MCP server.{_RST}\n")

    # Summary
    w(f"\n  {_C_GREEN}Codex adapter installed successfully.{_RST}\n")
    w(f"  {_C_GRAY}  Shared methodology: {codex_plamen} → {PLAMEN_HOME}{_RST}\n")
    w(f"  {_C_GRAY}  Codex config: {os.path.join(codex_home, 'config.toml')}{_RST}\n")
    w(f"  {_C_GRAY}  Agent roles: {os.path.join(codex_home, 'agents', '*.toml')}{_RST}\n")
    w(f"  {_C_GRAY}  Skill: {os.path.join(codex_home, 'skills', 'plamen', 'SKILL.md')}{_RST}\n")
    w(f"  {_C_GRAY}  Commands: {os.path.join(codex_home, 'commands', 'plamen*.md')}{_RST}\n")
    w(f"\n  {_C_ORANGE}>{_RST} Remember to replace API key placeholders in config.toml\n\n")
    return True


def run_doctor():
    """Fast install-verification — no audit run, no paid API calls.

    Checks every artifact the wizard / driver depends on:
      * Plamen home dir + manifest present
      * Required CLIs on PATH (python, git, npx — plus claude OR codex)
      * Python deps importable (rich, InquirerPy, core RAG packages)
      * ~/.claude symlinks resolving (Claude backend)
      * ~/.codex/plamen symlink resolving (Codex backend, if installed)
      * Submodules populated (custom-mcp/slither-mcp, farofino-mcp)
      * settings.json / CLAUDE.md markers intact

    Exit 0 if all green. Exit 1 if any check fails — useful for CI.
    """
    import json as _json

    w = sys.stdout.write
    show_banner()
    console.print(Rule(title="Plamen Doctor", style="color(238)"))

    failures = []
    warnings = []

    def ok(msg):
        w(f"  {_C_GREEN}✓{_RST} {msg}\n")

    def fail(msg):
        w(f"  {_C_RED}✗{_RST} {msg}\n")
        failures.append(msg)

    def warn(msg):
        w(f"  {_C_ORANGE}!{_RST} {msg}\n")
        warnings.append(msg)

    # 1. Plamen home dir
    plamen_dir = os.path.normpath(os.path.expanduser("~/.plamen"))
    if os.path.isdir(plamen_dir):
        ok(f"~/.plamen exists at {plamen_dir}")
    elif os.path.normpath(PLAMEN_HOME) != plamen_dir:
        warn(f"~/.plamen missing; running from {PLAMEN_HOME}")
    else:
        fail("~/.plamen missing")

    # 2. Required CLIs
    for tool in ("python", "git", "npx"):
        binary = _find_bin(tool) or (_find_bin("python3") if tool == "python" else "")
        if binary:
            ok(f"`{tool}` on PATH ({binary})")
        else:
            fail(f"`{tool}` not on PATH")

    # 2a. Windows: detect the Microsoft Store App Execution Alias stubs.
    # `python.exe` and `python3.exe` ship as 0-byte stubs in
    # `%LOCALAPPDATA%\Microsoft\WindowsApps\` that open the Store instead
    # of running Python. They sit at the front of PATH on fresh Windows
    # installs, so LLM-spawned shells (recon/depth agents, bake scripts)
    # that invoke `python` or `python3` from the prompt keep popping the
    # Store mid-audit. Plamen's own subprocess.run calls bypass this by
    # using sys.executable, but we can't control what an LLM types into
    # a Bash tool — best we can do is detect + warn.
    if sys.platform == "win32":
        store_stub_dir = os.path.normpath(os.path.expanduser(
            "~/AppData/Local/Microsoft/WindowsApps"
        ))
        for stub_name in ("python.exe", "python3.exe"):
            stub_path = os.path.join(store_stub_dir, stub_name)
            try:
                size = os.path.getsize(stub_path)
            except OSError:
                continue
            # Real interpreters are megabytes; the Store aliases are
            # 0 bytes (reparse-point stubs).
            if size == 0:
                warn(
                    f"Windows Microsoft Store stub at {stub_path} "
                    f"will open the Store every time `python`/`python3` "
                    f"is invoked by an LLM agent. Disable via Settings "
                    f"> Apps > Advanced app settings > App execution "
                    f"aliases (turn OFF App Installer python/python3)."
                )

    claude_bin = _find_bin("claude") or _find_bin("claude.cmd")
    codex_bin = _find_codex_bin()
    if claude_bin or codex_bin:
        if claude_bin:
            ok(f"`claude` on PATH ({claude_bin})")
        else:
            warn("`claude` not on PATH (Claude Code backend unavailable)")
        if codex_bin:
            ok(f"`codex` on PATH ({codex_bin})")
        else:
            warn("`codex` not on PATH (Codex CLI backend unavailable)")
    else:
        fail("Neither `claude` nor `codex` on PATH — no backend usable")

    # 3. Python deps
    for mod in ("rich", "InquirerPy"):
        try:
            __import__(mod)
            ok(f"Python module `{mod}` importable")
        except ImportError:
            fail(f"Python module `{mod}` missing (run `plamen install`)")
    for mod, hint in (("sentence_transformers", "RAG"), ("chromadb", "RAG")):
        try:
            __import__(mod)
            ok(f"Python module `{mod}` importable")
        except ImportError:
            warn(f"Python module `{mod}` missing — {hint} disabled (`plamen rag` to build)")

    # 4. ~/.claude install (if claude backend exists)
    if claude_bin:
        manifest_path = os.path.join(CLAUDE_HOME, _PLAMEN_MANIFEST)
        if os.path.isfile(manifest_path):
            ok(f"Plamen manifest at {manifest_path}")
            try:
                with open(manifest_path) as f:
                    m = _json.load(f)
                installed = m.get("installed", [])
                missing_links = [p for p in installed if not os.path.exists(p)]
                if missing_links:
                    fail(f"{len(missing_links)} symlinked items missing on disk (re-run `plamen install`)")
                else:
                    ok(f"All {len(installed)} symlinked items resolve")
            except Exception as e:
                fail(f"Manifest unreadable: {e}")
        else:
            warn(f"No Plamen manifest at {manifest_path} (run `plamen install`)")

        claude_md = os.path.join(CLAUDE_HOME, "CLAUDE.md")
        if os.path.isfile(claude_md):
            try:
                with open(claude_md, "r", encoding="utf-8") as f:
                    text = f.read()
                # Match the canonical marker constants (`_CLAUDE_MD_START`
                # is the long form `<!-- PLAMEN:START — managed by ... -->`;
                # the previous short literal `<!-- PLAMEN:START -->` is
                # never written by install, so the warn branch always fired
                # on a healthy install).
                if _CLAUDE_MD_START in text and _CLAUDE_MD_END in text:
                    ok("CLAUDE.md has Plamen marker block")
                else:
                    warn("CLAUDE.md missing PLAMEN markers (re-run `plamen install`)")
            except OSError as e:
                warn(f"Could not read CLAUDE.md: {e}")
        else:
            warn("CLAUDE.md missing")

    # 5. ~/.codex install (if codex backend exists)
    if codex_bin:
        codex_plamen = os.path.normpath(os.path.expanduser("~/.codex/plamen"))
        if os.path.isdir(codex_plamen) or os.path.islink(codex_plamen):
            ok(f"~/.codex/plamen exists ({codex_plamen})")
        else:
            warn("~/.codex/plamen missing (run `plamen install --codex`)")
        agents_md = os.path.normpath(os.path.expanduser("~/.codex/AGENTS.md"))
        if os.path.isfile(agents_md):
            ok("~/.codex/AGENTS.md present")
        else:
            warn("~/.codex/AGENTS.md missing (run `plamen install --codex`)")

    # 6. Submodules populated
    for sub, critical_for in (
        ("custom-mcp/slither-mcp", "EVM static analysis"),
        ("custom-mcp/farofino-mcp", "EVM/Aderyn integration"),
    ):
        path = os.path.join(PLAMEN_HOME, sub)
        if not os.path.isdir(path):
            warn(f"{sub} dir missing entirely")
            continue
        has_setup = (
            os.path.isfile(os.path.join(path, "setup.py"))
            or os.path.isfile(os.path.join(path, "pyproject.toml"))
            or os.listdir(path)
        )
        if has_setup:
            ok(f"Submodule `{sub}` populated")
        else:
            warn(f"Submodule `{sub}` empty — {critical_for} unavailable. "
                 f"Run `git -C ~/.plamen submodule update --init --recursive`.")

    # Summary
    w("\n")
    if not failures and not warnings:
        w(f"  {_C_GREEN}All checks green — Plamen is ready.{_RST}\n\n")
        return 0
    if failures:
        w(f"  {_C_RED}{len(failures)} hard failure(s):{_RST}\n")
        for f in failures:
            w(f"    {_C_RED}- {f}{_RST}\n")
    if warnings:
        w(f"  {_C_ORANGE}{len(warnings)} warning(s):{_RST}\n")
        for f in warnings:
            w(f"    {_C_ORANGE}- {f}{_RST}\n")
    w("\n")
    return 0 if not failures else 1


def run_migrate():
    """Atomic v1.x → v2.x migration.

    Three states handled:
      (a) v1.x with .git/  → rename ~/.claude → ~/.plamen
      (b) v1.x without .git/ → timestamped backup, then ask user to re-clone
      (c) v2.x state → just run install

    Heals dangling Plamen hook entries before touching anything (so a retry
    inside Claude Code isn't blocked by PreToolUse Bash). Runs the
    non-interactive install. Verifies CLAUDE.md PLAMEN markers.
    """
    import datetime as _dt

    w = sys.stdout.write
    show_banner()
    console.print(Rule(title="Plamen Migration", style="color(238)"))

    plamen_dir = os.path.normpath(os.path.expanduser("~/.plamen"))
    claude_dir = CLAUDE_HOME

    # v1.x put the whole repo in ~/.claude/. Detect by ANY of the usual
    # tracked top-level entries — broaden vs the prior commands+agents
    # AND-check, which missed users mid-migration who had renamed or
    # cleared one of the two.
    _v1_marker_paths = [
        os.path.join(claude_dir, "commands", "plamen.md"),
        os.path.join(claude_dir, "commands", "plamen-l1.md"),
        os.path.join(claude_dir, "agents", "depth-token-flow.md"),
        os.path.join(claude_dir, "scripts", "plamen_driver.py"),
        os.path.join(claude_dir, "plamen.py"),
        os.path.join(claude_dir, "rules", "orchestrator-rules.md"),
    ]
    looks_like_v1 = (
        not os.path.isdir(plamen_dir)
        and any(os.path.exists(p) for p in _v1_marker_paths)
    )
    has_git = os.path.isdir(os.path.join(claude_dir, ".git"))

    _heal_dangling_hooks(w)

    if looks_like_v1:
        w(f"  {_C_ORANGE}>{_RST} Detected v1.x install — Plamen files live in {claude_dir}\n")

        if os.path.isdir(plamen_dir):
            w(f"  {_C_RED}!{_RST} ~/.plamen already exists; cannot migrate over it.\n")
            w(f"    {_C_GRAY}Move or remove ~/.plamen, then re-run `plamen migrate`.{_RST}\n\n")
            return 1

        if has_git:
            try:
                os.rename(claude_dir, plamen_dir)
                w(f"  {_C_GREEN}✓{_RST} Moved {claude_dir} → {plamen_dir}\n")
            except OSError as e:
                w(f"  {_C_RED}Could not move {claude_dir}: {e}{_RST}\n\n")
                return 1
        else:
            ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_dir = os.path.normpath(os.path.expanduser(f"~/.plamen-backup-{ts}"))
            try:
                os.rename(claude_dir, backup_dir)
                w(f"  {_C_GREEN}✓{_RST} Backed up {claude_dir} → {backup_dir}\n")
            except OSError as e:
                w(f"  {_C_RED}Could not back up {claude_dir}: {e}{_RST}\n\n")
                return 1
            w(f"\n  {_C_WHITE}This install has no .git/ — re-clone the repo, then re-run install:{_RST}\n")
            w(f"    git clone --recurse-submodules <repo-url> ~/.plamen\n")
            w(f"    cd ~/.plamen && python plamen.py install\n\n")
            return 0
    else:
        if not os.path.isdir(plamen_dir):
            w(f"  {_C_ORANGE}!{_RST} No existing install detected at ~/.claude (v1.x) or ~/.plamen (v2.x).\n")
            w(f"    {_C_GRAY}Clone the repo first:{_RST}\n")
            w(f"    {_C_GRAY}  git clone --recurse-submodules <repo-url> ~/.plamen{_RST}\n")
            w(f"    {_C_GRAY}  cd ~/.plamen && python plamen.py install{_RST}\n\n")
            return 1
        w(f"  {_C_GREEN}✓{_RST} Found existing Plamen repo at {plamen_dir}\n")

    w(f"\n")
    rc = run_install()
    if rc != 0:
        w(f"\n  {_C_RED}Install failed during migration.{_RST}\n\n")
        return rc

    claude_md = os.path.join(CLAUDE_HOME, "CLAUDE.md")
    if os.path.isfile(claude_md):
        try:
            with open(claude_md, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = ""
        # Same marker-constant fix as run_doctor — install writes the long
        # form (`_CLAUDE_MD_START`), not the bare `<!-- PLAMEN:START -->`.
        if _CLAUDE_MD_START in text and _CLAUDE_MD_END in text:
            w(f"  {_C_GREEN}✓{_RST} CLAUDE.md PLAMEN markers present\n")
        else:
            w(f"  {_C_ORANGE}!{_RST} CLAUDE.md PLAMEN markers missing — re-run `plamen install` to inject\n")

    w(f"\n  {_C_GREEN}Migration complete.{_RST}\n")
    w(f"  {_C_GRAY}Run `plamen` to verify, or `plamen setup` for the toolchain wizard.{_RST}\n\n")
    return 0


def run_install():
    """Non-interactive install: symlinks, settings merge, CLAUDE.md inject,
    submodules, Python deps, config files.

    Idempotent. Safe to run from any non-TTY context (Claude Code Bash, Codex
    shell, CI). Returns 0 on success. The interactive toolchain wizard lives
    in `run_setup()` — keep this function free of `inquirer.*` calls.
    """
    w = sys.stdout.write

    # ── Pre-flight: heal dangling Plamen hook references ──────
    # If a previous install moved ~/.plamen away or settings.json points hooks
    # at paths that no longer exist, Claude Code's PreToolUse Bash hook blocks
    # every shell command — including a retry of `plamen install`. Strip those
    # entries BEFORE the new install touches anything.
    _heal_dangling_hooks(w)

    # ── Windows-only: drop a `python3` shim so LLM-typed shell commands
    # don't hit the Microsoft Store App Execution Alias.
    _ensure_python3_shim_windows(w)

    # ── Persist Plamen-managed toolchain dirs to the Windows User PATH ──
    # Background: when Foundry / Solana / Aptos / Sui installers ran (either
    # via `plamen setup` or out-of-band by the user), they put binaries in
    # well-known per-tool directories. The dir is usually on Git Bash's
    # transient PATH (sourced from .bashrc / .profile), so the interactive
    # `which forge` check succeeds. BUT codex / claude subprocesses inherit
    # PATH from the persistent Windows User PATH at spawn time, and the
    # installers don't always write there. Result: agents report
    # "forge: command not found" mid-audit even though the user (and
    # `plamen doctor`) sees forge fine.
    #
    # Fix: scan the standard toolchain dirs; for any that EXIST on disk,
    # persist them to the User PATH. Idempotent — already-present entries
    # are a no-op via _persist_path_windows.
    #
    # Cross-OS coverage: the persist-to-registry half is Windows-only
    # because there's no equivalent registry on POSIX. macOS / Linux
    # users hit the same class of bug differently: Foundry's installer
    # writes `export PATH=...` into `.bashrc` / `.zshrc`, but a Codex
    # subprocess launched from a parent shell that didn't source those
    # files inherits a PATH without `~/.foundry/bin`. The remediation
    # there is shell-config-level, not registry-level — see the
    # diagnostic block below for the user-facing message.
    if sys.platform == "win32":
        toolchain_dirs = [
            "~/.foundry/bin",       # Foundry (forge / cast / anvil / chisel)
            "~/go/bin",             # Medusa, scip-go, ast-grep (Go-based)
            "~/.cargo/bin",         # Rust tooling (Stellar CLI, Scout, rust-analyzer)
            "~/.aptoscli/bin",      # Aptos CLI
            "~/AppData/Local/bin",  # Sui CLI (winget install location)
            "~/.local/bin",         # Opengrep + npm user-local prefix
            "~/.local/share/solana/install/active_release/bin",  # Solana
            "~/.avm/bin",           # Anchor (Solana)
            "~/.npm-global/bin",    # User-local npm prefix (Codex CLI)
        ]
        _update_path_env(toolchain_dirs, persist=True)

    # ── Cross-OS toolchain visibility report (all platforms) ────
    # Tell the user which chain-specific toolchains are detected and
    # which are missing. Doesn't install anything — that's `plamen setup`.
    # Surfacing the truth here means a user who runs `plamen install`
    # and then launches an audit doesn't get bitten by silent
    # COMPILATION_FAILED fuzz reports for missing forge / cargo / sui /
    # aptos / solana.
    _report_toolchain_visibility(w)

    # ── Symlink install (if repo is not directly in ~/.claude) ─
    has_claude = bool(shutil.which("claude") or shutil.which("claude.cmd"))
    if has_claude and os.path.normpath(PLAMEN_HOME) != os.path.normpath(CLAUDE_HOME):
        console.print(Rule(title="Linking into Claude Code", style="color(238)"))
        _run_symlink_install(w)
    elif not has_claude:
        w(f"  {_C_GRAY}Claude Code not detected -- skipping ~/.claude/ symlinks{_RST}\n")

    # ── Submodules ─────────────────────────────────────────────
    slither_dir = os.path.join(PLAMEN_HOME, "custom-mcp", "slither-mcp")
    if os.path.isdir(slither_dir) and not os.listdir(slither_dir):
        if not os.path.isdir(os.path.join(PLAMEN_HOME, ".git")):
            # ZIP download — `git submodule` would fail with "not a git
            # repository". Tell the user how to fix it explicitly.
            w(f"  {_C_ORANGE}!{_RST} Empty submodule {os.path.relpath(slither_dir, PLAMEN_HOME)}/ but no .git/ — looks like a ZIP download.\n")
            w(f"    {_C_GRAY}Re-clone via `git clone --recurse-submodules <repo-url>`,{_RST}\n")
            w(f"    {_C_GRAY}or run `git submodule update --init --recursive` after `git init`.{_RST}\n\n")
        else:
            w(f"  {_C_ORANGE}>{_RST} Initializing git submodules...\n")
            sys.stdout.flush()
            _run_install_cmd(f'cd "{PLAMEN_HOME}" && git submodule update --init --recursive', retries=1)
            w("\n")

    # ── Python dependencies ───────────────────────────────────
    console.print(Rule(title="Python Dependencies", style="color(238)"))
    _setup_python_deps(w)

    # ── Config files ──────────────────────────────────────────
    # All four merge steps target ~/.claude/ — only meaningful when Claude
    # Code is installed. On Codex-only machines (or CI runners with neither
    # backend installed), there's nothing to merge INTO and the writes
    # would fail with FileNotFoundError on the missing ~/.claude/ dir.
    if has_claude:
        console.print(Rule(title="Configuration", style="color(238)"))
        _setup_config_files(w)
    else:
        w(f"  {_C_GRAY}Claude Code not detected -- skipping ~/.claude/ config merge{_RST}\n")
        w(f"  {_C_GRAY}(Codex side, if installed, is handled by `plamen install --codex`){_RST}\n")

    return 0


def run_setup():
    """Full interactive setup: install + toolchain wizard + RAG build.

    Calls `run_install()` first for the non-interactive symlink/config work,
    then drops into the toolchain checkbox. In a non-TTY context, prints a
    completion message and returns 0 — never calls `inquirer.*`.
    """
    w = sys.stdout.write

    run_install()

    # Non-TTY guard. The toolchain checkbox below uses prompt_toolkit, which
    # crashes with `OSError: [Errno 22] Invalid argument` from add_reader when
    # there is no controlling terminal (Claude Code Bash, Codex shell, CI).
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        w(f"\n  {_C_GREEN}Plamen install complete.{_RST}\n")
        w(f"  {_C_GRAY}Run `plamen setup` from a real terminal to install missing{_RST}\n")
        w(f"  {_C_GRAY}toolchains (Foundry / Solana / Anchor / etc.) and build the RAG DB.{_RST}\n\n")
        return

    # ── Show toolchain box ──────────────────────────────────
    check_dependencies()

    # ── Collect what's missing ──────────────────────────────
    missing = {}
    for group, recipes in _INSTALL_RECIPES.items():
        group_missing = []
        for entry in recipes:
            display, check_fn, cmds, provides, est, paths, requires = entry
            if not check_fn():
                group_missing.append(entry)
        if group_missing:
            missing[group] = group_missing

    rag_empty = _rag_needs_build()
    rag_count = _probe_rag_db()

    # ── Build checkbox choices ──────────────────────────────
    item_choices = []

    if missing:
        for group, entries in missing.items():
            names = ", ".join(d for d, _, _, _, _, _, _ in entries)
            item_choices.append({"name": f"{group:8s} {names}", "value": group})
    else:
        for group, recipes in _INSTALL_RECIPES.items():
            names = ", ".join(d for d, _, _, _, _, _, _ in recipes)
            item_choices.append({"name": f"{group:8s} {names}  {_C_GREEN}✓{_RST}",
                                 "value": group, "enabled": False})

    if rag_empty:
        item_choices.append({"name": "RAG DB   vulnerability knowledge base",
                             "value": "__rag__"})
    else:
        rag_label = f"RAG DB   rebuild ({rag_count:,} entries)" if rag_count > 0 else "RAG DB   build vulnerability knowledge base"
        item_choices.append({"name": rag_label, "value": "__rag__"})

    all_values = [c["value"] for c in item_choices]

    choices = []
    if missing:
        choices.append({"name": "All      install everything below",
                        "value": "__all__"})
    else:
        choices.append({"name": "All      reinstall everything below",
                        "value": "__all__"})
    choices.append(Separator())
    choices.extend(item_choices)
    choices.append(Separator())
    choices.append({"name": "Skip     back to menu", "value": "__skip__"})

    if missing:
        w(f"  {_C_GRAY}Time estimates:{_RST}\n")
        for group, entries in missing.items():
            for display, _, _, _, est, _, requires in entries:
                prereq_note = ""
                prereq_list = [requires] if isinstance(requires, str) else (requires or [])
                for pname in prereq_list:
                    if pname:
                        prereq = _PREREQ_INSTALLERS.get(pname, {})
                        if not prereq.get("check", lambda: True)():
                            prereq_note += f" + {prereq.get('label', pname)}"
                w(f"    {_C_DARK_GRAY}{display}: {est}{prereq_note}{_RST}\n")
        if rag_empty:
            w(f"    {_C_DARK_GRAY}RAG DB: ~3-5 min (downloads + indexes){_RST}\n")
        w(f"\n  {_C_GRAY}Press Enter to begin installation{_RST}\n\n")
    else:
        w(f"  {_C_GREEN}All tools installed ({rag_count:,} RAG entries).{_RST}\n")
        w(f"  {_C_GRAY}Select components to reinstall/rebuild, or Skip to go back.{_RST}\n\n")
    sys.stdout.flush()

    selected = inquirer.checkbox(
        message="Select components (space to toggle):",
        choices=choices,
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()

    if not selected or selected == ["__skip__"]:
        return

    # Expand "All" into individual items
    if "__all__" in selected:
        selected = all_values
    selected = [s for s in selected if s not in ("__skip__", "__all__")]

    if not selected:
        return

    # ── Run installs ─────────────────────────────────────────
    console.print(Rule(style="color(238)"))

    for group in selected:
        if group == "__skip__":
            continue

        if group == "__rag__":
            w(f"\n  {_BOLD}{_C_WHITE}Building RAG vulnerability database...{_RST}"
              f"  {_C_DARK_GRAY}~3-5 min{_RST}\n\n")
            sys.stdout.flush()
            _build_rag_db(w)
            continue

        w(f"\n  {_BOLD}{_C_WHITE}Installing {group} toolchain...{_RST}\n")
        if group == "Solana" and sys.platform == "win32":
            w(f"  {_C_DARK_GRAY}Note: Solana dev is best supported on Linux/macOS/WSL.{_RST}\n")
            w(f"  {_C_DARK_GRAY}Cargo builds may fail on Windows due to OpenSSL/AppLocker.{_RST}\n")
        w("\n")
        sys.stdout.flush()

        entries = missing.get(group, _INSTALL_RECIPES.get(group, []))
        for display, check_fn, cmds_fn, provides, est, paths, requires in entries:
            w(f"  {_C_ORANGE}>{_RST} {_C_WHITE}{display}{_RST}"
              f"  {_C_DARK_GRAY}{est}{_RST}\n")
            sys.stdout.flush()

            # Check and install prerequisites first
            prereq_list = [requires] if isinstance(requires, str) else (requires or [])
            prereq_ok = True
            for prereq_name in prereq_list:
                if prereq_name and not _ensure_prereq(prereq_name, w):
                    prereq_ok = False
                    break
            if not prereq_ok:
                w(f"  {_C_RED}  skipped — prerequisite unavailable{_RST}\n\n")
                continue

            # Pre-create target dirs and add to PATH BEFORE install,
            # so the tool's own installer sees them and skips PATH warnings
            if paths:
                for p in paths:
                    # Skip Windows-absolute paths (C:/...) on non-Windows to avoid
                    # creating junk "C:" directories on macOS/Linux
                    if sys.platform != "win32" and len(p) >= 2 and p[1] == ':':
                        continue
                    expanded = os.path.normpath(os.path.expanduser(p))
                    try:
                        os.makedirs(expanded, exist_ok=True)
                    except PermissionError:
                        pass  # System paths (e.g., Program Files) can't be pre-created
                _update_path_env(paths, persist=True)
                _refresh_system_path()

            cmds = cmds_fn()
            success = True
            for cmd in cmds:
                if not _run_install_cmd(cmd, retries=1):
                    w(f"  {_C_RED}  failed — see output above{_RST}\n")
                    success = False
                    break
            if success:
                w(f"  {_C_GREEN}  done{_RST}\n")

            # Refresh PATH after install so re-check finds newly installed tools
            # (especially important for winget which modifies system PATH)
            if paths:
                _update_path_env(paths)
                _refresh_system_path()

            # Post-install runtime probe. Verifies each binary this recipe
            # claims to "provide" actually runs (catches: cargo install
            # finished but linker failed; Windows AppLocker blocked .exe;
            # missing system .so/.dll; PATH refresh didn't pick it up).
            # Output-only — never flips `success`, never aborts, never raises.
            if success:
                try:
                    for binary in (provides or []):
                        ok, msg = _probe_tool_runtime(binary, paths or [])
                        if ok and msg == "ok":
                            w(f"  {_C_GRAY}    ✓ {binary} runs{_RST}\n")
                        elif ok and msg == "skipped":
                            # No probe configured. Don't print — visual noise.
                            pass
                        else:
                            w(f"  {_C_ORANGE}    ⚠ {binary}: {msg} "
                              f"(installed but couldn't verify){_RST}\n")
                except Exception:
                    # Probe block is best-effort. Any exception here is a
                    # bug in the probe itself, not the install — swallow.
                    pass
            w("\n")

    # ── Re-check ─────────────────────────────────────────────
    console.print(Rule(style="color(238)"))
    w(f"  {_C_GRAY}Re-checking...{_RST}\n\n")
    sys.stdout.flush()
    check_dependencies()
    w("\n")


# ── Banner ───────────────────────────────────────────────────

def show_banner():
    w = sys.stdout.write
    width = _term_width()
    w("\n")

    if width >= 60:
        art = _ART_FULL
    else:
        art = _ART_COMPACT

    grad = _BANNER_GRAD if len(art) > 1 else [_BANNER_GRAD[-1]]
    for row, ansi_color in zip(art, grad):
        w(f"  {_BOLD}{ansi_color}{row}{_RST}\n")

    w("\n")
    console.print(Rule(style="color(238)"))
    w(f"  {_C_GREEN}⬡{_RST} {_BOLD}{_C_WHITE}Security Auditor{_RST}  {_DIM}v{VERSION}{_RST}\n")
    w("\n")
    sys.stdout.flush()


def show_hint_panel():
    """Box-drawn panel showing what users need to prepare."""
    w = sys.stdout.write
    bx = _C_BOX
    W = 52  # inner visible width (matches ─ count)

    def row(parts):
        """parts: list of (visible_text, ansi_color_or_None) tuples."""
        vis = sum(len(t) for t, _ in parts)
        colored = "".join(f"{c}{t}{_RST}" if c else t for t, c in parts)
        w(f"  {bx}│{_RST}{colored}{' ' * max(0, W - vis)}{bx}│{_RST}\n")

    w(f"  {bx}╭{'─' * W}╮{_RST}\n")
    row([("  you'll need to provide", _C_GRAY)])
    row([])  # blank line
    row([("  ", None), ("target", _C_GREEN), ("          ", None),
         ("local project directory", _C_GRAY)])
    row([("  ", None), ("docs", _C_GREEN), ("            ", None),
         ("whitepaper or spec (optional)", _C_GRAY)])
    row([("  ", None), ("scope", _C_GREEN), ("           ", None),
         ("scope.txt or notes (optional)", _C_GRAY)])
    row([("  ", None), ("ground truth", _C_GREEN), ("    ", None),
         ("reference report (compare only)", _C_GRAY)])
    w(f"  {bx}╰{'─' * W}╯{_RST}\n")
    w("\n")
    sys.stdout.flush()


# ── Helpers ──────────────────────────────────────────────────

# Dirs skipped at ANY depth (build artifacts, tooling, never contain source)
_SKIP_ALWAYS = {'node_modules', '.git', 'cache', 'artifacts', '.anchor', '.aptos', '.stellar', '.soroban',
                'typechain', 'typechain-types', 'coverage', '__pycache__', 'vendor'}
# Dirs skipped only at project ROOT level (contain deps/tests/scripts, not source)
_SKIP_ROOT = {'lib', 'target', 'build', 'out', 'test', 'tests', 'mock', 'mocks',
              'script', 'deploy', 'migrations', 'flatten', 'docs', 'doc'}
_SC_EXTS = {'.sol', '.rs', '.move'}
_L1_EXTS = {'.go', '.rs'}
_SRC_EXTS = _SC_EXTS | _L1_EXTS


def _count_source_files(d: str) -> int:
    """Count .sol/.rs/.move files recursively, pruning skip dirs on descent."""
    d = os.path.normpath(d)
    total = 0
    for root, dirs, files in os.walk(d):
        if os.path.normpath(root) == d:
            dirs[:] = [x for x in dirs if x not in _SKIP_ALWAYS and x not in _SKIP_ROOT]
        else:
            dirs[:] = [x for x in dirs if x not in _SKIP_ALWAYS]
        total += sum(1 for f in files if os.path.splitext(f)[1] in _SRC_EXTS)
    return total


def _is_home_or_root(d: str) -> bool:
    """Return True if d is a home directory, user root, or system root."""
    d = os.path.normpath(d)
    home = os.path.normpath(os.path.expanduser("~"))
    if d == home or d == os.path.dirname(home):
        return True
    # Windows roots like C:\ or Unix /
    if len(d) <= 3 or d in ("/", "\\"):
        return True
    return False


def _detect_project_hint(d: str) -> str:
    # Skip recursive scan on home/root dirs — they're too large
    if _is_home_or_root(d):
        return ""
    indicators = {
        "foundry.toml": "Foundry", "hardhat.config.js": "Hardhat",
        "hardhat.config.ts": "Hardhat", "truffle-config.js": "Truffle",
        "Anchor.toml": "Anchor", "Move.toml": "Move", "stellar.toml": "Soroban",
        "go.mod": "Go", "Cargo.toml": "Rust",
    }
    for fname, label in indicators.items():
        if os.path.exists(os.path.join(d, fname)):
            total = _count_source_files(d)
            return f"{label} — {total} source files" if total else label
    # No config file — still check for source files
    total = _count_source_files(d)
    if total:
        return f"{total} source files"
    return ""


# ── Detection Helpers ──────────────────────────────────────

_L1_GO_IMPORTS = [
    "reth-", "libp2p", "cometbft", "cosmos-sdk", "beacon-chain",
    "eth/protocols", "fork_choice", "x/staking", "prysm", "lighthouse",
    "tendermint", "consensus/", "p2p/", "core/vm/",
]
_L1_RUST_CRATES = [
    "reth", "lighthouse", "substrate", "libp2p", "tendermint",
    "revm", "alloy-consensus", "sc-consensus", "sp-consensus",
]


def _count_loc(target: str, extensions: set, skip_patterns: set = None) -> int:
    """Count lines of code for given extensions, skipping test files and vendor dirs."""
    skip = skip_patterns or set()
    total = 0
    for root, dirs, files in os.walk(target):
        rel = os.path.relpath(root, target)
        if os.path.normpath(root) == os.path.normpath(target):
            dirs[:] = [x for x in dirs if x not in _SKIP_ALWAYS and x not in _SKIP_ROOT]
        else:
            dirs[:] = [x for x in dirs if x not in _SKIP_ALWAYS]
        for f in files:
            if os.path.splitext(f)[1] not in extensions:
                continue
            low = f.lower()
            if any(p in low for p in ("_test.", "test_", ".test.", "_mock", "mock_")):
                continue
            if any(p in low for p in skip):
                continue
            try:
                fp = os.path.join(root, f)
                with open(fp, "r", errors="ignore") as fh:
                    total += sum(1 for _ in fh)
            except OSError:
                pass
    return total


def _detect_language(target: str) -> str:
    """Detect project language: evm, solana, soroban, aptos, sui, go, rust."""
    sol_count = _count_loc(target, {".sol"})
    go_count = _count_loc(target, {".go"})
    rs_count = _count_loc(target, {".rs"})
    move_count = _count_loc(target, {".move"})

    if sol_count > 0 and sol_count >= max(go_count, rs_count, move_count):
        return "evm"

    if move_count > 0 and move_count >= max(sol_count, go_count, rs_count):
        move_toml = os.path.join(target, "Move.toml")
        if os.path.isfile(move_toml):
            try:
                with open(move_toml, "r", errors="ignore") as f:
                    content = f.read()
                if "AptosFramework" in content or "aptos" in content.lower():
                    return "aptos"
            except OSError:
                pass
        return "sui"

    if rs_count > 0 and rs_count >= max(sol_count, go_count, move_count):
        cargo_toml = os.path.join(target, "Cargo.toml")
        if os.path.isfile(cargo_toml):
            try:
                with open(cargo_toml, "r", errors="ignore") as f:
                    content = f.read().lower()
                if "soroban" in content or "stellar" in content:
                    return "soroban"
                if "anchor" in content or "solana" in content:
                    return "solana"
                if any(c in content for c in _L1_RUST_CRATES):
                    return "rust"
            except OSError:
                pass
        # Check workspace members for deeper Cargo.toml files
        for sub in os.listdir(target):
            sub_cargo = os.path.join(target, sub, "Cargo.toml")
            if os.path.isfile(sub_cargo):
                try:
                    with open(sub_cargo, "r", errors="ignore") as f:
                        c = f.read().lower()
                    if "soroban" in c or "stellar" in c:
                        return "soroban"
                    if "anchor" in c or "solana" in c:
                        return "solana"
                except OSError:
                    pass
        return "rust"

    if go_count > 0 and go_count >= max(sol_count, rs_count, move_count):
        return "go"

    # Fallback: check config files
    if os.path.isfile(os.path.join(target, "Anchor.toml")):
        return "solana"
    if os.path.isfile(os.path.join(target, "foundry.toml")):
        return "evm"
    if os.path.isfile(os.path.join(target, "go.mod")):
        return "go"
    return "evm"


def _detect_pipeline(language: str) -> str:
    """Map language to pipeline: 'sc' or 'l1'."""
    if language in ("go", "rust"):
        return "l1"
    return "sc"


def _detect_l1_tier(loc: int) -> str:
    if loc < 2000:
        return "t0"
    if loc < 30000:
        return "t1"
    if loc <= 100000:
        return "t2"
    return "t3"


_L1_TIER_LABELS = {
    "t0": ("T0 — Patch", "<=2k LOC diff, PR/commit review"),
    "t1": ("T1 — Subsystem", "5-30k LOC, one module cluster"),
    "t2": ("T2 — Whole-client", "30-100k LOC, full codebase"),
    "t3": ("T3 — Full screen", ">100k LOC, breadth over depth"),
}


_WORKSPACE_CONTAINERS = {
    'crates', 'packages', 'subcrates', 'libs', 'components',
    'cmd', 'pkg', 'internal', 'modules',
}


def _scan_modules(target: str, language: str) -> list:
    """Enumerate project modules with LOC counts. Returns [(name, path, loc)].

    Expands workspace container dirs (crates/, cmd/, pkg/, internal/, etc.)
    into their children so each subcrate/subpackage appears as a selectable module.
    """
    exts = {".go"} if language == "go" else {".rs"}
    skip = _SKIP_ALWAYS | _SKIP_ROOT
    modules = []
    try:
        entries = sorted(os.listdir(target))
    except OSError:
        return []
    for entry in entries:
        full = os.path.join(target, entry)
        if not os.path.isdir(full):
            continue
        if entry in skip or entry.startswith("."):
            continue
        if entry.lower() in _WORKSPACE_CONTAINERS:
            try:
                children = sorted(os.listdir(full))
            except OSError:
                children = []
            for child in children:
                child_full = os.path.join(full, child)
                if not os.path.isdir(child_full):
                    continue
                if child in _SKIP_ALWAYS or child.startswith("."):
                    continue
                loc = _count_loc(child_full, exts)
                if loc > 0:
                    modules.append((f"{entry}/{child}", child_full, loc))
            continue
        loc = _count_loc(full, exts)
        if loc > 0:
            modules.append((entry, full, loc))
    return modules


def _detect_fork(target: str) -> bool:
    """Check for fork indicators: upstream remote or README mentions."""
    try:
        r = subprocess.run(
            ["git", "remote", "-v"], capture_output=True, text=True,
            cwd=target, timeout=5)
        if r.returncode == 0 and "upstream" in r.stdout.lower():
            return True
    except Exception:
        pass
    for readme in ("README.md", "README", "readme.md"):
        rp = os.path.join(target, readme)
        if os.path.isfile(rp):
            try:
                with open(rp, "r", errors="ignore") as f:
                    head = f.read(2000).lower()
                if "fork" in head and ("upstream" in head or "based on" in head):
                    return True
            except OSError:
                pass
    return False


def _l1_stages(mode, bc, vc, est_findings, src_tok, total_lines,
               PROMPT_BASE, SKILL_AVG, ARTIFACT_SMALL, ARTIFACT_LARGE, orch_base):
    """Build L1 pipeline stage list for cost estimation.

    L1 differs from SC: bake phase, no chain analysis, no instantiate,
    graph sweeps + location recovery (thorough), more verify shards,
    larger codebases with module-scoped agents.
    """
    # L1 breadth agents are module-scoped — each sees a smaller source slice
    # but L1 codebases are much larger, so cap the per-agent source fraction
    src_per_agent = min(src_tok, int(src_tok * 0.3))

    # L1 verify shards: 10 high + 6 medium + 4 low (thorough), scaled by findings
    vc_high = min(10, max(2, int(est_findings * 0.3)))
    vc_med = min(6, max(2, int(est_findings * 0.25)))
    vc_low = min(4, max(1, int(est_findings * 0.15)))

    if mode == "light":
        bc_l = min(3, max(2, bc))
        vc_l = min(4, max(2, est_findings // 4))
        return [
            ("Bake",             1, "sonnet", PROMPT_BASE + int(src_tok * 0.2), 6),
            ("Recon",            2, "sonnet", PROMPT_BASE + int(src_tok * 0.4) + ARTIFACT_SMALL, 10),
            ("Breadth",          bc_l, "sonnet", PROMPT_BASE + SKILL_AVG + src_per_agent + ARTIFACT_LARGE, 10),
            ("Inventory",        1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
            ("Depth (merged)",   3, "sonnet", PROMPT_BASE + SKILL_AVG * 2 + int(src_tok * 0.2) + ARTIFACT_LARGE, 10),
            ("Verification",     vc_l, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.15) + ARTIFACT_SMALL, 12),
            ("Report",           1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
            ("Report assembler", 1, "haiku",  PROMPT_BASE + ARTIFACT_LARGE * 2, 6),
            ("Orchestrator",     1, "sonnet", orch_base, 20),
        ]

    # Core stages (Thorough appends below)
    stages = [
        ("Bake",             1, "sonnet", PROMPT_BASE + int(src_tok * 0.2), 8),
        ("Recon (opus)",     2, "opus",   PROMPT_BASE + int(src_tok * 0.5) + ARTIFACT_SMALL, 12),
        ("Recon (sonnet)",   2, "sonnet", PROMPT_BASE + int(src_tok * 0.2) + ARTIFACT_SMALL, 10),
        ("Breadth",         bc, "sonnet", PROMPT_BASE + SKILL_AVG + src_per_agent + ARTIFACT_LARGE, 12),
        ("Inventory",        4, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
        ("Sem. Invariants",  1, "sonnet", PROMPT_BASE + int(src_tok * 0.3) + ARTIFACT_SMALL, 10),
        ("Depth (opus)",     3, "opus",   PROMPT_BASE + SKILL_AVG + int(src_tok * 0.2) + ARTIFACT_LARGE, 12),
        ("Depth (sonnet)",   2, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.2) + ARTIFACT_LARGE, 10),
        ("RAG + Scoring",    3, "haiku",  PROMPT_BASE + ARTIFACT_LARGE, 6),
        ("Semantic Dedup",   1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
        ("Verify (high)",    vc_high, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.15) + ARTIFACT_SMALL, 12),
        ("Verify (medium)",  vc_med, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.15) + ARTIFACT_SMALL, 12),
        ("Report (sonnet)",  3, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
        ("Report (haiku)",   2, "haiku",  PROMPT_BASE + ARTIFACT_LARGE * 2, 6),
        ("Orchestrator",     1, "opus",   orch_base, 25),
    ]

    if mode == "thorough":
        est_high_crit = max(2, int(est_findings * 0.3))
        est_judge = max(1, est_high_crit // 3)
        stages += [
            ("Graph Sweeps",     1, "sonnet", PROMPT_BASE + int(src_tok * 0.3) + ARTIFACT_LARGE, 10),
            ("Location Recov.",  1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE, 8),
            ("Attention Repair", 1, "sonnet", PROMPT_BASE + int(src_tok * 0.2) + ARTIFACT_LARGE, 10),
            ("Verify (low)",     vc_low, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.1) + ARTIFACT_SMALL, 10),
            ("Skeptic",          est_high_crit, "sonnet",
             PROMPT_BASE + SKILL_AVG + int(src_tok * 0.15) + ARTIFACT_SMALL, 10),
            ("Judge",            est_judge, "haiku",
             PROMPT_BASE + ARTIFACT_SMALL * 2, 4),
            ("Cross-batch",      1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE, 8),
            ("Orch. extra",      1, "opus",   orch_base, 15),
        ]

    return stages


def estimate_cost(target: str, mode: str,
                  scope_file: str = "", scope_notes: str = "",
                  pipeline: str = "sc", backend: str = "claude",
                  subsystem_scope: str = "") -> dict:
    """Estimate audit resource usage by modeling pipeline stages with context accumulation.

    Each subagent is a multi-turn conversation where every turn re-sends the full
    prior context. A 10-turn agent with 30K base context consumes ~400-500K input
    tokens total, not 30K. This model accounts for that accumulation.

    Respects scope constraints: if scope_file or scope_notes list specific contracts,
    only those files are counted for the estimate.
    pipeline='l1' uses L1-specific phases, extensions (.go/.rs), and scaling.
    subsystem_scope: comma-separated module paths for L1 T1 (e.g. 'crates/p2p,crates/rpc').

    Returns: lines, files, agents, est_input_mtok, plan_pct (Max x5 / x20)
    """
    # ── Build scope filter ──────────────────────────────────
    # Extract filenames/contract names from scope_file or scope_notes
    scope_names = set()  # lowercase basenames or stems to match against

    if scope_file and os.path.isfile(scope_file):
        try:
            import re as _re
            with open(scope_file, 'r', errors='ignore') as sf:
                for line in sf:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('//'):
                        continue
                    # Extract .sol/.rs/.move filenames from any format:
                    #   bare paths: "src/contracts/Vault.sol"
                    #   markdown tables: "| GatewaySend.sol | 301 |"
                    #   bullet lists: "- contracts/Vault.sol"
                    matches = _re.findall(r'[\w/\\.-]+\.(?:sol|rs|move)', line)
                    if matches:
                        for m in matches:
                            base = os.path.basename(m)
                            scope_names.add(base.lower())
                            stem = os.path.splitext(base)[0].lower()
                            if stem:
                                scope_names.add(stem)
                    else:
                        # Fallback: treat entire line as a path
                        base = os.path.basename(line.strip().rstrip('/'))
                        if base and '.' in base:
                            scope_names.add(base.lower())
                            stem = os.path.splitext(base)[0].lower()
                            if stem:
                                scope_names.add(stem)
        except Exception:
            pass

    if scope_notes and not scope_names:
        # Parse contract names from free-text notes
        # e.g., "focus on Vault, Router, Pool contracts"
        import re
        # Match capitalized words that look like contract names
        words = re.findall(r'\b([A-Z][a-zA-Z0-9_]+)\b', scope_notes)
        for w in words:
            scope_names.add(w.lower())
            # Also add .sol/.rs/.move variants
            scope_names.add(w.lower() + '.sol')
            scope_names.add(w.lower() + '.rs')
            scope_names.add(w.lower() + '.move')

    has_scope = len(scope_names) > 0

    # ── L1 module scoping: restrict walk to subsystem paths ─
    module_roots = []
    if subsystem_scope and pipeline == "l1":
        for mod_path in subsystem_scope.split(","):
            mod_path = mod_path.strip()
            if mod_path:
                full = os.path.join(target, mod_path)
                if os.path.isdir(full):
                    module_roots.append(os.path.normpath(full))

    # ── Count source files and lines ────────────────────────
    total_files = 0
    total_lines = 0

    # Skip recursive scan on home/root dirs — too large, no useful results
    if _is_home_or_root(target):
        return {
            "files": 0, "lines": 0, "agents": 0,
            "input_mtok": 0, "output_mtok": 0, "api_cost": 0,
            "pct_x5": 0, "pct_x20": 0, "pct_pro": 0, "scoped": False,
        }

    walk_targets = module_roots if module_roots else [target]
    for walk_root in walk_targets:
        walk_root_norm = os.path.normpath(walk_root)
        for root, dirs, files in os.walk(walk_root):
            if os.path.normpath(root) == walk_root_norm and not module_roots:
                dirs[:] = [x for x in dirs if x not in _SKIP_ALWAYS and x not in _SKIP_ROOT]
            else:
                dirs[:] = [x for x in dirs if x not in _SKIP_ALWAYS]
            exts = _L1_EXTS if pipeline == "l1" else _SC_EXTS
            for fname in files:
                if os.path.splitext(fname)[1] not in exts:
                    continue

                # Apply scope filter if we have scope constraints
                if has_scope:
                    basename = fname.lower()
                    stem = os.path.splitext(basename)[0]
                    if not (basename in scope_names or stem in scope_names):
                        continue

                total_files += 1
                try:
                    with open(os.path.join(root, fname), 'r', errors='ignore') as fh:
                        total_lines += sum(1 for _ in fh)
                except Exception:
                    pass

    src_tok = total_lines * 4  # ~4 tokens per line of code
    effective_lines = total_lines
    if pipeline == "l1" and total_lines > 30_000:
        # L1 V2 does not stream the full client into every agent. Recon and
        # breadth build bounded summaries/shard packets, then later phases
        # consume those artifacts. Past estimates scaled near-linearly with
        # total LOC, which made 150k-250k LOC clients look 3x too expensive.
        # Keep a small logarithmic lift for larger clients, but cap the code
        # context used by the estimate to match the bounded driver behavior.
        import math
        effective_lines = 30_000 + int(
            min(10_000, math.log2(max(total_lines / 30_000, 1.0)) * 3_000)
        )
        src_tok = effective_lines * 4

    # ── Agent token model with context accumulation ─────────
    # Each agent turn re-sends full prior context. Total input for N turns:
    #   sum(base + i * growth for i in range(turns))
    #   = turns * base + turns*(turns-1)/2 * growth
    # base = system_prompt + CLAUDE.md + skill + artifacts + source subset
    # growth = tool result + agent output per turn (~3-5K)

    def agent_tokens(base_ctx: int, turns: int, growth: int = 4000):
        """Total input tokens for a multi-turn agent conversation."""
        total_in = int(turns * base_ctx + turns * (turns - 1) / 2 * growth)
        total_out = int(turns * 3000)  # ~3K output per turn
        return total_in, total_out

    # Base context per agent type (system + CLAUDE.md + skill + artifacts)
    PROMPT_BASE = 8_000    # system prompt + CLAUDE.md
    SKILL_AVG = 8_000      # average skill file tokens
    ARTIFACT_SMALL = 5_000   # scratchpad refs (summaries only)
    ARTIFACT_LARGE = 20_000  # scratchpad refs (full inventory, findings)

    def breadth_count(lines):
        if lines < 2000:  return 2
        if lines < 5000:  return 4
        return min(7, 3 + lines // 3000)

    bc = breadth_count(total_lines)
    est_findings = bc * 5  # ~5 findings per breadth agent
    vc = min(10, max(3, est_findings // 3))

    # ── Pipeline stages ─────────────────────────────────────
    # (name, count, model, base_context, turns)
    orch_base = PROMPT_BASE + 15_000  # CLAUDE.md + methodology prompt loaded

    if pipeline == "l1":
        stages = _l1_stages(mode, bc, vc, est_findings, src_tok, total_lines,
                            PROMPT_BASE, SKILL_AVG, ARTIFACT_SMALL, ARTIFACT_LARGE, orch_base)
    elif mode == "light":
        # Light mode: all sonnet/haiku, no opus, fewer agents, merged phases
        bc_light = min(3, max(2, bc))  # cap breadth at 3
        est_findings_light = bc_light * 4  # fewer findings from sonnet breadth
        vc_light = min(4, max(2, est_findings_light // 3))
        stages = [
            ("Recon",            2, "sonnet", PROMPT_BASE + int(src_tok * 0.5) + ARTIFACT_SMALL, 10),
            ("Breadth",          bc_light, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.7) + ARTIFACT_LARGE, 10),
            ("Inventory",        1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
            ("Depth (merged)",   2, "sonnet", PROMPT_BASE + SKILL_AVG * 2 + int(src_tok * 0.4) + ARTIFACT_LARGE, 10),
            ("Scanner+Sweep",    2, "sonnet", PROMPT_BASE + int(src_tok * 0.3) + ARTIFACT_LARGE, 8),
            ("Chain",            1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
            ("Verification",     vc_light, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.25) + ARTIFACT_SMALL, 12),
            ("Report",           1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
            ("Report assembler", 1, "haiku",  PROMPT_BASE + ARTIFACT_LARGE * 2, 6),
            ("Orchestrator",     1, "sonnet", orch_base, 20),
        ]
    else:
        # Core mode (default stages — Thorough appends below)
        stages = [
            ("Recon (opus)",     2, "opus",   PROMPT_BASE + int(src_tok * 0.8) + ARTIFACT_SMALL, 12),
            ("Recon (sonnet)",   2, "sonnet", PROMPT_BASE + int(src_tok * 0.3) + ARTIFACT_SMALL, 10),
            ("Breadth",         bc, "opus",   PROMPT_BASE + SKILL_AVG + int(src_tok * 0.8) + ARTIFACT_LARGE, 12),
            ("Inventory",        1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
            ("Sem. Invariants",  1, "sonnet", PROMPT_BASE + int(src_tok * 0.5) + ARTIFACT_SMALL, 10),
            ("Depth (opus)",     2, "opus",   PROMPT_BASE + SKILL_AVG + int(src_tok * 0.4) + ARTIFACT_LARGE, 12),
            ("Depth (sonnet)",   2, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.4) + ARTIFACT_LARGE, 10),
            ("Scanners",         3, "sonnet", PROMPT_BASE + int(src_tok * 0.3) + ARTIFACT_LARGE, 8),
            ("Validation Sweep", 1, "sonnet", PROMPT_BASE + int(src_tok * 0.3) + ARTIFACT_LARGE, 8),
            ("Niche agents",     3, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.3) + ARTIFACT_SMALL, 8),
            ("RAG + Scoring",    3, "haiku",  PROMPT_BASE + ARTIFACT_LARGE, 6),
            ("Chain Analysis",   2, "opus",   PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
            ("Verification",    vc, "opus",   PROMPT_BASE + SKILL_AVG + int(src_tok * 0.25) + ARTIFACT_SMALL, 14),
            ("Report (opus)",    1, "opus",   PROMPT_BASE + ARTIFACT_LARGE * 3, 8),
            ("Report (sonnet)",  2, "sonnet", PROMPT_BASE + ARTIFACT_LARGE * 2, 8),
            ("Report (haiku)",   2, "haiku",  PROMPT_BASE + ARTIFACT_LARGE * 3, 6),
            ("Orchestrator",     1, "opus",   orch_base, 25),
        ]

    if pipeline != "l1" and mode == "thorough":
        # Estimate HIGH/CRIT findings for skeptic-judge: ~40% of findings are M+, ~30% are H/C
        est_high_crit = max(2, int(est_findings * 0.3))
        est_judge = max(1, est_high_crit // 3)  # ~1/3 of skeptics disagree
        # Per-contract clusters: ~1 per 500 lines, not 1 per file
        pc_count = min(8, max(2, total_lines // 500))

        # Apply skip probabilities for conditional stages:
        # - Re-scan: iter 2 often exits early (0 new findings) → ~60% of max agents
        # - Depth iter 2-3: ~50% chance of early exit (high confidence after iter 1)
        # - Inv/Medusa Fuzz: ~50% chance of being available/triggered
        # - Design Stress: ~40% chance budget redirect triggers
        # - Skeptic-Judge: runs only on HIGH/CRIT, ~70% agreement rate → few judges
        stages += [
            ("Re-scan",      3, "sonnet", PROMPT_BASE + int(src_tok * 0.6) + ARTIFACT_LARGE, 10),
            ("Per-contract", pc_count, "sonnet",
             PROMPT_BASE + int(src_tok * 0.25) + ARTIFACT_SMALL, 8),
            ("Sem. Pass 2",  1, "sonnet", PROMPT_BASE + int(src_tok * 0.4) + ARTIFACT_LARGE, 8),
            ("Depth iter2-3", 3, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.2) + ARTIFACT_SMALL, 8),
            ("Inv. Fuzz",    1, "sonnet", PROMPT_BASE + int(src_tok * 0.3) + ARTIFACT_SMALL, 10),
            ("Design Stress", 1, "sonnet", PROMPT_BASE + ARTIFACT_LARGE, 8),
            ("Extra verify", 2, "sonnet", PROMPT_BASE + SKILL_AVG + int(src_tok * 0.2) + ARTIFACT_SMALL, 10),
            ("Skeptic",      est_high_crit, "sonnet",
             PROMPT_BASE + SKILL_AVG + int(src_tok * 0.25) + ARTIFACT_SMALL, 10),
            ("Judge",        est_judge, "haiku",
             PROMPT_BASE + ARTIFACT_SMALL * 2, 4),
            ("Orch. extra",  1, "opus",   orch_base, 15),
        ]

    # ── Compute totals per model ──────────────────────────────
    model_input = {"opus": 0, "sonnet": 0, "haiku": 0}
    model_output = {"opus": 0, "sonnet": 0, "haiku": 0}
    total_input = 0
    total_output = 0
    total_agents = 0

    for _name, count, model, base_ctx, turns in stages:
        ai, ao = agent_tokens(base_ctx, turns)
        model_input[model] += count * ai
        model_output[model] += count * ao
        total_input += count * ai
        total_output += count * ao
        total_agents += count

    input_mtok = total_input / 1_000_000
    output_mtok = total_output / 1_000_000

    # ── API cost estimate ────────────────────────────────────
    if backend == "codex":
        # OpenAI API-equivalent pricing as of 2026-05:
        # GPT-5.5 $5/$30, GPT-5.4 $2.50/$15, GPT-5.4 nano $0.20/$1.25.
        # The launcher maps Plamen opus/sonnet/haiku tiers to those models by
        # default; env overrides may change real billing.
        pricing = {"opus": (5.0, 30.0), "sonnet": (2.5, 15.0), "haiku": (0.20, 1.25)}
    else:
        # Claude pricing (Opus 4.6 $5/$25, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5)
        pricing = {"opus": (5.0, 25.0), "sonnet": (3.0, 15.0), "haiku": (1.0, 5.0)}
    api_cost = 0.0
    for m in ("opus", "sonnet", "haiku"):
        ip, op = pricing[m]
        api_cost += (model_input[m] / 1e6) * ip + (model_output[m] / 1e6) * op

    # ── Weekly plan usage estimate ───────────────────────────
    # Calibrated from empirical data:
    #   SC: A thorough audit on a ~5000-line project uses ~9% of Max x20.
    #   L1: Recent whole-client thorough runs usually land around 10-20%
    #       of Max x20, centered near ~15%, because V2 uses bounded shards
    #       rather than reading full-client LOC in every phase.
    #   Max x20 weekly allowance: 240-480h Sonnet + 24-40h Opus (resets weekly).
    #   Max x5 has 4x less capacity than x20.
    ref_tokens = 0

    if pipeline == "l1":
        ref_src = 30_000 * 4
        ref_pct = 10.5
        ref_per_agent = min(ref_src, int(ref_src * 0.3))
        ref_stages = [
            # L1 Core stages
            (1, ref_src * 0.2 + 8000, 8),                                       # Bake
            (2, ref_src * 0.5 + 13000, 12), (2, ref_src * 0.2 + 13000, 10),    # Recon
            (5, ref_per_agent + 36000, 12),                                      # Breadth
            (4, 48000, 8),                                                        # Inventory (4 chunks + merge)
            (1, ref_src * 0.3 + 13000, 10),                                      # Sem. Invariants
            (3, ref_src * 0.2 + 36000, 12), (2, ref_src * 0.2 + 36000, 10),    # Depth
            (3, 28000, 6), (1, 48000, 8),                                        # RAG + Dedup
            (5, ref_src * 0.15 + 21000, 12),                                     # Verify (high)
            (4, ref_src * 0.15 + 21000, 12),                                     # Verify (med)
            (3, 48000, 8), (2, 48000, 6),                                        # Report
            (1, 23000, 25),                                                       # Orchestrator
            # L1 Thorough-only
            (1, ref_src * 0.3 + 28000, 10),                                      # Graph Sweeps
            (1, 28000, 8),                                                        # Location Recovery
            (1, ref_src * 0.2 + 28000, 10),                                      # Attention Repair
            (3, ref_src * 0.1 + 21000, 10),                                      # Verify (low)
            (4, ref_src * 0.15 + 21000, 10), (1, 18000, 4),                     # Skeptic + Judge
            (1, 28000, 8),                                                        # Cross-batch
            (1, 23000, 15),                                                       # Orch. extra
        ]
    else:
        ref_src = 5000 * 4
        ref_pct = 9.0
        ref_stages = [
            # SC Core stages
            (2, ref_src * 0.8 + 8000, 12), (2, ref_src * 0.3 + 8000, 10),    # Recon
            (4, ref_src * 0.8 + 36000, 12), (1, 48000, 8),                     # Breadth + Inventory
            (1, ref_src * 0.5 + 13000, 10),                                     # Sem. Invariants
            (2, ref_src * 0.4 + 36000, 12), (2, ref_src * 0.4 + 36000, 10),   # Depth
            (3, ref_src * 0.3 + 28000, 8), (1, ref_src * 0.3 + 28000, 8),     # Scanners + VS
            (3, ref_src * 0.3 + 21000, 8), (3, 28000, 6), (2, 48000, 8),      # Niche(3) + RAG + Chain
            (5, ref_src * 0.25 + 21000, 14),                                    # Verification
            (1, 68000, 8), (2, 48000, 8), (2, 68000, 6),                       # Report
            (1, 23000, 35),                                                      # Orchestrator (capped)
            # SC Thorough-only stages
            (3, ref_src * 0.6 + 28000, 10), (4, ref_src * 0.25 + 13000, 8),   # Re-scan + Per-contract
            (1, ref_src * 0.4 + 28000, 8), (3, ref_src * 0.2 + 21000, 8),     # Sem. Pass 2 + Depth 2-3
            (1, ref_src * 0.3 + 13000, 10),                                     # Inv. Fuzz
            (1, 28000, 8),                                                       # Design Stress
            (2, ref_src * 0.2 + 21000, 10),                                     # Extra verify
            (4, ref_src * 0.25 + 21000, 10), (1, 18000, 4),                    # Skeptic + Judge
            (1, 23000, 15),                                                      # Orch. extra
        ]

    for c, b, t in ref_stages:
        ai, _ = agent_tokens(b, t)
        ref_tokens += c * ai

    # Plan usage percentages (Claude-specific — not applicable for Codex)
    if backend == "codex":
        pct_x20 = 0.0
        pct_x5 = 0.0
        pct_pro = 0.0
    else:
        pct_x20 = (total_input / ref_tokens) * ref_pct if ref_tokens else 0
        pct_x5 = pct_x20 * 4
        pct_pro = pct_x5 * 2.5 if mode == "light" else pct_x5 * 5

    return {
        "files": total_files,
        "lines": total_lines,
        "agents": total_agents,
        "input_mtok": round(input_mtok, 1),
        "output_mtok": round(output_mtok, 1),
        "api_cost": round(api_cost, 0),
        "effective_lines": effective_lines,
        "pct_x5": round(pct_x5, 1),
        "pct_x20": round(pct_x20, 1),
        "pct_pro": round(pct_pro, 1),
        "scoped": (has_scope or bool(module_roots)) and total_files > 0,
        "backend": backend,
    }


def _shorten(path: str, maxlen: int = 50) -> str:
    return path if len(path) <= maxlen else "..." + path[-(maxlen - 3):]


def _back_separator():
    return [Separator(), {"name": "← Go back", "value": _BACK}]


def _wrap_msg(full: str, short: str = "") -> str:
    """If full exceeds _MAX_LINE, print it above and return short for InquirerPy."""
    if len(full) <= _MAX_LINE:
        return full
    sys.stdout.write(f"\n  {_C_GRAY}{full}{_RST}\n")
    sys.stdout.flush()
    return short


def _cap(s: str, n: int = 44) -> str:
    """Truncate choice name to n chars (accounts for 4-char pointer prefix)."""
    return s if len(s) <= n else s[:n - 3] + "..."


# ── Prompts ──────────────────────────────────────────────────

def select_pipeline() -> str:
    """Returns 'sc', 'l1', 'compare', or 'setup'."""
    result = inquirer.select(
        message="What are you auditing?",
        choices=[
            {"name": "Smart Contract         EVM · Solana · Soroban · Aptos · Sui",
             "value": "sc"},
            {"name": "L1 / DLT               Go or Rust node client",
             "value": "l1"},
            Separator(),
            {"name": "Compare                Diff reports",
             "value": "compare"},
            {"name": "Setup                  Install tools + build RAG DB",
             "value": "setup"},
        ],
        default="sc",
        pointer="  >",
        style=_STYLE,
        qmark="⬡",
        amark="✓",
    ).execute()
    return result


def select_audit_mode(pipeline: str) -> str:
    """Returns 'light', 'core', or 'thorough' for the given pipeline."""
    if pipeline == "sc":
        choices = [
            {"name": "Light         18-22 agents  | Pro plan  | best under 3k LOC",
             "value": "light"},
            {"name": "Core          30-50 agents  | Max plan  | ALL severities",
             "value": "core"},
            {"name": "Thorough      40-100 agents | Max plan  | ALL + fuzz",
             "value": "thorough"},
            *_back_separator(),
        ]
    else:
        choices = [
            {"name": "Light         15-20 agents  | Quick scan",
             "value": "light"},
            {"name": "Core          25-40 agents  | Standard L1 depth",
             "value": "core"},
            {"name": "Thorough      35-55 agents  | Iterative + re-scan",
             "value": "thorough"},
            *_back_separator(),
        ]
    return inquirer.select(
        message="Select audit depth:",
        choices=choices,
        default="core",
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()


def select_target() -> tuple:
    """Returns (target_path, network_or_empty). Network is always empty in interactive mode."""
    cwd = os.getcwd()
    parent = os.path.dirname(cwd)

    cwd_hint = _detect_project_hint(cwd)
    parent_hint = _detect_project_hint(parent)

    subdirs = []
    for sub in ["src", "contracts", "programs"]:
        full = os.path.join(cwd, sub)
        if os.path.isdir(full):
            n = _count_source_files(full)
            subdirs.append((sub, full, f"{n} source files" if n else "no source files"))

    choices = []

    if cwd_hint:
        cwd_label = _cap(f"Current dir   {cwd_hint}")
    else:
        cwd_label = _cap(f"Current dir   {_shorten(cwd, 28)}")
    choices.append({"name": cwd_label, "value": cwd})

    for sub, full, hint in subdirs:
        choices.append({"name": _cap(f"./{sub}/        {hint}"), "value": full})

    if parent_hint:
        parent_label = _cap(f"Parent dir    {parent_hint}")
    else:
        parent_label = _cap(f"Parent dir    {_shorten(parent, 28)}")
    choices.append({"name": parent_label, "value": parent})

    choices.append(Separator())
    choices.append({"name": "Browse...    enter a different path", "value": "__browse__"})
    choices.extend(_back_separator())

    result = inquirer.select(
        message="Target project to audit:",
        choices=choices,
        default=cwd,
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()

    if result == _BACK:
        return (_BACK, "")

    if result == "__browse__":
        path = inquirer.filepath(
            message="Path to project:",
            validate=lambda v: os.path.exists(v) or "Path not found",
            only_directories=True,
            style=_STYLE, qmark=">", amark="✓",
        ).execute()
        return (os.path.abspath(path), "")

    return (result, "")


def select_docs() -> str:
    result = inquirer.select(
        message=_wrap_msg(
            "Docs describing trust roles or permissions?",
            "Docs:"),
        choices=[
            {"name": "No docs      inferred from code",        "value": "none"},
            {"name": "Local files  whitepaper, spec, or docs", "value": "local"},
            {"name": "URL          link to docs",              "value": "url"},
            *_back_separator(),
        ],
        default="none",
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()

    if result == _BACK:
        return _BACK
    if result == "none":
        return ""

    if result == "local":
        path = inquirer.filepath(
            message="Path to docs:",
            validate=lambda v: os.path.exists(v) or "Path not found",
            only_directories=False,
            style=_STYLE, qmark=">", amark="✓",
        ).execute()
        return os.path.abspath(path)

    if result == "url":
        return inquirer.text(
            message="Docs URL:",
            validate=lambda v: v.startswith("http") or "Enter a valid URL",
            style=_STYLE, qmark=">", amark="✓",
        ).execute()

    return ""


def select_scope() -> tuple:
    """Returns (scope_file_path, scope_notes). Both can be empty."""
    result = inquirer.select(
        message="Scope constraints?",
        choices=[
            {"name": "Full project  audit everything in target", "value": "none"},
            {"name": "Scope file    scope.txt with file list",   "value": "file"},
            {"name": "Notes         describe focus areas",       "value": "notes"},
            *_back_separator(),
        ],
        default="none",
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()

    if result == _BACK:
        return (_BACK, "")
    if result == "none":
        return ("", "")

    if result == "file":
        # Try to find scope files in cwd
        cwd = os.getcwd()
        scope_files = []
        for pattern in ["scope.txt", "scope.md", "SCOPE", "scope"]:
            full = os.path.join(cwd, pattern)
            if os.path.isfile(full):
                scope_files.append(full)

        if scope_files:
            choices = [{"name": os.path.basename(f), "value": f} for f in scope_files]
            choices.append(Separator())
            choices.append({"name": "Browse...  pick a different file", "value": "__browse__"})
            chosen = inquirer.select(
                message="Select scope file:",
                choices=choices,
                pointer="  >",
                style=_STYLE, qmark=">", amark="✓",
            ).execute()
            if chosen == "__browse__":
                chosen = inquirer.filepath(
                    message="Path to scope file:",
                    validate=lambda v: os.path.exists(v) or "File not found",
                    only_directories=False,
                    style=_STYLE, qmark=">", amark="✓",
                ).execute()
            return (os.path.abspath(chosen), "")
        else:
            path = inquirer.filepath(
                message="Path to scope file:",
                validate=lambda v: os.path.exists(v) or "File not found",
                only_directories=False,
                style=_STYLE, qmark=">", amark="✓",
            ).execute()
            return (os.path.abspath(path), "")

    if result == "notes":
        notes = inquirer.text(
            message="Scope notes (e.g., 'focus on vault module, ignore governance'):",
            style=_STYLE, qmark=">", amark="✓",
        ).execute()
        return ("", notes.strip())

    return ("", "")


def select_report(message: str, allow_back: bool = True) -> str:
    """Select a markdown report file. Only .md files — PDFs cannot be diffed."""
    cwd = os.getcwd()
    report_files = []
    for f in glob.glob(os.path.join(cwd, "*.md")):
        name = os.path.basename(f)
        if any(kw in name.lower() for kw in ["audit", "report", "finding", "security"]):
            report_files.append(f)

    choices = []
    for f in report_files[:5]:
        choices.append({"name": f"  {os.path.basename(f)}", "value": f})
    if choices:
        choices.append(Separator())
    choices.append({"name": "Browse...          pick a file", "value": "__browse__"})
    if allow_back:
        choices.extend(_back_separator())

    result = inquirer.select(
        message=message,
        choices=choices,
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()

    if result == _BACK:
        return _BACK

    if result == "__browse__":
        path = inquirer.filepath(
            message="Path to report:",
            validate=lambda v: os.path.exists(v) or "File not found",
            only_directories=False,
            style=_STYLE, qmark=">", amark="✓",
        ).execute()
        return os.path.abspath(path)

    return result


def confirm_launch() -> str:
    """Returns 'launch', 'back', or 'cancel'."""
    return inquirer.select(
        message="Ready to launch?",
        choices=[
            {"name": "Launch audit",                  "value": "launch"},
            {"name": "← Go back    change selections", "value": "back"},
            {"name": "Cancel       exit",              "value": "cancel"},
        ],
        default="launch",
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()


def select_l1_tier(detected_tier: str, loc: int) -> str:
    """Select L1 audit tier. Returns 't0'/'t1'/'t2'/'t3' or _BACK."""
    choices = []
    for tid, (label, desc) in _L1_TIER_LABELS.items():
        marker = " ←" if tid == detected_tier else ""
        choices.append({"name": f"{label:22s} {desc}{marker}", "value": tid})
    choices.extend(_back_separator())
    result = inquirer.select(
        message=f"Select audit tier (detected: {detected_tier.upper()} based on {loc:,} LOC):",
        choices=choices,
        default=detected_tier,
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()
    return result


def select_l1_modules(modules: list) -> list:
    """Select modules for T1 subsystem audit. Returns list of (name, path, loc) or [_BACK]."""
    if not modules:
        return []
    choices = [
        {"name": f"{name:20s} {loc:>7,} LOC   {path}", "value": (name, path, loc)}
        for name, path, loc in modules
    ]
    result = inquirer.checkbox(
        message="Select modules to audit (space=toggle, enter=confirm, empty=back):",
        choices=choices,
        style=_STYLE,
        qmark=">",
        amark="✓",
        pointer="  >",
    ).execute()
    if not result:
        return [_BACK]
    return result


def select_l1_fork() -> str:
    """Select fork analysis mode. Returns 'diff'/'standalone'/'both' or _BACK."""
    result = inquirer.select(
        message="Fork detected. Upstream comparison?",
        choices=[
            {"name": "Diff against upstream     focus on fork-specific changes", "value": "diff"},
            {"name": "Audit as standalone       treat as independent codebase",  "value": "standalone"},
            {"name": "Both                      upstream diff + standalone",      "value": "both"},
            *_back_separator(),
        ],
        default="standalone",
        pointer="  >",
        style=_STYLE,
        qmark=">",
        amark="✓",
    ).execute()
    return result


# ── Summary ──────────────────────────────────────────────────

def show_summary(mode: str, target: str, docs: str,
                 network: str = "", scope_file: str = "", scope_notes: str = "",
                 cost_estimate: dict = None, strict: bool = False,
                 pipeline: str = "sc", language: str = "",
                 tier: str = "", modules: list = None, fork_mode: str = "",
                 backend: str = "claude"):
    w = sys.stdout.write
    bx = _C_BOX
    W = 52

    def row(label, value, value_color=_C_WHITE):
        lpad = 12 - len(label)
        vis_val = value[:W - 14] if len(value) > W - 14 else value
        vis = 2 + len(label) + lpad + len(vis_val)
        w(f"  {bx}│{_RST}  {_C_GRAY}{label}{_RST}{' ' * lpad}"
          f"{value_color}{vis_val}{_RST}{' ' * max(0, W - vis)}{bx}│{_RST}\n")

    console.print()
    console.print(Rule(style="color(238)"))
    w("\n")
    w(f"  {bx}╭{'─' * W}╮{_RST}\n")

    # Header
    vis = 17  # "  Launch Summary" length
    w(f"  {bx}│{_RST}  {_BOLD}{_C_WHITE}Launch Summary{_RST}"
      f"{' ' * (W - vis)}{bx}│{_RST}\n")

    # Blank line
    w(f"  {bx}│{_RST}{' ' * W}{bx}│{_RST}\n")

    mode_table = L1_MODES if pipeline == "l1" else MODES
    mode_label = mode_table.get(mode, {}).get("label", mode)
    row("Pipeline", "L1 Infrastructure" if pipeline == "l1" else "Smart Contract", _C_ORANGE)
    row("Mode", mode_label, _C_ORANGE)
    if backend == "codex":
        row("Backend", "Codex CLI (OpenAI)", _C_ORANGE)
    row("AI Model", _wizard_model_summary(backend, mode), _C_ORANGE)
    row("Target", target)
    if language:
        row("Language", language.upper())
    if tier:
        tl = _L1_TIER_LABELS.get(tier, (tier, ""))[0]
        row("Tier", tl)
    if modules:
        names = ", ".join(m[0] for m in modules[:5])
        if len(modules) > 5:
            names += f" +{len(modules) - 5} more"
        row("Modules", names)
    if fork_mode and fork_mode != "standalone":
        row("Fork", fork_mode)
    if network:
        row("Network", NETWORKS.get(network, network))
    row("Docs", docs if docs else "none", _C_WHITE if docs else _C_DARK_GRAY)
    if scope_file:
        row("Scope", os.path.basename(scope_file))
    if scope_notes:
        row("Notes", scope_notes)
    if strict:
        row("Proven-only", "ON — unproven findings capped at Low", _C_ORANGE)

    if cost_estimate:
        lines = cost_estimate["lines"]
        agents = cost_estimate["agents"]
        scoped = cost_estimate.get("scoped", False)
        scope_tag = " (scoped)" if scoped else ""
        row("Codebase", f"~{lines:,} lines, {cost_estimate['files']} files{scope_tag}")
        row("Agents", f"~{agents}")
        row("Tokens", f"~{cost_estimate['input_mtok']}M in / ~{cost_estimate['output_mtok']}M out")
        api = cost_estimate.get("api_cost", 0)
        est_backend = cost_estimate.get("backend", backend)
        if est_backend == "codex":
            row("API equiv", f"~${api:.0f} USD (OpenAI)", _C_WHITE)
            row("Weekly", "N/A - Codex/OpenAI quotas differ", _C_DARK_GRAY)
        else:
            row("API cost", f"~${api:.0f} USD", _C_WHITE)
            pct_pro = cost_estimate.get("pct_pro", 0)
            pct5 = cost_estimate["pct_x5"]
            pct20 = cost_estimate["pct_x20"]
            if pct_pro > 0:
                color_pro = _C_RED if pct_pro > 80 else (_C_ORANGE if pct_pro > 40 else _C_GREEN)
                row("Pro", f"~{pct_pro:.0f}% of weekly allowance", color_pro)
            if pct5 > 0:
                color5 = _C_RED if pct5 > 80 else (_C_ORANGE if pct5 > 40 else _C_GREEN)
                color20 = _C_RED if pct20 > 80 else (_C_ORANGE if pct20 > 40 else _C_GREEN)
                row("Max x5", f"~{pct5:.0f}% of weekly allowance", color5)
                row("Max x20", f"~{pct20:.0f}% of weekly allowance", color20)

    w(f"  {bx}╰{'─' * W}╯{_RST}\n")

    if cost_estimate:
        w(f"  {_C_DARK_GRAY}Rough estimates only. Actual usage varies with protocol{_RST}\n")
        if cost_estimate.get("backend", backend) == "codex":
            w(f"  {_C_DARK_GRAY}Codex shows API-equivalent spend; weekly plan percent is not{_RST}\n")
            w(f"  {_C_DARK_GRAY}comparable to Claude Max/Pro. Run /cost after for actuals.{_RST}\n")
        else:
            w(f"  {_C_DARK_GRAY}complexity and findings count. Run /cost after for actuals.{_RST}\n")

    w("\n")
    sys.stdout.flush()


# ── Resume Detection ─────────────────────────────────────────

def _resolve_resume_progress(config: dict, completed: list[str]) -> dict:
    """Return last completed phase and the next active phase to run."""
    last_phase = completed[-1] if completed else "(not started)"
    pipeline = str(config.get("pipeline", "sc")).lower()
    mode = str(config.get("mode", "core")).lower()
    next_phase = "(complete)"

    try:
        scripts_dir = os.path.join(PLAMEN_HOME, "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from plamen_types import L1_PHASES, SC_PHASES  # type: ignore

        phases = L1_PHASES if pipeline == "l1" else SC_PHASES
        completed_set = set(completed or [])
        for phase in phases:
            if mode not in getattr(phase, "modes", ()):
                continue
            if phase.name not in completed_set:
                next_phase = phase.name
                break
    except Exception:
        fallback = {
            "sc": [
                "recon", "instantiate", "breadth", "rescan",
                "inventory_prepare", "inventory_chunk_a", "inventory_chunk_b",
                "inventory_chunk_c", "inventory", "invariants", "depth",
                "attention_repair", "rag_sweep", "sc_semantic_dedup",
                "chain", "chain_agent2", "sc_verify_queue",
                "sc_verify_crithigh", "sc_verify_high_b", "sc_verify_high_c",
                "sc_verify_high_d", "sc_verify_medium_a",
                "sc_verify_medium_b", "sc_verify_medium_c",
                "sc_verify_medium_d", "sc_verify_low_a", "sc_verify_low_b",
                "sc_verify_aggregate", "skeptic", "crossbatch",
                "report_index", "report_body_writer_critical_high",
                "report_body_writer_medium", "report_body_writer_low_info",
                "report_critical_high", "report_critical_high_merge",
                "report_medium", "report_medium_merge", "report_low_info",
                "report_low_info_merge", "report_assemble",
            ],
            "l1": [
                "bake", "recon", "breadth", "graph_sweeps",
                "inventory_prepare", "inventory_chunk_a", "inventory_chunk_b",
                "inventory_chunk_c", "inventory", "location_recovery",
                "invariants", "depth", "attention_repair", "rag_sweep",
                "chain", "chain_agent2", "semantic_dedup", "verify_queue",
                "verify_aggregate", "crossbatch", "skeptic", "report_index",
                "report_body_writer_critical_high",
                "report_body_writer_medium", "report_body_writer_low_info",
                "report_critical_high", "report_critical_high_merge",
                "report_medium", "report_medium_merge", "report_low_info",
                "report_low_info_merge", "report_assemble",
            ],
        }.get(pipeline, [])
        completed_set = set(completed or [])
        for name in fallback:
            if name not in completed_set:
                next_phase = name
                break

    return {
        "last_phase": last_phase,
        "next_phase": next_phase,
        "phases_done": len(completed or []),
    }


def _find_existing_audit(cwd: str = "") -> "dict | None":
    """Look for an existing audit scratchpad relative to cwd.

    Primary: .scratchpad/config.json (normal path).
    Fallback: .scratchpad exists with checkpoint or artifacts but config.json
    is missing (e.g. crash/interrupt deleted it). Tries to reconstruct config
    from the checkpoint's embedded copy (v2.6.2+) or from prompt snapshots.

    Returns dict with keys: config_path, scratchpad, mode, pipeline,
    language, target, last_phase, phases_done.
    Extra key 'config_missing' = True when config.json was reconstructed
    or is unrecoverable.
    Returns None if no existing audit found.
    """
    import json as _json

    if not cwd:
        cwd = os.getcwd()

    scratchpad_dirs = [
        os.path.join(cwd, ".scratchpad"),
        os.path.join(cwd, "src", ".scratchpad"),
        os.path.join(cwd, "contracts", ".scratchpad"),
    ]

    for scratchpad in scratchpad_dirs:
        config_path = os.path.join(scratchpad, "config.json")
        checkpoint_path = os.path.join(scratchpad, "_v2_checkpoint.json")

        # ── Primary: config.json exists ──
        if os.path.isfile(config_path):
            try:
                with open(config_path, encoding="utf-8") as f:
                    config = _json.load(f)
            except Exception:
                continue

            completed = []
            if os.path.isfile(checkpoint_path):
                try:
                    with open(checkpoint_path, encoding="utf-8") as f:
                        cp = _json.load(f)
                    completed = cp.get("completed", [])
                except Exception:
                    pass
            progress = _resolve_resume_progress(config, completed)

            return {
                "config_path": config_path,
                "scratchpad": scratchpad,
                "mode": config.get("mode", "?"),
                "pipeline": config.get("pipeline", "?"),
                "language": config.get("language", "?"),
                "target": config.get("project_root", "?"),
                "last_phase": progress["last_phase"],
                "next_phase": progress["next_phase"],
                "phases_done": progress["phases_done"],
            }

        # ── Fallback: scratchpad dir exists but config.json is missing ──
        if not os.path.isdir(scratchpad):
            continue

        # Need checkpoint or at least some real artifacts
        has_checkpoint = os.path.isfile(checkpoint_path)
        try:
            artifact_files = [
                f for f in os.listdir(scratchpad)
                if not f.startswith("_") and f.endswith(".md")
                and os.path.isfile(os.path.join(scratchpad, f))
            ]
        except Exception:
            artifact_files = []

        if not has_checkpoint and not artifact_files:
            continue

        completed = []
        recovered_config = None

        if has_checkpoint:
            try:
                with open(checkpoint_path, encoding="utf-8") as f:
                    cp = _json.load(f)
                completed = cp.get("completed", [])
                recovered_config = cp.get("config")
                if recovered_config and not isinstance(recovered_config, dict):
                    recovered_config = None
            except Exception:
                pass

        # Try to reconstruct config from checkpoint's embedded copy
        if recovered_config:
            try:
                with open(config_path, "w", encoding="utf-8") as f:
                    _json.dump(recovered_config, f, indent=2)
            except Exception:
                pass
            progress = _resolve_resume_progress(recovered_config, completed)
            return {
                "config_path": config_path,
                "scratchpad": scratchpad,
                "mode": recovered_config.get("mode", "?"),
                "pipeline": recovered_config.get("pipeline", "?"),
                "language": recovered_config.get("language", "?"),
                "target": recovered_config.get("project_root", "?"),
                "last_phase": progress["last_phase"],
                "next_phase": progress["next_phase"],
                "phases_done": progress["phases_done"],
                "recovered": True,
            }

        # Last resort: scratchpad has artifacts but no recoverable config.
        # We know an audit happened but can't reconstruct its settings.
        return {
            "config_path": None,
            "scratchpad": scratchpad,
            "mode": "?",
            "pipeline": "?",
            "language": "?",
            "target": os.path.dirname(scratchpad),
            "last_phase": completed[-1] if completed else "(unknown)",
            "next_phase": "(unknown)",
            "phases_done": len(completed),
            "config_missing": True,
        }

    return None


def _resume_audit_prompt(info: dict) -> str:
    """Show existing audit info and ask user what to do.

    Returns: 'resume', 'fresh', or 'new'.
    """
    config_missing = info.get("config_missing", False)
    recovered = info.get("recovered", False)

    w = sys.stdout.write
    w(f"\n  {_C_ORANGE}⬡ Existing audit detected{_RST}\n\n")
    w(f"    {_C_GRAY}Target:    {_RST}{info['target']}\n")
    w(f"    {_C_GRAY}Pipeline:  {_RST}{info['pipeline'].upper()} {info['mode']}\n")
    w(f"    {_C_GRAY}Language:  {_RST}{info['language']}\n")
    next_phase = info.get("next_phase", "(unknown)")
    w(f"    {_C_GRAY}Progress:  {_RST}{info['phases_done']} phases done, last = {info['last_phase']}, next = {next_phase}\n")

    if config_missing:
        w(f"    {_C_GRAY}Config:    {_RST}{_C_RED}missing (not recoverable){_RST}\n")
        w(f"\n    {_C_ORANGE}Config was lost and could not be reconstructed from checkpoint.{_RST}\n")
        w(f"    {_C_ORANGE}Scratchpad artifacts exist at: {info['scratchpad']}{_RST}\n\n")
    elif recovered:
        w(f"    {_C_GRAY}Config:    {_RST}{info['config_path']} {_C_GREEN}(recovered from checkpoint){_RST}\n\n")
    else:
        w(f"    {_C_GRAY}Config:    {_RST}{info['config_path']}\n\n")
    sys.stdout.flush()

    if config_missing:
        choices = [
            {"name": "Clean up (wipe scratchpad, configure from scratch)",
             "value": "new"},
            Separator(),
            {"name": "Cancel   (leave scratchpad intact, exit)",
             "value": "cancel"},
        ]
        default = "new"
    else:
        choices = [
            {"name": f"Resume  (next: {next_phase})",
             "value": "resume"},
            {"name": "Fresh   (wipe scratchpad, restart same config)",
             "value": "fresh"},
            Separator(),
            {"name": "New     (ignore existing, configure from scratch)",
             "value": "new"},
        ]
        default = "resume"

    result = inquirer.select(
        message="What would you like to do?",
        choices=choices,
        default=default,
        pointer="  >",
        style=_STYLE,
        qmark="⬡",
        amark="✓",
    ).execute()
    return result


def resume_v2(config_path: str, fresh: bool = False):
    """Resume (or fresh-restart) an existing audit via the driver."""
    driver = os.path.join(PLAMEN_HOME, "scripts", "plamen_driver.py")
    if not os.path.isfile(driver):
        sys.stdout.write(f"  {_C_RED}✗ Driver not found: {driver}{_RST}\n")
        sys.exit(1)

    import json as _json
    with open(config_path, encoding="utf-8") as f:
        config = _json.load(f)
    target = config.get("project_root", "")
    pipeline = config.get("pipeline", "sc").upper()
    mode = config.get("mode", "core")

    console.print(Rule(style="color(238)"))
    w = sys.stdout.write
    action = "Fresh restart" if fresh else "Resuming"
    w(f"\n  {_BOLD}{_C_WHITE}{action} audit driver ({pipeline} {mode})...{_RST}\n\n")
    sys.stdout.flush()

    cmd = [sys.executable, driver]
    if fresh:
        cmd.append("--fresh")
    cmd.append(config_path)

    if sys.platform == "win32":
        os.system("")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        report = os.path.join(target, "AUDIT_REPORT.md")
        w(f"\n  {_C_GREEN}✓ Pipeline completed.{_RST}\n")
        if os.path.isfile(report):
            w(f"  {_C_WHITE}Report: {report}{_RST}\n")
    elif result.returncode == 2:
        w(f"\n  {_C_ORANGE}⏸ Pipeline paused — rate limit or usage cap reached.{_RST}\n")
        w(f"  {_C_GRAY}Resume: run plamen again from the same directory.{_RST}\n")
    else:
        w(f"\n  {_C_RED}✗ Pipeline stopped with errors.{_RST}\n")
        w(f"  {_C_GRAY}Resume: run plamen again from the same directory.{_RST}\n")

    sys.exit(result.returncode)


# ── Launch ───────────────────────────────────────────────────

def launch_v2(pipeline: str, mode: str, target: str, language: str,
              docs: str = "", scope_file: str = "", scope_notes: str = "",
              proven_only: bool = False, tier: str = "",
              subsystem_scope: str = "", fork_mode: str = "standalone",
              cli_backend: str = ""):
    """Write config.json and run plamen_driver.py (deterministic driver)."""
    import json as _json

    target = os.path.abspath(target)
    scratchpad = os.path.join(target, ".scratchpad")
    os.makedirs(scratchpad, exist_ok=True)

    if not cli_backend:
        detected_backends = _detect_cli_backends()
        cli_backend = (
            detected_backends[0]
            if len(detected_backends) == 1
            else _ambient_backend(detected_backends)
        )

    config = {
        "project_root": target,
        "scratchpad": scratchpad,
        "mode": mode,
        "pipeline": pipeline,
        "language": language,
        "cli_backend": cli_backend,
    }

    if pipeline == "sc":
        config["docs_path"] = docs
        config["scope_file"] = scope_file
        config["scope_notes"] = scope_notes
        config["proven_only"] = proven_only
    elif pipeline == "l1":
        config["tier"] = tier
        config["subsystem_scope"] = subsystem_scope
        config["fork_mode"] = fork_mode
        config["docs_path"] = docs

    config_path = os.path.join(scratchpad, "config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        _json.dump(config, f, indent=2)

    driver = os.path.join(PLAMEN_HOME, "scripts", "plamen_driver.py")
    if not os.path.isfile(driver):
        sys.stdout.write(f"  {_C_RED}✗ Driver not found: {driver}{_RST}\n")
        sys.exit(1)

    console.print(Rule(style="color(238)"))
    w = sys.stdout.write
    w(f"\n  {_BOLD}{_C_WHITE}Launching audit driver ({pipeline.upper()} mode)...{_RST}\n\n")
    w(f"  {_C_GRAY}Config: {config_path}{_RST}\n")
    w(f"  {_C_GRAY}If interrupted, resume with:{_RST}\n")
    w(f"  {_C_WHITE}  python {driver} \"{config_path}\"{_RST}\n\n")
    sys.stdout.flush()

    if sys.platform == "win32":
        os.system("")
    result = subprocess.run([sys.executable, driver, config_path])

    if result.returncode == 0:
        report = os.path.join(target, "AUDIT_REPORT.md")
        w(f"\n  {_C_GREEN}✓ Pipeline completed.{_RST}\n")
        if os.path.isfile(report):
            w(f"  {_C_WHITE}Report: {report}{_RST}\n")
    elif result.returncode == 2:
        w(f"\n  {_C_ORANGE}⏸ Pipeline paused — rate limit or usage cap reached.{_RST}\n")
        w(f"  {_C_GRAY}Resume when usage resets:{_RST}\n")
        w(f"  {_C_WHITE}  python {driver} \"{config_path}\"{_RST}\n")
    else:
        violations = os.path.join(scratchpad, "violations.md")
        w(f"\n  {_C_RED}✗ Pipeline stopped with errors.{_RST}\n")
        if os.path.isfile(violations):
            w(f"  {_C_GRAY}Check: {violations}{_RST}\n")
        w(f"  {_C_GRAY}Resume (re-attempts failed phases):{_RST}\n")
        w(f"  {_C_WHITE}  python {driver} \"{config_path}\"{_RST}\n")

    sys.exit(result.returncode)


def launch_claude(mode: str, target: str, docs: str,
                  network: str = "", scope_file: str = "", scope_notes: str = "",
                  **kwargs):
    """Launch 'compare' mode — runs /plamen compare in a Claude Code session."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        sys.stdout.write(f"  {_C_RED}✗ 'claude' not found in PATH{_RST}\n")
        sys.exit(1)

    parts = ["/plamen compare"]
    if target:
        parts.append(f"report: {target}")
    if docs:
        parts.append(f"ground_truth: {docs}")
    prompt = " ".join(parts)

    console.print(Rule(style="color(238)"))
    w = sys.stdout.write
    w(f"\n  {_BOLD}{_C_WHITE}Launching Claude Code...{_RST}\n\n")
    sys.stdout.flush()

    if sys.platform == "win32":
        os.system("")
    result = subprocess.run([claude_bin, prompt])
    sys.exit(result.returncode)


# ── Main: state machine with back support ────────────────────

def _parse_cli_opts() -> dict:
    """Parse --key VALUE and --flag options from sys.argv into a dict."""
    opts = {"docs": "", "network": "", "scope_file": "", "scope_notes": "",
            "proven_only": False, "tier": "", "modules": "",
            "cli_backend": ""}
    for i, a in enumerate(sys.argv):
        if a == "--docs" and i + 1 < len(sys.argv):
            opts["docs"] = sys.argv[i + 1]
        if a == "--network" and i + 1 < len(sys.argv):
            opts["network"] = sys.argv[i + 1]
        if a == "--scope" and i + 1 < len(sys.argv):
            opts["scope_file"] = sys.argv[i + 1]
        if a == "--notes" and i + 1 < len(sys.argv):
            opts["scope_notes"] = sys.argv[i + 1]
        if a in ("--proven-only", "--strict"):
            opts["proven_only"] = True
        if a == "--tier" and i + 1 < len(sys.argv):
            opts["tier"] = sys.argv[i + 1].lower()
        if a == "--modules" and i + 1 < len(sys.argv):
            opts["modules"] = sys.argv[i + 1]
        if a == "--codex":
            opts["cli_backend"] = "codex"
        if a == "--claude":
            opts["cli_backend"] = "claude"
    return opts


def main():
    # Fast path: CLI args skip the interactive UI
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()

        # ── Help ──────────────────────────────────────────────
        if arg in ("help", "--help", "-h"):
            w = sys.stdout.write
            show_banner()
            w(f"  {_C_WHITE}Usage:{_RST}\n")
            w(f"    {_C_ORANGE}plamen{_RST}                              Interactive wizard\n")
            w(f"\n  {_C_WHITE}Smart Contract (auto-detect language):{_RST}\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}core{_RST} /path/to/project        SC audit in Core mode\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}thorough{_RST} /path/to/project    SC audit in Thorough mode\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}light{_RST} /path/to/project       SC audit in Light mode\n")
            w(f"\n  {_C_WHITE}L1 Infrastructure:{_RST}\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}l1 core{_RST} /path/to/project     L1 audit in Core mode\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}l1 thorough{_RST} /path             L1 audit in Thorough mode\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}l1 light{_RST} /path                L1 audit in Light mode\n")
            w(f"\n  {_C_WHITE}Other:{_RST}\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}resume{_RST}                       Resume interrupted audit\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}resume{_RST} path/config.json      Resume specific config\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}compare{_RST}                      Diff reports\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}install{_RST}                      Non-interactive install (symlinks + config)\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}setup{_RST}                        Install + interactive toolchain wizard + RAG\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}migrate{_RST}                      Migrate v1.x install (~/.claude) to v2.x (~/.plamen)\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}doctor{_RST}                       Verify install (no audit run, no API calls)\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}rag{_RST}                          Rebuild RAG database only\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}uninstall{_RST}                    Remove from ~/.claude\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}install --codex{_RST}             Install Codex adapter\n")
            w(f"\n  {_C_WHITE}Options:{_RST}\n")
            w(f"    {_C_GRAY}--docs{_RST} PATH              Whitepaper or spec file\n")
            w(f"    {_C_GRAY}--scope{_RST} PATH             Scope file listing contracts\n")
            w(f"    {_C_GRAY}--notes{_RST} TEXT             Scope notes (free text)\n")
            w(f"    {_C_GRAY}--network{_RST} NAME           Target network (SC only)\n")
            w(f"    {_C_GRAY}--proven-only{_RST}            Cap unproven findings at Low (SC only)\n")
            w(f"    {_C_GRAY}--tier{_RST} T0|T1|T2|T3      L1 tier override\n")
            w(f"    {_C_GRAY}--modules{_RST} a,b,c          L1 T1 module selection\n")
            w(f"    {_C_GRAY}--codex{_RST}                  Use Codex CLI backend\n")
            w(f"    {_C_GRAY}--claude{_RST}                 Use Claude Code backend (default)\n")
            w(f"\n")
            return

        # ── Estimate subcommand (for /plamen command) ────────
        if arg == "--estimate":
            import json as _json
            est_target = sys.argv[2] if len(sys.argv) > 2 else "."
            est_mode = sys.argv[3] if len(sys.argv) > 3 else "core"
            est_pipeline = "sc"
            est_scope = ""
            est_notes = ""
            for i, a in enumerate(sys.argv):
                if a == "--scope" and i + 1 < len(sys.argv):
                    est_scope = sys.argv[i + 1]
                if a == "--scope-notes" and i + 1 < len(sys.argv):
                    est_notes = sys.argv[i + 1]
                if a == "--l1":
                    est_pipeline = "l1"
            r = estimate_cost(est_target, est_mode, est_scope, est_notes,
                              pipeline=est_pipeline)
            print(_json.dumps(r))
            return

        # ── Install / setup / migrate / uninstall subcommands ─
        # `install` is non-interactive (symlinks + config + Python deps + hook
        # self-heal). Safe in Claude Code Bash, Codex shell, and CI. Exits 0.
        # `setup` runs install, then the interactive toolchain checkbox + RAG.
        # `--codex` runs only the Codex adapter generator (non-interactive).
        if arg in ("install", "setup"):
            if "--codex" in sys.argv:
                show_banner()
                w = sys.stdout.write
                console.print(Rule(title="Codex Adapter", style="color(238)"))
                _install_codex_adapter(w)
                return
            show_banner()
            if arg == "install":
                run_install()
            else:
                run_setup()
            return

        if arg == "uninstall":
            show_banner()
            run_uninstall()
            return

        if arg == "migrate":
            run_migrate()
            return

        if arg in ("doctor", "verify", "check"):
            rc = run_doctor()
            sys.exit(rc)

        if arg == "resume":
            show_banner()
            # Accept explicit config path or auto-detect
            config_path = sys.argv[2] if len(sys.argv) > 2 else None
            if config_path and os.path.isfile(config_path):
                resume_v2(config_path, fresh=False)
            else:
                existing = _find_existing_audit(config_path or "")
                if existing and existing.get("config_path"):
                    resume_v2(existing["config_path"], fresh=False)
                elif existing and existing.get("config_missing"):
                    sys.stdout.write(
                        f"  {_C_RED}✗ Audit found but config.json is missing and"
                        f" could not be recovered.{_RST}\n"
                        f"  {_C_GRAY}Scratchpad: {existing['scratchpad']}{_RST}\n"
                        f"  {_C_GRAY}Run interactively to clean up or start fresh.{_RST}\n"
                    )
                    sys.exit(1)
                else:
                    sys.stdout.write(f"  {_C_RED}✗ No existing audit found to resume.{_RST}\n")
                    sys.exit(1)
            return

        if arg == "rag":
            show_banner()
            w = sys.stdout.write
            w(f"\n  {_BOLD}{_C_WHITE}Building RAG vulnerability database...{_RST}\n\n")
            sys.stdout.flush()
            _build_rag_db(w)
            return

        # ── L1 CLI: plamen l1 <mode> [path] [--opts] ─────────
        if arg == "l1":
            _check_claude_md_version()
            l1_mode = sys.argv[2].lower() if len(sys.argv) > 2 else "core"
            if l1_mode not in ("light", "core", "thorough"):
                sys.stdout.write(f"  {_C_RED}Unknown L1 mode: {l1_mode}{_RST}\n")
                sys.exit(1)
            target = ""
            for a in sys.argv[3:]:
                if not a.startswith("--"):
                    target = a
                    break
            if not target:
                show_banner()
                target, _ = select_target()
            target = os.path.abspath(target)
            opts = _parse_cli_opts()
            language = _detect_language(target)
            if language not in ("go", "rust"):
                language = "go"  # default for L1
            exts = {".go"} if language == "go" else {".rs"}
            loc = _count_loc(target, exts)
            tier = opts["tier"] or _detect_l1_tier(loc)
            subsystem_scope = ""
            if tier == "t1" and opts["modules"]:
                subsystem_scope = opts["modules"]
            elif tier == "t1":
                modules = _scan_modules(target, language)
                if modules:
                    selected = select_l1_modules(modules)
                    subsystem_scope = ",".join(m[1] for m in selected)
            fork_mode = "standalone"
            if _detect_fork(target):
                fork_mode = select_l1_fork()
            launch_v2("l1", l1_mode, target, language,
                       docs=opts["docs"], tier=tier,
                       subsystem_scope=subsystem_scope, fork_mode=fork_mode,
                       cli_backend=opts["cli_backend"])
            return

        # ── SC CLI: plamen <mode> [path] [--opts] ─────────────
        if arg in ("light", "core", "thorough"):
            _check_claude_md_version()
            target = ""
            for a in sys.argv[2:]:
                if not a.startswith("--"):
                    target = a
                    break
            if not target:
                show_banner()
                target, _ = select_target()
            target = os.path.abspath(target)
            opts = _parse_cli_opts()
            language = _detect_language(target)
            if language in ("go", "rust"):
                language = "evm"  # SC mode, force SC language
            launch_v2("sc", arg, target, language,
                       docs=opts["docs"], scope_file=opts["scope_file"],
                       scope_notes=opts["scope_notes"],
                       proven_only=opts["proven_only"],
                       cli_backend=opts["cli_backend"])
            return

        # ── Compare ──────────────────────────────────────────
        if arg == "compare":
            _check_claude_md_version()
            target = sys.argv[2] if len(sys.argv) > 2 else ""
            docs = ""
            for i, a in enumerate(sys.argv):
                if a == "--docs" and i + 1 < len(sys.argv):
                    docs = sys.argv[i + 1]
            launch_claude("compare", target, docs)
            return

    # ── Interactive flow (state machine) ─────────────────────
    show_banner()
    _check_claude_md_version()

    # ── Resume detection: check for existing audit before anything else ──
    existing = _find_existing_audit()
    if existing:
        decision = _resume_audit_prompt(existing)
        if decision == "cancel":
            sys.stdout.write(f"\n  {_C_GRAY}Exiting. Scratchpad left intact.{_RST}\n")
            return
        elif decision == "resume":
            resume_v2(existing["config_path"], fresh=False)
            return
        elif decision == "fresh":
            resume_v2(existing["config_path"], fresh=True)
            return
        else:
            # "new" → wipe old scratchpad, then fall through to config wizard
            old_sp = existing["scratchpad"]
            if os.path.isdir(old_sp):
                shutil.rmtree(old_sp, ignore_errors=True)
            sys.stdout.write(
                f"\n  {_C_GREEN}✓{_RST} Previous audit cleared."
                f"  {_C_GRAY}Configuring new audit...{_RST}\n\n"
            )
            sys.stdout.flush()

    show_hint_panel()

    if not _quick_check_required():
        check_dependencies()
        sys.stdout.write(f"  {_C_RED}Cannot proceed without required tools.{_RST}\n")
        sys.stdout.write(
            f"  {_C_GRAY}Install Claude Code or Codex CLI, plus python, npm, and git, then retry.{_RST}\n"
        )
        sys.exit(1)

    # ── Non-TTY guard for the interactive wizard ─────────────
    # `plamen` (no args), `plamen compare`, and any path that falls into
    # the InquirerPy-driven wizard need a controlling terminal. Without
    # one, `inquirer.select(...).execute()` crashes inside
    # prompt_toolkit/input/vt100.py with
    # `OSError: [Errno 22] Invalid argument`. Catch it here with an
    # actionable message instead of a traceback.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        show_banner()
        w = sys.stdout.write
        w(f"\n  {_C_ORANGE}!{_RST} Plamen wizard needs a real terminal.\n")
        w(f"    {_C_GRAY}Detected non-TTY (Claude Code Bash / Codex shell / CI / piped stdio).{_RST}\n\n")
        w(f"  {_C_WHITE}Choose one:{_RST}\n")
        w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}install{_RST}                    Non-interactive install (safe here)\n")
        w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}core{_RST} /path/to/project      Skip wizard with explicit mode + path\n")
        w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}thorough{_RST} /path/to/project  Same, Thorough mode\n")
        w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}light{_RST} /path/to/project     Same, Light mode\n")
        w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}migrate{_RST}                    Migrate v1.x install layout\n")
        w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}help{_RST}                       Full subcommand list\n\n")
        w(f"  {_C_GRAY}Or open a real terminal and run `plamen` again.{_RST}\n\n")
        return

    pipeline = mode = target = docs = network = scope_file = scope_notes = ""
    language = tier = fork_mode = subsystem_scope = ""
    cli_backend = ""
    is_fork = False
    l1_modules = []
    report = ground_truth = ""
    strict = False
    step = 0

    # Helper: build breadcrumbs from current state for clear+rebanner
    def _sc_crumbs_to(step_id):
        """Breadcrumbs for SC flow up to (not including) step_id."""
        c = [("Pipeline", "Smart Contract"), ("Mode", mode)]
        if step_id > 1:
            c.append(("Target", _shorten(target, 40)))
        if step_id > 2:
            c.append(("Docs", docs if docs else "none"))
        if step_id > 3:
            sc = scope_file or scope_notes or "full project"
            c.append(("Scope", _shorten(sc, 40) if scope_file else sc))
        if step_id > 35:
            c.append(("Proven-only", "yes" if strict else "no"))
        return c

    def _l1_crumbs_to(step_id):
        """Breadcrumbs for L1 flow up to (not including) step_id."""
        c = [("Pipeline", "L1 Infrastructure"), ("Mode", mode)]
        if step_id > 1:
            c.append(("Target", f"{_shorten(target, 30)} ({language.upper()}, {loc:,} LOC)"))
        if step_id > 12:
            tl = _L1_TIER_LABELS.get(tier, (tier, ""))[0]
            c.append(("Tier", tl))
        if step_id > 13 and tier == "t1" and l1_modules:
            names = ", ".join(m[0] for m in l1_modules[:4])
            c.append(("Modules", names))
        if step_id > 14:
            c.append(("Fork", fork_mode))
        if step_id > 15:
            c.append(("Docs", docs if docs else "none"))
        return c

    def _cmp_crumbs_to(step_id):
        """Breadcrumbs for Compare flow."""
        c = [("Mode", "Compare")]
        if step_id > 1:
            c.append(("Report", _shorten(report, 40)))
        if step_id > 2:
            c.append(("Ground truth", _shorten(ground_truth, 40)))
        return c

    loc = 0  # needed by _l1_crumbs_to before L1 step 1 sets it
    detected_tier = "t2"

    while True:
        # ── Step 0: Pipeline selection ────────────────────────
        if step == 0:
            pipeline = select_pipeline()
            sys.stdout.write("\n"); sys.stdout.flush()
            if pipeline == "setup":
                run_setup()
                step = 0; continue
            if pipeline == "compare":
                mode = "compare"
                step = 1; continue
            step = 5; continue

        # ── Step 0.5: Audit depth selection ──────────────────
        if step == 5:
            mode = select_audit_mode(pipeline)
            if mode == _BACK:
                _clear_and_rebanner()
                step = 0; continue
            sys.stdout.write("\n"); sys.stdout.flush()
            step = 6; continue

        # ── Step 6: Backend selection (only if multiple runtimes exist) ──
        if step == 6:
            detected_backends = _detect_cli_backends()
            if _skip_backend_prompt() or len(detected_backends) <= 1:
                cli_backend = (
                    detected_backends[0]
                    if len(detected_backends) == 1
                    else _ambient_backend(detected_backends)
                )
                step = 1; continue
            choices = []
            if "claude" in detected_backends:
                choices.append({
                    "name": "Claude Code    Anthropic Claude",
                    "value": "claude",
                })
            if "codex" in detected_backends:
                choices.append({
                    "name": "Codex CLI      OpenAI GPT-5.5 / GPT-5.4-mini",
                    "value": "codex",
                })
            choices.extend(_back_separator())
            result = inquirer.select(
                message="AI runtime?",
                choices=choices,
                default=_ambient_backend(detected_backends),
                pointer="  >",
                style=_STYLE,
                qmark=">",
                amark="✓",
            ).execute()
            if result == _BACK:
                _clear_and_rebanner()
                step = 5; continue
            cli_backend = result
            sys.stdout.write("\n"); sys.stdout.flush()
            step = 1; continue

        # ── SC audit flow ────────────────────────────────────
        if pipeline == "sc" and mode in ("light", "core", "thorough"):
            if step == 1:
                result = select_target()
                if result[0] == _BACK:
                    _clear_and_rebanner()
                    step = 5; continue
                target, network = result
                language = _detect_language(target)
                if language in ("go", "rust"):
                    language = "evm"
                sys.stdout.write(f"  {_C_GRAY}Detected: {language.upper()}{_RST}\n")
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 2; continue

            if step == 2:
                result = select_docs()
                if result == _BACK:
                    _crumb_set(_sc_crumbs_to(1))
                    _clear_and_rebanner()
                    step = 1; continue
                docs = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 3; continue

            if step == 3:
                result = select_scope()
                if result[0] == _BACK:
                    _crumb_set(_sc_crumbs_to(2))
                    _clear_and_rebanner()
                    step = 2; continue
                scope_file, scope_notes = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 35; continue

            if step == 35:
                result = inquirer.select(
                    message="Proven-only mode?",
                    choices=[
                        {"name": "No        standard severity rules",          "value": False},
                        {"name": "Yes       unproven findings capped at Low",  "value": True},
                        *_back_separator(),
                    ],
                    default=False,
                    pointer="  >",
                    style=_STYLE,
                    qmark=">",
                    amark="✓",
                ).execute()
                if result == _BACK:
                    _crumb_set(_sc_crumbs_to(3))
                    _clear_and_rebanner()
                    step = 3; continue
                strict = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 4; continue

            if step == 4:
                cost_est = None
                if os.path.isdir(target):
                    cost_est = estimate_cost(target, mode, scope_file, scope_notes,
                                            backend=cli_backend or "claude")
                show_summary(mode, target, docs, network, scope_file, scope_notes, cost_est,
                             strict=strict, pipeline="sc", language=language,
                             backend=cli_backend or "claude")
                decision = confirm_launch()
                if decision == "back":
                    _crumb_set(_sc_crumbs_to(35))
                    _clear_and_rebanner()
                    step = 35; continue
                if decision == "cancel":
                    sys.stdout.write(f"  {_C_DARK_GRAY}Cancelled.{_RST}\n")
                    return
                launch_v2("sc", mode, target, language,
                          docs=docs, scope_file=scope_file,
                          scope_notes=scope_notes, proven_only=strict,
                          cli_backend=cli_backend)
                return

        # ── L1 audit flow ────────────────────────────────────
        if pipeline == "l1" and mode in ("light", "core", "thorough"):
            if step == 1:
                result = select_target()
                if result[0] == _BACK:
                    _clear_and_rebanner()
                    step = 5; continue
                target, _ = result
                language = _detect_language(target)
                if language not in ("go", "rust"):
                    language = "go"
                exts = {".go"} if language == "go" else {".rs"}
                loc = _count_loc(target, exts)
                detected_tier = _detect_l1_tier(loc)
                sys.stdout.write(f"  {_C_GRAY}Detected: {language.upper()}, {loc:,} LOC{_RST}\n")
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 12; continue

            if step == 12:
                result = select_l1_tier(detected_tier, loc)
                if result == _BACK:
                    _crumb_set(_l1_crumbs_to(1))
                    _clear_and_rebanner()
                    step = 1; continue
                tier = result
                sys.stdout.write("\n"); sys.stdout.flush()
                if tier == "t1":
                    step = 13; continue
                step = 14; continue

            if step == 13:
                l1_modules = _scan_modules(target, language)
                if l1_modules:
                    selected = select_l1_modules(l1_modules)
                    if selected == [_BACK]:
                        _crumb_set(_l1_crumbs_to(12))
                        _clear_and_rebanner()
                        step = 12; continue
                    subsystem_scope = ",".join(m[1] for m in selected)
                    total_mod_loc = sum(m[2] for m in selected)
                    if total_mod_loc > 30000:
                        sys.stdout.write(
                            f"  {_C_ORANGE}⚠ Selected modules total {total_mod_loc:,} LOC "
                            f"(T1 target: 5-30k){_RST}\n")
                    l1_modules = selected
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 14; continue

            if step == 14:
                is_fork = _detect_fork(target)
                if is_fork:
                    result = select_l1_fork()
                    if result == _BACK:
                        _crumb_set(_l1_crumbs_to(13 if tier == "t1" else 12))
                        _clear_and_rebanner()
                        step = 12 if tier != "t1" else 13; continue
                    fork_mode = result
                else:
                    fork_mode = "standalone"
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 15; continue

            if step == 15:
                result = select_docs()
                if result == _BACK:
                    # Step 14 (fork) is a no-op when no fork — skip it
                    if is_fork:
                        back_step = 14
                    elif tier == "t1":
                        back_step = 13
                    else:
                        back_step = 12
                    _crumb_set(_l1_crumbs_to(back_step))
                    _clear_and_rebanner()
                    step = back_step; continue
                docs = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 16; continue

            if step == 16:
                cost_est = None
                if os.path.isdir(target):
                    cost_est = estimate_cost(target, mode, pipeline="l1",
                                            backend=cli_backend or "claude",
                                            subsystem_scope=subsystem_scope)
                show_summary(mode, target, docs, cost_estimate=cost_est,
                             pipeline="l1", language=language,
                             tier=tier, modules=l1_modules, fork_mode=fork_mode,
                             backend=cli_backend or "claude")
                decision = confirm_launch()
                if decision == "back":
                    _crumb_set(_l1_crumbs_to(15))
                    _clear_and_rebanner()
                    step = 15; continue
                if decision == "cancel":
                    sys.stdout.write(f"  {_C_DARK_GRAY}Cancelled.{_RST}\n")
                    return
                launch_v2("l1", mode, target, language,
                          docs=docs, tier=tier,
                          subsystem_scope=subsystem_scope, fork_mode=fork_mode,
                          cli_backend=cli_backend)
                return

        # ── Compare flow ─────────────────────────────────────
        if mode == "compare":
            if step == 1:
                result = select_report("Your Plamen audit report (.md):")
                if result == _BACK:
                    _clear_and_rebanner()
                    step = 0; continue
                report = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 2; continue

            if step == 2:
                result = select_report("Ground truth report (.md):")
                if result == _BACK:
                    _crumb_set(_cmp_crumbs_to(1))
                    _clear_and_rebanner()
                    step = 1; continue
                ground_truth = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 3; continue

            if step == 3:
                show_summary(mode, report, ground_truth)
                decision = confirm_launch()
                if decision == "back":
                    _crumb_set(_cmp_crumbs_to(2))
                    _clear_and_rebanner()
                    step = 2; continue
                if decision == "cancel":
                    sys.stdout.write(f"  {_C_DARK_GRAY}Cancelled.{_RST}\n")
                    return
                launch_claude(mode, report, ground_truth)
                return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write(f"\n\n  {_C_DARK_GRAY}Interrupted.{_RST}\n")
        sys.exit(130)
    except EOFError:
        sys.stdout.write(f"\n\n  {_C_DARK_GRAY}Cancelled.{_RST}\n")
        sys.exit(0)
