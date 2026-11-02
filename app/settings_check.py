"""Setup-free settings check: known per-model-family rules over the final job graph.

Advice only, never blocks a job. A fix is a sampler change the phone can send back through `sampling`;
everything else is a message. Unknown model families get no family-specific advice rather than a guess.
"""
import re

LABELS = {'sd15': 'SD 1.5', 'sdxl': 'SDXL', 'ponyv7': 'Pony V7', 'krea2': 'Krea 2', 'qwen': 'Qwen Image', 'wan': 'Wan',
          'zimage': 'Z-Image', 'flux': 'Flux', 'moss': 'MOSS', 'ltx': 'LTX', 'sd2': 'SD 2.x'}
# The model folders this ComfyUI is organised by decide first; keywords cover files kept at the root.
FOLDERS = {'sd15': 'sd15', 'sdxl': 'sdxl', 'pony': 'sdxl', 'ponyv7': 'ponyv7', 'krea2': 'krea2', 'qwen': 'qwen',
           'wan': 'wan', 'zimage': 'zimage', 'flux': 'flux', 'moss': 'moss', 'ltx': 'ltx', 'sd2': 'sd2'}
KEYWORDS = [('ponyv7', r'pony_?v7'), ('sd15', r'sd_?1\.?5|v1-5'), ('sdxl', r'sdxl|pony|illustrious'), ('sd2', r'sd_?2\.?[01]|v2-[01]'), ('krea2', r'krea2'),
            ('qwen', r'qwen'), ('wan', r'wan2'), ('zimage', r'z_?image'), ('flux', r'flux'), ('moss', r'moss'),
            ('ltx', r'ltx'), ('sdxl', r'(?<![a-z])xl|xl(?![a-z])')]
SPEED = re.compile(r'turbo|lightning|lightx2v|hyper|lcm|dmd|schnell|distill|flash|\d+[-_ ]?steps?', re.I)
STEP_COUNT = re.compile(r'(\d+)[-_ ]?steps?', re.I)
CFG_FAMILIES = {'sd15', 'sdxl'}  # Generic 1–2 CFG advice is not safe for Qwen/Wan schedules.
TYPICAL_CFG = {'sd15': 7.0, 'sdxl': 6.0}


def family(name):
    parts = re.split(r'[\\/]', name.lower())
    if len(parts) > 1 and parts[0] in FOLDERS:
        return FOLDERS[parts[0]]
    return next((fam for fam, pattern in KEYWORDS if re.search(pattern, parts[-1])), None)

# Civitai's base model names, for files whose folder and name don't say. The first pattern that matches wins.
CIVITAI_BASES = [('ponyv7', r'^pony v7'), ('sdxl', r'^(sdxl|pony|illustrious|noobai)'), ('sd15', r'^sd 1\.'),
                 ('sd2', r'^sd 2\.'), ('krea2', r'^krea'), ('flux', r'^flux'), ('qwen', r'^qwen'), ('wan', r'^wan'),
                 ('ltx', r'^ltx'), ('moss', r'^moss'), ('zimage', r'^z ?image')]


def civitai_family(base):
    """The LABELS text for a Civitai base model ("SDXL 1.0" -> "SDXL"), or ''."""
    base = base.strip().lower()
    return next((LABELS[fam] for fam, pattern in CIVITAI_BASES if re.search(pattern, base)), '')


def short(name):
    return re.split(r'[\\/]', name)[-1].rsplit('.', 1)[0]


def _chain(graph, node_id):
    """The model file and active LoRAs (name, strength) feeding a node's `model` input."""
    loras, seen = [], set()
    link = graph[node_id]['inputs'].get('model')
    while isinstance(link, list) and link[0] in graph and link[0] not in seen:
        seen.add(link[0])
        inputs = graph[link[0]]['inputs']
        for field in ('ckpt_name', 'unet_name'):
            if isinstance(inputs.get(field), str):
                return inputs[field], loras
        if isinstance(inputs.get('lora_name'), str) and inputs.get('strength_model', 1) != 0:
            loras.append((inputs['lora_name'], inputs.get('strength_model', 1)))
        link = inputs.get('model')
    return None, loras


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_refinement(node):
    """Do not tune partial-noise or image-edit passes using text-to-image defaults."""
    inputs = node.get('inputs', {})
    denoise = inputs.get('denoise', 1)
    if _number(denoise) and denoise < 0.99:
        return True
    if node.get('class_type') == 'KSamplerAdvanced':
        return inputs.get('add_noise') == 'disable' or inputs.get('start_at_step', 0) != 0
    return False


def _speed_steps(name):
    """Only a declared numeric count justifies a concrete step-count fix."""
    match = STEP_COUNT.search(short(name))
    if match:
        count = int(match.group(1))
        return count if 1 <= count <= 16 else None
    return None


