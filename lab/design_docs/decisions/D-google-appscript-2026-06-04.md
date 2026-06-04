# D-google-appscript-2026-06-04
**title:** Google AppScript as GoogleSecretary execution layer — evaluation
**date:** 2026-06-04
**status:** complete
**verdict:** ADOPT for write operations; SWADL remains fallback for read-heavy flows

## What AppScript can do

AppScript runs server-side inside Google's infrastructure as the authenticated user.
It has native access to: Gmail (send/read/label/search), Calendar (create/update/delete/RSVP),
Sheets/Docs/Drive (CRUD), Forms, Contacts, Groups. No OAuth dance per operation — the
script executes with the user's credentials established at deployment time.

Trigger types: time-based (cron-style), event-based (form submit, sheet edit),
HTTP endpoint (doGet/doPost via Web App deployment). The HTTP endpoint trigger is
the key: deploy a script as a Web App → call it from Python with `requests.post(url, json=payload)`.

## Invocation path from Python

1. Deploy AppScript project as Web App (Execute as: Me, Who has access: Anyone with link or specific account).
2. From Python rack device:
   ```python
   import requests
   resp = requests.post(APPSCRIPT_URL, json={"action": "send_email", "to": "...", "body": "..."}, timeout=30)
   ```
3. AppScript doPost() routes action → handler → executes Workspace API call → returns JSON.

No service account needed for the HTTP approach (the script runs as the owner).
For more controlled access, Apps Script API (REST) requires OAuth2 service account — adds setup complexity.
Recommend: Web App + shared secret header for now; OAuth2 later if audit requirements demand it.

## Quota limits

| Operation | Quota |
|---|---|
| Email sends | 100/day (consumer), 1500/day (Workspace) |
| Calendar creates | 10,000/day |
| Script runtime | 6 min per execution, 90 min/day total |
| Triggers | 20 per user, 20 per script |

For the agent-datacenter use case (Igor posting calendar events, sending summaries, querying inbox),
these limits are non-binding at current volume.

## Comparison with SWADL

| Dimension | AppScript | SWADL |
|---|---|---|
| Fragility | Low — API calls, not DOM scraping | High — breaks on Gmail/Calendar UI changes |
| Setup | Deploy once, call forever | Requires running browser, session management |
| Latency | ~1-3s per call | ~5-15s (browser startup + nav) |
| Quota | Per-user API quotas | Rate-limited by Google's UI anti-bot logic |
| Write ops | Native (send email, create event) | Via UI actions (fragile) |
| Read ops | Native (search, fetch) | Can screen-scrape if API is missing |

## Verdict: ADOPT

AppScript as the execution layer for GoogleSecretary write operations.
Replace SWADL-based Gmail/Calendar actions with AppScript Web App calls.
SWADL remains available as fallback for niche UI-only operations with no API.

Next steps (not this ticket): implement the AppScript functions (email, calendar, drive),
wire into devices/google_secretary/device.py as a new dispatcher strategy.
