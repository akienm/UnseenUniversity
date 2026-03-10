"""
Filesystem tools.

Read/write tools (sandboxed to /home/akien):
  read_file, write_file, list_directory — paths relative to /home/akien
  Cannot read or write outside /home/akien.

System read-only tools (full filesystem, read only):
  read_system_file, list_system_dir — absolute paths required, no writes

Self-awareness tools:
  check_disk_usage — disk free space for Igor's key paths
"""

import os
import shutil
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


def read_pdf_pages(path: str, start_page: int = 1, end_page: int = 0) -> str:
    """
    Read specific pages from a PDF file.
    start_page is 1-based. end_page=0 means just start_page.
    Returns extracted text and a page-count header for cursor tracking.
    """
    try:
        target = _safe_path(path)
        if not target.exists():
            return f"Error: File not found: {path}"
        if target.suffix.lower() != ".pdf":
            return f"Error: Not a PDF file: {path}"
        try:
            import pypdf
        except ImportError:
            return "Error: pypdf not installed. Run: pip install pypdf"
        reader = pypdf.PdfReader(str(target))
        total = len(reader.pages)
        if end_page <= 0:
            end_page = start_page
        start_page = max(1, start_page)
        end_page   = min(total, end_page)
        if start_page > total:
            return f"Error: start_page {start_page} exceeds total pages ({total})."
        pages = []
        for i in range(start_page - 1, end_page):
            text = reader.pages[i].extract_text()
            if text and text.strip():
                pages.append(f"--- Page {i+1}/{total} ---\n{text.strip()}")
        if not pages:
            return (f"[PDF: {path} | total_pages={total} | "
                    f"pages {start_page}-{end_page} have no extractable text]")
        header = f"[PDF: {path} | total_pages={total} | showing pages {start_page}-{end_page}]\n\n"
        return header + "\n\n".join(pages)
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error reading PDF pages: {e}"


def check_disk_usage() -> str:
    """
    Check disk free space for Igor's key paths.
    Returns a summary with warnings if below thresholds.
    """
    warn_gb   = float(os.getenv("IGOR_DISK_WARN_GB", "1.0"))
    crit_gb   = float(os.getenv("IGOR_DISK_CRITICAL_GB", "0.2"))
    igor_home = Path.home() / ".TheIgors"
    src_home  = Path.home() / "TheIgors"

    paths = [
        ("runtime (~/.TheIgors)", igor_home),
        ("source (~/TheIgors)", src_home),
        ("disk (/)", Path("/")),
    ]

    lines = ["Disk usage report:"]
    alerts = []
    for label, p in paths:
        try:
            usage = shutil.disk_usage(str(p))
            free_gb  = usage.free  / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            used_pct = (usage.used / usage.total * 100) if usage.total else 0
            status = ""
            if free_gb < crit_gb:
                status = " ⚠ CRITICAL"
                alerts.append(f"CRITICAL: {label} has only {free_gb:.2f} GB free")
            elif free_gb < warn_gb:
                status = " ⚠ WARN"
                alerts.append(f"WARN: {label} has only {free_gb:.2f} GB free")
            lines.append(f"  {label}: {free_gb:.2f} GB free / {total_gb:.1f} GB total ({used_pct:.0f}% used){status}")
        except Exception as e:
            lines.append(f"  {label}: error — {e}")

    if alerts:
        lines.append("")
        lines.append("⚠ Alerts:")
        for a in alerts:
            lines.append(f"  {a}")
    else:
        lines.append("  All paths within normal thresholds.")

    return "\n".join(lines)


