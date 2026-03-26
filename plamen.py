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
def _pip_install_args():
    """Build pip install flags that work on PEP 668 systems (macOS Homebrew, Ubuntu 23.04+)."""
    args = [sys.executable, "-m", "pip", "install"]
    if sys.platform != "win32":
        args.append("--user")
    # Detect PEP 668 "externally managed" environments
    try:
        import sysconfig
        marker = os.path.join(sysconfig.get_path("stdlib"), "EXTERNALLY-MANAGED")
        if os.path.isfile(marker):
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

# ── Constants ────────────────────────────────────────────────
_BACK = "__back__"
_MAX_LINE = 48  # max visible chars for any prompt line (W - 4)

_STYLE = InquirerPyStyle({
    "questionmark": "#ff7800 bold",
    "pointer":      "#ff7800 bold",
    "highlighted":  "#ffffff bold bg:#101018",
    "selected":     "#00af00",
    "answer":       "#ff7800 bold",
    "question":     "#ffffff",
    "input":        "#ffffff",
})

console = Console(file=sys.stdout, highlight=False, force_terminal=True, legacy_windows=False)

# ── ANSI helpers ─────────────────────────────────────────────
_RST  = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM  = "\x1b[2m"

# Colors
_C_ORANGE    = "\x1b[38;2;255;140;66m"
_C_BLUE      = "\x1b[38;2;100;149;237m"
_C_GREEN     = "\x1b[38;2;0;175;0m"
_C_RED       = "\x1b[38;2;200;60;60m"
_C_WHITE     = "\x1b[38;2;255;255;255m"
_C_GRAY      = "\x1b[38;2;100;100;100m"
_C_DARK_GRAY = "\x1b[38;2;60;60;60m"
_C_BOX       = "\x1b[38;2;24;24;32m"

# ── Fire palette (256-color) ─────────────────────────────────
_FIRE = [160, 166, 202, 208, 214, 220]
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
    "light":    {"label": "Light Audit",    "agents": "15-18",    "scope": "ALL severities"},
    "core":     {"label": "Core Audit",     "agents": "25-45",    "scope": "ALL severities"},
    "thorough": {"label": "Thorough Audit", "agents": "35-95",    "scope": "ALL severities"},
    "compare":  {"label": "Compare",        "agents": "variable", "scope": "DELTA report"},
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
        # MCP uses content-length framed messages on stdio
        header = f"Content-Length: {len(init_msg)}\r\n\r\n"
        proc.stdin.write(header.encode() + init_msg.encode())
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
        cmd_base = os.path.basename(cmd).lower().replace(".cmd", "")
        if cmd_base == "npx" and len(args) >= 2 and args[0] == "-y":
            pkg = args[1].rsplit("@", 1)[0] if "@" in args[1] else args[1]
            if not _npx_package_cached(pkg):
                results.append((name, None))  # None = not cached, skip probe
                continue
        ok = _probe_mcp_server(name, cmd, args, cwd=cwd, env=env)
        results.append((name, ok))
    return results


def check_dependencies() -> bool:
    """Verify required and optional tools. Returns True if required deps OK."""
    w = sys.stdout.write
    bx = _C_BOX
    W = 52
    ok = True

    # ── Probe all tools ─────────────────────────────────────
    required = [
        ("claude",  _find_bin("claude")),
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
        ]),
        ("Move", [
            ("aptos",   _find_bin("aptos", ["~/.aptoscli/bin"])),
            ("sui",     _find_bin("sui", ["~/AppData/Local/bin", "~/.local/bin"])),
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
        rag_status = f"{_C_RED}not built{_RST}"
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
    ],

    "Move": [
        ("Aptos CLI",
         lambda: _find_bin("aptos", ["~/.aptoscli/bin"]),
         _aptos_cmds,
         ["aptos"], "~30s", ["~/.aptoscli/bin"], None),

        ("Sui CLI",
         lambda: _find_bin("sui", ["~/AppData/Local/bin", "~/.local/bin"]),
         _sui_cmds,
         ["sui"], "~1-2 min",
         ["~/AppData/Local/bin", "~/.local/bin"], None),
    ],
}


def _has_bash() -> bool:
    return bool(shutil.which("bash"))


def _has_brew() -> bool:
    return bool(shutil.which("brew"))


