# D-facia-index-tree-2026-06-17
**title:** Facia index tree — librarian second-pass + tree manager object
**date:** 2026-06-17
**status:** open
**spawned_tickets:** T-facia-index-tree, T-tree-manager-object, T-librarian-facia-second-pass, T-consequence-facia-index

## Decision narrative
A new tree in TreeIndex whose nodes ARE the facia memories from all other trees — the "filing cabinet tabs" view. When Librarian first-pass search fails, he reaches for the facia index tree as a second pass: "what might be close?" Each individual tree keeps its own facia current (not a centralised sweep). A new tree manager object (code puppet) loads a tree's facia and handles operations on self: versioning, calving at 5K nodes, link updates after calves. This object does not yet exist. Observable: Librarian logs facia_index_hit=true when second-pass fires. Whole-text search is aware of the facia index tree as a fallback root — not the first place to look, but the second on miss.

## Hypothesis
When Librarian first-pass fails, second-pass against the facia index returns relevant results more often than returning nothing.

## Measurement Signal
Librarian logs facia_index_hit=true; Akien observes "found it on second pass" in cases where first pass would have returned empty.

## Goal Link
none: factory-of-factories is the north star vision, no G-id filed yet
