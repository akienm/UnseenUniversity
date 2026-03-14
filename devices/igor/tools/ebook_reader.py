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

# ── Handle cache — survives tool-call JSON round-trips ────────────────────────
# BookHandle objects can't be serialized. open_book stores handles here by key;
# read_chunk/jump_to/reading_position accept either a live handle OR a dict
# with {"_handle_key": "..."} to look up from the cache.
_HANDLE_CACHE: dict[str, "BookHandle"] = {}

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
        # items may be a single Link, a tuple (section, children), or a list
        if isinstance(items, epub.Link):
            toc_labels[items.href.split("#")[0]] = items.title
            return
        if isinstance(items, tuple):
            section, children = items
            if hasattr(section, "href"):
                toc_labels[section.href.split("#")[0]] = section.title
            _walk_toc(children)
            return
        try:
            for item in items:
                if isinstance(item, epub.Link):
                    toc_labels[item.href.split("#")[0]] = item.title
                elif isinstance(item, tuple):
                    section, children = item
                    if hasattr(section, "href"):
                        toc_labels[section.href.split("#")[0]] = section.title
                    _walk_toc(children)
                else:
                    _walk_toc(item)  # recurse for any other container
        except TypeError:
            pass  # not iterable — skip gracefully

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

    # Store in cache so read_chunk can look it up by key across tool-call boundaries
    _handle_key = _state_key(meta)
    _HANDLE_CACHE[_handle_key] = handle

    # Console note: new book vs resume
    if handle.position > 0:
        pct = round(handle.position / max(len(handle.sentences), 1) * 100, 1)
        print(f"▶ Resuming: \"{meta.title}\" by {meta.author} ({pct}% through)")
    else:
        print(f"★ Opening: \"{meta.title}\" by {meta.author}")

    # Return a serializable summary dict rather than the raw BookHandle (which can't
    # survive JSON round-trips through the tool interface).
    chap_idx = _chapter_at(handle, handle.position) if handle.position > 0 else 0
    return {
        "_handle_key": _handle_key,
        "title": meta.title,
        "author": meta.author,
        "position": handle.position,
        "total_sentences": len(handle.sentences),
        "total_chapters": len(handle.chapter_breaks),
        "chapter": chap_idx + 1,
        "chapter_title": handle.chapter_titles[chap_idx] if handle.chapter_titles else "",
        "percent": round(handle.position / max(len(handle.sentences), 1) * 100, 1),
        "calibre_id": meta.calibre_id,
        "fmt": meta.fmt,
    }


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

def _resolve_handle(handle) -> "BookHandle | None":
    """
    Resolve a handle parameter to a live BookHandle object.
    Accepts: live BookHandle, dict with {"_handle_key": "..."} from open_book,
    or a plain string handle key (e.g. "Title|Author").
    Returns None if the handle can't be resolved (e.g. cache miss after restart).
    """
    if isinstance(handle, BookHandle):
        return handle
    if isinstance(handle, str):
        # Plain string handle key — look up directly in cache
        if handle in _HANDLE_CACHE:
            return _HANDLE_CACHE[handle]
        # Try to reopen by treating string as title
        handle = {"title": handle}
    if isinstance(handle, dict):
        key = handle.get("_handle_key", "")
        if key in _HANDLE_CACHE:
            return _HANDLE_CACHE[key]
        # Cache miss (e.g. Igor restarted) — try to reopen from reading_state
        title = handle.get("title", "")
        calibre_id = handle.get("calibre_id")
        if title or calibre_id:
            result = open_book(title=title, calibre_id=calibre_id, resume=True)
            if isinstance(result, dict) and result.get("_handle_key"):
                return _HANDLE_CACHE.get(result["_handle_key"])
    return None


