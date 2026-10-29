"""Conservative LiteGraph UI -> ComfyUI API conversion.

Never guess subgraph wiring or widget values: unsupported graphs fail closed.
"""
import copy
import json


class ConversionError(ValueError):
    pass


def _enum_options(definition):
    """Choices for a combo input: legacy `[[...]]` or V3 `["COMBO", {"options": [...]}]`."""
    if isinstance(definition[0], list):
        return definition[0]
    if definition[0] == "COMBO" and len(definition) > 1 and isinstance(definition[1], dict):
        return definition[1].get("options")
    return None


def _resolve_choice(value, options):
    """Map a bare model filename onto the unique subfolder entry ComfyUI lists for it."""
    if value in options or not isinstance(value, str) or "." not in value:
        return value
    base = value.replace("\\", "/").rsplit("/", 1)[-1]
    matches = [o for o in options if isinstance(o, str) and o.replace("\\", "/").rsplit("/", 1)[-1] == base]
    return matches[0] if len(matches) == 1 else value


WIDGET_TYPES = ("STRING", "INT", "FLOAT", "BOOLEAN", "COMBO")
DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"
AUTOGROW = "COMFY_AUTOGROW_V3"
SUBGRAPH_INPUT, SUBGRAPH_OUTPUT = "-10", "-20"


def _map_widgets(fields, values):
    """Map widgets_values onto inputs in schema order.

    ComfyUI keeps a slot for every widget, even when it is linked, and serializes a
    control widget (randomize/fixed) after seed-like INTs. A V3 dynamic combo is followed
    by its chosen option's sub-widgets, sent to the API as `<combo>.<sub>` keys.
    Returns (values by key, schema of the chosen dynamic sub-inputs, their required keys).
    """
    mapped, extra, required = {}, {}, []
    pos = 0

    def walk(definitions, prefix, nested):
        nonlocal pos
        for name, definition in definitions:
            if not (isinstance(definition, list) and definition):
                continue
            dtype = definition[0]
            if not (dtype in WIDGET_TYPES or dtype == DYNAMIC_COMBO or isinstance(dtype, list)):
                continue
            if pos >= len(values):
                return
            key = prefix + name
            mapped[key] = values[pos]
            pos += 1
            options = definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
            if dtype == "INT" and options.get("control_after_generate", not nested and name in ("seed", "noise_seed")):
                pos += 1
            if dtype == DYNAMIC_COMBO:
                chosen = next((o for o in options.get("options", []) if o.get("key") == mapped[key]), None)
                if chosen is None:
                    raise ConversionError(f"{key}: saved option {mapped[key]!r} is not available in ComfyUI")
                sub = chosen.get("inputs", {})
                for section in ("required", "optional"):
                    for sub_name, sub_def in sub.get(section, {}).items():
                        extra[f"{key}.{sub_name}"] = sub_def
                        if section == "required":
                            required.append(f"{key}.{sub_name}")
                walk(list(sub.get("required", {}).items()) + list(sub.get("optional", {}).items()), key + ".", True)

    walk(list(fields.items()), "", False)
    return mapped, extra, required


def _parse_links(raw):
    links = {}
    for link in raw or []:
        if isinstance(link, list) and len(link) >= 5:
            links[link[0]] = (str(link[1]), int(link[2]), str(link[3]), int(link[4]))
        elif isinstance(link, dict):
            links[link["id"]] = (str(link["origin_id"]), int(link["origin_slot"]), str(link["target_id"]), int(link["target_slot"]))
    return links


def _repair_links(nodes, links):
    """Restore links missing from the links array when both ends still reference them.

    ComfyUI's link validator repairs this on load; only an unambiguous single origin and target is accepted.
    """
    origins, targets = {}, {}
    for node in nodes:
        for slot, out in enumerate(node.get("outputs") or []):
            for lid in out.get("links") or []:
                origins.setdefault(lid, []).append((str(node.get("id")), slot))
        for slot, item in enumerate(node.get("inputs") or []):
            if item.get("link") is not None:
                targets.setdefault(item["link"], []).append((str(node.get("id")), slot))
    for lid, ends in targets.items():
        if lid not in links and len(ends) == 1 and len(origins.get(lid, [])) == 1:
            links[lid] = (*origins[lid][0], *ends[0])
    return links


