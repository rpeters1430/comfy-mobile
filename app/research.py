"""Bounded public model research and literal, model-specific evidence validation."""
import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit, urljoin, quote

import httpx
import trafilatura
from lxml import html

LABEL = r'(?:num_inference_steps|sampling[ _]+steps?|inference[ _]+steps?|steps?|cfg(?:[ _]+scale)?|guidance(?:[ _]+scale)?)'
NUMBER = re.compile(r'(?<![a-z0-9_])(?P<label>' + LABEL + r')[\s:=|*`_\-]*(?P<a>\d+(?:\.\d+)?)(?:\s*(?:[-–—]|to)\s*(?P<b>\d+(?:\.\d+)?))?',re.I)
SETTING = re.compile(r'steps?|cfg|guidance|sampler|scheduler|resolution|recommended',re.I)
VARIANT = re.compile(r'turbo|lightning|hyper|lcm|dmd|schnell|distill|flash|swift',re.I)
TRAINING = re.compile(r'train(?:ing)?|learning.rate|optimizer|epoch|warmup',re.I)
SOURCE_HOSTS = {'huggingface.co','civitai.com','github.com','raw.githubusercontent.com','docs.comfy.org'}


class ResearchError(ValueError):
    pass


def identity_matches(filename, text):
    stem = re.split(r'[/\\]',filename)[-1].rsplit('.',1)[0]
    stem = re.sub(r'(?:[_ .-](?:fp(?:8|16|32)|bf16|pruned|q[2-8](?:_k(?:_[ms])?)?))+$','',stem,flags=re.I)
    norm = lambda s: re.sub(r'[^a-z0-9]','',s.casefold())
    name = norm(stem)
    # A source mentioning a different acceleration variant is ambiguous, even if title matches.
    variants = {x.lower() for x in VARIANT.findall(text)}
    version = re.search(r'(?:^|[_ .-])v(\d+(?:\.\d+)*)',stem,re.I)
    if version and not re.search(r'(?<![a-z0-9])v?'+re.escape(version[1])+r'(?![\d.])',text,re.I):
        return False
    return len(name) >= 6 and name in norm(text) and variants <= {x.lower() for x in VARIANT.findall(stem)}


def extract_document(raw, url):
    if '<' not in raw[:2000]:
        return raw[:100_000]
    try:
        tree = html.fromstring(raw)
        # Preserve small label-value tables that article extractors may otherwise discard.
        rows = [' | '.join(' '.join(c.itertext()).strip() for c in row.xpath('./th|./td')) for row in tree.xpath('//tr')]
        main = trafilatura.extract(raw, url=url, output_format='markdown', include_tables=True,
                                   include_comments=False, favor_recall=True) or ''
        return (main + '\n' + '\n'.join(r for r in rows if SETTING.search(r)))[:100_000]
    except (ValueError, TypeError):
        return ''


def settings_excerpt(text):
    # Windows around the match, not the first N characters of a possibly huge line.
    windows = []
    for match in list(SETTING.finditer(text))[:24]:
        start, end = max(0,match.start()-120), min(len(text),match.end()+250)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(end,windows[-1][1]))
        else:
            windows.append((start,end))
    return '\n…\n'.join(text[a:b] for a,b in windows)[:3500]


def candidates(text):
    found=[]
    for m in NUMBER.finditer(text):
        # Stay within the sentence/paragraph containing the label and its values.
        start=max(text.rfind('\n\n',0,m.start())+2, text.rfind('. ',0,m.start())+2,0)
        end=min(len(text),m.end()+60)
        context=text[start:end]
        if TRAINING.search(context) or VARIANT.search(context):
            continue
        a=float(m['a']); b=float(m['b'] or m['a'])
        field='cfg' if re.search(r'cfg|guidance',m['label'],re.I) else 'steps'
        high=150 if field=='steps' else 30
        if not 0 < a <= b <= high or (field=='steps' and (a%1 or b%1)):
            continue
        found.append({'field':field,'low':a,'high':b,'value':'','quote':m[0]})
    return found


def unsafe_context(text, quote_text):
    # Validate the source context, not an AI-selected fragment that can omit a training/variant heading.
    pattern=r'\s+'.join(re.escape(word) for word in quote_text.split())
    locations=list(re.finditer(pattern,text)) if pattern else []
    if not locations: return True
    for match in locations:
        before=text[:match.start()]
        paragraph=before.rsplit('\n\n',1)[-1][-600:]
        stack=[]; plain=''; section_start=0
        for line in before.splitlines():
            heading=re.match(r'^(#{1,6})\s+(.+)',line)
            if heading:
                level=len(heading[1]); stack=[h for h in stack if h[0]<level]
                stack.append((level,heading[2])); plain=''; section_start=before.rfind(line)+len(line)
            elif re.fullmatch(r'\s*(?:training|inference|sampling|recommended|generation|lightning|turbo|hyper|lcm|dmd)(?:\s+(?:settings|parameters|configuration|details))?\s*:?\s*',line,re.I):
                plain=line; section_start=before.rfind(line)+len(line)
        paragraph=before[section_start:].rsplit('\n\n',1)[-1][-600:]
        scope=paragraph+' '+' '.join(h[1] for h in stack)+' '+plain+' '+match[0]
        if TRAINING.search(scope) or VARIANT.search(scope): return True
    return False