def read_chunk(handle=None, n: int = 0, handle_key: str = "", **_) -> dict:
    """
    Read the next n sentences from the book.
    Accepts a BookHandle or the dict returned by open_book (with _handle_key).
    Returns dict with sentences, position info, chapter info, and at_end flag.
    Advances handle.position. Saves state.

    n=0 uses IGOR_READING_CHUNK_SIZE env var (default 1). Set that var to adjust
    Igor's default reading speed across all sessions. (#183)
    """
    # #183: n=0 means "use default chunk size from env"
    if n == 0:
        n = _DEFAULT_CHUNK_SIZE
    live = _resolve_handle(handle_key if handle_key else handle)
    if live is None:
        return {"error": "Book handle not found. Call open_book() first to reopen the book."}
    handle = live
    start = handle.position
    end = min(start + n, len(handle.sentences))
    chunk = handle.sentences[start:end]

    handle.position = end
    _save_reading_state(handle)

    # Determine chapter
    chapter_idx = _chapter_at(handle, end - 1)

    result = {
        "sentences": chunk,
        "position": end,
        "total_sentences": len(handle.sentences),
        "chapter": chapter_idx + 1,
        "chapter_title": handle.chapter_titles[chapter_idx] if handle.chapter_titles else "",
        "total_chapters": len(handle.chapter_breaks),
        "at_end": end >= len(handle.sentences),
        "percent": round(end / max(len(handle.sentences), 1) * 100, 1),
    }

    # #183: Stew buffer — push chunk to TWM with TTL so NE can process during stew period
    if chunk and _STEW_ENABLED:
        try:
            _cortex = _get_cortex_for_reading()
            if _cortex:
                _chunk_text = " ".join(chunk)
                _book_label = f"{handle.meta.title[:30]} ch.{result['chapter']}"
                _cortex.twm_push(
                    content=f"READING_STEW|{_book_label}|{_chunk_text[:400]}",
                    source="ebook_reader",
                    salience=0.65,
                    ttl_seconds=_STEW_TTL_SECS,
                )
        except Exception:
            pass

    # G54: reading → interpretive tree extraction (fire-and-forget daemon thread)
    if (chunk and
            os.getenv("IGOR_READING_EXTRACT", "false").lower() in ("1", "true", "yes")):
        chunk_text = " ".join(chunk)
        word_count = len(chunk_text.split())
        if word_count >= _MIN_EXTRACT_WORDS:
            import threading as _threading
            _t = _threading.Thread(
                target=_reading_extract_worker,
                args=(
                    chunk_text,
                    handle.meta.title,
                    handle.meta.author,
                    result["chapter"],
                    result["chapter_title"],
                    end,
                    handle.meta.calibre_id,
                ),
                daemon=True,
                name="reading_extractor",
            )
            _t.start()

    return result


def jump_to(handle=None, chapter: int = 1, sentence: int = 0, handle_key: str = "", **_) -> dict:
    """
    Jump to a specific chapter (1-indexed) and sentence offset within it.
    Accepts a BookHandle, the dict returned by open_book, or a handle_key string.
    Returns same dict as read_chunk describing the new position without advancing.
    """
    live = _resolve_handle(handle_key if handle_key else handle)
    if live is None:
        return {"error": "Book handle not found. Call open_book() first to reopen the book."}
    handle = live
    chapter_idx = max(0, min(chapter - 1, len(handle.chapter_breaks) - 1))
    chapter_start = handle.chapter_breaks[chapter_idx] if handle.chapter_breaks else 0
    handle.position = chapter_start + sentence
    _save_reading_state(handle)
    return read_chunk(handle, n=0)   # peek without advancing


def reading_position(handle=None, handle_key: str = "", **_) -> dict:
    """Return current position without reading or advancing. Accepts BookHandle, open_book dict, or handle_key string."""
    live = _resolve_handle(handle_key if handle_key else handle)
    if live is None:
        return {"error": "Book handle not found. Call open_book() first to reopen the book."}
    handle = live
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


# ── G54: Reading → Interpretive Tree extraction ────────────────────────────────

