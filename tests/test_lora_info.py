import unittest

from app import lora_info
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
                                                            'url': 'https://civitai.com/models/1', 'source': 'civitai'}})

    def test_file_name_fallback_only_when_unambiguous(self):
        # Hard links of one file (same hash) count once; two different files with one name do not match.
        same = [record(ROOT + 'eyes.safetensors'), record(ROOT + 'SDXL\\eyes.safetensors')]
        self.assertIn('Pony/eyes.safetensors', match(['Pony/eyes.safetensors'], same))
        different = [record(ROOT + 'a\\eyes.safetensors', sha='b' * 64), record(ROOT + 'b\\eyes.safetensors')]
        self.assertEqual(match(['eyes.safetensors'], different), {})

    def test_ignores_other_devices_sources_and_bad_values(self):
        records = [record(ROOT + 'x.safetensors', device='pc:ollama'), record(ROOT + 'y.safetensors', provider='unknown'),
                   record(ROOT + 'z.safetensors', weight=5), 'junk']
        found = match(['x.safetensors', 'y.safetensors', 'z.safetensors'], records)
        self.assertEqual(list(found), ['z.safetensors'])
        self.assertIsNone(found['z.safetensors']['weight'])

    def test_huggingface_and_embedded_metadata_are_not_discarded(self):
        hf = record(ROOT + 'AnaDeArmas_KREA2.safetensors', ['AnaDeArmas'], base='Krea 2', provider='huggingface')
        local = {'device': 'pc:comfyui', 'path': ROOT + 'DuaLipa_KREA2.safetensors',
                 'source': None, 'trigger_words': '["DuaLipa"]', 'base_model_guess': 'Krea 2'}
        found = match(['AnaDeArmas_KREA2.safetensors', 'DuaLipa_KREA2.safetensors'], [hf, local])
        self.assertEqual(found['AnaDeArmas_KREA2.safetensors']['triggers'], ['AnaDeArmas'])
        self.assertEqual(found['AnaDeArmas_KREA2.safetensors']['family'], 'Krea 2')
        self.assertEqual(found['AnaDeArmas_KREA2.safetensors']['source'], 'huggingface')
        self.assertEqual(found['DuaLipa_KREA2.safetensors']['triggers'], ['DuaLipa'])

    def test_families(self):
        self.assertEqual([civitai_family(b) for b in ('SDXL 1.0', 'Pony', 'Illustrious', 'SD 1.5', 'SD 2.1 768', 'Pony V7', 'Other')],
                         ['SDXL', 'SDXL', 'SDXL', 'SD 1.5', 'SD 2.x', 'Pony V7', ''])
        self.assertEqual(family('SD2/artius21NSFW_v10VAE.safetensors'), 'sd2')
        self.assertEqual(family('v2-1_768-ema-pruned.safetensors'), 'sd2')
        self.assertEqual(family('sdxl_v2-0_merge.safetensors'), 'sdxl')


class HFSidecarTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_file_sidecars_are_cached_and_do_not_mix_characters(self):
        import httpx
        from app import lora_info
        calls = []
        async def respond(request):
            calls.append(str(request.url))
            return httpx.Response(200, text='# Persona\nTrigger word: AnaDeArmas\n' if 'AnaDeArmas' in str(request.url)
                                  else '**Trigger word:** `DuaLipa`\n')
        records = []
        names = ['AnaDeArmas_KREA2.safetensors', 'DuaLipa_KREA2.safetensors']
        for name, folder in zip(names, ['Ana De Armas', 'Dua Lipa']):
            row = record(ROOT + name, base='Krea 2', provider='huggingface')
            row['source']['url'] = f'https://huggingface.co/UntMods/Krea2_Chars_LoRA/blob/main/Characters/{folder}/{name}'
            records.append(row)
        lora_info._hf_cache.clear()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            entries = match(names, records)
            await lora_info.enrich_huggingface(entries, client)
            self.assertEqual(entries[names[0]]['triggers'], ['AnaDeArmas'])
            self.assertEqual(entries[names[1]]['triggers'], ['DuaLipa'])
            await lora_info.enrich_huggingface(match(names, records), client)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('/raw/main/Characters/' in url and url.endswith('.md') for url in calls))

    async def test_missing_sidecars_leave_metadata_usable_and_never_guess_filename(self):
        import httpx
        from app import lora_info
        calls = []
        async def respond(request):
            calls.append(request.url)
            return httpx.Response(404)
        row = record(ROOT + 'unknown.safetensors', base='Krea 2', provider='huggingface')
        row['source']['url'] = 'https://huggingface.co/owner/repo/blob/main/unknown.safetensors'
        lora_info._hf_cache.clear()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            entries = match(['unknown.safetensors'], [row])
            await lora_info.enrich_huggingface(entries, client)
        self.assertEqual(entries['unknown.safetensors']['triggers'], [])
        self.assertEqual(entries['unknown.safetensors']['family'], 'Krea 2')
        self.assertEqual(len(calls), 1)

    async def test_sidecar_requests_reject_untrusted_urls_and_preserve_existing_words(self):
        import httpx
        from app import lora_info
        def respond(request):
            self.fail('Unexpected external request')
        lora_info._hf_cache.clear()
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for url in ['http://huggingface.co/a/b/blob/main/x.safetensors',
                        'https://evil.example/a/b/blob/main/x.safetensors',
                        'https://huggingface.co/a/b', 'https://huggingface.co/a/b/blob/main/../x.safetensors']:
                entry = {'source': 'huggingface', 'url': url, 'triggers': []}
                await lora_info.enrich_huggingface({'x': entry}, client)
                self.assertEqual(entry['triggers'], [])
            entry = {'source': 'huggingface', 'url': 'https://huggingface.co/a/b/blob/main/x.safetensors', 'triggers': ['verified']}
            await lora_info.enrich_huggingface({'x': entry}, client)
            self.assertEqual(entry['triggers'], ['verified'])

    async def test_oversized_redirected_and_unavailable_documents_are_optional(self):
        import httpx
        from app import lora_info
        for response in [httpx.Response(200, text='x' * (lora_info.MAX_SIDECAR + 1) + '\nTrigger word: bad'),
                         httpx.Response(302, headers={'Location': 'https://evil.example/'}),
                         httpx.Response(200, text='# AnaDeArmas\nA persona without a labelled trigger.')]:
            lora_info._hf_cache.clear()
            entry = {'source': 'huggingface', 'url': 'https://huggingface.co/a/b/blob/main/x.safetensors', 'triggers': []}
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response), trust_env=False) as client:
                await lora_info.enrich_huggingface({'x': entry}, client)
            self.assertEqual(entry['triggers'], [])


