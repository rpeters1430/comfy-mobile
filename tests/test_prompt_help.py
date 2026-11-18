import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

import app.main as main
from app.adviser_ai import AI, AIConfig, AIError

UPLOAD = 'mobile_' + '0' * 32 + '.jpg'


class PromptHelpAITests(unittest.TestCase):
    def test_sends_prompt_and_model_and_returns_text(self):
        sent = []

        def respond(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({'prompt': 'knight girl, rain'})}})

        ai = AI(AIConfig(provider='ollama', model='local', base_url='http://pc:11434'), transport=httpx.MockTransport(respond))
        self.assertEqual(asyncio.run(ai.enhance_prompt('a knight', 'waiIllustriousSDXL_v170')), 'knight girl, rain')
        self.assertEqual(json.loads(sent[0]['messages'][-1]['content']), {'prompt': 'a knight', 'model': 'waiIllustriousSDXL_v170'})
        self.assertEqual(asyncio.run(ai.describe_image(b'\xff\xd8\xff', '')), 'knight girl, rain')
        self.assertEqual(len(sent[1]['messages'][-1]['images']), 1)


class PromptHelpApiTests(unittest.TestCase):
    def setUp(self):
        self.original_token = main.TOKEN
        main.TOKEN = 'secret123'
        self.client = TestClient(main.app)
        self.headers = {'Authorization': 'Bearer secret123'}

    def tearDown(self):
        main.TOKEN = self.original_token

    def test_model_label_drops_pc_folders(self):
        self.assertEqual(main.model_label('SDXL\\juggernautXL_ragnarok.safetensors'), 'juggernautXL_ragnarok')
        self.assertEqual(main.model_label('SDXL/waiIllustriousSDXL_v170.safetensors'), 'waiIllustriousSDXL_v170')

    def test_enhance(self):
        ai = MagicMock(enhance_prompt=AsyncMock(return_value='better'))
        with patch.object(main.adviser, 'ai', return_value=ai):
            self.assertEqual(self.client.post('/api/prompt/enhance', json={'prompt': 'x'}).status_code, 401)
            self.assertEqual(self.client.post('/api/prompt/enhance', headers=self.headers, json={'prompt': ' '}).status_code, 400)
            r = self.client.post('/api/prompt/enhance', headers=self.headers, json={'prompt': 'cat', 'model': 'SDXL/juggernautXL_ragnarok.safetensors'})
            self.assertEqual(r.json(), {'prompt': 'better'})
            ai.enhance_prompt.assert_awaited_with('cat', 'juggernautXL_ragnarok')
            ai.enhance_prompt.side_effect = AIError('Provider connection failed or timed out')
            self.assertEqual(self.client.post('/api/prompt/enhance', headers=self.headers, json={'prompt': 'cat'}).status_code, 503)

    def test_describe_reads_only_phone_uploads(self):
        ai = MagicMock(describe_image=AsyncMock(return_value='a photo'))
        fetch = AsyncMock(return_value=b'\xff\xd8\xff')
        with patch.object(main.adviser, 'vision', return_value=ai), patch.object(main, 'bounded_get', fetch):
            bad = self.client.post('/api/prompt/describe', headers=self.headers, json={'upload': '../secret.png'})
            self.assertEqual(bad.status_code, 400)
            r = self.client.post('/api/prompt/describe', headers=self.headers, json={'upload': UPLOAD})
            self.assertEqual(r.json(), {'prompt': 'a photo'})
            self.assertEqual(fetch.await_args.kwargs['params']['type'], 'input')
            self.assertEqual(fetch.await_args.kwargs['params']['filename'], UPLOAD)


if __name__ == '__main__':
    unittest.main()
