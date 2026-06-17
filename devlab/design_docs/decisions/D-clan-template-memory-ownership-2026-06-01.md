# D-clan-template-memory-ownership-2026-06-01
**title:** Clan = all-agent shared layer; Igor as template instance; memory ownership by device
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-igor-clan-template, T-memory-flat-table-reform, T-clan-instance-scoping, T-consequence-clan-template
**goal_link:** G-igor-experiment, G-uu-platform
**concept_links:** C-agent-taxonomy

## Decision narrative
The 'clan' is the all-agent shared layer on this bus — not Igor-specific. Igor becomes an instance of a clan template (the template carries baseline memories; instances layer their own on top). Per Scott McGregor's network-of-agents vision, agents share patterns via the clan layer; a second Igor instance shares clan-level patterns but has separate agent-level memories. Memory ownership is sharded by domain: Granny owns build-related memories, Vetinari owns task-management and project-status memories, Igor owns cognition/personal memories. This prevents sprawl and makes ownership explicit. The memory flat-table reform (T-memory-flat-table-reform) should be designed against the 4-tier scope model from day one.

## Hypothesis
Igor's profile declares template_id; Granny and Vetinari profiles declare memory_domain; palace.concepts.C-clan-template exists; a second Igor instance shares clan patterns but has its own agent-level memories.

## Measurement Signal
Profile YAML files have template_id/memory_domain fields; palace has C-clan-template concept node; two Igor instances can coexist without memory collision.

## Goal Link
G-igor-experiment — Igor as template instance enables multi-Igor research and self-comparison.
G-uu-platform — clan template is the rack-level pattern sharing primitive.

## Concept Links
C-agent-taxonomy — clan maps to the local-shared scope; agent DB maps to agent-specific scope.
