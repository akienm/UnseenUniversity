# D-json-envelope-principle-2026-06-12
**title:** All agent I/O envelopes are JSON; ProseVersion carried forward when already present
**date:** 2026-06-12
**status:** open
**spawned_tickets:** T-json-envelope-inference, T-slate-json-format, T-consequence-json-envelope

## Decision narrative
All agent-to-agent messages and tool responses use JSON envelopes with typed fields (RESULT, ERRORCLASS, ERRORNUMBER, etc.). If a prose representation already exists from the originating tool or agent, it is preserved as a named field (ProseVersion). Prose is NOT generated for all packets — only carried forward when it already exists. Agents key on structured fields; humans read ProseVersion. Nothing is lost: existing prose output is promoted to a named field rather than discarded.

## Hypothesis
Agent-to-agent messages and tool responses carry typed JSON envelopes; consumers parse structured fields rather than regex-extracting from prose; ProseVersion appears only when already present.

## Measurement Signal
Side-by-side token count comparison (same task, with/without envelopes); for build tasks, CLOSED on T-json-envelope-inference is the done signal.

## Goal Link
Cost savings (direct token reduction) + training signal quality (structured fields are cleaner training examples than prose).
