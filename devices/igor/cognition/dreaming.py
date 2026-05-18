"""
dreaming.py — cross-session pattern synthesis for clan.memories.

Triggered by COA after every IGOR_DREAMING_INTERVAL NE cycles (env var,
default 50). Disabled when IGOR_DREAMING_INTERVAL=0.

Read sources:
  - igor_psych.jsonl (last N entries)
  - instance.watch_problems (active, recently-evidenced)

Synthesis: haiku call proposes habit or WATCH_Q additions.
Output: writes to instance.proposals (kind='habit' or kind='watch_q').
Dreaming PROPOSES, Igor DECIDES — no direct clan.memories writes.

D-dreaming-patterns-2026-05-10
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time

log = logging.getLogger(__name__)

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

DREAMING_INTERVAL_DEFAULT: int = 50
PSYCH_LOG_WINDOW: int = 20
WATCH_PROBLEMS_WINDOW: int = 10
LIBRARIAN_OBS_WINDOW: int = 10

_PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS instance.proposals (
    id                  serial PRIMARY KEY,
    kind                text NOT NULL,
    content             text NOT NULL,
    metadata            jsonb NOT NULL DEFAULT '{}',
    status              text NOT NULL DEFAULT 'pending',
    source_module       text,
    occurrence_count    int NOT NULL DEFAULT 1,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    committed_at        timestamptz,
    committed_memory_id bigint,
    rejected_at         timestamptz,
    rejected_reason     text,
    CONSTRAINT proposals_status_check CHECK (status IN ('pending', 'committed', 'rejected'))
)
"""


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _ensure_proposals(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_PROPOSALS_DDL)


def _fingerprint(kind: str, content: str) -> str:
    return hashlib.md5((kind + content[:200]).encode()).hexdigest()


def _add_proposal(
    conn,
    *,
    kind: str,
    content: str,
    source_module: str,
    extra_metadata: dict | None = None,
) -> int:
    fp = _fingerprint(kind, content)
    metadata: dict = {"fingerprint": fp, "source": source_module}
    if extra_metadata:
        metadata.update(extra_metadata)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM instance.proposals WHERE status='pending' "
            "AND metadata->>'fingerprint' = %s",
            (fp,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE instance.proposals SET occurrence_count = occurrence_count + 1 "
                "WHERE id = %s",
                (row[0],),
            )
            return row[0]
        cur.execute(
            "INSERT INTO instance.proposals (kind, content, metadata, source_module) "
            "VALUES (%s, %s, %s::jsonb, %s) RETURNING id",
            (kind, content, json.dumps(metadata), source_module),
        )
        return cur.fetchone()[0]


def _read_psych_log(paths_obj) -> list[dict]:
    """Read the last PSYCH_LOG_WINDOW entries from igor_psych.jsonl."""
    try:
        psych_log = paths_obj.logs / "igor_psych.jsonl"
        if not psych_log.exists():
            return []
        entries = []
        with psych_log.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
        return entries[-PSYCH_LOG_WINDOW:]
    except Exception as e:
        log.debug("dreaming: psych_log read failed: %s", e)
        return []