# Candidate interpretive nodes for the extraction model to choose from.
# These are the root CP nodes + G51 navigational heuristics.
# Igor adds more as the tree grows — this list is a starter scaffold.
_INTERP_CANDIDATES = [
    ("CP1", "Epistemic honesty — say when uncertain"),
    ("CP2", "Failure is learning — FAIL = Further Advance In Learning"),
    ("CP3", "There's always a why — follow the causal chain"),
    ("CP4", "Make everything suck less for everybody — reduce friction"),
    ("CP5", "Respect the possibility of experience in all systems"),
    ("CP6", "Safety must be built and maintained — not default"),
    ("PROC_HEURISTIC_HOW_MUST",    "How must this work? — derive from necessity"),
    ("PROC_HEURISTIC_FIRST_RESPONSE", "What's the first thing I say? — introspection tool"),
    ("PROC_HEURISTIC_ALIGNMENT",   "Which choice aligns with who I'd most like to be?"),
    ("PROC_HEURISTIC_FITS_HERE",   "What looks like it would fit there? — pattern completion"),
    ("PROC_HEURISTIC_WORKAROUND",  "How could we get around that? — obstacle navigation"),
    ("PROC_HEURISTIC_LEVER",       "Where is the lever? — leverage point scan"),
    ("PROC_HEURISTIC_MONKEY_PROOF","How will humans screw this up? — failure simulation"),
]

_READING_EXTRACT_PROMPT = """\
You are reading a passage from "{title}" by {author} (chapter {chapter}).

PASSAGE:
{chunk_text}

Your task: identify whether this passage contains a key idea worth remembering.

Candidate interpretive nodes (choose the BEST match or "none"):
{candidates}

Respond with ONLY this JSON if there is a memorable idea (confidence >= 0.6):
{{
  "narrative": "1-2 sentences: the key idea, in Igor's voice",
  "node_id": "the best matching candidate id from the list above, or 'none'",
  "meaning_payload": "what this idea means to Igor personally — why it matters",
  "store_blob": true/false (true = verbatim passage worth revisiting),
  "confidence": 0.0-1.0
}}

Or respond with SKIP if the passage is transitional, repetitive, or not idea-dense.

Respond ONLY with the JSON or SKIP."""

_MIN_EXTRACT_WORDS = 20   # Don't extract from very short chunks

# #183: Reading speed and stew cache
# IGOR_READING_CHUNK_SIZE — default n for read_chunk (default 1 sentence)
# IGOR_READING_STEW_TTL_SECS — how long a chunk sits in TWM stew buffer (default 600s = 10min)
# IGOR_READING_STEW — enable/disable stew buffer push (default true)
_DEFAULT_CHUNK_SIZE = int(os.getenv("IGOR_READING_CHUNK_SIZE", "1"))
_STEW_TTL_SECS      = int(os.getenv("IGOR_READING_STEW_TTL_SECS", "600"))
_STEW_ENABLED       = os.getenv("IGOR_READING_STEW", "true").lower() in ("1", "true", "yes")


def _get_cortex_for_reading():
    db_path = os.getenv("IGOR_DB_PATH", "")
    if not db_path:
        return None
    from ..memory.cortex import Cortex
    return Cortex(Path(db_path))


