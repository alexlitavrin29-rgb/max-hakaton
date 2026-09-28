"""Bounded synthetic search timings; no MAX messages or user data in output."""
import asyncio
import json
import os
import time

import asyncpg

from project.llm.bot import close_network_clients
from project.llm.integrations import headhunter, reefapi, trudvsem
from project.llm.services import flow, rental, geography, work
from project.llm.services.scenario import ScenarioStore


async def main():
    pool = await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST','postgres'),
        port=int(os.getenv('POSTGRES_PORT','5432')), user=os.getenv('POSTGRES_USER','support_router'),
        password=os.environ['POSTGRES_PASSWORD'], database=os.getenv('POSTGRES_DB','support_router'),
        min_size=1,max_size=1)
    calls=[]
    originals=[]
    comparison=dict(lookups=0,old_ms=0.0,new_ms=0.0)
    if os.getenv('COMPARE_LEGACY')=='1':
        def reference(name,region=None):
            candidate=geography.ALIASES.get(geography.key(name),geography.key(name))
            matches=[p for p in geography.legacy_places() if geography.key(p['name'])==candidate]
            if not matches:
                matches=[p for p in geography.legacy_places() if geography.same_words(p['name'],name)
                         and len(geography.words(p['name']))==len(geography.words(name))]
            if region:
                codes={p['code'] for p in reference(region) if p['kind']=='region' or p['code'][:2] in {'77','78','92'}}
                matches=[p for p in matches if p['code'] in codes]
            return [dict(p) for p in matches]
        current=work.resolve
        originals.append((work,'resolve',current))
        def compared(*args,**kwargs):
            start=time.perf_counter();old=reference(*args,**kwargs)
            comparison['old_ms']+=(time.perf_counter()-start)*1000
            start=time.perf_counter();new=current(*args,**kwargs)
            comparison['new_ms']+=(time.perf_counter()-start)*1000
            assert old==new, 'Location matching changed'
            comparison['lookups']+=1
            return new
        work.resolve=compared
    def instrument(module,name,label):
        original=getattr(module,name);originals.append((module,name,original))
        async def timed(*args,**kwargs):
            start=time.perf_counter()
            try:return await original(*args,**kwargs)
            finally:calls.append(dict(stage=label,ms=round((time.perf_counter()-start)*1000,1)))
        setattr(module,name,timed)
    for module,name,label in [(flow,'call_llm','work_llm'),(rental,'call_llm','rental_llm'),
        (trudvsem,'search_vacancies','trudvsem'),(headhunter,'search_vacancies','hh'),
        (reefapi,'locations','reef_locations'),(reefapi,'search','reef_search')]:
        instrument(module,name,label)
    try:
        data=await ScenarioStore(pool).read(published=True)
        for repeat in range(1 if os.getenv('COMPARE_LEGACY')=='1' else 3):
            for branch,text in [('work','Томск, зарплата от 40000 рублей в месяц'),
                                ('rental','Москва, до 70000 рублей в месяц')]:
                engine=flow.FlowDialogue(flow.PreviewReminders(),data['config'],data['revision'])
                session=engine.session(1);session.values['role']='child'
                await engine.enter(1,session,branch)
                calls.clear();start=time.perf_counter()
                await engine.handle(1,text=text)
                metrics=[{k:v for k,v in event.items() if k=='kind' or k.endswith('_ms') or k in {'method','fetched','accepted','excluded_count','shown'}}
                         for event in session.trace if event['kind'] in {'search','rental_search','condition_recognition','location_lookup'}]
                print(json.dumps(dict(branch=branch,repeat=repeat,revision=data['revision'],
                    total_ms=round((time.perf_counter()-start)*1000,1),cards=len(session.result_cards),
                    calls=list(calls),metrics=metrics),ensure_ascii=False),flush=True)
        if comparison['lookups']:print(json.dumps(dict(equivalence=comparison)),flush=True)
    finally:
        for module,name,original in originals:setattr(module,name,original)
        await close_network_clients();await pool.close()


if __name__=='__main__':asyncio.run(main())
