import asyncio
from copy import deepcopy
import json

import pytest

from project.admin.seed import initial_config
from project.admin.rental_draft import configure
from project.admin.tests.test_flow import buttons
from project.llm.services.flow import FlowDialogue, PreviewReminders, PublishedDialogue
from project.llm.services import rental
from project.llm.integrations import reefapi
from project.llm.config import LLMConfigurationError

CITY = dict(name='Томск', region='Томская область', slug='tomsk', location_id=657600)


def row(identifier='1', **changes):
    return dict(ad_id=identifier, title='Квартира', url='https://www.avito.ru/tomsk/kvartiry/' + identifier,
                price=25000, currency='RUB', price_period='в месяц', location={'location_id':657600},
                realty_type={'transaction_type':'ltr'}, category={'slug':'kvartiry'}, **changes)


def page(rows, number=1, more=False, place='tomsk', budget=30000):
    return dict(listings=rows, page=number, has_more=more,
                filters_applied=dict(location=place,price_max=budget,transaction='rent_long',property_type='apartment'))


def fields(city=None, budget=None, other=None):
    return dict(city=None if city is None else dict(value=city,evidence=city),
                budget=None if budget is None else dict(value=None,evidence=budget),other=other,action=None)


def engine():
    return FlowDialogue(PreviewReminders(),configure(initial_config()),31)


PLACES = {
    'Томск': [CITY],
    'Казань': [dict(name='Казань', region='Республика Татарстан', slug='kazan', location_id=1)],
    'Тула': [dict(name='Тула', region='Тульская область', slug='tula', location_id=2)],
    'Екатеринбург': [dict(name='Екатеринбург', region='Свердловская область', slug='ekaterinburg', location_id=3)],
}


RENTAL_FAST_PATH_CASES = [
    ('Томск, до 30 тысяч', 'Томск', None, 'tomsk', 30000),
    ('Ищу квартиру в Омске до 28 тысяч рублей в месяц', 'Омск', None, 'omsk', 28000),
    ('Казань, Республика Татарстан — максимум 35 000 рублей ежемесячно', 'Казань', 'Республика Татарстан', 'kazan', 35000),
    ('до 25 тыс., город Тула, Тульская область', 'Тула', 'Тульская область', 'tula', 25000),
    ('Екатеринбург 40000 в месяц', 'Екатеринбург', None, 'ekaterinburg', 40000),
    ('В Новосибирске, не дороже 32 тысяч в месяц', 'Новосибирск', None, 'novosibirsk', 32000),
    ('Пермь — максимум 27 500 руб./мес.', 'Пермь', None, 'perm', 27500),
    ('Ищу в Ижевске, бюджет до 26 тыс. рублей ежемесячно', 'Ижевск', None, 'izhevsk', 26000),
    ('Сочи; аренда максимум 45 000 рублей в месяц', 'Сочи', None, 'sochi', 45000),
    ('Владивосток, до 50 тысяч за месяц', 'Владивосток', None, 'vladivostok', 50000),
    ('Калининград — не больше 38 тыс. в месяц', 'Калининград', None, 'kaliningrad', 38000),
    ('Челябинск 31000 рублей ежемесячно', 'Челябинск', None, 'chelyabinsk', 31000),
    ('Ростов-на-Дону, до 36 000 руб в месяц', 'Ростов-на-Дону', None, 'rostov-na-donu', 36000),
    ('Нижний Новгород — максимум 34 тысячи в месяц', 'Нижний Новгород', None, 'nizhny-novgorod', 34000),
    ('Санкт-Петербург, аренда до 55 000 рублей в месяц', 'Санкт-Петербург', None, 'sankt-peterburg', 55000),
    ('Уфа, Республика Башкортостан, до 29 тысяч в месяц', 'Уфа', 'Республика Башкортостан', 'ufa', 29000),
    ('Самара, Самарская область — 33 000 рублей в месяц максимум', 'Самара', 'Самарская область', 'samara', 33000),
    ('Краснодар, Краснодарский край, бюджет 42 тысячи ежемесячно', 'Краснодар', 'Краснодарский край', 'krasnodar', 42000),
    ('Красноярск, Красноярский край — до 37 тыс. руб./мес.', 'Красноярск', 'Красноярский край', 'krasnoyarsk', 37000),
    ('Воронеж, Воронежская область, не дороже 24 000 в месяц', 'Воронеж', 'Воронежская область', 'voronezh', 24000),
    ('Рязань — аренда до 23 тысяч ежемесячно', 'Рязань', None, 'ryazan', 23000),
    ('Барнаул, Алтайский край, максимум 22 500 рублей за месяц', 'Барнаул', 'Алтайский край', 'barnaul', 22500),
    ('тОмСк!!! ДО 30 ТЫСЯЧ.', 'Томск', None, 'tomsk', 30000),
    ('  Омск ; до 28 000 рублей / месяц  ', 'Омск', None, 'omsk', 28000),
    ('ЕКАТЕРИНБУРГ—МАКСИМУМ 40 000 РУБЛЕЙ В МЕСЯЦ', 'Екатеринбург', None, 'ekaterinburg', 40000),
    ('35 000 рублей в месяц, город Казань, Республика Татарстан', 'Казань', 'Республика Татарстан', 'kazan', 35000),
    ('Бюджет до 27 тысяч ежемесячно. Ищу в Перми', 'Пермь', None, 'perm', 27000),
    ('Не больше 50000 за месяц — Владивосток', 'Владивосток', None, 'vladivostok', 50000),
    ('Город: Ижевск. Месячная аренда: максимум 26 000 рублей', 'Ижевск', None, 'izhevsk', 26000),
    ('Снять надолго в Калининграде, до 38 тысяч рублей в месяц', 'Калининград', None, 'kaliningrad', 38000),
]


