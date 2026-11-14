"""Per-run model, LoRA, sampler, size and caption choices, found in a converted API graph so workflows need no editing."""
import math
import re

if __package__:
    from app import node_settings
    from app.settings_check import LABELS, family
    from app.workflow_conversion import _enum_options
else:  # flat imports inside the Docker image
    import node_settings
    from settings_check import LABELS, family
    from workflow_conversion import _enum_options

MODEL_FIELDS = {"CheckpointLoaderSimple": "ckpt_name", "UNETLoader": "unet_name"}
LORA_LOADERS = ("LoraLoader", "LoraLoaderModelOnly")
# LoRA Manager's text loader reads `<lora:name:strength>` (or `<lora:name:model:clip>`) from a string primitive.
LORA_TAG = re.compile(r"<lora:([^:<>]+):(-?\d+(?:\.\d+)?)(?::(-?\d+(?:\.\d+)?))?>", re.I)
STRING_PRIMITIVES = ("PrimitiveString", "PrimitiveStringMultiline")
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
# Auto-caption (VLM) nodes. Known classes always count; any other node whose class or category names a caption or
# Ollama model, takes an image and returns text first counts too, so a newly added caption node needs no code change.
CAPTION_CLASSES = ("Qwen3VLAbliteratedPromptPair", "Qwen3VLAbliteratedCaption", "JoyCaptionPromptPair")
CAPTION_WORDS = re.compile(r"caption|ollama|vlm|llava|florence", re.I)
# Every unlinked dropdown and on/off switch of a caption node is offered, except plumbing ones.
CAPTION_SKIP = re.compile(r"(^|_)(debug|keep|unload|memory|quantization|device|precision|dtype|attention|url|host|"
                          r"seed)(_|$)", re.I)
# The field that picks the caption style, which the phone keeps per workflow rather than sharing.
CAPTION_STYLES = ("caption_type", "prompt_style", "preset")
MAX_SIDE, MAX_PIXELS = 2880, 2048 * 2048
# Final image outputs that a fit-to-screen resize can be put in front of.
SAVE_CLASSES = ("SaveImage", "SaveImageAdvanced")
FIT_MAX_SIDE, FIT_MAX_PIXELS = 4096, 4096 * 4096


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


def _stem(name):
    return re.sub(r"\.(safetensors|ckpt|pt|pth|bin)$", "", re.split(r"[\\/]", name)[-1], flags=re.I).lower()


def _syntax_sources(graph):
    """String primitives feeding a LoRA Manager text loader's lora_syntax socket."""
    sources = []
    for node in graph.values():
        source = node["inputs"].get("lora_syntax") if "LoraManager" in node["class_type"] else None
        if (isinstance(source, list) and len(source) == 2 and source[0] in graph and source[0] not in sources
                and graph[source[0]]["class_type"] in STRING_PRIMITIVES and isinstance(graph[source[0]]["inputs"].get("value"), str)):
            sources.append(source[0])
    return sources


def _syntax_loras(graph, lora_options):
    """Each `<lora:...>` tag that names exactly one installed LoRA, as a LoRA picker. Others stay as written."""
    by_stem = {}
    for option in lora_options:
        by_stem.setdefault(_stem(option), []).append(option)
    found = []
    for source in _syntax_sources(graph):
        for index, tag in enumerate(LORA_TAG.finditer(graph[source]["inputs"]["value"])):
            matches = by_stem.get(_stem(tag.group(1)), [])
            if len(matches) == 1:
                found.append({"node": f"{source}#{index}", "value": matches[0], "strength": float(tag.group(2)),
                              "syntax": {"node": source, "index": index}})
    return found


