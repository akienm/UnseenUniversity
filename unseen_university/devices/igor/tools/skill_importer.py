"""
skill_importer.py — T-skill-to-engram-generalise

Generic skill→engram parser. Reads a skill markdown file from
~/.claude/skills/<name>/SKILL.md and seeds (or updates) a PROCEDURAL Memory
node in the DB so Igor can invoke it via cursor traversal.

What gets stored per skill:
  - SKILL_{NAME}_ENTRY Memory node
    - narrative: skill description from frontmatter
    - metadata: triggers, step titles, hard rules, source path, model hint
    - payload.run_cell: executable instructions
        • bash code blocks → EMITIF (set args) + MCPCALL run_bash
        • /skill-name references → FORKIF SKILL_{NAME}_ENTRY
    - payload.step_N_text: prose step text (Igor reads; not executed)

Design: the parser extracts structure, not semantics. Igor fills prose gaps
with inference. Bash blocks and skill-refs are the executable atoms.

Update path: re-run import_skill(name) to upsert — node reflects latest skill.
Igor can self-update via: DEFERRED_TASK|tool_call|import_skill|{"skill_name":"sprint"}
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from unseen_university.devices.igor.tools.registry import Tool, registry

logger = logging.getLogger(__name__)

_DEFAULT_SKILLS_DIR = Path("~/.claude/skills").expanduser()


# ── markdown parsing ──────────────────────────────────────────────────────────


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML-ish frontmatter (name/description/model) from skill markdown."""
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("---", 3)
    if end == -1:
        return meta, text
    fm_block = text[3:end].strip()
    body = text[end + 3 :].strip()
    for line in fm_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body


def _split_steps(body: str) -> list[dict]:
    """
    Split markdown body into step sections.
    Returns list of {"title": str, "text": str, "index": int}.
    """
    # Match "## Step N" or "## Step N.5" headers
    step_re = re.compile(r"^##\s+Step\s+[\d.]+\s*[—-]?\s*(.*)", re.MULTILINE)
    parts = []
    last_end = 0
    last_title = None
    last_idx = 0

    for m in step_re.finditer(body):
        if last_title is not None:
            parts.append(
                {
                    "title": last_title,
                    "text": body[last_end : m.start()].strip(),
                    "index": last_idx,
                }
            )
            last_idx += 1
        last_title = m.group(1).strip()
        last_end = m.end()

    if last_title is not None:
        parts.append(
            {"title": last_title, "text": body[last_end:].strip(), "index": last_idx}
        )

    return parts


def _extract_bash_blocks(text: str) -> list[str]:
    """Extract all ```bash ... ``` code block contents from text."""
    return re.findall(r"```bash\s*\n(.*?)```", text, re.DOTALL)


def _extract_skill_refs(text: str) -> list[str]:
    """Extract /skill-name references (e.g. /commit, /filter) from text."""
    refs = re.findall(r"/([a-z][a-z0-9_-]+)", text)
    # Filter to known skill-like names (avoid false positives from paths)
    skip = {
        "etc",
        "dev",
        "home",
        "var",
        "usr",
        "tmp",
        "bin",
        "opt",
        "proc",
        "sys",
        "run",
        "lib",
    }
    return [r for r in refs if r not in skip and len(r) >= 4]


def _extract_hard_rules(body: str) -> list[str]:
    """Extract lines from '## Hard rules' section."""
    m = re.search(r"##\s+Hard\s+rules?\s*\n(.*?)(?=\n##|\Z)", body, re.DOTALL | re.I)
    if not m:
        return []
    block = m.group(1)
    rules = []
    for line in block.splitlines():
        line = line.strip().lstrip("-* ").strip()
        if line:
            rules.append(line)
    return rules


def _build_payload(
    steps: list[dict], hard_rules: list[str], skill_refs: list[str]
) -> dict[str, Any]:
    """
    Build the Memory payload dict from parsed skill components.

    run_cell: executable instruction sequence
      - For each bash block across all steps: EMITIF (set args) + MCPCALL run_bash
      - For each /skill-ref: FORKIF to SKILL_{NAME}_ENTRY

    step_N_text: prose text for each step (data field — Igor reads)
    hard_rules_text: concatenated hard rules (data field)
    """
    payload: dict[str, Any] = {}
    run_cell: list[Any] = []
    bash_idx = 0

    for step in steps:
        step_key = f"step_{step['index']}_text"
        payload[step_key] = f"# {step['title']}\n{step['text']}"

        for bash_cmd in _extract_bash_blocks(step["text"]):
            cmd = bash_cmd.strip()
            if not cmd:
                continue
            args_key = f"bash_args_{bash_idx}"
            result_key = f"bash_result_{bash_idx}"
            # Store command as data field; set into basket at runtime
            payload[args_key] = {"command": cmd}
            run_cell.append(["EMITIF", True, args_key, ["payload", args_key], "basket"])
            run_cell.append(["MCPCALL", "run_bash", args_key, result_key])
            bash_idx += 1

    # Skill refs → FORKIF (spawns sub-cursor at the referenced skill's entry node)
    seen_refs: set[str] = set()
    for ref in skill_refs:
        target = f"SKILL_{ref.upper().replace('-', '_')}_ENTRY"
        if target not in seen_refs:
            run_cell.append(["FORKIF", True, target])
            seen_refs.add(target)

    if hard_rules:
        payload["hard_rules_text"] = "\n".join(f"- {r}" for r in hard_rules)

    run_cell.append("ENDIF")
    payload["run_cell"] = run_cell
    return payload


