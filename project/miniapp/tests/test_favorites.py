import asyncio
from copy import deepcopy

import httpx

from project.admin.miniapp_draft import configure
from project.llm.services.flow import FlowDialogue, PreviewReminders
from project.miniapp import app as api
from project.miniapp.favorites import card_key
from project.miniapp.service import MiniDialogue
from project.miniapp.tests.test_miniapp import config


class MemoryFavorites:
    def __init__(self): self.rows = {}
    async def list(self, owner):
        return [dict(card, favorite_id=key) for (user, key), card in self.rows.items() if user == owner]
    async def save(self, owner, key, card): self.rows[owner, key] = deepcopy(card)
    async def remove(self, owner, key): self.rows.pop((owner, key), None)


def test_delete_waits_for_started_save_and_revokes_queued_save(config, monkeypatch):
    card = dict(title='Квартира', text='Условия', section='rental', actions=[dict(url='https://example.org/1')])
    key = card_key('rental', card)
    entered = asyncio.Event()
    release = asyncio.Event()

    class PausedFavorites(MemoryFavorites):
        async def save(self, owner, card_id, value):
            entered.set()
            await release.wait()
            await super().save(owner, card_id, value)

        async def delete_account(self, owner):
            self.rows = {k: v for k, v in self.rows.items() if k[0] != owner}

    store = PausedFavorites()
    monkeypatch.setattr(api.app.state, 'preview', True, raising=False)
    monkeypatch.setattr(api.app.state, 'sessions', {
        'first': api.Session(MiniDialogue(config, 4), owner='123', seen_cards={key: card}),
        'second': api.Session(MiniDialogue(config, 4), owner='123', seen_cards={key: card}),
    }, raising=False)
    monkeypatch.setattr(api.app.state, 'favorites', store, raising=False)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://testserver') as client:
            save = asyncio.create_task(client.post('/api/favorites', headers={'Authorization': 'Bearer first'},
                                                   json=dict(card_id=key, saved=True)))
            await entered.wait()
            deletion = asyncio.create_task(client.delete('/api/account', headers={'Authorization': 'Bearer second'}))
            await asyncio.sleep(0)
            release.set()
            assert (await save).status_code == 200
            assert (await deletion).json() == {'deleted': True}
            assert store.rows == {}
            assert (await client.post('/api/favorites', headers={'Authorization': 'Bearer first'},
                                      json=dict(card_id=key, saved=True))).status_code == 401
    asyncio.run(run())


def test_save_waiting_for_delete_cannot_restore_data(config, monkeypatch):
    card = dict(title='Квартира', text='Условия', section='rental', actions=[dict(url='https://example.org/1')])
    key = card_key('rental', card)
    entered = asyncio.Event()
    release = asyncio.Event()

    class PausedDelete(MemoryFavorites):
        async def delete_account(self, owner):
            self.rows = {k: v for k, v in self.rows.items() if k[0] != owner}
            entered.set()
            await release.wait()

    store = PausedDelete()
    monkeypatch.setattr(api.app.state, 'preview', True, raising=False)
    monkeypatch.setattr(api.app.state, 'sessions', {
        'first': api.Session(MiniDialogue(config, 4), owner='123', seen_cards={key: card}),
        'second': api.Session(MiniDialogue(config, 4), owner='123', seen_cards={key: card}),
    }, raising=False)
    monkeypatch.setattr(api.app.state, 'favorites', store, raising=False)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://testserver') as client:
            deletion = asyncio.create_task(client.delete('/api/account', headers={'Authorization': 'Bearer first'}))
            await entered.wait()
            save = asyncio.create_task(client.post('/api/favorites', headers={'Authorization': 'Bearer second'},
                                                   json=dict(card_id=key, saved=True)))
            await asyncio.sleep(0)
            release.set()
            assert (await deletion).status_code == 200
            assert (await save).status_code == 401
            assert store.rows == {}
    asyncio.run(run())


