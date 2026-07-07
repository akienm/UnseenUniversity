"""
block_apply.py — SEARCH/REPLACE block parsing + a forgiving deterministic apply ladder.

Ported near-verbatim from aider `editblock_coder.py` (T-aider-port-editor-block-contract,
D-aider-port-to-nexus-writepath-2026-07-07). The DS editor died two ways the corpus pinned:
F-B — it never *chose* to call Edit (0 Write/Edit across 149 tool calls); and F-C — it
rejected any non-exact-unique edit with a bare error. aider avoids both: the editor emits ALL
edits in ONE completion as SEARCH/REPLACE blocks (nothing to *choose* — the completion IS the
edits), and this deterministic module applies them through a forgiving ladder.

The apply ladder (per block, best-effort, in order):
  1. perfect_replace — exact line-tuple match.
  2. replace_part_with_missing_leading_whitespace — the model dropped/mangled uniform indent.
  3. try_dotdotdots — the SEARCH/REPLACE used `...` elision to skip an unchanged middle.

DESIGN SCAR (respect it): aider's own `replace_closest_edit_distance` (SequenceMatcher fuzzy
content match) is DELIBERATELY NOT ported. aider disabled it upstream (a dead `return` guards
it in `replace_most_similar_chunk`) after it burned them by silently editing the WRONG lines.
The flexible layers here are apply-side *forgiveness of formatting only* (whitespace, elision)
— never fuzzy identity. A block that does not match a real span fails cleanly (no silent skip).

Pure by design: the parse + content-transform functions do NO I/O and are unit-tested on
in-memory strings. `apply_blocks_to_dir` is the only disk-touching entry point.
⛔ NO SQLITE — aider's repomap diskcache (a P1 concern) is not ported here; nothing persists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FENCE = ("`" * 3, "`" * 3)
triple_backticks = "`" * 3

HEAD = r"^<{5,9} SEARCH>?\s*$"
DIVIDER = r"^={5,9}\s*$"
UPDATED = r"^>{5,9} REPLACE\s*$"

DIVIDER_ERR = "======="
UPDATED_ERR = ">>>>>>> REPLACE"

missing_filename_err = (
    "Bad/missing filename. The filename must be alone on the line before the opening fence"
    " {fence[0]}"
)


# ─────────────────────────────── content transform (pure) ───────────────────────────────

def prep(content):
    if content and not content.endswith("\n"):
        content += "\n"
    lines = content.splitlines(keepends=True)
    return content, lines


def perfect_replace(whole_lines, part_lines, replace_lines):
    part_tup = tuple(part_lines)
    part_len = len(part_lines)

    for i in range(len(whole_lines) - part_len + 1):
        whole_tup = tuple(whole_lines[i : i + part_len])
        if part_tup == whole_tup:
            res = whole_lines[:i] + replace_lines + whole_lines[i + part_len :]
            return "".join(res)


def match_but_for_leading_whitespace(whole_lines, part_lines):
    num = len(whole_lines)

    # does the non-whitespace all agree?
    if not all(whole_lines[i].lstrip() == part_lines[i].lstrip() for i in range(num)):
        return

    # are they all offset the same?
    add = set(
        whole_lines[i][: len(whole_lines[i]) - len(part_lines[i])]
        for i in range(num)
        if whole_lines[i].strip()
    )

    if len(add) != 1:
        return

    return add.pop()


def replace_part_with_missing_leading_whitespace(whole_lines, part_lines, replace_lines):
    # GPT often messes up leading whitespace. It usually does it uniformly across the ORIG and
    # UPD blocks — either omitting all leading whitespace, or including only some of it.

    # Outdent everything in part_lines and replace_lines by the max fixed amount possible.
    leading = [len(p) - len(p.lstrip()) for p in part_lines if p.strip()] + [
        len(p) - len(p.lstrip()) for p in replace_lines if p.strip()
    ]

    if leading and min(leading):
        num_leading = min(leading)
        part_lines = [p[num_leading:] if p.strip() else p for p in part_lines]
        replace_lines = [p[num_leading:] if p.strip() else p for p in replace_lines]

    # can we find an exact match not including the leading whitespace
    num_part_lines = len(part_lines)

    for i in range(len(whole_lines) - num_part_lines + 1):
        add_leading = match_but_for_leading_whitespace(
            whole_lines[i : i + num_part_lines], part_lines
        )

        if add_leading is None:
            continue

        replace_lines = [add_leading + rline if rline.strip() else rline for rline in replace_lines]
        whole_lines = whole_lines[:i] + replace_lines + whole_lines[i + num_part_lines :]
        return "".join(whole_lines)

    return None


def perfect_or_whitespace(whole_lines, part_lines, replace_lines):
    # Try for a perfect match
    res = perfect_replace(whole_lines, part_lines, replace_lines)
    if res:
        return res

    # Try being flexible about leading whitespace
    res = replace_part_with_missing_leading_whitespace(whole_lines, part_lines, replace_lines)
    if res:
        return res


def try_dotdotdots(whole, part, replace):
    """See if the edit block has ``...`` lines; if so, apply the surrounding chunks.

    Returns None when there are no dots. Raises ValueError on an ambiguous/unpaired elision —
    a clean failure the caller surfaces, never a silent skip.
    """
    dots_re = re.compile(r"(^\s*\.\.\.\n)", re.MULTILINE | re.DOTALL)

    part_pieces = re.split(dots_re, part)
    replace_pieces = re.split(dots_re, replace)

    if len(part_pieces) != len(replace_pieces):
        raise ValueError("Unpaired ... in SEARCH/REPLACE block")

    if len(part_pieces) == 1:
        # no dots in this edit block, just return None
        return

    # Compare odd strings in part_pieces and replace_pieces
    all_dots_match = all(part_pieces[i] == replace_pieces[i] for i in range(1, len(part_pieces), 2))

    if not all_dots_match:
        raise ValueError("Unmatched ... in SEARCH/REPLACE block")

    part_pieces = [part_pieces[i] for i in range(0, len(part_pieces), 2)]
    replace_pieces = [replace_pieces[i] for i in range(0, len(replace_pieces), 2)]

    pairs = zip(part_pieces, replace_pieces)
    for part, replace in pairs:
        if not part and not replace:
            continue

        if not part and replace:
            if not whole.endswith("\n"):
                whole += "\n"
            whole += replace
            continue

        if whole.count(part) == 0:
            raise ValueError
        if whole.count(part) > 1:
            raise ValueError

        whole = whole.replace(part, replace, 1)

    return whole


def replace_most_similar_chunk(whole, part, replace):
    """Best-effort: find `part` in `whole` and replace with `replace`. Formatting-forgiving only.

    NB the fuzzy edit-distance layer (aider `replace_closest_edit_distance`) is INTENTIONALLY
    absent — see the module docstring's DESIGN SCAR. This ladder forgives whitespace and `...`
    elision; it never guesses at a similar-but-different span.
    """
    whole, whole_lines = prep(whole)
    part, part_lines = prep(part)
    replace, replace_lines = prep(replace)

    res = perfect_or_whitespace(whole_lines, part_lines, replace_lines)
    if res:
        return res

    # drop leading empty line, GPT sometimes adds them spuriously (issue #25)
    if len(part_lines) > 2 and not part_lines[0].strip():
        skip_blank_line_part_lines = part_lines[1:]
        res = perfect_or_whitespace(whole_lines, skip_blank_line_part_lines, replace_lines)
        if res:
            return res

    # Try to handle when it elides code with ...
    try:
        res = try_dotdotdots(whole, part, replace)
        if res:
            return res
    except ValueError:
        pass

    return


def strip_quoted_wrapping(res, fname=None, fence=DEFAULT_FENCE):
    """Remove extra fence/filename wrapping a model sometimes wraps around a block's body."""
    if not res:
        return res

    res = res.splitlines()

    if fname and res[0].strip().endswith(Path(fname).name):
        res = res[1:]

    if res[0].startswith(fence[0]) and res[-1].startswith(fence[1]):
        res = res[1:-1]

    res = "\n".join(res)
    if res and res[-1] != "\n":
        res += "\n"

    return res


