"""
agentic — the shared execution PRIMITIVES: the loop, the tool codecs, the editor.

D-domains-general-with-device-owned-specializations-2026-07-08 (Q2, confirmed by Akien):
a top-level non-device package, peer to `unseen_university/capabilities/`.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
These are primitives a domain CONSUMES. They are not a domain and they are not the proxy:

    worker -> domain (escalation walk + prompts) -> agentic (how one attempt is executed)
                                                 -> inference proxy (routing + dispatch)

They were misplaced twice, and both misplacements are worth remembering because each looked
reasonable at the time:

1. WRONG LAYER — they lived inside `devices/inference/`, the routing proxy. The proxy is a
   LEAF: it routes and dispatches and imports nothing above it. An execution loop that
   *calls* the proxy cannot live inside it without making `device -> domains -> agentic_loop
   -> device` a cycle (which is exactly what happened, hidden behind a function-local import).
2. WRONG SCOPE — `AgenticLoop` hung off the domain base, which asserted that every coding
   consumer must be driven turn-by-turn. False for two of the three: aider brings its own
   build loop, and CC *is* a loop. Driving the model turn-by-turn is DS's specialization, not
   what coding MEANS.

And it cannot be DS-private either: `devices/minion/tool_loop.py` imports the loop too. Shared
by two devices, owned by neither — that is the definition of a primitive, and it gets a home.

DIRECTION RULE (pinned structurally by tests/agentic/test_agentic_primitives_package.py):
this package must never import the domain layer. A domain consumes a primitive; a primitive
knows nothing of domains. The device imports below are all function-local by DEFERRAL, so
importing this package never eagerly pulls in a psycopg2-bound device — the hermetic tests
depend on that.
"""

from __future__ import annotations

from unseen_university.agentic.architect_editor import (
    ArchitectEditorFlow,
    get_file_mentions,
)
from unseen_university.agentic.block_apply import (
    BlockApplyResult,
    apply_blocks_to_dir,
    apply_wholefile_to_dir,
    build_repair_message,
    failure_class,
    parse_wholefile,
)
from unseen_university.agentic.loop import (
    HISTORY_WINDOW_TURNS,
    LOOP_AVAILABILITY,
    LOOP_COST_EXCEEDED,
    LOOP_DONE,
    LOOP_ESCALATE,
    LOOP_MAX_TURNS,
    LOOP_NO_CAPABLE_MODEL,
    TOOL_DEFINITIONS,
    AgenticLoop,
    LoopResult,
    NativeToolCodec,
    TextToolCodec,
    execute_tool,
    no_source_loop_outcome,
    parse_terminal_envelope,
)

__all__ = [
    "HISTORY_WINDOW_TURNS",
    "LOOP_AVAILABILITY",
    "LOOP_COST_EXCEEDED",
    "LOOP_DONE",
    "LOOP_ESCALATE",
    "LOOP_MAX_TURNS",
    "LOOP_NO_CAPABLE_MODEL",
    "TOOL_DEFINITIONS",
    "AgenticLoop",
    "ArchitectEditorFlow",
    "BlockApplyResult",
    "LoopResult",
    "NativeToolCodec",
    "TextToolCodec",
    "apply_blocks_to_dir",
    "apply_wholefile_to_dir",
    "build_repair_message",
    "execute_tool",
    "failure_class",
    "get_file_mentions",
    "no_source_loop_outcome",
    "parse_terminal_envelope",
    "parse_wholefile",
]
