"""Pick LoRAs whose training tags match a prompt or reviewed caption. No AI: kohya training metadata plus filenames.

A tag that many LoRAs share (1girl, solo) says little, so matches are weighted by rarity across the LoRAs being
compared, and a LoRA is only suggested when at least one rare tag or filename word matches.
"""
import json
import math
import re

TOP_TAGS = 40
# Subject words nearly every caption has; matching them says nothing about what a LoRA adds.
GENERIC = {'1girl', '1boy', '2girls', 'solo', 'a woman', 'a man', 'woman', 'man', 'girl', 'boy', 'female', 'male',
           'person', 'people', 'realistic', 'photo', 'photorealistic', 'looking at viewer'}
NOISE = {'xl', 'sdxl', 'sd15', 'lora', 'loras', 'off', 'fp16', 'bf16', 'fp8', 'epoch', 'last', 'final', 'model', 'style',
         'pony', 'krea2', 'qwen', 'moss', 'flux', 'wan', 'the', 'and', 'with', 'by', 'image', 'full'}


def _norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[_]+', ' ', str(text).lower())).strip()


def _compact(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


def profile(name, meta):
    """Terms (most frequent training tags first), filename words and a trigger word for one LoRA."""
    meta = meta if isinstance(meta, dict) else {}
    counts = {}
    try:
        for folder in json.loads(meta.get('ss_tag_frequency') or '{}').values():
            for tag, count in folder.items():
                tag = _norm(tag)
                if tag and isinstance(count, (int, float)):
                    counts[tag] = counts.get(tag, 0) + count
    except (ValueError, AttributeError, TypeError):
        counts = {}
    terms = [t for t in sorted(counts, key=lambda t: -counts[t]) if t not in GENERIC][:TOP_TAGS]
    stem = re.split(r'[\\/]', name)[-1].rsplit('.', 1)[0]
    words = [w for w in re.split(r'[^a-z]+', stem.lower()) if len(w) > 2 and w not in NOISE]
    # A training tag that is also the file's own name is almost always the trigger (techwear, jyzjk, rlytessa).
    names = _compact(stem) + ' ' + _compact(meta.get('ss_output_name', ''))
    trigger = meta.get('trigger') if isinstance(meta.get('trigger'), str) else None
    trigger = trigger or next((t for t in terms[:5] if len(_compact(t)) > 3 and _compact(t) in names), None)
    return {'name': name, 'terms': terms, 'words': words, 'trigger': trigger}


def _has(text, phrase):
    return re.search(r'(?<![a-z0-9])' + re.escape(phrase) + r'(?![a-z0-9])', text) is not None


def suggest(text, profiles, limit=2, strength=0.7):
    """[{name, strength, matched, trigger}] best first, at most `limit`."""
    text = _norm(text)
    if not text or not profiles or limit < 1:
        return []
    total = len(profiles)
    df = {}
    for p in profiles:
        for term in set(p['terms']) | set(p['words']):
            df[term] = df.get(term, 0) + 1
    rare = max(1, total // 8)
    picks = []
    for p in profiles:
        score, matched, distinctive, named = 0.0, [], False, False
        for rank, term in enumerate(p['terms']):
            if _has(text, term):
                weight = math.log(1 + total / df[term]) * (1.0 if rank < 10 else 0.5)
                score += weight
                matched.append(term)
                distinctive |= df[term] <= rare and rank < 10
        # The file's own name is the strongest hint of what it is for, so a name match counts on top of a tag match.
        for word in p['words']:
            if _has(text, word):
                score += 1.5 * math.log(1 + total / df[word])
                matched += [] if word in matched else [word]
                distinctive |= df[word] <= rare
                named = True
        # One shared word ("city") is a coincidence; the LoRA's own name or two of its tags is a real fit.
        if distinctive and (named or len(matched) >= 2):
            picks.append((score, p, matched))
    picks.sort(key=lambda x: -x[0])
    # A runner-up far behind the best match is usually a coincidental word, not a second fit.
    picks = [x for x in picks if x[0] >= picks[0][0] / 2] if picks else []
    return [{'name': p['name'], 'strength': strength, 'matched': matched[:6], 'trigger': p['trigger']}
            for _, p, matched in picks[:limit]]


def filename(name):
    return re.split(r'[\\/]', name)[-1].lower()


def unique(names):
    """One name per file: the same LoRA kept loose and inside a folder is listed once, as the folder copy."""
    best = {}
    for name in names:
        if filename(name) not in best or len(re.split(r'[\\/]', name)) > len(re.split(r'[\\/]', best[filename(name)])):
            best[filename(name)] = name
    return [n for n in names if best[filename(n)] == n]


def enrich(picks, verified):
    """Apply hash-verified ModelHarbor metadata without inventing trigger words.

    Multiple concept triggers require explicit user selection, not automatic injection.
    """
    output = []
    for candidate in picks:
        pick = dict(candidate)
        entry = verified.get(pick['name']) or {}
        triggers = entry.get('triggers') or []
        if len(triggers) == 1:
            pick['trigger'] = triggers[0]
        elif len(triggers) > 1:
            pick['trigger'] = None
        if entry.get('weight') is not None:
            pick['strength'] = entry['weight']
        pick['base_model'] = entry.get('base_model')
        pick['source'] = 'civitai' if entry else 'training tags'
        pick['trigger_options'] = triggers[:10]
        output.append(pick)
    return output