def do_replace(fname, content, before_text, after_text, fence=None):
    """Apply one SEARCH(before)/REPLACE(after) to `content`; return new content or None.

    None means the block did not match — the caller records a clean failure. Empty `before`
    on a missing file = create; empty `before` on an existing file = append.
    """
    before_text = strip_quoted_wrapping(before_text, fname, fence)
    after_text = strip_quoted_wrapping(after_text, fname, fence)
    fname = Path(fname)

    # does it want to make a new file?
    if not fname.exists() and not before_text.strip():
        content = ""

    if content is None:
        return

    if not before_text.strip():
        # append to existing file, or start a new file
        new_content = content + after_text
    else:
        new_content = replace_most_similar_chunk(content, before_text, after_text)

    return new_content


# ─────────────────────────────── parsing (pure) ───────────────────────────────

def strip_filename(filename, fence):
    filename = filename.strip()

    if filename == "...":
        return

    start_fence = fence[0]
    if filename.startswith(start_fence):
        candidate = filename[len(start_fence) :]
        if candidate and ("." in candidate or "/" in candidate):
            return candidate
        return

    if filename.startswith(triple_backticks):
        candidate = filename[len(triple_backticks) :]
        if candidate and ("." in candidate or "/" in candidate):
            return candidate
        return

    filename = filename.rstrip(":")
    filename = filename.lstrip("#")
    filename = filename.strip()
    filename = filename.strip("`")
    filename = filename.strip("*")

    return filename