def _flatten(data):
    """Inline subgraph instances. Inner node IDs become `<instance>:<inner>`, as in ComfyUI's API format.

    Instance widgets_values line up with the subgraph's widget-type inputs in order; a value
    feeding an inner widget becomes an `__overrides` entry on that inner node.
    """
    nodes = copy.deepcopy(data["nodes"])
    links = _repair_links(nodes, _parse_links(data.get("links")))
    defs = {s["id"]: s for s in (data.get("definitions") or {}).get("subgraphs", []) if isinstance(s, dict) and "id" in s}
    if not defs:
        return nodes, links
    flat = [n for n in nodes if n.get("type") not in defs]
    outputs = {}  # (instance id, output slot) -> ("link", origin, slot)
    consumed = set()
    for inst in (n for n in nodes if n.get("type") in defs):
        sg, iid = defs[inst["type"]], str(inst["id"])
        label = sg.get("name") or inst["type"]
        if inst.get("mode", 0) != 0:
            raise ConversionError(f"Bypassed or muted subgraph {label!r} requires manual conversion")
        sg_inputs = sg.get("inputs", [])
        widget_inputs = [i for i in sg_inputs if i.get("type") in WIDGET_TYPES]
        values = inst.get("widgets_values", [])
        if not isinstance(values, list) or len(values) != len(widget_inputs):
            raise ConversionError(f"Subgraph {label!r}: {len(values) if isinstance(values, list) else '?'} saved values for {len(widget_inputs)} widget inputs")
        literal = {i["name"]: v for i, v in zip(widget_inputs, values)}
        by_name = {i.get("name"): i for i in inst.get("inputs", [])}
        sources = []
        for sgi in sg_inputs:
            item = by_name.get(sgi.get("name"))
            if item and item.get("link") is not None:
                entry = links.get(item["link"])
                if not entry or entry[2] != iid:
                    raise ConversionError(f"Broken link on {iid}.{sgi.get('name')}")
                consumed.add(item["link"])
                sources.append(("link", entry[0], entry[1]))
            elif sgi.get("name") in literal:
                sources.append(("value", literal[sgi["name"]]))
            else:
                sources.append(None)
        inner = {}
        for node in copy.deepcopy(sg.get("nodes", [])):
            if node.get("type") in defs:
                raise ConversionError(f"Nested subgraph in {label!r} requires manual conversion")
            node["id"] = f"{iid}:{node['id']}"
            inner[node["id"]] = node
            flat.append(node)
        for lid, (origin, oslot, target, tslot) in _repair_links(sg.get("nodes", []), _parse_links(sg.get("links"))).items():
            key = (iid, lid)
            if target == SUBGRAPH_OUTPUT:
                if origin == SUBGRAPH_INPUT:
                    raise ConversionError(f"Subgraph {label!r} passes an input straight to an output")
                outputs[(iid, tslot)] = (f"{iid}:{origin}", oslot)
                continue
            dest = inner.get(f"{iid}:{target}")
            if dest is None or tslot >= len(dest.get("inputs", [])):
                raise ConversionError(f"Broken link inside subgraph {label!r}")
            slot = dest["inputs"][tslot]
            if origin == SUBGRAPH_INPUT:
                source = sources[oslot] if oslot < len(sources) else None
                if source and source[0] == "link":
                    links[key] = (source[1], source[2], dest["id"], tslot)
                    slot["link"] = key
                else:
                    slot["link"] = None
                    if source:
                        dest.setdefault("__overrides", {})[slot.get("name")] = source[1]
            else:
                links[key] = (f"{iid}:{origin}", oslot, dest["id"], tslot)
                slot["link"] = key
    for lid in consumed:
        links.pop(lid, None)
    instance_ids = {str(n.get("id")) for n in nodes if n.get("type") in defs}
    for lid, (origin, oslot, target, tslot) in list(links.items()):
        if origin in instance_ids:
            if (origin, oslot) not in outputs:
                raise ConversionError(f"Subgraph output {origin}.{oslot} is not connected inside the subgraph")
            links[lid] = (*outputs[(origin, oslot)], target, tslot)
    return flat, links


