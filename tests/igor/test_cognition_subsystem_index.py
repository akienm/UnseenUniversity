"""
T-cognition-subsystem-index: verify SUBSYSTEMS.md covers every .py in cognition/.

Fails if a new file is added without a corresponding entry — keeps the index
from silently drifting out of date.
"""

import pathlib
import re

_COGNITION_DIR = (
    pathlib.Path(__file__).parent.parent.parent / "devices" / "igor" / "cognition"
)
_SUBSYSTEMS_MD = _COGNITION_DIR / "SUBSYSTEMS.md"


def _files_in_cognition() -> set[str]:
    return {p.name for p in _COGNITION_DIR.glob("*.py")}


def _files_in_index() -> set[str]:
    # Only match filenames in Markdown table rows (lines starting with |)
    # to avoid picking up filenames in prose/comments/test references.
    files = set()
    for line in _SUBSYSTEMS_MD.read_text().splitlines():
        if line.startswith("|"):
            files.update(re.findall(r"\b(\w[\w_]*\.py)\b", line))
    return files


def test_subsystems_md_exists():
    assert _SUBSYSTEMS_MD.exists(), f"SUBSYSTEMS.md not found at {_SUBSYSTEMS_MD}"


def test_every_cognition_file_is_indexed():
    cognition_files = _files_in_cognition()
    indexed_files = _files_in_index()
    missing = cognition_files - indexed_files
    assert not missing, (
        f"{len(missing)} cognition file(s) not listed in SUBSYSTEMS.md:\n"
        + "\n".join(f"  {f}" for f in sorted(missing))
        + "\nAdd them to the appropriate subsystem section."
    )


def test_no_phantom_entries():
    """Indexed files should all actually exist (catches stale entries after renames)."""
    cognition_files = _files_in_cognition()
    indexed_files = _files_in_index()
    phantom = indexed_files - cognition_files
    assert not phantom, (
        f"{len(phantom)} file(s) in SUBSYSTEMS.md no longer exist in cognition/:\n"
        + "\n".join(f"  {f}" for f in sorted(phantom))
        + "\nRemove stale entries from SUBSYSTEMS.md."
    )