def _apply_syntax_loras(graph, by_lora, changes):
    """Rewrite the tags the phone changed; strength 0 removes the tag, as a LoRA turned off."""
    per_source = {}
    for node_id, choice in changes.items():
        per_source.setdefault(by_lora[node_id]["syntax"]["node"], {})[by_lora[node_id]["syntax"]["index"]] = choice
    for source, edits in per_source.items():
        counter = iter(range(10 ** 6))

        def rewrite(tag):
            choice = edits.get(next(counter))
            if choice is None:
                return tag.group(0)
            strength = _strength(choice["strength"])
            if strength == 0:
                return ""
            written = tag.group(1)
            name = re.split(r"[\\/]", choice["name"])[-1] if not re.search(r"[\\/]", written) else choice["name"].replace("\\", "/")
            if not re.search(r"\.(safetensors|ckpt|pt|pth|bin)$", written, re.I):
                name = re.sub(r"\.(safetensors|ckpt|pt|pth|bin)$", "", name, flags=re.I)
            clip = tag.group(3)
            if clip is not None and float(clip) == float(tag.group(2)):
                clip = f"{strength:g}"
            return f"<lora:{name}:{strength:g}" + (f":{clip}" if clip is not None else "") + ">"

        graph[source]["inputs"]["value"] = re.sub(r"\s{2,}", " ", LORA_TAG.sub(rewrite, graph[source]["inputs"]["value"])).strip()


def _owned(found, graph):
    """(node, field) pairs a dedicated control already offers, which the generic settings list leaves out."""
    owned = {(m["node"], m["field"]) for m in found["models"]}
    for lora in found["loras"]:
        if "syntax" in lora:
            owned.add((lora["syntax"]["node"], "value"))
        else:
            owned |= {(lora["node"], f) for f in ("lora_name", "strength_model", "strength_clip")}
    for smp in found["samplers"]:
        owned |= {(smp["node"], f) for f in SAMPLER_FIELDS.get(smp["class"], ())}
    for node_id in _latents(graph):
        owned |= {(node_id, "width"), (node_id, "height")}
    for cap in found["captions"]:
        owned |= {(cap["node"], f) for f in cap["values"]}
    for key in ("seconds_at", "width_at", "height_at", "megapixels_at"):
        if found["video"] and key in found["video"]:
            owned.add(tuple(found["video"][key]))
    if found["negative_prompt"]:
        owned.add((found["negative_prompt"]["node"], "text"))
    return owned


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