def _has_winget() -> bool:
    return sys.platform == "win32" and bool(shutil.which("winget"))


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
        except subprocess.TimeoutExpired:
            w(f"  {_C_RED}  timed out after {timeout}s{_RST}\n")
            sys.stdout.flush()
        if attempt < retries:
            w(f"  {_C_ORANGE}  retry {attempt + 1}/{retries}...{_RST}\n")
            sys.stdout.flush()
    return False


def _update_path_env(new_paths: list, persist: bool = False):
    """Add directories to the current process PATH (for post-install detection and subprocesses).

    If persist=True and on Windows, also adds to the user's persistent PATH via setx
    so future terminal sessions find the tools without manual configuration.
    """
    current = os.environ.get("PATH", "")
    for p in new_paths:
        expanded = os.path.normpath(os.path.expanduser(p))
        if os.path.isdir(expanded) and expanded not in current:
            os.environ["PATH"] = expanded + os.pathsep + os.environ.get("PATH", "")
            current = os.environ["PATH"]
            # Persist to Windows user PATH so future terminals find the tool
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

    # Fixed timeouts. Solodit is network-bound (29 tags × rate-limit delay);
    # DeFiHackLabs and Immunefi are fast with MiniLM (~2-3 min each).
    solodit_timeout  = 1200  # 20 min — generous for slow networks + rate limiting
    indexing_timeout =  600  # 10 min — more than enough for MiniLM on any hardware
    max_pages        =    5  # 29 tags × 5 pages × 3.5s ≈ 8 min API time

    steps = [
        # (label, est, cmd, retry_cmd, timeout)
        # Solodit: no retry — a hanging API call won't improve on retry
        ("Solodit — live API",
         "~10 min",
         f'cd "{vuln_db_dir}" && {py} -m unified_vuln.indexer index -s solodit --max-pages {max_pages}',
         None,
         solodit_timeout),
        # DeFiHackLabs: local parsing + embedding; retry with same command is safe
        ("DeFiHackLabs — local",
         "~1 min",
         f'cd "{vuln_db_dir}" && {py} -m unified_vuln.indexer index -s defihacklabs',
         f'cd "{vuln_db_dir}" && {py} -m unified_vuln.indexer index -s defihacklabs',
         indexing_timeout),
        # Immunefi: first attempt fetches 139 URLs + embeds; retry skips the HTTP fetch
        # phase (uses cached immunefi_fetched.json) and goes straight to embedding
        ("Immunefi — writeups",
         "~2 min",
         f'cd "{vuln_db_dir}" && {py} -m unified_vuln.indexer index -s immunefi',
         f'cd "{vuln_db_dir}" && {py} -m unified_vuln.indexer index -s immunefi --skip-fetch',
         indexing_timeout),
    ]

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
    for name in ("claude", "python", "npx", "npm", "git"):
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
        ("unified-vuln-db", "custom-mcp/unified-vuln-db/requirements.txt"),
        ("solodit-scraper", "custom-mcp/solodit-scraper/requirements.txt"),
        ("defihacklabs-rag", "custom-mcp/defihacklabs-rag/requirements.txt"),
        ("farofino-mcp", "custom-mcp/farofino-mcp/requirements.txt"),
    ]
    editable_pkgs = [
        ("unified-vuln-db", "custom-mcp/unified-vuln-db"),
        ("solana-fender", "custom-mcp/solana-fender"),
        ("slither-mcp (EVM)", "custom-mcp/slither-mcp"),
    ]

    # Check if core deps already installed
    try:
        import rich, InquirerPy  # noqa: F401
        core_ok = True
    except ImportError:
        core_ok = False

    if core_ok:
        # Quick-check: try importing a deep dep
        try:
            import torch, chromadb  # noqa: F401
            deep_ok = True
        except ImportError:
            deep_ok = False
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

    for label, pkg in editable_pkgs:
        path = os.path.join(base, pkg)
        if not os.path.isdir(path):
            w(f"  {_C_DARK_GRAY}  skipping {label} — not found{_RST}\n")
            continue
        w(f"  {_C_ORANGE}>{_RST} {label}\n")
        sys.stdout.flush()
        if not _run_install_cmd(f'{pip_base} -e "{path}"', retries=1):
            w(f"  {_C_RED}  failed (non-critical){_RST}\n")
        else:
            w(f"  {_C_GREEN}  done{_RST}\n")

    w("\n")
    return all_ok


