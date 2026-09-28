import asyncio
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest

from project.admin.miniapp_draft import configure
from project.admin.tests.test_rental import CITY, fields, page, row
from project.llm.services import flow, rental
from project.llm.services.work import field
from project.miniapp.app import app, Session
from project.miniapp.auth import validate_launch
from project.miniapp.service import ENTRIES, MiniDialogue, present


@pytest.fixture
def config():
    return json.loads(Path('docs/test-results/quality-2026-09-26/scenario.json').read_text(encoding='utf-8'))


def signed(fields=None):
    values = fields or dict(auth_date=str(int(time.time())), user=json.dumps({'id': 123}))
    secret = hmac.new(b'WebAppData', b'fake-token', hashlib.sha256).digest()
    payload = '\n'.join(f'{k}={values[k]}' for k in sorted(values))
    return urlencode({**values, 'hash': hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()})


def test_max_signature_and_age():
    data = signed()
    assert validate_launch(data, 'fake-token') == 123
    for invalid in [data + '&auth_date=1', data.replace('123', '456'),
                    signed(dict(auth_date='1', user=json.dumps({'id': 123}))),
                    signed(dict(auth_date=str(int(time.time())+1000), user=json.dumps({'id':123}))),
                    signed(dict(auth_date=str(int(time.time())), user=json.dumps({'id':True})))]:
        with pytest.raises(ValueError): validate_launch(invalid, 'fake-token')
    with pytest.raises(ValueError): validate_launch(data, 'different-token')


def test_published_config_unchanged_and_bot_buttons_idempotent(config):
    original = deepcopy(config)
    engine = MiniDialogue(config, 2)
    assert config == original
    assert engine.session(1).values == {'role': 'user'}
    updated = configure(config)
    assert config == original and configure(updated) == updated
    welcome = next(n for n in updated['nodes'] if n['id'] == updated['start'])
    assert len(welcome['buttons']) == 4
    assert welcome['buttons'][:3] == next(n for n in config['nodes'] if n['id'] == config['start'])['buttons']
    assert welcome['buttons'][-1]['url'].endswith('startapp=home')


def test_three_entries_and_help_cards(config):
    async def run():
        for section, entry in ENTRIES.items():
            engine = MiniDialogue(config, 2)
            replies = await engine.enter(1, engine.session(1), entry)
            view, offered = present(engine, replies, section)
            assert view['notices'] and engine.session(1).branch == entry
            assert 'parent' not in engine.session(1).values.values()
            if section == 'help':
                category = next(c for c in view['controls'] if c['label'] == 'Бесплатная еда')
                await engine.handle(1, payload=category['payload'])
                replies = await engine.handle(1, text='Москва')
                view, _ = present(engine, replies, section)
                if engine.session(1).help_points.get('choices'):
                    state = engine.session(1).help_points
                    index = next(i for i, p in enumerate(state['choices']) if p['code'].startswith('77'))
                    replies = await engine.handle(1, payload=f"hp:place:{index}:{state['nonce']}")
                    view, _ = present(engine, replies, section)
                assert view['cards'], view
                assert all(c['status'] == 'information' for c in view['cards'])
                assert all('Что можно получить' in c['text'] for c in view['cards'])
    asyncio.run(run())


def test_rental_same_engine_consent_and_cards(config, monkeypatch):
    calls = []
    async def llm(*a, **k): return json.dumps(fields('Томск', 'до 30 тысяч', 'без залога'))
    async def locations(*a): return [CITY]
    async def search(*a):
        calls.append(a)
        return page([row('one'), row('two')], more=True)
    monkeypatch.setattr(rental, 'call_llm', llm)
    monkeypatch.setattr(rental.reefapi, 'locations', locations)
    monkeypatch.setattr(rental.reefapi, 'search', search)
    async def run():
        engine = MiniDialogue(config, 2)
        await engine.enter(1, engine.session(1), 'rental')
        replies = await engine.handle(1, text='Томск до 30 тысяч без залога')
        view, _ = present(engine, replies, 'rental')
        assert not calls and not view['cards']
        consent = next(b for b in view['controls'] if 'Искать' in b['label'])
        replies = await engine.handle(1, payload=consent['payload'])
        view, _ = present(engine, replies, 'rental')
        assert len(calls) == 1 and len(view['cards']) == 2
        assert 'без залога' in view['summary']
        assert all(c['status'] == 'confirmed' for c in view['cards'])
        assert any(c['label'] == 'Показать ещё' for c in view['controls'])
        other = MiniDialogue(config, 2)
        assert other.session(1).rental == {}
    asyncio.run(run())


