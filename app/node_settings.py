"""Every plain setting of every node in a converted workflow, so nodes added or changed on the PC reach the phone
without code changes. Values are checked against ComfyUI's own schema (type, min/max, options) when a job applies them.

Fields the dedicated controls own (models, LoRAs, samplers, size, captions, video, prompts) are left to those
controls, and plumbing fields (seeds, file paths, URLs, devices) are never offered."""
import math
import re

# Seeds follow the phone's seed box; file names, folders and addresses stay as the workflow author set them.
SKIP_FIELD = re.compile(r"(^|_)(seed|noise_seed|control_after_generate|url|host|port|path|dir|directory|folder|"
                        r"filename|prefix|device|api_key|token)(_|$)", re.I)
SKIP_CLASSES = {"LoadImage", "LoadImageMask", "SaveImage", "PreviewImage", "PreviewAny"}
# A node titled with this marker is pinned to the top of the phone's settings list.
PIN = "📱"
MAX_TEXT = 10000
MAX_OPTIONS = 2000


class SettingsError(ValueError):
    pass


def _definition(object_info, class_type, field):
    schema = object_info.get(class_type, {}).get("input", {})
    return (schema.get("required") or {}).get(field) or (schema.get("optional") or {}).get(field)


def _kind(definition):
    """(type, extra) for a schema entry: int, float, bool, combo, text; None for sockets and unknown types."""
    if not isinstance(definition, list) or not definition:
        return None, {}
    head = definition[0]
    extra = definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
    if extra.get("forceInput"):
        return None, extra
    if isinstance(head, list):
        return "combo", {**extra, "options": head}
    return {"INT": "int", "FLOAT": "float", "BOOLEAN": "bool", "STRING": "text", "COMBO": "combo"}.get(head), extra


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _field(class_type, name, value, definition):
    kind, extra = _kind(definition)
    if kind is None or isinstance(value, (list, dict)):
        return None
    out = {"name": name, "type": kind, "value": value}
    if isinstance(extra.get("tooltip"), str):
        out["tip"] = extra["tooltip"][:300]
    if kind in ("int", "float"):
        if not _number(value) or (kind == "int" and value != int(value)):
            return None
        for key in ("min", "max", "step"):
            if _number(extra.get(key)) and abs(extra[key]) < 1e15:
                out[key] = extra[key]
        if extra.get("round") is not None and _number(extra.get("round")) and kind == "float" and "step" not in out:
            out["step"] = extra["round"]
    elif kind == "bool":
        if not isinstance(value, bool):
            return None
    elif kind == "combo":
        options = extra.get("options")
        if not isinstance(value, str) or not isinstance(options, list) or not options or len(options) > MAX_OPTIONS:
            return None
        out["options"] = [o for o in options if isinstance(o, str)]
    elif kind == "text":
        # Only paragraph text (and string primitives); one-line strings are mostly names and paths.
        primitive = class_type in ("PrimitiveString", "PrimitiveStringMultiline")
        if not isinstance(value, str) or "{{prompt}}" in value or len(value) > MAX_TEXT or not (extra.get("multiline") or primitive):
            return None
        out["multiline"] = True
    return out


def _order(node_id):
    return [int(p) if p.isdigit() else 0 for p in node_id.split(":")]


def find(graph: dict, object_info: dict, owned: set) -> list:
    """Groups of {node, class, title, pinned, fields} for every node with settings the phone may change."""
    groups = []
    for node_id, node in graph.items():
        class_type = node.get("class_type")
        if class_type in SKIP_CLASSES or node_id.startswith("mobile_"):
            continue
        fields = []
        for name, value in node.get("inputs", {}).items():
            if (node_id, name) in owned or (node_id, "*") in owned or SKIP_FIELD.search(name):
                continue
            definition = _definition(object_info, class_type, name)
            found = _field(class_type, name, value, definition) if definition else None
            if found:
                fields.append(found)
        if not fields:
            continue
        title = (node.get("_meta") or {}).get("title") or object_info.get(class_type, {}).get("display_name") or class_type
        pinned = title.startswith(PIN)
        groups.append({"node": node_id, "class": class_type, "title": title.removeprefix(PIN).strip() or class_type,
                       "pinned": pinned, "fields": fields})
    return sorted(groups, key=lambda g: (not g["pinned"], _order(g["node"])))


def apply(graph: dict, groups: list, changes: dict) -> None:
    """Apply {node: {field: value}} in place; only fields find() offered, within the schema's limits."""
    by_node = {g["node"]: {f["name"]: f for f in g["fields"]} for g in groups}
    for node_id, values in changes.items():
        fields = by_node.get(node_id)
        if fields is None:
            raise SettingsError(f"Node {node_id} has no settings that can change in this workflow")
        if not isinstance(values, dict):
            raise SettingsError(f"Settings for node {node_id} must be an object")
        for name, value in values.items():
            field = fields.get(name)
            if field is None:
                raise SettingsError(f"{name} cannot be changed on node {node_id}")
            graph[node_id]["inputs"][name] = _check(field, value, node_id)


def _check(field, value, node_id):
    name, kind = field["name"], field["type"]
    where = f"{name} on node {node_id}"
    if kind in ("int", "float"):
        if not _number(value):
            raise SettingsError(f"{where} must be a number")
        if kind == "int":
            if value != int(value):
                raise SettingsError(f"{where} must be a whole number")
            value = int(value)
        else:
            value = float(value)
        if ("min" in field and value < field["min"]) or ("max" in field and value > field["max"]):
            raise SettingsError(f"{where} must be between {field.get('min', '-∞')} and {field.get('max', '∞')}")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{where} must be on or off")
        return value
    if kind == "combo":
        if value not in field["options"]:
            raise SettingsError(f"Unknown {name.replace('_', ' ')} on node {node_id}: {value}")
        return value
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise SettingsError(f"{where} must be text of at most {MAX_TEXT} characters")
    if "{{prompt}}" in value:
        raise SettingsError(f"{where} cannot contain the prompt placeholder")
    return value
