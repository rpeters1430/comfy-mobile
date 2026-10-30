import unittest
from app import caption_review as review

INFO = {
 'LoadImage': {'output': ['IMAGE']},
 'Qwen3VLAbliteratedPromptPair': {'output': ['STRING', 'STRING', 'INT', 'INT']},
 'PreviewAny': {'output_node': True, 'input': {'required': {'source': ['*']}}},
 'SaveImage': {'output_node': True},
 'KSampler': {}, 'CheckpointLoaderSimple': {}, 'CLIPTextEncode': {},
}


def graph():
 return {
  '1': {'class_type': 'LoadImage', 'inputs': {'image': 'mobile_' + 'a' * 32 + '.png'}},
  '2': {'class_type': 'Qwen3VLAbliteratedPromptPair', 'inputs': {'image': ['1', 0]}},
  '3': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': 'model'}},
  '4': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['3', 1], 'text': ['2', 0]}},
  '5': {'class_type': 'KSampler', 'inputs': {'positive': ['4', 0], 'model': ['3', 0]}},
  '6': {'class_type': 'SaveImage', 'inputs': {'images': ['5', 0]}},
 }


class CaptionTests(unittest.TestCase):
 def test_caption_only_runs_no_diffusion_and_keeps_image(self):
  original = graph()
  result = review.caption_graph(original, INFO)
  self.assertEqual({'LoadImage', 'Qwen3VLAbliteratedPromptPair', 'PreviewAny'}, {n['class_type'] for n in result.values()})
  self.assertEqual(['2', 0], result['mobile_review_positive']['inputs']['source'])
  self.assertEqual(['2', 1], result['mobile_review_negative']['inputs']['source'])
  self.assertIn('5', original)

 def test_approved_caption_replaces_text_and_removes_vlm(self):
  result = review.apply_review(graph(), INFO, 'edited portrait', 'blur')
  self.assertEqual('edited portrait', result['4']['inputs']['text'])
  self.assertNotIn('2', result)
  self.assertNotIn('1', result)
  self.assertIn('6', result)

 def test_ambiguous_caption_and_non_text_consumers_fail_closed(self):
  for change in ('second', 'dimensions'):
   g = graph()
   if change == 'second': g['7'] = {'class_type': 'Qwen3VLAbliteratedPromptPair', 'inputs': {'image': ['1', 0]}}
   else: g['5']['inputs']['width'] = ['2', 2]
   self.assertFalse(review.capability(g, INFO)['supported'])
   with self.assertRaises(review.CaptionError): review.caption_graph(g, INFO)

 def test_missing_preview_output_explains_unsupported_review(self):
  info = {k: v for k, v in INFO.items() if k != 'PreviewAny'}
  result = review.capability(graph(), info)
  self.assertFalse(result['supported'])
  self.assertIn('PreviewAny', result['reason'])


class CaptionApiTests(unittest.TestCase):
 def test_preview_and_approved_render_are_separate_jobs(self):
  import json
  from pathlib import Path
  from tempfile import TemporaryDirectory
  from unittest.mock import patch
  from fastapi.testclient import TestClient
  import app.main as main
  from app.job_store import JobStore
  submitted = []
  async def info(*args, **kwargs): return INFO
  async def request(method, path, **kwargs):
   self.assertEqual('/prompt', path)
   submitted.append(kwargs['json']['prompt'])
   return {'prompt_id': '0f8d2a4c-1111-4222-8333-944455556666'}
  with TemporaryDirectory() as tmp:
   root = Path(tmp)
   (root / 'review.json').write_text(json.dumps(graph()))
   store = JobStore(root)
   with patch.object(main, 'WF_DIR', root), patch.object(main, 'TOKEN', 'token'), patch.object(main, 'job_store', store), \
        patch.object(main, 'get_object_info', info), patch.object(main, 'request', request):
    client = TestClient(main.app)
    job = {'workflow_id': 'review.json', 'images': ['mobile_' + 'a' * 32 + '.png'], 'stage': 'caption'}
    response = client.post('/api/jobs', json=job, headers={'Authorization': 'Bearer token'})
    self.assertEqual(200, response.status_code, response.text)
    self.assertNotIn('KSampler', {n['class_type'] for n in submitted[-1].values()})
    self.assertEqual('caption', store.get(response.json()['prompt_id'])['params']['stage'])
    job.update(stage='render', review={'positive': 'my edit', 'negative': 'blur'})
    response = client.post('/api/jobs', json=job, headers={'Authorization': 'Bearer token'})
    self.assertEqual(200, response.status_code, response.text)
    self.assertEqual('my edit', submitted[-1]['4']['inputs']['text'])
    self.assertNotIn('2', submitted[-1])
    self.assertEqual('model', store.get(response.json()['prompt_id'])['details']['models'][0]['value'])
    self.assertEqual('model', response.json()['details']['models'][0]['value'])
