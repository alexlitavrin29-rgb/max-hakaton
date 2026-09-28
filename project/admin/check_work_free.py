"""Live extraction + full shared-engine dialogues; source/MAX are isolated test adapters."""
import argparse,asyncio,copy,json,time,hashlib,urllib.request
from itertools import zip_longest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from project.admin.work_draft import configure_free_work
from project.admin.seed import initial_config
from project.llm.services import flow
from project.llm.services.geography import indexes
from project.llm.config import LLMSettings
from dotenv import dotenv_values
from project.admin.work_control_oracle import check_turns,same_value

def config():
    return configure_free_work(initial_config())

def development():
    return [dict(id=f'dev-{i}',group='development',messages=messages) for i,messages in enumerate([
      ['Мне 24, в Томске хочу работать токарем, не меньше семидесяти тысяч рублей в месяц'],
      ['Ищу сварщиком в Мельниково Томской области, мне 20, от 70.000 рублей в месяц'],
      ['Мне 25, хочу быть поваром в Казани, оплата до 125 тысяч рублей в месяц'],
      ['Мне 22. Москва, медицинская сестра-анестезист, от 70 до 90 тысяч рублей в месяц'],
      ['Мне 27, ищу работу курьером в Омске, 1,5 тысячи за смену на руки'],
      ['Мне 26, Томск, сварщик, хочу 70','70 тысяч','в месяц'],
      ['Мне 28, зарплата от 60000 в месяц, Томск, повар','Не Томск, а Тула','Не поваром, а пекарем','Не 60, а 85 тысяч рублей в месяц'],
      ['Мне 43, дочери 20, она хочет быть ветеринарным фельдшером в Туле, от 70 тысяч в месяц'],
      ['Ищу любую работу в Томске. Мне 20. Зарплата неважна','от 70 тысяч рублей в месяц'],
      ['Мне 20. Ищу поваром в Советске, от 40 тысяч рублей в месяц','Тульская область'],
      ['Мне 20. Живу в Туле, работу ищу в Омске. Раньше был кассиром, теперь хочу работать бариста, от 70 тысяч в месяц'],
      ['Мне 20. В Туле хочу работать в области квантового программирования как разработчик квантовых алгоритмов, около 150 тысяч в месяц'],
      ['томск сварщик','20'],
      ['мне 20, после колледжа, подработка от 60 тысяч','в месяц','Томск'],
      ['ищу любую работу с жильём','Томск','Не хочу указывать возраст'],
    ])]

