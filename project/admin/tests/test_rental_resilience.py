import asyncio
from copy import deepcopy

import pytest

from project.admin.tests.test_rental import engine, CITY, page, row
from project.admin.tests.test_flow import buttons
from project.llm.services import rental
from project.llm.integrations import reefapi


@pytest.mark.parametrize('failure', ['timeout', 'slow'])
def test_timeout_gets_one_bounded_retry_before_reserve(monkeypatch, failure):
    monkeypatch.setattr(rental, 'SOURCE_TIMEOUT', .1)
    monkeypatch.setattr(rental, 'ATTEMPT_TIMEOUT', .02, raising=False)
    monkeypatch.setattr(rental, 'RETRY_DELAY', 0, raising=False)
    calls = []
    async def operation():
        calls.append(True)
        if len(calls) == 1:
            if failure == 'slow': await asyncio.sleep(1)
            raise reefapi.ReefError('timeout')
        return {'live': True}
    async def run():
        bot = engine(); s = bot.session(1)
        result = await bot.rental.source(s, 'search', operation, lambda: ({'reserved': True}, 90))
        assert result == {'live': True}
        assert len(calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize('code', ['http_401', 'http_402', 'http_403', 'http_429'])
def test_auth_or_quota_failure_is_not_retried(monkeypatch, code):
    calls = []
    async def operation():
        calls.append(True)
        raise reefapi.ReefError(code)
    async def run():
        bot = engine(); s = bot.session(1)
        result = await bot.rental.source(s, 'search', operation, lambda: ({'reserved': True}, 90))
        assert result == {'reserved': True} and len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize('code', ['http_401', 'http_402', 'http_403', 'http_429'])
def test_key_or_quota_failure_uses_exact_reserve(monkeypatch, code):
    async def failed(*args): raise reefapi.ReefError(code)
    monkeypatch.setattr(reefapi, 'search', failed)
    monkeypatch.setattr(reefapi, 'cached_search', lambda *args: (page([row()]), 420))
    async def run():
        bot=engine();s=bot.session(1);s.branch='rental'
        bot.rental.state(s).update(city=CITY, budget=30000)
        replies=await bot.rental.show(s)
        assert s.result_cards
        assert any('резерв' in reply['text'] and '7 мин' in reply['text'] for reply in replies)
    asyncio.run(run())


def test_slow_source_has_one_deadline_and_uses_reserve(monkeypatch):
    monkeypatch.setattr(rental, 'SOURCE_TIMEOUT', .02, raising=False)
    async def slow():
        await asyncio.sleep(.2)
        return {'live': True}
    async def run():
        bot=engine();s=bot.session(1)
        result=await bot.rental.source(s,'search',slow,lambda: ({'reserved': True}, 90))
        assert result == {'reserved': True}
    asyncio.run(run())


def test_buffer_keeps_reserve_warning_on_next_turn_and_clears_on_live_page(monkeypatch):
    failing=[True]
    async def source(*args):
        if failing[0]: raise reefapi.ReefError('timeout')
        return page([row('live')])
    monkeypatch.setattr(reefapi,'search',source)
    monkeypatch.setattr(reefapi,'cached_search',lambda *args:(page([row(str(i)) for i in range(6)],more=True),420))
    async def run():
        bot=engine();s=bot.session(1);s.branch='rental'
        bot.rental.state(s).update(city=CITY,budget=30000)
        await bot.rental.show(s)
        s.trace=[];s.result_cards=[]
        replies=await bot.rental.show(s)
        assert any('резерв' in r['text'] for r in replies)
        failing[0]=False;s.trace=[];s.result_cards=[]
        replies=await bot.rental.show(s)
        assert not any('резерв' in r['text'] for r in replies)
    asyncio.run(run())


def test_simple_city_then_budget_does_not_need_model(monkeypatch):
    async def forbidden(*args, **kwargs): raise AssertionError('No model for simple input')
    async def locations(query): return [CITY]
    async def search(*args): return page([row()])
    monkeypatch.setattr(rental,'call_llm',forbidden)
    monkeypatch.setattr(reefapi,'locations',locations)
    monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        await bot.handle(1,'Томск')
        assert bot.session(1).rental['city']['name']=='Томск'
        await bot.handle(1,'до 30000 рублей в месяц')
        assert bot.session(1).result_cards
    asyncio.run(run())


def test_parse_error_offers_manual_mode_without_dropping_existing_wishes(monkeypatch):
    async def failed(*args, **kwargs): raise rental.LLMError('authentication')
    monkeypatch.setattr(rental,'call_llm',failed)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        s=bot.session(1);s.rental.update(other='с кошкой',other_accepted=False)
        before=deepcopy(s.rental)
        replies=await bot.handle(1,'подберите что-нибудь')
        assert s.rental==before
        manual=next(b for b in buttons(replies) if b.get('payload','').endswith(':manual'))
        await bot.handle(1,payload=manual['payload'])
        assert s.rental['other']=='с кошкой'
        replies=await bot.handle(1,'хочу возле работы')
        assert not s.result_cards
        assert s.rental['other']=='с кошкой'
    asyncio.run(run())
