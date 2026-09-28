import asyncio
import httpx
from project.admin.tests.test_help_points import engine, buttons
from project.llm.services import help_points


def test_city_first_menu_keeps_city_and_shows_all(monkeypatch):
    monkeypatch.setenv('HELP_CITY_FIRST', '1')
    monkeypatch.delenv('HELP_API_URL', raising=False)
    async def run():
        bot = engine()
        session = bot.session(1)
        session.values['role'] = 'child'
        replies = await bot.enter(1, session, 'help_points')
        assert 'Напиши город' in replies[0]['text']
        replies = await bot.handle(1, text='Орёл, Орловская область')
        choices = buttons(replies[0])
        assert any('Еда' in c['text'] for c in choices)
        assert not any('Ночлег' in c['text'] for c in choices)
        all_places = next(c for c in choices if 'Все места' in c['text'])
        cards = await bot.handle(1, payload=all_places['payload'])
        assert any('Что можно получить' in c['text'] for c in cards)
        back = next(c for c in buttons(cards[-1]) if c['text'] == 'К видам помощи')
        replies = await bot.handle(1, payload=back['payload'])
        assert 'Орёл' in replies[0]['text']
    asyncio.run(run())


def test_api_failure_returns_retry_message(monkeypatch):
    monkeypatch.setenv('HELP_CITY_FIRST', '1')
    monkeypatch.setenv('HELP_API_URL', 'http://unused/api/help')
    async def failure(*args, **kwargs):
        raise httpx.ConnectError('unavailable')
    monkeypatch.setattr(help_points.httpx.AsyncClient, 'get', failure)
    async def run():
        bot = engine()
        session = bot.session(1)
        session.values['role'] = 'child'
        await bot.enter(1, session, 'help_points')
        replies = await bot.handle(1, text='Горно-Алтайск')
        assert 'временно недоступен' in replies[0]['text']
        assert not session.help_points.get('matches')
    asyncio.run(run())
