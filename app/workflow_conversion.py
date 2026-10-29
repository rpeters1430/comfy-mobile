"""Conservative LiteGraph UI -> ComfyUI API conversion.

Never guess subgraph wiring or widget values: unsupported graphs fail closed.
"""
import copy


class ConversionError(ValueError):
    pass


def convert_ui(data, object_info):
    if not isinstance(data.get("nodes"), list):
        return copy.deepcopy(data)
    nodes = data["nodes"]
    links = {}
    for link in data.get("links", []):
        if isinstance(link, list) and len(link) >= 5:
            links[link[0]] = (str(link[1]), int(link[2]), str(link[3]), int(link[4]))
        elif isinstance(link, dict):
            links[link["id"]] = (str(link["origin_id"]), int(link["origin_slot"]), str(link["target_id"]), int(link["target_slot"]))
    result = {}
    skipped = {"MarkdownNote", "Note", "Reroute"}
    for node in nodes:
        kind = node.get("type")
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
        widget_values = node.get("widgets_values", [])
        if isinstance(widget_values, dict):
            widget_map = widget_values
        elif isinstance(widget_values, list):
            widget_fields = [k for k, v in fields.items() if isinstance(v, list) and len(v) > 1 and isinstance(v[1], dict) and "defaultInput" not in v[1]]
            widget_map = dict(zip(widget_fields, widget_values))
        else:
            widget_map = {}
        for item in node.get("inputs", []):
            field = item.get("name")
            lid = item.get("link")
            if lid is not None:
                entry = links.get(lid)
                if not entry or entry[2] != str(node["id"]):
                    raise ConversionError(f"Broken link on {node['id']}.{field}")
                inputs[field] = [entry[0], entry[1]]
        for field in fields:
            if field not in inputs and field in widget_map:
                inputs[field] = widget_map[field]
        for field in spec.get("required", {}):
            if field not in inputs:
                raise ConversionError(f"Missing required value for {kind}.{field}")
        result[str(node["id"])] = {"class_type": kind, "inputs": inputs}
    if not result:
        raise ConversionError("No executable nodes")
    # Never submit dangling connections.
    for node in result.values():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0].isdigit():
                if value[0] not in result:
                    raise ConversionError("Link references a skipped or missing node")
    return result
