"""
The inference proxy is a LEAF: it routes and dispatches, and imports nothing above it.

T-inference-break-proxy-domain-cycle. Before this, `device.py` imported
`domains.resolve_domain` to call `BaseDomain.select()`, while `domains/base.py` imported
`agentic_loop`, which reached back with a function-local
`from ...device import InferenceDevice` and constructed one. That is a cycle:

    device.py  ->  domains  ->  agentic_loop  ->  device.py

The function-local import is what kept it from exploding at import time — a lazy import inside
a function is the classic smell of a layering violation, not a fix for one.

Root cause: BaseDomain glued two opposite-facing responsibilities. `select()` was consumed BY
the proxy; `run()` CONSUMES the proxy. And `select()` was a pure pass-through — it built a
RouteRequest from the domain's own name, which the proxy already had as `request.domain`.

The correct layering has exactly one direction:

    worker  ->  domain (loop + escalation + prompts)  ->  inference proxy (routing + dispatch)

These tests pin that direction structurally, by reading the import graph — so a future edit
cannot quietly re-create the cycle and still pass a behavioral suite.
"""

from __future__ import annotations

import ast
from pathlib import Path

import unseen_university.devices.inference.device as device_mod

#: The routing core: the modules that ARE the proxy. None of them may import anything from
#: the domain layer (which sits above them), directly or lazily.
ROUTING_CORE = [
    "device.py",
    "rules_engine.py",
    "sources.py",
    "models_registry.py",
    "connections.py",
    "policy.py",
    "dimensions.py",
    "routing_buckets.py",
]

#: Modules that live ABOVE the proxy. The proxy must never name them.
ABOVE_THE_PROXY = ("domains", "agentic_loop", "architect_editor")

_INFERENCE_DIR = Path(device_mod.__file__).parent


def _imported_names(path: Path) -> set[str]:
    """Every module name this file imports — including imports nested inside functions.

    Walks the whole AST rather than just the top level, because the cycle this test guards
    was hidden precisely by a function-local import.
    """
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_routing_core_does_not_import_the_domain_layer():
    """The proxy never imports a domain or the agentic loop — no cycle, at any nesting depth."""
    violations: list[str] = []
    for filename in ROUTING_CORE:
        path = _INFERENCE_DIR / filename
        assert path.exists(), f"routing-core module missing: {filename}"
        for imported in _imported_names(path):
            for banned in ABOVE_THE_PROXY:
                # match 'x.domains', 'x.domains.y', 'x.agentic_loop' — not 'domain_prompts'
                parts = imported.split(".")
                if banned in parts:
                    violations.append(f"{filename} imports {imported}")
    assert not violations, (
        "the inference proxy must not import the layer above it (domain / agentic loop) — "
        "that is the device -> domains -> agentic_loop -> device cycle:\n  "
        + "\n  ".join(sorted(violations))
    )


def test_domain_object_has_no_routing_method():
    """A domain consumes the proxy; it is not something the proxy calls to pick a model."""
    from unseen_university.devices.inference.domains.base import BaseDomain

    assert not hasattr(BaseDomain, "select"), (
        "BaseDomain.select() was a routing method on a consumer object — it forced the proxy "
        "to import a domain in order to route. Routing belongs to dimensions.route_request + "
        "rules_engine.resolve; `domain` is just a dimension string."
    )


#: Execution machinery — what it means to *do* the work. Owned by the domain object, never by
#: the proxy. The proxy routes an inference request and dispatches it; it is not a coding agent.
EXECUTION_MACHINERY = (
    "agentic_loop.py",
    "architect_editor.py",
    "block_apply.py",
    "edit_format.py",
    "stuck_ladder.py",
    "domain_prompts.py",
)


def test_proxy_package_contains_no_execution_machinery():
    """The agentic loop and its edit machinery live in the domain layer, not in the proxy.

    'The agentic loops do not go in the proxy. They go in domain objects.' (Akien, 2026-07-08)
    A proxy that also contains a 900-line agentic loop, an architect/editor split, and an edit
    dialect engine is not a proxy — it is a coding agent wearing a router as a hat.
    """
    stray = [m for m in EXECUTION_MACHINERY if (_INFERENCE_DIR / m).exists()]
    assert not stray, (
        "execution machinery must live in the domain layer, not the inference proxy: "
        + ", ".join(stray)
    )
    # ...and it really is in the domain layer.
    for m in EXECUTION_MACHINERY:
        assert (_INFERENCE_DIR / "domains" / m).exists(), f"{m} missing from the domain layer"


def test_proxy_package_ships_no_test_module():
    """A shipped runtime package must not carry a pytest module (it imported pytest at runtime)."""
    assert not (_INFERENCE_DIR / "test_inference.py").exists()


def test_the_proxy_builds_its_own_route_request():
    """The proxy owns routing end to end: dimensions -> RouteRequest -> resolve."""
    from unseen_university.devices.inference.dimensions import RouteRequest, route_request

    assert hasattr(device_mod.InferenceDevice, "_route")
    req = route_request(task_class="worker", domain="coding")
    assert isinstance(req, RouteRequest)
    # the task_class -> ticket_tier bridge lives in the routing layer, not on a domain
    assert req.ticket_tier == "builder" and req.domain == "coding"
    assert req.seed_difficulty == "code"
