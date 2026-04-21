"""
reading_integration.py — T-reading-integration #295: Igor tool wrapper.

Registers `integrate_reading` so Igor (or a habit) can trigger the
second-pass integration pipeline for reading/book_learner memories.
Delegates to claudecode/reading_integrator.py as a subprocess.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from .registry import Tool, registry

logger = logging.getLogger("igor.tools.reading_integration")

_INTEGRATOR = (
    Path(__file__).parent.parent.parent.parent
    / "lab"
    / "claudecode"
    / "reading_integrator.py"
)


def integrate_reading(book: str = "", batch: str = "200") -> str:
    """
    Run the reading integration pipeline on unembedded reading memories.

    book  — filter by book_title substring (empty = all)
    batch — max nodes to process per run (default 200)

    Runs as subprocess so Ollama embedding calls don't block Igor's event loop.
    """
    if not _INTEGRATOR.exists():
        return f"reading_integrator.py not found at {_INTEGRATOR}"

    cmd = [sys.executable, str(_INTEGRATOR)]
    if book.strip():
        cmd += ["--book", book.strip()]
    else:
        cmd += ["--all"]
    cmd += ["--batch", str(int(batch))]

    logger.info("integrate_reading: launching %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min cap — Ollama can be slow
        )
        output = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            logger.warning(
                "integrate_reading: exit %d — %s", result.returncode, err[:200]
            )
            return f"Integration failed (exit {result.returncode}):\n{err[:400]}"
        logger.info("integrate_reading: done — %s", output[-200:])
        # Return the summary line (last non-empty line)
        lines = [l for l in output.splitlines() if l.strip()]
        return lines[-1] if lines else "Done (no output)."
    except subprocess.TimeoutExpired:
        return "Integration timed out after 10 minutes."
    except Exception as e:
        return f"integrate_reading error: {e}"


registry.register(
    Tool(
        name="integrate_reading",
        description=(
            "Run the second-pass reading integration pipeline on existing reading/book_learner "
            "memories that are missing embeddings. "
            "Steps: embed → link to similar nodes → build book/chapter spine → "
            "add interpretive CP edges → score arousal. "
            "book='' processes all; book='Descartes' filters by book_title. "
            "batch controls max nodes per run (default 200)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "book": {
                    "type": "string",
                    "description": "Book title substring to filter (empty = process all unembedded reading nodes)",
                },
                "batch": {
                    "type": "string",
                    "description": "Max nodes per run (default '200')",
                },
            },
            "required": [],
        },
        fn=integrate_reading,
    )
)
