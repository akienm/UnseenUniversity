"""Igor-specific observability tools — traces, tails, habits, turn logs."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

SCHEMAS = [
    {
        "name": "traces_recent",
        "description": (
            "Return the N most recent search traces — what memory nodes activated, "
            "in what order, for each search() call. Useful for diagnosing what Igor "
            "retrieved when processing a message."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of traces to return (default 10)",
                },
                "since_minutes": {
                    "type": "integer",
                    "description": "Only traces from last N minutes (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "traces_get",
        "description": (
            "Fetch the full ordered activation sequence for a specific trace by ID. "
            "Returns all nodes that fired (node_id, sequence_pos, relevance, "
            "memory_type, narrative snippet) — lets you replay a reasoning chain."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "trace_id": {
                    "type": "string",
                    "description": "Trace UUID from traces_recent",
                },
            },
            "required": ["trace_id"],
        },
    },
    {
        "name": "tail_heat",
        "description": "Get current decaying activation heat for a node (sum of weighted recent activations).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Memory node ID"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "hot_nodes",
        "description": "List nodes with highest current tail heat — most recently and strongly activated.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of nodes (default 10)",
                },
                "since_hours": {
                    "type": "number",
                    "description": "Only tails from last N hours (default 2)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "hot_attractors",
        "description": "List the top N attractor nodes — highest activation × inbound-edge score.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of attractors (default 10)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "habit_list",
        "description": "List active habits — id, trigger, type, score metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Filter by id or trigger text (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 30)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "turn_trace_recent",
        "description": (
            "Read recent turn traces from Igor's reasoning log. "
            "Each entry shows: input preview, thalamus intent, BG top habit, "
            "habit fired (if any), tier used, cost, response preview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of turns (default 5)",
                },
                "since_minutes": {
                    "type": "integer",
                    "description": "Only turns from last N minutes (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "consult_sessions_recent",
        "description": (
            "Read recent peer-LLM consult sessions from the forensic JSONL log. "
            "Each session shows: problem_kind, tier, ticket, every ask turn, "
            "confab flags, and the conclusion."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of sessions (default 10)",
                },
                "since_minutes": {
                    "type": "integer",
                    "description": "Only sessions from last N minutes (optional)",
                },
                "session_id": {
                    "type": "string",
                    "description": "Return only this session_id (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "wg_neighbors",
        "description": "Get word graph neighbors for a node — edges and weights.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "Word to look up"},
                "limit": {
                    "type": "integer",
                    "description": "Max neighbors (default 20)",
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum edge score (default 0.1)",
                },
            },
            "required": ["word"],
        },
    },
]


def _q(sql: str, params=(), pg_url: str = _PG_URL) -> list[dict]:
    with psycopg2.connect(pg_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def traces_recent(
    limit: int = 10, since_minutes: int | None = None, pg_url: str = _PG_URL
) -> str:
    params: list = []
    where = ""
    if since_minutes:
        cutoff = (datetime.now() - timedelta(minutes=since_minutes)).isoformat()
        where = "WHERE recorded_at > %s "
        params.append(cutoff)
    params.append(limit)
    rows = _q(
        f"SELECT id, recorded_at, query, nodes FROM traces {where}"
        f"ORDER BY recorded_at DESC LIMIT %s",
        params,
        pg_url,
    )
    if not rows:
        return "No traces found."
    lines = [f"{len(rows)} recent traces:\n"]
    for r in rows:
        nodes = json.loads(r["nodes"]) if isinstance(r["nodes"], str) else r["nodes"]
        type_counts: dict[str, int] = {}
        for n in nodes:
            t = n.get("memory_type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        top3 = [f"{n['node_id']}({n['relevance']:.2f})" for n in nodes[:3]]
        lines.append(
            f"  {r['recorded_at'][:19]}  q={repr((r['query'] or '')[:50])}\n"
            f"    nodes={len(nodes)} types={type_counts}\n"
            f"    top3: {', '.join(top3)}"
        )
    return "\n".join(lines)


def traces_get(trace_id: str, pg_url: str = _PG_URL) -> str:
    rows = _q(
        "SELECT id, recorded_at, query, nodes FROM traces WHERE id = %s LIMIT 1",
        (trace_id,),
        pg_url,
    )
    if not rows:
        return f"Trace {trace_id!r} not found."
    r = rows[0]
    nodes = (
        json.loads(r["nodes"]) if isinstance(r["nodes"], str) else (r["nodes"] or [])
    )
    node_ids = [n["node_id"] for n in nodes]
    narratives: dict[str, str] = {}
    if node_ids:
        placeholders = ",".join(["%s"] * len(node_ids))
        mem_rows = _q(
            f"SELECT id, narrative FROM memories WHERE id IN ({placeholders})",
            node_ids,
            pg_url,
        )
        for m in mem_rows:
            narratives[m["id"]] = str(m["narrative"] or "")[:80]
    lines = [
        f"Trace {trace_id[:8]}…  {r['recorded_at'][:19]}",
        f"Query: {repr((r['query'] or '')[:80])}",
        f"{len(nodes)} nodes activated:\n",
    ]
    for n in sorted(nodes, key=lambda x: x.get("sequence_pos", 0)):
        nid = n["node_id"]
        narrative = narratives.get(nid, "")
        lines.append(
            f"  [{n.get('sequence_pos', '?'):>3}] rel={n.get('relevance', 0):.3f} "
            f"[{n.get('memory_type', '?')}] {nid}  {narrative}"
        )
    return "\n".join(lines)


def tail_heat(node_id: str, pg_url: str = _PG_URL) -> str:
    rows = _q(
        "SELECT weight, recorded_at FROM tails WHERE node_id = %s "
        "ORDER BY recorded_at DESC LIMIT 50",
        (node_id,),
        pg_url,
    )
    if not rows:
        return f"No tail entries for {node_id} — never activated or too old."
    now = datetime.now()
    total = 0.0
    for r in rows:
        try:
            recorded_at = datetime.fromisoformat(str(r["recorded_at"]))
            elapsed_hours = (now - recorded_at).total_seconds() / 3600.0
            factor = 0.5 ** (max(0.0, elapsed_hours) / 24.0)
            total += (r["weight"] or 1.0) * factor
        except Exception:
            continue
    return f"Tail heat for {node_id}: {total:.4f} ({len(rows)} entries)"


def hot_nodes(limit: int = 10, since_hours: float = 2.0, pg_url: str = _PG_URL) -> str:
    cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
    rows = _q(
        "SELECT node_id, SUM(weight) as raw_weight, MAX(recorded_at) as last_seen, COUNT(*) as hits "
        "FROM tails WHERE recorded_at > %s "
        "GROUP BY node_id ORDER BY raw_weight DESC LIMIT %s",
        (cutoff, limit * 3),
        pg_url,
    )
    if not rows:
        return f"No tail activity in last {since_hours}h."
    now = datetime.now()
    scored = []
    for r in rows:
        try:
            last = datetime.fromisoformat(str(r["last_seen"]))
            elapsed = (now - last).total_seconds() / 3600.0
            heat = float(r["raw_weight"] or 0) * (0.5 ** (elapsed / 24.0))
            scored.append((heat, r))
        except Exception:
            continue
    scored.sort(key=lambda x: -x[0])
    lines = [f"Hot nodes (last {since_hours}h):\n"]
    for heat, r in scored[:limit]:
        lines.append(
            f"  heat={heat:.3f} hits={r['hits']} id={r['node_id']} last={str(r['last_seen'])[:19]}"
        )
    return "\n".join(lines)


def hot_attractors(limit: int = 10, pg_url: str = _PG_URL) -> str:
    rows = _q(
        "SELECT m.id, m.narrative, m.activation_count, m.memory_type, "
        "COUNT(ie.id) as inbound_count "
        "FROM memories m "
        "LEFT JOIN interpretive_edges ie ON ie.to_id = m.id "
        "WHERE m.memory_type NOT IN ('PROCEDURAL') "
        "GROUP BY m.id, m.narrative, m.activation_count, m.memory_type "
        "ORDER BY m.activation_count * (1 + COUNT(ie.id)) DESC "
        "LIMIT %s",
        (limit,),
        pg_url,
    )
    if not rows:
        return "No attractors found yet."
    lines = [f"Top {len(rows)} attractors:\n"]
    for r in rows:
        score = (r["activation_count"] or 0) * (1 + (r["inbound_count"] or 0))
        lines.append(
            f"  score={score} act={r['activation_count']} in={r['inbound_count']} "
            f"[{r['memory_type']}] {r['id'][:8]}… {str(r['narrative'] or '')[:60]}"
        )
    return "\n".join(lines)


def habit_list(query: str | None = None, limit: int = 30, pg_url: str = _PG_URL) -> str:
    params: list = []
    where = "WHERE metadata->>'trigger' IS NOT NULL "
    if query:
        where += "AND (LOWER(id) LIKE %s OR LOWER(metadata->>'trigger') LIKE %s) "
        params += [f"%{query.lower()}%", f"%{query.lower()}%"]
    params.append(limit)
    rows = _q(
        f"SELECT id, memory_type, metadata->>'trigger' as trigger, "
        f"metadata->>'habit_type' as habit_type "
        f"FROM memories {where}ORDER BY id LIMIT %s",
        params,
        pg_url,
    )
    if not rows:
        return "No habits found."
    lines = [f"{len(rows)} habits:\n"]
    for r in rows:
        lines.append(
            f"  {r['id']}\n"
            f"    type={r['habit_type']}  trigger={repr((r['trigger'] or '')[:80])}"
        )
    return "\n".join(lines)


def turn_trace_recent(limit: int = 5, since_minutes: int | None = None) -> str:
    log_dir = Path.home() / ".unseen_university" / "local" / "logs"
    today = datetime.now().strftime("%Y%m%d")
    log_file = log_dir / f"turn_trace.{today[:4]}-{today[4:6]}-{today[6:]}.log"
    if not log_file.exists():
        files = sorted(
            log_dir.glob("turn_trace.*.log"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return "No turn_trace log found."
        log_file = files[0]

    cutoff = None
    if since_minutes:
        cutoff = datetime.now() - timedelta(minutes=since_minutes)

    text = log_file.read_text(errors="replace")
    blocks = re.split(r"=== turn ", text)[1:]
    turns = []
    for block in blocks:
        try:
            header_end = block.index("\n")
            header = block[:header_end]
            parts = [p.strip() for p in header.split("|")]
            turn_id = parts[0]
            ts_str = parts[2] if len(parts) > 2 else ""
            if cutoff:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts < cutoff:
                        continue
                except Exception:
                    pass
            json_start = block.index("{")
            json_end = block.rindex("}") + 1
            data = json.loads(block[json_start:json_end])
            turns.append((ts_str, turn_id, data))
        except Exception:
            continue

    if not turns:
        return "No turns found in log."
    turns.sort(key=lambda x: x[0], reverse=True)
    turns = turns[:limit]
    lines = [f"{len(turns)} recent turns:\n"]
    for ts, turn_id, d in turns:
        intent = d.get("thalamus", {}).get("intent", "?")
        bg = d.get("bg_prospect", {}).get("habit", "none")
        habit_fired = d.get("habit_exec", {}).get("habit_id", "—")
        tier = d.get("response", {}).get("tier", "?")
        cost = d.get("response", {}).get("cost_usd", 0)
        preview = (d.get("response", {}).get("preview") or "")[:80].replace("\n", " ")
        inp = (d.get("input") or "")[:60].replace("\n", " ")
        lines.append(
            f"  [{ts[:19]}] {turn_id}\n"
            f"    in:     {inp!r}\n"
            f"    intent: {intent}  BG: {bg}  fired: {habit_fired}  tier: {tier}  ${cost:.4f}\n"
            f"    out:    {preview!r}"
        )
    return "\n".join(lines)


def consult_sessions_recent(
    limit: int = 10, since_minutes: int | None = None, session_id: str | None = None
) -> str:
    log_path = Path.home() / ".unseen_university" / "local" / "logs" / "consults.log"
    if not log_path.exists():
        return f"No consults log found at {log_path}."

    cutoff_iso: str | None = None
    if since_minutes is not None:
        cutoff = datetime.now() - timedelta(minutes=since_minutes)
        cutoff_iso = cutoff.isoformat(timespec="seconds")

    sessions: dict[str, dict] = {}
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            sid = evt.get("session_id")
            if not sid:
                continue
            if session_id and sid != session_id:
                continue
            sess = sessions.setdefault(
                sid, {"open": None, "turns": [], "confab_flags": [], "close": None}
            )
            kind = evt.get("event")
            if kind == "session_open":
                sess["open"] = evt
            elif kind in ("ask_ok", "ask_error", "parse_error"):
                sess["turns"].append(evt)
            elif kind == "confab_flag":
                sess["confab_flags"].append(evt)
            elif kind == "session_close":
                sess["close"] = evt

    if cutoff_iso is not None:
        sessions = {
            sid: s
            for sid, s in sessions.items()
            if s.get("open") and s["open"].get("ts", "") >= cutoff_iso
        }

    ordered = sorted(
        sessions.items(),
        key=lambda kv: kv[1].get("open", {}).get("ts", ""),
        reverse=True,
    )
    if not session_id:
        ordered = ordered[:limit]

    if not ordered:
        return "No matching consult sessions."

    blocks: list[str] = []
    for sid, s in ordered:
        opn = s.get("open") or {}
        cls = s.get("close") or {}
        turn_count = cls.get("turn_count", len(s["turns"]))
        head = (
            f"── session {sid} "
            f"({opn.get('problem_kind','?')} / {opn.get('tier','?')} / "
            f"{opn.get('model','?')})\n"
            f"   ticket={opn.get('ticket_id','-')} pursuit={opn.get('pursuit_id','-')}\n"
            f"   summary: {(opn.get('summary') or '')[:200]}\n"
            f"   turns: {turn_count}  confab_flags: {len(s['confab_flags'])}"
        )
        blocks.append(head)
        for i, t in enumerate(s["turns"]):
            ev = t.get("event", "?")
            if ev == "ask_ok":
                hyps = t.get("hypotheses") or []
                hyp_lines = "\n".join(f"      - {h[:200]}" for h in hyps)
                blocks.append(
                    f"  turn {t.get('turn_idx', i)} ({ev}, conf={t.get('confidence',0):.2f}, "
                    f"{t.get('elapsed_ms',0)}ms)\n"
                    f"    hypotheses:\n{hyp_lines if hyp_lines else '      (none)'}\n"
                    f"    next_question: {(t.get('next_question') or '')[:200]}"
                )
            else:
                blocks.append(
                    f"  turn {t.get('turn_idx', i)} ({ev}): "
                    f"{(t.get('error') or '')[:200]}"
                )
        if s["confab_flags"]:
            for cf in s["confab_flags"]:
                flag_summaries = ", ".join(
                    f"{fl.get('subtype','?')}@{fl.get('confidence',0):.2f}"
                    for fl in cf.get("flags", [])
                )
                blocks.append(
                    f"  ⚠ confab turn={cf.get('turn_idx','?')}: {flag_summaries}"
                )
        if cls:
            blocks.append(
                f"  conclusion: {(cls.get('final_hypothesis') or '')[:200]} "
                f"(conf={cls.get('confidence',0):.2f})"
            )
        else:
            blocks.append("  conclusion: <session still open>")
        blocks.append("")
    return "\n".join(blocks).rstrip()


def wg_neighbors(
    word: str, limit: int = 20, min_score: float = 0.1, pg_url: str = _PG_URL
) -> str:
    rows = _q(
        "SELECT word_b as neighbor, score FROM wg_edges "
        "WHERE word_a = %s AND score >= %s "
        "ORDER BY score DESC LIMIT %s",
        (word.lower(), min_score, limit),
        pg_url,
    )
    if not rows:
        rows = _q(
            "SELECT word_a as neighbor, score FROM wg_edges "
            "WHERE word_b = %s AND score >= %s "
            "ORDER BY score DESC LIMIT %s",
            (word.lower(), min_score, limit),
            pg_url,
        )
    if not rows:
        return f"No wg_edges neighbors found for '{word}'"
    lines = [f"Neighbors of '{word}' ({len(rows)}):\n"]
    for r in rows:
        lines.append(f"  {r['score']:.4f}  {r['neighbor']}")
    return "\n".join(lines)


def dispatch(name: str, args: dict, pg_url: str = _PG_URL) -> str | None:
    _handlers = {
        "traces_recent":         lambda a: traces_recent(a.get("limit", 10), a.get("since_minutes"), pg_url),
        "traces_get":            lambda a: traces_get(a["trace_id"], pg_url),
        "tail_heat":             lambda a: tail_heat(a["node_id"], pg_url),
        "hot_nodes":             lambda a: hot_nodes(a.get("limit", 10), a.get("since_hours", 2.0), pg_url),
        "hot_attractors":        lambda a: hot_attractors(a.get("limit", 10), pg_url),
        "habit_list":            lambda a: habit_list(a.get("query"), a.get("limit", 30), pg_url),
        "turn_trace_recent":     lambda a: turn_trace_recent(a.get("limit", 5), a.get("since_minutes")),
        "consult_sessions_recent": lambda a: consult_sessions_recent(
            a.get("limit", 10), a.get("since_minutes"), a.get("session_id")
        ),
        "wg_neighbors":          lambda a: wg_neighbors(a["word"], a.get("limit", 20), a.get("min_score", 0.1), pg_url),
    }
    handler = _handlers.get(name)
    return handler(args) if handler else None
