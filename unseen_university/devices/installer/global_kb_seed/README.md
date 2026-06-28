# Unseen University Global Knowledge Base

This directory seeds the `unseen-university-kb` standalone git repository.
When published, new UU instances clone that repo at bootstrap to receive
universal patterns, agent templates, and coding idioms without having to
discover them through experience.

## Record format

Each `.jsonl` file in `patterns/` is a sequence of pattern records, one per line:

```json
{
  "id": "P-<kebab-slug>",
  "title": "short human title",
  "type": "pattern|template|idiom|rule",
  "tags": ["tag1", "tag2"],
  "content": "the pattern body — markdown, code, or structured text",
  "version": "1.0",
  "source": "unseen-university-kb",
  "origin_instance": null
}
```

Fields:
- `id` — unique across the repo; prefixed `P-`
- `type` — `pattern` (design pattern), `template` (starter scaffold), `idiom` (coding convention), `rule` (hard constraint)
- `tags` — free-form, used for filtering on import
- `content` — the actual knowledge; no credentials, no instance-specific paths
- `version` — semver-style; bumped on meaningful content change
- `source` — always `"unseen-university-kb"` for global records
- `origin_instance` — null for global records; set to instance_id when a local pattern is staged for contribution

## Import

Bootstrap a new instance with:
```
./uu bootstrap --global-kb https://github.com/akienm/unseen-university-kb.git
```

Or update an existing install:
```
./uu bootstrap --global-kb --update
```

The bootstrap command:
1. Clones (or pulls) the KB repo to `~/.unseen_university/global-kb/`
2. Reads all `patterns/*.jsonl` files
3. Upserts records into the local Global DB (Postgres `global_kb.patterns` table)

## Contributing

The Archivist's contribution path (T-archivist-global-contrib, follow-on):
locally-proven patterns that pass a quality threshold get staged as PRs to
this repo. The repo README describes governance; no enforcement tooling yet.

## What does NOT belong here

- Credentials, API keys, tokens
- Instance-specific paths or ticket IDs
- Personal memory or diary content
- Anything under `.env` or `config.cfg`
