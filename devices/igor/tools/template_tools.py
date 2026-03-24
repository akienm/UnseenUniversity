"""
Template engine for Engram language primitives.

TEMPLATE nodes are PROCEDURAL Memory nodes with metadata.template_schema.
They are the language primitives of the matrix layer — macros that expand
into habits at seed time. At runtime there are only habits firing.

Three-layer node structure (T-template-schema design, D209):
  slot_manifest     — named slots with type_hint, required, default, validator
  expansion_schema  — Jinja2 templates producing habit dicts; {{slot}} and {{slot|filter}}
  instantiation_contract — postconditions: produces, condition_signature, invariants, edge_policy

Substitution engine: Jinja2 (schema_version 1).
  - {{slot_name}} — basic substitution
  - {{slot_name|upper}}, |lower|, |snake|, |title| — built-in Jinja2 filters + custom
  - {%- if slot -%}...{%- endif -%} — conditional habit inclusion
  - {%- for item in list_slot -%}...{%- endfor -%} — loop to produce N habits from list slot

TEMPLATE nodes are NEVER executed directly by the habit executor — they have no trigger
and carry tag 'template'. The BG executor guard skips any node where 'template' in tags.
"""

import json
import os
import re
import urllib.request
import uuid
import logging
from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, UndefinedError
from ..memory.models import Memory, MemoryType
from ..cognition.forensic_logger import log_error

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_CHEAP_MODEL = None  # resolved lazily from env

logger = logging.getLogger(__name__)

_SLOT_TYPES = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}

# Jinja2 environment — strict: missing variables raise UndefinedError
_jinja = Environment(undefined=StrictUndefined)
_jinja.filters["snake"] = lambda s: s.lower().replace(" ", "_").replace("-", "_")


def _render(obj, params: dict):
    """Recursively render Jinja2 templates in all string values of a dict/list."""
    if isinstance(obj, str):
        try:
            return _jinja.from_string(obj).render(**params)
        except (TemplateSyntaxError, UndefinedError) as e:
            raise ValueError(f"Jinja2 render failed on {obj!r}: {e}") from e
    elif isinstance(obj, dict):
        return {k: _render(v, params) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_render(item, params) for item in obj]
    return obj  # int/float/bool pass through unchanged


def _validate_slot_value(name: str, value, validator: dict) -> list:
    """Run validator constraints on a resolved slot value. Returns list of errors."""
    errors = []
    if "min" in validator and value < validator["min"]:
        errors.append(f"slot '{name}': {value} < min {validator['min']}")
    if "max" in validator and value > validator["max"]:
        errors.append(f"slot '{name}': {value} > max {validator['max']}")
    if "enum" in validator and value not in validator["enum"]:
        errors.append(f"slot '{name}': '{value}' not in enum {validator['enum']}")
    if "pattern" in validator:
        if not re.search(validator["pattern"], str(value)):
            errors.append(
                f"slot '{name}': '{value}' does not match pattern {validator['pattern']!r}"
            )
    return errors


def _validate_slots(slot_manifest: list, params: dict) -> tuple:
    """
    Validate params against slot_manifest.
    Returns (resolved_params, errors).
    Applies type coercion, fills defaults, runs validator constraints.
    """
    resolved = {}
    errors = []
    for slot in slot_manifest:
        name = slot.get("name")
        if not name:
            errors.append("slot_manifest entry missing 'name'")
            continue
        hint = slot.get("type_hint", "str")
        coerce = _SLOT_TYPES.get(hint, str)
        if name in params:
            try:
                resolved[name] = coerce(params[name])
            except (ValueError, TypeError) as e:
                errors.append(
                    f"slot '{name}': cannot coerce '{params[name]}' to {hint}: {e}"
                )
                continue
            validator = slot.get("validator")
            if validator:
                errors.extend(_validate_slot_value(name, resolved[name], validator))
        elif slot.get("required", True):
            errors.append(f"required slot '{name}' not provided")
        else:
            resolved[name] = slot.get("default", "")
    return resolved, errors


