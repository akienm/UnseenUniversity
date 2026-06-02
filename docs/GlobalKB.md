# Global Knowledge Base

The Global KB is a standalone git repository (`unseen-university-kb`) that ships
universal patterns to every new UU installation. New instances clone it at bootstrap
to get agent templates, coding idioms, and architecture rules without having to
rediscover them through experience.

## Relationship to the UU repo

```
UnseenUniversity/           ← this repo — the runtime substrate
  global_kb/                ← seed content for the KB repo (canonical source)
    patterns/baseline.jsonl ← shipped to unseen-university-kb on publish

unseen-university-kb        ← standalone repo (separate, published to GitHub)
  patterns/*.jsonl          ← version-controlled patterns
```

The `global_kb/` directory in this repo is the canonical source. When ready to
publish, copy its contents to the `unseen-university-kb` repo and push.

## Bootstrap a new instance

```bash
./uu bootstrap --global-kb https://github.com/akienm/unseen-university-kb.git
```

Or use the local seed content (no GitHub needed):

```bash
./uu bootstrap --local-kb ./global_kb
```

This clones (or pulls) the KB repo, reads all `patterns/*.jsonl` files, and
upserts records into `global_kb.patterns` in Postgres.

## Update an existing install

```bash
./uu bootstrap --global-kb --update
```

(Equivalent to clone_or_update + import — the `--update` flag is implicit when
the local clone already exists.)

## Record format

See `global_kb/README.md` for the full JSONL schema.

## What the baseline ships

Five patterns in `global_kb/patterns/baseline.jsonl`:
- `P-device-lifecycle` — start/stop/restart/self_test pattern
- `P-ticket-shape` — sprint ticket description template
- `P-channel-post-shape` — pipe-delimited channel event format
- `P-gate-semaphore` — availability semaphore protocol
- `P-external-state` — KnightlyBuilder external state rule

## Contribution path (future)

The Archivist (T-archivist-global-contrib, follow-on) will stage locally-proven
patterns for PR to the global repo. Not yet implemented.

## What never goes in the global KB

- Credentials, API keys, tokens
- Instance-specific paths (`/home/akien/...`, `Igor-wild-0001`)
- Personal memory or ticket IDs  
- Anything that belongs in `.env` or `config.cfg`
