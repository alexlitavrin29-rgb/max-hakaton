"""Open development regressions, with expectations fixed before runtime changes."""
import asyncio
import json
from copy import deepcopy

import pytest

from project.admin.tests.test_rental import CITY, engine, fields, page, row
from project.llm.services import rental
from project.llm.services.work import field
from project.llm.services.work_schema import valid_structure
from project.admin.check_work_free import config
from project.llm.services import flow


@pytest.mark.parametrize('attribute,value', [('intent', []), ('intent', {}), ('status', []),
                                           ('value', {'invented': 'nested'}), ('value', 1.5)])
def test_invalid_work_field_transport_is_rejected(attribute, value):
    item = field('known', 23, 'Мне 23')
    item[attribute] = value
    assert valid_structure({'work_fields': {'age': item}}) is False


@pytest.mark.parametrize('change', [
    {'location': {'location_id': 657600, 'name': 'Омск'}},
    {'location': {'location_id': 657600, 'name': 'Томск', 'region': 'Омская область'}},
])
def test_rental_matching_id_cannot_hide_conflicting_place(change):
    assert rental.assess(row() | change, CITY, 30000)[0]


@pytest.mark.parametrize('text', ['Жильё больше не нужно', 'Больше не нужно общежитие', 'Проживание не нужно'])
def test_false_housing_extraction_honors_explicit_removal(text):
    bot = flow.FlowDialogue(flow.PreviewReminders(), config())
    session = bot.session(1)
    bot.work.apply(session, {'housing': field('known', True, 'Нужно жильё')}, 'Нужно жильё')
    bot.work.apply(session, {'housing': field('known', False, text) | {'intent': 'excluded'}}, text)
    assert session.work['fields']['housing']['status'] == 'any'
    assert session.work['fields']['housing']['value'] is None
    assert 'housing' not in session.work['pending_fields']


@pytest.mark.parametrize('name,text', [('city', 'Город больше не важен'),
                                      ('budget', 'Ограничение по бюджету снимаю')])
def test_removed_rental_field_does_not_keep_old_filter(monkeypatch, name, text):
    removal = fields()
    removal[name] = {'value': None, 'status': 'any', 'evidence': text}
    responses = [removal]
    calls = []
    async def llm(*args, **kwargs):
        return json.dumps(responses.pop(0), ensure_ascii=False)
    async def locations(query):
        return [CITY]
    async def search(place, budget, number):
        calls.append((place, budget, number))
        return page([row()])
    monkeypatch.setattr(rental, 'call_llm', llm)
    monkeypatch.setattr(rental.reefapi, 'locations', locations)
    monkeypatch.setattr(rental.reefapi, 'search', search)
    async def run():
        bot = engine()
        await bot.handle(1, payload='jump:rental')
        await bot.handle(1, 'Томск до 30 тысяч')
        await bot.handle(1, text)
        state = bot.session(1).rental
        assert state[name] is None
        assert state['budget' if name == 'city' else 'city'] == (30000 if name == 'city' else CITY)
        assert state['awaiting'] == name
        assert calls == [('tomsk', 30000, 1)]
    asyncio.run(run())


@pytest.mark.parametrize('bad', [dict(action=[]), dict(unknown_filter=True), dict(budget=[]),
                               dict(city=None, budget=None, other=None, action='invented'),
                               dict(budget={'value': None, 'evidence': 'Изменить поиск', 'status': []})])
def test_bad_rental_json_never_changes_saved_state(monkeypatch, bad):
    async def llm(*args, **kwargs):
        return json.dumps(bad)
    monkeypatch.setattr(rental, 'call_llm', llm)
    async def run():
        bot = engine()
        await bot.handle(1, payload='jump:rental')
        original = deepcopy(bot.session(1).rental)
        replies = await bot.handle(1, 'Изменить поиск')
        assert bot.session(1).rental == original
        assert 'не получилось' in replies[0]['text']
    asyncio.run(run())


@pytest.mark.parametrize('intent', ['Снять комнату', 'Арендовать дом'])
def test_other_property_cannot_reuse_apartment_budget_after_task_switch(monkeypatch, intent):
    from project.admin.tests.test_flow import buttons
    responses = [fields(other=intent)]
    calls = []
    async def llm(*args, **kwargs):
        return json.dumps(responses.pop(0), ensure_ascii=False)
    async def locations(query):
        return [CITY]
    async def search(place, budget, number):
        calls.append((place, budget, number))
        return page([row()])
    monkeypatch.setattr(rental, 'call_llm', llm)
    monkeypatch.setattr(rental.reefapi, 'locations', locations)
    monkeypatch.setattr(rental.reefapi, 'search', search)
    async def run():
        bot = engine()
        await bot.handle(1, payload='jump:rental')
        await bot.handle(1, 'Томск до 30 тысяч')
        replies = await bot.handle(1, intent)
        assert calls == [('tomsk', 30000, 1)]
        assert 'долгосрочная аренда квартиры' in replies[0]['text']
        consent = next(b['payload'] for b in buttons(replies) if b['text'] == 'Искать только по городу и бюджету')
        await bot.handle(1, payload=consent)
        assert calls == [('tomsk', 30000, 1)]
        assert bot.session(1).rental['budget'] is None
        assert bot.session(1).rental['awaiting'] == 'budget'
    asyncio.run(run())


@pytest.mark.parametrize('address', ['дом 12', 'дом № 7'])
def test_house_number_is_an_extra_address_not_a_property_switch(monkeypatch, address):
    from project.admin.tests.test_flow import buttons
    text = 'Томск, квартира на Ленина, ' + address + '. До 30 тысяч в месяц'
    async def llm(*args, **kwargs):
        return json.dumps(fields('Томск', 'До 30 тысяч в месяц', 'на Ленина, ' + address), ensure_ascii=False)
    async def locations(query):
        return [CITY]
    calls = []
    async def search(place, budget, number):
        calls.append((place, budget, number))
        return page([row()])
    monkeypatch.setattr(rental, 'call_llm', llm)
    monkeypatch.setattr(rental.reefapi, 'locations', locations)
    monkeypatch.setattr(rental.reefapi, 'search', search)
    async def run():
        bot = engine()
        await bot.handle(1, payload='jump:rental')
        replies = await bot.handle(1, text)
        assert not calls
        payload = next(b['payload'] for b in buttons(replies) if b['text'] == 'Искать только по городу и бюджету')
        await bot.handle(1, payload=payload)
        assert calls == [('tomsk', 30000, 1)]
        assert address in bot.session(1).rental['other']
    asyncio.run(run())
