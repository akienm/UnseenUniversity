"""
ConstraintNormalizer — ingests project constraints from multiple sources
into devlab.constraints for queryable access.

Ingestion sources (V1):
  1. CLAUDE.md — hard rules and structural rules sections
  2. docs/design_patterns_inventory.md — per-pattern invariants
  3. Palace unseenuniversity/rules/* nodes (MCP memory_get, optional)
  4. Safeguards HIGH-inertia list (from palace or CLAUDE.md inertia refs)

Idempotency: each ingest() call deletes all rows for the source it is about
to write, then reinserts. Running twice produces the same final row count.

MCP tool exposed: get_constraints(files=[], tags=[], severity=None)

D-storage-layer-formalization-2026-06-14
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

_UU_ROOT = Path(__file__).parent.parent.parent


def _db_url() -> str:
    for key in ("UU_HOME_DB_URL", "IGOR_HOME_DB_URL"):
        val = os.environ.get(key, "")
        if val:
            return val
    raise RuntimeError("No DB URL — set UU_HOME_DB_URL or IGOR_HOME_DB_URL")


def _connect():
    import psycopg2
    return psycopg2.connect(_db_url())


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:16]


# ── Parsers ───────────────────────────────────────────────────────────────────


def _parse_claude_md() -> list[dict]:
    """Extract hard rules and structural rules from CLAUDE.md."""
    claude_md = _UU_ROOT / "CLAUDE.md"
    if not claude_md.exists():
        log.warning("ConstraintNormalizer: CLAUDE.md not found at %s", claude_md)
        return []

    text = claude_md.read_text()
    rows = []

    # Hard rules section — each bullet is a prohibit/require constraint
    hard_rules_match = re.search(
        r"## Hard rules\n(.*?)(?=\n## |\Z)", text, re.DOTALL
    )
    if hard_rules_match:
        section = hard_rules_match.group(1)
        for bullet in re.findall(r"^- (.+?)(?=\n- |\Z)", section, re.MULTILINE | re.DOTALL):
            bullet = bullet.strip()
            if not bullet:
                continue
            # Classify kind
            if any(kw in bullet.lower() for kw in ("no ", "never", "must not", "⛔")):
                kind = "prohibit"
            elif any(kw in bullet.lower() for kw in ("must", "always", "required")):
                kind = "require"
            else:
                kind = "prefer"

            # Classify severity
            if "⛔" in bullet or "NO SQLITE" in bullet or "ABSOLUTE" in bullet.upper():
                severity = "hard_block"
            elif "must" in bullet.lower() or "no " in bullet.lower():
                severity = "error"
            else:
                severity = "warn"

            rows.append({
                "text": bullet[:500],
                "kind": kind,
                "severity": severity,
                "applies_to": {"files": [], "operations": [], "tags": ["all"]},
                "source": {"type": "claude_md", "ref": "CLAUDE.md#hard-rules"},
                "implies": [],
            })

    # Structural rules — prefer/require constraints
    structural_match = re.search(
        r"## Structural rules\n(.*?)(?=\n## |\Z)", text, re.DOTALL
    )
    if structural_match:
        section = structural_match.group(1)
        for bullet in re.findall(r"^- (.+?)(?=\n- |\Z)", section, re.MULTILINE | re.DOTALL):
            bullet = bullet.strip()
            if not bullet:
                continue
            kind = "require"
            # Audit-enforced structural rules are not advisory: an audit check
            # (e.g. AR-009 "log every state change and interface crossing") gates
            # the build, so a violation is a hard block, not a soft error. Detect
            # the narrowest signal — an explicit audit-rule reference — rather than
            # blanket-promoting every structural bullet.
            lo = bullet.lower()
            if "enforced by audit" in lo or re.search(r"\bar-\d", lo):
                severity = "hard_block"
            else:
                severity = "error"
            rows.append({
                "text": bullet[:500],
                "kind": kind,
                "severity": severity,
                "applies_to": {"files": [], "operations": [], "tags": ["architecture"]},
                "source": {"type": "claude_md", "ref": "CLAUDE.md#structural-rules"},
                "implies": [],
            })

    log.info("ConstraintNormalizer: parsed %d constraints from CLAUDE.md", len(rows))
    return rows


def _parse_design_patterns() -> list[dict]:
    """Extract invariants from docs/design_patterns_inventory.md."""
    inv = _UU_ROOT / "docs" / "design_patterns_inventory.md"
    if not inv.exists():
        log.warning("ConstraintNormalizer: design_patterns_inventory.md not found")
        return []

    text = inv.read_text()
    rows = []

    # Find each pattern section
    for match in re.finditer(
        r"## (PATTERN-\d+: .+?)\n(.*?)(?=\n## PATTERN-|\Z)", text, re.DOTALL
    ):
        pattern_title = match.group(1).strip()
        section = match.group(2)

        pattern_tag = pattern_title.split(":")[0].strip().lower()
        pattern_source = {"type": "design_patterns", "ref": f"docs/design_patterns_inventory.md#{pattern_title}"}
        pattern_applies = {"files": [], "operations": [], "tags": ["pattern", pattern_tag]}

        # Extract **Invariant:**, **Rule:**, **External state rule:** lines
        for rule_match in re.finditer(
            r"\*\*(Invariant|Rule|External state rule|Canonical rule):\*\*\s*(.+?)(?=\n\n|\n\*\*|\Z)",
            section, re.DOTALL
        ):
            rule_text = rule_match.group(2).strip()
            label = rule_match.group(1)
            if not rule_text:
                continue
            is_prohibit = any(kw in rule_text.lower() for kw in ("never", "must not", "prohibited"))
            rows.append({
                "text": f"{pattern_title}: {rule_text}"[:500],
                "kind": "prohibit" if is_prohibit else "require",
                "severity": "error",
                "applies_to": pattern_applies,
                "source": pattern_source,
                "implies": [],
            })

    log.info("ConstraintNormalizer: parsed %d constraints from design_patterns_inventory.md", len(rows))
    return rows


def _parse_palace_rules_db(conn=None) -> list[dict]:
    """Read unseenuniversity/rules/* from clan.memory_palace via direct DB query.

    Falls back gracefully if table is unavailable or unreachable.
    """
    close_conn = False
    if conn is None:
        try:
            conn = _connect()
            close_conn = True
        except Exception as exc:
            log.info("ConstraintNormalizer: palace DB unavailable — %s", exc)
            return []

    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT path, content FROM clan.memory_palace
                WHERE path LIKE 'unseenuniversity/rules/%'
                  AND content IS NOT NULL AND content != ''
                ORDER BY path
                LIMIT 40
                """
            )
            for path, content in cur.fetchall():
                if not content or not content.strip():
                    continue
                # Use first 350 chars as the constraint text
                text = content.strip().replace("\n", " ")[:350]
                # Classify kind based on keywords in content
                lower = content.lower()
                if any(kw in lower for kw in ("never ", "must not", "prohibited", "no ")):
                    kind = "prohibit"
                elif any(kw in lower for kw in ("must ", "always ", "required", "enforce")):
                    kind = "require"
                else:
                    kind = "prefer"
                severity = "error" if kind in ("prohibit", "require") else "warn"
                rows.append({
                    "text": text,
                    "kind": kind,
                    "severity": severity,
                    "applies_to": {"files": [], "operations": [], "tags": ["palace-rule", path.split("/")[-1]]},
                    "source": {"type": "palace", "ref": path},
                    "implies": [],
                })
    except Exception as exc:
        log.info("ConstraintNormalizer: palace DB query failed — %s", exc)
    finally:
        if close_conn and conn:
            conn.close()

    log.info("ConstraintNormalizer: parsed %d constraints from palace DB", len(rows))
    return rows


def _parse_palace_safeguards() -> list[dict]:
    """Read HIGH-inertia safeguards from palace via memory_get. Fail-open."""
    try:
        from devices.igor.tools.memory import memory_get  # type: ignore
        content = memory_get(path="unseenuniversity/rules/safeguards")
        if not content:
            return []
    except Exception as exc:
        log.info("ConstraintNormalizer: palace safeguards unavailable — %s", exc)
        return []

    rows = []
    # Parse HIGH-inertia entries: lines like "- HIGH: <description>"
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("- HIGH:") or "HIGH-inertia" in line:
            text = re.sub(r"^- HIGH:\s*", "", line).strip()
            if text:
                rows.append({
                    "text": text[:500],
                    "kind": "require",
                    "severity": "hard_block",
                    "applies_to": {"files": [], "operations": ["edit"], "tags": ["high-inertia"]},
                    "source": {"type": "palace", "ref": "unseenuniversity/rules/safeguards"},
                    "implies": [],
                })

    log.info("ConstraintNormalizer: parsed %d HIGH-inertia constraints from palace", len(rows))
    return rows


def _parse_palace_rules() -> list[dict]:
    """Read unseenuniversity/rules/* from palace. Fail-open."""
    rules = []
    rule_paths = [
        "unseenuniversity/rules/database",
        "unseenuniversity/rules/logging",
        "unseenuniversity/rules/docs-live-in-code",
        "unseenuniversity/rules/capability-protocol",
    ]
    try:
        from devices.igor.tools.memory import memory_get  # type: ignore
    except Exception:
        return []

    for path in rule_paths:
        try:
            content = memory_get(path=path)
            if not content:
                continue
            # Use first paragraph as the constraint text
            first_para = content.strip().split("\n\n")[0][:400]
            rules.append({
                "text": first_para,
                "kind": "require",
                "severity": "error",
                "applies_to": {"files": [], "operations": [], "tags": ["palace-rule", path.split("/")[-1]]},
                "source": {"type": "palace", "ref": path},
                "implies": [],
            })
        except Exception as exc:
            log.debug("ConstraintNormalizer: palace rule %s unavailable — %s", path, exc)

    log.info("ConstraintNormalizer: parsed %d palace rule constraints", len(rules))
    return rules


# ── Ingest ────────────────────────────────────────────────────────────────────


def ingest(conn=None) -> int:
    """Ingest all constraint sources into devlab.constraints.

    Idempotent: deletes existing rows for each source type before inserting.
    Returns total row count after ingestion.
    """
    close_conn = conn is None
    if conn is None:
        conn = _connect()

    all_rows = []
    all_rows.extend(_parse_claude_md())
    all_rows.extend(_parse_design_patterns())
    all_rows.extend(_parse_palace_safeguards())
    all_rows.extend(_parse_palace_rules())
    # Live palace rules via direct SQL — the import-based palace parsers above
    # fail-open when devices.igor.tools.memory is absent, so this is the source
    # that actually populates source.type="palace" in practice. All three palace
    # parsers share that one DELETE key, so they MUST run together (below) or
    # palace rows get orphaned across re-ingests.
    try:
        all_rows.extend(_parse_palace_rules_db(conn))
    except Exception as exc:  # fail-open: palace is one source among several
        log.warning("ConstraintNormalizer.ingest: palace SQL parse failed (%s) — continuing", exc)

    if not all_rows:
        log.warning("ConstraintNormalizer.ingest: no constraints extracted — check sources")
        if close_conn:
            conn.close()
        return 0

    try:
        with conn.cursor() as cur:
            # Delete existing rows per source type for idempotency
            source_types = list({r["source"]["type"] for r in all_rows})
            for src_type in source_types:
                cur.execute(
                    "DELETE FROM devlab.constraints WHERE source->>'type' = %s",
                    (src_type,),
                )
            log.info("ConstraintNormalizer.ingest: cleared existing rows for %d source types", len(source_types))

            # Insert all rows
            for row in all_rows:
                cur.execute(
                    """
                    INSERT INTO devlab.constraints
                        (text, kind, severity, applies_to, source, implies, created_at, updated_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, ARRAY[]::jsonb[], NOW(), NOW())
                    """,
                    (
                        row["text"],
                        row["kind"],
                        row["severity"],
                        json.dumps(row["applies_to"]),
                        json.dumps(row["source"]),
                    ),
                )
            conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM devlab.constraints")
            total = cur.fetchone()[0]

        log.info("ConstraintNormalizer.ingest: %d total constraints in devlab.constraints", total)
        return total

    finally:
        if close_conn:
            conn.close()


# ── MCP tool ──────────────────────────────────────────────────────────────────


def get_constraints(
    files: list[str] | None = None,
    tags: list[str] | None = None,
    severity: str | None = None,
    conn=None,
) -> list[dict]:
    """Return constraints from devlab.constraints matching the given filters.

    files: list of file paths — returns constraints whose applies_to.files
           overlap with the given list (or constraints with applies_to.files=[]).
    tags: list of tags — constraints whose applies_to.tags overlap.
    severity: 'hard_block' | 'error' | 'warn' — exact match.

    Returns list of dicts with keys: id, text, kind, severity, applies_to, source.
    """
    close_conn = conn is None
    if conn is None:
        conn = _connect()

    try:
        clauses = []
        params: list = []

        if severity:
            clauses.append("severity = %s")
            params.append(severity)

        if tags:
            clauses.append("applies_to->'tags' ?| %s")
            params.append(tags)

        if files:
            # Match if applies_to.files is empty OR overlaps with given files
            clauses.append(
                "(applies_to->'files' = '[]'::jsonb OR applies_to->'files' ?| %s)"
            )
            params.append(files)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT id, text, kind, severity, applies_to, source
            FROM devlab.constraints
            {where}
            ORDER BY severity DESC, id
            LIMIT 200
        """

        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = ["id", "text", "kind", "severity", "applies_to", "source"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    finally:
        if close_conn:
            conn.close()
