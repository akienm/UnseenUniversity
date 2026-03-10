"""
ebook_reader.py — read local ebooks sentence-by-sentence with Igor.

Supports:
  - epub  → ebooklib (primary format, most of the Calibre library)
  - mobi/prc → mobi package
  - azw/azw3 → DeDRM decrypt → mobi parser; fallback: browse_as_employer
  - lit   → requires `ebook-convert` (Calibre CLI); fallback: browse
  - pdf   → pdfminer.six (layout-dependent, last resort)

Library search: queries Calibre's metadata.db by title/author/ASIN.

Reading state (chapter + sentence offset) persisted per-book in
  ~/.TheIgors/igor_wild_0001/reading_state.json
so Igor can resume across restarts.

Usage (from Igor's tool dispatch):
  find_book(query)                   → list of matching books
  open_book(title_or_path, author?)  → BookHandle (chapters, metadata)
  read_chunk(handle, n=1)            → next N sentences; advances position
  jump_to(handle, chapter, sentence) → seek to position
  reading_position(handle)           → current (chapter, sentence, total)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .registry import Tool, registry

# ── Paths ──────────────────────────────────────────────────────────────────────

_CALIBRE_LIBRARY = Path(
    os.getenv(
        "CALIBRE_LIBRARY_PATH",
        str(Path.home() / ".TheIgors" / "akien" / "onedrive" /
            "AkiensMedia" / "Ebooks" / "Calibre Portable" / "Calibre Library"),
    )
)
_KINDLE_DIR = Path(
    os.getenv(
        "KINDLE_BOOKS_PATH",
        str(Path.home() / ".TheIgors" / "akien" / "onedrive" /
            "AkiensMedia" / "Ebooks" / "Kindle"),
    )
)

_INSTANCE_DIR = Path(
    os.getenv("IGOR_DB_PATH", str(Path.home() / ".TheIgors" / "igor_wild_0001" / "wild-0001.db"))
).parent
_READING_STATE_PATH = _INSTANCE_DIR / "reading_state.json"

_DEDRM_DIR = Path(__file__).parent / "ebook_drm"


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class BookMeta:
    title: str
    author: str
    path: Path
    fmt: str           # epub | mobi | azw | azw3 | lit | pdf
    calibre_id: int | None = None
    asin: str | None = None


@dataclass
class BookHandle:
    meta: BookMeta
    sentences: list[str] = field(default_factory=list)
    chapter_breaks: list[int] = field(default_factory=list)   # sentence indices where chapters start
    chapter_titles: list[str] = field(default_factory=list)
    # current position
    position: int = 0   # absolute sentence index


# ── Calibre library search ─────────────────────────────────────────────────────

def _calibre_db() -> Optional[sqlite3.Connection]:
    db_path = _CALIBRE_LIBRARY / "metadata.db"
    if not db_path.exists():
        return None
    return sqlite3.connect(str(db_path))


def find_book(query: str, author: str = "") -> list[dict]:
    """
    Search Calibre library for books matching title/author query.
    Returns list of dicts with title, author, formats, calibre_id, asin.
    """
    results = []
    con = _calibre_db()
    if con:
        try:
            cur = con.cursor()
            q = f"%{query.lower()}%"
            a = f"%{author.lower()}%" if author else "%"
            cur.execute(
                """
                SELECT b.id, b.title, b.author_sort,
                       GROUP_CONCAT(d.format || '|' || d.name, ';;') as formats
                FROM books b
                LEFT JOIN data d ON d.book = b.id
                WHERE lower(b.title) LIKE ? AND lower(b.author_sort) LIKE ?
                GROUP BY b.id
                ORDER BY b.title
                LIMIT 20
                """,
                (q, a),
            )
            for row in cur.fetchall():
                bid, title, auth, fmt_str = row
                # Get ASIN if present
                cur2 = con.cursor()
                cur2.execute(
                    "SELECT val FROM identifiers WHERE book=? AND type='amazon'",
                    (bid,),
                )
                asin_row = cur2.fetchone()
                asin = asin_row[0] if asin_row else None

                fmts = {}
                if fmt_str:
                    for part in fmt_str.split(";;"):
                        if "|" in part:
                            fmt, name = part.split("|", 1)
                            fmts[fmt.lower()] = name

                results.append({
                    "calibre_id": bid,
                    "title": title,
                    "author": auth,
                    "formats": list(fmts.keys()),
                    "best_format": _best_format(list(fmts.keys())),
                    "asin": asin,
                    "_fmt_names": fmts,
                })
        finally:
            con.close()

    # Also search Kindle folder for azw files not in Calibre
    if _KINDLE_DIR.exists():
        for azw in _KINDLE_DIR.rglob("*.azw"):
            stem = azw.stem.replace("_EBOK", "")
            if query.lower() in stem.lower() or stem.lower() in query.lower():
                results.append({
                    "calibre_id": None,
                    "title": stem,
                    "author": "unknown",
                    "formats": ["azw"],
                    "best_format": "azw",
                    "asin": stem if stem.startswith("B0") else None,
                    "_fmt_names": {"azw": str(azw)},
                })

    return results


def _best_format(fmts: list[str]) -> str:
    """Pick the most readable format in priority order."""
    for f in ("epub", "mobi", "azw3", "azw", "prc", "pdf", "lit"):
        if f in fmts:
            return f
    return fmts[0] if fmts else "epub"


def _calibre_book_path(calibre_id: int, fmt: str, name: str) -> Path:
    """Reconstruct file path from Calibre library layout."""
    # Calibre stores files as: Library/<Author>/<Title (id)>/<name>.<fmt>
    con = _calibre_db()
    if not con:
        return Path()
    try:
        cur = con.cursor()
        cur.execute(
            """
            SELECT b.author_sort, b.title, b.id
            FROM books b WHERE b.id = ?
            """,
            (calibre_id,),
        )
        row = cur.fetchone()
        if not row:
            return Path()
        auth, title, bid = row
        # Calibre uses first author name for directory
        author_dir = auth.split(",")[0].strip() if "," in auth else auth
        # Find the actual directory (Calibre uses <Title (id)> naming)
        pattern = f"*({bid})"
        matches = list(_CALIBRE_LIBRARY.glob(f"**/{pattern}"))
        if matches:
            candidate = matches[0] / f"{name}.{fmt}"
            if candidate.exists():
                return candidate
        # Fallback: glob for the filename
        hits = list(_CALIBRE_LIBRARY.rglob(f"{name}.{fmt}"))
        return hits[0] if hits else Path()
    finally:
        con.close()


# ── DRM decryption ─────────────────────────────────────────────────────────────

def _decrypt_azw(src: Path) -> Optional[Path]:
    """
    Attempt DRM removal on a Kindle AZW/MOBI file.
    Uses DeDRM v6.5.5 scripts (k4mobidedrm + kindlekey).
    Returns path to decrypted file (in a temp dir), or None on failure.

    NOTE: Requires Kindle for PC (≤1.17) to have been installed and run on
    this machine at least once — key is derived from its sqlite database.
    On Linux without KFP, this will return None and caller falls back to
    browse_as_employer.
    """
    if str(_DEDRM_DIR) not in sys.path:
        sys.path.insert(0, str(_DEDRM_DIR))

    try:
        import kindlekey      # type: ignore
        import k4mobidedrm    # type: ignore
    except ImportError as e:
        return None

    # Try to get Kindle for PC keys
    try:
        kindlekeys = kindlekey.getkey(None)   # list of (serial, key) tuples
    except Exception:
        kindlekeys = []

    if not kindlekeys:
        return None

    tmp_dir = Path(tempfile.mkdtemp(prefix="igor_drm_"))
    out_path = tmp_dir / (src.stem + "_decrypted.mobi")

    for _serial, key in kindlekeys:
        try:
            result = k4mobidedrm.decryptBook(str(src), str(out_path), [key])
            if result == 0 and out_path.exists():
                return out_path
        except Exception:
            continue

    return None


# ── Format parsers ─────────────────────────────────────────────────────────────

def _sentences_from_text(text: str) -> list[str]:
    """Split text into sentences using nltk punkt tokenizer."""
    try:
        import nltk
        sentences = nltk.sent_tokenize(text)
        # Filter blanks, dedent
        return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 2]
    except Exception:
        # Fallback: split on . / ! / ? followed by whitespace
        parts = re.split(r'(?<=[.!?])\s+', text)
        return [p.strip() for p in parts if p.strip() and len(p.strip()) > 2]


def _html_to_text(html: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    text = re.sub(r'<[^>]+>', ' ', html)
    # Basic entity decode
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&nbsp;', ' ').replace('&#160;', ' ').replace('&quot;', '"') \
               .replace('&apos;', "'").replace('&#39;', "'")
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _parse_epub(path: Path) -> tuple[list[str], list[int], list[str]]:
    """
    Parse epub into (sentences, chapter_breaks, chapter_titles).
    chapter_breaks[i] = sentence index where chapter i+1 starts.
    Uses epub TOC for chapter titles; falls back to h1/h2 content tags.
    """
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(str(path), options={"ignore_ncx": False})

    # Build TOC label map: href → title
    toc_labels: dict[str, str] = {}

    def _walk_toc(items):
        for item in items:
            if isinstance(item, epub.Link):
                href = item.href.split("#")[0]   # strip fragment
                toc_labels[href] = item.title
            elif isinstance(item, tuple):
                section, children = item
                if hasattr(section, "href"):
                    href = section.href.split("#")[0]
                    toc_labels[href] = section.title
                _walk_toc(children)

    _walk_toc(book.toc)

    sentences: list[str] = []
    breaks: list[int] = []
    titles: list[str] = []

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        content = item.get_content().decode("utf-8", errors="replace")
        text = _html_to_text(content)
        if not text.strip():
            continue

        # Title: TOC label → h1/h2/h3 in content → "Section N"
        item_href = item.get_name()
        chapter_title = toc_labels.get(item_href, "")
        if not chapter_title:
            title_match = re.search(r'<h[123][^>]*>(.*?)</h[123]>', content,
                                    re.IGNORECASE | re.DOTALL)
            chapter_title = (
                _html_to_text(title_match.group(1))
                if title_match
                else f"Section {len(breaks) + 1}"
            )

        breaks.append(len(sentences))
        titles.append(chapter_title)
        sentences.extend(_sentences_from_text(text))

    return sentences, breaks, titles


def _parse_mobi(path: Path) -> tuple[list[str], list[int], list[str]]:
    """Parse mobi/prc/azw into sentences. Mobi is single-document so one chapter."""
    try:
        import mobi as _mobi
        tmp_dir = tempfile.mkdtemp(prefix="igor_mobi_")
        _, ripped = _mobi.extract(str(path))
        # ripped is a list of (filename, content) tuples or a path
        if isinstance(ripped, str):
            html_path = Path(ripped)
            if html_path.is_dir():
                htmls = sorted(html_path.rglob("*.html")) + sorted(html_path.rglob("*.htm"))
            else:
                htmls = [html_path]
        else:
            htmls = []

        sentences: list[str] = []
        breaks: list[int] = []
        titles: list[str] = []
        for h in htmls:
            text = _html_to_text(h.read_text(errors="replace"))
            if text:
                breaks.append(len(sentences))
                titles.append(h.stem)
                sentences.extend(_sentences_from_text(text))
        return sentences, breaks, titles

    except Exception as e:
        return [f"[mobi parse error: {e}]"], [0], ["error"]


def _parse_pdf(path: Path) -> tuple[list[str], list[int], list[str]]:
    """Parse PDF — page-by-page, each page is a 'chapter'."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer

    sentences: list[str] = []
    breaks: list[int] = []
    titles: list[str] = []

    for i, page in enumerate(extract_pages(str(path))):
        page_text = ""
        for element in page:
            if isinstance(element, LTTextContainer):
                page_text += element.get_text()
        page_text = re.sub(r'\s+', ' ', page_text).strip()
        if page_text:
            breaks.append(len(sentences))
            titles.append(f"Page {i + 1}")
            sentences.extend(_sentences_from_text(page_text))

    return sentences, breaks, titles