def _plain(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _photo_size(graph, node):
    """The GetImageSize node an empty latent copies its width and height from, or None.

    Image-to-prompt-to-image workflows only caption the photo and size the new image after it; a phone shape can
    replace those links with fixed numbers."""
    width, height = node["inputs"].get("width"), node["inputs"].get("height")
    if (isinstance(width, list) and isinstance(height, list) and width[0] == height[0] and (width[1], height[1]) == (0, 1)
            and graph.get(width[0], {}).get("class_type") == "GetImageSize"):
        return width[0]
    return None


def _latents(graph):
    """Empty latent nodes whose size the phone may set: plain numbers, or copied from the input photo's size."""
    return [node_id for node_id, node in graph.items()
            if re.fullmatch(r"Empty\w*LatentImage", node["class_type"])
            and (all(_plain(node["inputs"].get(f)) for f in ("width", "height")) or _photo_size(graph, node))]


def _base_size(graph, node_id):
    """The size the phone's shapes keep the pixel count of. For a photo-sized latent that is the megapixels the
    photo is scaled to (ImageScaleToTotalPixels), as a square."""
    node = graph[node_id]
    source = _photo_size(graph, node)
    if source is None:
        return {"width": node["inputs"]["width"], "height": node["inputs"]["height"]}, False
    image = graph[source]["inputs"].get("image")
    scaler = graph.get(image[0], {}) if isinstance(image, list) else {}
    megapixels = scaler.get("inputs", {}).get("megapixels") if scaler.get("class_type") == "ImageScaleToTotalPixels" else None
    side = round(math.sqrt(megapixels if isinstance(megapixels, (int, float)) and 0.25 <= megapixels <= 4 else 1) * 1024 / 64) * 64
    return {"width": side, "height": side}, True


def _saves(graph):
    return [node_id for node_id, node in graph.items()
            if node["class_type"] in SAVE_CLASSES and isinstance(node["inputs"].get("images"), list)]


DETAILERS = ("FaceDetailer", "FaceDetailerPipe")


def _detailers(graph):
    """Detailers whose only used output is the refined image, so skipping one can pass its input image through."""
    found = []
    for node_id, node in graph.items():
        if node["class_type"] not in DETAILERS or not isinstance(node["inputs"].get("image"), list):
            continue
        used = {v[1] for n in graph.values() for v in n["inputs"].values() if isinstance(v, list) and len(v) == 2 and v[0] == node_id}
        if used <= {0}:
            found.append(node_id)
    return sorted(found)


def _skip_detailers(graph, found):
    for node_id in found["detailers"]:
        source = graph.pop(node_id)["inputs"]["image"]
        for node in graph.values():
            for field, value in node["inputs"].items():
                if value == [node_id, 0]:
                    node["inputs"][field] = source


def _schema_inputs(object_info, class_type):
    schema = object_info.get(class_type, {}).get("input", {})
    return {**(schema.get("required") or {}), **(schema.get("optional") or {})}


def is_caption_node(class_type, object_info):
    if class_type in CAPTION_CLASSES:
        return True
    schema = object_info.get(class_type) or {}
    if not CAPTION_WORDS.search(f"{class_type} {schema.get('category', '')}"):
        return False
    if str(schema.get("category", "")).startswith("partner/"):
        return False
    outputs = schema.get("output") or []
    takes_image = any(isinstance(d, list) and d and d[0] == "IMAGE" for d in _schema_inputs(object_info, class_type).values())
    return takes_image and bool(outputs) and outputs[0] == "STRING"


def _tooltip(definition):
    extra = definition[1] if isinstance(definition, list) and len(definition) > 1 and isinstance(definition[1], dict) else {}
    tip = extra.get("tooltip")
    return tip[:300] if isinstance(tip, str) else None


def _captions(graph, object_info):
    found = []
    for node_id, node in graph.items():
        if not is_caption_node(node["class_type"], object_info):
            continue
        inputs, values, options, tips = node["inputs"], {}, {}, {}
        for field, definition in _schema_inputs(object_info, node["class_type"]).items():
            if CAPTION_SKIP.search(field) or field not in inputs:
                continue
            value = inputs[field]
            choices = list(_enum_options(definition) or []) if isinstance(definition, list) and definition else []
            if isinstance(value, str) and choices:
                values[field], options[field] = value, choices
            elif isinstance(value, bool):
                values[field] = value
            else:
                continue
            if _tooltip(definition):
                tips[field] = _tooltip(definition)
        if values:
            style = next((f for f in CAPTION_STYLES if f in options), None)
            found.append({"node": node_id, "class": node["class_type"], "values": values, "options": options,
                          "tips": tips, "style": style})
    return found


VIDEO_LATENTS = ("WanImageToVideo", "Wan22ImageToVideoLatent", "WanFirstLastFrameToVideo", "EmptyLTXVLatentVideo",
                 "EmptyHunyuanLatentVideo")
LENGTH_EXPR = re.compile(r"(?:floor)?\(?a\*b\+1\)?")  # frames = seconds * fps + 1
SIDE_EXPR = re.compile(r"a(?:/\d+)?")  # a side set directly, or a first pass at a fraction of it (LTX)
MAX_SECONDS = 20
# Wan 2.x is trained on 5 s clips (81 frames at 16 fps, 121 at 24). Longer ones loop, and attention memory grows with
# the square of the frame count: 10 s of a 14B model on a 16 GB card stalled ComfyUI instead of failing.
WAN_MAX_SECONDS = 5


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _primitive(graph, value):
    node = graph.get(value[0]) if isinstance(value, list) and len(value) == 2 else None
    if node and node["class_type"] in ("PrimitiveInt", "PrimitiveFloat") and _numeric(node["inputs"].get("value")):
        return value[0]
    return None


def _expression(graph, value, pattern):
    """The primitives a math expression of the given form reads as `a` and `b`, or None."""
    node = graph.get(value[0]) if isinstance(value, list) and len(value) == 2 else None
    if not node or node["class_type"] != "ComfyMathExpression":
        return None
    if not pattern.fullmatch(re.sub(r"\s+", "", str(node["inputs"].get("expression", "")))):
        return None
    return _primitive(graph, node["inputs"].get("values.a")), _primitive(graph, node["inputs"].get("values.b"))


def _video(graph):
    """Length and size of the single video latent, traced back to the workflow's own seconds, fps and size
    primitives, so everything else wired to them (audio latent, frame rate, second pass) stays in step."""
    latents = [node_id for node_id, node in graph.items() if node["class_type"] in VIDEO_LATENTS]
    if len(latents) != 1:
        return None
    inputs = graph[latents[0]]["inputs"]
    found = {"node": latents[0]}
    length = _expression(graph, inputs.get("length"), LENGTH_EXPR)
    if length and all(length):
        seconds = graph[length[0]]["inputs"]["value"]
        wan = graph[latents[0]]["class_type"].startswith("Wan")
        found.update(seconds_at=[length[0], "value"], seconds=seconds, fps=graph[length[1]]["inputs"]["value"],
                     max_seconds=max(WAN_MAX_SECONDS, int(seconds)) if wan else MAX_SECONDS)
    sides = [_expression(graph, inputs.get(f), SIDE_EXPR) for f in ("width", "height")]
    photo = _photo_size(graph, graph[latents[0]])
    if all(side and side[0] for side in sides) and sides[0][0] != sides[1][0]:
        found.update(width_at=[sides[0][0], "value"], height_at=[sides[1][0], "value"])
    elif all(_plain(inputs.get(f)) for f in ("width", "height")):
        found.update(width_at=[latents[0], "width"], height_at=[latents[0], "height"])
    elif photo:
        image = graph[photo]["inputs"].get("image")
        scaler = image[0] if isinstance(image, list) else None
        if graph.get(scaler, {}).get("class_type") == "ImageScaleToTotalPixels" and _numeric(graph[scaler]["inputs"].get("megapixels")):
            found.update(megapixels_at=[scaler, "megapixels"], megapixels=graph[scaler]["inputs"]["megapixels"])
    if "width_at" in found:
        found.update(width=graph[found["width_at"][0]]["inputs"][found["width_at"][1]],
                     height=graph[found["height_at"][0]]["inputs"][found["height_at"][1]])
    return found if len(found) > 1 else None


def _apply_video(graph, found, video):
    control = found["video"]
    if not control:
        raise ControlError("This workflow has no video length or size to change")
    editable = {key for key in ("seconds", "width", "height", "megapixels") if key + "_at" in control}
    if set(video) - editable:
        raise ControlError(f"This workflow's video can't change: {', '.join(sorted(set(video) - editable))}")

    def put(key, value):
        node_id, field = control[key + "_at"]
        current = graph[node_id]["inputs"][field]
        graph[node_id]["inputs"][field] = float(value) if isinstance(current, float) else value

    if "seconds" in video:
        seconds = video["seconds"]
        top = control.get("max_seconds", MAX_SECONDS)
        if "seconds_at" not in control or not _numeric(seconds) or seconds != int(seconds) or not 1 <= seconds <= top:
            raise ControlError(f"Video length must be 1-{top} whole seconds for this model")
        put("seconds", int(seconds))
    if "width" in video or "height" in video:
        width, height = video.get("width"), video.get("height")
        if "width_at" not in control:
            raise ControlError("This workflow sizes its video from the input image; change the quality instead")
        width, height = (int(v) if _numeric(v) and v == int(v) else v for v in (width, height))
        for side in (width, height):
            if not _plain(side) or not 256 <= side <= MAX_SIDE or side % 8:
                raise ControlError(f"Video width and height must be multiples of 8 between 256 and {MAX_SIDE}")
        if width * height > MAX_PIXELS:
            raise ControlError("Video size is too large")
        put("width", width)
        put("height", height)
    if "megapixels" in video:
        megapixels = video["megapixels"]
        if "megapixels_at" not in control or not _numeric(megapixels) or not 0.2 <= megapixels <= 1.5:
            raise ControlError("Video quality must be between 0.2 and 1.5 megapixels")
        put("megapixels", round(float(megapixels), 2))


def _negative_prompt(graph):
    """Find a single identifiable unlinked CLIPTextEncode feeding into a sampler negative socket."""
    targets = {}
    for node_id, node in graph.items():
        if "KSampler" in node.get("class_type", "") or "CFGGuider" in node.get("class_type", ""):
            neg_link = node.get("inputs", {}).get("negative")
            if isinstance(neg_link, list) and len(neg_link) == 2:
                origin_id = str(neg_link[0])
                origin_node = graph.get(origin_id)
                if origin_node and origin_node.get("class_type") == "CLIPTextEncode":
                    text = origin_node["inputs"].get("text")
                    if isinstance(text, str) and "{{prompt}}" not in text:
                        targets[origin_id] = text
    if len(targets) == 1:
        nid, default_txt = next(iter(targets.items()))
        return {"node": nid, "default": default_txt}
    return None


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
    size, from_photo = _base_size(graph, latents[0]) if latents else (None, False)
    lora_options = _options(object_info, "LoraLoaderModelOnly", "lora_name") or _options(object_info, "LoraLoader", "lora_name")
    loras += _syntax_loras(graph, lora_options)
    # Same family rules as the settings check, so the phone's grouping and its warnings agree.
    names = {name for m in models for name in m["options"]} | set(lora_options)
    families = {name: LABELS[fam] for name in names if (fam := family(name))}
    saves = bool(_saves(graph))
    found = {"models": models, "loras": loras, "lora_options": lora_options, "families": families,
            "can_add_lora": _lora_anchor(graph, models) is not None,
            "samplers": _samplers(graph), "size": size, "size_from_photo": from_photo, "captions": _captions(graph, object_info),
            "can_fit": saves and "ImageScale" in object_info,
            "can_crop": saves and all(node in object_info for node in CROP_NODES),
            "detailers": _detailers(graph), "video": _video(graph),
            "upscale_options": _options(object_info, "UpscaleModelLoader", "model_name"),
            "sampler_options": _options(object_info, "KSampler", "sampler_name"),
            "scheduler_options": _options(object_info, "KSampler", "scheduler"), "negative_prompt": _negative_prompt(graph)}
    # Everything else the workflow lets you set, so new nodes added on the PC show up on the phone too.
    found["settings"] = node_settings.find(graph, object_info, _owned(found, graph))
    return found


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


CROP_NODES = ("GetImageSize", "ComfyMathExpression", "ImageCrop")


def _apply_crop(graph, aspect):
    """Center-crop every saved image to an aspect ratio, trimming only the long side, at whatever size the
    workflow produced (a hires pass makes it bigger than the latent, so the crop is computed at run time)."""
    a, b = aspect
    for i, save in enumerate(_saves(graph), 1):
        source = graph[save]["inputs"]["images"]
        size, w, h = f"mobile_crop_size_{i}", [f"mobile_crop_size_{i}", 0], [f"mobile_crop_size_{i}", 1]
        graph[size] = {"class_type": "GetImageSize", "inputs": {"image": source}}
        for name, expression, values in (("w", f"min(a, b * {a} // {b})", (w, h)), ("h", f"min(b, a * {b} // {a})", (w, h)),
                                         ("x", "(a - b) // 2", (w, [f"mobile_crop_w_{i}", 1])),
                                         ("y", "(a - b) // 2", (h, [f"mobile_crop_h_{i}", 1]))):
            graph[f"mobile_crop_{name}_{i}"] = {"class_type": "ComfyMathExpression", "inputs": {
                "expression": expression, "values.a": values[0], "values.b": values[1]}}
        graph[f"mobile_crop_{i}"] = {"class_type": "ImageCrop", "inputs": {"image": source,
            **{key: [f"mobile_crop_{key[0]}_{i}", 1] for key in ("width", "height", "x", "y")}}}
        graph[save]["inputs"]["images"] = [f"mobile_crop_{i}", 0]


def _apply_fit(graph, found, fit, object_info):
    """Fit every saved image to a phone screen.

    {"width", "height"}: optional model upscale, then lanczos scale-to-fill with a center crop, to an exact size.
    {"aspect": [w, h]}: center crop to that shape at the image's own size.
    The workflow still renders near its own pixel count, which is what the model was trained for; rendering a
    2.6 MP phone screen in one pass tends to duplicate subjects."""
    if not found["can_fit"]:
        raise ControlError("This workflow has no image output to fit to a screen")
    if "aspect" in fit:
        aspect = fit["aspect"]
        if (not isinstance(aspect, (list, tuple)) or len(aspect) != 2 or fit.get("width") or fit.get("height")
                or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 100 for n in aspect)
                or not 0.25 <= aspect[0] / aspect[1] <= 4):
            raise ControlError("Crop aspect must be two whole numbers between 1:4 and 4:1")
        if any(node not in object_info for node in CROP_NODES):
            raise ControlError("This ComfyUI is too old to crop to a screen shape")
        return _apply_crop(graph, aspect)
    width, height, upscaler = fit.get("width"), fit.get("height"), fit.get("upscaler")
    for side in (width, height):
        if isinstance(side, bool) or not isinstance(side, int) or not 256 <= side <= FIT_MAX_SIDE:
            raise ControlError(f"Screen width and height must be between 256 and {FIT_MAX_SIDE}")
    if width * height > FIT_MAX_PIXELS:
        raise ControlError("Screen size is too large")
    if upscaler is not None and upscaler not in found["upscale_options"]:
        raise ControlError(f"Upscale model not found in ComfyUI: {upscaler}")
    if upscaler is not None:
        graph["mobile_upscale_model"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscaler}}
    for i, save in enumerate(_saves(graph), 1):
        source = graph[save]["inputs"]["images"]
        if upscaler is not None:
            graph[f"mobile_upscale_{i}"] = {"class_type": "ImageUpscaleWithModel",
                                            "inputs": {"upscale_model": ["mobile_upscale_model", 0], "image": source}}
            source = [f"mobile_upscale_{i}", 0]
        graph[f"mobile_fit_{i}"] = {"class_type": "ImageScale", "inputs": {
            "image": source, "upscale_method": "lanczos", "width": width, "height": height, "crop": "center"}}
        graph[save]["inputs"]["images"] = [f"mobile_fit_{i}", 0]