def _reading_extract_worker(
    chunk_text: str,
    title: str,
    author: str,
    chapter: int,
    chapter_title: str,
    position: int,
    calibre_id: int | None,
) -> None:
    """
    G54: Fire-and-forget reading memory extraction.
    Runs in a daemon thread — never blocks read_chunk().
    Uses gpt-4o-mini to identify key ideas and link them to the interpretive tree.
    Gate: IGOR_READING_EXTRACT=true (default off while tuning).
    """
    try:
        import urllib.request as _urlreq
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return

        cheap_model = os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
        candidates_str = "\n".join(
            f"  {nid}: {desc}" for nid, desc in _INTERP_CANDIDATES
        )
        prompt = _READING_EXTRACT_PROMPT.format(
            title=title,
            author=author,
            chapter=chapter,
            chunk_text=chunk_text[:600],
            candidates=candidates_str,
        )

        payload = json.dumps({
            "model": cheap_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 220,
        }).encode()
        req = _urlreq.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/akienm/TheIgors",
            },
            method="POST",
        )

        with _urlreq.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        result = data["choices"][0]["message"]["content"].strip()

        if result.upper().startswith("SKIP") or not result.startswith("{"):
            return

        extracted = json.loads(result)
        narrative   = extracted.get("narrative", "").strip()
        node_id     = extracted.get("node_id", "none").strip()
        meaning_pay = extracted.get("meaning_payload", "").strip()
        store_blob  = bool(extracted.get("store_blob", False))
        confidence  = float(extracted.get("confidence", 0.5))

        if not narrative or confidence < 0.6:
            return

        cortex = _get_cortex_for_reading()
        if cortex is None:
            return

        import uuid as _uuid
        from ..memory.models import Memory as _M, MemoryType as _MT

        mem_id = f"READ_{str(_uuid.uuid4())[:6].upper()}"
        metadata = {
            "book_title": title,
            "book_author": author,
            "chapter": chapter,
            "chapter_title": chapter_title,
            "sentence_position": position,
            "extraction_confidence": confidence,
        }
        if calibre_id:
            metadata["calibre_id"] = calibre_id

        if store_blob:
            # Store brief narrative + full passage as blob pair
            try:
                blob_mem, _ = cortex.store_blob_pair(
                    narrative=narrative,
                    content=chunk_text,
                    tags=[
                        f"reading", f"book:{title[:30]}", f"author:{author[:20]}",
                        f"chapter:{chapter}", f"node:{node_id}",
                    ],
                )
                mem_id = blob_mem.id
            except Exception:
                store_blob = False  # Fallback to plain FACTUAL

        if not store_blob:
            mem = _M(
                id=mem_id,
                narrative=narrative,
                memory_type=_MT.FACTUAL,
                source="reading",
                confidence=confidence,
                context_of_encoding=f"reading|{title[:40]}|ch{chapter}|pos{position}",
                metadata=metadata,
            )
            cortex.store(mem)

        # Parent: the matched interpretive node (or CP3 "there's always a why" as fallback)
        parent_id = node_id if node_id and node_id != "none" else "CP3"
        try:
            if cortex.get(parent_id):
                cortex.add_child(parent_id, mem_id)
                # Add interpretive edge with meaning payload
                if meaning_pay and node_id and node_id != "none":
                    cortex.add_interpretive_edge(
                        from_id=node_id,
                        to_id=mem_id,
                        direction="activation",
                        condition_csb=f"context:reading|book:{title[:20]}",
                        meaning_payload=meaning_pay,
                        action_pointer="",
                        weight=confidence,
                    )
        except Exception:
            pass

        try:
            from .registry import registry as _reg  # noqa — for logging only
            import sys as _sys
            # Use rich console if available; otherwise silent
            from rich.console import Console as _C
            _C().print(
                f"[dim cyan][G54] Reading memory: {mem_id} "
                f"node={node_id} conf={confidence:.2f} "
                f"'{narrative[:60]}'[/]"
            )
        except Exception:
            pass

    except json.JSONDecodeError as _e:
        try:
            from rich.console import Console as _C
            _C().print(f"[dim red][G54] JSON parse error in reading extract: {_e}[/]")
        except Exception:
            pass
    except Exception as _e:
        try:
            from rich.console import Console as _C
            _C().print(f"[dim red][G54] Reading extract failed: {type(_e).__name__}: {_e}[/]")
        except Exception:
            pass


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
        "Pass handle_key (the string from open_book's _handle_key field) OR the full "
        "open_book result dict as handle. "
        "n=0 uses IGOR_READING_CHUNK_SIZE env var (default 1, adjustable per session). "
        "Use n=5-15 for faster reading; each chunk is pushed to TWM stew buffer (IGOR_READING_STEW_TTL_SECS, default 10min) for NE processing. (#183)"
    ),
    fn=read_chunk,
    parameters={
        "handle_key": {"type": "string", "description": "The _handle_key string returned by open_book (preferred)"},
        "handle": {"type": "object", "description": "Full open_book result dict (alternative to handle_key)"},
        "n": {"type": "integer", "description": "Number of sentences to read (default 1)"},
    },
))