# ── open_book ──────────────────────────────────────────────────────────────────

def open_book(
    title: str = "",
    author: str = "",
    path: str = "",
    calibre_id: int | None = None,
    resume: bool = True,
) -> BookHandle | str:
    """
    Open a book for reading. Returns BookHandle or error string.

    Priority:
      1. Explicit path
      2. calibre_id + best available format
      3. Title/author search → first match

    If resume=True, restores last reading position from reading_state.json.
    """
    meta: BookMeta | None = None

    if path:
        p = Path(path).expanduser()
        if not p.exists():
            return f"File not found: {path}"
        fmt = p.suffix.lstrip(".").lower()
        meta = BookMeta(title=title or p.stem, author=author, path=p, fmt=fmt)

    elif calibre_id is not None:
        con = _calibre_db()
        if not con:
            return "Calibre library not found"
        try:
            cur = con.cursor()
            cur.execute("SELECT b.title, b.author_sort FROM books b WHERE b.id=?", (calibre_id,))
            row = cur.fetchone()
            if not row:
                return f"No book with calibre_id={calibre_id}"
            _title, _auth = row
            cur.execute("SELECT format, name FROM data WHERE book=?", (calibre_id,))
            fmts = {r[0].lower(): r[1] for r in cur.fetchall()}
        finally:
            con.close()

        best = _best_format(list(fmts.keys()))
        name = fmts[best]
        file_path = _calibre_book_path(calibre_id, best, name)
        if not file_path.exists():
            return f"Book file not found in library: {name}.{best}"
        meta = BookMeta(title=_title, author=_auth, path=file_path, fmt=best,
                        calibre_id=calibre_id)

    else:
        results = find_book(title, author)
        if not results:
            return f"No books found matching '{title}'" + (f" by '{author}'" if author else "")
        best_result = results[0]
        fmt = best_result["best_format"]
        name = best_result["_fmt_names"].get(fmt, "")
        if best_result["calibre_id"] and name:
            file_path = _calibre_book_path(best_result["calibre_id"], fmt, name)
        elif name and Path(name).exists():
            file_path = Path(name)
        else:
            file_path = Path()

        if not file_path.exists():
            return (
                f"Found '{best_result['title']}' by {best_result['author']} "
                f"but could not locate file. Available formats: {best_result['formats']}"
            )
        meta = BookMeta(
            title=best_result["title"],
            author=best_result["author"],
            path=file_path,
            fmt=fmt,
            calibre_id=best_result["calibre_id"],
            asin=best_result.get("asin"),
        )

    # Parse
    sentences, breaks, titles = _load_book_content(meta)
    handle = BookHandle(meta=meta, sentences=sentences,
                        chapter_breaks=breaks, chapter_titles=titles)

    # Restore position if resuming
    if resume:
        state = _load_reading_state()
        key = _state_key(meta)
        if key in state:
            handle.position = state[key].get("position", 0)

    return handle