def test_miniapp_uses_deterministic_rental_fallback(config, monkeypatch):
    async def llm(*a,**k):return json.dumps(fields())
    async def locations(query):return [CITY]
    async def search(place,budget,number):return page([],place=place,budget=budget)
    monkeypatch.setattr(rental,'call_llm',llm)
    monkeypatch.setattr(rental.reefapi,'locations',locations)
    monkeypatch.setattr(rental.reefapi,'search',search)
    async def run():
        engine=MiniDialogue(config,2);await engine.enter(1,engine.session(1),'rental')
        replies=await engine.handle(1,text='Томск, до 30 тысяч')
        view,_=present(engine,replies,'rental')
        assert engine.session(1).rental['city']['name']=='Томск'
        assert engine.session(1).rental['budget']==30000
        assert 'Город: Томск' in view['summary'] and '30000' in view['summary']
    asyncio.run(run())


def test_work_uses_existing_filter_and_keeps_warnings(config, monkeypatch):
    async def llm(*a, **k):
        return json.dumps(dict(branch='work',work_fields=dict(city=field('known','Томск','Томск'),age=field('known',30,'Мне 30'),query=field('known','сварщик','сварщик'))))
    # Use source-shaped fixtures already covered by the bot's integration tests.
    async def search(*a, **k):
        job = SimpleNamespace(id='one',title='Сварщик',company='Завод',address='Томск, Томская область',city='Томск',region='Томская область',salary_from=50000,salary_to=60000,experience=None,accommodation=None,education=None,employment=None,schedule=None,requirements=None,responsibilities=None,url='https://trudvsem.ru/vacancy/one')
        return SimpleNamespace(items=[job],next_offset=None)
    monkeypatch.setattr(flow,'call_llm',llm)
    monkeypatch.setattr(flow,'search_vacancies',search)
    async def run():
        engine=MiniDialogue(config,2)
        await engine.enter(1,engine.session(1),'work')
        replies=await engine.handle(1,text='Мне 30, Томск, сварщик')
        view,_=present(engine,replies,'work')
        assert view['cards'] and view['cards'][0]['title']=='Сварщик'
        assert view['cards'][0]['status']=='unverified'
        assert 'Не подтверждено' in view['cards'][0]['text']
        assert engine.session(1).work['applicant']=='self'
        assert engine.session(1).work['fields']['age']['value']==30
    asyncio.run(run())


def test_public_api_auth_isolation_and_allowlisted_actions(config):
    class Store:
        async def read(self, published=False):
            assert published
            return dict(config=config, revision=2)
    async def run():
        app.state.preview=False; app.state.origin='https://mini.example'
        app.state.token='fake-token'; app.state.bot_name='test_bot'
        app.state.sessions={}; app.state.launches={}; app.state.capacity_lock=asyncio.Lock()
        app.state.search_slots=asyncio.Semaphore(8); app.state.store=Store()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://mini.example') as client:
            assert (await client.post('/api/session',json={})).status_code==401
            assert (await client.post('/api/session',json={'init_data':signed()},headers={'Origin':'https://evil.example'})).status_code==403
            response=await client.post('/api/session',json={'init_data':signed()})
            assert response.status_code==200
            key=response.json()['session'];headers={'Authorization':'Bearer '+key}
            assert (await client.post('/api/action',json={'section':'work'})).status_code==401
            result=await client.post('/api/action',json={'section':'work'},headers=headers)
            assert result.status_code==200 and result.json()['section']=='work'
            state=app.state.sessions[key];state.last_request=0
            assert (await client.post('/api/action',json={'payload':'jump:family'},headers=headers)).status_code==409
            state.last_request=0
            assert (await client.post('/api/action',json={'section':'home','text':'secret'},headers=headers)).status_code==400
            assert (await client.get('/api/state')).status_code==404
            assert 'config' not in result.json() and 'trace' not in result.json()
            assert (await client.post('/api/session',json={'init_data':signed()})).status_code==429
            state.touched=time.monotonic()-3601
            assert (await client.post('/api/action',json={'section':'work'},headers=headers)).status_code==401
    asyncio.run(run())


