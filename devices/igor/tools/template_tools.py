"""
Template engine for Engram language primitives.

TEMPLATE nodes are PROCEDURAL Memory nodes with metadata.template_schema.
They are the language primitives of the matrix layer — macros that expand
into habits at seed time. At runtime there are only habits firing.

Three-layer node structure (T-template-schema design):
  slot_manifest     — named slots with type constraints and defaults
  expansion_schema  — habit dicts with {slot_name} and {slot_name|filter} placeholders
  instantiation_contract — postconditions: produces, condition_signature, invariants, edge_policy
"""

import json
import re
import uuid
import logging
from ..memory.models import Memory, MemoryType
from ..cognition.forensic_logger import log_error

logger = logging.getLogger(__name__)

_SLOT_FILTERS = {
    "upper": str.upper,
    "lower": str.lower,
    "snake": lambda s: s.lower().replace(" ", "_").replace("-", "_"),
    "title": str.title,
}

_SLOT_TYPES = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}

_SLOT_PATTERN = re.compile(r"\{(\w+(?:\|\w+)?)\}")


def _apply_slot(slot_ref: str, params: dict) -> str:
    """Resolve a slot reference like 'slot_name' or 'slot_name|filter'."""
    if "|" in slot_ref:
        name, filt = slot_ref.split("|", 1)
        val = str(params.get(name, f"{{{slot_ref}}}"))
        return _SLOT_FILTERS.get(filt, lambda s: s)(val)
    return str(params.get(slot_ref, f"{{{slot_ref}}}"))


def _substitute(text: str, params: dict) -> str:
    """Replace {slot} and {slot|filter} in a string."""
    return _SLOT_PATTERN.sub(lambda m: _apply_slot(m.group(1), params), text)


def _substitute_deep(obj, params: dict):
    """Recursively substitute {slot} refs in all string values of a dict/list."""
    if isinstance(obj, str):
        return _substitute(obj, params)
    elif isinstance(obj, dict):
        return {k: _substitute_deep(v, params) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_substitute_deep(item, params) for item in obj]
    return obj  # int/float/bool pass through unchanged


def _validate_slots(slot_manifest: list, params: dict) -> tuple:
    """
    Validate params against slot_manifest.
    Returns (resolved_params, errors).
    Applies type coercion and fills defaults.
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

    # Layer 2: expand expansion_schema
    expansion_schema = schema.get("expansion_schema", [])
    if not expansion_schema:
        return f"ERROR: template '{template_id}' has empty expansion_schema"

    expanded_items = [
        _substitute_deep(item, resolved_params) for item in expansion_schema
    ]

    # Layer 3: check instantiation_contract invariants
    contract = schema.get("instantiation_contract", {})
    violations = _check_invariants(contract, resolved_params, expanded_items)
    if violations:
        return "ERROR: instantiation_contract invariants failed:\n" + "\n".join(
            f"  - {v}" for v in violations
        )

    # Seed each expanded item as a Memory node
    pattern_name = schema.get("pattern_name", "UNKNOWN")
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