@pytest.mark.parametrize('text,city,region,slug,budget', RENTAL_FAST_PATH_CASES)
def test_unambiguous_rental_input_bypasses_llm(monkeypatch, text, city, region, slug, budget):
    calls = dict(llm=0, locations=0, search=0)

    async def forbidden_llm(*args, **kwargs):
        calls['llm'] += 1
        raise AssertionError('LLM must not be called for deterministic rental input')

    async def locations(query):
        calls['locations'] += 1
        assert rental.same_words(query, city)
        return [dict(name=city, region=region or city + ' регион', slug=slug, location_id=100)]

    async def search(actual_slug, actual_budget, number):
        calls['search'] += 1
        assert (actual_slug, actual_budget, number) == (slug, budget, 1)
        return page([], place=slug, budget=budget)

    monkeypatch.setattr(rental, 'call_llm', forbidden_llm)
    monkeypatch.setattr(reefapi, 'locations', locations)
    monkeypatch.setattr(reefapi, 'search', search)

    async def run():
        bot = engine()
        await bot.handle(1, payload='jump:rental')
        reply = await bot.handle(1, text)
        state = bot.session(1).rental
        assert calls == dict(llm=0, locations=1, search=1)
        assert state['city']['name'] == city and state['budget'] == budget
        assert all(bot.text('rental_unrecognized') not in item['text'] for item in reply)
        recognition = [item for item in bot.session(1).trace if item.get('kind') == 'condition_recognition']
        assert len(recognition) == 1 and recognition[0]['method'] == 'deterministic'
        metrics=recognition[0]
        assert metrics['branch']=='rental' and metrics['recognition_method']=='deterministic'
        assert metrics['llm_call_count']==0 and metrics['location_call_count']==1 and metrics['source_call_count']==1
        assert metrics['source_pages']==1 and metrics['fetched_count']==0 and metrics['shown_count']==0
        assert all(metrics[name]>=0 for name in ('deterministic_ms','llm_ms','location_lookup_ms','source_search_ms','filter_render_ms'))
        assert not any(item.get('kind') == 'llm_router' for item in bot.session(1).trace)

    asyncio.run(run())