if __name__ == '__main__':
    unittest.main()


class LoraManagerTests(unittest.TestCase):
    ITEMS = [
        {'file_path': '/home/u/AI/ComfyUI/models/loras/SDXL/Magician_XL.safetensors', 'sha256': 'a', 'base_model': 'SDXL 1.0',
         'from_civitai': True, 'civitai': {'trainedWords': ['MagicianAi', 'dark magician girl']}, 'usage_tips': '{"strength": 0.7}'},
        {'file_path': '/home/u/AI/ComfyUI/models/loras/SDXL/add-detail-xl.safetensors', 'sha256': 'b', 'base_model': 'SDXL 1.0',
         'from_civitai': True, 'civitai': {}, 'usage_tips': '{}'},
        {'file_path': '/elsewhere/not-a-lora.safetensors', 'base_model': 'SDXL 1.0', 'civitai': {}, 'usage_tips': 'bad json'},
    ]

    def test_triggers_base_and_usage_tip_strength(self):
        found = lora_info.match_manager(['SDXL/Magician_XL.safetensors', 'SDXL\\add-detail-xl.safetensors', 'x.safetensors'], self.ITEMS)
        magician = found['SDXL/Magician_XL.safetensors']
        self.assertEqual((magician['triggers'], magician['weight'], magician['weight_from'], magician['source']),
                         (['MagicianAi', 'dark magician girl'], 0.7, 'loramanager', 'civitai'))
        detail = found['SDXL\\add-detail-xl.safetensors']
        self.assertEqual((detail['triggers'], detail['weight'], detail['base_model']), ([], None, 'SDXL 1.0'))
        self.assertNotIn('x.safetensors', found)

    def test_lora_manager_wins_and_modelharbor_fills_gaps(self):
        manager = {'family': 'SDXL', 'base_model': 'SDXL 1.0', 'source': 'civitai', 'triggers': [], 'weight': None, 'weight_from': None, 'url': None}
        harbor = {'family': 'SDXL', 'base_model': 'SDXL 1.0', 'source': 'civitai', 'triggers': ['portrait'], 'weight': 0.8, 'url': 'https://civitai.com/x'}
        merged = lora_info.merge(manager, harbor)
        self.assertEqual((merged['triggers'], merged['weight'], merged.get('weight_from'), merged['url']), (['portrait'], 0.8, None, 'https://civitai.com/x'))
        merged = lora_info.merge({**manager, 'triggers': ['mine'], 'weight': 0.5, 'weight_from': 'loramanager'}, harbor)
        self.assertEqual((merged['triggers'], merged['weight'], merged['weight_from']), (['mine'], 0.5, 'loramanager'))
        self.assertIs(lora_info.merge(None, harbor), harbor)


class LibraryTests(unittest.TestCase):
    def test_manager_item_by_path_or_unique_stem(self):
        items=[{'file_path':'/m/checkpoints/SDXL/juggernaut.safetensors','file_name':'juggernaut','sha256':'a'},
               {'file_path':'/m/loras/SDXL/detail.safetensors','file_name':'detail','sha256':'b'},
               {'file_path':'/m/loras/detail.safetensors','file_name':'detail','sha256':'b'}]
        self.assertEqual('a',lora_info.manager_item('SDXL\\juggernaut.safetensors',items)['sha256'])
        self.assertEqual('b',lora_info.manager_item('detail',items)['sha256'])
        self.assertIsNone(lora_info.manager_item('other',items))

    def test_library_entry_links_the_exact_civitai_version(self):
        entry=lora_info.library_entry({'model_name':'Jugg','base_model':'SDXL 1.0','civitai':{'modelId':1,'id':2,'name':'Ragnarok'},
                                       'usage_tips':'{"strength": 0.7}','source_url':'javascript:x'})
        self.assertEqual({'name':'Jugg Ragnarok','base_model':'SDXL 1.0','strength_tip':0.7,'url':'https://civitai.com/models/1?modelVersionId=2'},entry)