def test_search_slot_metrics_are_internal_and_slot_is_released_after_error(config,monkeypatch,caplog):
    class Store:
        async def read(self,published=False):return dict(config=config,revision=2)
    async def run():
        app.state.preview=True;app.state.origin='';app.state.sessions={};app.state.launches={}
        app.state.capacity_lock=asyncio.Lock();app.state.search_slots=asyncio.Semaphore(1)
        app.state.active_searches=0;app.state.max_active_searches=0;app.state.action_metrics=[];app.state.store=Store()
        key='session';app.state.sessions[key]=Session(MiniDialogue(config,2),section='work')
        async def broken(*args,**kwargs):raise RuntimeError('private')
        monkeypatch.setattr(app.state.sessions[key].engine,'handle',broken)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            response=await client.post('/api/action',json={'text':'hello'},headers={'Authorization':'Bearer '+key})
            assert response.status_code==503 and app.state.search_slots._value==1
            assert 'trace' not in response.json() and 'metrics' not in response.json()
        metric=app.state.action_metrics[-1]
        assert set(metric)>={'channel','request_total_ms','slot_wait_ms','slot_active_ms','concurrent_requests','safe_error_code'}
        assert metric['safe_error_code']=='action_failed' and 'hello' not in repr(metric)
    asyncio.run(run())
    assert 'type=RuntimeError' in caplog.text
    assert 'private' not in caplog.text


def test_search_slot_limit_is_bounded_and_waiting_is_measured(config, monkeypatch):
    class Store:
        async def read(self, published=False):
            return dict(config=config, revision=2)

    async def run():
        app.state.preview=True;app.state.origin='';app.state.sessions={};app.state.launches={}
        app.state.capacity_lock=asyncio.Lock();app.state.search_slots=asyncio.Semaphore(1)
        app.state.active_searches=0;app.state.max_active_searches=0;app.state.action_metrics=[];app.state.store=Store()
        entered=asyncio.Event();release=asyncio.Event()
        for key in ('one','two'):
            state=Session(MiniDialogue(config,2),section='work')
            async def slow(*args,**kwargs):
                entered.set();await release.wait();raise RuntimeError('private')
            monkeypatch.setattr(state.engine,'handle',slow)
            app.state.sessions[key]=state
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            first=asyncio.create_task(client.post('/api/action',json={'text':'first'},headers={'Authorization':'Bearer one'}))
            await entered.wait()
            second=asyncio.create_task(client.post('/api/action',json={'text':'second'},headers={'Authorization':'Bearer two'}))
            for _ in range(100):
                if app.state.search_slots._waiters:
                    break
                await asyncio.sleep(0)
            assert app.state.active_searches==1 and not second.done()
            release.set()
            assert (await first).status_code==503
            assert (await second).status_code==503
        assert app.state.max_active_searches==1
        assert sum(metric['slot_limit_reached'] for metric in app.state.action_metrics)==1
        assert all(metric['slot_wait_ms']>=0 for metric in app.state.action_metrics)
        assert 'first' not in repr(app.state.action_metrics) and 'second' not in repr(app.state.action_metrics)

    asyncio.run(run())


def test_account_delete_requires_session_and_revokes_only_owner(config, monkeypatch):
    deleted = []
    class Favorites:
        async def delete_account(self, owner): deleted.append(owner)
    app.state.preview = True
    app.state.sessions = {
        'one': Session(MiniDialogue(config, 2), owner='101'),
        'same': Session(MiniDialogue(config, 2), owner='101'),
        'other': Session(MiniDialogue(config, 2), owner='202'),
    }
    monkeypatch.setattr(app.state, 'favorites', Favorites(), raising=False)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            assert (await client.delete('/api/account')).status_code == 401
            assert (await client.delete('/api/account', headers={'Authorization': 'Bearer one'})).json() == {'deleted': True}
            assert (await client.delete('/api/account', headers={'Authorization': 'Bearer same'})).status_code == 401
            assert 'other' in app.state.sessions

    asyncio.run(run())
    assert deleted == ['101']
