#!/usr/bin/env python3
"""palace_bootstrap.py — Create and seed the ADC palace table.

Usage:
    python3 scripts/palace_bootstrap.py              # migrate + seed (idempotent)
    python3 scripts/palace_bootstrap.py --migrate    # table + indexes only
    python3 scripts/palace_bootstrap.py --seed       # insert/upsert initial nodes
    python3 scripts/palace_bootstrap.py --rollback   # DROP TABLE (destructive)
    python3 scripts/palace_bootstrap.py --schema adc # target schema (default: adc)
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import argparse
import os
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

# ── SQL ───────────────────────────────────────────────────────────────────────

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS {schema};"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS {schema}.palace (
    path        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    node_type   TEXT NOT NULL DEFAULT 'doc',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB NOT NULL DEFAULT '{{}}'
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS palace_tags_gin ON {schema}.palace USING GIN (metadata jsonb_path_ops);",
    "CREATE INDEX IF NOT EXISTS palace_fts_gin  ON {schema}.palace USING GIN (to_tsvector('english', coalesce(content,'') || ' ' || coalesce(title,'')));",
    "CREATE INDEX IF NOT EXISTS palace_node_type ON {schema}.palace (node_type);",
    "CREATE INDEX IF NOT EXISTS palace_updated   ON {schema}.palace (updated_at DESC);",
]

_DROP_TABLE = "DROP TABLE IF EXISTS {schema}.palace CASCADE;"

_UPSERT = """
INSERT INTO {schema}.palace (path, title, content, node_type, updated_at, metadata)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (path) DO UPDATE
    SET title      = EXCLUDED.title,
        content    = EXCLUDED.content,
        node_type  = EXCLUDED.node_type,
        updated_at = EXCLUDED.updated_at,
        metadata   = EXCLUDED.metadata;