def find_filename(lines, fence, valid_fnames):
    """Search back over the ≤3 preceding lines for the block's target filename.

    Resolution order stays exact-first → basename → difflib fuzzy(0.8) → any-with-extension.
    This fuzzy step matches an emitted name against a KNOWN `valid_fnames` set (filename
    resolution) — it is NOT content fuzzing; identity of the edited span stays exact-first.
    """
    import difflib

    if valid_fnames is None:
        valid_fnames = []

    # Go back through the 3 preceding lines
    lines = list(reversed(lines))[:3]

    filenames = []
    for line in lines:
        filename = strip_filename(line, fence)
        if filename:
            filenames.append(filename)

        # Only continue as long as we keep seeing fences
        if not line.startswith(fence[0]) and not line.startswith(triple_backticks):
            break

    if not filenames:
        return

    # Check for exact match first
    for fname in filenames:
        if fname in valid_fnames:
            return fname

    # Check for partial match (basename match)
    for fname in filenames:
        for vfn in valid_fnames:
            if fname == Path(vfn).name:
                return vfn

    # Perform fuzzy matching with valid_fnames
    for fname in filenames:
        close_matches = difflib.get_close_matches(fname, valid_fnames, n=1, cutoff=0.8)
        if len(close_matches) == 1:
            return close_matches[0]

    # If no fuzzy match, look for a file w/extension
    for fname in filenames:
        if "." in fname:
            return fname

    if filenames:
        return filenames[0]


