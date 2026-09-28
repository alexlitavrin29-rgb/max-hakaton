"""Bounded public vacancy API checks. No MAX delivery or applicant conversations."""
import asyncio,json
from pathlib import Path
from project.admin.check_work_free import config
from project.llm.services import flow,money
from project.llm.services.geography import resolve

async def main():
    rows=[]
    cases=[('Томск','Томская область','сварщик','от 73500 рублей в месяц'),
           ('Мельниково','Томская область',None,'до 90000 рублей в месяц'),
           ('Омск','Омская область','повар','от 1,5 тысячи рублей за смену'),
           ('Томск','Томская область',None,None)]
    for city,region,query,salary in cases:
        place=resolve(city,region)
        if len(place)!=1:rows.append(dict(city=city,error='ambiguous_test_place'));continue
        params=dict(region_code=place[0]['code'],limit=30,offset=0)
        if salary is None:params['accommodation']=True
        row=dict(city=city,query=query,parameters=params,salary=salary)
        try:
            page=await flow.search_vacancies(query,**params)
            b=flow.FlowDialogue(flow.PreviewReminders(),config());s=b.session(1)
            values={'salary':money.parse(salary)[0]} if salary else {'housing':True}
            if query:values['query']=query
            results=[]
            for job in page.items:
                reason,missing=b.work.assess_job(s,job,values,place[0])
                results.append(dict(id=job.id,title=job.title,url=job.url,city=job.city,address=job.address,
                    region=job.region,salary_from=job.salary_from,salary_to=job.salary_to,
                    accommodation=job.accommodation,excluded=reason,unconfirmed=missing))
            row.update(fetched=len(page.items),total=page.total,next_offset=page.next_offset,items=results)
        except flow.TrudvsemError as e:row['error']=e.code
        rows.append(row);print(json.dumps({k:v for k,v in row.items() if k!='items'},ensure_ascii=True),flush=True)
    Path('docs/test-results/work-free-input/live-source.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':asyncio.run(main())
