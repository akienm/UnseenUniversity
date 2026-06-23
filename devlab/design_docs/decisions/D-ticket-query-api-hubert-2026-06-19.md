# D-ticket-query-api-hubert-2026-06-19

**title:** Ticket query API in ticket_store; ticket-query tools relocate Librarian→Hubert
**date:** 2026-06-19
**status:** open
**spawned_tickets:** T-ticket-store-query-api, T-ticket-tools-to-hubert, T-consequence-ticket-query-api-hubert

## Decision narrative

Tickets used to live in Igor's memory (clan.memories), moved to devlab/runtime/memory/tickets/ (D-build-queue-filesystem-first-2026-06-19). The Librarian owned ticket-query tools because tickets lived in the knowledge corpus it manages — that premise is gone. Hubert owns the dev process; the build queue is the dev process; ticket-query tools belong to Hubert.

The second half: ticket_store.py is the filesystem queue chokepoint but exposes no named query surface. Every caller reimplements the same inline list-comprehensions. Akien's framing (2026-06-19): "that lets us build new skills on top of that." Add `next/text/by_status/by_worker/by_gate/by_decision` as named helpers — named in cognition terms so the surface survives when Igor's cognition rides the same model. `ts.next()` is the canonical implementation; `cmd_next` in cc_queue.py delegates to it.

Alternatives rejected: generic ranked-search (overbuilt for a structured queue); leave in Librarian (wrong owner post-cutover; Librarian owns knowledge corpus not the dev pipeline).

## Hypothesis

After these tickets ship, any CC/DS skill or device can call `ticket_store.next()`, `text()`, `by_status()` directly without reimplementing the query; the Librarian returns zero ticket-typed results; Hubert owns the ticket-query MCP surface.

## Measurement Signal

`ticket_store.text("granny")` returns results; `grep -rn "_search_tickets\|kind.*ticket" devices/librarian/` returns zero active hits; Hubert device has a ticket-query MCP tool callable; `cmd_next` delegates to `ts.next()` (verified by agreeing on a test store); `node_registry` has no ticket entry.

## Goal Link

none — serves the system self-improvement substrate goal: new skills and future devices call the query API without re-deriving it; the mechanism (not outputs) is what lets the system build new capabilities on top.
