"""Per-run model, LoRA, sampler, size and caption choices, found in a converted API graph so workflows need no editing."""
import math
import re

try:
    from app.workflow_conversion import _enum_options
except ImportError:  # flat imports inside the Docker image
    from workflow_conversion import _enum_options

MODEL_FIELDS = {"CheckpointLoaderSimple": "ckpt_name", "UNETLoader": "unet_name"}
LORA_LOADERS = ("LoraLoader", "LoraLoaderModelOnly")
MAX_EXTRA_LORAS = 4
MAX_STRENGTH = 5.0
# Plain values the phone may change on each sampler-type node.
SAMPLER_FIELDS = {
    "KSampler": ("steps", "cfg", "sampler_name", "scheduler", "denoise"),
    "KSamplerAdvanced": ("steps", "cfg", "sampler_name", "scheduler"),
    "KSamplerSelect": ("sampler_name",),
    "CFGGuider": ("cfg",),
    "BasicScheduler": ("steps", "scheduler", "denoise"),
}
LIMITS = {"steps": (1, 150), "cfg": (0.0, 30.0), "denoise": (0.01, 1.0)}
# Auto-caption (VLM) nodes: the choices and switches the Open WebUI function exposed as caption Valves.
CAPTION_CLASSES = ("Qwen3VLAbliteratedPromptPair", "Qwen3VLAbliteratedCaption", "JoyCaptionPromptPair")
CAPTION_CHOICES = ("model", "caption_type", "length", "content_mode")
CAPTION_TOGGLES = ("vulgar_slang", "lighting", "camera_angle", "shot_type", "composition", "depth_of_field",
                   "camera_settings", "aesthetic_quality", "mention_mood")
MAX_SIDE, MAX_PIXELS = 2048, 2048 * 2048


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


def _sampler_role(node):
    """Full generation, or a later pass that only refines (hi-res fix, detailer)."""
    inputs = node["inputs"]
    denoise = inputs.get("denoise", 1)
    partial = node["class_type"] == "KSamplerAdvanced" and (inputs.get("add_noise") == "disable" or inputs.get("start_at_step", 0) != 0)
    return "refine" if partial or (isinstance(denoise, (int, float)) and denoise < 0.99) else "base"


def _samplers(graph):
    found = []
    for node_id, node in graph.items():
        fields = SAMPLER_FIELDS.get(node["class_type"], ())
        values = {f: node["inputs"][f] for f in fields
                  if f in node["inputs"] and not isinstance(node["inputs"][f], (list, dict, bool))}
        if node["class_type"] == "KSampler" and _sampler_role(node) == "base":
            values.pop("denoise", None)  # a full pass stays at 1; lowering it only makes sense for refiners
        if values:
            found.append({"node": node_id, "class": node["class_type"], "role": _sampler_role(node), "values": values})
    return sorted(found, key=lambda c: (c["role"] != "base", c["node"]))


def _latents(graph):
    """Empty latent nodes with a plain size: the output size of text-to-image workflows."""
    return [node_id for node_id, node in graph.items()
            if re.fullmatch(r"Empty\w*LatentImage", node["class_type"])
            and all(isinstance(node["inputs"].get(f), int) and not isinstance(node["inputs"].get(f), bool) for f in ("width", "height"))]


def _captions(graph, object_info):
    found = []
    for node_id, node in graph.items():
        if node["class_type"] not in CAPTION_CLASSES:
            continue
        inputs, values, options = node["inputs"], {}, {}
        for field in CAPTION_CHOICES:
            choices = _options(object_info, node["class_type"], field)
            if isinstance(inputs.get(field), str) and choices:
                values[field], options[field] = inputs[field], choices
        values.update({f: inputs[f] for f in CAPTION_TOGGLES if isinstance(inputs.get(f), bool)})
        if values:
            found.append({"node": node_id, "class": node["class_type"], "values": values, "options": options})
    return found


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
    latents = _latents(graph)
    size = {"width": graph[latents[0]]["inputs"]["width"], "height": graph[latents[0]]["inputs"]["height"]} if latents else None
    return {"models": models, "loras": loras, "lora_options": _options(object_info, "LoraLoaderModelOnly", "lora_name"),
            "can_add_lora": _lora_anchor(graph, models) is not None,
            "samplers": _samplers(graph), "size": size, "captions": _captions(graph, object_info),
            "sampler_options": _options(object_info, "KSampler", "sampler_name"),
            "scheduler_options": _options(object_info, "KSampler", "scheduler")}


def _strength(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > MAX_STRENGTH:
        raise ControlError(f"LoRA strength must be between -{MAX_STRENGTH:g} and {MAX_STRENGTH:g}")
    return float(value)


def _number(field, value):
    low, high = LIMITS[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ControlError(f"{field} must be between {low:g} and {high:g}")
    if field == "steps":
        if value != int(value):
            raise ControlError("steps must be a whole number")
        return int(value)
    return float(value)


def _apply_sampling(graph, found, sampling):
    by_node = {c["node"]: c for c in found["samplers"]}
    options = {"sampler_name": set(found["sampler_options"]), "scheduler": set(found["scheduler_options"])}
    for node_id, changes in sampling.items():
        control = by_node.get(node_id)
        if control is None:
            raise ControlError(f"Node {node_id} has no sampler settings in this workflow")
        for field, value in changes.items():
            if field not in control["values"]:
                raise ControlError(f"{field} cannot be changed on node {node_id}")
            if field in options:
                if value not in options[field]:
                    raise ControlError(f"Unknown {field.replace('_name', '')}: {value}")
            else:
                value = _number(field, value)
            graph[node_id]["inputs"][field] = value


def _apply_caption(graph, found, caption):
    by_node = {c["node"]: c for c in found["captions"]}
    for node_id, changes in caption.items():
        control = by_node.get(node_id)
        if control is None:
            raise ControlError(f"Node {node_id} is not a caption node in this workflow")
        for field, value in changes.items():
            if field not in control["values"]:
                raise ControlError(f"{field} cannot be changed on node {node_id}")
            if field in control["options"]:
                if value not in control["options"][field]:
                    raise ControlError(f"Unknown {field.replace('_', ' ')}: {value}")
            elif not isinstance(value, bool):
                raise ControlError(f"{field} must be on or off")
            graph[node_id]["inputs"][field] = value


def _apply_size(graph, size):
    width, height = size.get("width"), size.get("height")
    for side in (width, height):
        if isinstance(side, bool) or not isinstance(side, int) or not 256 <= side <= MAX_SIDE or side % 8:
            raise ControlError(f"Width and height must be multiples of 8 between 256 and {MAX_SIDE}")
    if width * height > MAX_PIXELS:
        raise ControlError("Image size is too large")
    latents = _latents(graph)
    if not latents:
        raise ControlError("This workflow takes its size from the input image")
    for node_id in latents:
        graph[node_id]["inputs"]["width"], graph[node_id]["inputs"]["height"] = width, height


def apply_controls(graph: dict, object_info: dict, models: dict, loras: dict, extra_loras: list,
                   sampling: dict | None = None, size: dict | None = None, caption: dict | None = None) -> None:
    """Apply phone choices in place. Only nodes and options found by find_controls are accepted."""
    if not (models or loras or extra_loras or sampling or size or caption):
        return
    found = find_controls(graph, object_info)
    if sampling:
        _apply_sampling(graph, found, sampling)
    if caption:
        _apply_caption(graph, found, caption)
    if size:
        _apply_size(graph, size)
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
