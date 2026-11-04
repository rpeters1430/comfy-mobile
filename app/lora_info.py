"""Trigger words, recommended strength and model family from ModelHarbor and exact HF file sidecars.

ModelHarbor inventories the ComfyUI PC by absolute path; ComfyUI names a LoRA by its path under models/loras, so
records are matched on that tail, then by file name when it is unique."""
import asyncio
import json
import re
import time
from urllib.parse import quote, unquote, urlsplit

import httpx

try:
    from .settings_check import civitai_family
except ImportError:  # flat imports in the Docker image
    from settings_check import civitai_family

MAX_TRIGGERS = 20
_hf_cache = {}
HF_TTL = 300
MAX_SIDECAR = 64 * 1024


def _words(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(w.strip()[:200] for w in value if isinstance(w, str) and w.strip()))[:MAX_TRIGGERS]


def _rel(path):
    """`SDXL/foo.safetensors` for any path inside a `loras` folder, lowercased; None otherwise."""
    parts = re.split(r'[\\/]+', str(path).lower())
    if 'loras' not in parts:
        return None
    return '/'.join(parts[len(parts) - parts[::-1].index('loras'):])


def _entry(record):
    source = record.get('source') or {}
    if not isinstance(source, dict):
        return None
    provider = source.get('provider')
    if provider not in ('civitai', 'huggingface', None):
        return None
    local = record.get('local_meta') or {}
    triggers = _words(source.get('trained_words')) or _words(local.get('trained_words')) or _words(record.get('trigger_words'))
    base = source.get('base_model') or local.get('base_model') or record.get('base_model_guess') or ''
    if not provider and not (triggers or base):
        return None
    weight = source.get('weight') if provider == 'civitai' else None
    weight = float(weight) if isinstance(weight, (int, float)) and 0 < weight <= 2 else None
    return {'family': civitai_family(base), 'base_model': base or None, 'source': provider or 'embedded',
            'triggers': triggers, 'weight': weight, 'url': source.get('url') or None}


def sidecar_url(url):
    """Only the .md beside a specific Hugging Face weight file; never a repo-wide README."""
    try:
        parsed = urlsplit(url or '')
        parts = unquote(parsed.path).split('/')
        if (parsed.scheme != 'https' or parsed.netloc != 'huggingface.co' or parsed.query or parsed.fragment
                or len(parts) < 6 or parts[3] != 'blob' or not parts[-1].endswith('.safetensors')
                or any(not p or p in ('.', '..') or '\\' in p for p in parts[1:])):
            return None
        parts[3] = 'raw'
        parts[-1] = parts[-1][:-len('.safetensors')] + '.md'
        return 'https://huggingface.co' + quote('/'.join(parts), safe='/')
    except (TypeError, ValueError):
        return None


def sidecar_triggers(text):
    # Explicit author-labelled phrases only; persona descriptions and file names are not trigger words.
    values = []
    for line in text.splitlines():
        match = re.match(r'^\s*(?:[-*]\s+)?(?:\*\*)?Trigger\s+(?:words?|phrase)(?:\*\*)?\s*:(?:\*\*)?\s*(.+?)\s*$', line, re.I)
        if match:
            values.extend(word.strip(' `*\t') for word in match[1].split(','))
    return _words(values)


async def enrich_huggingface(entries, client):
    """Fill missing HF triggers from bounded, cached, per-file persona documents. Failure is optional."""
    urls = {url for entry in entries.values() if entry.get('source') == 'huggingface' and not entry['triggers']
            if (url := sidecar_url(entry.get('url')))}
    gate = asyncio.Semaphore(8)

    async def fetch(url):
        cached = _hf_cache.get(url)
        if cached and time.monotonic() - cached[0] < HF_TTL:
            return url, cached[1]
        words = []
        async with gate:
            try:
                # No credentials or redirects: the only destination is the validated public HF raw URL.
                async with client.stream('GET', url, timeout=httpx.Timeout(3.0), follow_redirects=False) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_SIDECAR:
                            raise ValueError('LoRA sidecar too large')
                    words = sidecar_triggers(body.decode('utf-8'))
            except (httpx.HTTPError, ValueError, UnicodeError):
                pass
        _hf_cache[url] = (time.monotonic(), words)
        while len(_hf_cache) > 256:
            del _hf_cache[next(iter(_hf_cache))]
        return url, words

    fetched = dict(await asyncio.gather(*(fetch(url) for url in urls)))
    for entry in entries.values():
        if entry.get('source') == 'huggingface' and not entry['triggers']:
            entry['triggers'] = list(fetched.get(sidecar_url(entry.get('url')), []))


def match(names, records):
    """{name: entry} for the ComfyUI LoRA names with ModelHarbor source or embedded metadata."""
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
