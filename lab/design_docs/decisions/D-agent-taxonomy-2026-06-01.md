# D-agent-taxonomy-2026-06-01
**title:** Three-class agent taxonomy: utility / specialized / general-purpose
**date:** 2026-06-01
**status:** open
**spawned_tickets:** T-agent-taxonomy-concept, T-device-registry-agent-class, T-consequence-agent-taxonomy
**goal_link:** G-uu-platform
**concept_links:** C-agent-taxonomy (new — created by this decision)

## Decision narrative
Establish a formal three-class taxonomy for rack agents: utility (single bounded capability, composable, function-like — Scraps, GoogleSecretary), specialized (domain workflow expert, orchestrates within its lane — Granny, Nanny), and general-purpose (broad reasoning, novel problem-solving — Igor, CC). Librarian sits at the edge: utility in function but wider scope; annotated as 'platform utility' within the utility class. The taxonomy is primarily vocabulary — when Akien says "utility agent" there's a canonical definition to point at. Practically: every device self-declares agent_class in who_am_i(); datacenter_manifest exposes it; Granny routing can eventually use it to route non-coding requests to utility agents.

## Hypothesis
Every device in the registry carries an agent_class field; datacenter_manifest returns the class for each device.

## Measurement Signal
datacenter_manifest JSON includes agent_class for every registered device; no device returns unknown or missing.

## Goal Link
G-uu-platform — self-describing rack where devices declare their capabilities and class.

## Concept Links
C-agent-taxonomy — this decision creates the concept node.