def find_original_update_blocks(content, fence=DEFAULT_FENCE, valid_fnames=None):
    """Yield ``(filename, original, updated)`` for each SEARCH/REPLACE block in `content`.

    Raises ValueError on a malformed block (missing DIVIDER/REPLACE, or no resolvable
    filename) — a clean parse failure, surfaced rather than silently dropped.
    """
    lines = content.splitlines(keepends=True)
    i = 0
    current_filename = None

    head_pattern = re.compile(HEAD)
    divider_pattern = re.compile(DIVIDER)
    updated_pattern = re.compile(UPDATED)

    while i < len(lines):
        line = lines[i]

        if head_pattern.match(line.strip()):
            try:
                # if next line after HEAD is DIVIDER, it's a new file (no SEARCH body)
                if i + 1 < len(lines) and divider_pattern.match(lines[i + 1].strip()):
                    filename = find_filename(lines[max(0, i - 3) : i], fence, None)
                else:
                    filename = find_filename(lines[max(0, i - 3) : i], fence, valid_fnames)

                if not filename:
                    if current_filename:
                        filename = current_filename
                    else:
                        raise ValueError(missing_filename_err.format(fence=fence))

                current_filename = filename

                original_text = []
                i += 1
                while i < len(lines) and not divider_pattern.match(lines[i].strip()):
                    original_text.append(lines[i])
                    i += 1

                if i >= len(lines) or not divider_pattern.match(lines[i].strip()):
                    raise ValueError(f"Expected `{DIVIDER_ERR}`")

                updated_text = []
                i += 1
                while i < len(lines) and not (
                    updated_pattern.match(lines[i].strip())
                    or divider_pattern.match(lines[i].strip())
                ):
                    updated_text.append(lines[i])
                    i += 1

                if i >= len(lines) or not (
                    updated_pattern.match(lines[i].strip())
                    or divider_pattern.match(lines[i].strip())
                ):
                    raise ValueError(f"Expected `{UPDATED_ERR}` or `{DIVIDER_ERR}`")

                yield filename, "".join(original_text), "".join(updated_text)

            except ValueError as e:
                processed = "".join(lines[: i + 1])
                err = e.args[0]
                raise ValueError(f"{processed}\n^^^ {err}")

        i += 1


# ─────────────────────────────── disk apply (the one I/O entry point) ───────────────────────────────

@dataclass
class BlockApplyResult:
    """Outcome of applying a completion's SEARCH/REPLACE blocks to a working dir."""
    applied: list = field(default_factory=list)      # relative paths that were written
    failed: list = field(default_factory=list)       # (path, original, updated) blocks that didn't match
    parse_error: str = ""                             # non-empty if the completion didn't parse

    @property
    def any_applied(self) -> bool:
        return bool(self.applied)

    @property
    def clean(self) -> bool:
        """True when every block applied — no failures and no parse error (the DONE condition)."""
        return not self.failed and not self.parse_error


