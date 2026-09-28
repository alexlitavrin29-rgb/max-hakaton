import asyncio
from copy import deepcopy

import pytest

from project.admin.tests.test_rental import engine, CITY, page, row
from project.llm.integrations import reefapi
from project.llm.services import rental


def setup():
    bot=engine();s=bot.session(1);s.branch='rental'
    bot.rental.state(s).update(pending_city='Томск',budget=30000)
    return bot,s


def result(**location):
    return dict(page([row()]),location={**CITY,**location})


def test_cold_city_search_overlaps_lookup_and_reuses_verified_result(monkeypatch):
    async def run():
        started=asyncio.Event();calls=[]
        async def locations(name):
            await asyncio.wait_for(started.wait(),.2)
            return [deepcopy(CITY)]
        async def search(name,budget,number):
            calls.append((name,budget,number));started.set()
            return result()
        monkeypatch.setattr(reefapi,'locations',locations)
        monkeypatch.setattr(reefapi,'search',search)
        bot,s=setup()
        await bot.rental.ready(1,s)
        assert calls==[('Томск',30000,1)]
        assert len(s.result_cards)==1
    asyncio.run(run())


@pytest.mark.parametrize('failure',['wrong_id','wrong_slug','wrong_budget','error','no_location'])
def test_unverified_prefetch_falls_back_to_confirmed_slug(monkeypatch,failure):
    calls=[]
    async def locations(name):
        await asyncio.sleep(0)
        return [deepcopy(CITY)]
    async def search(name,budget,number):
        calls.append(name)
        if name=='tomsk':return result()
        if failure=='error':raise reefapi.ReefError('provider')
        data=result()
        if failure=='wrong_id':data['location']['location_id']=1
        if failure=='wrong_slug':data['location']['slug']='omsk'
        if failure=='wrong_budget':data['filters_applied']['price_max']=99999
        if failure=='no_location':data.pop('location')
        data['listings']=[row('wrong')]
        return data
    monkeypatch.setattr(reefapi,'locations',locations)
    monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot,s=setup();await bot.rental.ready(1,s)
        assert calls==['Томск','tomsk']
        assert bot.rental.state(s)['seen']==['1']
    asyncio.run(run())


@pytest.mark.parametrize('kind',['ambiguous','unknown','failure'])
def test_lookup_not_confirmed_cancels_search_and_never_shows_cards(monkeypatch,kind):
    async def run():
        started=asyncio.Event();cancelled=asyncio.Event()
        async def locations(name):
            await asyncio.wait_for(started.wait(),.2)
            if kind=='failure':raise reefapi.ReefError('provider')
            return [CITY,{**CITY,'location_id':123,'region':'Другой регион'}] if kind=='ambiguous' else []
        async def search(*args):
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        monkeypatch.setattr(reefapi,'locations',locations)
        monkeypatch.setattr(reefapi,'search',search)
        monkeypatch.setattr(reefapi,'cached_locations',lambda *args:None)
        bot,s=setup();await bot.rental.ready(1,s)
        assert not s.result_cards
        assert cancelled.is_set()
    asyncio.run(run())


@pytest.mark.parametrize('change',[{'budget':None},{'budget_issue':True},{'other':'нужен балкон'},{'different_task':True}])
def test_no_search_before_budget_and_consent(monkeypatch,change):
    calls=[]
    async def locations(name):return [deepcopy(CITY)]
    async def search(*args):calls.append(args);return result()
    monkeypatch.setattr(reefapi,'locations',locations)
    monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot,s=setup();bot.rental.state(s).update(change)
        if change.get('different_task'):bot.rental.state(s).update(other='комната')
        await bot.rental.ready(1,s)
        assert calls==[]
    asyncio.run(run())


def test_cancelled_request_leaves_no_background_search(monkeypatch):
    async def run():
        started=asyncio.Event();cancelled=asyncio.Event()
        async def locations(*args):await asyncio.Event().wait()
        async def search(*args):
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        monkeypatch.setattr(reefapi,'locations',locations)
        monkeypatch.setattr(reefapi,'search',search)
        bot,s=setup();task=asyncio.create_task(bot.rental.ready(1,s))
        try:
            await asyncio.wait_for(started.wait(),.2)
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        assert cancelled.is_set()
    asyncio.run(run())


def test_immediate_location_cache_hit_does_not_launch_speculative_search(monkeypatch):
    calls=[]
    async def locations(name):return [deepcopy(CITY)]
    async def search(name,budget,number):calls.append(name);return result()
    monkeypatch.setattr(reefapi,'locations',locations)
    monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot,s=setup();await bot.rental.ready(1,s)
        assert calls==['tomsk']
        assert s.result_cards
    asyncio.run(run())


def test_slow_prefetch_is_bounded_then_normal_search_recovers(monkeypatch):
    monkeypatch.setattr(rental,'ATTEMPT_TIMEOUT',.02)
    calls=[]
    async def locations(name):
        await asyncio.sleep(0)
        return [deepcopy(CITY)]
    async def search(name,budget,number):
        calls.append(name)
        if name=='Томск':await asyncio.Event().wait()
        return result()
    monkeypatch.setattr(reefapi,'locations',locations)
    monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot,s=setup()
        await asyncio.wait_for(bot.rental.ready(1,s),.5)
        assert calls==['Томск','tomsk'] and s.result_cards
    asyncio.run(run())
