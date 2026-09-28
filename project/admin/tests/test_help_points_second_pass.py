"""Second-pass points must be reachable and keep service-specific restrictions."""
import asyncio
import httpx
from project.admin.tests.test_help_points import engine
from project.llm.services import help_points
from project.llm.services.help_points import catalog, is_local
from project.llm.services.geography import resolve
from project.miniapp.help_api import identified, points


def test_every_second_pass_point_is_reachable_in_its_city():
    added = [p for p in catalog() if p.get('research_id')]
    assert len(added) == 24
    for p in added:
        city = 'г. ' + p['city'] if p['city'] in {'Волхов', 'Находка'} else p['city']
        region = p['region'] + (', Клин район' if p['city'] == 'д. Заовражье' else '')
        result = points(city, region, '', 0, 100, 'city')
        assert identified(p)['id'] in {i['id'] for i in result['items']}, p['city']


def test_youth_services_do_not_inherit_unrelated_programs():
    rows = catalog()
    tagil = [p for p in rows if p['city'] == 'Нижний Тагил']
    assert len(tagil) == 5
    assert all(p['categories'] == ['food'] for p in tagil)
    assert all(p['flags']['food_package'] == 'UNVERIFIED' for p in tagil)
    assert all(p['flags']['hygiene_items'] == 'UNVERIFIED' for p in tagil)
    syzran = next(p for p in rows if p['city'] == 'Сызрань')
    assert syzran['categories'] == ['shelter']
    yurga = next(p for p in rows if p['city'] == 'Юрга')
    assert 'городского округа' in yurga['target_group']
    kirensk = next(p for p in rows if p['city'] == 'Киренск')
    assert 'СНИЛС' in kirensk['documents_required']
    assert 'двух раз в месяц' in kirensk['cost']
    assert not any(p['city'] in {'Сосновское', 'Старый Оскол', 'Прохладный', 'Дербент', 'Малые Дербеты'} for p in rows)
    sevastopol = next(p for p in rows if p['city'] == 'Севастополь' and 'социальная гостиница' in p['name'])
    assert sevastopol['flags']['shower'] == 'RESTRICTIONS'
    assert 'для проживающих' in sevastopol['cost']


def test_namesakes_do_not_inherit_a_city_point():
    for p in catalog():
        if not p.get('place_source_id'):
            continue
        choices = resolve(p['city'], p['region'])
        assert len(choices) > 1
        assert sum(is_local(p, place) for place in choices) == 1
        assert all(is_local(p, place) == (place['source_id'] == p['place_source_id']) for place in choices)


def test_selected_namesake_uses_unambiguous_regional_api_request(monkeypatch):
    monkeypatch.setenv('HELP_API_URL', 'http://directory/api/help')
    monkeypatch.setenv('HELP_CITY_FIRST', '1')
    real_client = httpx.AsyncClient

    def respond(request):
        assert request.url.params['place'] == 'Ленинградская Область'
        assert request.url.params['scope'] == 'region'
        return httpx.Response(200, json={'items': [p for p in catalog() if p['city'] == 'Волхов'], 'next_offset': None})

    monkeypatch.setattr(help_points.httpx, 'AsyncClient',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(respond)))

    async def run():
        bot = engine()
        branch = help_points.HelpPointsBranch(bot)
        session = bot.session(1)
        place = resolve('г. Волхов', 'Ленинградская область')[0]
        await branch.choose_place(session, place)
        assert session.help_points['place']['source_id'] == '4700400100000'
        assert session.help_points['catalog'][0]['place_source_id'] == '4700400100000'

    asyncio.run(run())