def verify_facts(facts,sources,filename):
    by_id={s['id']:s for s in sources}
    good=[]; rejected=[]
    for fact in facts:
        src=by_id.get(fact.get('source_id'))
        q=fact.get('quote','')
        norm=lambda s: re.sub(r'\s+',' ',s).strip()
        reason=''
        if not src or not src.get('verified'):
            reason='Model/version identity unverified'
        elif not isinstance(q,str) or not 3<=len(q)<=600 or norm(q) not in norm(src.get('text','')):
            reason='Supporting quote is not in extracted source'
        elif TRAINING.search(q) or VARIANT.search(q) or unsafe_context(src.get('text',''),q):
            reason='Training or acceleration-variant evidence needs explicit support'
        elif fact.get('field') in ('steps','cfg'):
            matches=candidates(q)
            if not any(all(m[k]==fact.get(k) for k in ('field','low','high')) for m in matches):
                reason='Numeric bounds do not match the quoted setting'
        elif fact.get('field') in ('sampler_name','scheduler'):
            value=fact.get('value','')
            norm_opt=lambda v: re.sub(r'[^a-z0-9]','',v.lower().replace('++','pp'))
            if not value or norm_opt(value) not in norm_opt(q) or not re.search(r'sampl|schedul',q,re.I):
                reason='Named option is not supported by the quote'
        elif fact.get('field')=='resolution':
            if not re.fullmatch(r'\d{3,4}x\d{3,4}',fact.get('value','')) or fact['value'] not in q.replace('×','x').replace(' ',''):
                reason='Resolution is not a quoted pair'
        else:
            reason='Unsupported setting'
        if reason:
            rejected.append({'field':fact.get('field'),'reason':reason})
        elif fact not in good:
            good.append(fact)
    return good,rejected


async def public_get(url, *, accept='text/html', limit=256_000):
    """Pin each connection to validated public DNS results, including every redirect."""
    for _ in range(4):
        parsed=urlsplit(url)
        host=parsed.hostname or ''
        if parsed.scheme!='https' or host not in SOURCE_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ResearchError('Source URL is not an allowed public HTTPS source')
        addresses=await asyncio.to_thread(socket.getaddrinfo,host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ResearchError('Source resolved to a non-public address')
        ip=addresses[0][4][0]
        pinned=httpx.URL(url).copy_with(host=ip)
        async with httpx.AsyncClient(timeout=httpx.Timeout(12,connect=4),follow_redirects=False,trust_env=False) as client:
            async with client.stream('GET',pinned,headers={'Host':host,'Accept':accept,'User-Agent':'Comfy-Mobile model research'},extensions={'sni_hostname':host}) as response:
                if response.status_code in (301,302,303,307,308):
                    url=urljoin(url,response.headers.get('location',''))
                    continue
                if response.status_code!=200:
                    raise ResearchError(f'Source HTTP {response.status_code}')
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>limit:
                        raise ResearchError('Source exceeds size limit')
                return data.decode('utf-8',errors='replace')
    raise ResearchError('Source redirected too many times')


def model_words(filename):
    stem=re.split(r'[/\\]',filename)[-1].rsplit('.',1)[0]
    stem=re.sub(r'(?<=[a-z])(?=[A-Z])',' ',stem)
    stem=re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])',' ',stem)
    return re.sub(r'[_-]+',' ',stem).strip()


def search_queries(filename):
    basename=re.split(r'[/\\]',filename)[-1]
    return [f'"{basename}" model recommended settings',
            model_words(filename)+' model recommended inference settings steps cfg sampler']


def relevant_result(filename,text):
    ignored={'sdxl','sd15','lora','model','nsfw','safetensors','fp16','fp8','bf16','all','one'}
    words=[w.lower() for w in re.findall(r'[A-Za-z]{3,}',model_words(filename)) if w.lower() not in ignored]
    normalized=re.sub(r'[^a-z0-9]','',text.lower())
    return bool(words) and any(w in normalized for w in words)


def civitai_version(model,filename):
    basename=re.split(r'[/\\]',filename)[-1].casefold()
    versions=[v for v in model.get('modelVersions',[]) if any(f.get('name','').casefold()==basename for f in v.get('files',[]))]
    return versions[0] if len(versions)==1 else None