def _check_invariants(
    contract: dict, resolved_params: dict, expanded_items: list
) -> list:
    """
    Check instantiation_contract invariants.
    Returns list of violation strings (empty = all clear).
    """
    violations = []
    for inv in contract.get("invariants", []):
        if "code_ref must be registered" in inv:
            for item in expanded_items:
                code_ref = item.get("metadata", {}).get("code_ref", "")
                if code_ref:
                    try:
                        from .registry import registry

                        if not registry.get(code_ref):
                            violations.append(
                                f"invariant: code_ref '{code_ref}' not found in tool registry"
                            )
                    except Exception:
                        pass  # registry not available — skip check
    return violations


def _get_cortex():
    from ..paths import paths
    from ..memory.cortex import Cortex

    return Cortex(paths().instance / "wild-0001.db")


def instantiate_template(template_id: str, params_json: str) -> str:
    """
    Expand a TEMPLATE Memory node into seeded habit nodes.

    template_id: id of a PROCEDURAL Memory node with metadata.template_schema
    params_json: JSON object mapping slot names to values

    Returns a summary of what was seeded, or an error string.
    """
    cortex = _get_cortex()

    node = cortex.get(template_id)
    if not node:
        return f"ERROR: template node '{template_id}' not found"

    schema = node.metadata.get("template_schema")
    if not schema:
        return f"ERROR: node '{template_id}' has no metadata.template_schema"

    try:
        params = json.loads(params_json) if params_json.strip() else {}
    except json.JSONDecodeError as e:
        return f"ERROR: invalid params_json: {e}"

    # Layer 1: validate slots + coerce types
    slot_manifest = schema.get("slot_manifest", [])
    resolved_params, slot_errors = _validate_slots(slot_manifest, params)
    if slot_errors:
        return "ERROR: slot validation failed:\n" + "\n".join(
            f"  - {e}" for e in slot_errors
        )

    # Layer 2: expand expansion_schema via Jinja2
    expansion_schema = schema.get("expansion_schema", [])
    if not expansion_schema:
        return f"ERROR: template '{template_id}' has empty expansion_schema"

    try:
        expanded_items = [_render(item, resolved_params) for item in expansion_schema]
    except ValueError as e:
        return f"ERROR: expansion_schema render failed: {e}"

    # Layer 3: check instantiation_contract invariants
    contract = schema.get("instantiation_contract", {})
    violations = _check_invariants(contract, resolved_params, expanded_items)
    if violations:
        return "ERROR: instantiation_contract invariants failed:\n" + "\n".join(
            f"  - {v}" for v in violations
        )

    # Collision detection — check for id clashes before any stores
    pattern_name = schema.get("pattern_name", "UNKNOWN")
    collisions = []
    for item in expanded_items:
        candidate_id = item.get("id")
        if candidate_id and cortex.get(candidate_id):
            collisions.append(candidate_id)
    if collisions:
        return (
            f"ERROR: collision — node(s) already exist: {', '.join(collisions)}. "
            "Use distinct slot values or delete existing nodes first."
        )

    # Seed each expanded item as a Memory node
    seeded_ids = []
    for item in expanded_items:
        item_copy = dict(item)
        node_id = (
            item_copy.pop("id", None)
            or f"tpl_{pattern_name.lower()}_{uuid.uuid4().hex[:8]}"
        )
        narrative = item_copy.pop(
            "narrative",
            f"{pattern_name} instance seeded from template {template_id}",
        )
        mt_str = item_copy.pop("memory_type", "PROCEDURAL")
        try:
            mt = MemoryType[mt_str]
        except KeyError:
            mt = MemoryType.PROCEDURAL

        # nested metadata key takes precedence; flat item_copy is fallback
        habit_meta = item_copy.pop(
            "metadata", item_copy if "metadata" not in item else {}
        )
        habit_meta["template_origin"] = template_id
        habit_meta["template_pattern"] = pattern_name

        mem = Memory(
            id=node_id,
            narrative=narrative,
            memory_type=mt,
            metadata=habit_meta,
            source="user_seeded",
            context_of_encoding=f"instantiated from template {template_id}",
        )
        cortex.store(mem)
        seeded_ids.append(node_id)
        logger.info(
            "TEMPLATE_INSTANTIATE|template=%s|node=%s|pattern=%s",
            template_id,
            node_id,
            pattern_name,
        )

    return (
        f"Instantiated {len(seeded_ids)} node(s) from template '{template_id}' "
        f"(pattern={pattern_name}): {', '.join(seeded_ids)}"
    )


