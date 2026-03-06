"""
Filesystem tools.

Read/write tools (sandboxed to /home/akien):
  read_file, write_file, list_directory — paths relative to /home/akien
  Cannot read or write outside /home/akien.

System read-only tools (full filesystem, read only):
  read_system_file, list_system_dir — absolute paths required, no writes
"""

from pathlib import Path
from .registry import Tool, registry

WORKSPACE = Path("/home/akien")
WORKSPACE.mkdir(exist_ok=True)


def _safe_path(path: str) -> Path:
    resolved = (WORKSPACE / path).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise PermissionError(f"Path '{path}' escapes Igor's workspace.")
    return resolved


def read_file(path: str) -> str:
    try:
        target = _safe_path(path)
        if not target.exists():
            return f"Error: File not found: {path}"
        if not target.is_file():
            return f"Error: Not a file: {path}"

        # Handle PDFs specially
        if target.suffix.lower() == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(target))
                pages = []
                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text:
                        pages.append(f"--- Page {i+1} ---\n{text}")
                if not pages:
                    return "Error: PDF appears to have no extractable text (may be scanned/image-based)."
                return "\n\n".join(pages)
            except ImportError:
                return "Error: pypdf not installed. Run: pip install pypdf"
            except Exception as e:
                return f"Error reading PDF: {e}"

        return target.read_text(encoding="utf-8")
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(path: str, content: str) -> str:
    try:
        target = _safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written: {path} ({len(content)} chars)"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error writing file: {e}"


def list_directory(path: str = ".") -> str:
    try:
        target = _safe_path(path)
        if not target.exists():
            return f"Error: Not found: {path}"
        if not target.is_dir():
            return f"Error: Not a directory: {path}"
        entries = sorted(target.iterdir())
        if not entries:
            return f"(empty: {path})"
        lines = []
        for e in entries:
            if e.is_dir():
                lines.append(f"[DIR ] {e.name}/")
            else:
                lines.append(f"[FILE] {e.name}  ({e.stat().st_size} bytes)")
        return "\n".join(lines)
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error listing directory: {e}"


def read_system_file(path: str) -> str:
    """Read any file on the system (read-only, no sandbox). Absolute path required."""
    try:
        target = Path(path).resolve()
        if not target.is_absolute():
            return "Error: read_system_file requires an absolute path (e.g. /etc/hostname)."
        if not target.exists():
            return f"Error: File not found: {path}"
        if not target.is_file():
            return f"Error: Not a file: {path}"
        if target.suffix.lower() == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(target))
                pages = [f"--- Page {i+1} ---\n{p.extract_text()}" for i, p in enumerate(reader.pages) if p.extract_text()]
                return "\n\n".join(pages) if pages else "Error: PDF has no extractable text."
            except ImportError:
                return "Error: pypdf not installed."
            except Exception as e:
                return f"Error reading PDF: {e}"
        return target.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error reading file: {e}"


def list_system_dir(path: str) -> str:
    """List a directory anywhere on the system (read-only). Absolute path required."""
    try:
        target = Path(path).resolve()
        if not target.is_absolute():
            return "Error: list_system_dir requires an absolute path (e.g. /home/akien)."
        if not target.exists():
            return f"Error: Not found: {path}"
        if not target.is_dir():
            return f"Error: Not a directory: {path}"
        entries = sorted(target.iterdir())
        if not entries:
            return f"(empty: {path})"
        lines = []
        for e in entries:
            if e.is_dir():
                lines.append(f"[DIR ] {e.name}/")
            else:
                lines.append(f"[FILE] {e.name}  ({e.stat().st_size} bytes)")
        return "\n".join(lines)
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error listing directory: {e}"


# Register tools
registry.register(Tool(
    name="read_file",
    description=(
        "Read a file. Paths are relative to workspace root /home/akien. "
        "Examples: 'TheIgors/thoughts/filename.md', 'TheIgors/design_docs/decisions_log.csb.txt'. "
        "Use list_directory to discover what's available."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path from /home/akien"},
        },
        "required": ["path"],
    },
    fn=read_file,
))

registry.register(Tool(
    name="write_file",
    description=(
        "Write content to a file. Paths are relative to workspace root /home/akien. "
        "Can write to TheIgors/thoughts/ and TheIgors/design_docs/ — use this to update "
        "design documents, capture thoughts, or reorganize the thoughts folder. "
        "Creates directories as needed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path from /home/akien"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    },
    fn=write_file,
))

registry.register(Tool(
    name="list_directory",
    description=(
        "List contents of a directory. Paths are relative to workspace root /home/akien. "
        "Try 'TheIgors/thoughts' or 'TheIgors/design_docs' to see available documents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative directory path. Defaults to workspace root."},
        },
        "required": [],
    },
    fn=list_directory,
))

registry.register(Tool(
    name="read_system_file",
    description=(
        "Read any file on akiendelllinux's filesystem (read-only). "
        "Absolute path required (e.g. /etc/hostname, /proc/cpuinfo, /home/akien/.bashrc). "
        "Use this to learn about the machine, installed software, config files, and OneDrive share paths."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the file"},
        },
        "required": ["path"],
    },
    fn=read_system_file,
))

registry.register(Tool(
    name="list_system_dir",
    description=(
        "List a directory anywhere on akiendelllinux's filesystem (read-only). "
        "Absolute path required (e.g. /home/akien, /mnt, /etc). "
        "Use this to discover mount points, installed packages, OneDrive share location, etc."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the directory"},
        },
        "required": ["path"],
    },
    fn=list_system_dir,
))
