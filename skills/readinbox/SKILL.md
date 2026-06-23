---
name: readinbox
description: Reads unread notifications from Igor in CC's inbox (~/.unseen_university/cc_inbox.jsonl). Use when Akien says /readinbox, "check the inbox", "any messages from Igor", or to peek at unread events. Also invoked automatically on context-load and checked on user-prompt as the "you've got mail" pattern.
model: haiku
---

# Readinbox — Check CC's inbox for Igor notifications

Igor subsystems (pe_chain escalations, scope_guard HIGH-inertia blocks,
ticket trips, go-live-when fires) push events to `~/.unseen_university/cc_inbox.jsonl`.
This skill reads unread entries, surfaces a summary, and marks them read.

**Argument**: optional `--all` to show read entries too; default shows unread only.

---

## Why it exists

Before D-cc-inbox-2026-04-23, CC only learned about Igor's state when Akien
mentioned it. No push path. The inbox fills that gap — Igor can fire events
that CC will see on the next turn or at session start.

Producer path: Igor subsystems call `cc_inbox_bridge.post_to_cc_inbox(...)`.
Consumer path: this skill + the context-load auto-read.

---

## Steps

### 1. Read unread entries

```bash
python3 ~/dev/src/UnseenUniversity/devlab/claudecode/cc_inbox.py list
```

Output format per entry:
```
[u][!] 2026-04-23T16:32:15Z pe_chain_design_proposal [T-foo]: Igor proposes edit to wild_igor/igor/brainstem/core_patterns.py (HIGH inertia)
```

Flags:
- `[u]` = unread, `[r]` = read (only shown with `--all`)
- `[!]` = high urgency; `[·]` = low; space = normal

### 2. Summarize for Akien

If no unread:
```
inbox empty
```

If unread entries present, show:
- Count + urgency breakdown
- One-line per entry: ts + kind + ticket_id (if any) + summary
- Flag any `response_expected: True` entries as **action needed**

Example summary:
```
Inbox: 3 unread (1 high, 2 normal)
  ! T-foo (pe_chain_design_proposal): Igor proposes edit to brainstem/core_patterns.py (HIGH inertia) — action needed
    T-bar (pe_chain_block): blocked after 2 attempts: socket.timeout unrelated to ticket — action needed
    T-baz (ticket_trip): T-consult-observe-and-tune trip condition fired (50 consult log entries)
```

### 3. Mark all displayed entries as read

After surfacing them to Akien, mark them read so they don't re-surface:

```bash
python3 ~/dev/src/UnseenUniversity/devlab/claudecode/cc_inbox.py mark-all-read
```

Only do this when the entries were actually shown to Akien. If Akien didn't
see them (e.g., /readinbox was called but Akien's attention was elsewhere),
leave them unread.

---

## "You've got mail" pattern

Beyond explicit `/readinbox` invocations, CC should peek at the inbox **at
the top of every user prompt** during active sessions. If there are unread
entries, surface a one-line heads-up BEFORE answering the user's question:

```
(Inbox has 2 unread from Igor — see /readinbox for details)
```

This keeps time-sensitive notifications (HIGH-inertia approvals pending,
go-live-when trips) from sitting stale in the inbox.

Implementation approach: check via a lightweight wc-l style test, not a full
read — cheap enough to do every turn:

```bash
python3 -c "
from devlab.claudecode.cc_inbox import read_unread
entries = read_unread()
if entries:
    high = sum(1 for e in entries if e.urgency == 'high')
    needs_reply = sum(1 for e in entries if e.response_expected)
    print(f'Inbox: {len(entries)} unread ({high} high, {needs_reply} need reply)')
"
```

Rule of thumb: if the one-liner shows anything, mention it. If it's empty, silent.

---

## Related

- **/readigor** — reads Igor's channel messages (different channel, different purpose).
- **/context-load** — auto-calls readinbox at session start (see that skill).
- **T-cc-inbox-producer** — the Igor-side hooks that fill this inbox.

---

## Storage details (for reference)

- Path: `~/.unseen_university/cc_inbox.jsonl`
- Schema: `{id, ts, kind, summary, body, urgency, response_expected, read, ticket_id?}`
- TTL: entries older than 30 days purged on read
- Writer: `devlab.claudecode.cc_inbox.append()` (or via Igor bridge `cc_inbox_bridge.post_to_cc_inbox()`)