def list_templates(pattern_filter: str = "") -> str:
    """List all Engram TEMPLATE Memory nodes (PROCEDURAL with metadata.template_schema)."""
    cortex = _get_cortex()

    all_procedural = cortex.get_by_type(MemoryType.PROCEDURAL, limit=200)
    templates = [m for m in all_procedural if m.metadata.get("template_schema")]
    if pattern_filter:
        templates = [
            t
            for t in templates
            if pattern_filter.lower()
            in t.metadata.get("template_schema", {}).get("pattern_name", "").lower()
        ]
    if not templates:
        suffix = f" matching '{pattern_filter}'" if pattern_filter else ""
        return f"No TEMPLATE nodes found{suffix}."

    lines = []
    for t in templates:
        schema = t.metadata["template_schema"]
        slot_manifest = schema.get("slot_manifest", [])
        required = [s["name"] for s in slot_manifest if s.get("required", True)]
        all_slots = [s["name"] for s in slot_manifest]
        contract = schema.get("instantiation_contract", {})
        produces = contract.get("produces", [])
        lines.append(
            f"{t.id} | {schema.get('pattern_name', '?'):20s} "
            f"| required: {', '.join(required) or 'none':30s} "
            f"| all slots: {', '.join(all_slots) or 'none':30s} "
            f"| produces: {', '.join(produces)}"
        )
    return "\n".join(lines)


def validate_template_schema(template_json: str) -> str:
    """Validate a template_schema JSON blob before storing it in a Memory node."""
    try:
        schema = json.loads(template_json)
    except json.JSONDecodeError as e:
        return f"ERROR: invalid JSON: {e}"

    errors = []
    if "pattern_name" not in schema:
        errors.append("missing 'pattern_name'")
    if "slot_manifest" not in schema:
        errors.append("missing 'slot_manifest'")
    if "expansion_schema" not in schema:
        errors.append("missing 'expansion_schema'")
    if "instantiation_contract" not in schema:
        errors.append("missing 'instantiation_contract'")

    for i, slot in enumerate(schema.get("slot_manifest", [])):
        if "name" not in slot:
            errors.append(f"slot_manifest[{i}]: missing 'name'")
        if "required" not in slot:
            errors.append(f"slot_manifest[{i}]: missing 'required'")

    contract = schema.get("instantiation_contract", {})
    if "produces" not in contract:
        errors.append("instantiation_contract: missing 'produces'")
    if "edge_policy" not in contract:
        errors.append("instantiation_contract: missing 'edge_policy'")

    if not schema.get("expansion_schema"):
        errors.append("expansion_schema is empty")

    if errors:
        return "INVALID:\n" + "\n".join(f"  - {e}" for e in errors)

    pattern = schema.get("pattern_name", "unnamed")
    n_slots = len(schema.get("slot_manifest", []))
    n_expands = len(schema.get("expansion_schema", []))
    produces = contract.get("produces", [])
    return f"VALID: pattern={pattern}, slots={n_slots}, expands={n_expands}, produces={produces}"


# ── LLM helper ───────────────────────────────────────────────────────────────


def _cheap_model() -> str:
    global _CHEAP_MODEL
    if _CHEAP_MODEL is None:
        _CHEAP_MODEL = os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini")
    return _CHEAP_MODEL


