"""Modularization structural tests.

Guards the 5-module split: types ← parsers ← validators ← mechanical, prompt.
Ensures no circular imports, no missing exports, and backward compatibility
with `import plamen_driver as D`.

Run: python test_modularization.py
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label} :: {detail}")


# ═════════════════════════════════════════════════════════════
# 1. Import chain — no circular imports
# ═════════════════════════════════════════════════════════════

def test_import_chain_no_circular():
    """Each module must load cleanly in dependency order."""
    order = [
        "plamen_types", "plamen_parsers", "plamen_validators",
        "plamen_mechanical", "plamen_prompt", "plamen_driver",
    ]
    loaded = []
    for mod_name in order:
        try:
            importlib.import_module(mod_name)
            loaded.append(mod_name)
        except Exception as exc:
            check(f"IMPORT.{mod_name}", False, repr(exc))
            return
    check("IMPORT.chain_clean", len(loaded) == len(order),
          f"loaded={loaded}")


# ═════════════════════════════════════════════════════════════
# 2. Every module has __all__
# ═════════════════════════════════════════════════════════════

def test_all_modules_have_all_list():
    for mod_name in ["plamen_types", "plamen_parsers", "plamen_validators",
                     "plamen_mechanical", "plamen_prompt"]:
        mod = importlib.import_module(mod_name)
        has_all = hasattr(mod, "__all__") and len(mod.__all__) > 0
        check(f"ALL_LIST.{mod_name}", has_all,
              f"__all__={'missing' if not hasattr(mod, '__all__') else len(mod.__all__)}")


# ═════════════════════════════════════════════════════════════
# 3. Backward compatibility: D.X works for all public names
# ═════════════════════════════════════════════════════════════

def test_backward_compat_driver_re_exports():
    """Everything in sub-module __all__ lists must be accessible via plamen_driver."""
    import plamen_driver as D
    missing = []
    for mod_name in ["plamen_types", "plamen_parsers", "plamen_validators",
                     "plamen_mechanical", "plamen_prompt"]:
        mod = importlib.import_module(mod_name)
        for name in mod.__all__:
            if not hasattr(D, name):
                missing.append(f"{mod_name}.{name}")
    check("COMPAT.all_names_accessible_via_D",
          len(missing) == 0,
          f"{len(missing)} missing: {missing[:10]}")


# ═════════════════════════════════════════════════════════════
# 4. No name collisions across modules
# ═════════════════════════════════════════════════════════════

def test_no_name_collisions():
    """No two modules export the same name."""
    seen: dict[str, str] = {}
    collisions = []
    for mod_name in ["plamen_types", "plamen_parsers", "plamen_validators",
                     "plamen_mechanical", "plamen_prompt"]:
        mod = importlib.import_module(mod_name)
        for name in mod.__all__:
            if name in seen:
                collisions.append(f"{name} in {seen[name]} AND {mod_name}")
            else:
                seen[name] = mod_name
    check("COLLISION.no_duplicates",
          len(collisions) == 0,
          f"{len(collisions)} collisions: {collisions[:5]}")


# ═════════════════════════════════════════════════════════════
# 5. Dependency direction check (no reverse imports)
# ═════════════════════════════════════════════════════════════

def test_dependency_direction():
    """Verify import direction rules:
    - parsers does NOT import from validators/mechanical/prompt
    - validators does NOT import from mechanical/prompt
    - types does NOT import from any plamen_* module
    """
    forbidden = {
        "plamen_types": {"plamen_parsers", "plamen_validators",
                         "plamen_mechanical", "plamen_prompt"},
        "plamen_parsers": {"plamen_validators", "plamen_mechanical",
                           "plamen_prompt"},
        "plamen_validators": {"plamen_mechanical", "plamen_prompt"},
    }
    violations = []
    for mod_name, banned in forbidden.items():
        path = SCRIPTS_DIR / f"{mod_name}.py"
        source = path.read_text(encoding="utf-8")
        for b in banned:
            if f"from {b}" in source or f"import {b}" in source:
                violations.append(f"{mod_name} imports {b}")
    check("DIRECTION.no_reverse_imports",
          len(violations) == 0,
          "; ".join(violations))


# ═════════════════════════════════════════════════════════════
# 6. Monolith completeness: every name in monolith is assigned
# ═════════════════════════════════════════════════════════════

def test_monolith_completeness():
    """Every top-level name in the monolith has a module assignment."""
    monolith = SCRIPTS_DIR / "plamen_driver_monolith.py"
    if not monolith.exists():
        modules_present = all(
            (SCRIPTS_DIR / f"{name}.py").exists()
            for name in (
                "plamen_types", "plamen_parsers", "plamen_validators",
                "plamen_mechanical", "plamen_prompt", "plamen_driver",
            )
        )
        check(
            "COMPLETE.modular_sources_present_without_monolith",
            modules_present,
            "monolith removed and one or more modular sources missing",
        )
        return
    source = monolith.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.add(node.target.id)

    # Check all names are in one of the modules or the driver
    import plamen_driver as D
    missing = []
    for name in sorted(names):
        if not hasattr(D, name):
            # Check if it's in the driver module's own globals
            missing.append(name)
    check("COMPLETE.all_monolith_names_accessible",
          len(missing) == 0,
          f"{len(missing)} missing: {missing[:10]}")


# ═════════════════════════════════════════════════════════════
# 7. Module sizes are reasonable
# ═════════════════════════════════════════════════════════════

def test_module_sizes():
    """No module should be empty or suspiciously small."""
    for mod_name in ["plamen_types", "plamen_parsers", "plamen_validators",
                     "plamen_mechanical", "plamen_prompt", "plamen_driver"]:
        path = SCRIPTS_DIR / f"{mod_name}.py"
        size = path.stat().st_size
        check(f"SIZE.{mod_name}_not_empty",
              size > 1000,
              f"{size} bytes")


# ═════════════════════════════════════════════════════════════
# 8. datetime import check
# ═════════════════════════════════════════════════════════════

def test_datetime_accessible():
    """datetime class must be importable from modules that use it."""
    import plamen_mechanical as M
    has_dt = hasattr(M, "datetime") or "datetime" in dir(M)
    check("DATETIME.accessible_in_mechanical", has_dt)

    import plamen_validators as V
    has_dt_v = hasattr(V, "datetime") or "datetime" in dir(V)
    check("DATETIME.accessible_in_validators", has_dt_v)


# ═════════════════════════════════════════════════════════════
# Runner
# ═════════════════════════════════════════════════════════════

TESTS = [
    test_import_chain_no_circular,
    test_all_modules_have_all_list,
    test_backward_compat_driver_re_exports,
    test_no_name_collisions,
    test_dependency_direction,
    test_monolith_completeness,
    test_module_sizes,
    test_datetime_accessible,
]


def main() -> int:
    print(f"Running {len(TESTS)} modularization structural tests...")
    for t in TESTS:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception as exc:
            global FAIL
            FAIL += 1
            print(f"  CRASH {t.__name__} :: {exc!r}")
    print(f"\n{'=' * 64}")
    print(f"  PASS: {PASS}   FAIL: {FAIL}")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