def apply_blocks_to_dir(response_text: str, cwd: Path, fence=DEFAULT_FENCE,
                        committer=None) -> BlockApplyResult:
    """Parse SEARCH/REPLACE blocks from `response_text` and apply them to files under `cwd`.

    Reads the current file content from disk, runs each block through the forgiving ladder, and
    writes matches back. A block that does not match a real span is recorded in ``failed`` — it
    is never silently skipped (F-C). Malformed block syntax lands in ``parse_error``.

    ``committer`` (optional, duck-typed ``before(rel)``/``after(rel)`` — a CloneCommitter) gives
    commit-per-edit granularity in the throwaway clone: dirty-snapshot each file before it is
    touched, commit each applied edit after. None → no commits (the pure default; unit tests).
    """
    cwd = Path(cwd)
    result = BlockApplyResult()

    valid_fnames = [str(p.relative_to(cwd)) for p in cwd.rglob("*") if p.is_file()]

    try:
        edits = list(find_original_update_blocks(response_text, fence, valid_fnames))
    except ValueError as exc:
        result.parse_error = str(exc)
        return result

    for path, original, updated in edits:
        if path is None:  # shell command block (aider yields (None, cmd)) — out of scope here
            continue
        full_path = (cwd / path)
        content = full_path.read_text(encoding="utf-8") if full_path.exists() else None
        new_content = do_replace(str(full_path), content, original, updated, fence)

        if new_content is None:
            result.failed.append((path, original, updated))
            continue

        # Commit-per-edit (clone only): snapshot a dirty file BEFORE touching it, commit AFTER.
        if committer is not None:
            committer.before(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(new_content, encoding="utf-8")
        result.applied.append(path)
        if committer is not None:
            committer.after(path)

    return result


# ── Rich, file-grounded repair errors (port of aider apply_edits error construction) ──────────

def find_similar_lines(search: str, content: str, threshold: float = 0.6) -> str:
    """Find the run of lines in `content` most similar to `search` (the 'did you mean' hint).

    Port of aider editblock_coder.find_similar_lines: slide a window the size of `search` over
    `content`, keep the best SequenceMatcher ratio; return that window (± a few lines of context)
    when it clears `threshold`, else ''. This grounds a repair in the ACTUAL file, not a generic
    'no match' string — the whole point of the reflection loop (F-C/F-E).
    """
    from difflib import SequenceMatcher

    search_lines = search.splitlines()
    content_lines = content.splitlines()
    if not search_lines or not content_lines:
        return ""

    best_ratio = 0.0
    best_match = None
    best_i = 0
    for i in range(len(content_lines) - len(search_lines) + 1):
        chunk = content_lines[i : i + len(search_lines)]
        ratio = SequenceMatcher(None, search_lines, chunk).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = chunk
            best_i = i

    if best_ratio < threshold or best_match is None:
        return ""

    if best_match[0] == search_lines[0] and best_match[-1] == search_lines[-1]:
        return "\n".join(best_match)

    n = 5
    start = max(0, best_i - n)
    end = min(len(content_lines), best_i + len(search_lines) + n)
    return "\n".join(content_lines[start:end])


def failure_class(result: BlockApplyResult, cwd: Path) -> str:
    """A coarse class for the FIRST failure — the label the corpus keys repair pairs by.

    'parse_error' | 'replace_already_present' | 'no_exact_match' | 'clean'. Recurring
    (class → successful-repair) shapes are future nexus rows (per the ticket).
    """
    if result.parse_error:
        return "parse_error"
    if not result.failed:
        return "clean"
    path, _original, updated = result.failed[0]
    full = Path(cwd) / path
    if updated.strip() and full.exists() and updated in full.read_text(encoding="utf-8"):
        return "replace_already_present"
    return "no_exact_match"


def build_repair_message(result: BlockApplyResult, cwd: Path, fence=DEFAULT_FENCE) -> str:
    """Build a rich, file-grounded repair message from a failed apply, or '' if nothing to repair.

    Port of aider editblock_coder.apply_edits error construction: per-failed-block SEARCH/REPLACE
    echo, 'did you mean' similar lines pulled from the ACTUAL file, a 'REPLACE already present'
    note, and a partial-success ledger so the model does NOT re-send the blocks that applied.
    """
    cwd = Path(cwd)
    if result.clean:
        return ""
    if result.parse_error:
        return (
            "The SEARCH/REPLACE blocks were malformed and could not be parsed:\n"
            f"{result.parse_error}\n\n"
            "Resend ALL edits as well-formed SEARCH/REPLACE blocks (filename on its own line, "
            "then <<<<<<< SEARCH / ======= / >>>>>>> REPLACE)."
        )

    n = len(result.failed)
    blocks = "block" if n == 1 else "blocks"
    parts = [f"# {n} SEARCH/REPLACE {blocks} failed to match!"]
    for path, original, updated in result.failed:
        full = cwd / path
        content = full.read_text(encoding="utf-8") if full.exists() else ""
        parts.append(
            f"\n## SearchReplaceNoExactMatch: this SEARCH block failed to exactly match lines in {path}\n"
            f"<<<<<<< SEARCH\n{original}=======\n{updated}>>>>>>> REPLACE"
        )
        did_you_mean = find_similar_lines(original, content)
        if did_you_mean:
            parts.append(
                f"\nDid you mean to match some of these actual lines from {path}?\n\n"
                f"{fence[0]}\n{did_you_mean}\n{fence[1]}"
            )
        if updated and updated in content:
            parts.append(
                f"\nAre you sure you need this SEARCH/REPLACE block?\n"
                f"The REPLACE lines are already present in {path}!"
            )
    parts.append(
        "\nThe SEARCH section must EXACTLY match existing lines including all whitespace, "
        "comments, indentation, and docstrings."
    )
    if result.applied:
        m = len(result.applied)
        pblocks = "block" if m == 1 else "blocks"
        parts.append(
            f"\n# The other {m} SEARCH/REPLACE {pblocks} applied successfully — do NOT re-send them. "
            f"Reply with fixed versions of ONLY the {blocks} above that failed to match."
        )
    return "\n".join(parts)
