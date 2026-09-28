import asyncio
from copy import deepcopy
import json
from pathlib import Path

import httpx

from project.llm.services.saved_answers import TITLES, material, buttons
from project.miniapp import app as api
from project.miniapp.service import MiniDialogue
from project.miniapp.tests.test_favorites import MemoryFavorites
from project.llm.services.flow import FlowDialogue, PreviewReminders


def config():
    return json.loads(Path('docs/test-results/warm-child-2026-09-27/proposed.json').read_text(encoding='utf-8'))


def test_catalog_only_contains_complete_child_answers():
    c = config()
    for node_id in TITLES:
        card = material(c, node_id)
        assert card and card['text'].strip() and card['actions'] is not None, node_id
    for node_id in ['menu', 'documents', 'work', 'rental', 'hp_city', 'pa_home', 'missing']:
        assert material(c, node_id) is None
    node = next(n for n in c['nodes'] if n['id'] == 'doc_passport_q3')
    node['text'] = 'Личное имя: {name}'
    assert material(c, node['id']) is None


def test_buttons_save_opens_app_and_share_has_only_material_identity():
    c = config()
    assert buttons(c, 'doc_passport_q3') == []
    c.setdefault('rules', {})['miniapp_answers_bot'] = 'test_bot'
    save, share = buttons(c, 'doc_passport_q3')
    assert save == dict(type='open_app', text='♡ Сохранить в Моё', web_app='test_bot', payload='save_doc_passport_q3')
    from urllib.parse import unquote
    assert 'https://max.ru/test_bot?startapp=view_doc_passport_q3' in unquote(share['url'])
    assert 'save_' not in share['url']
    assert buttons(c, 'documents') == []
    bot = FlowDialogue(PreviewReminders(), c, 4)
    session = bot.session(1);session.values = {'role':'child'}
    for node_id in TITLES:
        controls = bot.node_buttons(bot.nodes[node_id], session)
        assert controls[0]['payload'] == 'save_' + node_id
        assert controls[1]['text'] == 'Поделиться ↗'
        assert any(b['text'] == 'К оглавлению' for b in controls)


def test_view_save_update_withdraw_and_account_isolation(monkeypatch):
    c = config()
    class Store:
        async def read(self, published=False):
            assert published
            return dict(config=deepcopy(c), revision=4)
    class Saved(MemoryFavorites):
        async def list(self, owner):
            return [dict(card, saved_at='2026-09-27T12:00:00+00:00') for card in await super().list(owner)]
    store = Saved()
    monkeypatch.setattr(api.app.state, 'preview', True, raising=False)
    monkeypatch.setattr(api.app.state, 'store', Store(), raising=False)
    monkeypatch.setattr(api.app.state, 'favorites', store, raising=False)
    monkeypatch.setattr(api.app.state, 'sessions', {
        'sender': api.Session(MiniDialogue(c, 4), owner='1'),
        'receiver': api.Session(MiniDialogue(c, 4), owner='2')}, raising=False)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://testserver') as client:
            async def open_as(user, save=False, node_id='doc_passport_q3'):
                return await client.post('/api/material', headers={'Authorization':'Bearer '+user}, json=dict(node_id=node_id, save=save))
            assert (await open_as('missing')).status_code == 401
            assert (await open_as('sender', node_id='pa_home')).status_code == 404
            response = await open_as('sender', True)
            assert response.status_code == 200
            await open_as('sender', True)
            assert len(await store.list('1')) == 1
            assert (await open_as('receiver')).status_code == 200
            assert await store.list('2') == []
            await open_as('receiver', True)
            assert len(await store.list('2')) == 1
            node = next(n for n in c['nodes'] if n['id'] == 'doc_passport_q3')
            node['text'] += '\nУточнённый порядок действий.'
            response = await client.get('/api/favorites', headers={'Authorization':'Bearer sender'})
            card = response.json()['cards'][0]
            assert card['updated'] and 'Уточнённый' in card['text']
            assert card['favorite_id'] == (await store.list('1'))[0]['favorite_id']
            c['nodes'].remove(node)
            assert (await open_as('receiver')).status_code == 404
            response = await client.get('/api/favorites', headers={'Authorization':'Bearer sender'})
            assert response.json()['cards'][0]['unavailable']
            assert 'Уточнённый' not in response.json()['cards'][0]['text']
    asyncio.run(run())
