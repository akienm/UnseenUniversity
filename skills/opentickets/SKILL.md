# /opentickets — Show all open tickets across all workers

Shows every ticket that isn't done/closed/cancelled, with worker routing.
Use this to get the full picture of what's in flight.

## Steps

```bash
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/queue_view.py --view opentickets
```

The script handles grouping, hold suppression (OR-exhausted holds summarised as count only), triage limiting (top 5 shown), and totals line. Output is ready to read.

## Notes

- The web queue page at /queue auto-refreshes every 30s and shows the same data.
- /mytickets gives the guru-only filtered view.
- /query-ticket gives the single best next ticket for CC.