def _setup_config_files(w):
    """Merge Plamen's config into Claude Code's ~/.claude/ (additive, non-destructive)."""
    _merge_settings_json(w)
    _merge_mcp_json(w)
    _merge_claude_md(w)
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
            # Junctions don't need admin privileges on Windows
            subprocess.run(["cmd", "/c", "mklink", "/J", dst, src],
                           check=True, capture_output=True)
        else:
            os.symlink(src, dst, target_is_directory=is_dir)
        return True
    except OSError as e:
        w(f"  {_C_RED}  failed to link {os.path.basename(dst)}: {e}{_RST}\n")
        if sys.platform == "win32" and "privilege" in str(e).lower():
            w(f"  {_C_GRAY}  Enable Developer Mode: Settings > System > For Developers{_RST}\n")
        return False


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

    # 2. Skills directory (Plamen-only)
    skills_src = os.path.join(PLAMEN_HOME, "agents", "skills")
    skills_dst = os.path.join(agents_dir, "skills")
    if os.path.isdir(skills_src):
        w(f"  {_C_ORANGE}>{_RST} Linking skills\n")
        if _safe_link(skills_src, skills_dst, w):
            installed.append(skills_dst)

    # 3. Slash command (individual — user may have own commands)
    commands_dir = os.path.join(CLAUDE_HOME, "commands")
    os.makedirs(commands_dir, exist_ok=True)
    cmd_src = os.path.join(PLAMEN_HOME, "commands", "plamen.md")
    if os.path.isfile(cmd_src):
        w(f"  {_C_ORANGE}>{_RST} Linking /plamen command\n")
        dst = os.path.join(commands_dir, "plamen.md")
        if _safe_link(cmd_src, dst, w):
            installed.append(dst)

    # 4. Rule files (individual — user may have own rules)
    rules_dir = os.path.join(CLAUDE_HOME, "rules")
    os.makedirs(rules_dir, exist_ok=True)
    rule_files = sorted(glob.glob(os.path.join(PLAMEN_HOME, "rules", "*.md")))
    if rule_files:
        w(f"  {_C_ORANGE}>{_RST} Linking rules ({len(rule_files)} files)\n")
        for f in rule_files:
            dst = os.path.join(rules_dir, os.path.basename(f))
            if _safe_link(f, dst, w):
                installed.append(dst)

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

    # 7. Utility files
    for fname in ("plamen", "plamen.py", "plamen.sh", "plamen.bat", "VERSION"):
        src = os.path.join(PLAMEN_HOME, fname)
        if os.path.isfile(src):
            dst = os.path.join(CLAUDE_HOME, fname)
            if _safe_link(src, dst, w):
                installed.append(dst)

    # 8. Write install manifest
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
        with open(target) as f:
            existing = _json.load(f)

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

    # Resolve relative cwd paths to absolute (pointing to real repo location)
    for _name, config in plamen.get("mcpServers", {}).items():
        if "cwd" in config and config["cwd"].startswith("./"):
            config["cwd"] = os.path.join(PLAMEN_HOME, config["cwd"][2:])

    existing = {"mcpServers": {}}
    if os.path.isfile(target):
        with open(target) as f:
            existing = _json.load(f)
        existing.setdefault("mcpServers", {})

    added, skipped, patched_env = [], [], []
    for name, config in plamen.get("mcpServers", {}).items():
        if name in existing["mcpServers"]:
            skipped.append(name)
            # Backfill missing env vars into existing servers (e.g., new keys added
            # to mcp.json.example after initial install — propagate to existing config)
            template_env = config.get("env", {})
            if template_env:
                existing_env = existing["mcpServers"][name].setdefault("env", {})
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
    if patched_env:
        w(f"  {_C_GREEN}mcp.json: backfilled env vars: {', '.join(patched_env)}{_RST}\n")
    if skipped:
        w(f"  {_C_GRAY}mcp.json: kept existing {', '.join(skipped)}{_RST}\n")
    if not added and not skipped and not patched_env:
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
                settings["permissions"][key] = [x for x in current if x not in plamen_list]
            # Remove Plamen env vars
            for k in plamen_settings.get("env", {}):
                settings.get("env", {}).pop(k, None)
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


