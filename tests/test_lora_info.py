import unittest

from app.lora_info import match
from app.settings_check import civitai_family, family


def record(path, words=(), weight=None, base='SDXL 1.0', sha='a' * 64, device='pc:comfyui', provider='civitai'):
    return {'device': device, 'path': path, 'sha256': sha,
            'source': {'provider': provider, 'base_model': base, 'trained_words': list(words), 'weight': weight,
                       'url': 'https://civitai.com/models/1'}}


ROOT = 'C:\\Users\\me\\ComfyUI\\models\\loras\\'


class LoraInfoTests(unittest.TestCase):
    def test_matches_by_path_under_loras(self):
        found = match(['SDXL\\eyes.safetensors', 'other.safetensors'],
                      [record(ROOT + 'SDXL\\eyes.safetensors', ['perfecteyes'], 0.8)])
        self.assertEqual(found, {'SDXL\\eyes.safetensors': {'family': 'SDXL', 'base_model': 'SDXL 1.0',
                                                            'triggers': ['perfecteyes'], 'weight': 0.8,
                                                            'url': 'https://civitai.com/models/1'}})

    def test_file_name_fallback_only_when_unambiguous(self):
        # Hard links of one file (same hash) count once; two different files with one name do not match.
        same = [record(ROOT + 'eyes.safetensors'), record(ROOT + 'SDXL\\eyes.safetensors')]
        self.assertIn('Pony/eyes.safetensors', match(['Pony/eyes.safetensors'], same))
        different = [record(ROOT + 'a\\eyes.safetensors', sha='b' * 64), record(ROOT + 'b\\eyes.safetensors')]
        self.assertEqual(match(['eyes.safetensors'], different), {})

    def test_ignores_other_devices_sources_and_bad_values(self):
        records = [record(ROOT + 'x.safetensors', device='pc:ollama'), record(ROOT + 'y.safetensors', provider='huggingface'),
                   record(ROOT + 'z.safetensors', weight=5), 'junk']
        found = match(['x.safetensors', 'y.safetensors', 'z.safetensors'], records)
        self.assertEqual(list(found), ['z.safetensors'])
        self.assertIsNone(found['z.safetensors']['weight'])

    def test_families(self):
        self.assertEqual([civitai_family(b) for b in ('SDXL 1.0', 'Pony', 'Illustrious', 'SD 1.5', 'SD 2.1 768', 'Pony V7', 'Other')],
                         ['SDXL', 'SDXL', 'SDXL', 'SD 1.5', 'SD 2.x', 'Pony V7', ''])
        self.assertEqual(family('SD2/artius21NSFW_v10VAE.safetensors'), 'sd2')
        self.assertEqual(family('v2-1_768-ema-pruned.safetensors'), 'sd2')
        self.assertEqual(family('sdxl_v2-0_merge.safetensors'), 'sdxl')


if __name__ == '__main__':
    unittest.main()
