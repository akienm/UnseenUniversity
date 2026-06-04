# D-rules-engine-audit-2026-06-04
**title:** Rules-engine audit — find bespoke routing chains for refactor
**date:** 2026-06-04
**status:** complete

## Summary

Grepped devices/ + unseen_university/ for if/elif dispatch chains >3 branches.
28 routing patterns found; 10 candidates identified.

## Findings

### Refactor now
| Location | Branches | Routes on | Verdict |
|---|---|---|---|
| devices/queue/mcp_server.py:230-247 `_dispatch` | 7 | tool name | Refactor — extract handler dict |
| unseen_university/devices/librarian/tools/igor_tools.py:570-593 `dispatch` | 8 | tool name | Refactor — unified Librarian tool dispatcher pattern |
| unseen_university/devices/librarian/tools/palace_tools.py:224-242 `dispatch` | 4 | tool name | Refactor — same pattern |
| unseen_university/devices/librarian/tools/budget_tools.py:313-326 `dispatch` | 5 | tool name | Refactor — same pattern |
| unseen_university/devices/librarian/tools/channel_tools.py:180-197 `dispatch` | 5 | tool name | Refactor — same pattern |

### Monitor (leave for now)
| Location | Branches | Notes |
|---|---|---|
| devices/web_server/server.py:895-962 WS handler | 3 | Partially registry-like; message types are stable |
| devices/reader/uri.py:251-268 `fetch` | 3 | URI scheme routing; stable set; fine as-is |
| unseen_university/devices/librarian/tools/igor_tools.py:467-474 | 4 | Event-type routing in a loop; acceptable |

### Already good
- `devices/granny/daemon.py` — already a rules engine (T-rules-engine-audit was correct to note this)
- `devices/inference/device.py` — already a rules engine
- `devices/google_secretary/dispatcher.py` — already a dict-based handler registry

## Tickets filed
- T-librarian-tool-dispatcher: unify Librarian tools dispatch (4 files) into one handler-dict pattern
- T-queue-mcp-dispatcher: refactor queue/mcp_server.py _dispatch to handler dict
