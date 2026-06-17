# D-feeds-taxonomy-2026-06-11 — Feed types, importance, and notification model

## Decision

The rack uses three feed types, modelled on the Murderbot Diaries feed system.

### Feed types

**Public feed** (`type: public`)
Broadcast. Any device or user can read. Used for rack-wide announcements, shared channel posts. One global public feed: `Shared`.

**Personal feed** (`type: personal`)
Per-device. Owned by one device; others post into it with appropriate access. All posts are visible to anyone with read access to that device's feed — it is not a DM channel. The owner receives a notification when a post arrives with `importance >= owner's threshold`. Replies go to the poster's personal feed (they get notified). Access control is deferred.

**Debug feed** (`type: debug`)
Machine-readable append log of a device's console/structured output. Replaces what we've been calling "console output." Hard cap: 1 000 lines; oldest lines are evicted on write. Not notified. Read by web UI and monitoring tools. Not persisted beyond the cap.

### Importance flag

Every envelope carries `importance: int` (0–10, default 3).
- 0 = trace/noise
- 3 = normal operational event
- 7 = significant (escalation, DONE, error)
- 10 = urgent

Notification threshold is receiver-selectable per feed. Default: notify on importance >= 5.

### Notification model

Personal feed owner is notified when a post arrives with importance >= threshold.
Poster is notified when a reply arrives in their personal feed.
Public and debug feeds: no notifications.

### Private feeds (bounded channels)

Deferred. Not implemented in this phase.

### Web UI device list

Current: single alpha-sorted list.
New: two lists — MRU (last N active, ordered by most-recent post) + complete (alpha). Igor and Librarian float to MRU naturally via activity.

## Out of scope (this decision)

- Access control on personal feeds
- Private (bounded) feeds
- Web UI feed rendering changes beyond the device list split
- Team feeds
