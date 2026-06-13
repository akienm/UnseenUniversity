"""
code_sweep.py — AST sweep of .py files → clan.code_index.

Walks devices/**/*.py and unseen_university/**/*.py, extracts function/class
signatures + docstrings, upserts into clan.code_index using content_hash for
staleness detection. Called by Nanny Ogg's daily sweep schedule entry.

Usage (standalone):
    python3 -m devices.nanny.sweeps.code_sweep [--repo-root PATH] [--dry-run]

D-semantic-indexing-2026-06-09
"""

from __future__ import annotations

import ast
import hashlib
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Generator


# ── Symbol extraction ──────────────────────────────────────────────────────────


@dataclass
class CodeSymbol:
    """A function or class extracted from a .py file."""

    path: str       # relative path from repo root
    symbol: str     # function/class name (dotted for methods: ClassName.method)
    kind: str       # 'function' | 'async_function' | 'class' | 'method'
    summary: str    # first docstring line + signature (truncated to 500 chars)
    content_hash: str  # MD5 of normalized source


def _first_docstring(node: ast.AST) -> str:
    """Return the first string constant in a function/class body, or ''."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return ""
    if node.body and isinstance(node.body[0], ast.Expr):
        val = node.body[0].value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            return val.value.strip().splitlines()[0]
    return ""


def _node_hash(source: str, node: ast.AST) -> str:
    """MD5 of the source lines covered by the AST node."""
    lines = source.splitlines()
    start = node.lineno - 1
    end = getattr(node, "end_lineno", start + 1)
    body = "\n".join(lines[start:end])
    return hashlib.md5(body.encode()).hexdigest()


def _make_summary(node: ast.AST, source: str) -> str:
    """Build a short summary: signature + first docstring line."""
    docstring = _first_docstring(node)
    if isinstance(node, ast.ClassDef):
        sig = f"class {node.name}"
        bases = ", ".join(ast.unparse(b) for b in node.bases) if node.bases else ""
        if bases:
            sig += f"({bases})"
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            args_str = ast.unparse(node.args)
        except Exception:
            args_str = "..."
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        sig = f"{prefix} {node.name}({args_str})"
    else:
        sig = str(node)

    combined = sig
    if docstring:
        combined += f" — {docstring}"
    return combined[:500]


def extract_symbols(path: Path, repo_root: Path) -> list[CodeSymbol]:
    """Parse a .py file and return all top-level + method symbols."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    rel_path = str(path.relative_to(repo_root))
    symbols: list[CodeSymbol] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            symbols.append(CodeSymbol(
                path=rel_path,
                symbol=node.name,
                kind=kind,
                summary=_make_summary(node, source),
                content_hash=_node_hash(source, node),
            ))
        elif isinstance(node, ast.ClassDef):
            symbols.append(CodeSymbol(
                path=rel_path,
                symbol=node.name,
                kind="class",
                summary=_make_summary(node, source),
                content_hash=_node_hash(source, node),
            ))
            # Methods inside the class
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(CodeSymbol(
                        path=rel_path,
                        symbol=f"{node.name}.{child.name}",
                        kind="method",
                        summary=_make_summary(child, source),
                        content_hash=_node_hash(source, child),
                    ))

    return symbols


def iter_py_files(repo_root: Path) -> Generator[Path, None, None]:
    """Yield .py files under devices/ and unseen_university/."""
    for subdir in ("devices", "unseen_university"):
        base = repo_root / subdir
        if base.exists():
            for f in base.rglob("*.py"):
                if "__pycache__" not in str(f):
                    yield f


# ── DB upsert ──────────────────────────────────────────────────────────────────


def run_sweep(
    db_url: str | None = None,
    repo_root: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Run the full sweep. Returns {"inserted": N, "updated": N, "unchanged": N, "errors": N}."""
    if repo_root is None:
        from unseen_university._uu_root import uu_root
        repo_root = Path(uu_root())

    if db_url is None:
        db_url = os.environ.get(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )

    counters = {"inserted": 0, "updated": 0, "unchanged": 0, "errors": 0}

    if dry_run:
        for f in iter_py_files(repo_root):
            syms = extract_symbols(f, repo_root)
            counters["inserted"] += len(syms)
        return counters

    import psycopg2

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            for py_file in iter_py_files(repo_root):
                symbols = extract_symbols(py_file, repo_root)
                for sym in symbols:
                    try:
                        # Check for existing row
                        cur.execute(
                            "SELECT content_hash FROM clan.code_index WHERE path = %s AND symbol = %s",
                            (sym.path, sym.symbol),
                        )
                        row = cur.fetchone()
                        if row is None:
                            cur.execute(
                                """INSERT INTO clan.code_index
                                   (path, symbol, kind, summary, content_hash, updated_at)
                                   VALUES (%s, %s, %s, %s, %s, now())""",
                                (sym.path, sym.symbol, sym.kind, sym.summary, sym.content_hash),
                            )
                            counters["inserted"] += 1
                        elif row[0] != sym.content_hash:
                            cur.execute(
                                """UPDATE clan.code_index
                                   SET kind = %s, summary = %s, content_hash = %s,
                                       embedding = NULL, updated_at = now()
                                   WHERE path = %s AND symbol = %s""",
                                (sym.kind, sym.summary, sym.content_hash, sym.path, sym.symbol),
                            )
                            counters["updated"] += 1
                        else:
                            counters["unchanged"] += 1
                    except Exception as e:
                        counters["errors"] += 1
                        conn.rollback()
                        continue
        conn.commit()
    finally:
        conn.close()

    return counters


def queue_depth(db_url: str | None = None) -> int:
    """Return count of code_index rows with no embedding."""
    if db_url is None:
        db_url = os.environ.get(
            "UU_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
    import psycopg2
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM clan.code_index WHERE embedding IS NULL")
            return cur.fetchone()[0]
    finally:
        conn.close()


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sweep .py files into clan.code_index")
    parser.add_argument("--repo-root", default=None, help="Path to repo root")
    parser.add_argument("--dry-run", action="store_true", help="Count symbols without writing")
    parser.add_argument("--db-url", default=None, help="Postgres DB URL")
    args = parser.parse_args()

    root = Path(args.repo_root) if args.repo_root else None
    result = run_sweep(db_url=args.db_url, repo_root=root, dry_run=args.dry_run)
    action = "dry-run" if args.dry_run else "sweep"
    print(
        f"{action}: inserted={result['inserted']} updated={result['updated']} "
        f"unchanged={result['unchanged']} errors={result['errors']}"
    )
