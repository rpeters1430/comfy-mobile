"""Read-only ComfyUI workflow diagnostics. No graph mutation or execution."""
import json
from pathlib import Path


def load_workflow(root: Path, workflow_id: str) -> dict:
    candidate = (root / workflow_id).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate.suffix.lower() != ".json" or not candidate.is_file():
        raise ValueError("Unknown workflow")
    if candidate.stat().st_size > 2_000_000:
        raise ValueError("Workflow too large")
    data = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Workflow JSON must be an object")
    return data


def inspect_workflow(data: dict, object_info: dict) -> dict:
    ui = isinstance(data.get("nodes"), list)
    api = bool(data) and all(
        isinstance(node, dict) and isinstance(node.get("class_type"), str)
        and isinstance(node.get("inputs"), dict) for node in data.values()
    )
    if not ui and not api:
        return {"format": "unknown", "runnable": False, "errors": ["Unsupported workflow format"], "warnings": []}
    types = sorted({
        str(n.get("type", "")) for n in data["nodes"] if isinstance(n, dict)
    }) if ui else sorted({n["class_type"] for n in data.values()})
    missing = sorted(set(types) - set(object_info))
    errors = [f"Missing ComfyUI node type: {t}" for t in missing]
    warnings = []
    if ui:
        errors.insert(0, "UI workflow must be exported as API format in ComfyUI before execution")
        return {"format": "ui", "runnable": False, "node_types": types, "errors": errors, "warnings": warnings}
    for node_id, node in data.items():
        spec = object_info.get(node["class_type"])
        if not isinstance(spec, dict):
            continue
        required = spec.get("input", {}).get("required", {})
        for field in required:
            if field not in node["inputs"]:
                errors.append(f"Node {node_id} ({node['class_type']}): missing required input {field}")
        for field, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                if value[0] not in data:
                    errors.append(f"Node {node_id}: input {field} references missing node {value[0]}")
    if not any("{{prompt}}" in v for node in data.values() for v in node["inputs"].values() if isinstance(v, str)):
        warnings.append("No {{prompt}} placeholder; mobile prompt submission will be rejected")
    return {"format": "api", "runnable": not errors and not warnings, "node_types": types, "errors": errors, "warnings": warnings}
