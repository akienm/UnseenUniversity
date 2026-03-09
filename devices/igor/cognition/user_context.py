"""
User context — per-user profile, formality tracking, and chat logging.

Storage layout (under DATA_DIR/chats/):
  <slug>/
    context.json      — UserContext fields (persisted)
    YYYY-MM-DD.jsonl  — daily chat log (one JSON line per message)

Slugs are lowercase, spaces → underscores. Unknown users start as
"thread_<suffix>" until they give their name, at which point the
directory is renamed atomically.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


def _slugify(name: str) -> str:
    """Convert a display name to a safe directory slug."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "unknown"


def _looks_like_name(s: str) -> bool:
    """Return True if s looks like a real name (1–3 words, mostly alpha)."""
    s = s.strip()
    if not s or len(s) > 60:
        return False
    # Allow letters, spaces, hyphens, apostrophes — typical name characters
    return bool(re.match(r"^[A-Za-z][A-Za-z\s\-']{0,58}$", s))


@dataclass
class UserContext:
    name: str
    slug: str
    relationship: str = "guest"
    formality: float = 0.9        # 0.0 = very casual, 1.0 = very formal
    session_count: int = 0
    message_count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    memory_summary: str = ""
    pending_name: bool = False     # True = waiting for name response

    def context_block(self) -> str:
        """Compact user-context string for injection into synthetic input."""
        if self.pending_name or self.slug.startswith("thread_"):
            return ""
        parts = [f"TALKING WITH: {self.name} | relationship: {self.relationship}"]
        if self.memory_summary:
            parts.append(f"What I remember: {self.memory_summary}")
        return " | ".join(parts)

    def update_formality(self) -> None:
        """Decay formality with experience. Floor at 0.2 (never fully casual)."""
        self.formality = round(
            max(0.2, 0.9 - self.session_count * 0.08 - self.message_count * 0.001),
            3,
        )


class UserContextManager:
    """
    Manages per-user context and chat logs.

    Cache: _cache maps thread_id → UserContext (in-memory for speed).
    Disk:  DATA_DIR/chats/<slug>/context.json + YYYY-MM-DD.jsonl
    """

    def __init__(self, data_dir: Path):
        self._root = data_dir / "chats"
        self._root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, UserContext] = {}  # thread_id → UserContext

    # ── Get / create ──────────────────────────────────────────────────────────

    def get(self, thread_id: str, author: str = "") -> UserContext:
        """Return cached context, loading from disk or creating fresh if needed."""
        if thread_id in self._cache:
            return self._cache[thread_id]

        # Derive initial slug from author name if provided, else use thread suffix
        slug = _slugify(author) if author else f"thread_{thread_id[-8:]}"
        ctx_path = self._root / slug / "context.json"

        if ctx_path.exists():
            ctx = self._load(slug)
        else:
            ctx = UserContext(
                name=author or slug,
                slug=slug,
                pending_name=False,   # caller decides whether to trigger first-contact
                first_seen=datetime.now().isoformat(),
            )
        self._cache[thread_id] = ctx
        return ctx

    def _load(self, slug: str) -> UserContext:
        try:
            data = json.loads((self._root / slug / "context.json").read_text())
            return UserContext(**{k: v for k, v in data.items() if k in UserContext.__dataclass_fields__})
        except Exception:
            return UserContext(name=slug, slug=slug)

    # ── Save ──────────────────────────────────────────────────────────────────

    def save(self, ctx: UserContext) -> None:
        d = self._root / ctx.slug
        d.mkdir(exist_ok=True)
        (d / "context.json").write_text(json.dumps(asdict(ctx), indent=2))

    # ── Rename ────────────────────────────────────────────────────────────────

    def rename(self, thread_id: str, new_name: str) -> UserContext:
        """
        Update name + slug + directory atomically when a user's name is learned.
        Handles: close (implicit via pathlib), rename dir, reopen.
        """
        ctx = self._cache.get(thread_id)
        if ctx is None:
            return UserContext(name=new_name, slug=_slugify(new_name))

        old_slug = ctx.slug   # capture before mutation
        old_dir = self._root / old_slug
        new_slug = _slugify(new_name)
        new_dir = self._root / new_slug

        if old_dir.exists() and old_slug != new_slug:
            # If target already exists (name collision), merge by keeping target
            if new_dir.exists():
                # Just update the in-memory context, don't clobber existing dir
                pass
            else:
                old_dir.rename(new_dir)

        ctx.name = new_name.strip().title() if _looks_like_name(new_name) else new_name
        ctx.slug = new_slug
        ctx.pending_name = False
        self.save(ctx)
        return ctx

    # ── Chat logging ──────────────────────────────────────────────────────────

    def log(self, ctx: UserContext, direction: str, content: str,
            thread_id: str = "") -> None:
        """Append one message to the user's daily JSONL chat log."""
        log_dir = self._root / ctx.slug
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        entry: dict = {
            "ts":  datetime.now().isoformat(),
            "dir": direction,   # "in" | "out"
            "content": content,
        }
        if thread_id:
            entry["thread_id"] = thread_id
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── Pre-seed known users ──────────────────────────────────────────────────

    def preseed(self, thread_id: str, name: str, relationship: str = "operator") -> UserContext:
        """
        Pre-seed a known user (e.g. Akien on stdin) so first-contact never fires.
        Loads existing context or creates a known-user context with pending_name=False.
        """
        slug = _slugify(name)
        ctx_path = self._root / slug / "context.json"
        if ctx_path.exists():
            ctx = self._load(slug)
        else:
            ctx = UserContext(
                name=name,
                slug=slug,
                relationship=relationship,
                formality=0.5,    # known user — already comfortable
                pending_name=False,
                first_seen=datetime.now().isoformat(),
            )
            self.save(ctx)
        self._cache[thread_id] = ctx
        return ctx
