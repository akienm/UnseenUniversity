"""
hot_reload.py — Hot module reload without restarting Igor.

reload_module(module_name):
    importlib.reload() a loaded module by dotted name.
    Tool modules re-register themselves automatically on reload
    (their registry.register() calls re-execute at module level).

    HIGH inertia modules are blocked — they require a restart.

list_loaded_modules():
    Show all currently loaded wild_igor modules so Igor knows what
    can be reloaded.

Part of #207.
"""
from __future__ import annotations

import importlib
import sys
from .registry import Tool, registry


# ── Inertia guard ─────────────────────────────────────────────────────────────
# Modules whose reload would corrupt live state or violate architectural safety.

_BLOCKED_PREFIXES = (
    "wild_igor.igor.brainstem",
    "wild_igor.igor.memory.models",        # dataclass defs — isinstance breaks on reload
    "wild_igor.igor.memory.cortex",        # owns live DB proxy
    "wild_igor.igor.cognition.reasoners.base",
    "wild_igor.igor.tools.registry",       # registry itself — would wipe all tools
    "wild_igor.igor.tools.hot_reload",     # this module
)


def reload_module(module_name: str) -> str:
    """
    Hot-reload a module by its dotted name. Returns a status string.
    Tool modules re-register in the global registry automatically.
    HIGH inertia modules are blocked and require a full restart.
    """
    for blocked in _BLOCKED_PREFIXES:
        if module_name == blocked or module_name.startswith(blocked + "."):
            return (
                f"Blocked: '{module_name}' is HIGH inertia. "
                f"A restart is required to change it."
            )

    if module_name not in sys.modules:
        # Try a short-form lookup: if user typed "tools.filesystem", expand it
        candidates = [k for k in sys.modules if k.endswith("." + module_name) or k == module_name]
        if len(candidates) == 1:
            module_name = candidates[0]
        elif len(candidates) > 1:
            return (
                f"Ambiguous module name '{module_name}'. "
                f"Did you mean one of: {', '.join(sorted(candidates))}?"
            )
        else:
            return (
                f"Module '{module_name}' is not loaded. "
                f"Use list_loaded_modules() to see what's available."
            )

    before_tools = set(registry._tools.keys())

    try:
        module = sys.modules[module_name]
        importlib.reload(module)
    except Exception as exc:
        return f"Error reloading '{module_name}': {exc}"

    after_tools  = set(registry._tools.keys())
    new_tools    = sorted(after_tools - before_tools)
    updated_tools = sorted(after_tools & before_tools)

    parts = [f"Reloaded '{module_name}'."]
    if new_tools:
        parts.append(f"New tools: {new_tools}.")
    if updated_tools:
        parts.append(f"Updated tools: {updated_tools}.")
    if not new_tools and not updated_tools:
        parts.append("No tool changes detected.")

    return " ".join(parts)


def list_loaded_modules() -> str:
    """
    List all currently loaded wild_igor modules by dotted name.
    Use this to find the exact name to pass to reload_module().
    """
    modules = sorted(
        k for k in sys.modules
        if k.startswith("wild_igor.igor")
        and sys.modules[k] is not None
    )
    if not modules:
        return "No wild_igor modules found in sys.modules."
    return "\n".join(modules)


# ── Register tools ─────────────────────────────────────────────────────────────

registry.register(Tool(
    name="reload_module",
    description=(
        "Hot-reload a Python module by dotted name without restarting Igor. "
        "Tool modules re-register automatically after reload. "
        "HIGH inertia modules (brainstem, memory.models, cortex) are blocked. "
        "Use list_loaded_modules() to find the exact module name. "
        "Example: reload_module('wild_igor.igor.tools.filesystem')"
    ),
    parameters={
        "type": "object",
        "properties": {
            "module_name": {
                "type": "string",
                "description": "Dotted module name, e.g. 'wild_igor.igor.tools.filesystem'",
            },
        },
        "required": ["module_name"],
    },
    fn=reload_module,
))

registry.register(Tool(
    name="list_loaded_modules",
    description=(
        "List all currently loaded wild_igor modules by dotted name. "
        "Use this to find the exact name to pass to reload_module()."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=list_loaded_modules,
))
