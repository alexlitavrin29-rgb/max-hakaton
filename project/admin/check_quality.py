"""Working dialogues, real LLM + deterministic public fixtures, never a holdout.

python -m project.admin.check_quality --live --output PATH [--ids W01,H07]
No scenario writes, MAX network calls, or real user data. One provider attempt.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from project.admin.tests.quality_oracles import corpus, PLACES
from project.admin.tests.test_quality_regressions import job
from project.admin.tests.test_rental import row, page
from project.llm.config import LLMSettings
from project.llm.services import flow, rental

ROOT=Path('docs/test-results/quality-2026-09-26')

def sha(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def code_hash():
    return sha({p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('project/llm').rglob('*.py'))})

def buttons(replies):
    return [b for r in replies for a in r.get('attachments',[]) for row_ in a.get('payload',{}).get('buttons',[]) for b in row_]

def work_fixtures(turn):
    """Construct from frozen human oracle, NOT actual query, state or model output."""
    known={k:v['value'] for k,v in turn['fields'].items() if v['status']=='known'}
    title=known.get('query') or 'сварщик'
    city=known.get('city') or 'Томск'
    if isinstance(city,list):city=city[0]
    salary=known.get('salary') or {}
    amount=salary.get('lower') or salary.get('upper') or 70000
    base=vars(job(title=title,city=city,address=city,region={'Томск':'Томская область','Омск':'Омская область','Тула':'Тульская область'}.get(city),
                  salary_from=amount,salary_to=amount,salary_period=salary.get('period','month'),salary_tax=salary.get('tax','unknown'),
                  requirements='Возраст от 16 лет',schedule='2/2',employment='Полная занятость' if known.get('employment')=='full_time' else 'Частичная занятость'))
    patches=[('W-confirmed',{}),('W-city-conflict',dict(address='Москва')),('W-shift-conflict',dict(schedule='3/3 сменный')),
             ('W-sparse',dict(salary_period=None,requirements=None,accommodation=None)),
             ('W-wrong-title',dict(title='повар' if title!='повар' else 'сварщик')),
             ('W-age-conflict',dict(requirements='Возраст от 99 лет'))]
    expected={'W-confirmed':'confirmed','W-city-conflict':'excluded','W-shift-conflict':'excluded' if known.get('schedule') else 'confirmed',
              'W-sparse':'unverified' if any(k in known for k in ['age','salary','housing']) else 'confirmed',
              'W-wrong-title':'excluded' if known.get('query') else 'confirmed','W-age-conflict':'excluded' if known.get('age') else 'confirmed'}
    return [SimpleNamespace(**(base|change|dict(id=i,url='https://example.invalid/'+i))) for i,change in patches],expected

def rental_fixtures(turn):
    base=row('H-good')
    patches=[('H-good',{}),('H-over',dict(price=35000)),('H-daily',dict(price_period='в сутки')),
             ('H-other-city',dict(location={'location_id':1})),('H-unknown-period',dict(price_period=None)),
             ('H-unknown-type',dict(category={})),('H-from',dict(price_is_from=True))]
    rows=[base|change|dict(ad_id=i,url='https://www.avito.ru/tomsk/kvartiry/'+i) for i,change in patches]
    expected={'H-good':'confirmed','H-over':'excluded','H-daily':'excluded','H-other-city':'excluded',
              'H-unknown-period':'unverified','H-unknown-type':'unverified','H-from':'unverified'}
    if turn.get('city') and turn['city']['location_id']!=657600:expected={k:'excluded' for k in expected}
    return rows,expected

def evaluate(case, expected, session, requests, assessments, replies, allowed):
    errors=[[],[],[]]
    if case['branch']=='work':
        actual={k:dict(status=v['status'],value=v['value']) for k,v in session.work['fields'].items()}
        # Only presentation label "график" and inflection-free whitespace are ignored.
        if actual.get('schedule',{}).get('value'):
            actual['schedule']['value']=re.sub(r'^график\s+','',actual['schedule']['value'],flags=re.I)
        def casefold_query(fields):
            fields=copy.deepcopy(fields)
            value=fields['query']['value']
            if isinstance(value,str):fields['query']['value']=value.casefold()
            return fields
        if casefold_query(actual)!=casefold_query(expected['fields']):errors[0].append(dict(fields=actual,expected=expected['fields']))
        if sorted(session.work['pending_fields'])!=sorted(expected['pending']):errors[0].append(dict(pending=list(session.work['pending_fields']),expected=expected['pending']))
        applicant=session.work.get('applicant','child')
        if applicant!=expected['applicant']:errors[0].append(dict(applicant=applicant))
        question=session.work.get('awaiting')
    else:
        st=session.rental
        if st['city']!=expected['city'] or st['budget']!=expected['budget']:
            errors[0].append(dict(city=st['city'],budget=st['budget'],expected_city=expected['city'],expected_budget=expected['budget']))
        if bool(st['other'])!=bool(expected['other']) or (expected['other'] and expected['other'].casefold() not in st['other'].casefold()):errors[0].append(dict(other=st['other'],expected=expected['other']))
        if bool(st.get('other_accepted'))!=expected['consent']:errors[0].append(dict(consent=st.get('other_accepted')))
        actual_pending=(['city'] if st.get('pending_city') else [])+(['budget'] if st.get('budget_issue') else [])
        if sorted(actual_pending)!=sorted(expected['pending']):errors[0].append(dict(pending=actual_pending,expected=expected['pending']))
        question=st.get('awaiting')
    if question!=expected['question']:errors[1].append(dict(question=question,expected=expected['question']))
    if bool(requests)!=bool(expected['requests']):errors[1].append('wrong decision to search')
    def canonical_request(r):
        return {k:(v.casefold() if k=='query' and isinstance(v,str) else v) for k,v in r.items()}
    if list(map(canonical_request,requests))!=list(map(canonical_request,expected['requests'])):errors[2].append(dict(requests=requests,expected=expected['requests']))
    for a in assessments:
        if a['status']!=allowed[a['id']]:errors[2].append(dict(card=a,expected=allowed[a['id']]))
    shown={b['url'].rsplit('/',1)[-1] for b in buttons(replies) if b.get('url','').startswith(('https://example.invalid/','https://www.avito.ru/'))}
    if any(allowed.get(i)=='excluded' for i in shown):errors[2].append(dict(excluded_shown=list(shown)))
    text='\n'.join(r.get('text','') for r in replies)
    for a in assessments:
        if a['id'] in shown and a['status']=='unverified':
            for missing in a['missing']:
                if missing not in text:errors[2].append(dict(unlabelled=missing,id=a['id']))
    return errors

async def run(args):
    cfg=json.loads((ROOT/'scenario.json').read_text(encoding='utf-8'))
    settings=LLMSettings.from_env(Path('.env'))
    cases=corpus()
    if args.ids:cases=[c for c in cases if c['id'] in args.ids.split(',')]
    result=dict(kind='development_live' if args.live else 'replay',model=settings.model,timeout=settings.timeout_seconds,
                attempts=1,code_sha256=code_hash(),scenario_sha256=sha(cfg),oracle_sha256=sha(cases),cases=[],cost_upper_rub=0)
    saved={c['id']:c for c in json.loads(Path(args.replay).read_text(encoding='utf-8'))['cases']} if args.replay else {}
    original=flow.call_llm
    for case in cases:
        bot=flow.FlowDialogue(flow.PreviewReminders(),cfg,49)
        bot.session(1).values['role']=case['role']
        await bot.handle(1,payload='jump:'+case['branch'])
        case_result=dict(id=case['id'],branch=case['branch'],turns=[],metrics=[True]*3,available=True)
        replies=[]
        for index,expected in enumerate(case['turns']):
            raw=[];inputs=[];metrics=[];requests=[];assessments=[]
            fixtures,allowed=work_fixtures(expected) if case['branch']=='work' else rental_fixtures(expected)
            async def llm(messages,**kw):
                inputs.append(copy.deepcopy(messages));measured={}
                if args.replay:
                    old=saved[case['id']]['turns'][index]
                    if not old['raw']:raise flow.LLMError('replay_missing')
                    value=old['raw'][0];raw.append(value)
                    if isinstance(value,dict):raise flow.LLMError(value['error'])
                    return value
                # Official ProxyAPI 20/40 RUB per million. UTF-8 bytes >= tokens;
                # include output cap, no cache discount. Abort before exceeding cap.
                bound=len(json.dumps(messages,ensure_ascii=False).encode())*20/1e6+4096*40/1e6
                if result['cost_upper_rub']+bound>50:raise flow.LLMError('test_budget')
                result['cost_upper_rub']+=bound
                try:
                    value=await original(messages,**kw,settings=settings,metrics=measured)
                    raw.append(value);return value
                except flow.LLMError as e:
                    raw.append(dict(error=e.code));case_result['available']=False;raise
                finally:metrics.append(measured)
            async def source(query,**kw):
                requests.append(dict(query=query,**kw));return SimpleNamespace(items=fixtures,next_offset=None)
            async def locations(query):
                return [p for p in PLACES if p['name'].casefold()==query.casefold()]
            async def rentals(location,budget,number):
                requests.append(dict(location=location,budget=budget,page=number))
                return page(fixtures,number,False,place=location,budget=budget)
            assess_original=bot.work.assess_job
            rental_assess=rental.assess
            def assess_work(s,j,v,p):
                reason,missing=assess_original(s,j,v,p)
                assessments.append(dict(id=j.id,status='excluded' if reason else 'unverified' if missing else 'confirmed',reason=reason,missing=missing))
                return reason,missing
            def assess_rental(r,p,b):
                reason,missing=rental_assess(r,p,b)
                assessments.append(dict(id=r['ad_id'],status='excluded' if reason else 'unverified' if missing else 'confirmed',reason=reason,missing=missing))
                return reason,missing
            start=time.monotonic()
            with patch.object(flow,'call_llm',llm),patch.object(rental,'call_llm',llm),patch.object(flow,'search_vacancies',source),patch.object(rental.reefapi,'locations',locations),patch.object(rental.reefapi,'search',rentals),patch.object(bot.work,'assess_job',assess_work),patch.object(rental,'assess',assess_rental):
                payload=next((b.get('payload') for b in buttons(replies) if b['text']==expected.get('button')),None)
                replies=await bot.handle(1,expected['text'],payload)
            s=bot.session(1)
            errors=evaluate(case,expected,s,requests,assessments,replies,allowed)
            if expected.get('button') and payload is None:errors[1].append('expected button missing')
            case_result['metrics']=[old and not err for old,err in zip(case_result['metrics'],errors)]
            case_result['turns'].append(dict(input=expected,raw=raw,llm_inputs=inputs,usage=metrics,requests=requests,assessments=assessments,
                allowed=allowed,replies=replies,work=copy.deepcopy(s.work),rental=copy.deepcopy(s.rental),trace=copy.deepcopy(s.trace),errors=errors,seconds=time.monotonic()-start))
        result['cases'].append(case_result)
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(case['id'],case_result['metrics'],'available',case_result['available'],flush=True)
    assert code_hash()==result['code_sha256'],'Runtime changed during run'
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--replay');p.add_argument('--output',required=True);p.add_argument('--ids')
    args=p.parse_args()
    if not args.live and not args.replay:p.error('choose --live or --replay')
    asyncio.run(run(args))
