"""
repo_graph_map.py — graph-ranked, token-budgeted orientation packet (T-aider-port-graph-orientation-packet).

Our loop read-wandered (F-A: 47-102 Reads, 0 edits) because orientation was a MODEL behavior
driven by tool calls. aider makes orientation a DATA STRUCTURE: a graph-ranked repo map
delivered as context with zero tool calls — the single capability that carried aider's 4/4
multi-file checkout with nothing pre-added. This ports aider `repomap.py::get_ranked_tags` +
`get_ranked_tags_map_uncached` (D-aider-port-to-nexus-writepath-2026-07-07), onto substrate
that fits this environment:

  - tree-sitter is ABSENT → tags come from stdlib `ast` (Python def/ref graph — the ticket
    allows "Python import/ref graph OR tree-sitter tags"). Getting refs requires an ast pass
    anyway; that same pass yields defs, so there is no duplicate parse.
  - networkx (and its scipy dep) is ABSENT → personalized PageRank is a pure-Python power
    iteration with dangling mass redistributed by the personalization vector (aider passes
    `dangling=personalization`). Deterministic.
  - aider's TAGS_CACHE is SQLite → re-homed to a flat-file JSON cache keyed by mtime. ⛔ NO SQLITE.

DESIGN INVARIANTS:
  - The `def ∩ ref` intersection is THE noise filter and is load-bearing with `ast` (Name-loads
    include every local/self/builtin). Only idents both DEFINED and REFERENCED in-repo form
    edges — everything else is dropped before ranking.
  - PageRank is a RANK, never an identity key. The packet's structure/keys stay exact so it can
    be the deterministic input to the nexus packet-fingerprint (T-nexus-write-path-ds-architect-loop).
  - Fail-open: any error → the caller falls back to the keyword map; orientation never hard-fails.
"""

from __future__ import annotations

import ast
import json
import logging
import math
import re
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

log = logging.getLogger(__name__)

_HEADER = (
    "## Repo graph map (files ranked by reference-graph centrality toward the task; "
    "plan from this structure, read a file only if the map is insufficient)"
)
_DEFAULT_BUDGET_CHARS = 3500
_DAMPING = 0.85
_MAX_ITER = 100
_TOL = 1e-6


# ── Tag extraction (stdlib ast — one pass yields defs AND refs) ──────────────────────────────

