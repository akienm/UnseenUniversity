# C-clan-template
**type:** concept
**concept_id:** C-clan-template
**date:** 2026-06-04
**decision_ref:** D-clan-template-memory-ownership-2026-06-01
**status:** active

## Definition

A **clan template** is the shared identity and baseline memory layer for a family of
agent instances. When an agent declares `template_id: igor`, it inherits:
- Clan-level memories (universal patterns, bootstrap sequences, shared procedures)
- The clan's memory scope (all agents in the clan read/write `clan.*` DB schema)
- The template's capability profile (allowed devices, ACL shape)

Individual instances layer their own memories on top. Two Igor instances sharing the
same `template_id: igor` see the same clan-layer memories but have separate
agent-layer memories. A second Igor cannot read the first's personal memories by
default — it can only read clan-level patterns.

## Memory ownership model

Memory ownership is declared per-device in the profile (`memory_domain` field).
The owning device is the canonical writer for that domain. Other devices read only
via explicit inter-agent request protocol (see C-clan-instance-scoping).

| Device    | memory_domain          | What it owns                                   |
|-----------|------------------------|------------------------------------------------|
| igor      | cognition              | NE cycles, ring memories, personal milieu      |
| granny    | build                  | Sprint outcomes, escalation history, queue ops |
| vetinari  | task_management        | Project status, stakeholder tracking, roadmap  |
| librarian | knowledge_retrieval    | Indexed recall, research results               |
| cc        | session                | CC session transcripts, sprint logs            |

## Rack namespace

The rack has its own IMAP namespace segment: `rack.*` (distinct from `igor.*` in
the shared IMAP server). This prevents the rack infrastructure from co-opting
TheIgors' shared namespace. Devices register under `rack.{device_id}`.

Current status: `rack.*` namespace is declared as the target shape; migration from
`comms://` to `rack.{id}` is a future infrastructure ticket.

## Relationship to C-clan-instance-scoping

C-clan-instance-scoping defines the global/local/agent/client boundary.
C-clan-template defines the identity and memory inheritance model.
Together they answer: "who shares what, and how do they ask for more?"

## Implementation notes (as of 2026-06-04)

- `template_id` field added to `config/profiles/igor.yaml`
- `memory_domain` field added to `config/profiles/granny.yaml` and `config/profiles/vetinari.yaml`
- Palace concept node at `lab/design_docs/decisions/C-clan-template.md` (this file)
- Full memory scope isolation (T-clan-instance-scoping) is a separate ticket
