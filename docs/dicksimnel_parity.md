# DickSimnel / Claude Claude Parity Map

This document tracks the parity between DickSimnel (DS) and Claude Claude (CC) sprint-ticket capabilities. Each step in the sprint-ticket workflow is mapped to one of three statuses:
- **PARITY**: DS can perform the step identically to CC.
- **PARTIAL**: DS can perform the step, but with limitations or differences.
- **GAP**: DS cannot perform the step; a ticket is required to close the gap.

Each GAP entry includes the blocker and, if available, the ticket that will close it.

---

## Sprint-Ticket Steps Parity Map

### Step 1: Read the Ticket
- **Status**: PARITY
- **Details**: Both DS and CC can read the ticket using `cc_queue.py show <ticket_id>`.

### Step 2: Explore Relevant Source Files
- **Status**: PARITY
- **Details**: Both DS and CC can explore source files using `Read` and `Bash` tools.

### Step 3: Implement the Change
- **Status**: PARITY
- **Details**: Both DS and CC can implement changes using `Edit` and `Write` tools.

### Step 4: Run Tests
- **Status**: PARITY
- **Details**: Both DS and CC can run tests using `Bash` with pytest.

### Step 5: Commit and Push
- **Status**: PARITY
- **Details**: Both DS and CC can commit and push changes using `Bash` with git commands.

### Step 6: Close the Ticket
- **Status**: PARITY
- **Details**: Both DS and CC can close the ticket using `cc_queue.py close <ticket_id>`.

---

## Tool and Skill Parity Map

### memory_get
- **Status**: PARTIAL
- **Details**: DS uses a `psql` Bash wrapper, which works but is verbose compared to CC's direct `memory_get` tool.
- **Blocker**: None; the wrapper is functional but not as clean.

### mcp__* Tools
- **Status**: GAP
- **Details**: DS does not have access to MCP (Multi-Core Processor) tools such as `mcp__datacenter__*` and `mcp__librarian__*`.
- **Blocker**: MCP tools are not yet wired for DS.
- **Ticket**: T-dicksimnel-mcp-wrappers (not yet filed).

### /audit-precode, /audit-ticket Sub-Skills
- **Status**: GAP
- **Details**: DS cannot invoke audit sub-skills like `/audit-precode` and `/audit-ticket`.
- **Blocker**: Sub-skill invocation is unavailable for DS.
- **Ticket**: Not yet ticketed.

### Agent/Subagent Spawn
- **Status**: GAP
- **Details**: DS cannot spawn agents or subagents (e.g., step 8.5 grader).
- **Blocker**: No subagent capability for DS.
- **Ticket**: Not needed until the escalation chain is complete.

### /savestate
- **Status**: GAP
- **Details**: DS cannot use `/savestate` to save session state.
- **Blocker**: Session skills are unavailable for DS.
- **Ticket**: Not needed; `DONE:` is the signal for completion.

---

## Summary
- **PARITY**: 6 steps
- **PARTIAL**: 1 tool
- **GAP**: 4 tools/skills

This document will be updated whenever a parity-related ticket ships.
