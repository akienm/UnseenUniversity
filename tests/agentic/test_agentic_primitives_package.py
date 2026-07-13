"""
The shared execution primitives live in unseen_university/agentic/ — not in the proxy.

T-agentic-primitives-package (D-domains-general-with-device-owned-specializations-2026-07-08,
Q2 resolved by Akien: "unseen_university/agentic/ — a top-level non-device package, peer to
the existing unseen_university/capabilities/").

WHY THIS IS STRUCTURAL, NOT BEHAVIORAL: the claim of this ticket is a LAYERING claim, and a
behavioral suite cannot fail on a layering violation — the code runs identically from either
path. So this reads the import graph (AST), the way test_proxy_domain_layering.py does.

The primitives were misplaced TWICE: wrong layer (inside the inference proxy, which is a
routing LEAF) and wrong scope (hung off the domain base, which asserted that every coding
consumer must be driven turn-by-turn — false for aider and for CC). AgenticLoop cannot be
DS-private either: minion/tool_loop.py imports it too. It is a shared execution PRIMITIVE that
a domain specialization CONSUMES; it is neither a domain nor part of the proxy.

The four assertions below are chosen so that each failure mode of a hollow build is caught:
  (1) an empty/stub agentic package         -> the public-API import fails
  (2) a COPY instead of a MOVE              -> the residue scan finds the old modules
  (3) a move with the old import paths left -> the old-path scan finds them
  (4) a move that inverts the layering      -> agentic must not import the domain layer
"""

from __future__ import annotations

import ast
from pathlib import Path

import unseen_university

#: The execution primitives, by module basename. These must not exist under the proxy.
PRIMITIVE_MODULES = {"agentic_loop", "architect_editor", "block_apply", "edit_format"}

#: The domain layer sits ABOVE agentic: a domain CONSUMES a primitive, never the reverse.
#: (`loop` is agentic's own module and is deliberately not listed.)
DOMAIN_LAYER = ("domains", "base", "general", "coding")

_UU_ROOT = Path(unseen_university.__file__).parent
_PROXY_DIR = _UU_ROOT / "devices" / "inference"
_AGENTIC_DIR = _UU_ROOT / "agentic"


def _imported_modules(path: Path) -> set[str]:
    """Every module name this file imports — including imports nested inside functions.

    Walks the whole AST, not just the top level: a function-local import is the classic way a
    layering violation hides (it is what kept the old device -> domains -> device cycle from
    exploding at import time).

    A file that is not valid Python 3 is skipped, and the skip is narrow on purpose: the only
    such file in the tree is the VENDORED python-2 DRM tool under devices/igor/tools/ebook_drm/,
    which cannot import our primitives because it cannot be imported at all. Catching only
    SyntaxError (not bare Exception) keeps a real unreadable-file bug loud.
    """
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_execution_primitives_live_in_agentic_not_the_proxy() -> None:
    """PROOF: the primitives moved to unseen_university/agentic/, and the graph proves it.

    Pre-move there is no unseen_university.agentic package at all, so the public-API import
    below raises ImportError and this asserts a clean RED.
    """
    # (1) The package exists and actually exposes the primitives — a stub package fails here.
    try:
        from unseen_university.agentic import (
            AgenticLoop,
            ArchitectEditorFlow,
            LoopResult,
            NativeToolCodec,
            apply_blocks_to_dir,
        )
    except ImportError as exc:
        raise AssertionError(
            f"unseen_university.agentic does not expose the execution primitives: {exc}"
        ) from exc

    assert all(
        callable(obj) or isinstance(obj, type)
        for obj in (AgenticLoop, ArchitectEditorFlow, LoopResult, NativeToolCodec, apply_blocks_to_dir)
    ), "unseen_university.agentic exports names that are not the real primitives"

    # (2) The proxy package contains NO execution primitive — a copy-instead-of-move fails here.
    residue = sorted(
        str(p.relative_to(_UU_ROOT))
        for p in _py_files(_PROXY_DIR)
        if p.stem in PRIMITIVE_MODULES
    )
    assert not residue, (
        f"execution primitives still live inside the inference proxy: {residue} — the proxy is a "
        f"routing LEAF; a primitive there is the original misplacement this ticket undoes"
    )

    # (3) NOTHING imports a primitive from the retired path — a half-swept move fails here.
    stale: list[str] = []
    for path in _py_files(_UU_ROOT) + _py_files(Path(unseen_university.__file__).parent.parent / "tests"):
        for mod in _imported_modules(path):
            if "inference.domains" in mod and mod.rsplit(".", 1)[-1] in PRIMITIVE_MODULES:
                stale.append(f"{path.name}: {mod}")
    assert not stale, f"imports still reach a primitive at its retired proxy path: {sorted(stale)}"

    # (4) Direction guard: agentic is BELOW the domain layer and must never import it.
    inverted: list[str] = []
    for path in _py_files(_AGENTIC_DIR):
        for mod in _imported_modules(path):
            tail = mod.rsplit(".", 1)[-1]
            if mod.startswith("unseen_university") and tail in DOMAIN_LAYER:
                inverted.append(f"{path.name}: {mod}")
    assert not inverted, (
        f"unseen_university/agentic/ imports the DOMAIN layer: {sorted(inverted)} — a domain "
        f"consumes a primitive, never the reverse; this re-creates the cycle in a new place"
    )

    # (5) The real consumers reach the primitives through the new home.
    consumers = [
        _UU_ROOT / "devices" / "minion" / "tool_loop.py",
        _UU_ROOT / "devices" / "inference" / "domains" / "base.py",
    ]
    for consumer in consumers:
        assert any(
            m.startswith("unseen_university.agentic") for m in _imported_modules(consumer)
        ), f"{consumer.name} does not import its execution primitive from unseen_university.agentic"