def convert_ui(data, object_info):
    if not isinstance(data.get("nodes"), list):
        return copy.deepcopy(data)
    data, _ = bind_prompt(data)
    nodes, links = _flatten(data)
    by_id = {str(n.get("id")): n for n in nodes}

    def resolve(origin, slot, dtype, field):
        # Follow bypassed (mode 4) nodes the way ComfyUI does: pass through the
        # same-index input if its type matches, else the first input of that type.
        for _ in range(len(nodes) + 1):
            source = by_id.get(origin)
            if source is None or source.get("mode", 0) == 0:
                return origin, slot
            if source.get("mode") == 2:
                return None  # ComfyUI drops inputs fed by muted nodes; required ones then fail below
            if source.get("mode") != 4:
                raise ConversionError(f"Unsupported mode {source.get('mode')!r} on node {origin}")
            candidates = [i for i in source.get("inputs", []) if i.get("link") is not None and i.get("type") == dtype]
            same = source.get("inputs", [])[slot] if slot < len(source.get("inputs", [])) else None
            chosen = same if same in candidates else candidates[0] if candidates else None
            if chosen is None:
                raise ConversionError(f"Bypassed node {origin} has no {dtype} input to pass through")
            entry = links.get(chosen["link"])
            if not entry:
                raise ConversionError(f"Broken link on {origin}.{chosen.get('name')}")
            origin, slot = entry[0], entry[1]
        raise ConversionError("Bypass chain does not terminate")

    result = {}
    edges = []
    skipped = {"MarkdownNote", "Note", "Reroute"}
    for node in nodes:
        kind = node.get("type")
        if node.get("mode", 0) in (2, 4):
            continue
        if kind in skipped:
            # Notes must not be used as active graph dependencies.
            if any(str(node.get("id")) == origin for origin, _, _, _ in links.values()):
                raise ConversionError("Connected note/reroute node requires manual conversion")
            continue
        if kind not in object_info:
            raise ConversionError(f"Unsupported node or unexpanded subgraph: {kind}")
        spec = object_info[kind].get("input", {})
        fields = {**spec.get("required", {}), **spec.get("optional", {})}
        inputs = {}
        dropped, linked = set(), set()
        display_only = False
        widget_values = node.get("widgets_values", [])
        required = list(spec.get("required", {}))
        if isinstance(widget_values, dict):
            widget_map = widget_values
        elif isinstance(widget_values, list):
            # Never drop or shift slots to make values fit.
            widget_map, dynamic_fields, dynamic_required = _map_widgets(fields, widget_values)
            fields = {**fields, **dynamic_fields}
            required += dynamic_required
        else:
            widget_map = {}
        for item in node.get("inputs", []):
            field = item.get("name")
            lid = item.get("link")
            if lid is not None:
                entry = links.get(lid)
                if not entry or entry[2] != str(node["id"]):
                    raise ConversionError(f"Broken link on {node['id']}.{field}")
                source = resolve(entry[0], entry[1], item.get("type"), field)
                if source is None:
                    dropped.add(field)
                    continue
                inputs[field] = list(source)
                linked.add(field)
                edges.append(inputs[field][0])
        for field, value in node.get("__overrides", {}).items():
            definition = fields.get(field)
            # Sub-widgets were read for the saved option; a different one from outside can't be mapped.
            if isinstance(definition, list) and definition and definition[0] == DYNAMIC_COMBO and value != widget_map.get(field):
                raise ConversionError(f"{kind}.{field}: option set from outside the subgraph requires manual conversion")
            inputs.setdefault(field, value)
        for field in fields:
            if field not in inputs and field not in dropped and field in widget_map:
                inputs[field] = widget_map[field]
        for field in required:
            definition = fields.get(field)
            if isinstance(definition, list) and definition and definition[0] == AUTOGROW:
                template = definition[1].get("template", {}) if len(definition) > 1 and isinstance(definition[1], dict) else {}
                if sum(1 for k in inputs if k.startswith(field + ".")) < template.get("min", 0):
                    raise ConversionError(f"{kind}.{field}: needs at least {template.get('min')} connected inputs")
                continue
            if field in dropped:
                raise ConversionError(f"{kind}.{field}: required input comes from a muted node")
            if field not in inputs and isinstance(definition, list) and len(definition) > 1 and isinstance(definition[1], dict) and "default" in definition[1]:
                inputs[field] = definition[1]["default"]
            # A required socketless display widget (e.g. ImageCompare.compare_view) can't be supplied
            # from the API; drop the preview node. Anything depending on it fails the dangling-link check.
            if field not in inputs and isinstance(definition, list) and len(definition) > 1 and isinstance(definition[1], dict) and definition[1].get("socketless"):
                display_only = True
                continue
            if field not in inputs:
                raise ConversionError(f"Missing required value for {kind}.{field}")
        # Reject values which are provably incompatible with ComfyUI schema.
        for field, value in inputs.items():
            definition = fields.get(field)
            if not isinstance(definition, list) or not definition or isinstance(value, list):
                continue
            dtype = definition[0]
            choices = _enum_options(definition)
            if kind == "LoadImage" and field == "image":
                continue  # filled with an uploaded file at job time; see image_inputs()
            if choices is not None:
                value = inputs[field] = _resolve_choice(value, choices)
                if value not in choices:
                    raise ConversionError(f"{kind}.{field}: saved value {value!r} is not available in ComfyUI")
            if dtype == "INT" and (isinstance(value, bool) or not isinstance(value, int)):
                raise ConversionError(f"{kind}.{field}: expected integer, got {value!r}")
            if dtype == "FLOAT" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ConversionError(f"{kind}.{field}: expected number, got {value!r}")
        if display_only:
            continue
        # A bare list means a link to ComfyUI; list widget values are sent wrapped, as its frontend does.
        for field, value in inputs.items():
            if isinstance(value, list) and field not in linked:
                inputs[field] = {"__value__": value}
        result[str(node["id"])] = {"class_type": kind, "inputs": inputs}
    if not result:
        raise ConversionError("No executable nodes")
    # Never submit dangling connections.
    if any(origin not in result for origin in edges):
        raise ConversionError("Link references a skipped or missing node")
    return result