def run_setup():
    """Full setup flow: Python deps → config files → toolchain → RAG → re-check."""
    w = sys.stdout.write

    # ── Symlink install (if repo is not directly in ~/.claude) ─
    if os.path.normpath(PLAMEN_HOME) != os.path.normpath(CLAUDE_HOME):
        console.print(Rule(title="Linking into Claude Code", style="color(238)"))
        _run_symlink_install(w)

    # ── Submodules ─────────────────────────────────────────────
    slither_dir = os.path.join(PLAMEN_HOME, "custom-mcp", "slither-mcp")
    if os.path.isdir(slither_dir) and not os.listdir(slither_dir):
        w(f"  {_C_ORANGE}>{_RST} Initializing git submodules...\n")
        sys.stdout.flush()
        _run_install_cmd(f'cd "{PLAMEN_HOME}" && git submodule update --init --recursive', retries=1)
        w("\n")

    # ── Python dependencies ───────────────────────────────────
    console.print(Rule(title="Python Dependencies", style="color(238)"))
    _setup_python_deps(w)

    # ── Config files ──────────────────────────────────────────
    console.print(Rule(title="Configuration", style="color(238)"))
    _setup_config_files(w)

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

    if not missing and rag_count > 0 and not rag_empty:
        w(f"  {_C_GREEN}Everything is set up ({rag_count:,} RAG entries).{_RST}\n")
        w(f"  {_C_GRAY}To rebuild RAG: plamen rag{_RST}\n\n")
        return

    # ── Build checkbox choices with time estimates ───────────
    item_choices = []
    for group, entries in missing.items():
        names = ", ".join(d for d, _, _, _, _, _, _ in entries)
        item_choices.append({"name": f"{group:8s} {names}", "value": group})

    if rag_empty:
        item_choices.append({"name": "RAG DB   vulnerability knowledge base",
                             "value": "__rag__"})
    elif rag_count > 0:
        item_choices.append({"name": f"RAG DB   rebuild/extend ({rag_count:,} entries currently)",
                             "value": "__rag__"})

    all_values = [c["value"] for c in item_choices]

    choices = []
    choices.append({"name": "All      install everything below",
                    "value": "__all__"})
    choices.append(Separator())
    choices.extend(item_choices)
    choices.append(Separator())
    choices.append({"name": "Skip     back to menu", "value": "__skip__"})

    # Show time estimates
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

        for display, check_fn, cmds_fn, provides, est, paths, requires in missing[group]:
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
                    expanded = os.path.normpath(os.path.expanduser(p))
                    os.makedirs(expanded, exist_ok=True)
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

    for row, ci in zip(art, _FIRE if len(art) > 1 else [214]):
        t = Text(row)
        t.stylize(f"bold color({ci})")
        console.print(t)

    w("\n")
    console.print(Rule(style="color(238)"))
    w(f"  {_C_GRAY}⬡{_RST} {_BOLD}{_C_WHITE}Web3 Security Auditor{_RST}  {_DIM}v{VERSION}{_RST}\n")
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
    row([("  ", None), ("target", _C_ORANGE), ("          ", None),
         ("local project directory", _C_GRAY)])
    row([("  ", None), ("docs", _C_ORANGE), ("            ", None),
         ("whitepaper or spec (optional)", _C_GRAY)])
    row([("  ", None), ("scope", _C_ORANGE), ("           ", None),
         ("scope.txt or notes (optional)", _C_GRAY)])
    row([("  ", None), ("ground truth", _C_ORANGE), ("    ", None),
         ("reference report (compare only)", _C_GRAY)])
    w(f"  {bx}╰{'─' * W}╯{_RST}\n")
    w("\n")
    sys.stdout.flush()


# ── Helpers ──────────────────────────────────────────────────

# Dirs skipped at ANY depth (build artifacts, tooling, never contain source)
_SKIP_ALWAYS = {'node_modules', '.git', 'cache', 'artifacts', '.anchor', '.aptos',
                'typechain', 'typechain-types', 'coverage', '__pycache__'}
# Dirs skipped only at project ROOT level (contain deps/tests/scripts, not source)
_SKIP_ROOT = {'lib', 'target', 'build', 'out', 'test', 'tests', 'mock', 'mocks',
              'script', 'deploy', 'migrations', 'flatten', 'docs', 'doc'}
_SRC_EXTS = {'.sol', '.rs', '.move'}


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
        "Anchor.toml": "Anchor", "Move.toml": "Move",
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