def _local_copy(path: Path) -> tuple[Path, bool]:
    """
    Return a local copy of the file if it's on a network/CIFS mount.
    Returns (local_path, was_copied). Caller must delete if was_copied.
    Copying avoids 'Stale file handle' errors on SMB/NFS mounts.
    """
    try:
        # Attempt a tiny seek to probe for stale handle without reading
        with path.open("rb") as f:
            f.seek(0, 2)   # seek to end — cheap probe
        return path, False
    except OSError:
        pass
    # Copy to local tmp
    import shutil
    tmp = Path(tempfile.mktemp(suffix=path.suffix, prefix="igor_ebook_"))
    shutil.copy2(str(path), str(tmp))
    return tmp, True


def _load_book_content(
    meta: BookMeta,
) -> tuple[list[str], list[int], list[str]]:
    """Dispatch to the right parser, with DRM fallback."""
    fmt = meta.fmt

    if fmt == "epub":
        local, copied = _local_copy(meta.path)
        try:
            return _parse_epub(local)
        finally:
            if copied:
                local.unlink(missing_ok=True)

    if fmt in ("mobi", "prc"):
        return _parse_mobi(meta.path)

    if fmt in ("azw", "azw3"):
        # Try DeDRM first
        decrypted = _decrypt_azw(meta.path)
        if decrypted and decrypted.exists():
            result = _parse_mobi(decrypted)
            # Clean up temp
            try:
                decrypted.unlink()
                decrypted.parent.rmdir()
            except Exception:
                pass
            return result
        # DRM removal failed — caller should use browse_as_employer
        asin = meta.asin or meta.path.stem.replace("_EBOK", "")
        return (
            [
                f"[DRM-ENCRYPTED] Could not decrypt {meta.path.name}. "
                f"Use browse_as_employer to read this book on read.amazon.com "
                f"(search for ASIN {asin} or title '{meta.title}')."
            ],
            [0],
            ["DRM notice"],
        )

    if fmt == "pdf":
        return _parse_pdf(meta.path)

    if fmt == "lit":
        # Try ebook-convert if available
        ebook_convert = _find_ebook_convert()
        if ebook_convert:
            tmp = Path(tempfile.mktemp(suffix=".epub"))
            import subprocess
            r = subprocess.run(
                [ebook_convert, str(meta.path), str(tmp)],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0 and tmp.exists():
                result = _parse_epub(tmp)
                tmp.unlink(missing_ok=True)
                return result
        return (
            [
                f"[LIT FORMAT] Cannot parse .lit directly. "
                "Install Calibre (`sudo apt install calibre`) for automatic conversion."
            ],
            [0],
            ["LIT notice"],
        )

    return ([f"[UNSUPPORTED FORMAT: {fmt}]"], [0], ["unknown"])


def _find_ebook_convert() -> Optional[str]:
    import shutil
    return shutil.which("ebook-convert")


# ── Reading interface ──────────────────────────────────────────────────────────

def read_chunk(handle: BookHandle, n: int = 1) -> dict:
    """
    Read the next n sentences from the book.
    Returns dict with sentences, position info, chapter info, and at_end flag.
    Advances handle.position. Saves state.
    """
    start = handle.position
    end = min(start + n, len(handle.sentences))
    chunk = handle.sentences[start:end]

    handle.position = end
    _save_reading_state(handle)

    # Determine chapter
    chapter_idx = _chapter_at(handle, end - 1)

    return {
        "sentences": chunk,
        "position": end,
        "total_sentences": len(handle.sentences),
        "chapter": chapter_idx + 1,
        "chapter_title": handle.chapter_titles[chapter_idx] if handle.chapter_titles else "",
        "total_chapters": len(handle.chapter_breaks),
        "at_end": end >= len(handle.sentences),
        "percent": round(end / max(len(handle.sentences), 1) * 100, 1),
    }


def jump_to(handle: BookHandle, chapter: int = 1, sentence: int = 0) -> dict:
    """
    Jump to a specific chapter (1-indexed) and sentence offset within it.
    Returns same dict as read_chunk describing the new position without advancing.
    """
    chapter_idx = max(0, min(chapter - 1, len(handle.chapter_breaks) - 1))
    chapter_start = handle.chapter_breaks[chapter_idx] if handle.chapter_breaks else 0
    handle.position = chapter_start + sentence
    _save_reading_state(handle)
    return read_chunk(handle, n=0)   # peek without advancing


def reading_position(handle: BookHandle) -> dict:
    """Return current position without reading or advancing."""
    chapter_idx = _chapter_at(handle, handle.position)
    return {
        "position": handle.position,
        "total_sentences": len(handle.sentences),
        "chapter": chapter_idx + 1,
        "chapter_title": handle.chapter_titles[chapter_idx] if handle.chapter_titles else "",
        "total_chapters": len(handle.chapter_breaks),
        "percent": round(handle.position / max(len(handle.sentences), 1) * 100, 1),
        "title": handle.meta.title,
        "author": handle.meta.author,
    }


def _chapter_at(handle: BookHandle, pos: int) -> int:
    """Return 0-indexed chapter index for sentence at pos."""
    idx = 0
    for i, start in enumerate(handle.chapter_breaks):
        if start <= pos:
            idx = i
        else:
            break
    return idx


# ── Persistence ────────────────────────────────────────────────────────────────

def _state_key(meta: BookMeta) -> str:
    return f"{meta.title}|{meta.author}"


def _load_reading_state() -> dict:
    if _READING_STATE_PATH.exists():
        try:
            return json.loads(_READING_STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_reading_state(handle: BookHandle):
    state = _load_reading_state()
    key = _state_key(handle.meta)
    state[key] = {
        "position": handle.position,
        "title": handle.meta.title,
        "author": handle.meta.author,
        "path": str(handle.meta.path),
        "fmt": handle.meta.fmt,
        "total_sentences": len(handle.sentences),
        "percent": round(handle.position / max(len(handle.sentences), 1) * 100, 1),
    }
    _READING_STATE_PATH.write_text(json.dumps(state, indent=2))


def list_reading_sessions() -> list[dict]:
    """Return all books with saved positions — Igor's 'reading shelf'."""
    state = _load_reading_state()
    return sorted(state.values(), key=lambda x: x.get("percent", 0), reverse=True)


# ── Tool registration ──────────────────────────────────────────────────────────

registry.register(Tool(
    name="find_book",
    description=(
        "Search the Calibre ebook library and Kindle folder for books by title or author. "
        "Returns matching books with available formats and calibre_id for opening."
    ),
    fn=find_book,
    parameters={
        "query": {"type": "string", "description": "Title keywords or author name to search"},
        "author": {"type": "string", "description": "Optional author filter"},
    },
))

registry.register(Tool(
    name="open_book",
    description=(
        "Open an ebook for reading. Returns a BookHandle for use with read_chunk. "
        "Accepts title search, calibre_id, or explicit file path. "
        "Restores last reading position automatically if the book was read before."
    ),
    fn=open_book,
    parameters={
        "title": {"type": "string", "description": "Book title (partial match OK)"},
        "author": {"type": "string", "description": "Author name filter"},
        "path": {"type": "string", "description": "Direct file path (optional)"},
        "calibre_id": {"type": "integer", "description": "Calibre library ID (from find_book)"},
        "resume": {"type": "boolean", "description": "Resume from last position (default true)"},
    },
))

registry.register(Tool(
    name="read_chunk",
    description=(
        "Read the next N sentences from an open book. Advances the reading position. "
        "Returns sentences, chapter info, and position percentage. "
        "Use n=1 for sentence-by-sentence interactive reading sessions."
    ),
    fn=read_chunk,
    parameters={
        "handle": {"type": "object", "description": "BookHandle from open_book"},
        "n": {"type": "integer", "description": "Number of sentences to read (default 1)"},
    },
))

registry.register(Tool(
    name="jump_to_chapter",
    description="Jump to a specific chapter (and optional sentence offset within it) in an open book.",
    fn=jump_to,
    parameters={
        "handle": {"type": "object", "description": "BookHandle from open_book"},
        "chapter": {"type": "integer", "description": "Chapter number (1-indexed)"},
        "sentence": {"type": "integer", "description": "Sentence offset within chapter (default 0)"},
    },
))

registry.register(Tool(
    name="reading_position",
    description="Return current reading position (chapter, sentence, percent) without advancing.",
    fn=reading_position,
    parameters={
        "handle": {"type": "object", "description": "BookHandle from open_book"},
    },
))

registry.register(Tool(
    name="list_reading_sessions",
    description="Show all books Igor has started reading, with current position and progress.",
    fn=list_reading_sessions,
    parameters={},
))
