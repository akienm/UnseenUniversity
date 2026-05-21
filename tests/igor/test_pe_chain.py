"""
test_pe_chain.py — Registration coverage for pe_* per-step MCPCALL tools.

T-engram-mcpcall-register-pe-steps: the 11 ENGRAM_CODE_* engram payloads
call one of pe_entry_init / pe_read_ticket / pe_plan / pe_filter /
pe_situate / pe_observe / pe_hypothesize / pe_implement / pe_test / pe_probe /
pe_close_loop via MCPCALL. Before this ticket, only the high-level wrappers
(run_pe_chain, run_pe_plan, run_pe_filter, run_pe_probe) were registered, so
every per-step MCPCALL hit the unknown-tool branch silently.

These tests verify:
  1. Each of the 11 names is resolvable via the tool registry.
  2. The registered fn is callable with zero args (how MCPCALL dispatches).
  3. A dry registry.execute for each name does NOT return the
     "Unknown tool" error message that registry.execute() emits when a
     name is absent (this is the registry-level equivalent of the
     "unknown tool" warning node_executor surfaces).

The whole-chain integration test lives on T-worker-dispatch-validation;
this file covers registration only.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Importing pe_chain runs its module-level registry.register calls.
import wild_igor.igor.tools.pe_chain  # noqa: F401
from lab.utility_closet.registry import registry

PE_STEP_NAMES = [
    "pe_entry_init",
    "pe_read_ticket",
    "pe_plan",
    "pe_filter",
    "pe_situate",
    "pe_observe",
    "pe_hypothesize",
    "pe_implement",
    "pe_test",
    "pe_probe",
    "pe_close_loop",
]

# Three names are excluded from the execute-level check:
#
#   pe_test / pe_close_loop — heavy-dispatch: pe_test runs the full pytest
#     suite; pe_close_loop makes a tier-2 LLM call and recurses.
#
#   pe_entry_init — queries the live Postgres DB for an active goal.  When
#     Igor has an in-flight ticket, pe_entry_init({}) returns with the real
#     ticket_id logged to pe_chain.log (shared with Igor's running process),
#     making the log look like a ghost pe_chain invocation.  The registration
#     guarantee is already covered by test_tool_resolvable +
#     test_tool_fn_is_callable — registry.get returning non-None is necessary
#     and sufficient for registry.execute to NOT hit the unknown-tool branch.
DRY_MCPCALL_NAMES = [
    n for n in PE_STEP_NAMES if n not in {"pe_test", "pe_close_loop", "pe_entry_init"}
]


class TestPeStepRegistration:
    @pytest.mark.parametrize("name", PE_STEP_NAMES)
    def test_tool_resolvable(self, name):
        """registry.get(name) returns a Tool (not None) for every pe_* step."""
        tool = registry.get(name)
        assert tool is not None, f"pe_* step {name!r} is not registered"

    @pytest.mark.parametrize("name", PE_STEP_NAMES)
    def test_tool_fn_is_callable(self, name):
        """Each registered tool has a callable fn."""
        tool = registry.get(name)
        assert tool is not None
        assert callable(tool.fn), f"{name}.fn is not callable"

    @pytest.mark.parametrize("name", PE_STEP_NAMES)
    def test_tool_has_description(self, name):
        """Each registered tool has a non-empty description (for reasoner UX)."""
        tool = registry.get(name)
        assert tool is not None
        assert tool.description, f"{name} has no description"

    @pytest.mark.parametrize("name", PE_STEP_NAMES)
    def test_tool_has_object_schema(self, name):
        """Parameters schema is a JSON Schema object (what MCP adapters need)."""
        tool = registry.get(name)
        assert tool is not None
        assert (
            tool.parameters.get("type") == "object"
        ), f"{name} parameters schema is not an object"
        assert "properties" in tool.parameters

    @pytest.mark.parametrize("name", DRY_MCPCALL_NAMES)
    def test_dry_mcpcall_not_unknown(self, name):
        """
        registry.execute(name, {}) must NOT return the 'Unknown tool' error.

        node_executor.MCPCALL dispatches with an empty args dict today
        (args_basket_key '_basket_args' is never populated). Before this
        ticket, registry.get returned None for these names and execution
        fell through to the unknown-tool path. Post-registration, the
        call is routed to the real fn; any step-level failure (missing
        active goal, no ticket, etc.) is fine — it just must not be
        'Unknown tool'.
        """
        result = registry.execute(name, {})
        result_str = str(result)
        assert (
            "Unknown tool" not in result_str
        ), f"MCPCALL {name} still hits unknown-tool branch: {result_str[:120]!r}"