def estimate_cost(target: str, mode: str,
                  scope_file: str = "", scope_notes: str = "") -> dict:
    """Estimate audit resource usage by modeling pipeline stages with context accumulation.

    Each subagent is a multi-turn conversation where every turn re-sends the full
    prior context. A 10-turn agent with 30K base context consumes ~400-500K input
    tokens total, not 30K. This model accounts for that accumulation.

    Respects scope constraints: if scope_file or scope_notes list specific contracts,
    only those files are counted for the estimate.

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

    target_norm = os.path.normpath(target)
    for root, dirs, files in os.walk(target):
        if os.path.normpath(root) == target_norm:
            dirs[:] = [x for x in dirs if x not in _SKIP_ALWAYS and x not in _SKIP_ROOT]
        else:
            dirs[:] = [x for x in dirs if x not in _SKIP_ALWAYS]
        for fname in files:
            if os.path.splitext(fname)[1] not in _SRC_EXTS:
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
    orch_base = PROMPT_BASE + 15_000  # CLAUDE.md + plamen.md loaded

    if mode == "light":
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

    if mode == "thorough":
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
            ("Orch. extra",  1, "opus",   orch_base, 15),  # reduced: context compression limits real accumulation
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
    # Current pricing (Opus 4.6 $5/$25, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5)
    pricing = {"opus": (5.0, 25.0), "sonnet": (3.0, 15.0), "haiku": (1.0, 5.0)}
    api_cost = 0.0
    for m in ("opus", "sonnet", "haiku"):
        ip, op = pricing[m]
        api_cost += (model_input[m] / 1e6) * ip + (model_output[m] / 1e6) * op

    # ── Weekly plan usage estimate ───────────────────────────
    # Calibrated from empirical data:
    #   A thorough audit on a ~5000-line project uses ~9% of Max x20 weekly allowance.
    #   Max x20 weekly allowance: 240-480h Sonnet + 24-40h Opus (resets weekly).
    #   Max x5 has 4x less capacity than x20.
    ref_tokens_thorough_5k = 0
    ref_src = 5000 * 4
    ref_stages = [
        # Core stages (same model as main stages)
        (2, ref_src * 0.8 + 8000, 12), (2, ref_src * 0.3 + 8000, 10),    # Recon
        (4, ref_src * 0.8 + 36000, 12), (1, 48000, 8),                     # Breadth + Inventory
        (1, ref_src * 0.5 + 13000, 10),                                     # Sem. Invariants
        (2, ref_src * 0.4 + 36000, 12), (2, ref_src * 0.4 + 36000, 10),   # Depth
        (3, ref_src * 0.3 + 28000, 8), (1, ref_src * 0.3 + 28000, 8),     # Scanners + VS
        (3, ref_src * 0.3 + 21000, 8), (3, 28000, 6), (2, 48000, 8),      # Niche(3) + RAG + Chain
        (5, ref_src * 0.25 + 21000, 14),                                    # Verification
        (1, 68000, 8), (2, 48000, 8), (2, 68000, 6),                       # Report
        (1, 23000, 35),                                                      # Orchestrator (capped)
        # Thorough-only stages (with skip-probability adjustments)
        (3, ref_src * 0.6 + 28000, 10), (4, ref_src * 0.25 + 13000, 8),   # Re-scan(3) + Per-contract(4)
        (1, ref_src * 0.4 + 28000, 8), (3, ref_src * 0.2 + 21000, 8),     # Sem. Pass 2 + Depth 2-3(3)
        (1, ref_src * 0.3 + 13000, 10),                                     # Inv. Fuzz only (Medusa ~50% skip)
        (1, 28000, 8),                                                       # Design Stress
        (2, ref_src * 0.2 + 21000, 10),                                     # Extra verify(2)
        (4, ref_src * 0.25 + 21000, 10), (1, 18000, 4),                    # Skeptic + Judge
        (1, 23000, 15),                                                      # Orch. extra (capped)
    ]
    for c, b, t in ref_stages:
        ai, _ = agent_tokens(b, t)
        ref_tokens_thorough_5k += c * ai

    pct_x20 = (total_input / ref_tokens_thorough_5k) * 9.0 if ref_tokens_thorough_5k else 0
    pct_x5 = pct_x20 * 4

    # Pro plan estimate: ~1/8th of Max x5 weekly capacity (sonnet-only, no opus,
    # significantly lower rate limits). Calibrated conservatively.
    pct_pro = pct_x5 * 2.5 if mode == "light" else pct_x5 * 5

    return {
        "files": total_files,
        "lines": total_lines,
        "agents": total_agents,
        "input_mtok": round(input_mtok, 1),
        "output_mtok": round(output_mtok, 1),
        "api_cost": round(api_cost, 0),
        "pct_x5": round(pct_x5, 1),
        "pct_x20": round(pct_x20, 1),
        "pct_pro": round(pct_pro, 1),
        "scoped": has_scope and total_files > 0,
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

def select_mode() -> str:
    return inquirer.select(
        message="Select audit mode:",
        choices=[
            {"name": "Light      15-18 agents | Pro plan  | best under 3k LOC", "value": "light"},
            {"name": "Core       25-45 agents | Max plan  | ALL severities",  "value": "core"},
            {"name": "Thorough   35-95 agents | Max plan  | ALL severities + fuzz", "value": "thorough"},
            Separator(),
            {"name": "Compare    variable     | DELTA report",               "value": "compare"},
            {"name": "Setup      install tools + build RAG DB",              "value": "setup"},
        ],
        default="light",
        pointer="  >",
        style=_STYLE,
        qmark="⬡",
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


# ── Summary ──────────────────────────────────────────────────

def show_summary(mode: str, target: str, docs: str,
                 network: str = "", scope_file: str = "", scope_notes: str = "",
                 cost_estimate: dict = None, strict: bool = False):
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

    row("Mode", f"{MODES[mode]['label']}", _C_ORANGE)
    row("Target", target)
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
        w(f"  {_C_DARK_GRAY}complexity and findings count. Run /cost after for actuals.{_RST}\n")

    w("\n")
    sys.stdout.flush()


# ── Launch ───────────────────────────────────────────────────

def launch_claude(mode: str, target: str, docs: str,
                  network: str = "", scope_file: str = "", scope_notes: str = "",
                  **kwargs):
    claude_bin = shutil.which("claude")
    if not claude_bin:
        sys.stdout.write(f"  {_C_RED}✗ 'claude' not found in PATH{_RST}\n")
        sys.exit(1)

    # Build the prompt string
    if mode == "compare":
        parts = ["/plamen compare"]
        if target:
            parts.append(f"report: {target}")
        if docs:
            parts.append(f"ground_truth: {docs}")
        prompt = " ".join(parts)
    else:
        parts = [f"/plamen {mode} {target} wrapper-launch"]
        if docs:
            parts.append(f"docs: {docs}")
        else:
            parts.append("nodocs")
        if network:
            parts.append(f"network: {network}")
        if scope_file:
            parts.append(f"scope: {scope_file}")
        if scope_notes:
            parts.append(f"notes: {scope_notes}")
        if kwargs.get("strict"):
            parts.append("proven-only: true")
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
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}core{_RST} /path/to/project        Audit in Core mode\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}thorough{_RST} /path/to/project    Audit in Thorough mode\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}light{_RST} /path/to/project       Audit in Light mode\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}compare{_RST}                      Diff reports\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}setup{_RST}                        Install tools + build RAG\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}rag{_RST}                          Rebuild RAG database only\n")
            w(f"    {_C_ORANGE}plamen{_RST} {_C_GRAY}uninstall{_RST}                    Remove from ~/.claude\n")
            w(f"\n  {_C_WHITE}Options (for audit modes):{_RST}\n")
            w(f"    {_C_GRAY}--docs{_RST} PATH              Whitepaper or spec file\n")
            w(f"    {_C_GRAY}--scope{_RST} PATH             Scope file listing contracts\n")
            w(f"    {_C_GRAY}--notes{_RST} TEXT             Scope notes (free text)\n")
            w(f"    {_C_GRAY}--network{_RST} NAME           Target network (ethereum, arbitrum, etc.)\n")
            w(f"    {_C_GRAY}--proven-only{_RST}            Cap unproven findings at Low severity\n")
            w(f"\n  {_C_WHITE}Inside Claude Code:{_RST}\n")
            w(f"    {_C_GRAY}/plamen{_RST}                          Interactive wizard\n")
            w(f"    {_C_GRAY}/plamen core{_RST} docs: file.pdf      With options\n")
            w(f"\n")
            return

        # ── Estimate subcommand (for /plamen command) ────────
        if arg == "--estimate":
            import json as _json
            est_target = sys.argv[2] if len(sys.argv) > 2 else "."
            est_mode = sys.argv[3] if len(sys.argv) > 3 else "core"
            est_scope = ""
            est_notes = ""
            for i, a in enumerate(sys.argv):
                if a == "--scope" and i + 1 < len(sys.argv):
                    est_scope = sys.argv[i + 1]
                if a == "--scope-notes" and i + 1 < len(sys.argv):
                    est_notes = sys.argv[i + 1]
            r = estimate_cost(est_target, est_mode, est_scope, est_notes)
            print(_json.dumps(r))
            return

        # ── Install / uninstall subcommands ───────────────────
        if arg in ("install", "setup"):
            show_banner()
            run_setup()
            return

        if arg == "uninstall":
            show_banner()
            run_uninstall()
            return

        if arg == "rag":
            show_banner()
            w = sys.stdout.write
            w(f"\n  {_BOLD}{_C_WHITE}Building RAG vulnerability database...{_RST}\n\n")
            sys.stdout.flush()
            _build_rag_db(w)
            return

        if arg in ("light", "core", "thorough", "compare"):
            target = sys.argv[2] if len(sys.argv) > 2 else ""
            docs = ""
            network = ""
            scope_file = ""
            scope_notes = ""
            strict = False
            for i, a in enumerate(sys.argv):
                if a == "--docs" and i + 1 < len(sys.argv):
                    docs = sys.argv[i + 1]
                if a == "--network" and i + 1 < len(sys.argv):
                    network = sys.argv[i + 1]
                if a == "--scope" and i + 1 < len(sys.argv):
                    scope_file = sys.argv[i + 1]
                if a == "--notes" and i + 1 < len(sys.argv):
                    scope_notes = sys.argv[i + 1]
                if a in ("--proven-only", "--strict"):
                    strict = True
            if not target and arg != "compare":
                show_banner()
                target, network = select_target()
            launch_claude(arg, target, docs, network, scope_file, scope_notes,
                          strict=strict)
            return

    # ── Interactive flow (state machine) ─────────────────────
    show_banner()
    show_hint_panel()

    if not _quick_check_required():
        check_dependencies()
        sys.stdout.write(f"  {_C_RED}Cannot proceed without required tools.{_RST}\n")
        sys.stdout.write(f"  {_C_GRAY}Install claude, python, npx/npm, and git, then retry.{_RST}\n")
        sys.exit(1)

    mode = target = docs = network = scope_file = scope_notes = ""
    report = ground_truth = ""
    strict = False
    step = 0

    while True:
        # ── Step 0: Mode selection ───────────────────────────
        if step == 0:
            mode = select_mode()
            sys.stdout.write("\n")
            sys.stdout.flush()
            if mode == "setup":
                run_setup()
                step = 0
                continue
            step = 1
            continue

        # ── Core / Thorough flow ─────────────────────────────
        if mode in ("light", "core", "thorough"):
            if step == 1:
                result = select_target()
                if result[0] == _BACK:
                    step = 0; continue
                target, network = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 2; continue

            if step == 2:
                result = select_docs()
                if result == _BACK:
                    step = 1; continue
                docs = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 3; continue

            if step == 3:
                result = select_scope()
                if result[0] == _BACK:
                    step = 2; continue
                scope_file, scope_notes = result
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
                    step = 3; continue
                strict = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 4; continue

            if step == 4:
                cost_est = None
                if os.path.isdir(target):
                    cost_est = estimate_cost(target, mode, scope_file, scope_notes)
                show_summary(mode, target, docs, network, scope_file, scope_notes, cost_est,
                             strict=strict)
                decision = confirm_launch()
                if decision == "back":
                    step = 35; continue
                if decision == "cancel":
                    sys.stdout.write(f"  {_C_DARK_GRAY}Cancelled.{_RST}\n")
                    return
                launch_claude(mode, target, docs, network, scope_file, scope_notes,
                              strict=strict)
                return

        # ── Compare flow ─────────────────────────────────────
        if mode == "compare":
            if step == 1:
                result = select_report("Your Plamen audit report (.md):")
                if result == _BACK:
                    step = 0; continue
                report = result
                sys.stdout.write("\n"); sys.stdout.flush()
                step = 2; continue

            if step == 2:
                result = select_report("Ground truth report (.md):")
                if result == _BACK:
                    step = 1; continue
                ground_truth = result
                step = 3; continue

            if step == 3:
                show_summary(mode, report, ground_truth)
                decision = confirm_launch()
                if decision == "back":
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