@pytest.mark.parametrize('text,query,budget', [
    ('Томск, до 30 тысяч', 'Томск', 30000),
    ('ищу в Казани максимум за 35 000 рублей в месяц', 'Казань', 35000),
    ('до 25 тыс., город Тула', 'Тула', 25000),
    ('Екатеринбург 40000 в месяц', 'Екатеринбург', 40000),
    ('тОмСк!!! ДО 30 ТЫСЯЧ.', 'Томск', 30000),
    ('Томск, Томская область — до 30 тысяч', 'Томск', 30000),
])
def test_explicit_rental_conditions_survive_empty_llm(monkeypatch, text, query, budget):
    async def llm(*a, **k): raise AssertionError('simple rental input must bypass LLM')
    async def locations(actual):
        assert rental.same_words(actual, query) and rental.same_words(query, actual)
        return PLACES[query]
    async def search(place, actual_budget, number):
        assert actual_budget == budget
        return page([], place=place, budget=actual_budget)
    monkeypatch.setattr(rental, 'call_llm', llm)
    monkeypatch.setattr(reefapi, 'locations', locations)
    monkeypatch.setattr(reefapi, 'search', search)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        reply=await bot.handle(1,text)
        state=bot.session(1).rental
        assert state['city']['name']==query and state['budget']==budget
        assert all(bot.text('rental_unrecognized') not in item['text'] for item in reply)
        assert any(item.get('kind')=='condition_recognition' and item.get('method')=='deterministic'
                   for item in bot.session(1).trace)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['empty', 'error', 'configuration'])
def test_explicit_rental_conditions_survive_empty_or_failed_llm(monkeypatch, failure):
    async def llm(*a, **k):
        if failure == 'error': raise rental.LLMError('timeout')
        if failure == 'configuration': raise LLMConfigurationError('missing key')
        return json.dumps(fields())
    async def locations(query): return [CITY]
    async def search(place, budget, number): return page([], place=place, budget=budget)
    monkeypatch.setattr(rental, 'call_llm', llm)
    monkeypatch.setattr(reefapi, 'locations', locations)
    monkeypatch.setattr(reefapi, 'search', search)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        reply=await bot.handle(1,'Томск, до 30 тысяч, хочу с мебелью')
        assert bot.session(1).rental['city']['name']=='Томск'
        assert bot.session(1).rental['budget']==30000
        assert bot.session(1).rental['other']
        assert any(item.get('kind')=='condition_recognition' and item.get('method')=='deterministic_fallback'
                   for item in bot.session(1).trace)
        assert all(bot.text('rental_unrecognized') not in item['text'] for item in reply)
    asyncio.run(run())


def test_rental_unknown_and_ambiguous_places_are_not_guessed(monkeypatch):
    candidates=[dict(name='Советск',slug='sovetsk_a',region='Калининградская область',location_id=10),
                dict(name='Советск',slug='sovetsk_b',region='Кировская область',location_id=11)]
    async def llm(*a,**k):return json.dumps(fields())
    async def locations(query):return candidates if query.casefold()=='советск' else []
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(reefapi,'locations',locations)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        reply=await bot.handle(1,'Советск, до 30 тысяч')
        assert bot.session(1).rental['city'] is None and len(bot.session(1).rental['choices'])==2
        assert {b['text'] for b in buttons(reply) if 'Советск' in b['text']}=={
            'Советск — Калининградская область','Советск — Кировская область'}
        bot=engine();await bot.handle(1,payload='jump:rental')
        reply=await bot.handle(1,'Несуществующийград, до 30 тысяч')
        assert bot.session(1).rental['city'] is None and bot.session(1).rental['budget']==30000
        assert any('город' in item['text'].casefold() for item in reply)
    asyncio.run(run())