def extract_tags(source: str) -> tuple[set[str], list[str]]:
    """Return ``(defs, refs)`` for one Python source string.

    defs  = function/class names (any nesting) + MODULE-LEVEL assigned names (constants).
    refs  = every Name(Load) id and Attribute attr (the noise the def∩ref intersection filters).
    A syntax error yields empty tags (fail-open per file), never a raise.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set(), []

    defs: set[str] = set()
    refs: list[str] = []

    # Module-level assigned names are meaningful defs (constants, singletons); locals are not.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    defs.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defs.add(node.target.id)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            refs.append(node.id)
        elif isinstance(node, ast.Attribute):
            refs.append(node.attr)

    return defs, refs


# ── Personalized PageRank (pure power iteration; dangling → personalization) ──────────────────

def pagerank(
    nodes,
    edges,
    personalization: dict | None = None,
    *,
    damping: float = _DAMPING,
    max_iter: int = _MAX_ITER,
    tol: float = _TOL,
) -> dict:
    """Personalized PageRank over a weighted directed multigraph, pure Python.

    ``edges`` is an iterable of ``(src, dst, weight, ident)`` (ident ignored here; edge weights
    to the same dst accumulate). ``personalization`` maps node→score (unnormalized); it seeds
    the teleport vector AND absorbs dangling-node mass (mirrors aider's `dangling=personalization`).
    Absent/empty personalization → uniform. Deterministic: no randomness, fixed iteration order.
    """
    nodes = list(nodes)
    n = len(nodes)
    if n == 0:
        return {}

    out_weight: dict = defaultdict(float)
    adj: dict = defaultdict(list)
    for src, dst, weight, *_ in edges:
        out_weight[src] += weight
        adj[src].append((dst, weight))

    if personalization and sum(personalization.get(u, 0.0) for u in nodes) > 0:
        total = sum(personalization.get(u, 0.0) for u in nodes)
        pers = {u: personalization.get(u, 0.0) / total for u in nodes}
    else:
        pers = {u: 1.0 / n for u in nodes}

    rank = {u: pers[u] for u in nodes}
    dangling_nodes = [u for u in nodes if out_weight[u] == 0.0]

    for _ in range(max_iter):
        prev = rank
        rank = {u: 0.0 for u in nodes}
        # Teleport + dangling mass, both distributed over the personalization vector.
        dangling_mass = sum(prev[u] for u in dangling_nodes)
        leak = (1.0 - damping) + damping * dangling_mass
        for u in nodes:
            rank[u] += leak * pers[u]
        # Follow edges, splitting each source's rank across its out-weight.
        for src, out in adj.items():
            src_rank = prev[src]
            w_total = out_weight[src]
            if w_total <= 0.0:
                continue
            for dst, weight in out:
                rank[dst] += damping * src_rank * weight / w_total
        if sum(abs(rank[u] - prev[u]) for u in nodes) < tol:
            break

    return rank


# ── Graph build + ranking (port of aider get_ranked_tags) ────────────────────────────────────

def rank_tags(tags: dict, mentioned_fnames: set, mentioned_idents: set):
    """Rank (file, ident) definitions by personalized PageRank over the def/ref graph.

    ``tags`` maps rel-path → ``(defs, refs)`` (pre-extracted, so the caller parses/caches once).
    Returns ``(ranked_tags, ranks)``: ranked_tags is a list of ``(fname, ident)`` (definitions,
    most-central first) then ``(fname,)`` for ranked files with no surviving tag; ranks is the
    per-file PageRank. This is the single ranking core — `build_ranked_tags` and `build_graph_map`
    both feed it.
    """
    defines: dict = defaultdict(set)       # ident -> {rel}
    references: dict = defaultdict(list)   # ident -> [rel, ...]
    personalization: dict = {}

    relpaths = sorted(tags)
    if not relpaths:
        return [], {}
    personalize = 100.0 / len(relpaths)

    for rel in relpaths:
        defs, refs = tags[rel]

        cur = 0.0
        if rel in mentioned_fnames:
            cur = max(cur, personalize)
        path_obj = Path(rel)
        components = set(path_obj.parts) | {path_obj.name, path_obj.stem}
        if components & mentioned_idents:
            cur += personalize
        if cur > 0:
            personalization[rel] = cur

        for d in defs:
            defines[d].add(rel)
        for r in refs:
            references[r].append(rel)

    if not references:
        references = {k: list(v) for k, v in defines.items()}

    # THE noise filter: only idents both defined AND referenced in-repo form edges.
    idents = set(defines.keys()) & set(references.keys())

    edges = []  # (referencer, definer, weight, ident)
    # Self-edge for definitions never referenced (keeps isolated defs in the graph, low weight).
    for ident in defines:
        if ident in references:
            continue
        for definer in defines[ident]:
            edges.append((definer, definer, 0.1, ident))

    for ident in idents:
        definers = defines[ident]
        mul = 1.0
        is_snake = ("_" in ident) and any(c.isalpha() for c in ident)
        is_kebab = ("-" in ident) and any(c.isalpha() for c in ident)
        is_camel = any(c.isupper() for c in ident) and any(c.islower() for c in ident)
        if ident in mentioned_idents:
            mul *= 10.0
        if (is_snake or is_kebab or is_camel) and len(ident) >= 8:
            mul *= 10.0
        if ident.startswith("_"):
            mul *= 0.1
        if len(defines[ident]) > 5:
            mul *= 0.1
        for referencer, num_refs in Counter(references[ident]).items():
            weight = mul * math.sqrt(num_refs)
            for definer in definers:
                edges.append((referencer, definer, weight, ident))

    ranks = pagerank(set(relpaths), edges, personalization)

    # Distribute each source's rank across its out-edges → per-(dst, ident) rank.
    out_weight: dict = defaultdict(float)
    for src, _dst, weight, _ident in edges:
        out_weight[src] += weight
    ranked_definitions: dict = defaultdict(float)
    for src, dst, weight, ident in edges:
        if out_weight[src] > 0:
            ranked_definitions[(dst, ident)] += ranks.get(src, 0.0) * weight / out_weight[src]

    ordered = sorted(ranked_definitions.items(), reverse=True, key=lambda kv: (kv[1], kv[0]))
    ranked_tags = []
    seen_files = set()
    for (fname, ident), _rank in ordered:
        ranked_tags.append((fname, ident))
        seen_files.add(fname)

    # Append any ranked file with no surviving tag, in PageRank order (bare-file entries).
    for fname, _r in sorted(ranks.items(), key=lambda kv: (-kv[1], kv[0])):
        if fname not in seen_files:
            ranked_tags.append((fname,))
            seen_files.add(fname)

    return ranked_tags, ranks


def build_ranked_tags(files: dict, mentioned_fnames: set, mentioned_idents: set):
    """Parse ``files`` (rel-path → source) into tags, then rank. Thin wrapper over `rank_tags`."""
    tags = {rel: extract_tags(src) for rel, src in files.items()}
    return rank_tags(tags, mentioned_fnames, mentioned_idents)


# ── Token-budget rendering (port of get_ranked_tags_map_uncached binary search) ───────────────

def _tags_to_text(prefix, header: str) -> str:
    """Render a prefix of ranked tags into grouped per-file signature lines, in rank order."""
    by_file: dict = OrderedDict()
    for tag in prefix:
        fname = tag[0]
        by_file.setdefault(fname, [])
        if len(tag) > 1 and tag[1] not in by_file[fname]:
            by_file[fname].append(tag[1])
    lines = [header]
    for fname, idents in by_file.items():
        lines.append(f"{fname}: {', '.join(idents)}" if idents else fname)
    return "\n".join(lines)


def budget_packet(ranked_tags, budget_chars: int, header: str = _HEADER) -> str:
    """Binary-search the largest prefix of ranked_tags whose rendered text fits budget_chars.

    Mirrors aider's `get_ranked_tags_map_uncached`: bisect on the number of tags included,
    keeping the largest map that stays within budget. Always shows at least the top entry.
    """
    if not ranked_tags:
        return ""
    lo, hi = 0, len(ranked_tags)
    best = ""
    mid = len(ranked_tags)
    while lo <= hi:
        text = _tags_to_text(ranked_tags[:mid], header)
        if len(text) <= budget_chars:
            best = text
            lo = mid + 1
        else:
            hi = mid - 1
        mid = (lo + hi) // 2
    return best or _tags_to_text(ranked_tags[:1], header)


# ── Flat-file tag cache (mtime-keyed; NO SQLITE) ─────────────────────────────────────────────

class TagCache:
    """A flat-file JSON cache of per-file (defs, refs), keyed by absolute path + mtime_ns.

    Re-homes aider's SQLite TAGS_CACHE to a plain JSON file (⛔ NO SQLITE). A stale entry
    (mtime changed) is re-parsed. ``None`` path → an in-memory-only cache (no persistence).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._data: dict = {}
        self._dirty = False
        if self._path and self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get_tags(self, abspath: Path) -> tuple[set[str], list[str]]:
        key = str(abspath)
        try:
            mtime = abspath.stat().st_mtime_ns
        except OSError:
            return set(), []
        entry = self._data.get(key)
        if entry and entry.get("mtime") == mtime:
            return set(entry["defs"]), list(entry["refs"])
        try:
            source = abspath.read_text(encoding="utf-8")
        except OSError:
            return set(), []
        defs, refs = extract_tags(source)
        self._data[key] = {"mtime": mtime, "defs": sorted(defs), "refs": refs}
        self._dirty = True
        return defs, refs

    def save(self) -> None:
        if self._path and self._dirty:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data), encoding="utf-8")
            self._dirty = False