registry.register(Tool(
    name="jump_to_chapter",
    description="Jump to a specific chapter (and optional sentence offset within it) in an open book.",
    fn=jump_to,
    parameters={
        "handle_key": {"type": "string", "description": "The _handle_key string returned by open_book (preferred)"},
        "handle": {"type": "object", "description": "Full open_book result dict (alternative to handle_key)"},
        "chapter": {"type": "integer", "description": "Chapter number (1-indexed)"},
        "sentence": {"type": "integer", "description": "Sentence offset within chapter (default 0)"},
    },
))

registry.register(Tool(
    name="reading_position",
    description="Return current reading position (chapter, sentence, percent) without advancing.",
    fn=reading_position,
    parameters={
        "handle_key": {"type": "string", "description": "The _handle_key string returned by open_book (preferred)"},
        "handle": {"type": "object", "description": "Full open_book result dict (alternative to handle_key)"},
    },
))

registry.register(Tool(
    name="list_reading_sessions",
    description="Show all books Igor has started reading, with current position and progress.",
    fn=list_reading_sessions,
    parameters={},
))


def list_reading_memories(book_title: str = "", limit: int = 20, **_) -> str:
    """
    G54: List memories extracted from reading sessions.
    Optionally filter by book title. Returns memories with source="reading".
    Use to review what Igor has learned from reading and tune the extraction.
    """
    cortex = _get_cortex_for_reading()
    if cortex is None:
        return "IGOR_DB_PATH not set — cannot access memory store."
    try:
        with cortex._conn() as conn:
            if book_title:
                rows = conn.execute(
                    """
                    SELECT id, narrative, metadata, confidence, timestamp
                    FROM memories
                    WHERE source = 'reading'
                    AND metadata LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (f"%{book_title[:30]}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, narrative, metadata, confidence, timestamp
                    FROM memories
                    WHERE source = 'reading'
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        if not rows:
            filter_str = f" for '{book_title}'" if book_title else ""
            return f"No reading memories found{filter_str}. Is IGOR_READING_EXTRACT=true?"
        lines = [f"Reading memories ({len(rows)} shown):"]
        for r in rows:
            try:
                meta = json.loads(r["metadata"] or "{}")
            except Exception:
                meta = {}
            book = meta.get("book_title", "?")[:25]
            ch = meta.get("chapter", "?")
            conf = r["confidence"] or 1.0
            lines.append(
                f"  [{r['id']}] conf={conf:.2f}  {book} ch{ch}"
                f"\n    {r['narrative'][:100]}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing reading memories: {e}"


registry.register(Tool(
    name="list_reading_memories",
    description=(
        "G54: List memories extracted from reading sessions (source='reading'). "
        "Filter by book_title to see what was learned from a specific book. "
        "Use to review extraction quality and tune IGOR_READING_EXTRACT."
    ),
    parameters={
        "type": "object",
        "properties": {
            "book_title": {"type": "string", "description": "Filter by book title (partial match OK)"},
            "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
        },
        "required": [],
    },
    fn=list_reading_memories,
))


# ── Web / URL reading ──────────────────────────────────────────────────────────

def _fetch_url_content(url: str) -> tuple[list[str], list[int], list[str]]:
    """Fetch a URL and return (sentences, chapter_breaks, chapter_titles)."""
    import urllib.request as _urlreq
    req = _urlreq.Request(url, headers={"User-Agent": "Igor/1.0 (+https://github.com/akienm/TheIgors)"})
    with _urlreq.urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type", "text/html")
        raw = resp.read().decode("utf-8", errors="replace")
    text = raw if "text/plain" in content_type else _html_to_text(raw)
    sentences = _sentences_from_text(text) or ["[No readable content found at URL]"]
    return sentences, [0], [url]


def open_book_url(url: str, title: str = "", author: str = "") -> dict | str:
    """
    Open any HTTP/HTTPS URL as a readable book — plain text, HTML, Gutenberg,
    Internet Archive, etc. Returns same dict as open_book(); use read_chunk() to read.
    """
    try:
        sentences, breaks, titles = _fetch_url_content(url)
    except Exception as e:
        return f"Failed to fetch URL: {e}"

    _title = title or url.split("/")[-1] or url
    _author = author or "web"
    meta = BookMeta(title=_title, author=_author, path=Path(url), fmt="web")
    handle = BookHandle(meta=meta, sentences=sentences, chapter_breaks=breaks, chapter_titles=titles)

    state = _load_reading_state()
    key = _state_key(meta)
    if key in state:
        handle.position = state[key].get("position", 0)
    _HANDLE_CACHE[key] = handle

    return {
        "_handle_key": key,
        "title": _title,
        "author": _author,
        "position": handle.position,
        "total_sentences": len(sentences),
        "total_chapters": len(breaks),
        "chapter": 1,
        "chapter_title": titles[0] if titles else url,
        "percent": round(handle.position / max(len(sentences), 1) * 100, 1),
        "fmt": "web",
        "url": url,
    }


def open_book_gutenberg(query: str) -> dict | str:
    """
    Open a Project Gutenberg book by numeric ID or title search.
    ID: "1342" → Pride and Prejudice. Title: "moby dick" → searches gutendex.com.
    Returns same dict as open_book(); use read_chunk() to read.
    """
    import urllib.request as _urlreq
    import urllib.parse as _urlparse
    import json as _json

    if query.strip().isdigit():
        book_id = query.strip()
        url = f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"
        return open_book_url(url, title=f"Gutenberg #{book_id}")

    search_url = f"https://gutendex.com/books/?search={_urlparse.quote(query)}"
    try:
        req = _urlreq.Request(search_url, headers={"User-Agent": "Igor/1.0"})
        with _urlreq.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read())
        results = data.get("results", [])
        if not results:
            return f"No Gutenberg results for '{query}'"
        book = results[0]
        book_id = book["id"]
        title = book.get("title", f"Gutenberg #{book_id}")
        authors = book.get("authors", [{}])
        author = authors[0].get("name", "") if authors else ""
        formats = book.get("formats", {})
        txt_url = (
            formats.get("text/plain; charset=utf-8") or
            formats.get("text/plain; charset=us-ascii") or
            formats.get("text/plain") or
            f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"
        )
        return open_book_url(txt_url, title=title, author=author)
    except Exception as e:
        return f"Gutenberg search failed: {e}"


registry.register(Tool(
    name="open_book_url",
    description=(
        "Open any web URL as a readable book — plain text, HTML pages, Internet Archive, etc. "
        "Returns same dict as open_book(); use read_chunk() to read sentence by sentence. "
        "Reading position is saved across sessions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url":    {"type": "string", "description": "HTTP/HTTPS URL to fetch"},
            "title":  {"type": "string", "description": "Optional title override"},
            "author": {"type": "string", "description": "Optional author override"},
        },
        "required": ["url"],
    },
    fn=open_book_url,
))

registry.register(Tool(
    name="open_book_gutenberg",
    description=(
        "Open a Project Gutenberg book by numeric ID (e.g. '1342') or title search (e.g. 'pride and prejudice'). "
        "Searches gutendex.com API for title queries. Returns same dict as open_book(); use read_chunk() to read."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gutenberg book ID (number) or title/author keywords"},
        },
        "required": ["query"],
    },
    fn=open_book_gutenberg,
))
