"""Name known anime/game characters in auto-caption workflows.

A Booru tagger (trained on Danbooru's character tags) looks at the same photo as the caption node, and the
characters it is confident about go into the caption node's `character_name` input, so the caption, and with it
the prompt, refers to them by name ("Tifa Lockhart standing in…"). Added at run time, so PC workflows need no
edit; a workflow that already wires or types a character name is left alone.

It only recognises fictional characters from the tagger's tag list. It does not identify real people.
"""
if __package__:
    from app.workflow_controls import is_caption_node
else:  # flat imports inside the Docker image
    from workflow_controls import is_caption_node

LOADER, TAGGER = "Load Booru Tagger", "Booru Tagger"
# PixAI v1.0 (what the bundled workflows used) first: per-tag best thresholds and a large character list.
PREFERRED_MODELS = ("pixai-tagger-v1.0-fp16", "pixai-tagger-v1.0-fp32", "wd-eva02-large-tagger-v3", "wd-vit-large-tagger-v3")
# The bundled workflows' settings: a low general threshold (tags are not used) and a character threshold that,
# with PixAI's per-tag best thresholds, names clear likenesses and cosplay without guessing on every face.
SETTINGS = {"threshold": 0.35, "character_threshold": 0.45, "use_best_threshold": True, "trailing_comma": False,
            "sort_tags": False, "exclude_tags": "", "chunk_size": 0, "replace_underscore": True}
TAGGER_ID, LOADER_ID, PREVIEW_ID = "mobile_tagger", "mobile_tagger_model", "mobile_characters"


def _link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)


def _models(info):
    from_schema = (info.get(LOADER, {}).get("input", {}).get("required") or {}).get("model_name")
    if not from_schema:
        return []
    head = from_schema[0]
    options = head if isinstance(head, list) else (from_schema[1].get("options") if len(from_schema) > 1 and isinstance(from_schema[1], dict) else None)
    return [o for o in options or [] if isinstance(o, str)]


def _slot(info):
    names = info.get(TAGGER, {}).get("output_name") or []
    return names.index("character_tags") if "character_tags" in names else None


def support(graph: dict, info: dict) -> dict:
    """Whether this workflow can name characters, and on which caption node."""
    if TAGGER not in info or not _models(info) or _slot(info) is None:
        return {"supported": False, "reason": "The Booru Tagger nodes are not installed on ComfyUI."}
    nodes = [key for key, node in graph.items() if is_caption_node(node["class_type"], info)
             and "character_name" in (info.get(node["class_type"], {}).get("input", {}).get("required") or {})]
    if len(nodes) != 1:
        return {"supported": False, "reason": "Needs exactly one caption node with a character name input."}
    inputs = graph[nodes[0]]["inputs"]
    if not _link(inputs.get("image")):
        return {"supported": False, "reason": "The caption node has no input photo."}
    if _link(inputs.get("character_name")) or str(inputs.get("character_name") or "").strip():
        return {"supported": False, "reason": "This workflow already sets the character name itself."}
    if any(key in graph for key in (TAGGER_ID, LOADER_ID, PREVIEW_ID)):
        return {"supported": False, "reason": "Reserved character tagger node ID is already used."}
    model = next((m for m in PREFERRED_MODELS if m in _models(info)), _models(info)[0])
    return {"supported": True, "node": nodes[0], "model": model}


def add_tagger(graph: dict, info: dict) -> bool:
    """Wire a tagger's character names into the caption node. False when the workflow can't take it."""
    found = support(graph, info)
    if not found["supported"]:
        return False
    caption = graph[found["node"]]["inputs"]
    graph[LOADER_ID] = {"class_type": LOADER, "inputs": {"model_name": found["model"]}}
    schema = info[TAGGER].get("input", {}).get("required") or {}
    inputs = {"tagger_model": [LOADER_ID, 0], "tagger_info": [LOADER_ID, 1], "image": caption["image"]}
    for field, definition in schema.items():
        if field in inputs:
            continue
        if field in SETTINGS:
            inputs[field] = SETTINGS[field]
        elif isinstance(definition, list) and len(definition) > 1 and isinstance(definition[1], dict) and "default" in definition[1]:
            inputs[field] = definition[1]["default"]
    graph[TAGGER_ID] = {"class_type": TAGGER, "inputs": inputs}
    caption["character_name"] = [TAGGER_ID, _slot(info)]
    return True


def add_preview(graph: dict, info: dict) -> None:
    """Show the recognised names on the job card, if the tagger is still part of this run's graph."""
    if TAGGER_ID in graph and "PreviewAny" in info:
        graph[PREVIEW_ID] = {"class_type": "PreviewAny", "inputs": {"source": [TAGGER_ID, _slot(info)]}}