def apply_controls(graph: dict, object_info: dict, models: dict, loras: dict, extra_loras: list,
                   sampling: dict | None = None, size: dict | None = None, caption: dict | None = None,
                   fit: dict | None = None, skip_detailers: bool = False, video: dict | None = None,
                   negative_prompt: str | None = None, settings: dict | None = None) -> None:
    """Apply phone choices in place. Only nodes and options found by find_controls are accepted."""
    if not (models or loras or extra_loras or sampling or size or caption or fit or skip_detailers or video
            or negative_prompt is not None or settings):
        return
    found = find_controls(graph, object_info)
    if settings:
        # First, while every node is still there (skipping detailers removes some).
        try:
            node_settings.apply(graph, found["settings"], settings)
        except node_settings.SettingsError as exc:
            raise ControlError(str(exc)) from exc
    if skip_detailers:
        if not found["detailers"]:
            raise ControlError("This workflow has no face detailer to skip")
        _skip_detailers(graph, found)
        found = find_controls(graph, object_info)
    if fit:
        _apply_fit(graph, found, fit, object_info)
    if sampling:
        _apply_sampling(graph, found, sampling)
    if caption:
        _apply_caption(graph, found, caption)
    if size:
        _apply_size(graph, size)
    if video:
        _apply_video(graph, found, video)
    if negative_prompt is not None and found.get("negative_prompt"):
        neg_node = found["negative_prompt"]["node"]
        if neg_node in graph and "inputs" in graph[neg_node] and "text" in graph[neg_node]["inputs"]:
            graph[neg_node]["inputs"]["text"] = negative_prompt
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
    syntax = {}
    for node_id, choice in loras.items():
        if node_id not in by_lora:
            raise ControlError(f"Node {node_id} is not a LoRA loader in this workflow")
        if choice["name"] not in lora_options:
            raise ControlError(f"LoRA not found in ComfyUI: {choice['name']}")
        if "syntax" in by_lora[node_id]:
            syntax[node_id] = choice
            continue
        inputs = graph[node_id]["inputs"]
        strength = _strength(choice["strength"])
        # The CLIP strength follows the model strength when they matched; turning a LoRA off zeroes both,
        # which makes ComfyUI skip loading it.
        if "strength_clip" in inputs and (inputs["strength_clip"] == inputs["strength_model"] or strength == 0):
            inputs["strength_clip"] = strength
        inputs["lora_name"], inputs["strength_model"] = choice["name"], strength
    if syntax:
        _apply_syntax_loras(graph, by_lora, syntax)
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


def run_details(graph, object_info):
    """Small snapshot of effective settings; omit the potentially huge option catalogs."""
    found = find_controls(graph, object_info)
    return {
        'models': [{key: model[key] for key in ('node', 'field', 'value')} for model in found['models']],
        'loras': [{key: lora[key] for key in ('node', 'value', 'strength')} for lora in found['loras']],
        'samplers': found['samplers'], 'size': found['size'], 'video': found['video'],
        'captions': [{'node': caption['node'], 'values': caption['values']} for caption in found['captions']],
        'negative_prompt': found.get('negative_prompt', {}).get('default') if found.get('negative_prompt') else None,
    }
