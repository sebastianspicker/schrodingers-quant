"""Import-boundary, order-safety and provenance-single-source contract tests.

AST-scans `src/sq`, `user_data/strategies` and `ops/*.py` for their imports
(no need to actually run them), so these checks hold even for modules whose
runtime dependencies (ccxt, freqtrade) may not be importable in every
environment this test suite runs in.
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_SQ = REPO_ROOT / "src" / "sq"
STRATEGIES_DIR = REPO_ROOT / "user_data" / "strategies"
OPS_DIR = REPO_ROOT / "ops"


def imported_modules(path: Path) -> set[str]:
    """Every top-level dotted module name this file imports (any depth)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # level == 0: an absolute import; a relative "from . import x"
            # within this project's own packages is never one of the
            # forbidden targets checked here.
            if node.module and node.level == 0:
                modules.add(node.module)
    return modules


def _imports(modules: set[str], target: str) -> bool:
    return any(m == target or m.startswith(target + ".") for m in modules)


# --- Jev isolation: the worker package sees no exchange, config or pandas --


def test_jev_package_imports_only_stdlib_and_itself():
    # sq.jev is what the credential-free jev-worker container runs. Offline
    # analysis of its records lives in sq.research.jev_evaluation instead.
    for path in sorted((SRC_SQ / "jev").rglob("*.py")):
        for module in imported_modules(path):
            root = module.split(".")[0]
            if root in sys.stdlib_module_names or _imports({module}, "sq.jev"):
                continue
            raise AssertionError(f"{path} imports {module!r}; sq.jev is stdlib + sq.jev only")


# --- research is a leaf: runtime code never depends on it ----------------


def test_only_research_imports_research():
    for path in sorted(SRC_SQ.rglob("*.py")):
        if path.is_relative_to(SRC_SQ / "research"):
            continue
        assert not _imports(imported_modules(path), "sq.research"), f"{path} imports sq.research"


# --- ops/*.py: host-side systemd scripts, stdlib-only ---------------------


def test_ops_scripts_are_stdlib_only():
    ops_files = sorted(OPS_DIR.glob("*.py"))
    assert ops_files, "expected at least one ops/*.py file to check"
    for path in ops_files:
        for module in imported_modules(path):
            root = module.split(".")[0]
            assert root in sys.stdlib_module_names, f"{path} imports non-stdlib {module!r}"


# --- strategies: no project package, no ccxt ------------------------------


def test_strategies_never_import_sq_or_exchange_code():
    # Strategies stay self-contained: the bot container does not mount src/,
    # and H1JevShadow's class source is part of every Jev candidate_id.
    forbidden = ("sq", "ccxt")
    strategy_files = sorted(STRATEGIES_DIR.glob("*.py"))
    assert strategy_files, "expected at least one strategy file to check"
    for path in strategy_files:
        modules = imported_modules(path)
        for target in forbidden:
            assert not _imports(modules, target), f"{path} imports {target}"


# --- ccxt is only imported where live/exchange access is the point --------


def test_only_live_and_proxy_check_import_ccxt():
    allowed = {
        SRC_SQ / "live" / "preflight.py",
        SRC_SQ / "live" / "reconcile.py",
        SRC_SQ / "research" / "proxy_check.py",
    }
    for path in sorted(SRC_SQ.rglob("*.py")):
        modules = imported_modules(path)
        if _imports(modules, "ccxt"):
            assert path in allowed, f"{path} imports ccxt but is not in the allowlist"


# --- nothing outside Freqtrade places, cancels or edits an order ---------

ORDER_MUTATING_FRAGMENTS = (
    "createorder",
    "createlimit",
    "createmarket",
    "cancelorder",
    "cancelall",
    "editorder",
)
PROJECT_CODE_DIRS = (SRC_SQ, OPS_DIR, STRATEGIES_DIR, REPO_ROOT / "research" / "strategies")


def test_no_project_code_names_an_order_mutating_method():
    # Normalized source text, so create_order, createOrder and cancel_all_orders
    # are all caught, in calls, getattr strings and comments alike.
    for directory in PROJECT_CODE_DIRS:
        for path in sorted(directory.rglob("*.py")):
            source = path.read_text(encoding="utf-8").replace("_", "").lower()
            for fragment in ORDER_MUTATING_FRAGMENTS:
                assert fragment not in source, f"{path} names an order-mutating method"


# --- the pinned image digest lives in exactly one live-config place -------


def _digest_from_compose() -> str:
    """The pinned image digest, read from compose.yaml's own anchor.

    Extracted with a generic sha256 pattern rather than hardcoded here, so
    this file itself is never a second place the literal digest string
    appears in (see the assertion below).
    """
    text = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    match = re.search(r"sha256:([0-9a-f]{64})", text)
    assert match, "compose.yaml has no sha256:<digest> image reference"
    return match.group(1)


def test_pinned_digest_appears_only_in_compose_and_the_historical_allowlist():
    digest = _digest_from_compose()

    allowlist_files = {
        REPO_ROOT / "compose.yaml",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "README.md",
    }
    allowlist_dirs = (
        REPO_ROOT / "research" / "experiments",
        REPO_ROOT / "docs",
    )

    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT)
        if relative.parts[0] == "user_data":
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path in allowlist_files or any(path.is_relative_to(d) for d in allowlist_dirs):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError, OSError:
            continue
        assert digest not in text, f"{path} contains the pinned image digest"