def test_favorites_survive_sessions_and_are_isolated(config, monkeypatch):
    card = dict(title='Квартира', text='Условия', section='rental', actions=[dict(url='https://example.org/1')])
    key = card_key('rental', card)
    owner = api.Session(MiniDialogue(config, 4), owner='123', seen_cards={key: card})
    reopened = api.Session(MiniDialogue(config, 4), owner='123')
    other = api.Session(MiniDialogue(config, 4), owner='456')
    monkeypatch.setattr(api.app.state, 'preview', True, raising=False)
    monkeypatch.setattr(api.app.state, 'sessions', {'owner': owner, 'new': reopened, 'other': other}, raising=False)
    monkeypatch.setattr(api.app.state, 'favorites', MemoryFavorites(), raising=False)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://testserver') as client:
            async def read(token):
                return await client.get('/api/favorites', headers={'Authorization': 'Bearer '+token})
            async def write(token, saved, card_id=key):
                return await client.post('/api/favorites', headers={'Authorization': 'Bearer '+token},
                                         json=dict(card_id=card_id, saved=saved))
            assert (await read('missing')).status_code == 401
            assert (await write('other', True)).status_code == 409
            assert (await write('owner', True)).status_code == 200
            await write('owner', True)
            assert len((await read('new')).json()['cards']) == 1
            assert (await read('other')).json()['cards'] == []
            await write('other', False)
            assert len((await read('owner')).json()['cards']) == 1
            await write('new', False)
            assert (await read('owner')).json()['cards'] == []
            assert (await write('owner', True, 'not-a-card')).status_code == 422
    asyncio.run(run())


def test_identity_uses_source_and_section():
    card = dict(title='Название', text='Описание', actions=[dict(url='https://example.org/1')])
    assert card_key('work', card) == card_key('work', dict(card, text='Другое описание'))
    assert card_key('work', card) != card_key('rental', card)
    assert card_key('help', card) != card_key('help', dict(card, text='Другой адрес'))


def test_bot_opens_three_searches_but_miniapp_keeps_its_own_controls(config):
    updated = configure(config)
    bot = FlowDialogue(PreviewReminders(), updated, 5)
    app = MiniDialogue(updated, 5)
    session = bot.session(1);session.values = {'role': 'child'}
    node = dict(id='menu', buttons=[dict(label=target, target=target) for target in ('work', 'rental', 'help_points')])
    buttons = bot.node_buttons(node, session)
    assert [b['payload'] for b in buttons] == ['work', 'rental', 'help']
    assert all(b['type'] == 'open_app' and b['web_app'] == 't143_hakaton_max_bot' for b in buttons)
    assert all(b['type'] == 'callback' for b in app.node_buttons(node, app.session(1)))
    assert updated['rules']['miniapp_search_bot'] == 't143_hakaton_max_bot'


def test_child_menu_preserves_information_and_opens_apps_one_step_later():
    import json
    from pathlib import Path
    original = json.loads(Path('docs/test-results/warm-child-2026-09-27/proposed.json').read_text(encoding='utf-8'))
    # Also repair the direct main-menu entries published in version 5.
    menu = next(n for n in original['nodes'] if n['id'] == original['menu'])
    for button in menu['buttons']:
        if button['label'] == 'Ищу работу': button['target'] = 'work'
        if button['label'] == 'Ищу жильё': button['target'] = 'rental'
    updated = configure(original)
    engine = FlowDialogue(PreviewReminders(), updated, 5)
    session = engine.session(1); session.values = {'role': 'child'}
    controls = engine.node_buttons(engine.nodes[updated['menu']], session)
    assert {b['text']: b['payload'] for b in controls if b['type'] == 'open_app'} == {'Где могут помочь': 'help'}
    for label, target, search in [('Ищу работу', 'work_entry', 'work'), ('Ищу жильё', 'housing', 'rental')]:
        button = next(b for b in controls if b['text'] == label)
        assert button['type'] == 'callback'
        index = int(button['payload'].rsplit(':', 1)[1])
        assert engine.nodes[updated['menu']]['buttons'][index]['target'] == target
        nested = engine.node_buttons(engine.nodes[target], session)
        assert any(b.get('type') == 'open_app' and b['payload'] == search for b in nested)
        assert engine.nodes[target] == next(n for n in original['nodes'] if n['id'] == target)
    assert configure(updated) == updated