@pytest.mark.parametrize('text', [
    'Томск, до 30',
    'Томск, залог 30 тысяч',
    'Томск, при заселении 60 тысяч',
    'Томск, покупка до 5 миллионов',
    'Томск, посуточно до 3000 рублей',
])
def test_rental_does_not_confuse_other_numbers_with_monthly_budget(monkeypatch, text):
    async def llm(*a,**k):return json.dumps(fields())
    async def locations(query):return [CITY]
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(reefapi,'locations',locations)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental');await bot.handle(1,text)
        state=bot.session(1).rental
        assert state['city']['name']=='Томск' and state['budget'] is None
    asyncio.run(run())


def test_rental_correction_changes_only_explicit_field(monkeypatch):
    async def llm(*a,**k):return json.dumps(fields())
    async def locations(query):return [CITY]
    async def search(place,budget,number):return page([],place=place,budget=budget)
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(reefapi,'locations',locations);monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        await bot.handle(1,'Томск, до 30 тысяч')
        city=deepcopy(bot.session(1).rental['city'])
        await bot.handle(1,'нет, максимум 25 тысяч в месяц')
        assert bot.session(1).rental['city']==city and bot.session(1).rental['budget']==25000
    asyncio.run(run())


@pytest.mark.parametrize('text,expected', [('до 30 тысяч',30000),('25000',25000),('двадцать пять тысяч',25000),
    ('30',None),('до 3000 в сутки',None),('минус 20000',None),('от 20000',None),('20000 евро',None)])
def test_budget_semantics(text,expected):
    assert rental.budget_value(text)==expected


def test_sparse_and_conflicting_listings():
    valid=row()
    assert rental.assess(valid,CITY,30000)==(None,[])
    for patch,reason in [({'price':40000},'выше бюджета'),({'location':{'location_id':1}},'другой город'),
                         ({'price_period':'в сутки'},'не месячная аренда'),({'realty_type':{'transaction_type':'sale'}},'не долгосрочная аренда')]:
        assert rental.assess({**valid,**patch},CITY,30000)[0]==reason
    _,unknown=rental.assess({**valid,'price':None,'location':{},'price_period':None},CITY,30000)
    assert len(unknown)>=3


def test_dialogue_memory_more_correction_failures_and_stale(monkeypatch):
    responses=[fields('Томск'),fields(budget='до 30 тысяч'),fields(budget='до 25 тысяч')]
    calls=[]
    async def llm(*args,**kwargs):return json.dumps(responses.pop(0),ensure_ascii=False)
    async def locations(query):assert query=='Томск';return [CITY]
    async def search(place,budget,number):
        calls.append((place,budget,number))
        if len(calls) in {2,3}:raise reefapi.ReefError('timeout')
        return page([row(str(number))],number,True,budget=budget)
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(reefapi,'locations',locations);monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        r=await bot.handle(1,'Томск');assert 'сумму' in r[0]['text'];assert not calls
        r=await bot.handle(1,'до 30 тысяч');assert calls==[('tomsk',30000,1)]
        old_more=next(b['payload'] for b in buttons(r) if b['text']=='Показать ещё')
        r=await bot.handle(1,payload=old_more);assert bot.session(1).rental['page']==2
        assert 'не удалось' in r[0]['text']
        r=await bot.handle(1,payload=next(b['payload'] for b in buttons(r) if b['text']=='Повторить'))
        assert calls[-1]==('tomsk',30000,2)
        changed=await bot.handle(1,payload=next(b['payload'] for b in buttons(r) if b['text']=='Изменить условия'))
        assert bot.session(1).rental['budget']==30000
        r=await bot.handle(1,'до 25 тысяч');assert calls[-1]==('tomsk',25000,1)
        count=len(calls);await bot.handle(1,payload=old_more);assert len(calls)==count
    asyncio.run(run())


