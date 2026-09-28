"""Finite real-provider comparison in an isolated process, with empty local caches."""
import asyncio
import json
import os
import time
from pathlib import Path

import asyncpg
import httpx

from project.llm.integrations import reefapi
from project.llm.services import flow, rental
from project.llm.services.scenario import ScenarioStore


async def main():
    pool=await asyncpg.create_pool(host=os.getenv('POSTGRES_HOST','postgres'),
        user=os.getenv('POSTGRES_USER','support_router'),password=os.environ['POSTGRES_PASSWORD'],
        database=os.getenv('POSTGRES_DB','support_router'),min_size=1,max_size=1)
    try:
        data=await ScenarioStore(pool).read(published=True)
        versions=[('current',rental.RentalBranch)]
        candidate=os.getenv('RENTAL_CANDIDATE')
        if candidate:
            namespace=dict(rental.__dict__)
            exec(compile(Path(candidate).read_text(),rental.__file__,'exec'),namespace)
            versions.append(('candidate',namespace['RentalBranch']))
        # Directory loading is unrelated to parallel provider calls; warm equally.
        rental.resolve('Тверь')
        for city in ['Тверь','Йошкар-Ола','Торжок']:
            for version,branch in versions:
                calls=[]
                client=reefapi.ReefClient(os.environ['HOUSING_API_KEY'])
                original=client.request
                async def timed(endpoint,params):
                    started=time.perf_counter()
                    try:return await original(endpoint,params)
                    finally:calls.append(dict(endpoint=endpoint,seconds=round(time.perf_counter()-started,3)))
                client.request=timed
                reefapi._default=client;reefapi._default_factory=httpx.AsyncClient
                try:
                    engine=flow.FlowDialogue(flow.PreviewReminders(),data['config'],data['revision'])
                    engine.rental=branch(engine)
                    session=engine.session(1);session.values['role']='child'
                    await engine.enter(1,session,'rental')
                    started=time.perf_counter()
                    await engine.handle(1,text=f'{city}, до 50000 рублей в месяц')
                    state=engine.rental.state(session)
                    assert session.result_cards, 'No cards'
                    assert state['city']['name']==city and state['budget']==50000
                    print(json.dumps(dict(version=version,city=city,seconds=round(time.perf_counter()-started,3),
                        cards=len(session.result_cards),calls=calls,revision=data['revision']),ensure_ascii=False),flush=True)
                finally:await reefapi.close()
    finally:await pool.close()


if __name__=='__main__':asyncio.run(main())