def check(graph, controls):
    """Return [{level: 'warn'|'tip', message, changes: [{node, field, value}]}] for the graph about to run."""
    editable = {c['node']: c['values'] for c in controls.get('samplers', [])}
    issues, seen = [], set()

    def add(level, message, node=None, **fix):
        if message in seen:
            return
        seen.add(message)
        # Advanced samplers share one noise schedule across passes, so only plain KSamplers get automatic fixes.
        ok = graph.get(node, {}).get('class_type') == 'KSampler' and all(f in editable.get(node, {}) for f in fix)
        issues.append({'level': level, 'message': message,
                       'changes': [{'node': node, 'field': f, 'value': v} for f, v in fix.items()] if ok else []})

    families = set()
    for node_id, node in graph.items():
        if node['class_type'] not in ('KSampler', 'KSamplerAdvanced', 'CFGGuider', 'BasicGuider', 'SamplerCustom'):
            continue
        model, loras = _chain(graph, node_id)
        if model is None:
            continue
        fam = family(model)
        families.add(fam)
        names = [n for n, _ in loras]
        for name, strength in loras:
            lora_fam = family(name)
            if fam and lora_fam and lora_fam != fam:
                add('warn', f'LoRA “{short(name)}” is made for {LABELS[lora_fam]}, but the model is {LABELS[fam]}. '
                            'It won’t apply properly; turn it off or pick a matching LoRA.')
            if names.count(name) > 1:
                add('warn', f'LoRA “{short(name)}” is loaded more than once, which stacks its strength.')
            if abs(strength) > 1.5:
                add('tip', f'LoRA “{short(name)}” is at strength {strength:g}. Above about 1.2 most LoRAs distort the image.')

        inputs = node['inputs']
        steps, cfg, denoise = inputs.get('steps'), inputs.get('cfg'), inputs.get('denoise', 1)
        if not (_number(steps) and _number(cfg)):
            continue
        # A denoising/refinement pass is not a fresh latent. It may intentionally
        # share noise schedules with another sampler; automatic fixes here are unsafe.
        if _is_refinement(node):
            continue
        speed = next((n for n in [model, *names] if SPEED.search(short(n))), None)
        if speed:
            target = _speed_steps(speed)
            if target is not None and steps > max(2 * target, 12):
                add('warn', f'“{short(speed)}” indicates a {target}-step variant, but this pass uses {steps} steps. '
                            'Check the model author’s recommended schedule.', node_id, steps=target)
            elif target is None and steps > 20:
                add('tip', f'“{short(speed)}” appears to be a speed/distilled model, but its exact schedule is unknown. '
                           'Confirm the author’s step count before changing settings.')
            if fam in CFG_FAMILIES and cfg > 2.5:
                add('warn', f'“{short(speed)}” is a speed variant; CFG {cfg:g} may be too high for this {LABELS[fam]} pass. '
                            'Check its author guidance before applying a lower CFG.',
                    node_id, cfg=1.5)
        elif fam in TYPICAL_CFG and _number(denoise) and denoise >= 0.99:
            label = LABELS[fam]
            if steps < 15:
                add('warn', f'{steps} steps is few for a full {label} pass; 25–30 is typical.', node_id, steps=25)
            elif steps > 60:
                add('tip', f'{steps} steps rarely looks better than 30 on {label}, and takes longer.', node_id, steps=30)
            if cfg > 10:
                add('warn', f'CFG {cfg:g} is high for {label}; expect burnt, over-saturated colours. 5–7 is typical.',
                    node_id, cfg=TYPICAL_CFG[fam])
            elif cfg < 3:
                add('warn', f'CFG {cfg:g} is low for {label} without a speed LoRA; expect washed-out images. 5–7 is typical.',
                    node_id, cfg=TYPICAL_CFG[fam])

    if len(families) == 1:
        fam = next(iter(families))
        for node in graph.values():
            w, h = node['inputs'].get('width'), node['inputs'].get('height')
            if not (node['class_type'].startswith('Empty') and 'Latent' in node['class_type'] and _number(w) and _number(h)):
                continue
            mp = w * h / 1e6
            if fam == 'sd15' and mp > 0.6:
                add('warn', f'{w}×{h} is large for SD 1.5, which was trained at 512 px; expect doubled people or objects. '
                            'Use a smaller size and a hires pass.')
            elif fam == 'sdxl' and not 0.5 <= mp <= 1.8:
                add('warn', f'{w}×{h} is far from SDXL’s ~1 megapixel training size; expect '
                            f'{"blurry, low-detail" if mp < 0.5 else "doubled or stretched"} results.')
        texts = [n['inputs'].get('text') for n in graph.values() if n['class_type'] == 'CLIPTextEncode']
        models = [n['inputs'].get('ckpt_name', '') for n in graph.values()]
        if (fam == 'sdxl' and any('pony' in str(m).lower() for m in models) and texts
                and all(isinstance(t, str) for t in texts) and not any('score_' in t for t in texts)):
            add('tip', 'Pony models follow prompts best with “score_9, score_8_up, score_7_up” at the start.')
    return issues