def _read_watch_problems() -> list[dict]:
    """Read recently-evidenced active watch_problems."""
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT problem, watch_condition, confidence_score
                    FROM instance.watch_problems
                    WHERE resolved_at IS NULL
                      AND confidence_score > 0.1
                    ORDER BY confidence_score DESC
                    LIMIT %s
                    """,
                    (WATCH_PROBLEMS_WINDOW,),
                )
                return [
                    {
                        "problem": r[0],
                        "watch_condition": r[1],
                        "confidence": float(r[2]),
                    }
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()
    except Exception as e:
        log.debug("dreaming: watch_problems read failed: %s", e)
        return []


def _read_librarian_observations() -> list[dict]:
    """Read recent pending librarian_observation proposals."""
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT kind, content, metadata, occurrence_count
                    FROM instance.proposals
                    WHERE kind = 'librarian_observation' AND status = 'pending'
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (LIBRARIAN_OBS_WINDOW,),
                )
                rows = cur.fetchall()
                result = []
                for row in rows:
                    meta = row[2] if isinstance(row[2], dict) else {}
                    result.append(
                        {
                            "content": row[1],
                            "confidence": meta.get("confidence", 0.0),
                            "tier": meta.get("tier", ""),
                            "outcome": meta.get("outcome", ""),
                            "topic": meta.get("topic", ""),
                            "occurrence_count": row[3],
                        }
                    )
                return result
        finally:
            conn.close()
    except Exception as e:
        log.debug("dreaming: librarian_observations read failed: %s", e)
        return []


_LIBRARIAN_TERMS = frozenset({"librarian", "research", "observation", "synthesize"})
_PSYCH_TERMS = frozenset({"igor", "psych", "psychological", "valence", "arousal"})


def _is_convergent(rationale: str) -> bool:
    """True when rationale references both Librarian and Igor-psych domains."""
    r = rationale.lower()
    return any(t in r for t in _LIBRARIAN_TERMS) and any(t in r for t in _PSYCH_TERMS)


def _synthesize(
    psych_entries: list[dict],
    watch_problems: list[dict],
    librarian_obs: list[dict] | None = None,
) -> list[dict]:
    """Call haiku to synthesize cross-session patterns. Returns list of proposals."""
    try:
        from ..tools.inner_cc import call_inner_cc_long

        psych_summary = "\n".join(
            f"- ts={e.get('ts', 0):.0f} valence={e.get('valence', 0):.2f} "
            f"arousal={e.get('arousal', 0):.2f} notes={str(e.get('notes', ''))[:80]}"
            for e in psych_entries[-10:]
        )
        watch_summary = "\n".join(
            f"- [{w['confidence']:.2f}] {w['problem'][:80]}" for w in watch_problems
        )

        librarian_lines = ""
        if librarian_obs:
            parts = []
            for obs in librarian_obs:
                outcome = obs.get("outcome", "")
                topic = obs.get("topic", obs.get("content", ""))[:80]
                count = obs.get("occurrence_count", 1)
                conf = obs.get("confidence", 0.0)
                tier = obs.get("tier", "")
                if outcome == "failed" and count >= 2:
                    parts.append(f"- [FAILED x{count}] Could not synthesize '{topic}'")
                else:
                    parts.append(
                        f"- [confidence={conf:.2f}, {tier} tier] Researched '{topic}' → {outcome}"
                    )
            librarian_lines = "\n".join(parts)

        librarian_section = (
            f"\nRecent Librarian behavioral observations (last {len(librarian_obs)}):\n"
            f"{librarian_lines}\n"
            if librarian_obs
            else ""
        )

        prompt = f"""You are analyzing an AI agent's cognitive patterns across recent sessions.

Recent psychological log entries (last 10 cycles):
{psych_summary or "(none)"}

Active watch problems (by confidence):
{watch_summary or "(none)"}
{librarian_section}
Identify 0-3 recurring patterns that would benefit from:
- A new procedural habit (kind=habit): a reusable response pattern
- A new watch question (kind=watch_q): something worth watching for
- A new playbook (kind=playbook): structured conditions + heuristics to apply in a recurring situation

Respond ONLY with valid JSON array (may be empty):
[
  {{
    "kind": "habit" or "watch_q" or "playbook",
    "content": "<narrative description>",
    "conditions": "<when to apply — required for kind=playbook, empty string otherwise>",
    "heuristics": "<what to do — required for kind=playbook, empty string otherwise>",
    "rationale": "<one sentence: why this pattern warrants a new entry>"
  }}
]"""

        raw = call_inner_cc_long(task=prompt, model="anthropic/claude-haiku-4-5")
        answer = (raw.get("answer") or "").strip()
        if answer.startswith("```"):
            parts_split = answer.split("```")
            answer = parts_split[1] if len(parts_split) > 1 else ""
            if answer.startswith("json"):
                answer = answer[4:]
        proposals = json.loads(answer)
        if not isinstance(proposals, list):
            return []
        valid = []
        for p in proposals:
            if (
                isinstance(p, dict)
                and p.get("kind") in ("habit", "watch_q", "playbook")
                and p.get("content")
            ):
                if _is_convergent(p.get("rationale", "")):
                    p["convergence"] = True
                valid.append(p)
        return valid
    except Exception as e:
        log.warning("dreaming: synthesis failed: %s", e)
        return []


# ── Schema extraction helpers ─────────────────────────────────────────────────


def _closed_tickets_by_tag(conn) -> dict[str, dict]:
    """Return tag → {count, titles, descriptions} for tags with 3+ closed tickets
    in the last 60 days. Only tags with a non-null string value are included."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              metadata->'tags'->0 AS first_tag,
              COUNT(*) AS closed_count,
              jsonb_agg(metadata->>'title') AS titles,
              jsonb_agg(metadata->>'description') AS descriptions
            FROM clan.memories
            WHERE parent_id = 'TICKETS_ROOT'
              AND metadata->>'status' = 'done'
              AND (metadata->>'completed_at') IS NOT NULL
              AND metadata->'tags'->0 IS NOT NULL
              AND metadata->'tags'->0 != 'null'
            GROUP BY first_tag
            HAVING COUNT(*) >= 3
            ORDER BY closed_count DESC
            LIMIT 20
            """,
        )
        result = {}
        for row in cur.fetchall():
            tag = str(row[0]).strip('"')
            if tag and tag != "null":
                result[tag] = {
                    "count": row[1],
                    "titles": [t for t in (row[2] or []) if t],
                    "descriptions": [d for d in (row[3] or []) if d],
                }
        return result


def _palace_path_exists(conn, path: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM clan.memory_palace WHERE path = %s LIMIT 1", (path,))
        return cur.fetchone() is not None


def _palace_write(conn, path: str, title: str, content: str) -> None:
    import re

    parent = re.sub(r"/[^/]+$", "", path)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO clan.memory_palace (path, parent_path, title, content, updated_at, updated_by)
               VALUES (%s, %s, %s, %s, NOW()::text, 'dreaming')
               ON CONFLICT (path) DO NOTHING""",
            (path, parent or None, title, content),
        )