async def run(args):
    indexes();rows=development() if args.development else json.loads(Path(args.cases).read_text(encoding='utf-8'))
    replay={r['id']:r for r in json.loads(Path(args.replay).read_text(encoding='utf-8'))} if args.replay else {}
    if args.replay:rows=[r for r in rows if r['id'] in replay]
    results=[];original=flow.call_llm;spent_bound=0;failed_cases=0
    settings=LLMSettings.from_env()
    if args.provider=='polza':
        settings=LLMSettings(api_key=dotenv_values('.env')['POLZA_API_KEY'],base_url='https://polza.ai/api/v1',model=settings.model,timeout_seconds=settings.timeout_seconds)
    scenario=config();revision=None
    if args.draft:
        with urllib.request.urlopen('http://127.0.0.1:18765/api/state') as r:saved=json.load(r)
        scenario=saved['config'];revision=saved['revision']
    scenario_hash=hashlib.sha256(json.dumps(scenario,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    code_hash=hashlib.sha256(b''.join(p.as_posix().encode()+p.read_bytes() for p in sorted(Path('project').rglob('*.py')))).hexdigest()
    if args.ids: rows=[r for r in rows if r['id'] in args.ids.split(',')]
    if args.interleave:
        groups={name:[r for r in rows if r['group']==name] for name in dict.fromkeys(r['group'] for r in rows)}
        rows=[r for batch in zip_longest(*groups.values()) for r in batch if r is not None]
    for case in rows:
        raw=[];requests=[];turns=[];metrics=[];inputs=[];engine=flow.FlowDialogue(flow.PreviewReminders(),scenario,revision or 1)
        if args.replay:
            assert replay[case['id']]['scenario_sha256']==scenario_hash, 'Replay requires the same draft content'
            replay_calls=iter(t for t in replay[case['id']]['turns'] if t.get('llm_inputs'))
        async def llm(*a,**kw):
            nonlocal spent_bound
            kw['settings']=settings
            inputs.append(copy.deepcopy(a[0]))
            if args.replay:
                saved=next(replay_calls)
                assert a[0]==saved['llm_inputs'][0], 'Model input changed: cached answer cannot validate this version'
                assert len(saved['raw'])==1, 'Replay requires one provider attempt per turn'
                raw.extend(copy.deepcopy(saved['raw']));metrics.extend(copy.deepcopy(saved['metrics']))
                answer=saved['raw'][0]
                if isinstance(answer,dict):raise flow.LLMError(answer['error'],answer.get('status'))
                return answer
            if args.provider=='polza':
                # Conservative upper bound: each UTF-8 byte could be a token, plus capped output.
                # Polza model catalogue, checked 2026-09-23; prices are RUB per million tokens.
                input_rate,output_rate=(10.11578932,20.23157864) if settings.model=='deepseek/deepseek-v4-flash' else (4.71968,16.51888)
                bound=len(json.dumps(a[0],ensure_ascii=False).encode())*input_rate/1e6+4096*output_rate/1e6
                if spent_bound+bound>args.budget:raise flow.LLMError('test_budget')
                spent_bound+=bound;kw.update(settings=settings,max_tokens=4096)
            for attempt in range(args.attempts):
                try:
                    measured={};answer=await original(*a,**kw,metrics=measured);raw.append(answer);metrics.append(measured);return answer
                except flow.LLMError as e:
                    metrics.append(measured)
                    raw.append(dict(error=e.code,status=e.status_code))
                    if e.code!='rate_limit' or attempt==args.attempts-1:raise
                    await asyncio.sleep(35)
        async def source(query,**params):
            requests.append(dict(query=query,**params))
            expected=case.get('expected')
            if not expected:return SimpleNamespace(items=[],next_offset=None)
            pay=expected.get('salary') or {};amount=pay.get('lower') or pay.get('upper') or 70000
            base=dict(title='Синтетическая вакансия: '+(expected.get('query') or 'Работник'),company='Тестовый работодатель',city=expected['city'],
                      address=expected['city'],region='Регион синтетического теста',salary_from=amount,salary_to=amount,
                      experience='0',education=None,employment=None,schedule=None,accommodation=None)
            good=SimpleNamespace(**base,id='fixture-full',url='https://example.invalid/full')
            sparse=SimpleNamespace(**{**base,'salary_from':None,'salary_to':None},id='fixture-sparse',url='https://example.invalid/sparse')
            return SimpleNamespace(items=[good,sparse],next_offset=None)
        await engine.handle(1,payload='jump:work')
        with patch.object(flow,'call_llm',llm),patch.object(flow,'search_vacancies',source):
            for text in case['messages']:
                replies=await engine.handle(1,text)
                s=engine.session(1)
                turns.append(copy.deepcopy(dict(text=text,replies=replies,node=s.node,values=s.values,work=s.work,trace=s.trace,raw=raw[:],llm_inputs=inputs[:],metrics=metrics[:],requests=requests[:])))
                print(json.dumps(dict(id=case['id'],turn=len(turns),node=s.node,errors=[e.get('code') for e in s.trace if e['kind']=='llm_error'])),flush=True)
                raw.clear();metrics.clear();inputs.clear();await asyncio.sleep(args.delay)
        final=engine.session(1);expected=case.get('expected',{});failures=[]
        for k,v in expected.items():
            actual=final.values.get(k)
            ok=same_value(k,actual,v)
            if not ok:failures.append(dict(field=k,expected=v,actual=actual))
        if final.work.get('pending_fields'):failures.append(dict(pending=final.work['pending_fields']))
        if not requests:failures.append(dict(no_search=True))
        if any(t['kind'] in {'llm_error','scenario_error'} for turn in turns for t in turn['trace']):failures.append(dict(engine_error=True))
        if expected and requests:
            last=requests[-1]
            if last['query']!=expected.get('query') or last['region_code']!=expected['region_code']:failures.append(dict(api=last))
            text='\n'.join(r['text'] for r in turns[-1]['replies'])
            if 'не подтвержден' not in text.lower():failures.append(dict(sparse_card_not_explained=True))
        if expected:failures.extend(check_turns(case,turns))
        result=dict(id=case['id'],group=case['group'],provider=args.provider,model=settings.model,revision=revision,scenario_sha256=scenario_hash,code_sha256=code_hash,cost_bound_rub=spent_bound,passed=not failures,failures=failures,turns=turns)
        if args.replay:result['replayed_from']=args.replay
        results.append(result)
        p=Path(args.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(id=case['id'],passed=result['passed'],failures=failures),ensure_ascii=True),flush=True)
        provider_failure=any(isinstance(e,dict) and e.get('error') for t in turns for e in t['raw'])
        failed_cases=failed_cases+1 if provider_failure else 0
        if args.stop_after_errors and failed_cases>=args.stop_after_errors:
            print(json.dumps(dict(stopped='consecutive_provider_errors',completed=len(results))),flush=True);break
    print(json.dumps(dict(passed=sum(r['passed'] for r in results),total=len(results))),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cases',default='docs/test-results/work-free-input/control-v1.json')
    p.add_argument('--development',action='store_true');p.add_argument('--delay',type=float,default=12)
    p.add_argument('--ids',default='');p.add_argument('--output',default='tmp/work-v3/development-results.json')
    p.add_argument('--provider',choices=['configured','polza'],default='configured');p.add_argument('--budget',type=float,default=20)
    p.add_argument('--stop-after-errors',type=int,default=3)
    p.add_argument('--draft',action='store_true')
    p.add_argument('--interleave',action='store_true',help='Alternate groups so a quota-limited run still covers every category.')
    p.add_argument('--attempts',type=int,choices=[1,2,3],default=1,help='Default matches production: one provider call, no hidden retry.')
    p.add_argument('--replay',default='',help='Offline replay of recorded model answers; inputs must match exactly, never counted as new live calls.')
    asyncio.run(run(p.parse_args()))
