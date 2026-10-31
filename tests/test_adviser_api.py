import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app import main
from app.adviser import Adviser
from tests.test_adviser import GRAPH, FACT

AUTH={'Authorization':'Bearer adviser-test'}
INFO={
 'CheckpointLoaderSimple':{'input':{'required':{'ckpt_name':[['Example_v1.safetensors']]}}},
 'KSampler':{'input':{'required':{'sampler_name':[['euler','dpmpp_2m']],'scheduler':[['normal']]}}},
 'SaveImage':{'input':{'required':{'images':['IMAGE']}}},
}


class AdviserApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.path=self.root/'one.json'; self.path.write_text(json.dumps(GRAPH))
        self.adviser=Adviser(self.root)
        self.patches=[patch.object(main,'TOKEN','adviser-test'), patch.object(main,'WF_DIR',self.root),
                      patch.object(main,'adviser',self.adviser), patch.object(main,'get_object_info',AsyncMock(return_value=INFO))]
        for p in self.patches:p.start()
        self.client=TestClient(main.app)
        self.job={'workflow_id':'one.json','seed':42}

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def seeded_review(self):
        # Seed a finished review through the same preparation path used by the routes.
        import asyncio
        job=main.Job(**self.job)
        graph,info,_,_,source_hash=asyncio.run(main.prepare_job(job))
        from app.workflow_controls import find_controls
        from app.adviser import propose
        controls=find_controls(graph,info)
        proposals,_=propose(graph,controls,[{'file':'Example_v1.safetensors','facts':[FACT]}])
        row={'id':'review','owner':main.adviser_owner(),'status':'done','graph':graph,'controls':controls,
             'source_hash':source_hash,'job':job.model_dump(exclude={'adviser_token','adviser_index'}),
             'seeds':[42,43],'cards':[],'proposals':proposals,'notes':[]}
        self.adviser.store.put('review','review',row)
        return row

    def test_auth_and_no_implicit_render(self):
        self.assertEqual(401,self.client.get('/api/adviser/status').status_code)
        self.seeded_review()
        with patch.object(main,'request',AsyncMock(side_effect=AssertionError('must not render'))):
            r=self.client.post('/api/adviser/reviews/review/apply',headers=AUTH,json={'job':self.job,'selected':['0']})
            self.assertEqual(200,r.status_code,r.text)
            self.assertEqual(25,r.json()['sampling']['2']['steps'])

    def test_changed_workflow_unknown_selection_and_seed_rejected(self):
        self.seeded_review()
        r=self.client.post('/api/adviser/reviews/review/apply',headers=AUTH,json={'job':self.job,'selected':['fake']})
        self.assertEqual(400,r.status_code)
        g=copy.deepcopy(GRAPH);g['2']['inputs']['cfg']=8
        self.path.write_text(json.dumps(g))
        r=self.client.post('/api/adviser/reviews/review/apply',headers=AUTH,json={'job':self.job,'selected':['0']})
        self.assertEqual(409,r.status_code)

    def test_generate_binds_approved_changes_batch_and_audits(self):
        self.seeded_review()
        approved=self.client.post('/api/adviser/reviews/review/apply',headers=AUTH,json={'job':self.job,'selected':['0']}).json()
        token=approved['token']
        request=AsyncMock(return_value={'prompt_id':'0f8d2a4c-1111-4222-8333-944455556666'})
        with patch.object(main,'request',request),patch.object(main.job_store,'add'):
            data={**self.job,'sampling':approved['sampling'],'adviser_token':token,'adviser_index':1,'seed':43}
            r=self.client.post('/api/jobs',headers=AUTH,json=data)
            self.assertEqual(200,r.status_code,r.text)
            self.assertEqual('review',r.json()['details']['adviser']['review_id'])
            data['seed']=44
            self.assertEqual(409,self.client.post('/api/jobs',headers=AUTH,json=data).status_code)
            self.assertEqual(1,request.await_count)

class ProviderSetupTests(unittest.TestCase):
    setUp = AdviserApiTests.setUp
    tearDown = AdviserApiTests.tearDown
    def test_save_requires_working_ai_and_never_returns_secret(self):
        body={'provider':'openai','base_url':'https://provider.example/v1','model':'chosen','api_key':'private-api-key'}
        self.assertEqual(401,self.client.get('/api/adviser/config').status_code)
        with patch('app.main.AI.test',AsyncMock(return_value={'summary':'Connected','changes':[]})):
            result=self.client.put('/api/adviser/config',headers=AUTH,json=body)
        self.assertEqual(200,result.status_code,result.text)
        self.assertNotIn('private-api-key',result.text)
        saved=self.client.get('/api/adviser/config',headers=AUTH)
        self.assertTrue(saved.json()['has_key'])
        self.assertNotIn('private-api-key',saved.text)
        self.assertEqual('private-api-key',self.adviser.config().api_key)
        # Changing destination must not forward the old key.
        self.assertEqual('',self.adviser.proposed_config({**body,'base_url':'https://other.example/v1','api_key':''}).api_key)

    def test_bad_connection_is_not_saved(self):
        from app.adviser_ai import AIError
        with patch('app.main.AI.test',AsyncMock(side_effect=AIError('Provider HTTP 401'))):
            response=self.client.put('/api/adviser/config',headers=AUTH,json={'provider':'openai','base_url':'https://provider.example/v1','model':'chosen','api_key':'bad'})
        self.assertEqual(400,response.status_code)
        self.assertIsNone(self.adviser.store.get('config','ai'))
