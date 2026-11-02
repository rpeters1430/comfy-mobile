import json
import unittest
import httpx
from app.adviser_ai import AI, AIConfig, AIError


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_ollama_schema_and_truncation(self):
        def respond(req):
            body = json.loads(req.content)
            self.assertIsInstance(body['format'], dict)
            self.assertGreaterEqual(body['options']['num_predict'], 2000)
            return httpx.Response(200, json={'done':True,'done_reason':'length','message':{'content':'{"facts":[]}'}})
        ai = AI(AIConfig(provider='ollama', model='local', base_url='http://pc:11434'), transport=httpx.MockTransport(respond))
        with self.assertRaisesRegex(AIError, 'incomplete'):
            await ai.extract({'file':'example'}, [])

    async def test_hosted_schema_request_and_private_data_excluded(self):
        for provider in ('openai','gemini','openrouter','compatible'):
            def respond(req):
                body = json.loads(req.content)
                self.assertEqual('json_schema', body['response_format']['type'])
                self.assertEqual('Bearer test-key', req.headers['authorization'])
                self.assertNotIn('secret prompt', req.content.decode())
                return httpx.Response(200, json={'choices':[{'finish_reason':'stop','message':{'content':'{"facts":[]}'}}]})
            cfg=AIConfig(provider=provider, model='chosen', api_key='test-key', base_url='https://provider.example/v1')
            self.assertEqual([], await AI(cfg,transport=httpx.MockTransport(respond)).extract({'file':'example','prompt':'secret prompt'}, []))

    async def test_missing_key_and_malformed_json(self):
        with self.assertRaises(AIError):
            await AI(AIConfig(provider='openai',model='chosen')).extract({'file':'x'}, [])
        ai=AI(AIConfig(provider='ollama',model='local',base_url='http://pc:11434'), transport=httpx.MockTransport(lambda req: httpx.Response(200,json={'done':True,'message':{'content':'bad'}})))
        with self.assertRaisesRegex(AIError,'JSON'):
            await ai.extract({'file':'x'}, [])

class WorkflowReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_calls_model_without_web_sources_and_excludes_private_inputs(self):
        def respond(req):
            body=json.loads(req.content)
            context=json.loads(body['messages'][1]['content'])
            self.assertEqual('30',str(context['capabilities'][0]['current']))
            self.assertNotIn('secret prompt',req.content.decode())
            result={'summary':'Review completed','changes':[{'node':'2','field':'steps','value':'25','reason':'A modest initial trial; compare with the same seed.'}]}
            return httpx.Response(200,json={'done':True,'message':{'content':json.dumps(result)}})
        ai=AI(AIConfig(provider='ollama',model='local',base_url='http://pc:11434'),transport=httpx.MockTransport(respond))
        result=await ai.review({'models':['Example'],'capabilities':[{'node':'2','field':'steps','current':30}],'prompt':'secret prompt','images':['private.png']})
        self.assertEqual('Review completed',result['summary'])


class LoraSemanticTests(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_opt_in_sends_only_caption_and_bounded_labels(self):
        def respond(req):
            body = json.loads(req.content)
            prompt = json.loads(body['messages'][1]['content'])
            self.assertEqual('a person in a red raincoat', prompt['caption'])
            self.assertEqual(2, len(prompt['candidates']))
            self.assertNotIn('C:/Users/', req.content.decode())
            return httpx.Response(200, json={'done': True, 'message': {
                'content': json.dumps({'choices': [
                    {'index': 1, 'reason': 'Raincoat styling matches the scene.'},
                    {'index': 1, 'reason': 'Duplicated choice is ignored.'},
                    {'index': 30, 'reason': 'Invalid index is ignored.'}]})}})
        ai = AI(AIConfig(provider='ollama', model='local', base_url='http://pc:11434'),
                transport=httpx.MockTransport(respond))
        picks = await ai.rank_loras('a person in a red raincoat', [
            {'label': 'castle', 'tags': ['castle']},
            {'label': 'raincoat', 'tags': ['raincoat']}
        ], 2)
        self.assertEqual([1], [p['index'] for p in picks])

    async def test_semantic_rejects_excessive_caption_and_candidates(self):
        ai = AI(AIConfig(provider='ollama', model='local', base_url='http://pc:11434'))
        with self.assertRaises(AIError):
            await ai.rank_loras('x' * 3001, [{'label': 'x'}])
        with self.assertRaises(AIError):
            await ai.rank_loras('caption', [{'label': 'x'}] * 41)