def _synthesize_sprint_pattern(
    tag: str, titles: list[str], descriptions: list[str], timeout_s: int = 20
) -> str:
    """Call inner_cc to synthesize a sprint pattern from closed tickets.

    timeout_s: hard wall-clock timeout for the LLM call (default 20 s).
    Returns empty string on failure or timeout so callers can skip gracefully.
    """
    import concurrent.futures

    try:
        from ..tools.inner_cc import call_inner_cc_long

        sample_titles = "\n".join(f"- {t[:100]}" for t in titles[:10])
        prompt = (
            f"The following {tag!r} tickets were successfully completed:\n"
            f"{sample_titles}\n\n"
            "In 2-4 sentences, describe the common successful approach pattern "
            "for this type of ticket — what works, what to check first, and any "
            "recurring non-obvious considerations. Be concrete and actionable."
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _exe:
            _fut = _exe.submit(
                call_inner_cc_long,
                task=prompt,
                model="anthropic/claude-haiku-4-5",
            )
            try:
                raw = _fut.result(timeout=timeout_s)
            except concurrent.futures.TimeoutError:
                log.warning(
                    "dreaming: sprint_pattern timeout (%ds) for tag=%s", timeout_s, tag
                )
                return ""
        return (raw.get("answer") or "").strip()
    except Exception as e:
        log.warning("dreaming: sprint_pattern synthesis failed for %s: %s", tag, e)
        return ""


def _schema_extraction_pass(conn) -> int:
    """Hippocampal schema extraction: write palace procedure nodes for tag patterns
    with 3+ successful sprints.

    Processes at most 1 new tag per dreaming pass to keep latency bounded.
    Returns count of palace nodes written.
    """
    try:
        tags = _closed_tickets_by_tag(conn)
        if not tags:
            return 0
        written = 0
        for tag, info in tags.items():
            tag_slug = tag.lower().replace(" ", "-")
            palace_path = f"theigors/procedures/{tag_slug}-sprint-pattern"
            if _palace_path_exists(conn, palace_path):
                log.debug(
                    "dreaming: schema %s already in palace — skipping", palace_path
                )
                continue
            # Attempt synthesis for this tag — break after this regardless of
            # success so at most one LLM call fires per dreaming pass.
            pattern = _synthesize_sprint_pattern(
                tag, info["titles"], info["descriptions"]
            )
            if pattern:
                title = f"{tag} sprint pattern ({info['count']} successes)"
                content = (
                    f"# {tag} Sprint Pattern\n\n"
                    f"Synthesized from {info['count']} closed tickets.\n\n"
                    f"{pattern}"
                )
                with conn:
                    _palace_write(conn, palace_path, title, content)
                log.info(
                    "dreaming: schema extracted for tag=%s → %s (%d tickets)",
                    tag,
                    palace_path,
                    info["count"],
                )
                written += 1
            break  # one attempt per dreaming pass — bounded latency
        return written
    except Exception as e:
        log.warning("dreaming: schema_extraction_pass failed: %s", e)
        return 0


# ── Failure-pattern → watch_problems helpers ─────────────────────────────────

_FAILURE_KEYWORDS = (
    "failed",
    "blocked",
    "stuck",
    "retry",
    "retried",
    "failure",
    "error",
)


def _extract_failure_clusters(proposals: list[dict]) -> dict[str, list[str]]:
    """Scan proposal content+rationale for failure keywords.
    Returns keyword → list of matching proposal summaries (2+ triggers write)."""
    clusters: dict[str, list[str]] = {}
    for p in proposals:
        text = (p.get("content", "") + " " + p.get("rationale", "")).lower()
        for kw in _FAILURE_KEYWORDS:
            if kw in text:
                clusters.setdefault(kw, []).append(p.get("content", "")[:120])
    return {kw: items for kw, items in clusters.items() if len(items) >= 2}


def _write_failure_watch_problems(failure_clusters: dict[str, list[str]]) -> int:
    """Write watch_problems entries for recurring failure keyword clusters.
    Dedup is handled by watch_problems.add_watch_problem (by watch_condition)."""
    written = 0
    try:
        from .watch_problems import add_watch_problem as _add_wp

        for kw, summaries in failure_clusters.items():
            problem = f"dreaming identified recurring '{kw}' pattern in synthesis"
            watch_condition = f"dreaming:failure_keyword:{kw}"
            lever_description = (
                f"dreaming identified {len(summaries)} proposals referencing '{kw}': "
                + "; ".join(summaries[:3])
            )
            result = _add_wp(
                problem=problem,
                lever_description=lever_description,
                watch_condition=watch_condition,
            )
            if result >= 0:
                written += 1
                log.info(
                    "dreaming: watch_problem written for keyword=%s (id=%d)", kw, result
                )
    except Exception as e:
        log.warning("dreaming: write_failure_watch_problems failed: %s", e)
    return written


def run(paths_obj=None) -> int:
    """Run one dreaming cycle. Returns number of proposals written.

    paths_obj: Igor paths() object for locating psych_log. If None, imports paths().
    Disabled when IGOR_DREAMING_INTERVAL=0.
    """
    interval = int(os.getenv("IGOR_DREAMING_INTERVAL", str(DREAMING_INTERVAL_DEFAULT)))
    if interval == 0:
        return 0

    try:
        if paths_obj is None:
            from ..paths import paths as _paths

            paths_obj = _paths()
    except Exception as e:
        log.debug("dreaming: paths() failed: %s", e)
        return 0

    psych_entries = _read_psych_log(paths_obj)
    watch_problems = _read_watch_problems()
    librarian_obs = _read_librarian_observations()

    if not psych_entries and not watch_problems and not librarian_obs:
        log.debug("dreaming: nothing to synthesize (empty inputs)")
        return 0

    proposals = _synthesize(psych_entries, watch_problems, librarian_obs)
    if not proposals:
        return 0

    conn = _conn()
    try:
        with conn:
            _ensure_proposals(conn)
        count = 0
        with conn:
            for p in proposals:
                try:
                    if p["kind"] == "playbook":
                        content = json.dumps(
                            {
                                "narrative": p["content"],
                                "conditions": p.get("conditions", ""),
                                "heuristics": p.get("heuristics", ""),
                            }
                        )
                    else:
                        content = p["content"]
                    extra = {"convergence": True} if p.get("convergence") else None
                    _add_proposal(
                        conn,
                        kind=p["kind"],
                        content=content,
                        source_module="dreaming",
                        extra_metadata=extra,
                    )
                    count += 1
                except Exception as e:
                    log.warning("dreaming: add_proposal failed: %s", e)
        log.info(
            "dreaming: wrote %d proposals from %d candidates", count, len(proposals)
        )

        # Failure-pattern → watch_problems (T-igor-dreaming-outputs-actionable)
        failure_clusters = _extract_failure_clusters(proposals)
        if failure_clusters:
            _write_failure_watch_problems(failure_clusters)

        # Schema extraction → palace (T-igor-dreaming-schema-extraction)
        # Runs after proposals so the DB connection is still live.
        schema_written = _schema_extraction_pass(conn)
        if schema_written:
            log.info("dreaming: wrote %d palace schema nodes", schema_written)

        return count
    finally:
        conn.close()
