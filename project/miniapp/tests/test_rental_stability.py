"""Exercise housing through the same HTTP boundary used by the app menu."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from project.miniapp import app as api
from project.miniapp.service import MiniDialogue
from project.admin.tests.test_rental import CITY, page, row
from project.llm.integrations import reefapi


@pytest.fixture
def setup(monkeypatch):
    config = json.loads(Path('docs/test-results/quality-2026-09-26/scenario.json').read_text(encoding='utf-8'))
    state = api.Session(MiniDialogue(config, 3))
    for name, value in dict(preview=True, origin='', sessions={'test': state},
                            search_slots=asyncio.Semaphore(1), active_searches=0,
                            max_active_searches=0, action_metrics=[]).items():
        monkeypatch.setattr(api.app.state, name, value, raising=False)
    async def locations(query): return [CITY]
    monkeypatch.setattr(reefapi, 'locations', locations)
    monkeypatch.setattr(reefapi, 'cached_search', lambda *args: None)
    return state


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
                            base_url='http://testserver', headers={'Authorization': 'Bearer test'})


async def action(http, state, body):
    state.last_request = 0
    response = await http.post('/api/action', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_menu_housing_results_next_page_edit_and_home(setup, monkeypatch):
    async def search(place, budget, number):
        return page([row(str(number))], number=number, more=number == 1, budget=budget)
    monkeypatch.setattr(reefapi, 'search', search)
    async def run():
        async with client() as http:
            await action(http, setup, {'section': 'rental'})
            view = await action(http, setup, {'text': 'Томск, до 30000 рублей в месяц'})
            assert len(view['cards']) == 1 and '30000' in view['summary']
            more = next(c['payload'] for c in view['controls'] if c.get('more'))
            view = await action(http, setup, {'payload': more})
            assert view['cards'][0]['actions'][0]['url'].endswith('/2')
            assert not any(c.get('more') for c in view['controls'])
            view = await action(http, setup, {'text': 'до 28000 рублей в месяц'})
            assert 'Томск' in view['summary'] and '28000' in view['summary']
            home = next(c['payload'] for c in view['controls'] if c['label'] == 'К разделам')
            assert (await action(http, setup, {'payload': home}))['section'] == 'home'
    asyncio.run(run())


def test_provider_failure_retry_preserves_conditions(setup, monkeypatch):
    failing = [True]
    async def search(place, budget, number):
        if failing[0]: raise reefapi.ReefError('http_401')
        return page([row()], budget=budget)
    monkeypatch.setattr(reefapi, 'search', search)
    async def run():
        async with client() as http:
            await action(http, setup, {'section': 'rental'})
            view = await action(http, setup, {'text': 'Томск, до 30000 рублей в месяц'})
            assert not view['cards'] and '30000' in view['summary']
            retry = next(c['payload'] for c in view['controls'] if c['label'] == 'Повторить')
            failing[0] = False
            view = await action(http, setup, {'payload': retry})
            assert len(view['cards']) == 1 and 'Томск' in view['summary']
    asyncio.run(run())


def test_queue_wait_expires_without_changing_conditions(setup, monkeypatch):
    monkeypatch.setattr(api, 'QUEUE_TIMEOUT', .02, raising=False)
    async def run():
        async with client() as http:
            await action(http, setup, {'section': 'rental'})
            await api.app.state.search_slots.acquire()
            try:
                setup.last_request = 0
                response = await asyncio.wait_for(http.post('/api/action', json={'text': 'Томск'}), .2)
                assert response.status_code == 503
                assert setup.engine.session(1).rental['city'] is None
                assert not setup.lock.locked()
                assert api.app.state.action_metrics[-1]['safe_error_code'] == 'queue_timeout'
            finally:
                api.app.state.search_slots.release()
    asyncio.run(run())


def test_failed_action_restores_state_and_allows_retry(setup, monkeypatch):
    original = api.MiniDialogue.handle
    failed = [False]

    async def interrupted(engine, user, **kwargs):
        if not failed[0]:
            failed[0] = True
            engine.session(user).rental['city'] = {'name': 'Не сохранён'}
            raise TimeoutError()
        return await original(engine, user, **kwargs)

    monkeypatch.setattr(api.MiniDialogue, 'handle', interrupted)

    async def run():
        async with client() as http:
            await action(http, setup, {'section': 'rental'})
            before = setup.engine.session(1).rental.copy()
            setup.last_request = 0
            response = await http.post('/api/action', json={'text': 'Томск'})
            assert response.status_code == 503
            assert setup.engine.session(1).rental == before
            setup.last_request = 0
            response = await http.post('/api/action', json={'text': 'Томск'})
            assert response.status_code == 200

    asyncio.run(run())


def test_cancelled_action_restores_state(setup, monkeypatch):
    started = asyncio.Event()
    async def interrupted(engine, user, **kwargs):
        engine.session(user).rental['city'] = {'name': 'Не сохранён'}
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(api.MiniDialogue, 'handle', interrupted)

    async def run():
        async with client() as http:
            await action(http, setup, {'section': 'rental'})
            before = setup.engine.session(1).rental.copy()
            setup.last_request = 0
            request = asyncio.create_task(http.post('/api/action', json={'text': 'Томск'}))
            await started.wait()
            request.cancel()
            try:
                await request
            except asyncio.CancelledError:
                pass
            assert setup.engine.session(1).rental == before
            assert not setup.lock.locked()

    asyncio.run(run())
