import json
import unittest

from app.lora_suggest import profile, suggest, unique, enrich


def meta(*tags):
    return {'ss_tag_frequency': json.dumps({'10_set': {t: 100 - i for i, t in enumerate(tags)}})}


PROFILES = [
    profile('SDXL\\TechWearXL.safetensors', meta('techwear', 'solo', '1girl', 'hood', 'blurry background')),
    profile('SDXL\\steampunk_xl-off.safetensors', meta('steampunk style', 'steampunk', 'no humans', 'gears', 'outdoors')),
    profile('SDXL\\cyberpunk_style_xl-off.safetensors', meta('cyberpunk style', '1girl', 'cyberpunk', 'city', 'outdoors')),
    profile('SDXL\\SDXL_BetterFaces-LoRA_v1.safetensors', meta('4ng3l face', 'long hair', 'portrait', 'jewelry')),
    profile('SDXL\\fv.safetensors', {}),
] + [profile(f'SDXL\\filler{i}.safetensors', meta('1girl', 'solo', 'long hair', 'outdoors')) for i in range(8)]


class SuggestTests(unittest.TestCase):
    def test_trigger_is_the_tag_named_after_the_file(self):
        self.assertEqual('techwear', PROFILES[0]['trigger'])
        self.assertEqual('steampunk', PROFILES[1]['trigger'])
        self.assertIsNone(PROFILES[3]['trigger'])
        self.assertNotIn('1girl', PROFILES[0]['terms'])

    def test_rare_tag_match_wins(self):
        picks = suggest('a woman in techwear with a hood, blurry background', PROFILES)
        self.assertEqual('SDXL\\TechWearXL.safetensors', picks[0]['name'])
        self.assertEqual('techwear', picks[0]['trigger'])
        self.assertEqual(['SDXL\\steampunk_xl-off.safetensors'],
                         [p['name'] for p in suggest('steampunk airship, brass gears, outdoors', PROFILES)])

    def test_common_tags_alone_suggest_nothing(self):
        self.assertEqual([], suggest('1girl, solo, long hair, outdoors, looking at viewer', PROFILES))
        self.assertEqual([], suggest('', PROFILES))

    def test_whole_words_only_and_limit(self):
        self.assertEqual([], suggest('a hoodie', PROFILES[:1] + PROFILES[4:]))
        self.assertEqual(1, len(suggest('techwear in a cyberpunk city', PROFILES, limit=1)))

    def test_modelharbor_enrichment_prefers_exact_strength_and_trigger(self):
        original = [{'name': 'SDXL/a.safetensors', 'strength': 0.7, 'trigger': 'guess', 'matched': ['dress']}]
        entries = {'SDXL/a.safetensors': {'weight': 0.55, 'triggers': ['unique'], 'base_model': 'Illustrious'}}
        results = enrich(original, entries)
        self.assertEqual(0.55, results[0]['strength'])
        self.assertEqual('unique', results[0]['trigger'])
        self.assertEqual('Illustrious', results[0]['base_model'])
        self.assertEqual('guess', original[0]['trigger'])  # no mutation

    def test_multiple_triggers_are_not_auto_injected(self):
        original = [{'name': 'a.safetensors', 'strength': 0.7, 'trigger': 'guess', 'matched': []}]
        entry = {'a.safetensors': {'triggers': ['pose_a', 'pose_b'], 'weight': None}}
        result = enrich(original, entry)[0]
        self.assertIsNone(result['trigger'])
        self.assertEqual(['pose_a', 'pose_b'], result['trigger_options'])
        self.assertEqual(0.7, result['strength'])

    def test_unique_prefers_the_folder_copy(self):
        self.assertEqual(['SDXL\\fv.safetensors', 'other.safetensors'],
                         unique(['fv.safetensors', 'SDXL\\fv.safetensors', 'other.safetensors']))


if __name__ == '__main__':
    unittest.main()
