"""
AuditorDevice — codebase audit runner as a rack device.

Loads registered checks from devlab/claudecode/audit_checks.json and runs them on
demand. Additional checks may be placed in config/audit_checks/*.json (merged
at load time — no restart needed). Check results are persisted to
adc.audit_findings for history queries. Suppression patterns live in
adc.audit_allowlist.

MCP tools exposed:
    run_check(name)                        — run one check by name → finding[]
    run_all(severity_min='med', kind=None) — run all checks at/above severity → finding[]
    check_add(name, kind, pattern, ...)    — register a new forever check
    check_list()                           — list all registered checks
    allowlist_add(pattern, reason)         — add a suppression pattern
    finding_history(days=7)                — recent finding history

Check kinds: shell, grep, sql, python, baseline.
Baseline checks compare a metric_sql (current window) against a baseline_sql
(rolling average) and FAIL when current > threshold_multiplier × baseline.

Degrades gracefully when DB is unavailable — shell/grep/python checks still
run; only history recording, allowlist, and baseline checks require the DB.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from unseen_university.device import BaseDevice, INTERFACE_VERSION

log = logging.getLogger(__name__)

_START_TIME = time.time()

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKS_PATH = REPO_ROOT / "devlab" / "claudecode" / "audit_checks.json"
CHECKS_CONFIG_DIR = REPO_ROOT / "config" / "audit_checks"

_SEVERITY_ORDER: dict[str, int] = {"high": 0, "med": 1, "low": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_conn():
    import psycopg2

    url = os.environ.get("UU_HOME_DB_URL", "")
    if not url:
        raise RuntimeError(
            "UU_HOME_DB_URL not set — auditor device cannot connect to findings storage"
        )
    return psycopg2.connect(url)


def _is_acked(entry: dict) -> bool:
    until = entry.get("ack_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now()
    except Exception:
        return False


def _run_grep(entry: dict) -> tuple[str, str]:
    target = REPO_ROOT / "devices" / "igor"
    if not target.exists():
        return "ERROR", f"target not found: {target}"
    try:
        result = subprocess.run(
            ["grep", "-rn", "-i", "--include=*.py", entry["pattern"], str(target)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "ERROR", "grep timed out"
    if result.returncode == 0 and result.stdout:
        lines = result.stdout.strip().splitlines()
        return "FAIL", f"{len(lines)} match(es) — first: {lines[0][:120]}"
    if result.returncode == 1:
        return "PASS", "no matches"
    return "ERROR", result.stderr.strip()[:200]


def _run_sql(entry: dict) -> tuple[str, str]:
    try:
        import psycopg2
    except ImportError:
        return "ERROR", "psycopg2 not available"
    try:
        conn = _db_conn()
        cur = conn.cursor()
        cur.execute(entry["pattern"])
        rows = cur.fetchall()
        conn.close()
        if rows:
            return "FAIL", f"{len(rows)} row(s) — first: {str(rows[0])[:120]}"
        return "PASS", "no rows"
    except Exception as exc:
        return "ERROR", str(exc)[:200]


def _run_shell(entry: dict) -> tuple[str, str]:
    try:
        result = subprocess.run(
            entry["pattern"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return "ERROR", "shell timed out"
    if result.returncode == 0:
        detail = (
            result.stdout.strip().splitlines()[0][:120]
            if result.stdout.strip()
            else "ok"
        )
        return "PASS", detail
    detail = (
        (result.stdout or result.stderr).strip().splitlines()[0][:120]
        if (result.stdout or result.stderr).strip()
        else f"exit {result.returncode}"
    )
    return "FAIL", detail


def _run_python(entry: dict) -> tuple[str, str]:
    try:
        ns: dict = {"__builtins__": __builtins__}
        result = eval(entry["pattern"], ns)  # noqa: S307
    except Exception as exc:
        return "ERROR", str(exc)[:200]
    if result:
        return "FAIL", str(result)[:120]
    return "PASS", "falsy"


def _run_baseline(entry: dict) -> tuple[str, str]:
    """Baseline drift check: compare current metric to rolling average.

    Entry fields (native, not JSON-encoded):
      metric_sql          — SQL returning a single numeric count for the current window
      baseline_sql        — SQL returning a single numeric avg over the baseline window
      threshold_multiplier — FAIL when current > threshold × baseline (default 3.0)

    Returns PASS when rate is normal or baseline data is insufficient.
    Returns FAIL with current/baseline/ratio detail when drift exceeds threshold.
    """
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return "ERROR", "psycopg2 not available"

    metric_sql = entry.get("metric_sql", "")
    baseline_sql = entry.get("baseline_sql", "")
    threshold = float(entry.get("threshold_multiplier", 3.0))

    if not metric_sql or not baseline_sql:
        return "ERROR", "baseline check requires metric_sql and baseline_sql fields"

    try:
        conn = _db_conn()
        cur = conn.cursor()

        cur.execute(metric_sql)
        metric_row = cur.fetchone()
        current = (
            float(metric_row[0]) if metric_row and metric_row[0] is not None else 0.0
        )

        cur.execute(baseline_sql)
        baseline_row = cur.fetchone()
        baseline_val = (
            float(baseline_row[0])
            if baseline_row and baseline_row[0] is not None
            else 0.0
        )

        conn.close()
    except Exception as exc:
        return "ERROR", str(exc)[:200]

    if baseline_val == 0.0:
        return "PASS", f"insufficient-baseline (baseline=0, current={current:.1f})"

    ratio = current / baseline_val
    if ratio > threshold:
        return (
            "FAIL",
            f"drift: current={current:.1f} baseline={baseline_val:.1f} ratio={ratio:.2f}x > {threshold}x threshold",
        )

    return (
        "PASS",
        f"current={current:.1f} baseline={baseline_val:.1f} ratio={ratio:.2f}x",
    )


_DISPATCH = {
    "grep": _run_grep,
    "sql": _run_sql,
    "shell": _run_shell,
    "python": _run_python,
    "baseline": _run_baseline,
}


class AuditorDevice(BaseDevice):
    """Rack device that runs registered codebase audit checks.

    Check registry: lab/claudecode/audit_checks.json (forever + next_sweep lists).
    Check scripts: lab/claudecode/audit_check_*.py invoked as shell commands.
    Finding history: adc.audit_findings in Postgres (UU_HOME_DB_URL).
    Allowlist: adc.audit_allowlist in Postgres.
    """

    DEVICE_ID = "auditor"

    def __init__(self) -> None:
        super().__init__()
        self._errors: list[str] = []
        try:
            self._init_db_schema()
        except Exception as exc:
            log.warning("startup: DB schema init failed (non-fatal) — %s", exc)
            self._errors.append(f"DB schema init failed: {exc}")

    # ── DB schema ─────────────────────────────────────────────────────────────

    def _init_db_schema(self) -> None:
        conn = _db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS adc.audit_findings (
                        id       SERIAL PRIMARY KEY,
                        ran_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        check_name TEXT NOT NULL,
                        severity   TEXT NOT NULL,
                        status     TEXT NOT NULL,
                        detail     TEXT NOT NULL DEFAULT ''
                    )
                    """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS adc.audit_allowlist (
                        id         SERIAL PRIMARY KEY,
                        pattern    TEXT NOT NULL,
                        reason     TEXT NOT NULL,
                        added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """)
            conn.commit()
        finally:
            conn.close()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_checks(self) -> dict:
        result: dict = {"forever": [], "next_sweep": [], "history": []}
        if CHECKS_PATH.exists():
            result = json.loads(CHECKS_PATH.read_text())
        if CHECKS_CONFIG_DIR.exists():
            for cfg_file in sorted(CHECKS_CONFIG_DIR.glob("*.json")):
                try:
                    data = json.loads(cfg_file.read_text())
                    result["forever"].extend(data.get("forever", []))
                    result["next_sweep"].extend(data.get("next_sweep", []))
                except Exception as exc:
                    log.warning("_load_checks: failed to parse %s — %s", cfg_file, exc)
        return result

    def _run_one(self, check: dict) -> dict:
        name = check.get("name", "")
        severity = check.get("severity", "med")
        kind = check.get("kind", "shell")

        if _is_acked(check):
            return {
                "name": name,
                "severity": severity,
                "status": "ACKED",
                "detail": f"until {check['ack_until']}",
            }

        fn = _DISPATCH.get(kind)
        if fn is None:
            return {
                "name": name,
                "severity": severity,
                "status": "ERROR",
                "detail": f"unknown kind: {kind}",
            }

        status, detail = fn(check)
        return {"name": name, "severity": severity, "status": status, "detail": detail}

    def _record(self, finding: dict) -> None:
        try:
            conn = _db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO adc.audit_findings (check_name, severity, status, detail) VALUES (%s, %s, %s, %s)",
                        (
                            finding["name"],
                            finding["severity"],
                            finding["status"],
                            finding.get("detail", ""),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            log.debug("_record: DB unavailable — %s", exc)

    # ── MCP tools ─────────────────────────────────────────────────────────────

    def run_check(self, name: str) -> list[dict]:
        """Run a single named check. Returns a list containing one finding dict."""
        checks = self._load_checks()
        all_checks = checks.get("forever", []) + checks.get("next_sweep", [])
        entry = next((c for c in all_checks if c.get("name") == name), None)
        if entry is None:
            return [
                {
                    "name": name,
                    "severity": "med",
                    "status": "ERROR",
                    "detail": f"check not found: {name}",
                }
            ]
        finding = self._run_one(entry)
        self._record(finding)
        return [finding]

    def run_all(self, severity_min: str = "med", kind: str | None = None) -> list[dict]:
        """Run checks at or above severity_min, optionally filtered by kind.

        kind='baseline' runs only drift-detection checks; omit to run all kinds.
        Returns list of finding dicts.
        """
        checks = self._load_checks()
        all_checks = checks.get("forever", []) + checks.get("next_sweep", [])
        min_order = _SEVERITY_ORDER.get(severity_min, _SEVERITY_ORDER["med"])

        results = []
        for check in all_checks:
            if kind is not None and check.get("kind") != kind:
                continue
            check_sev = check.get("severity", "med")
            if _SEVERITY_ORDER.get(check_sev, 99) <= min_order:
                finding = self._run_one(check)
                self._record(finding)
                results.append(finding)
        return results

    def check_add(
        self,
        name: str,
        kind: str,
        pattern: str,
        severity: str,
        description: str,
    ) -> dict:
        """Register a new forever check in the checks file."""
        checks = self._load_checks()
        forever = checks.get("forever", [])
        if any(c.get("name") == name for c in forever):
            return {"status": "error", "detail": f"check {name} already exists"}
        entry = {
            "name": name,
            "kind": kind,
            "pattern": pattern,
            "severity": severity,
            "description": description,
            "added_by": "mcp",
            "added_at": _now(),
            "mode": "forever",
            "ack_until": None,
        }
        forever.append(entry)
        checks["forever"] = forever
        CHECKS_PATH.write_text(json.dumps(checks, indent=2) + "\n")
        return {"status": "ok"}

    def check_list(self) -> dict:
        """Return all registered checks (forever + next_sweep)."""
        checks = self._load_checks()
        return {
            "forever": checks.get("forever", []),
            "next_sweep": checks.get("next_sweep", []),
        }

    def allowlist_add(self, pattern: str, reason: str) -> dict:
        """Add a suppression pattern to the audit allowlist in Postgres."""
        try:
            conn = _db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO adc.audit_allowlist (pattern, reason) VALUES (%s, %s) RETURNING id",
                        (pattern, reason),
                    )
                conn.commit()
            finally:
                conn.close()
            return {"status": "ok"}
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}

    def finding_history(self, days: int = 7) -> list[dict]:
        """Return audit findings from the last N days, newest first."""
        try:
            conn = _db_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                SELECT id, ran_at, check_name, severity, status, detail
                FROM adc.audit_findings
                WHERE ran_at >= NOW() - (%s || ' days')::INTERVAL
                ORDER BY ran_at DESC
                LIMIT 500
                """,
                        (str(days),),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            log.warning("finding_history failed: %s", exc)
            return []

        result = []
        for id_, ran_at, check_name, severity, status, detail in rows:
            ts = ran_at.isoformat() if hasattr(ran_at, "isoformat") else str(ran_at)
            result.append(
                {
                    "id": id_,
                    "ran_at": ts,
                    "check_name": check_name,
                    "severity": severity,
                    "status": status,
                    "detail": detail,
                }
            )
        return result

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Auditor",
            "version": "1.0.0",
            "purpose": "Run registered codebase audit checks; persist findings history",
        }

    def requirements(self) -> dict:
        return {
            "deps": ["psycopg2"],
            "system": ["UU_HOME_DB_URL env var for findings storage"],
        }

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": False,
            "emitted_keywords": [],
            "mcp_tools": [
                "run_check",
                "run_all",
                "check_add",
                "check_list",
                "allowlist_add",
                "finding_history",
            ],
        }

    def comms(self) -> dict:
        return {
            "address": "comms://auditor",
            "mode": "read_only",
            "supports_push": False,
            "supports_pull": False,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        if not CHECKS_PATH.exists():
            return {
                "status": "unhealthy",
                "detail": f"checks file not found: {CHECKS_PATH}",
                "checked_at": _now(),
            }
        checks = self._load_checks()
        forever_count = len(checks.get("forever", []))
        next_count = len(checks.get("next_sweep", []))
        total = forever_count + next_count
        return {
            "status": "healthy",
            "detail": f"{total} registered checks",
            "checked_at": _now(),
            "check_count": {"forever": forever_count, "next_sweep": next_count},
        }

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._errors)

    def logs(self) -> dict:
        return {
            "paths": {"trace": str(Path.home() / ".unseen_university" / "datacenter_logs" / "auditor" / "trace")}
        }

    def update_info(self) -> dict:
        return {"current_version": "1.0.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.environ.get("HOSTNAME", "localhost"),
            "pid": os.getpid(),
            "launch_command": "python -m devices.auditor.mcp_server",
        }

    def restart(self) -> None:
        self._errors.clear()

    def block(self, reason: str) -> None:
        log.warning("auditor device blocked: %s", reason)
        self._errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        log.info("auditor device halt requested")

    def recovery(self) -> None:
        self._errors.clear()
