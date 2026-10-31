import tempfile
import unittest
from pathlib import Path
from app.adviser import propose, targets, fingerprint
from app.adviser_store import Store

GRAPH={
 '1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'Example_v1.safetensors'}},
 '2':{'class_type':'KSampler','inputs':{'model':['1',0],'steps':30,'cfg':7.0,'sampler_name':'euler','scheduler':'normal','denoise':1.0}},
 '3':{'class_type':'SaveImage','inputs':{'images':['2',0]}},
}
CONTROLS={'samplers':[{'node':'2','role':'base','values':{'steps':30,'cfg':7.0,'sampler_name':'euler','scheduler':'normal'}}], 'sampler_options':['euler','dpmpp_2m'],'scheduler_options':['normal'],'size':None}
FACT={'field':'steps','low':20,'high':25,'value':'','quote':'Steps: 20-25','source_id':'s1'}


class AdviserTests(unittest.TestCase):
    def test_outside_range_only_and_conflicts(self):
        cards=[{'file':'Example_v1.safetensors','facts':[FACT]}]
        proposals,notes=propose(GRAPH,CONTROLS,cards)
        self.assertEqual(25,proposals[0]['changes'][0]['value'])
        self.assertEqual([],propose(GRAPH,CONTROLS,[{**cards[0],'facts':[{**FACT,'high':35}]}])[0])
        self.assertEqual([],propose(GRAPH,CONTROLS,[{**cards[0],'facts':[FACT,{**FACT,'low':10,'high':15}]}])[0])

    def test_only_active_reachable_models_and_ambiguous_branches(self):
        self.assertEqual('Example_v1.safetensors',targets(GRAPH)[0]['file'])
        g={**GRAPH,'unused':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'ignored'}}}
        self.assertEqual(1,len(targets(g)))
        g={**GRAPH,'4':{'class_type':'UNETLoader','inputs':{'unet_name':'other'}},'5':{'class_type':'SaveImage','inputs':{'images':['4',0]}}}
        self.assertEqual([],propose(g,CONTROLS,[{'file':'Example_v1.safetensors','facts':[FACT]}])[0])

    def test_store_expiry_owner_cache_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder))
            store.put('review','id',{'owner':'a','status':'done'},ttl=600)
            self.assertIsNone(store.get('review','id',owner='b'))
            self.assertEqual('done',Store(Path(folder)).get('review','id',owner='a')['status'])
            store.put('cache','expired',{'x':1},ttl=-1)
            self.assertIsNone(store.get('cache','expired'))
            store.put('review','pending',{'owner':'a','status':'researching'},ttl=600)
            store.interrupt()
            self.assertEqual('interrupted',store.get('review','pending',owner='a')['status'])

    def test_fingerprint_binds_seed_graph_and_schema(self):
        self.assertNotEqual(fingerprint(GRAPH,{},'source'),fingerprint(GRAPH,{'options':['new']},'source'))


class TaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_deterministic_extraction_cache_and_cancel(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.adviser import Adviser
        source={'id':'s1','url':'https://example.org/model','text':'Example v1. Inference steps: 20-25.','excerpt':'Inference steps: 20-25.','verified':True}
        with tempfile.TemporaryDirectory() as folder:
            adviser=Adviser(Path(folder))
            async def finish():
                for _ in range(100):
                    await asyncio.sleep(.001)
                    if not adviser.tasks: return
                self.fail('background review did not finish')
            gather=AsyncMock(return_value=([source],[{'phase':'search','results':1}]))
            with patch('app.adviser.Research.gather',gather),patch.dict('os.environ',{'ADVISER_MODEL':''}):
                one=adviser.start('owner',GRAPH,CONTROLS,'source',{},[42])
                await finish()
                row=adviser.get(one['id'],'owner')
                self.assertEqual('done',row['status'])
                self.assertEqual(1,len(row['proposals']))
                two=adviser.start('owner',GRAPH,CONTROLS,'source',{},[43])
                await finish()
                self.assertTrue(adviser.get(two['id'],'owner')['cards'][0]['cached'])
                self.assertEqual(1,gather.await_count)
                three=adviser.start('owner',GRAPH,CONTROLS,'source',{},[44])
                adviser.cancel(three['id'],'owner')
                await finish()
                self.assertEqual('cancelled',adviser.get(three['id'],'owner')['status'])

class RefinementRegressionTests(unittest.TestCase):
    def test_identifies_base_and_leaves_downstream_refiner_unchanged(self):
        import copy
        g=copy.deepcopy(GRAPH)
        g['4']={'class_type':'KSampler','inputs':{**g['2']['inputs'],'latent_image':['2',0],'denoise':0.4,'steps':15}}
        g['3']['inputs']['images']=['4',0]
        controls=copy.deepcopy(CONTROLS)
        controls['samplers'].append({'node':'4','role':'refine','values':{'steps':15,'cfg':7}})
        proposals,notes=propose(g,controls,[{'file':'Example_v1.safetensors','facts':[FACT]}])
        self.assertEqual(['2'],[c['node'] for p in proposals for c in p['changes']])
        self.assertTrue(any('refinement' in note for note in notes))
        # A separate branch at low denoise is not established as this base pass's refinement.
        del g['4']['inputs']['latent_image']
        self.assertEqual([],propose(g,controls,[{'file':'Example_v1.safetensors','facts':[FACT]}])[0])

class AdvancedScheduleRegressionTests(unittest.TestCase):
    def test_split_advanced_noise_schedules_remain_manual(self):
        import copy
        from app.workflow_controls import _samplers
        g=copy.deepcopy(GRAPH)
        g['2']['class_type']='KSamplerAdvanced'
        g['2']['inputs'].update(start_at_step=0,end_at_step=20,add_noise='enable',return_with_leftover_noise='enable')
        g['4']={'class_type':'KSamplerAdvanced','inputs':{**g['2']['inputs'],'latent_image':['2',0], 'start_at_step':20,'end_at_step':30,'add_noise':'disable','return_with_leftover_noise':'disable'}}
        g['3']['inputs']['images']=['4',0]
        controls={**CONTROLS,'samplers':_samplers(g)}
        self.assertEqual([],propose(g,controls,[{'file':'Example_v1.safetensors','facts':[FACT]}])[0])