"""

# ── Seed data ─────────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc).isoformat()


def _seed_rows() -> list[tuple]:
    """Return (path, title, content, node_type, updated_at, metadata) tuples."""
    import json

    def row(path, title, content, node_type="doc", tags=None, extra=None):
        meta = {"tags": tags or []}
        if extra:
            meta.update(extra)
        return (path, title, content, node_type, _NOW, json.dumps(meta))

    return [
        # ── palace.shared.akien ──────────────────────────────────────────────
        row(
            "palace.shared.akien.goals",
            "Akien's Goals Tree",
            (
                "ROOT: 'Akien makes the world suck less' (Fred Rogers flavor, not Silicon Valley).\n"
                "Central project: Igor Experiment (1.x) — brain-modeled, small hardware, emergent\n"
                "self-awareness, recursive self-improvement toward Igor designing himself.\n"
                "Platform: UnseenUniversity (3.x) as universal erector set — Igor, OpenClaw-style\n"
                "agents, CC, SWADL automation all as equal first-class consumers.\n"
                "Compute efficiency as planetary good (2.x) — graph-tree traces live paths only,\n"
                "no GPU required; MIT license as safety mechanism.\n"
                "Sustainability via SWADL consulting + writing corpus (4.x).\n"
                "Governing values: CP1-CP6 — honest, learning-safe, make-visible, everyone (not just users),\n"
                "inherent worth of every being, look-for-the-helpers.\n\n"
                "Full tree: /home/akien/.unseen_university/akien/goals_tree.20260507.md"
            ),
            tags=["shared", "akien"],
            extra={
                "pointer_to": "/home/akien/.unseen_university/akien/goals_tree.20260507.md"
            },
        ),
        row(
            "palace.shared.akien.profile",
            "Akien profile",
            (
                "Akien MacIain — builds Igor (brain-modeled AI experiment) and UnseenUniversity\n"
                "(portable agent runtime). Creative professional with coding background;\n"
                "native mode is 'dump ideas → organize.' Prefers terse CC responses with\n"
                "concrete action over explanation. High latitude: 'up to you, approved, go.'\n"
                "Context-is-compute: less token burn → more work done per session.\n"
                "Family: Leah (partner), daughters, brother. Goals at palace.shared.akien.goals."
            ),
            tags=["shared", "akien"],
        ),
        # ── palace.shared.rules ──────────────────────────────────────────────
        row(
            "palace.shared.rules.coding",
            "Coding standards",
            (
                "- No SQLite anywhere. Postgres or flat-file only.\n"
                "- No TheIgors imports inside UnseenUniversity — portability hard rule.\n"
                "- OOP-first: BaseDevice / BaseShim are the design center.\n"
                "  No standalone functions doing device work.\n"
                "- bus/ owns comms:// routing. Nothing outside bus/ speaks to IMAP directly.\n"
                "- skeleton/ owns MCP aggregator and flat-file registry. No Postgres dependency.\n"
                "- devices/ contains one subdirectory per device; each independently deployable.\n"
                "- Log hierarchy: datacenter_logs/<device>/<subsystem>/ — never flat root logs.\n"
                "- No live keys or passwords in source. .env is gitignored.\n"
                "- pip install -e . must succeed at all times (even with empty stubs)."
            ),
            tags=["shared", "rules"],
        ),
        row(
            "palace.shared.rules.commits",
            "Commit conventions",
            (
                "- Stage files by name, never git add -A or git add .\n"
                "- Always run pre-commit hooks (no --no-verify).\n"
                "- Push non-force to main.\n"
                "- Pull --rebase before push.\n"
                "- Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com> on every commit.\n"
                "- Message: type: description (feat/fix/docs/refactor/test).\n"
                "- Never amend — always new commits."
            ),
            tags=["shared", "rules"],
        ),
        row(
            "palace.shared.rules.collaboration",
            "Collaboration rules",
            (
                "- When Akien says 'up to you / approved / go' → proceed without further asks.\n"
                "- Ask before destructive actions (delete files, reset --hard, force push).\n"
                "- Prefer editing existing files to creating new ones.\n"
                "- No features beyond what the task requires. No premature abstractions.\n"
                "- Terse responses: state results and decisions, skip commentary.\n"
                "- CC is Akien's co-builder and executor; Igor is the domain expert on his own code.\n"
                "- Reach for ADC MCP tools before inline implementation: cheaper tokens, shared infra."
            ),
            tags=["shared", "rules"],
        ),
        # ── palace.shared.capabilities ───────────────────────────────────────
        row(
            "palace.shared.capabilities.index",
            "Capability inventory index",
            (
                "Installed devices:\n"
                "- Librarian (UnseenUniversity/devices/librarian/) — MCP server, DB proxy,\n"
                "  inference routing, research/summarization, health aggregation.\n"
                "  MCP tools: db_query, db_dispatch, memory_get, memory_search, memory_list_by_type,\n"
                "  channel_read, channel_send, rack_health, traces_recent, habit_list,\n"
                "  summarize, research, build_summary, datacenter_manifest.\n\n"
                "Routing manifest: call datacenter_manifest(routing_only=True) for task-shape → tool map.\n\n"
                "Skills: see ~/.claude/skills/ (CC) and UU palace unseenuniversity/skills (Igor)."
            ),
            tags=["shared", "capabilities"],
        ),
        row(
            "palace.shared.audits.registry",
            "Registered audit checks",
            (
                "Audit checks run at day-close via devlab/claudecode/audit_runner.py --drain.\n"
                "Persistent (forever) checks: no-sqlite-imports, no-bare-except-pass,\n"
                "primary-classes-must-inherit-igorbase.\n"
                "One-shot (next_sweep) checks: added at moment of insight, drain on next run.\n\n"
                "To add: python3 devlab/claudecode/audit_add.py add forever|next <name> ...\n"
                "Authoritative source: audit_runner.py registered check list."
            ),
            tags=["shared", "audits"],
        ),
        # ── palace.projects.unseen_university ─────────────────────────────────
        row(
            "palace.projects.unseen_university.summary",
            "UnseenUniversity — executive summary",
            (
                "Portable agent runtime substrate — the erector set for building agents.\n"
                "Igor is one device on the rack; CC is a consumer; Librarian is the MCP aggregator.\n\n"
                "Current state (2026-05-08): Phases 0-4 complete, Phase 5 partial.\n"
                "Live: BaseDevice, BaseShim, bus/IMAP, Librarian (MCP+DB+inference+research),\n"
                "DiagnosticBase micro-package, SWADL drivers, palace bootstrap.\n\n"
                "Top priorities: T-capability-extraction-from-igor (XL — move capabilities\n"
                "out of Igor monolith into shared ADC devices); Igor diagnostic tickets\n"
                "(T-igor-console-logging, T-igor-web-message-receive)."
            ),
            tags=["UnseenUniversity", "projects"],
        ),
        row(
            "palace.projects.unseen_university.map",
            "UnseenUniversity — architecture map",
            (
                "UnseenUniversity/\n"
                "  skeleton/    — MCP aggregator, flat-file registry, no Postgres dependency\n"
                "  bus/         — comms:// routing, IMAP client/server, heartbeat\n"
                "  devices/     — one subdir per device (independently deployable)\n"
                "    librarian/ — MCP server, inference router, DB proxy, research, health\n"
                "    installer/ — device installer/manifest\n"
                "  announce/    — device announce/manifest\n"
                "scripts/       — migration and seeder scripts (palace_bootstrap.py)\n"
                "docs/          — palace_schema.md, framework_overview.md, etc.\n"
                "skills/        — CC skill files deployed from this repo\n"
                "tests/         — pytest suite (601 tests as of 2026-05-08)\n\n"
                "Seams: devices communicate via bus/ (comms://); no direct cross-device imports.\n"
                "Librarian is the single MCP entry point for CC and Igor."
            ),
            tags=["UnseenUniversity", "projects"],
        ),
        row(
            "palace.projects.unseen_university.standards",
            "UnseenUniversity — standards and conventions",
            (
                "Beyond palace.shared.rules.coding:\n"
                "- Every device inherits BaseDevice (device.py design center).\n"
                "- Every shim inherits BaseShim (shim.py design center).\n"
                "- No standalone functions doing device work — OOP only.\n"
                "- bus/ is the sole owner of IMAP/comms:// routing.\n"
                "- MCP tools go in devices/librarian/tools/ — one module per domain.\n"
                "- Routing manifest (datacenter_manifest) must be updated when a device is added.\n"
                "- Tests: pytest, no live-DB required (mock pool via patch of get_conn).\n"
                "- Log hierarchy: datacenter_logs/<device>/<subsystem>/ — no flat root logs."
            ),
            tags=["UnseenUniversity", "projects"],
        ),
        # ── palace.projects.theigors (federation pointer) ────────────────────
        row(
            "palace.projects.theigors",
            "TheIgors — federated palace pointer",
            (
                "Igor's palace lives in TheIgors Postgres, table memory_palace (clan schema).\n"
                "It is NOT merged into this database — federation via pointer only.\n\n"
                "Query Igor's palace:\n"
                "  psql postgresql://unseen_university:<password>@127.0.0.1/unseen_university \\\n"
                "    -c \"SELECT path, title FROM memory_palace WHERE path LIKE 'unseenuniversity/%' ORDER BY path\"\n\n"
                "Via MCP (when Igor running):\n"
                "  memory_get(path='unseenuniversity/rules/coding')\n"
                "  memory_search(query='...')"
            ),
            node_type="pointer",
            tags=["theigors", "projects"],
            extra={
                "pointer_to": "postgresql://unseen_university@127.0.0.1/unseen_university clan.memory_palace unseenuniversity/*"
            },
        ),
    ]


# ── Migration ─────────────────────────────────────────────────────────────────


def migrate(conn, schema: str = "adc") -> None:
    with conn.cursor() as cur:
        cur.execute(_CREATE_SCHEMA.format(schema=schema))
        cur.execute(_CREATE_TABLE.format(schema=schema))
        for idx_sql in _CREATE_INDEXES:
            cur.execute(idx_sql.format(schema=schema))
    conn.commit()
    print(f"  migrate: adc.palace table + 4 indexes created in schema '{schema}'")


def seed(conn, schema: str = "adc") -> None:
    rows = _seed_rows()
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(_UPSERT.format(schema=schema), r)
    conn.commit()
    print(f"  seed: {len(rows)} nodes upserted into {schema}.palace")


def rollback(conn, schema: str = "adc") -> None:
    with conn.cursor() as cur:
        cur.execute(_DROP_TABLE.format(schema=schema))
    conn.commit()
    print(f"  rollback: {schema}.palace dropped")


# ── Entry point ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ADC palace bootstrap")
    parser.add_argument(
        "--migrate", action="store_true", help="Create table + indexes only"
    )
    parser.add_argument("--seed", action="store_true", help="Upsert initial nodes only")
    parser.add_argument(
        "--rollback", action="store_true", help="DROP TABLE (destructive)"
    )
    parser.add_argument(
        "--schema", default="adc", help="Target Postgres schema (default: adc)"
    )
    parser.add_argument("--pg-url", default=home_db_url(), help="Postgres connection URL")
    args = parser.parse_args(argv)

    conn = psycopg2.connect(args.pg_url)
    try:
        if args.rollback:
            rollback(conn, args.schema)
            return

        run_all = not args.migrate and not args.seed
        if args.migrate or run_all:
            migrate(conn, args.schema)
        if args.seed or run_all:
            seed(conn, args.schema)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
