import unittest
from app.research import extract_document, settings_excerpt, candidates, verify_facts, identity_matches


class ExtractionTests(unittest.TestCase):
    def test_split_lines_and_tables(self):
        text = extract_document('<h1>Example v1</h1><table><tr><td>Sampling steps</td><td>25</td></tr><tr><td>CFG scale</td><td>5</td></tr></table>', 'https://example.org')
        got = candidates(text)
        self.assertTrue(any(x['field'] == 'steps' and x['low'] == 25 for x in got))
        self.assertTrue(any(x['field'] == 'cfg' and x['low'] == 5 for x in candidates('CFG scale:\n5')))

    def test_long_line_keeps_setting(self):
        self.assertIn('25', settings_excerpt('intro ' * 200 + 'Sampling steps: 25.'))

    def test_evidence_rejects_training_wrong_variant_and_invention(self):
        source = {'id': 's1', 'verified': True, 'text': 'Inference steps: 20-30. CFG: 5.', 'url': 'https://example.org'}
        fact = {'field': 'steps', 'low': 20, 'high': 30, 'value': '', 'source_id': 's1', 'quote': 'Inference steps: 20-30.'}
        self.assertEqual(1, len(verify_facts([fact], [source], 'example_v1.safetensors')[0]))
        self.assertFalse(verify_facts([{**fact, 'low': 12}], [source], 'example_v1.safetensors')[0])
        self.assertFalse(verify_facts([{**fact, 'quote': 'invented quote'}], [source], 'example_v1.safetensors')[0])
        for text in ('Training steps: 25', 'Lightning inference steps: 25'):
            self.assertFalse(verify_facts([{**fact, 'low':25,'high':25,'quote':text}], [{**source,'text':text}], 'example_v1.safetensors')[0])

    def test_variant_identity_is_not_fuzzy(self):
        self.assertTrue(identity_matches('Example_v1.safetensors', 'Example v1 recommended settings'))
        self.assertFalse(identity_matches('Example_v1.safetensors', 'Example v2 recommended settings'))
        self.assertFalse(identity_matches('Example_v1.safetensors', 'Example v10 recommended settings'))
        self.assertFalse(identity_matches('Example_v1.safetensors', 'Example v1 Lightning recommended settings'))


class PublicFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_private_dns_and_untrusted_domains(self):
        from unittest.mock import patch
        from app.research import public_get, ResearchError
        with self.assertRaises(ResearchError):
            await public_get('https://localhost/private')
        with patch('app.research.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ResearchError):
                await public_get('https://huggingface.co/user/model')

    async def test_pins_public_connection_and_checks_redirect_again(self):
        import httpx
        from unittest.mock import patch
        from app.research import public_get, ResearchError
        client_type=httpx.AsyncClient
        requests=[]
        def respond(req):
            requests.append(req)
            return httpx.Response(302,headers={'location':'https://127.0.0.1/private'})
        with patch('app.research.socket.getaddrinfo',return_value=[(2,1,6,'',('1.1.1.1',443))]), patch('app.research.httpx.AsyncClient',side_effect=lambda **kw:client_type(transport=httpx.MockTransport(respond),**kw)):
            with self.assertRaises(ResearchError): await public_get('https://huggingface.co/user/model')
        self.assertEqual('1.1.1.1',requests[0].url.host)
        self.assertEqual('huggingface.co',requests[0].headers['host'])
        self.assertEqual('huggingface.co',requests[0].extensions['sni_hostname'])
        self.assertEqual(1,len(requests))


class ContextRegressionTests(unittest.TestCase):
    def test_precision_suffixes_dont_hide_model_identity(self):
        self.assertTrue(identity_matches('Example_v1_fp16.safetensors','Example v1 recommended inference steps: 25.'))

    def test_ai_cannot_quote_training_label_out_of_context(self):
        source={'id':'s1','verified':True,'text':'Example v1. Training\nSteps: 25.','url':'https://example.org'}
        fact={'field':'steps','low':25,'high':25,'value':'','source_id':'s1','quote':'Steps: 25'}
        self.assertEqual([],verify_facts([fact],[source],'Example_v1.safetensors')[0])

class SectionRegressionTests(unittest.TestCase):
    def test_training_section_survives_blank_paragraphs_and_nested_headings(self):
        from app.research import unsafe_context
        for text in ('Example v1\n\nTraining settings\n\nSteps: 25\nCFG: 5',
                     '# Example v1\n## Training\n### Parameters\n\nSteps: 25\nCFG: 5'):
            self.assertTrue(unsafe_context(text,'Steps: 25'))
            source={'id':'s1','verified':True,'text':text,'url':'https://example.org'}
            fact={'field':'steps','low':25,'high':25,'value':'','source_id':'s1','quote':'Steps: 25'}
            self.assertEqual([],verify_facts([fact],[source],'Example_v1.safetensors')[0])
        self.assertFalse(unsafe_context('## Training\nSteps: 100\n## Inference\nSteps: 25','Steps: 25'))

class ScreenshotRegressionTests(unittest.TestCase):
    def test_markdown_table_and_diffusers_code(self):
        text='| Steps | `30 – 40` |\n| CFG scale | `3 – 7` (lower = realistic) |'
        got=candidates(text)
        self.assertTrue(any(f['field']=='steps' and f['low']==30 and f['high']==40 for f in got))
        self.assertTrue(any(f['field']=='cfg' and f['low']==3 and f['high']==7 for f in got))
        code='num_inference_steps=35,\nguidance_scale=5.0,'
        self.assertEqual({'steps','cfg'},{f['field'] for f in candidates(code)})

    def test_search_queries_clean_camel_case_and_filter_unrelated_model(self):
        from app.research import search_queries, relevant_result
        queries=search_queries('SDXL\\juggernautXL_ragnarok.safetensors')
        self.assertIn('juggernaut XL ragnarok',queries[-1])
        self.assertFalse(relevant_result('SDXL\\juggernautXL_ragnarok.safetensors','Wan2.2 Lightning video model'))
        self.assertTrue(relevant_result('SDXL\\juggernautXL_ragnarok.safetensors','Juggernaut XL Ragnarok model card'))

    def test_civitai_exact_match_uses_windows_basename(self):
        from app.research import civitai_version
        model={'modelVersions':[{'name':'Ragnarok','files':[{'name':'juggernautXL_ragnarok.safetensors'}]}]}
        self.assertEqual('Ragnarok',civitai_version(model,'SDXL\\juggernautXL_ragnarok.safetensors')['name'])
        self.assertIsNone(civitai_version(model,'SDXL\\juggernautXL_v9.safetensors'))

class SearchGatherRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_correct_civitai_version_prioritized_and_irrelevant_page_not_fetched(self):
        import json
        from unittest.mock import AsyncMock, patch
        from app.research import Research
        results=[{'title':'Wan2.2 Lightning','url':'https://huggingface.co/lightx2v/Wan2.2-Lightning'},
                 {'title':'Juggernaut XL Ragnarok','url':'https://civitai.com/models/123'}]
        model={'name':'Juggernaut XL','description':'', 'modelVersions':[{'name':'Ragnarok','description':'<p>Steps: 25</p>', 'files':[{'name':'juggernautXL_ragnarok.safetensors'}]}]}
        fetch=AsyncMock(return_value=json.dumps(model))
        with patch.object(Research,'search',AsyncMock(return_value=results)),patch('app.research.public_get',fetch):
            sources,diagnostics=await Research('http://nas').gather({'file':'SDXL\\juggernautXL_ragnarok.safetensors'})
        self.assertEqual(1,len(sources));self.assertTrue(sources[0]['verified'])
        self.assertEqual(1,fetch.await_count)
        self.assertTrue(any(d['phase']=='relevance' for d in diagnostics))

    async def test_wrong_version_has_brief_diagnostic_instead_of_full_excerpt(self):
        from unittest.mock import AsyncMock,patch
        from app.research import Research
        results=[{'title':'Juggernaut XL v9','url':'https://huggingface.co/author/Juggernaut-XL-v9'}]
        with patch.object(Research,'search',AsyncMock(return_value=results)),patch('app.research.public_get',AsyncMock(return_value='Juggernaut XL v9\nSteps: 30-40\nCFG: 3-7')):
            sources,diagnostics=await Research('http://nas').gather({'file':'SDXL\\juggernautXL_ragnarok.safetensors'})
        self.assertEqual('',sources[0]['text']);self.assertNotIn('30-40',sources[0]['excerpt'])
        self.assertFalse(sources[0]['verified'])
        self.assertTrue(any(d['phase']=='identity' for d in diagnostics))
