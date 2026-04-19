"""
filesystem.py — Re-export shim (T-uc-filesystem-shelf inverted 2026-04-19).

The canonical implementation lives at lab/utility_closet/filesystem.py.
Existing `from ..tools.filesystem import ...` imports keep working via
this shim. New code should import from `lab.utility_closet.filesystem`
directly.
"""

from lab.utility_closet.filesystem import *  # noqa: F401, F403

from lab.utility_closet.filesystem import (  # noqa: F401
    Tool,
    WORKSPACE,
    _PATH_RE,
    _resource_load_dict,
    _safe_path,
    check_disk_usage,
    check_resource_load,
    evaluate_threshold_habits,
    list_directory,
    list_system_dir,
    read_file,
    read_file_from_text,
    read_pdf_pages,
    read_system_file,
    registry,
    write_file,
)