def _llm_call(system: str, user: str, max_tokens: int = 512) -> str:
    """
    Minimal OpenRouter call using the cheap model.
    Returns the raw text content or raises RuntimeError.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set — cannot call LLM")
    payload = json.dumps(
        {
            "model": _cheap_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
    ).encode()
    req = urllib.request.Request(
        f"{_OPENROUTER_BASE}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/akienm/TheIgors",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise RuntimeError(f"LLM call failed: {e}") from e


# ── Pattern recognition ───────────────────────────────────────────────────────


def recognize_pattern(code_or_description: str) -> str:
    """
    Given Python code or a habit/process description, identify which Engram
    template pattern it best matches.

    Uses list_templates() to get the 21-pattern inventory, then asks the cheap
    LLM model to classify the input.

    Returns JSON: {"pattern_name": str, "template_id": str, "confidence": float,
                   "reasoning": str}
    or an error string starting with "ERROR:".
    """
    if not code_or_description.strip():
        return "ERROR: code_or_description is empty"

    template_summary = list_templates()
    if template_summary.startswith("No TEMPLATE"):
        return "ERROR: no TEMPLATE nodes found in matrix — run seed_templates.py first"

    system = (
        "You are an Engram pattern classifier. "
        "Engram is a macro language for Igor's habit matrix. "
        "There are 21 named patterns. Each is a structural template for how a habit or "
        "habit chain is organised. "
        "Given the list of patterns below and a code/description input, "
        "identify the SINGLE best matching pattern. "
        "Reply ONLY with valid JSON: "
        '{"pattern_name": "...", "template_id": "...", "confidence": 0.0-1.0, "reasoning": "one sentence"}'
        " — no markdown, no extra text."
    )
    user = (
        f"Available patterns:\n{template_summary}\n\n"
        f"Input to classify:\n{code_or_description[:3000]}"
    )

    try:
        raw = _llm_call(system, user, max_tokens=256)
        # Validate it's parseable JSON with required keys
        parsed = json.loads(raw)
        for key in ("pattern_name", "template_id", "confidence", "reasoning"):
            if key not in parsed:
                raise ValueError(f"missing key '{key}'")
        logger.info(
            "RECOGNIZE_PATTERN|pattern=%s|confidence=%.2f",
            parsed["pattern_name"],
            parsed["confidence"],
        )
        return json.dumps(parsed)
    except (RuntimeError, json.JSONDecodeError, ValueError) as e:
        log_error(kind="TEMPLATE_RECOGNIZE", detail=str(e))
        return f"ERROR: {e}"


# ── Template parameterization ─────────────────────────────────────────────────


def parameterize_template(code_or_description: str, pattern_name: str) -> str:
    """
    Given Python code or a habit/process description and an Engram pattern name,
    extract the slot values that would be needed to instantiate that template.

    Looks up the template's slot_manifest to know what slots exist, then asks
    the cheap LLM model to extract values from the input text.

    Returns JSON: {"template_id": str, "pattern_name": str,
                   "params": {slot_name: value, ...}, "missing": [slot_names]}
    or an error string starting with "ERROR:".
    """
    if not code_or_description.strip():
        return "ERROR: code_or_description is empty"
    if not pattern_name.strip():
        return "ERROR: pattern_name is empty"

    # Find the template node by pattern_name
    cortex = _get_cortex()
    all_procedural = cortex.get_by_type(MemoryType.PROCEDURAL, limit=200)
    template_node = None
    for m in all_procedural:
        schema = m.metadata.get("template_schema", {})
        if schema.get("pattern_name", "").upper() == pattern_name.upper():
            template_node = m
            break

    if template_node is None:
        return f"ERROR: no TEMPLATE node found for pattern '{pattern_name}'"

    schema = template_node.metadata["template_schema"]
    slot_manifest = schema.get("slot_manifest", [])
    if not slot_manifest:
        return f"ERROR: template '{template_node.id}' has no slot_manifest"

    slots_desc = "\n".join(
        f"  {s['name']} ({s.get('type_hint','str')}, "
        f"{'required' if s.get('required', True) else 'optional'}"
        f"{', default=' + repr(s['default']) if 'default' in s else ''}): "
        f"{s.get('description', '')}"
        for s in slot_manifest
    )

    system = (
        "You are an Engram template parameterizer. "
        f"The pattern is {pattern_name}. "
        "Given the slot definitions below and the input code/description, "
        "extract a value for each slot. "
        "Reply ONLY with valid JSON: "
        '{"template_id": "...", "pattern_name": "...", '
        '"params": {slot_name: extracted_value, ...}, '
        '"missing": [slot_names_you_could_not_extract]}'
        " — no markdown, no extra text."
    )
    user = (
        f"Template id: {template_node.id}\n"
        f"Pattern: {pattern_name}\n\n"
        f"Slots:\n{slots_desc}\n\n"
        f"Input to parameterize:\n{code_or_description[:3000]}"
    )

    try:
        raw = _llm_call(system, user, max_tokens=512)
        parsed = json.loads(raw)
        for key in ("template_id", "pattern_name", "params", "missing"):
            if key not in parsed:
                raise ValueError(f"missing key '{key}'")
        # Ensure template_id is correct
        parsed["template_id"] = template_node.id
        parsed["pattern_name"] = schema["pattern_name"]
        logger.info(
            "PARAMETERIZE_TEMPLATE|template=%s|params=%s|missing=%s",
            template_node.id,
            list(parsed["params"].keys()),
            parsed["missing"],
        )
        return json.dumps(parsed)
    except (RuntimeError, json.JSONDecodeError, ValueError) as e:
        log_error(kind="TEMPLATE_PARAMETERIZE", detail=str(e))
        return f"ERROR: {e}"


# ── Tool registration ────────────────────────────────────────────────────────

from .registry import Tool, registry  # noqa: E402

registry.register(
    Tool(
        name="instantiate_template",
        description=(
            "Expand an Engram TEMPLATE Memory node into seeded habit nodes. "
            "template_id is the id of a PROCEDURAL node with metadata.template_schema. "
            "params_json is a JSON object mapping slot names to values. "
            "Returns a summary of what was seeded."
        ),
        parameters={
            "template_id": "string — id of the TEMPLATE Memory node",
            "params_json": "string — JSON object of {slot_name: value} pairs",
        },
        fn=instantiate_template,
    )
)

registry.register(
    Tool(
        name="list_templates",
        description=(
            "List all Engram TEMPLATE Memory nodes (PROCEDURAL nodes with template_schema). "
            "Optionally filter by pattern_name substring."
        ),
        parameters={
            "pattern_filter": "string — optional pattern_name substring filter (default: list all)",
        },
        fn=list_templates,
    )
)

registry.register(
    Tool(
        name="validate_template_schema",
        description=(
            "Validate a template_schema JSON blob before storing it in a Memory node. "
            "Returns VALID or a list of structural errors."
        ),
        parameters={
            "template_json": "string — JSON object representing a template_schema",
        },
        fn=validate_template_schema,
    )
)

registry.register(
    Tool(
        name="recognize_pattern",
        description=(
            "Given Python code or a habit/process description, identify which Engram "
            "template pattern it best matches. Uses the 21-pattern inventory from the "
            "matrix and LLM classification. "
            "Returns JSON: {pattern_name, template_id, confidence, reasoning}."
        ),
        parameters={
            "code_or_description": (
                "string — Python code snippet or plain-language description of a habit/process"
            ),
        },
        fn=recognize_pattern,
    )
)

registry.register(
    Tool(
        name="parameterize_template",
        description=(
            "Given Python code or a habit/process description plus a pattern name, "
            "extract the slot values needed to instantiate that Engram template. "
            "Returns JSON: {template_id, pattern_name, params, missing}. "
            "Use recognize_pattern first if you don't know the pattern name."
        ),
        parameters={
            "code_or_description": (
                "string — Python code snippet or plain-language description of a habit/process"
            ),
            "pattern_name": (
                "string — Engram pattern name (e.g. CACHED_PROBE, THRESHOLD_ALERT)"
            ),
        },
        fn=parameterize_template,
    )
)