def _prompt_targets(data):
    """Top-level places a typed prompt can go, as (node, widgets_values index).

    A linked text input (e.g. from a captioner) is never a target, and neither is anything
    titled negative or a prefix/trigger field that auto-caption workflows prepend.
    """
    defs = {s["id"]: s for s in (data.get("definitions") or {}).get("subgraphs", []) if isinstance(s, dict) and "id" in s}
    targets = []
    for node in data["nodes"]:
        if node.get("mode", 0) != 0 or not isinstance(node.get("widgets_values"), list):
            continue
        linked = {i.get("name") for i in node.get("inputs", []) if i.get("link") is not None}
        title = str(node.get("title") or "").lower()
        if node.get("type") == "CLIPTextEncode" and "text" not in linked:
            if "negative" not in title and "neg prompt" not in title:
                targets.append((node, 0))
        elif node.get("type") in ("PrimitiveString", "PrimitiveStringMultiline") and "value" not in linked:
            if "prompt" in title and not any(w in title for w in ("negative", "prefix", "trigger")):
                targets.append((node, 0))
        elif node.get("type") in defs:
            widget_inputs = [i for i in defs[node["type"]].get("inputs", []) if i.get("type") in WIDGET_TYPES]
            for index, sgi in enumerate(widget_inputs):
                names = {str(sgi.get("name", "")).lower(), str(sgi.get("label", "")).lower()}
                if sgi.get("type") == "STRING" and sgi.get("name") not in linked and names & {"prompt", "positive_prompt"}:
                    targets.append((node, index))
    return targets


def bind_prompt(data):
    """Return a copy of a UI graph with `{{prompt}}` in its single prompt field, and whether it takes one.

    Graphs with no prompt field (e.g. auto-caption workflows) are valid and take no prompt.
    """
    data = copy.deepcopy(data)
    if "{{prompt}}" in json.dumps(data):
        return data, True
    targets = _prompt_targets(data)
    if len(targets) > 1:
        raise ConversionError("Cannot identify a unique positive prompt node; add {{prompt}} in ComfyUI")
    if not targets:
        return data, False
    node, index = targets[0]
    if index >= len(node["widgets_values"]):
        raise ConversionError(f"Prompt field on node {node.get('id')} has no saved value slot")
    node["widgets_values"][index] = "{{prompt}}"
    return data, True


def image_inputs(graph):
    """LoadImage nodes a job must fill with uploaded files, in node order."""
    def order(key):
        return [int(p) if p.isdigit() else p for p in key.split(":")]
    return sorted((k for k, n in graph.items() if n.get("class_type") == "LoadImage"), key=order)
