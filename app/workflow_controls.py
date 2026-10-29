"""Per-run model and LoRA choices, found in a converted API graph so workflows need no editing."""
import math

try:
    from app.workflow_conversion import _enum_options
except ImportError:  # flat imports inside the Docker image
    from workflow_conversion import _enum_options

MODEL_FIELDS = {"CheckpointLoaderSimple": "ckpt_name", "UNETLoader": "unet_name"}
LORA_LOADERS = ("LoraLoader", "LoraLoaderModelOnly")
MAX_EXTRA_LORAS = 4
MAX_STRENGTH = 5.0


class ControlError(ValueError):
    pass


def _options(object_info, class_type, field):
    schema = object_info.get(class_type, {}).get("input", {})
    definition = (schema.get("required") or {}).get(field) or (schema.get("optional") or {}).get(field)
    return list(_enum_options(definition) or []) if definition else []


def _consumers(graph, node_id, slot=0):
    return [(nid, field) for nid, node in graph.items() for field, value in node["inputs"].items()
            if value == [node_id, slot]]


def _lora_anchor(graph, models):
    """Where extra LoRAs go: after the single model loader and any LoRAs already chained to it."""
    if len(models) != 1 or not _consumers(graph, models[0]["node"]):
        return None
    current = models[0]["node"]
    while True:
        loras = [nid for nid, field in _consumers(graph, current)
                 if field == "model" and graph[nid]["class_type"] in LORA_LOADERS]
        if len(loras) != 1:
            return current
        current = loras[0]


def find_controls(graph: dict, object_info: dict) -> dict:
    models, loras = [], []
    for node_id, node in graph.items():
        field = MODEL_FIELDS.get(node["class_type"])
        if field and isinstance(node["inputs"].get(field), str):
            models.append({"node": node_id, "field": field, "value": node["inputs"][field],
                           "options": _options(object_info, node["class_type"], field)})
        elif node["class_type"] in LORA_LOADERS and isinstance(node["inputs"].get("lora_name"), str):
            loras.append({"node": node_id, "value": node["inputs"]["lora_name"],
                          "strength": node["inputs"].get("strength_model", 1.0)})
    return {"models": models, "loras": loras, "lora_options": _options(object_info, "LoraLoaderModelOnly", "lora_name"),
            "can_add_lora": _lora_anchor(graph, models) is not None}


def _strength(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > MAX_STRENGTH:
        raise ControlError(f"LoRA strength must be between -{MAX_STRENGTH:g} and {MAX_STRENGTH:g}")
    return float(value)


def apply_controls(graph: dict, object_info: dict, models: dict, loras: dict, extra_loras: list) -> None:
    """Apply phone choices in place. Only nodes and options found by find_controls are accepted."""
    if not (models or loras or extra_loras):
        return
    found = find_controls(graph, object_info)
    by_model = {c["node"]: c for c in found["models"]}
    by_lora = {c["node"]: c for c in found["loras"]}
    lora_options = set(found["lora_options"])
    for node_id, name in models.items():
        control = by_model.get(node_id)
        if control is None:
            raise ControlError(f"Node {node_id} is not a model loader in this workflow")
        if name not in control["options"]:
            raise ControlError(f"Model not found in ComfyUI: {name}")
        graph[node_id]["inputs"][control["field"]] = name
    for node_id, choice in loras.items():
        if node_id not in by_lora:
            raise ControlError(f"Node {node_id} is not a LoRA loader in this workflow")
        if choice["name"] not in lora_options:
            raise ControlError(f"LoRA not found in ComfyUI: {choice['name']}")
        inputs = graph[node_id]["inputs"]
        strength = _strength(choice["strength"])
        # The CLIP strength follows the model strength when they matched; turning a LoRA off zeroes both,
        # which makes ComfyUI skip loading it.
        if "strength_clip" in inputs and (inputs["strength_clip"] == inputs["strength_model"] or strength == 0):
            inputs["strength_clip"] = strength
        inputs["lora_name"], inputs["strength_model"] = choice["name"], strength
    if not extra_loras:
        return
    if len(extra_loras) > MAX_EXTRA_LORAS:
        raise ControlError(f"At most {MAX_EXTRA_LORAS} extra LoRAs")
    anchor = _lora_anchor(graph, found["models"])
    if anchor is None:
        raise ControlError("This workflow has no single model loader to add LoRAs to")
    downstream = _consumers(graph, anchor)
    previous = anchor
    for i, choice in enumerate(extra_loras, 1):
        if choice["name"] not in lora_options:
            raise ControlError(f"LoRA not found in ComfyUI: {choice['name']}")
        node_id = f"mobile_lora_{i}"
        graph[node_id] = {"class_type": "LoraLoaderModelOnly",
                          "inputs": {"model": [previous, 0], "lora_name": choice["name"], "strength_model": _strength(choice["strength"])}}
        previous = node_id
    for node_id, field in downstream:
        graph[node_id]["inputs"][field] = [previous, 0]