# ── Top-level entry ──────────────────────────────────────────────────────────────────────────

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _gather_py_files(repo_root: Path, cache: TagCache | None) -> tuple[dict, dict]:
    """Return ``(sources, tags)``: rel-path → source, and rel-path → (defs, refs).

    Uses the cache for tags when given (mtime-keyed), else parses. Skips hidden/venv/site dirs.
    """
    sources: dict = {}
    tags: dict = {}
    skip = {".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist", ".tox"}
    for p in sorted(repo_root.rglob("*.py")):
        if any(part in skip for part in p.parts):
            continue
        rel = str(p.relative_to(repo_root))
        try:
            sources[rel] = p.read_text(encoding="utf-8")
        except OSError:
            continue
        tags[rel] = cache.get_tags(p) if cache else extract_tags(sources[rel])
    return sources, tags


def _ticket_mentions(ticket: dict, relpaths) -> tuple[set, set]:
    """Extract (mentioned_fnames, mentioned_idents) from the ticket text.

    fnames = known rel-paths (or their basenames) that appear in the text; idents = identifier
    tokens in the text (used both to personalize files and to ×10 edges into those symbols).
    """
    text = f"{ticket.get('title', '')} {ticket.get('description', '')}"
    idents = set(_IDENT_RE.findall(text))
    basenames = {Path(r).name: r for r in relpaths}
    stems = {Path(r).stem: r for r in relpaths}
    mentioned_fnames = set()
    for token in set(re.findall(r"[\w./-]+\.py", text)):
        name = Path(token).name
        if token in relpaths:
            mentioned_fnames.add(token)
        elif name in basenames:
            mentioned_fnames.add(basenames[name])
    # Idents that name a known file (by stem) also personalize that file.
    mentioned_idents = idents | (set(stems) & idents)
    return mentioned_fnames, mentioned_idents


def build_graph_map(
    ticket: dict,
    repo_root: str | Path,
    *,
    budget_chars: int = _DEFAULT_BUDGET_CHARS,
    cache: TagCache | None = None,
) -> str:
    """Build the graph-ranked, budgeted orientation packet for ``ticket`` over ``repo_root``.

    Fail-open: any error or an empty repo → '' (the caller falls back to the keyword map).
    """
    # STUB (scaffold commit): graph ranking lands in the next commit; return empty so the caller
    # uses the keyword map until then.
    return ""
