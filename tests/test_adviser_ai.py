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
