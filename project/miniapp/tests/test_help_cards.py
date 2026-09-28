"""Help categories must open actual cards through the mini-app HTTP boundary."""
import asyncio
import json
from pathlib import Path

import pytest

from project.miniapp import app as api
from project.miniapp.service import MiniDialogue
from project.miniapp.tests.test_rental_stability import action, client


@pytest.mark.parametrize('category', ['clothes', 'all'])
def test_izhevsk_category_opens_details(monkeypatch, category):
    monkeypatch.setenv('HELP_CITY_FIRST', '1')
    monkeypatch.delenv('HELP_API_URL', raising=False)
    config = json.loads(Path('docs/test-results/quality-2026-09-26/scenario.json').read_text(encoding='utf-8'))
    config.setdefault('rules', {})['navigation_contents'] = True
    state = api.Session(MiniDialogue(config, 3))
    for name, value in dict(preview=True, origin='', sessions={'test': state},
                            search_slots=asyncio.Semaphore(1), active_searches=0,
                            max_active_searches=0, action_metrics=[]).items():
        monkeypatch.setattr(api.app.state, name, value, raising=False)

    async def run():
        async with client() as http:
            await action(http, state, {'section': 'help'})
            menu = await action(http, state, {'text': 'Ижевск'})
            button = next(b for b in menu['controls'] if b.get('payload', '').startswith(f'hp:category:{category}:'))
            view = await action(http, state, {'payload': button['payload']})
            assert view['cards']
            for card in view['cards']:
                assert card['title'] and card['status'] == 'information'
                assert 'Что можно получить:' in card['text']
                assert 'Адрес:' in card['text'] or 'Точный адрес' in card['text']
            if category == 'clothes':
                assert 'Благодарю' in view['cards'][0]['title']
                assert 'одежду, обувь' in view['cards'][0]['text']
            home = next(b for b in view['controls'] if b['payload'] == 'mini:home')
            assert (await action(http, state, {'payload': home['payload']}))['section'] == 'home'
    asyncio.run(run())
