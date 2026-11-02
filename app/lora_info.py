"""Trigger words, recommended strength and model family for each LoRA, from ModelHarbor's Civitai matches.

ModelHarbor inventories the ComfyUI PC by absolute path; ComfyUI names a LoRA by its path under models/loras, so
records are matched on that tail, then by file name when it is unique."""
import re

try:
    from .settings_check import civitai_family
except ImportError:  # flat imports in the Docker image
    from settings_check import civitai_family

MAX_TRIGGERS = 20


def _rel(path):
    """`SDXL/foo.safetensors` for any path inside a `loras` folder, lowercased; None otherwise."""
    parts = re.split(r'[\\/]+', str(path).lower())
    if 'loras' not in parts:
        return None
    return '/'.join(parts[len(parts) - parts[::-1].index('loras'):])


def _entry(record):
    source = record.get('source') or {}
    if source.get('provider') != 'civitai':
        return None
    triggers = [str(w).strip()[:200] for w in source.get('trained_words') or [] if str(w).strip()][:MAX_TRIGGERS]
    weight = source.get('weight')
    weight = float(weight) if isinstance(weight, (int, float)) and 0 < weight <= 2 else None
    return {'family': civitai_family(source.get('base_model') or ''), 'base_model': source.get('base_model') or None,
            'triggers': triggers, 'weight': weight, 'url': source.get('url') or None}


def match(names, records):
    """{name: entry} for the ComfyUI LoRA names ModelHarbor has a Civitai match for."""
    by_path, by_file = {}, {}
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict) or not str(record.get('device', '')).endswith(':comfyui'):
            continue
        rel, entry = _rel(record.get('path', '')), _entry(record)
        if rel and entry:
            by_path[rel] = entry
            # Hard-linked copies (loose and in a family folder) are one file: keyed by hash.
            by_file.setdefault(rel.rsplit('/', 1)[-1], {})[record.get('sha256') or rel] = entry
    out = {}
    for name in names:
        key = re.sub(r'[\\/]+', '/', name.lower())
        found = by_path.get(key)
        if not found:
            same = list(by_file.get(key.rsplit('/', 1)[-1], {}).values())
            found = same[0] if len(same) == 1 else None
        if found:
            out[name] = found
    return out
