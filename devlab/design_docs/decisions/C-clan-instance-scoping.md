# C-clan-instance-scoping
**type:** concept
**concept_id:** C-clan-instance-scoping
**date:** 2026-06-04
**decision_ref:** D-clan-template-memory-ownership-2026-06-01
**status:** active
**related:** C-clan-template

## The four knowledge tiers

| Tier           | Scope                            | Who reads                  | Who writes              |
|----------------|----------------------------------|----------------------------|-------------------------|
| **universal**  | All UU instances everywhere      | Any agent on any rack      | Human curation only     |
| **clan**       | This rack's shared layer         | All agents on this rack    | Memory-domain owner     |
| **agent**      | One agent type (e.g. igor)       | That agent type only       | That agent type         |
| **client**     | One user's private data          | That user's agents only    | That user's agents      |

A UU instance running a legal citation project receives universal and clan patterns
but NOT the sprint ticket history from another installation. Sprint tickets are
agent-tier (igor) or clan-tier (granny/build). Legal citation patterns would be
agent-tier on that installation, invisible to ours.

## Boundary rules

1. **Universal → clan** join: any rack can read universal. Writing requires a
   contribution protocol (not yet defined; gated on T-global-kb-git-repo).
2. **Clan → agent**: agents read clan freely. Reading another agent's tier requires
   an inter-agent knowledge request (see Bus protocol below).
3. **Agent → client**: client data is siloed. No cross-client reads. No exceptions.
4. **Memory ownership** declares the write boundary (see C-clan-template).

## Inter-agent knowledge request — bus protocol

When an agent needs access to another agent's tier-specific memories, it sends
a typed bus message rather than querying the DB directly. This keeps memory
isolation enforcement in one place (the owning agent) rather than in access
control rules scattered across the DB schema.

### Request message schema

```json
{
  "kind": "memory.access_request",
  "from_agent": "librarian-wild-0001",
  "to_agent":   "igor-wild-0001",
  "scope":      "agent",
  "query":      "recent escalation summaries on T-inference-*",
  "intent":     "populating recall index for inference tickets",
  "max_results": 5
}
```

**Fields:**
- `kind`: always `"memory.access_request"` — allows the owning agent to dispatch
- `from_agent`: requesting agent's instance ID
- `to_agent`: owning agent's instance ID (the one whose tier-specific memory is requested)
- `scope`: `"agent"` | `"client"` — which tier is being requested
- `query`: natural-language or structured query string
- `intent`: why the requesting agent needs this (logged; used for audit)
- `max_results`: optional cap (default 10)

### Response message schema

```json
{
  "kind": "memory.access_response",
  "request_id": "<echoes request envelope uuid>",
  "from_agent": "igor-wild-0001",
  "to_agent":   "librarian-wild-0001",
  "approved":   true,
  "memories":   [{"id": "...", "narrative": "...", "memory_type": "EPISODIC"}],
  "denied_reason": null
}
```

**Fields:**
- `approved`: true = memories included; false = denied_reason set, memories empty
- `denied_reason`: human-readable reason when denied (scope too broad, client data, etc.)

The owning agent decides approval policy. Default policy (v1): approve `scope=agent`
requests from same-rack agents; deny `scope=client` unconditionally.

### Implementation note

This protocol is a bus envelope pair (see §1.3 of TheoryOfOperation). Neither
the requesting agent nor the owning agent needs a shared DB connection — the
owning agent reads its own DB and returns results via the bus.

Current status: schema defined; implementation pending. The test below verifies
the request/response shape at the envelope level.

## Relationship to C-clan-template

C-clan-template defines the identity and memory inheritance model (template_id,
memory_domain). C-clan-instance-scoping defines the boundary rules and the
inter-agent protocol for crossing those boundaries. Together they answer:
- "who shares what by default?" (C-clan-template)
- "how does an agent ask for more?" (C-clan-instance-scoping)
