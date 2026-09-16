"""Import the real ToolRush plugin modules under test.

The plugin ships inside a Hermes install, so ``tools.*`` / ``agent.*`` resolve
against ``v2/plugin/lib`` and a handful of Hermes-side modules are expected to
exist. Tests run outside Hermes, so the *host* modules ToolRush consumes are
stubbed here while every ToolRush module is imported for real -- no
reimplementation of production logic in the tests.
"""
import contextlib
import importlib
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "v2" / "plugin"
PLUGIN_LIB = PLUGIN_DIR / "lib"
SCRIPTS_DIR = REPO_ROOT / "scripts"


def export_dump_stub(target, exclusions):
    """Stand-in for Hermes' ``_export_dump_excluding_session_vars``.

    build_frame() only flattens this clause when it starts with ``{ ( `` and
    contains ``) || true; }``; the trailing ``;`` before ``)`` is what keeps the
    flattened form valid bash. Mirroring that shape here keeps the contract
    tests honest about the rewrite production code actually performs.
    """
    unsets = "".join("unset %s; " % name for name in exclusions)
    return "{ ( " + unsets + "export -p > " + target + "; ) || true; }"


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _install_host_stubs():
    """Register the Hermes-side modules the plugin imports, if absent."""
    if str(PLUGIN_LIB) not in sys.path:
        sys.path.insert(0, str(PLUGIN_LIB))

    try:  # a real Hermes install always wins
        importlib.import_module("hermes_cli._subprocess_compat")
    except ImportError:
        package = _module("hermes_cli")
        package.__path__ = []
        compat = _module("hermes_cli._subprocess_compat", windows_hide_flags=lambda: 0)
        package._subprocess_compat = compat
        sys.modules.setdefault("hermes_cli", package)
        sys.modules["hermes_cli._subprocess_compat"] = compat

    if "tools.environments.base" not in sys.modules:
        environments = _module("tools.environments")
        environments.__path__ = []
        base = _module(
            "tools.environments.base",
            _export_dump_excluding_session_vars=export_dump_stub,
        )
        environments.base = base
        sys.modules["tools.environments"] = environments
        sys.modules["tools.environments.base"] = base

    if "agent.thread_scoped_output" not in sys.modules:
        sys.modules["agent.thread_scoped_output"] = _module(
            "agent.thread_scoped_output",
            thread_scoped_silence=contextlib.nullcontext,
        )

    if "tools.thread_context" not in sys.modules:
        sys.modules["tools.thread_context"] = _module(
            "tools.thread_context",
            propagate_context_to_thread=lambda fn: fn,
        )


_install_host_stubs()


@pytest.fixture(scope="session")
def toolrush_process():
    return importlib.import_module("tools.toolrush_process")


@pytest.fixture(scope="session")
def toolrush_shell():
    return importlib.import_module("tools.toolrush_shell")


@pytest.fixture(scope="session")
def toolrush_rpc():
    return importlib.import_module("tools.toolrush_rpc")


@pytest.fixture(scope="session")
def toolrush_runtime():
    return importlib.import_module("tools.toolrush_runtime")


@pytest.fixture(scope="session")
def toolrush_admission():
    return importlib.import_module("agent.toolrush_admission")


@pytest.fixture(scope="session")
def package_release():
    """Import scripts/package_release.py as a module (not a copy of its logic)."""
    spec = importlib.util.spec_from_file_location(
        "toolrush_package_release", SCRIPTS_DIR / "package_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
