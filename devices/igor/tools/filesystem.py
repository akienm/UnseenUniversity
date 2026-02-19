"""
Filesystem tools - sandboxed to Igor's workspace.
Cannot read or write outside workspace/.
"""

from pathlib import Path
from .registry import Tool, registry

WORKSPACE = Path(__file__).parent.parent.parent / "workspace"
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


# Register tools
registry.register(Tool(
    name="read_file",
    description="Read a file from Igor's workspace. Paths are relative to the workspace root.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
        },
        "required": ["path"],
    },
    fn=read_file,
))

registry.register(Tool(
    name="write_file",
    description="Write content to a file in Igor's workspace. Creates directories as needed.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path for the file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    },
    fn=write_file,
))

registry.register(Tool(
    name="list_directory",
    description="List contents of a directory in Igor's workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative directory path. Defaults to workspace root."},
        },
        "required": [],
    },
    fn=list_directory,
))