# ── main import function ──────────────────────────────────────────────────────


def import_skill(
    skill_name: str = "",
    skills_dir: str = "",
    **_,
) -> str:
    """
    Parse a skill markdown file and seed/update its engram node in the DB.

    Args:
        skill_name: name of the skill (e.g. 'sprint', 'commit', 'filter')
        skills_dir: override for skills directory (default: ~/.claude/skills)

    Returns a summary string of what was seeded.
    """
    if not skill_name:
        return "import_skill: skill_name required"

    base = Path(skills_dir).expanduser() if skills_dir else _DEFAULT_SKILLS_DIR
    skill_path = base / skill_name / "SKILL.md"

    if not skill_path.exists():
        return f"import_skill: {skill_path} not found"

    text = skill_path.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(text)

    name = frontmatter.get("name", skill_name)
    description = frontmatter.get("description", f"{skill_name} skill")
    model_hint = frontmatter.get("model", "sonnet")

    steps = _split_steps(body)
    hard_rules = _extract_hard_rules(body)
    skill_refs = _extract_skill_refs(body)
    payload = _build_payload(steps, hard_rules, skill_refs)

    node_id = f"SKILL_{skill_name.upper().replace('-', '_')}_ENTRY"
    narrative = f"{node_id} — {description}"

    # Count executable instructions
    bash_count = sum(1 for k in payload if k.startswith("bash_args_"))
    ref_count = len(
        [
            i
            for i in payload.get("run_cell", [])
            if isinstance(i, list) and i[0] == "FORKIF"
        ]
    )

    metadata = {
        "memory_type": "PROCEDURAL",
        "skill_name": name,
        "skill_source": str(skill_path),
        "skill_model_hint": model_hint,
        "triggers": {"__entry__": "run_cell"},
        "step_titles": [s["title"] for s in steps],
        "hard_rules_count": len(hard_rules),
        "bash_blocks": bash_count,
        "skill_refs": skill_refs,
        "inertia": 0.2,
    }

    try:
        from ..memory.cortex import Cortex
        from ..memory.models import Memory, MemoryType
        from ..paths import paths as _paths

        cortex = Cortex()
        mem = Memory(
            id=node_id,
            narrative=narrative,
            memory_type=MemoryType.PROCEDURAL,
            metadata=metadata,
            payload=payload,
            source="skill_importer",
        )
        cortex.store(mem)

        logger.info(
            "import_skill: seeded %s — %d steps, %d bash blocks, %d skill refs",
            node_id,
            len(steps),
            bash_count,
            ref_count,
        )
        return (
            f"import_skill: seeded {node_id}\n"
            f"  Steps: {len(steps)} ({', '.join(s['title'][:40] for s in steps[:5])}{'...' if len(steps)>5 else ''})\n"
            f"  Bash blocks: {bash_count}\n"
            f"  Skill refs: {skill_refs[:8]}\n"
            f"  Hard rules: {len(hard_rules)}"
        )

    except Exception as e:
        logger.warning("import_skill failed %s: %s", skill_name, e)
        return f"import_skill: error — {e}"


def import_all_skills(skills_dir: str = "", **_) -> str:
    """
    Import all skills found in skills_dir.
    Returns a summary of what was seeded.
    """
    base = Path(skills_dir).expanduser() if skills_dir else _DEFAULT_SKILLS_DIR
    if not base.exists():
        return f"import_all_skills: {base} not found"

    results = []
    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        result = import_skill(skill_name=skill_dir.name, skills_dir=str(base))
        results.append(f"  {skill_dir.name}: {result.splitlines()[0]}")

    if not results:
        return "import_all_skills: no skills found"

    return f"import_all_skills: {len(results)} skills imported\n" + "\n".join(results)


# ── tool registration ─────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="import_skill",
        description=(
            "Parse a Claude Code skill markdown file (~/.claude/skills/<name>/SKILL.md) "
            "and seed/update it as a PROCEDURAL Memory engram node in the DB. "
            "Igor can invoke the skill via cursor traversal or habit trigger. "
            "Re-run to update when skill file changes. "
            "Args: skill_name (string, e.g. 'sprint'), skills_dir (optional override)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "skill_name": {"type": "string"},
                "skills_dir": {"type": "string"},
            },
            "required": ["skill_name"],
        },
        fn=import_skill,
    )
)

registry.register(
    Tool(
        name="import_all_skills",
        description=(
            "Import all Claude Code skills from ~/.claude/skills/ as engram nodes. "
            "Upserts each skill — safe to re-run. Use after skill files are updated."
        ),
        parameters={
            "type": "object",
            "properties": {"skills_dir": {"type": "string"}},
            "required": [],
        },
        fn=import_all_skills,
    )
)
