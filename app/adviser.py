"""Research tasks and conservative proposals; no graph execution or arbitrary AI patches."""
import asyncio
import copy
import math
import hashlib
import json
import os
import secrets
import time

try:
    from .research import Research, candidates, verify_facts, VARIANT
    from .adviser_ai import AI, AIError, AIConfig, DEFAULT_URLS
    from .adviser_store import Store
except ImportError:
    from research import Research, candidates, verify_facts, VARIANT
    from adviser_ai import AI, AIError, AIConfig, DEFAULT_URLS
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


def workflow_context(graph,controls):
    graph=reachable(graph)
    controls={**controls,'samplers':[c for c in controls['samplers'] if c['node'] in graph]}
    active=targets(graph)
    models=[t['file'] for t in active if t['kind']=='model']
    base=[c for c in controls['samplers'] if c['role']=='base' and graph.get(c['node'],{}).get('class_type')=='KSampler']
    other_base=[c for c in controls['samplers'] if c['role']=='base' and graph.get(c['node'],{}).get('class_type') in ('KSamplerAdvanced','SamplerCustom','SamplerCustomAdvanced')]
    allowed=[]
    complex_pass=any(n['class_type'] in ('KSamplerAdvanced','SamplerCustom','SamplerCustomAdvanced') for n in reachable(graph).values())
    if len(models)==1 and len(base)==1 and not other_base and not complex_pass and _base_chain(graph,base[0]['node']) and not any(t['kind']=='lora' and VARIANT.search(t['file']) for t in active):
        for field,current in base[0]['values'].items():
            if field in ('steps','cfg','sampler_name','scheduler'):
                cap={'node':base[0]['node'],'field':field,'current':current}
                if field in ('steps','cfg'):cap.update(min=1 if field=='steps' else 0,max=150 if field=='steps' else 30)
                else:cap['options']=controls.get('sampler_options' if field=='sampler_name' else 'scheduler_options',[])
                allowed.append(cap)
    return {'models':models,'loras':[t['file'] for t in active if t['kind']=='lora'],
            'samplers':controls['samplers'],'capabilities':allowed}


def ai_proposals(context,report):
    capabilities={(c['node'],c['field']):c for c in context['capabilities']}
    proposals=[]; seen=set()
    for change in report.get('changes',[]):
        key=(change['node'],change['field']); cap=capabilities.get(key)
        if not cap or key in seen: continue
        value=change['value'];field=change['field']
        if field in ('steps','cfg'):
            try:value=float(value)
            except (ValueError,TypeError):continue
            if not math.isfinite(value) or not cap['min']<=value<=cap['max'] or (field=='steps' and value%1):continue
            if field=='steps':value=int(value)
        elif value not in cap['options']:continue
        if value==cap['current']:continue
        seen.add(key)
        proposals.append({'id':'ai-'+str(len(proposals)),'basis':'ai','label':f"{field} · node {change['node']} · AI suggestion",
                          'changes':[{'node':change['node'],'field':field,'current':cap['current'],'value':value}],
                          'reason':change['reason'],'evidence':[]})
    return proposals


def merge_proposals(published,ai):
    fields={(c.get('node'),c['field']) for p in published for c in p['changes']}
    return [{**p,'basis':'published'} for p in published]+[p for p in ai if not any((c.get('node'),c['field']) in fields for c in p['changes'])]