class Research:
    def __init__(self,searxng_url):
        self.url=searxng_url.rstrip('/')

    async def search(self,query):
        async with httpx.AsyncClient(timeout=httpx.Timeout(18,connect=4),follow_redirects=False,trust_env=False) as client:
            async with client.stream('GET',self.url+'/search',params={'q':query,'format':'json'}) as r:
                if r.status_code!=200:
                    raise ResearchError('SearXNG JSON disabled (403)' if r.status_code==403 else f'Search HTTP {r.status_code}')
                data=bytearray()
                async for chunk in r.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>256_000: raise ResearchError('Search response exceeds limit')
        results=json.loads(data).get('results',[])
        if not isinstance(results,list): raise ResearchError('Search results are not a list')
        return results[:8]

    async def gather(self,target):
        if not self.url:
            return [],[{'phase':'search','reason':'SEARXNG_URL is not configured'}]
        filename=target['file']; sources=[]; mismatches=[]; diagnostics=[]; results=[]; seen=set()
        for query in search_queries(filename):
            try:
                found=await self.search(query)
                diagnostics.append({'phase':'search','query':query,'results':len(found)})
                for item in found:
                    if not isinstance(item,dict) or not isinstance(item.get('url'),str): continue
                    url=item['url']
                    if url in seen: continue
                    seen.add(url)
                    if not relevant_result(filename,str(item.get('title',''))+' '+url):
                        diagnostics.append({'phase':'relevance','reason':'Unrelated model result skipped','title':str(item.get('title',''))[:100]})
                        continue
                    results.append(item)
            except (httpx.HTTPError,ValueError,AttributeError,TypeError) as exc:
                diagnostics.append({'phase':'search','reason':str(exc) if isinstance(exc,ResearchError) else 'Search unavailable or invalid JSON'})
        words=[w.lower() for w in model_words(filename).split() if len(w)>2]
        results.sort(key=lambda item:-sum(w in (str(item.get('title',''))+' '+item['url']).lower() for w in words))
        for item in results[:8]:
            if len(sources)>=5: break
            url=item['url']
            try:
                parsed=urlsplit(url); host=parsed.hostname
                if host not in SOURCE_HOSTS:
                    diagnostics.append({'phase':'fetch','reason':'Source domain unsupported','host':str(host)[:100]}); continue
                fetch_url=url
                if host=='huggingface.co':
                    parts=parsed.path.strip('/').split('/')
                    if len(parts)<2: continue
                    revision=parts[3] if len(parts)>3 and parts[2] in ('blob','raw','resolve','tree') else 'main'
                    fetch_url=f'https://huggingface.co/{parts[0]}/{parts[1]}/raw/{revision}/README.md'
                elif host=='github.com':
                    parts=parsed.path.strip('/').split('/')
                    if len(parts)>=5 and parts[2]=='blob':
                        fetch_url='https://raw.githubusercontent.com/'+'/'.join(parts[:2]+parts[3:])
                elif host=='civitai.com':
                    match=re.match(r'/models/(\d+)',parsed.path)
                    if not match: continue
                    raw=await public_get('https://civitai.com/api/v1/models/'+match[1],accept='application/json')
                    model=json.loads(raw); version=civitai_version(model,filename)
                    if version is None:
                        diagnostics.append({'phase':'identity','reason':'No unique exact Civitai file version','url':url}); continue
                    text=f"{filename}\n{model.get('name','')} {version.get('name','')}\n"+extract_document(model.get('description','')+'\n'+version.get('description',''),url)
                    sources.append(self.source(len(sources),url,text,True)); continue
                raw=await public_get(fetch_url); text=extract_document(raw,fetch_url)
                if not text: raise ResearchError('No readable page text')
                verified=identity_matches(filename,text)
                diagnostics.append({'phase':'extract','characters':len(text),'settings_characters':len(settings_excerpt(text)),'verified':verified,'url':url})
                if verified: sources.append(self.source(len(sources),url,text,True))
                else:
                    diagnostics.append({'phase':'identity','reason':'Page read, but exact model/version was not established; settings withheld','url':url})
                    if len(mismatches)<2: mismatches.append(self.source(0,url,'',False))
            except (ResearchError,httpx.HTTPError,ValueError,TypeError,KeyError,OSError):
                diagnostics.append({'phase':'fetch','reason':'Source unavailable, blocked, invalid or too large','host':str(urlsplit(url).hostname)[:100]})
        # Keep brief mismatch links for diagnosis, without pages of irrelevant settings or AI input.
        for mismatch in mismatches[:max(0,5-len(sources))]:
            mismatch['id']=f's{len(sources)+1}'; sources.append(mismatch)
        return sources,diagnostics

    @staticmethod
    def source(index,url,text,verified):
        return {'id':f's{index+1}','url':url[:1000],'text':text,'excerpt':settings_excerpt(text) if verified else 'Page does not establish this exact model/version; its settings were excluded.','verified':verified}
