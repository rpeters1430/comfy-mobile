"""Research tasks and conservative proposals; no graph execution or arbitrary AI patches."""
import asyncio
import copy
import hashlib
import json
import os
import secrets
import time

try:
    from .research import Research, candidates, verify_facts, VARIANT
    from .adviser_ai import AI, AIError
    from .adviser_store import Store
except ImportError:
    from research import Research, candidates, verify_facts, VARIANT
    from adviser_ai import AI, AIError
    from adviser_store import Store


def fingerprint(graph,controls,source_hash):
    return hashlib.sha256(json.dumps([graph,controls,source_hash],sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def reachable(graph):
    roots=[k for k,n in graph.items() if n['class_type'] in ('SaveImage','SaveImageAdvanced','SaveAnimatedWEBP','VHS_VideoCombine')]
    keep=set(); pending=list(roots)
    while pending:
        key=pending.pop()
        if key in keep or key not in graph: continue
        keep.add(key)
        pending.extend(v[0] for v in graph[key]['inputs'].values() if isinstance(v,list) and len(v)==2 and isinstance(v[0],str))
    return {k:v for k,v in graph.items() if k in keep}


def targets(graph):
    seen=set(); found=[]
    for node in reachable(graph).values():
        inputs=node['inputs']
        for field,kind in (('ckpt_name','model'),('unet_name','model'),('lora_name','lora')):
            name=inputs.get(field)
            if not isinstance(name,str) or name.lower() in ('','none') or name in seen: continue
            if kind=='lora' and inputs.get('strength_model',1)==0 and inputs.get('strength_clip',0)==0: continue
            seen.add(name); found.append({'file':name,'kind':kind})
    return sorted(found,key=lambda x:x['kind']=='lora')


def _base_chain(graph,node_id):
    """Accept only model loaders, known sampling wrappers, and LoRA chains."""
    seen=set(); current=node_id
    while current not in seen and current in graph:
        seen.add(current); n=graph[current]; c=n['class_type']
        if c in ('CheckpointLoaderSimple','UNETLoader'): return True
        if c not in ('KSampler','KSamplerAdvanced','CFGGuider','BasicGuider','BasicScheduler','LoraLoader','LoraLoaderModelOnly',
                     'ModelSamplingSD3','ModelSamplingFlux','ModelSamplingDiscrete'):
            return False
        link=n['inputs'].get('model')
        if not isinstance(link,list) or len(link)!=2: return False
        current=link[0]
    return False


def propose(graph,controls,cards):
    graph=reachable(graph); notes=[]; result=[]
    active=targets(graph)
    if any(t['kind']=='lora' and VARIANT.search(t['file']) for t in active):
        return [],['Acceleration LoRA combinations require combined model-specific evidence; settings remain manual.']
    models=[x for x in active if x['kind']=='model']
    samplers=[(k,n) for k,n in graph.items() if n['class_type'] in ('KSampler','KSamplerAdvanced','SamplerCustom','SamplerCustomAdvanced')]
    base=[c for c in controls['samplers'] if c['role']=='base' and c['node'] in graph]
    roles={c['node']:c['role'] for c in controls['samplers']}
    advanced=[n for k,n in samplers if n['class_type']=='KSamplerAdvanced']
    # Advanced continuation passes share a sigma schedule; changing one pass alone is unsafe.
    if advanced and (len(samplers)>1 or any(n['inputs'].get('return_with_leftover_noise')=='enable' or
        not isinstance(n['inputs'].get('end_at_step',10000),(int,float)) or
        n['inputs'].get('end_at_step',10000)<n['inputs'].get('steps',150) for n in advanced)):
        return [],['Partial or connected advanced sampler schedules remain manual to preserve noise-schedule continuity.']
    base_passes=[(k,n) for k,n in samplers if roles.get(k)=='base' or n['class_type'] in ('SamplerCustom','SamplerCustomAdvanced')]
    if len(models)!=1 or len(base_passes)!=1 or not base:
        return [],[f'Workflow analysis: {len(models)} active model files, {len(samplers)} sampler passes, {len(base_passes)} identifiable base passes. Changes require one identifiable base pass; research remains available.']
    main_id,main=base_passes[0]
    def upstream(root):
        seen=set(); pending=[root]
        while pending:
            key=pending.pop()
            if key in seen or key not in graph: continue
            seen.add(key)
            pending.extend(v[0] for v in graph[key]['inputs'].values() if isinstance(v,list) and len(v)==2 and isinstance(v[0],str))
        return seen
    refinement=[k for k,n in samplers if k!=main_id]
    if any(roles.get(k)!='refine' or main_id not in upstream(k) for k in refinement):
        return [],['Additional sampler branches are not proven downstream refinements; settings remain manual.']
    if refinement: notes.append(f'Base sampler {main_id} identified; {len(refinement)} downstream refinement passes are left unchanged.')
    main_inputs=upstream(main_id)
    base=[c for c in base if c['node'] in main_inputs]
    if main['class_type'] in ('KSampler','KSamplerAdvanced'):
        if not any(c['node']==main_id for c in base) or not _base_chain(graph,main_id):
            return [],['Refine passes or model wrapper chains are unsupported.']
    else:
        chain_controls=[c for c in base if c.get('class') in ('CFGGuider','BasicScheduler')]
        if not chain_controls or not all(_base_chain(graph,c['node']) for c in chain_controls):
            return [],['Model wrapper chain is unsupported.']
    facts=[f for card in cards if card['file']==models[0]['file'] for f in card.get('facts',[])]
    for cap in base:
        for field,current in cap['values'].items():
            if field not in ('steps','cfg','sampler_name','scheduler'): continue
            eligible=[f for f in facts if f['field']==field]
            unique={(f.get('low'),f.get('high'),f.get('value','')) for f in eligible}
            if len(unique)!=1:
                if unique: notes.append(f'Conflicting {field} evidence; no change proposed.')
                continue
            f=eligible[0]; value=None
            if field in ('steps','cfg'):
                if current<f['low']: value=f['low']
                elif current>f['high']: value=f['high']
                if value is not None and field=='steps': value=int(value)
            else:
                options=controls.get('sampler_options' if field=='sampler_name' else 'scheduler_options',[])
                norm=lambda s: ''.join(c for c in s.lower().replace('++','pp') if c.isalnum())
                matches=[o for o in options if norm(o)==norm(f['value'])]
                if len(matches)==1 and matches[0]!=current: value=matches[0]
            if value is not None:
                result.append({'id':str(len(result)),'label':f'{field} · node {cap["node"]}',
                               'changes':[{'node':cap['node'],'field':field,'current':current,'value':value}],
                               'evidence':[f], 'reason':'Current value is outside the source recommendation.'})
    # Only propose size when a unique plain latent is adjustable; fit/output resolution remains unchanged.
    latents=[k for k,n in graph.items() if n['class_type'].startswith('Empty') and 'LatentImage' in n['class_type']]
    sizes=[f for f in facts if f['field']=='resolution']
    values={f['value'] for f in sizes}
    if len(values)==1 and len(latents)==1 and controls.get('size'):
        w,h=map(int,next(iter(values)).split('x'))
        if all(256<=v<=2880 and v%8==0 for v in (w,h)) and w*h<=2048*2048 and controls['size']!={'width':w,'height':h}:
            result.append({'id':str(len(result)),'label':'Render size (paired)','changes':[{'field':'size','current':controls['size'],'value':{'width':w,'height':h}}],
                           'evidence':[sizes[0]],'reason':'Published render size; final wallpaper fit is retained.'})
    return result,notes


class Adviser:
    def __init__(self,root):
        self.store=Store(root); self.tasks={}; self.lock=asyncio.Lock()

    def status(self):
        cfg=AI().config
        return {'provider':cfg.provider,'model':cfg.model,'ai_configured':not bool(cfg.problem()),
                'search_configured':bool(os.getenv('SEARXNG_URL','')),
                'problem':cfg.problem(), 'hosted':cfg.provider!='ollama'}

    def get(self,key,owner):
        row=self.store.get('review',key,owner=owner)
        if not row: raise ValueError('Review missing or expired')
        return row

    def public(self,row):
        return {k:v for k,v in row.items() if k not in ('owner','graph','controls','source_hash','hashes','approved_hashes','job')}

    def start(self,owner,graph,controls,source_hash,job,seeds,refresh=False):
        if len(self.tasks)>=3: raise ValueError('Research queue is full; cancel a review or wait')
        key=secrets.token_urlsafe(24)
        row={'id':key,'owner':owner,'status':'queued','created':time.time(),'expires':time.time()+600,
             'graph':graph,'controls':controls,'source_hash':source_hash,'job':job,'seeds':seeds,
             'cards':[],'proposals':[],'notes':[]}
        self.store.put('review',key,row)
        self.tasks[key]=asyncio.create_task(self._run(key,owner,refresh))
        self.tasks[key].add_done_callback(lambda task:self.tasks.pop(key,None))
        return self.public(row)

    async def _run(self,key,owner,refresh):
        try:
            async with self.lock:
                async with asyncio.timeout(180):
                    row=self.get(key,owner); row['status']='researching'; self.store.put('review',key,row)
                    ts=targets(row['graph'])
                    if len(ts)>16: row['notes'].append('Only the first 16 active models are researched.')
                    for i,target in enumerate(ts[:16]):
                        cachekey=hashlib.sha256(json.dumps([os.getenv('COMFYUI_URL',''),target,4],sort_keys=True).encode()).hexdigest()
                        card=None if refresh else self.store.get('cache',cachekey)
                        if card:
                            card={**card,'cached':True}
                        else:
                            sources,diagnostics=await Research(os.getenv('SEARXNG_URL','')).gather(target)
                            raw=[]
                            for src in sources:
                                raw.extend({**f,'source_id':src['id']} for f in candidates(src['text']))
                            facts,rejected=verify_facts(raw,sources,target['file'])
                            ai=AI()
                            if any(s.get('verified') for s in sources) and not ai.config.problem():
                                try:
                                    assisted=await ai.extract(target,[s for s in sources if s.get('verified')])
                                    extra,bad=verify_facts(assisted,sources,target['file'])
                                    facts.extend(f for f in extra if f not in facts); rejected.extend(bad)
                                    diagnostics.append({'phase':'ai','reason':'Structured response validated','accepted':len(extra)})
                                except AIError as exc:
                                    diagnostics.append({'phase':'ai','reason':str(exc)})
                            elif ai.config.problem():
                                diagnostics.append({'phase':'ai','reason':ai.config.problem()+'; explicit numeric extraction remains available'})
                            card={**target,'facts':facts,'sources':[{k:v for k,v in s.items() if k!='text'} for s in sources],
                                  'diagnostics':diagnostics,'rejected':rejected,'retrieved':time.time(),'cached':False}
                            self.store.put('cache',cachekey,card,ttl=30*86400 if facts else 3600)
                        # Source IDs are local to a card; retain file context in all evidence.
                        for fact in card['facts']: fact['model_file']=target['file']
                        row['cards'].append(card)
                        row['phase']=f'Researched {i+1}/{min(len(ts),16)} models'
                        self.store.put('review',key,row)
                    row['proposals'],notes=propose(row['graph'],row['controls'],row['cards'])
                    row['notes'].extend(notes); row['status']='done'; self.store.put('review',key,row)
        except asyncio.CancelledError:
            return
        except TimeoutError:
            try:
                row=self.get(key,owner); row['status']='done'; row['notes'].append('Research deadline reached; partial results shown.')
                row['proposals'],notes=propose(row['graph'],row['controls'],row['cards']); row['notes'].extend(notes)
                self.store.put('review',key,row)
            except ValueError: pass
        except Exception:
            try:
                row=self.get(key,owner); row['status']='failed'; row['notes'].append('Research failed; inspect source diagnostics or retry.')
                self.store.put('review',key,row)
            except (ValueError,OSError): pass

    def cancel(self,key,owner):
        row=self.get(key,owner)
        task=self.tasks.get(key)
        if task: task.cancel()
        row['status']='cancelled'; self.store.put('review',key,row)
        return self.public(row)

    async def shutdown(self):
        tasks=list(self.tasks.values())
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
        self.store.interrupt()
