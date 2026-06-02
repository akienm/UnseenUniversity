---
name: export-chat
description: Dump the current CC session transcript to ~/dev/src/UnseenUniversity/claude_chat_logs/YYYY-MM-DD.md for recovery if chat scrolls off the top. Run on demand; also supports --all for bulk backfill and --session <id> for a specific one.
model: haiku
---

# /export-chat — Dump current chat to markdown

Recovery snapshot. If something scrolls off the top of the chat, `/export-chat` gives you a durable copy on disk.

## Steps

Run the helper script (default target is the most-recently-modified transcript — i.e. the current session):

```bash
python3 ~/dev/src/UnseenUniversity/lab/claudecode/export_chat.py
```

Output: `~/dev/src/UnseenUniversity/claude_chat_logs/YYYY-MM-DD.md`. Overwrites today's file; appends with a separator if the same day already has content from a different session.

## Flags

- `--session <session-id>` — render a specific session by id (the UUID filename minus `.jsonl`).
- `--all` — render every transcript in `~/.claude/projects/-home-akien-dev-src-UnseenUniversity/*.jsonl`, each to its corresponding day's file. Idempotent — running multiple times just appends separators.
- `--dry-run` — print what would be written, don't touch disk.

## What it renders

- User turns and assistant turns with timestamps.
- Tool calls: rendered as inline one-liners like `_[tool: Bash({"command":"..."})]_`.
- Tool results: elided to first 200 chars so the log stays readable.

## What it skips

- Empty / system-reminder / hook messages.
- Full tool result bodies (too noisy for a recovery log — the goal is "remember what we talked about", not reconstruct every command output).

## When to run

- Anytime you see the chat approaching its visible scroll limit and you want insurance.
- Before `/compact`, so you have the pre-compact state preserved.
- End of session (though `/savestate` covers the structured summary; `/export-chat` covers the verbatim transcript).

## Related

- **T-chat-history-igor-backfill** (gated) — Igor background job that runs `--all` periodically to keep every day's transcript archived.
- **/savestate** — structured session summary; different shape, different purpose.

## Source location

- Transcripts: `~/.claude/projects/-home-akien-dev-src-UnseenUniversity/<session-id>.jsonl`
- Script:      `~/dev/src/UnseenUniversity/lab/claudecode/export_chat.py`
- Output:      `~/dev/src/UnseenUniversity/claude_chat_logs/YYYY-MM-DD.md`