def test_rental_search_retries_one_transient_provider_failure(monkeypatch):
    calls = []
    chita = dict(name='Чита', region='Забайкальский край', slug='chita', location_id=661950)

    async def locations(query):
        return [chita]

    async def forbidden_llm(*args, **kwargs):
        raise AssertionError('A simple "city, budget" request must not depend on LLM availability')

    async def search(place, budget, number):
        calls.append((place, budget, number))
        if len(calls) == 1:
            raise reefapi.ReefError('connection')
        recovered = row('recovered')
        recovered['location'] = {'location_id': 661950}
        return page([recovered], place=place, budget=budget)

    monkeypatch.setattr(reefapi, 'locations', locations)
    monkeypatch.setattr(reefapi, 'search', search)
    monkeypatch.setattr(rental, 'call_llm', forbidden_llm)

    async def run():
        bot = engine()
        await bot.handle(1, payload='jump:rental')
        replies = await bot.handle(1, 'чита, 50к')
        assert calls == [('chita', 50000, 1), ('chita', 50000, 1)]
        assert bot.session(1).result_cards and bot.session(1).result_cards[0]['title'] == 'Квартира'
        assert all(bot.text('rental_api_error') not in item['text'] for item in replies)
        retries = [item for item in bot.session(1).trace if item.get('kind') == 'rental_retry']
        assert len(retries) == 1 and retries[0]['code'] == 'connection'

    asyncio.run(run())


def test_rental_uses_last_successful_exact_search_after_two_transient_failures(monkeypatch):
    calls = []
    chita = dict(name='Чита', region='Забайкальский край', slug='chita', location_id=661950)

    async def locations(query):
        return [chita]

    async def search(place, budget, number):
        calls.append((place, budget, number))
        raise reefapi.ReefError('connection')

    cached = row('cached')
    cached['location'] = {'location_id': 661950}
    stale = page([cached], place='chita', budget=50000)

    monkeypatch.setattr(reefapi, 'locations', locations)
    monkeypatch.setattr(reefapi, 'search', search)
    monkeypatch.setattr(reefapi, 'cached_search', lambda *args: (deepcopy(stale), 420), raising=False)

    async def run():
        bot = engine()
        await bot.handle(1, payload='jump:rental')
        replies = await bot.handle(1, 'Чита, 50к')
        assert calls == [('chita', 50000, 1), ('chita', 50000, 1)]
        assert bot.session(1).result_cards[0]['title'] == 'Квартира'
        assert any('резерв' in item['text'].casefold() and '7 мин' in item['text'] for item in replies)
        fallback = [item for item in bot.session(1).trace if item.get('kind') == 'rental_cache_fallback']
        assert fallback == [{'kind': 'rental_cache_fallback', 'boundary': 'search', 'age_seconds': 420}]

    asyncio.run(run())


def test_published_dialogue_persists_rental_retry_counter(monkeypatch):
    calls = []

    class Store:
        def __init__(self):
            self.events = []

        async def read(self, published=False):
            return dict(config=configure(initial_config()), revision=31, published_id=1)

        async def event(self, *args):
            self.events.append(args)

    async def locations(query):
        return [CITY]

    async def search(place, budget, number):
        calls.append((place, budget, number))
        if len(calls) == 1:
            raise reefapi.ReefError('connection')
        return page([row()], place=place, budget=budget)

    monkeypatch.setattr(reefapi, 'locations', locations)
    monkeypatch.setattr(reefapi, 'search', search)

    async def run():
        store = Store()
        bot = PublishedDialogue(PreviewReminders(), store)
        await bot.handle(1, payload='jump:rental')
        await bot.handle(1, text='Томск, до 30 тысяч')
        assert ('rental_retry_connection', 'rental') in store.events

    asyncio.run(run())


def test_grounding_atomicity_and_llm_failure(monkeypatch):
    responses=[{'city':{'value':'Москва','evidence':'Томск'},'budget':None},
               {'city':None,'budget':{'evidence':'до 30 тысяч'},'other':'не существующая фраза'}]
    async def llm(*a,**k):
        if responses:return json.dumps(responses.pop(0),ensure_ascii=False)
        raise rental.LLMError('timeout')
    monkeypatch.setattr(rental,'call_llm',llm)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental');original=deepcopy(bot.session(1).rental)
        for text in ['город Томск, это важно','до 30 тысяч для бабушки','подберите что-нибудь']:
            r=await bot.handle(1,text);assert 'не получилось' in r[0]['text'], text
            assert bot.session(1).rental==original
    asyncio.run(run())


