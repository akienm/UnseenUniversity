"""
sprint_infrastructure_brief.py — Surface infrastructure brief for /sprint plan-review.

Reads unseenuniversity/infrastructure/by_area/<area> palace nodes and returns a
one-screen summary of the relevant MCP tools, proxies, base classes, IMAP
buses, and channels for the areas touched by a sprint plan.

Called by /sprint after plan-review, before audit-precode, to surface
rules at the point of use (D-scaffold-not-correct-2026-04-21).

Updated 2026-04-29T00:00:00Z
"""

from __future__ import annotations

import os
import re

from devices.igor.igor_base import IgorBase

_DB_URL = os.environ["UU_HOME_DB_URL"]
_SEARCH_PATH = os.environ.get("IGOR_HOME_SEARCH_PATH") or "clan,infra,public"

KNOWN_AREAS = frozenset(
    {"cognition", "memory", "network", "tools", "reasoning", "brainstem"}
)

# File-path prefix → area
_AREA_PREFIXES = [
    ("devices/igor/brainstem/", "brainstem"),
    ("devices/igor/cognition/reasoners/", "reasoning"),
    ("devices/igor/cognition/", "cognition"),
    ("devices/igor/memory/", "memory"),
    ("devices/igor/network/", "network"),
    ("devices/igor/tools/", "tools"),
]


class InfrastructureBrief(IgorBase):
    """Produces a one-screen infrastructure brief for a set of touched areas."""

    def __init__(self):
        super().__init__()
        self._cache: dict[str, str | None] = {}

    def detect_areas(self, files: list[str]) -> set[str]:
        """Map file paths to their area names."""
        areas: set[str] = set()
        for f in files:
            for prefix, area in _AREA_PREFIXES:
                if prefix in f or f.startswith(prefix):
                    areas.add(area)
                    break
        return areas

    def _load_brief(self, area: str) -> str | None:
        if area in self._cache:
            return self._cache[area]
        try:
            import psycopg2

            conn = psycopg2.connect(_DB_URL)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(f"SET search_path TO {_SEARCH_PATH}")
            cur.execute(
                "SELECT content FROM memory_palace WHERE path = %s",
                (f"unseenuniversity/infrastructure/by_area/{area}",),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            result = row[0] if row else None
        except Exception:
            result = None
        self._cache[area] = result
        return result

    def render(self, files: list[str]) -> str:
        """
        Return a one-screen infrastructure brief for the given file list.
        Files can be paths from a sprint plan. Returns a formatted string.
        """
        areas = self.detect_areas(files)
        if not areas:
            return "(No known areas detected in file list — add paths to detect infrastructure.)"

        lines = ["## Infrastructure brief for this sprint\n"]
        for area in sorted(areas):
            content = self._load_brief(area)
            if content is None:
                lines.append(
                    f"### {area}\n_(no palace entry — consider adding unseenuniversity/infrastructure/by_area/{area})_\n"
                )
                continue
            lines.append(f"### {area}")
            # Extract key fields from YAML content
            for field in (
                "base_classes",
                "mcp_tools",
                "proxies",
                "imap_buses",
                "notes",
            ):
                match = re.search(rf"^{field}:(.*?)(?=^\w|\Z)", content, re.M | re.S)
                if match:
                    val = match.group(1).strip()
                    if val and val != "none" and val != "none active in this area":
                        lines.append(f"**{field}:** {val[:300]}")
            lines.append("")
        return "\n".join(lines)


def main(files: list[str]) -> None:
    brief = InfrastructureBrief()
    print(brief.render(files))


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
