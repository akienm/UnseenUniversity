# /mytickets — Show tickets assigned to Akien (guru role)

Shows all open tickets with role=guru or worker=akien. These are the tickets
that need Akien's hands — Granny will not dispatch them to CC or DickSimnel.

## Steps

```bash
python3 ${CC_WORKFLOW_TOOLS}/queue_view.py --view mytickets
```

The script handles filtering, grouping, and formatting. Output is ready to read — no further processing needed.

If nothing is in the guru/akien filter: prints "No tickets assigned to Akien right now."

## Output shape

```
MY TICKETS — Akien (guru role)
Sprint (ready):
  T-xxx  (S)  title — action needed

Akien (waiting on you):
  T-yyy  (M)  title — action needed

Hold (blocked):
  T-zzz  (S)  title — blocked on: reason
```