class Adviser:
    def __init__(self,root):
        self.store=Store(root); self.tasks={}; self.lock=asyncio.Lock()

    def config(self):
        try: saved=self.store.get('config','ai')
        except (ValueError,OSError): saved=None
        return AIConfig(**saved) if saved else AIConfig.env()

    def ai(self):
        return AI(self.config())

    def public_config(self):
        cfg=self.config()
        return {'provider':cfg.provider,'model':cfg.model,'base_url':cfg.base_url,'has_key':bool(cfg.api_key)}

    def proposed_config(self,values):
        old=self.config()
        base=values.get('base_url','').strip() or DEFAULT_URLS.get(values['provider'],'')
        key=values.get('api_key','').strip()
        if not key and old.provider==values['provider'] and old.base_url.rstrip('/')==base.rstrip('/'):
            key=old.api_key
        return AIConfig(values['provider'],values.get('model','').strip(),base,key)

    def save_config(self,cfg):
        from dataclasses import asdict
        self.store.put('config','ai',asdict(cfg),ttl=10*365*86400)
        os.chmod(self.store.path,0o600)

    def status(self):
        cfg=self.config()
        return {'provider':cfg.provider,'model':cfg.model,'ai_configured':not bool(cfg.problem()),
                'search_configured':bool(os.getenv('SEARXNG_URL','')),
                'problem':cfg.problem(), 'hosted':cfg.provider!='ollama'}

    def get(self,key,owner):
        row=self.store.get('review',key,owner=owner)
        if not row: raise ValueError('Review missing or expired')
        return row

    def public(self,row):
        return {k:v for k,v in row.items() if k not in ('owner','graph','controls','source_hash','hashes','approved_hashes','job')}

    def start(self,owner,graph,controls,source_hash,job,seeds,refresh=False,web_research=False):
        if len(self.tasks)>=3: raise ValueError('Research queue is full; cancel a review or wait')
        key=secrets.token_urlsafe(24)
        row={'id':key,'owner':owner,'status':'queued','created':time.time(),'expires':time.time()+600,
             'graph':graph,'controls':controls,'source_hash':source_hash,'job':job,'seeds':seeds,
             'cards':[],'proposals':[],'notes':[],'ai_called':False,'web_research':web_research}
        self.store.put('review',key,row)
        self.tasks[key]=asyncio.create_task(self._run(key,owner,refresh))
        self.tasks[key].add_done_callback(lambda task:self.tasks.pop(key,None))
        return self.public(row)

    async def _run(self,key,owner,refresh):
        try:
            async with self.lock:
                async with asyncio.timeout(180):
                    row=self.get(key,owner); row['status']='researching'; self.store.put('review',key,row)
                    ai=self.ai()
                    if ai.config.problem(): raise AIError('AI is not configured: '+ai.config.problem())
                    row['phase']='Calling '+ai.config.provider+' / '+ai.config.model+' to review workflow settings'
                    self.store.put('review',key,row)
                    context=workflow_context(row['graph'],row['controls'])
                    if not context['capabilities']: row['notes'].append('This graph has no safely editable base sampler controls; the AI assessment is informational.')
                    report=await ai.review(context)
                    row['ai_called']=True;row['ai_summary']=report['summary']
                    row['ai_provider']=ai.config.provider;row['ai_model']=ai.config.model
                    row['proposals']=ai_proposals(context,report)
                    self.store.put('review',key,row)
                    ts=targets(row['graph']) if row.get('web_research') else []
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
                            ai=self.ai()
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
                    published,notes=propose(row['graph'],row['controls'],row['cards']) if row.get('web_research') else ([],[])
                    row['proposals']=merge_proposals(published,row['proposals'])
                    row['notes'].extend(notes); row['status']='done'; self.store.put('review',key,row)
        except asyncio.CancelledError:
            return
        except AIError as exc:
            row=self.get(key,owner);row['status']='failed';row['notes'].append('AI review failed: '+str(exc))
            self.store.put('review',key,row)
        except TimeoutError:
            try:
                row=self.get(key,owner); row['status']='done' if row.get('ai_called') else 'failed'; row['notes'].append('Review deadline reached; '+('AI assessment retained.' if row.get('ai_called') else 'AI did not finish.'))
                published,notes=propose(row['graph'],row['controls'],row['cards'])
                row['proposals']=merge_proposals(published,row.get('proposals',[])); row['notes'].extend(notes)
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