def check_resource_load() -> str:
    """
    Report current CPU, RAM, and swap load on this machine.
    Used before starting bulk/batch operations to avoid OOM crashes.

    Returns:
      - CPU: 1/5/15-min load averages + logical core count
      - RAM: used/total/available + percent
      - Swap: used/total/free + percent
      - This process: RSS memory (Igor's own footprint)
      - Verdict: ok / warn / critical with a plain-language note
    """
    import psutil

    # CPU load averages (POSIX: /proc/loadavg)
    load1, load5, load15 = os.getloadavg()
    ncpus = os.cpu_count() or 1
    load_pct = load1 / ncpus * 100  # normalised to core count

    # RAM
    vm = psutil.virtual_memory()
    ram_total_gb  = vm.total    / (1024 ** 3)
    ram_used_gb   = vm.used     / (1024 ** 3)
    ram_avail_gb  = vm.available / (1024 ** 3)
    ram_pct       = vm.percent

    # Swap
    sw = psutil.swap_memory()
    swap_total_gb = sw.total / (1024 ** 3)
    swap_used_gb  = sw.used  / (1024 ** 3)
    swap_free_gb  = sw.free  / (1024 ** 3)
    swap_pct      = sw.percent

    # This process's own footprint
    try:
        proc = psutil.Process(os.getpid())
        self_rss_mb = proc.memory_info().rss / (1024 ** 2)
    except Exception:
        self_rss_mb = 0.0

    # Thresholds (overridable via env)
    _cpu_warn  = float(os.getenv("IGOR_LOAD_CPU_WARN",  "80"))   # % of all cores
    _cpu_crit  = float(os.getenv("IGOR_LOAD_CPU_CRIT",  "95"))
    _ram_warn  = float(os.getenv("IGOR_LOAD_RAM_WARN",  "80"))   # % RAM used
    _ram_crit  = float(os.getenv("IGOR_LOAD_RAM_CRIT",  "92"))
    _swap_warn = float(os.getenv("IGOR_LOAD_SWAP_WARN", "40"))   # % swap used
    _swap_crit = float(os.getenv("IGOR_LOAD_SWAP_CRIT", "75"))

    alerts = []
    verdict = "ok"

    if load_pct >= _cpu_crit:
        alerts.append(f"CPU critical: {load_pct:.0f}% load ({load1:.1f}/{ncpus} cores)")
        verdict = "critical"
    elif load_pct >= _cpu_warn:
        alerts.append(f"CPU high: {load_pct:.0f}% load ({load1:.1f}/{ncpus} cores)")
        verdict = max(verdict, "warn")

    if ram_pct >= _ram_crit:
        alerts.append(f"RAM critical: {ram_pct:.0f}% used ({ram_avail_gb:.1f} GB free)")
        verdict = "critical"
    elif ram_pct >= _ram_warn:
        alerts.append(f"RAM high: {ram_pct:.0f}% used ({ram_avail_gb:.1f} GB free)")
        verdict = max(verdict, "warn")

    if swap_pct >= _swap_crit:
        alerts.append(f"Swap critical: {swap_pct:.0f}% used ({swap_free_gb:.1f} GB free) — "
                      "bulk operations risk thrashing")
        verdict = "critical"
    elif swap_pct >= _swap_warn:
        alerts.append(f"Swap elevated: {swap_pct:.0f}% used ({swap_free_gb:.1f} GB free)")
        verdict = max(verdict, "warn")

    _verdict_note = {
        "ok":       "System is healthy — bulk operations are fine.",
        "warn":     "System under moderate load — consider deferring large batch work.",
        "critical": "System under heavy load — defer bulk/training operations now.",
    }[verdict]

    lines = [
        f"Resource load [{verdict.upper()}] — {_verdict_note}",
        f"  CPU:  {load1:.2f} / {load5:.2f} / {load15:.2f} load avg (1/5/15m) "
        f"| {ncpus} logical cores | {load_pct:.0f}% normalised",
        f"  RAM:  {ram_used_gb:.1f} / {ram_total_gb:.1f} GB used ({ram_pct:.0f}%) "
        f"| {ram_avail_gb:.1f} GB available",
        f"  Swap: {swap_used_gb:.1f} / {swap_total_gb:.1f} GB used ({swap_pct:.0f}%) "
        f"| {swap_free_gb:.1f} GB free",
        f"  Igor: {self_rss_mb:.0f} MB RSS (this process)",
    ]
    if alerts:
        lines.append("  Alerts:")
        for a in alerts:
            lines.append(f"    ⚠ {a}")

    return "\n".join(lines)


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

registry.register(Tool(
    name="read_pdf_pages",
    description=(
        "Read specific pages from a PDF file (1-based page numbers). "
        "Use this to read a book or document one page at a time — read page 1, discuss, "
        "then read page 2, etc. Returns page text plus total_pages so you can track your cursor. "
        "Paths are relative to workspace root /home/akien. "
        "Example: read pages 1-2 of TheIgorsProject/akien/Readings/SomeBook.pdf"
    ),
    parameters={
        "type": "object",
        "properties": {
            "path":       {"type": "string",  "description": "Relative path from /home/akien"},
            "start_page": {"type": "integer", "description": "First page to read (1-based). Default: 1"},
            "end_page":   {"type": "integer", "description": "Last page to read (inclusive). 0 = same as start_page. Default: 0"},
        },
        "required": ["path"],
    },
    fn=read_pdf_pages,
))

registry.register(Tool(
    name="check_disk_usage",
    description=(
        "Check free disk space for Igor's key paths (~/.TheIgors, ~/TheIgors, /). "
        "Returns usage summary with warnings if below IGOR_DISK_WARN_GB (default 1GB) "
        "or IGOR_DISK_CRITICAL_GB (default 0.2GB) thresholds. "
        "Call this after large ingestion tasks or whenever storage feels tight."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=check_disk_usage,
))

registry.register(Tool(
    name="check_resource_load",
    description=(
        "Report current CPU, RAM, and swap load on this machine. "
        "Call this before starting bulk operations (training fetches, batch jobs, "
        "large background tasks) to check if the system can handle the load. "
        "Returns a verdict: ok / warn / critical with plain-language guidance."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=check_resource_load,
))
