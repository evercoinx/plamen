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

from rich.console import Console
from rich.text import Text
from rich.rule import Rule
from InquirerPy import inquirer
from InquirerPy.separator import Separator
from InquirerPy.utils import InquirerPyStyle

# ── Version ─────────────────────────────────────────────────
def _read_version() -> str:
    vfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
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
    "light":    {"label": "Light Audit",    "agents": "15-18",    "scope": "ALL Medium+"},
    "core":     {"label": "Core Audit",     "agents": "25-45",    "scope": "ALL Medium+"},
    "thorough": {"label": "Thorough Audit", "agents": "35-95",    "scope": "ALL severities"},
    "compare":  {"label": "Compare",        "agents": "variable", "scope": "DELTA report"},
}


# ── Dependency check ─────────────────────────────────────────

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


def _probe_rag_db() -> int:
    """Return the number of entries in the RAG vulnerability database, or -1 if not found."""
    db_path = os.path.expanduser("~/.claude/unified-vuln-db/data/chroma_db/chroma.sqlite3")
    if not os.path.isfile(db_path):
        return -1
    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        return count
    except Exception:
        return -1


def check_dependencies() -> bool:
    """Verify required and optional tools. Returns True if required deps OK."""
    w = sys.stdout.write
    bx = _C_BOX
    W = 52
    ok = True

    # ── Probe all tools ─────────────────────────────────────
    required = [
        ("claude",  _find_bin("claude")),
        ("python",  _find_bin("python", _python_extra_paths())),
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
    if rag_count > 0:
        rag_status = f"{_C_GREEN}{rag_count:,} entries{_RST}"
    elif rag_count == 0:
        rag_status = f"{_C_RED}empty{_RST}"
    else:
        rag_status = f"{_C_RED}not built{_RST}"
    _box_row(w, bx, W,
             f"  {_C_GRAY}RAG DB{_RST}   vulnerability knowledge base",
             rag_status)

    w(f"  {bx}╰{'─' * W}╯{_RST}\n")

    # Summary line
    total_opt = sum(len(t) for _, t in groups)
    total_found = sum(1 for _, tools in groups for _, b in tools if b)
    if total_found == total_opt:
        w(f"  {_C_DARK_GRAY}All {total_opt} optional tools available{_RST}\n")
    else:
        w(f"  {_C_DARK_GRAY}{total_found}/{total_opt} optional — "
          f"install per your target chain{_RST}\n")

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


def _openssl_check():
    """Check if OpenSSL dev libs are available for cargo builds."""
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
        "check": _openssl_check,
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
        if _openssl_check():
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
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "_solana_installer.py").replace('\\', '/')
        return [f'python "{script}"']
    return ['sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"']


def _anchor_cmds():
    if sys.platform == "win32":
        # Download prebuilt AVM from GitHub, then use AVM to install Anchor
        # AVM downloads prebuilt anchor binaries since v0.31.0
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
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
    return ['python -c "'
            'import urllib.request,tempfile,os,subprocess;'
            "p=os.path.join(tempfile.gettempdir(),'install_aptos.py');"
            "urllib.request.urlretrieve('https://aptos.dev/scripts/install_cli.py',p);"
            "subprocess.run(['" + ("python" if sys.platform == "win32" else "python3") + "',p])"
            '"']


def _sui_cmds():
    # suiup is the official Sui version manager (prebuilt binary, fast)
    if sys.platform == "win32":
        # Pure Python: download zip from GitHub releases, extract, place in ~/.local/bin
        # No bash/sh needed — works in cmd.exe and PowerShell
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
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
         lambda: ['pip install slither-analyzer'
                  + (' --user' if sys.platform != "win32" else '')],
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


def _run_install_cmd(cmd: str, retries: int = 1) -> bool:
    """Run a single install command with visible output. Returns True on success."""
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
        result = subprocess.run(cmd, **run_kwargs)
        if result.returncode == 0:
            return True
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
    """Check if the RAG database needs building (empty or missing)."""
    return _probe_rag_db() <= 0


def _build_rag_db(w):
    """Run the RAG indexer pipeline. Returns True on success."""
    vuln_db_dir = os.path.expanduser("~/.claude/custom-mcp/unified-vuln-db")
    if not os.path.isdir(vuln_db_dir):
        w(f"  {_C_RED}unified-vuln-db not found at {vuln_db_dir}{_RST}\n")
        return False

    steps = [
        ("Solodit — live API",       "~2 min",
         f'cd "{vuln_db_dir}" && python -m unified_vuln.indexer index -s solodit --max-pages 10'),
        ("DeFiHackLabs — local",     "~1 min",
         f'cd "{vuln_db_dir}" && python -m unified_vuln.indexer index -s defihacklabs'),
        ("Immunefi — writeups",      "~30s",
         f'cd "{vuln_db_dir}" && python -m unified_vuln.indexer index -s immunefi'),
    ]

    for label, est, cmd in steps:
        w(f"  {_C_ORANGE}>{_RST} {_C_WHITE}{label}{_RST}"
          f"  {_C_DARK_GRAY}{est}{_RST}\n")
        sys.stdout.flush()
        if not _run_install_cmd(cmd, retries=1):
            w(f"  {_C_RED}  failed — continuing with partial data{_RST}\n")
        else:
            w(f"  {_C_GREEN}  done{_RST}\n")
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
            if not _find_bin("python", _python_extra_paths()):
                return False
        elif not _find_bin(name):
            return False
    return True


def run_setup():
    """Full setup flow: show toolchain status → select what to install → run → re-check."""
    w = sys.stdout.write

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

    if not missing and not rag_empty:
        w(f"  {_C_GREEN}Everything is set up.{_RST}\n\n")
        return

    # ── Build checkbox choices with time estimates ───────────
    item_choices = []
    for group, entries in missing.items():
        names = ", ".join(d for d, _, _, _, _, _, _ in entries)
        item_choices.append({"name": f"{group:8s} {names}", "value": group})

    if rag_empty:
        item_choices.append({"name": "RAG DB   vulnerability knowledge base",
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

_SKIP_DIRS = {'lib', 'node_modules', 'target', 'build', '.git', 'test', 'tests',
              'out', 'cache', 'artifacts', '.anchor', '.aptos', 'mock', 'mocks',
              'script', 'deploy', 'migrations', 'flatten', 'typechain',
              'typechain-types', 'coverage', 'docs', 'doc'}
_SRC_EXTS = {'.sol', '.rs', '.move'}


def _count_source_files(d: str) -> int:
    """Count .sol/.rs/.move files recursively, pruning skip dirs on descent."""
    total = 0
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
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
            with open(scope_file, 'r', errors='ignore') as sf:
                for line in sf:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('//'):
                        continue
                    # Extract filename from paths like "src/contracts/Vault.sol"
                    base = os.path.basename(line.strip().rstrip('/'))
                    if base:
                        scope_names.add(base.lower())
                        # Also add stem without extension
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

    for root, dirs, files in os.walk(target):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
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
            {"name": "Core       25-45 agents | Max plan  | ALL Medium+",    "value": "core"},
            {"name": "Thorough   35-95 agents | Max plan  | ALL severities", "value": "thorough"},
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
        parts = [f"/plamen {mode} {target}"]
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

        # ── Install subcommand ───────────────────────────────
        if arg in ("install", "setup"):
            show_banner()
            run_setup()
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
                if a == "--strict":
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
