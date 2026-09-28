"""Bounded source-only probe. No LLM calls, MAX, contacts, or scenario mutations."""
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from project.llm.integrations import reefapi, trudvsem

OUT=Path('docs/test-results/quality-independent-2026-09-26')

async def run():
    result={'attempts_per_request':1,'reef_requests_max':3,'llm_calls':0,'work':[],'rental':[]}
    for offset in [0,1]:
        try:
            p=await trudvsem.search_vacancies('сварщик',region_code='7000000000000',limit=5,offset=offset)
            allowed={'id','source','title','salary_from','salary_to','region','city','address','experience','accommodation','url','education','schedule','employment','salary_period','salary_tax'}
            result['work'].append(dict(offset=offset,total=p.total,next_offset=p.next_offset,rows=[{k:v for k,v in asdict(j).items() if k in allowed} for j in p.items]))
        except trudvsem.TrudvsemError as e:result['work'].append(dict(offset=offset,error=e.code))
    try:
        places=await reefapi.locations('Томск')
        result['locations']=[{k:p.get(k) for k in ['name','region','slug','location_id']} for p in places]
        place=next((p for p in places if p.get('slug')=='tomsk'),None)
        if place:
            for number in [1,2]:
                try:
                    data=await reefapi.search('tomsk',30000,number)
                    allowed={'ad_id','url','title','price','currency','price_period','price_is_from','price_min','price_max','price_not_published'}
                    rows=[]
                    for r in data.get('listings',[]):
                        item={k:v for k,v in r.items() if k in allowed}
                        item['location']={k:v for k,v in (r.get('location') or {}).items() if k in {'location_id','name','city','region'}}
                        item['realty_type']={k:v for k,v in (r.get('realty_type') or {}).items() if k in {'transaction_type','rent_term'}}
                        item['category']={k:v for k,v in (r.get('category') or {}).items() if k=='slug'}
                        rows.append(item)
                    result['rental'].append(dict(page=number,has_more=data.get('has_more'),filters_applied=data.get('filters_applied'),rows=rows))
                except reefapi.ReefError as e:result['rental'].append(dict(page=number,error=e.code))
    except reefapi.ReefError as e:result['locations_error']=e.code
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'live-sources.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:([{x:y for x,y in item.items() if x!='rows'} for item in v] if k in {'work','rental'} else v) for k,v in result.items()},ensure_ascii=False))

if __name__=='__main__':asyncio.run(run())
