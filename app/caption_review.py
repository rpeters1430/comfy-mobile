"""Split supported VLM caption workflows into preview and approved rendering graphs."""
import copy
try:
    from app.workflow_controls import CAPTION_CLASSES
except ImportError:
    from workflow_controls import CAPTION_CLASSES


class CaptionError(ValueError):
    pass


def link(value):
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)


def capability(graph, info):
    nodes = [key for key, node in graph.items() if node['class_type'] in CAPTION_CLASSES]
    if len(nodes) != 1:
        return {'supported': False, 'reason': 'Review needs exactly one supported caption node.'}
    key = nodes[0]
    outputs = info.get(graph[key]['class_type'], {}).get('output', [])
    if not outputs or outputs[0] != 'STRING':
        return {'supported': False, 'reason': 'Caption text output schema is unavailable.'}
    slots = [0] + ([1] if len(outputs) > 1 and outputs[1] == 'STRING' else [])
    consumers = [value[1] for node in graph.values() for value in node['inputs'].values() if link(value) and value[0] == key]
    if 0 not in consumers or any(slot not in slots for slot in consumers):
        return {'supported': False, 'reason': 'Caption dimensions or non-text outputs are used; review cannot safely bypass this node.'}
    preview = info.get('PreviewAny', {})
    if not preview.get('output_node') or 'source' not in preview.get('input', {}).get('required', {}):
        return {'supported': False, 'reason': 'Caption review needs the PreviewAny output node on ComfyUI.'}
    return {'supported': True, 'node': key, 'slots': slots}


def ancestors(graph, roots):
    keep, pending = set(), list(roots)
    while pending:
        key = pending.pop()
        if key in keep:
            continue
        if key not in graph:
            raise CaptionError('Caption graph contains a missing dependency')
        keep.add(key)
        pending.extend(v[0] for v in graph[key]['inputs'].values() if link(v))
    return {key: copy.deepcopy(node) for key, node in graph.items() if key in keep}


def caption_graph(graph, info):
    cap = capability(graph, info)
    if not cap['supported']:
        raise CaptionError(cap['reason'])
    result = ancestors(graph, [cap['node']])
    # Reject unexpected upstream output/sampler branches rather than run expensive work in a preview.
    if any(node['class_type'] in ('KSampler', 'KSamplerAdvanced', 'SamplerCustom', 'SamplerCustomAdvanced')
           or (key != cap['node'] and info.get(node['class_type'], {}).get('output_node'))
           for key, node in result.items()):
        raise CaptionError('Caption depends on rendering or output nodes; preview is unsafe')
    for slot in cap['slots']:
        key = 'mobile_review_' + ('positive' if slot == 0 else 'negative')
        if key in graph: raise CaptionError('Reserved caption-review node ID is already used')
        result[key] = {'class_type': 'PreviewAny', 'inputs': {'source': [cap['node'], slot]}}
    return result


def apply_review(graph, info, positive, negative):
    cap = capability(graph, info)
    if not cap['supported']:
        raise CaptionError(cap['reason'])
    result = copy.deepcopy(graph)
    for node in result.values():
        for field, value in node['inputs'].items():
            if link(value) and value[0] == cap['node']:
                node['inputs'][field] = positive if value[1] == 0 else negative
    roots = [key for key, node in result.items() if key != cap['node'] and info.get(node['class_type'], {}).get('output_node')]
    if not roots: raise CaptionError('Workflow has no render outputs')
    return ancestors(result, roots)