def test_ambiguous_city_and_extra_condition_require_choice(monkeypatch):
    candidates=[dict(name='Советск',slug='sovetsk_a',region='Первая область',location_id=1),
                dict(name='Советск',slug='sovetsk_b',region='Вторая область',location_id=2)]
    calls=[]
    async def llm(*a,**k):return json.dumps(fields('Советск','до 30 тысяч','без залога'),ensure_ascii=False)
    async def locations(query):return candidates
    async def search(place,budget,number):calls.append(place);return page([],place=place)
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(reefapi,'locations',locations);monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        r=await bot.handle(1,'Советск до 30 тысяч без залога');assert not calls
        assert 'Первая область' in buttons(r)[0]['text']
        r=await bot.handle(1,payload=buttons(r)[1]['payload']);assert not calls
        r=await bot.handle(1,payload=buttons(r)[0]['payload']);assert calls==['sovetsk_b']
        assert any('просмотренной странице' in item['text'] for item in r)
    asyncio.run(run())


def test_budget_first_and_short_clarification(monkeypatch):
    responses=[fields(budget='30'),fields('Томск')]
    async def llm(*a,**k):return json.dumps(responses.pop(0),ensure_ascii=False)
    async def locations(query):return [CITY]
    async def search(*args):return page([])
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(reefapi,'locations',locations);monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        await bot.handle(1,'30');assert bot.session(1).rental['budget_issue']
        await bot.handle(1,'тысяч');assert bot.session(1).rental['budget']==30000
        await bot.handle(1,'Томск');assert bot.session(1).rental['city']['slug']=='tomsk'
    asyncio.run(run())


def test_cards_deduplicate_and_mark_unknown_and_page_filter_failure(monkeypatch):
    async def llm(*a,**k):return json.dumps(fields('Томск','до 30 тысяч'),ensure_ascii=False)
    async def locations(query):return [CITY]
    async def search(place,budget,number):
        if number==2:return page([row('unsafe')],budget=99999)
        sparse={**row('sparse'),'currency':None,'location':{},'price_period':None}
        return page([row('one'),row('one'),sparse],more=True)
    monkeypatch.setattr(rental,'call_llm',llm);monkeypatch.setattr(reefapi,'locations',locations);monkeypatch.setattr(reefapi,'search',search)
    async def run():
        bot=engine();await bot.handle(1,payload='jump:rental')
        r=await bot.handle(1,'Томск до 30 тысяч')
        assert bot.session(1).rental['seen']==['one','sparse']
        assert any('Нужно уточнить:' in item['text'] and 'валюта не указана' in item['text'] for item in r)
        before=deepcopy(bot.session(1).rental)
        r=await bot.handle(1,payload=next(b['payload'] for b in buttons(r) if b['text']=='Показать ещё'))
        assert 'не удалось' in r[0]['text'] and bot.session(1).rental==before
    asyncio.run(run())


def test_rental_reminder_requires_confirmation_and_keeps_search():
    from datetime import datetime,timedelta,timezone
    async def run():
        config=configure(initial_config());config['search']['contract_version']=3
        bot=FlowDialogue(PreviewReminders(),config,31);await bot.handle(1,payload='jump:rental')
        st=bot.session(1).rental;st.update(city=CITY,budget=30000)
        before=deepcopy(st)
        await bot.handle(1,payload=bot.rental.button(bot.session(1),'Напомнить','remind')['payload'])
        await bot.handle(1,'Томск')
        due=(datetime.now(timezone.utc)+timedelta(days=2)).strftime('18:30, %d.%m.%Y')
        r=await bot.handle(1,due)
        assert not bot.reminders.rows
        await bot.handle(1,payload=next(b['payload'] for b in buttons(r) if b['text']=='Сохранить'))
        assert len(bot.reminders.rows)==1 and bot.session(1).rental==before
        assert next(iter(bot.reminders.rows.values()))['text']=='Вернуться к поиску жилья.'
    asyncio.run(run())
